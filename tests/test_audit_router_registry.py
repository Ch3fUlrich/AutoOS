"""Tests for tools/audit-router.py's repo_combos() sourcing its combo list
from catalog/ai-registry.json instead of configuration/omniroute/combos.json
directly (routing v2 spec 3.2 phase 2, task A5c).

Run from the repo root:

    python3 tests/test_audit_router_registry.py

Every test works on temp files; nothing in the checkout is ever written, no
network call is ever made.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "audit-router.py"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("autoos_audit_router_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A minimal registry fixture with two routes: one with legs (becomes a
# combo, mapping doc section 4/A4a) and one without (a LiteLLM-only/auto
# route, never a combo).
MINIMAL_REGISTRY = {
    "routes": {
        "t3-driver": {
            "strategy": "priority",
            "legs": ["groq/openai/gpt-oss-120b", "cerebras/qwen-3.8-27b"],
            "surfaces": {"omniroute": {"context_declared": "128k"}},
        },
        "t3-driver-paid": {"strategy": "priority", "legs": [],
                            "surfaces": {"litellm": {}}},
    }
}


class RegistrySourcedRepoCombosTests(unittest.TestCase):
    """repo_combos() now renders catalog/ai-registry.json via
    tools/registry.py's render_omniroute() (task A4a) instead of reading
    configuration/omniroute/combos.json directly (task A5c)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"
        self.registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_a_registry_fixture_with_no_combos_json_anywhere(self):
        # self.dir has no configuration/omniroute/combos.json at all - proves
        # the default read never falls back to it.
        audit = _load_module()
        combos = audit.repo_combos(registry_path=self.registry_path)
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0]["name"], "t3-driver")
        self.assertEqual(combos[0]["models"],
                          ["groq/openai/gpt-oss-120b", "cerebras/qwen-3.8-27b"])

    def test_a_legs_empty_route_never_becomes_a_combo(self):
        audit = _load_module()
        combos = audit.repo_combos(registry_path=self.registry_path)
        names = [c["name"] for c in combos]
        self.assertNotIn("t3-driver-paid", names)

    def test_default_matches_the_real_repo_combos_json_semantically(self):
        # No path given at all: the tool's own default (catalog/ai-
        # registry.json) must still name every combo the real, committed
        # configuration/omniroute/combos.json has (task A4a already proves
        # render_omniroute() <-> combos.json equality; this pins that
        # repo_combos() actually calls it by default).
        audit = _load_module()
        got = {c["name"] for c in audit.repo_combos()}
        want = {c["name"] for c in json.loads(COMBOS_PATH.read_text(encoding="utf-8"))["combos"]}
        self.assertEqual(got, want)

    def test_explicit_combos_path_override_still_works(self):
        # --combos-path is the explicit escape hatch onto the old file's own
        # shape (spec 3.2's two-phase rule: never deleted).
        audit = _load_module()
        combos = audit.repo_combos(combos_path=COMBOS_PATH)
        got = {c["name"] for c in combos}
        want = {c["name"] for c in json.loads(COMBOS_PATH.read_text(encoding="utf-8"))["combos"]}
        self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
