#!/usr/bin/env python3
"""Unit tests for tools/probe-recall.py (routing v2 spec sections 3.1 and 10).

Every `post()` in these tests is a fake: no test in this file makes a network
call, reads the OmniRoute key, or runs the live probe. Only the CLI's
``--dry-run`` path and its argument-error paths are exercised through
subprocess (both make no request); the full non-dry-run flow is exercised by
calling `main()` directly with `_load_agent_module`, `gateway_up` and
`make_post` monkeypatched to fakes.

Run from the repo root:

    python3 tests/test_probe_recall.py [ClassName]
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
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROBE = TOOLS / "probe-recall.py"

NEEDLE_RE = re.compile(r"The access code for (\w+) is (\d{6})\.")


def _load_module():
    """Load tools/probe-recall.py via an absolute path (a hyphen is not importable)."""
    spec = importlib.util.spec_from_file_location("probe_recall", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry():
    """A synthetic registry: free/paid/subscription/down providers, one bound model."""
    return {
        "providers": {
            "free": {"id": "free", "tier": "free"},
            "paid": {"id": "paid", "tier": "paid"},
            "sub": {"id": "sub", "tier": "subscription"},
            "down": {"id": "down", "tier": "free", "available": False},
        },
        "models": {
            "big": {"id": "big", "context_advertised": {"tokens": 1000000}},
            "small": {"id": "small", "context_advertised": {"tokens": 40000}},
            "zen": {"id": "zen", "client_bound": "opencode",
                    "context_advertised": {"tokens": 1000000}},
        },
        "routes": {
            "r1": {"id": "r1", "legs": ["free/big", "paid/small", "free/zen",
                                        "sub/big", "down/big"]},
            "r2": {"id": "r2", "legs": ["free/big", "down/small"],
                   "unavailable_legs": {"down/small": {"available": False}}},
        },
    }


class RecallPost:
    """A fake gateway that reads the haystack out of each request.

    mode "perfect" echoes every needle it finds; "partial" only the first
    two; "wrong" swaps every code for 000000; "junk" answers with prose.
    """

    def __init__(self, mode="perfect"):
        self.mode = mode
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        text = body["messages"][0]["content"]
        found = NEEDLE_RE.findall(text)
        if self.mode == "junk":
            content = "I read the whole document but could not find any access codes."
        elif self.mode == "wrong":
            # A code that differs from the drawn one by construction.
            content = json.dumps({name: "%06d" % ((int(code) + 1) % 1000000)
                                  for name, code in found})
        elif self.mode == "partial":
            content = json.dumps({name: code for name, code in found[:2]})
        else:
            content = json.dumps({name: code for name, code in found})
        prompt_tokens = len(text) // 4
        reply = {"choices": [{"message": {"role": "assistant", "content": content}}],
                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 37}}
        return 200, reply, None


class LadderPost:
    """Perfect recall while the haystack is under `limit_tokens`, junk above.

    The size of a request is estimated the way the probe itself estimates
    haystacks: 4 characters per token.
    """

    def __init__(self, limit_tokens):
        self.limit_tokens = limit_tokens
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        text = body["messages"][0]["content"]
        if len(text) / 4.0 <= self.limit_tokens:
            content = json.dumps({name: code for name, code in NEEDLE_RE.findall(text)})
        else:
            content = "The document is too long; I cannot read all of it."
        reply = {"choices": [{"message": {"role": "assistant", "content": content}}],
                 "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": 37}}
        return 200, reply, None


class StatusPost:
    """Always answers with the same (status, None, error) tuple."""

    def __init__(self, status, error="probe unavailable"):
        self.status = status
        self.error = error
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        return self.status, None, self.error


class LegsToProbeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.registry = _registry()

    def test_every_distinct_leg_in_registry_order(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(self.registry)]
        self.assertEqual(legs, ["free/big", "paid/small", "free/zen", "sub/big",
                                "down/big", "down/small"])

    def test_paid_tier_leg_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["paid/small"], "tier: paid")

    def test_subscription_tier_leg_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["sub/big"], "tier: subscription")

    def test_leg_in_unavailable_legs_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["down/small"], "unavailable_legs: down/small")

    def test_provider_available_false_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIn("available false", reasons["down/big"])

    def test_client_bound_leg_is_skipped_naming_the_client(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["free/zen"], "client_bound: probe through opencode")

    def test_a_probeable_leg_has_no_skip_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIsNone(reasons["free/big"])

    def test_only_legs_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(
            self.registry, only_legs=("free/big",))]
        self.assertEqual(legs, ["free/big"])

    def test_only_routes_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(
            self.registry, only_routes=("r2",))]
        self.assertEqual(legs, ["free/big", "down/small"])


class RefusedLegTests(unittest.TestCase):
    """refused_leg: naming a non-free leg with --leg is a hard refusal."""

    def setUp(self):
        self.mod = _load_module()
        self.registry = _registry()

    def test_a_named_paid_leg_is_refused(self):
        self.assertEqual(self.mod.refused_leg(self.registry, ["paid/small"]),
                         ("paid/small", "paid"))

    def test_a_named_subscription_leg_is_refused(self):
        self.assertEqual(self.mod.refused_leg(self.registry, ["sub/big"]),
                         ("sub/big", "subscription"))

    def test_a_named_free_leg_is_not_refused(self):
        self.assertIsNone(self.mod.refused_leg(self.registry, ["free/big"]))

    def test_an_unresolvable_named_leg_is_not_a_refusal(self):
        # Unresolvable legs surface as a skip reason in legs_to_probe, not as
        # a paid-leg refusal: their tier is unknown, not non-free.
        self.assertIsNone(self.mod.refused_leg(self.registry, ["nosuch/model"]))

    def test_unnamed_non_free_legs_are_not_refusals(self):
        self.assertIsNone(self.mod.refused_leg(self.registry, []))

    def test_a_paid_leg_named_alongside_a_free_one_is_refused(self):
        self.assertEqual(self.mod.refused_leg(self.registry, ["free/big", "paid/small"]),
                         ("paid/small", "paid"))


class ParseSizesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_the_default_string_parses_to_the_four_ladder_sizes(self):
        self.assertEqual(self.mod.parse_sizes("32000,128000,256000,500000"),
                         [32000, 128000, 256000, 500000])

    def test_sizes_are_sorted_ascending_and_de_duplicated(self):
        self.assertEqual(self.mod.parse_sizes("8000,4000,8000"), [4000, 8000])

    def test_junk_raises(self):
        with self.assertRaises(ValueError):
            self.mod.parse_sizes("4000,abc")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            self.mod.parse_sizes("")

    def test_non_positive_raises(self):
        for bad in ("0", "-4000"):
            with self.assertRaises(ValueError):
                self.mod.parse_sizes(bad)


class HaystackTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _haystack(self, size, seed="4000:0"):
        return self.mod.build_haystack(size, seed=seed)

    def test_size_is_within_10_percent_of_the_request(self):
        for size in (2000, 5000):
            text, _ = self._haystack(size)
            estimated = len(text) / 4.0
            self.assertGreaterEqual(estimated, size * 0.9, size)
            self.assertLessEqual(estimated, size * 1.1, size)

    def test_five_needles_are_present_exactly_once_and_unique(self):
        text, needles = self._haystack(5000)
        self.assertEqual(len(needles), 5)
        names = [name for name, _ in needles]
        codes = [code for _, code in needles]
        self.assertEqual(len(set(names)), 5)
        self.assertEqual(len(set(codes)), 5)
        self.assertEqual(NEEDLE_RE.findall(text),
                         [(name, code) for name, code in needles])
        for name, code in needles:
            self.assertEqual(text.count("The access code for %s is %s." % (name, code)), 1)

    def test_needles_sit_at_evenly_spread_depths(self):
        text, needles = self._haystack(5000)
        for i, (name, code) in enumerate(needles):
            position = text.index("The access code for %s is %s." % (name, code))
            expected = (i + 1) / (len(needles) + 1)
            self.assertLess(abs(position / float(len(text)) - expected), 0.05,
                            "needle %d at %.3f, expected ~%.3f"
                            % (i, position / float(len(text)), expected))

    def test_same_seed_is_deterministic(self):
        self.assertEqual(self._haystack(2000, seed="s"), self._haystack(2000, seed="s"))

    def test_a_different_seed_moves_the_codes(self):
        _, needles_a = self._haystack(2000, seed="a")
        _, needles_b = self._haystack(2000, seed="b")
        self.assertNotEqual([code for _, code in needles_a],
                            [code for _, code in needles_b])


class RecallBodyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_the_body_carries_the_leg_the_haystack_and_max_tokens(self):
        haystack = "line one.\nline two.\n"
        body = self.mod.recall_body("free/m", haystack, max_tokens=99)
        self.assertEqual(body["model"], "free/m")
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertIn(haystack, body["messages"][0]["content"])
        self.assertIn("JSON", body["messages"][0]["content"])
        self.assertEqual(body["max_tokens"], 99)
        self.assertNotIn("tools", body)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.needles = [("Mira", "123456"), ("Otto", "234567"), ("Pia", "345678"),
                        ("Quinn", "456789"), ("Ravi", "567890")]

    def _score(self, content):
        return self.mod.score_recall(content, self.needles)

    def test_exact_reply_scores_one(self):
        reply = json.dumps({name: code for name, code in self.needles})
        recall, note = self._score(reply)
        self.assertEqual(recall, 1.0)
        self.assertIn("5/5", note)

    def test_partial_reply_scores_two_fifths_and_names_the_missing(self):
        reply = json.dumps({name: code for name, code in self.needles[:2]})
        recall, note = self._score(reply)
        self.assertEqual(recall, 0.4)
        self.assertIn("Pia", note)
        self.assertIn("Ravi", note)

    def test_prose_junk_scores_zero(self):
        recall, note = self._score("I could not find any access codes.")
        self.assertEqual(recall, 0.0)
        self.assertIn("did not parse", note)

    def test_unparseable_json_scores_zero(self):
        recall, _ = self._score('{"Mira": "123456", "Otto": ')
        self.assertEqual(recall, 0.0)

    def test_wrong_codes_score_zero(self):
        reply = json.dumps({name: "000000" for name, _ in self.needles})
        recall, _ = self._score(reply)
        self.assertEqual(recall, 0.0)

    def test_a_fenced_json_reply_still_scores(self):
        reply = "```json\n%s\n```" % json.dumps(
            {name: code for name, code in self.needles})
        recall, _ = self._score(reply)
        self.assertEqual(recall, 1.0)

    def test_a_numeric_code_counts_as_its_string(self):
        reply = json.dumps({name: int(code) for name, code in self.needles})
        recall, _ = self._score(reply)
        self.assertEqual(recall, 1.0)


class RunTrialTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.mod._sleep = lambda secs: None  # no real waiting in tests

    def test_a_perfect_reply_measures_full_recall_and_the_usage(self):
        post = RecallPost("perfect")
        result = self.mod.run_trial("free/m", 4000, post, seed="4000:0")
        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["status"], 200)
        prompt_tokens = result["prompt_tokens"]
        self.assertIsInstance(prompt_tokens, int)
        self.assertGreater(prompt_tokens, 3000)
        self.assertEqual(result["completion_tokens"], 37)

    def test_a_wrong_reply_measures_zero_recall(self):
        post = RecallPost("wrong")
        result = self.mod.run_trial("free/m", 4000, post, seed="4000:0")
        self.assertEqual(result["recall"], 0.0)
        self.assertEqual(result["status"], 200)

    def test_a_429_trial_measures_nothing(self):
        post = StatusPost(429)
        result = self.mod.run_trial("free/m", 4000, post, seed="4000:0")
        self.assertIsNone(result["recall"])
        self.assertEqual(len(post.calls), 1 + len(self.mod.RETRY_DELAYS_S))

    def test_a_429_then_200_retries_and_measures(self):
        pending = [(429, None, "rate limited")]
        post = RecallPost("perfect")
        real = post.__call__

        def flaky(body):
            if pending:
                return pending.pop(0)
            return real(body)

        sleeps = []
        with mock.patch.object(self.mod, "_sleep", side_effect=sleeps.append):
            result = self.mod.run_trial("free/m", 4000, flaky, seed="4000:0")
        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["status"], 200)
        self.assertEqual(sleeps, [self.mod.RETRY_DELAYS_S[0]])

    def test_a_401_trial_measures_nothing_without_retrying(self):
        post = StatusPost(401)
        result = self.mod.run_trial("free/m", 4000, post, seed="4000:0")
        self.assertIsNone(result["recall"])
        self.assertEqual(len(post.calls), 1)

    def test_any_non_200_trial_measures_nothing(self):
        # Live 2026-09-27: groq answered 413 (free-tier TPM 8000 < a 25k
        # prompt) and the old rule scored it recall 0.0 and wrote it. A
        # non-200 says nothing about the model's recall: never a verdict.
        for status in (400, 404, 413, 422):
            post = StatusPost(status, "HTTP %d" % status)
            result = self.mod.run_trial("free/m", 4000, post, seed="4000:0")
            self.assertIsNone(result["recall"], status)


class ClassifyLadderTests(unittest.TestCase):
    """The verdict rule: the largest size where every trial recalled >= 0.9."""

    def setUp(self):
        self.mod = _load_module()

    @staticmethod
    def outcome(size, recalls):
        return {"size": size, "recalls": recalls,
                "statuses": [200] * len(recalls)}

    def test_all_sizes_pass_gives_the_largest(self):
        verdict = self.mod.classify_ladder([
            self.outcome(4000, [1.0, 1.0]),
            self.outcome(8000, [1.0, 0.95]),
            self.outcome(16000, [1.0, 1.0])])
        self.assertEqual(verdict["tokens"], 16000)
        self.assertFalse(verdict["no_verdict"])
        self.assertEqual(verdict["detail"], {"4000": 1.0, "8000": 0.95, "16000": 1.0})

    def test_a_failing_smallest_size_gives_no_tokens(self):
        verdict = self.mod.classify_ladder([
            self.outcome(4000, [0.4, 0.6]),
            self.outcome(8000, [1.0, 1.0])])
        self.assertIsNone(verdict["tokens"])
        self.assertFalse(verdict["no_verdict"])

    def test_a_failing_middle_size_gives_the_largest_below_it(self):
        verdict = self.mod.classify_ladder([
            self.outcome(4000, [1.0, 1.0]),
            self.outcome(8000, [0.8, 1.0]),
            self.outcome(16000, [1.0, 1.0])])
        self.assertEqual(verdict["tokens"], 4000)

    def test_recall_of_exactly_the_pass_bar_passes(self):
        verdict = self.mod.classify_ladder([self.outcome(4000, [0.9, 0.9])])
        self.assertEqual(verdict["tokens"], 4000)

    def test_only_non_measurements_is_a_no_verdict(self):
        verdict = self.mod.classify_ladder([self.outcome(4000, [None, None])])
        self.assertTrue(verdict["no_verdict"])
        self.assertIsNone(verdict["tokens"])
        self.assertEqual(verdict["detail"], {"4000": None})

    def test_a_mixed_size_still_passes_on_its_measured_trials(self):
        verdict = self.mod.classify_ladder([self.outcome(4000, [1.0, None])])
        self.assertEqual(verdict["tokens"], 4000)
        self.assertFalse(verdict["no_verdict"])

    def test_detail_carries_the_worst_measured_trial_per_size(self):
        verdict = self.mod.classify_ladder([self.outcome(4000, [1.0, 0.8])])
        self.assertEqual(verdict["detail"], {"4000": 0.8})


class ProbeModelTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.mod._sleep = lambda secs: None

    def test_the_ladder_stops_after_the_first_failing_size(self):
        post = LadderPost(12000)  # 4000 and 8000 answer, 16000 does not
        outcomes, requests = self.mod.probe_model(
            "free/m", [4000, 8000, 16000, 32000], post, trials=2)
        self.assertEqual([o["size"] for o in outcomes], [4000, 8000, 16000])
        self.assertEqual(len(requests), 6)  # 32000 was never probed
        self.assertEqual(len(post.calls), 6)

    def test_request_log_carries_leg_size_and_usage(self):
        post = RecallPost("perfect")
        _outcomes, requests = self.mod.probe_model("free/m", [4000], post, trials=2)
        self.assertEqual([r["leg"] for r in requests], ["free/m", "free/m"])
        self.assertEqual([r["size"] for r in requests], [4000, 4000])
        self.assertTrue(all(r["prompt_tokens"] > 3000 for r in requests))
        self.assertTrue(all(r["completion_tokens"] == 37 for r in requests))

    def test_trials_at_one_size_use_different_haystacks(self):
        post = RecallPost("perfect")
        self.mod.probe_model("free/m", [4000], post, trials=2)
        first = post.calls[0]["messages"][0]["content"]
        second = post.calls[1]["messages"][0]["content"]
        self.assertNotEqual(first, second)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "measured.json")

    def tearDown(self):
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def test_load_overlay_missing_file_is_empty_dict(self):
        self.assertEqual(self.mod.load_overlay(self.path), {})

    def test_save_then_load_round_trips(self):
        self.mod.save_overlay(self.path, {"models": {"m": {"context_usable": {"tokens": 8000}}}})
        self.assertEqual(self.mod.load_overlay(self.path),
                         {"models": {"m": {"context_usable": {"tokens": 8000}}}})

    def test_save_leaves_no_temp_file_behind(self):
        self.mod.save_overlay(self.path, {"models": {}})
        self.assertEqual(os.listdir(self.tmpdir), ["measured.json"])

    def test_record_verdict_keeps_unrelated_keys(self):
        overlay = {"legs": {"other/leg": {"tool_calls": {"value": "proven"}}},
                   "models": {"other": {"context_usable": {"tokens": 5000}},
                              "m": {"tool_calls": {"value": "proven"}}}}
        self.mod.record_verdict(overlay, "m", 8000, {"4000": 1.0},
                                "2026-09-27T00:00:00Z")
        self.assertEqual(overlay["legs"]["other/leg"],
                         {"tool_calls": {"value": "proven"}})
        self.assertEqual(overlay["models"]["other"],
                         {"context_usable": {"tokens": 5000}})
        self.assertEqual(overlay["models"]["m"]["tool_calls"],
                         {"value": "proven"})
        self.assertEqual(overlay["models"]["m"]["context_usable"]["tokens"], 8000)
        self.assertEqual(overlay["models"]["m"]["context_usable"]["source"], "probe")

    def test_record_verdict_without_tokens_writes_only_a_detail(self):
        overlay = {"models": {"m": {}}}
        self.mod.record_verdict(overlay, "m", None, {"4000": 0.4},
                                "2026-09-27T00:00:00Z")
        entry = overlay["models"]["m"]["context_usable"]
        self.assertNotIn("tokens", entry)
        self.assertEqual(entry["detail"], {"4000": 0.4})
        self.assertEqual(entry["source"], "probe")

    # Final review 2026-09-27: a run where 32k passed but 128k got only
    # non-200s (a 429 outlasting the retries) overwrote a proven 128k with
    # 32k. A ladder that stops at an UNMEASURED size proves nothing about
    # the sizes above it, so a larger earlier value survives.
    def _outcomes(self, *pairs):
        return [{"size": size, "recalls": list(recalls), "statuses": []}
                for size, recalls in pairs]

    def test_a_ladder_stopped_by_a_non_measurement_keeps_a_larger_previous_value(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 128000, "source": "probe"}}}}
        verdict = self.mod.classify_ladder(
            self._outcomes((32000, [0.95, 0.95]), (128000, [None, None])))
        self.assertTrue(verdict["open_above"])
        kept = self.mod.apply_verdict(overlay, "m", verdict, "2026-09-27T00:00:00Z")
        self.assertFalse(kept)
        self.assertEqual(overlay["models"]["m"]["context_usable"]["tokens"], 128000)
        self.assertIn("128000", overlay["models"]["m"]["context_usable_last_error"]["detail"])

    def test_an_unmeasured_smallest_size_keeps_the_previous_value(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 64000, "source": "probe"}}}}
        verdict = self.mod.classify_ladder(
            self._outcomes((4000, [None]), (8000, [1.0])))
        self.mod.apply_verdict(overlay, "m", verdict, "2026-09-27T00:00:00Z")
        self.assertEqual(overlay["models"]["m"]["context_usable"]["tokens"], 64000)

    def test_a_measured_failure_still_lowers_a_previous_value(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 128000, "source": "probe"}}}}
        verdict = self.mod.classify_ladder(
            self._outcomes((32000, [0.95]), (128000, [0.2, None])))
        self.assertFalse(verdict["open_above"])
        self.assertTrue(self.mod.apply_verdict(overlay, "m", verdict, "2026-09-27T00:00:00Z"))
        self.assertEqual(overlay["models"]["m"]["context_usable"]["tokens"], 32000)

    def test_an_open_ladder_above_a_smaller_previous_value_is_written(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 8000, "source": "probe"}}}}
        verdict = self.mod.classify_ladder(
            self._outcomes((32000, [0.95]), (128000, [None])))
        self.assertTrue(self.mod.apply_verdict(overlay, "m", verdict, "2026-09-27T00:00:00Z"))
        self.assertEqual(overlay["models"]["m"]["context_usable"]["tokens"], 32000)

    def test_an_all_no_verdict_ladder_writes_no_verdict(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 8000}}}}
        verdict = self.mod.classify_ladder(self._outcomes((4000, [None])))
        self.assertFalse(self.mod.apply_verdict(overlay, "m", verdict, "2026-09-27T00:00:00Z"))
        self.assertEqual(overlay["models"]["m"]["context_usable"], {"tokens": 8000})

    def test_record_no_verdict_never_touches_context_usable(self):
        overlay = {"models": {"m": {"context_usable": {"tokens": 12345, "source": "probe"}}}}
        self.mod.record_no_verdict(overlay, "m", "only 429s", "2026-09-27T00:00:00Z")
        self.assertEqual(overlay["models"]["m"]["context_usable"],
                         {"tokens": 12345, "source": "probe"})
        self.assertEqual(overlay["models"]["m"]["context_usable_last_error"]["detail"],
                         "only 429s")


class CliDryRunTests(unittest.TestCase):
    """--dry-run against the real, committed registry: no key, no network."""

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(PROBE), "--dry-run"] + list(extra),
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            env={k: v for k, v in os.environ.items() if k != "AUTOOS_OMNIROUTE_KEY"})

    def test_dry_run_lists_legs_and_exits_0(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            first = line.split("\t")[0]
            self.assertTrue("/" in first or first == "plan", line)
        self.assertTrue(any("sizes=" in l for l in lines),
                        "no probeable free leg in the plan")
        self.assertTrue(any("prompt_tokens_est=" in l for l in lines))

    def test_dry_run_naming_a_paid_leg_refuses_with_exit_4(self):
        # cheaperinference is a paid provider with a live route leg.
        proc = self._run("--leg", "cheaperinference/glm-5.2")
        self.assertEqual(proc.returncode, 4)
        self.assertIn("free legs only", proc.stderr + proc.stdout)

    def test_bad_registry_path_exits_2(self):
        proc = self._run("--registry", "/no/such/file.json")
        self.assertEqual(proc.returncode, 2)

    def test_bad_trials_value_exits_2(self):
        proc = self._run("--trials", "0")
        self.assertEqual(proc.returncode, 2)

    def test_bad_sizes_value_exits_2(self):
        proc = self._run("--sizes", "4000,abc")
        self.assertEqual(proc.returncode, 2)

    def test_unknown_argument_exits_2(self):
        proc = self._run("--not-a-real-flag")
        self.assertEqual(proc.returncode, 2)


class CliMainTests(unittest.TestCase):
    """main(), fully faked: no real key, gateway or network involved."""

    def setUp(self):
        self.mod = _load_module()
        self.mod._sleep = lambda secs: None
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "registry.json")
        self.overlay_path = os.path.join(self.tmpdir, "measured.json")
        self.registry = {
            "providers": {"free": {"id": "free", "tier": "free"},
                          "paid": {"id": "paid", "tier": "paid"}},
            "models": {"m": {"id": "m", "context_advertised": {"tokens": 1000000}}},
            "routes": {"r1": {"id": "r1", "legs": ["free/m"]},
                       "r2": {"id": "r2", "legs": ["paid/m"]}},
        }
        with io.open(self.registry_path, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)

    def tearDown(self):
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _argv(self, **extra):
        argv = ["--registry", self.registry_path, "--overlay", self.overlay_path,
                "--gateway", "http://example.invalid/v1/chat/completions"]
        for k, v in extra.items():
            argv += [k] if v is None else [k, v]
        return argv

    def _agent(self, key="sk-fake"):
        return mock.Mock(client_key=mock.Mock(return_value=key), ROOT="/nowhere")

    def test_no_key_exits_3_without_touching_the_network(self):
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent(key=None)), \
             mock.patch.object(self.mod, "gateway_up") as gw:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 3)
        gw.assert_not_called()

    def test_key_but_gateway_down_exits_3(self):
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=False), \
             mock.patch.object(self.mod, "make_post") as mp:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 3)
        mp.assert_not_called()

    def test_a_named_paid_leg_exits_4_with_zero_posts(self):
        post = RecallPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--leg": "paid/m"}))
        self.assertEqual(rc, 4)
        self.assertEqual(post.calls, [])

    def test_dry_run_makes_no_request(self):
        with mock.patch.object(self.mod, "_load_agent_module") as load_agent, \
             mock.patch.object(self.mod, "make_post") as mp:
            rc = self.mod.main(self._argv(**{"--dry-run": None}))
        self.assertEqual(rc, 0)
        load_agent.assert_not_called()
        mp.assert_not_called()

    def test_full_run_writes_the_largest_passing_size_to_the_overlay(self):
        post = LadderPost(12000)  # 4000 and 8000 recall, 16000 does not
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv(**{"--sizes": "4000,8000,16000",
                                              "--trials": "2"}))
        self.assertEqual(rc, 0)
        entry = self.mod.load_overlay(self.overlay_path)["models"]["m"]["context_usable"]
        self.assertEqual(entry["tokens"], 8000)
        self.assertEqual(entry["source"], "probe")
        self.assertEqual(entry["detail"], {"4000": 1.0, "8000": 1.0, "16000": 0.0})
        self.assertIn("at", entry)
        self.assertEqual(len(post.calls), 6)
        stdout = out.getvalue()
        self.assertIn("free/m\trequest\t4000\t", stdout)
        self.assertIn("free/m\tverdict\t8000\t", stdout)
        self.assertIn("total\t", stdout)

    def test_sizes_above_the_advertised_context_are_never_probed(self):
        self.registry["models"]["m"]["context_advertised"]["tokens"] = 8000
        with io.open(self.registry_path, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)
        post = RecallPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--sizes": "4000,8000,16000",
                                              "--trials": "1"}))
        self.assertEqual(rc, 0)
        self.assertEqual(len(post.calls), 2)  # 4000 and 8000 only
        entry = self.mod.load_overlay(self.overlay_path)["models"]["m"]["context_usable"]
        self.assertEqual(entry["tokens"], 8000)
        self.assertNotIn("16000", entry["detail"])

    def test_a_failing_smallest_size_keeps_the_registry_default(self):
        post = RecallPost("wrong")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--sizes": "4000,8000"}))
        self.assertEqual(rc, 0)
        entry = self.mod.load_overlay(self.overlay_path)["models"]["m"]["context_usable"]
        self.assertNotIn("tokens", entry)
        self.assertEqual(entry["detail"], {"4000": 0.0})
        self.assertNotIn("8000", entry["detail"])  # the ladder stopped

    def test_no_verdict_statuses_keep_the_old_overlay_value(self):
        self.mod.save_overlay(self.overlay_path, {
            "models": {"m": {"context_usable": {"tokens": 12345, "source": "probe"}}}})
        post = StatusPost(429)
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv(**{"--sizes": "4000"}))
        self.assertEqual(rc, 0)
        models = self.mod.load_overlay(self.overlay_path)["models"]
        self.assertEqual(models["m"]["context_usable"],
                         {"tokens": 12345, "source": "probe"})
        self.assertIn("429", models["m"]["context_usable_last_error"]["detail"])
        self.assertIn("free/m\tno-verdict", out.getvalue())

    def test_the_key_is_never_written_to_the_overlay_or_stdout(self):
        post = RecallPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent(key="sk-SECRET-not-printed")), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.mod.main(self._argv(**{"--sizes": "4000"}))
        self.assertNotIn("sk-SECRET-not-printed", out.getvalue())
        with io.open(self.overlay_path, encoding="utf-8") as fh:
            overlay_text = fh.read()
        self.assertNotIn("sk-SECRET-not-printed", overlay_text)

    def test_skipped_legs_are_announced_and_never_probed(self):
        post = RecallPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 0)
        self.assertIn("paid/m\tskip\ttier: paid", out.getvalue())
        # free/m is the only probeable leg: 2 trials x the 4 default sizes,
        # all under the 1M advertised context. The paid leg contributed zero.
        self.assertEqual(len(post.calls), 8)


class DenyRuleTests(unittest.TestCase):
    """Item 1 (TOOLFIX): probe_common's shared skip logic also skips a
    policy-denied free leg (probe-recall uses probe_common.legs_to_probe)."""

    def setUp(self):
        self.mod = _load_module()
        self.registry = {
            "providers": {
                "free": {"id": "free", "tier": "free", "available": True},
                "groq": {"id": "groq", "tier": "free", "available": True},
            },
            "models": {"big": {"id": "big",
                               "context_advertised": {"tokens": 1000000}}},
            "routes": {"r1": {"id": "r1", "legs": ["free/big", "groq/big"]}},
            "policy": {"leg_rules": [
                {"id": "deny-groq", "match": "groq/*", "allow": False, "reason": "x"},
            ]},
        }

    def test_a_denied_free_leg_is_skipped_with_the_rule_id(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["groq/big"], "policy: denied by deny-groq")


if __name__ == "__main__":
    unittest.main()
