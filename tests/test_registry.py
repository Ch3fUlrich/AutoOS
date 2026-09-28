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
import importlib.util
import json
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
ALLOWED_LIMIT_KEYS = {"rpm", "rpd", "tpm", "tpd", "source"}


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
    """Rule 3: -clean routes only use available legs whose provider does not train."""

    def test_clean_route_with_a_training_leg_is_flagged(self):
        reg = mutated()
        reg["providers"]["mistral"]["trains_on_prompts"] = True
        problems = registry.check_registry(reg)
        self.assertIn("privacy: t2-worker-clean leg mistral/mistral-small-latest trains on prompts",
                      problems)

    def test_allow_training_does_not_exempt_the_route(self):
        # review-a3: spec 3.1 has no allow_training escape; a clean route that
        # carries one is still checked (fail closed).
        reg = mutated()
        reg["providers"]["mistral"]["trains_on_prompts"] = True
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
        reg["providers"]["mistral"]["trains_on_prompts"] = None
        problems = registry.check_registry(reg)
        self.assertTrue(any("privacy: t2-worker-clean" in p for p in problems), problems)

    def test_clean_route_with_a_free_tier_leg_is_flagged(self):
        # PRIV brief 2026-09-26: a free-tier provider is never private-safe,
        # even one whose own trains_on_prompts is false (the found bug: a
        # free pool was treated as "clean" because only trains_on_prompts
        # was checked, never tier).
        reg = mutated()
        reg["providers"]["mistral"]["tier"] = "free"
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p and "mistral/mistral-small-latest" in p
               for p in problems), problems)

    def test_clean_route_with_a_training_model_override_is_flagged(self):
        # A model-level trains_on_prompts: true overrides an otherwise-clean
        # paid provider (mistral-code-latest's real-world case).
        reg = mutated()
        reg["models"]["mistral-small-latest"]["trains_on_prompts"] = True
        problems = registry.check_registry(reg)
        self.assertTrue(
            any("privacy: t2-worker-clean" in p and "mistral/mistral-small-latest" in p
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


if __name__ == "__main__":
    unittest.main()
