"""Tests for tools/mirror-litellm-env.py sourcing its provider -> litellm .env
name map from catalog/ai-registry.json instead of the deleted
catalog/providers.json (routing v2 spec 3.2 phase 2, task A5c).

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
    (task A5c) - same field names as the deleted catalog/providers.json
    (mapping doc section 2), so this is a source change only, never a shape
    change."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.registry_path = self.dir / "ai-registry.json"
        self.registry_path.write_text(json.dumps(MINIMAL_REGISTRY), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_a_registry_fixture_with_no_old_catalog_anywhere(self):
        # self.dir has no catalog/providers.json at all (it was deleted in
        # task A5e) - proves the default read never falls back to it.
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


class SharedKeyNameTests(unittest.TestCase):
    """MUSEAPI: providers.meta_api has no api-keys.yml entry of its own - it
    says key_name: meta and reuses the key the user already has. KEY_MAP is
    keyed by the api-keys.yml NAME, because that is what render() looks a value
    up in. Keyed by provider id (the old shape) meta_api would look up a
    'meta_api' that never exists and emit a second META_API_KEY= line whose
    REPLACE_WITH_ placeholder wins the last-line-wins race in the shell's env
    parser - LiteLLM would be handed a placeholder instead of the real key.
    Same rule apply.sh/apply.ps1 follow (MUSEAPI step 4)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _registry(self, providers):
        path = self.dir / "ai-registry.json"
        path.write_text(json.dumps({"providers": providers}), encoding="utf-8")
        return path

    def test_a_key_name_moves_the_entry_to_the_shared_api_keys_name(self):
        registry = self._registry({
            "groq": {"id": "groq", "litellm_env": "GROQ_API_KEY"},
            "meta": {"id": "meta", "litellm_env": "META_API_KEY"},
            "meta_api": {"id": "meta_api", "litellm_env": "META_API_KEY",
                         "key_name": "meta"},
        })
        mirror = _load_mirror()
        self.assertEqual(mirror.load_key_map(registry), {
            "groq": "GROQ_API_KEY",
            "meta": "META_API_KEY",
        })

    def test_the_shared_key_is_mirrored_once_and_is_not_overwritten_by_a_placeholder(self):
        registry = self._registry({
            "meta": {"id": "meta", "litellm_env": "META_API_KEY"},
            "meta_api": {"id": "meta_api", "litellm_env": "META_API_KEY",
                         "key_name": "meta"},
        })
        keys = self.dir / "api-keys.yml"
        keys.write_text("meta: real-looking-value\n", encoding="utf-8")
        env = self.dir / ".env"
        result = subprocess.run(
            [sys.executable, str(TOOL), "--registry", str(registry),
             "--keys", str(keys), "--env", str(env)],
            capture_output=True, text=True, cwd=str(self.dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [l for l in env.read_text(encoding="utf-8").splitlines()
                 if l.startswith("META_API_KEY=")]
        self.assertEqual(lines, ["META_API_KEY=real-looking-value"])

    def test_the_real_registry_mirrors_META_API_KEY_once(self):
        # The bug this test exists for, asserted against the shipped registry:
        # one META_API_KEY line, holding the value, no placeholder under it.
        mirror = _load_mirror()
        text, _mirrored, missing = mirror.render({"meta": "real-looking-value"}, None)
        meta_lines = [l for l in text.splitlines() if l.startswith("META_API_KEY=")]
        self.assertEqual(meta_lines, ["META_API_KEY=real-looking-value"])
        self.assertNotIn("REPLACE_WITH_META_API", text)
        self.assertNotIn("META_API_KEY", missing)

    def test_one_api_keys_name_feeding_two_different_env_vars_is_refused(self):
        # Silent first-wins here would mean one provider's key never reaches
        # the .env at all, so the ambiguity is an error, not a preference.
        registry = self._registry({
            "meta": {"id": "meta", "litellm_env": "META_API_KEY"},
            "meta_api": {"id": "meta_api", "litellm_env": "META_API_V2_KEY",
                         "key_name": "meta"},
        })
        mirror = _load_mirror()
        with self.assertRaises(ValueError):
            mirror.load_key_map(registry)

    def test_the_registry_ships_exactly_one_shared_key(self):
        # key_name is not a general pattern to spread: every share is a
        # decision, so a second one must be added on purpose (and documented
        # in docs/api-keys.md, which is where a reader learns the file's name).
        providers = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["providers"]
        self.assertEqual(
            sorted(n for n, e in providers.items() if isinstance(e, dict) and e.get("key_name")),
            ["meta_api"])


class RenderNeverEmitsANullDestinationTests(unittest.TestCase):
    """render() must never emit a line for a provider whose registry entry
    carries no litellm_env (an OAuth/subscription bridge) - regression for
    the "None=REPLACE_WITH_CC" bug a naive, unfiltered registry read would
    produce (cc/antigravity are new registry-only entries with no
    catalog/providers.json counterpart - that file was deleted in task A5e,
    so this bug has no old-catalog equivalent to have already caught it)."""

    def test_render_has_no_none_destination_line(self):
        mirror = _load_mirror()
        mirror.KEY_MAP = {"groq": "GROQ_API_KEY", "cc": None}
        text, mirrored, missing = mirror.render({"groq": "dummy"}, None)
        self.assertNotIn("None=", text)


class CliRegistryFlagTests(unittest.TestCase):
    """--registry lets a caller point the tool at a fixture directly - proves
    the default run needs no catalog/providers.json present anywhere (that
    file was deleted in task A5e; task A5c: 'a test that runs the tool with
    a registry fixture and NO old catalog present')."""

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
    against the deleted catalog/providers.json directly."""

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


class EnvSharingContractTests(unittest.TestCase):
    """tests/helpers/check-provider-registry.py's rule for two providers writing
    one .env variable. The check is data, not a name allowlist: an allowlist of
    ("meta", "meta_api") would let the next share through silently and stop
    being the reason the current one is safe."""

    def _check(self):
        return _load_module(ROOT / "tests" / "helpers" / "check-provider-registry.py",
                            "autoos_check_provider_registry_test")

    def test_two_providers_reading_the_same_name_may_share_one_env_var(self):
        helper = self._check()
        providers = {
            "meta": {"litellm_env": "META_API_KEY"},
            "meta_api": {"litellm_env": "META_API_KEY", "key_name": "meta"},
        }
        self.assertEqual(helper.litellm_env_problems(providers), [])

    def test_two_different_names_feeding_one_env_var_is_a_problem(self):
        helper = self._check()
        providers = {
            "meta": {"litellm_env": "META_API_KEY"},
            "other": {"litellm_env": "META_API_KEY"},
        }
        problems = helper.litellm_env_problems(providers)
        self.assertEqual(len(problems), 1)
        self.assertIn("META_API_KEY", problems[0])
        self.assertIn("key_name", problems[0])

    def test_a_key_name_must_point_at_another_provider(self):
        helper = self._check()
        providers = {
            "meta_api": {"litellm_env": "META_API_KEY", "key_name": "not_a_provider"},
        }
        self.assertTrue(any("not_a_provider" in p for p in helper.litellm_env_problems(providers)))

    def test_the_shipped_registry_satisfies_the_rule(self):
        helper = self._check()
        providers = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["providers"]
        self.assertEqual(helper.litellm_env_problems(providers), [])


class ModelPrefixContractTests(unittest.TestCase):
    """The same helper's rule for providers.<id>.model_prefix - the spelling
    gateway_ref() writes into combos.json (MUSEAPI). The bug it pins: meta_api
    claimed prefix 'meta', which is another provider's registry key, so
    tools/sync-router-tiers.py --combos - the explicit escape hatch onto
    combos.json's own leg order - looked 'meta' up in maps keyed only by
    provider name and omniroute_id, found the OTHER Meta record (no
    litellm_prefix, no api_base), and rendered the contributor leg as a plain
    `meta/<model>` with no api_base at all. The registry-sourced default run
    could not catch it because it never reads that spelling."""

    def _check(self):
        return _load_module(ROOT / "tests" / "helpers" / "check-provider-registry.py",
                            "autoos_check_provider_registry_prefix_test")

    def test_a_servable_provider_may_not_borrow_another_providers_key(self):
        helper = self._check()
        providers = {
            "meta": {"litellm_env": "META_API_KEY"},
            "meta_api": {"litellm_env": "META_API_KEY", "litellm_prefix": "openai",
                         "key_name": "meta", "omniroute_id": "meta-api",
                         "model_prefix": "meta"},
        }
        problems = helper.model_prefix_problems(providers)
        self.assertTrue(any("another provider's registry" in p for p in problems), problems)
        self.assertTrue(any("neither its own key nor its omniroute_id" in p
                            for p in problems), problems)

    def test_a_gateway_prefix_of_its_own_omniroute_id_is_clean(self):
        helper = self._check()
        providers = {
            "meta": {"litellm_env": "META_API_KEY"},
            "meta_api": {"litellm_env": "META_API_KEY", "litellm_prefix": "openai",
                         "key_name": "meta", "omniroute_id": "meta-api",
                         "model_prefix": "meta-api"},
        }
        self.assertEqual(helper.model_prefix_problems(providers), [])

    def test_a_gateway_only_bridge_keeps_its_foreign_spelling(self):
        # antigravity -> agy: no LiteLLM transport at all, so no managed block
        # ever resolves the prefix, and sync-router-tiers lists both spellings
        # in GATEWAY_ONLY by hand.
        helper = self._check()
        providers = {
            "antigravity": {"omniroute_id": "antigravity", "litellm_env": None,
                            "litellm_prefix": None, "model_prefix": "agy"},
        }
        self.assertEqual(helper.model_prefix_problems(providers), [])

    def test_the_shipped_registry_satisfies_the_rule(self):
        helper = self._check()
        providers = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["providers"]
        self.assertEqual(helper.model_prefix_problems(providers), [])


if __name__ == "__main__":
    unittest.main()
