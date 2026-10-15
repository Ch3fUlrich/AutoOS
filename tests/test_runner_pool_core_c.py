import unittest
import sys
import os
import tempfile
import threading
from pathlib import Path
import concurrent.futures
import re

# Ensure the tools directory is on the import path
repo_root = Path(__file__).resolve().parents[1]
tools_path = repo_root / "tools"
sys.path.insert(0, str(tools_path))

import runner_pool_core as rpc

class TestGroupC(unittest.TestCase):
    # ---------- locked ----------
    def test_locked_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "my.lock"
            # First acquisition should create the file
            with rpc.locked(lock_path) as f1:
                self.assertTrue(lock_path.is_file())
                # Inside lock we can write something
                f1.write(b"test")
                f1.flush()
                # Re-acquire after exiting should succeed (no deadlock)
            # After block, lock should be released; acquiring again must succeed
            with rpc.locked(lock_path) as f2:
                self.assertTrue(lock_path.is_file())
                # Ensure we can write again
                f2.write(b"again")
                f2.flush()

    def test_locked_release_on_exception(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "exc.lock"
            class DummyError(RuntimeError):
                pass
            # First block raises exception
            with self.assertRaises(DummyError):
                with rpc.locked(lock_path):
                    raise DummyError("boom")
            # Second acquisition must still work
            with rpc.locked(lock_path) as f:
                self.assertTrue(lock_path.is_file())
                f.write(b"ok")
                f.flush()

    # ---------- sample_decision ----------
    def _read_counter(self, path):
        try:
            return int(path.read_text())
        except FileNotFoundError:
            return 0

    def test_sample_decision_basic_counters_and_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            pool_dir = Path(td)
            submitters_dir = pool_dir / "submitters"
            submitters_dir.mkdir()
            # First three calls for new submitter should be deep due to 'first3'
            for i in range(1, 4):
                res = rpc.sample_decision(
                    pool_dir, "alice", rc=0, risk="R0",
                    diff_empty=False, tests_skipped=False, sample_every=1000
                )
                self.assertTrue(res["deep"])
                self.assertIn("first3", res["reasons"])
                # No other reasons expected
                self.assertEqual(set(res["reasons"]), {"first3"})
            # Fourth call: no 'first3', no sample (sample_every large)
            res = rpc.sample_decision(
                pool_dir, "alice", rc=0, risk="R0",
                diff_empty=False, tests_skipped=False, sample_every=1000
            )
            self.assertFalse(res["deep"])
            self.assertEqual(res["reasons"], [])
            # Verify counters persisted
            global_cnt = self._read_counter(pool_dir / "sample.count")
            submitter_cnt = self._read_counter(pool_dir / "submitters" / "alice.count")
            self.assertEqual(global_cnt, 4)
            self.assertEqual(submitter_cnt, 4)

    def test_sample_decision_global_sampling(self):
        with tempfile.TemporaryDirectory() as td:
            pool_dir = Path(td)
            # Use sample_every=4; pre-seed bob past first3 to isolate global sampling
            (pool_dir / "submitters").mkdir()
            (pool_dir / "submitters" / "bob.count").write_text("3")
            for i in range(1, 5):
                res = rpc.sample_decision(
                    pool_dir, "bob", rc=0, risk="R0",
                    diff_empty=False, tests_skipped=False, sample_every=4
                )
                if i == 4:
                    self.assertTrue(res["deep"])
                    self.assertIn("sample", res["reasons"])
                else:
                    self.assertFalse(res["deep"])
                    self.assertEqual(res["reasons"], [])
            # Check counter file
            self.assertEqual(self._read_counter(pool_dir / "sample.count"), 4)

    def test_sample_decision_rc_reason(self):
        with tempfile.TemporaryDirectory() as td:
            pool_dir = Path(td)
            res = rpc.sample_decision(
                pool_dir, "charlie", rc=1, risk="R0",
                diff_empty=False, tests_skipped=False, sample_every=1000
            )
            self.assertTrue(res["deep"])
            self.assertIn("rc", res["reasons"])
            self.assertEqual(set(res["reasons"]), {"rc", "first3"})  # first3 also applies

    def test_sample_decision_risk_reasons(self):
        for risk in ("R2", "R3", "R4"):
            with self.subTest(risk=risk), tempfile.TemporaryDirectory() as td:
                pool_dir = Path(td)
                res = rpc.sample_decision(
                    pool_dir, "dave", rc=0, risk=risk,
                    diff_empty=False, tests_skipped=False, sample_every=1000
                )
                self.assertTrue(res["deep"])
                self.assertIn("risk", res["reasons"])
                # first3 also triggers on first call
                self.assertIn("first3", res["reasons"])

        for risk in ("R0", "R1"):
            with self.subTest(risk=risk), tempfile.TemporaryDirectory() as td:
                pool_dir = Path(td)
                res = rpc.sample_decision(
                    pool_dir, "eve", rc=0, risk=risk,
                    diff_empty=False, tests_skipped=False, sample_every=1000
                )
                # first3 applies, but risk not
                self.assertTrue(res["deep"])
                self.assertNotIn("risk", res["reasons"])

    def test_sample_decision_empty_diff_and_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            pool_dir = Path(td)
            res = rpc.sample_decision(
                pool_dir, "frank", rc=0, risk="R0",
                diff_empty=True, tests_skipped=False, sample_every=1000
            )
            self.assertTrue(res["deep"])
            self.assertIn("empty-diff", res["reasons"])
            self.assertIn("first3", res["reasons"])

            res2 = rpc.sample_decision(
                pool_dir, "grace", rc=0, risk="R0",
                diff_empty=False, tests_skipped=True, sample_every=1000
            )
            self.assertTrue(res2["deep"])
            self.assertIn("tests-skipped", res2["reasons"])
            self.assertIn("first3", res2["reasons"])

    def test_sample_decision_invalid_submitter(self):
        invalid_names = ["", "a/b", "../x", "a"*65]
        for name in invalid_names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                pool_dir = Path(td)
                with self.assertRaises(ValueError):
                    rpc.sample_decision(
                        pool_dir, name, rc=0, risk="R0",
                        diff_empty=False, tests_skipped=False, sample_every=1000
                    )
                # Ensure no counter file was created
                cnt_path = pool_dir / "submitters" / f"{name}.count"
                self.assertFalse(cnt_path.exists())

    def test_sample_decision_threaded_global_counter(self):
        with tempfile.TemporaryDirectory() as td:
            pool_dir = Path(td)
            submitter = "hank"
            sample_every = 1  # force deep each call
            def worker():
                return rpc.sample_decision(
                    pool_dir, submitter, rc=0, risk="R0",
                    diff_empty=False, tests_skipped=False, sample_every=sample_every
                )
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as exec:
                futures = [exec.submit(worker) for _ in range(32)]
                results = [f.result() for f in futures]
            # All should be deep with 'sample' reason (first3 also present)
            for res in results:
                self.assertTrue(res["deep"])
                self.assertIn("sample", res["reasons"])
            # Global counter should be exactly 32
            self.assertEqual(self._read_counter(pool_dir / "sample.count"), 32)

    # ---------- requeue_dead ----------
    def test_requeue_dead_first_and_second_call(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            job_dir = base / "jobs"
            job_dir.mkdir()
            job_path = job_dir / "job1.txt"
            job_path.write_text("payload")
            queue_dir = base / "queue"
            failed_dir = base / "failed"

            # First call -> requeued
            result = rpc.requeue_dead(job_path, queue_dir, failed_dir)
            self.assertEqual(result, "requeued")
            self.assertFalse(job_path.exists())
            queued_job = queue_dir / "job1.txt"
            self.assertTrue(queued_job.is_file())
            marker = queue_dir / "job1.txt.requeued"
            self.assertTrue(marker.is_file())

            # Simulate restart: a slot claimed the requeued job, then died with it
            queued_job.unlink()
            job_path.write_text("payload")
            # Second call -> failed
            result2 = rpc.requeue_dead(job_path, queue_dir, failed_dir)
            self.assertEqual(result2, "failed")
            self.assertFalse(job_path.exists())
            failed_job = failed_dir / "job1.txt"
            self.assertTrue(failed_job.is_file())
            # Marker should still exist
            self.assertTrue(marker.is_file())

    # ---------- sanity ----------
    def test_module_has_expected_attributes(self):
        self.assertTrue(callable(getattr(rpc, "locked", None)))
        self.assertTrue(callable(getattr(rpc, "sample_decision", None)))
        self.assertTrue(callable(getattr(rpc, "requeue_dead", None)))

if __name__ == "__main__":
    unittest.main()
