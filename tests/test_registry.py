#!/usr/bin/env python3
"""Unit tests for tools/registry.py (routing v2 spec section 3.1, task A3).

Path-independent: everything is anchored on ROOT = Path(__file__).resolve().parent.parent,
never on the current working directory or a hard-coded home path (this repo is public,
AGENTS.md rule 1).

Stdlib-only, no jsonschema dependency (matching every other suite in this repo): the
"required keys" check the tool performs is a small hand-rolled structural walk of
catalog/ai-registry.schema.json's own `required` arrays, not a real validator.

Run directly (`python3 tests/test_registry.py`), never via `unittest discover` - the
suite has to be runnable on a machine where nothing is installed (AGENTS.md section 5).
"""
from __future__ import annotations

import copy
import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
REGISTRY_TOOL = ROOT / "tools" / "registry.py"
GOLDEN_LEGACY_MODELS_PATH = ROOT / "tests" / "fixtures" / "legacy-models.golden.json"


def _load_tool():
    """Import tools/registry.py by path (its name is not a valid module identifier)."""
    spec = importlib.util.spec_from_file_location("autoos_registry", REGISTRY_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load_tool()


def load_registry() -> dict:
    with REGISTRY_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def mutated() -> dict:
    return copy.deepcopy(load_registry())


# The allowed keys of a providers.<id>.limits.<model> entry, matching
# catalog/ai-registry.schema.json $defs.provider_limits.
ALLOWED_LIMIT_KEYS = {"rpm", "rpd", "tpm", "tpd", "plan_available", "source"}


def live_legs(reg: dict, route_id: str) -> list:
    """The legs of `route_id` the gateway could actually answer: not flagged
    unavailable, not client-bound, not denied by policy.leg_rules (those are all
    `gateway_legs`'s job) and not dead on the plan (0 rpm / plan_available
    false). One helper for the route-liveness invariant and for every test that
    wants to say "this route has N live legs", so the two can never drift."""
    route = reg["routes"][route_id]
    return [leg for leg in registry.gateway_legs(route, reg)
            if not registry.plan_dead_reasons(*registry.resolve_leg(leg, reg),
                                              registry=reg)]


class RealRegistryTests(unittest.TestCase):
    """The committed registry passes the real checker (exit 0 through the CLI)."""

    def test_check_registry_reports_no_problems(self):
        self.assertEqual(registry.check_registry(load_registry()), [])

    def test_cli_check_exits_zero_and_prints_ok(self):
        proc = subprocess.run(
            [sys.executable, str(REGISTRY_TOOL), "check"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # PRIV2: any privacy_exemption_lines() "info:" line(s) print first
        # (t1-orchestrator-clean today), then the ok line, always last.
        self.assertRegex(
            proc.stdout,
            r"^(info: .*\n)*ok: registry \d{4}-\d{2}-\d{2}, \d+ routes, \d+ models, \d+ providers\n$")

    def test_cli_validate_succeeds(self):
        proc = subprocess.run(
            [sys.executable, str(REGISTRY_TOOL), "validate"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok: registry", proc.stdout)


class ResolveLegTests(unittest.TestCase):
    """resolve_leg splits at the FIRST '/', accepting a provider key or omniroute_id."""

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_provider_key_prefix(self):
        self.assertEqual(registry.resolve_leg("mistral/mistral-small-latest", self.reg),
                         ("mistral", "mistral-small-latest"))

    def test_omniroute_id_prefix_resolves_to_the_provider_key(self):
        self.assertEqual(registry.resolve_leg("opencode-zen/deepseek-v4.1-flash", self.reg),
                         ("zen", "deepseek-v4.1-flash"))
        self.assertEqual(registry.resolve_leg("gemini/gemini-3.8-flash", self.reg),
                         ("google_ai_studio", "gemini-3.8-flash"))

    def test_only_the_first_slash_splits(self):
        self.assertEqual(
            registry.resolve_leg("openrouter/meta/muse-spark-1.3-contributor", self.reg),
            ("openrouter", "meta/muse-spark-1.3-contributor"))

    def test_unknown_provider_raises_value_error_naming_the_leg(self):
        with self.assertRaises(ValueError) as ctx:
            registry.resolve_leg("ghost-provider/gpt", self.reg)
        self.assertIn("ghost-provider/gpt", str(ctx.exception))

    def test_unknown_model_raises_value_error_naming_the_leg(self):
        with self.assertRaises(ValueError) as ctx:
            registry.resolve_leg("mistral/not-a-model", self.reg)
        self.assertIn("mistral/not-a-model", str(ctx.exception))


class GatewayRefTests(unittest.TestCase):
    """AGYID: gateway_ref() is the one translation point between a registry leg
    and the id the OmniRoute catalog actually serves. A provider that declares
    model_prefix (antigravity -> agy) is rewritten; every other leg is returned
    unchanged, and the registry's own spelling never moves."""

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_model_prefix_is_applied(self):
        self.assertEqual(
            registry.gateway_ref("antigravity/claude-opus-4-6-thinking", self.reg),
            "agy/claude-opus-4-6-thinking")

    def test_provider_without_model_prefix_is_unchanged(self):
        self.assertEqual(
            registry.gateway_ref("mistral/mistral-small-latest", self.reg),
            "mistral/mistral-small-latest")

    def test_unresolvable_leg_is_unchanged(self):
        self.assertEqual(registry.gateway_ref("ghost/x", self.reg), "ghost/x")

    def test_non_string_leg_is_unchanged(self):
        self.assertIsNone(registry.gateway_ref(None, self.reg))

    def test_registry_leg_spelling_is_untouched(self):
        # The provider still answers to its own id; only the gateway ref moves.
        self.assertEqual(
            registry.resolve_leg("antigravity/claude-opus-4-6-thinking", self.reg),
            ("antigravity", "claude-opus-4-6-thinking"))

    def test_provider_omni_id_is_still_antigravity(self):
        # apply.sh finds/registers the provider connection by omniroute_id
        # (apply.sh ~90-144) - the model_prefix rename must not touch it.
        self.assertEqual(self.reg["providers"]["antigravity"]["omniroute_id"],
                         "antigravity")


class RuleOneLegResolutionTests(unittest.TestCase):
    """Rule 1: every route leg resolves to a providers x models pair."""

    def test_unknown_leg_is_flagged(self):
        reg = mutated()
        reg["routes"]["t3-driver-clean"]["legs"].append("ghost-provider/ghost-model")
        problems = registry.check_registry(reg)
        self.assertTrue(any("t3-driver-clean" in p and "ghost-provider/ghost-model" in p
                            for p in problems), problems)


class RuleTwoIdUniquenessTests(unittest.TestCase):
    """Rule 2: ids unique within and across providers/models/clients (routes exempt)."""

    def test_duplicate_id_across_providers_and_models_is_flagged(self):
        reg = mutated()
        reg["models"]["muse-spark"]["id"] = "zen"  # 'zen' is also a provider id
        problems = registry.check_registry(reg)
        self.assertTrue(any("duplicate" in p and "zen" in p for p in problems), problems)

    def test_duplicate_id_within_a_section_is_flagged(self):
        reg = mutated()
        reg["providers"]["groq"]["id"] = "zen"
        problems = registry.check_registry(reg)
        self.assertTrue(any("duplicate" in p and "zen" in p for p in problems), problems)

    def test_route_sharing_a_model_id_is_not_flagged(self):
        # Open choice 11: routes.gemini-3.8-flash shares its id with
        # models.gemini-3.8-flash by convention and must not be reported.
        problems = registry.check_registry(load_registry())
        self.assertFalse(any("duplicate" in p and "gemini" in p for p in problems), problems)


class RuleThreePrivacyTests(unittest.TestCase):
    """Rule 3: -clean routes only use available legs whose provider does not train.

    MISTRALFIX (2026-09-28) re-derived the leg these tests dirty: the route's
    mistral leg is gone (0 rpm on the plan), so they now flip the provider of
    the leg that actually heads `t2-worker-clean` on this base —
    deepseek/deepseek-flash — which is the head the privacy rule exists to keep
    clean.
    """

    def setUp(self):
        reg = load_registry()
        self.leg = reg["routes"]["t2-worker-clean"]["legs"][0]
        self.provider, _, self.model = self.leg.partition("/")

    def test_clean_route_with_a_training_leg_is_flagged(self):
        reg = mutated()
        reg["providers"][self.provider]["trains_on_prompts"] = True
        problems = registry.check_registry(reg)
        self.assertIn("privacy: t2-worker-clean leg %s trains on prompts"
                      % self.leg, problems)

    def test_allow_training_does_not_exempt_the_route(self):
        # review-a3: spec 3.1 has no allow_training escape; a clean route that
        # carries one is still checked (fail closed).
        reg = mutated()
        reg["providers"][self.provider]["trains_on_prompts"] = True
        reg["routes"]["t2-worker-clean"]["allow_training"] = True
        problems = registry.check_registry(reg)
        self.assertTrue(any("privacy: t2-worker-clean" in p for p in problems), problems)

    def test_unavailable_leg_of_a_clean_route_is_still_checked(self):
        # PRIV2, 2026-09-26 (supersedes the old review-a3 "not checked"
        # behaviour): the gateway does not consult unavailable_legs/
        # available:false, so rule 3 must not either. Use t2-worker-clean
        # (not exempt, unlike t1-orchestrator-clean) and its
        # openrouter leg, flagged here (OR2 2026-09-27 un-gated it in the real
        # data - the BYOK allowlist - so the test sets its own precondition).
        reg = mutated()
        reg["providers"]["openrouter"]["trains_on_prompts"] = True
        reg["routes"]["t2-worker-clean"].setdefault("unavailable_legs", {})[
            "openrouter/deepseek/deepseek-v4.1-flash"] = {"available": False}
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p
                and "openrouter/deepseek/deepseek-v4.1-flash" in p
                for p in problems), problems)

    def test_unknown_trains_on_prompts_is_flagged(self):
        # review-a3: a null/missing trains_on_prompts is unverified, not clean.
        reg = mutated()
        reg["providers"][self.provider]["trains_on_prompts"] = None
        problems = registry.check_registry(reg)
        self.assertTrue(any("privacy: t2-worker-clean" in p for p in problems), problems)

    def test_clean_route_with_a_free_tier_leg_is_flagged(self):
        # PRIV brief 2026-09-26: a free-tier provider is never private-safe,
        # even one whose own trains_on_prompts is false (the found bug: a
        # free pool was treated as "clean" because only trains_on_prompts
        # was checked, never tier).
        reg = mutated()
        reg["providers"][self.provider]["tier"] = "free"
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p and self.leg in p
                for p in problems), problems)

    def test_clean_route_with_a_training_model_override_is_flagged(self):
        # A model-level trains_on_prompts: true overrides an otherwise-clean
        # paid provider (mistral-code-latest's real-world case).
        reg = mutated()
        reg["models"][self.model]["trains_on_prompts"] = True
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p and self.leg in p
                for p in problems), problems)


class PrivateSafeTests(unittest.TestCase):
    """private_safe(): the single predicate rule 3 and the resolver's privacy
    filter both use (PRIV brief, 2026-09-26 operator finding "Free first,
    private never"). A leg is private-safe only when its provider tier is
    not "free", the provider's trains_on_prompts is exactly False, and the
    model does not carry its own trains_on_prompts: true (missing means
    inherit the provider)."""

    def reg(self, provider_extra=None, model_extra=None):
        provider = {"id": "p", "tier": "paid", "trains_on_prompts": False}
        provider.update(provider_extra or {})
        model = {"id": "m"}
        model.update(model_extra or {})
        return {"providers": {"p": provider}, "models": {"m": model}}

    def test_free_provider_is_not_safe(self):
        safe, reason = registry.private_safe("p", "m", self.reg(provider_extra={"tier": "free"}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_training_provider_is_not_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(provider_extra={"trains_on_prompts": True}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_training_model_on_a_clean_provider_is_not_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(model_extra={"trains_on_prompts": True}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_clean_paid_leg_is_safe(self):
        safe, reason = registry.private_safe("p", "m", self.reg())
        self.assertTrue(safe)
        self.assertIsNone(reason)

    def test_missing_provider_trains_on_prompts_is_not_safe(self):
        reg = self.reg()
        del reg["providers"]["p"]["trains_on_prompts"]
        safe, reason = registry.private_safe("p", "m", reg)
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_unknown_provider_is_not_safe(self):
        safe, reason = registry.private_safe("ghost", "m", self.reg())
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_unknown_model_is_not_safe(self):
        safe, reason = registry.private_safe("p", "ghost", self.reg())
        self.assertFalse(safe)
        self.assertTrue(reason)


class RuleFourPrivateHostTests(unittest.TestCase):
    """Rule 4: api_base / direct.base_url hold only public vendor endpoints."""

    def test_private_ip_provider_api_base_is_flagged(self):
        reg = mutated()
        reg["providers"]["zen"]["api_base"] = "http://10.0.0.5:8080/v1"
        problems = registry.check_registry(reg)
        self.assertIn("private host: providers.zen.api_base http://10.0.0.5:8080/v1",
                      problems)

    def test_loopback_in_direct_base_url_is_allowed(self):
        problems = registry.check_registry(load_registry())
        self.assertFalse(any("ollama-qwen2.5-coder" in p for p in problems), problems)

    def test_loopback_in_provider_api_base_is_flagged(self):
        reg = mutated()
        reg["providers"]["cheapinference"]["api_base"] = "http://127.0.0.1:8000/v1"
        problems = registry.check_registry(reg)
        self.assertIn(
            "private host: providers.cheapinference.api_base http://127.0.0.1:8000/v1",
            problems)

    def test_loopback_alias_localhost_in_provider_api_base_is_flagged(self):
        reg = mutated()
        reg["providers"]["zen"]["api_base"] = "http://localhost:8000/v1"
        problems = registry.check_registry(reg)
        self.assertTrue(any("private host: providers.zen.api_base" in p for p in problems),
                        problems)

    def test_userinfo_is_flagged_even_over_https(self):
        reg = mutated()
        reg["providers"]["zen"]["api_base"] = "https://placeholder@api.example.com/v1"
        problems = registry.check_registry(reg)
        self.assertIn(
            "private host: providers.zen.api_base https://placeholder@api.example.com/v1",
            problems)

    def test_private_suffix_host_in_direct_base_url_is_flagged(self):
        reg = mutated()
        reg["models"]["muse-spark"]["direct"]["base_url"] = "https://gpu-box.lan/v1"
        problems = registry.check_registry(reg)
        self.assertIn("private host: models.muse-spark.direct.base_url https://gpu-box.lan/v1",
                      problems)

    def test_plain_http_public_host_is_flagged(self):
        reg = mutated()
        reg["models"]["muse-spark"]["direct"]["base_url"] = "http://api.meta.ai/v1"
        problems = registry.check_registry(reg)
        self.assertIn("private host: models.muse-spark.direct.base_url http://api.meta.ai/v1",
                      problems)


class RuleFiveDatedValueTests(unittest.TestCase):
    """Rule 5: no date-like value outside source/verified/version or a comment."""

    def test_dated_display_name_is_flagged(self):
        reg = mutated()
        reg["models"]["muse-spark"]["display_name"] = "2026-01-01"
        problems = registry.check_registry(reg)
        self.assertIn("dated value: models.muse-spark.display_name", problems)

    def test_dated_value_inside_a_comment_is_allowed(self):
        reg = mutated()
        reg["models"]["muse-spark"]["$comment"] = "measured 2026-01-01"
        problems = registry.check_registry(reg)
        self.assertFalse(any("dated value" in p and "muse-spark" in p for p in problems),
                         problems)

    def test_source_and_verified_keys_may_hold_dates(self):
        # policy.handoff_caps.*.source and provider windows[].verified carry dates.
        problems = registry.check_registry(load_registry())
        self.assertEqual([p for p in problems if p.startswith("dated value")], [])


class RuleSixRequiredKeyTests(unittest.TestCase):
    """Rule 6: every schema-required key is present (hand-rolled walk)."""

    def test_missing_provider_key_is_flagged(self):
        reg = mutated()
        del reg["providers"]["zen"]["trains_on_prompts"]
        problems = registry.check_registry(reg)
        self.assertTrue(any("missing" in p and "providers.zen" in p for p in problems),
                        problems)

    def test_missing_model_key_is_flagged(self):
        reg = mutated()
        del reg["models"]["muse-spark"]["family"]
        problems = registry.check_registry(reg)
        self.assertTrue(any("missing" in p and "models.muse-spark" in p for p in problems),
                        problems)

    def test_missing_policy_key_is_flagged(self):
        reg = mutated()
        del reg["policy"]["latency_seed"]
        problems = registry.check_registry(reg)
        self.assertTrue(any("missing" in p and "policy" in p for p in problems), problems)


class CliTests(unittest.TestCase):
    """`check` and `validate` command-line behaviour (exit codes, messages)."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REGISTRY_TOOL), *args],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        )

    def _write(self, reg):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="registry-test-", delete=False, encoding="utf-8")
        json.dump(reg, tmp)
        tmp.close()
        return tmp.name

    def test_check_on_a_mutated_file_exits_one_and_prints_each_problem(self):
        reg = mutated()
        reg["providers"]["zen"]["api_base"] = "http://10.0.0.5:8080/v1"
        path = self._write(reg)
        try:
            proc = self._run("check", "--registry", path)
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("private host: providers.zen.api_base", proc.stdout)

class ModelLevelTierOverrideTests(unittest.TestCase):
    """PRIV2 brief 2026-09-26: an optional models.<id>.tier overrides its
    provider's tier in private_safe() -- needed for the zen provider (tier
    "free") whose paid, direct-key opencode-zen/deepseek-v4.1-flash leg
    (tests/run-tests.sh's own combos.json policy line: "Direct-key legs
    (mistral-small, deepseek, openrouter paid, zen paid) bill past the pool
    on the same key, so they stay") is not itself a free pool."""

    def reg(self, provider_extra=None, model_extra=None):
        provider = {"id": "p", "tier": "paid", "trains_on_prompts": False}
        provider.update(provider_extra or {})
        model = {"id": "m"}
        model.update(model_extra or {})
        return {"providers": {"p": provider}, "models": {"m": model}}

    def test_model_tier_override_makes_a_free_provider_leg_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(provider_extra={"tier": "free"}, model_extra={"tier": "paid"}))
        self.assertTrue(safe, reason)
        self.assertIsNone(reason)

    def test_model_tier_override_can_make_a_paid_provider_leg_unsafe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(model_extra={"tier": "free"}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_zen_deepseek_v4_1_flash_model_carries_a_paid_tier_override(self):
        model = load_registry()["models"]["deepseek-v4.1-flash"]
        self.assertEqual(model.get("tier"), "paid")
        self.assertIn("$comment", model)

    def test_zen_provider_itself_stays_free(self):
        self.assertEqual(load_registry()["providers"]["zen"]["tier"], "free")

    def test_zen_deepseek_leg_is_private_safe_via_the_model_override(self):
        safe, reason = registry.private_safe("zen", "deepseek-v4.1-flash", load_registry())
        self.assertTrue(safe, reason)


class ModelLevelTrainingContributorTests(unittest.TestCase):
    """PRIV2 brief 2026-09-26: the contributor model trains by contract
    (tests/run-tests.sh's own combos.json policy line: "-contributor
    (trains by contract) is banned in t2-worker-clean/t3-driver-clean") --
    both the OpenRouter paid contributor leg and the Zen free
    contributor-promo leg carry the model-level override, not just a
    provider-level one."""

    def test_openrouter_contributor_model_trains(self):
        model = load_registry()["models"]["meta/muse-spark-1.3-contributor"]
        self.assertIs(model.get("trains_on_prompts"), True)
        self.assertIn("$comment", model)

    def test_zen_free_contributor_model_trains(self):
        model = load_registry()["models"]["muse-spark-1.3-contributor-free"]
        self.assertIs(model.get("trains_on_prompts"), True)
        self.assertIn("$comment", model)

    def test_openrouter_contributor_leg_is_not_private_safe(self):
        safe, reason = registry.private_safe(
            "openrouter", "meta/muse-spark-1.3-contributor", load_registry())
        self.assertFalse(safe)
        self.assertTrue(reason)


class FailClosedPrivateSafeTests(unittest.TestCase):
    """PRIV2 brief 2026-09-26: private_safe() fails closed on anything that
    is not exactly the safe shape -- an unrecognised/missing effective tier,
    or a model-level trains_on_prompts that is present but not exactly
    `false` (null, 1, "no", 0, ...), is never treated as safe."""

    def reg(self, provider_extra=None, model_extra=None):
        provider = {"id": "p", "tier": "paid", "trains_on_prompts": False}
        provider.update(provider_extra or {})
        model = {"id": "m"}
        model.update(model_extra or {})
        return {"providers": {"p": provider}, "models": {"m": model}}

    def test_effective_tier_missing_is_not_safe(self):
        reg = self.reg()
        del reg["providers"]["p"]["tier"]
        safe, reason = registry.private_safe("p", "m", reg)
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_effective_tier_unrecognised_value_is_not_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(provider_extra={"tier": "beta"}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_model_trains_on_prompts_explicit_null_is_not_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(model_extra={"trains_on_prompts": None}))
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_model_trains_on_prompts_truthy_non_bool_is_not_safe(self):
        for value in (1, "no", "false", 0):
            with self.subTest(value=value):
                safe, reason = registry.private_safe(
                    "p", "m", self.reg(model_extra={"trains_on_prompts": value}))
                self.assertFalse(safe, "value %r wrongly treated as safe" % (value,))

    def test_model_trains_on_prompts_missing_key_is_still_safe(self):
        safe, reason = registry.private_safe("p", "m", self.reg())
        self.assertTrue(safe, reason)

    def test_model_trains_on_prompts_exactly_false_is_still_safe(self):
        safe, reason = registry.private_safe(
            "p", "m", self.reg(model_extra={"trains_on_prompts": False}))
        self.assertTrue(safe, reason)


class CleanRouteExemptionTests(unittest.TestCase):
    """PRIV2 brief 2026-09-26: rule 3 checks every leg of a -clean route,
    including one flagged in unavailable_legs -- the OmniRoute gateway does
    not consult that registry-only flag, and combos.json/apply.sh still
    push the leg verbatim (tests/run-tests.sh's own "apply --dry-run" /
    "provider registry drives apply" tests). The one documented exception is
    registry.CLEAN_ROUTE_EXEMPTIONS -- t1-orchestrator-clean, which
    deliberately carries the training OpenRouter contributor leg (the
    spawner requires --allow-training to reach it) -- reported by
    `check`/`validate` as an info line, never silently skipped and never a
    check_registry() problem."""

    def test_unavailable_leg_of_a_non_exempt_clean_route_is_now_checked(self):
        reg = mutated()
        reg["providers"]["openrouter"]["trains_on_prompts"] = True
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p
                and "openrouter/deepseek/deepseek-v4.1-flash" in p
                for p in problems), problems)

    def test_t1_orchestrator_clean_is_in_the_exemption_table(self):
        self.assertIn("t1-orchestrator-clean", registry.CLEAN_ROUTE_EXEMPTIONS)

    def test_t1_orchestrator_clean_training_leg_is_not_a_check_registry_problem(self):
        problems = registry.check_registry(load_registry())
        self.assertFalse(any("t1-orchestrator-clean" in p for p in problems), problems)

    def test_privacy_exemption_lines_names_the_route_and_a_reason(self):
        lines = registry.privacy_exemption_lines(load_registry())
        self.assertTrue(
            any("t1-orchestrator-clean" in line and line.startswith("info:")
                for line in lines), lines)

    def test_check_cli_prints_the_exemption_info_line_and_still_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, str(REGISTRY_TOOL), "check"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("t1-orchestrator-clean", proc.stdout)
        self.assertIn("info:", proc.stdout)
        self.assertIn("ok: registry", proc.stdout)


class ReviewA3Tests(unittest.TestCase):
    """Findings of the cross-family review of A3 (agy, 2026-09-26)."""

    def test_route_id_colliding_with_a_provider_is_flagged(self):
        reg = mutated()
        route = copy.deepcopy(reg["routes"]["t2-worker-clean"])
        route["id"] = "mistral"
        reg["routes"]["mistral"] = route
        self.assertTrue(any(p.startswith("duplicate id: mistral")
                            for p in registry.check_registry(reg)))

    def test_route_named_after_a_model_it_serves_is_allowed(self):
        self.assertIn("deepseek-v4.1-flash", load_registry()["routes"])
        self.assertIn("deepseek-v4.1-flash", load_registry()["models"])
        self.assertEqual(registry.check_registry(load_registry()), [])

    def test_route_named_after_a_model_it_does_not_serve_is_flagged(self):
        reg = mutated()
        route = copy.deepcopy(reg["routes"]["t2-worker-clean"])
        route["id"] = "claude-opus-4-6"
        reg["routes"]["claude-opus-4-6"] = route
        self.assertTrue(any(p.startswith("duplicate id: claude-opus-4-6")
                            for p in registry.check_registry(reg)))

    def test_unresolved_unavailable_leg_is_flagged(self):
        reg = mutated()
        reg["routes"]["t1-orchestrator-clean"]["unavailable_legs"]["nope/nothing"] = {
            "available": False}
        self.assertIn("unresolved leg: routes.t1-orchestrator-clean leg nope/nothing",
                      registry.check_registry(reg))

    def test_single_label_host_is_private(self):
        reg = mutated()
        reg["providers"]["zen"]["api_base"] = "https://gpu-box/v1"
        self.assertIn("private host: providers.zen.api_base https://gpu-box/v1",
                      registry.check_registry(reg))

    def test_ip_before_query_or_fragment_is_private(self):
        for url in ("https://10.0.0.1?v1", "https://10.0.0.1#x"):
            reg = mutated()
            reg["providers"]["zen"]["api_base"] = url
            self.assertIn("private host: providers.zen.api_base %s" % url,
                          registry.check_registry(reg))

    def test_dated_key_is_flagged(self):
        reg = mutated()
        reg["policy"]["latency_seed"]["2026-09-25"] = {"minutes": 1, "source": "default"}
        self.assertTrue(any("dated key: policy.latency_seed.2026-09-25" in p
                            for p in registry.check_registry(reg)))

    def test_null_sections_report_instead_of_crashing(self):
        for mutate in (lambda r: r["routes"]["t2-worker"].__setitem__("legs", None),
                       lambda r: r.__setitem__("providers", None),
                       lambda r: r.__setitem__("models", None)):
            reg = mutated()
            mutate(reg)
            self.assertIsInstance(registry.check_registry(reg), list)


class LegacyModelsTests(unittest.TestCase):
    """tools/registry.py legacy_models(): the one projection of registry
    models into the legacy llm-models entry shape (A5dfix) --
    every installer read (lib/linux/install.sh x2, AutoOS.Install.psm1
    embedded python) calls this instead of carrying its own copy. The
    committed golden fixture tests/fixtures/legacy-models.golden.json pins
    that shape: it is the deleted legacy models catalog's own "models"
    list, copied verbatim in task A5e."""

    def test_legacy_models_match_the_committed_golden_fixture_field_for_field(self):
        with GOLDEN_LEGACY_MODELS_PATH.open(encoding="utf-8") as fh:
            old = {m["id"]: m for m in json.load(fh)["models"]}
        new = {m["id"]: m for m in registry.legacy_models(load_registry())}
        self.assertEqual(set(new), set(old))
        for mid, old_entry in sorted(old.items()):
            self.assertEqual(new[mid], old_entry, mid)

    def test_models_without_a_direct_block_are_excluded(self):
        doc = {"models": {
            "with-direct": {"id": "with-direct", "display_name": "D",
                            "direct": {"provider": "meta", "model": "m"},
                            "context_advertised": 1, "output_max": 1,
                            "price_in": 0, "price_out": 0},
            "no-direct": {"id": "no-direct", "display_name": "N",
                          "context_advertised": 1, "output_max": 1,
                          "price_in": 0, "price_out": 0},
        }}
        self.assertEqual([m["id"] for m in registry.legacy_models(doc)],
                         ["with-direct"])

    def test_openrouter_free_stays_first_in_the_generated_openrouter_map(self):
        by_id = {m["id"]: m for m in registry.legacy_models(load_registry())}
        openrouter_map = {m["openrouter_id"]: m for m in by_id.values()
                          if m.get("openrouter_id")}
        self.assertTrue(openrouter_map)
        self.assertEqual(next(iter(openrouter_map.values()))["id"],
                         "openrouter-free")

    def test_default_for_is_projected(self):
        by_id = {m["id"]: m for m in registry.legacy_models(load_registry())}
        self.assertEqual(by_id["muse-spark"].get("default_for"), "muse_key")
        self.assertEqual(by_id["ollama-qwen2.5-coder"].get("default_for"),
                         "fallback")


class UnavailableUntilTests(unittest.TestCase):
    """Time-bounded unavailability (brief UNTIL, 2026-09-26; skill rule
    R-gateway-12: a 429 with retryable:true but a multi-day reset is not
    soon-retryable -- mark the entry unavailable till the reset, then let it
    come back on its own instead of relying on someone hand-undoing an
    ``available: false``).

    One helper, registry.unavailable_now(entry, now), answers "is this
    clients/providers/unavailable_legs entry unavailable at `now`":

    - ``available: false`` with no ``unavailable_until`` is unavailable
      forever (today's behaviour, unchanged);
    - a future ``unavailable_until`` is unavailable until that instant;
    - a past ``unavailable_until`` counts as available again -- the entry
      self-heals, no hand edit to undo.
    """

    def dt(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_available_false_without_an_until_stays_unavailable_forever(self):
        entry = {"available": False}
        self.assertTrue(registry.unavailable_now(entry, self.dt(2026, 9, 26)))
        self.assertTrue(registry.unavailable_now(entry, self.dt(2030, 1, 1)))

    def test_a_future_until_is_unavailable(self):
        entry = {"unavailable_until": "2026-10-01T09:05:00Z"}
        self.assertTrue(registry.unavailable_now(entry, self.dt(2026, 9, 26, 19, 17)))
        self.assertTrue(registry.unavailable_now(
            entry, self.dt(2026, 10, 1, 9, 4, 59)))

    def test_a_past_until_counts_as_available_again(self):
        entry = {"available": False, "unavailable_until": "2026-10-01T09:05:00Z"}
        self.assertTrue(registry.unavailable_now(
            entry, self.dt(2026, 10, 1, 9, 4, 59)))
        self.assertFalse(registry.unavailable_now(
            entry, self.dt(2026, 10, 1, 9, 5, 0)))
        self.assertFalse(registry.unavailable_now(
            entry, self.dt(2026, 12, 31)))

    def test_no_flags_at_all_is_available(self):
        self.assertFalse(registry.unavailable_now({}, self.dt(2026, 9, 26)))
        self.assertFalse(registry.unavailable_now(None, self.dt(2026, 9, 26)))

    def test_an_unparsable_until_fails_check_not_the_helper(self):
        # The helper itself never raises (a resolver runs it against live
        # state); the shape is a check-time problem, reported by rule 7.
        entry = {"unavailable_until": "next tuesday"}
        self.assertIs(registry.unavailable_now(entry, self.dt(2026, 9, 26)),
                      False)


class RuleSevenUnavailableUntilTests(unittest.TestCase):
    """Rule 7: every ``unavailable_until`` value anywhere in the registry is
    an ISO-8601 UTC timestamp. The key itself holds a date by design, so
    rule 5 (no dated values) must exempt it; a value that does not parse is
    a check failure, never a silent pass (an unparsable until would quietly
    read as available forever -- the exact hand-edit-forgot-to-undo failure
    this field exists to remove)."""

    def test_an_unparsable_until_on_a_client_is_flagged(self):
        reg = mutated()
        reg["clients"]["agy"]["unavailable_until"] = "next tuesday"
        problems = registry.check_registry(reg)
        self.assertIn("bad unavailable_until: clients.agy 'next tuesday'",
                      problems)

    def test_an_unparsable_until_on_a_provider_is_flagged(self):
        reg = mutated()
        reg["providers"]["openrouter"]["unavailable_until"] = "soon"
        problems = registry.check_registry(reg)
        self.assertIn("bad unavailable_until: providers.openrouter 'soon'",
                      problems)

    def test_an_unparsable_until_on_an_unavailable_leg_is_flagged(self):
        reg = mutated()
        reg["routes"]["t2-worker-clean"]["unavailable_legs"][
            "opencode-zen/deepseek-v4.1-flash"]["unavailable_until"] = "2026-13-01"
        problems = registry.check_registry(reg)
        self.assertTrue(any(p.startswith(
            "bad unavailable_until: routes.t2-worker-clean.unavailable_legs."
            "opencode-zen/deepseek-v4.1-flash") for p in problems), problems)

    def test_a_non_string_until_is_flagged(self):
        reg = mutated()
        reg["clients"]["agy"]["unavailable_until"] = 1759258200
        problems = registry.check_registry(reg)
        self.assertTrue(any(p.startswith("bad unavailable_until: clients.agy")
                            for p in problems), problems)

    def test_a_parsable_until_is_not_a_dated_value(self):
        # Rule 5 exempts the key by name: "2026-10-01T09:05:00Z" matches
        # DATE_RE, so only an explicit exemption keeps this quiet.
        reg = mutated()
        reg["clients"]["agy"]["unavailable_until"] = "2026-10-01T09:05:00Z"
        problems = registry.check_registry(reg)
        self.assertFalse(any("dated" in p and "agy" in p for p in problems),
                         problems)
        self.assertFalse(any(p.startswith("bad unavailable_until")
                             for p in problems), problems)

    def test_an_explicit_zero_offset_is_rejected_too(self):
        # UNTILfix: the schema's until_tag pattern is Z-only, so an explicit
        # +00:00 must fail check even though it names the same instant. This
        # was the one offset rule 7 used to let through.
        reg = mutated()
        reg["clients"]["agy"]["unavailable_until"] = (
            "2026-10-01T09:05:00+00:00")
        problems = registry.check_registry(reg)
        self.assertTrue(any(p.startswith("bad unavailable_until: clients.agy")
                            for p in problems), problems)

    def test_offsets_and_naive_timestamps_are_rejected(self):
        # UTC only ("...Z"): an offset or a naive timestamp is ambiguous
        # about which clock it means on a machine in another zone.
        for value in ("2026-10-01T11:05:00+02:00", "2026-10-01T09:05:00"):
            reg = mutated()
            reg["clients"]["agy"]["unavailable_until"] = value
            problems = registry.check_registry(reg)
            self.assertTrue(any(p.startswith("bad unavailable_until: clients.agy")
                                for p in problems), (value, problems))

    def test_the_committed_schema_permits_the_field_on_all_three_surfaces(self):
        # The schema carries additionalProperties: false on client, provider
        # and unavailable_legs entries; it must name unavailable_until on all
        # three, and the committed registry itself passes check with one set.
        schema = json.loads((ROOT / "catalog" / "ai-registry.schema.json")
                            .read_text(encoding="utf-8"))
        defs = schema["$defs"]
        for surface in (defs["client"]["properties"],
                        defs["provider"]["properties"],
                        defs["route"]["properties"]["unavailable_legs"]
                        ["additionalProperties"]["properties"]):
            self.assertIn("unavailable_until", surface)
        self.assertEqual(registry.check_registry(load_registry()), [])


class RuleEightUnavailableUntilPairsAvailableTests(unittest.TestCase):
    """Rule 8: an entry with ``unavailable_until`` must also carry ``available:
    false``. Without the flag the resolver's re-probe note cannot fire after
    the until expires (FUP 2026-09-27, measured on ``clients.agy``)."""

    def test_committed_registry_passes_rule_8(self):
        # agy now has both available: false and unavailable_until.
        self.assertFalse(
            any("entry with unavailable_until but no available: false"
                in p for p in registry.check_registry(load_registry())),
            "committed registry must not violate rule 8")

    def test_missing_available_on_client_is_flagged(self):
        reg = mutated()
        del reg["clients"]["agy"]["available"]
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("entry with unavailable_until but no available: false: clients.agy"
                in p for p in problems),
            problems)

    def test_missing_available_on_a_provider_is_flagged(self):
        reg = mutated()
        # DSMAX 2026-09-27 switched openrouter off at the provider level, so
        # drop that flag before asserting rule 8 fires on the missing one.
        del reg["providers"]["openrouter"]["available"]
        reg["providers"]["openrouter"]["unavailable_until"] = "2026-10-01T09:05:00Z"
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("entry with unavailable_until but no available: false: providers.openrouter"
                in p for p in problems),
            problems)

    def test_missing_available_on_unavailable_leg_is_flagged(self):
        reg = mutated()
        leg = reg["routes"]["t2-worker-clean"]["unavailable_legs"][
            "opencode-zen/deepseek-v4.1-flash"]
        del leg["available"]
        leg["unavailable_until"] = "2026-10-01T09:05:00Z"
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("entry with unavailable_until but no available: false: "
                "routes.t2-worker-clean.unavailable_legs.opencode-zen/deepseek-v4.1-flash"
                in p for p in problems),
            problems)

    def test_available_true_with_unavailable_until_is_flagged_too(self):
        # Explicit available: true is still not available: false, and the
        # resolver needs the flag to emit the re-probe note.
        reg = mutated()
        del reg["clients"]["agy"]["available"]
        reg["clients"]["agy"]["available"] = True
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("entry with unavailable_until but no available: false: clients.agy"
                in p for p in problems),
            problems)


class OpenRouterByokLegTests(unittest.TestCase):
    """Operator 2026-09-27T13:5xZ measured the openrouter/openai/gpt-oss-120b
    BYOK leg 200 on 3/3 trials and briefly un-gated it, but the L0 C change was
    reverted the same day (a re-probe hit 401 credits exhausted), so t2-worker
    keeps its `available: false` gate until the operator sets BYOK Prioritized
    and a fresh probe passes. The per-leg allow rule is kept in place."""

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()
        cls.t2_worker_legs = cls.reg["routes"]["t2-worker"]["legs"]

    def test_leg_is_present_after_sambanova_gpt_oss_120b(self):
        idx = self.t2_worker_legs.index("openrouter/openai/gpt-oss-120b")
        # Must be right after sambanova/gpt-oss-120b
        self.assertGreaterEqual(idx, 1)
        self.assertEqual(self.t2_worker_legs[idx - 1], "sambanova/gpt-oss-120b")

    def test_leg_resolves_to_a_model(self):
        # resolve_leg ignores route membership, so this pins the spelling only;
        # the membership/order pin is the test above.
        provider, model = registry.resolve_leg("openrouter/openai/gpt-oss-120b", self.reg)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(model, "openai/gpt-oss-120b")

    def test_a_sibling_spelling_with_no_model_entry_is_refused(self):
        with self.assertRaises(ValueError):
            registry.resolve_leg("openrouter/openai/gpt-oss-120b-nope", self.reg)

    def test_t2_worker_gates_the_leg_until_byok_is_prioritized(self):
        # C was reverted: the leg stays `available: false` until the operator
        # sets BYOK Prioritized and a fresh probe passes (probe-toolcalls.py
        # skips legs listed here).
        entry = (self.reg["routes"]["t2-worker"].get("unavailable_legs") or {}).get(
            "openrouter/openai/gpt-oss-120b")
        self.assertIsNotNone(entry, "t2-worker no longer gates the BYOK leg")
        self.assertFalse(entry["available"])

    def test_unavailable_entry_has_the_l0_comment(self):
        """PROV finding 12: the route-level gate must carry its provenance
        ($comment), not only the boolean flag - the removed gate-comment test's
        contract (D20: an unmeasured lesson is not a rule)."""
        entry = (self.reg["routes"]["t2-worker"].get("unavailable_legs") or {}).get(
            "openrouter/openai/gpt-oss-120b")
        self.assertIsNotNone(entry, "t2-worker no longer gates the BYOK leg")
        comment = entry.get("$comment")
        self.assertIsInstance(comment, str, "$comment provenance is missing")
        self.assertTrue(comment.strip())
        self.assertIn("2026-09-27T03:39Z", comment)
        self.assertIn("probe-toolcalls.py", comment)

    def test_gated_leg_is_not_servable_in_any_route_listing_it(self):
        listing = 0
        for rid, route in self.reg["routes"].items():
            if "openrouter/openai/gpt-oss-120b" not in (route.get("legs") or []):
                continue
            listing += 1
            kept = registry.gateway_legs(route, self.reg)
            self.assertNotIn("openrouter/openai/gpt-oss-120b", kept, rid)
        self.assertGreater(listing, 0)

    def test_other_openrouter_legs_stay_denied(self):
        # The BYOK allow is per-leg; the blanket deny-openrouter still gates
        # every other openrouter leg (first match wins).
        for leg in ("openrouter/google/gemini-3.8-flash",
                    "openrouter/qwen/qwen3.8-235b"):
            self.assertTrue(registry.leg_denied(leg, self.reg), leg)

    def test_leg_rule_records_the_reverted_byok_answer(self):
        # The allow rule survives the revert (a per-leg allow before the blanket
        # deny-openrouter), but it carries the operator's 03:39Z answer note,
        # not the reverted C measurement.
        rules = self.reg["policy"]["leg_rules"]
        rule = next(r for r in rules if r["id"] == "allow-openrouter-gpt-oss-byok")
        self.assertIn("BYOK", rule["reason"])
        self.assertNotIn("3/3", rule["reason"])
        self.assertIn("2026-09-27T03:39Z", rule["source"])


class LegRulesTests(unittest.TestCase):
    """leg_rules policy gates (briefs/common.md Claude budget rules, encoded as
    policy.leg_rules: ordered list of {"id", "match" (fnmatch), "allow" (bool),
    "reason", "source"}; first match wins; no match = allowed.

    A denied leg that routes.<id>.unavailable_legs already gates (available:false)
    passes the check — the operator has already acknowledged it. A denied leg
    that is still serving (not flagged in unavailable_legs) is an error naming
    the rule that denied it.
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_denied_serving_leg_fails_check_naming_the_rule(self):
        """A leg matching a deny rule with no unavailable_legs gate is flagged."""
        reg = mutated()
        # groq/qwen/qwen3.8-27b resolves (provider groq, model qwen/qwen3.8-27b)
        # and would be denied by groq/* rule
        reg["routes"]["t2-worker"]["legs"].append("groq/qwen/qwen3.8-27b")
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("leg_rules" in p and "groq/qwen/qwen3.8-27b" in p for p in problems),
            problems)

    def test_denied_leg_gated_via_unavailable_legs_passes(self):
        """A leg matching a deny rule that is already in unavailable_legs is not
        flagged — the operator has already acknowledged it."""
        reg = mutated()
        reg["routes"]["t2-worker"]["legs"].append("groq/qwen/qwen3.8-27b")
        reg["routes"]["t2-worker"].setdefault("unavailable_legs", {})[
            "groq/qwen/qwen3.8-27b"] = {"available": False}
        problems = registry.check_registry(reg)
        self.assertFalse(
            any("leg_rules" in p and "groq/qwen/qwen3.8-27b" in p for p in problems),
            problems)

    def test_allowed_leg_passes_after_deny_rule(self):
        """First-match-wins: an allow rule placed before a deny rule exempts."""
        reg = mutated()
        # deepseek/deepseek-flash resolves and should be allowed by an early
        # allow rule before the *deepseek* deny
        reg["routes"]["t2-worker"]["legs"].append("deepseek/deepseek-flash")
        problems = registry.check_registry(reg)
        self.assertFalse(
            any("leg_rules" in p and "deepseek/deepseek-flash" in p for p in problems),
            problems)

    def test_leg_with_no_matching_rule_is_allowed(self):
        """No match in any leg_rule = allowed (not flagged)."""
        reg = mutated()
        # mistral/leg exists and is not matched by any leg_rule
        reg["routes"]["t2-worker"]["legs"].append("mistral/mistral-small-latest")
        problems = registry.check_registry(reg)
        self.assertFalse(
            any("leg_rules" in p and "mistral/mistral-small-latest" in p for p in problems),
            problems)

    def test_rule_order_pins_the_budget_decisions(self):
        """The committed rules decide each measured case (first match wins)."""
        cases = {
            "groq/openai/gpt-oss-120b": False,          # deny-groq before allow-gpt-oss
            "samba/gpt-oss-120b": True,
            # operator 2026-09-27T07:3xZ: zen allowed as opencode-client-bound (was denied);
            # the zen rule still sits before the deepseek deny, so zen deepseek is allowed
            "opencode-zen/deepseek-v4.1-flash": True,
            "opencode-zen/muse-spark-1.3-contributor-free": True,
            "openrouter/deepseek/deepseek-v4.1-flash": True,
            "openrouter/meta/muse-spark-1.3-contributor-xhigh": True,
            "openrouter/openai/gpt-oss-120b": True,
            "openrouter/google/gemini-3.8-flash": False,
            "deepseek/deepseek-flash": True,
            "cheaperinference/deepseek-v4-flash": False,
            "cc/claude-opus-4-6": True,                  # subscription seat
            "antigravity/claude-opus-4-6-thinking": True,  # agy sign-in
            "cheaperinference/claude-sonnet-5": False,
            "cheaperinference/gpt-5.5": False,
            "mistral/mistral-small-latest": True,        # no rule matches
        }
        got = {leg: not registry.leg_denied(leg, self.reg) for leg in cases}
        self.assertEqual(got, cases)

    def test_matching_is_case_insensitive(self):
        """DSAMEND2 (Muse review of DSAMEND, operator rule 'never DeepSeek Pro
        under ANY provider id'): the matcher casefolds pattern and leg alike.
        Under case-SENSITIVE fnmatchcase a deny only binds the exact casing it
        was written in, so `samba/DeepSeek-V4-Pro` — the very model the operator
        forbids, spelled with capitals — matched no rule at all and fell through
        to the no-match-allowed default. No deny may be escapable by
        re-capitalising it."""
        for leg in ("samba/DeepSeek-V4-Pro",
                    "openrouter/deepseek/DeepSeek-V4-PRO",
                    "Deepseek-V4-Pro"):
            with self.subTest(leg=leg):
                self.assertIs(registry.leg_denied(leg, self.reg), True)
                self.assertEqual(
                    (registry.leg_rule_for(leg, self.reg) or {}).get("id"),
                    "deny-deepseek-pro", leg)
        # and the mixed-case spellings of an ALLOW stay allowed (first match
        # still wins, the casefold does not let a deny outrank an allow that
        # precedes it): the registry's one mixed-case leg is samba/MiniMax-M3.
        self.assertIs(registry.leg_denied("samba/MiniMax-M3", self.reg), False)
        self.assertIs(registry.leg_denied("deepseek/deepseek-flash", self.reg), False)

    def test_no_committed_verdict_changes_when_matching_folds_case(self):
        """The 'expect none' guard the DSAMEND2 review asks for: for every leg
        the registry actually names — every route leg, every unavailable_legs
        key, and every provider spelling x model id it can be written with —
        folding the case must not change which rule fires or its verdict. A leg
        whose verdict does change is a leg some rule was only ever binding in
        one casing, and the render and the resolver would disagree with the
        operator's budget over it."""
        legs = set()
        for route in self.reg["routes"].values():
            legs.update(l for l in (route.get("legs") or []) if isinstance(l, str))
            legs.update(l for l in (route.get("unavailable_legs") or {})
                        if isinstance(l, str))
        for pid, prov in self.reg["providers"].items():
            for sp in {pid, prov.get("omniroute_id") or pid}:
                for mid in self.reg["models"]:
                    legs.add("%s/%s" % (sp, mid))
        # the case-SENSITIVE matcher, exactly as leg_rule_for worked before the
        # fix: same rule order, fnmatchcase on the raw and canonical spellings.
        rules = [r for r in self.reg["policy"]["leg_rules"]
                 if isinstance(r, dict) and isinstance(r.get("match"), str)]

        def old_rule(leg):
            candidates = [leg]
            canonical = registry._canonical_leg_spelling(leg, self.reg)
            if canonical not in candidates:
                candidates.append(canonical)
            for rule in rules:
                for candidate in candidates:
                    if fnmatch.fnmatchcase(candidate, rule["match"]):
                        return rule
            return None

        changed = {}
        for leg in sorted(legs):
            before, after = old_rule(leg), registry.leg_rule_for(leg, self.reg)
            if (before or {}).get("id") != (after or {}).get("id") or \
                    (before or {}).get("allow") != (after or {}).get("allow"):
                changed[leg] = ((before or {}).get("id"), (after or {}).get("id"))
        self.assertEqual(changed, {}, "rules whose verdict changed for a leg in "
                                     "the registry: %s" % sorted(changed.items()))
        self.assertGreater(len(legs), 1000, "the sweep must cover the registry")

    def test_providers_key_spelling_matches_the_omniroute_id_rule(self):
        """PROV finding 3: resolve_leg() accepts either the providers key or a
        provider's omniroute_id, so leg_rules must gate BOTH spellings. A leg
        written with the providers key (cheapinference/…) must hit the rule
        written against the canonical id (cheaperinference/*)."""
        # both spellings of the denied leg are denied
        self.assertTrue(registry.leg_denied("cheapinference/glm-4.5-air", self.reg))
        self.assertTrue(registry.leg_denied("cheaperinference/glm-4.5-air", self.reg))
        # and both spellings of an allowed leg stay allowed (allow wins, first
        # match, before the trailing deny-cheaperinference)
        self.assertFalse(registry.leg_denied("cheapinference/kimi-k3", self.reg))
        self.assertFalse(registry.leg_denied("cheaperinference/kimi-k3", self.reg))
        # the providers key spelling is what the canonical resolution yields
        self.assertEqual(
            registry._canonical_leg_spelling("cheapinference/glm-4.5-air", self.reg),
            "cheaperinference/glm-4.5-air")

    def test_providers_key_spelling_is_flagged_when_serving(self):
        """A denied leg is only tolerated while the route gates it by the same
        exact string; a providers-key spelling with no gate is still a problem.
        Use groq (available) since cheaperinference is now provider-off."""
        reg = mutated()
        reg["routes"]["t2-worker"]["legs"].append("groq/qwen/qwen3.8-27b")
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("leg_rules" in p and "groq/qwen/qwen3.8-27b" in p
                for p in problems), problems)

    def test_available_true_entry_does_not_gate_a_denied_leg(self):
        """Only available:false gates (the renders' _leg_is_unavailable)."""
        reg = mutated()
        reg["routes"]["t2-worker"]["legs"].append("groq/qwen/qwen3.8-27b")
        reg["routes"]["t2-worker"].setdefault("unavailable_legs", {})[
            "groq/qwen/qwen3.8-27b"] = {"available": True}
        problems = registry.check_registry(reg)
        self.assertTrue(any("groq/qwen/qwen3.8-27b" in p for p in problems), problems)

    def test_problem_names_rule_id_and_reason(self):
        reg = mutated()
        reg["routes"]["t2-worker"]["legs"].append("groq/qwen/qwen3.8-27b")
        rule = registry.leg_rule_for("groq/qwen/qwen3.8-27b", reg)
        hits = [p for p in registry.check_registry(reg) if "groq/qwen/qwen3.8-27b" in p]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn(rule["id"], hits[0])
        self.assertIn(rule["reason"], hits[0])

    def test_non_string_leg_is_left_to_rule_one(self):
        # review-or2: a null leg must not crash the whole check in rule 9.
        reg = mutated()
        reg["routes"]["t2-worker"]["legs"].append(None)
        problems = registry.check_registry(reg)
        self.assertFalse(any(p.startswith("leg_rules") and "None" in p for p in problems))

    def test_real_registry_passes_leg_rules_check(self):
        """The committed registry must itself pass check (any denied serving
        leg must be gated via unavailable_legs)."""
        self.assertEqual(registry.check_registry(self.reg), [])


class MonthlyCapTests(unittest.TestCase):
    """WS-DSCALL (2026-09-28): providers.<id>.monthly_cap_usd is the paid cap a
    caller refuses at; the validator accepts only a positive number with a
    monthly_cap_source."""

    def test_the_live_deepseek_cap_is_25_with_a_source(self):
        deepseek = load_registry()["providers"]["deepseek"]
        self.assertEqual(deepseek["monthly_cap_usd"], 25)
        self.assertTrue(deepseek["monthly_cap_source"].strip())
        self.assertEqual(registry._check_monthly_caps(load_registry()), [])

    def test_a_bad_cap_is_flagged_naming_the_provider(self):
        for bad in (0, -1, "25", True, None):
            reg = mutated()
            reg["providers"]["deepseek"]["monthly_cap_usd"] = bad
            problems = [p for p in registry.check_registry(reg) if "monthly_cap_usd" in p]
            self.assertTrue(problems, bad)
            self.assertIn("deepseek", problems[0])

    def test_a_cap_without_a_source_is_flagged(self):
        reg = mutated()
        del reg["providers"]["deepseek"]["monthly_cap_source"]
        self.assertTrue([p for p in registry.check_registry(reg) if "monthly_cap_source" in p])


class ProviderLimitsTests(unittest.TestCase):
    """providers.<id>.limits: per-model free-tier rate caps as data (brief R4,
    2026-09-27). Keyed by the provider's own model spelling (the part of the
    leg after '<provider>/'); validated against resolve_leg so an unknown model
    spelling is caught here, not at route time. Values are non-negative ints;
    rpm/rpd/tpm/tpd are each optional, source is required (D20).
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_live_groq_limits_entries_are_shape_valid(self):
        """Live shape only (review R4FIX): each groq limits entry carries only
        the schema's allowed keys, every cap present is a non-negative int, and
        the source is a non-empty string. The exact console numbers are pinned
        by test_groq_limits_carry_the_console_numbers_inline against an inline
        registry, so a legitimate re-measure edits one place instead of an
        assertion over live data.
        """
        limits = self.reg["providers"]["groq"]["limits"]
        self.assertEqual(
            sorted(limits),
            ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"])
        for model, entry in limits.items():
            self.assertTrue(set(entry) <= ALLOWED_LIMIT_KEYS, (model, entry))
            self.assertIsInstance(entry["source"], str, model)
            self.assertTrue(entry["source"].strip(), model)
            for field in ("rpm", "rpd", "tpm", "tpd"):
                if field not in entry:
                    continue
                value = entry[field]
                self.assertIsInstance(value, int, (model, field))
                self.assertNotIsInstance(value, bool, (model, field))
                self.assertGreaterEqual(value, 0, (model, field))

    def test_groq_limits_carry_the_console_numbers_inline(self):
        """The operator's measured Groq console numbers (brief R4), held as an
        inline registry so the pin is over data, not over the live file."""
        limits = {
            "openai/gpt-oss-120b": {
                "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
                "source": "operator Groq console screenshot 2026-09-27"},
            "openai/gpt-oss-20b": {
                "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
                "source": "operator Groq console screenshot 2026-09-27"},
            "qwen/qwen3.8-27b": {
                "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
                "source": "operator Groq console screenshot 2026-09-27"},
        }
        for entry in limits.values():
            self.assertEqual(entry["rpm"], 30)
            self.assertEqual(entry["rpd"], 1000)
            self.assertEqual(entry["tpm"], 8000)
            self.assertEqual(entry["tpd"], 200000)
            self.assertEqual(entry["source"],
                             "operator Groq console screenshot 2026-09-27")

    def test_unknown_limits_field_is_flagged_with_provider_model_and_key(self):
        """Review R4FIX: a 'tmp' key in a limits entry (measured to pass before
        the fix) must be a problem naming the provider, the model and the key."""
        reg = mutated()
        reg["providers"]["groq"]["limits"]["openai/gpt-oss-120b"]["tmp"] = 1
        problems = [p for p in registry.check_registry(reg) if "tmp" in p]
        self.assertTrue(problems, registry.check_registry(reg))
        joined = " ".join(problems)
        self.assertIn("groq", joined)
        self.assertIn("openai/gpt-oss-120b", joined)

    def test_every_limits_key_resolves_as_a_groq_leg(self):
        for key in self.reg["providers"]["groq"]["limits"]:
            provider_id, model_id = registry.resolve_leg("groq/" + key, self.reg)
            self.assertEqual(provider_id, "groq")
            self.assertEqual(model_id, key)

    def test_unknown_limits_key_is_flagged(self):
        reg = mutated()
        reg["providers"]["groq"]["limits"]["ghost-model"] = {
            "tpm": 8000, "source": "test"}
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("limits" in p and "ghost-model" in p for p in problems),
            problems)

    def test_a_negative_limit_value_is_flagged(self):
        reg = mutated()
        reg["providers"]["groq"]["limits"]["openai/gpt-oss-120b"]["tpm"] = -1
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("limits" in p and "tpm" in p
                and "openai/gpt-oss-120b" in p for p in problems),
            problems)

    def test_real_registry_passes_limits_check(self):
        self.assertEqual(registry.check_registry(self.reg), [])


class CheaperinferenceUnavailableTests(unittest.TestCase):
    """Operator 2026-09-27T17:2xZ: wallet balance exhausted (402), operator
    rule: credits out -> off until topped up. Provider marked available:false.
    Its pinned combos cheaperinference/{glm-5.2,kimi-k3} then go to omitted
    like deepseek - re-pin tests accordingly."""

    def test_provider_is_unavailable(self):
        entry = load_registry()["providers"]["cheapinference"]
        self.assertIs(entry.get("available"), False)

    def test_no_cheaperinference_legs_are_servable(self):
        reg = load_registry()
        served = set()
        for rid, route in reg["routes"].items():
            for leg in registry.gateway_legs(route, reg):
                if leg.startswith("cheaperinference/"):
                    served.add(leg)
        self.assertEqual(served, set())

    def test_glm_4_5_air_and_deepseek_flash_stay_out(self):
        reg = load_registry()
        for rid, route in reg["routes"].items():
            kept = registry.gateway_legs(route, reg)
            self.assertNotIn("cheaperinference/glm-4.5-air", kept, rid)
            self.assertNotIn("cheaperinference/deepseek-v4-flash", kept, rid)

    def test_glm_4_5_air_is_route_gated_where_listed(self):
        reg = load_registry()
        for rid in ("t2-worker", "t3-driver"):
            entry = (reg["routes"][rid].get("unavailable_legs") or {}).get(
                "cheaperinference/glm-4.5-air")
            self.assertIsNotNone(entry, rid)
            self.assertIs(entry.get("available"), False)

    def test_leg_rules_allow_the_three_and_deny_the_rest(self):
        reg = load_registry()
        # The three legs are allowed by leg_rules (explicit allow rules), but
        # unavailable due to provider-level available:false.
        for leg in ("cheaperinference/kimi-k3", "cheaperinference/glm-5.2",
                    "cheaperinference/minimax-m2.7"):
            self.assertFalse(registry.leg_denied(leg, reg), leg)
            self.assertTrue(registry._leg_is_unavailable(leg, reg["routes"]["t2-worker"], reg), leg)
        # Other cheaperinference legs are denied by leg_rules (deny-cheaperinference)
        # AND by provider-level available:false.
        for leg in ("cheaperinference/glm-4.5-air",
                    "cheaperinference/deepseek-v4-flash",
                    "cheaperinference/claude-sonnet-5",
                    "cheaperinference/anything-else"):
            self.assertTrue(registry.leg_denied(leg, reg), leg)

    def test_real_registry_passes_check_with_cheaperinference_back(self):
        self.assertEqual(registry.check_registry(load_registry()), [])


class CerebrasDisabledTests(unittest.TestCase):
    """Operator 2026-09-27T12:55Z: cerebras has no free tier and no credits, so
    OmniRoute disables the connection - provider available:false, and no
    gateway declaration carries any of its legs."""

    def test_provider_is_unavailable(self):
        self.assertIs(load_registry()["providers"]["cerebras"].get("available"), False)

    def test_gateway_legs_drop_every_cerebras_leg(self):
        reg = load_registry()
        for rid, route in reg["routes"].items():
            kept = registry.gateway_legs(route, reg)
            self.assertFalse([leg for leg in kept if leg.startswith("cerebras/")],
                             "route %s still serves a cerebras leg" % rid)


class ClaudeCodeLegsUnavailableTests(unittest.TestCase):
    """Operator 2026-09-27T11:1xZ (via L0): Claude Code (cc/*) will not be
    connected to OmniRoute, so provider cc is unavailable and no gateway
    declaration carries a cc leg."""

    def test_provider_is_unavailable(self):
        self.assertIs(load_registry()["providers"]["cc"].get("available"), False)

    def test_gateway_legs_drop_every_cc_leg(self):
        reg = load_registry()
        for rid, route in reg["routes"].items():
            kept = registry.gateway_legs(route, reg)
            self.assertFalse([leg for leg in kept if leg.startswith("cc/")],
                             "route %s still serves a cc leg" % rid)


class FreeAiProviderTests(unittest.TestCase):
    """BRIEF FREEAI (2026-09-27): Free.ai joins as the free provider `free_ai`
    with model `qwen7b`, wired as the LAST free leg of both zero-spend routes
    so `t3-driver-free-only` serves again. `free_ai` is a public pool whose
    terms allow training on prompts, so it is never private-safe and never
    enters a -clean route."""

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_provider_entry_shape(self):
        provider = self.reg["providers"]["free_ai"]
        self.assertEqual(provider["api_base"], "https://api.free.ai/v1")
        self.assertEqual(provider["omniroute_id"], "free-ai")
        self.assertEqual(provider["model_prefix"], "free-ai")
        self.assertEqual(provider["litellm_prefix"], "openai")
        self.assertEqual(provider["litellm_env"], "FREE_AI_API_KEY")
        self.assertEqual(provider["tier"], "free")
        self.assertIs(provider["trains_on_prompts"], True)

    def test_gateway_ref_translates_to_the_builtin_free_ai_id(self):
        # free_ai is a built-in OmniRoute connection; the gateway catalog names
        # its model free-ai/qwen7b, exactly as antigravity -> agy.
        self.assertEqual(
            registry.gateway_ref("free_ai/qwen7b", self.reg), "free-ai/qwen7b")

    def test_provider_limits_are_rpm_and_tpd_only(self):
        # free_ai's terms cap requests per minute and tokens per DAY; there is
        # no published per-minute token cap, so only rpm/tpd are declared (the
        # resolver reads only `tpm`, which is deliberately absent).
        limits = self.reg["providers"]["free_ai"]["limits"]
        self.assertEqual(sorted(limits), ["qwen7b"])
        entry = limits["qwen7b"]
        self.assertEqual(entry["rpm"], 10)
        self.assertEqual(entry["tpd"], 30000)
        self.assertNotIn("tpm", entry)
        self.assertTrue(entry["source"].strip())

    def test_model_qwen7b_mirrors_its_qwen_sibling(self):
        model = self.reg["models"]["qwen7b"]
        self.assertEqual(model["context_advertised"], 131072)
        self.assertEqual(model["output_max"], 16384)
        self.assertEqual(model["price_in"], 0.0)
        self.assertEqual(model["price_out"], 0.0)

    def test_free_ai_is_never_private_safe(self):
        safe, reason = registry.private_safe("free_ai", "qwen7b", self.reg)
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_free_ai_leg_is_last_on_both_free_only_routes(self):
        for route_id in ("t2-worker-free-only", "t3-driver-free-only"):
            legs = self.reg["routes"][route_id]["legs"]
            self.assertEqual(legs[-1], "free_ai/qwen7b", route_id)

    def test_t2_worker_ends_with_the_free_ai_stopgap_leg(self):
        """BRIEF T2FREE (S1, urgent stopgap 2026-09-28): the routing-00 smoke
        run got 503 ALL_TARGETS_SKIPPED from t2-worker — every leg ahead of
        this one was down at the time (gemini 429 cooldown, agy out of quota,
        meta-api not registered) — so the leg measured answering 200 is the
        route's last resort. The free-only routes already carried it last."""
        route = self.reg["routes"]["t2-worker"]
        self.assertEqual(route["legs"][-1], "free_ai/qwen7b")
        comment = route.get("$comment")
        self.assertIsInstance(comment, str, "$comment provenance is missing")
        self.assertIn("stopgap routing-00 smoke 2026-09-28T04:5xZ (T2FREE)",
                      comment)

    def test_no_clean_route_carries_free_ai(self):
        # PROV finding 11: assert BOTH spellings - the registry leg (free_ai/)
        # and its rendered omniroute_id (free-ai/) - so a regression that emits
        # either into a -clean route is caught.
        for route_id, route in self.reg["routes"].items():
            if not route_id.endswith("-clean"):
                continue
            self.assertFalse(
                [leg for leg in route.get("legs") or []
                 if leg.startswith(("free_ai/", "free-ai/"))],
                "clean route %s carries free_ai" % route_id)

    def test_gateway_legs_keep_free_ai_on_the_free_only_routes(self):
        for route_id in ("t2-worker-free-only", "t3-driver-free-only"):
            kept = registry.gateway_legs(self.reg["routes"][route_id], self.reg)
            self.assertIn("free_ai/qwen7b", kept, route_id)

    def test_t3_driver_free_only_serves_again(self):
        ids = registry.servable_route_ids(self.reg)
        self.assertIn("t3-driver-free-only", ids)
        self.assertIn("t2-worker-free-only", ids)

    def test_leg_rules_allow_the_free_ai_leg(self):
        self.assertFalse(registry.leg_denied("free_ai/qwen7b", self.reg))

    def test_real_registry_passes_check_with_free_ai(self):
        self.assertEqual(registry.check_registry(self.reg), [])


class MetaApiProviderTests(unittest.TestCase):
    """BRIEF MUSEAPI (2026-09-27): the Meta Model API joins as the paid provider
    `meta_api` (OpenAI-compatible at api.meta.ai/v1, key 'meta'), its
    `muse-spark-1.3-contributor` model heading t1-orchestrator,
    t1-orchestrator-paid and spark-1.3-contributor. It trains on prompts by
    contributor contract, so it is never private-safe and never enters a
    -clean route; on t2-worker/t3-driver it is a paid escalation placed AFTER
    that route's free legs (the trailing T2FREE stopgap free leg on t2-worker
    excepted — it is last on purpose, see FreeAiProviderTests)."""

    LEG = "meta_api/muse-spark-1.3-contributor"

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_provider_entry_shape(self):
        provider = self.reg["providers"]["meta_api"]
        self.assertEqual(provider["api_base"], "https://api.meta.ai/v1")
        self.assertEqual(provider["omniroute_id"], "meta-api")
        # Not "meta": that is another provider's registry key (the direct
        # opencode-only record), and a combos leg's prefix is looked up by name
        # first, so borrowing it gave the leg that record's (absent) LiteLLM
        # transport. The gateway prefix is the connection id, as free_ai's is
        # "free-ai".
        self.assertEqual(provider["model_prefix"], "meta-api")
        self.assertEqual(provider["litellm_prefix"], "openai")
        self.assertEqual(provider["litellm_env"], "META_API_KEY")
        self.assertEqual(provider["tier"], "paid")
        self.assertIs(provider["trains_on_prompts"], True)

    def test_the_key_name_is_the_existing_meta_entry(self):
        # The operator's Meta key is api-keys.yml's 'meta' (providers.meta's
        # name). key_name says so instead of asking for a second key; every
        # other provider's api-keys.yml name is its own id, so at most one
        # provider may ever point at another one's name.
        providers = self.reg["providers"]
        self.assertEqual(providers["meta_api"]["key_name"], "meta")
        self.assertIn("meta", providers)
        for name, entry in providers.items():
            claimed = entry.get("key_name")
            if claimed is None:
                continue
            self.assertIn(claimed, providers,
                          "%s claims key_name %r, which is no provider's name"
                          % (name, claimed))
            self.assertNotEqual(claimed, name,
                                "%s repeats its own name as key_name" % name)

    def test_gateway_ref_translates_through_the_model_prefix(self):
        # The combos.json spelling is the connection apply registers, so a
        # reader of combos.json can resolve the leg back to this provider.
        self.assertEqual(registry.gateway_ref(self.LEG, self.reg),
                         "meta-api/muse-spark-1.3-contributor")

    def test_limits_are_the_published_contributor_ceilings(self):
        entry = self.reg["providers"]["meta_api"]["limits"]["muse-spark-1.3-contributor"]
        self.assertEqual(entry["rpm"], 100)
        self.assertEqual(entry["tpm"], 3000000)
        self.assertTrue(entry["source"].startswith("https://"))

    def test_model_entry_matches_the_l0_measurement(self):
        model = self.reg["models"]["muse-spark-1.3-contributor"]
        self.assertEqual(model["context_advertised"], 1048576)
        self.assertEqual(model["output_max"], 131072)
        self.assertIs(model["reasoning"], True)
        # $0.10 / $0.20 per 1M tokens, stored per token like every other entry.
        self.assertAlmostEqual(model["price_in"], 0.10 / 1_000_000)
        self.assertAlmostEqual(model["price_out"], 0.20 / 1_000_000)
        self.assertEqual(model["effort_ladder"],
                         ["minimal", "low", "medium", "high", "xhigh"])
        self.assertNotIn("none", model["effort_ladder"])
        self.assertNotIn("max", model["effort_ladder"])

    def test_tool_calls_stays_the_conservative_unknown(self):
        # Only tools/probe-toolcalls.py promotes this field (mapping doc
        # section 3); the L0 call measured reasoning_effort, not a tool call.
        self.assertEqual(self.reg["models"]["muse-spark-1.3-contributor"]["tool_calls"],
                         "unproven")

    def test_the_leg_heads_the_three_routes(self):
        for route_id in ("t1-orchestrator", "t1-orchestrator-paid",
                         "spark-1.3-contributor"):
            self.assertEqual(self.reg["routes"][route_id]["legs"][0], self.LEG,
                             route_id)

    def test_the_leg_follows_the_free_legs_on_t2_and_t3(self):
        providers = self.reg["providers"]

        def tier_of(leg):
            provider_id = registry.resolve_leg(leg, self.reg)[0]
            return providers[provider_id]["tier"]

        # T2FREE (2026-09-28) appended the free stopgap leg LAST on t2-worker,
        # i.e. behind this paid escalation; every other free leg still comes
        # first, which is what this pins.
        stopgap = "free_ai/qwen7b"
        for route_id in ("t2-worker", "t3-driver"):
            legs = self.reg["routes"][route_id]["legs"]
            self.assertIn(self.LEG, legs, route_id)
            last_free = max(i for i, leg in enumerate(legs)
                            if leg not in (self.LEG, stopgap)
                            and tier_of(leg) == "free")
            self.assertGreater(legs.index(self.LEG), last_free, route_id)

    def test_no_clean_route_carries_the_leg(self):
        for route_id, route in self.reg["routes"].items():
            if not route_id.endswith("-clean"):
                continue
            self.assertFalse(
                [leg for leg in route.get("legs") or []
                 if leg.startswith(("meta_api/", "meta/"))],
                "clean route %s carries the contributor leg" % route_id)

    def test_it_is_never_private_safe(self):
        safe, reason = registry.private_safe("meta_api", "muse-spark-1.3-contributor",
                                             self.reg)
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_the_three_headed_routes_are_servable_again(self):
        ids = registry.servable_route_ids(self.reg)
        for route_id in ("t1-orchestrator", "t1-orchestrator-paid",
                         "spark-1.3-contributor"):
            self.assertIn(route_id, ids, route_id)

    def test_gateway_legs_keep_the_contributor_first(self):
        kept = registry.gateway_legs(self.reg["routes"]["spark-1.3-contributor"],
                                     self.reg)
        self.assertEqual(kept[0], self.LEG)

    def test_real_registry_passes_check_with_meta_api(self):
        self.assertEqual(registry.check_registry(self.reg), [])


class ReviewerPolicyTests(unittest.TestCase):
    """Brief REVROUTE (S2) item 1 (2026-09-27): who may review is data, not
    code. ``policy.reviewers`` is the ordered preference list the resolver walks
    for a review card, and every entry names the model's ``family`` so the
    different-family rule (an author is never reviewed by its own family) is
    checkable without a vendor table in the resolver.

    The operator's fixed order: the PAID Meta Muse contributor first (a
    different family from every free author, and it is what the paid API was
    bought for), the other free families next, Claude Haiku last and only as a
    first-pass fallback. Sonnet stays the high-risk closer and is deliberately
    absent -- it is not on a preference list, it closes.
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()
        cls.reviewers = cls.reg["policy"]["reviewers"]

    def test_the_list_is_ordered_and_non_empty(self):
        self.assertIsInstance(self.reviewers, list)
        self.assertTrue(self.reviewers, "policy.reviewers is empty")

    def test_every_entry_carries_the_four_fields(self):
        for index, entry in enumerate(self.reviewers):
            for key in ("client", "model", "family", "paid"):
                self.assertIn(key, entry, "reviewers[%d] has no %s" % (index, key))
            self.assertIsInstance(entry["client"], str)
            self.assertIsInstance(entry["model"], str)
            self.assertIsInstance(entry["family"], str)
            self.assertTrue(entry["family"], "reviewers[%d].family is empty" % index)
            self.assertIsInstance(entry["paid"], bool)

    def test_every_entry_names_a_real_client(self):
        for index, entry in enumerate(self.reviewers):
            self.assertIn(entry["client"], self.reg["clients"],
                          "reviewers[%d] client %r is not a registry client"
                          % (index, entry["client"]))

    def test_the_paid_muse_reviewer_leads(self):
        first = self.reviewers[0]
        self.assertEqual(first["family"], "meta")
        self.assertIs(first["paid"], True)
        self.assertEqual(first["client"], "opencode")
        self.assertIn("spark-1.3-contributor", first["model"])

    def test_haiku_is_a_first_pass_only_fallback(self):
        haiku = [e for e in self.reviewers if e["family"] == "anthropic"]
        self.assertTrue(haiku, "Claude Haiku is missing from the fallback list")
        for entry in haiku:
            self.assertIs(entry["first_pass_only"], True, entry["model"])

    def test_sonnet_is_not_a_preference(self):
        # The closer is the resolver's, not the list's: pinning that here so a
        # later "add Sonnet to the reviewers list" edit has to say why.
        for entry in self.reviewers:
            self.assertNotIn("sonnet", entry["model"].lower())

    def test_the_list_spans_several_families(self):
        # A one-family reviewer list cannot satisfy the different-family rule
        # for most authors.
        self.assertGreater(len({e["family"] for e in self.reviewers}), 2)

    def test_the_missing_family_on_a_model_is_still_a_check_failure(self):
        # REVROUTE's premise: family is what the reviewer rule reads, so it is
        # schema-required on every model (rule 6). Guards the field the brief
        # asked for against a later "make it optional" edit.
        reg = mutated()
        model_id = sorted(reg["models"])[0]
        del reg["models"][model_id]["family"]
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems if "family" in p], problems)

    def test_check_rule_flags_a_reviewer_without_a_family(self):
        reg = mutated()
        del reg["policy"]["reviewers"][0]["family"]
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems if "reviewers" in p], problems)

    def test_check_rule_flags_a_reviewer_with_an_unknown_client(self):
        reg = mutated()
        reg["policy"]["reviewers"][0]["client"] = "not-a-client"
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems if "reviewers" in p], problems)

    def test_check_rule_flags_a_non_boolean_paid(self):
        reg = mutated()
        reg["policy"]["reviewers"][0]["paid"] = "yes"
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems if "reviewers" in p], problems)

    def test_check_rule_rejects_an_empty_or_missing_list(self):
        for mutate in (lambda r: r["policy"].__setitem__("reviewers", []),
                       lambda r: r["policy"].pop("reviewers")):
            reg = mutated()
            mutate(reg)
            problems = registry.check_registry(reg)
            self.assertTrue([p for p in problems if "reviewers" in p], problems)

    def test_check_rule_flags_a_reviewer_leg_that_does_not_resolve(self):
        # A leg is the only link from a reviewer to a provider, and the
        # resolver reads availability and the training test off it.
        reg = mutated()
        reg["policy"]["reviewers"][0]["leg"] = "no-such-provider/no-such-model"
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems
                         if "reviewers" in p and "does not resolve" in p],
                        problems)

    def test_check_rule_flags_a_family_disagreeing_with_its_leg(self):
        # The different-family rule compares the entry's spelling; a leg whose
        # own model says something else means the rule silently mis-fires.
        reg = mutated()
        reg["policy"]["reviewers"][0]["family"] = "qwen"
        problems = registry.check_registry(reg)
        self.assertTrue([p for p in problems
                         if "reviewers" in p and "disagrees" in p], problems)

    def test_every_leg_on_the_reviewer_list_resolves(self):
        legs = [e["leg"] for e in self.reviewers if "leg" in e]
        self.assertTrue(legs, "no reviewer names a leg, so nothing is checked")
        for leg in legs:
            provider_id, model_id = registry.resolve_leg(leg, self.reg)
            self.assertIn(model_id, self.reg["models"], leg)
            self.assertIn(provider_id, self.reg["providers"], leg)

    def test_the_list_is_ordered_paid_first_then_free(self):
        # The operator's cost preference made visible: pay for the different
        # family, fall back to free, and never let Haiku close.
        self.assertIs(self.reviewers[0]["paid"], True)
        self.assertEqual(self.reviewers[-1]["family"], "anthropic")

    def test_real_registry_passes_the_reviewers_check(self):
        self.assertEqual(registry.check_registry(self.reg), [])


class HandoffCapsPolicyTests(unittest.TestCase):
    """rule 13 (brief AUTHORS (S1) item 2, 2026-09-28): the hand-off cap is
    *derived* data, and nothing checked the derivation.

    ``policy.handoff_caps`` is the single source for the context cap a lane stops
    at (spec 8.3; tools/autoos_context.py reads ``cap_tokens`` and never
    recomputes it), so a row whose ``cap_tokens`` disagrees with
    ``window * cap_fraction`` states two caps at once: the operator edits the
    fraction, the lane still hands off at the old number. The schema checks each
    field's type and can say nothing about the arithmetic between them, so the
    invariant needs a rule of its own. The ``match: ["*"]`` row is checked too --
    it is what gives a model no other row names a cap at all, and its absence is
    what makes ``autoos_context.load_caps`` silently fall back to the hardcoded
    ``DEFAULT_CAPS`` nobody maintains.
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()
        cls.caps = cls.reg["policy"]["handoff_caps"]

    def caps_problems(self, reg):
        return [p for p in registry.check_registry(reg) if p.startswith("handoff_caps:")]

    # -- the real registry --------------------------------------------------

    def test_every_real_row_hands_off_at_window_times_fraction(self):
        for key, entry in sorted(self.caps.items()):
            with self.subTest(row=key):
                self.assertEqual(entry["cap_tokens"],
                                 round(entry["window"] * entry["cap_fraction"]), key)

    def test_exactly_one_star_fallback_row_exists(self):
        rows = sorted(k for k, e in self.caps.items() if "*" in (e.get("match") or []))
        self.assertEqual(len(rows), 1, rows)

    def test_the_real_registry_passes_the_handoff_caps_check(self):
        self.assertEqual(self.caps_problems(self.reg), [])

    # -- red on a bad row ---------------------------------------------------

    def test_a_cap_that_disagrees_with_its_formula_names_the_row(self):
        reg = mutated()
        key = sorted(reg["policy"]["handoff_caps"])[0]
        row = reg["policy"]["handoff_caps"][key]
        row["cap_tokens"] = row["cap_tokens"] + 1
        problems = self.caps_problems(reg)
        self.assertEqual(len(problems), 1, problems)
        # It names the row, and states both sides: "which number is wrong" must
        # be answerable from the message, not from reopening the registry.
        self.assertIn("policy.handoff_caps.%s" % key, problems[0])
        self.assertIn(str(row["cap_tokens"]), problems[0])
        self.assertIn(str(round(row["window"] * row["cap_fraction"])), problems[0])

    def test_a_fraction_that_no_rounding_reproduces_is_reported_once(self):
        # The other half of the same row: an operator lowers the fraction and
        # forgets the tokens.
        reg = mutated()
        key = "claude-opus-1m"
        reg["policy"]["handoff_caps"][key]["cap_fraction"] = 0.4
        problems = self.caps_problems(reg)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("policy.handoff_caps.%s" % key, problems[0])

    def test_deleting_the_star_row_is_reported_as_a_missing_fallback(self):
        reg = mutated()
        for key, entry in list(reg["policy"]["handoff_caps"].items()):
            if "*" in entry["match"]:
                del reg["policy"]["handoff_caps"][key]
        problems = self.caps_problems(reg)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('"*"', problems[0])

    def test_a_star_row_replaced_by_a_named_one_is_still_no_fallback(self):
        # The defect is the same with a row that looks like a default: match
        # ["*"] is the only spelling the cap reader treats as the fallback.
        reg = mutated()
        for entry in reg["policy"]["handoff_caps"].values():
            if "*" in entry["match"]:
                entry["match"] = ["sonnet"]
        problems = self.caps_problems(reg)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('"*"', problems[0])

    def test_a_row_that_is_not_an_object_is_named_and_nothing_crashes(self):
        reg = mutated()
        reg["policy"]["handoff_caps"]["opus-ish"] = "600000"
        problems = self.caps_problems(reg)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("handoff_caps.opus-ish", problems[0])

    def test_a_row_whose_numbers_are_not_numbers_is_named(self):
        # A string window or a boolean fraction cannot be multiplied; it is
        # reported, not skipped (rule 6 only walks `required`, never types).
        for key, patch in (("window", "1000000"), ("cap_fraction", "0.6"),
                           ("cap_tokens", True)):
            reg = mutated()
            reg["policy"]["handoff_caps"]["claude-opus-1m"][key] = patch
            problems = self.caps_problems(reg)
            with self.subTest(field=key, value=patch):
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("policy.handoff_caps.claude-opus-1m", problems[0])

    def test_a_non_object_handoff_caps_section_is_reported_once(self):
        # An absent section stays rule 6's (the schema marks it required); a
        # present-but-wrong-typed one is this rule's, because nothing else can
        # multiply it.
        reg = mutated()
        reg["policy"]["handoff_caps"] = []
        self.assertEqual(len(self.caps_problems(reg)), 1, self.caps_problems(reg))
        absent = mutated()
        del absent["policy"]["handoff_caps"]
        self.assertEqual(self.caps_problems(absent), [],
                         "a missing required key belongs to rule 6, not here")
        self.assertTrue([p for p in registry.check_registry(absent) if "handoff_caps" in p],
                        "rule 6 must still name it")

    def test_validate_exits_1_and_names_the_bad_row(self):
        reg = mutated()
        reg["policy"]["handoff_caps"]["gemini-1m"]["cap_tokens"] = 199999
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="registry-caps-", delete=False, encoding="utf-8")
        json.dump(reg, tmp)
        tmp.close()
        try:
            proc = subprocess.run(
                [sys.executable, str(REGISTRY_TOOL), "validate", "--registry", tmp.name],
                cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        finally:
            Path(tmp.name).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("policy.handoff_caps.gemini-1m", proc.stdout)


class DeepSeekBackTests(unittest.TestCase):
    """BRIEF DSBACK (S2 urgent, operator 2026-09-28 07:4xZ): DeepSeek's credit
    ran out on 2026-09-27T16:4xZ (402 Insufficient Balance) and the whole
    provider was switched off at the registry. The operator topped the balance
    up and the router's own measured GET /user/balance answers
    is_available=true at 19.99 USD, so the provider is back on.

    These are written against the registry the renders read from, so a second
    writer who flips the flag back off fails here rather than silently shipping
    combos.json without the head of the -clean routes.
    """

    #: The rung list every laddered deepseek model must keep, verbatim.
    LADDER = ["none", "low", "high", "max"]
    #: The v4.1-Flash snapshots that carry the reasoning ladder (DSMAX).
    #: 'deepseek-v4-flash' is a *different* snapshot — non-reasoning, ladder
    #: empty, and denied outright by the operator's "DeepSeek = ONLY V4.1
    #: Flash" rule — so it is pinned separately, not folded in here.
    LADDERED = ("deepseek-flash", "deepseek-v4.1-flash",
                "deepseek/deepseek-v4.1-flash")

    @property
    def reg(self):
        return load_registry()

    def test_deepseek_provider_is_available_again(self):
        self.assertIs(self.reg["providers"]["deepseek"]["available"], True)

    def test_deepseek_availability_carries_the_operator_source_note(self):
        # The note is the provenance a later reader needs to know WHY the flag
        # is on: a bare flip reads like an accidental re-enable (DSBACK).
        entry = self.reg["providers"]["deepseek"]
        note = " ".join(str(entry.get(key, "")) for key in ("$comment", "source"))
        self.assertIn(
            "operator top-up 2026-09-28T07:4xZ, router balance check "
            "19.99 USD (DSBACK)", note)

    def test_every_deepseek_ladder_rung_is_unchanged(self):
        # DSMAX moved the ladder onto the native leg precisely so the aliases
        # would re-render unchanged when the balance came back; a flip that
        # also edited the ladders would silently change the effort surface.
        models = self.reg["models"]
        for mid in self.LADDERED:
            self.assertEqual(models[mid]["effort_ladder"], self.LADDER, mid)
        # The v4 (not v4.1) snapshot stays exactly as it was: an empty ladder
        # and reasoning off. Pinning it here keeps "unchanged" honest for the
        # whole family instead of only for the models we wanted flipped.
        self.assertEqual(models["deepseek-v4-flash"]["effort_ladder"], [])
        self.assertIs(models["deepseek-v4-flash"]["reasoning"], False)

    def test_the_zen_leg_keeps_its_own_route_gate(self):
        # DSBACK changes the PROVIDER, not the per-leg operator flags: the
        # tool-calling probe measured 402/429 on the zen leg and that verdict
        # stands until someone re-probes it.
        reg = self.reg
        for rid in ("deepseek-v4.1-flash", "t2-worker", "t2-worker-clean",
                    "t3-driver", "t3-driver-clean"):
            entry = reg["routes"][rid]["unavailable_legs"].get(
                "opencode-zen/deepseek-v4.1-flash")
            self.assertIsInstance(entry, dict, rid)
            self.assertIs(entry["available"], False, rid)

    def test_openrouter_stays_unavailable(self):
        # DSBACK is a DeepSeek-credit event; OpenRouter's own blanket flag is
        # not ours to lift.
        self.assertIs(self.reg["providers"]["openrouter"]["available"], False)

    def test_deepseek_legs_are_no_longer_gated_by_the_provider(self):
        reg = self.reg
        self.assertFalse(
            registry._leg_is_unavailable("deepseek/deepseek-flash",
                                         reg["routes"]["t2-worker-clean"], reg))

    def test_t2_worker_clean_starts_with_the_native_deepseek_leg(self):
        # The render, not the registry list, is what the gateway serves: the
        # -clean route's head must be back at the front of the combo.
        rendered = {c["name"]: c for c in
                    registry.render_omniroute(self.reg)["combos"]}
        self.assertEqual(rendered["t2-worker-clean"]["models"][0],
                         "deepseek/deepseek-flash")
        self.assertEqual(rendered["t3-driver-clean"]["models"][0],
                         "deepseek/deepseek-flash")

    def test_the_deepseek_route_is_servable_again(self):
        # The route whose ONLY served leg was deepseek/* failed closed during
        # the 402; it must be offered again.
        self.assertIn("deepseek-v4.1-flash", registry.servable_route_ids(self.reg))

    def test_models_doc_no_longers_strike_the_native_leg(self):
        row = next(r for r in registry.render_models_doc(self.reg).splitlines()
                   if "t2-worker-clean" in r)
        self.assertNotIn("~~`deepseek-flash`~~ (unavailable)", row)
        self.assertIn("`deepseek-flash`", row)

    def test_real_registry_passes_check_after_the_flip(self):
        self.assertEqual(registry.check_registry(self.reg), [])


class DeepSeekNativeIdAndProDenialTests(unittest.TestCase):
    """BRIEF DSAMEND (S1-S2, operator 2026-09-28 10:0xZ, measured on
    api.deepseek.com by routing-00): the native DeepSeek catalog is exactly
    ['deepseek-flash', 'deepseek-v4-pro'], so

      1. `deepseek-flash` is the ONLY id that may ever be sent to
         api.deepseek.com — every other native spelling (the legacy
         `deepseek/deepseek-v4-flash` row, which the API accepts only as an
         alias that answers served=deepseek-flash) is a defect, and
      2. `deepseek-v4-pro` is DENIED by standing operator rule: never route or
         fall back to it. It must be denied by an explicit leg_rule that is
         evaluated BEFORE any DeepSeek allow rule, so no future allow rule can
         let it through, and it must appear in no render.

    The other providers' spellings (`openrouter/deepseek/deepseek-v4.1-flash`,
    opencode-zen's bare `deepseek-v4.1-flash`, `cheaperinference/deepseek-v4-
    flash`) are other vendors' ids and are deliberately NOT touched here.
    """

    #: The only id allowed on the wire to the native DeepSeek API.
    NATIVE = "deepseek/deepseek-flash"
    #: A native spelling is `deepseek/deepseek-<x>` NOT prefixed by another
    #: provider's segment (openrouter/deepseek/deepseek-v4.1-flash is
    #: OpenRouter's own vendor-prefixed id, not a native call).
    NATIVE_ID_RE = re.compile(r"([a-z0-9][a-z0-9._-]*/)?deepseek/deepseek-[a-z0-9._-]+")
    #: Any DeepSeek id carrying a "pro" segment (the denied family).
    PRO_ID_RE = re.compile(r"deepseek[a-z0-9._/-]*pro", re.IGNORECASE)

    LITELLM_CONFIG_PATH = ROOT / "configuration" / "litellm" / "config.yaml"

    def _prefixed_by_other_provider(self, text: str, at: int) -> bool:
        """True when the bare `deepseek/<id>` match at `at` is really another
        provider's id written with a separator instead of a slash — the shape
        docs/models.md uses in its tables and prose ('openrouter
        `deepseek/deepseek-v4.1-flash`'). A structured config always spells the
        prefix with the slash, which match.group(1) already covers."""
        return bool(re.search(r"openrouter[\s`'\"|>~=-]*$", text[:at]))

    def _surfaces(self) -> dict:
        """Every machine-readable render and config that can put a DeepSeek id
        on the wire, keyed by a name a failure can point at. Renders come from
        the registry's own render functions (R-worker-01: never a hand-written
        expectation of a render); the committed files are read so drift in a
        file a render does not own (the hand-curated LiteLLM groups, the
        vendored OpenHands profiles, the agent harness) is caught too."""
        reg = self.reg
        combo = registry.render_omniroute(reg)
        surfaces = {
            "render omniroute": json.dumps(combo, sort_keys=True),
            "render litellm": "\n".join(
                registry.render_litellm_blocks(reg, self._litellm_text()).values()),
            "render ide": json.dumps(registry.render_ide(reg), sort_keys=True),
            "render openhands": json.dumps(registry.render_openhands(reg),
                                           sort_keys=True),
            "render models-doc": registry.render_models_doc(reg),
            "configuration/omniroute/combos.json": self._read(
                ROOT / "configuration" / "omniroute" / "combos.json"),
            "configuration/litellm/config.yaml": self._litellm_text(),
            "catalog/ide-models.json": self._read(ROOT / "catalog" / "ide-models.json"),
            "configuration/openhands/tier-profiles.json": self._read(
                ROOT / "configuration" / "openhands" / "tier-profiles.json"),
            "catalog/agent-harness.json": self._read(
                ROOT / "catalog" / "agent-harness.json"),
            "routes.*.legs": json.dumps(
                {rid: r.get("legs") for rid, r in reg["routes"].items()},
                sort_keys=True),
        }
        for path in sorted((ROOT / "openhands").glob("*/*.json")):
            surfaces[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
        return surfaces

    def _read(self, path) -> str:
        return path.read_text(encoding="utf-8")

    def _litellm_text(self) -> str:
        return self.LITELLM_CONFIG_PATH.read_text(encoding="utf-8")

    @property
    def reg(self):
        return load_registry()

    def _rule_ids(self) -> list:
        return [r.get("id") for r in self.reg["policy"]["leg_rules"]]

    # -- 1. the native id --------------------------------------------------

    def test_registry_projects_no_native_deepseek_id_but_flash(self):
        # models.<id>.direct is what the installers and the vendored OpenHands
        # profiles take their wire model from, so a stale native spelling here is
        # the root cause, not a render bug.
        offenders = {}
        for mid, entry in self.reg["models"].items():
            direct = entry.get("direct")
            if not isinstance(direct, dict):
                continue
            native = (direct.get("provider") == "deepseek"
                      or "api.deepseek.com" in str(direct.get("base_url", "")))
            if native and direct.get("model") != self.NATIVE:
                offenders[mid] = direct.get("model")
        self.assertEqual(offenders, {}, "models.<id>.direct sends a non-canonical "
                                        "id to the native DeepSeek API")

    def test_no_route_leg_uses_a_non_canonical_native_deepseek_leg(self):
        for rid, route in self.reg["routes"].items():
            for leg in list(route.get("legs") or []):
                if leg.startswith("deepseek/"):
                    self.assertEqual(leg, self.NATIVE, rid)

    def test_every_vendored_profile_that_calls_deepseek_directly_uses_flash(self):
        for path in sorted((ROOT / "openhands" / "profiles").glob("*.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            if "api.deepseek.com" in str(profile.get("base_url", "")):
                self.assertEqual(profile.get("model"), self.NATIVE,
                                 str(path.relative_to(ROOT)))

    def test_no_surface_sends_a_non_canonical_native_deepseek_id(self):
        for name, text in self._surfaces().items():
            for match in self.NATIVE_ID_RE.finditer(text):
                if match.group(1) or self._prefixed_by_other_provider(
                        text, match.start()):
                    continue  # another provider's vendor-prefixed id
                self.assertEqual(match.group(0), self.NATIVE,
                                 "%s sends %r to the native DeepSeek API"
                                 % (name, match.group(0)))

    # -- 2. deepseek-v4-pro is denied everywhere ---------------------------

    def test_deny_deepseek_pro_rule_exists(self):
        rules = self.reg["policy"]["leg_rules"]
        entry = next((r for r in rules if r.get("id") == "deny-deepseek-pro"), None)
        self.assertIsInstance(entry, dict,
                              "no deny-deepseek-pro rule (ids: %s)" % self._rule_ids())
        self.assertIs(entry["allow"], False)
        self.assertEqual(entry["match"], "*deepseek*pro*")
        self.assertIn("routing-00", entry.get("source", ""))
        self.assertIn("719cee9", entry.get("source", ""))

    def test_deny_deepseek_pro_precedes_every_deepseek_allow_rule(self):
        rules = self.reg["policy"]["leg_rules"]
        try:
            deny_at = [r.get("id") for r in rules].index("deny-deepseek-pro")
        except ValueError:
            self.fail("no deny-deepseek-pro rule (ids: %s)" % self._rule_ids())
        for at, rule in enumerate(rules):
            if rule.get("allow") is True and "deepseek" in str(rule.get("match", "")):
                self.assertLess(deny_at, at,
                                "deny-deepseek-pro must be evaluated before %s"
                                % rule.get("id"))

    def test_leg_rules_deny_the_pro_ids_though_deepseek_is_allowed(self):
        self.assertIs(registry.leg_denied(self.NATIVE, self.reg), False,
                      "the allowed native flash leg must stay allowed")
        # Every spelling of the pro model, including the ones a wildcard allow
        # rule (opencode-zen/*, cc/*) would otherwise have opened: the deny is
        # first in the list precisely so no allow can reach it.
        for leg in ("deepseek/deepseek-v4-pro",
                    "openrouter/deepseek/deepseek-v4-pro",
                    "cheaperinference/deepseek-v4-pro",
                    "opencode-zen/deepseek-v4-pro",
                    "samba/deepseek-v4-pro"):
            with self.subTest(leg=leg):
                self.assertIs(registry.leg_denied(leg, self.reg), True)
                self.assertEqual(
                    (registry.leg_rule_for(leg, self.reg) or {}).get("id"),
                    "deny-deepseek-pro", leg)

    def _pro_surfaces(self) -> dict:
        """The machine surfaces the operator rule is about: docs/models.md is
        prose about the ids, not a config that sends one, so it is scanned for
        the native id above but excluded here."""
        return {k: v for k, v in self._surfaces().items()
                if k != "render models-doc"}

    def test_no_render_or_config_contains_a_pro_deepseek_id(self):
        for name, text in self._pro_surfaces().items():
            found = self.PRO_ID_RE.findall(text)
            self.assertEqual(found, [], "%s carries a DeepSeek pro id: %s"
                             % (name, sorted(set(found))))

    def test_litellm_router_fallbacks_carry_no_deepseek(self):
        # The router's own fallback list is hand-curated, outside every managed
        # block, so the surface scan above would miss a fallback added there.
        block = re.search(r"^router_settings:\n((?:[ #].*\n)*)",
                          self._litellm_text(), re.MULTILINE)
        self.assertIsNotNone(block, "config.yaml has no router_settings block")
        # The prose in there explains why the chains are empty and names the
        # deployment they used to end in; only the settings themselves route.
        settings = "\n".join(line for line in block.group(1).splitlines()
                             if not line.lstrip().startswith("#"))
        self.assertIn("fallbacks: []", settings)
        self.assertNotIn("deepseek", settings.lower())

    def test_real_registry_still_passes_check_with_the_deny_rule(self):
        # rule 9 (_check_leg_rules) must stay clean: a deny rule may not strand
        # a serving leg, and no route may leg into the pro model.
        self.assertEqual(registry.check_registry(self.reg), [])


class DeepSeekNativeEffortLadderTests(unittest.TestCase):
    """DSAMEND item 3, measured live (routing-00, 2026-09-28T10:0xZ, source
    'operator via routing-00 2026-09-28T10:0xZ; Server 719cee9'): 13 one-word
    calls to POST https://api.deepseek.com/chat/completions as
    model=deepseek-flash, max_tokens 16 and 512.

    The wire parameter is `reasoning_effort`, and a deliberately bad value came
    back 422 naming the whole accepted enum — quoted verbatim below, because
    that string IS the vendor's contract for this leg:

        reasoning_effort: unknown variant `zzz`, expected one of `none`,
        `minimal`, `low`, `medium`, `high`, `xhigh`, `ultra`, `max`

    Measured per call (HTTP 200 unless noted), completion_tokens_details.
    reasoning_tokens at max_tokens 512: no param 20 (content 'ok', thinking
    ran), low 18, high 25, max 18, `reasoning_effort: none` → no
    completion_tokens_details at all (thinking off), `thinking:
    {"type": "disabled"}` → same off. So the declared ladder needs no
    re-mapping — every rung is a real wire value — but rung "none" is a LITERAL
    the API must be told, not an omission: sending nothing gets thinking ON.
    """

    #: The vendor's own accepted set, from the 422 above.
    WIRE_ENUM = ("none", "minimal", "low", "medium",
                 "high", "xhigh", "ultra", "max")
    MODEL_ID = "deepseek-flash"

    @property
    def model(self):
        return load_registry()["models"][self.MODEL_ID]

    def test_every_declared_rung_is_a_wire_value_this_api_accepts(self):
        ladder = self.model["effort_ladder"]
        self.assertEqual(ladder, ["none", "low", "high", "max"])
        for rung in ladder:
            self.assertIn(rung, self.WIRE_ENUM,
                          "rung %r is not a reasoning_effort value the native "
                          "API accepts (measured enum: %s)"
                          % (rung, ", ".join(self.WIRE_ENUM)))

    def test_the_off_rung_is_declared_because_omission_is_not_off(self):
        # Removing "none" from this ladder would silently mean something else:
        # callers that express rung none by sending no parameter (render_ide's
        # filter, tools/autoos-agent.py apply_effort_rung) would leave this leg
        # reasoning — measured 20 of 22 completion tokens with no parameter at
        # all. The declared rung is what keeps that trap visible.
        self.assertIn("none", self.model["effort_ladder"])
        self.assertIs(self.model["reasoning"], True)

    def test_the_measured_mapping_stays_recorded_on_the_leg(self):
        # The registry is where a reader looks for what a rung costs on the
        # wire; the measurement may be superseded but never quietly dropped.
        note = self.model.get("$comment", "")
        self.assertIn("reasoning_effort", note)
        self.assertIn("reasoning is ON by default".lower(), note.lower())


class MistralPlanLimitsTests(unittest.TestCase):
    """BRIEF MISTRALFIX (S1-S2, L1-routing 2026-09-28T10:5xZ): a direct probe of
    api.mistral.ai with the operator's key read the x-ratelimit headers per
    model. Four models 429 at **0 requests/minute** on this plan
    (mistral-small-latest, devstral-latest, mistral-medium-latest,
    magistral-medium-latest), mistral-large-latest answers 403 (not on the
    plan at all), and only the codestral pair (125 rpm) and the nemo/ministral
    pair (188 rpm) serve. The gateway's own 7-day log agrees:
    mistral/mistral-small-latest failed 51 of 51 calls.

    Two defects this closes, both asserted over the live registry the renders
    read from:
      1. a measured plan limit that says "cannot serve" was data only — nothing
         read rpm, so the resolver planned a 0-rpm leg as if it worked;
      2. mistral/mistral-small-latest was still a leg of three routes, so a
         real request fell through to a leg that cannot answer.
    """

    #: The probe source string every measured row must carry (D20: a limit is
    #: worthless without naming how it was measured).
    SOURCE = ("L1-routing direct probe 2026-09-28T10:5xZ "
              "x-ratelimit headers")

    @property
    def reg(self):
        return load_registry()

    # -- the measured data -------------------------------------------------

    def test_mistral_small_is_recorded_at_zero_rpm(self):
        entry = self.reg["providers"]["mistral"]["limits"]["mistral-small-latest"]
        self.assertEqual(entry["rpm"], 0)
        self.assertEqual(entry["source"], self.SOURCE)

    def test_mistral_code_is_recorded_at_its_measured_capacity(self):
        # The model that DOES serve stays plannable: a 125 rpm / 625k tpm row
        # must not be written as a deny just because its sibling is dead.
        entry = self.reg["providers"]["mistral"]["limits"]["mistral-code-latest"]
        self.assertEqual(entry["rpm"], 125)
        self.assertEqual(entry["tpm"], 625000)
        self.assertEqual(entry["source"], self.SOURCE)
        self.assertNotIn("plan_available", entry)

    def test_plan_available_false_is_flagged_when_not_a_bool(self):
        # `plan_available: "false"` (a string) would read as truthy at the gate,
        # so the type is a validation problem, not a rendering detail.
        reg = mutated()
        reg["providers"]["mistral"]["limits"]["mistral-small-latest"] = {
            "plan_available": "false", "source": self.SOURCE}
        problems = [p for p in registry.check_registry(reg)
                    if "plan_available" in p]
        self.assertTrue(problems, registry.check_registry(reg))

    def test_a_true_plan_available_flag_is_a_valid_entry(self):
        reg = mutated()
        reg["providers"]["mistral"]["limits"]["mistral-small-latest"][
            "plan_available"] = True
        self.assertEqual(registry.check_registry(reg), [])

    # -- the gate reads the table ------------------------------------------

    def test_plan_dead_reasons_names_the_zero_rpm_leg(self):
        pid, mid = registry.resolve_leg("mistral/mistral-small-latest", self.reg)
        self.assertEqual(registry.plan_dead_reasons(pid, mid, self.reg),
                         ["plan: 0 rpm"])

    def test_plan_dead_reasons_is_empty_for_a_serving_leg(self):
        pid, mid = registry.resolve_leg("mistral/mistral-code-latest", self.reg)
        self.assertEqual(registry.plan_dead_reasons(pid, mid, self.reg), [])

    def test_plan_dead_reasons_is_empty_without_a_limits_row(self):
        # No measurement is not a deny: a provider with no limits table, and a
        # model with no row in one, are both simply ungated.
        reg = self.reg
        self.assertEqual(
            registry.plan_dead_reasons("deepseek", "deepseek-flash", reg), [])
        self.assertIsNone(
            registry.provider_plan_limits("deepseek", "deepseek-flash", reg))
        self.assertIsNone(registry.provider_plan_limits("ghost", "ghost", reg))

    def test_provider_plan_limits_returns_the_measured_row(self):
        entry = registry.provider_plan_limits("mistral", "mistral-small-latest",
                                              self.reg)
        self.assertEqual(entry["rpm"], 0)

    def test_plan_available_false_is_a_dead_reason(self):
        reg = mutated()
        reg["providers"]["mistral"]["limits"]["mistral-small-latest"] = {
            "plan_available": False, "source": self.SOURCE}
        self.assertEqual(
            registry.plan_dead_reasons("mistral", "mistral-small-latest", reg),
            ["plan: plan_available false"])

    # -- the routes no longer point at it ----------------------------------

    def test_no_route_carries_a_mistral_small_leg(self):
        for route_id, route in self.reg["routes"].items():
            self.assertNotIn("mistral/mistral-small-latest",
                             route.get("legs") or [], route_id)

    def test_the_clean_twins_head_on_native_deepseek(self):
        # DSBACK (2026-09-28) put the native DeepSeek leg back at the head of
        # both -clean twins; with the Mistral leg gone it is the only head.
        # Checked here because the invariant below is only interesting while
        # this head is live.
        for route_id in ("t2-worker-clean", "t3-driver-clean"):
            self.assertEqual(self.reg["routes"][route_id]["legs"][0],
                             "deepseek/deepseek-flash", route_id)
            self.assertEqual(
                registry.plan_dead_reasons("deepseek", "deepseek-flash", self.reg),
                [], route_id)

    def test_t3_driver_keeps_the_codestral_mistral_leg(self):
        # Only the dead model left the route: codestral measures 200/125 rpm on
        # this plan and is still t3-driver's head.
        self.assertEqual(self.reg["routes"]["t3-driver"]["legs"][0],
                         "mistral/mistral-code-latest")

    # -- the invariant ------------------------------------------------------

    def test_every_route_that_serves_traffic_has_at_least_one_live_leg(self):
        """A combo the gateway offers must be able to answer at least one of its
        legs: not denied by policy.leg_rules, not flagged unavailable, not
        client-bound, and not dead on the plan (0 rpm / plan_available false).

        A route with no gateway-servable leg at all is out of scope — it gets no
        combo, so it serves no traffic (t1-orchestrator-clean and the other
        fully-flagged routes). That is the difference between "marked
        unavailable" and "routed into a dead leg", and only the latter is a
        defect.
        """
        reg = self.reg
        dead = []
        for route_id in sorted(registry.servable_route_ids(reg)):
            if not live_legs(reg, route_id):
                dead.append("%s: %s" % (route_id,
                                        registry.gateway_legs(
                                            reg["routes"][route_id], reg)))
        self.assertEqual(dead, [])


class MistralReplaceTests(unittest.TestCase):
    """BRIEF MISTRALFIX2 (S1, operator via L1-main 2026-09-28 11:5xZ):
    mistral-small does not work — replace it with Mistral Codestral wherever it
    was a leg; if both mistral-code-latest and codestral-latest answer, use
    mistral-code-latest. Measured through the gateway (max_tokens 4096, 3 calls
    each, evidence work/L1-routing/MISTRALREPL.probe.jsonl):
      mistral/mistral-code-latest  3/3 200, p50 0.3 s
      mistral/codestral-latest     3/3 200, p50 0.3 s
      mistral/mistral-small-latest 0/3 (429)
    """

    GATEWAY_SOURCE = ("L1-routing gateway probe 2026-09-28T11:5xZ "
                      "(MISTRALREPL.probe.jsonl)")
    CLEAN_TWINS = ("t2-worker-clean", "t3-driver-clean")

    @property
    def reg(self):
        return load_registry()

    # -- the replacement leg in the twins the brief names --------------------

    def test_the_clean_twins_do_not_carry_mistral_code(self):
        """BRIEF MISTRALFIX2 step 1 asks for mistral/mistral-code-latest at the
        position mistral-small held in t2-worker-clean / t3-driver-clean, and for
        each twin to have two live legs. Measured 2026-09-28 in this sandbox:
        that leg cannot be added — `python3 tools/registry.py validate` exits 1
        with 'privacy: <route> leg mistral/mistral-code-latest model trains on
        prompts' for both twins (spec 3.1 rule 3 / `private_safe`: the model
        record carries `trains_on_prompts: true`, its free pool trains). These
        are the routes a `privacy=sensitive` card lands on (tests/test_autoos_
        spawner.py), so the swap would send private prompts to a training pool —
        and `tests/linux/33-documentation.sh` independently bans `mistral/
        mistral-code` in any `-clean` combo. So the twins keep MISTRALFIX's legs
        and one live leg each, and the operator's replacement lands as the
        registered tested alternative (codestral) plus the probe record. This
        test is the guard: a later lane that adds the leg fails here rather than
        silently shipping a training leg into a no-training route.
        """
        reg = self.reg
        for route_id, route in reg["routes"].items():
            if route_id.endswith("-clean") and \
                    route_id not in registry.CLEAN_ROUTE_EXEMPTIONS:
                self.assertNotIn("mistral/mistral-code-latest",
                                 route.get("legs") or [], route_id)
        for route_id in self.CLEAN_TWINS:
            safe, reason = registry.private_safe("mistral", "mistral-code-latest",
                                                 reg)
            self.assertFalse(safe, route_id)
            self.assertEqual(reason, "model trains on prompts", route_id)

    def test_the_clean_twins_keep_their_head_live_leg(self):
        # One live leg, and it is the native DeepSeek head: the twins still
        # serve, which is what the route-liveness invariant needs.
        reg = self.reg
        for route_id in self.CLEAN_TWINS:
            self.assertEqual(live_legs(reg, route_id),
                             ["deepseek/deepseek-flash"], route_id)
            self.assertEqual(self.reg["routes"][route_id]["legs"][0],
                             "deepseek/deepseek-flash", route_id)

    # -- the registered tested alternative ----------------------------------

    def test_codestral_is_registered_with_its_measured_limits(self):
        entry = self.reg["providers"]["mistral"]["limits"]["codestral-latest"]
        self.assertEqual(entry["rpm"], 125)
        self.assertEqual(entry["tpm"], 625000)
        self.assertIn("10:5xZ direct", entry["source"])
        self.assertIn(self.GATEWAY_SOURCE, entry["source"])
        self.assertIn("codestral-latest", self.reg["models"])

    def test_codestral_counts_as_training_until_sourced(self):
        # Operator 2026-09-28T12:0xZ: "Mistral trains -> never in -clean tiers".
        # Nobody sourced that codestral does NOT train, so the record says it
        # does and private_safe() keeps it out of every -clean route.
        self.assertIs(self.reg["models"]["codestral-latest"].get("trains_on_prompts"), True)
        safe, reason = registry.private_safe("mistral", "codestral-latest", self.reg)
        self.assertFalse(safe)
        self.assertEqual(reason, "model trains on prompts")

    def test_mistral_comment_counts_three_limits_rows(self):
        note = self.reg["providers"]["mistral"]["$comment"]
        self.assertNotIn("Only the two models", note)
        self.assertNotIn("mistral-large/codestral/", note)
        self.assertIn("Only the three models", note)

    def test_codestral_is_not_a_leg_of_any_route(self):
        for route_id, route in self.reg["routes"].items():
            self.assertNotIn("mistral/codestral-latest",
                             route.get("legs") or [], route_id)

    def test_the_gateway_probe_is_recorded_in_the_registry(self):
        # A measurement that lives only in a probe file is data, not a record:
        # the provider comment is where a later lane reads what was measured.
        note = self.reg["providers"]["mistral"]["$comment"]
        self.assertIn("MISTRALREPL.probe.jsonl", note)

    # -- the rest of the registry agrees ------------------------------------

    def test_no_route_carries_a_mistral_small_leg(self):
        for route_id, route in self.reg["routes"].items():
            self.assertNotIn("mistral/mistral-small-latest",
                             route.get("legs") or [], route_id)

    def test_t3_driver_has_exactly_one_mistral_code_leg(self):
        # mistral-code-latest is t3-driver's head already; the swap must not
        # duplicate it into the body of the same route.
        legs = self.reg["routes"]["t3-driver"]["legs"]
        self.assertEqual(legs.count("mistral/mistral-code-latest"), 1)

    def test_real_registry_passes_check_after_the_swap(self):
        self.assertEqual(registry.check_registry(self.reg), [])


if __name__ == "__main__":
    unittest.main()
