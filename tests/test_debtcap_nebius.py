"""D-287 (lane DEBTCAP-T2 NEBIUS-OUT): nebius is debt-capable, so it is out
of every free/trial combo or route.

Operator rule D-287: nebius incurred real debt (it is debt-capable billing),
so providers.nebius must be unavailable to the free/trial tiers. The provider
row and its three model rows stay registered for pricing history; only
`available` flips to false with the reason recorded on the row, and the
resolver then drops every nebius/* leg through its own `unavailable_now`
helper.

Stdlib-only unittest, no network, no gateway, no container (matching every
other suite in this repo).
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import autoos_resolver as r  # noqa: E402  (the shipped resolver view)
import registry  # noqa: E402  (tools/registry.py; load/unavailable_now live here)

REASON = ("D-287 operator: debt-capable billing (incurred debt), "
          "excluded from free/trial tiers")
NEBIUS_LEG_IDS = (
    "nebius/zai-org/GLM-5.1",
    "nebius/zai-org/GLM-5.2",
    "nebius/zai-org/GLM-5.3-Flash",
)
NEBIUS_MODELS = (
    "zai-org/GLM-5.1",
    "zai-org/GLM-5.2",
    "zai-org/GLM-5.3-Flash",
)
# The full tiers the brief names alongside the free/trial/credit classes.
NAMED_TIERS = ("t1-orchestrator", "t2-worker", "t3-driver")
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def load_registry():
    return registry.load(str(ROOT / "catalog" / "ai-registry.json"))


def free_tier_routes(reg):
    """The route set D-287 scopes: route class `free`, any `*-free-only`
    route, plus the three named full tiers (t1-orchestrator, t2-worker,
    t3-driver)."""
    picked = {}
    for route_id, route in (reg.get("routes") or {}).items():
        if (isinstance(route, dict)
                and (route.get("class") == "free"
                     or route_id.endswith("-free-only")
                     or route_id in NAMED_TIERS)):
            picked[route_id] = route
    return picked


def strings(node):
    """Every string in a JSON tree, depth-first."""
    if isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


class ProviderFlagTests(unittest.TestCase):
    """(a) providers.nebius: available false + the exact D-287 reason."""

    def test_provider_available_is_false(self):
        provider = load_registry()["providers"]["nebius"]
        self.assertIs(provider.get("available"), False,
                      "providers.nebius must carry available: false (D-287)")

    def test_reason_sentence_is_recorded_on_the_provider_row(self):
        provider = load_registry()["providers"]["nebius"]
        self.assertTrue(
            any(REASON in text for text in strings(provider)),
            "providers.nebius must record the exact reason sentence %r" % REASON)


class ResolverRefusesNebiusTests(unittest.TestCase):
    """(b) the shipped resolver view serves no nebius leg in those tiers."""

    def test_the_target_route_set_is_not_empty(self):
        self.assertTrue(free_tier_routes(load_registry()),
                        "route filter selected nothing - the test would pass vacuously")

    def test_no_target_route_serves_a_nebius_leg(self):
        reg = load_registry()
        for route_id, route in free_tier_routes(reg).items():
            served = [leg for leg in r.serving_legs(route, reg, now=NOW)
                      if leg[0] == "nebius"]
            self.assertEqual(
                served, [],
                "route %s still serves nebius legs: %r" % (route_id, served))

    def test_the_provider_flag_hard_refuses_every_nebius_leg(self):
        # The mechanism, not just today's route lists: the resolver's own
        # availability helper must hand every nebius/* leg a hard reason.
        reg = load_registry()
        for leg in NEBIUS_LEG_IDS:
            hard_reason, _notes = r._leg_availability(leg, "nebius", {}, reg, NOW)
            self.assertTrue(
                hard_reason,
                "resolver does not refuse %s (no hard reason; available still true?)"
                % leg)

    def test_serving_legs_drops_a_nebius_leg_inserted_into_a_target_route(self):
        # Seed the bug state directly: a route that lists a nebius leg must
        # not serve it, whatever the other legs look like.
        reg = load_registry()
        route_id, route = sorted(free_tier_routes(reg).items())[0]
        mutated = dict(route, legs=list(route.get("legs") or [])
                       + ["nebius/zai-org/GLM-5.2"])
        served = [pid for pid, _mid in r.serving_legs(mutated, reg, now=NOW)]
        self.assertNotIn(
            "nebius", served,
            "route %s serves a nebius leg the resolver should drop" % route_id)


class CombosCarryNoNebiusLegTests(unittest.TestCase):
    """(c) configuration/omniroute/combos.json: no 'nebius/' string in any
    combo's models/legs arrays (comments may mention it)."""

    def _entries(self):
        doc = json.loads(
            (ROOT / "configuration" / "omniroute" / "combos.json")
            .read_text(encoding="utf-8"))
        for key in ("combos", "omitted"):
            for entry in doc.get(key) or []:
                if isinstance(entry, dict):
                    yield key, entry

    def test_no_models_or_legs_entry_names_nebius(self):
        found = []
        for section, entry in self._entries():
            for field in ("models", "legs"):
                for value in entry.get(field) or []:
                    if isinstance(value, str) and "nebius/" in value:
                        found.append((section, entry.get("id"), field, value))
        self.assertEqual(found, [], "nebius legs still in combos: %r" % found)

    def test_the_scan_actually_looks_at_combos(self):
        # Guard against an empty scan passing vacuously.
        self.assertTrue(any(True for _ in self._entries()))


class RowsKeptTests(unittest.TestCase):
    """(d) the nebius rows still exist - pricing history, not deletions."""

    def test_provider_row_still_exists(self):
        self.assertIn("nebius", load_registry()["providers"])

    def test_three_model_rows_still_exist(self):
        models = load_registry()["models"]
        for model_id in NEBIUS_MODELS:
            self.assertIn(model_id, models, "models.%s must stay registered" % model_id)


if __name__ == "__main__":
    unittest.main()
