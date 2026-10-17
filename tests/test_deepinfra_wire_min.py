"""DEEPINFRA retirement pins (operator 2026-10-13, DIRETIRED): DeepInfra left
the setup entirely - no provider row, no model rows, no opencode pin, no route
legs. The $5 prepaid grant history lives in CHANGELOG (DEEPINFRA-WIRE-MIN,
FREEKEYS-1) and git history, not in live data.

No network, no gateway: reads opencode.jsonc and catalog/ai-registry.json only.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_ID = "deepinfra-mimo-v2.6-flash"


def load_registry():
    return json.loads((ROOT / "catalog" / "ai-registry.json").read_text(encoding="utf-8"))


def load_opencode():
    # Whole-line // comments only (the repo's own strip rule, see tools/audit-router.py).
    raw = (ROOT / "opencode.jsonc").read_text(encoding="utf-8")
    return json.loads(re.sub(r"(?m)^\s*//.*$", "", raw))


def route_legs(route):
    """Every leg a route names: serving legs plus unavailable_legs keys."""
    legs = list(route.get("legs") or [])
    unavailable = route.get("unavailable_legs") or {}
    legs.extend(unavailable if isinstance(unavailable, dict) else list(unavailable))
    return legs


class DeepinfraRetiredTests(unittest.TestCase):
    def test_opencode_has_no_mimo_pin(self):
        models = load_opencode()["providers"]["omniroute"]["models"]
        self.assertNotIn(PIN_ID, models)

    def test_no_deepinfra_provider_row(self):
        providers = load_registry()["providers"]
        self.assertNotIn("deepinfra", providers)

    def test_no_deepinfra_model_rows(self):
        models = load_registry()["models"]
        offenders = [mid for mid, row in models.items()
                     if isinstance(row, dict) and (
                         str(mid).lower().startswith("deepinfra/")
                         or str(row.get("display_name") or "").lower().startswith("deepinfra/"))]
        self.assertEqual(offenders, [])

    def test_no_route_leg_names_deepinfra(self):
        for route_id, route in load_registry()["routes"].items():
            for leg in route_legs(route):
                self.assertNotIn("deepinfra", leg.lower(),
                                 "route %s has deepinfra leg %s" % (route_id, leg))

    def test_no_deepinfra_leg_serves_claude_or_anthropic(self):
        for route_id, route in load_registry()["routes"].items():
            for leg in route_legs(route):
                if leg.lower().startswith("deepinfra/"):
                    self.assertIsNone(re.search(r"anthropic/|claude", leg, re.I),
                                      "route %s has deepinfra leg %s" % (route_id, leg))

    def test_no_clean_route_contains_a_deepinfra_leg(self):
        for route_id, route in load_registry()["routes"].items():
            if route_id.endswith("-clean"):
                for leg in route_legs(route):
                    self.assertFalse(leg.lower().startswith("deepinfra/"),
                                     "-clean route %s has deepinfra leg %s" % (route_id, leg))


if __name__ == "__main__":
    unittest.main()
