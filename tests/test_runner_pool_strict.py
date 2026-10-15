# TEST-GAMING: unsure - production source code not provided in the material; only the contract and signatures were available to inspect.
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import runner_pool as pool
import runner_pool_core as rpc

class TestPoolStrict(unittest.TestCase):
    def test_write_slot_state_rejects_bad_n_and_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            for bad_n in (0, 7, True, "1", 1.0):
                with self.assertRaises(ValueError):
                    pool.write_slot_state(root, bad_n, "idle", "job1", 1000, 123, "ft")
            for bad_state in ("slot-dead", "bad", ""):
                with self.assertRaises(ValueError):
                    pool.write_slot_state(root, 1, bad_state, "job1", 1000, 123, "ft")

    def test_write_slot_state_keys_and_since(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            now = 111
            pool.write_slot_state(root, 2, "idle", "jobA", now, 456, "ftA")
            data = json.loads((root / "slots" / "2" / "slot.json").read_text())
            self.assertEqual(set(data.keys()), {"slot", "state", "job", "since", "heartbeat", "pid", "filetime"})
            self.assertEqual(data, {"slot": 2, "state": "idle", "job": "jobA", "since": now, "heartbeat": now, "pid": 456, "filetime": "ftA"})
            pool.write_slot_state(root, 3, "running", "jobB", now, 789, "ftB", since=99)
            data2 = json.loads((root / "slots" / "3" / "slot.json").read_text())
            self.assertEqual(data2["since"], 99)

    def test_build_status_slot_entry_exact_keys_and_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            pool.write_slot_state(root, 2, "running", "jobX", 1000, 1234, "ftX")
            res = pool.build_status(root, 1000, lambda p: True, lambda p: "ftX", T=120)
            self.assertEqual(len(res["slots"]), 1)
            entry = res["slots"][0]
            self.assertEqual(set(entry.keys()), {"slot", "state", "job", "since", "heartbeat"})
            self.assertEqual(entry, {"slot": 2, "state": "running", "job": "jobX", "since": 1000, "heartbeat": 1000})

    def test_build_status_filetime_mismatch_stale_is_dead(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            pool.write_slot_state(root, 1, "running", "job1", 1000, 123, "ft1")
            res = pool.build_status(root, 1300, lambda p: True, lambda p: "ft2", T=120)
            self.assertEqual(res["slots"][0]["state"], "slot-dead")

    def test_build_status_dead_pid_fresh_heartbeat_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            pool.write_slot_state(root, 1, "running", "job1", 1000, 123, "ft1")
            res = pool.build_status(root, 1000, lambda p: False, lambda p: "ft1", T=120)
            self.assertEqual(res["slots"][0]["state"], "running")

    def test_build_status_alive_match_stale_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            pool.write_slot_state(root, 1, "running", "job1", 1000, 123, "ft1")
            res = pool.build_status(root, 1300, lambda p: True, lambda p: "ft1", T=120)
            self.assertEqual(res["slots"][0]["state"], "running")

    def test_build_status_ignores_non_numeric_slot_dir_and_sorts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            pool.write_slot_state(root, 3, "idle", "j3", 1000, 1, "f3")
            pool.write_slot_state(root, 1, "idle", "j1", 1000, 1, "f1")
            pool.write_slot_state(root, 2, "idle", "j2", 1000, 1, "f2")
            abc = root / "slots" / "abc"
            abc.mkdir(parents=True, exist_ok=True)
            (abc / "slot.json").write_text(json.dumps({"slot": "abc", "state": "idle", "job": "jabc", "since": 0, "heartbeat": 0, "pid": 1, "filetime": "f"}))
            res = pool.build_status(root, 1000, lambda p: True, lambda p: "f", T=120)
            self.assertEqual([s["slot"] for s in res["slots"]], [1, 2, 3])

    def test_build_status_queue_json_only_and_oldest_wait(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            q = root / "queue"
            q.mkdir(parents=True, exist_ok=True)
            old = q / "old.json"
            old.write_text("{}")
            os.utime(old, (900, 900))
            tmp = q / "tmp.tmp"
            tmp.write_text("{}")
            os.utime(tmp, (100, 100))
            req = q / "req.json.requeued"
            req.write_text("{}")
            os.utime(req, (50, 50))
            res = pool.build_status(root, 1000, lambda p: True, lambda p: "f", T=120)
            self.assertEqual(res["oldest_wait_s"], 100)
            self.assertIs(type(res["oldest_wait_s"]), int)

    def test_build_status_oldest_wait_never_negative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool.ensure_dirs(root)
            q = root / "queue"
            q.mkdir(parents=True, exist_ok=True)
            f = q / "future.json"
            f.write_text("{}")
            os.utime(f, (2000, 2000))
            res = pool.build_status(root, 1000, lambda p: True, lambda p: "f", T=120)
            self.assertEqual(res["oldest_wait_s"], 0)
            self.assertIs(type(res["oldest_wait_s"]), int)

    def test_job_argv_rejects_invalid_job_ids(self):
        repo_root = Path("repo")
        for bad in ("a.b", "../x", "x" * 65):
            with self.assertRaises(ValueError):
                pool.job_argv({"id": bad, "submitter": "s", "card": {"client": "opencode"}, "brief": "hi"}, repo_root, "python")

    def test_job_argv_minimal_exact_and_title_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            job = {"id": "abc", "submitter": "sub", "card": {"client": "opencode"}, "brief": "hi"}
            argv = pool.job_argv(job, repo_root, "python")
            expected = ["python", str(repo_root / "tools" / "autoos-agent.py"), "run", "--client", "opencode", "--isolate", "--run-id", "abc", "--title", "abc", "--", "hi"]
            self.assertEqual(argv, expected)

    def test_job_argv_brief_dash_stays_last(self):
        repo_root = Path("repo")
        job = {"id": "abc", "submitter": "sub", "card": {"client": "opencode"}, "brief": "-x"}
        argv = pool.job_argv(job, repo_root, "python")
        self.assertEqual(argv[-2:], ["--", "-x"])

    def test_job_argv_length_limit(self):
        repo_root = Path("repo")
        job_long = {"id": "abc", "submitter": "sub", "card": {"client": "opencode", "model": "m" * 20000}, "brief": "x" * 29990}
        with self.assertRaises(ValueError) as cm:
            pool.job_argv(job_long, repo_root, "python")
        self.assertIn("argv-too-long", str(cm.exception))
        job_ok = {"id": "abc", "submitter": "sub", "card": {"client": "opencode"}, "brief": "y" * 20001}
        argv = pool.job_argv(job_ok, repo_root, "python")
        self.assertEqual(argv[-1], "y" * 20001)

    def test_done_record_state_and_keys(self):
        job = {"id": "job1", "submitter": "sub1", "card": {"client": "opencode"}, "brief": "hi"}
        deep = {"deep": True, "reasons": []}
        r0 = pool.done_record(job, 0, 100, 200, deep)
        self.assertEqual(r0["state"], "done")
        self.assertNotEqual(r0["state"], "succeeded")
        self.assertEqual(set(r0.keys()), {"id", "submitter", "rc", "started", "ended", "deep", "state"})
        r1 = pool.done_record(job, 1, 100, 200, deep)
        self.assertEqual(r1["state"], "failed")

    def test_done_record_omits_brief_and_card(self):
        job = {"id": "job1", "submitter": "sub1", "card": {"client": "opencode", "secret": "CARD_SECRET_123"}, "brief": "BRIEF_SECRET_456"}
        deep = {"deep": False, "reasons": ["r1"]}
        res = pool.done_record(job, 0, 100, 200, deep)
        s = json.dumps(res)
        self.assertNotIn("brief", s)
        self.assertNotIn("card", s)
        self.assertNotIn("BRIEF_SECRET_456", s)
        self.assertNotIn("CARD_SECRET_123", s)

if __name__ == "__main__":
    unittest.main()
