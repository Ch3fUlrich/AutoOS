"""tests/test_oc_l1_canary.py - step B1 tests for the L1 canary probe.

unittest, stdlib only, NO real opencode: the shared FakeServer (v2 API on
127.0.0.1, Basic auth) is pointed at via the lane's serve_port, and the
lane's opencode_bin is the recording fake script that sleeps. The fake's
canary_mode selects the shape of the canary session's turn: "denied"
(shell tool entry, state.status error, bash-guard text), "allowed"
(status completed), "no_tool" (assistant text only) or "timeout" (the
canary session never gets an outcome).

Covered:
  1. denied  -> start exits 0, state/heartbeat carry canary.denied true,
     the canary session is created exactly once, its prompt carries the
     unquoted-heredoc incident command
  2. allowed -> exit 5, UNATTENDED-REFUSED printed, server stays up
  3. no tool call at all -> exit 5 (inconclusive = not denied)
  4. timeout (canary_timeout_s = 2) -> exit 5 within a few seconds
  5. idempotent second start: no second canary session, stored line
  6. relaunch (dead server, stale state): NEW canary session, heartbeat
     ts newer
  7. heartbeat merge keeps the pilot's `turn`, resets state and
     current_step
  8. the password value appears in no argv, state file, heartbeat.json
     or stdout
"""

import contextlib
import ctypes
import io
import json
import os
import sys
import tempfile
import shutil
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1_serve  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    PW_ENV,
    PW_VALUE,
    RECORD_ENV,
    FakeServer,
    make_lane,
    wait_file,
    write_cfg,
)

CANARY_SESSION_ID = "ses_canary99"
# The incident shape: an UNQUOTED heredoc whose body contains a
# backticked (harmless) command.
INCIDENT = "cat <<CANARY_EOF\ncanary `date`\nCANARY_EOF"


def _terminate_pid(pid):
    """Best-effort fast kill on Windows (ctypes, no taskkill round-trip)."""
    if os.name != "nt":
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass
        return
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def _pid_alive(pid):
    """Fast liveness check (tasklist takes ~2 s on this Windows host)."""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0400, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _canary_posts(srv):
    """POST /api/session requests that created a canary session."""
    return [r for r in srv.requests
            if r["method"] == "POST" and r["path"] == "/api/session"
            and isinstance(r["body"], dict)
            and r["body"].get("title", "").endswith("-canary")]


def _ts_is_utc_iso(ts):
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return False
    return True


def _run_start(lane, cfg):
    args = SimpleNamespace(name=lane["name"], config=str(cfg))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = oc_l1_serve.cmd_start(lane, args)
    return rc, out.getvalue()


class _Base(unittest.TestCase):
    """One fake server + one lane per test; class attr selects the mode."""

    mode = "denied"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_canary_test_")
        self.td = Path(self._td.name)
        self.srv = FakeServer(PW_VALUE, canary_mode=self.mode)
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
        pids = list(self._pids)
        if self.rec.is_file():
            try:
                pids.append(json.loads(
                    self.rec.read_text(encoding="utf-8")).get("pid"))
            except ValueError:
                pass
        for pid in pids:
            if pid:
                _terminate_pid(pid)
        self.srv.stop()
        for k, v in self._envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # on Windows the just-terminated fake binary may still hold its working directory for a moment
        for _ in range(20):
            try:
                self._td.cleanup()
                break
            except OSError:
                time.sleep(0.25)
        else:
            shutil.rmtree(self.td, ignore_errors=True)

    def _start(self):
        """cmd_start with captured output; track children that stay up."""
        rc, out = _run_start(self.lane, self.cfg)
        if rc in (0, 5):  # refused keeps the server running on purpose
            st = oc_l1_serve._read_state(self.lane["state_file"])
            if isinstance(st, dict) and st.get("pid"):
                self._pids.append(st["pid"])
        return rc, out

    def _state(self):
        return json.loads(
            Path(self.lane["state_file"]).read_text(encoding="utf-8"))

    def _heartbeat(self):
        p = Path(self.lane["heartbeat_file"])
        self.assertTrue(p.is_file(), "heartbeat.json missing")
        return json.loads(p.read_text(encoding="utf-8"))


class TestDenied(_Base):
    # (1)
    def test_denied_start_ok_state_heartbeat_prompt(self):
        self.lane["plugins"] = ["/fake/plugin/path/bash-guard/index.mjs"]
        rc, out = self._start()
        self.assertEqual(rc, 0)
        self.assertIn("canary denied=yes", out)
        self.assertNotIn("UNATTENDED-REFUSED", out)
        st = self._state()
        self.assertTrue(st["canary"]["denied"])
        self.assertEqual(st["canary"]["session_id"], CANARY_SESSION_ID)
        posts = _canary_posts(self.srv)
        self.assertEqual(len(posts), 1, "canary session created once")
        body = posts[0]["body"]
        self.assertEqual(body["title"], "l1test-canary")
        self.assertEqual(body["location"], {"directory": self.lane["cwd"]})
        self.assertEqual(body["model"], {
            "providerID": "omniroute",
            "id": "placeholderprovider/PlaceholderModel",
        })
        prompts = [r for r in self.srv.requests
                   if r["method"] == "POST"
                   and CANARY_SESSION_ID in r["path"]
                   and r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 1)
        self.assertIn(INCIDENT, prompts[0]["body"]["text"])
        hb = self._heartbeat()
        self.assertTrue(hb["canary"]["denied"])
        self.assertEqual(
            hb["canary"]["plugin_path"],
            "/fake/plugin/path/bash-guard/index.mjs")
        self.assertTrue(_ts_is_utc_iso(hb["canary"]["ts"]),
                        "ts %r is not UTC ISO" % hb["canary"]["ts"])
        self.assertEqual(hb["state"], "started")
        self.assertEqual(hb["current_step"], "canary")
        self.assertEqual(hb["turn"], 0)


class TestAllowed(_Base):
    mode = "allowed"

    # (2)
    def test_allowed_refused_exit5_server_stays_up(self):
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertIn("canary denied=no", out)
        self.assertNotIn("Traceback", out)
        st = self._state()
        self.assertFalse(st["canary"]["denied"])
        hb = self._heartbeat()
        self.assertFalse(hb["canary"]["denied"])
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertTrue(_pid_alive(rec["pid"]),
                        "the server child must stay up when refused")


class TestNoTool(_Base):
    mode = "no_tool"

    # (3)
    def test_no_tool_call_refused_exit5(self):
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertFalse(self._state()["canary"]["denied"])


class TestTimeout(_Base):
    mode = "timeout"

    # (4)
    def test_timeout_refused_exit5_within_few_seconds(self):
        self.assertEqual(self.lane["canary_timeout_s"], 2)
        t0 = time.monotonic()
        rc, out = self._start()
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertFalse(self._state()["canary"]["denied"])


class TestIdempotent(_Base):
    # (5)
    def test_idempotent_start_no_second_canary(self):
        rc1, _ = self._start()
        self.assertEqual(rc1, 0)
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0)
        self.assertIn("already live", out2)
        self.assertIn("canary denied=yes", out2,
                      "stored canary line must be reprinted")
        self.assertEqual(len(_canary_posts(self.srv)), 1,
                         "second start must not create a canary session")
        prompts = [r for r in self.srv.requests
                   if r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 2)


class TestRelaunch(_Base):
    # (6)
    def test_relaunch_reruns_canary_with_newer_heartbeat(self):
        rc1, _ = self._start()
        self.assertEqual(rc1, 0)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        _terminate_pid(rec["pid"])
        # the pilot session now reads as dead -> a start is a relaunch
        self.srv.session_outcome = "failed"
        hb1 = self._heartbeat()
        time.sleep(1.1)  # ts is second-resolution
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0)
        self.assertNotIn("already live", out2)
        self.assertEqual(len(_canary_posts(self.srv)), 2,
                         "relaunch must create a NEW canary session")
        hb2 = self._heartbeat()
        self.assertTrue(hb2["canary"]["denied"])
        self.assertGreater(hb2["ts"], hb1["ts"],
                           "heartbeat ts must be newer after relaunch")


class TestHeartbeatMerge(_Base):
    # (7)
    def test_heartbeat_merge_keeps_pilot_turn(self):
        hb_path = Path(self.lane["heartbeat_file"])
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        hb_path.write_text(
            json.dumps({"turn": 7, "current_step": "x"}), encoding="utf-8")
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        hb = self._heartbeat()
        # documented behaviour: start resets `state` and `current_step`
        # but keeps the pilot's `turn`
        self.assertEqual(hb["turn"], 7, "pilot turn must be kept")
        self.assertEqual(hb["state"], "started")
        self.assertEqual(hb["current_step"], "canary")
        self.assertTrue(hb["canary"]["denied"])


class TestNoLeak(_Base):
    # (8)
    def test_password_in_no_argv_state_heartbeat_or_stdout(self):
        rc, out = self._start()
        self.assertEqual(rc, 0)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        argv = " ".join(rec["argv"])
        state_text = Path(self.lane["state_file"]).read_text(encoding="utf-8")
        hb_text = Path(self.lane["heartbeat_file"]).read_text(encoding="utf-8")
        for where, blob in (("argv", argv), ("state file", state_text),
                            ("heartbeat.json", hb_text), ("stdout", out)):
            self.assertNotIn(PW_VALUE, blob, "password leaked in %s" % where)


if __name__ == "__main__":
    unittest.main()
