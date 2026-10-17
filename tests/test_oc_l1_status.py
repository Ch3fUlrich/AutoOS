"""tests/test_oc_l1_status.py - step A2 tests for oc_l1_serve.cmd_status."""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1_serve  # noqa: E402
from _oc_l1_fakes import (  # noqa: E402
    FAKE_SESSION_ID,
    PW_ENV,
    PW_VALUE,
    FakeServer,
    make_lane,
    write_cfg,
)


class StatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeServer(PW_VALUE)
        cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_status_test_")
        self.td = Path(self._td.name)
        self.srv.session_outcome = "succeeded"
        self.srv.items = []
        self.srv.requests = []
        self.lane = make_lane(self.td, self.srv.port)
        self.cfg = write_cfg(self.td, self.lane)
        self._orig_pw = os.environ.get(PW_ENV)
        os.environ[PW_ENV] = PW_VALUE

    def tearDown(self):
        if self._orig_pw is None:
            os.environ.pop(PW_ENV, None)
        else:
            os.environ[PW_ENV] = self._orig_pw
        self._td.cleanup()

    def _args(self):
        return SimpleNamespace(name="l1test", config=str(self.cfg))

    def _write_state(self, sid=FAKE_SESSION_ID, port=None):
        st_path = Path(self.lane["state_file"])
        st_path.parent.mkdir(parents=True, exist_ok=True)
        st_path.write_text(
            json.dumps({
                "name": "l1test",
                "session_id": sid,
                "port": port or self.srv.port,
                "pid": 99999,
                "started_utc": "2026-10-04T12:00:00Z",
            }),
            encoding="utf-8",
        )

    def _run_status(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = oc_l1_serve.cmd_status(self.lane, self._args())
        return rc, out.getvalue().strip()

    def test_status_no_state_file(self):
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_corrupt_state_file(self):
        p = Path(self.lane["state_file"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not json", encoding="utf-8")
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_missing_session_id(self):
        self._write_state(sid="")
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_no_password_env(self):
        self._write_state()
        os.environ.pop(PW_ENV, None)
        rc, out = self._run_status()
        self.assertEqual(rc, 2)
        self.assertIn("dead", out)

    def test_status_server_down(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        self._write_state(port=free_port)
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_outcome_failed(self):
        self._write_state()
        self.srv.session_outcome = "failed"
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_outcome_interrupted(self):
        self._write_state()
        self.srv.session_outcome = "interrupted"
        rc, out = self._run_status()
        self.assertEqual((rc, out), (2, "dead"))

    def test_status_live_assistant_message(self):
        self._write_state()
        now = time.time()
        self.srv.items = [{"type": "assistant",
                           "info": {"time": {"created": now - 3600,
                                             "updated": now - 30}}}]
        rc, out = self._run_status()
        self.assertEqual((rc, out), (0, "live"))

    def test_status_live_tool_item(self):
        self._write_state()
        now = time.time()
        self.srv.items = [{"type": "tool", "time": now - 10}]
        rc, out = self._run_status()
        self.assertEqual((rc, out), (0, "live"))

    def test_status_silent_old_assistant_message(self):
        self._write_state()
        now = time.time()
        self.srv.items = [
            {"type": "user", "info": {"time": {"created": now, "updated": now}}},
            {"type": "assistant", "info": {"time": {"created": now - 7200,
                                                     "updated": now - 7200}}},
        ]
        rc, out = self._run_status()
        self.assertEqual((rc, out), (1, "silent"))

    def test_status_silent_no_progress_items(self):
        self._write_state()
        self.srv.items = []
        rc, out = self._run_status()
        self.assertEqual((rc, out), (1, "silent"))


if __name__ == "__main__":
    unittest.main()
