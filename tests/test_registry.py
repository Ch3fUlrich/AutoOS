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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
REGISTRY_TOOL = ROOT / "tools" / "registry.py"


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

    def test_cli_validate_succeeds_and_confirm_no_drift(self):
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
        # unavailable_legs-flagged openrouter leg.
        reg = mutated()
        reg["providers"]["openrouter"]["trains_on_prompts"] = True
        self.assertIn("openrouter/deepseek/deepseek-v4.1-flash",
                      reg["routes"]["t2-worker-clean"]["unavailable_legs"])
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

    def test_validate_reports_drift_for_a_valid_but_stale_file(self):
        reg = mutated()
        reg["version"] = "2026-01-01"  # 'version' may hold a date; check still passes
        self.assertEqual(registry.check_registry(reg), [])
        path = self._write(reg)
        try:
            proc = self._run("validate", "--registry", path)
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("drift:", proc.stdout)



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

if __name__ == "__main__":
    unittest.main()
