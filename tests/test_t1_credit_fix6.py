"""T1-CREDIT-FIX-6 (red first): per-provider pricing for credit legs.

`gemini-3.8-flash` is served FREE by AI-Studio (model-level price 0) and PAID
by Vertex AI (credit grant $250). Pricing the shared model row would bill the
free leg; leaving it 0 skips the vertex leg as 'credit leg unpriced'. The
price lives per provider: models.<id>.provider_prices.<provider_id>.
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage
import registry as regmod

from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent

NOW = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
SINCE = datetime.datetime(2026, 9, 1, 0, 0, tzinfo=datetime.timezone.utc)

VERTEX_PRICE = (1.5e-06, 7.5e-06)


def _ok_guard(provider, cap=250.0):
    return {"provider": provider, "state": "ok", "spend_usd": 0.0,
            "spend_unknown": False, "cap_usd": cap, "warn_usd": cap * 0.8,
            "models_unpriced": 0, "note": "ok"}


def _synthetic(with_provider_price=True):
    model = {"id": "gemini-3.8-flash", "tool_calls": "proven",
             "context_usable": {"tokens": 100000, "source": "default"},
             "price_in": 0.0, "price_out": 0.0}
    if with_provider_price:
        model["provider_prices"] = {
            "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                          "price_source": "https://example.invalid/pricing",
                          "price_as_of": "2026-10-01"}}
    return {
        "providers": {
            "vertex_ai": {"id": "vertex_ai", "tier": "credit",
                          "omniroute_id": "vertex",
                          "credit_usd": 250.0, "monthly_cap_usd": 250.0,
                          "monthly_warn_fraction": 0.8,
                          "trains_on_prompts": False},
            "gemini": {"id": "gemini", "tier": "free",
                       "trains_on_prompts": True},
        },
        "models": {"gemini-3.8-flash": model},
        "routes": {"r-vertex": {"id": "r-vertex",
                               "legs": ["vertex/gemini-3.8-flash"]}},
        "policy": {"leg_rules": []},
    }


def _legs_for(reg, guards):
    warns = []
    kept, skipped, _notes = r.usable_legs(
        reg["routes"]["r-vertex"], {"kind": "chat", "privacy": "public"},
        {"need_tokens": 10},
        {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
        reg, {}, credit_guards=guards, credit_warns=warns)
    return kept, skipped


class C1ShippedRegistryVertexPriced(unittest.TestCase):
    """The shipped registry prices the vertex leg without pricing the free leg."""

    @classmethod
    def setUpClass(cls):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            cls.reg = json.load(fh)

    def test_leg_price_vertex_uses_list_price(self):
        self.assertEqual(
            regmod.leg_price("gemini-3.8-flash", "vertex_ai", self.reg),
            VERTEX_PRICE)

    def test_credit_leg_priced_vertex_true_free_false(self):
        self.assertTrue(
            r.credit_leg_priced("gemini-3.8-flash", self.reg, "vertex_ai"))
        self.assertFalse(r.credit_leg_priced("gemini-3.8-flash", self.reg))
        self.assertFalse(
            r.credit_leg_priced("gemini-3.8-flash", self.reg, "gemini"))

    def test_shipped_vertex_route_not_skipped_unpriced(self):
        guards = {"vertex_ai": _ok_guard("vertex_ai")}
        warns = []
        kept, skipped, _notes = r.usable_legs(
            self.reg["routes"]["vertex-gemini-3.8-flash"],
            {"kind": "chat", "privacy": "public"},
            {"need_tokens": 1000},
            {"opencode": {"installed": True, "signed_in": True,
                          "reason": ""}},
            self.reg, {}, credit_guards=guards, credit_warns=warns)
        self.assertIn(("vertex_ai", "gemini-3.8-flash"), kept)
        for leg, reasons in skipped.items():
            for reason in reasons:
                self.assertNotIn("credit leg unpriced", reason)

    def test_shipped_registry_validates_clean(self):
        self.assertEqual(regmod.check_registry(self.reg), [])

    def test_price_table_has_vertex_spelling_not_bare(self):
        prices = usage.prices_from_registry(self.reg)
        self.assertIn("vertex/gemini-3.8-flash", prices)
        self.assertEqual(prices["vertex/gemini-3.8-flash"], VERTEX_PRICE)
        self.assertNotIn("gemini-3.8-flash", prices)


class C2UsableLegsKeepsProviderPriced(unittest.TestCase):
    def test_provider_priced_credit_leg_kept(self):
        reg = _synthetic(True)
        kept, skipped = _legs_for(reg, {"vertex_ai": _ok_guard("vertex_ai")})
        self.assertIn(("vertex_ai", "gemini-3.8-flash"), kept)
        self.assertNotIn("vertex/gemini-3.8-flash", skipped)

    def test_unpriced_control_still_skipped(self):
        reg = _synthetic(False)
        kept, skipped = _legs_for(reg, {"vertex_ai": _ok_guard("vertex_ai")})
        self.assertNotIn(("vertex_ai", "gemini-3.8-flash"), kept)
        self.assertIn("credit leg unpriced gemini-3.8-flash",
                      skipped.get("vertex/gemini-3.8-flash", []))


class C3ValidationRejectsBadProviderPrices(unittest.TestCase):
    def _reg_with(self, entry):
        reg = _synthetic(True)
        reg["models"]["gemini-3.8-flash"]["provider_prices"] = {
            "vertex_ai": entry}
        return reg

    def _problems(self, entry):
        return [p for p in regmod.check_registry(self._reg_with(entry))
                if "provider_prices" in p]

    def test_zero_price_rejected(self):
        probs = self._problems(
            {"price_in": 0.0, "price_out": 7.5e-06,
             "price_source": "https://example.invalid/p",
             "price_as_of": "2026-10-01"})
        self.assertTrue(any("price_in" in p for p in probs), probs)

    def test_missing_source_rejected(self):
        probs = self._problems(
            {"price_in": 1.5e-06, "price_out": 7.5e-06,
             "price_as_of": "2026-10-01"})
        self.assertTrue(any("price_source" in p for p in probs), probs)

    def test_unknown_provider_rejected(self):
        reg = _synthetic(True)
        reg["models"]["gemini-3.8-flash"]["provider_prices"] = {
            "no_such_provider": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                                 "price_source": "https://example.invalid/p",
                                 "price_as_of": "2026-10-01"}}
        probs = [p for p in regmod.check_registry(reg)
                 if "provider_prices" in p]
        self.assertTrue(any("no_such_provider" in p for p in probs), probs)

    def test_bad_date_rejected(self):
        probs = self._problems(
            {"price_in": 1.5e-06, "price_out": 7.5e-06,
             "price_source": "https://example.invalid/p",
             "price_as_of": "10/01/2026"})
        self.assertTrue(any("price_as_of" in p for p in probs), probs)

    def test_good_entry_accepted(self):
        probs = self._problems(
            {"price_in": 1.5e-06, "price_out": 7.5e-06,
             "price_source": "https://example.invalid/p",
             "price_as_of": "2026-10-01"})
        self.assertEqual([p for p in probs if "provider_prices" in p], [])


class C4UsagePricesVertexNotFree(unittest.TestCase):
    def test_paid_spend_vertex_uses_provider_price(self):
        reg = _synthetic(True)
        prices = usage.prices_from_registry(reg)
        rows = [{"provider": "vertex_ai", "model": "vertex/gemini-3.8-flash",
                 "tokens": {"in": 1_000_000, "out": 1_000_000},
                 "timestamp": "2026-10-01T00:00:00Z"}]
        spend = usage.paid_spend(rows, prices, reg, SINCE,
                                 provider="vertex_ai")
        self.assertAlmostEqual(spend["spend_usd"], 9.0, places=6)
        self.assertEqual(spend["models_unpriced"], 0)

    def test_free_leg_stays_free(self):
        reg = _synthetic(True)
        prices = usage.prices_from_registry(reg)
        rows = [{"provider": "gemini", "model": "gemini/gemini-3.8-flash",
                 "tokens": {"in": 1_000_000, "out": 1_000_000},
                 "timestamp": "2026-10-01T00:00:00Z"}]
        spend = usage.paid_spend(rows, prices, reg, SINCE, provider="gemini")
        self.assertAlmostEqual(spend["spend_usd"], 0.0, places=9)
        self.assertEqual(spend["models_unpriced"], 1)

    def test_leg_price_falls_back_to_model_price(self):
        reg = _synthetic(True)
        reg["models"]["plain"] = {
            "id": "plain", "price_in": 1e-06, "price_out": 2e-06}
        self.assertEqual(
            regmod.leg_price("plain", "vertex_ai", reg), (1e-06, 2e-06))
        self.assertEqual(
            regmod.leg_price("plain", None, reg), (1e-06, 2e-06))
        self.assertIsNone(regmod.leg_price("gemini-3.8-flash", None, reg))
        self.assertIsNone(regmod.leg_price("missing", "vertex_ai", reg))


if __name__ == "__main__":
    unittest.main()
