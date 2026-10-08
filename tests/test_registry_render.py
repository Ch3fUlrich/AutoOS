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
                         combos_by_name["l2-worker-clean"]["models"])
        # D657-CHAIN 2026-10-08: expectation moved by the re-chain - l2-worker is
        # bazaarlink -> openrouter nemotron -> vertex now, so the served-leg example
        # moves to l3-driver, which still carries the re-opened groq leg
        # (FREEWIRE 2026-09-30: allow-groq-gpt-oss proved it on a tool-call probe).
        self.assertIn("groq/openai/gpt-oss-120b",
                      combos_by_name["l3-driver"]["models"])
        # the OpenRouter BYOK gpt-oss-120b leg is gated (measured 401,
        # credits exhausted 2026-09-27), so it does not reach the combo.
        self.assertNotIn("openrouter/openai/gpt-oss-120b",
                         combos_by_name["l2-worker"]["models"])
        # FREEWIRE 2026-09-30: the DSMAX provider-level gate moved to the route
        # level (providers.openrouter.available is now true for its ':free'
        # ids); the paid openrouter deepseek leg stays route-gated.
        self.assertNotIn("openrouter/deepseek/deepseek-v4.1-flash",
                         combos_by_name["l2-worker"]["models"])
        # CIGREEN's `gemini/gemini-3.8-flash` head is gone for good: D657-CHAIN
        # measured the AI Studio spelling at HTTP 401 on both gateways (no quota),
        # so it is route-gated and reaches no render - `vertex/gemini-3.8-flash` is
        # the gemini that serves. Pinned as absent across the whole render.
        self.assertNotIn("gemini/gemini-3.8-flash", json.dumps(rendered))

    def test_paid_and_auto_routes_have_no_combo(self):
        # l2-worker-paid/l3-driver-paid (LiteLLM-only) and
        # auto/auto-smart/auto-cheap (OmniRoute's dynamic strategy) carry
        # legs: [] in the registry and have no combos.json counterpart.
        # MUSEAPI 2026-09-27 moved l1-orchestrator-paid out of that set: it
        # declares a servable leg now, so it renders a combo - test_the_legless
        # _route_renders_no_combo below pins the rule that made it an exception.
        rendered = registry.render_omniroute(real_registry())
        names = {c["name"] for c in rendered["combos"]}
        for absent in ("l2-worker-paid", "l3-driver-paid",
                      "auto", "auto/smart", "auto/cheap"):
            self.assertNotIn(absent, names)

    def test_the_legless_route_renders_no_combo(self):
        # The rule, kept general: a route with legs: [] is deliberately
        # LiteLLM-only and never becomes a combo, however it is spelled.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["l1-orchestrator-paid"]["legs"] = []
        names = {c["name"] for c in registry.render_omniroute(reg)["combos"]}
        self.assertNotIn("l1-orchestrator-paid", names)


class GatewayRefTests(unittest.TestCase):
    """AGYID/AGYCANON/CLAUDE55: the live OmniRoute catalog flipped twice - to
    canonical antigravity/* (re-measured 2026-09-30, AGYCANON retired the agy
    prefix) and back to agy/* (re-measured 2026-10-05, CLAUDE55: the 4-6
    generation is retired upstream and the operator ordered the swap to the
    agy/claude-{opus,sonnet}-5-5* spellings) - so
    providers.antigravity.model_prefix is 'agy' again and gateway_ref()
    rewrites this provider's legs to it. The model_prefix mechanism itself
    stays live for providers that genuinely diverge (scaleway -> scw)."""

    def test_render_omniroute_renders_antigravity_by_its_canonical_id(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted every
        # antigravity leg, 2026-10-01) and moved back by the operator's
        # CLAUDE55 order (2026-10-05): the provider is re-opened with
        # model_prefix 'agy' (the live catalog's canonical spelling again),
        # so gateway_ref emits agy/* and the real render carries exactly the
        # 5-5 spellings apply's validation checks.
        self.assertEqual(
            real_registry()["providers"]["antigravity"].get("model_prefix"),
            "agy")
        self.assertEqual(
            registry.gateway_ref("antigravity/claude-sonnet-5-5-medium",
                                 real_registry()),
            "agy/claude-sonnet-5-5-medium")
        # D657-CHAIN 2026-10-08: both antigravity legs are route-gated now (the
        # probe answered 429 on both gateways, and spec SS8 forbids agy as a head),
        # so the committed render carries neither spelling - opus-5-5 is omitted and
        # l1-orchestrator no longer lists the sonnet leg. The re-spelling itself is
        # pinned below on a copy that lifts the gate, so the mechanism stays covered
        # while the provider is off.
        rendered = registry.render_omniroute(real_registry())
        self.assertNotIn("agy/claude-opus-5-5-medium", json.dumps(rendered))
        ungated = copy.deepcopy(real_registry())
        ungated["routes"]["opus-5-5"].pop("unavailable_legs", None)
        re_gated = registry.render_omniroute(ungated)
        by_name = {c["name"]: c for c in re_gated["combos"]}
        self.assertEqual(by_name["opus-5-5"]["models"],
                         ["agy/claude-opus-5-5-medium"])
        # No registry spelling may leak into the gateway render, and no pre-CLAUDE55
        # 4-6 generation may resurrect.
        self.assertNotIn("antigravity/", json.dumps(re_gated))
        self.assertNotIn("claude-opus-4-6", json.dumps(re_gated))

    def test_render_omniroute_still_applies_a_declared_model_prefix(self):
        # The mechanism AGYID added (and this AGYCANON change must not break): a
        # provider whose catalog id differs from its registry id renders under the
        # declared model_prefix. SCWREMOVAL 2026-10-06 retired the scaleway example
        # with the provider and D657-CHAIN 2026-10-08 gated ovhcloud
        # (available: false, six spellings x both gateways answered 404), so the
        # live example was bazaarlink -> bzl, which now heads l1-orchestrator.
        # OVH-REPROBE 2026-10-08 (AO-DENYLEGS open item 1) puts ovhcloud back on
        # the live side of that example — 200 on re-probe, an L3 leg only — so the
        # render carries BOTH prefix spellings, and the re-spelling is pinned in
        # the render instead of only as a provider fact.
        self.assertEqual(registry.gateway_ref("ovhcloud/Qwen3.8-27B", real_registry()),
                         "ovh/Qwen3.8-27B")
        self.assertIs(real_registry()["providers"]["ovhcloud"].get("available"), True)
        rendered = registry.render_omniroute(real_registry())
        by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertEqual(by_name["l1-orchestrator"]["models"][0],
                         "bzl/deepseek/deepseek-v4-flash-0731free:free")
        self.assertIn("ovh/Qwen3.8-27B", by_name["l3-implementer"]["models"])
        # and no orchestrator layer renders it (OVH documents no prompt cache)
        for name, combo in by_name.items():
            if name.startswith(("l0-", "l1-", "l2-")):
                for model in combo["models"]:
                    self.assertFalse(model.startswith("ovh/"), (name, model))

    def test_render_omniroute_leaves_other_providers_unchanged(self):
        # Providers without a model_prefix keep their registry spelling in
        # the render. D657-CHAIN 2026-10-08 moved the examples: mistral-code-latest
        # and the deepseek DIRECT leg left every chain (probe-banned: §8 drops the
        # paid deepseek leg, and mistral answered no ack on either gateway), so the
        # unprefixed spellings pinned here are groq, cohere and vertex.
        rendered = registry.render_omniroute(real_registry())
        by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("groq/qwen/qwen3.8-27b", by_name["l3-driver"]["models"])
        self.assertIn("cohere/command-a-03-2025", by_name["l3-driver"]["models"])
        self.assertIn("vertex/gemini-3.8-flash",
                      by_name["l2-worker-clean"]["models"])
        for gone in ("mistral/mistral-code-latest", "deepseek/deepseek-flash"):
            self.assertNotIn(gone, json.dumps(rendered))

    def test_registry_legs_keep_their_own_spelling(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted the
        # antigravity legs from the bands, provider available:false). Pins the
        # same rule on a surviving multi-segment leg: the registry spelling is
        # kept verbatim and resolve_leg still splits at the first '/'.
        self.assertIn("openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                      real_registry()["routes"]["l2-worker"]["legs"])
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
        reg["routes"]["l3-driver-clean"]["legs"][0] = "ghost-provider/ghost-model"
        path = write_registry(reg)
        try:
            proc = run_cli("render", "omniroute", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("combos.l3-driver-clean", proc.stdout)

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
        # leg after gateway_legs()/GATEWAY_ONLY gets a block. l1-orchestrator
        # used to be hand-kept, then fail-closed 2026-09-27 (Zen client-bound,
        # OpenRouter off) and lost its block; t4-rag and l2-worker-clean never
        # had markers.
        reg = real_registry()
        rendered = registry.render_litellm_blocks(reg, real_litellm_config())
        sync = registry._load_sync_router_tiers()
        self.assertEqual(set(rendered), set(sync.managed_tiers(reg)))
        # D657-CHAIN 2026-10-08: l2-worker-clean is no longer in this set - its
        # chain collapsed to the single vertex leg, which authenticates without a
        # LiteLLM api_key, so the mirror strips it (pinned in
        # test_a_route_with_no_litellm_servable_leg_gets_no_block). The three
        # §3/§2 routes that joined the render take its place as the example.
        for managed in ("l2-worker", "t4-rag", "l3-implementer", "l3-review-diff"):
            self.assertIn(managed, rendered)

    def test_a_route_with_no_litellm_servable_leg_gets_no_block(self):
        # An all-gateway-only route (opus-5-5: CLAUDE55 2026-10-05 renamed the
        # opus-4-6 route; its antigravity leg has no litellm_env, so it renders
        # no block either) and the samba one-leg routes (provider
        # available:false) render no block at all - the same shape
        # render_omniroute() gives an all-dead route, not an empty model list.
        # l1-orchestrator-free-only is NOT gone: T1FREE gave it a gemini
        # servable leg. l3-driver-free-only left this set when FREEAI gave it
        # a servable free_ai/qwen7b leg.
        # D657-CHAIN 2026-10-08 adds the clean band and two omitted singles: every
        # *-clean route now serves only vertex/gemini-3.8-flash (litellm_auth, no
        # api_key -> stripped), and providers.ovhcloud.available: false empties the
        # three ovh-* routes, so they render nothing here and sit in combos.json's
        # `omitted` there. OVH-REPROBE 2026-10-08 restores the provider, so
        # ovh-qwen3.8-27b renders its block again (it carries an OVH api_key);
        # ovh-gpt-oss-120b stays in this set because its leg is gated per-leg as
        # unpriced — which is the shape this test exists to pin: an all-gated
        # route renders nothing. ovh-qwen3-coder-30b left with OVHCODER-DROP
        # 2026-10-08, and not as an example of that shape: OVH withdrew the id
        # upstream, so the route was deleted and its id retired rather than
        # declared-and-gated. A deleted route is not a declaration that renders
        # nothing, so it belongs to no set here (pinned in
        # test_ovh_coder_leg_is_retired_not_merged).
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        for gone in ("opus-5-5", "samba/gpt-oss-120b", "samba/MiniMax-M3",
                     "l2-worker-clean", "l3-driver-clean", "l1-orchestrator-clean",
                     "ovh-gpt-oss-120b"):
            self.assertNotIn(gone, rendered)
        self.assertIn("ovh-qwen3.8-27b", rendered)
        self.assertIn("os.environ/OVHCLOUD_API_KEY", rendered["ovh-qwen3.8-27b"])

    def test_a_legless_hand_group_is_never_rendered(self):
        # l2-worker-paid/l3-driver-paid declare no legs; they are hand-curated
        # fallback chains and must stay outside the AUTOOS-MANAGED markers.
        # MUSEAPI 2026-09-27 had given l1-orchestrator-paid a leg; D657-CHAIN
        # 2026-10-08 took it back out (spec SS8 removes the paid muse leg from every
        # chain), so the route is legless again and joins this set - the marker was
        # pruned, not left behind stale.
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        for paid in ("l2-worker-paid", "l3-driver-paid", "l1-orchestrator-paid"):
            self.assertNotIn(paid, rendered)

    def test_the_legged_paid_route_is_rendered(self):
        # MUSEAPI 2026-09-27 gave l1-orchestrator-paid a leg and the registry took
        # over the block. D657-CHAIN 2026-10-08 returned the committed route to
        # `legs: []` (spec SS8 bans the paid muse leg from every chain), so the
        # committed config carries no marker for it any more and the mechanism - a
        # paid route WITH legs gets a registry-owned block, and losing its legs
        # loses the marker too - is pinned on a synthetic leg plus the marker pair
        # a human has to hand-write (render_litellm_blocks refuses to invent one).
        anchor = "  # AUTOOS-MANAGED-END l1-orchestrator-free-only\n"
        cfg = real_litellm_config().replace(
            anchor,
            anchor + "\n  # AUTOOS-MANAGED-START l1-orchestrator-paid\n"
                     "  # AUTOOS-MANAGED-END l1-orchestrator-paid\n", 1)
        self.assertNotIn("l1-orchestrator-paid",
                         registry.render_litellm_blocks(real_registry(), real_litellm_config()))
        reg = copy.deepcopy(real_registry())
        reg["routes"]["l1-orchestrator-paid"]["legs"] = ["meta_api/muse-spark-1.3-contributor"]
        rendered = registry.render_litellm_blocks(reg, cfg)
        block = rendered["l1-orchestrator-paid"]
        self.assertIn("model: openai/muse-spark-1.3-contributor", block)
        self.assertIn("api_base: https://api.meta.ai/v1", block)
        self.assertIn("api_key: os.environ/META_API_KEY", block)
        self.assertIn("  # AUTOOS-MANAGED-START l1-orchestrator-paid\n", block)
        self.assertIn("  # AUTOOS-MANAGED-END l1-orchestrator-paid", block)

        # ...and the rule behind the old test survives: strip the legs and the
        # block is gone again, marker and all.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["l1-orchestrator-paid"]["legs"] = []
        self.assertNotIn("l1-orchestrator-paid",
                         registry.render_litellm_blocks(reg, cfg))

    def test_gateway_only_leg_is_dropped_not_silently_kept_or_missing(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY deleted exactly the
        # antigravity fixture leg from l2-worker). The drop-behavior is now
        # pinned with a synthetic gateway-only leg: cc sits in
        # tools/sync-router-tiers.py GATEWAY_ONLY, so with its provider
        # re-opened the leg IS served by the gateway combo yet still dropped
        # from the LiteLLM mirror - dropped, not silently kept - while a real
        # sibling leg stays mirrored and the fixture leg stays declared.
        # D657-CHAIN 2026-10-08 moved the surviving sibling: ovhcloud's legs left
        # the route with the provider gated, so the bazaarlink free leg is the
        # mirrored one.
        reg = copy.deepcopy(real_registry())
        reg["providers"]["cc"]["available"] = True
        leg = "cc/claude-opus-4-6"
        reg["routes"]["l2-worker"]["legs"] = (
            reg["routes"]["l2-worker"]["legs"] + [leg])
        legs = reg["routes"]["l2-worker"]["legs"]
        self.assertIn(leg, legs)
        rendered = registry.render_litellm_blocks(reg, real_litellm_config())
        self.assertNotIn("claude-opus-4-6", rendered["l2-worker"])
        combos = {c["name"]: c for c in registry.render_omniroute(reg)["combos"]}
        self.assertIn(leg, combos["l2-worker"]["models"])
        self.assertIn("bazaarlink/deepseek/deepseek-v4-flash-0731free:free",
                      rendered["l2-worker"])



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
        reg["routes"]["l3-driver"]["legs"][0] = "ghost-provider/ghost-model"
        path = write_registry(reg)
        try:
            proc = run_cli("render", "litellm", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("differs: l3-driver", proc.stdout)

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
        # surface. Spark fails closed on the real registry (D657-CHAIN 2026-10-08
        # gated its paid meta_api leg under spec SS8 and its zen leg is
        # client-bound, so the route is in combos.json's `omitted` and has no ide
        # entry at all), so exercise the mechanism on a copy with the provider
        # re-funded and the route gate lifted: the extra surface key is still there
        # and the route still renders from its omniroute surface.
        reg = copy.deepcopy(real_registry())
        reg["providers"]["openrouter"]["available"] = True
        self.assertNotIn("spark-1.3-contributor",
                         [m["id"] for m in registry.render_ide(real_registry())["models"]])
        reg["routes"]["spark-1.3-contributor"].pop("unavailable_legs", None)
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
        reg["routes"]["l3-driver"]["surfaces"]["omniroute"]["context"] = 1
        path = write_registry(reg)
        try:
            proc = run_cli("render", "ide", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("models.l3-driver", proc.stdout)

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
        del reg["routes"]["l3-driver"]
        with self.assertRaises(ValueError) as ctx:
            registry.render_ide(reg)
        self.assertIn("l3-driver", str(ctx.exception))

    def test_an_unexpected_extra_route_raises_and_names_it(self):
        reg = copy.deepcopy(real_registry())
        reg["routes"]["brand-new-route"] = copy.deepcopy(reg["routes"]["l3-driver"])
        reg["routes"]["brand-new-route"]["id"] = "brand-new-route"
        with self.assertRaises(ValueError) as ctx:
            registry.render_ide(reg)
        self.assertIn("brand-new-route", str(ctx.exception))


class EffortLadderInRenderIdeTests(unittest.TestCase):
    """render_ide() derives effort_ladder from the model of the route's SERVED
    head leg (finding 8 - the leg that answers, not legs[0] when earlier legs are
    gated), filtering out "none" and omitting for legless routes, routes whose
    head declares no ladder, and routes that serve nothing at all (A6a)."""

    def test_effort_ladder_derived_from_head_leg(self):
        reg = copy.deepcopy(real_registry())
        # Pick a route whose head leg's model actually carries an effort_ladder
        # (l2-orchestrator heads antigravity/claude-sonnet-5-5-medium since the
        # operator's CLAUDE55 order; l2-worker was the fixture until GLM55
        # 2026-10-05 put oc/glm-5.3-flash - no ladder - at its head;
        # l1-orchestrator was the example before that until it fail-closed
        # 2026-09-27 and left the ide render).
        # D657-CHAIN + D657-EFFORT 2026-10-08 move it once more: l1-orchestrator is
        # back in the render but heads the free bazaarlink mirror, whose row
        # documents an EMPTY ladder, so the fixture is l2-orchestrator-clean (one
        # vertex leg, ladder low/medium/high) and l1-orchestrator is counter-pinned
        # as the no-ladder head case.
        route = reg["routes"]["l2-orchestrator-clean"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        model_entry = reg["models"][mid]
        self.assertIn("effort_ladder", model_entry)
        self.assertTrue(model_entry["effort_ladder"])
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertEqual(by_id["l2-orchestrator-clean"]["effort_ladder"],
                         [e for e in model_entry["effort_ladder"] if e != "none"])
        _pid, head_mid = registry.resolve_leg(
            registry.gateway_legs(reg["routes"]["l1-orchestrator"], reg)[0], reg)
        self.assertEqual(reg["models"][head_mid]["effort_ladder"], [])
        self.assertNotIn("effort_ladder", by_id["l1-orchestrator"])
        self.assertNotIn("reasoning_effort", by_id["l1-orchestrator"])

    def test_none_is_dropped_from_effort_ladder(self):
        reg = copy.deepcopy(real_registry())
        # GLM55 2026-10-05: the fixture moved from l2-worker - its head legs
        # changed twice (oc/glm-5.3-flash then the gate) and the render derives
        # the ladder from the first SERVABLE leg. D657-CHAIN 2026-10-08: that leg
        # of l2-worker-clean is now vertex/gemini-3.8-flash, still stable.
        route = reg["routes"]["l2-worker-clean"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        reg["models"][mid]["effort_ladder"] = ["none", "low", "medium", "high"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertEqual(by_id["l2-worker-clean"]["effort_ladder"], ["low", "medium", "high"])

    def test_no_effort_ladder_when_model_has_no_ladder(self):
        reg = copy.deepcopy(real_registry())
        # D657-CHAIN 2026-10-08: the fixture moved off l3-driver-clean, which now
        # heads vertex/gemini-3.8-flash - the SAME model row every other -clean
        # route heads, so popping its ladder emptied the control with it.
        # l3-review-codebase is the only route SS3 left that heads a ladder model
        # nobody else heads (openrouter nemotron-3-ultra :free).
        route = reg["routes"]["l3-review-codebase"]
        _pid, mid = registry.resolve_leg(route["legs"][0], reg)
        # Ensure the model has no effort_ladder
        reg["models"][mid].pop("effort_ladder", None)
        # l2-orchestrator-clean still has a ladder via the first leg's model
        # (l2-orchestrator was the control until D657-CHAIN put the bazaarlink
        # DeepSeek mirror - which documents no ladder - at its head; l2-worker was
        # the control before that, until GLM55 2026-10-05).
        t2_route = reg["routes"]["l2-orchestrator-clean"]
        _t2_pid, t2_mid = registry.resolve_leg(t2_route["legs"][0], reg)
        t2_ladder = reg["models"][t2_mid].get("effort_ladder", [])
        expected = [e for e in t2_ladder if isinstance(e, str) and e != "none"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertNotIn("effort_ladder", by_id["l3-review-codebase"])
        self.assertEqual(by_id["l2-orchestrator-clean"]["effort_ladder"], expected)

    def test_no_effort_ladder_when_route_has_no_legs(self):
        # The rule is about a route that declares no legs at all. SS8 (operator
        # D-657) removed the paid muse leg from l1-orchestrator-paid, so the real
        # route now genuinely declares none; the assignment is kept so the fixture
        # still means "no legs" if anyone legs it again.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["l1-orchestrator-paid"]["legs"] = []
        # l2-orchestrator-clean heads a model that carries a ladder (the
        # l2-orchestrator control moved with D657-CHAIN, see above).
        t2_route = reg["routes"]["l2-orchestrator-clean"]
        _t2_pid, t2_mid = registry.resolve_leg(t2_route["legs"][0], reg)
        t2_ladder = reg["models"][t2_mid].get("effort_ladder", [])
        expected = [e for e in t2_ladder if isinstance(e, str) and e != "none"]
        rendered = registry.render_ide(reg)
        by_id = {m["id"]: m for m in rendered["models"]}
        self.assertNotIn("effort_ladder", by_id["l1-orchestrator-paid"])
        self.assertEqual(by_id["l2-orchestrator-clean"]["effort_ladder"], expected)

    def test_the_contributor_ladder_reaches_every_surface_that_carries_one(self):
        # MUSEAPI step 3 pinned the contributor ladder on the routes the
        # contributor headed (ide-models effort_ladder and, from it, opencode.jsonc
        # per-effort `variants` - the #minimal/#low/#medium/#high/#xhigh pickers).
        # SS8 (operator D-657) took the paid meta_api muse leg out of every chain
        # and probe D-657 refused the free copies (403 on both gateways), so
        # spark-1.3-contributor declares legs and serves none: OR1d drops it off
        # every surface and l1-orchestrator-paid declares no legs at all. The
        # ladder now survives on exactly one surface - the direct-override model,
        # which is not a combo and never routes - and the cross-surface half of
        # this test walks whatever ladder the rendered routes still carry.
        ladder = real_registry()["models"]["muse-spark-1.3-contributor"]["effort_ladder"]
        self.assertEqual(ladder, ["minimal", "low", "medium", "high", "xhigh"])
        by_id = {m["id"]: m for m in real_ide_models()["models"]}
        self.assertNotIn("spark-1.3-contributor", by_id)
        self.assertNotIn("effort_ladder", by_id["l1-orchestrator-paid"])
        self.assertIn("spark-1.3-contributor", set(real_combos()["omitted"]))

        oc = json.loads(_strip_jsonc((ROOT / "opencode.jsonc").read_text(encoding="utf-8")))
        direct = oc["providers"]["omniroute"]["models"]["meta-direct-muse-spark-1.3-contributor"]
        self.assertEqual([v["id"] for v in direct["variants"]], ladder)
        for rung in direct["variants"]:
            self.assertEqual(rung["settings"], {"reasoningEffort": rung["id"]})

        # The half that did not move: a route's picker equals its rendered ladder
        # on every surface it is declared for, and a route with no ladder has no
        # picker. D657-CHAIN made every LiteLLM-served head the bazaarlink
        # DeepSeek mirror, which documents no ladder - so the walk below must find
        # omniroute only; a litellm picker appearing again is this assertion's
        # failure, not a silent extra surface.
        seen = set()
        for m in real_ide_models()["models"]:
            want = m.get("effort_ladder")
            for provider in m.get("surfaces", {}):
                models = oc["providers"][provider]["models"]
                got = [v["id"] for v in models[m["id"]].get("variants", [])]
                if want is None:
                    self.assertEqual(got, [], "%s/%s picker" % (provider, m["id"]))
                    continue
                self.assertEqual(got, want, "%s/%s variants" % (provider, m["id"]))
                for rung in models[m["id"]]["variants"]:
                    self.assertEqual(rung["settings"], {"reasoningEffort": rung["id"]})
                seen.add(provider)
        self.assertEqual(seen, {"omniroute"}, "a ladder reached a second surface")

    def test_an_omniroute_combo_cannot_carry_a_per_effort_alias(self):
        # The gap step 3 anticipated: a combos.json entry is
        # {name, strategy, context, models} and the gateway has no per-effort
        # parameter, so gemini-3.8-flash-low as a *combo* is not renderable.
        # D657-CHAIN moved this off spark-1.3-contributor (SS8 dropped it out of
        # the file), and the claim was never per-route, so it is pinned across
        # every combo: none of them is named after an effort rung. Pinned here so
        # the day the gateway grows a per-effort parameter, this test fails and the
        # alias is rendered on that surface too.
        combos = real_combos()["combos"]
        for c in combos:
            self.assertEqual(sorted(c), ["context", "models", "name", "strategy"], c["name"])
        rungs = set()
        for model in real_registry()["models"].values():
            rungs.update(e for e in (model.get("effort_ladder") or [])
                         if isinstance(e, str) and e != "none")
        self.assertIn("low", rungs)
        aliased = sorted(n["name"] for n in combos
                         if any(n["name"].endswith("-" + r) for r in rungs))
        self.assertEqual(aliased, [])


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
        # SS8 (operator D-657) gated every spark leg, so OR1d drops the tier from
        # the real render (asserted in test_the_contributor_ladder...); exercise
        # the mapping on a copy with the gates lifted, as this test always did for
        # the DSMAX gate.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["spark-1.3-contributor"]["unavailable_legs"] = {}
        rendered = registry.render_openhands(reg)
        by_id = {t["id"]: t for t in rendered["tiers"]}
        direct = by_id["openrouter-muse-spark-1.3-contributor"]
        self.assertEqual(direct["gateway"], "openrouter")
        self.assertEqual(direct["model"], "openrouter/meta/muse-spark-1.3-contributor")
        self.assertEqual(direct["base_url"], "https://openrouter.ai/api/v1")

    def test_or1d_drops_the_direct_profile_tier_with_its_gated_route(self):
        # The other side of the same rule: OR1d is keyed on the ROUTE, so a route
        # that declares legs and serves none loses its standalone direct_profile
        # tier as well - having its own key does not exempt it.
        ids = {t["id"] for t in registry.render_openhands(real_registry())["tiers"]}
        self.assertNotIn("openrouter-muse-spark-1.3-contributor", ids)


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
        reg["routes"]["l3-driver"]["surfaces"]["omniroute"]["openhands_profile"]["max_input_tokens"] = 1
        path = write_registry(reg)
        try:
            proc = run_cli("render", "openhands", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("tiers.omniroute-l3-driver", proc.stdout)

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
        del reg["routes"]["l3-driver"]["surfaces"]["omniroute"]["openhands_profile"]
        with self.assertRaises(ValueError) as ctx:
            registry.render_openhands(reg)
        self.assertIn("omniroute-l3-driver", str(ctx.exception))

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
            # l1-orchestrator was the canary until it fail-closed 2026-09-27
            # (omitted: Zen client-bound, OpenRouter off); l2-worker is the
            # servable equivalent.
            written = Path(d) / "profiles" / "omniroute-l2-worker.json"
            self.assertTrue(written.exists())
            self.assertEqual(json.loads(written.read_text())["model"], "openai/l2-worker")


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
        reg["routes"]["l3-driver"]["surfaces"]["omniroute"]["context_declared"] = "999k"
        rendered = registry.render_models_doc(reg)
        self.assertIn("999k", row_for(rendered, "l3-driver"))

    def test_context_column_falls_back_to_the_surfaces_numeric_context(self):
        reg = copy.deepcopy(real_registry())
        del reg["routes"]["l3-driver"]["surfaces"]["omniroute"]["context_declared"]
        rendered = registry.render_models_doc(reg)
        # FREEKEYS-2c: the numeric fallback is clamped like every other context
        # cell (clamp_route_context narrows a numeric surface context too).
        # D657-CHAIN 2026-10-08 moved the clamped number from 128,000 to 131,072:
        # the scaleway/nebius 128k legs left the chain and the smallest window a
        # serving leg now advertises is the groq/cohere 131072 band, which equals
        # the surface promise - so the cell follows the numeric field rather than
        # the deleted "128k" label. OVH-REPROBE 2026-10-08 puts a 128,000 leg back
        # in the chain, and the clamp follows it: the cell narrows under the
        # smallest serving leg, which is the mechanism this test owns. The
        # comma-spelled number is the fallback's signature either way.
        self.assertIn("128,000", row_for(rendered, "l3-driver"))
        self.assertNotIn("128k", row_for(rendered, "l3-driver"))

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
        # on the first one. (No l3-driver leg repeats a spelling today - the
        # 16:4xZ revision removed its openrouter/qwen/qwen3.8-27b leg, so this
        # currently asserts order only - but l2-worker still repeats
        # `gpt-oss-120b` across providers, so the forward search is the right
        # shape if a repeat is ever reintroduced here.)
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "l3-driver")
        legs = real_registry()["routes"]["l3-driver"]["legs"]
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
        # CIGREEN: expectation moved by c4c3654b (TORDER-OR removed the paid
        # openrouter legs from l2-orchestrator) + aced9915 (removed its
        # antigravity leg too): the route carried ovhcloud legs then. Moved
        # again by the operator's CACHEORCH order (2026-10-05: l2-orchestrator
        # dropped its OVH legs - orchestrators serve long sessions and OVH does
        # not cache), and once more by D657-CHAIN 2026-10-08, which took every
        # ovhcloud leg out of every chain (SS8: OVH credits off), so the
        # provider-wide flip uses openrouter - l2-worker still reaches a ':free'
        # leg through it.
        reg["providers"]["openrouter"]["available"] = False
        rendered = registry.render_models_doc(reg)
        row = row_for(rendered, "l2-worker")
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
        reg["routes"]["l2-worker"]["legs"] = ["groq/any-model"]
        rendered = registry.render_litellm_blocks(
            reg, real_litellm_config(), tiers=("l2-worker",))
        self.assertEqual(rendered, {})

    def test_an_explicit_tier_absent_from_the_registry_still_raises(self):
        # An explicit tier is still validated: a tier the registry does not
        # have at all is a caller error, unlike one that renders empty.
        with self.assertRaises(ValueError) as ctx:
            registry.render_litellm_blocks(
                real_registry(), real_litellm_config(), tiers=("no-such-tier",))
        self.assertIn("no-such-tier", str(ctx.exception))

    def test_real_omniroute_drops_gated_legs_and_keeps_live_order(self):
        # D657-CHAIN 2026-10-08 (AO-DENYLEGS D2) replaced the L1-CLEAN/DSBACK leg
        # sets this test pinned: SS8 took the ovh/* credit legs and the paid
        # native deepseek leg out of every chain, and probe D-657 refused
        # gemini/gemini-3.8-flash (401 on both gateways) while vertex answered, so
        # the cached pool is the bazaarlink DeepSeek mirror plus vertex only.
        # Every -clean combo is one leg now: spec 3.1 rule 3 admits only a
        # non-training paid/credit/subscription leg and rule (b-or) forces every
        # openrouter leg ':free', so no free band can ever be clean.
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        self.assertEqual(
            combos["l2-worker"]["models"],
            ["bzl/deepseek/deepseek-v4-flash-0731free:free",
             "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
             "vertex/gemini-3.8-flash"])
        self.assertEqual(combos["l2-worker-clean"]["models"], ["vertex/gemini-3.8-flash"])
        # A gated leg is dropped while the route survives on its other leg:
        # l1-orchestrator-clean's contributor copy is probe-denied (402/403), the
        # vertex leg serves, so the combo carries one model, not none.
        self.assertEqual(combos["l1-orchestrator-clean"]["models"], ["vertex/gemini-3.8-flash"])
        self.assertNotIn("openrouter/meta/muse-spark-1.3-contributor",
                         combos["l1-orchestrator-clean"]["models"])
        # samba/SambaNova is available: false, so every one of its legs goes -
        # including the pinned one-leg routes, and with them spark-1.3-contributor,
        # whose only legs are the SS8-banned contributor copies (OR1d: declared
        # legs, none servable -> no declaration). OVH-REPROBE 2026-10-08 moved
        # ovh-qwen3.8-27b out of this set (the provider is live and priced again,
        # so the seat renders); ovh-gpt-oss-120b stays omitted because its leg is
        # gated for want of a price. OVHCODER-DROP 2026-10-08 took ovh-qwen3-coder-30b
        # out of the omitted set by deleting the route: an omitted route is still a
        # declaration, and a model OVH no longer serves is not one - it sits in the
        # render's `retired` list instead, which is what makes apply prune the live
        # combo (pinned in test_ovh_coder_leg_is_retired_not_merged).
        rendered = registry.render_omniroute(real_registry())
        combos = {c["name"]: c for c in rendered["combos"]}
        for gone in ("samba/gpt-oss-120b", "samba/MiniMax-M3", "spark-1.3-contributor",
                     "ovh-gpt-oss-120b",
                     "deepseek-v4.1-flash"):
            self.assertNotIn(gone, combos)
            self.assertIn(gone, rendered["omitted"])
        self.assertNotIn("ovh-qwen3-coder-30b", combos)
        self.assertNotIn("ovh-qwen3-coder-30b", rendered["omitted"])
        self.assertIn("ovh-qwen3-coder-30b", rendered["retired"])
        self.assertEqual(combos["ovh-qwen3.8-27b"]["models"], ["ovh/Qwen3.8-27B"])


    def test_real_litellm_drops_gated_legs(self):
        rendered = registry.render_litellm_blocks(
            real_registry(), real_litellm_config())
        # D657-CHAIN 2026-10-08: the chains are the probe union, so the legs this
        # test used to pin as available (the ovhcloud credit legs, the deepseek
        # DIRECT leg, mistral-code-latest) are out of every route now, and the
        # bazaarlink DeepSeek mirror is the head of every LiteLLM-served tier.
        self.assertIn("bazaarlink/deepseek/deepseek-v4-flash-0731free:free",
                      rendered["l2-worker"])
        for gone in ("sambanova/gpt-oss-120b", "ovhcloud/gpt-oss-120b",
                     "groq/openai/gpt-oss-120b", "model: openai/deepseek-v4-flash",
                     "model: deepseek/deepseek-flash",
                     "model: openai/deepseek-v4.1-flash",
                     "model: openrouter/deepseek/deepseek-v4.1-flash"):
            self.assertNotIn(gone, rendered["l2-worker"], gone)
        # vertex/gemini-3.8-flash IS a live leg but authenticates without a LiteLLM
        # api_key, so it lands as a litellm-skip comment, never a model line.
        self.assertIn("# litellm-skip: vertex/gemini-3.8-flash", rendered["l2-worker"])
        self.assertNotIn("model: vertex/gemini-3.8-flash", rendered["l2-worker"])
        # l3-driver keeps the proven free band: groq carries both the qwen3.8-27b
        # and the gpt-oss-120b spelling, and nothing of the dead providers
        # (samba/cerebras) or of the retired mistral leg survives.
        self.assertIn("qwen3.8-27b", rendered["l3-driver"])
        self.assertIn("groq/openai/gpt-oss-120b", rendered["l3-driver"])
        self.assertNotIn("MiniMax-M3", rendered["l3-driver"])
        self.assertNotIn("mistral-code-latest", rendered["l3-driver"])
        self.assertNotIn("ovhcloud/Qwen3.8-27B", rendered["l3-driver"])

    def test_models_doc_still_strikes_through_a_gated_leg(self):
        # D657-CHAIN 2026-10-08: l2-worker no longer keeps a gated leg at all (SS8
        # deleted the OVH credit legs from its declared chain), so
        # l1-orchestrator-clean is the example - it serves vertex/gemini-3.8-flash
        # and still shows the probe-denied contributor copy struck through for the
        # human reader, which no gateway render mirrors.
        row = row_for(registry.render_models_doc(real_registry()), "l1-orchestrator-clean")
        self.assertIn("~~openrouter `meta/muse-spark-1.3-contributor`~~ (unavailable)", row)
        self.assertIn("vertex `gemini-3.8-flash`", row)


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
        # l1-orchestrator-free-only is NOT dropped: T1FREE gave it a gemini
        # servable leg.
        for gone in ("samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertNotIn(gone, ids)

    def test_ide_keeps_a_deliberately_legless_route(self):
        ids = [m["id"] for m in registry.render_ide(real_registry())["models"]]
        for kept in ("l1-orchestrator-paid", "l2-worker-paid", "l3-driver-paid",
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
        route = reg["routes"]["l1-orchestrator"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            route["unavailable_legs"][leg] = {"available": False}
        self.assertEqual(registry.gateway_legs(route, reg), [])
        ids = {t["id"] for t in registry.render_openhands(reg)["tiers"]}
        self.assertNotIn("omniroute-l1-orchestrator", ids)
        self.assertNotIn("litellm-l1-orchestrator", ids)
        # every other openhands declaration is untouched by the gate
        self.assertEqual(len(ids), len(registry.render_openhands(real_registry())["tiers"]) - 2)

    def test_openhands_keeps_a_tier_whose_route_now_declares_no_legs(self):
        # The rule reaches a route that DECLARED legs and cannot serve them -
        # never one that declares none at all.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["l1-orchestrator"]["legs"] = []
        ids = {t["id"] for t in registry.render_openhands(reg)["tiers"]}
        self.assertIn("omniroute-l1-orchestrator", ids)
        self.assertIn("litellm-l1-orchestrator", ids)

    def test_openhands_tier_returns_when_its_leg_becomes_servable(self):
        # FREEKEYS-2c (D-141) put a servable free band (scaleway/nebius) ahead of
        # free_ai in this route, so gating one leg no longer empties it: gate
        # EVERY leg and its declaration goes; lift the gate and it comes back.
        gated = copy.deepcopy(real_registry())
        route = gated["routes"]["l3-driver-free-only"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            route["unavailable_legs"][leg] = {"available": False}
        self.assertEqual(registry.gateway_legs(route, gated), [])
        gone = {t["id"] for t in registry.render_openhands(gated)["tiers"]}
        self.assertNotIn("omniroute-l3-driver-free-only", gone)
        self.assertNotIn("litellm-l3-driver-free-only", gone)

        served = copy.deepcopy(gated)
        for leg in list(served["routes"]["l3-driver-free-only"]["legs"]):
            served["routes"]["l3-driver-free-only"]["unavailable_legs"].pop(leg, None)
        ids = {t["id"] for t in registry.render_openhands(served)["tiers"]}
        self.assertIn("omniroute-l3-driver-free-only", ids)
        self.assertIn("litellm-l3-driver-free-only", ids)

    def test_models_doc_is_not_filtered(self):
        # Out of scope by OR1d's own decision: models-doc still shows every
        # route, gated legs struck through, for the human reader.
        doc = registry.render_models_doc(real_registry())
        for kept in ("l1-orchestrator-free-only", "l3-driver-free-only",
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
        # D657-CHAIN 2026-10-08: the fixture moved off l2-worker-clean, whose
        # gated opencode-zen leg SS8 deleted with the chain re-cut; the
        # contributor's own gated leg carries the same available:false shape.
        reg["routes"]["spark-1.3-contributor"]["unavailable_legs"][
            "meta_api/muse-spark-1.3-contributor"]["unavailable_until"] = (
                "2026-10-01T09:05:00Z")
        # One leg with an until but no available:false at all - inert because its
        # PROVIDER is the gate (ovhcloud is off, so the route already renders
        # nothing either way). CLAUDE55 2026-10-05 moved this from opus-4-6 (now
        # opus-5-5); D657-CHAIN moved it again because every leg that still has a
        # route-level gate also has available:false now.
        reg["routes"]["ovh-qwen3.8-27b"]["unavailable_legs"] = {
            "ovhcloud/Qwen3.8-27B": {
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
    """BRIEF FREEAI (2026-09-27) wired free_ai/qwen7b into the two zero-spend
    routes. D657-CHAIN 2026-10-08 (operator D-657) took it back out: probe
    D-657 recorded `free-ai/qwen7b` ack ok on both gateways but its ONE TOOL CALL
    FAILED (configuration/omniroute/probes/probe-d657-{central,workstation}.tsv,
    columns ack/tool) and cache `unknown`, and an agent lane that cannot call a
    tool is not a leg - so it rides no chain. What survives here is the part of
    the brief that was never about free_ai: the zero-spend routes stay servable,
    and no training leg enters a -clean declaration."""

    def test_t3_driver_free_only_leaves_omitted_and_gains_a_combo(self):
        rendered = registry.render_omniroute(real_registry())
        self.assertNotIn("l3-driver-free-only", rendered["omitted"])
        combos = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("l3-driver-free-only", combos)
        models = combos["l3-driver-free-only"]["models"]
        # D657-CHAIN: the route heads the probe-confirmed bazaarlink DeepSeek
        # mirror (free, cache hit measured) and closes with the cohere leg, which
        # is the tail the free band has once SCWREMOVAL's grants and free-ai are
        # gone. The GLM55/AINATIVE leaders were agy/trial legs SS8 keeps out of
        # position 1 or that expire 2026-11-02.
        self.assertEqual(models[0], "bzl/deepseek/deepseek-v4-flash-0731free:free")
        self.assertEqual(models[-1], "cohere/command-a-plus-05-2026")
        self.assertNotIn("free-ai/qwen7b", models)

    def test_free_ai_rides_no_chain_after_the_d657_tool_probe(self):
        # The tail pin this test used to hold (free-ai/qwen7b last) is gone with
        # the leg. Pinned instead as an absence, in both spellings, across every
        # combo - a re-add that ignores the tool-call failure fails here.
        combos = registry.render_omniroute(real_registry())["combos"]
        for combo in combos:
            self.assertFalse(
                [m for m in combo["models"]
                 if m.startswith(("free-ai/", "free_ai/"))],
                combo["name"])

    def test_l2_worker_combo_closes_with_the_vertex_leg(self):
        # CIGREEN: expectation moved by ba73f1cf (same TORDER TASK2 reorder:
        # paid last, deepseek last). D657-CHAIN supersedes that tail again - SS8
        # removed deepseek/deepseek-flash from every chain, so the combo closes
        # on the cached vertex leg and free_ai appears nowhere.
        combos = {c["name"]: c for c in
                  registry.render_omniroute(real_registry())["combos"]}
        models = combos["l2-worker"]["models"]
        self.assertEqual(models[-1], "vertex/gemini-3.8-flash")
        self.assertNotIn("deepseek/deepseek-flash", models)
        self.assertNotIn("free-ai/qwen7b", models)

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

    def test_litellm_blocks_carry_the_free_band_on_both_free_only_routes(self):
        # D657-CHAIN: the zero-spend band is the probe-confirmed free legs, not
        # free_ai (its tool call failed). The block must still be a real spend
        # -nothing chain, so the bazaarlink mirror leads and every leg is a ':free'
        # spelling; no qwen7b line survives anywhere.
        rendered = registry.render_litellm_blocks(
            real_registry(), real_litellm_config())
        for route_id in ("l2-worker-free-only", "l3-driver-free-only"):
            self.assertIn("model: bazaarlink/deepseek/deepseek-v4-flash-0731free:free",
                          rendered[route_id], route_id)
            self.assertIn("api_key: os.environ/BAZAARLINK_API_KEY",
                          rendered[route_id], route_id)
            self.assertNotIn("model: openai/qwen7b", rendered[route_id], route_id)
            self.assertNotIn("api_key: os.environ/FREE_AI_API_KEY",
                             rendered[route_id], route_id)

    def test_ide_lists_t3_driver_free_only_again(self):
        ids = [m["id"] for m in registry.render_ide(real_registry())["models"]]
        self.assertIn("l3-driver-free-only", ids)

    def test_openhands_lists_both_t3_driver_free_only_tiers_again(self):
        ids = {t["id"] for t in
               registry.render_openhands(real_registry())["tiers"]}
        self.assertIn("omniroute-l3-driver-free-only", ids)
        self.assertIn("litellm-l3-driver-free-only", ids)

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
        for route_id in ("l1-orchestrator", "l1-orchestrator-free-only"):
            route = reg["routes"][route_id]
            cap = registry.route_context_cap(route, reg)
            self.assertEqual(cap, 1048576, route_id)
            self.assertEqual(combos[route_id]["context"],
                             registry.context_tokens_to_label(cap), route_id)
        # spark-1.3-contributor promised 1M from its contributor leg. SS8 (operator
        # D-657) banned that leg and probe D-657 refused the free copies, so the
        # route declares legs and serves none: OR1d gives it no combo at all, which
        # means there is no window left to over-promise - the clamp rule now
        # polices its absence.
        rendered = registry.render_omniroute(real_registry())
        self.assertNotIn("spark-1.3-contributor", combos)
        self.assertIn("spark-1.3-contributor", rendered["omitted"])

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
        route = reg["routes"]["l1-orchestrator"]
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
        route = reg["routes"]["l1-orchestrator"]
        route.setdefault("unavailable_legs", {})
        for leg in list(route["legs"]):
            if leg != "vertex/gemini-3.8-flash":
                route["unavailable_legs"][leg] = {"available": False}
        reg["routes"]["l1-orchestrator"]["legs"] = ["vertex/gemini-3.8-flash"]
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
        entry = self._ide_entry(reg, "l1-orchestrator")
        route = reg["routes"]["l1-orchestrator"]
        self.assertEqual(
            entry["context"],
            registry.clamp_route_context(
                route, reg, route["surfaces"]["omniroute"]["context"]))
        self.assertEqual(entry["context"], 1000000)

    def test_effort_ladder_comes_from_the_first_servable_leg(self):
        entry = self._ide_entry(self._t1_gated_to_vertex_gemini(), "l1-orchestrator")
        self.assertEqual(entry["effort_ladder"], ["low", "medium", "high"])

    def test_an_effort_default_the_served_head_rejects_is_dropped(self):
        entry = self._ide_entry(self._t1_gated_to_vertex_gemini(), "l1-orchestrator")
        self.assertNotIn("reasoning_effort", entry)

    def test_a_default_the_served_head_carries_is_still_forwarded(self):
        # FREEKEYS-2 (D-141) made a free leg the real head, and gemini's ladder
        # tops out at "high" - so the positive branch needs a registry whose
        # served head DOES carry the surface default. D657-CHAIN 2026-10-08 took
        # meta_api/muse-spark-1.3-contributor (whose "xhigh" this was) out of every
        # chain under SS8, so the branch uses the vertex gemini leg the route does
        # serve - ladder low/medium/high - and names "high" as the surface default.
        reg = self._t1_gated_to_vertex_gemini()
        reg["routes"]["l1-orchestrator"]["surfaces"]["omniroute"]["effort_default"] = "high"
        entry = self._ide_entry(reg, "l1-orchestrator")
        self.assertEqual(entry.get("reasoning_effort"), "high")
        self.assertIn("high", entry["effort_ladder"])

    def test_the_real_head_carries_no_ladder_rather_than_an_invented_one(self):
        # CIGREEN: expectation moved by 018438ed (TASK1 put the gemini head
        # first: ladder low/medium/high, no xhigh). D657-CHAIN 2026-10-08 moved
        # it again: the served head of every orchestrator chain is now the
        # bazaarlink DeepSeek mirror, whose registry row documents NO effort
        # ladder, so render_ide() omits the field rather than inventing rungs -
        # test_sync_ide_models.py pins the same thing on the picker surface. The
        # cached clean band still heads vertex gemini and keeps its ladder.
        for route_id in ("l1-orchestrator", "l1-orchestrator-free-only",
                         "l2-orchestrator", "l3-researcher"):
            entry = self._ide_entry(real_registry(), route_id)
            self.assertNotIn("effort_ladder", entry, route_id)
            self.assertNotIn("reasoning_effort", entry, route_id)
        entry = self._ide_entry(real_registry(), "l1-orchestrator-clean")
        self.assertEqual(entry["effort_ladder"], ["low", "medium", "high"])

    def test_openhands_max_input_tokens_is_clamped(self):
        # CIGREEN: expectation moved by 20c4a816 (TASK3 window raise) +
        # 018438ed (TASK1 1M-only band): the min servable t1 window is 1M, so
        # the clamp keeps the 1M profile value. Pins the rule: each tier
        # carries its surface profile value clamped to the servable legs.
        reg = real_registry()
        tiers = {t["id"]: t for t in
                 registry.render_openhands(reg)["tiers"]}
        for tier_id in ("omniroute-l1-orchestrator", "litellm-l1-orchestrator"):
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
        # would silently gut the mirror. D657-CHAIN 2026-10-08 moved the example
        # off ovhcloud, whose credit legs SS8 took out of every chain: the keys
        # the served chains actually reach through are bazaarlink, openrouter,
        # groq and cohere.
        joined = "\n".join(self.blocks.values())
        self.assertIn("model: bazaarlink/deepseek/deepseek-v4-flash-0731free:free", joined)
        self.assertIn("BAZAARLINK_API_KEY", joined)
        self.assertIn("model: groq/qwen/qwen3.8-27b", joined)
        self.assertIn("GROQ_API_KEY", joined)


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
            path = self._combos(tmp, [{"name": "l1-orchestrator", "models": [
                "vertex/gemini-3.8-flash", "ovhcloud/gpt-oss-120b"]}])
            refs = self.sync.combos_refs(path, registry=self.reg)
            self.assertEqual(refs["l1-orchestrator"], ["ovhcloud/gpt-oss-120b"])
            self.assertEqual(self.sync.SKIPPED_BY_TIER["l1-orchestrator"],
                             ["vertex/gemini-3.8-flash"])
            block = self.sync.render_block("l1-orchestrator",
                                           refs["l1-orchestrator"])
            self.assertIn("# litellm-skip: vertex/gemini-3.8-flash",
                          "\n".join(block), block)

    def test_each_call_owns_the_skip_state_it_leaves_behind(self):
        # F3: SKIPPED_BY_TIER is module state that was only ever .update()d and
        # assigned per tier, never cleared - a second call in one process left
        # the first call's skips sitting there for render_block() to pick up.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "l2-worker", "models": [
                "vertex/gemini-3.8-flash", "ovhcloud/gpt-oss-120b"]}])
            self.sync.combos_refs(path, registry=self.reg)
            second = self._combos(tmp, [{"name": "l1-orchestrator", "models": [
                "ovhcloud/gpt-oss-120b"]}])
            combos = self.sync.combos_refs(second, registry=self.reg)
            self.assertEqual(set(self.sync.SKIPPED_BY_TIER), {"l1-orchestrator"})
            block = "\n".join(self.sync.render_block(
                "l2-worker", combos.get("l2-worker", [])))
            self.assertNotIn("litellm-skip", block)

    def test_the_registry_call_clears_a_prior_combos_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._combos(tmp, [{"name": "l2-worker", "models": [
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
