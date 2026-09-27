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


def rec(route="r", cls="free", bucket="S0", effort="low", gate="pass",
        failure_class=None):
    if gate == "fail" and failure_class is None:
        failure_class = "logic"  # a route's own failure, so p_success counts it
    return entry(route=route, gate=gate, failure_class=failure_class,
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

    def test_the_minimal_rung_is_a_known_effort(self):
        # The resolver's CANONICAL_EFFORT_ORDER can hand a model "minimal"; a
        # record naming it must not be rejected and silently dropped.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            track.record(path, entry(effort="minimal"))
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

    def test_a_provider_or_containment_failure_does_not_lower_p(self):
        # REVFIX review 3: only `logic`/`capability` are the route's own answer
        # quality (spec 5.7). A provider (rc 8) or containment (rc 7) record
        # stays in the file for availability/escalation but must not move p.
        records = [rec(route="r1", gate="fail", failure_class="provider"),
                   rec(route="r1", gate="fail", failure_class="containment")]
        p, source = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "route")
        self.assertEqual(p, (1 + 0) / (1 + 1 + 0 + 0))  # no failures counted

    def test_a_headless_refusal_does_not_lower_p(self):
        # rc 6 (the client could not prompt headlessly) is not the route's answer.
        records = [rec(route="r1", gate="fail", failure_class="refusal")]
        p, _ = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(p, 1 / 2)

    def test_a_logic_failure_at_the_chosen_effort_does_lower_p(self):
        records = [rec(route="r1", gate="fail", failure_class="logic")]
        p, source = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "route")
        self.assertEqual(p, (1 + 0) / (1 + 1 + 0 + 1))  # 1/3

    def test_an_unknown_effort_record_matches_any_effort(self):
        # REVFIX review 1: a record stamped "unknown" (no rung known at write
        # time) must still be matched by a real query rung, or it never moves p.
        records = [rec(route="r1", gate="fail", failure_class="logic",
                       effort="unknown")]
        p, source = track.p_success(records, "r1", "free", "S0", "low", PRIORS)
        self.assertEqual(source, "route")
        self.assertEqual(p, (1 + 0) / (1 + 1 + 0 + 1))


class SpawnerRecordTests(unittest.TestCase):
    def _spawner_plan(self):
        """A plan track_entry() accepts: a gateway client on a known route."""
        return {"client": "opencode", "free": False,
                "route": {"combo": "t2-worker-clean", "class": "cheap", "card": {}}}

    def test_track_entry_stamps_the_resolvers_chosen_effort(self):
        # REVFIX review 1: without the rung, every record reads as effort
        # "unknown" and (before the wildcard) never matches the resolver's
        # bucket+effort filter, so a fail record could not lower p.
        cli = load_agent()
        plan = self._spawner_plan()
        plan["route"]["effort"] = "medium"
        tracked = cli.track_entry(plan, 0, 1.0)
        self.assertEqual(tracked["effort"], "medium")

    def test_track_entry_maps_a_none_effort_to_none(self):
        # A resolver route whose leg is not a reasoning model carries effort
        # None; the resolver scores it as "none", so the record must say so.
        cli = load_agent()
        plan = self._spawner_plan()
        plan["route"]["effort"] = None
        tracked = cli.track_entry(plan, 0, 1.0)
        self.assertEqual(tracked["effort"], "none")

    def test_track_entry_leaves_a_v1_route_effort_unknown(self):
        # A v1/--tier route carries no effort key at all.
        cli = load_agent()
        tracked = cli.track_entry(self._spawner_plan(), 0, 1.0)
        self.assertEqual(tracked["effort"], "unknown")

    def test_record_run_accepts_every_failure_class_track_entry_emits(self):
        # autoos-agent.track_entry() maps rc 5/6/7/8 to capability/refusal/
        # containment/provider. Before FAILURES named containment and provider,
        # validate() raised and record_run()'s except swallowed it - the record
        # was silently dropped (REVFIX). Pipe the real track_entry() output
        # through record_run(), not just assert on the returned dict.
        cli = load_agent()
        fields = {0: None, 5: "capability", 6: "refusal", 7: "containment",
                  8: "provider"}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "track-record.jsonl")
            for rc, failure in fields.items():
                with self.subTest(rc=rc):
                    tracked = cli.track_entry(self._spawner_plan(), rc, 1.0)
                    self.assertIsNotNone(tracked)
                    self.assertEqual(tracked["failure_class"], failure)
                    self.assertEqual(tracked["gate"], "pass" if rc == 0 else "fail")
                    self.assertTrue(cli.record_run(path, tracked),
                                    "rc %d must be recorded, not dropped" % rc)
            self.assertEqual(len(track.load(path)), len(fields))

    @unittest.skipIf(os.name == "nt", "chmod mode bits; POSIX only")
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
