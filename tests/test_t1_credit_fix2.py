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


class C4PaidLastResort(unittest.TestCase):
    """D-212: paid legs (deepseek) are STANDING LAST-RESORT legs.
    
    A paid leg with unmeasurable spend is KEPT with a visible note
    'paid spend unmeasured <provider> - leg kept (last resort, D-212)'.
    It is REFUSED only when MEASURED spend >= its monthly_cap_usd.
    """

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        self.keydir = tempfile.mkdtemp(prefix="t1c2-paid-")
        with open(os.path.join(self.keydir, "manage.key"), "w") as fh:
            fh.write("x")

    def test_paid_with_unmeasurable_spend_keeps_the_leg(self):
        """Paid leg with unmeasurable spend is kept (last resort), not held."""
        def down(url, headers, timeout):
            raise OSError("connection refused")
        guards = agent.plan_credit_guards(
            _reg_paid(), now=NOW, fetch=down, env=_env_key(self.keydir))
        # The guard state should be "unknown" with a note about unmeasured spend
        self.assertEqual(guards["deepseek"]["state"], "unknown")
        self.assertIn("paid spend unmeasured deepseek", guards["deepseek"]["note"])
        self.assertIn("leg kept (last resort, D-212)", guards["deepseek"]["note"])
        kept, skipped, warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertIn(("deepseek", "ds-model"), kept)
        self.assertNotIn("deepseek/ds-model", skipped)
        self.assertTrue(any("paid spend unmeasured deepseek" in w and "leg kept" in w
                            for w in warns), warns)

    def test_paid_with_measured_spend_above_cap_refuses_the_leg(self):
        """Paid leg with measured spend >= monthly_cap_usd is refused."""
        # Create a registry with paid provider and measured spend above cap
        reg = _reg_paid(cap=25.0)
        # 30M in + 30M out at 1e-06/token = $60, above $25 cap
        rows = [{"timestamp": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "provider": "deepseek", "model": "ds-model",
                 "tokens": {"in": 30_000_000, "out": 30_000_000}}]
        def ok_fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode()
        guards = agent.plan_credit_guards(
            reg, now=NOW, fetch=ok_fetch, env=_env_key(self.keydir))
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        kept, skipped, _warns = _legs_for(reg, "r-paid", guards)
        self.assertNotIn(("deepseek", "ds-model"), kept)
        self.assertIn("deepseek/ds-model", skipped)

    def test_paid_with_measured_spend_below_cap_keeps_the_leg(self):
        """Paid leg with measured spend < monthly_cap_usd is kept."""
        reg = _reg_paid(cap=25.0)
        # 5M in + 5M out at 1e-06/token = $10, below $25 cap
        rows = [{"timestamp": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "provider": "deepseek", "model": "ds-model",
                 "tokens": {"in": 5_000_000, "out": 5_000_000}}]
        def ok_fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode()
        guards = agent.plan_credit_guards(
            reg, now=NOW, fetch=ok_fetch, env=_env_key(self.keydir))
        self.assertEqual(guards["deepseek"]["state"], "ok")
        kept, skipped, _warns = _legs_for(reg, "r-paid", guards)
        self.assertIn(("deepseek", "ds-model"), kept)
        self.assertNotIn("deepseek/ds-model", skipped)

    def test_credit_with_unmeasurable_spend_keeps_the_leg(self):
        """Credit tier still fails open (unchanged behavior)."""
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


class C6ClassFilterLatentBug(unittest.TestCase):
    """T1-CREDIT-FIX-4: a class missing from EITHER scoring map is unscorable.

    latency_minutes() and track.p_success() both need the class in
    policy.latency_seed AND policy.seed_priors, so a route whose class is
    present in only one map is removed by the filter with the gap named --
    never left to crash the whole plan in step 4."""

    def test_only_latency_seed_nonempty_credit_class_is_removed(self):
        """CREDIT-FIX-4: a class missing from either map is unscorable.

        With only latency_seed populated the class is absent from
        seed_priors, so track.p_success would raise; the route is removed
        with the named reason instead."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "model_prefix": "ovh", "credit_usd": 200.0,
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "trains_on_prompts": False},
            },
            "models": {
                "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                               "context_usable": {"tokens": 100000,
                                                 "source": "default"},
                               "price_in": 1e-06, "price_out": 1e-06},
            },
            "routes": {
                "r-credit": {"id": "r-credit", "class": "credit",
                            "legs": ["ovhcloud/ovh-priced"]},
            },
            "policy": {"leg_rules": [],
                       "latency_seed": {"credit": {"minutes": 5, "source": "default"}},
                       "seed_priors": {}},  # empty!
        }
        guards = usage.credit_guards(reg, [], datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))
        survivors, removed = r.filter_routes(
            {"kind": "implement", "privacy": "public"}, {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards)
        self.assertNotIn("r-credit", survivors)
        self.assertIn("r-credit", removed)
        self.assertTrue(any("no scoring priors for class" in reason
                            for reason in removed["r-credit"]),
                        removed["r-credit"])

    def test_only_seed_priors_nonempty_credit_class_is_removed(self):
        """CREDIT-FIX-4: a class missing from either map is unscorable.

        With only seed_priors populated the class is absent from latency_seed,
        so latency_minutes would raise; the route is removed with the named
        reason instead."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "model_prefix": "ovh", "credit_usd": 200.0,
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "trains_on_prompts": False},
            },
            "models": {
                "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                               "context_usable": {"tokens": 100000,
                                                 "source": "default"},
                               "price_in": 1e-06, "price_out": 1e-06},
            },
            "routes": {
                "r-credit": {"id": "r-credit", "class": "credit",
                            "legs": ["ovhcloud/ovh-priced"]},
            },
            "policy": {"leg_rules": [],
                       "latency_seed": {},  # empty!
                       "seed_priors": {"credit": {"S0": {"alpha": 2, "beta": 1, "source": "default"}}}},
        }
        guards = usage.credit_guards(reg, [], datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))
        survivors, removed = r.filter_routes(
            {"kind": "implement", "privacy": "public"}, {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards)
        self.assertNotIn("r-credit", survivors)
        self.assertIn("r-credit", removed)
        self.assertTrue(any("no scoring priors for class" in reason
                            for reason in removed["r-credit"]),
                        removed["r-credit"])

    def test_both_maps_nonempty_credit_class_survives(self):
        """When both maps have credit entries, the route should survive."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "model_prefix": "ovh", "credit_usd": 200.0,
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "trains_on_prompts": False},
            },
            "models": {
                "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                               "context_usable": {"tokens": 100000,
                                                 "source": "default"},
                               "price_in": 1e-06, "price_out": 1e-06},
            },
            "routes": {
                "r-credit": {"id": "r-credit", "class": "credit",
                            "legs": ["ovhcloud/ovh-priced"]},
            },
            "policy": {"leg_rules": [],
                       "latency_seed": {"credit": {"minutes": 5, "source": "default"}},
                       "seed_priors": {"credit": {"S0": {"alpha": 2, "beta": 1, "source": "default"}}}},
        }
        guards = usage.credit_guards(reg, [], datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))
        survivors, removed = r.filter_routes(
            {"kind": "implement", "privacy": "public"}, {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards)
        self.assertIn("r-credit", survivors, "credit class should survive when both maps have entries")
        self.assertNotIn("r-credit", removed)

    def test_missing_class_in_both_maps_is_removed(self):
        """When both maps have entries but the class is missing from both, route is removed."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "model_prefix": "ovh", "credit_usd": 200.0,
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "trains_on_prompts": False},
            },
            "models": {
                "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                               "context_usable": {"tokens": 100000,
                                                 "source": "default"},
                               "price_in": 1e-06, "price_out": 1e-06},
            },
            "routes": {
                "r-unknown": {"id": "r-unknown", "class": "unknown-class",
                             "legs": ["ovhcloud/ovh-priced"]},
            },
            "policy": {"leg_rules": [],
                       "latency_seed": {"credit": {"minutes": 5, "source": "default"}},
                       "seed_priors": {"credit": {"S0": {"alpha": 2, "beta": 1, "source": "default"}}}},
        }
        guards = usage.credit_guards(reg, [], datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))
        survivors, removed = r.filter_routes(
            {"kind": "implement", "privacy": "public"}, {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards)
        self.assertNotIn("r-unknown", survivors)
        self.assertIn("r-unknown", removed)
        self.assertTrue(any("scoring priors" in reason for reason in removed["r-unknown"]))


class C7NaNAndDateValidation(unittest.TestCase):
    """T1-CREDIT-FIX-3: reject non-finite values and validate date format."""

    def test_manual_credit_spend_rejects_nan(self):
        """NaN credit_spent_usd is rejected (returns None, None)."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "credit_spent_usd": float("nan"),
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        figure, as_of = usage.manual_credit_spend(reg, "ovhcloud")
        self.assertIsNone(figure)
        self.assertIsNone(as_of)

    def test_manual_credit_spend_rejects_inf(self):
        """Infinity credit_spent_usd is rejected."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "credit_spent_usd": float("inf"),
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        figure, as_of = usage.manual_credit_spend(reg, "ovhcloud")
        self.assertIsNone(figure)
        self.assertIsNone(as_of)

    def test_manual_credit_spend_rejects_neg_inf(self):
        """-Infinity credit_spent_usd is rejected."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "credit_spent_usd": float("-inf"),
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        figure, as_of = usage.manual_credit_spend(reg, "ovhcloud")
        self.assertIsNone(figure)
        self.assertIsNone(as_of)

    def test_manual_credit_spend_rejects_negative(self):
        """Negative credit_spent_usd is rejected.

        CREDIT-FIX-4: this pins the pre-existing manual-credit rule; it passed
        before any fix, so it is a regression guard, not proof of a change."""

        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "credit_spent_usd": -1.0,
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        figure, as_of = usage.manual_credit_spend(reg, "ovhcloud")
        self.assertIsNone(figure)
        self.assertIsNone(as_of)

    def test_manual_credit_spend_rejects_invalid_date(self):
        """Invalid date format is rejected."""
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "credit_spent_usd": 50.0,
                             "credit_spent_as_of": "tomorrow"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        figure, as_of = usage.manual_credit_spend(reg, "ovhcloud")
        self.assertIsNone(figure)
        self.assertIsNone(as_of)

    def test_registry_validation_rejects_nan(self):
        """Registry validation flags NaN credit_spent_usd."""
        import registry as registry_mod
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_spent_usd": float("nan"),
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        problems = list(registry_mod._check_credit_guards(reg))
        self.assertTrue(any("credit_spent_usd must be a finite number" in p for p in problems), problems)

    def test_registry_validation_rejects_inf(self):
        """Registry validation flags Infinity credit_spent_usd."""
        import registry as registry_mod
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_spent_usd": float("inf"),
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        problems = list(registry_mod._check_credit_guards(reg))
        self.assertTrue(any("credit_spent_usd must be a finite number" in p for p in problems), problems)

    def test_registry_validation_rejects_invalid_date(self):
        """Registry validation flags invalid date format."""
        import registry as registry_mod
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_spent_usd": 50.0,
                             "credit_spent_as_of": "tomorrow"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        problems = list(registry_mod._check_credit_guards(reg))
        self.assertTrue(any("credit_spent_as_of must be a valid date" in p for p in problems), problems)

    def test_registry_validation_accepts_valid_date(self):
        """Registry validation accepts valid YYYY-MM-DD date."""
        import registry as registry_mod
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_spent_usd": 50.0,
                             "credit_spent_as_of": "2026-09-15"},
            },
            "models": {},
            "routes": {},
            "policy": {},
        }
        problems = list(registry_mod._check_credit_guards(reg))
        self.assertFalse(any("credit_spent_as_of" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
