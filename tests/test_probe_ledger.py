"""Tests for tools/probe-ledger.py (availability ledger)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "tools", "probe-ledger.py")


def run(*args, state=None):
    with tempfile.TemporaryDirectory() as tmp:
        st = state if state is not None else os.path.join(tmp, "a.json")
        p = subprocess.run([sys.executable, LEDGER, "--state", st, *args],
                           capture_output=True, text=True, timeout=60)
        return p


class RecordTests(unittest.TestCase):
    def test_record_then_check_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "a.json")
            r = run("record", "--leg", "ovh/Qwen3.8-27B", "--status",
                    "served", "--reason", "probe 200", "--latency-ms", "767",
                    state=st)
            self.assertEqual(r.returncode, 0, r.stderr)
            c = run("check", "--leg", "ovh/Qwen3.8-27b", state=st)
            # case differs: unknown, not a silent hit
            self.assertEqual(c.returncode, 2)
            self.assertIn("unknown", c.stdout)
            c = run("check", "--leg", "ovh/Qwen3.8-27B", state=st)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.assertIn("served", c.stdout)

    def test_failed_counts_consecutive_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "a.json")
            run("record", "--leg", "x/y", "--status", "failed",
                "--reason", "404", state=st)
            run("record", "--leg", "x/y", "--status", "failed",
                "--reason", "404 again", state=st)
            doc = json.load(open(st, encoding="utf-8"))
            self.assertEqual(doc["legs"]["x/y"]["fails"], 2)
            run("record", "--leg", "x/y", "--status", "served",
                "--reason", "200", state=st)
            doc = json.load(open(st, encoding="utf-8"))
            self.assertEqual(doc["legs"]["x/y"]["fails"], 0)

    def test_bad_status_is_rejected(self):
        r = run("record", "--leg", "x/y", "--status", "maybe",
                "--reason", "r")
        self.assertEqual(r.returncode, 2)

    def test_stale_entry_fails_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "a.json")
            run("record", "--leg", "x/y", "--status", "served",
                "--reason", "old", state=st)
            doc = json.load(open(st, encoding="utf-8"))
            doc["legs"]["x/y"]["at"] = "2026-01-01T00:00:00Z"
            json.dump(doc, open(st, "w", encoding="utf-8"))
            c = run("check", "--leg", "x/y", state=st)
            self.assertEqual(c.returncode, 2)
            self.assertIn("stale", c.stdout)

    def test_credit_records_balance_sighting(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "a.json")
            r = run("credit", "--provider", "freeaiapikey", "--usd", "6",
                    state=st)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.load(open(st, encoding="utf-8"))
            self.assertEqual(doc["credits"]["freeaiapikey"]["usd"], 6.0)
            self.assertIn("as_of", doc["credits"]["freeaiapikey"])

    def test_dump_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "a.json")
            run("record", "--leg", "x/y", "--status", "served",
                "--reason", "r", state=st)
            d = run("dump", state=st)
            self.assertEqual(d.returncode, 0)
            self.assertIn("x/y", json.loads(d.stdout)["legs"])


if __name__ == "__main__":
    unittest.main()
