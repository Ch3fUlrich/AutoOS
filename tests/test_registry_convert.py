#!/usr/bin/env python3
"""Unit tests for tools/registry-convert.py (routing v2 spec, task A2).

Path-independent: everything is anchored on ROOT = Path(__file__).resolve().parent.parent,
never on the current working directory or a hard-coded home path (this repo is public,
AGENTS.md rule 1).

No jsonschema dependency (stdlib only, matching the rest of this repo's test suites): the
"every schema-required key is present" check is a small hand-rolled structural walk of
catalog/ai-registry.schema.json's own $defs required-lists, not a real validator. A real
validator is tools/registry.py check (task A3, not this one).
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "tools" / "registry-convert.py"
SCHEMA_PATH = ROOT / "catalog" / "ai-registry.schema.json"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"

COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
IDE_MODELS_PATH = ROOT / "catalog" / "ide-models.json"

sys.path.insert(0, str(ROOT / "tools"))
import autoos_resolver  # noqa: E402  (path set up above)


def run_converter():
    """Run the converter fresh and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(CONVERTER)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class ConverterRunTests(unittest.TestCase):
    """Running the converter succeeds and produces valid, deterministic JSON."""

    @classmethod
    def setUpClass(cls):
        cls.assertTrue = unittest.TestCase.assertTrue  # keep pylint quiet, unused
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\nstdout:\n%s\nstderr:\n%s"
                                  % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_converter_exits_zero_and_writes_the_registry(self):
        self.assertTrue(REGISTRY_PATH.is_file(), "%s was not written" % REGISTRY_PATH)

    def test_output_is_deterministic_on_a_second_run(self):
        first = REGISTRY_PATH.read_bytes()
        rc, out, err = run_converter()
        self.assertEqual(rc, 0, "second run failed:\n%s\n%s" % (out, err))
        second = REGISTRY_PATH.read_bytes()
        self.assertEqual(first, second, "a second run changed the byte-identical output")

    def test_output_ends_with_a_trailing_newline(self):
        self.assertTrue(REGISTRY_PATH.read_bytes().endswith(b"\n"))

    def test_output_keys_are_sorted_and_2_space_indented(self):
        text = REGISTRY_PATH.read_text(encoding="utf-8")
        # 2-space indent: the second line (first nested key) starts with exactly 2 spaces,
        # not 4, not a tab.
        lines = text.splitlines()
        indented = [ln for ln in lines if ln.startswith("  ") and not ln.startswith("   ")]
        self.assertTrue(indented, "no line found at the expected 2-space indent level")
        reserialized = json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False)
        self.assertEqual(text.rstrip("\n"), reserialized,
                          "output is not sort_keys=True, indent=2 json.dumps output")


class StructuralValidatorTests(unittest.TestCase):
    """A tiny hand-rolled structural check: every schema-required key is present.

    Deliberately not a real JSON Schema validator (no jsonschema dependency, matching
    every other suite in this repo - see AGENTS.md section 5).
    """

    REQUIRED_TOP = ("version", "providers", "models", "routes", "clients", "policy")
    REQUIRED_PROVIDER = ("id", "omniroute_id", "litellm_env", "litellm_prefix",
                          "trains_on_prompts", "tier")
    REQUIRED_MODEL = ("id", "family", "context_advertised", "context_usable", "output_max",
                       "reasoning", "effort_ladder", "tool_calls", "price_in", "price_out")
    REQUIRED_CONTEXT_USABLE = ("tokens", "source")
    REQUIRED_ROUTE = ("id", "class", "strategy", "legs", "surfaces")
    REQUIRED_CLIENT = ("id", "binary", "gateway_mode", "skills_dir", "supports_effort",
                        "signin_check")
    REQUIRED_POLICY = ("modes", "bucket_table", "effort_rules", "handoff_caps",
                        "risk_rules", "seed_priors", "latency_seed", "verify_tokens",
                        "brief_tokens")

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)
        cls.schema = load_json(SCHEMA_PATH)

    def _assert_keys(self, entry, required, where):
        missing = [k for k in required if k not in entry]
        self.assertFalse(missing, "%s missing required key(s): %s (has: %s)"
                          % (where, missing, sorted(entry)))

    def test_schema_is_draft_2020_12(self):
        self.assertEqual(self.schema.get("$schema"),
                          "https://json-schema.org/draft/2020-12/schema")

    def test_schema_defines_every_section(self):
        for section in ("providers", "models", "routes", "clients", "policy"):
            self.assertIn(section, self.schema["properties"],
                          "schema has no top-level property %r" % section)

    def test_top_level_keys(self):
        self._assert_keys(self.registry, self.REQUIRED_TOP, "registry root")

    def test_every_provider_has_required_keys(self):
        for pid, entry in self.registry["providers"].items():
            self._assert_keys(entry, self.REQUIRED_PROVIDER, "providers.%s" % pid)
            self.assertEqual(entry["id"], pid)
            self.assertIn(entry["tier"], ("free", "paid", "subscription"))
            self.assertIsInstance(entry["trains_on_prompts"], bool)

    def test_every_model_has_required_keys(self):
        for mid, entry in self.registry["models"].items():
            self._assert_keys(entry, self.REQUIRED_MODEL, "models.%s" % mid)
            self.assertEqual(entry["id"], mid)
            self._assert_keys(entry["context_usable"], self.REQUIRED_CONTEXT_USABLE,
                              "models.%s.context_usable" % mid)
            self.assertIn(entry["tool_calls"], ("proven", "unproven", "broken"))
            self.assertIsInstance(entry["effort_ladder"], list)

    def test_every_route_has_required_keys(self):
        for rid, entry in self.registry["routes"].items():
            self._assert_keys(entry, self.REQUIRED_ROUTE, "routes.%s" % rid)
            self.assertEqual(entry["id"], rid)
            self.assertIn(entry["class"], ("free", "cheap", "mid", "frontier"))
            self.assertIsInstance(entry["legs"], list)
            self.assertIsInstance(entry["surfaces"], dict)

    def test_every_client_has_required_keys(self):
        expected_ids = {"opencode", "claude", "qwen", "gemini", "codex", "agy", "qoder"}
        self.assertEqual(set(self.registry["clients"]), expected_ids)
        for cid, entry in self.registry["clients"].items():
            self._assert_keys(entry, self.REQUIRED_CLIENT, "clients.%s" % cid)
            self.assertEqual(entry["id"], cid)
            self.assertIn(entry["gateway_mode"], ("omniroute-run", "native", "none"))

    def test_policy_has_required_keys(self):
        self._assert_keys(self.registry["policy"], self.REQUIRED_POLICY, "policy")


class IdUniquenessTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_ids_are_unique_across_sections(self):
        # providers/models/clients must be globally unique among themselves - a bare
        # id from a leg (provider/model) or a client id is never ambiguous with
        # another section's id. routes are exempted from colliding with *models*
        # only: a pinned single-model route is conventionally named after its own
        # leading leg (routes.'gemini-3.8-flash' / routes.'deepseek-v4.1-flash' both
        # share their id with the bare model spelling of their own head leg) - see
        # docs/plans/2026-09-25-registry-mapping.md Open choices. A route id still
        # may not collide with a provider or a client id.
        seen = {}
        for section in ("providers", "models", "clients"):
            for entry_id in self.registry[section]:
                self.assertNotIn(entry_id, seen,
                                  "id %r used in both %s and %s" % (entry_id, seen.get(entry_id), section))
                seen[entry_id] = section
        non_model_ids = set(self.registry["providers"]) | set(self.registry["clients"])
        for route_id in self.registry["routes"]:
            self.assertNotIn(route_id, non_model_ids,
                              "route id %r collides with a provider/client id" % route_id)


class RouteResolutionTests(unittest.TestCase):
    """Every combos.json leg resolves to a models x providers pair, and every
    ide-models.json id shows up as a route surface (spec 3.2 migration gate)."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)
        cls.combos = load_json(COMBOS_PATH)
        cls.ide_models = load_json(IDE_MODELS_PATH)

    def _provider_ids(self):
        ids = set()
        for pid, entry in self.registry["providers"].items():
            ids.add(pid)
            if entry.get("omniroute_id"):
                ids.add(entry["omniroute_id"])
        return ids

    def test_every_combos_leg_resolves_to_a_model_and_a_provider(self):
        provider_ids = self._provider_ids()
        model_ids = set(self.registry["models"])
        registry_legs = set()
        for route in self.registry["routes"].values():
            registry_legs.update(route["legs"])

        checked = 0
        for combo in self.combos["combos"]:
            for leg in combo["models"]:
                checked += 1
                self.assertIn(leg, registry_legs,
                              "combos.json leg %r (combo %r) is not a leg of any registry route"
                              % (leg, combo["name"]))
                provider, _, model = leg.partition("/")
                self.assertIn(provider, provider_ids,
                              "leg %r: provider %r has no registry providers entry" % (leg, provider))
                self.assertIn(model, model_ids,
                              "leg %r: model %r has no registry models entry" % (leg, model))
        self.assertGreater(checked, 0, "combos.json had no legs to check - test is vacuous")

    def test_every_ide_models_id_appears_as_a_route_surface(self):
        route_ids = set(self.registry["routes"])
        for entry in self.ide_models["models"]:
            self.assertIn(entry["id"], route_ids,
                          "ide-models.json id %r has no registry routes entry" % entry["id"])
            surfaces = self.registry["routes"][entry["id"]]["surfaces"]
            self.assertTrue(surfaces, "routes.%s.surfaces is empty" % entry["id"])


PRIVATE_HOST_NAMES = {"localhost", "host.docker.internal"}


def looks_like_a_private_host(value: str) -> bool:
    """True if `value` (a URL or bare host) names a private/loopback/link-local host."""
    host = value
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    host = host.rsplit("@", 1)[-1]  # userinfo@host
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host  # host:port (not IPv6)
    host = host.strip("[]")
    if host.lower() in PRIVATE_HOST_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


class UnavailableLegTests(unittest.TestCase):
    """Operator decision 2026-09-26: opencode-zen/deepseek-v4.1-flash answered
    402 (payment required) in the tool-calling probe -> unavailable, kept in
    place like the OpenRouter legs."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_zen_deepseek_leg_is_unavailable_everywhere_it_appears(self):
        leg = "opencode-zen/deepseek-v4.1-flash"
        seen = 0
        for route_id, route in self.registry["routes"].items():
            if leg in route["legs"]:
                seen += 1
                self.assertIn(leg, route.get("unavailable_legs", {}), route_id)
                self.assertIs(route["unavailable_legs"][leg]["available"], False)
        self.assertGreaterEqual(seen, 3)

    def test_cerebras_legs_are_unavailable_everywhere_they_appear(self):
        # L0 measurement 2026-09-26T11:44Z: gpt-oss-120b 402, qwen-3.8-27b 401
        # "credits exhausted"; OmniRoute deactivated the connection itself.
        for leg in ("cerebras/gpt-oss-120b", "cerebras/qwen-3.8-27b"):
            seen = 0
            for route_id, route in self.registry["routes"].items():
                if leg in route["legs"]:
                    seen += 1
                    self.assertIs(route.get("unavailable_legs", {}).get(leg, {})
                                  .get("available"), False, (leg, route_id))
            self.assertGreaterEqual(seen, 1, leg)

    def test_openrouter_legs_stay_unavailable_beside_it(self):
        route = self.registry["routes"]["t2-worker-clean"]
        self.assertIn("openrouter/deepseek/deepseek-v4.1-flash", route["unavailable_legs"])
        self.assertIn("opencode-zen/deepseek-v4.1-flash", route["unavailable_legs"])


class PrivacyTests(unittest.TestCase):
    """registry.py check rule (spec 3.1): api_base holds only a public vendor endpoint."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_no_private_host_or_ip_in_any_provider_api_base(self):
        checked = 0
        for pid, entry in self.registry["providers"].items():
            api_base = entry.get("api_base")
            if not api_base:
                continue
            checked += 1
            self.assertFalse(looks_like_a_private_host(api_base),
                              "providers.%s.api_base %r looks like a private host/IP"
                              % (pid, api_base))
        self.assertGreater(checked, 0, "no provider had an api_base to check - test is vacuous")


def _bucket_table_from_registry(policy_bucket_table: dict) -> dict:
    """Reconstruct the exact DEFAULT_BUCKET_TABLE shape from its JSON mirror.

    JSON has no boolean or tuple types: the registry stores `tests` keys as the
    strings "true"/"false" and every band/bucket list as a JSON array. This
    reverses both so the result can be compared to DEFAULT_BUCKET_TABLE with a
    plain ==, matching what a real loader in tools/autoos_resolver.py would need
    to do before calling points()/bucket() with it.
    """
    table = policy_bucket_table["table"]
    features = {
        name: tuple(dict(band) for band in bands)
        for name, bands in table["features"].items()
    }
    tests = {True: table["tests"]["true"], False: table["tests"]["false"]}
    buckets = tuple(dict(b) for b in table["buckets"])
    return {
        "features": features,
        "spec": dict(table["spec"]),
        "tests": tests,
        "kind": dict(table["kind"]),
        "buckets": buckets,
    }


class MistralCodeTrainsOnPromptsTests(unittest.TestCase):
    """PRIV brief 2026-09-26: mistral-code-latest is a free, training pool
    served through the otherwise-clean paid `mistral` provider (docs/models.md:
    "FREE 1B/mo pool ... same key bills past it"; tests/run-tests.sh's own
    combos.json rule already treats `mistral/mistral-code` as a "free" leg
    that must never appear in a `-clean` combo). Provider-level
    trains_on_prompts alone cannot mark this one leg unsafe without also
    marking every other mistral leg (mistral-small-latest, which does not
    train) unsafe -- hence a model-level override."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_mistral_code_latest_carries_a_true_override_with_a_comment(self):
        model = self.registry["models"]["mistral-code-latest"]
        self.assertIs(model.get("trains_on_prompts"), True)
        self.assertIn("$comment", model)

    def test_sibling_mistral_small_latest_has_no_override(self):
        # The provider-level flag alone still governs every other mistral leg.
        model = self.registry["models"]["mistral-small-latest"]
        self.assertNotIn("trains_on_prompts", model)

    def test_mistral_provider_itself_stays_clean(self):
        # The provider-level flag is unchanged: mistral hosts both a training
        # (mistral-code-latest) and a non-training (mistral-small-latest) leg,
        # same documented shape as the openrouter/zen exceptions.
        self.assertIs(self.registry["providers"]["mistral"]["trains_on_prompts"], False)


class PRIV2ModelLevelOverrideTests(unittest.TestCase):
    """PRIV2 brief 2026-09-26: optional model-level tier/trains_on_prompts
    overrides, converted from small explicit tables (same EXTRA_MODELS
    convention as MistralCodeTrainsOnPromptsTests above)."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)
        cls.schema = load_json(SCHEMA_PATH)

    def test_schema_model_defines_an_optional_tier_override(self):
        tier_schema = self.schema["$defs"]["model"]["properties"].get("tier")
        self.assertIsNotNone(tier_schema, "schema $defs.model.properties has no 'tier'")
        self.assertEqual(sorted(tier_schema.get("enum", [])), ["free", "paid", "subscription"])
        self.assertNotIn("tier", self.schema["$defs"]["model"].get("required", []))

    def test_zen_deepseek_v4_1_flash_carries_a_paid_tier_override(self):
        model = self.registry["models"]["deepseek-v4.1-flash"]
        self.assertEqual(model.get("tier"), "paid")
        self.assertIn("$comment", model)

    def test_zen_provider_itself_stays_free(self):
        self.assertEqual(self.registry["providers"]["zen"]["tier"], "free")

    def test_openrouter_contributor_model_carries_a_training_override(self):
        model = self.registry["models"]["meta/muse-spark-1.3-contributor"]
        self.assertIs(model.get("trains_on_prompts"), True)
        self.assertIn("$comment", model)

    def test_zen_free_contributor_model_carries_a_training_override(self):
        model = self.registry["models"]["muse-spark-1.3-contributor-free"]
        self.assertIs(model.get("trains_on_prompts"), True)
        self.assertIn("$comment", model)

    def test_sibling_models_have_no_unwanted_overrides(self):
        # deepseek/deepseek-v4.1-flash (openrouter's spelling of the same
        # real model as the zen leg above) and deepseek-flash (deepseek
        # direct) are different legs and keep no tier override.
        for mid in ("deepseek/deepseek-v4.1-flash", "deepseek-flash"):
            self.assertNotIn("tier", self.registry["models"][mid])


class BucketTableParityTests(unittest.TestCase):
    """policy.bucket_table must equal tools/autoos_resolver.DEFAULT_BUCKET_TABLE (task brief)."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run_converter()
        if rc != 0:
            raise AssertionError("registry-convert.py exited %d\n%s\n%s" % (rc, out, err))
        cls.registry = load_json(REGISTRY_PATH)

    def test_bucket_table_matches_the_resolver_default(self):
        reconstructed = _bucket_table_from_registry(self.registry["policy"]["bucket_table"])
        self.assertEqual(reconstructed, autoos_resolver.DEFAULT_BUCKET_TABLE)

    def test_bucket_table_carries_a_source_tag(self):
        self.assertIn("source", self.registry["policy"]["bucket_table"])


if __name__ == "__main__":
    unittest.main()
