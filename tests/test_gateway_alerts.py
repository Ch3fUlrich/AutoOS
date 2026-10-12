#!/usr/bin/env python3
"""Tests for tools/gateway_alerts.py (P1.2, D-1179): provider-suspension signals in the gateway call log.

A fixture sqlite call_logs table (same column names as OmniRoute's), opened read-only by the tool; no gateway,
no live db. The alert file is a tmp file.

Run directly:  python3 tests/test_gateway_alerts.py
"""
import contextlib
import datetime
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gateway_alerts as ga  # noqa: E402

NOW = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)


def stamp(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "storage.sqlite"
        self.alerts = self.root / "alerts.md"
        self.state = self.root / "state.json"
        con = sqlite3.connect(self.db)
        con.execute("create table call_logs (id text, timestamp text, status integer, model text, provider text,"
                    " error_summary text, error_type text, api_key_name text)")
        con.commit()
        con.close()
        self.n = 0

    def add(self, provider, status, minutes_ago=5, summary=None, model="mimo-v2.6-flash"):
        self.n += 1
        con = sqlite3.connect(self.db)
        con.execute("insert into call_logs values (?,?,?,?,?,?,?,?)",
                    ("id%d" % self.n, stamp(minutes_ago), status, model, provider, summary, None, "k"))
        con.commit()
        con.close()

    def run_check(self, **kw):
        kw.setdefault("window_min", 60)
        return ga.check(db_path=self.db, alert_file=self.alerts, state_path=self.state, now=NOW, **kw)

    def lines(self):
        return self.alerts.read_text().splitlines() if self.alerts.exists() else []


class TestSignals(Fixture):
    def test_mimo_401_raises_one_alert(self):
        self.add("xiaomi-mimo", 401)
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("GATEWAY-ALERT xiaomi-mimo suspension signal", self.lines()[0])

    def test_403_and_body_text_signals(self):
        self.add("xiaomi-mimo", 403)
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])

    def test_suspend_text_on_another_status(self):
        self.add("xiaomi-mimo", 400, summary="Your account has been Suspended")
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])
        self.setUp()
        self.add("xiaomi-mimo", 500, summary="key banned")
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])
        self.setUp()
        self.add("xiaomi-mimo", 400, summary="model disabled for this key")
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])

    def test_healthy_rows_and_other_providers_do_not_alert(self):
        self.add("xiaomi-mimo", 200)
        self.add("xiaomi-mimo", 404)
        self.add("openrouter", 401, model="x")
        self.assertEqual(self.run_check(), [])
        self.assertEqual(self.lines(), [])

    def test_rows_outside_the_window_are_ignored(self):
        self.add("xiaomi-mimo", 401, minutes_ago=600)
        self.assertEqual(self.run_check(), [])

    def test_one_alert_per_provider_per_24h(self):
        self.add("xiaomi-mimo", 401)
        self.assertEqual(self.run_check(), ["xiaomi-mimo"])
        self.add("xiaomi-mimo", 403, minutes_ago=1)
        self.assertEqual(self.run_check(), [])
        self.assertEqual(len(self.lines()), 1)
        later = NOW + datetime.timedelta(hours=25)
        self.add("xiaomi-mimo", 403, minutes_ago=-(25 * 60) + 1)
        self.assertEqual(ga.check(db_path=self.db, alert_file=self.alerts, state_path=self.state, now=later,
                                  window_min=60), ["xiaomi-mimo"])
        self.assertEqual(len(self.lines()), 2)

    def test_no_alert_file_prints_to_stderr(self):
        self.add("xiaomi-mimo", 401)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ga.check(db_path=self.db, alert_file=None, state_path=self.state, now=NOW, window_min=60)
        self.assertIn("GATEWAY-ALERT xiaomi-mimo", err.getvalue())

    def test_db_is_opened_read_only(self):
        self.add("xiaomi-mimo", 401)
        before = self.db.read_bytes()
        self.run_check()
        self.assertEqual(self.db.read_bytes(), before)

    def test_missing_db_is_a_clean_error(self):
        with self.assertRaises(ga.AlertError):
            ga.check(db_path=self.root / "nope.sqlite", alert_file=None, state_path=self.state, now=NOW, window_min=60)

    def test_cli_exit_codes(self):
        self.add("xiaomi-mimo", 401)
        argv = ["--db", str(self.db), "--alert-file", str(self.alerts), "--state", str(self.state)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ga.main(argv, now=NOW), 0)
        self.assertIn("xiaomi-mimo", out.getvalue())


if __name__ == "__main__":
    unittest.main()
