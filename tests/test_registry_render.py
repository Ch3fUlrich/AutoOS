#!/usr/bin/env python3
"""Unit tests for tools/registry.py's `render omniroute` subcommand (routing v2
spec section 3.2 phase 1, task A4a): configuration/omniroute/combos.json rendered
from catalog/ai-registry.json, gated on semantic equality with today's committed
file. docs/plans/2026-09-25-registry-mapping.md section 10 documents the mapping
and its two intentional equality exceptions ($comment, array order).

Path-independent: everything is anchored on ROOT = Path(__file__).resolve().parent.parent,
never on the current working directory or a hard-coded home path (this repo is public,
AGENTS.md rule 1).

Run directly (`python3 tests/test_registry_render.py`), never via `unittest discover`
- the suite has to be runnable on a machine where nothing is installed (AGENTS.md
section 5).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
LITELLM_CONFIG_PATH = ROOT / "configuration" / "litellm" / "config.yaml"
IDE_MODELS_PATH = ROOT / "catalog" / "ide-models.json"
TIER_PROFILES_PATH = ROOT / "configuration" / "openhands" / "tier-profiles.json"
MODELS_DOC_PATH = ROOT / "docs" / "models.md"
SYNC_ROUTER_TIERS_TOOL = ROOT / "tools" / "sync-router-tiers.py"
SYNC_OPENHANDS_PROFILES_TOOL = ROOT / "tools" / "sync-openhands-profiles.py"
CHECK_PROVIDER_REGISTRY_HELPER = ROOT / "tests" / "helpers" / "check-provider-registry.py"
REGISTRY_TOOL = ROOT / "tools" / "registry.py"


def _load_tool():
    """Import tools/registry.py by path (its name is not a valid module identifier)."""
    spec = importlib.util.spec_from_file_location("autoos_registry", REGISTRY_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load_tool()


def _load_sync_openhands_profiles_tool():
    """Import tools/sync-openhands-profiles.py by path (its name is not a
    valid module identifier) - the same technique _load_tool() above uses."""
    spec = importlib.util.spec_from_file_location(
        "autoos_sync_openhands_profiles", SYNC_OPENHANDS_PROFILES_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def real_registry() -> dict:
    return load_json(REGISTRY_PATH)


def real_combos() -> dict:
    return load_json(COMBOS_PATH)


def real_ide_models() -> dict:
    return load_json(IDE_MODELS_PATH)


def real_litellm_config() -> str:
    return LITELLM_CONFIG_PATH.read_text(encoding="utf-8")


def real_tier_profiles() -> dict:
    return load_json(TIER_PROFILES_PATH)


def real_models_doc() -> str:
    return MODELS_DOC_PATH.read_text(encoding="utf-8")


def _strip_jsonc(text: str) -> str:
    """opencode.jsonc is JSON with whole-line `//` comments (the same strip
    tests/test_sync_ide_models.py does - that module is not importable from
    here, the suites run standalone)."""
    return re.sub(r"(?m)^\s*//.*$", "", text)


def row_for(block_text: str, route_id: str) -> str:
    """The one models-doc table row whose first cell is `` `<route_id>` ``,
    or fails the calling test loudly if there is none/more than one."""
    marker = "| `%s`" % route_id
    matches = [line for line in block_text.splitlines() if line.startswith(marker)]
    assert len(matches) == 1, "expected exactly one row for %r, found %d" % (route_id, len(matches))
    return matches[0]


def run_helper(path: Path, *args, timeout=60):
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )


def run_cli(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(REGISTRY_TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )


def write_registry(reg) -> str:
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="registry-render-test-", delete=False, encoding="utf-8")
    json.dump(reg, tmp)
    tmp.close()
    return tmp.name


def synthetic_gateway_registry() -> dict:
    """A tiny registry exercising every gateway_legs() drop rule at once: a
    route-level unavailable_legs gate, a policy.leg_rules deny, a provider whose
    available is false, and a model carrying client_bound, plus two live legs
    either side so the surviving order is observable."""
    return {
        "providers": {
            "clean": {"id": "clean"},
            "dead": {"id": "dead", "available": False},
        },
        "models": {
            "first": {"id": "first"},
            "second": {"id": "second"},
            "bound": {"id": "bound", "client_bound": "opencode"},
            "deadmodel": {"id": "deadmodel"},
        },
        "policy": {"leg_rules": [
            {"id": "deny-locked", "match": "locked/*", "allow": False},
        ]},
        "routes": {
            "mix": {
                "id": "mix",
                "class": "cheap",
                "strategy": "priority",
                "legs": ["clean/first", "locked/one", "dead/deadmodel",
                         "clean/bound", "clean/second"],
                "surfaces": {"omniroute": {"context_declared": "8k"}},
            },
        },
    }


class RenderMatchesTodayTests(unittest.TestCase):
    """Render of the real registry equals today's combos.json semantically."""

    def test_render_matches_committed_combos_semantically(self):
        problems = registry.omniroute_diff(registry.render_omniroute(real_registry()), real_combos())
        self.assertEqual(problems, [])

    def test_render_carries_the_spec_3_2_generated_marker(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertIn("generated from catalog/ai-registry.json", rendered["$comment"])
        self.assertIn("do not edit", rendered["$comment"])

    def test_generated_marker_is_new_not_borrowed_from_the_committed_file(self):
        # today's hand-edited $comment predates spec 3.2's marker line - if this
        # ever appears in the committed file, omniroute_diff's "$comment is
        # entirely ignored" exception would be masking a real accidental match.
        self.assertNotIn("generated from catalog/ai-registry.json",
                         json.dumps(real_combos()["$comment"]))

    def test_retired_ids_match_todays_file(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertEqual(sorted(rendered["retired"]), sorted(real_combos()["retired"]))

    def test_operator_flagged_dead_leg_is_not_mirrored_into_combos(self):
        # OR1a: a leg the registry marks unavailable/denied/client-bound must
        # not reach a gateway render, or the live gateway serves a dead leg.
        rendered = registry.render_omniroute(real_registry())
        combos_by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertNotIn("opencode-zen/deepseek-v4.1-flash",
                         combos_by_name["t2-worker-clean"]["models"])
        # FREEWIRE 2026-09-30: allow-groq-gpt-oss re-opened this leg on a
        # single-tool-call probe, so it IS served now (deny-groq used to gate it).
        self.assertIn("groq/openai/gpt-oss-120b",
                      combos_by_name["t2-worker"]["models"])
        # the OpenRouter BYOK gpt-oss-120b leg is gated (measured 401,
        # credits exhausted 2026-09-27), so it does not reach the combo.
        self.assertNotIn("openrouter/openai/gpt-oss-120b",
                         combos_by_name["t2-worker"]["models"])
        # FREEWIRE 2026-09-30: the DSMAX provider-level gate moved to the route
        # level (providers.openrouter.available is now true for its ':free'
        # ids); the paid openrouter deepseek leg stays route-gated.
        self.assertNotIn("openrouter/deepseek/deepseek-v4.1-flash",
                         combos_by_name["t2-worker"]["models"])
        # CIGREEN: expectation moved by ba73f1cf (TASK2 re-added the gemini
        # head) + 20c4a816 (provider re-open): gemini/gemini-3.8-flash is a
        # live servable head of t2-worker again, so the combo correctly
        # contains it - the FREEWIRE-era removal is superseded (D-255 allows
        # 3.8 Flash).
        self.assertIn("gemini/gemini-3.8-flash",
                      combos_by_name["t2-worker"]["models"])

    def test_paid_and_auto_routes_have_no_combo(self):
        # t2-worker-paid/t3-driver-paid (LiteLLM-only) and
        # auto/auto-smart/auto-cheap (OmniRoute's dynamic strategy) carry
        # legs: [] in the registry and have no combos.json counterpart.
        # MUSEAPI 2026-09-27 moved t1-orchestrator-paid out of that set: it
        # declares a servable leg now, so it renders a combo - test_the_legless
        # _route_renders_no_combo below pins the rule that made it an exception.
        rendered = registry.render_omniroute(real_registry())
        names = {c["name"] for c in rendered["combos"]}
        for absent in ("t2-worker-paid", "t3-driver-paid",
                      "auto", "auto/smart", "auto/cheap"):
            self.assertNotIn(absent, names)

    def test_the_legless_route_renders_no_combo(self):
        # The rule, kept general: a route with legs: [] is deliberately
        # LiteLLM-only and never becomes a combo, however it is spelled.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t1-orchestrator-paid"]["legs"] = []
        names = {c["name"] for c in registry.render_omniroute(reg)["combos"]}
        self.assertNotIn("t1-orchestrator-paid", names)


class GatewayRefTests(unittest.TestCase):
    """AGYID/AGYCANON: the live OmniRoute catalog flipped antigravity's ids back
    to canonical antigravity/* (re-measured /v1/models 2026-09-30: 19
    antigravity/* rows, zero agy/*), so providers.antigravity.model_prefix is
    null and an omniroute render emits the registry spelling unchanged - the
    exact ids apply's validation checks. The model_prefix mechanism itself
    stays live for providers that genuinely diverge (scaleway -> scw)."""

    def test_render_omniroute_renders_antigravity_by_its_canonical_id(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted every
        # antigravity leg from the bands, provider available:false, revisit
        # 2026-10-15): no combo can carry one, so the AGYCANON pin moves to
        # the unit that owns it - gateway_ref emits the canonical
        # antigravity spelling (no agy/ rewrite while
        # providers.antigravity.model_prefix is null) - and the real render
        # is pinned to contain no resurrected agy/ spelling.
        self.assertIsNone(
            real_registry()["providers"]["antigravity"].get("model_prefix"))
        self.assertEqual(
            registry.gateway_ref("antigravity/gemini-3.7-flash-high",
                                 real_registry()),
            "antigravity/gemini-3.7-flash-high")
        rendered = registry.render_omniroute(real_registry())
        self.assertNotIn("agy/", json.dumps(rendered))

    def test_render_omniroute_still_applies_a_declared_model_prefix(self):
        # The mechanism AGYID added (and this AGYCANON change must not break):
        # scaleway's free grant renders under its declared scw prefix.
        rendered = registry.render_omniroute(real_registry())
        by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("scw/mistral-small-3.2-24b-instruct-2506",
                      by_name["t3-driver"]["models"])

    def test_render_omniroute_leaves_other_providers_unchanged(self):
        # Providers without a model_prefix keep their registry spelling in
        # the render (mistral has none; deepseek's omniroute_id is its registry
        # id too, so its leg renders under the same spelling it is declared
        # with - DSBACK 2026-09-28 put that leg back in the renders).
        rendered = registry.render_omniroute(real_registry())
        by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("mistral/mistral-code-latest",
                      by_name["t3-driver"]["models"])
        # FREEWIRE 2026-09-30: the gemini head was removed; groq (no prefix)
        # keeps its registry spelling in the render.
        self.assertIn("groq/qwen/qwen3.8-27b", by_name["t2-worker"]["models"])
        self.assertIn("deepseek/deepseek-flash",
                      by_name["t2-worker-clean"]["models"])

    def test_registry_legs_keep_their_own_spelling(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted the
        # antigravity legs from the bands, provider available:false). Pins the
        # same rule on a surviving multi-segment leg: the registry spelling is
        # kept verbatim and resolve_leg still splits at the first '/'.
        self.assertIn("openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                      real_registry()["routes"]["t2-worker"]["legs"])
        # resolve_leg still splits the registry spelling at the first '/'.
        self.assertEqual(
            registry.resolve_leg("openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                                 real_registry()),
            ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"))

    def test_gateway_ref_returns_the_leg_when_there_is_no_model_prefix(self):
        self.assertEqual(
            registry.gateway_ref("openrouter/deepseek/deepseek-v4.1-flash",
                                 real_registry()),
            "openrouter/deepseek/deepseek-v4.1-flash")

    def test_gateway_ref_leaves_an_unresolvable_leg_alone(self):
        self.assertEqual(registry.gateway_ref("ghost/provider", real_registry()),
                         "ghost/provider")


class RenderDeterminismTests(unittest.TestCase):
    """A second render changes nothing (spec 11: idempotence)."""

    def test_two_in_process_renders_are_byte_for_byte_equal(self):
        first = registry.render_json(registry.render_omniroute(real_registry()))
        second = registry.render_json(registry.render_omniroute(real_registry()))
        self.assertEqual(first, second)

    def test_cli_two_runs_write_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out1, out2 = Path(d) / "a.json", Path(d) / "b.json"
            for out in (out1, out2):
                proc = run_cli("render", "omniroute", "--out", str(out))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
            self.assertTrue(out1.read_bytes().endswith(b"\n"))


class MalformedComboTests(unittest.TestCase):
    """review-b5a4: a nameless combo (or a bare string) in the compared file
    was dropped before comparing, so --check passed with extra legs."""

    def test_a_nameless_combo_is_a_difference(self):
        combos = real_combos()
        for extra in ({"strategy": "priority", "models": ["x/y"]}, "stray"):
            doc = dict(combos, combos=list(combos["combos"]) + [extra])
            self.assertNotEqual(registry.omniroute_diff(registry.render_omniroute(real_registry()), doc), [])


class ChangedLegFailsCheckTests(unittest.TestCase):
    """--check exits 1 and names the combo when a leg differs from combos.json."""

    def test_changed_leg_exits_one_and_names_the_combo(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t3-driver-clean"]["legs"][0] = "ghost-provider/ghost-model"
        path = write_registry(reg)
        try:
            proc = run_cli("render", "omniroute", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("combos.t3-driver-clean", proc.stdout)

    def test_unmodified_registry_check_exits_zero_on_the_real_files(self):
        proc = run_cli("render", "omniroute", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)

    def test_missing_combos_file_exits_one(self):
        proc = run_cli("render", "omniroute", "--check",
                       "--combos", "/nonexistent/combos.json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


class UnknownRenderTargetTests(unittest.TestCase):
    def test_unknown_target_exits_two(self):
        proc = run_cli("render", "frobnicate")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_unknown_top_level_command_exits_two(self):
        proc = run_cli("frobnicate")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class ExistingCommandsStillWorkTests(unittest.TestCase):
    """Adding `render` must not disturb `check`/`validate` (the brief's own
    'keep all existing behaviour' rule)."""

    def test_check_subcommand_still_exits_zero(self):
        proc = run_cli("check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_validate_subcommand_still_exits_zero(self):
        proc = run_cli("validate", timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class LitellmRenderMatchesTodayTests(unittest.TestCase):
    """Render of the real registry equals today's configuration/litellm/config.yaml
    AUTOOS-MANAGED blocks, byte for byte (task A4b; docs/plans/2026-09-25-registry-
    mapping.md section 11 - unlike render omniroute above, no documented equality
    exception is needed here)."""

    def test_render_matches_committed_config_byte_for_byte(self):
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        self.assertEqual(registry.litellm_diff(rendered, real_litellm_config()), [])

    def test_every_registry_managed_tier_is_rendered(self):
        # The managed set is derived from the registry, not a hand-kept pair:
        # every route that declares legs and keeps at least one LiteLLM-servable
        # leg after gateway_legs()/GATEWAY_ONLY gets a block. t1-orchestrator
        # used to be hand-kept, then fail-closed 2026-09-27 (Zen client-bound,
        # OpenRouter off) and lost its block; t4-rag and t2-worker-clean never
        # had markers.
        reg = real_registry()
        rendered = registry.render_litellm_blocks(reg, real_litellm_config())
        sync = registry._load_sync_router_tiers()
        self.assertEqual(set(rendered), set(sync.managed_tiers(reg)))
        for managed in ("t2-worker", "t2-worker-clean", "t4-rag"):
            self.assertIn(managed, rendered)

    def test_a_route_with_no_litellm_servable_leg_gets_no_block(self):
        # An all-gateway-only route (opus-4-6) and the samba one-leg routes
        # (provider available:false) render no block at all - the same shape
        # render_omniroute() gives an all-dead route, not an empty model list.
        # t1-orchestrator-free-only is NOT gone: T1FREE gave it a gemini
        # servable leg. t3-driver-free-only left this set when FREEAI gave it
        # a servable free_ai/qwen7b leg.
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        for gone in ("opus-4-6", "samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertNotIn(gone, rendered)

    def test_a_legless_hand_group_is_never_rendered(self):
        # t2-worker-paid/t3-driver-paid declare no legs; they are hand-curated
        # fallback chains and must stay outside the AUTOOS-MANAGED markers.
        # (t1-orchestrator-paid was one of them until MUSEAPI 2026-09-27 gave
        # it a leg - see test_the_legged_paid_route_is_rendered.)
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        for paid in ("t2-worker-paid", "t3-driver-paid"):
            self.assertNotIn(paid, rendered)

    def test_the_legged_paid_route_is_rendered(self):
        # MUSEAPI 2026-09-27: t1-orchestrator-paid's LiteLLM group used to 404
        # (no model_name anywhere in config.yaml, no legs in the registry).
        # With meta_api/muse-spark-1.3-contributor as its leg the registry owns
        # the block, so the render must produce it...
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        block = rendered["t1-orchestrator-paid"]
        self.assertIn("model: openai/muse-spark-1.3-contributor", block)
        self.assertIn("api_base: https://api.meta.ai/v1", block)
        self.assertIn("api_key: os.environ/META_API_KEY", block)
        self.assertIn("  # AUTOOS-MANAGED-START t1-orchestrator-paid\n", block)
        self.assertIn("  # AUTOOS-MANAGED-END t1-orchestrator-paid", block)

        # ...and the rule behind the old test survives: strip the legs and the
        # block is gone again, marker and all.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t1-orchestrator-paid"]["legs"] = []
        self.assertNotIn("t1-orchestrator-paid",
                         registry.render_litellm_blocks(reg, real_litellm_config()))

    def test_gateway_only_leg_is_dropped_not_silently_kept_or_missing(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted exactly the
        # antigravity fixture leg from t2-worker). The drop-behavior is now
        # pinned with a synthetic gateway-only leg: cc sits in
        # tools/sync-router-tiers.py GATEWAY_ONLY, so with its provider
        # re-opened the leg IS served by the gateway combo yet still dropped
        # from the LiteLLM mirror - dropped, not silently kept - while a real
        # sibling leg stays mirrored and the fixture leg stays declared.
        reg = copy.deepcopy(real_registry())
        reg["providers"]["cc"]["available"] = True
        leg = "cc/claude-opus-4-6"
        reg["routes"]["t2-worker"]["legs"] = (
            reg["routes"]["t2-worker"]["legs"] + [leg])
        legs = reg["routes"]["t2-worker"]["legs"]
        self.assertIn(leg, legs)
        rendered = registry.render_litellm_blocks(reg, real_litellm_config())
        self.assertNotIn("claude-opus-4-6", rendered["t2-worker"])
        combos = {c["name"]: c for c in registry.render_omniroute(reg)["combos"]}
        self.assertIn(leg, combos["t2-worker"]["models"])
        self.assertIn("scaleway/mistral-small-3.2-24b-instruct-2506",
                      rendered["t2-worker"])


class StaleLitellmBlockIsDriftTests(unittest.TestCase):
    """PROV review: litellm_diff() only walked the rendered tiers, so a managed
    block the registry no longer produces stayed in config.yaml with the
    `render litellm --check` gate green. A stale block is drift and must be
    named, matching tools/sync-router-tiers.py's own --check."""

    STALE = (
        "\n  # AUTOOS-MANAGED-START dead-tier\n"
        "  - model_name: dead-tier\n"
        "    litellm_params:\n"
        "      model: groq/ghost\n"
        "      api_key: os.environ/GROQ_API_KEY\n"
        "  # AUTOOS-MANAGED-END dead-tier\n"
    )

    def test_a_stale_managed_block_is_reported(self):
        config = real_litellm_config() + self.STALE
        rendered = registry.render_litellm_blocks(real_registry(), config)
        self.assertIn("dead-tier", registry.litellm_diff(rendered, config))

    def test_the_committed_config_has_no_stale_block(self):
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        self.assertEqual(registry.litellm_diff(rendered, real_litellm_config()), [])


class LitellmRenderDeterminismTests(unittest.TestCase):
    """A second render changes nothing (spec 11: idempotence)."""

    def test_two_in_process_renders_are_identical(self):
        first = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        second = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        self.assertEqual(first, second)

    def test_cli_two_runs_write_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out1, out2 = Path(d) / "a.txt", Path(d) / "b.txt"
            for out in (out1, out2):
                proc = run_cli("render", "litellm", "--out", str(out))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
            self.assertTrue(out1.read_bytes().endswith(b"\n"))


class ChangedLegFailsLitellmCheckTests(unittest.TestCase):
    """--check exits 1 and names the tier when a leg differs from config.yaml."""

    def test_changed_leg_exits_one_and_names_the_tier(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t3-driver"]["legs"][0] = "ghost-provider/ghost-model"
        path = write_registry(reg)
        try:
            proc = run_cli("render", "litellm", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("differs: t3-driver", proc.stdout)

    def test_unmodified_registry_check_exits_zero_on_the_real_files(self):
        proc = run_cli("render", "litellm", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)

    def test_missing_config_file_exits_one(self):
        proc = run_cli("render", "litellm", "--check",
                       "--config", "/nonexistent/config.yaml")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


class ExistingSyncRouterTiersStillWorkTests(unittest.TestCase):
    """tools/sync-router-tiers.py must stay a working tool - the brief's own 'it
    stays a working tool, its tests must pass' rule. Since OR1a its default
    source (the registry) mirrors the same gateway_legs() render_omniroute()
    writes, so --check with no override exits 0 on the committed files."""

    def test_sync_router_tiers_check_still_exits_zero(self):
        proc = run_helper(SYNC_ROUTER_TIERS_TOOL, "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_provider_registry_consistency_helper_still_passes(self):
        proc = run_helper(CHECK_PROVIDER_REGISTRY_HELPER)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class IdeRenderMatchesTodayTests(unittest.TestCase):
    """Render of the real registry equals today's catalog/ide-models.json
    semantically (task A4c; docs/plans/2026-09-25-registry-mapping.md section 12
    documents the mapping and its one intentional equality exception: $comment,
    same as render omniroute's - section 10). Unlike combos.json's `combos`/
    `retired` arrays, `models[]` order IS semantic here (catalog/ide-models.json's
    own top-level comment: "List order = picker order on every surface"), so
    render_ide() reproduces it via IDE_MODEL_ORDER rather than treating it as an
    exception."""

    def test_render_matches_committed_ide_models_semantically(self):
        problems = registry.ide_diff(registry.render_ide(real_registry()), real_ide_models())
        self.assertEqual(problems, [])

    def test_render_carries_the_spec_3_2_generated_marker(self):
        rendered = registry.render_ide(real_registry())
        self.assertIn("generated from catalog/ai-registry.json", rendered["$comment"])
        self.assertIn("do not edit", rendered["$comment"])

    def test_committed_file_carries_the_generated_marker(self):
        # A5f: catalog/ide-models.json is now the renderer's byte-exact output,
        # so its $comment IS the generated marker (phase 1 kept the hand-written
        # glossary; the mapping doc sections 3/9 own that prose now).
        self.assertIn("generated from catalog/ai-registry.json",
                      json.dumps(real_ide_models()["$comment"]))

    def test_committed_file_is_byte_identical_to_a_fresh_render(self):
        # A5f (spec 3.2 D11): catalog/ide-models.json is generated, not
        # hand-written. The committed bytes must equal a fresh
        # `python3 tools/registry.py render ide --out <tmp>` render exactly
        # (not only semantically); regenerate with
        # `python3 tools/registry.py render ide --out catalog/ide-models.json`.
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "ide-render.json"
            proc = run_cli("render", "ide", "--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                out.read_bytes(), IDE_MODELS_PATH.read_bytes(),
                "catalog/ide-models.json drifts from a fresh render ide "
                "(run: python3 tools/registry.py render ide "
                "--out catalog/ide-models.json)")

    def test_render_order_matches_todays_picker_order(self):
        rendered = registry.render_ide(real_registry())
        self.assertEqual([m["id"] for m in rendered["models"]],
                         [m["id"] for m in real_ide_models()["models"]])

    def test_every_registry_route_is_covered_by_ide_model_order(self):
        self.assertEqual(set(registry.IDE_MODEL_ORDER), set(real_registry()["routes"]))

    def test_a_route_with_no_omniroute_or_litellm_surface_is_out_of_scope(self):
        # spark-1.3-contributor's surfaces.openhands.direct_profile (mapping doc
        # section 6) is not an omniroute/litellm surface - render_ide must not
        # choke on it, and must still render the route from its omniroute
        # surface. Spark fails closed on the real registry (Zen client-bound,
        # OpenRouter off since DSMAX 2026-09-27), so exercise it on a copy
        # with the provider re-funded: the extra surface key is still there
        # and the route still renders from its omniroute surface.
        reg = copy.deepcopy(real_registry())
        reg["providers"]["openrouter"]["available"] = True
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertEqual(by_id["spark-1.3-contributor"]["surfaces"], {"omniroute": ["opencode", "zed", "openhands"]})


class IdeRenderDeterminismTests(unittest.TestCase):
    """A second render changes nothing (spec 11: idempotence)."""

    def test_two_in_process_renders_are_byte_for_byte_equal(self):
        first = registry.render_json(registry.render_ide(real_registry()))
        second = registry.render_json(registry.render_ide(real_registry()))
        self.assertEqual(first, second)

    def test_cli_two_runs_write_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out1, out2 = Path(d) / "a.json", Path(d) / "b.json"
            for out in (out1, out2):
                proc = run_cli("render", "ide", "--out", str(out))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
            self.assertTrue(out1.read_bytes().endswith(b"\n"))


class ChangedFieldFailsIdeCheckTests(unittest.TestCase):
    """--check exits 1 and names the model when a field differs from
    catalog/ide-models.json."""

    def test_changed_context_exits_one_and_names_the_model(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t3-driver"]["surfaces"]["omniroute"]["context"] = 1
        path = write_registry(reg)
        try:
            proc = run_cli("render", "ide", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("models.t3-driver", proc.stdout)

    def test_reordered_registry_render_still_matches_by_content(self):
        # routes is a dict, so registry key order never drives render_ide()'s
        # output order (IDE_MODEL_ORDER does) - reordering the dict on disk
        # must not itself count as drift.
        reg = copy.deepcopy(real_registry())
        reordered = dict(reversed(list(reg["routes"].items())))
        reg["routes"] = reordered
        path = write_registry(reg)
        try:
            proc = run_cli("render", "ide", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_unmodified_registry_check_exits_zero_on_the_real_files(self):
        proc = run_cli("render", "ide", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)

    def test_missing_ide_models_file_exits_one(self):
        proc = run_cli("render", "ide", "--check",
                       "--ide-models", "/nonexistent/ide-models.json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


class MissingRouteFailsIdeRenderTests(unittest.TestCase):
    """render_ide() fails loudly, naming the id, when a registry route
    IDE_MODEL_ORDER expects is missing - never silently drops it from the
    render (the same 'never mask a real gap' rule as render_omniroute()'s
    surfaces.omniroute.context_declared check)."""

    def test_a_route_missing_from_the_registry_raises_and_names_it(self):
        reg = copy.deepcopy(real_registry())
        del reg["routes"]["t3-driver"]
        with self.assertRaises(ValueError) as ctx:
            registry.render_ide(reg)
        self.assertIn("t3-driver", str(ctx.exception))

    def test_an_unexpected_extra_route_raises_and_names_it(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["brand-new-route"] = copy.deepcopy(reg["routes"]["t3-driver"])
        reg["routes"]["brand-new-route"]["id"] = "brand-new-route"
        with self.assertRaises(ValueError) as ctx:
            registry.render_ide(reg)
        self.assertIn("brand-new-route", str(ctx.exception))


class EffortLadderInRenderIdeTests(unittest.TestCase):
    """render_ide() derives effort_ladder from the first leg's model definition
    in the registry, filtering out "none" and omitting for legless routes or
    models without a ladder (A6a)."""

    def test_effort_ladder_derived_from_head_leg(self):
        reg = copy.deepcopy(real_registry())
        # Pick a route whose first leg's model actually carries an effort_ladder
        # (t2-worker heads gemini-3.8-flash; t1-orchestrator was the example
        # until it fail-closed 2026-09-27 and left the ide render).
        route = reg["routes"]["t2-worker"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        model_entry = reg["models"][mid]
        self.assertIn("effort_ladder", model_entry)
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertEqual(by_id["t2-worker"]["effort_ladder"],
                         [e for e in model_entry["effort_ladder"] if e != "none"])

    def test_none_is_dropped_from_effort_ladder(self):
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["t2-worker"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        reg["models"][mid]["effort_ladder"] = ["none", "low", "medium", "high"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertEqual(by_id["t2-worker"]["effort_ladder"], ["low", "medium", "high"])

    def test_no_effort_ladder_when_model_has_no_ladder(self):
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["t3-driver-clean"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        # Ensure the model has no effort_ladder
        reg["models"][mid].pop("effort_ladder", None)
        # t2-worker still has a ladder via the first leg's model
        # (t1-orchestrator was the control until it fail-closed 2026-09-27).
        t2_route = reg["routes"]["t2-worker"]
        _t2_pid, t2_mid = registry.resolve_leg(t2_route["legs"][0], reg)
        t2_ladder = reg["models"][t2_mid].get("effort_ladder", [])
        expected = [e for e in t2_ladder if isinstance(e, str) and e != "none"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertNotIn("effort_ladder", by_id["t3-driver-clean"])
        self.assertEqual(by_id["t2-worker"]["effort_ladder"], expected)

    def test_no_effort_ladder_when_route_has_no_legs(self):
        # The rule is about a route that declares no legs at all, so it is
        # tested against a synthesized one - MUSEAPI 2026-09-27 gave the real
        # t1-orchestrator-paid a leg, and t2-worker-paid/t3-driver-paid would
        # drift out of the fixture the moment anyone legs them too.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t1-orchestrator-paid"]["legs"] = []
        # t2-worker has legs whose first model carries a ladder
        t2_route = reg["routes"]["t2-worker"]
        _t2_pid, t2_mid = registry.resolve_leg(t2_route["legs"][0], reg)
        t2_ladder = reg["models"][t2_mid].get("effort_ladder", [])
        expected = [e for e in t2_ladder if isinstance(e, str) and e != "none"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertNotIn("effort_ladder", by_id["t1-orchestrator-paid"])
        self.assertEqual(by_id["t2-worker"]["effort_ladder"], expected)

    def test_the_contributor_ladder_reaches_every_surface_that_carries_one(self):
        # MUSEAPI step 3: the effort aliases for the contributor writer are the
        # ladder the renderers already project - catalog/ide-models.json's
        # effort_ladder and, from it, opencode.jsonc's per-effort `variants`
        # (the #minimal/#low/#medium/#high/#xhigh pickers).
        # FREEKEYS-2 (D-141) moved the free band ahead of the contributor leg,
        # so t1-orchestrator's SERVED head is gemini and its picker follows that
        # head (finding 8) - the contributor ladder is pinned on the routes the
        # contributor actually heads.
        ladder = real_registry()["models"]["muse-spark-1.3-contributor"]["effort_ladder"]
        self.assertEqual(ladder, ["minimal", "low", "medium", "high", "xhigh"])
        by_id = {m["id"]: m for m in real_ide_models()["models"]}
        for route_id in ("t1-orchestrator-paid", "spark-1.3-contributor"):
            self.assertEqual(by_id[route_id]["effort_ladder"], ladder, route_id)

        oc = json.loads(_strip_jsonc((ROOT / "opencode.jsonc").read_text(encoding="utf-8")))
        seen = set()
        for route_id in ("t1-orchestrator-paid", "spark-1.3-contributor"):
            for provider in by_id[route_id]["surfaces"]:
                models = oc["providers"][provider]["models"]
                variants = [v["id"] for v in models[route_id].get("variants", [])]
                self.assertEqual(variants, ladder,
                                 "%s/%s variants" % (provider, route_id))
                for rung in variants:
                    self.assertEqual(models[route_id]["variants"]
                                     [variants.index(rung)]["settings"]["reasoningEffort"],
                                     rung, "%s/%s#%s" % (provider, route_id, rung))
                seen.add(provider)
        # spark has no LiteLLM surface, so the loop above is not vacuous only
        # because omniroute carried everything.
        self.assertIn("litellm", seen)

    def test_an_omniroute_combo_cannot_carry_a_per_effort_alias(self):
        # The gap step 3 anticipated: a combos.json entry is
        # {name, strategy, context, models} and the gateway has no per-effort
        # parameter, so spark-1.3-contributor-minimal as a *combo* is not
        # renderable. Pinned here so the day the gateway grows one, this test
        # fails and the alias is rendered on that surface too.
        combo = {c["name"]: c for c in real_combos()["combos"]}["spark-1.3-contributor"]
        self.assertEqual(sorted(combo), ["context", "models", "name", "strategy"])
        self.assertEqual([c["name"] for c in real_combos()["combos"]
                          if c["name"].startswith("spark-1.3-contributor-")], [])


class OpenhandsRenderMatchesTodayTests(unittest.TestCase):
    """Render of the real registry equals today's configuration/openhands/
    tier-profiles.json semantically (task A4d; docs/plans/2026-09-25-registry-
    mapping.md section 13 documents the mapping). Like render_ide()'s
    models[], `tiers[]` order IS semantic here - tier-profiles.json's own
    $comment: "ORDER IS THE PUSH PRIORITY" - so render_openhands() reproduces
    it via OPENHANDS_TIER_ORDER rather than treating it as an exception."""

    def test_render_matches_committed_tier_profiles_semantically(self):
        problems = registry.openhands_diff(registry.render_openhands(real_registry()), real_tier_profiles())
        self.assertEqual(problems, [])

    def test_render_carries_the_spec_3_2_generated_marker(self):
        rendered = registry.render_openhands(real_registry())
        self.assertIn("generated from catalog/ai-registry.json", rendered["$comment"])
        self.assertIn("do not edit", rendered["$comment"])

    def test_generated_marker_is_new_not_borrowed_from_the_committed_file(self):
        self.assertNotIn("generated from catalog/ai-registry.json",
                         json.dumps(real_tier_profiles()["$comment"]))

    def test_render_order_matches_todays_push_priority(self):
        rendered = registry.render_openhands(real_registry())
        self.assertEqual([t["id"] for t in rendered["tiers"]],
                         [t["id"] for t in real_tier_profiles()["tiers"]])

    def test_retired_ids_match_todays_file(self):
        rendered = registry.render_openhands(real_registry())
        self.assertEqual(sorted(rendered["retired_ids"]), sorted(real_tier_profiles()["retired_ids"]))

    def test_base_urls_match_todays_file(self):
        rendered = registry.render_openhands(real_registry())
        current = real_tier_profiles()
        self.assertEqual(rendered["gateway_base_url"], current["gateway_base_url"])
        self.assertEqual(rendered["litellm_base_url"], current["litellm_base_url"])

    def test_direct_profile_tier_carries_its_own_gateway_and_key_surface(self):
        # spark-1.3-contributor's standalone surfaces.openhands.direct_profile
        # (mapping doc section 6) becomes the one tier with "gateway":
        # "openrouter" - its own endpoint/key, not the gateway client's.
        # Spark fails closed on the real registry (OpenRouter off since DSMAX),
        # so exercise it on a copy with the provider re-funded.
        reg = copy.deepcopy(real_registry())
        reg["providers"]["openrouter"]["available"] = True
        rendered = registry.render_openhands(reg)
        by_id = {t["id"]: t for t in rendered["tiers"]}
        direct = by_id["openrouter-muse-spark-1.3-contributor"]
        self.assertEqual(direct["gateway"], "openrouter")
        self.assertEqual(direct["model"], "openrouter/meta/muse-spark-1.3-contributor")
        self.assertEqual(direct["base_url"], "https://openrouter.ai/api/v1")


class OpenhandsRenderDeterminismTests(unittest.TestCase):
    """A second render changes nothing (spec 11: idempotence)."""

    def test_two_in_process_renders_are_byte_for_byte_equal(self):
        first = registry.render_json(registry.render_openhands(real_registry()))
        second = registry.render_json(registry.render_openhands(real_registry()))
        self.assertEqual(first, second)

    def test_cli_two_runs_write_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out1, out2 = Path(d) / "a.json", Path(d) / "b.json"
            for out in (out1, out2):
                proc = run_cli("render", "openhands", "--out", str(out))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
            self.assertTrue(out1.read_bytes().endswith(b"\n"))


class ChangedFieldFailsOpenhandsCheckTests(unittest.TestCase):
    """--check exits 1 and names the tier when a field differs from
    configuration/openhands/tier-profiles.json."""

    def test_changed_tokens_exits_one_and_names_the_tier(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t3-driver"]["surfaces"]["omniroute"]["openhands_profile"]["max_input_tokens"] = 1
        path = write_registry(reg)
        try:
            proc = run_cli("render", "openhands", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("tiers.omniroute-t3-driver", proc.stdout)

    def test_unmodified_registry_check_exits_zero_on_the_real_files(self):
        proc = run_cli("render", "openhands", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)

    def test_missing_tier_profiles_file_exits_one(self):
        proc = run_cli("render", "openhands", "--check",
                       "--tier-profiles", "/nonexistent/tier-profiles.json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


class MissingTierFailsOpenhandsRenderTests(unittest.TestCase):
    """render_openhands() fails loudly, naming the id, when the registry and
    OPENHANDS_TIER_ORDER disagree on which openhands profiles exist - never
    silently drops or invents one (the same 'never mask a real gap' rule as
    render_ide()'s IDE_MODEL_ORDER check)."""

    def test_a_profile_missing_from_the_registry_raises_and_names_it(self):
        reg = copy.deepcopy(real_registry())
        del reg["routes"]["t3-driver"]["surfaces"]["omniroute"]["openhands_profile"]
        with self.assertRaises(ValueError) as ctx:
            registry.render_openhands(reg)
        self.assertIn("omniroute-t3-driver", str(ctx.exception))

    def test_an_unexpected_extra_profile_raises_and_names_it(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t4-rag"]["surfaces"]["litellm"]["openhands_profile"] = {
            "max_input_tokens": 1, "max_output_tokens": 1, "reasoning": False}
        with self.assertRaises(ValueError) as ctx:
            registry.render_openhands(reg)
        self.assertIn("litellm-t4-rag", str(ctx.exception))

    def test_a_direct_profile_route_missing_from_the_id_table_raises(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["deepseek-v4.1-flash"]["surfaces"]["openhands"] = {
            "direct_profile": {"model": "x/y", "max_input_tokens": 1,
                               "max_output_tokens": 1, "reasoning": False}}
        with self.assertRaises(ValueError) as ctx:
            registry.render_openhands(reg)
        self.assertIn("deepseek-v4.1-flash", str(ctx.exception))


class SyncOpenhandsProfilesSourcesFromRegistryTests(unittest.TestCase):
    """A4d retarget: tools/sync-openhands-profiles.py's default (unflagged)
    spec now comes from tools/registry.py's render_openhands() instead of
    reading configuration/openhands/tier-profiles.json directly; --spec stays
    an explicit override onto the old file's own shape - the same convention
    A4c gave tools/sync-ide-models.py's --catalog flag."""

    def test_default_spec_matches_the_registry_render(self):
        sync = _load_sync_openhands_profiles_tool()
        spec = sync.load_spec(None, REGISTRY_PATH)
        self.assertEqual(spec, registry.render_openhands(real_registry()))

    def test_explicit_spec_flag_still_reads_the_old_file_directly(self):
        sync = _load_sync_openhands_profiles_tool()
        spec = sync.load_spec(TIER_PROFILES_PATH, REGISTRY_PATH)
        self.assertEqual(spec, real_tier_profiles())

    def test_cli_default_run_still_matches_the_committed_spec(self):
        # tests/run-tests.sh's own "tier profiles come from the spec, installer
        # and tool agree" case is the real regression coverage for this (grepped
        # per task A4d's own brief); this only proves the Python entry point
        # picked the registry-sourced path by default.
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, AUTOOS_OMNIROUTE_KEY="sk-fake-registry-test-key")
            for var in ("LITELLM_MASTER_KEY", "AUTOOS_LITELLM_API_KEY", "OPENROUTER_API_KEY"):
                env.pop(var, None)
            proc = subprocess.run(
                [sys.executable, str(SYNC_OPENHANDS_PROFILES_TOOL),
                 "--openhands-dir", d, "--keys-file", str(Path(d) / "none.yml"),
                 "--litellm-env", str(Path(d) / "none.env")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            # t1-orchestrator was the canary until it fail-closed 2026-09-27
            # (omitted: Zen client-bound, OpenRouter off); t2-worker is the
            # servable equivalent.
            written = Path(d) / "profiles" / "omniroute-t2-worker.json"
            self.assertTrue(written.exists())
            self.assertEqual(json.loads(written.read_text())["model"], "openai/t2-worker")


class ModelsDocRenderMatchesTodayTests(unittest.TestCase):
    """Render of the real registry equals docs/models.md's committed
    "models-doc" block, byte for byte (task A4e; docs/plans/2026-09-25-
    registry-mapping.md section 14 documents the mapping). Unlike the
    rejected first attempt at this task (commit 6a61052, never merged - see
    the task brief), every cell comes from a registry field: there is no
    hand-copied constant of the table's text anywhere in tools/registry.py."""

    def test_render_matches_committed_models_doc_block(self):
        rendered = registry.render_models_doc(real_registry())
        current = registry.models_doc_block_text(real_models_doc())
        self.assertEqual(registry.models_doc_diff(rendered, current), [])

    def test_render_carries_the_spec_3_2_generated_notice(self):
        rendered = registry.render_models_doc(real_registry())
        self.assertIn("catalog/ai-registry.json", rendered)
        self.assertIn("do not edit", rendered)

    def test_one_row_per_registry_route(self):
        rendered = registry.render_models_doc(real_registry())
        rendered_ids = set(registry._models_doc_rows(rendered))
        self.assertEqual(rendered_ids, set(real_registry()["routes"]))


class ModelsDocCellsComeFromTheRegistryTests(unittest.TestCase):
    """Every column is read straight from a registry field - no table text is
    a hand-held constant in tools/registry.py (the task's own hard
    requirement). Each test below mutates one field in a copy of the
    registry and checks the rendered cell follows it."""

    def test_class_column_reflects_routes_class(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t4-rag"]["class"] = "frontier"
        rendered = registry.render_models_doc(reg)
        self.assertIn("frontier", row_for(rendered, "t4-rag"))

    def test_context_column_prefers_context_declared(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t3-driver"]["surfaces"]["omniroute"]["context_declared"] = "999k"
        rendered = registry.render_models_doc(reg)
        self.assertIn("999k", row_for(rendered, "t3-driver"))

    def test_context_column_falls_back_to_the_surfaces_numeric_context(self):
        reg = copy.deepcopy(real_registry())
        del reg["routes"]["t3-driver"]["surfaces"]["omniroute"]["context_declared"]
        rendered = registry.render_models_doc(reg)
        # FREEKEYS-2c: the numeric fallback is clamped like every other context
        # cell (clamp_route_context narrows a numeric surface context too), and
        # t3-driver's servable legs now head with the free band, whose smallest
        # advertised window is the scaleway/nebius 128k. The surface promise of
        # 131,072 no longer survives the clamp; the comma-spelled number is the
        # fallback's signature - "128k" is the label form the deleted cell had.
        self.assertIn("128,000", row_for(rendered, "t3-driver"))

    def test_context_column_falls_back_to_a_legs_model_when_no_surface_carries_one(self):
        reg = copy.deepcopy(real_registry())
        for gw in ("omniroute", "litellm"):
            surface = reg["routes"]["t4-rag"]["surfaces"].get(gw)
            if isinstance(surface, dict):
                surface.pop("context_declared", None)
                surface.pop("context", None)
        rendered = registry.render_models_doc(reg)
        row = row_for(rendered, "t4-rag")
        # t4-rag's first leg is cohere/command-a-03-2025, context_advertised 131072.
        self.assertIn("131,072", row)
        self.assertIn("leg model", row)

    def test_legs_column_lists_every_leg_in_order(self):
        # Legs render as `model-spelling` without the provider prefix, so
        # two legs sharing one spelling render identical backtick text and a
        # naive str.index() finds only the FIRST occurrence for both. Search
        # forward from the previous match instead, so a repeated model
        # spelling is found at its own, later position rather than colliding
        # on the first one. (No t3-driver leg repeats a spelling today - the
        # 16:4xZ revision removed its openrouter/qwen/qwen3.8-27b leg, so this
        # currently asserts order only - but t2-worker still repeats
        # `gpt-oss-120b` across providers, so the forward search is the right
        # shape if a repeat is ever reintroduced here.)
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "t3-driver")
        legs = real_registry()["routes"]["t3-driver"]["legs"]
        positions = []
        cursor = 0
        for leg in legs:
            needle = "`%s`" % leg.split("/", 1)[1]
            found = row.index(needle, cursor)
            positions.append(found)
            cursor = found + 1
        self.assertEqual(positions, sorted(positions))

    def test_empty_legs_render_as_none(self):
        rendered = registry.render_models_doc(real_registry())
        self.assertIn("(none)", row_for(rendered, "auto"))

    def test_leg_flagged_unavailable_in_its_own_route_is_marked(self):
        # All legs of routes.deepseek-v4.1-flash flagged in a copy (the real
        # data flags its zen leg and both OpenRouter BYOK gpt-oss legs stay
        # gated since the 2026-09-27 credits-exhausted measurement). The route
        # carries two legs since DSMAX dropped the OpenRouter leg, so both
        # render struck through.
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["deepseek-v4.1-flash"]
        for leg in route["legs"]:
            route.setdefault("unavailable_legs", {})[leg] = {"available": False}
        rendered = registry.render_models_doc(reg)
        row = row_for(rendered, "deepseek-v4.1-flash")
        self.assertEqual(row.count("(unavailable)"), len(route["legs"]))

    def test_leg_whose_provider_is_globally_unavailable_is_marked(self):
        # A provider-wide providers.<id>.available: false (independent of any
        # per-route unavailable_legs annotation) still marks every leg
        # reached through it - exercised with a synthetic flip on a copied
        # registry rather than tying this test to whichever real provider
        # happens to be globally down today (openrouter's own blanket flag
        # was lifted 2026-09-26; per the 16:4xZ revision OpenRouter is BYOK
        # with no shared credit, and its still-dead legs are flagged
        # individually now - see
        # test_leg_flagged_unavailable_in_its_own_route_is_marked).
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t2-orchestrator"]["unavailable_legs"] = {}
        reg["providers"]["openrouter"]["available"] = False
        rendered = registry.render_models_doc(reg)
        row = row_for(rendered, "t2-orchestrator")
        self.assertIn("~~openrouter", row)
        self.assertIn("(unavailable)", row)

    def test_available_leg_is_not_marked(self):
        # t4-rag: a multi-leg route with every leg available today (opus-4-6
        # was the example until cc went unavailable, operator 2026-09-27).
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "t4-rag")
        self.assertIn("→", row)
        self.assertNotIn("(unavailable)", row)


class ModelsDocRenderDeterminismTests(unittest.TestCase):
    """A second render changes nothing (same convention as every other
    render_* function's own determinism test above)."""

    def test_two_in_process_renders_are_identical(self):
        first = registry.render_models_doc(real_registry())
        second = registry.render_models_doc(real_registry())
        self.assertEqual(first, second)

    def test_cli_two_runs_write_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out1, out2 = Path(d) / "a.md", Path(d) / "b.md"
            for out in (out1, out2):
                proc = run_cli("render", "models-doc", "--out", str(out))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())
            self.assertTrue(out1.read_bytes().endswith(b"\n"))


class ChangedLegAvailabilityFailsModelsDocCheckTests(unittest.TestCase):
    """The task's own acceptance test: mark a leg unavailable in a copy of
    the registry and --check must exit 1, naming the route - never silently
    pass because the doc's prose (now generated) does not actually read the
    field."""

    def test_marking_a_leg_unavailable_exits_one_and_names_the_route(self):
        reg = copy.deepcopy(real_registry())
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted the
        # antigravity/claude-opus-4-6-thinking fixture leg from opus-4-6, whose
        # only remaining leg cc/claude-opus-4-6 is already struck through by
        # providers.cc available:false - flipping it changes no render, so the
        # fixture moves to t4-rag, whose legs are all live today).
        leg = reg["routes"]["t4-rag"]["legs"][0]
        reg["routes"]["t4-rag"]["unavailable_legs"] = {
            leg: {"available": False},
        }
        path = write_registry(reg)
        try:
            proc = run_cli("render", "models-doc", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("routes.t4-rag", proc.stdout)

    def test_a_class_change_exits_one_and_names_the_route(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t4-rag"]["class"] = "frontier"
        path = write_registry(reg)
        try:
            proc = run_cli("render", "models-doc", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("routes.t4-rag", proc.stdout)

    def test_unmodified_registry_check_exits_zero_on_the_real_files(self):
        proc = run_cli("render", "models-doc", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)

    def test_missing_docs_file_exits_one(self):
        proc = run_cli("render", "models-doc", "--check",
                       "--docs", "/nonexistent/models.md")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


class ModelsDocMarkerLocationTests(unittest.TestCase):
    """_locate_managed_block() never returns a partial/wrong match on a
    missing, duplicated or unmatched marker - mirrors tools/sync-router-
    tiers.py's own locate_blocks() "never mask a gap" contract for its `#
    AUTOOS-MANAGED-START/END` syntax, applied to docs/models.md's `<!--
    AUTOOS-MANAGED-START/END -->` Markdown-comment convention."""

    def test_missing_start_marker_raises(self):
        with self.assertRaises(ValueError):
            registry.models_doc_block_text("no markers here\n")

    def test_missing_end_marker_raises(self):
        text = "%s\nrow\n" % registry.MODELS_DOC_START_LINE
        with self.assertRaises(ValueError):
            registry.models_doc_block_text(text)

    def test_duplicate_start_marker_raises(self):
        text = "%s\nrow\n%s\nrow\n%s\n" % (
            registry.MODELS_DOC_START_LINE, registry.MODELS_DOC_START_LINE, registry.MODELS_DOC_END_LINE)
        with self.assertRaises(ValueError):
            registry.models_doc_block_text(text)

    def test_stray_end_marker_with_no_start_raises(self):
        text = "row\n%s\n" % registry.MODELS_DOC_END_LINE
        with self.assertRaises(ValueError):
            registry.models_doc_block_text(text)

    def test_well_formed_block_round_trips(self):
        text = "before\n%s\nrow one\nrow two\n%s\nafter\n" % (
            registry.MODELS_DOC_START_LINE, registry.MODELS_DOC_END_LINE)
        self.assertEqual(registry.models_doc_block_text(text), "row one\nrow two")


class UnknownModelsDocDocsFlagStillGoesThroughRenderTargetsTests(unittest.TestCase):
    """models-doc is wired into the same `render <target>` subparser tree as
    omniroute/litellm/ide/openhands - it must not disturb them."""

    def test_existing_render_targets_still_work(self):
        for target in ("omniroute", "litellm", "ide", "openhands"):
            proc = run_cli("render", target, "--check")
            self.assertEqual(proc.returncode, 0, "%s: %s" % (target, proc.stdout + proc.stderr))


class GatewayLegsFilterTests(unittest.TestCase):
    """OR1a (ONE-ROUTER step 1a): the gateway renders mirror only legs a live
    gateway can actually serve - never a leg the registry marks unavailable
    (route-level or provider-level), a leg policy.leg_rules denies, or a
    client_bound leg the gateway 403s. models-doc is deliberately not in this
    set: it still shows a gated leg struck through for the human reader."""

    def setUp(self):
        self.reg = synthetic_gateway_registry()

    def test_drops_denied_provider_dead_and_client_bound_keeps_order(self):
        # clean/first and clean/second survive; locked/one (denied),
        # dead/deadmodel (provider available: false) and clean/bound
        # (client_bound) do not; the two survivors keep their declared order.
        self.assertEqual(
            registry.gateway_legs(self.reg["routes"]["mix"], self.reg),
            ["clean/first", "clean/second"])

    def test_drops_a_leg_gated_in_its_own_route(self):
        reg = copy.deepcopy(self.reg)
        reg["routes"]["mix"]["unavailable_legs"] = {
            "clean/first": {"available": False}}
        self.assertEqual(
            registry.gateway_legs(reg["routes"]["mix"], reg), ["clean/second"])

    def test_provider_available_false_drops_its_legs(self):
        reg = copy.deepcopy(self.reg)
        reg["routes"]["mix"]["legs"] = ["dead/deadmodel"]
        self.assertEqual(registry.gateway_legs(reg["routes"]["mix"], reg), [])

    def test_client_bound_leg_dropped(self):
        reg = copy.deepcopy(self.reg)
        reg["routes"]["mix"]["legs"] = ["clean/bound"]
        self.assertEqual(registry.gateway_legs(reg["routes"]["mix"], reg), [])

    def test_omniroute_mirrors_only_the_servable_legs(self):
        combo = {c["name"]: c for c in
                 registry.render_omniroute(self.reg)["combos"]}["mix"]
        self.assertEqual(combo["models"], ["clean/first", "clean/second"])

    def test_omniroute_omits_a_route_with_no_servable_leg(self):
        # A combo whose every leg is dead would serve nothing - and a caller
        # must not be handed a dead combo - so the route gets no entry at all,
        # exactly like a route whose `legs` is already [].
        reg = copy.deepcopy(self.reg)
        reg["routes"]["mix"]["legs"] = ["dead/deadmodel"]
        rendered = registry.render_omniroute(reg)
        self.assertEqual([c["name"] for c in rendered["combos"]], [])

    def test_all_gateway_legs_dropped_gets_no_block_not_a_raise(self):
        # A tier whose every leg is unavailable/denied/gateway-only renders no
        # block at all rather than raising: an absent group is a valid config
        # and the same shape render_omniroute() gives an all-dead route.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t2-worker"]["legs"] = ["groq/any-model"]
        rendered = registry.render_litellm_blocks(
            reg, real_litellm_config(), tiers=("t2-worker",))
        self.assertEqual(rendered, {})

    def test_an_explicit_tier_absent_from_the_registry_still_raises(self):
        # An explicit tier is still validated: a tier the registry does not
        # have at all is a caller error, unlike one that renders empty.
        with self.assertRaises(ValueError) as ctx:
            registry.render_litellm_blocks(
                real_registry(), real_litellm_config(), tiers=("no-such-tier",))
        self.assertIn("no-such-tier", str(ctx.exception))

    def test_real_omniroute_drops_gated_legs_and_keeps_live_order(self):
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        # DSBACK 2026-09-28: t2-worker-clean serves deepseek FIRST again —
        # providers.deepseek is back on after the operator top-up. The openrouter
        # leg stays out (provider off, DSMAX) and the zen leg stays out (its own
        # route gate). MISTRALFIX 2026-09-28 took the route's fourth leg,
        # mistral/mistral-small-latest, out of the registry itself (0 rpm on the
        # measured plan), so the combo is now exactly the one live leg — and the
        # combo survives, which is what the invariant in tests/test_registry.py
        # requires of a route that still serves traffic.
        # L1-CLEAN 2026-10-01 supersedes the DSBACK single-leg shape: the trial
        # credits lead and the native DeepSeek leg is the last paid fallback.
        self.assertEqual(
            combos["t2-worker-clean"]["models"],
            ["ovh/gpt-oss-120b", "ovh/Qwen3.8-27B", "vertex/gemini-3.8-flash",
             "deepseek/deepseek-flash"])
        # samba/SambaNova is available: false, so every one of its legs goes -
        # including the pinned one-leg routes.
        # t1-orchestrator-free-only is NOT gone: T1FREE gave it a gemini
        # servable leg. t3-driver-free-only is NOT gone: FREEAI gave it a
        # servable leg.
        for gone in ("samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertNotIn(gone, combos)

    def test_real_litellm_drops_gated_legs(self):
        rendered = registry.render_litellm_blocks(
            real_registry(), real_litellm_config())
        # sambanova/openrouter gpt-oss-120b legs are unavailable and dropped;
        # the ovhcloud credit-tier leg (added 2026-09-30,
        # L1-backlog/ws-ovh-20260930) is available and kept.
        self.assertNotIn("sambanova/gpt-oss-120b", rendered["t2-worker"])
        # FREEWIRE 2026-09-30: allow-groq-gpt-oss re-opened this leg on a
        # single-tool-call probe, so the render now mirrors it.
        self.assertIn("groq/openai/gpt-oss-120b", rendered["t2-worker"])
        self.assertIn("ovhcloud/gpt-oss-120b", rendered["t2-worker"])
        self.assertNotIn("model: openai/deepseek-v4-flash", rendered["t2-worker"])
        # the client-bound opencode-zen leg (litellm transport openai/…) is
        # dropped, and so is the openrouter leg of the same model (DSMAX moved
        # to the route level in FREEWIRE 2026-09-30). The deepseek DIRECT leg is
        # back in since DSBACK 2026-09-28 topped the balance up — pinned here as
        # present, so a future flip that drops it again names it.
        self.assertIn("model: deepseek/deepseek-flash", rendered["t2-worker"])
        self.assertNotIn("model: openai/deepseek-v4.1-flash", rendered["t2-worker"])
        self.assertNotIn("model: openrouter/deepseek/deepseek-v4.1-flash", rendered["t2-worker"])
        # CIGREEN: expectation moved by 018438ed (TASK1 re-added the gemini
        # head) + ba73f1cf (TASK2) + 20c4a816 (TASK3 re-opened google_ai_studio
        # available:true): the render correctly mirrors the live head. Gated
        # legs are still dropped - every assertNotIn above still holds.
        self.assertIn("gemini-3.8-flash", rendered["t2-worker"])
        # t3-driver: samba/sambanova/cerebras provider-dead; opencode-zen
        # client-bound. FREEWIRE re-opened the groq qwen3.8-27b and openrouter
        # ':free' qwen3.8-27b legs, so the lowercase spelling is now present.
        self.assertIn("qwen3.8-27b", rendered["t3-driver"])
        self.assertNotIn("MiniMax-M3", rendered["t3-driver"])
        self.assertIn("mistral-code-latest", rendered["t3-driver"])
        # ovhcloud credit-tier legs (added 2026-09-30) are available;
        # groq's lowercase qwen3.8-27b is denied and dropped, and the
        # OVH model ID is mixed-case (Qwen3.8-27B) so the assertNotIn
        # above still passes.
        self.assertIn("ovhcloud/gpt-oss-120b", rendered["t3-driver"])
        self.assertIn("ovhcloud/Qwen3.8-27B", rendered["t3-driver"])

    def test_models_doc_still_strikes_through_a_gated_leg(self):
        # L1-CLEAN (2026-10-01): the -clean twins no longer carry a gated leg
        # (their trial-first legs are all servable), so the stale OVH coder leg
        # kept in t2-worker's legs and marked unavailable_legs is the example.
        row = row_for(registry.render_models_doc(real_registry()), "t2-worker")
        self.assertIn(
            "~~ovhcloud `Qwen3-Coder-30B-A3B-Instruct`~~ (unavailable)", row)


class NoServableLegOffersNoDeclarationTests(unittest.TestCase):
    """OR1d (ONE-ROUTER step 1d): a route that declares legs but has no
    gateway-servable leg is offered by no declaration - render_ide and
    render_openhands drop it exactly as render_omniroute/render_litellm_blocks
    already do. A deliberately legless route (the LiteLLM-only *-paid and the
    dynamic auto* routes) keeps its declaration: it never promised a gateway
    leg, so the rule does not reach it. docs/models.md is deliberately out of
    scope and still lists every route for the human reader."""

    def test_servable_route_ids_is_nonempty_only_with_a_servable_leg(self):
        reg = copy.deepcopy(synthetic_gateway_registry())
        self.assertIn("mix", registry.servable_route_ids(reg))
        reg["routes"]["mix"]["legs"] = ["dead/deadmodel"]
        self.assertNotIn("mix", registry.servable_route_ids(reg))

    def test_servable_route_ids_treats_a_legless_route_as_unsatisfied(self):
        # A route with no declared legs serves nothing, so it is not in the
        # set - the renders below are what deliberately keep it.
        reg = copy.deepcopy(synthetic_gateway_registry())
        reg["routes"]["mix"]["legs"] = []
        self.assertNotIn("mix", registry.servable_route_ids(reg))

    def test_ide_drops_a_route_that_declares_legs_but_serves_none(self):
        ids = [m["id"] for m in registry.render_ide(real_registry())["models"]]
        # t1-orchestrator-free-only is NOT dropped: T1FREE gave it a gemini
        # servable leg.
        for gone in ("samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertNotIn(gone, ids)

    def test_ide_keeps_a_deliberately_legless_route(self):
        ids = [m["id"] for m in registry.render_ide(real_registry())["models"]]
        for kept in ("t1-orchestrator-paid", "t2-worker-paid", "t3-driver-paid",
                     "auto", "auto/smart", "auto/cheap"):
            self.assertIn(kept, ids)

    def test_ide_route_returns_when_its_leg_becomes_servable(self):
        reg = copy.deepcopy(real_registry())
        reg["providers"]["samba"]["available"] = True
        ids = [m["id"] for m in registry.render_ide(reg)["models"]]
        self.assertIn("samba/gpt-oss-120b", ids)
        self.assertIn("samba/MiniMax-M3", ids)

    def test_openhands_drops_a_tier_that_declares_legs_but_serves_none(self):
        # PROVFIX3 finding 7: T1FREE re-serviced every tier that used to be in
        # the dropped set, so the real registry alone proves nothing here.
        # Synthesize the dead route instead of commenting the assertion out.
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["t1-orchestrator"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            route["unavailable_legs"][leg] = {"available": False}
        self.assertEqual(registry.gateway_legs(route, reg), [])
        ids = {t["id"] for t in registry.render_openhands(reg)["tiers"]}
        self.assertNotIn("omniroute-t1-orchestrator", ids)
        self.assertNotIn("litellm-t1-orchestrator", ids)
        # every other openhands declaration is untouched by the gate
        self.assertEqual(len(ids), len(registry.render_openhands(real_registry())["tiers"]) - 2)

    def test_openhands_keeps_a_tier_whose_route_now_declares_no_legs(self):
        # The rule reaches a route that DECLARED legs and cannot serve them -
        # never one that declares none at all.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t1-orchestrator"]["legs"] = []
        ids = {t["id"] for t in registry.render_openhands(reg)["tiers"]}
        self.assertIn("omniroute-t1-orchestrator", ids)
        self.assertIn("litellm-t1-orchestrator", ids)

    def test_openhands_tier_returns_when_its_leg_becomes_servable(self):
        # FREEKEYS-2c (D-141) put a servable free band (scaleway/nebius) ahead of
        # free_ai in this route, so gating one leg no longer empties it: gate
        # EVERY leg and its declaration goes; lift the gate and it comes back.
        gated = copy.deepcopy(real_registry())
        route = gated["routes"]["t3-driver-free-only"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            route["unavailable_legs"][leg] = {"available": False}
        self.assertEqual(registry.gateway_legs(route, gated), [])
        gone = {t["id"] for t in registry.render_openhands(gated)["tiers"]}
        self.assertNotIn("omniroute-t3-driver-free-only", gone)
        self.assertNotIn("litellm-t3-driver-free-only", gone)

        served = copy.deepcopy(gated)
        for leg in list(served["routes"]["t3-driver-free-only"]["legs"]):
            served["routes"]["t3-driver-free-only"]["unavailable_legs"].pop(leg, None)
        ids = {t["id"] for t in registry.render_openhands(served)["tiers"]}
        self.assertIn("omniroute-t3-driver-free-only", ids)
        self.assertIn("litellm-t3-driver-free-only", ids)

    def test_models_doc_is_not_filtered(self):
        # Out of scope by OR1d's own decision: models-doc still shows every
        # route, gated legs struck through, for the human reader.
        doc = registry.render_models_doc(real_registry())
        for kept in ("t1-orchestrator-free-only", "t3-driver-free-only",
                     "samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertIn("| `%s`" % kept, doc)


class UnavailableUntilRenderIndependenceTests(unittest.TestCase):
    """``unavailable_until`` (brief UNTIL, 2026-09-26) is resolver-only: the
    OmniRoute gateway handles a quota 429 itself, so every render -- and the
    CI drift gates built on them -- must stay TIME-INDEPENDENT. Adding or
    removing the field (on a provider, an unavailable_legs entry or a
    client) changes no render, byte-for-byte; a future until and a past one
    render identically. models-doc's strikethrough keeps reacting only to
    the timeless ``available: false`` flags (_leg_is_unavailable).
    """

    def with_untils(self):
        reg = copy.deepcopy(real_registry())
        reg["providers"]["cxa"]["unavailable_until"] = "2026-10-01T09:05:00Z"
        reg["routes"]["t2-worker-clean"]["unavailable_legs"][
            "opencode-zen/deepseek-v4.1-flash"]["unavailable_until"] = (
                "2026-10-01T09:05:00Z")
        # One leg with an until but no available:false at all.
        reg["routes"]["opus-4-6"]["unavailable_legs"] = {
            "antigravity/claude-opus-4-6-thinking": {
                "unavailable_until": "2026-10-01T09:05:00Z"},
        }
        reg["clients"]["agy"]["unavailable_until"] = "2026-10-01T09:05:00Z"
        return reg

    def test_omniroute_render_ignores_unavailable_until(self):
        self.assertEqual(registry.render_omniroute(real_registry()),
                         registry.render_omniroute(self.with_untils()))

    def test_litellm_render_ignores_unavailable_until(self):
        config = real_litellm_config()
        self.assertEqual(
            registry.render_litellm_blocks(real_registry(), config),
            registry.render_litellm_blocks(self.with_untils(), config))

    def test_ide_render_ignores_unavailable_until(self):
        self.assertEqual(registry.render_ide(real_registry()),
                         registry.render_ide(self.with_untils()))

    def test_openhands_render_ignores_unavailable_until(self):
        self.assertEqual(registry.render_openhands(real_registry()),
                         registry.render_openhands(self.with_untils()))

    def test_models_doc_render_ignores_unavailable_until(self):
        self.assertEqual(registry.render_models_doc(real_registry()),
                         registry.render_models_doc(self.with_untils()))

    def test_leg_is_unavailable_ignores_the_until_field(self):
        route = {"legs": ["clean/big"],
                 "unavailable_legs": {"clean/big": {
                     "available": False,
                     "unavailable_until": "2026-10-01T09:05:00Z"}}}
        reg = {"providers": {"clean": {"id": "clean"}},
               "models": {"big": {"id": "big"}}}
        self.assertTrue(registry._leg_is_unavailable("clean/big", route, reg))
        # An until alone (no available: false) never strikes a leg through.
        route2 = {"legs": ["clean/big"],
                  "unavailable_legs": {"clean/big": {
                      "unavailable_until": "2026-10-01T09:05:00Z"}}}
        self.assertFalse(registry._leg_is_unavailable("clean/big", route2, reg))


class OmittedRoutesListTests(unittest.TestCase):
    """OR1g: render_omniroute names as omitted only the ORPHANED routes - those
    that declared legs but have no gateway-servable leg left. A deliberately
    legless route (`legs: []`: the LiteLLM-only *-paid routes and the dynamic
    auto*/auto-cheap/auto-smart strategy) is not omitted: it never promised a
    gateway leg, so apply must never prune a live combo with one of its ids.
    apply.sh/apply.ps1 prune exactly this list plus "retired", so a combo the
    registry stopped serving leaves the live store instead of being created
    forever and never removed."""

    def test_omitted_is_exactly_the_orphaned_routes(self):
        reg = real_registry()
        rendered = registry.render_omniroute(reg)
        self.assertEqual(
            rendered["omitted"],
            sorted(route_id for route_id, route in reg["routes"].items()
                   if route.get("legs") and not registry.gateway_legs(route, reg)))

    def test_omitted_and_combos_partition_every_route_with_declared_legs(self):
        reg = real_registry()
        rendered = registry.render_omniroute(reg)
        names = [c["name"] for c in rendered["combos"]]
        with_legs = sorted(route_id for route_id, route in reg["routes"].items()
                           if route.get("legs"))
        self.assertEqual(sorted(names + rendered["omitted"]), with_legs)
        # A legless route is in neither list - it gets no combo and is never
        # named as an orphan apply could prune.
        legless = [route_id for route_id, route in reg["routes"].items()
                   if not route.get("legs")]
        self.assertTrue(legless, "no legless route to prove the exclusion with")
        for route_id in legless:
            self.assertNotIn(route_id, names)
            self.assertNotIn(route_id, rendered["omitted"])

    def test_omitted_is_disjoint_from_retired(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertEqual(set(rendered["omitted"]) & set(rendered["retired"]), set())

    def test_omitted_is_sorted(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertEqual(rendered["omitted"], sorted(rendered["omitted"]))

    def test_omitted_matches_todays_combos_file(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertEqual(rendered["omitted"], real_combos()["omitted"])

    def test_legless_route_is_not_omitted(self):
        # OR1g: a route that never declared a gateway leg (auto*, *-paid) is
        # deliberately served elsewhere - it must not be named as an orphan.
        reg = {
            "providers": {},
            "models": {},
            "routes": {"auto": {"id": "auto", "strategy": "priority", "legs": []}},
        }
        rendered = registry.render_omniroute(reg)
        self.assertEqual(rendered["combos"], [])
        self.assertEqual(rendered["omitted"], [])

    def test_orphaned_route_is_omitted(self):
        # A route that did declare a leg but has none servable is a managed
        # orphan: no combo, and named so apply can prune a stale live combo.
        reg = {
            "providers": {"dead": {"id": "dead", "available": False}},
            "models": {"m": {"id": "m"}},
            "routes": {
                "orphan": {
                    "id": "orphan",
                    "strategy": "priority",
                    "legs": ["dead/m"],
                    "surfaces": {"omniroute": {"context_declared": "8k"}},
                },
            },
        }
        rendered = registry.render_omniroute(reg)
        self.assertEqual(rendered["combos"], [])
        self.assertEqual(rendered["omitted"], ["orphan"])

    def test_all_dead_route_has_no_combo_but_is_omitted(self):
        # Every leg dropped by gateway_legs: no combo, but the id must still be
        # named so apply.sh can prune a live combo the registry abandoned.
        reg = {
            "providers": {"dead": {"id": "dead", "available": False}},
            "models": {"m": {"id": "m"}},
            "routes": {
                "dead-route": {
                    "id": "dead-route",
                    "strategy": "priority",
                    "legs": ["dead/m"],
                    "surfaces": {"omniroute": {"context_declared": "8k"}},
                },
            },
        }
        rendered = registry.render_omniroute(reg)
        self.assertEqual(rendered["combos"], [])
        self.assertEqual(rendered["omitted"], ["dead-route"])

    def test_diff_ignores_the_omitted_order(self):
        rendered = registry.render_omniroute(real_registry())
        current = dict(real_combos())
        current["omitted"] = list(reversed(current["omitted"]))
        self.assertEqual(registry.omniroute_diff(rendered, current), [])

    def test_diff_reports_a_changed_omitted_list(self):
        rendered = registry.render_omniroute(real_registry())
        current = json.loads(json.dumps(real_combos()))
        current["omitted"] = current["omitted"][:-1]
        self.assertIn("omitted", registry.omniroute_diff(rendered, current))


class FreeAiRenderTests(unittest.TestCase):
    """BRIEF FREEAI (2026-09-27): adding free_ai/qwen7b to the two zero-spend
    routes makes t3-driver-free-only servable again - it leaves combos.json's
    `omitted` list, gains an OmniRoute combo, a LiteLLM block, an IDE model and
    both OpenHands tiers - and free_ai never enters a -clean declaration."""

    def test_t3_driver_free_only_leaves_omitted_and_gains_a_combo(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertNotIn("t3-driver-free-only", rendered["omitted"])
        combos = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("t3-driver-free-only", combos)
        # CIGREEN (ba73f1cf TORDER TASK2): the trial-free-credits-paid band
        # leads and the self-hosted free_ai rides mid-list under its
        # omniroute_id spelling (D: model_prefix free-ai).
        models = combos["t3-driver-free-only"]["models"]
        # CIGREEN: expectation moved by ba73f1cf (TORDER TASK2:
        # trial-free-credits-paid order - gemini trial head, free band, the
        # self-hosted free_ai mid-list, scaleway grants trail it, deepseek
        # last where present). The T2FREE-era free-ai-last invariant is
        # superseded; the band head and tail below pin the new order.
        self.assertEqual(models[-1], "scw/qwen3-235b-a22b-instruct-2507")
        self.assertIn("free-ai/qwen7b", models)
        self.assertEqual(models[:5],
                         ["gemini/gemini-3.8-flash",
                          "groq/qwen/qwen3.8-27b",
                          "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                          "openrouter/poolside/laguna-s-2.1:free",
                          "groq/openai/gpt-oss-20b"])

    def test_free_ai_is_last_in_the_free_only_combos(self):
        # CIGREEN: expectation moved by ba73f1cf (TORDER TASK2:
        # trial-free-credits-paid, free_ai middle, scaleway grants trail it).
        # Pins the new band tails: free-ai/qwen7b rides third-from-last with
        # the scw grants behind it - the T2FREE-era free-ai-last invariant is
        # superseded, so a future reorder back to last fails loudly here.
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        self.assertEqual(combos["t2-worker-free-only"]["models"][-3:],
                         ["free-ai/qwen7b",
                          "scw/qwen3-235b-a22b-instruct-2507",
                          "scw/mistral-small-3.2-24b-instruct-2506"])
        self.assertEqual(combos["t3-driver-free-only"]["models"][-3:],
                         ["free-ai/qwen7b",
                          "scw/mistral-small-3.2-24b-instruct-2506",
                          "scw/qwen3-235b-a22b-instruct-2507"])

    def test_t2_worker_combo_ends_with_the_free_ai_leg(self):
        # CIGREEN: expectation moved by ba73f1cf (same TORDER TASK2 reorder:
        # paid last, deepseek last). The T2FREE-era last-leg invariant is
        # superseded - free_ai rides mid-list under the provider's own
        # spelling (model_prefix free-ai) and deepseek closes the combo.
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        models = combos["t2-worker"]["models"]
        self.assertEqual(models[-1], "deepseek/deepseek-flash")
        self.assertIn("free-ai/qwen7b", models)
        self.assertLess(models.index("free-ai/qwen7b"), len(models) - 1)

    def test_free_ai_never_enters_a_clean_combo(self):
        # PROV finding 11: neither spelling may appear - the rendered omniroute_id
        # (free-ai/) nor the registry leg (free_ai/).
        for combo in registry.render_omniroute(real_registry())["combos"]:
            if not combo["name"].endswith("-clean"):
                continue
            self.assertFalse(
                [m for m in combo["models"]
                 if m.startswith(("free-ai/", "free_ai/"))],
                combo["name"])

    def test_litellm_blocks_carry_free_ai_on_both_free_only_routes(self):
        rendered = registry.render_litellm_blocks(
            real_registry(), real_litellm_config())
        for route_id in ("t2-worker-free-only", "t3-driver-free-only"):
            self.assertIn("model: openai/qwen7b", rendered[route_id], route_id)
            self.assertIn("api_base: https://api.free.ai/v1",
                          rendered[route_id], route_id)
            self.assertIn("api_key: os.environ/FREE_AI_API_KEY",
                          rendered[route_id], route_id)

    def test_ide_lists_t3_driver_free_only_again(self):
        ids = [m["id"] for m in registry.render_ide(real_registry())["models"]]
        self.assertIn("t3-driver-free-only", ids)

    def test_openhands_lists_both_t3_driver_free_only_tiers_again(self):
        ids = {t["id"] for t in
               registry.render_openhands(real_registry())["tiers"]}
        self.assertIn("omniroute-t3-driver-free-only", ids)
        self.assertIn("litellm-t3-driver-free-only", ids)

    def test_combos_file_matches_the_render(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertEqual(registry.omniroute_diff(rendered, real_combos()), [])


class LitellmFallbackServabilityTests(unittest.TestCase):
    """PROVFIX3 finding 5: router_settings.fallbacks is the last resort, so a
    chain that ends in a group whose every model belongs to a provider the
    registry has switched off is not an escalation — the retry 404s/503s exactly
    like the tier it replaced ("the escalation must actually answer"). A dropped
    chain must say why in the file, or the next edit re-adds it."""

    def _fallback_targets(self, text):
        block = re.search(r"(?ms)^\s*fallbacks:\s*$(.*?)(?=^\S|\Z)", text)
        if not block:
            return []
        targets = []
        for line in block.group(1).splitlines():
            for match in re.finditer(r"\[([^\]]*)\]", line):
                targets += [t.strip() for t in match.group(1).split(",") if t.strip()]
        return targets

    def _models_by_group(self, text):
        groups = {}
        current = None
        for line in text.splitlines():
            name = re.match(r"\s*-\s*model_name:\s*(\S+)\s*$", line)
            if name:
                current = name.group(1)
                groups.setdefault(current, [])
                continue
            model = re.match(r"\s*model:\s*(\S+)\s*$", line)
            if model and current:
                groups[current].append(model.group(1))
        return groups

    def _provider_is_off(self, ref, providers):
        prefix = ref.split("/", 1)[0] if "/" in ref else ""
        provider = providers.get(prefix)
        # "openai" is a transport prefix several providers share (meta_api's
        # Muse, free_ai's qwen7b), so it can never condemn a leg here; an
        # unresolvable prefix is not evidence either.
        if not isinstance(provider, dict) or prefix == "openai":
            return False
        return provider.get("available") is False

    def test_no_fallback_chain_ends_in_an_all_dead_group(self):
        text = real_litellm_config()
        providers = real_registry()["providers"]
        models = self._models_by_group(text)
        for target in self._fallback_targets(text):
            legs = models.get(target)
            self.assertIsNotNone(legs, "fallbacks name %r, which has no group" % target)
            self.assertTrue(
                [ref for ref in legs if not self._provider_is_off(ref, providers)],
                "fallback chain ends in %r, whose every model is an off provider: %s"
                % (target, ", ".join(legs)))

    def test_the_committed_config_explains_the_dropped_chains(self):
        # The guard above is satisfied by an empty `fallbacks` block only when the
        # file says so right above the key; a silent removal reads like an
        # oversight and gets reverted.
        lines = real_litellm_config().splitlines()
        at = next((i for i, line in enumerate(lines)
                   if re.match(r"\s*fallbacks:\s*\[\]\s*$", line)), None)
        self.assertIsNotNone(at, "config.yaml has no emptied `fallbacks: []` key")
        block = []
        for line in reversed(lines[:at]):
            if not line.lstrip().startswith("#"):
                break
            block.append(line)
        note = " ".join(reversed(block)).lower()
        self.assertIn("fallback", note)
        self.assertTrue(
            any(word in note for word in
                ("deepseek", "openrouter", "402", "dead", "unservable", "off")),
            "the note above fallbacks says nothing about why the chains are gone")


class RouteContextCapTests(unittest.TestCase):
    """PROVFIX3 finding 1: a route's declared context is a promise a client sizes
    its requests against. A priority route falls to its next leg whenever the head
    refuses, so the promise may only be as big as the SMALLEST context among the
    legs it can actually be served by - otherwise the fallback leg 400s
    mid-conversation (docs/models.md: "a 200k model in a 1M-declared route would
    400"). The renderers clamp, so no declaration can carry the lie.

    Gated legs (provider off, route-gated, client-bound, policy-denied) do NOT
    clamp: a leg the gateway will never be asked to serve cannot be the reason a
    client is throttled. A leg with no recorded window doesn't clamp either -
    absence of data is not evidence of a small model.
    """

    def _reg(self):
        reg = synthetic_gateway_registry()
        reg["models"]["first"]["context_advertised"] = 1_048_576
        reg["models"]["second"]["context_advertised"] = 131_072
        reg["routes"]["mix"]["surfaces"]["omniroute"]["context_declared"] = "1M"
        return reg

    def _combo(self, reg):
        return {c["name"]: c for c in registry.render_omniroute(reg)["combos"]}["mix"]

    def test_combo_context_is_clamped_to_the_smallest_servable_leg(self):
        self.assertEqual(self._combo(self._reg())["context"], "128k")

    def test_a_promise_every_servable_leg_can_take_is_left_alone(self):
        reg = self._reg()
        reg["models"]["second"]["context_advertised"] = 1_048_576
        self.assertEqual(self._combo(reg)["context"], "1M")

    def test_a_gated_leg_does_not_lower_the_promise(self):
        reg = self._reg()
        reg["models"]["deadmodel"]["context_advertised"] = 1024
        reg["models"]["bound"]["context_advertised"] = 1024
        self.assertEqual(self._combo(reg)["context"], "128k")

    def test_a_leg_with_no_recorded_window_does_not_clamp(self):
        reg = self._reg()
        del reg["models"]["second"]["context_advertised"]
        self.assertEqual(self._combo(reg)["context"], "1M")

    def test_the_real_t1_combo_does_not_promise_more_than_gemini_takes(self):
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        # CIGREEN: expectation moved by 20c4a816 (TASK3 raised the twin
        # gemini-3.8-flash rows 131072->1048576, so every servable t1 leg now
        # advertises 1M). Pins the clamp RULE against the real data instead of
        # the stale 128k: each combo promises exactly the min over its
        # servable legs' advertised windows (usable defaults are D8 50%
        # unprobed placeholders, never the clamp input - see
        # route_context_cap). The 1048576 pin fails loudly if the band moves.
        reg = real_registry()
        for route_id in ("t1-orchestrator", "t1-orchestrator-free-only"):
            route = reg["routes"][route_id]
            cap = registry.route_context_cap(route, reg)
            self.assertEqual(cap, 1048576, route_id)
            self.assertEqual(combos[route_id]["context"],
                             registry.context_tokens_to_label(cap), route_id)
        # spark-1.3-contributor's only servable leg is the 1M contributor model,
        # so its promise is not clamped.
        self.assertEqual(combos["spark-1.3-contributor"]["context"], "1M")

    def test_no_combo_in_the_real_render_overshoots_a_leg_it_serves(self):
        # The rule as a loop over real data, not just the two flagged combos:
        # every combo's declared window is <= every model it lists.
        reg = real_registry()
        rendered = registry.render_omniroute(reg)
        for combo in rendered["combos"]:
            declared = registry.context_label_to_tokens(combo["context"])
            if declared is None:
                continue
            for ref in combo["models"]:
                window = registry.leg_advertised_context(ref, reg)
                if window is None:
                    continue
                self.assertLessEqual(
                    declared, window,
                    "combo %s declares %s (%d) but falls to %s at %d"
                    % (combo["name"], combo["context"], declared, ref, window))

    def test_docs_promise_carries_the_clamped_window(self):
        # CIGREEN: expectation moved by 20c4a816 (same 1M window raise as the
        # combo twin above: route cap is 1048576, so the declared "1M"
        # survives the clamp). Pins the rule: the docs cell is the declared
        # window clamped to the servable legs, not the raw declaration.
        reg = real_registry()
        route = reg["routes"]["t1-orchestrator"]
        promise = registry._route_context_promise(route, reg)
        self.assertEqual(
            promise,
            registry.clamp_route_context(
                route, reg, route["surfaces"]["omniroute"]["context_declared"]))
        self.assertEqual(promise, "1M")


class IdeContextAndEffortFollowServedLegsTests(unittest.TestCase):
    """PROVFIX3 finding 1 (picker side) and finding 8: catalog/ide-models.json is
    what the client pickers read, so it must carry the same clamped window as the
    combo, and its effort ladder / default effort must describe the leg that will
    actually answer - the first SERVED leg, not the first DECLARED one. A default
    the served head rejects is dropped rather than forwarded (combos.json's old
    rule: "a combo NEVER forwards an effort its head leg rejects")."""

    def _t1_gated_to_vertex_gemini(self):
        # FREEWIRE 2026-09-30: the gemini/gemini-3.8-flash head was removed from
        # every route, so the single-served-leg fixture now uses the vertex leg
        # that shares the same gemini-3.8-flash model row (ladder low/medium/high,
        # no xhigh).
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["t1-orchestrator"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            if leg != "vertex/gemini-3.8-flash":
                route["unavailable_legs"][leg] = {"available": False}
        reg["routes"]["t1-orchestrator"]["legs"] = ["vertex/gemini-3.8-flash"]
        return reg

    def _ide_entry(self, reg, route_id):
        return {m["id"]: m for m in registry.render_ide(reg)["models"]}[route_id]

    def test_the_real_t1_picker_window_is_clamped(self):
        # CIGREEN: expectation moved by 20c4a816 + 018438ed (1M band: every
        # servable t1 leg advertises 1048576, so the clamp keeps the 1M surface
        # context). Pins the rule: the picker window is the canonical surface
        # context (IDE_GATEWAYS puts omniroute first) clamped to the servable
        # legs, never above any leg's window.
        reg = real_registry()
        entry = self._ide_entry(reg, "t1-orchestrator")
        route = reg["routes"]["t1-orchestrator"]
        self.assertEqual(
            entry["context"],
            registry.clamp_route_context(
                route, reg, route["surfaces"]["omniroute"]["context"]))
        self.assertEqual(entry["context"], 1000000)

    def test_effort_ladder_comes_from_the_first_servable_leg(self):
        entry = self._ide_entry(self._t1_gated_to_vertex_gemini(), "t1-orchestrator")
        self.assertEqual(entry["effort_ladder"], ["low", "medium", "high"])

    def test_an_effort_default_the_served_head_rejects_is_dropped(self):
        entry = self._ide_entry(self._t1_gated_to_vertex_gemini(), "t1-orchestrator")
        self.assertNotIn("reasoning_effort", entry)

    def _t1_headed_by_contributor(self):
        reg = copy.deepcopy(real_registry())
        route = reg["routes"]["t1-orchestrator"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            if leg != "meta_api/muse-spark-1.3-contributor":
                route["unavailable_legs"][leg] = {"available": False}
        return reg

    def test_a_default_the_served_head_carries_is_still_forwarded(self):
        # FREEKEYS-2 (D-141) made a free leg the real head, and gemini's ladder
        # tops out at "high" - so the positive branch needs a registry whose
        # served head DOES carry the surface default: gate the band and
        # meta_api/muse-spark-1.3-contributor answers with its "xhigh".
        entry = self._ide_entry(self._t1_headed_by_contributor(), "t1-orchestrator")
        self.assertEqual(entry.get("reasoning_effort"), "xhigh")
        self.assertIn("xhigh", entry["effort_ladder"])

    def test_the_real_free_head_keeps_its_own_default(self):
        # CIGREEN: expectation moved by 018438ed (TASK1 put the gemini head
        # first: ladder low/medium/high, no xhigh). render_ide()'s served-head
        # rule drops a surface default the head rejects instead of forwarding
        # it, so the picker is not offered a level the answering leg refuses.
        entry = self._ide_entry(real_registry(), "t1-orchestrator")
        self.assertEqual(entry["effort_ladder"], ["low", "medium", "high"])
        self.assertNotIn("reasoning_effort", entry)

    def test_openhands_max_input_tokens_is_clamped(self):
        # CIGREEN: expectation moved by 20c4a816 (TASK3 window raise) +
        # 018438ed (TASK1 1M-only band): the min servable t1 window is 1M, so
        # the clamp keeps the 1M profile value. Pins the rule: each tier
        # carries its surface profile value clamped to the servable legs.
        reg = real_registry()
        tiers = {t["id"]: t for t in
                 registry.render_openhands(reg)["tiers"]}
        for tier_id in ("omniroute-t1-orchestrator", "litellm-t1-orchestrator"):
            gw, route_id = tier_id.split("-", 1)
            route = reg["routes"][route_id]
            profile = route["surfaces"][gw]["openhands_profile"]
            self.assertEqual(
                tiers[tier_id]["max_input_tokens"],
                registry.clamp_route_context(
                    route, reg, profile["max_input_tokens"]),
                tier_id)
            self.assertEqual(tiers[tier_id]["max_input_tokens"], 1000000, tier_id)


class VertexNoKeyLitellmSkipTests(unittest.TestCase):
    """T1-CLEAN-4 K6 (2026-10-01): providers.vertex_ai authenticates from a GCP
    service-account JSON (its own $comment says the credential is NOT a plain API
    key) and declares no litellm_env, yet the managed LiteLLM blocks rendered
    `api_key: os.environ/VERTEX_API_KEY` - a guessed name for a variable that does
    not exist, the same unset-key hazard GATEWAY_ONLY exists to avoid
    (the META_API_KEY lesson). The renderer now drops such a leg and NAMES it."""

    def setUp(self):
        self.reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.text = LITELLM_CONFIG_PATH.read_text(encoding="utf-8")
        self.blocks = registry.render_litellm_blocks(self.reg, self.text)

    def test_no_keyless_provider_leg_is_rendered(self):
        for tier, block in sorted(self.blocks.items()):
            self.assertNotIn("model: vertex/", block, tier)
            self.assertNotIn("VERTEX_API_KEY", block, tier)

    def test_a_dropped_leg_is_named_in_a_rendered_comment(self):
        tiers = [t for t, r in self.reg["routes"].items()
                 if isinstance(r, dict)
                 and any(l.startswith("vertex/") for l in (r.get("legs") or []))]
        self.assertTrue(tiers, "no route carries a vertex leg any more?")
        named = [t for t in tiers if t in self.blocks]
        self.assertTrue(named, "no managed tier carries the vertex leg: %s" % tiers)
        for tier in named:
            self.assertIn("# litellm-skip: vertex/gemini-3.8-flash", self.blocks[tier], tier)

    def test_the_committed_config_matches_the_render(self):
        self.assertEqual(registry.litellm_diff(self.blocks, self.text), [])

    def test_a_key_provider_keeps_its_leg(self):
        # the drop is declared-auth only: providers that do have a LiteLLM key
        # (the <UPPER>_API_KEY convention) must keep rendering, or this "fix"
        # would silently gut the mirror.
        joined = "\n".join(self.blocks.values())
        self.assertIn("model: ovhcloud/gpt-oss-120b", joined)
        self.assertIn("OVHCLOUD_API_KEY", joined)


class CombosDeclaredAuthSkipTests(unittest.TestCase):
    """T1-CLEAN-4 rework F2/F3/F4: tools/sync-router-tiers.py's --combos path
    applies the same declared-auth drop as the registry path, and the drop,
    its naming, and its per-call state were all untested."""

    def setUp(self):
        self.sync = registry._load_sync_router_tiers()
        self.reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.sync.PROVIDER_PREFIX, self.sync.API_BASE, self.sync.ENV_KEY = (
            self.sync.provider_maps_from_dict(self.reg["providers"]))

    def _combos(self, tmp, combos):
        path = Path(tmp) / "combos.json"
        path.write_text(json.dumps({"combos": combos}), encoding="utf-8")
        return str(path)

    def test_a_declared_auth_combo_leg_is_dropped_and_named(self):
        # F2: combos_refs' `no_key` clause had no test - deleting it survived.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "t1-orchestrator", "models": [
                "vertex/gemini-3.8-flash", "ovhcloud/gpt-oss-120b"]}])
            refs = self.sync.combos_refs(path, registry=self.reg)
            self.assertEqual(refs["t1-orchestrator"], ["ovhcloud/gpt-oss-120b"])
            self.assertEqual(self.sync.SKIPPED_BY_TIER["t1-orchestrator"],
                             ["vertex/gemini-3.8-flash"])
            block = self.sync.render_block("t1-orchestrator",
                                           refs["t1-orchestrator"])
            self.assertIn("# litellm-skip: vertex/gemini-3.8-flash",
                          "\n".join(block), block)

    def test_each_call_owns_the_skip_state_it_leaves_behind(self):
        # F3: SKIPPED_BY_TIER is module state that was only ever .update()d and
        # assigned per tier, never cleared - a second call in one process left
        # the first call's skips sitting there for render_block() to pick up.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "t2-worker", "models": [
                "vertex/gemini-3.8-flash", "ovhcloud/gpt-oss-120b"]}])
            self.sync.combos_refs(path, registry=self.reg)
            second = self._combos(tmp, [{"name": "t1-orchestrator", "models": [
                "ovhcloud/gpt-oss-120b"]}])
            combos = self.sync.combos_refs(second, registry=self.reg)
            self.assertEqual(set(self.sync.SKIPPED_BY_TIER), {"t1-orchestrator"})
            block = "\n".join(self.sync.render_block(
                "t2-worker", combos.get("t2-worker", [])))
            self.assertNotIn("litellm-skip", block)

    def test_the_registry_call_clears_a_prior_combos_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "t2-worker", "models": [
                "vertex/gemini-3.8-flash", "ovhcloud/gpt-oss-120b"]}])
            self.sync.combos_refs(path, registry=self.reg)
            self.sync.registry_refs(str(REGISTRY_PATH), tiers=("t4-rag",))
            self.assertEqual(set(self.sync.SKIPPED_BY_TIER), {"t4-rag"})

    def test_a_combo_dropped_whole_still_gets_its_skip_named(self):
        # F4: the default tier set derived from the POST-filter list, so a combo
        # whose every leg is a declared-auth skip got no block and its
        # "# litellm-skip:" line rendered nowhere - the drop went invisible.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "vertex-gemini-3.8-flash",
                                       "models": ["vertex/gemini-3.8-flash"]}])
            combos = self.sync.combos_refs(path, registry=self.reg)
            self.assertEqual(combos, {})
            self.assertEqual(self.sync.SKIPPED_BY_TIER["vertex-gemini-3.8-flash"],
                             ["vertex/gemini-3.8-flash"])
            self.assertIn("vertex-gemini-3.8-flash: vertex/gemini-3.8-flash",
                          self.sync.unmanaged_skip_notices(combos))

    def test_a_combo_dropped_only_by_gateway_only_stays_unnamed(self):
        # the notice is for the declared-auth drop only: an all-GATEWAY_ONLY
        # combo was never a LiteLLM leg at all (the opus-4-6 case combos_refs
        # documents), and naming it would be a different rule with one home.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "opus-4-6",
                                       "models": ["cc/opus-4-6"]}])
            combos = self.sync.combos_refs(path, registry=self.reg)
            self.assertEqual(combos, {})
            self.assertEqual(self.sync.unmanaged_skip_notices(combos), [])


if __name__ == "__main__":
    unittest.main()
