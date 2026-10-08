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
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_serve  # noqa: E402
import oc_l2  # noqa: E402
import autoos_inbox  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    FAKE_PY,
    FAKE_SESSION_ID,
    FAKE_STDERR_NOTE,
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
# a stand-in for "somebody else's process", long enough to outlive the test body
_SLEEP_CODE = "import time; time.sleep(120)"
# ... and one that has a child of its own, so a group kill is observable: the
# child must survive a stop of the parent it is grouped with.
_PARENT_WITH_CHILD = (
    "import subprocess, sys, time; "
    "kid = subprocess.Popen(['sleep', '120']); "
    "open(sys.argv[1], 'w').write(str(kid.pid)); "
    "time.sleep(120)"
)

# The environment name the forking fake writes its grandchild's PID to. Like the
# record path, the launcher's child env is an allowlist, so a lane that wants the
# fake to report a child must declare this name.
CHILD_FILE_ENV = "OC_L1_FAKE_CHILD_FILE"
# AO-L2-LAUNCH criterion b: a fake `opencode serve` that FORKS a grandchild which
# IGNORES SIGTERM. The recorded PID is a session/group leader (the launcher spawns
# it with start_new_session), so a stop that only SIGTERMs the group kills the
# leader but NOT this grandchild - the tree survives unless the stop escalates a
# SIGKILL to the whole group. The grandchild is deliberately stubborn precisely so
# the test fails if the stop signals a lone PID instead of the group.
_FORK_SERVE_PY = """import json, os, sys, subprocess, time
rec = os.environ["%s"]
with open(rec, "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "env_names": sorted(os.environ),
               "pid": os.getpid(),
               "guard_role": os.environ.get("AUTOOS_GUARD_ROLE"),
               "l1_inbox": os.environ.get("AUTOOS_L1_INBOX"),
               "agent_layer": os.environ.get("AUTOOS_AGENT_LAYER")}, f)
sys.stderr.write("%s\\n"); sys.stderr.flush()
kid = subprocess.Popen([sys.executable, "-c",
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "time.sleep(300)"])
with open(os.environ["%s"], "w", encoding="utf-8") as f:
    f.write(str(kid.pid))
time.sleep(300)
""" % (RECORD_ENV, FAKE_STDERR_NOTE, CHILD_FILE_ENV)


def _is_alive(pid):
    """Is this PID still running? A grandchild is not this test's child, so it
    is never a zombie of ours."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True



class NameTest(unittest.TestCase):
    def _tag(self, repo):
        """The expected 6-hex identity: the digest of the same path the name is
        built from, computed here rather than called through the module so the
        test pins the SHAPE, not a re-run of the implementation."""
        import hashlib
        digest = hashlib.sha256(
            oc_l2.slug(os.path.abspath(str(repo))).encode("utf-8")).hexdigest()
        return digest[:6]

    def test_lane_name_is_l2_repo_hash_phase(self):
        repo = "/srv/checkouts/AutoOS"
        self.assertEqual(oc_l2.lane_name(repo, "AO-DEADROWS"),
                         "l2-autoos-%s-ao-deadrows" % self._tag(repo))

    def test_the_name_separates_two_checkouts_of_the_same_repo_name(self):
        # finding 7: two worktrees of two different projects can share a
        # directory name; a lane per phase is one lane per CHECKOUT, or the
        # second project's start silently joins the first project's session.
        a = oc_l2.lane_name("/home/u/one/autoos", "p1")
        b = oc_l2.lane_name("/home/u/two/autoos", "p1")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("l2-autoos-"), a)
        self.assertTrue(b.startswith("l2-autoos-"), b)

    def test_the_same_project_spelled_two_ways_is_one_lane(self):
        self.assertEqual(oc_l2.lane_name("/a/autoos-ci", "p1"),
                         oc_l2.lane_name("/a/AutoOS CI", "p1"))
        self.assertEqual(oc_l2.lane_name("./autoos", "p1"),
                         oc_l2.lane_name(os.path.abspath("autoos"), "p1"))

    def test_lane_name_stays_within_the_lane_shape(self):
        # the repo part gives way; the phase and the identity tag never do,
        # because one lane per phase per checkout is the unit that has to stay
        # distinct
        name = oc_l2.lane_name("a" * 40, "phase-one")
        self.assertRegex(name, r"^[a-z0-9][a-z0-9-]{0,31}$")
        self.assertTrue(name.endswith("-phase-one"), name)
        self.assertIn("-%s-" % self._tag("a" * 40), name)
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
                  password_env=PW_ENV, port=self.srv.port,
                  # the fake binary reads its record path from the environment,
                  # and the lane child env is an allowlist (REJECT finding 2), so
                  # the lane has to declare that name like any real extra would
                  child_env=[RECORD_ENV])
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
        # Sonnet final REJECT 2026-10-08 finding 1: `permission.task` alone left
        # the renderer's `edit: allow` standing, so an L2 could write files with
        # its own editor tools and never spawn. Every file-mutating key the
        # opencode schema declares is denied here; read and the guarded shell
        # stay allowed because the L2 still has to inspect and run checks.
        self.assertEqual(cfg["permission"], {
            "bash": "allow", "edit": "deny", "read": "allow",
            "autoos-agent_*": "allow",
            "write": "deny", "patch": "deny", "apply_patch": "deny",
            "task": "deny", "subagent": "deny",
        })
        self.assertEqual(cfg["permission"], dict(oc_l2.PERMISSION_DEFAULTS,
                                                 **oc_l2.L2_PERMISSIONS))
        self.assertEqual(cfg["plugins"], [str(GUARD_DIR)])
        # the spawner MCP still gets its workers dir pinned (the 600 s `ps` hang)
        self.assertEqual(cfg["mcp"]["autoos-agent"]["environment"]["AUTOOS_WORKERS_DIR"],
                         str(self.proj / "logs" / "workers"))
        # AO-L2-LAUNCH merge criterion 2: the spawner an L2 starts is told which
        # tool profile to register, in its own environment.
        self.assertEqual(cfg["mcp"]["autoos-agent"]["environment"]["AUTOOS_AGENT_LAYER"],
                         "L2")

    def test_guard_role_and_l1_inbox_reach_the_child(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        # AO-L2-LAUNCH merge criterion 1: an L2 is not merely an orchestrator
        # whose writes stay inside .oc-pilot/ - it has NO writable scope, so it
        # renders the guard's read-only `l2` role.
        self.assertEqual(rec["guard_role"], "l2")
        self.assertEqual(oc_l2.read_config(self._lane_name())["guard_role"], "l2")
        self.assertEqual(rec["l1_inbox"], str(self.l1_inbox))

    def test_the_rendered_role_is_a_role_the_plugin_dispatches_on(self):
        # oc_l2's `guard_role` and the plugin's role dispatch are one contract:
        # a lane that renders a role nobody implements is an unguarded lane that
        # believes itself guarded, because the canary only proves the guard
        # denied SOMETHING. Pinned by reading both sides, not by a shared const.
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        role = oc_l2.read_config(self._lane_name())["guard_role"]
        src = (GUARD_DIR / "index.mjs").read_text(encoding="utf-8")
        self.assertEqual(role, "l2")
        self.assertIn("process.env.AUTOOS_GUARD_ROLE", src)
        self.assertIn('role === "%s"' % role, src)

    # Sonnet final REJECT 2026-10-08 finding 2: the same MCP server answers an
    # L1 and an L2, so an L2 could start, stop and nudge lanes - i.e. relaunch
    # its own supervisor or dead-man-switch another phase. The lane marks its
    # layer in its own environment and the MCP refuses on reading it; the mark
    # has to come from the lane, because a marker a process inherits from some
    # unrelated shell is a lie in both directions.
    def test_the_lane_marks_its_layer_for_the_mcp_fence(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["agent_layer"], "L2")
        self.assertEqual(oc_l2.read_config(self._lane_name())["agent_layer"], "L2")

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
        # AO-L2-LAUNCH criterion b: the report is an MCP tool the L2 calls
        # directly, no longer a tier-3 spawn whose whole task was to append the
        # line - so the footer names `l2_report` and drops the old indirection.
        self.assertIn("l2_report", text)
        self.assertNotIn("the report is a tier-3 spawn", text)
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

    def test_already_running_names_an_unverified_lane_as_one(self):
        # finding 7: a duplicate start of an rc5 lane used to be told "it is
        # already running, send it work with l2_inbox" - but that lane has no
        # running phase: its pilot was never prompted and the inbox refuses the
        # nudge. The message says what the lane is and what clears it.
        bad = FakeServer(PW_VALUE, canary_mode="allowed")
        bad.start()
        mock.patch.object(oc_l1, "PORT_MIN", 1024).start()
        mock.patch.object(oc_l1, "PORT_MAX", 65535).start()
        try:
            result, rc = self._start(port=bad.port)
            self.assertEqual(rc, 5)
            with self.assertRaises(oc_l2.L2Error) as cm:
                self._start(port=bad.port)
            msg = str(cm.exception)
            self.assertIn(result["lane"], msg)
            self.assertIn("canary", msg)
            self.assertNotIn("send it work with l2_inbox", msg)
            self.assertIn("l2_stop", msg)
            oc_l2.cmd_stop(result["lane"])
        finally:
            bad.stop()

    def test_already_running_of_a_guarded_lane_points_at_the_inbox(self):
        _, rc = self._start()
        self.assertEqual(rc, 0)
        with self.assertRaises(oc_l2.L2Error) as cm:
            self._start()
        self.assertIn("already running", str(cm.exception))
        self.assertIn("l2_inbox", str(cm.exception))

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

    @unittest.skipIf(os.name == "nt", "process groups")
    def test_stop_kills_a_forked_grandchild_that_ignores_sigterm(self):
        # AO-L2-LAUNCH criterion b: the recorded PID is a group leader, but the
        # lane's child tree outlives it if the stop only signals the leader or
        # only SIGTERMs the group. This fake forks a grandchild that IGNORES
        # SIGTERM, so the group survives TERM - only a SIGKILL to the whole
        # process group brings it down. A stop that must "leave no process in the
        # group" has to escalate to the group, not the lone PID.
        self.bin_ = make_fake_bin(self.td, self.td / "fake_fork.py", sys.executable)
        (self.td / "fake_fork.py").write_text(_FORK_SERVE_PY, encoding="utf-8")
        child_file = self.td / "child_pid.txt"
        self._envs[CHILD_FILE_ENV] = os.environ.get(CHILD_FILE_ENV)
        os.environ[CHILD_FILE_ENV] = str(child_file)
        result, rc = self._start(opencode_bin=self.bin_,
                                 child_env=[RECORD_ENV, CHILD_FILE_ENV])
        self.assertEqual(rc, 0, result)
        pid = result["pid"]
        self.addCleanup(self._hard_kill_group, pid)

        wait_file(child_file, seconds=10)
        kid = int(child_file.read_text(encoding="utf-8").strip())
        self.addCleanup(self._hard_kill_pid, kid)
        # the grandchild is genuinely a MEMBER of the recorded lane's process
        # group - otherwise a group kill could not be expected to reach it
        self.assertEqual(oc_l2._pgid(pid), pid,
                         "the recorded pid is not a group leader (start_new_session)")
        self.assertEqual(oc_l2._pgid(kid), pid,
                         "the fake's child is not in the lane's process group")
        self.assertTrue(pid_alive(kid), "grandchild never came up")

        out = oc_l2.cmd_stop(result["lane"])
        self.assertTrue(out["stopped"], out)
        self.assertFalse(pid_alive(pid), "the lane server survived stop")
        # the whole point: a stubborn grandchild the leader cannot drag down with
        # a TERM alone is killed by the group SIGKILL, and the stop says so
        self.assertTrue(self._wait_until_gone(kid),
                        "criterion b: l2_stop left a process in the group")

    def _wait_until_gone(self, pid, seconds=6.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.1)
        return not pid_alive(pid)

    def _hard_kill_group(self, pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _hard_kill_pid(self, pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _lane_with_foreign_pid(self, name, pid, argv=None, start_time="unset"):
        """A lane whose state file names a PID stop must not accept.

        `argv` and `start_time` are what the launcher records for its own child;
        `start_time="unset"` keeps the pre-fix shape (no such key at all),
        "match" reads the live PID's real value, anything else is written as-is.
        """
        lane = oc_l2.build_lane(name, self.proj, "p1", self.brief,
                                "l2-orchestrator", self.l1_inbox,
                                opencode_bin=self.bin_,
                                password_env=PW_ENV, port=self.srv.port)
        Path(lane["scratch_dir"]).mkdir(parents=True, exist_ok=True)
        st = {"name": name, "session_id": "ses_x", "port": self.srv.port,
              "pid": pid, "started_utc": oc_l2._now_ts()}
        if argv is not None:
            st["argv"] = argv
        if start_time == "match":
            st["start_time"] = oc_l1_serve.proc_starttime(pid)
        elif start_time != "unset":
            st["start_time"] = start_time
        Path(lane["state_file"]).write_text(json.dumps(st), encoding="utf-8")
        oc_l2.write_config(lane)
        return lane

    def test_stop_refuses_a_pid_that_is_not_the_lane(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            name = self._lane_name()
            lane = self._lane_with_foreign_pid(name, sleeper.pid)
            sf = Path(lane["state_file"])
            out = oc_l2.cmd_stop(name)
            self.assertIsNone(sleeper.poll(), "stop killed a process that is not the lane")
            self.assertIs(out["stopped"], False)
            self.assertTrue(out["orphan"])
            self.assertIn("argv", out["detail"],
                          "a state file with no recorded command must be refused, "
                          "naming the missing record as the reason")
            self.assertTrue(sf.is_file(), "a refused stop must keep the state file")
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_stop_refuses_a_recycled_pid_that_only_mentions_the_word(self):
        # Identity is the whole recorded command line, not a token that happens
        # to contain the binary's name: a recycled PID running a grep, a tail or
        # an editor over such a path is not the lane, and signalling its process
        # group would be the kill-by-name this tool forbids.
        argv = [self.bin_, "serve", "--hostname", "127.0.0.1",
                "--port", str(self.srv.port)]
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                    "--pattern=fake_opencode serve"])
        try:
            name = self._lane_name()
            lane = self._lane_with_foreign_pid(name, sleeper.pid, argv=argv,
                                               start_time="match")
            out = oc_l2.cmd_stop(name)
            self.assertIsNone(sleeper.poll(), "stop killed a recycled PID")
            self.assertIs(out["stopped"], False)
            self.assertIn("command", out["detail"])
            self.assertTrue(Path(lane["state_file"]).is_file())
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_stop_of_an_unknown_lane(self):
        out = oc_l2.cmd_stop("l2-nosuchlane-nophase")
        self.assertIs(out["stopped"], False)
        self.assertIn("no lane config", out["detail"])

    # (3b) Sonnet final REJECT 2026-10-08 finding 4: identity is the WHOLE
    #      recorded command line plus the kernel start time of that exact PID,
    #      and the process GROUP is only signalled when the recorded PID leads
    #      it - which only a lane this launcher started with start_new_session
    #      ever does.
    def test_stop_refuses_a_recycled_pid_that_replays_the_lane_argv(self):
        # A recycled PID can inherit the dead lane's number AND, if the new
        # owner is another copy of the same wrapper, its command line. The
        # starttime is the one fact a recycler cannot fake.
        argv = [self.bin_, "serve", "--hostname", "127.0.0.1",
                "--port", str(self.srv.port)]
        victim = subprocess.Popen([sys.executable, "-c", _SLEEP_CODE] + argv[1:])
        try:
            name = self._lane_name()
            lane = self._lane_with_foreign_pid(name, victim.pid, argv=argv,
                                               start_time=oc_l1_serve.proc_starttime(victim.pid) + 1)
            out = oc_l2.cmd_stop(name)
            self.assertIsNone(victim.poll(), "stop killed a recycled PID")
            self.assertIs(out["stopped"], False)
            self.assertTrue(out["orphan"])
            self.assertIn("start", out["detail"].lower())
            self.assertTrue(Path(lane["state_file"]).is_file())
        finally:
            victim.kill()
            victim.wait()

    @unittest.skipIf(os.name == "nt", "process groups")
    def test_stop_never_signals_a_group_it_did_not_create(self):
        argv = [self.bin_, "serve", "--hostname", "127.0.0.1",
                "--port", str(self.srv.port)]
        pid_file = self.td / "kid.pid"
        # a lane-shaped command line, but started by the test process: it is in
        # the TEST's group, so killpg() here would signal this very suite and
        # every sibling process - the exact bug the fix closes.
        victim = subprocess.Popen([sys.executable, "-c", _PARENT_WITH_CHILD,
                                   str(pid_file)] + argv[1:])
        kid = None
        calls = []
        try:
            deadline = time.time() + 10
            while not pid_file.is_file() and time.time() < deadline:
                time.sleep(0.1)
            kid = int(pid_file.read_text(encoding="utf-8"))
            name = self._lane_name()
            self._lane_with_foreign_pid(name, victim.pid, argv=argv,
                                        start_time="match")
            # spy, not proxy: running the pre-fix code here must not take the
            # whole suite down with it - the group in question is the suite's own.
            with mock.patch.object(os, "killpg",
                                   side_effect=lambda pgid, sig: calls.append((pgid, sig))):
                out = oc_l2.cmd_stop(name)
            self.assertEqual(calls, [],
                             "stop signalled a process group it did not create")
            self.assertTrue(out["stopped"], out)
            self.assertEqual(out["killed_pids"], [victim.pid],
                             "the group was signalled, not just the recorded PID")
            self.assertFalse(pid_alive(victim.pid))
            self.assertTrue(_is_alive(kid),
                            "an unrelated process in the caller's group died")
        finally:
            victim.kill()
            victim.wait()
            if kid:
                try:
                    os.kill(kid, signal.SIGKILL)
                except OSError:
                    pass  # already gone with its group in the pre-fix code

    # (4) inbox: the line lands, the session is nudged
    def test_inbox_appends_a_parseable_record_and_nudges(self):
        result, rc = self._start()
        self.assertEqual(rc, 0, result)
        out = oc_l2.cmd_inbox(result["lane"], "2026-10-08T00:00:00Z take the phase")
        self.assertTrue(out["nudged"], out)
        self.assertIs(out["refused"], False)
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

    def test_inbox_refuses_to_nudge_a_lane_that_never_cleared_the_canary(self):
        # Sonnet final REJECT finding 3: an rc5 start leaves a server whose
        # session answers for a live lane, but no guard denial was seen and the
        # pilot was never prompted - nudging it would hand work to an unverified
        # session as though it were running.
        bad = FakeServer(PW_VALUE, canary_mode="allowed")
        bad.start()
        mock.patch.object(oc_l1, "PORT_MIN", 1024).start()
        mock.patch.object(oc_l1, "PORT_MAX", 65535).start()
        try:
            result, rc = self._start(port=bad.port)
            self.assertEqual(rc, 5)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli_rc = oc_l2.main(["inbox", "--lane", result["lane"],
                                    "--text", "work for a guarded lane"])
            self.assertEqual(cli_rc, 2,
                             "a caller that only reads the exit code must see the refusal")
            out = json.loads(buf.getvalue())
            self.assertIs(out["refused"], True, out)
            self.assertIs(out["nudged"], False)
            self.assertIn("canary", out["detail"])
            self.assertIn("work for a guarded lane",
                          Path(out["inbox"]).read_text(encoding="utf-8"),
                          "the inbox is the durable half of the contract")
            self.assertEqual([r for r in bad.requests
                              if FAKE_SESSION_ID in r["path"]
                              and r["path"].endswith("/prompt")], [],
                             "an unverified session was nudged")
            oc_l2.cmd_stop(result["lane"])
        finally:
            bad.stop()

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


class McpToolTest(unittest.TestCase):
    """tools/autoos_agent_mcp.py's four wrappers: they hand the lane over as
    ARGV, give the child only the allowlisted environment, and return the
    CLI's JSON as the answer - the server itself renders no lane."""

    def setUp(self):
        import autoos_agent_mcp as mcp
        self.mcp = mcp

    def _call(self, fn, *args, **env):
        seen = {}

        class Res:
            returncode = 0
            stdout = json.dumps({"lane": "l2-proj-p1", "exit_code": 0})
            stderr = ""

        def fake_run(argv, **kw):
            seen["argv"] = argv
            seen["kw"] = kw
            return Res()

        saved = {k: os.environ.get(k) for k in env}
        for k, v in env.items():
            os.environ[k] = v
        try:
            with mock.patch.object(self.mcp.subprocess, "run", fake_run):
                out = fn(*args)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return out, seen

    def test_l2_start_argv_and_allowlisted_env(self):
        out, seen = self._call(self.mcp.l2_start, "/srv/proj", "p1", "/srv/brief.md",
                               **{"AUTOOS_OCL1_PW": "sk-NOT-A-REAL-VALUE-000",
                                  "AUTOOS_OMNIROUTE_URL": "https://gateway.invalid",
                                  "AUTOOS_OMNIROUTE_KEY": "sk-NOT-A-REAL-VALUE-000"})
        self.assertEqual(out["lane"], "l2-proj-p1")
        self.assertEqual(seen["argv"][2:], ["start", "--repo", "/srv/proj",
                                            "--phase", "p1", "--brief", "/srv/brief.md",
                                            "--combo", "l2-orchestrator"])
        self.assertEqual(seen["argv"][1], os.path.join(self.mcp.TOOLS_DIR, "oc_l2.py"))
        self.assertIs(seen["kw"]["stdin"], subprocess.DEVNULL)
        env = seen["kw"]["env"]
        self.assertEqual(env["AUTOOS_OCL1_PW"], "sk-NOT-A-REAL-VALUE-000")
        # an allowlist, not the caller's whole environment
        self.assertNotIn("GH_TOKEN", env)
        # REJECT finding 2: the lane child inherits the LAUNCHER's environment,
        # and the rendered config references both gateway names as {env:...}.
        # Forwarding only the URL starts a lane whose model call has no key.
        self.assertEqual(env["AUTOOS_OMNIROUTE_URL"], "https://gateway.invalid")
        self.assertEqual(env["AUTOOS_OMNIROUTE_KEY"], "sk-NOT-A-REAL-VALUE-000")
        self.assertEqual(seen["kw"]["timeout"], self.mcp._L2_TIMEOUT_S["start"])

    def test_the_other_three_tools_pass_the_lane(self):
        for fn, args, expected in (
                (self.mcp.l2_status, ("l2-proj-p1",), ["status", "--lane", "l2-proj-p1"]),
                (self.mcp.l2_stop, ("l2-proj-p1",), ["stop", "--lane", "l2-proj-p1"]),
                (self.mcp.l2_inbox, ("l2-proj-p1", "take it"),
                 ["inbox", "--lane", "l2-proj-p1", "--text", "take it"])):
            _, seen = self._call(fn, *args)
            self.assertEqual(seen["argv"][2:], expected)
            self.assertEqual(seen["kw"]["timeout"],
                             self.mcp._L2_TIMEOUT_S[expected[0]])

    def test_a_launcher_that_prints_nothing_still_answers(self):
        class Res:
            returncode = 1
            stdout = ""
            stderr = "Traceback: boom"

        with mock.patch.object(self.mcp.subprocess, "run", lambda *a, **k: Res()):
            out = self.mcp.l2_status("l2-proj-p1")
        self.assertIs(out["ok"], False)
        self.assertEqual(out["exit_code"], 1)
        self.assertIn("boom", out["detail"])

    def test_the_password_value_never_travels_in_argv(self):
        _, seen = self._call(self.mcp.l2_inbox, "l2-proj-p1", "work",
                             **{"AUTOOS_OCL1_PW": "sk-NOT-A-REAL-VALUE-000"})
        self.assertNotIn("sk-NOT-A-REAL-VALUE-000", " ".join(seen["argv"]))

    # REJECT finding 2: the five lane-CONTROL tools. Their refusal is the whole
    # point, so the launcher subprocess must never be reached at all - a spy that
    # raises proves the code path was not taken.
    def test_lane_control_is_refused_from_inside_an_l2(self):
        def boom(*a, **k):
            raise AssertionError("a refused tool must run no subprocess: %r" % (a,))

        calls = ((self.mcp.l2_start, ("/srv/proj", "p1", "/srv/brief.md")),
                 (self.mcp.l2_stop, ("l2-proj-p1",)),
                 (self.mcp.l2_inbox, ("l2-proj-p1", "take it")),
                 (self.mcp.oc_start, ("l1-pilot",)),
                 (self.mcp.oc_restart, ("l1-pilot",)))
        self.assertEqual({fn.__name__ for fn, _ in calls},
                         set(self.mcp.LANE_CONTROL_TOOLS),
                         "the fenced set and the tools tested must be one list")
        with mock.patch.object(self.mcp.subprocess, "run", boom), \
                mock.patch.dict(os.environ, {self.mcp.ENV_AGENT_LAYER: "L2"}):
            for fn, args in calls:
                out = fn(*args)
                self.assertIs(out["refused"], True, fn.__name__)
                self.assertIs(out["ok"], False, fn.__name__)
                self.assertIn("L2", out["detail"], fn.__name__)
                self.assertIn("spawn", out["detail"],
                              "the refusal must say what an L2 may still do")

    def test_reading_a_lane_is_still_allowed_from_inside_an_l2(self):
        # the fence is on CONTROL, not on reading: this same code answers an L1,
        # which must be able to read any lane. What an L2's own server does with
        # a read tool is a separate question - criterion 2 hides it from the
        # list entirely (tests/test_agent_mcp_tool_profile.py).
        with mock.patch.dict(os.environ, {self.mcp.ENV_AGENT_LAYER: "L2"}):
            out, seen = self._call(self.mcp.l2_status, "l2-proj-p1")
        self.assertEqual(seen["argv"][2:], ["status", "--lane", "l2-proj-p1"])
        self.assertNotIn("refused", out)
        with mock.patch.dict(os.environ, {self.mcp.ENV_AGENT_LAYER: "L2"}):
            out, seen = self._call(self.mcp.oc_status, "l1-pilot")
        self.assertEqual(seen["argv"][2:], ["status", "--name", "l1-pilot"])
        self.assertNotIn("refused", out)

    def test_the_fence_is_off_for_a_server_that_is_not_in_a_lane(self):
        # the L1's own MCP: same code, no marker in its environment
        self.assertNotIn(self.mcp.ENV_AGENT_LAYER, os.environ)
        _, seen = self._call(self.mcp.l2_stop, "l2-proj-p1")
        self.assertEqual(seen["argv"][2:], ["stop", "--lane", "l2-proj-p1"])
        _, seen = self._call(self.mcp.oc_start, "l1-pilot")
        self.assertEqual(seen["argv"][2:], ["start", "--name", "l1-pilot"])


if __name__ == "__main__":
    unittest.main()
