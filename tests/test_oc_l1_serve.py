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
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
                                   "started_utc", "canary", "prompted",
                                   # Sonnet final REJECT 2026-10-08 finding 4: a
                                   # stop must recognise the process it is killing
                                   # by its WHOLE recorded command line and by the
                                   # kernel start time of that exact PID.
                                   "argv", "start_time"})
        self.assertEqual(st["argv"][0], self.lane["opencode_bin"])
        self.assertEqual(st["argv"][1:],
                         ["serve", "--hostname", "127.0.0.1",
                          "--port", str(self.srv.port)])
        if os.name != "nt":
            self.assertIsInstance(st["start_time"], int)
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

    # (3d) Sonnet final REJECT 2026-10-08 finding 2: `_spawn` handed the lane
    #      `dict(os.environ)` - every credential the launcher's shell happened to
    #      export sat in the lane's /proc/<pid>/environ, readable by the lane, by
    #      its MCP server and by every tier-3 worker it spawns. The child env is
    #      an allowlist now: names the lane's own machinery reads, plus what the
    #      lane declares in `child_env`; a value never comes from the config file.
    def test_child_env_is_an_allowlist_not_the_launchers_environment(self):
        secrets = {"GH_TOKEN": "ghp_NOT-A-REAL-TOKEN-000",
                   "AUTOOS_LITELLM_API_KEY": "sk-not-a-real-key-000",
                   "DB_PASSWORD": "not-a-real-password-000",
                   "STRIPE_SECRET": "sk_not_a_real_value"}
        needed = {"AUTOOS_OMNIROUTE_URL": "https://gateway.invalid",
                  "AUTOOS_OMNIROUTE_KEY": "sk-NOT-A-REAL-VALUE-000",
                  "AUTOOS_TASK_DIR": str(self.td / "task"),
                  "AUTOOS_RUN_DIR": str(self.td / "run"),
                  "AUTOOS_AGENT_DEPTH": "1",
                  "AUTOOS_CLAUDE_CRITICAL": "1"}
        for name, value in list(secrets.items()) + list(needed.items()):
            self.addCleanup(os.environ.pop, name, None)
            os.environ[name] = value
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        for name in list(secrets) + [PW_ENV]:
            self.assertNotIn(name, rec["env_names"],
                             "%s must not reach the lane child" % name)
        for name in list(needed) + ["PATH", "HOME", "OPENCODE_CONFIG",
                                    "OPENCODE_SERVER_PASSWORD", "XDG_CONFIG_HOME",
                                    RECORD_ENV]:
            self.assertIn(name, rec["env_names"],
                          "the lane lost a variable its own tools read: %s" % name)
        # the report of what stayed behind is NAMES ONLY: a value printed by the
        # launcher travels into the log, the terminal and any transcript
        for value in list(secrets.values()):
            self.assertNotIn(value, out + json.dumps(rec),
                             "a credential value must never be printed or recorded")

    # the server password goes to the child under ONE name, and a lane cannot
    # smuggle the launcher's password variable in through child_env either
    def test_the_password_name_is_never_forwarded_even_when_the_lane_asks(self):
        self.lane["child_env"] = [RECORD_ENV, PW_ENV]
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertNotIn(PW_ENV, rec["env_names"],
                         "the launcher's password variable must stay with the launcher")
        self.assertIn("OPENCODE_SERVER_PASSWORD", rec["env_names"])

    # (3f) the layer marker: an L2 lane's own env says L2, so the MCP server the
    #      lane starts can refuse lane-control tools. An L1 lane marks nothing.
    def test_agent_layer_marker_reaches_the_child_from_the_lane(self):
        self.lane["agent_layer"] = "L2"
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["agent_layer"], "L2")
        self.assertIn("AUTOOS_AGENT_LAYER", rec["env_names"])

    def test_agent_layer_is_not_inherited_from_the_shell(self):
        os.environ["AUTOOS_AGENT_LAYER"] = "L2"
        try:
            rc, out = self._start()
            self.assertEqual(rc, 0, out)
            wait_file(self.rec)
            rec = json.loads(self.rec.read_text(encoding="utf-8"))
        finally:
            os.environ.pop("AUTOOS_AGENT_LAYER", None)
        self.assertIsNone(rec["agent_layer"],
                          "an unmarked lane is an L1 lane: it must not inherit L2")

    # (3e) D-665 (AO-L2-LAUNCH): the guard role and the L1 inbox are LANE
    #      properties. The child gets them from the lane - and never from the
    #      shell that happened to export them.
    def test_guard_role_and_inbox_reach_the_child_env(self):
        self.lane["guard_role"] = "orchestrator"
        self.lane["inbox_file"] = str(self.td / "inbox" / "l1-pilot.md")
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["guard_role"], "orchestrator")
        self.assertEqual(rec["l1_inbox"], self.lane["inbox_file"])

    def test_guard_role_is_not_inherited_from_the_shell(self):
        os.environ["AUTOOS_GUARD_ROLE"] = "orchestrator"
        os.environ["AUTOOS_L1_INBOX"] = "/some/other/inbox.md"
        try:
            rc, out = self._start()
            self.assertEqual(rc, 0, out)
            wait_file(self.rec)
            rec = json.loads(self.rec.read_text(encoding="utf-8"))
        finally:
            os.environ.pop("AUTOOS_GUARD_ROLE", None)
            os.environ.pop("AUTOOS_L1_INBOX", None)
        self.assertIsNone(rec["guard_role"],
                          "an unguarded lane inherited the orchestrator role")
        self.assertIsNone(rec["l1_inbox"])

    # (3f) the lane's own first-prompt file is posted verbatim (no handoff head)
    def test_first_prompt_file_is_posted_verbatim(self):
        pf = self.td / "first-prompt.md"
        pf.write_text("BRIEF BODY\n\nFOOTER LINE\n", encoding="utf-8")
        self.lane["first_prompt_file"] = str(pf)
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        prompts = [r for r in self.srv.requests
                   if r["method"] == "POST" and r["path"].endswith("/prompt")]
        pilot = [r for r in prompts if FAKE_SESSION_ID in r["path"]]
        self.assertEqual(len(pilot), 1)
        self.assertEqual(pilot[0]["body"]["text"], "BRIEF BODY\n\nFOOTER LINE\n")
        self.assertNotIn("relaunched from the handoff", pilot[0]["body"]["text"])

    def test_missing_first_prompt_file_refused_before_spawn(self):
        self.lane["first_prompt_file"] = str(self.td / "gone.md")
        rc, out = self._start()
        self.assertEqual(rc, 2, out)
        self.assertIn("first_prompt_file", out)
        self.assertFalse(self.rec.is_file(), "no child may be spawned")
        self.assertEqual(self.srv.requests, [])

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


class TestPortCollision(unittest.TestCase):
    """Fix 6: a port somebody else holds is found by binding it BEFORE the
    spawn, and the port the child actually got is what the state records.

    Nothing here patches the probe. The foreign holder is a live listener, the
    lane's server is the fake bound to the port the launcher moved to, and the
    argv the fake binary recorded is the spawn that really ran - so the walk,
    the child's `--port` and the state file are all the production path.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_port_")
        self.td = Path(self._td.name)
        self.held = self.held_port = None
        for p in range(oc_l1.PORT_MIN, oc_l1.PORT_MAX):
            if not oc_l1.port_is_free(p):
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", p))
                sock.listen(1)
            except OSError:
                sock.close()
                continue
            self.held, self.held_port = sock, p
            break
        if self.held is None:
            self.skipTest("every lane port of the range is taken on this host")
        # where the launcher must land: the first free port after the held one,
        # read with the same probe it uses, before this test binds it
        self.chosen = oc_l1.next_free_port(self.held_port + 1)
        if self.chosen == self.held_port:
            self.skipTest("the walk did not move off the held port")
        self.srv = FakeServer(PW_VALUE, port=self.chosen)
        self.srv.start()
        self.lane = make_lane(self.td, self.held_port)
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
        self.held.close()
        for k, v in self._envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def _start(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = oc_l1_serve.cmd_start(
                self.lane, SimpleNamespace(name="l1test", config=str(self.cfg)))
        if rc == 0:
            self._pids.append(
                oc_l1_serve._read_state(self.lane["state_file"])["pid"])
        return rc, out.getvalue()

    def test_a_held_port_moves_the_lane_and_everything_reads_the_move(self):
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        self.assertIn("serves on %d" % self.chosen, out)
        st = oc_l1_serve._read_state(self.lane["state_file"])
        self.assertEqual(st["port"], self.chosen)
        self.assertNotEqual(st["port"], self.held_port)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(rec["argv"][-2:], ["--port", str(self.chosen)],
                         "the child was spawned on the port the config named, "
                         "not the one it moved to")
        self.assertTrue([r for r in self.srv.requests
                         if r["path"].endswith("/prompt")],
                        "the lane never talked to the server it moved to")


@unittest.skipIf(os.name == "nt", "process groups")
class TestKillPidScope(unittest.TestCase):
    """Sonnet final REJECT (finding 4): a process GROUP may be signalled only
    when the recorded PID leads it.

    `os.killpg(os.getpgid(pid), ...)` is correct for a child the launcher started
    with start_new_session=True and catastrophic for anything else: getpgid()
    then names the CALLER's group, so the launcher kills itself and every sibling
    process sharing it. The group call is spied rather than run, so the pre-fix
    code cannot take this suite down with it.
    """

    _SLEEP = "import time; time.sleep(120)"

    def setUp(self):
        self._procs = []

    def tearDown(self):
        for proc in self._procs:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def _spawn(self, **kwargs):
        proc = subprocess.Popen([sys.executable, "-c", self._SLEEP], **kwargs)
        self._procs.append(proc)
        return proc

    def test_a_group_leader_child_is_signalled_as_a_group(self):
        proc = self._spawn(start_new_session=True)
        calls = []
        with mock.patch.object(os, "killpg",
                               side_effect=lambda pgid, sig: calls.append((pgid, sig))):
            oc_l1_serve._kill_pid(proc.pid)
        self.assertEqual(calls, [(proc.pid, signal.SIGTERM)],
                         "the tree the child forked survives a pid-only kill")

    def test_a_process_in_the_callers_group_is_signalled_by_pid_only(self):
        proc = self._spawn()  # no start_new_session: it shares THIS process's group
        calls = []
        with mock.patch.object(os, "killpg",
                               side_effect=lambda pgid, sig: calls.append((pgid, sig))):
            oc_l1_serve._kill_pid(proc.pid)
        self.assertEqual(calls, [], "the caller's own group was signalled")
        proc.wait(timeout=10)
        self.assertEqual(proc.poll(), -int(signal.SIGTERM))

    def test_proc_starttime_names_a_live_process_and_nothing_a_dead_one(self):
        self.assertIsInstance(oc_l1_serve.proc_starttime(os.getpid()), int)
        gone = self._spawn()
        gone.kill()
        gone.wait()
        self.assertIsNone(oc_l1_serve.proc_starttime(gone.pid))
        self.assertIsNone(oc_l1_serve.proc_argv(gone.pid))


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


class TestChildEnvGatewayDefault(unittest.TestCase):
    """D2 (live check 2026-10-08): the gateway base URL default is applied at start.

    The rendered config references the variable by NAME (`{env:AUTOOS_OMNIROUTE_URL}/v1`)
    and `oc_l1.py`'s docstring promises the default `http://127.0.0.1:20128` is applied
    at start time. The child env was a pure allowlist filter over the launcher's own
    environment, so a launcher that never exported the name handed the lane an EMPTY
    base URL: opencode died in `LLM.compile` with `TypeError: Invalid URL` and the
    session produced nothing (l2-canary serve log, run 19711e43).
    """

    def _env(self, parent, **lane_over):
        with tempfile.TemporaryDirectory(prefix="oc_l1_env_") as td:
            lane = make_lane(Path(td), 47255)
            lane.update(lane_over)
            with mock.patch.dict(os.environ, parent, clear=True):
                return oc_l1_serve._child_env(
                    lane, Path(td) / "opencode.json", PW_VALUE)

    def test_the_default_reaches_the_child_when_the_parent_lacks_it(self):
        env = self._env({"PATH": os.environ.get("PATH", "")})
        self.assertEqual(env.get("AUTOOS_OMNIROUTE_URL"),
                         oc_l1.DEFAULT_BASE_URL,
                         "a lane with no base URL cannot reach its model")

    def test_an_explicit_parent_value_is_kept(self):
        env = self._env({"AUTOOS_OMNIROUTE_URL": "http://gateway.invalid:9999"})
        self.assertEqual(env["AUTOOS_OMNIROUTE_URL"],
                         "http://gateway.invalid:9999")

    def test_the_default_carries_no_v1_suffix(self):
        # the render appends /v1 itself, so a default that carried it resolves twice
        env = self._env({})
        self.assertFalse(env["AUTOOS_OMNIROUTE_URL"].endswith("/v1"))

    def test_an_exported_empty_url_is_replaced_by_the_default(self):
        # L2-SECRETS fix 1 (2026-10-08): `setdefault` answers only a MISSING name.
        # A launcher that exported AUTOOS_OMNIROUTE_URL="" — a shell that sourced a
        # template, a CI job that sets every var it mentions — kept the empty
        # value, and the lane died exactly as the unset case did:
        # `TypeError: Invalid URL` in opencode's LLM.compile.
        env = self._env({"AUTOOS_OMNIROUTE_URL": ""})
        self.assertEqual(env["AUTOOS_OMNIROUTE_URL"], oc_l1.DEFAULT_BASE_URL,
                         "an empty base URL is as dead as no base URL")

    def test_a_whitespace_only_url_is_replaced_by_the_default(self):
        for blank in (" ", "  ", "\t"):
            env = self._env({"AUTOOS_OMNIROUTE_URL": blank})
            self.assertEqual(env["AUTOOS_OMNIROUTE_URL"], oc_l1.DEFAULT_BASE_URL,
                             repr(blank))

    def test_a_blank_export_still_lets_an_explicit_value_win(self):
        # the rule is blank -> default, not unset -> default: a real override,
        # including one that carries a port and no scheme-relative path, is kept
        env = self._env({"AUTOOS_OMNIROUTE_URL": "http://gateway.invalid:9999/"})
        self.assertEqual(env["AUTOOS_OMNIROUTE_URL"], "http://gateway.invalid:9999/")

    def test_the_key_is_not_invented(self):
        # only the URL gets a default: a lane with no key has no model to call,
        # and a fake key would read as a working gateway
        env = self._env({})
        self.assertNotIn("AUTOOS_OMNIROUTE_KEY", env)


if __name__ == "__main__":
    unittest.main()
