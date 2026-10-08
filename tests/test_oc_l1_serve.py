"""tests/test_oc_l1_serve.py - step A2 tests for oc_l1_serve.cmd_start.

unittest, stdlib only, NO real opencode: a fake v2 API server runs in a
thread on 127.0.0.1 (ephemeral port) and requires Basic auth; the lane's
opencode_bin is a tiny script that records its argv and the NAMES (not
values) of selected env vars to a JSON file, then sleeps.

TestPrivateScratchModes covers F4: the scratch tree is 0700 and the child's
stderr log 0600, both modes applied at creation, with no chmod-after-open
window and a permissive umask unable to loosen them.

TestScrubCredentialShapes covers F5: _scrub removes the bare password, the
base64 'Basic <b64(user:pw)>' form and any Authorization header value —
including on a path that has no password to compare against.
"""

import contextlib
import io
import json
import os
import shutil
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
import oc_l1_http  # noqa: E402
import oc_l1_serve  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    FAKE_SESSION_ID,
    FAKE_STDERR_NOTE,
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


def wait_log_text(path, needle, seconds=5):
    """Wait until the child's stderr note reaches the log (the write is async)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if path.is_file() and needle in path.read_text(encoding="utf-8", errors="replace"):
            return True
        time.sleep(0.1)
    return False


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

    # (3b) D-665 fix 4: the plugin's fail-open notes go to the child's stderr,
    #      which used to be DEVNULL — a guard that never loaded was invisible.
    def test_child_stderr_lands_in_scratch_opencode_log(self):
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        log = Path(self.lane["scratch_dir"]) / "opencode.log"
        self.assertTrue(log.is_file(), "the child stderr log must exist")
        self.assertTrue(wait_log_text(log, FAKE_STDERR_NOTE),
                        "the child's stderr must be captured, not DEVNULL")

    # (3c) a relaunch appends: the previous cycle's notes are the evidence
    def test_child_stderr_log_survives_a_relaunch(self):
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        log = Path(self.lane["scratch_dir"]) / "opencode.log"
        self.assertTrue(wait_log_text(log, FAKE_STDERR_NOTE))
        first = log.read_text(encoding="utf-8")
        os.remove(self.lane["state_file"])  # the watcher's rc!=0 path deletes it
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0, out2)
        both = FAKE_STDERR_NOTE + "\n" + FAKE_STDERR_NOTE
        self.assertTrue(wait_log_text(log, both),
                        "the second start must append its own stderr line")
        body = log.read_text(encoding="utf-8")
        self.assertTrue(body.startswith(first),
                        "the earlier stderr must not be truncated away")

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


class TestPrivateScratchModes(unittest.TestCase):
    """F4: the scratch dir and the child's log get their mode AT CREATION.

    `open(path, "ab")` followed by `os.chmod` is a window: between the two the
    log holds the child's environment-adjacent output with the caller's umask
    (0o022 -> 0o644 world-readable; 0o000 -> 0o666). The mode must come from
    os.open(..., 0o600) and mkdir under a 0o077 umask, so no chmod is needed
    for a file or directory that did not exist yet.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_modes_")
        self.td = Path(self._td.name)
        self._old_umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self._old_umask)
        try:
            self._td.cleanup()
        except OSError:
            shutil.rmtree(self.td, ignore_errors=True)

    def _log_with_chmod_spy(self, scratch):
        """Return (file handle, [(path, mode)]) for os.chmod calls during open."""
        seen = []
        real = os.chmod

        def spy(path, mode, *a, **k):
            seen.append((str(path), mode))
            return real(path, mode, *a, **k)

        os.chmod = spy
        try:
            fh = oc_l1_serve._child_stderr_log(scratch)
        finally:
            os.chmod = real
        return fh, seen

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_scratch_dir_is_0700_from_mkdir(self):
        scratch = self.td / "scratch" / "deep"
        fh = oc_l1_serve._child_stderr_log(scratch)
        self.assertIsNotNone(fh)
        fh.close()
        self.assertEqual(os.stat(scratch).st_mode & 0o777, 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_log_is_0600_without_a_chmod_after_the_open(self):
        scratch = self.td / "scratch"
        log = scratch / "opencode.log"
        fh, chmods = self._log_with_chmod_spy(scratch)
        self.assertIsNotNone(fh)
        fh.close()
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)
        touching_log = [c for c in chmods if c[0].endswith("opencode.log")]
        self.assertEqual(touching_log, [],
                         "log mode must be set by os.open, not chmod after it")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_a_permissive_umask_cannot_loosen_the_created_modes(self):
        os.umask(0o000)
        scratch = self.td / "scratch"
        fh = oc_l1_serve._child_stderr_log(scratch)
        self.assertIsNotNone(fh)
        fh.close()
        self.assertEqual(os.stat(scratch).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(scratch / "opencode.log").st_mode & 0o777,
                         0o600)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_a_log_left_loose_by_an_earlier_run_is_repaired_not_widened(self):
        scratch = self.td / "scratch"
        scratch.mkdir()
        log = scratch / "opencode.log"
        log.write_text("old\n", encoding="utf-8")
        os.chmod(log, 0o644)
        fh = oc_l1_serve._child_stderr_log(scratch)
        self.assertIsNotNone(fh)
        fh.write(b"new\n")
        fh.close()
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)
        self.assertEqual(log.read_text(encoding="utf-8"), "old\nnew\n")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_spawn_dirs_are_private(self):
        scratch = self.td / "scratch"
        for _var, sub in oc_l1_serve.XDG_SUBDIRS:
            oc_l1_serve._private_dir(scratch / sub)
        self.assertEqual(os.stat(scratch).st_mode & 0o777, 0o700)

    def test_the_source_no_longer_opens_then_chmods_the_handle(self):
        src = (ROOT / "tools" / "oc_l1_serve.py").read_text(encoding="utf-8")
        self.assertNotIn("os.chmod(fh.fileno()", src)
        self.assertIn("os.O_CREAT", src)
        self.assertIn("os.O_APPEND", src)
        self.assertIn("0o600", src)


class TestScrubCredentialShapes(unittest.TestCase):
    """F5: the password travels in more shapes than the bare string.

    urllib and friends echo the request's Authorization header into the messages
    the launcher prints and logs, so `_scrub` must remove the base64 Basic form
    and any `Authorization:` value — even in a call path that has no password
    to compare against.
    """

    def setUp(self):
        self.pw = PW_VALUE
        self.header = oc_l1_http._basic_header(self.pw)
        self.b64 = self.header.split(" ", 1)[1]

    def test_the_bare_password_is_scrubbed(self):
        self.assertNotIn(self.pw,
                         oc_l1_http._scrub("bad pw %s" % self.pw, self.pw))

    def test_the_basic_header_the_launcher_builds_is_scrubbed(self):
        out = oc_l1_http._scrub(
            "401 Client Error: %s" % self.header, self.pw)
        self.assertNotIn(self.b64, out)
        self.assertNotIn(self.pw, out)

    def test_the_basic_form_is_scrubbed_with_no_password_in_scope(self):
        # an exception path that lost the password must still not leak the token
        out = oc_l1_http._scrub("Authorization: %s" % self.header, "")
        self.assertNotIn(self.b64, out)

    def test_a_quoted_authorization_value_is_scrubbed(self):
        out = oc_l1_http._scrub(
            "request %s" % json.dumps({"Authorization": self.header}), self.pw)
        self.assertNotIn(self.b64, out)
        self.assertNotIn(self.pw, out)

    def test_any_authorization_value_is_scrubbed(self):
        out = oc_l1_http._scrub(
            "header Authorization: Bearer sk-abcdef123456, retrying", "")
        self.assertNotIn("sk-abcdef123456", out)
        self.assertEqual(out, "header Authorization: ***")

    def test_an_unquoted_header_value_takes_only_its_own_line(self):
        out = oc_l1_http._scrub(
            "Authorization: %s\nthe next line stays\n" % self.header, "")
        self.assertNotIn(self.b64, out)
        self.assertIn("the next line stays", out)

    def test_prose_that_says_basic_auth_is_left_alone(self):
        self.assertEqual(
            oc_l1_http._scrub("use Basic auth with the server", ""),
            "use Basic auth with the server")

    def test_ordinary_text_is_left_alone(self):
        for text in ("create session returned HTTP 401",
                     "bash-guard: DENIED - unquoted heredoc"):
            self.assertEqual(oc_l1_http._scrub(text, self.pw), text)

    def test_non_string_input_does_not_raise(self):
        self.assertIsInstance(oc_l1_http._scrub(None, ""), str)


if __name__ == "__main__":
    unittest.main()
