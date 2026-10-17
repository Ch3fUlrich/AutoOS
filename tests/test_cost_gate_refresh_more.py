#!/usr/bin/env python3
"""Tests for tools/cost-gate-refresh.py and tools/cost-gate-status.py.

Every subprocess call is either an injected `run` mock or pointed at a
tiny synthetic rows file run through the real run_budget.py (which
never calls a gateway in --rows mode). Run with:

    python tests/test_cost_gate_refresh_more.py
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
for _p in (str(TOOLS), str(REPO / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


refresh = _load("cost_gate_refresh", "cost-gate-refresh.py")
status = _load("cost_gate_status", "cost-gate-status.py")

FIXED_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def make_doc(**over):
    d = {"day": "2026-10-03", "usd": 1.5, "by_provider": {"vertex": 1.5},
         "verdict": "ok", "unverified": False, "budget": 25.0,
         "bad_rows": 0, "unpriced_models": [{"model": "m", "count": 2}],
         "unpriced_default_used": True, "truncated": False}
    d.update(over)
    return d


class FakeCompleted:
    def __init__(self, returncode=0, bstdout=b""):
        self.returncode, self.stdout, self.stderr = returncode, bstdout, b""


def recorder(make):
    """Injectable `run`: records argv, returns FakeCompleted. Fails the
    test if --gateway appears alongside --rows (a real gateway call)."""
    calls = []

    def fake_run(cmd, **kwargs):
        args = list(cmd)
        if "--rows" in args and "--gateway" in args:
            raise AssertionError("both --rows and --gateway in one run_budget call")
        calls.append(args)
        return make(args)

    fake_run.calls = calls
    return fake_run


def write_gate_file(td, content=b'{"old": true}\n'):
    (td / "daily-gate.json").write_bytes(content)


def tiny_rows_file(td, encoding="utf-8-sig"):
    """One tiny real row (vertex, priced model, recent timestamp)."""
    now = datetime.now(timezone.utc)
    row = {"provider": "vertex", "model": "gemini-3.8-flash",
           "timestamp": (now - timedelta(hours=1)).isoformat(),
           "tokens": {"in": 100, "out": 20, "cacheRead": 0}}
    p = Path(td) / "rows.json"
    p.write_bytes((json.dumps([row]) + "\n").encode(encoding))
    return p


class TestRefreshRowsPassThrough(unittest.TestCase):
    def test_rows_files_passed_through_verbatim(self):
        """A BOM rows file and a UTF-16 rows file are both forwarded to
        run_budget verbatim. Whether run_budget itself can price a
        UTF-16 file is run_budget's own business; the refresh script
        only passes the paths through."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            bom = td / "a-rows.json"
            bom.write_bytes(b"\xef\xbb\xbf" + b"[[]]\n")
            u16 = td / "b-rows.json"
            u16.write_bytes("[[]]".encode("utf-16-le"))
            files = [str(bom), str(u16)]
            run = recorder(lambda a: FakeCompleted(0, json.dumps(make_doc()).encode()))
            rc = refresh.main(["--rows"] + files + ["--state-dir", str(td),
                                                    "--config", str(td / "absent.conf")],
                              run=run, now=FIXED_NOW)
            self.assertEqual(rc, 0)
            cmd = run.calls[0]
            i = cmd.index("--rows")
            self.assertEqual(cmd[i + 1:i + 3], files)
            self.assertNotIn("--gateway", cmd)

    def test_real_run_budget_accepts_bom_file(self):
        """run_budget.py day on a tiny UTF-8 (BOM) rows file: one real
        subprocess, no gateway involved."""
        with tempfile.TemporaryDirectory() as td:
            rows_file = tiny_rows_file(td)
            proc = subprocess.run(
                [sys.executable, str(TOOLS / "run_budget.py"), "day",
                 "--rows", str(rows_file)],
                capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            doc = json.loads(proc.stdout)
            self.assertEqual(doc["verdict"], "ok")
            self.assertEqual(doc["bad_rows"], 0)
            self.assertEqual(doc["unpriced_default_used"], False)

    def test_real_refresh_with_rows_file(self):
        """The refresh script driving the real run_budget.py on a tiny
        BOM rows file (still no gateway)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rows_file = tiny_rows_file(td)
            rc = refresh.main(["--rows", str(rows_file), "--state-dir", str(td),
                               "--config", str(td / "absent.conf")],
                              run=None, now=FIXED_NOW)
            self.assertEqual(rc, 0)
            doc = json.loads((td / "daily-gate.json").read_text())
            self.assertIn(doc["verdict"], ("ok", "warn", "block"))
            line = (td / "daily-gate.status").read_text().rstrip("\n")
            self.assertIn("verdict=", line)
            self.assertNotIn("UNAVAILABLE", line)


class TestStatusScript(unittest.TestCase):
    FRESH_LINE = "2026-10-03T12:00:00Z verdict=ok usd=1.5 budget=25.0 " \
                 "truncated=false unpriced=0 unpriced_default_used=false bad_rows=0"

    def run_status(self, argv, now=FIXED_NOW):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = status.main(argv, now=now)
        return rc, out.getvalue().rstrip("\n")

    def write_status(self, td, text):
        (Path(td) / "daily-gate.status").write_text(text + "\n", encoding="utf-8")

    def test_fresh_and_stale_boundaries(self):
        """29:59 fresh, exactly 30:00 fresh, 30:01 STALE, 60:00 STALE."""
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, self.FRESH_LINE)
            for offset, want in [
                    (timedelta(minutes=29, seconds=59), self.FRESH_LINE),
                    (timedelta(minutes=30), self.FRESH_LINE),
                    (timedelta(minutes=30, seconds=1), self.FRESH_LINE + " STALE"),
                    (timedelta(minutes=60), self.FRESH_LINE + " STALE")]:
                with self.subTest(offset=str(offset)):
                    rc, line = self.run_status(["--state-dir", td],
                                               now=FIXED_NOW + offset)
                    self.assertEqual(rc, 0)
                    self.assertEqual(line, want)

    def test_missing_file_stale(self):
        with tempfile.TemporaryDirectory() as td:
            rc, line = self.run_status(["--state-dir", td], now=FIXED_NOW)
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" STALE"), line)

    def test_unavailable_line_stale(self):
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, "2026-10-03T12:00:00Z UNAVAILABLE: run budget exit 2")
            rc, line = self.run_status(["--state-dir", td],
                                       now=FIXED_NOW + timedelta(minutes=1))
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" STALE"), line)

    def test_price_gap_and_stale_beats_price_gap(self):
        line1 = self.FRESH_LINE.replace(
            "unpriced_default_used=false", "unpriced_default_used=true")
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, line1)
            rc, line = self.run_status(["--state-dir", td],
                                       now=FIXED_NOW + timedelta(minutes=1))
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" PRICE-GAP"), line)
            rc, line = self.run_status(["--state-dir", td],
                                       now=FIXED_NOW + timedelta(minutes=60))
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" STALE"), line)

    def test_now_flag_is_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, self.FRESH_LINE)
            a_rc, a = self.run_status(["--state-dir", td,
                                       "--now", "2026-10-03T12:29:59Z"])
            b_rc, b = self.run_status(["--state-dir", td,
                                       "--now", "2026-10-03T12:29:59Z"])
            self.assertEqual(a_rc, 0)
            self.assertEqual(a, b)
            self.assertEqual(a, self.FRESH_LINE)
            c_rc, c = self.run_status(["--state-dir", td,
                                       "--now", "2026-10-03T12:30:01Z"])
            self.assertEqual(c_rc, 0)
            self.assertEqual(c, self.FRESH_LINE + " STALE")

    def test_exit_zero_for_missing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            rc, line = self.run_status(["--state-dir", str(Path(td) / "nope")],
                                       now=FIXED_NOW)
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" STALE"), line)

    def test_status_line_from_real_refresh_run(self):
        """End-to-end: refresh (real run_budget on a tiny BOM rows file)
        then cost-gate-status at the same fixed time prints the line
        fresh, without a suffix."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rows_file = tiny_rows_file(td)
            rc = refresh.main(["--rows", str(rows_file), "--state-dir", str(td),
                               "--config", str(td / "absent.conf")],
                              run=None, now=FIXED_NOW)
            self.assertEqual(rc, 0)
            out = io.StringIO()
            with redirect_stdout(out):
                s_rc = status.main(["--state-dir", str(td)],
                                   now=FIXED_NOW)
            self.assertEqual(s_rc, 0)
            line = out.getvalue().rstrip("\n")
            self.assertIn("verdict=", line)
            self.assertNotIn("UNAVAILABLE", line)
            # The status line is stamped with the injected now, and
            # status is asked with the same instant: the line is fresh.
            self.assertNotIn(" STALE", line)

    def test_future_dated_status_is_stale(self):
        """A line more than 5 minutes ahead of now is STALE, a line up
        to 5 minutes ahead is fresh (a runaway clock must not read as
        healthy)."""
        future = self.FRESH_LINE.replace("12:00:00Z", "12:06:00Z")
        close = self.FRESH_LINE.replace("12:00:00Z", "12:04:00Z")
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, future)
            rc, line = self.run_status(["--state-dir", td], now=FIXED_NOW)
            self.assertEqual(rc, 0)
            self.assertEqual(line, future + " STALE")
            self.write_status(td, close)
            rc, line = self.run_status(["--state-dir", td], now=FIXED_NOW)
            self.assertEqual(rc, 0)
            self.assertEqual(line, close)

    def test_unparseable_timestamp_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            self.write_status(td, "not-a-timestamp verdict=ok usd=1.5")
            rc, line = self.run_status(["--state-dir", td], now=FIXED_NOW)
            self.assertEqual(rc, 0)
            self.assertEqual(line, "not-a-timestamp verdict=ok usd=1.5 STALE")

    def test_status_without_state_dir_is_stale(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with mock.patch.object(status.os, "name", "nt"), \
             mock.patch.dict(status.os.environ, env, clear=True):
            rc, line = self.run_status([])
            self.assertEqual(rc, 0)
            self.assertTrue(line.endswith(" STALE"), line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
