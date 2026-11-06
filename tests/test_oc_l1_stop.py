"""tests/test_oc_l1_stop.py - AO-OC-RECYCLE-FOLLOWUPS: `oc_l1.py stop` (tools/oc_l1_stop.py).

Hermetic: the "lane server" is a throwaway python sleeper (plus a grandchild of
its own) in a tmp dir, no opencode and no network. The kill path is the one
`oc_l2.py stop` shares (process group on POSIX, `taskkill /T` on Windows)."""

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
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_serve  # noqa: E402
import oc_l1_stop  # noqa: E402
import oc_l2  # noqa: E402
from _oc_l1_fakes import make_lane, pid_alive, wait_file, write_cfg  # noqa: E402

# the parent starts a child of its own and records its pid, so a tree kill is observable
SERVER = ("import subprocess, sys, time\n"
          "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
          "open(sys.argv[1], 'w').write(str(kid.pid))\n"
          "time.sleep(120)\n")


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = oc_l1.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class StopTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_stop_test_")
        self.td = Path(self._td.name)
        self.lane = make_lane(self.td, 47251)
        self.cfg = write_cfg(self.td, self.lane)
        self.state_file = Path(self.lane["state_file"])
        self.procs, self.kids = [], []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
        for kid in filter(pid_alive, self.kids):  # the grandchild outlives a parent-only kill
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(kid), "/F"], capture_output=True)
            else:
                os.kill(kid, signal.SIGTERM)
        self._td.cleanup()

    def start_server(self):
        """A live 'serve' with a grandchild; its state file as `start` writes it."""
        kid_file = self.td / "kid.pid"
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # a group leader, as `start` spawns it
        argv = [sys.executable, "-c", SERVER, str(kid_file)]
        proc = subprocess.Popen(argv, **kwargs)
        self.procs.append(proc)
        wait_file(kid_file)
        kid = int(kid_file.read_text())
        self.kids.append(kid)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "name": "l1test", "session_id": "ses_x", "port": 47251, "pid": proc.pid,
            "started_utc": datetime.fromtimestamp(time.time() + 4, timezone.utc).isoformat(),
            "argv": argv, "start_time": oc_l1_serve.proc_starttime(proc.pid) or 0}), encoding="utf-8")
        return proc, kid

    def gone(self, proc, pid):
        deadline = time.time() + 10
        while time.time() < deadline and (proc.poll() is None or pid_alive(pid)):
            time.sleep(0.1)
        return proc.poll() is not None and not pid_alive(pid)

    def stop(self):
        return run_main("stop", "--name", "l1test", "--config", str(self.cfg))

    def test_stop_kills_the_process_tree_and_removes_the_state_file(self):
        proc, kid = self.start_server()
        self.assertTrue(pid_alive(kid))
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertIn("stopped", out)
        self.assertIn("killed pid: %d;" % proc.pid, out)
        self.assertTrue(self.gone(proc, kid), "the server or its child survived stop")
        self.assertFalse(self.state_file.exists())

    def test_a_second_stop_is_not_running_and_kills_nothing(self):
        self.start_server()
        self.assertEqual(self.stop()[0], 0)
        killed = []
        real = oc_l1_serve._kill_pid
        oc_l1_serve._kill_pid = killed.append
        self.addCleanup(setattr, oc_l1_serve, "_kill_pid", real)
        rc, out, err = self.stop()
        self.assertEqual((rc, killed), (0, []))
        self.assertIn("not running", out)

    def test_a_recorded_gone_pid_reports_none_and_removes_state(self):
        proc, kid = self.start_server()
        proc.kill()
        proc.wait()
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertIn("killed pid: none;", out)
        self.assertFalse(self.state_file.exists())

    @unittest.skipUnless(hasattr(os, "killpg") and hasattr(signal, "SIGKILL"), "POSIX signals")
    def test_posix_reports_only_pre_signal_live_pid(self):
        for gone in (True, False):
            with self.subTest(gone=gone), \
                    mock.patch.object(oc_l1_stop, "pid_gone", return_value=gone), \
                    mock.patch.object(oc_l1_stop, "pgid", return_value=None), \
                    mock.patch.object(oc_l1_stop.os, "kill") as kill, \
                    mock.patch.object(oc_l1_stop, "wait_gone", return_value=True):
                result = {"killed_pids": []}
                oc_l1_stop._kill_posix(42, result)
                kill.assert_called_once_with(42, signal.SIGTERM)
                self.assertEqual(result["killed_pids"], [] if gone else [42])

    @unittest.skipUnless(hasattr(os, "killpg") and hasattr(signal, "SIGKILL"), "POSIX signals")
    def test_posix_group_leader_reports_only_pre_signal_live_pid(self):
        for gone in (True, False):
            reads = mock.Mock()
            with self.subTest(gone=gone), \
                    mock.patch.object(oc_l1_stop, "pgid", return_value=42) as group, \
                    mock.patch.object(oc_l1_stop, "pid_gone", return_value=gone) as liveness, \
                    mock.patch.object(oc_l1_stop.os, "killpg") as killpg, \
                    mock.patch.object(oc_l1_stop, "wait_gone", return_value=True):
                reads.attach_mock(group, "pgid")
                reads.attach_mock(liveness, "pid_gone")
                result = {"killed_pids": []}
                oc_l1_stop._kill_posix(42, result)
                self.assertEqual(reads.mock_calls, [mock.call.pgid(42), mock.call.pid_gone(42)])
                killpg.assert_any_call(42, signal.SIGTERM)
                self.assertEqual(result["killed_pids"], [] if gone else [42])

    def test_stop_with_no_state_file_ever_written_is_not_running(self):
        rc, out, _ = self.stop()
        self.assertEqual(rc, 0)
        self.assertIn("not running", out)

    def test_a_state_file_with_no_pid_is_dropped(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"name": "l1test", "port": 47251}), encoding="utf-8")
        rc, out, _ = self.stop()
        self.assertEqual(rc, 0)
        self.assertFalse(self.state_file.exists())

    @unittest.skipIf(os.name == "nt", "the identity check is POSIX-only (no /proc on Windows)")
    def test_a_pid_that_is_not_provably_the_lane_is_not_killed(self):
        proc, kid = self.start_server()
        st = json.loads(self.state_file.read_text(encoding="utf-8"))
        st.pop("start_time")
        self.state_file.write_text(json.dumps(st), encoding="utf-8")
        rc, _, err = self.stop()
        self.assertEqual(rc, 2)
        self.assertIn("NOT stopped", err)
        self.assertIsNone(proc.poll())
        self.assertTrue(self.state_file.exists())

    def test_an_unknown_lane_is_exit_2(self):
        rc, out, err = run_main("stop", "--name", "nosuch", "--config", str(self.cfg))
        self.assertEqual(rc, 2)
        self.assertIn("no lane named 'nosuch'", err)

    def test_unreadable_state_is_kept_and_refused(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        for text in ('{broken', '[]', 'null', '42'):
            with self.subTest(text=text):
                self.state_file.write_text(text, encoding="utf-8")
                rc, _, err = self.stop()
                self.assertEqual(rc, 2)
                self.assertIn("unreadable state file", err)
                self.assertIn(str(self.state_file), err)
                self.assertTrue(self.state_file.exists())
        with mock.patch.object(oc_l1_stop, "_read_state", return_value=None):
            result = oc_l1_stop.stop_lane("l1test", self.lane, None, 47251)
        self.assertFalse(result["stopped"])
        self.assertFalse(result["orphan"])

    def test_readable_pidless_dict_after_unreadable_guard_is_removed(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text('{}', encoding="utf-8")
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertIn("killed pid: none", out)
        self.assertFalse(self.state_file.exists())

    @unittest.skipUnless(os.name == "nt", "Windows kernel32")
    def test_nt_alive_direct(self):
        import ctypes
        for handle, error, code, success, alive in (
                (0, 5, 0, 0, True), (0, 87, 0, 0, False),
                (123, 0, 259, 1, True), (123, 0, 0, 1, False),
                (123, 0, 259, 0, False)):
            with self.subTest(handle=handle, error=error, code=code, success=success):
                kernel = mock.Mock()
                kernel.OpenProcess.return_value = handle
                def exit_code(h, ptr):
                    ptr._obj.value = code
                    return success
                kernel.GetExitCodeProcess.side_effect = exit_code
                with mock.patch.object(ctypes, "WinDLL", return_value=kernel), \
                        mock.patch.object(ctypes, "get_last_error", return_value=error):
                    self.assertEqual(oc_l1_stop._nt_alive(42), alive)
                self.assertEqual(kernel.CloseHandle.call_count, bool(handle))

    @unittest.skipUnless(os.name == "nt", "Windows kernel32")
    def test_windows_identity_and_survivor_detail(self):
        import ctypes
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text('{}', encoding="utf-8")
        for recorded, seconds, readable, refused in (
                ('2026-01-01T00:00:00Z', 10, True, True),
                ('2026-01-01T00:00:00Z', 1, True, False),
                ('2026-01-01T00:00:00Z', -10, True, False),
                ('2026-01-01T00:00:00Z', -40, True, False),
                ('2026-01-01T00:00:00Z', 2, True, False),
                ('2026-01-01T00:00:00', 2, True, False),
                (None, 10, True, False),
                ('2026-01-01T00:00:00Z', 10, False, False)):
            with self.subTest(recorded=recorded, seconds=seconds, readable=readable):
                kernel = mock.Mock()
                kernel.OpenProcess.return_value = 123
                def times(h, created, *rest):
                    ticks = int((1767225600 + seconds + 11644473600) * 10000000)
                    created._obj.dwLowDateTime = ticks & 0xffffffff
                    created._obj.dwHighDateTime = ticks >> 32
                    return readable
                kernel.GetProcessTimes.side_effect = times
                with mock.patch.object(ctypes, "WinDLL", return_value=kernel), \
                        mock.patch.object(oc_l1_serve, "_kill_pid") as kill, \
                        mock.patch.object(oc_l1_stop, "pid_gone", return_value=False):
                    result = oc_l1_stop.stop_lane("l1test", self.lane,
                        {"pid": 42, "started_utc": recorded}, 47251)
                self.assertFalse(result["stopped"])
                self.assertTrue(result["orphan"])
                self.assertTrue(self.state_file.exists())
                self.assertEqual(kill.call_count, 0 if refused else 1)
                self.assertIn("recycled pid" if refused else "after taskkill /F /T",
                              result["detail"])
                if recorded:
                    kernel.CloseHandle.assert_called_once()
                    self.assertEqual(kernel.OpenProcess.restype, ctypes.c_void_p)
                    self.assertEqual(kernel.CloseHandle.argtypes, [ctypes.c_void_p])
                if refused:
                    self.assertIn("2026-01-01T00:00:10Z", result["detail"])
                    self.assertIn("2026-01-01T00:00:00Z", result["detail"])
                    self.assertIn("delete the state file", result["detail"])
                    self.assertNotIn("lane's command", result["detail"])

    def test_stop_is_a_listed_subcommand(self):
        self.assertIn("stop", oc_l1.build_parser().format_help().split("{")[1].split("}")[0])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            oc_l1.main(["stop"])  # --name is required
        self.assertEqual(cm.exception.code, 2)

    def test_the_l2_stop_shares_the_helpers(self):
        self.assertIs(oc_l2._wait_gone, oc_l1_stop.wait_gone)
        self.assertIs(oc_l2._pid_gone, oc_l1_stop.pid_gone)
        self.assertIs(oc_l2._identity_ok, oc_l1_stop.identity_ok)
        self.assertIs(oc_l2._pgid, oc_l1_stop.pgid)


if __name__ == "__main__":
    unittest.main()
