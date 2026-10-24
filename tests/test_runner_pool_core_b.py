import sys, os, unittest, json, pathlib, time, tempfile
from types import SimpleNamespace

# Insert the tools directory into sys.path
repo_root = pathlib.Path(__file__).resolve().parents[1]
tools_dir = repo_root / "tools"
sys.path.insert(0, str(tools_dir))

import runner_pool_core as rpc  # noqa: E402

class TestGroupB(unittest.TestCase):
    # Helper to create a file with optional mtime
    def _make_file(self, dir_path, name, mtime=None):
        p = dir_path / name
        p.write_text("{}")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    # -------------------- claim() tests --------------------
    def test_claim_moves_oldest_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            f1 = self._make_file(q, "b.json", mtime=200)
            f2 = self._make_file(q, "a.json", mtime=100)  # oldest
            renamed = rpc.claim(q, s)
            self.assertIsNotNone(renamed)
            self.assertEqual(renamed, s / "a.json")
            self.assertFalse(f2.exists())
            self.assertTrue(f1.exists())

    def test_claim_two_claimers_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s1 = pathlib.Path(tmp) / "slot1"
            s2 = pathlib.Path(tmp) / "slot2"
            q.mkdir(); s1.mkdir(); s2.mkdir()
            self._make_file(q, "x.json")
            r1 = rpc.claim(q, s1)
            r2 = rpc.claim(q, s2)
            # Exactly one succeeds
            self.assertTrue((r1 is None) ^ (r2 is None))
            # The file ends up in the successful slot
            if r1:
                self.assertEqual(r1, s1 / "x.json")
                self.assertTrue(r1.exists())
            else:
                self.assertEqual(r2, s2 / "x.json")
                self.assertTrue(r2.exists())

    def test_claim_rename_filenotfound_skips_to_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            f1 = self._make_file(q, "first.json")
            f2 = self._make_file(q, "second.json")
            # Define rename that raises FileNotFoundError for first candidate only
            calls = []
            def fake_rename(src, dst):
                calls.append((src, dst))
                if src.name == "first.json":
                    raise FileNotFoundError()
                else:
                    os.rename(src, dst)
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            result = rpc.claim(q, s, rename=fake_rename, sleep=fake_sleep)
            self.assertIsNotNone(result)
            self.assertEqual(result, s / "second.json")
            self.assertFalse(f2.exists())
            self.assertTrue(f1.exists())
            self.assertEqual(slept, [])  # no sleep on FileNotFoundError

    def test_claim_sharing_violation_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            f = self._make_file(q, "file.json")
            attempts = []
            def fake_rename(src, dst):
                attempts.append(src)
                if len(attempts) <= 3:
                    raise PermissionError("share violation")
                os.rename(src, dst)
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            result = rpc.claim(q, s, rename=fake_rename, sleep=fake_sleep)
            self.assertIsNotNone(result)
            self.assertEqual(result, s / "file.json")
            # three sleeps with exponential backoff starting at 25ms
            expected = [0.025, 0.05, 0.1]
            self.assertEqual(slept, expected)

    def test_claim_oserror_winerror_32_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            f = self._make_file(q, "file.json")
            attempts = []
            def fake_rename(src, dst):
                attempts.append(src)
                if len(attempts) <= 3:
                    err = OSError("win error")
                    err.winerror = 32
                    raise err
                os.rename(src, dst)
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            result = rpc.claim(q, s, rename=fake_rename, sleep=fake_sleep)
            self.assertIsNotNone(result)
            self.assertEqual(slept, [0.025, 0.05, 0.1])

    def test_claim_always_permission_error_gives_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            f = self._make_file(q, "stay.json")
            calls = 0
            def fake_rename(src, dst):
                nonlocal calls
                calls += 1
                raise PermissionError("always")
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            result = rpc.claim(q, s, rename=fake_rename, sleep=fake_sleep)
            self.assertIsNone(result)
            self.assertEqual(calls, rpc.CLAIM_ATTEMPTS)
            # sleep called attempts-1 times with capped backoff
            exp = [0.025, 0.05, 0.1, 0.2, 0.4, 0.4, 0.4]
            self.assertEqual(slept, exp)
            self.assertTrue(f.exists())

    def test_claim_other_oserror_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = pathlib.Path(tmp) / "queue"
            s = pathlib.Path(tmp) / "slot"
            q.mkdir(); s.mkdir()
            self._make_file(q, "boom.json")
            def fake_rename(src, dst):
                raise OSError("disk full", 28)  # ENOSPC, winerror not 32
            with self.assertRaises(OSError):
                rpc.claim(q, s, rename=fake_rename, sleep=lambda _: None)

    # -------------------- write_json_atomic() tests --------------------
    def test_write_json_atomic_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            target = d / "data.json"
            obj = {"a": 1, "b": [2, 3]}
            replaced = 0
            def fake_replace(src, dst):
                nonlocal replaced
                replaced += 1
                os.replace(src, dst)
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            ok = rpc.write_json_atomic(target, obj, replace=fake_replace, sleep=fake_sleep)
            self.assertTrue(ok)
            self.assertEqual(replaced, 1)
            self.assertEqual(slept, [])
            # content matches
            with target.open() as f:
                data = json.load(f)
            self.assertEqual(data, obj)
            # no stray temp files
            self.assertEqual(len(list(d.iterdir())), 1)

    def test_write_json_atomic_permission_error_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            target = d / "conf.json"
            obj = {"x": 42}
            attempts = 0
            def fake_replace(src, dst):
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise PermissionError("share")
                os.replace(src, dst)
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            ok = rpc.write_json_atomic(target, obj, replace=fake_replace, sleep=fake_sleep)
            self.assertTrue(ok)
            self.assertEqual(attempts, 3)
            self.assertEqual(slept, [0.025, 0.05])
            with target.open() as f:
                self.assertEqual(json.load(f), obj)
            self.assertEqual(len(list(d.iterdir())), 1)

    def test_write_json_atomic_always_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            target = d / "persist.json"
            # pre‑existing file
            target.write_text(json.dumps({"old": True}))
            attempts = 0
            def fake_replace(src, dst):
                nonlocal attempts
                attempts += 1
                raise PermissionError("locked")
            slept = []
            def fake_sleep(sec):
                slept.append(sec)
            ok = rpc.write_json_atomic(target, {"new": False}, replace=fake_replace, sleep=fake_sleep)
            self.assertFalse(ok)
            self.assertEqual(attempts, rpc.CLAIM_ATTEMPTS)  # same constant used for retries
            # backoff list
            self.assertEqual(slept, [0.025, 0.05, 0.1, 0.2, 0.4, 0.4, 0.4])
            # original content unchanged
            self.assertEqual(json.loads(target.read_text()), {"old": True})
            # no temp files left
            self.assertEqual(len(list(d.iterdir())), 1)

if __name__ == '__main__':
    unittest.main()
