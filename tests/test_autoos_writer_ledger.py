#!/usr/bin/env python3
"""Tests for tools/autoos_writer_ledger.py (P3, hermetic: tmp dirs only)."""
import contextlib
import datetime
import inspect
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_writer_ledger as ledger  # noqa: E402


NOW = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=datetime.timezone.utc)
MODEL = "vertex/gemini-3.8-flash"


def path():
    return os.path.join(tempfile.mkdtemp(), "writer-ledger.jsonl")


def entry(**over):
    base = {"run_id": "20261009-105910-task-abc123", "verdict": "rejected",
            "failure_class": "syntax", "writer_client": "opencode",
            "writer_model_served": MODEL,
            "task_type": "code", "reviewer": "t3"}
    base.update(over)
    return base


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def ago(days):
    return iso(NOW - datetime.timedelta(days=days))


def minutes(n):
    return iso(NOW + datetime.timedelta(minutes=n))


def write_rows(target, rows):
    with open(target, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._real_now = ledger._now
        ledger._now = lambda: NOW

    def tearDown(self):
        ledger._now = self._real_now

    def test_record_round_trip(self):
        got = ledger.record(entry(), path=path())
        self.assertEqual(got["verdict"], "rejected")
        self.assertEqual(got["ts"], iso(NOW))

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

    def test_record_rejects_future_ts(self):
        target = path()

        with self.assertRaises(ValueError):
            ledger.record(entry(ts=minutes(6)), path=target)

        with self.assertRaises(ValueError):
            ledger.record(entry(ts=minutes(60 * 24)), path=target)

        got = ledger.record(entry(run_id="skew-ok", ts=minutes(4)), path=target)
        self.assertEqual(got["ts"], minutes(4))
        self.assertTrue(os.path.exists(target))

    def test_record_rejects_naive_ts(self):
        target = path()
        naive = [iso(NOW).replace("Z", ""), "2026-10-09 12:00:00", "2026-10-09",
                 datetime.datetime(2026, 10, 9, 12, 0),
                 datetime.datetime(2026, 10, 9, 12, 0).isoformat()]

        for ts in naive:
            with self.subTest(ts=ts):
                with self.assertRaises(ValueError):
                    ledger.record(entry(ts=ts), path=target)

        for i, ts in enumerate([iso(NOW), NOW.isoformat(),
                              NOW.astimezone(datetime.timezone(
                                  datetime.timedelta(hours=2))).isoformat()]):
            with self.subTest(ts=ts):
                got = ledger.record(entry(run_id="tz-%d" % i, ts=ts), path=target)
                self.assertTrue(got["ts"].endswith("Z"))

    def test_record_and_probe_use_now_hook(self):
        self.assertNotIn("now", inspect.signature(ledger.record).parameters)
        self.assertNotIn("now", inspect.signature(ledger.probe_pass).parameters)
        got = ledger.probe_pass(MODEL, "code", path=path())
        self.assertEqual(got["ts"], iso(NOW))
        self.assertTrue(got["probe"])

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

    def test_torn_tail_then_two_rejects_seen(self):
        target = path()

        with open(target, "w", encoding="utf-8") as fh:
            fh.write('{"run_id": "torn-fragment-without-newline')

        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        rows, skipped = ledger.load(target)
        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(skipped, 1)
        self.assertEqual({r["run_id"] for r in rows}, {"r1", "r2"})
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, path=target))

    def test_torn_midfile_three_of_four_counted(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)

        with open(target, "a", encoding="utf-8") as fh:
            fh.write('{"run_id": "torn-midfile-fragment')

        ledger.record(entry(run_id="r3", ts=ago(3)), path=target)
        ledger.record(entry(run_id="r4", ts=ago(4)), path=target)
        rows, skipped = ledger.load(target)
        self.assertEqual(len(rows), 4)
        self.assertGreaterEqual(skipped, 1)
        self.assertEqual({r["run_id"] for r in rows}, {"r1", "r2", "r3", "r4"})
        mixed = path()
        write_rows(mixed, [entry(run_id="r1", ts=ago(1)),
                           entry(run_id="r2", ts=ago(2))])

        with open(mixed, "a", encoding="utf-8") as fh:
            fh.write('{"run_id": "torn-r3-fragment\n')

        write_rows(mixed, [entry(run_id="r4", ts=ago(4))])
        rows, skipped = ledger.load(mixed)
        self.assertEqual(len(rows), 3)
        self.assertGreaterEqual(skipped, 1)
        self.assertEqual({r["run_id"] for r in rows}, {"r1", "r2", "r4"})
        slot = ledger.rollup(mixed, NOW)["gemini-3.8-flash"]["code"]
        self.assertEqual(slot["rejected"], 3)

    def test_record_short_write_raises(self):
        target = path()
        real_write = os.write

        def short(fd, buf):
            keep = len(buf) - 1 if len(buf) > 1 else 0
            return real_write(fd, buf[:keep]) if keep else 0

        os.write = short

        try:
            with self.assertRaises(OSError):
                ledger.record(entry(), path=target)
        finally:
            os.write = real_write

    def test_ledger_error_is_value_error(self):
        self.assertTrue(issubclass(ledger.LedgerError, ValueError))

    def test_directory_raises_and_cli_exits_4(self):
        target = tempfile.mkdtemp()

        with self.assertRaises(ledger.LedgerError):
            ledger.load(target)

        with self.assertRaises(ledger.LedgerError):
            ledger.demoted(MODEL, "code", now=NOW, path=target)

        with self.assertRaises(ledger.LedgerError):
            ledger.rollup(target, NOW)

        state = tempfile.mkdtemp()
        os.mkdir(os.path.join(state, "writer-ledger.jsonl"))
        os.environ["AUTOOS_STATE_DIR"] = state

        try:
            buf = io.StringIO()

            with contextlib.redirect_stderr(buf):
                self.assertEqual(ledger.main(["demoted", MODEL, "code"]), 4)

            self.assertIn("ledger", buf.getvalue().lower())
        finally:
            del os.environ["AUTOOS_STATE_DIR"]

    def test_fifo_does_not_block(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo unavailable")

        target = os.path.join(tempfile.mkdtemp(), "fifo")
        os.mkfifo(target)
        out = {}

        def run_load():
            try:
                ledger.load(target)
            except Exception as ex:  # noqa: BLE001 - recorded for the assert
                out["ex"] = ex
            else:
                out["ex"] = None

        worker = threading.Thread(target=run_load, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "load blocked on a FIFO")
        self.assertIsInstance(out.get("ex"), ledger.LedgerError)

        def run_demoted():
            try:
                ledger.demoted(MODEL, "code", now=NOW, path=target)
            except Exception as ex:  # noqa: BLE001 - recorded for the assert
                out["demoted"] = ex

        worker = threading.Thread(target=run_demoted, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "demoted blocked on a FIFO")
        self.assertIsInstance(out.get("demoted"), ledger.LedgerError)

    def test_demoted_threshold_window_spelling(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, path=target))
        self.assertTrue(ledger.demoted("VERTEX/gemini-3.8-flash:free", "code", now=NOW, path=target))
        self.assertFalse(ledger.demoted(MODEL, "ops", now=NOW, path=target))
        old = path()
        ledger.record(entry(run_id="o1", ts=ago(30)), path=old)
        ledger.record(entry(run_id="o2", ts=ago(31)), path=old)
        self.assertFalse(ledger.demoted(MODEL, "code", now=NOW, path=old))

    def test_canonical_spellings_demote_each_other(self):
        full = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
        stored = [full, "nemotron-3-super"]

        for query in (full, "nemotron-3-super", "NEMOTRON-3-SUPER"):
            target = path()
            ledger.record(entry(run_id="r1", writer_model_served=stored[0], ts=ago(1)),
                          path=target)
            ledger.record(entry(run_id="r2", writer_model_served=stored[1], ts=ago(2)),
                          path=target)

            with self.subTest(query=query):
                self.assertTrue(ledger.demoted(query, "code", now=NOW, path=target))

    def test_flash_and_flash_lite_count_as_same(self):
        # Conservative on purpose: 'gemini-3.8-flash' vs
        # 'gemini-3.8-flash-lite' share a prefix at a '-' boundary, so a
        # reject of either demotes both.
        target = path()
        ledger.record(entry(run_id="r1", writer_model_served="vertex/gemini-3.8-flash-lite",
                            ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", writer_model_served="vertex/gemini-3.8-flash-lite",
                            ts=ago(2)), path=target)
        self.assertTrue(ledger.demoted("vertex/gemini-3.8-flash", "code", now=NOW, path=target))

    def test_probe_clears_pair(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        ledger.probe_pass(MODEL, "code", path=target)
        self.assertFalse(ledger.demoted(MODEL, "code", now=NOW, path=target))
        ledger.record(entry(run_id="r3", ts=minutes(1)), path=target)
        ledger.record(entry(run_id="r4", ts=minutes(2)), path=target)
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, path=target))

    def test_demoted_ignores_far_future_rows(self):
        target = path()
        write_rows(target, [entry(run_id="f1", ts=minutes(6)),
                            entry(run_id="f2", ts=minutes(60))])
        self.assertFalse(ledger.demoted(MODEL, "code", now=NOW, path=target))
        rows, skipped = ledger.load(target)
        self.assertEqual((len(rows), skipped), (2, 0))
        self.assertEqual(ledger.rollup(target, NOW), {})

    def test_future_probe_does_not_clear(self):
        target = path()
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(2)), path=target)
        probe = entry(run_id="px", verdict="accepted", ts=minutes(30),
                      reviewer="probe", probe=True)
        del probe["failure_class"]
        write_rows(target, [probe])
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, path=target))

    def test_duplicate_run_id_counts_once(self):
        target = path()
        ledger.record(entry(run_id="r1", verdict="reworked", ts=ago(3)), path=target)
        ledger.record(entry(run_id="r1", verdict="rejected", ts=ago(1)), path=target)
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, threshold=1, path=target))
        self.assertFalse(ledger.demoted(MODEL, "code", now=NOW, threshold=2, path=target))
        dup = path()
        ledger.record(entry(run_id="r1", ts=ago(2)), path=dup)
        ledger.record(entry(run_id="r1", ts=ago(1)), path=dup)
        self.assertTrue(ledger.demoted(MODEL, "code", now=NOW, threshold=1, path=dup))
        self.assertFalse(ledger.demoted(MODEL, "code", now=NOW, threshold=2, path=dup))

    def test_rollup_all_time_window_and_dedupe(self):
        target = path()
        good = entry(run_id="a1", verdict="accepted")
        del good["failure_class"]
        ledger.record(good, path=target)
        ledger.record(entry(run_id="r1", ts=ago(1)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(30)), path=target)
        ledger.record(entry(run_id="r2", ts=ago(29)), path=target)  # same run: counts once
        slot = ledger.rollup(target, NOW)["gemini-3.8-flash"]["code"]
        self.assertEqual((slot["accepted"], slot["rejected"]), (1, 2))
        week = ledger.rollup(target, NOW, window_days=7)["gemini-3.8-flash"]["code"]
        self.assertEqual((week["accepted"], week["rejected"]), (1, 1))

        with self.assertRaises(ValueError):
            ledger.rollup(target, NOW, window_days=-1)

        with self.assertRaises(ValueError):
            ledger.rollup(target, NOW, window_days="7")

    def test_rollup_and_cli(self):
        ledger._now = self._real_now
        target = path()
        good = entry(run_id="a1", verdict="accepted")
        del good["failure_class"]
        ledger.record(good, path=target)
        ledger.record(entry(run_id="r1"), path=target)
        ledger.record(entry(run_id="r2"), path=target)
        slot = ledger.rollup(target)["gemini-3.8-flash"]["code"]
        self.assertEqual((slot["accepted"], slot["rejected"]), (1, 2))
        state = tempfile.mkdtemp()
        os.rename(target, os.path.join(state, "writer-ledger.jsonl"))
        os.environ["AUTOOS_STATE_DIR"] = state

        try:
            self.assertEqual(ledger.main(["demoted", MODEL, "code"]), 3)
            self.assertEqual(ledger.main(["demoted", MODEL, "ops"]), 0)
            self.assertEqual(ledger.main(["probe", MODEL, "code"]), 0)
            buf = io.StringIO()

            with contextlib.redirect_stdout(buf):
                self.assertEqual(ledger.main(["rollup"]), 0)

            rolled = json.loads(buf.getvalue())
            self.assertEqual(rolled["gemini-3.8-flash"]["code"]["rejected"], 2)
            buf = io.StringIO()

            with contextlib.redirect_stdout(buf):
                self.assertEqual(ledger.main(["rollup", "--days", "7"]), 0)

            self.assertEqual(json.loads(buf.getvalue())["gemini-3.8-flash"]["code"]["rejected"], 2)
            buf = io.StringIO()

            with contextlib.redirect_stdout(buf):
                self.assertEqual(ledger.main(["accept", "cli-a1", "--reviewer", "t3",
                                              "--task-type", "code", "--writer-client", "opencode",
                                              "--writer-model", MODEL]), 0)

            stamped = json.loads(buf.getvalue())
            self.assertTrue(stamped["ts"].endswith("Z"))
            self.assertEqual(stamped["writer_model_served"], MODEL)
        finally:
            del os.environ["AUTOOS_STATE_DIR"]

    def test_cli_task_type_required(self):
        flags = ["--reviewer", "t3", "--writer-client", "c", "--writer-model", "m"]

        for cmd in ("accept", "reject", "rework"):
            argv = [cmd, "r1"] + flags

            if cmd != "accept":
                argv += ["--class", "syntax"]

            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as ctx:
                    ledger.main(argv)

                self.assertEqual(ctx.exception.code, 2)

    def test_cli_reject_failure_paths(self):
        flags = ["--reviewer", "t3", "--task-type", "code",
                 "--writer-client", "c", "--writer-model", "m"]

        with self.assertRaises(SystemExit) as ctx:
            ledger.main(["reject", "r1"] + flags)

        self.assertEqual(ctx.exception.code, 2)  # missing --class

        with self.assertRaises(SystemExit) as ctx:
            ledger.main(["reject", "r1", "--class", "bogus"] + flags)

        self.assertEqual(ctx.exception.code, 2)  # unknown class
        self.assertEqual(ledger.main(["reject", "bad id!", "--class", "syntax"] + flags), 2)
        self.assertEqual(ledger.main(["reject", "bad id!", "--class", "syntax",
                                      "--reviewer", "t3", "--task-type", "code"]), 2)

    def test_cli_takes_no_ts(self):
        flags = ["--reviewer", "t3", "--task-type", "code",
                 "--writer-client", "c", "--writer-model", "m",
                 "--ts", iso(NOW)]

        with self.assertRaises(SystemExit) as ctx:
            ledger.main(["accept", "r1"] + flags)

        self.assertEqual(ctx.exception.code, 2)

    def test_filesystem_owns_integrity(self):
        self.assertIn("filesystem", ledger.__doc__)

    def test_pep8_two_blank_lines_between_defs(self):
        text = (ROOT / "tools" / "autoos_writer_ledger.py").read_text(encoding="utf-8")
        names = [m.group(1) for m in re.finditer(r"^def (\w+)", text, re.M)]
        self.assertTrue(names)

        for name in names:
            with self.subTest(name=name):
                self.assertIsNotNone(
                    re.search(r"\n\n\ndef %s\b" % re.escape(name), "\n\n" + text),
                    "expected two blank lines before def %s" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
