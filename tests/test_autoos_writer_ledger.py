#!/usr/bin/env python3
"""Tests for tools/autoos_writer_ledger.py (P3, hermetic: tmp dirs only)."""
import datetime, json, os, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_writer_ledger as ledger  # noqa: E402
NOW = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=datetime.timezone.utc)
def path():
    return os.path.join(tempfile.mkdtemp(), "writer-ledger.jsonl")
def entry(**over):
    base = {"run_id": "20261009-105910-task-abc123", "verdict": "rejected",
            "failure_class": "syntax", "writer_client": "opencode",
            "writer_model_served": "vertex/gemini-3.8-flash",
            "task_type": "code", "reviewer": "t3"}
    base.update(over)
    return base
def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")
def ago(days):
    return iso(NOW - datetime.timedelta(days=days))
class LedgerTests(unittest.TestCase):
    def test_record_round_trip(self):
        got = ledger.record(entry(), path=path())
        self.assertEqual(got["verdict"], "rejected")
        self.assertIn("ts", got)
    def test_record_strict(self):
        target = path()
        bads = [dict(entry(), verdict="x"), dict(entry(), failure_class="x"),
                dict(entry(), run_id="bad id!"), dict(entry(), task_type="x"),
                dict(entry(), writer_client="a\nb"), dict(entry(), reviewer="x" * 201),
                dict(entry(), verdict="rejected", failure_class=None),
                dict(entry(), unknown="x"), {"verdict": "rejected"}]
        for bad in bads:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ledger.record(bad, path=target)
        self.assertFalse(os.path.exists(target))
    def test_accepted_needs_no_class(self):
        good = entry(verdict="accepted")
        del good["failure_class"]
        self.assertEqual(ledger.record(good, path=path())["verdict"], "accepted")
    def test_load_tolerates_tears(self):
        target = path()
        ledger.record(entry(), path=target)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write('{"run_id": "torn\n' + "garbage\n" + "\n")
        rows, skipped = ledger.load(target)
        self.assertEqual((len(rows), skipped), (1, 2))
        self.assertEqual(ledger.load(os.path.join(tempfile.mkdtemp(), "no.jsonl")), ([], 0))
    def test_demoted_threshold_window_spelling(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        self.assertTrue(ledger.demoted("vertex/gemini-3.8-flash", "code", now=NOW, path=target))
        self.assertTrue(ledger.demoted("VERTEX/gemini-3.8-flash:free", "code", now=NOW, path=target))
        self.assertFalse(ledger.demoted("vertex/gemini-3.8-flash", "ops", now=NOW, path=target))
        old = path()
        ledger.record(entry(run_id="o1", ts=ago(30)), path=old)
        ledger.record(entry(run_id="o2", ts=ago(31)), path=old)
        self.assertFalse(ledger.demoted("vertex/gemini-3.8-flash", "code", now=NOW, path=old))
    def test_probe_clears_pair(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        ledger.probe_pass("vertex/gemini-3.8-flash", "code", path=target, now=NOW)
        self.assertFalse(ledger.demoted("vertex/gemini-3.8-flash", "code", now=NOW, path=target))
        later = NOW + datetime.timedelta(minutes=10)
        ledger.record(entry(run_id="r3", ts=iso(later)), path=target)
        ledger.record(entry(run_id="r4", ts=iso(later)), path=target)
        self.assertTrue(ledger.demoted("vertex/gemini-3.8-flash", "code", now=later, path=target))
    def test_rollup_and_cli(self):
        target = path()
        good = entry(run_id="a1", verdict="accepted")
        del good["failure_class"]
        ledger.record(good, path=target)
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        slot = ledger.rollup(target, NOW)["vertex/gemini-3.8-flash"]["code"]
        self.assertEqual((slot["accepted"], slot["rejected"]), (1, 2))
        state = tempfile.mkdtemp()
        os.rename(target, os.path.join(state, "writer-ledger.jsonl"))
        os.environ["AUTOOS_STATE_DIR"] = state
        try:
            self.assertEqual(ledger.main(["demoted", "vertex/gemini-3.8-flash", "code"]), 3)
            self.assertEqual(ledger.main(["demoted", "vertex/gemini-3.8-flash", "ops"]), 0)
            self.assertEqual(ledger.main(["probe", "vertex/gemini-3.8-flash", "code"]), 0)
            self.assertEqual(ledger.main(["rollup"]), 0)
        finally:
            del os.environ["AUTOOS_STATE_DIR"]
if __name__ == "__main__":
    unittest.main(verbosity=2)
