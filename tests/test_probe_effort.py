#!/usr/bin/env python3
"""Unit tests for tools/probe-effort.py and tools/probe_common.py (routing
v2 spec sections 3.1, 5.5 and 10).

Every `post()` in these tests is a fake: no test in this file makes a network
call, reads the OmniRoute key, or runs the live probe. Only the CLI's
``--dry-run`` path and its argument-error paths are exercised through
subprocess (both make no request); the full non-dry-run flow is exercised by
calling `main()` directly with `_load_agent_module`, `gateway_up` and
`make_post` monkeypatched to fakes.

Run from the repo root:

    python3 tests/test_probe_effort.py [ClassName]
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROBE = TOOLS / "probe-effort.py"
COMMON = TOOLS / "probe_common.py"


def _load_module():
    """Load tools/probe-effort.py via an absolute path (a hyphen is not importable)."""
    spec = importlib.util.spec_from_file_location("probe_effort", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_common():
    """Load tools/probe_common.py via an absolute path, as its own module."""
    spec = importlib.util.spec_from_file_location("probe_common_for_tests", str(COMMON))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The fixed task set, read once for the fake gateways below (the per-test
# reloads of the probe module re-execute it; the answers never change).
TASKS = _load_module().TASKS
EXPECTED_ANSWERS = ["396", "6", "seborp", "42", "13"]


def _registry():
    """A synthetic registry: free/paid providers, reasoning and plain models."""
    return {
        "providers": {
            "free": {"id": "free", "tier": "free"},
            "paid": {"id": "paid", "tier": "paid"},
        },
        "models": {
            "reasoner": {"id": "reasoner", "reasoning": True,
                         "effort_ladder": ["none", "low", "medium", "high"],
                         "output_max": 100000,
                         "context_advertised": {"tokens": 200000}},
            "capped": {"id": "capped", "reasoning": True,
                       "effort_ladder": ["low", "high"],
                       "output_max": 32768},
            "plain": {"id": "plain", "reasoning": False, "effort_ladder": []},
            "noladder": {"id": "noladder", "reasoning": True, "effort_ladder": []},
        },
        "routes": {
            "r1": {"id": "r1", "legs": ["free/reasoner", "paid/reasoner",
                                        "free/plain", "free/noladder"]},
            "r2": {"id": "r2", "legs": ["free/capped"]},
        },
    }


class EffortPost:
    """A fake gateway for the fixed task set.

    mode "perfect" answers every puzzle exactly (five lines, the last line
    the fifth answer); "wrong" gets the third answer wrong; "junk" replies
    with prose. `reasoning` becomes usage.completion_tokens_details.
    reasoning_tokens: an int reported by every call, or a list consumed one
    entry per call (a None entry: that call reports no reasoning tokens).
    """

    def __init__(self, mode="perfect", reasoning=None, completion=37):
        self.mode = mode
        self.reasoning = reasoning
        self.completion = completion
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        expected = [answer for _question, answer in TASKS]
        if self.mode == "junk":
            content = "Let me think about these questions carefully."
        elif self.mode == "wrong":
            content = "\n".join(
                answer if i != 2 else "999" for i, answer in enumerate(expected))
        else:
            content = "\n".join(expected)
        usage = {"prompt_tokens": 120, "completion_tokens": self.completion}
        reasoning = self.reasoning
        if isinstance(reasoning, list):
            reasoning = (reasoning[len(self.calls) - 1]
                         if len(self.calls) <= len(reasoning) else None)
        if reasoning is not None:
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
        reply = {"choices": [{"message": {"role": "assistant",
                                          "content": content}}],
                 "usage": usage}
        return 200, reply, None


class RungPost:
    """Perfect for every rung but `fail_rung`, which answers `status`."""

    def __init__(self, fail_rung, status):
        self.fail_rung = fail_rung
        self.status = status
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        if body.get("reasoning_effort") == self.fail_rung:
            return self.status, None, "probe unavailable"
        content = "\n".join(answer for _question, answer in TASKS)
        reply = {"choices": [{"message": {"role": "assistant",
                                          "content": content}}],
                 "usage": {"prompt_tokens": 120, "completion_tokens": 37,
                           "completion_tokens_details":
                               {"reasoning_tokens": 500}}}
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


class TaskSetTests(unittest.TestCase):
    """The fixed task set: five deterministic puzzles, answers locked."""

    def setUp(self):
        self.mod = _load_module()

    def test_five_puzzles_with_exact_checkable_answers(self):
        self.assertEqual([answer for _question, answer in self.mod.TASKS],
                         EXPECTED_ANSWERS)
        self.assertEqual(len(set(EXPECTED_ANSWERS)), 5)
        self.assertEqual(len(self.mod.TASKS), 5)

    def test_the_prompt_carries_every_puzzle_and_the_last_line_rule(self):
        for question, _answer in self.mod.TASKS:
            self.assertIn(question, self.mod.TASK_PROMPT)
        self.assertIn("last line", self.mod.TASK_PROMPT)


class EffortLegsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.registry = _registry()

    def test_a_free_reasoning_leg_with_a_ladder_is_probeable(self):
        reasons = dict(self.mod.effort_legs(self.registry))
        self.assertIsNone(reasons["free/reasoner"])
        self.assertIsNone(reasons["free/capped"])

    def test_a_paid_leg_keeps_the_shared_tier_skip_reason(self):
        reasons = dict(self.mod.effort_legs(self.registry))
        self.assertEqual(reasons["paid/reasoner"], "tier: paid")

    def test_a_non_reasoning_model_is_skipped(self):
        reasons = dict(self.mod.effort_legs(self.registry))
        self.assertEqual(reasons["free/plain"], "not a reasoning model")

    def test_a_reasoning_model_without_a_ladder_is_skipped(self):
        reasons = dict(self.mod.effort_legs(self.registry))
        self.assertEqual(reasons["free/noladder"], "no effort_ladder")

    def test_only_legs_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.effort_legs(
            self.registry, only_legs=("free/reasoner",))]
        self.assertEqual(legs, ["free/reasoner"])

    def test_only_routes_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.effort_legs(
            self.registry, only_routes=("r2",))]
        self.assertEqual(legs, ["free/capped"])


class MaxTokensTests(unittest.TestCase):
    """Spec section 5.5: >= 48k at any effort, >= 64k at high and above,
    capped by the model's output_max."""

    def setUp(self):
        self.mod = _load_module()

    def test_any_effort_rung_gets_48k(self):
        for rung in ("none", "low", "medium", "minimal"):
            self.assertEqual(self.mod.max_tokens_for(rung, 100000), 48000, rung)

    def test_high_and_above_get_64k(self):
        for rung in ("high", "xhigh", "max"):
            self.assertEqual(self.mod.max_tokens_for(rung, 100000), 64000, rung)

    def test_output_max_caps_the_budget(self):
        self.assertEqual(self.mod.max_tokens_for("high", 32768), 32768)
        self.assertEqual(self.mod.max_tokens_for("none", 16384), 16384)

    def test_a_missing_output_max_leaves_the_bar_uncapped(self):
        self.assertEqual(self.mod.max_tokens_for("high", None), 64000)
        self.assertEqual(self.mod.max_tokens_for("low", None), 48000)


class EffortBodyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_rung_none_omits_the_reasoning_effort_field(self):
        body = self.mod.effort_body("free/m", "none", 48000)
        self.assertEqual(body["model"], "free/m")
        self.assertEqual(body["max_tokens"], 48000)
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], self.mod.TASK_PROMPT)

    def test_a_real_rung_is_sent_as_reasoning_effort(self):
        body = self.mod.effort_body("free/m", "low", 48000)
        self.assertEqual(body["reasoning_effort"], "low")


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _score(self, content):
        return self.mod.score_effort(content)

    def _answers(self, override=None):
        lines = list(EXPECTED_ANSWERS)
        if override is not None:
            lines[2] = override
        return "\n".join(lines)

    def test_the_exact_five_answers_pass(self):
        passed, note = self._score(self._answers())
        self.assertIs(passed, True)
        self.assertIn("5/5", note)

    def test_one_wrong_answer_fails_and_names_it(self):
        passed, note = self._score(self._answers(override="999"))
        self.assertIs(passed, False)
        self.assertIn("answer 3", note)
        self.assertIn("999", note)

    def test_a_preamble_line_fails_the_line_count(self):
        passed, note = self._score("Here are the answers:\n" + self._answers())
        self.assertIs(passed, False)
        self.assertIn("got 6", note)

    def test_a_missing_answer_fails_the_line_count(self):
        passed, note = self._score("\n".join(EXPECTED_ANSWERS[:4]))
        self.assertIs(passed, False)
        self.assertIn("got 4", note)

    def test_an_empty_reply_fails(self):
        passed, _note = self._score("")
        self.assertIs(passed, False)

    def test_none_content_fails_without_raising(self):
        passed, _note = self._score(None)
        self.assertIs(passed, False)

    def test_blank_lines_between_answers_are_ignored(self):
        passed, _note = self._score(
            "\n".join(a + "\n" for a in EXPECTED_ANSWERS))
        self.assertIs(passed, True)

    def test_answer_lines_are_stripped_before_comparing(self):
        passed, _note = self._score(
            "\n".join(" %s " % a for a in EXPECTED_ANSWERS))
        self.assertIs(passed, True)


class RunEffortTrialTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.mod._sleep = lambda secs: None  # no real waiting in tests

    def test_a_perfect_reply_passes_and_carries_the_usage(self):
        post = EffortPost("perfect", reasoning=1000)
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIs(result["pass"], True)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["prompt_tokens"], 120)
        self.assertEqual(result["completion_tokens"], 37)
        self.assertEqual(result["reasoning_tokens"], 1000)
        self.assertIsInstance(result["latency_s"], float)
        self.assertGreaterEqual(result["latency_s"], 0.0)

    def test_rung_none_sends_no_reasoning_effort_field(self):
        post = EffortPost("perfect")
        self.mod.run_effort_trial("free/m", "none", post, 48000)
        self.assertNotIn("reasoning_effort", post.calls[0])

    def test_a_wrong_answer_fails_the_trial(self):
        post = EffortPost("wrong")
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIs(result["pass"], False)
        self.assertIn("answer 3", result["note"])

    def test_prose_junk_fails_the_trial(self):
        post = EffortPost("junk")
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIs(result["pass"], False)
        self.assertIn("got 1", result["note"])

    def test_a_missing_reasoning_tokens_field_is_reported_as_none(self):
        post = EffortPost("perfect", reasoning=None)
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIs(result["reasoning_tokens"], None)

    def test_a_429_trial_measures_nothing(self):
        post = StatusPost(429)
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIsNone(result["pass"])
        self.assertEqual(len(post.calls), 1 + len(self.mod.RETRY_DELAYS_S))

    def test_a_429_then_200_retries_and_measures(self):
        pending = [(429, None, "rate limited")]
        post = EffortPost("perfect")
        real = post.__call__

        def flaky(body):
            if pending:
                return pending.pop(0)
            return real(body)

        sleeps = []
        with mock.patch.object(self.mod, "_sleep", side_effect=sleeps.append):
            result = self.mod.run_effort_trial("free/m", "low", flaky, 48000)
        self.assertIs(result["pass"], True)
        self.assertEqual(result["status"], 200)
        self.assertEqual(sleeps, [self.mod.RETRY_DELAYS_S[0]])

    def test_a_401_trial_measures_nothing_without_retrying(self):
        post = StatusPost(401)
        result = self.mod.run_effort_trial("free/m", "low", post, 48000)
        self.assertIsNone(result["pass"])
        self.assertEqual(len(post.calls), 1)

    def test_any_non_200_trial_measures_nothing(self):
        for status in (400, 404, 413, 422):
            post = StatusPost(status, "HTTP %d" % status)
            result = self.mod.run_effort_trial("free/m", "low", post, 48000)
            self.assertIsNone(result["pass"], status)


class AggregateRungTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _trial(self, passed, reasoning=None, completion=37, latency=1.5):
        return {"pass": passed, "status": 200, "prompt_tokens": 120,
                "completion_tokens": completion, "reasoning_tokens": reasoning,
                "latency_s": latency, "note": "n"}

    def test_only_measured_trials_count_towards_n_and_pass_rate(self):
        record = self.mod.aggregate_rung(
            [self._trial(True), self._trial(None), self._trial(False),
             self._trial(True)])
        self.assertEqual(record["n"], 3)
        self.assertEqual(record["passes"], 2)
        self.assertEqual(record["pass_rate"], 0.6667)

    def test_a_perfect_rung_records_a_pass_rate_of_one(self):
        record = self.mod.aggregate_rung([self._trial(True)] * 5)
        self.assertEqual(record["n"], 5)
        self.assertEqual(record["passes"], 5)
        self.assertEqual(record["pass_rate"], 1.0)

    def test_the_reasoning_mean_averages_only_the_trials_that_reported_one(self):
        record = self.mod.aggregate_rung(
            [self._trial(True, reasoning=100), self._trial(True, reasoning=None),
             self._trial(True, reasoning=300)])
        self.assertEqual(record["reasoning_tokens"], 200.0)

    def test_the_reasoning_mean_is_null_when_no_trial_reported_one(self):
        record = self.mod.aggregate_rung(
            [self._trial(True, reasoning=None), self._trial(True, reasoning=None)])
        self.assertIsNone(record["reasoning_tokens"])

    def test_completion_and_latency_means(self):
        record = self.mod.aggregate_rung(
            [self._trial(True, completion=37, latency=1.0),
             self._trial(True, completion=37, latency=2.0),
             self._trial(True, completion=43, latency=3.0)])
        self.assertEqual(record["completion_tokens"], 39.0)
        self.assertEqual(record["latency_s"], 2.0)

    def test_an_all_no_verdict_rung_measures_nothing(self):
        record = self.mod.aggregate_rung([self._trial(None), self._trial(None)])
        self.assertEqual(record["n"], 0)
        self.assertEqual(record["passes"], 0)
        self.assertIsNone(record["pass_rate"])
        self.assertIsNone(record["reasoning_tokens"])
        self.assertIsNone(record["completion_tokens"])
        self.assertIsNone(record["latency_s"])


class RecordEffortTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_writes_source_at_and_the_measured_rungs(self):
        overlay = {"models": {"m": {}}}
        rung = {"n": 5, "passes": 4, "pass_rate": 0.8,
                "reasoning_tokens": 123.5, "completion_tokens": 37.0,
                "latency_s": 2.0}
        self.mod.record_effort(overlay, "m", {"low": rung}, "2026-09-27T00:00:00Z")
        entry = overlay["models"]["m"]["effort"]
        self.assertEqual(entry["source"], "probe")
        self.assertEqual(entry["at"], "2026-09-27T00:00:00Z")
        self.assertEqual(entry["rungs"], {"low": rung})

    def test_a_rung_absent_from_the_merge_keeps_its_previous_value(self):
        old_high = {"n": 5, "passes": 4, "pass_rate": 0.8,
                    "reasoning_tokens": 111.0, "completion_tokens": 222.0,
                    "latency_s": 3.0}
        overlay = {"models": {"m": {
            "effort": {"source": "probe", "at": "2026-09-26T00:00:00Z",
                       "rungs": {"high": old_high}}}}}
        new_low = {"n": 3, "passes": 3, "pass_rate": 1.0,
                   "reasoning_tokens": 50.0, "completion_tokens": 37.0,
                   "latency_s": 1.0}
        self.mod.record_effort(overlay, "m", {"low": new_low},
                               "2026-09-27T00:00:00Z")
        rungs = overlay["models"]["m"]["effort"]["rungs"]
        self.assertEqual(rungs["high"], old_high)
        self.assertEqual(rungs["low"], new_low)

    def test_every_other_overlay_key_is_kept(self):
        overlay = {"legs": {"other/leg": {"tool_calls": {"value": "proven"}}},
                   "models": {"other": {"context_usable": {"tokens": 5000}},
                              "m": {"context_usable":
                                    {"tokens": 12345, "source": "probe"}}}}
        self.mod.record_effort(overlay, "m", {}, "2026-09-27T00:00:00Z")
        self.assertEqual(overlay["legs"]["other/leg"],
                         {"tool_calls": {"value": "proven"}})
        self.assertEqual(overlay["models"]["other"],
                         {"context_usable": {"tokens": 5000}})
        self.assertEqual(overlay["models"]["m"]["context_usable"],
                         {"tokens": 12345, "source": "probe"})


class CliDryRunTests(unittest.TestCase):
    """--dry-run against the real, committed registry: no key, no network."""

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(PROBE), "--dry-run"] + list(extra),
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            env={k: v for k, v in os.environ.items() if k != "AUTOOS_OMNIROUTE_KEY"})

    def test_dry_run_lists_legs_rungs_and_the_budget_and_exits_0(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            first = line.split("\t")[0]
            self.assertTrue("/" in first or first == "plan", line)
        self.assertTrue(any("rungs=" in l for l in lines),
                        "no probeable free reasoning leg in the plan")
        self.assertTrue(any("trials=5" in l for l in lines))
        self.assertTrue(any("completion_tokens_est=" in l for l in lines))

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

    def test_trials_below_five_without_allow_fewer_exits_2(self):
        proc = self._run("--trials", "3")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("allow-fewer", proc.stderr)

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
            "models": {"reasoner": {"id": "reasoner", "reasoning": True,
                                    "effort_ladder": ["none", "low"],
                                    "output_max": 100000},
                       "plain": {"id": "plain", "reasoning": False,
                                 "effort_ladder": []},
                       "noladder": {"id": "noladder", "reasoning": True,
                                    "effort_ladder": []}},
            "routes": {"r1": {"id": "r1", "legs": ["free/reasoner",
                                                   "paid/reasoner",
                                                   "free/plain",
                                                   "free/noladder"]}},
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

    def _rewrite_registry(self):
        with io.open(self.registry_path, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)

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
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--leg": "paid/reasoner"}))
        self.assertEqual(rc, 4)
        self.assertEqual(post.calls, [])

    def test_dry_run_makes_no_request(self):
        with mock.patch.object(self.mod, "_load_agent_module") as load_agent, \
             mock.patch.object(self.mod, "make_post") as mp:
            rc = self.mod.main(self._argv(**{"--dry-run": None}))
        self.assertEqual(rc, 0)
        load_agent.assert_not_called()
        mp.assert_not_called()

    def test_trials_below_five_is_refused_before_any_request(self):
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3"}))
        self.assertEqual(rc, 2)
        self.assertEqual(post.calls, [])

    def test_trials_below_five_runs_with_allow_fewer(self):
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        self.assertEqual(len(post.calls), 6)  # 3 trials x the 2-rung ladder

    def test_a_full_run_writes_every_measured_rung_to_the_overlay(self):
        post = EffortPost("perfect", reasoning=1000)
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 0)
        self.assertEqual(len(post.calls), 10)  # 5 trials x 2 rungs
        entry = self.mod.load_overlay(self.overlay_path)["models"]["reasoner"]["effort"]
        self.assertEqual(entry["source"], "probe")
        self.assertIn("at", entry)
        for rung in ("none", "low"):
            record = entry["rungs"][rung]
            self.assertEqual(record["n"], 5)
            self.assertEqual(record["passes"], 5)
            self.assertEqual(record["pass_rate"], 1.0)
            self.assertEqual(record["reasoning_tokens"], 1000.0)
            self.assertEqual(record["completion_tokens"], 37.0)
            self.assertGreaterEqual(record["latency_s"], 0.0)
        stdout = out.getvalue()
        self.assertIn("free/reasoner\trequest\tnone\t120\t37\t1000", stdout)
        self.assertIn("free/reasoner\trequest\tlow\t120\t37\t1000", stdout)
        self.assertIn("free/reasoner\trung\tnone\tn=5 passes=5 pass_rate=1.0",
                      stdout)
        self.assertIn("free/reasoner\teffort\t2/2 rungs measured", stdout)
        self.assertIn("total\t1200\t370\t10000", stdout)

    def test_rung_none_omits_the_field_and_only_ladder_rungs_are_sent(self):
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        # Rung "none" omits the field: it must be absent, never sent as "none".
        self.assertNotIn("none", [b.get("reasoning_effort") for b in post.calls])
        efforts = [body.get("reasoning_effort", "<omitted>") for body in post.calls]
        self.assertEqual(sorted(set(efforts)), ["<omitted>", "low"])
        self.assertEqual(efforts.count("<omitted>"), 3)
        self.assertEqual(efforts.count("low"), 3)

    def test_max_tokens_follows_the_spec_and_the_output_max_cap(self):
        self.registry["models"]["reasoner"]["effort_ladder"] = ["low", "high"]
        self._rewrite_registry()
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        seen = {body.get("reasoning_effort"): body["max_tokens"]
                for body in post.calls}
        self.assertEqual(seen, {"low": 48000, "high": 64000})

    def test_an_output_max_below_the_bar_caps_every_rung(self):
        self.registry["models"]["reasoner"]["effort_ladder"] = ["low", "high"]
        self.registry["models"]["reasoner"]["output_max"] = 32768
        self._rewrite_registry()
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        self.assertTrue(all(body["max_tokens"] == 32768 for body in post.calls))

    def test_the_overlay_merge_keeps_context_usable_and_other_keys(self):
        self.mod.save_overlay(self.overlay_path, {
            "legs": {"other/leg": {"tool_calls": {"value": "proven"}}},
            "models": {"reasoner": {"context_usable":
                                     {"tokens": 12345, "source": "probe"}},
                       "other": {"context_usable": {"tokens": 5000}}}})
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        overlay = self.mod.load_overlay(self.overlay_path)
        self.assertEqual(overlay["legs"]["other/leg"],
                         {"tool_calls": {"value": "proven"}})
        self.assertEqual(overlay["models"]["other"],
                         {"context_usable": {"tokens": 5000}})
        self.assertEqual(overlay["models"]["reasoner"]["context_usable"],
                         {"tokens": 12345, "source": "probe"})
        self.assertIn("effort", overlay["models"]["reasoner"])

    def test_a_no_verdict_rung_keeps_its_previous_overlay_value(self):
        self.registry["models"]["reasoner"]["effort_ladder"] = ["low", "high"]
        self._rewrite_registry()
        old_high = {"n": 5, "passes": 4, "pass_rate": 0.8,
                    "reasoning_tokens": 111.0, "completion_tokens": 222.0,
                    "latency_s": 3.0}
        self.mod.save_overlay(self.overlay_path, {
            "models": {"reasoner": {
                "effort": {"source": "probe", "at": "2026-09-26T00:00:00Z",
                           "rungs": {"high": old_high}}}}})
        post = RungPost(fail_rung="high", status=429)
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        rungs = self.mod.load_overlay(self.overlay_path)["models"]["reasoner"]["effort"]["rungs"]
        self.assertEqual(rungs["high"], old_high)
        self.assertEqual(rungs["low"]["n"], 3)
        self.assertIn("free/reasoner\tno-verdict\thigh", out.getvalue())
        self.assertIn("free/reasoner\teffort\t1/2 rungs measured", out.getvalue())

    def test_the_key_is_never_written_to_the_overlay_or_stdout(self):
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent(key="sk-SECRET-not-printed")), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.mod.main(self._argv(**{"--trials": "3",
                                        "--allow-fewer": None}))
        self.assertNotIn("sk-SECRET-not-printed", out.getvalue())
        with io.open(self.overlay_path, encoding="utf-8") as fh:
            overlay_text = fh.read()
        self.assertNotIn("sk-SECRET-not-printed", overlay_text)

    def test_skipped_legs_are_announced_and_never_probed(self):
        post = EffortPost("perfect")
        with mock.patch.object(self.mod, "_load_agent_module",
                               return_value=self._agent()), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv(**{"--trials": "3",
                                              "--allow-fewer": None}))
        self.assertEqual(rc, 0)
        stdout = out.getvalue()
        self.assertIn("paid/reasoner\tskip\ttier: paid", stdout)
        self.assertIn("free/plain\tskip\tnot a reasoning model", stdout)
        self.assertIn("free/noladder\tskip\tno effort_ladder", stdout)
        # free/reasoner is the only probeable leg: 3 trials x the 2-rung
        # ladder. The other three legs contributed zero requests.
        self.assertEqual(len(post.calls), 6)


class ProbeCommonTests(unittest.TestCase):
    """tools/probe_common.py directly: the shared plumbing. The recall suite
    keeps exercising these functions through tools/probe-recall.py; these
    cases pin them on the shared module itself."""

    def setUp(self):
        self.common = _load_common()
        self.registry = {
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

    def test_legs_to_probe_lists_every_distinct_leg_in_registry_order(self):
        legs = [leg for leg, _ in self.common.legs_to_probe(self.registry)]
        self.assertEqual(legs, ["free/big", "paid/small", "free/zen", "sub/big",
                                "down/big", "down/small"])

    def test_legs_to_probe_carries_the_skip_reasons(self):
        reasons = dict(self.common.legs_to_probe(self.registry))
        self.assertEqual(reasons["paid/small"], "tier: paid")
        self.assertEqual(reasons["sub/big"], "tier: subscription")
        self.assertEqual(reasons["down/small"], "unavailable_legs: down/small")
        self.assertIn("available false", reasons["down/big"])
        self.assertEqual(reasons["free/zen"],
                         "client_bound: probe through opencode")
        self.assertIsNone(reasons["free/big"])

    def test_legs_to_probe_narrows_by_legs_and_by_routes(self):
        legs = [leg for leg, _ in self.common.legs_to_probe(
            self.registry, only_legs=("free/big",))]
        self.assertEqual(legs, ["free/big"])
        legs = [leg for leg, _ in self.common.legs_to_probe(
            self.registry, only_routes=("r2",))]
        self.assertEqual(legs, ["free/big", "down/small"])

    def test_refused_leg_names_a_named_non_free_leg(self):
        self.assertEqual(self.common.refused_leg(self.registry, ["paid/small"]),
                         ("paid/small", "paid"))
        self.assertEqual(self.common.refused_leg(self.registry, ["sub/big"]),
                         ("sub/big", "subscription"))
        self.assertIsNone(self.common.refused_leg(self.registry, ["free/big"]))
        # Unresolvable: tier unknown, so a skip reason, never a refusal.
        self.assertIsNone(self.common.refused_leg(self.registry, ["nosuch/model"]))
        self.assertIsNone(self.common.refused_leg(self.registry, []))

    def test_is_no_verdict_status(self):
        for status in (401, 402, 403, 429, "ERR", "timeout", 500, 502, 503,
                       400, 404, 413, 422, None):
            self.assertTrue(self.common.is_no_verdict_status(status), status)
        self.assertFalse(self.common.is_no_verdict_status(200))

    def test_make_post_never_returns_a_provider_error_body(self):
        # Provider error bodies carry org/project ids; they reach stdout and
        # the overlay detail, so only the status may come back.
        import urllib.error
        body = io.BytesIO(b'{"error":{"message":"Rate limit reached for org-SECRET123"}}')
        err = urllib.error.HTTPError("http://x/v1/chat/completions", 413,
                                     "Payload Too Large", {}, body)
        post = self.common.make_post("http://x/v1/chat/completions", "k")
        with mock.patch.object(self.common.urllib.request, "urlopen",
                               side_effect=err):
            status, parsed, error = post({"model": "m"})
        self.assertEqual(status, 413)
        self.assertIsNone(parsed)
        self.assertNotIn("SECRET", error)
        self.assertNotIn("org-", error)
        self.assertIn("413", error)
        with mock.patch.object(self.common.urllib.request, "urlopen",
                               side_effect=OSError("connect to 10.0.0.9 refused")):
            status, _, error = post({"model": "m"})
        self.assertEqual(status, "ERR")
        self.assertNotIn("10.0.0.9", error)

    def test_post_with_retry_passes_a_non_retryable_status_through(self):
        post = StatusPost(200)
        sleeps = []
        status, parsed, error = self.common.post_with_retry(
            post, {"model": "free/m"}, sleeps.append)
        self.assertEqual((status, parsed, error), (200, None, "probe unavailable"))
        self.assertEqual(len(post.calls), 1)
        self.assertEqual(sleeps, [])

    def test_post_with_retry_exhausts_the_429_backoff_and_returns_it(self):
        post = StatusPost(429)
        sleeps = []
        status, _parsed, _error = self.common.post_with_retry(
            post, {}, sleeps.append)
        self.assertEqual(status, 429)
        self.assertEqual(sleeps, list(self.common.RETRY_DELAYS_S))
        self.assertEqual(len(post.calls), 1 + len(self.common.RETRY_DELAYS_S))

    def test_post_with_retry_retries_a_429_then_returns_the_success(self):
        good = (200, {"ok": True}, None)
        pending = [(429, None, "rate limited")]
        calls = []

        def post(body):
            calls.append(body)
            if pending:
                return pending.pop(0)
            return good

        sleeps = []
        status, parsed, error = self.common.post_with_retry(
            post, {"m": 1}, sleeps.append)
        self.assertEqual((status, parsed, error), good)
        self.assertEqual(sleeps, [self.common.RETRY_DELAYS_S[0]])
        self.assertEqual(len(calls), 2)

    def test_post_with_retry_does_not_retry_a_401(self):
        post = StatusPost(401)
        sleeps = []
        status, _parsed, _error = self.common.post_with_retry(
            post, {}, sleeps.append)
        self.assertEqual(status, 401)
        self.assertEqual(len(post.calls), 1)
        self.assertEqual(sleeps, [])

    def test_post_with_retry_retries_a_503_too(self):
        post = StatusPost(503)
        sleeps = []
        self.common.post_with_retry(post, {}, sleeps.append)
        self.assertEqual(len(post.calls), 1 + len(self.common.RETRY_DELAYS_S))

    def test_load_overlay_missing_file_is_empty_dict(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertEqual(self.common.load_overlay(
                os.path.join(tmpdir, "measured.json")), {})
        finally:
            os.rmdir(tmpdir)

    def test_save_then_load_round_trips_and_leaves_no_temp_file(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "measured.json")
        try:
            overlay = {"models": {"m": {"context_usable": {"tokens": 8000}}}}
            self.common.save_overlay(path, overlay)
            self.assertEqual(self.common.load_overlay(path), overlay)
            # The lock file beside it is deliberate (OVERLAYHOME: locked read-modify-write).
            self.assertEqual(sorted(os.listdir(tmpdir)), ["measured.json", "measured.json.lock"])
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    def test_print_request_and_print_total_shapes(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.common.print_request("free/m", 4000, 100, 37)
            self.common.print_request("free/m", "low", 100, None, 999)
            self.common.print_request("free/m", 4000, None, None)
            self.common.print_total(8, 4)
            self.common.print_total(8, 4, 2)
        self.assertEqual(out.getvalue().splitlines(), [
            "free/m\trequest\t4000\t100\t37",
            "free/m\trequest\tlow\t100\t-\t999",
            "free/m\trequest\t4000\t-\t-",
            "total\t8\t4",
            "total\t8\t4\t2",
        ])


if __name__ == "__main__":
    unittest.main()
