"""Tests for tools/mirror-litellm-env.py sourcing its provider -> litellm .env
name map from catalog/ai-registry.json instead of catalog/providers.json
(routing v2 spec 3.2 phase 2, task A5c).

Run from the repo root:

    python3 tests/test_mirror_litellm_env_registry.py

Every test works on temp files; nothing in the checkout is ever written, and
no api-keys.yml value is ever printed.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "mirror-litellm-env.py"
REGISTRY_TOOL_PATH = ROOT / "tools" / "registry.py"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_mirror():
    return _load_module(TOOL, "autoos_mirror_litellm_env_test")


def _load_registry():
    return _load_module(REGISTRY_TOOL_PATH, "autoos_registry_for_mirror_test")


# A minimal registry fixture: two ordinary providers plus "cc", an OAuth/
# subscription bridge whose litellm_env is null - exactly the shape the real
# catalog/ai-registry.json gives cc/antigravity (docs/plans/
# 2026-09-25-registry-mapping.md section 2).
MINIMAL_REGISTRY = {
    "providers": {
        "groq": {"id": "groq", "litellm_env": "GROQ_API_KEY"},
        "meta": {"id": "meta", "litellm_env": "META_API_KEY"},
        "cc": {"id": "cc", "litellm_env": None},
        "omniroute": {"id": "omniroute", "litellm_env": "AUTOOS_OMNIROUTE_KEY"},
    }
}


class RegistrySourcedKeyMapTests(unittest.TestCase):
    """load_key_map() now reads catalog/ai-registry.json's `providers` section
    (task A5c) - same field names as the old catalog/providers.json (mapping
    doc section 2), so this is a source change only, never a shape change."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"
        self.registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_a_registry_fixture_with_no_old_catalog_anywhere(self):
        # self.dir has no catalog/providers.json at all - proves the default
        # read never falls back to it.
        mirror = _load_mirror()
        got = mirror.load_key_map(self.registry_path)
        self.assertEqual(got, {
            "groq": "GROQ_API_KEY",
            "meta": "META_API_KEY",
            "omniroute": "AUTOOS_OMNIROUTE_KEY",
        })

    def test_an_oauth_bridge_provider_with_no_litellm_env_is_skipped(self):
        mirror = _load_mirror()
        got = mirror.load_key_map(self.registry_path)
        self.assertNotIn("cc", got)

    def test_default_source_is_the_registry_not_the_old_catalog(self):
        mirror = _load_mirror()
        self.assertEqual(mirror.DEFAULT_REGISTRY_PATH, REGISTRY_PATH)
        self.assertEqual(mirror.KEY_MAP, mirror.load_key_map(REGISTRY_PATH))


class RenderNeverEmitsANullDestinationTests(unittest.TestCase):
    """render() must never emit a line for a provider whose registry entry
    carries no litellm_env (an OAuth/subscription bridge) - regression for
    the "None=REPLACE_WITH_CC" bug a naive, unfiltered registry read would
    produce (cc/antigravity are new registry-only entries with no
    catalog/providers.json counterpart, so this bug has no old-catalog
    equivalent to have already caught it)."""

    def test_render_has_no_none_destination_line(self):
        mirror = _load_mirror()
        mirror.KEY_MAP = {"groq": "GROQ_API_KEY", "cc": None}
        text, mirrored, missing = mirror.render({"groq": "dummy"}, None)
        self.assertNotIn("None=", text)


class CliRegistryFlagTests(unittest.TestCase):
    """--registry lets a caller point the tool at a fixture directly - proves
    the default run needs no catalog/providers.json present anywhere (task
    A5c: 'a test that runs the tool with a registry fixture and NO old
    catalog present')."""

    def test_check_and_write_both_work_from_a_bare_registry_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry_path = tmp / "ai-registry.json"
            registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")
            keys_path = tmp / "api-keys.yml"
            keys_path.write_text("groq: dummy-groq-1\n", encoding="utf-8")
            env_path = tmp / ".env"
            result = subprocess.run(
                [sys.executable, str(TOOL), "--registry", str(registry_path),
                 "--keys", str(keys_path), "--env", str(env_path)],
                capture_output=True, text=True, cwd=str(tmp))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("GROQ_API_KEY=dummy-groq-1", text)
            self.assertNotIn("None=", text)


class ProviderFieldMapTests(unittest.TestCase):
    """tools/registry.py's small new loader (task A5c): a generic, provider-
    id-keyed, presence-filtered map for a single registry field - the
    reusable primitive load_key_map() above now builds on, replacing the
    hand-rolled comprehension tools/mirror-litellm-env.py used to carry
    against catalog/providers.json directly."""

    def test_filters_falsy_and_keeps_registry_order(self):
        registry = _load_registry()
        providers = {
            "b": {"litellm_env": "B_KEY"},
            "a": {"litellm_env": "A_KEY"},
            "c": {"litellm_env": None},
            "d": {},
            "e": "not-a-dict",
        }
        self.assertEqual(
            registry.provider_field_map(providers, "litellm_env"),
            {"b": "B_KEY", "a": "A_KEY"})
        self.assertEqual(
            list(registry.provider_field_map(providers, "litellm_env")),
            ["b", "a"])


if __name__ == "__main__":
    unittest.main()
