"""Tests for tools/autoos_track.py - the routing track record (spec §5.6).

Pure stdlib, no I/O beyond a temp dir. Written before the implementation
(coding-principles 2). Run from anywhere:

    python3 tests/test_autoos_track.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_track as track  # noqa: E402


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(**overrides):
    """One fully valid record; overrides mutate a fresh copy."""
    base = {
        "route": "t2-worker",
        "class": "cheap",
        "served_leg": "unknown",
        "bucket": "S0",
        "effort": "unknown",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0,
        "latency_s": 1.5,
        "gate": "pass",
        "failure_class": None,
    }
    base.update(overrides)
    return base


def rec(route="r", cls="free", bucket="S0", effort="low", gate="pass"):
    return entry(route=route, gate=gate, failure_class=None,
                 **{"class": cls, "bucket": bucket, "effort": effort})


# alpha = beta = 1 for every class/bucket the tests ask about.
PRIORS = {"free": {"S0": {"alpha": 1, "beta": 1}},
          "cheap": {"S0": {"alpha": 1, "beta": 1}}}


class RecordLoadTests(unittest.TestCase):
    def test_record_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logs", "routing", "track-record.jsonl")
            first = entry(route="r1", gate="pass")
            second = entry(route="r2", gate="fail", failure_class="logic")
            track.record(path, first)
            track.record(path, second)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(track.load(path), [first, second])

    def test_record_writes_sorted_keys_one_line_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            track.record(path, entry(route="r1"))
            track.record(path, entry(route="r2", gate="fail", failure_class="capability"))
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], json.dumps(entry(route="r1"), sort_keys=True))
            self.assertEqual(list(json.loads(lines[0])), sorted(entry(route="r1")))

    def test_load_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(track.load(os.path.join(tmp, "nope.jsonl")), [])

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            good = entry(route="r1")
            later = entry(route="r2", gate="fail", failure_class="logic")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(good) + "\n")
                fh.write("not json at all\n")
                fh.write('["a", "list", "is", "not", "a", "record"]\n')
                fh.write("\n")
                fh.write(json.dumps(later) + "\n")
            self.assertEqual(track.load(path), [good, later])

    def test_a_missing_key_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = entry()
            del bad["gate"]
            with self.assertRaises(ValueError):
                track.record(os.path.join(tmp, "t.jsonl"), bad)

    def test_an_unknown_key_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                track.record(os.path.join(tmp, "t.jsonl"), entry(extra=1))

    def test_bad_values_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            for bad in ({"gate": "maybe"}, {"failure_class": "flaky"},
                        {"class": "nope"}, {"bucket": "S9"}, {"effort": "deepest"},
                        {"tokens_in": -1}, {"latency_s": "soon"}, {"route": ""}):
                with self.assertRaises(ValueError, msg=bad):
                    track.record(path, entry(**bad))
            self.assertFalse(os.path.exists(path))

    def test_a_bad_entry_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            track.record(path, entry())
            with self.assertRaises(ValueError):
                track.record(path, entry(gate="maybe"))
            self.assertEqual(len(track.load(path)), 1)


class SuccessEstimateTests(unittest.TestCase):
    def test_no_observations_falls_back_to_the_prior(self):
        p, source = track.p_success([], "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "prior")
        self.assertEqual(p, 1 / 2)

    def test_class_observations_win_over_the_prior(self):
        records = [rec(route="other", gate="pass"),
                   rec(route="other", gate="pass"),
                   rec(route="other", gate="fail")]
        p, source = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "class")
        self.assertEqual(p, (1 + 2) / (1 + 1 + 2 + 1))  # 3/5

    def test_route_observations_win_over_the_class(self):
        records = [rec(route="r1", gate="pass"),
                   rec(route="r1", gate="fail"),
                   rec(route="other", gate="pass"),
                   rec(route="other", gate="pass")]
        p, source = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "route")
        self.assertEqual(p, (1 + 1) / (1 + 1 + 1 + 1))  # 2/4

    def test_only_the_matching_bucket_and_effort_count(self):
        records = [rec(route="r1", bucket="S1", gate="pass"),   # wrong bucket
                   rec(route="r1", effort="high", gate="pass"),  # wrong effort
                   rec(route="r1", gate="fail")]
        p, source = track.p_success(records, "r1", "cheap", "S0", "low", PRIORS)
        self.assertEqual(source, "route")
        self.assertEqual(p, (1 + 0) / (1 + 1 + 0 + 1))  # 1/3

    def test_a_missing_prior_fails_closed(self):
        with self.assertRaises(ValueError):
            track.p_success([], "r1", "frontier", "S0", "low", PRIORS)
        with self.assertRaises(ValueError):
            track.p_success([], "r1", "free", "S4", "low", PRIORS)


class SpawnerRecordTests(unittest.TestCase):
    def test_the_record_step_never_raises_when_the_log_dir_is_unwritable(self):
        cli = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o500)
            try:
                path = os.path.join(tmp, "logs", "routing", "track-record.jsonl")
                self.assertFalse(cli.record_run(path, entry()))
            finally:
                os.chmod(tmp, 0o700)


if __name__ == "__main__":
    unittest.main()
