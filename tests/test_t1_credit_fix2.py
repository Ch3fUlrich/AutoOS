"""T1-CREDIT-FIX-2 (red first): narrow guards, total fallback, usage exit 3,
paid fail-closed, missing-guard wording. Real entries only, no gateway."""
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage

from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "autoos_agent", _ROOT / "tools" / "autoos-agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

NOW = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.timezone.utc)


def _reg_credit(cap=200.0):
    return {
        "providers": {
            "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                         "model_prefix": "ovh", "credit_usd": cap,
                         "monthly_cap_usd": cap,
                         "monthly_warn_fraction": 0.8,
                         "trains_on_prompts": False},
        },
        "models": {
            "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                           "context_usable": {"tokens": 100000,
                                             "source": "default"},
                           "price_in": 1e-06, "price_out": 1e-06},
        },
        "routes": {"r-trial": {"id": "r-trial",
                               "legs": ["ovhcloud/ovh-priced"]}},
        "policy": {"leg_rules": []},
    }


def _reg_paid(cap=25.0):
    return {
        "providers": {
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "monthly_cap_usd": cap,
                         "monthly_warn_fraction": 0.8,
                         "trains_on_prompts": False},
        },
        "models": {
            "ds-model": {"id": "ds-model", "tool_calls": "proven",
                         "context_usable": {"tokens": 100000,
                                           "source": "default"},
                         "price_in": 1e-06, "price_out": 1e-06},
        },
        "routes": {"r-paid": {"id": "r-paid", "legs": ["deepseek/ds-model"]}},
        "policy": {"leg_rules": []},
    }


def _env_key(keydir):
    return {"AUTOOS_AI_STACK_CONFIG": keydir,
            "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1"}


def _legs_for(reg, route_id, guards, warns=None):
    if warns is None:
        warns = []
    kept, skipped, _notes = r.usable_legs(
        reg["routes"][route_id], {"kind": "implement", "privacy": "public"},
        {"need_tokens": 10},
        {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
        reg, {}, credit_guards=guards, credit_warns=warns)
    return kept, skipped, warns


class C1UnexpectedBugIsAGuardError(unittest.TestCase):
    """plan_credit_guards catches only expected failures; an unexpected bug
    type is a distinct kept-visible 'guard error', never silent 'unknown'."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        self.keydir = tempfile.mkdtemp(prefix="t1c2-")
        with open(os.path.join(self.keydir, "manage.key"), "w") as fh:
            fh.write("x")

    def test_typeerror_from_the_read_is_a_guard_error_not_unknown(self):
        def explode(registry, rows, since=None, failure=None):
            raise TypeError("unsupported operand for *: 'NoneType' and 'float'")
        def ok_fetch(url, headers, timeout):
            return 200, b"[]"
        with mock.patch.object(agent.usage_mod, "credit_guards", explode):
            guards = agent.plan_credit_guards(
                _reg_credit(), now=NOW, fetch=ok_fetch,
                env=_env_key(self.keydir))
        self.assertEqual(guards["ovhcloud"]["state"], "guard error")
        self.assertIn("TypeError", guards["ovhcloud"]["note"])
        self.assertNotIn("NoneType", guards["ovhcloud"]["note"])

    def test_guard_error_keeps_the_leg_with_a_visible_line(self):
        guards = {"ovhcloud": {"provider": "ovhcloud", "state": "guard error",
                              "spend_usd": 0.0, "spend_unknown": True,
                              "cap_usd": 200.0, "warn_usd": 160.0,
                              "models_unpriced": 0,
                              "note": "credit guard error ovhcloud "
                                      "(TypeError) - leg kept"}}
        kept, skipped, warns = _legs_for(_reg_credit(), "r-trial", guards)
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)
        self.assertTrue(any("guard error" in w for w in warns), warns)


class C2FallbackNeverRaises(unittest.TestCase):
    def test_unreadable_fallback_survives_a_malformed_registry(self):
        with mock.patch.object(usage, "monthly_cap_usd",
                              side_effect=KeyError("monthly_cap_usd")):
            out = usage.credit_guards_unreadable(_reg_credit(), "boom")
        self.assertEqual(out["ovhcloud"]["state"], "unknown")

    def test_unreadable_fallback_survives_a_non_dict_providers(self):
        out = usage.credit_guards_unreadable({"providers": ["morph"]}, "boom")
        self.assertEqual(out, {})


class C3UsageReportMissingCap(unittest.TestCase):
    def test_missing_cap_is_exit_3_with_one_line(self):
        reg = _reg_credit()
        del reg["providers"]["ovhcloud"]["monthly_cap_usd"]
        with tempfile.TemporaryDirectory() as tmp:
            rp = os.path.join(tmp, "reg.json")
            with open(rp, "w", encoding="utf-8") as fh:
                json.dump(reg, fh)
            cfg = os.path.join(tmp, "ai-stack")
            os.mkdir(cfg)
            with open(os.path.join(cfg, "manage.key"), "w",
                      encoding="utf-8") as fh:
                fh.write("test-key\n")
            env = {"AUTOOS_AI_STACK_CONFIG": cfg,
                   "AUTOOS_OMNIROUTE_URL": "http://gw.invalid:20128",
                   "HOME": tmp}
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "stdout", out), \
                    mock.patch.object(sys, "stderr", err):
                rc = usage.main(["--since", "1h", "--cost", "--registry", rp],
                                fetch=lambda u, h, t: (200, b"[]"),
                                env=env, now=NOW)
        self.assertEqual(rc, 3)
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class C4PaidStaysFailClosed(unittest.TestCase):
    """A tier==paid provider with a cap holds the leg when spend is
    unmeasurable (post-paid overage bills real money); tier==credit keeps."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        self.keydir = tempfile.mkdtemp(prefix="t1c2-paid-")
        with open(os.path.join(self.keydir, "manage.key"), "w") as fh:
            fh.write("x")

    def test_paid_with_unmeasurable_spend_holds_the_leg(self):
        def down(url, headers, timeout):
            raise OSError("connection refused")
        guards = agent.plan_credit_guards(
            _reg_paid(), now=NOW, fetch=down, env=_env_key(self.keydir))
        kept, skipped, _warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertNotIn(("deepseek", "ds-model"), kept)
        self.assertIn("deepseek/ds-model", skipped)

    def test_credit_with_unmeasurable_spend_keeps_the_leg(self):
        def down(url, headers, timeout):
            raise OSError("connection refused")
        guards = agent.plan_credit_guards(
            _reg_credit(), now=NOW, fetch=down, env=_env_key(self.keydir))
        self.assertEqual(guards["ovhcloud"]["state"], "unknown")
        kept, skipped, _warns = _legs_for(_reg_credit(), "r-trial", guards)
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)


class C5MissingGuardIsNamed(unittest.TestCase):
    def test_empty_map_for_a_credit_provider_says_no_guard(self):
        kept, skipped, warns = _legs_for(_reg_credit(), "r-trial", {})
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)
        self.assertTrue(any("no guard for ovhcloud" in w for w in warns),
                       warns)


if __name__ == "__main__":
    unittest.main()
