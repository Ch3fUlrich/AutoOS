"""tests/test_oc_l1_serve.py - step A2 tests for oc_l1_serve.cmd_start.

unittest, stdlib only, NO real opencode: a fake v2 API server runs in a
thread on 127.0.0.1 (ephemeral port) and requires Basic auth; the lane's
opencode_bin is a tiny script that records its argv and the NAMES (not
values) of selected env vars to a JSON file, then sleeps.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_serve  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    FAKE_SESSION_ID,
    HINT_EXPECTED,
    PW_ENV,
    PW_VALUE,
    RECORD_ENV,
    FakeServer,
    make_lane,
    pid_alive,
    wait_file,
    write_cfg,
)


class StartTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_serve_test_")
        self.td = Path(self._td.name)
        self.srv = FakeServer(PW_VALUE)
        self.srv.start()
        self.lane = make_lane(self.td, self.srv.port)
        self.cfg = write_cfg(self.td, self.lane)
        self.rec = self.td / "bin_record.json"
        self._pids = []
        self._envs = {}
        for k in (PW_ENV, RECORD_ENV):
            self._envs[k] = os.environ.get(k)
            os.environ[k] = PW_VALUE if k == PW_ENV else str(self.rec)

    def tearDown(self):
        for pid in self._pids:
            oc_l1_serve._kill_pid(pid)
        self.srv.stop()
        for k, v in self._envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def _args(self):
        return SimpleNamespace(name="l1test", config=str(self.cfg))

    def _start(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = oc_l1_serve.cmd_start(self.lane, self._args())
        if rc == 0:
            st = oc_l1_serve._read_state(self.lane["state_file"])
            self._pids.append(st["pid"])
        return rc, out.getvalue()

    # (1) password env var refusal
    def test_start_without_password_refused(self):
        os.environ.pop(PW_ENV, None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = oc_l1_serve.cmd_start(self.lane, self._args())
        self.assertEqual(rc, 2)
        self.assertIn(PW_ENV, out.getvalue())
        self.assertFalse(self.rec.is_file(), "child must not be started")
        self.assertFalse(Path(self.lane["state_file"]).is_file())
        self.assertEqual(self.srv.requests, [], "no request may be made")

    # (1b) D-665 fix 3: a lane with no plugin carries no bash-guard, so its
    #      canary can never be denied — refusing to start is the only honest
    #      answer (before it, such a lane burned a real model call and exited 5
    #      every cycle forever).
    def test_start_without_plugins_refused_rc2_no_spawn(self):
        self.lane["plugins"] = []
        rc, out = self._start()
        self.assertEqual(rc, 2, out)
        self.assertIn("plugins", out)
        self.assertFalse(self.rec.is_file(), "no child may be spawned")
        self.assertFalse(Path(self.lane["state_file"]).is_file())
        self.assertEqual(self.srv.requests, [], "no request may be made")
        self.assertNotIn("UNATTENDED-REFUSED", out,
                         "a config defect is not a canary refusal (rc 5)")

    # (2) happy path
    def test_start_happy_path(self):
        rc, out = self._start()
        self.assertEqual(rc, 0)
        self.assertIn(
            "started l1test session %s port %d" % (FAKE_SESSION_ID,
                                                   self.srv.port),
            out,
        )
        posts = [r for r in self.srv.requests
                 if r["method"] == "POST" and r["path"] == "/api/session"]
        self.assertEqual(len(posts), 2)
        body = posts[0]["body"]
        self.assertEqual(body["title"], "l1test")
        self.assertEqual(body["location"], {"directory": self.lane["cwd"]})
        self.assertEqual(body["model"], {
            "providerID": "omniroute",
            "id": "placeholderprovider/PlaceholderModel",
        })
        prompts = [r for r in self.srv.requests
                   if r["method"] == "POST" and r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 2)
        # order: the canary session is prompted FIRST; the pilot only after a denied canary
        self.assertIn(self.srv.canary_session_id, prompts[0]["path"])
        self.assertIn(FAKE_SESSION_ID, prompts[1]["path"])
        text = prompts[1]["body"]["text"]
        head = "You are l1test, relaunched from the handoff. Read %s " \
               "first, then continue.\n\n" % self.lane["handoff"]
        self.assertTrue(text.startswith(head), text[:120])
        self.assertIn("line-40", text)
        self.assertNotIn("line-41", text)
        self.assertIn(HINT_EXPECTED, text)
        for r in self.srv.requests:
            self.assertTrue(r["auth_ok"], "request without Basic auth: %r" % r)
        st = json.loads(Path(self.lane["state_file"]).read_text())
        self.assertEqual(set(st), {"name", "session_id", "port", "pid",
                                   "started_utc", "canary", "prompted"})
        self.assertIs(st["prompted"], True)
        self.assertEqual(st["name"], "l1test")
        self.assertEqual(st["session_id"], FAKE_SESSION_ID)
        self.assertEqual(st["port"], self.srv.port)
        if os.name != "nt":
            mode = os.stat(self.lane["state_file"]).st_mode & 0o777
            self.assertEqual(mode, 0o600, "state file mode %o" % mode)
        for d in (self.td / "scratch", self.td / "state"):
            self.assertEqual(list(d.glob("*.tmp")), [])
        self.assertTrue((self.td / "scratch" / "opencode.json").is_file())

    # (3) argv: localhost bind, password never on the command line,
    #     child env carries OPENCODE_SERVER_PASSWORD (name check)
    def test_start_argv_and_child_env(self):
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["argv"][:5],
                         ["serve", "--hostname", "127.0.0.1",
                          "--port", str(self.srv.port)])
        self.assertNotIn("0.0.0.0", rec["argv"])
        self.assertNotIn(PW_VALUE, " ".join(rec["argv"]))
        state_text = Path(self.lane["state_file"]).read_text()
        self.assertNotIn(PW_VALUE, state_text)
        for name in ("OPENCODE_SERVER_PASSWORD", "OPENCODE_CONFIG",
                     "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                     "XDG_CACHE_HOME"):
            self.assertIn(name, rec["env_names"], "child env missing %s" % name)

    # (4) second start is idempotent
    def test_start_idempotent(self):
        rc1, _ = self._start()
        self.assertEqual(rc1, 0)
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0)
        self.assertIn("already live", out2)
        posts = [r for r in self.srv.requests
                 if r["method"] == "POST" and r["path"] == "/api/session"]
        prompts = [r for r in self.srv.requests
                   if r["path"].endswith("/prompt")]
        # 2 = pilot session + its canary session; a SECOND start must add
        # neither a session nor a prompt.
        self.assertEqual(
            len(posts), 2,
            "second start created a new session (expected pilot + canary)")
        self.assertEqual(
            len(prompts), 2,
            "second start posted a prompt (expected pilot + canary)")

    # (4b) canary NOT denied: the pilot session never receives its first prompt (exit 5, server left up, pilot idle)
    def _start_not_denied(self):
        bad = FakeServer(PW_VALUE, canary_mode="allowed")
        bad.start()
        self.lane["serve_port"] = bad.port
        self.cfg = write_cfg(self.td, self.lane)
        return bad

    def test_canary_not_denied_never_prompts_the_pilot(self):
        bad = self._start_not_denied()
        try:
            rc, out = self._start()
            self.assertEqual(rc, 5, out)
            self.assertIn("UNATTENDED-REFUSED", out)
            pilot_prompts = [r for r in bad.requests
                             if r["path"] == "/api/session/%s/prompt" % FAKE_SESSION_ID]
            self.assertEqual(pilot_prompts, [], "the pilot was prompted although the canary was not denied")
            st = json.loads(Path(self.lane["state_file"]).read_text(encoding="utf-8"))
            self.assertIs(st["prompted"], False)
            self.assertIs(st["canary"]["denied"], False)
            self._pids.append(st["pid"])
            # a SECOND start must not turn the refusal into "already live" / exit 0, and still must not prompt
            rc2, out2 = self._start()
            self.assertEqual(rc2, 5, out2)
            self.assertIn("UNATTENDED-REFUSED", out2)
            self.assertEqual([r for r in bad.requests
                              if r["path"] == "/api/session/%s/prompt" % FAKE_SESSION_ID], [])
        finally:
            bad.stop()

    # (5) a corrupt state file is treated as no state: a fresh start rewrites it valid
    def test_start_with_corrupt_state_file_starts_fresh(self):
        sf = Path(self.lane["state_file"])
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text('{"name": "l1test", "session_id": ', encoding="utf-8")
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        st = json.loads(sf.read_text(encoding="utf-8"))
        self.assertEqual(st["session_id"], FAKE_SESSION_ID)

    # (5b) the launcher kills only the child it spawned: a PID written in a stale state file is never signalled
    def test_stale_state_pid_is_never_killed(self):
        import subprocess
        dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            sf = Path(self.lane["state_file"])
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(json.dumps({"name": "l1test", "session_id": "ses_gone", "port": 1,
                                      "pid": dummy.pid, "started_utc": "2026-01-01T00:00:00Z"}),
                          encoding="utf-8")
            rc, out = self._start()
            self.assertEqual(rc, 0, out)
            time.sleep(0.5)
            self.assertIsNone(dummy.poll(), "the PID of a stale state file was signalled")
        finally:
            dummy.kill()
            dummy.wait()

    # (6) health timeout: 503 -> exit 4, child killed, no state file
    def test_health_timeout_kills_child(self):
        bad = FakeServer(PW_VALUE, active_503=True)
        bad.start()
        try:
            self.lane["scratch_dir"] = str(self.td / "scratch2")
            self.lane["state_file"] = str(self.td / "state" / "h.state.json")
            self.lane["serve_port"] = bad.port
            self.lane["health_timeout_s"] = 2
            self.cfg = write_cfg(self.td, self.lane)
            rc, out = self._start()
        finally:
            bad.stop()
        self.assertEqual(rc, 4)
        self.assertIn("not healthy", out)
        self.assertIn("killed", out)
        self.assertFalse(Path(self.lane["state_file"]).is_file())
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        state_pid = rec.get("pid")
        self.assertTrue(state_pid is not None)
        time.sleep(0.5)
        self.assertFalse(pid_alive(state_pid), "child %s still alive" % state_pid)


if __name__ == "__main__":
    unittest.main()
