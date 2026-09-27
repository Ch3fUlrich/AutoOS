#!/usr/bin/env python3
"""Unit tests for tools/probe-toolcalls.py (routing v2 spec sections 3.1, 5.3, 10).

Every `post()` in these tests is a fake: no test in this file makes a network
call, reads the OmniRoute key, or runs the live probe. Only the CLI's
``--dry-run`` path and its argument-error paths are exercised through
subprocess (both make no request); the full non-dry-run flow is exercised by
calling `main()` directly with `_load_agent_module`, `gateway_up` and
`make_post` monkeypatched to fakes — except `MakePostScrubTests` and the
error-body case in `CliMainTests`, which call the real `make_post` with
`urllib.request.urlopen` patched to raise.

Run from the repo root:

    python3 tests/test_probe_toolcalls.py [ClassName]
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
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROBE = TOOLS / "probe-toolcalls.py"

# What a gateway error body says that must never be printed or stored: an org
# id and an internal host.
ORG = "org-SECRET123"
HOST = "10.0.0.9"
GATEWAY_URL = "http://gateway.invalid/v1/chat/completions"


def _load_module():
    """Load tools/probe-toolcalls.py via an absolute path (a hyphen is not importable)."""
    spec = importlib.util.spec_from_file_location("probe_toolcalls", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_error(code, body):
    """The HTTPError urlopen raises for a failed call whose body is `body`."""
    return urllib.error.HTTPError(GATEWAY_URL, code, "Bad Request", {},
                                  io.BytesIO(body.encode("utf-8")))


def ok_response(city="Paris", call_id="call_1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": "get_weather",
                                     "arguments": json.dumps({"city": city})}}]}}]}


def text_only_response(text="It is sunny."):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def round_trip_response(content="It is 18 degrees C in Paris."):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class FakePost:
    """A queue of canned (status, parsed, error) replies, recording every body sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        if not self.replies:
            raise AssertionError("FakePost exhausted: unexpected extra call")
        return self.replies.pop(0)


class LegsToProbeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.registry = {
            "providers": {
                "clean": {"id": "clean", "tier": "free", "available": True},
                "flaky": {"id": "flaky", "tier": "free", "available": False},
            },
            "models": {
                "big": {"id": "big"},
                "small": {"id": "small"},
                "zen": {"id": "zen", "client_bound": "opencode"},
            },
            "routes": {
                "r1": {"id": "r1", "legs": ["clean/big", "clean/small"],
                      "unavailable_legs": {"clean/small": {"available": False}}},
                "r2": {"id": "r2", "legs": ["clean/big", "flaky/small"]},
                "r3": {"id": "r3", "legs": ["clean/zen"]},
            },
        }

    def test_every_distinct_leg_in_registry_order(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(self.registry)]
        self.assertEqual(legs, ["clean/big", "clean/small", "flaky/small", "clean/zen"])

    def test_leg_in_unavailable_legs_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["clean/small"], "unavailable_legs: clean/small")

    def test_provider_available_false_is_skipped_with_a_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIn("available false", reasons["flaky/small"])

    def test_provider_with_a_future_until_is_skipped(self):
        # UNTILfix: _skip_reason uses registry.unavailable_now, so a provider
        # in a known multi-day until window is not probed at all.
        self.registry["providers"]["clean"]["unavailable_until"] = (
            "2999-01-01T00:00:00Z")
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIn("clean", reasons["clean/big"])

    def test_provider_whose_until_passed_is_probed_again(self):
        # A passed until self-heals: probe it again even with available: false.
        self.registry["providers"]["flaky"]["unavailable_until"] = (
            "2000-01-01T00:00:00Z")
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIsNone(reasons["flaky/small"])

    def test_client_bound_leg_is_skipped_naming_the_client(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["clean/zen"], "client_bound: probe through opencode")

    def test_a_probeable_leg_has_no_skip_reason(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertIsNone(reasons["clean/big"])

    def test_only_legs_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(
            self.registry, only_legs=("clean/big",))]
        self.assertEqual(legs, ["clean/big"])

    def test_only_routes_narrows_the_list(self):
        legs = [leg for leg, _ in self.mod.legs_to_probe(
            self.registry, only_routes=("r3",))]
        self.assertEqual(legs, ["clean/zen"])


class DenyRuleTests(unittest.TestCase):
    """probe_common's shared skip logic also skips a policy-denied free leg."""

    def setUp(self):
        self.mod = _load_module()
        self.registry = {
            "providers": {
                "free": {"id": "free", "tier": "free", "available": True},
                "groq": {"id": "groq", "tier": "free", "available": True},
            },
            "models": {"big": {"id": "big"}},
            "routes": {"r1": {"id": "r1", "legs": ["free/big", "groq/big"]}},
            "policy": {"leg_rules": [
                {"id": "deny-groq", "match": "groq/*", "allow": False, "reason": "x"},
            ]},
        }

    def test_a_denied_free_leg_is_skipped_with_the_rule_id(self):
        reasons = dict(self.mod.legs_to_probe(self.registry))
        self.assertEqual(reasons["groq/big"], "policy: denied by deny-groq")


class SingleCallCheckTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_pass_a_valid_paris_call(self):
        ok, call, note = self.mod._check_single(ok_response())
        self.assertTrue(ok, note)
        self.assertEqual(call["function"]["name"], "get_weather")

    def test_pass_is_case_insensitive_on_city(self):
        ok, _, _ = self.mod._check_single(ok_response(city="PARIS, FR"))
        self.assertTrue(ok)

    def test_fail_no_tool_call_at_all(self):
        ok, call, note = self.mod._check_single(text_only_response())
        self.assertFalse(ok)
        self.assertIsNone(call)

    def test_fail_wrong_city(self):
        ok, _, note = self.mod._check_single(ok_response(city="London"))
        self.assertFalse(ok)
        self.assertIn("London", note)

    def test_fail_two_tool_calls(self):
        resp = ok_response()
        resp["choices"][0]["message"]["tool_calls"].append(
            resp["choices"][0]["message"]["tool_calls"][0])
        ok, _, note = self.mod._check_single(resp)
        self.assertFalse(ok)
        self.assertIn("got 2", note)

    def test_fail_arguments_not_json(self):
        resp = ok_response()
        resp["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "city=Paris"
        ok, _, note = self.mod._check_single(resp)
        self.assertFalse(ok)

    def test_fail_wrong_tool_name(self):
        resp = ok_response()
        resp["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "get_time"
        ok, _, note = self.mod._check_single(resp)
        self.assertFalse(ok)
        self.assertIn("get_time", note)


class RoundTripCheckTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_pass_mentions_18_and_no_further_tool_call(self):
        ok, note = self.mod._check_round_trip(round_trip_response())
        self.assertTrue(ok, note)

    def test_fail_missing_18(self):
        ok, note = self.mod._check_round_trip(round_trip_response("It is quite warm."))
        self.assertFalse(ok)

    def test_fail_another_tool_call(self):
        resp = ok_response()
        ok, note = self.mod._check_round_trip(resp)
        self.assertFalse(ok)
        self.assertIn("another tool_call", note)


class RunTrialTests(unittest.TestCase):
    """run_trial: single call, retry, and the round trip."""

    def setUp(self):
        self.mod = _load_module()
        self.mod._sleep = lambda secs: None  # no real waiting in tests

    def test_full_pass(self):
        post = FakePost([(200, ok_response(), None), (200, round_trip_response(), None)])
        trial = self.mod.run_trial("p/m", post)
        self.assertEqual(trial["single"], "pass")
        self.assertEqual(trial["round"], "pass")
        self.assertEqual(trial["status"], 200)

    def test_text_only_response_fails_single_and_skips_round(self):
        post = FakePost([(200, text_only_response(), None)])
        trial = self.mod.run_trial("p/m", post)
        self.assertEqual(trial["single"], "fail")
        self.assertEqual(trial["round"], "skipped")
        self.assertEqual(len(post.calls), 1)  # round trip never sent

    def test_400_tools_not_supported_is_an_error(self):
        # post() gives back the fixed token, never the body (MakePostScrubTests);
        # run_trial passes it through as the trial's note.
        post = FakePost([(400, None, self.mod.TOOLS_UNSUPPORTED)])
        trial = self.mod.run_trial("p/m", post)
        self.assertEqual(trial["single"], "error")
        self.assertEqual(trial["status"], 400)
        self.assertEqual(trial["note"], self.mod.TOOLS_UNSUPPORTED)

    def test_429_then_200_retries_with_backoff_and_passes(self):
        post = FakePost([
            (429, None, "rate limited"),
            (429, None, "rate limited"),
            (200, ok_response(), None),
            (200, round_trip_response(), None),
        ])
        sleeps = []
        self.mod._sleep = sleeps.append
        trial = self.mod.run_trial("p/m", post)
        self.assertEqual(trial["single"], "pass")
        self.assertEqual(trial["round"], "pass")
        self.assertEqual(sleeps, list(self.mod.RETRY_DELAYS_S[:2]))

    def test_round_trip_transport_error_is_recorded(self):
        post = FakePost([(200, ok_response(), None), ("ERR", None, "connection reset")])
        trial = self.mod.run_trial("p/m", post)
        self.assertEqual(trial["single"], "pass")
        self.assertEqual(trial["round"], "error")
        self.assertIn("connection reset", trial["note"])


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _pass_trial(self):
        return {"single": "pass", "round": "pass", "status": 200, "note": "ok"}

    def _text_only_trial(self):
        return {"single": "fail", "round": "skipped", "status": 200, "note": "no tool_calls"}

    def _tools_400_trial(self):
        # The fixed token make_post returns for a 400 whose body mentioned
        # tool/function support — never the body itself.
        return {"single": "error", "round": "skipped", "status": 400,
                "note": self.mod.TOOLS_UNSUPPORTED}

    def _rate_limited_trial(self):
        return {"single": "error", "round": "skipped", "status": 429, "note": "rate limited"}

    def test_all_pass_is_proven(self):
        value, _ = self.mod.classify([self._pass_trial()] * 3)
        self.assertEqual(value, "proven")

    def test_every_trial_answered_but_no_valid_call_is_broken(self):
        value, _ = self.mod.classify([self._text_only_trial()] * 3)
        self.assertEqual(value, "broken")

    def test_400_mentioning_tool_support_is_broken(self):
        value, _ = self.mod.classify([self._tools_400_trial()])
        self.assertEqual(value, "broken")

    def test_400_mentioning_tool_support_is_broken_even_mixed_with_a_pass(self):
        value, _ = self.mod.classify([self._pass_trial(), self._tools_400_trial()])
        self.assertEqual(value, "broken")

    def test_a_400_without_the_tool_token_is_not_broken(self):
        # A 400 that said nothing about tools is an unknown, not a verdict
        # about tool calling: 400 is not a no-verdict status here.
        trial = {"single": "error", "round": "skipped", "status": 400,
                 "note": "HTTP 400"}
        value, _ = self.mod.classify([trial])
        self.assertEqual(value, "unproven")

    def test_a_raw_error_body_is_never_matched_as_tool_support(self):
        # classify() reads only the fixed token. Body text reaching it means
        # something classified outside make_post, and a body is not evidence.
        trial = {"single": "error", "round": "skipped", "status": 400,
                 "note": "this model does not support tool use: org-SECRET123"}
        value, _ = self.mod.classify([trial])
        self.assertNotEqual(value, "broken")

    def test_the_broken_detail_of_a_400_carries_no_body(self):
        _value, detail = self.mod.classify([self._tools_400_trial()])
        self.assertIn("400", detail)
        self.assertNotIn(ORG, detail)
        self.assertNotIn(HOST, detail)

    def test_mixed_pass_and_fail_is_unproven(self):
        value, _ = self.mod.classify([self._pass_trial(), self._text_only_trial()])
        self.assertEqual(value, "unproven")

    def test_only_transport_errors_is_no_verdict(self):
        value, detail = self.mod.classify([self._rate_limited_trial()] * 3)
        self.assertIsNone(value)
        self.assertIn("429", detail)

    def test_missing_credentials_is_no_verdict(self):
        # Live run 2026-09-26: antigravity/cc/cerebras answered 401 with a body
        # saying "No active credentials for provider" - a sign-in gap, not a
        # tool-calling fact. Only the status survives make_post now.
        for status in (401, 403):
            trial = {"single": "error", "round": "skipped", "status": status,
                     "note": "HTTP %d" % status}
            value, detail = self.mod.classify([trial] * 3)
            self.assertIsNone(value, status)
            self.assertIn(str(status), detail)

    def test_5xx_counts_as_no_verdict(self):
        trial = {"single": "error", "round": "skipped", "status": 503, "note": "unavailable"}
        value, _ = self.mod.classify([trial] * 2)
        self.assertIsNone(value)

    def test_empty_trials_is_no_verdict(self):
        value, _ = self.mod.classify([])
        self.assertIsNone(value)


class MakePostScrubTests(unittest.TestCase):
    """The real `make_post` with `urlopen` patched: what may come back as an error.

    A provider error body carries org/project ids and internal hosts, and the
    error text is printed and stored in the overlay, so the body is read only
    to pick a fixed token and then dropped. `urlopen` raises, so nothing here
    reaches the network.
    """

    def setUp(self):
        self.mod = _load_module()
        self.post = self.mod.make_post(GATEWAY_URL, "sk-fake-key")

    def _urlopen(self, exc):
        return mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=exc)

    def test_a_400_body_mentioning_tools_becomes_the_fixed_token(self):
        body = '{"error":{"message":"tools are not supported for org %s on %s"}}' % (ORG, HOST)
        with self._urlopen(http_error(400, body)):
            status, parsed, error = self.post({"model": "m"})
        self.assertEqual((status, parsed), (400, None))
        self.assertEqual(error, self.mod.TOOLS_UNSUPPORTED)
        self.assertNotIn(ORG, error)
        self.assertNotIn(HOST, error)

    def test_a_400_body_about_anything_else_is_only_the_status(self):
        body = '{"error":{"message":"rate limit reached for org %s on %s"}}' % (ORG, HOST)
        with self._urlopen(http_error(400, body)):
            status, _parsed, error = self.post({"model": "m"})
        self.assertEqual((status, error), (400, "HTTP 400"))

    def test_a_non_400_body_mentioning_tools_is_still_only_the_status(self):
        # Only a 400's body is read at all: a 401 credential gap is no
        # tool-calling fact however it is worded (live 2026-09-26).
        body = '{"error":{"message":"no credentials for the tools API at %s"}}' % HOST
        with self._urlopen(http_error(401, body)):
            status, _parsed, error = self.post({"model": "m"})
        self.assertEqual((status, error), (401, "HTTP 401"))

    def test_a_transport_error_names_only_the_exception_type(self):
        with self._urlopen(OSError("connect to %s port 8080 refused" % HOST)):
            status, parsed, error = self.post({"model": "m"})
        self.assertEqual((status, parsed, error),
                         ("ERR", None, "transport error: OSError"))

    def test_the_token_is_a_word_classify_actually_matches(self):
        # The token and classify() have to agree, and both are written here
        # independently of the module: an inline pattern in make_post that no
        # longer matched the token would have left every error a no-verdict.
        token = self.mod.TOOLS_UNSUPPORTED
        self.assertRegex(token, re.compile(r"(?i)\b(tool|function)s?\b"))
        self.assertEqual(
            self.mod.classify([{"single": "error", "round": "skipped",
                                "status": 400, "note": token}])[0], "broken")


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
        self.mod.save_overlay(self.path, {"legs": {"p/m": {"tool_calls": {"value": "proven"}}}})
        self.assertEqual(self.mod.load_overlay(self.path),
                         {"legs": {"p/m": {"tool_calls": {"value": "proven"}}}})

    def test_save_leaves_no_temp_file_behind(self):
        self.mod.save_overlay(self.path, {"legs": {}})
        self.assertEqual(os.listdir(self.tmpdir), ["measured.json"])

    def test_record_verdict_keeps_unrelated_keys(self):
        overlay = {"legs": {"other/leg": {"tool_calls": {"value": "proven"}},
                            "p/m": {"context_usable": {"tokens": 5000}}}}
        self.mod.record_verdict(overlay, "p/m", "proven", "all pass", [], 3, "2026-09-26T00:00:00Z")
        self.assertEqual(overlay["legs"]["other/leg"], {"tool_calls": {"value": "proven"}})
        self.assertEqual(overlay["legs"]["p/m"]["context_usable"], {"tokens": 5000})
        self.assertEqual(overlay["legs"]["p/m"]["tool_calls"]["value"], "proven")

    def test_record_verdict_never_writes_a_none_value(self):
        overlay = {}
        self.mod.record_verdict(overlay, "p/m", None, "only transport errors", [], 0,
                                "2026-09-26T00:00:00Z")
        self.assertNotIn("tool_calls", overlay["legs"]["p/m"])
        self.assertEqual(overlay["legs"]["p/m"]["tool_calls_last_error"]["detail"],
                         "only transport errors")

    def test_record_verdict_a_later_none_does_not_erase_a_prior_value(self):
        overlay = {"legs": {"p/m": {"tool_calls": {"value": "proven", "source": "probe"}}}}
        self.mod.record_verdict(overlay, "p/m", None, "rate limited", [], 0,
                                "2026-09-26T00:00:00Z")
        self.assertEqual(overlay["legs"]["p/m"]["tool_calls"]["value"], "proven")
        self.assertIn("tool_calls_last_error", overlay["legs"]["p/m"])


class CliDryRunTests(unittest.TestCase):
    """--dry-run against the real, committed registry: no key, no network."""

    def test_dry_run_lists_legs_and_exits_0(self):
        proc = subprocess.run(
            [sys.executable, str(PROBE), "--dry-run"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items() if k != "AUTOOS_OMNIROUTE_KEY"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            leg, _, _rest = line.partition("\t")
            self.assertIn("/", leg)

    def test_dry_run_with_a_bad_registry_path_exits_2(self):
        proc = subprocess.run(
            [sys.executable, str(PROBE), "--dry-run", "--registry", "/no/such/file.json"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)

    def test_bad_trials_value_exits_2(self):
        proc = subprocess.run(
            [sys.executable, str(PROBE), "--dry-run", "--trials", "0"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)

    def test_unknown_argument_exits_2(self):
        proc = subprocess.run(
            [sys.executable, str(PROBE), "--not-a-real-flag"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)


class CliMainTests(unittest.TestCase):
    """main(), fully faked: no real key, gateway or network involved."""

    def setUp(self):
        self.mod = _load_module()
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "registry.json")
        self.overlay_path = os.path.join(self.tmpdir, "measured.json")
        registry = {
            "providers": {"clean": {"id": "clean", "tier": "free", "available": True}},
            "models": {"m": {"id": "m"}},
            "routes": {"r1": {"id": "r1", "legs": ["clean/m"]}},
        }
        with io.open(self.registry_path, "w", encoding="utf-8") as fh:
            json.dump(registry, fh)

    def tearDown(self):
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _argv(self, **extra):
        argv = ["--registry", self.registry_path, "--overlay", self.overlay_path,
               "--gateway", "http://example.invalid/v1/chat/completions"]
        for k, v in extra.items():
            argv += [k, v]
        return argv

    def test_no_key_exits_3_without_touching_the_network(self):
        fake_agent = mock.Mock(client_key=mock.Mock(return_value=None), ROOT="/nowhere")
        with mock.patch.object(self.mod, "_load_agent_module", return_value=fake_agent), \
             mock.patch.object(self.mod, "gateway_up") as gw:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 3)
        gw.assert_not_called()

    def test_key_but_gateway_down_exits_3(self):
        fake_agent = mock.Mock(client_key=mock.Mock(return_value="sk-fake"), ROOT="/nowhere")
        with mock.patch.object(self.mod, "_load_agent_module", return_value=fake_agent), \
             mock.patch.object(self.mod, "gateway_up", return_value=False), \
             mock.patch.object(self.mod, "make_post") as mp:
            rc = self.mod.main(self._argv())
        self.assertEqual(rc, 3)
        mp.assert_not_called()

    def test_full_run_writes_the_overlay_and_exits_0(self):
        fake_agent = mock.Mock(client_key=mock.Mock(return_value="sk-fake"), ROOT="/nowhere")
        post = FakePost([(200, ok_response(), None), (200, round_trip_response(), None)] * 3)
        with mock.patch.object(self.mod, "_load_agent_module", return_value=fake_agent), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post):
            rc = self.mod.main(self._argv(**{"--trials": "3"}))
        self.assertEqual(rc, 0)
        overlay = self.mod.load_overlay(self.overlay_path)
        self.assertEqual(overlay["legs"]["clean/m"]["tool_calls"]["value"], "proven")
        self.assertEqual(overlay["legs"]["clean/m"]["tool_calls"]["passes"], 3)

    def test_key_is_never_written_to_the_overlay_or_stdout(self):
        fake_agent = mock.Mock(client_key=mock.Mock(return_value="sk-SECRET-not-printed"),
                               ROOT="/nowhere")
        post = FakePost([(200, ok_response(), None), (200, round_trip_response(), None)] * 3)
        with mock.patch.object(self.mod, "_load_agent_module", return_value=fake_agent), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod, "make_post", return_value=post), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.mod.main(self._argv())
        self.assertNotIn("sk-SECRET-not-printed", out.getvalue())
        with io.open(self.overlay_path, encoding="utf-8") as fh:
            overlay_text = fh.read()
        self.assertNotIn("sk-SECRET-not-printed", overlay_text)

    def test_a_provider_error_body_reaches_neither_stdout_nor_the_overlay(self):
        # The whole flow with the real make_post and urlopen failing: a 400
        # whose body names an org and an internal host. The verdict is still
        # "broken", and only the fixed token may be printed or stored.
        fake_agent = mock.Mock(client_key=mock.Mock(return_value="sk-fake"), ROOT="/nowhere")
        err = http_error(400, '{"error":{"message":"tools are not supported '
                              'for org %s on %s"}}' % (ORG, HOST))
        with mock.patch.object(self.mod, "_load_agent_module", return_value=fake_agent), \
             mock.patch.object(self.mod, "gateway_up", return_value=True), \
             mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=err), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.mod.main(self._argv(**{"--trials": "1"}))
        self.assertEqual(rc, 0)
        with io.open(self.overlay_path, encoding="utf-8") as fh:
            text = out.getvalue() + fh.read()
        self.assertIn("broken", text)
        for leak in (ORG, HOST, "tools are not supported"):
            self.assertNotIn(leak, text)


if __name__ == "__main__":
    unittest.main()
