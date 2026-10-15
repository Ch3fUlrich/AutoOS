import unittest
import sys
import os
import tempfile

# Insert the tools directory onto the path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
tools_path = os.path.join(repo_root, 'tools')
sys.path.insert(0, tools_path)

import runner_pool_core as rpc

class TestGroupA(unittest.TestCase):
    # n_clamp ----------------------------------------------------
    def test_n_clamp_returns_requested_when_within_limits(self):
        self.assertEqual(rpc.n_clamp(1), 1)
        self.assertEqual(rpc.n_clamp(5), 5)

    def test_n_clamp_clamps_to_six(self):
        self.assertEqual(rpc.n_clamp(10), 6)

    def test_n_clamp_invalid_type_raises(self):
        for bad in (None, 3.5, "4", True, False, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    rpc.n_clamp(bad)

    def test_n_clamp_negative_or_zero_raises(self):
        for bad in (0, -1, -10):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    rpc.n_clamp(bad)

    def test_n_clamp_calls_log_when_clamped(self):
        calls = []

        def logger(msg):
            calls.append(msg)

        rpc.n_clamp(8, log=logger)
        self.assertEqual(len(calls), 1)
        self.assertIn('clamp requested=8 used=6', calls[0])

    def test_n_clamp_no_log_when_not_clamped(self):
        calls = []

        def logger(msg):
            calls.append(msg)

        rpc.n_clamp(4, log=logger)
        self.assertEqual(calls, [])

    # dead_slot --------------------------------------------------
    def test_dead_slot_pid_gone_fresh_heartbeat(self):
        self.assertFalse(rpc.dead_slot(False, True, 100))

    def test_dead_slot_pid_gone_stale_heartbeat(self):
        self.assertTrue(rpc.dead_slot(False, True, 130))

    def test_dead_slot_filetime_mismatch_fresh(self):
        self.assertFalse(rpc.dead_slot(True, False, 100))

    def test_dead_slot_filetime_mismatch_stale(self):
        self.assertTrue(rpc.dead_slot(True, False, 130))

    def test_dead_slot_all_ok_fresh(self):
        self.assertFalse(rpc.dead_slot(True, True, 100))

    def test_dead_slot_all_ok_stale(self):
        self.assertFalse(rpc.dead_slot(True, True, 130))

    def test_dead_slot_boundary_exact_T(self):
        self.assertFalse(rpc.dead_slot(False, False, 120))

    def test_dead_slot_boundary_just_over_T(self):
        self.assertTrue(rpc.dead_slot(False, False, 121))

    # strip_env --------------------------------------------------
    def test_strip_env_removes_key_and_preserves_others(self):
        env = {'PATH': '/bin', 'AUTOOS_ADMISSION_OFF': '1', 'HOME': '/home/user'}
        result = rpc.strip_env(env)
        self.assertNotIn('AUTOOS_ADMISSION_OFF', result)
        self.assertIn('PATH', result)
        self.assertIn('HOME', result)
        # original unchanged
        self.assertIn('AUTOOS_ADMISSION_OFF', env)

    def test_strip_env_missing_key(self):
        env = {'USER': 'alice'}
        result = rpc.strip_env(env)
        self.assertEqual(result, env)

    def test_strip_env_key_present_with_empty_value(self):
        env = {'AUTOOS_ADMISSION_OFF': ''}
        result = rpc.strip_env(env)
        self.assertNotIn('AUTOOS_ADMISSION_OFF', result)

    # may_claim --------------------------------------------------
    def test_may_claim_none_free_mb(self):
        self.assertFalse(rpc.may_claim(None, 100))

    def test_may_claim_exact_floor(self):
        self.assertTrue(rpc.may_claim(100, 100))

    def test_may_claim_above_floor(self):
        self.assertTrue(rpc.may_claim(150, 100))

    def test_may_claim_below_floor(self):
        self.assertFalse(rpc.may_claim(99, 100))

    # mem_available_mb -------------------------------------------
    def test_mem_available_mb_linux_parses_file(self):
        content = "MemTotal:       2048 kB\nMemAvailable:  3072 kB\n"
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write(content)
            tf_path = tf.name
        try:
            mb = rpc.mem_available_mb(os_name='posix', meminfo_path=tf_path)
            self.assertEqual(mb, 3072 // 1024)
        finally:
            os.remove(tf_path)

    def test_mem_available_mb_linux_missing_file(self):
        mb = rpc.mem_available_mb(os_name='posix', meminfo_path='/nonexistent/path')
        self.assertIsNone(mb)

    def test_mem_available_mb_linux_no_memavailable_line(self):
        content = "MemTotal: 2048 kB\n"
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write(content)
            tf_path = tf.name
        try:
            mb = rpc.mem_available_mb(os_name='posix', meminfo_path=tf_path)
            self.assertIsNone(mb)
        finally:
            os.remove(tf_path)

    def test_mem_available_mb_nt_uses_reader(self):
        def fake_reader():
            return 2048

        mb = rpc.mem_available_mb(os_name='nt', nt_reader=fake_reader)
        self.assertEqual(mb, 2048)

    def test_mem_available_mb_nt_reader_raises(self):
        def bad_reader():
            raise RuntimeError("boom")

        mb = rpc.mem_available_mb(os_name='nt', nt_reader=bad_reader)
        self.assertIsNone(mb)

if __name__ == '__main__':
    unittest.main()
