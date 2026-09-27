"""Tests for tools/sync-router-tiers.py sourcing its provider maps and tier
leg lists from catalog/ai-registry.json instead of the deleted
catalog/providers.json and configuration/omniroute/combos.json (routing v2
spec 3.2 phase 2, task A5c).

Run from the repo root:

    python3 tests/test_sync_router_tiers_registry.py

Every test works on temp copies; nothing in the checkout is ever written.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "sync-router-tiers.py"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
CONFIG_PATH = ROOT / "configuration" / "litellm" / "config.yaml"


def _load_module():
    spec = importlib.util.spec_from_file_location("autoos_sync_router_tiers_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A minimal registry fixture: enough for provider_maps()/registry_refs() -
# no other registry section needed for either function.
MINIMAL_REGISTRY = {
    "providers": {
        "groq": {"omniroute_id": "groq", "litellm_env": "GROQ_API_KEY"},
        "zen": {"omniroute_id": "opencode-zen", "litellm_prefix": "openai",
                "api_base": "https://opencode-zen.example/v1", "litellm_env": "OPENCODE_ZEN_API_KEY"},
        # OAuth/subscription bridges: no LiteLLM transport or key.
        "cc": {"omniroute_id": "cc"},
        "antigravity": {"omniroute_id": "antigravity"},
    },
    "routes": {
        "t2-worker": {"legs": ["groq/openai/gpt-oss-120b", "antigravity/gemini-3.7-flash-high"]},
        "t3-driver": {"legs": ["opencode-zen/deepseek-v4.1-flash", "cc/opus-4-6"]},
    },
}


class ProviderMapsReadTheRegistryTests(unittest.TestCase):
    """provider_maps(path) already returns the same shape for a registry-
    shaped `{"providers": {...}}` file as for the deleted
    catalog/providers.json (both share the same field names, mapping doc
    section 2) - this pins that the tool's DEFAULT (no path) now targets
    catalog/ai-registry.json, not the deleted catalog/providers.json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"
        self.registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_a_registry_fixture_with_no_old_catalog_anywhere(self):
        sync = _load_module()
        prefix, api_base, env_key = sync.provider_maps(self.registry_path)
        self.assertEqual(prefix, {"opencode-zen": "openai"})
        self.assertEqual(api_base, {"opencode-zen": "https://opencode-zen.example/v1"})
        self.assertEqual(env_key, {"groq": "GROQ_API_KEY", "opencode-zen": "OPENCODE_ZEN_API_KEY"})

    def test_default_source_is_the_registry_not_the_old_catalog(self):
        sync = _load_module()
        self.assertEqual(sync.REGISTRY_FILE, REGISTRY_PATH)
        self.assertEqual(sync.provider_maps(), sync.provider_maps(REGISTRY_PATH))


class RegistryRefsTests(unittest.TestCase):
    """registry_refs() is the new default leg source (task A5c), reading
    routes.<tier>.legs from catalog/ai-registry.json instead of combos.json's
    combos[].models - same GATEWAY_ONLY drop as combos_refs() (mapping doc
    section 4: unchanged, in order)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"
        self.registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_drops_gateway_only_legs_and_keeps_order(self):
        sync = _load_module()
        refs = sync.registry_refs(self.registry_path)
        self.assertEqual(refs["t2-worker"], ["groq/openai/gpt-oss-120b"])
        self.assertEqual(refs["t3-driver"], ["opencode-zen/deepseek-v4.1-flash"])

    def test_missing_route_raises_config_error(self):
        sync = _load_module()
        with self.assertRaises(sync.ConfigError):
            sync.registry_refs(self.registry_path, tiers=("t2-worker", "no-such-tier"))


class ManagedTierSelectionTests(unittest.TestCase):
    """The default tier set is derived from the registry instead of a
    hand-kept pair: registry_refs() (and so both consumers) manage every route
    that declares legs and keeps at least one LiteLLM-servable leg. A legless
    route (the *-paid fallbacks) and a route whose every leg is gateway-only
    (cc/antigravity: no LiteLLM transport or key) get no managed entry."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_registry(self, routes):
        doc = {
            "providers": {
                "groq": {"omniroute_id": "groq", "litellm_env": "GROQ_API_KEY"},
                "cc": {"omniroute_id": "cc"},
                "antigravity": {"omniroute_id": "antigravity"},
            },
            "routes": routes,
        }
        self.registry_path.write_text(json.dumps(doc), encoding="utf-8")

    def test_default_covers_a_servable_route_outside_the_old_synced_pair(self):
        self._write_registry({
            "t2-worker": {"legs": ["groq/openai/gpt-oss-120b"]},
            "t3-driver": {"legs": ["groq/qwen/qwen3.8-27b"]},
            "t4-rag": {"legs": ["groq/command-a-03-2025"]},
        })
        sync = _load_module()
        self.assertEqual(set(sync.registry_refs(self.registry_path)),
                         {"t2-worker", "t3-driver", "t4-rag"})

    def test_a_legless_route_gets_no_managed_entry(self):
        self._write_registry({
            "t2-worker": {"legs": ["groq/openai/gpt-oss-120b"]},
            "t3-driver": {"legs": ["groq/qwen/qwen3.8-27b"]},
            "t2-worker-paid": {"legs": []},
        })
        sync = _load_module()
        self.assertEqual(set(sync.registry_refs(self.registry_path)),
                         {"t2-worker", "t3-driver"})

    def test_a_gateway_only_route_gets_no_managed_entry(self):
        self._write_registry({
            "t2-worker": {"legs": ["groq/openai/gpt-oss-120b"]},
            "t3-driver": {"legs": ["groq/qwen/qwen3.8-27b"]},
            "opus-4-6": {"legs": ["cc/claude-opus-4-6",
                                  "antigravity/claude-opus-4-6-thinking"]},
        })
        sync = _load_module()
        self.assertEqual(set(sync.registry_refs(self.registry_path)),
                         {"t2-worker", "t3-driver"})

    def test_combos_override_defaults_to_every_servable_combo(self):
        combos_path = self.dir / "combos.json"
        combos_path.write_text(json.dumps({"combos": [
            {"name": "t2-worker", "models": ["groq/openai/gpt-oss-120b"]},
            {"name": "t3-driver", "models": []},
            {"name": "opus-4-6", "models": ["antigravity/claude-opus-4-6-thinking"]},
        ]}), encoding="utf-8")
        sync = _load_module()
        refs = sync.combos_refs(combos_path)
        self.assertEqual(set(refs), {"t2-worker"})


class RegistrySandbox:
    """Temp copies of ai-registry.json, combos.json and config.yaml, plus a
    runner pointed at them - proves the tool's default (unflagged --combos)
    run needs neither the deleted catalog/providers.json nor combos.json
    present."""

    def __init__(self, with_combos=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.paths = {"registry": self.dir / REGISTRY_PATH.name,
                      "config": self.dir / CONFIG_PATH.name}
        shutil.copyfile(REGISTRY_PATH, self.paths["registry"])
        shutil.copyfile(CONFIG_PATH, self.paths["config"])
        if with_combos:
            self.paths["combos"] = self.dir / COMBOS_PATH.name
            shutil.copyfile(COMBOS_PATH, self.paths["combos"])

    def close(self):
        self._tmp.cleanup()

    def run(self, *extra):
        args = [sys.executable, str(TOOL), "--registry", str(self.paths["registry"]),
                "--config", str(self.paths["config"])]
        if "combos" in self.paths:
            args += ["--combos", str(self.paths["combos"])]
        return subprocess.run(args + list(extra), capture_output=True, text=True,
                               cwd=str(self.dir))

    def registry(self):
        return json.loads(self.paths["registry"].read_text(encoding="utf-8"))

    def save_registry(self, doc):
        self.paths["registry"].write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


class CliDefaultsToRegistryTests(unittest.TestCase):
    def test_check_is_clean_on_a_fresh_registry_copy_with_no_combos_present(self):
        box = RegistrySandbox()
        try:
            result = box.run("--check", "--quiet")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            box.close()

    def test_a_registry_leg_change_is_drift(self):
        box = RegistrySandbox()
        try:
            doc = box.registry()
            doc["routes"]["t3-driver"]["legs"] = list(doc["routes"]["t3-driver"]["legs"])
            doc["routes"]["t3-driver"]["legs"].insert(0, "mistral/mistral-small-latest")
            box.save_registry(doc)
            result = box.run("--check", "--quiet")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("DRIFT", result.stderr)
            self.assertIn("t3-driver", result.stderr)
        finally:
            box.close()

    def test_explicit_combos_override_still_works(self):
        # --combos is the explicit escape hatch onto the old file's own leg
        # order (never deleted, spec 3.2's two-phase rule) - proven to still
        # match the registry-sourced default on an unmodified checkout.
        box = RegistrySandbox(with_combos=True)
        try:
            result = box.run("--check", "--quiet")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            box.close()


class GatewayLegsNoPathGrowthTests(unittest.TestCase):
    """_gateway_legs() imports tools/registry.py lazily (registry.py loads
    THIS module by path, so a module-level import would be circular). It must
    add tools/ to sys.path once, not on every leg lookup: a long-lived caller
    (a test process, a tool called in a loop) otherwise accumulates one
    duplicate sys.path entry per route - the same unbounded-growth bug the
    review flagged in _gateway_legs()."""

    def test_repeated_leg_lookups_do_not_grow_sys_path(self):
        sync = _load_module()
        tools_dir = str(TOOL.resolve().parent)
        before = list(sys.path)
        try:
            # Start from a clean slate: the first call is the one allowed to
            # add tools/, any later call must not add another copy.
            sys.path[:] = [p for p in sys.path if p != tools_dir]
            route = MINIMAL_REGISTRY["routes"]["t2-worker"]
            sync._gateway_legs(route, MINIMAL_REGISTRY)
            after_first = sys.path.count(tools_dir)
            sync._gateway_legs(route, MINIMAL_REGISTRY)
            sync._gateway_legs(MINIMAL_REGISTRY["routes"]["t3-driver"], MINIMAL_REGISTRY)
            after_many = sys.path.count(tools_dir)
        finally:
            sys.path[:] = before
        self.assertEqual(after_first, 1, "tools/ was not added exactly once")
        self.assertEqual(after_many, 1, "sys.path grew once per leg lookup")


if __name__ == "__main__":
    unittest.main()
