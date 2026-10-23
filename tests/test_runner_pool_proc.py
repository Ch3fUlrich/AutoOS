import unittest
import tempfile
import pathlib
import json
import os
import sys
import subprocess
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import runner_pool_proc as proc
import runner_pool_core as rpc

class TestProcStarttime(unittest.TestCase):
    def test_parse_comm_with_spaces_and_parentheses(self):
        # Field 3 is state; fields 4 through 21 precede starttime (22).
        for comm in ['worker name', 'worker (nested) name)', '(worker) (name)']:
            with self.subTest(comm=comm):
                text = '123 (' + comm + ') S ' + ' '.join(
                    str(n) for n in range(4, 22)) + ' 987654 23 24\n'
                self.assertEqual(proc._parse_proc_starttime(text), 987654)

    def test_parse_invalid_stat(self):
        for text in ['', '123 (worker) S', '123 (worker) S ' + '0 ' * 18 + 'bad']:
            with self.subTest(text=text):
                self.assertIsNone(proc._parse_proc_starttime(text))

    @unittest.skipUnless(os.name != 'nt' and pathlib.Path('/proc/self/stat').exists(),
                         'requires POSIX procfs')
    def test_self_identity_is_integer_and_stable(self):
        identity = proc.filetime_of(os.getpid())
        self.assertIsInstance(identity, int)
        self.assertEqual(proc.filetime_of(os.getpid()), identity)

    @unittest.skipUnless(os.name != 'nt' and pathlib.Path('/proc/self/stat').exists(),
                         'requires POSIX procfs')
    def test_dead_pid_has_no_identity(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=10)
        self.assertIsNone(proc.filetime_of(child.pid))

    def test_unavailable_procfs_has_no_identity(self):
        with mock.patch.object(proc.os, 'name', 'posix'):
            with mock.patch.object(proc, '_read_proc_stat', side_effect=FileNotFoundError):
                self.assertIsNone(proc.filetime_of(123))

class TestConstants(unittest.TestCase):
    def test_constant_values(self):
        self.assertEqual(proc.JOB_MEMORY_MB, 8192)
        self.assertEqual(proc.JOB_FLAG_KILL_ON_CLOSE, 0x2000)
        self.assertEqual(proc.JOB_FLAG_JOB_MEMORY, 0x200)
        self.assertEqual(proc.JOB_FLAG_BREAKAWAY_OK, 0x800)
        self.assertEqual(proc.JOB_FLAG_SILENT_BREAKAWAY, 0x1000)

    def test_job_limit_flags(self):
        flags = proc.job_limit_flags()
        self.assertIsInstance(flags, int)
        expected = proc.JOB_FLAG_KILL_ON_CLOSE | proc.JOB_FLAG_JOB_MEMORY
        self.assertEqual(flags, expected)

    def test_job_memory_bytes(self):
        self.assertEqual(proc.job_memory_bytes(),
                         proc.JOB_MEMORY_MB * 1024 * 1024)

class TestWriteReadRunPrivate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.slot_dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _valid_params(self):
        return {
            "run_id": "validID_123",
            "pid": 4242,
            "filetime": 123456,
            "mode": "toolless",
        }

    def test_roundtrip_success(self):
        p = self._valid_params()
        written = proc.write_run_private(
            self.slot_dir, p["run_id"], p["pid"], p["filetime"], p["mode"]
        )
        self.assertTrue(written)
        read = proc.read_run_private(self.slot_dir)
        self.assertEqual(read, p)

    def test_write_invalid_run_id(self):
        p = self._valid_params()
        for bad in ["", "a"*65, "invalid!", "space id"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    proc.write_run_private(
                        self.slot_dir, bad, p["pid"], p["filetime"], p["mode"]
                    )

    def test_write_invalid_pid(self):
        p = self._valid_params()
        for bad in [0, -1, True, False, "12"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    proc.write_run_private(
                        self.slot_dir, p["run_id"], bad, p["filetime"], p["mode"]
                    )

    def test_write_invalid_filetime(self):
        p = self._valid_params()
        for bad in [-1, "0"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    proc.write_run_private(
                        self.slot_dir, p["run_id"], p["pid"], bad, p["mode"]
                    )

    def test_write_invalid_mode(self):
        p = self._valid_params()
        for bad in ["", "unknown", "TOOLUSE"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    proc.write_run_private(
                        self.slot_dir, p["run_id"], p["pid"], p["filetime"], bad
                    )

    def test_write_json_atomic_returned(self):
        p = self._valid_params()
        with mock.patch.object(rpc, "write_json_atomic", return_value=False) as mock_write:
            result = proc.write_run_private(
                self.slot_dir, p["run_id"], p["pid"], p["filetime"], p["mode"]
            )
            self.assertFalse(result)
            mock_write.assert_called_once()

    def test_read_missing_key(self):
        # write a file missing 'pid'
        data = {"run_id": "id", "filetime": 0, "mode": "toolless"}
        path = self.slot_dir / "run.private"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            proc.read_run_private(self.slot_dir)

    def test_read_extra_key(self):
        data = {"run_id": "id", "pid": 1, "filetime": 0, "mode": "toolless", "extra": 5}
        path = self.slot_dir / "run.private"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            proc.read_run_private(self.slot_dir)

    def test_read_bad_types(self):
        bad_cases = [
            {"run_id": "id", "pid": "1", "filetime": 0, "mode": "toolless"},
            {"run_id": "id", "pid": True, "filetime": 0, "mode": "toolless"},
            {"run_id": "id", "pid": -5, "filetime": 0, "mode": "toolless"},
            {"run_id": "id", "pid": 0, "filetime": 0, "mode": "toolless"},
            {"run_id": "id", "pid": 1, "filetime": -1, "mode": "toolless"},
            {"run_id": "id", "pid": 1, "filetime": 0, "mode": "badmode"},
        ]
        for i, data in enumerate(bad_cases):
            with self.subTest(i=i):
                path = self.slot_dir / "run.private"
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    proc.read_run_private(self.slot_dir)

    def test_read_invalid_json(self):
        path = self.slot_dir / "run.private"
        path.write_text("{ not json")
        with self.assertRaises(ValueError):
            proc.read_run_private(self.slot_dir)

    def test_malformed_metadata_is_value_error(self):
        cases = [[], 'text', 1, None, {}]
        for key in self._valid_params():
            for bad in ([], {}, None):
                cases.append(dict(self._valid_params(), **{key: bad}))
        for data in cases:
            with self.subTest(data=data):
                (self.slot_dir / 'run.private').write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    proc.read_run_private(self.slot_dir)

class TestKillJob(unittest.TestCase):
    def test_kill_tree_adapter(self):
        self.assertTrue(hasattr(proc, 'kill_tree'))
        for code in (0, 1):
            run = mock.Mock(return_value=mock.Mock(returncode=code))
            self.assertEqual(proc.kill_tree(['taskkill', '/PID', '42'], run=run), code == 0)
            run.assert_called_once_with(['taskkill', '/PID', '42'])

    def test_failed_kill_and_run_mismatch(self):
        self._make_run_private(4242, 111)
        kt = mock.Mock(return_value=False)
        self.assertEqual(proc.kill_job(self.slot_dir, lambda _: 111, kt, os_name='nt'), 'kill-failed')
        kt.reset_mock()
        self.assertEqual(proc.kill_job(self.slot_dir, lambda _: 111, kt, os_name='nt',
                                       expect_run_id='new-run'), 'run-mismatch')
        kt.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.slot_dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_run_private(self, pid, filetime, mode="toolless"):
        proc.write_run_private(self.slot_dir, "run1", pid, filetime, mode)

    def test_not_nt(self):
        with self.assertRaises(NotImplementedError):
            proc.kill_job(self.slot_dir, lambda _: None, lambda _: None, os_name="posix")

    def test_process_gone(self):
        self._make_run_private(4242, 111)
        ft_of = mock.Mock(return_value=None)
        kt = mock.Mock()
        result = proc.kill_job(self.slot_dir, ft_of, kt, os_name="nt")
        self.assertEqual(result, "gone")
        kt.assert_not_called()

    def test_filetime_mismatch(self):
        self._make_run_private(4242, 111)
        ft_of = mock.Mock(return_value=222)
        kt = mock.Mock()
        result = proc.kill_job(self.slot_dir, ft_of, kt, os_name="nt")
        self.assertEqual(result, "filetime-mismatch")
        kt.assert_not_called()

    def test_killed(self):
        self._make_run_private(4242, 111)
        ft_of = mock.Mock(return_value=111)
        kt = mock.Mock()
        result = proc.kill_job(self.slot_dir, ft_of, kt, os_name="nt")
        self.assertEqual(result, "killed")
        kt.assert_called_once_with(['taskkill', '/T', '/F', '/PID', '4242'])

    def test_poisoned_job_json_ignored(self):
        # create a misleading job.json
        (self.slot_dir / "job.json").write_text(json.dumps({"pid": 999, "filetime": 0}))
        self._make_run_private(4242, 111)
        ft_of = mock.Mock(return_value=111)
        kt = mock.Mock()
        result = proc.kill_job(self.slot_dir, ft_of, kt, os_name="nt")
        self.assertEqual(result, "killed")
        kt.assert_called_once()
        args = kt.call_args[0][0]
        self.assertIn('4242', args)
        self.assertNotIn('999', args)

    def test_corrupt_run_private(self):
        path = self.slot_dir / "run.private"
        path.write_text("{ not json")
        ft_of = mock.Mock()
        kt = mock.Mock()
        with self.assertRaises(ValueError):
            proc.kill_job(self.slot_dir, ft_of, kt, os_name="nt")
        kt.assert_not_called()

class TestCreateJobNt(unittest.TestCase):
    def test_lazy_windows_types_and_non_nt_filetime(self):
        import subprocess
        code = """
import builtins, os, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'ctypes.wintypes':
        raise AssertionError('eager Windows import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.path.insert(0, 'tools')
import runner_pool_proc as p
os.name = 'posix'
p._read_proc_stat = lambda pid: '123 (worker) S ' + '0 ' * 18 + '456'
assert p.filetime_of(123) == 456
"""
        subprocess.run([sys.executable, '-c', code], check=True)

    def test_filetime_uses_typed_handle_and_closes(self):
        k32 = mock.Mock()
        k32.OpenProcess.return_value = 1 << 40
        k32.GetExitCodeProcess.side_effect = lambda h, p: setattr(p._obj, 'value', 259) or 1
        with mock.patch.object(proc, '_k32_instance', None), mock.patch.object(
                proc.ctypes, 'WinDLL', return_value=k32, create=True):
            with mock.patch.object(proc.os, 'name', 'nt'):
                proc._nt_filetime(42)
        self.assertEqual(k32.OpenProcess.restype, proc.ctypes.c_void_p)
        k32.CloseHandle.assert_called_once_with(1 << 40)

    def test_posix_raises(self):
        with self.assertRaises(NotImplementedError):
            proc.create_job_nt(os_name="posix")

    def test_assign_flow_order(self):
        expected = [
            "CreateProcess(CREATE_SUSPENDED)",
            "AssignProcessToJobObject",
            "ResumeThread",
        ]
        self.assertEqual(proc.assign_flow_order(), expected)

if __name__ == '__main__':
    unittest.main()
