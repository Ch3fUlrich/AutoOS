"""Tests for the agent spawner's daily budget gate.

Covers overflow, NaN and infinite gate values, argument parser restrictions,
exhaustive Google-paid model sources, non-dict gate files, and reason lines.
"""
import argparse
import contextlib
import importlib.util
import inspect
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = _load("autoos_agent", ROOT / "tools" / "autoos-agent.py")

GATE_ENV = "AUTOOS_DAILY_GATE_FILE"
VERTEX = "omniroute/vertex-gemini-3.8-flash"


def utc_day(delta_days=0):
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def make_args(**over):
    base = dict(task="probe task", run_id=None, model=None, client="opencode",
                free=False, free_model=None, tier=None, card=None, clean=False,
                dry_run=True, isolate=True, read_only=False, joinable=False,
                not_family=None, review_of=None, lean=False, allow_training=False,
                auto=True, no_fallthrough=False, max_depth=None, title=None,
                review_base=None,
                allow_mode_only=False)
    base.update(over)
    return argparse.Namespace(**base)


class GateFileBaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gate_path = os.path.join(self.tmp.name, "daily_gate.json")

    def write_gate(self, verdict="block", usd=26.50, budget=25.0, day=None, raw_content=None):
        if raw_content is not None:
            text = raw_content
        else:
            data = {"day": day if day is not None else utc_day(), "usd": usd,
                    "verdict": verdict, "budget": budget, "by_provider": {}}
            text = json.dumps(data)
        with open(self.gate_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.gate_path

    def assert_fail_open(self, env, label, reason=None, args=None):
        if args is None:
            args = make_args(model=VERTEX)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = agent.daily_gate_refusal(args, env=env)
        self.assertIsNone(res, label)
        if reason is None:
            self.assertIn("daily gate unavailable", err.getvalue(), label)
        else:
            expected = f"daily gate unavailable: {reason}"
            self.assertEqual(expected.strip(), err.getvalue().strip(), label)


class NumericOverflowAndSpecialValueTests(GateFileBaseCase):
    def test_overflow_and_special_values_fail_open(self):
        huge_int_str = "9" * 400
        cases = [
            ("usd 400-digit int", f'{{"day": "{utc_day()}", "verdict": "block", "usd": {huge_int_str}, "budget": 25.0}}'),
            ("budget 400-digit int", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": {huge_int_str}}}'),
            ("usd NaN literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": NaN, "budget": 25.0}}'),
            ("budget NaN literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": NaN}}'),
            ("usd Infinity literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": Infinity, "budget": 25.0}}'),
            ("budget Infinity literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": Infinity}}'),
            ("usd -Infinity literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": -Infinity, "budget": 25.0}}'),
            ("budget -Infinity literal", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": -Infinity}}'),
            ("usd NaN string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": "NaN", "budget": 25.0}}'),
            ("budget NaN string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": "NaN"}}'),
            ("usd Infinity string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": "Infinity", "budget": 25.0}}'),
            ("budget Infinity string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": "Infinity"}}'),
            ("usd 1e999", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 1e999, "budget": 25.0}}'),
            ("budget 1e999", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": 1e999}}'),
            ("usd abc string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": "abc", "budget": 25.0}}'),
            ("budget abc string", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": "abc"}}'),
            ("usd boolean true", f'{{"day": "{utc_day()}", "verdict": "block", "usd": true, "budget": 25.0}}'),
            ("budget boolean true", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": true}}'),
            ("usd non-empty list", f'{{"day": "{utc_day()}", "verdict": "block", "usd": [1, 2], "budget": 25.0}}'),
            ("budget non-empty list", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": [1, 2]}}'),
            ("usd non-empty dict", f'{{"day": "{utc_day()}", "verdict": "block", "usd": {{"a": 1}}, "budget": 25.0}}'),
            ("budget non-empty dict", f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": {{"a": 1}}}}'),
        ]
        for label, raw_content in cases:
            with self.subTest(label=label):
                path = self.write_gate(raw_content=raw_content)
                self.assert_fail_open({GATE_ENV: path}, label, reason="garbage value")


class FalsyGateValuesTests(GateFileBaseCase):
    def test_usd_falsy_empty_string(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": "", "budget": 25.0}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res)

    def test_usd_falsy_empty_list(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": [], "budget": 25.0}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res)

    def test_usd_falsy_empty_dict(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": {{}}, "budget": 25.0}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res)

    def test_usd_falsy_boolean_false(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": false, "budget": 25.0}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res)

    def test_usd_falsy_null(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": null, "budget": 25.0}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res)

    def test_budget_falsy_empty_string_with_real_usd(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": ""}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res)

    def test_budget_falsy_empty_list_with_real_usd(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": []}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res)

    def test_budget_falsy_empty_dict_with_real_usd(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": {{}}}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res)

    def test_budget_falsy_boolean_false_with_real_usd(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": false}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res)

    def test_budget_falsy_null_with_real_usd(self):
        path = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": null}}')
        res = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: path})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res)

    def test_boolean_usd_and_budget_message_paths(self):
        p_usd_true = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": true, "budget": 25.0}}')
        self.assert_fail_open({GATE_ENV: p_usd_true}, "usd true", reason="garbage value")

        p_budget_true = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 20.0, "budget": true}}')
        self.assert_fail_open({GATE_ENV: p_budget_true}, "budget true", reason="garbage value")

        p_usd_false = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": false, "budget": 25.0}}')
        res_usd = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: p_usd_false})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res_usd)

        p_budget_false = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": 26.50, "budget": false}}')
        res_budget = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: p_budget_false})
        self.assertEqual("daily budget blocked: day total $26.50 exceeds budget $25.00", res_budget)

        p_both_false = self.write_gate(raw_content=f'{{"day": "{utc_day()}", "verdict": "block", "usd": false, "budget": false}}')
        res_both = agent.daily_gate_refusal(make_args(model=VERTEX), env={GATE_ENV: p_both_false})
        self.assertEqual("daily budget blocked: day total $0.00 exceeds budget $25.00", res_both)


class ParserRestrictionsTests(unittest.TestCase):
    def test_run_parser_has_no_override_options(self):
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd", required=True)
        agent._parser_run(sub)
        run_parser = sub.choices["run"]

        opts = set()
        for action in run_parser._actions:
            opts.update(action.option_strings)

        forbidden = ["--budget", "--daily-budget", "--gate-budget", "--override", "--allow-paid"]
        for opt in forbidden:
            self.assertNotIn(opt, opts, f"Option {opt} must not exist on run parser")

    def test_override_flags_rejected_with_exit_code_2_and_gate_not_called(self):
        forbidden = ["--budget", "--daily-budget", "--gate-budget", "--override", "--allow-paid"]
        for opt in forbidden:
            with self.subTest(opt=opt):
                with mock.patch.object(agent, "daily_gate_refusal") as mock_gate:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as ctx:
                            agent.main(["run", opt, "10", "task"])
                        self.assertEqual(ctx.exception.code, 2)
                    mock_gate.assert_not_called()

    def test_daily_gate_refusal_signature_has_no_budget_parameter(self):
        sig = inspect.signature(agent.daily_gate_refusal)
        self.assertNotIn("budget", sig.parameters)


class ExhaustiveModelSourceTests(GateFileBaseCase):
    def test_google_paid_model_sources_refused_and_wholly_non_google_allowed(self):
        gate = self.write_gate(verdict="block", usd=30.0, budget=25.0)
        env = {GATE_ENV: gate}

        # (a) plan route legs
        plan_leg_google = {"model": "ovh/deepseek-r1", "route": {"legs": [VERTEX]}}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_leg_google, env=env))
        plan_leg_dict_google = {"model": "ovh/deepseek-r1", "route": {"legs": [{"model": VERTEX}]}}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_leg_dict_google, env=env))
        plan_legs_ovh = {"model": "ovh/deepseek-r1", "route": {"legs": ["ovh/deepseek-r1", "agy/gemini-3.8-flash"]}}
        self.assertIsNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_legs_ovh, env=env))

        # (b) plan route model
        plan_route_google = {"model": "ovh/deepseek-r1", "route": {"model": VERTEX, "legs": ["ovh/deepseek-r1"]}}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_route_google, env=env))
        plan_route_ovh = {"model": "ovh/deepseek-r1", "route": {"model": "ovh/deepseek-r1", "legs": ["ovh/deepseek-r1"]}}
        self.assertIsNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_route_ovh, env=env))

        # (c) leg provider field
        plan_prov_vertex = {"model": "ovh/deepseek-r1", "route": {"legs": [{"model": "ovh/deepseek-r1", "provider": "vertex"}]}}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_prov_vertex, env=env))
        plan_prov_google = {"model": "ovh/deepseek-r1", "route": {"legs": [{"model": "ovh/deepseek-r1", "provider": "google"}]}}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_prov_google, env=env))
        plan_prov_ovh = {"model": "ovh/deepseek-r1", "route": {"legs": [{"model": "ovh/deepseek-r1", "provider": "ovh"}]}}
        self.assertIsNone(agent.daily_gate_refusal(make_args(model="ovh/deepseek-r1"), plan_prov_ovh, env=env))

        # (d) free-model leg
        args_free_google = make_args(free=True, free_model=VERTEX)
        self.assertIsNotNone(agent.daily_gate_refusal(args_free_google, env=env))
        args_free_pin_google = make_args(free_model=VERTEX)
        self.assertIsNotNone(agent.daily_gate_refusal(args_free_pin_google, env=env))
        plan_free_model_google = {"free_model": VERTEX}
        self.assertIsNotNone(agent.daily_gate_refusal(make_args(), plan_free_model_google, env=env))

        args_free_ovh = make_args(free=True, free_model="ovh/deepseek-r1")
        self.assertIsNone(agent.daily_gate_refusal(args_free_ovh, env=env))
        args_free_pin_ovh = make_args(free_model="ovh/deepseek-r1")
        self.assertIsNone(agent.daily_gate_refusal(args_free_pin_ovh, env=env))
        plan_free_model_ovh = {"free_model": "ovh/deepseek-r1"}
        self.assertIsNone(agent.daily_gate_refusal(make_args(), plan_free_model_ovh, env=env))

    def test_antigravity_client_with_google_model_allowed(self):
        gate = self.write_gate(verdict="block", usd=30.0, budget=25.0)
        env = {GATE_ENV: gate}
        for client in ("agy", "antigravity"):
            with self.subTest(client=client):
                args = make_args(model="vertex/gemini-3.8-flash", client=client)
                self.assertIsNone(agent.daily_gate_refusal(args, env=env))


class NonDictGateFileAndReasonLineTests(GateFileBaseCase):
    def test_non_dict_json_files_fail_open_with_not_a_json_object(self):
        cases = [
            ("json list", json.dumps([1, 2, 3])),
            ("json string", json.dumps("hello world")),
            ("json number", json.dumps(12345)),
            ("json boolean", json.dumps(True)),
            ("json null", json.dumps(None)),
        ]
        for label, raw_content in cases:
            with self.subTest(label=label):
                path = self.write_gate(raw_content=raw_content)
                self.assert_fail_open({GATE_ENV: path}, label, reason="not a JSON object")

    def test_all_seven_unavailable_reason_lines_pinned(self):
        # 1: env var not set
        self.assert_fail_open({}, "env var not set", reason="env var not set and no default gate file")

        # 2: file stale (future mtime)
        path = self.write_gate()
        future = time.time() + 3600
        os.utime(path, (future, future))
        self.assert_fail_open({GATE_ENV: path}, "future mtime", reason="file stale (future mtime)")

        # 3: file stale (age)
        path = self.write_gate()
        old = time.time() - 7205
        os.utime(path, (old, old))
        self.assert_fail_open({GATE_ENV: path}, "old mtime", reason="file stale (age)")

        # 4: file unreadable
        missing = os.path.join(self.tmp.name, "missing.json")
        self.assert_fail_open({GATE_ENV: missing}, "unreadable", reason="file unreadable")

        # 5: not a JSON object
        corrupt = self.write_gate(raw_content="corrupt json not parseable")
        self.assert_fail_open({GATE_ENV: corrupt}, "corrupt json", reason="not a JSON object")

        # 6: file stale (not today's UTC day)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        stale_day = self.write_gate(day=yesterday)
        self.assert_fail_open({GATE_ENV: stale_day}, "stale day", reason="file stale (not today's UTC day)")

        # 7: garbage value
        garbage = self.write_gate(usd="not-a-number")
        self.assert_fail_open({GATE_ENV: garbage}, "garbage value", reason="garbage value")


if __name__ == "__main__":
    unittest.main()
