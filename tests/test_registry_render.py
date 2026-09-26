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

    def test_every_leg_is_preserved_including_operator_flagged_dead_ones(self):
        # 2026-09-25/26 operator decisions mark some legs unavailable in the
        # registry (routes.<id>.unavailable_legs / providers.openrouter.available)
        # without removing them from `legs` - today's combos.json already lists
        # these same dead legs, so the render must too (mapping doc section 10).
        rendered = registry.render_omniroute(real_registry())
        combos_by_name = {c["name"]: c for c in rendered["combos"]}
        self.assertIn("opencode-zen/deepseek-v4.1-flash",
                      combos_by_name["t2-worker-clean"]["models"])
        self.assertIn("openrouter/deepseek/deepseek-v4.1-flash",
                      combos_by_name["t2-worker"]["models"])

    def test_paid_and_auto_routes_have_no_combo(self):
        # t1-orchestrator-paid/t2-worker-paid/t3-driver-paid (LiteLLM-only) and
        # auto/auto-smart/auto-cheap (OmniRoute's dynamic strategy) carry
        # legs: [] in the registry and have no combos.json counterpart.
        rendered = registry.render_omniroute(real_registry())
        names = {c["name"] for c in rendered["combos"]}
        for absent in ("t1-orchestrator-paid", "t2-worker-paid", "t3-driver-paid",
                      "auto", "auto/smart", "auto/cheap"):
            self.assertNotIn(absent, names)


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

    def test_synced_tiers_are_rendered(self):
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        sync = registry._load_sync_router_tiers()
        self.assertEqual(set(rendered), set(sync.SYNCED_TIERS))

    def test_gateway_only_leg_is_dropped_not_silently_kept_or_missing(self):
        # routes.t2-worker.legs carries antigravity/gemini-3.7-flash-high (a
        # real registry leg) but antigravity has no LiteLLM transport or key
        # (tools/sync-router-tiers.py GATEWAY_ONLY) - today's config.yaml
        # never mirrors it, and the render must match: this leg's model name
        # absent, its sibling gemini-3.8-flash leg present.
        legs = real_registry()["routes"]["t2-worker"]["legs"]
        self.assertIn("antigravity/gemini-3.7-flash-high", legs)
        rendered = registry.render_litellm_blocks(real_registry(), real_litellm_config())
        self.assertNotIn("gemini-3.7-flash-high", rendered["t2-worker"])
        self.assertIn("gemini-3.8-flash", rendered["t2-worker"])


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
    """A4b must not disturb tools/sync-router-tiers.py - the brief's own 'it stays
    a working tool, its tests must pass' rule. Both are the *actual* existing
    tests/checks for that tool (grepped from tests/), not new ones invented here."""

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

    def test_generated_marker_is_new_not_borrowed_from_the_committed_file(self):
        self.assertNotIn("generated from catalog/ai-registry.json",
                         json.dumps(real_ide_models()["$comment"]))

    def test_render_order_matches_todays_picker_order(self):
        rendered = registry.render_ide(real_registry())
        self.assertEqual([m["id"] for m in rendered["models"]],
                         [m["id"] for m in real_ide_models()["models"]])

    def test_every_registry_route_is_covered_by_ide_model_order(self):
        self.assertEqual(set(registry.IDE_MODEL_ORDER), set(real_registry()["routes"]))

    def test_a_route_with_no_omniroute_or_litellm_surface_is_out_of_scope(self):
        # spark-1.3-contributor's surfaces.openhands.direct_profile (mapping doc
        # section 6) is not an omniroute/litellm surface - render_ide must not
        # choke on it, and must still render the route from its omniroute surface.
        rendered = registry.render_ide(real_registry())
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
        rendered = registry.render_openhands(real_registry())
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
            written = Path(d) / "profiles" / "omniroute-t1-orchestrator.json"
            self.assertTrue(written.exists())
            self.assertEqual(json.loads(written.read_text())["model"], "openai/t1-orchestrator")


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
        self.assertIn("131,072", row_for(rendered, "t3-driver"))

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
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "t3-driver")
        legs = real_registry()["routes"]["t3-driver"]["legs"]
        positions = [row.index("`%s`" % leg.split("/", 1)[1]) for leg in legs]
        self.assertEqual(positions, sorted(positions))

    def test_empty_legs_render_as_none(self):
        rendered = registry.render_models_doc(real_registry())
        self.assertIn("(none)", row_for(rendered, "auto"))

    def test_leg_flagged_unavailable_in_its_own_route_is_marked(self):
        # routes.deepseek-v4.1-flash.unavailable_legs flags both its legs today.
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "deepseek-v4.1-flash")
        self.assertEqual(row.count("(unavailable)"), 2)

    def test_leg_whose_provider_is_globally_unavailable_is_marked(self):
        # providers.openrouter.available is false today, independent of any
        # per-route unavailable_legs annotation.
        reg = copy.deepcopy(real_registry())
        reg["routes"]["t2-orchestrator"]["unavailable_legs"] = {}
        rendered = registry.render_models_doc(reg)
        row = row_for(rendered, "t2-orchestrator")
        self.assertIn("~~openrouter", row)
        self.assertIn("(unavailable)", row)

    def test_available_leg_is_not_marked(self):
        rendered = registry.render_models_doc(real_registry())
        row = row_for(rendered, "opus-4-6")
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
        # opus-4-6 has no unavailable legs today - flip one off.
        reg["routes"]["opus-4-6"]["unavailable_legs"] = {
            "antigravity/claude-opus-4-6-thinking": {"available": False},
        }
        path = write_registry(reg)
        try:
            proc = run_cli("render", "models-doc", "--registry", path, "--check")
        finally:
            Path(path).unlink()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("routes.opus-4-6", proc.stdout)

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


if __name__ == "__main__":
    unittest.main()
