"""DEEPINFRA-WIRE-MIN (operator D-366/D-381): MiMo-V2.6-Flash as an opencode writer pin.

DeepInfra's XiaomiMiMo/MiMo-V2.6-Flash is reachable through the gateway as
``deepinfra/XiaomiMiMo/MiMo-V2.6-Flash``.  The provider is a $5 prepaid grant with
auto top-up removed, so the registry's credit guard (monthly_cap_usd == credit_usd,
refuse at the cap) is the hard stop.  DeepInfra is not private-safe (no cited
no-training terms) and must never serve a Claude/Anthropic model.

No network, no gateway: reads opencode.jsonc and catalog/ai-registry.json only.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_ID = "deepinfra-mimo-v2.6-flash"
MODEL_ID = "XiaomiMiMo/MiMo-V2.6-Flash"
GATEWAY_MODEL = "deepinfra/" + MODEL_ID


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


class DeepinfraWireMinTests(unittest.TestCase):
    def test_opencode_declares_the_writer_pin(self):
        models = load_opencode()["providers"]["omniroute"]["models"]
        self.assertIn(PIN_ID, models)
        self.assertEqual(models[PIN_ID]["modelID"], GATEWAY_MODEL)
        self.assertIn("hard stop", models[PIN_ID]["name"])
        self.assertIn("limit", models[PIN_ID])

    def test_deepinfra_is_a_prepaid_hard_stop(self):
        # The registry has no `billing` block; the hard stop is the credit guard:
        # tier credit, cap == grant (refuse at 100%), the operator's prepaid note.
        provider = load_registry()["providers"]["deepinfra"]
        self.assertEqual(provider["tier"], "credit")
        self.assertEqual(provider["credit_usd"], 5)
        self.assertEqual(provider["monthly_cap_usd"], provider["credit_usd"])
        source = provider["monthly_cap_source"]
        self.assertIn("prepaid", source)
        self.assertIn("top-up removed", source)
        self.assertIn("hard stop", source)

    def test_mimo_model_row_exists_and_is_xiaomi(self):
        row = load_registry()["models"][MODEL_ID]
        self.assertEqual(row["id"], MODEL_ID)
        self.assertEqual(row["family"], "xiaomi")
        self.assertEqual(row["display_name"], GATEWAY_MODEL)
        self.assertIsNone(row.get("trains_on_prompts"))

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
