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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
REGISTRY_TOOL = ROOT / "tools" / "registry.py"


def _load_tool():
    """Import tools/registry.py by path (its name is not a valid module identifier)."""
    spec = importlib.util.spec_from_file_location("autoos_registry", REGISTRY_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load_tool()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def real_registry() -> dict:
    return load_json(REGISTRY_PATH)


def real_combos() -> dict:
    return load_json(COMBOS_PATH)


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


if __name__ == "__main__":
    unittest.main()
