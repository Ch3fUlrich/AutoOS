#!/usr/bin/env python3
"""Unit tests for tools/combo-contract.py's D1 (D-657/D-658) checks (f)-(i).

AO-DENYLEGS D1 is contract only: it writes the gates D2 has to satisfy, and
changes no combo chain. Each check is its own function, so each test builds a
small registry/combo dict and asserts one rule names its combo AND its leg —
a gate that cannot name what it rejected is a gate a reviewer cannot act on.

Path-independent (ROOT from __file__), stdlib-only, run directly:
    python3 tests/test_combo_contract.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"


def _load(name, path):
    """Import a tools/*.py by path (their names are not valid module ids)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load("autoos_combo_contract", ROOT / "tools" / "combo-contract.py")


def mini_registry():
    """One row per shape the four gates care about — a model cached at one
    provider and not at another, an aliased provider namespace, a credit grant
    with and without a price row, a paid model id with a free twin."""
    return {
        "version": "2026-10-08",
        "providers": {
            "vertex_ai": {"omniroute_id": "vertex", "tier": "credit"},
            "google_ai_studio": {"omniroute_id": "gemini", "tier": "free"},
            "bazaarlink": {"model_prefix": "bzl", "tier": "credit"},
            "ovhcloud": {"model_prefix": "ovh", "aliases": ["ovh-eu"],
                         "tier": "credit"},
            "openrouter": {"tier": "paid"},
            "deepseek": {"tier": "paid"},
            "meta_api": {"model_prefix": "meta-api", "tier": "paid"},
            "antigravity": {"model_prefix": "agy", "tier": "free"},
            "qoder_ai": {"omniroute_id": "qoder", "tier": "free"},
        },
        "models": {
            "gemini-3.8-flash": {
                "prompt_cache_by_provider": {"google_ai_studio": "unknown",
                                             "vertex_ai": "documented"},
                "prompt_cache_source": "probe D-657 2026-10-08",
                "provider_prices": {
                    "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                                  "price_source": "vendor pricing page",
                                  "price_as_of": "2026-10-01"}},
            },
            "ds-cached": {"prompt_cache": "true", "tier": "free"},
            "ds-flash": {},
            "ds-v4-flash-free": {"tier": "free"},
            "muse-spark-1.3-contributor": {},
            "muse-spark-1.3-contributor-free": {"tier": "free"},
            "north-mini-code:free": {"tier": "free"},
            "laguna-s-2.1:free": {"tier": "free"},
            "qwen3.8-27b": {"prompt_cache": "false"},
            "opus-5-5": {},
            "sonnet-5-5": {},
            "kimi-k3": {},
        },
        "routes": {}, "clients": {}, "policy": {},
    }


REG = mini_registry()


class CacheGateTests(unittest.TestCase):
    """(f) an l0-/l1-/l2- combo may hold no leg that is not cached."""

    def test_an_orchestrator_combo_with_an_uncached_leg_fails_naming_both(self):
        problems = cc.check_cache_gate(REG, "l2-worker",
                                       ["vertex/gemini-3.8-flash",
                                        "vertex_ai/qwen3.8-27b"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("l2-worker", problems[0])
        self.assertIn("vertex_ai/qwen3.8-27b", problems[0])
        self.assertIn("'false'", problems[0])

    def test_a_measured_or_documented_leg_passes(self):
        self.assertEqual(cc.check_cache_gate(REG, "l1-orchestrator",
                                             ["vertex/gemini-3.8-flash",
                                              "bzl/ds-cached"]), [])

    def test_caching_is_read_per_provider_not_per_model(self):
        """The judge nit (f) exists for: one model id, two providers, two
        verdicts. Vertex's documented caching must not clear the AI-Studio leg."""
        self.assertEqual(
            cc.REGISTRY_TOOL.leg_prompt_cache(REG, "vertex/gemini-3.8-flash"),
            "documented")
        problems = cc.check_cache_gate(REG, "l1-orchestrator",
                                       ["gemini/gemini-3.8-flash"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unknown", problems[0])

    def test_a_missing_verdict_is_unknown_not_a_pass(self):
        problems = cc.check_cache_gate(REG, "l0-anything", ["agy/opus-5-5"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unknown", problems[0])

    def test_l3_and_named_routes_are_out_of_scope(self):
        """§2 gates L0-L2; an L3 implementer chain is chosen for cost, not
        cache, and `deepseek-v4.1-flash` is not a layer id."""
        for combo_id in ("l3-driver", "l3-researcher-diff", "opus-5-5"):
            self.assertEqual(cc.check_cache_gate(REG, combo_id,
                                                 ["agy/opus-5-5"]), [], combo_id)


class ProviderDiversityTests(unittest.TestCase):
    """(g) consecutive legs change rate-limit domain (§7b delta a)."""

    def test_two_consecutive_legs_of_one_provider_fail(self):
        problems = cc.check_provider_diversity(REG, "l2-worker",
                                               ["ovh/gpt-oss-120b",
                                                "ovhcloud/qwen3.8-27b"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ovhcloud", problems[0])
        self.assertIn("l2-worker", problems[0])

    def test_an_alias_spelling_is_the_same_domain(self):
        """bzl == bazaarlink and an `aliases` entry == the provider: one bucket
        of 429s, whichever way the combo spells it."""
        self.assertEqual(cc.provider_domain(REG, "bzl/ds-flash"), "bazaarlink")
        self.assertEqual(cc.provider_domain(REG, "bazaarlink/ds-flash"),
                         "bazaarlink")
        self.assertEqual(cc.provider_domain(REG, "ovh/qwen3.8-27b"), "ovhcloud")
        self.assertEqual(cc.provider_domain(REG, "ovh-eu/qwen3.8-27b"),
                         "ovhcloud")
        self.assertEqual(len(cc.check_provider_diversity(
            REG, "l3-driver", ["bzl/ds-flash", "bazaarlink/ds-v4-flash-free"])), 1)

    def test_a_provider_may_return_later_in_the_chain(self):
        """Only neighbours are compared — vertex -> bzl -> vertex is a real
        fallback sequence, each domain tried once before any is retried."""
        self.assertEqual(cc.check_provider_diversity(
            REG, "l1-orchestrator", ["vertex/gemini-3.8-flash",
                                     "bzl/ds-flash",
                                     "google_ai_studio/gemini-3.8-flash"]), [])

    def test_two_openrouter_free_legs_back_to_back_are_one_domain(self):
        problems = cc.check_provider_diversity(
            REG, "l3-driver-free-only",
            ["openrouter/north-mini-code:free", "openrouter/laguna-s-2.1:free"])
        self.assertEqual(len(problems), 1, problems)

    def test_an_unknown_namespace_is_its_own_domain(self):
        # Never merged with a provider on a coincidence of spelling.
        self.assertEqual(cc.provider_domain(REG, "whoever/qwen3.8-27b"),
                         "whoever")
        self.assertEqual(cc.provider_domain(REG, "not-a-leg"), "not-a-leg")
        self.assertEqual(cc.check_provider_diversity(
            REG, "l3-driver", ["whoever/qwen3.8-27b", "another/qwen3.8-27b"]), [])


class PriceGateTests(unittest.TestCase):
    """(h) a credit/paid leg must have a price row (§7b delta b)."""

    def test_a_credit_leg_with_no_price_and_no_source_fails(self):
        problems = cc.check_price_gate(REG, "l2-worker", ["ovh/qwen3.8-27b"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no price row", problems[0])
        self.assertIn("l2-worker", problems[0])

    def test_a_provider_scoped_price_row_clears_the_leg_it_prices(self):
        """One model id, two prices: the Vertex row prices the vertex leg and
        nothing else — the free AI-Studio leg is not in scope at all."""
        self.assertEqual(cc.check_price_gate(REG, "l1-orchestrator",
                                             ["vertex/gemini-3.8-flash"]), [])
        self.assertEqual(cc.check_price_gate(REG, "l1-orchestrator",
                                             ["gemini/gemini-3.8-flash"]), [])

    def test_a_price_source_counts_as_a_price_row(self):
        reg = mini_registry()
        reg["models"]["qwen3.8-27b"]["price_source"] = "vendor pricing page"
        self.assertEqual(cc.check_price_gate(reg, "l2-worker",
                                             ["ovh/qwen3.8-27b"]), [])

    def test_a_free_model_row_on_a_paid_provider_is_out_of_scope(self):
        """An OpenRouter `:free` row is not the uncostable grant this gate is
        about — the leg's EFFECTIVE tier reads it, as rule (b) already does."""
        self.assertEqual(cc.check_price_gate(
            REG, "l3-driver-free-only", ["openrouter/north-mini-code:free"]), [])
        # A paid-spelling leg of the same provider is in scope.
        self.assertEqual(len(cc.check_price_gate(REG, "l1-orchestrator",
                                                 ["openrouter/ds-flash"])), 1)

    def test_an_unresolvable_leg_is_rule_cs_business_not_a_crash(self):
        self.assertEqual(cc.check_price_gate(REG, "l2-worker",
                                             ["whoever/nope"]), [])


class LegBanTests(unittest.TestCase):
    """(i) qoder out, paid deepseek/muse out, agy never head."""

    def test_a_qoder_leg_fails_wherever_it_sits(self):
        for index in (0, 1, 2):
            legs = ["vertex/gemini-3.8-flash", "bzl/ds-flash",
                    "bazaarlink/ds-v4-flash-free"]
            legs.insert(index, "qoder/kimi-k3")
            problems = cc.check_leg_bans(REG, "l2-orchestrator", legs)
            self.assertEqual(len(problems), 1, (index, problems))
            self.assertIn("spawner", problems[0])

    def test_paid_deepseek_and_paid_muse_fail_but_their_free_twins_pass(self):
        problems = cc.check_leg_bans(REG, "l1-orchestrator",
                                     ["deepseek/ds-flash",
                                      "meta-api/muse-spark-1.3-contributor"])
        self.assertEqual(len(problems), 2, problems)
        self.assertEqual(cc.check_leg_bans(
            REG, "l1-orchestrator",
            ["bzl/ds-v4-flash-free", "meta_api/muse-spark-1.3-contributor-free"]),
            [])

    def test_an_agy_leg_is_refused_at_position_1_only(self):
        self.assertEqual(len(cc.check_leg_bans(REG, "l2-orchestrator",
                                               ["agy/opus-5-5",
                                                "vertex/gemini-3.8-flash"])), 1)
        self.assertEqual(cc.check_leg_bans(REG, "l2-orchestrator",
                                           ["vertex/gemini-3.8-flash",
                                            "agy/opus-5-5"]), [])

    def test_a_single_leg_agy_combo_is_still_a_head(self):
        problems = cc.check_leg_bans(REG, "opus-5-5", ["agy/sonnet-5-5"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("position 1", problems[0])


class StrictGateTests(unittest.TestCase):
    """(f)-(i) judge the combos a reviewer opted in, so main stays green until
    D2 re-chains them."""

    def test_an_absent_list_judges_nothing(self):
        self.assertFalse(cc.d657_run_for("l1-orchestrator", {}, False))
        self.assertFalse(cc.d657_run_for("l1-orchestrator",
                                         {"d657_combos": []}, False))

    def test_a_listed_combo_is_judged_and_its_neighbour_is_not(self):
        doc = {"d657_combos": ["l2-orchestrator"]}
        self.assertTrue(cc.d657_run_for("l2-orchestrator", doc, False))
        self.assertFalse(cc.d657_run_for("l3-driver", doc, False))

    def test_strict_judges_every_combo_whatever_the_list_says(self):
        self.assertTrue(cc.d657_run_for("opus-5-5", {}, True))
        self.assertTrue(cc.d657_run_for("opus-5-5",
                                        {"d657_combos": []}, True))

    def test_a_non_list_shape_does_not_crash_the_gate(self):
        self.assertFalse(cc.d657_run_for("l1-orchestrator",
                                         {"d657_combos": "l1-orchestrator"},
                                         False))

    def test_the_committed_combos_json_has_not_opted_in_yet(self):
        """D1 changes no combo chain: until D2 lists an id, the shipped gate
        stays rc 0 on the shipped file."""
        doc = json.loads(COMBOS_PATH.read_text(encoding="utf-8"))
        judged = [c["name"] for c in doc.get("combos", [])
                  if cc.d657_run_for(c["name"], doc, False)]
        self.assertEqual(judged, [])

    def test_the_script_passes_by_default_and_reports_under_strict(self):
        default = subprocess.run([sys.executable, str(ROOT / "tools" /
                                                     "combo-contract.py")],
                                 cwd=str(ROOT), capture_output=True, text=True,
                                 timeout=180)
        self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
        self.assertIn("judged 0 of", default.stdout)
        strict = subprocess.run([sys.executable, str(ROOT / "tools" /
                                                     "combo-contract.py"),
                                 "--strict-d657"],
                                cwd=str(ROOT), capture_output=True, text=True,
                                timeout=180)
        self.assertEqual(strict.returncode, 1, strict.stdout[-2000:])
        # --strict-d657 judges every combo, whatever the file's count is today.
        match = re.search(r"judged (\d+) of (\d+) combos", strict.stdout)
        self.assertIsNotNone(match, strict.stdout[-500:])
        self.assertGreater(int(match.group(1)), 0)
        self.assertEqual(match.group(1), match.group(2))
        for tag in (": (f)", ": (g)", ": (h)", ": (i)"):
            self.assertIn(tag, strict.stdout, tag)


if __name__ == "__main__":
    unittest.main()
