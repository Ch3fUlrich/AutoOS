"""tests/test_oc_l2.py - the L2 phase lane (D-665 AO-L2-LAUNCH).

unittest, stdlib only, NO real opencode: the same fakes as the L1 launcher
suite (tests/_oc_l1_fakes.py) stand in for the server and the binary, and the
lane goes through `oc_l1.main` for real, so what is pinned here is what the
launcher would actually render, spawn and prompt.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l2  # noqa: E402
import autoos_inbox  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    FAKE_PY,
    FAKE_SESSION_ID,
    HINT_EXPECTED,
    PW_ENV,
    PW_VALUE,
    RECORD_ENV,
    FakeServer,
    make_fake_bin,
    pid_alive,
    wait_file,
)

GUARD_DIR = ROOT / "configuration" / "opencode" / "plugins" / "bash-guard"
BRIEF = "GOAL: make the tests green.\nFILES: tools/\nDONE: suite passes.\n"


class NameTest(unittest.TestCase):
    def test_lane_name_is_l2_repo_phase(self):
        self.assertEqual(oc_l2.lane_name("/srv/checkouts/AutoOS", "AO-DEADROWS"),
                         "l2-autoos-ao-deadrows")
        # the same project spelled two ways is one lane
        self.assertEqual(oc_l2.lane_name("/a/autoos-ci", "p1"),
                         oc_l2.lane_name("/a/AutoOS CI", "p1"))

    def test_lane_name_stays_within_the_lane_shape(self):
        # the repo part gives way; the phase never does, because one lane per
        # phase is the unit that has to stay distinct
        name = oc_l2.lane_name("a" * 40, "phase-one")
        self.assertRegex(name, r"^[a-z0-9][a-z0-9-]{0,31}$")
        self.assertTrue(name.endswith("-phase-one"), name)
        self.assertLessEqual(len(name), 32)
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.lane_name("AutoOS", "x" * 40)
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.lane_name("AutoOS", "???")

    def test_check_lane_rejects_shapes_that_are_not_lanes(self):
        for bad in ("", "L1-pilot", "../etc", "a b", "x" * 33):
            with self.assertRaises(oc_l2.L2Error):
                oc_l2.check_lane(bad)


class ComboModelTest(unittest.TestCase):
    def test_l2_orchestrator_resolves_with_its_declared_window(self):
        m = oc_l2.combo_model("l2-orchestrator")
        self.assertEqual(m["provider"], "omniroute")
        self.assertEqual(m["modelID"], "l2-orchestrator")
        # the 128k clamp is the bug this repo already fixed once: the route
        # declares 1M, so the lane must render 1M.
        self.assertEqual(m["limit"]["context"], 1048576)

    def test_unknown_combo_refused(self):
        with self.assertRaises(oc_l2.L2Error) as cm:
            oc_l2.combo_model("no-such-combo")
        self.assertIn("unknown combo", str(cm.exception))

    def test_combo_without_the_gateway_surface_refused(self):
        with tempfile.TemporaryDirectory(prefix="oc_l2_reg_") as td:
            p = Path(td) / "registry.json"
            p.write_text(json.dumps({"routes": {"x": {"surfaces": {"zed": {}}}}}),
                         encoding="utf-8")
            with self.assertRaises(oc_l2.L2Error) as cm:
                oc_l2.combo_model("x", registry_path=p)
            self.assertIn("omniroute", str(cm.exception))

    def test_unreadable_registry_refused(self):
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.combo_model("l2-orchestrator",
                              registry_path=Path(tempfile.gettempdir()) / "nope.json")


class LaneTest(unittest.TestCase):
    """The start pipeline: render content, first prompt, duplicate refusal,
    canary forwarding, stop, inbox."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l2_test_")
        self.td = Path(self._td.name)
        self.srv = FakeServer(PW_VALUE)
        self.srv.start()
        self.proj = self.td / "proj"
        self.proj.mkdir()
        self.brief = self.td / "brief.md"
        self.brief.write_text(BRIEF, encoding="utf-8")
        self.l1_inbox = self.td / "inbox" / "L1-routing.md"
        (self.td / "fake_opencode.py").write_text(FAKE_PY, encoding="utf-8")
        self.bin_ = make_fake_bin(self.td, self.td / "fake_opencode.py", sys.executable)
        self.rec = self.td / "bin_record.json"
        self._envs = {}
        for k, v in ((PW_ENV, PW_VALUE), (RECORD_ENV, str(self.rec)),
                     (oc_l2.ENV_STATE_DIR, str(self.td / "state")),
                     (oc_l2.ENV_L1_INBOX, str(self.l1_inbox))):
            self._envs[k] = os.environ.get(k)
            os.environ[k] = v
        os.environ.pop(oc_l2.ENV_RUN_DIR, None)
        os.environ.pop(oc_l2.ENV_BIN, None)
        # The fake server binds an ephemeral port; a real lane port must sit in
        # the launcher's 47200-47299 band, so the band widens for the test only.
        mock.patch.object(oc_l1, "PORT_MIN", 1024).start()
        mock.patch.object(oc_l1, "PORT_MAX", 65535).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        for k, v in self._envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.srv.stop()
        self._td.cleanup()

    def _start(self, **over):
        kw = dict(combo="l2-orchestrator", opencode_bin=self.bin_,
                  password_env=PW_ENV, port=self.srv.port)
        kw.update(over)
        return oc_l2.cmd_start(self.proj, "ao-deadrows", self.brief, **kw)

    def _lane_name(self):
        return oc_l2.lane_name(self.proj, "ao-deadrows")

    def _rendered(self):
        name = self._lane_name()
        return json.loads((self.td / "state" / name / "opencode.json")
                          .read_text(encoding="utf-8"))

    # (1) the rendered lane: model, spawner-only MCP, task deny, guard plugin
    def test_rendered_lane_is_the_l2_shape(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        cfg = self._rendered()
        self.assertEqual(cfg["model"], "omniroute/l2-orchestrator")
        models = cfg["provider"]["omniroute"]["models"]
        self.assertEqual(set(models), {"l2-orchestrator"})
        self.assertEqual(models["l2-orchestrator"]["limit"]["context"], 1048576)
        self.assertEqual({n for n, e in cfg["mcp"].items() if e.get("enabled")},
                         {"autoos-agent"})
        self.assertEqual(cfg["permission"]["task"], "deny")
        self.assertEqual(cfg["permission"]["bash"], "allow")
        self.assertEqual(cfg["plugins"], [str(GUARD_DIR)])
        # the spawner MCP still gets its workers dir pinned (the 600 s `ps` hang)
        self.assertEqual(cfg["mcp"]["autoos-agent"]["environment"]["AUTOOS_WORKERS_DIR"],
                         str(self.proj / "logs" / "workers"))

    def test_guard_role_and_l1_inbox_reach_the_child(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["guard_role"], "orchestrator")
        self.assertEqual(rec["l1_inbox"], str(self.l1_inbox))

    def test_first_prompt_is_the_brief_plus_the_fixed_footer(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        pilot = [r for r in self.srv.requests
                 if r["method"] == "POST" and r["path"].endswith("/prompt")
                 and FAKE_SESSION_ID in r["path"]]
        self.assertEqual(len(pilot), 1)
        text = pilot[0]["body"]["text"]
        self.assertTrue(text.startswith(BRIEF.rstrip()), text[:80])
        for needle in ("`unattended-orchestration`", "NEVER edit code",
                       "autoos-agent", "tier-3", "REPORT", "DONE",
                       str(self.l1_inbox), HINT_EXPECTED):
            self.assertIn(needle, text, needle)
        # the L1 relaunch head is not part of an L2's first prompt
        self.assertNotIn("relaunched from the handoff", text)

    # (2) a phase lane that is live is refused, never re-prompted
    def test_duplicate_start_refused_and_prompts_nothing(self):
        _, rc = self._start()
        self.assertEqual(rc, 0)
        sessions = len([r for r in self.srv.requests
                        if r["method"] == "POST" and r["path"] == "/api/session"])
        prompts = len([r for r in self.srv.requests
                       if r["path"].endswith("/prompt")])
        before = len(self.srv.requests)
        with self.assertRaises(oc_l2.L2Error) as cm:
            self._start()
        self.assertIn("already running", str(cm.exception))
        self.assertIn(self._lane_name(), str(cm.exception))
        added = self.srv.requests[before:]
        self.assertEqual(len([r for r in self.srv.requests
                              if r["method"] == "POST" and r["path"] == "/api/session"]),
                         sessions, "the refused start created a session")
        self.assertEqual(len([r for r in self.srv.requests
                              if r["path"].endswith("/prompt")]),
                         prompts, "the refused start posted a prompt")
        self.assertTrue(all(r["method"] == "GET" for r in added),
                        "the duplicate check may only read: %r" % (added,))

    def test_missing_password_env_refused_before_anything(self):
        os.environ.pop(PW_ENV)
        with self.assertRaises(oc_l2.L2Error) as cm:
            self._start()
        self.assertIn(PW_ENV, str(cm.exception))
        self.assertFalse(self.rec.is_file())
        self.assertEqual(self.srv.requests, [])

    def test_missing_brief_and_bad_repo_refused(self):
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.cmd_start(self.proj, "p1", self.td / "gone.md",
                            opencode_bin=self.bin_, password_env=PW_ENV,
                            port=self.srv.port)
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.cmd_start(self.td / "no-such-dir", "p1", self.brief,
                            opencode_bin=self.bin_, password_env=PW_ENV,
                            port=self.srv.port)
        self.assertEqual(self.srv.requests, [])

    def test_unknown_combo_refused_before_spawn(self):
        with self.assertRaises(oc_l2.L2Error) as cm:
            self._start(combo="no-such-combo")
        self.assertIn("unknown combo", str(cm.exception))
        self.assertEqual(self.srv.requests, [])

    def test_no_l1_inbox_refused(self):
        os.environ.pop(oc_l2.ENV_L1_INBOX)
        with self.assertRaises(oc_l2.L2Error) as cm:
            self._start()
        self.assertIn(oc_l2.ENV_L1_INBOX, str(cm.exception))

    def test_config_and_result_carry_no_password_value(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        text = Path(result["config"]).read_text(encoding="utf-8")
        self.assertNotIn(PW_VALUE, text)
        self.assertNotIn(PW_VALUE, json.dumps(result))
        self.assertIn(PW_ENV, text)  # the NAME travels; the value never does
        if os.name != "nt":
            self.assertEqual(os.stat(result["config"]).st_mode & 0o777, 0o600)
        rendered = (self.td / "state" / self._lane_name() / "opencode.json")
        self.assertNotIn(PW_VALUE, rendered.read_text(encoding="utf-8"))

    def test_result_reports_lane_port_session_and_canary(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result["session_id"], FAKE_SESSION_ID)
        self.assertEqual(result["port"], self.srv.port)
        self.assertIs(result["canary"]["denied"], True)
        self.assertEqual(result["lane"], self._lane_name())
        self.assertEqual(result["phase"], "ao-deadrows")
        self.assertTrue(Path(result["prompt_file"]).is_file())
        self.assertTrue(Path(result["config"]).is_file())

    def test_canary_not_denied_is_rc5_and_never_prompts_the_pilot(self):
        bad = FakeServer(PW_VALUE, canary_mode="allowed")
        bad.start()
        mock.patch.object(oc_l1, "PORT_MIN", 1024).start()
        mock.patch.object(oc_l1, "PORT_MAX", 65535).start()
        try:
            result, rc = self._start(port=bad.port)
            self.assertEqual(rc, 5)
            self.assertIs(result["canary"]["denied"], False)
            self.assertIn("UNATTENDED-REFUSED", result["detail"])
            self.assertEqual([r for r in bad.requests
                              if FAKE_SESSION_ID in r["path"]
                              and r["path"].endswith("/prompt")], [])
            oc_l2.cmd_stop(result["lane"])
        finally:
            bad.stop()

    # (3) stop: the whole child tree goes, the state file goes, no orphan serve
    def test_stop_kills_the_child_tree(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        pid = result["pid"]
        state_file = Path(oc_l2.lane_dir(result["lane"])
                          / ("oc-l1-%s.state.json" % result["lane"]))
        self.assertTrue(state_file.is_file())
        out = oc_l2.cmd_stop(result["lane"])
        self.assertTrue(out["stopped"], out)
        self.assertFalse(pid_alive(pid), "the opencode server survived stop")
        self.assertFalse(state_file.is_file(),
                         "R-coord-10: an orphan server must not keep a state file")
        # a stop of a stopped lane is a no-op, not an error
        out2 = oc_l2.cmd_stop(result["lane"])
        self.assertTrue(out2["stopped"])

    def test_stop_refuses_a_pid_that_is_not_the_lane(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            name = self._lane_name()
            lane = oc_l2.build_lane(name, self.proj, "p1", self.brief,
                                    "l2-orchestrator", self.l1_inbox,
                                    opencode_bin=self.bin_,
                                    password_env=PW_ENV, port=self.srv.port)
            scratch = Path(lane["scratch_dir"])
            scratch.mkdir(parents=True, exist_ok=True)
            sf = Path(lane["state_file"])
            sf.write_text(json.dumps({"name": name, "session_id": "ses_x",
                                      "port": self.srv.port, "pid": sleeper.pid,
                                      "started_utc": oc_l2._now_ts()}),
                          encoding="utf-8")
            oc_l2.write_config(lane)
            out = oc_l2.cmd_stop(name)
            self.assertIsNone(sleeper.poll(), "stop killed a process that is not the lane")
            self.assertIs(out["stopped"], False)
            self.assertTrue(out["orphan"])
            self.assertTrue(sf.is_file(), "a refused stop must keep the state file")
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_stop_of_an_unknown_lane(self):
        out = oc_l2.cmd_stop("l2-nosuchlane-nophase")
        self.assertIs(out["stopped"], False)
        self.assertIn("no lane config", out["detail"])

    # (4) inbox: the line lands, the session is nudged
    def test_inbox_appends_a_parseable_record_and_nudges(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        out = oc_l2.cmd_inbox(result["lane"], "2026-10-08T00:00:00Z take the phase")
        self.assertTrue(out["nudged"], out)
        path = Path(out["inbox"])
        self.assertTrue(path.is_file())
        records, malformed = autoos_inbox.parse_file(str(path))
        self.assertEqual(malformed, [], "the appended line is not a valid record")
        self.assertEqual(len(records), 1)
        self.assertIn("[l1]", records[0].text)
        nudges = [r for r in self.srv.requests
                  if r["method"] == "POST" and FAKE_SESSION_ID in r["path"]
                  and r["path"].endswith("/prompt")]
        self.assertEqual(len(nudges), 2, "the session was not nudged exactly once")
        self.assertIn("take the phase", nudges[1]["body"]["text"])

    def test_inbox_uses_the_run_dir_inbox_when_set(self):
        os.environ[oc_l2.ENV_RUN_DIR] = str(self.td / "run")
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        out = oc_l2.cmd_inbox(result["lane"], "work")
        self.assertEqual(Path(out["inbox"]).parent, self.td / "run" / "inbox")
        self.assertEqual(Path(out["inbox"]).name, result["lane"] + ".md")

    def test_inbox_keeps_the_line_when_the_lane_is_not_live(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        oc_l2.cmd_stop(result["lane"])
        out = oc_l2.cmd_inbox(result["lane"], "queued while down")
        self.assertIs(out["nudged"], False)
        self.assertIn("queued while down", Path(out["inbox"]).read_text(encoding="utf-8"))

    def test_inbox_refuses_empty_text(self):
        with self.assertRaises(oc_l2.L2Error):
            oc_l2.cmd_inbox(self._lane_name(), "   ")

    # (5) status
    def test_status_verdicts(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        out, src = oc_l2.cmd_status(result["lane"])
        self.assertIn(out["verdict"], ("live", "silent"))
        self.assertEqual(src, out["exit_code"])
        self.assertEqual(out["phase"], "ao-deadrows")
        self.assertIs(out["canary"]["denied"], True)
        oc_l2.cmd_stop(result["lane"])
        out2, rc2 = oc_l2.cmd_status(result["lane"])
        self.assertEqual(out2["verdict"], "dead")
        self.assertEqual(rc2, 2)

    def test_status_of_an_unknown_lane_is_absent(self):
        out, rc = oc_l2.cmd_status("l2-nosuchlane-nophase")
        self.assertEqual(rc, 2)
        self.assertEqual(out["verdict"], "absent")

    # (6) the CLI prints one JSON object and forwards the exit code
    def test_cli_main_returns_codes(self):
        for argv, expected in ((["status", "--lane", "l2-nosuchlane-nophase"], 2),
                               (["start", "--repo", str(self.td / "nope"),
                                 "--phase", "p", "--brief", str(self.td / "nope.md")], 2)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = oc_l2.main(argv)
            self.assertEqual(rc, expected)
            payload = json.loads(buf.getvalue())  # exactly one JSON object
            self.assertTrue(payload.get("detail") or payload.get("error"), payload)


if __name__ == "__main__":
    unittest.main()
