"""T1-CREDIT-FIX-7 (red first): review findings on 12d19789 (CREDIT-FIX-6).

R1: is_spend_row must accept registry id, omniroute_id, and model-prefix rows.
R2: credit_started extends the measured window to the whole grant.
R3: pins that gateway_legs does NO credit gating (D-220 follow-up T1-COMBO3).
R4: nan/inf/huge spend is unknown / total, one bad row never kills the map.
R5: SPEND UNKNOWN loud line only for legs that survive reasons.
"""
import datetime
import json
import math
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage
import registry as regmod

_ROOT = Path(__file__).resolve().parent.parent

NOW = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
SINCE_MONTH = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)

VERTEX_PRICE = (1.5e-06, 7.5e-06)
# 100M in + 30M out at vertex price = 150 + 225 = $375
BIG = {"in": 100_000_000, "out": 30_000_000}


def _reg():
    return {
        "providers": {
            "vertex_ai": {"id": "vertex_ai", "tier": "credit",
                          "omniroute_id": "vertex",
                          "credit_usd": 250.0, "monthly_cap_usd": 250.0,
                          "monthly_warn_fraction": 0.8,
                          "trains_on_prompts": False},
            "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                         "omniroute_id": "ovhcloud",
                         "model_prefix": "ovh",
                         "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                         "monthly_warn_fraction": 0.8,
                         "trains_on_prompts": False},
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "omniroute_id": "deepseek",
                         "monthly_cap_usd": 25.0,
                         "monthly_warn_fraction": 0.8},
            "google_ai_studio": {"id": "google_ai_studio", "tier": "free"},
        },
        "models": {
            "gemini-3.8-flash": {
                "id": "gemini-3.8-flash", "price_in": 0.0, "price_out": 0.0,
                "tool_calls": "proven",
                "provider_prices": {
                    "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                                  "price_source": "https://example.invalid",
                                  "price_as_of": "2026-10-01"}}},
        },
        "routes": {},
        "policy": {"leg_rules": []},
    }


def _row(provider, model, tokens=None, ts="2026-10-01T10:00:00Z"):
    return {"id": "r-%s-%s" % (provider, ts), "provider": provider,
            "model": model, "tokens": tokens or dict(BIG),
            "timestamp": ts, "status": 200}


def _prices(reg):
    return usage.prices_from_registry(reg)


class R1GatewaySpellingsCount(unittest.TestCase):
    def test_vertex_gateway_id_counts(self):
        reg = _reg()
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "provider 'vertex' row must bill vertex_ai, got %r"
                         % (guards["vertex_ai"],))

    def test_vertex_registry_id_counts(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse")

    def test_model_prefix_only_counts(self):
        # provider field carries the gateway spelling, model carries prefix
        reg = _reg()
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        self.assertTrue(usage.is_spend_row(rows[0], "vertex_ai"))

    def test_similar_prefix_does_not_count(self):
        self.assertFalse(usage.is_spend_row(
            _row("vertexish", "vertexish/gemini-3.8-flash"), "vertex_ai"))
        self.assertFalse(usage.is_spend_row(
            _row("google_ai_studio", "gemini-3.8-flash"), "vertex_ai"))

    def test_ai_studio_free_stays_zero(self):
        reg = _reg()
        rows = [_row("google_ai_studio", "gemini/gemini-3.8-flash",
                     tokens={"in": 10_000_000, "out": 1_000_000})]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 0.0)
        self.assertEqual(guards["vertex_ai"]["state"], "ok")


class R2CreditStartedWindow(unittest.TestCase):
    def test_grant_window_includes_prior_month(self):
        reg = _reg()
        reg["providers"]["vertex_ai"]["credit_started"] = "2026-09-01"
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     ts="2026-09-15T10:00:00Z")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH, today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "September spend must count when credit_started "
                         "is 2026-09-01, got %r" % (guards["vertex_ai"],))

    def test_absent_credit_started_stays_month_to_date(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     ts="2026-09-15T10:00:00Z")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH, today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 0.0)
        self.assertIn("month-to-date", guards["vertex_ai"]["note"])


class R3GatewayLegsUngated(unittest.TestCase):
    def test_gateway_legs_still_lists_credit_leg(self):
        # D-220 follow-up: gateway-side credit gate (T1-COMBO3).
        # Pins CURRENT (ungated) behaviour; change only with that gate.
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        found = None
        for rid, route in (reg.get("routes") or {}).items():
            for leg in (route.get("legs") or []):
                if leg.split("/")[0] in ("vertex", "vertex_ai"):
                    found = (rid, route)
                    break
            if found:
                break
        self.assertIsNotNone(found, "shipped registry must carry a vertex leg")
        rid, route = found
        legs = regmod.gateway_legs(route, reg)
        self.assertIn(route["legs"][0].split("/", 1)[1] if "/" in route["legs"][0] else route["legs"][0],
                      [str(x).split("/", 1)[-1] for x in legs],
                      "gateway_legs must still list the vertex leg %r in %s"
                      % (route["legs"], rid))


class R4NonFiniteSpend(unittest.TestCase):
    def test_nan_spend_is_unknown(self):
        reg = _reg()
        state, _note = usage.spend_guard(reg, "vertex_ai", float("nan"))
        self.assertEqual(state, "unknown")

    def test_inf_spend_is_unknown(self):
        reg = _reg()
        state, _note = usage.spend_guard(reg, "vertex_ai", float("inf"))
        self.assertEqual(state, "unknown")

    def test_as_int_total(self):
        self.assertEqual(usage._as_int(float("inf")), 0)
        self.assertEqual(usage._as_int(float("nan")), 0)
        self.assertEqual(usage._as_int(10 ** 400), 0)

    def test_one_bad_row_degrades_only_that_row(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     tokens={"in": float("inf"), "out": 5}),
                _row("vertex_ai", "vertex/gemini-3.8-flash",
                     tokens={"in": 100, "out": 100})]
        spend = usage.paid_spend(rows, _prices(reg), reg, SINCE_MONTH,
                                 provider="vertex_ai")
        self.assertEqual(spend["calls"], 1)
        self.assertGreater(spend["spend_usd"], 0.0)


class R5LoudLineOnlyForSurvivors(unittest.TestCase):
    def _vertex_route(self):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        for rid, route in (reg.get("routes") or {}).items():
            legs = route.get("legs") or []
            if any(l.split("/")[0] in ("vertex", "vertex_ai") for l in legs):
                return reg, rid, route
        self.fail("shipped registry must carry a vertex leg")

    def _unknown_guards(self, reg):
        return usage.credit_guards_unreadable(reg, "test: no ledger")

    def test_skipped_leg_emits_no_kept_line(self):
        reg, rid, route = self._vertex_route()
        guards = self._unknown_guards(reg)
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "implement", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        skipped_ids = [s[0] if isinstance(s, (list, tuple)) else s
                       for s in skipped]
        vertex_skipped = any(str(x).split("/")[0] in ("vertex", "vertex_ai")
                             for x in skipped_ids)
        if vertex_skipped:
            self.assertFalse(any("SPEND UNKNOWN" in w and "vertex" in w
                                 for w in warns),
                             "skipped leg must not say 'kept': %r" % (warns,))

    def test_kept_leg_emits_loud_line(self):
        reg, rid, route = self._vertex_route()
        guards = self._unknown_guards(reg)
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "review", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        kept_ids = [k if isinstance(k, str) else k[0] for k in kept]
        if any(str(x).split("/")[0] in ("vertex", "vertex_ai")
               for x in kept_ids):
            self.assertTrue(any("SPEND UNKNOWN" in w for w in warns),
                            "kept unknown-spend leg must warn LOUDLY")


if __name__ == "__main__":
    unittest.main()
