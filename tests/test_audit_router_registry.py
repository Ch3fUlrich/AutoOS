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
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        "l3-driver": {
            "strategy": "priority",
            "legs": ["groq/openai/gpt-oss-120b", "cerebras/qwen-3.8-27b"],
            "surfaces": {"omniroute": {"context_declared": "128k"}},
        },
        "l3-driver-paid": {"strategy": "priority", "legs": [],
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
        self.assertEqual(combos[0]["name"], "l3-driver")
        self.assertEqual(combos[0]["models"],
                          ["groq/openai/gpt-oss-120b", "cerebras/qwen-3.8-27b"])

    def test_a_legs_empty_route_never_becomes_a_combo(self):
        audit = _load_module()
        combos = audit.repo_combos(registry_path=self.registry_path)
        names = [c["name"] for c in combos]
        self.assertNotIn("l3-driver-paid", names)

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

    def test_a_registry_with_no_routes_section_exits_with_an_error(self):
        # A document with no `routes` key is unreadable input, not a repo with
        # zero combos - render_omniroute() alone would render an empty list
        # silently. The old combos.json read raised SystemExit on its missing
        # key and this read must fail just as loudly.
        audit = _load_module()
        path = self.dir / "no-routes.json"
        path.write_text(json.dumps({"version": "2026-09-25", "providers": {}, "models": {}}),
                        encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            audit.repo_combos(registry_path=path)
        self.assertIn(f"cannot read routes from {path}", str(caught.exception))


# A registry fixture with two combo routes: one served to opencode only,
# one served to openhands too. Only the openhands one needs an
# tier profile named <name> (PREFIX-STRIP, no omniroute- prefix) (Q1fix: the four pinned opencode+zed-only
# routes were wrongly reported as drift).
OPENHANDS_FIXTURE = {
    "routes": {
        "route-opencode-only": {
            "strategy": "priority",
            "legs": ["groq/openai/gpt-oss-120b"],
            "surfaces": {"omniroute": {"clients": ["opencode"],
                                       "context_declared": "128k"}},
        },
        "route-openhands": {
            "strategy": "priority",
            "legs": ["groq/openai/gpt-oss-120b"],
            "surfaces": {"omniroute": {"clients": ["opencode", "openhands"],
                                       "context_declared": "128k"}},
        },
    }
}


class OpenHandsTierProfileTests(unittest.TestCase):
    """Only routes OpenHands actually serves need a tier profile: routes
    whose surfaces.omniroute.clients contains "openhands"."""

    def test_openhands_route_names_come_from_the_registry(self):
        audit = _load_module()
        self.assertEqual(audit.openhands_route_names(OPENHANDS_FIXTURE),
                         {"route-openhands"})

    def test_only_the_openhands_route_without_a_profile_is_drift(self):
        audit = _load_module()
        drift = audit.tier_profile_drift(
            ["route-opencode-only", "route-openhands"], set(), OPENHANDS_FIXTURE)
        self.assertEqual(drift, ["tier-profiles.json lacks 'route-openhands'"])


class ClientExposureTests(unittest.TestCase):
    def audit_missing_clients(self, omni, routes=None):
        audit = _load_module()
        doc = {"routes": {"fixture-route": {
            "strategy": "priority", "legs": ["groq/openai/gpt-oss-120b"],
            "surfaces": {"omniroute": {"context_declared": "128k", **omni}}}}}
        if routes is not None:
            doc["routes"] = routes
        with tempfile.TemporaryDirectory(dir=ROOT / "logs") as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            output = io.StringIO()
            with patch.object(audit, "opencode_models", return_value=[]), \
                    patch.object(audit, "repo_combos", return_value=[
                        {"name": "fixture-route", "models": []}]), \
                    patch.object(audit, "ide_models", return_value=[]), \
                    contextlib.redirect_stdout(output):
                audit.main(["--offline", "--json", "--registry", str(path)])
            return json.loads(output.getvalue())["drift"]

    def assert_client_drift(self, omni, required):
        drift = self.audit_missing_clients(omni)
        for line in ("opencode.jsonc lacks model 'fixture-route'",
                     "catalog/ide-models.json does not offer 'fixture-route' to zed"):
            self.assertEqual(line in drift, required, line)

    def test_explicit_ide_clients_still_required(self):
        self.assert_client_drift({"clients": ["opencode", "zed"]}, True)

    def test_unreadable_registry_requires_both_ide_clients(self):
        audit = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing-registry.json"
            output = io.StringIO()
            with patch.object(audit, "opencode_models", return_value=[]), \
                    patch.object(audit, "repo_combos", return_value=[
                        {"name": "fixture-route", "models": []}]), \
                    patch.object(audit, "ide_models", return_value=[]), \
                    contextlib.redirect_stdout(output):
                result = audit.main(["--offline", "--json", "--registry", str(path)])
            self.assertNotEqual(result, 0)
            drift = json.loads(output.getvalue())["drift"]
            self.assertIn("opencode.jsonc lacks model 'fixture-route'", drift)
            self.assertIn("catalog/ide-models.json does not offer 'fixture-route' to zed",
                          drift)

    def assert_unknown_route_required(self, routes):
        drift = self.audit_missing_clients({}, routes=routes)
        self.assertIn("opencode.jsonc lacks model 'fixture-route'", drift)
        self.assertIn("catalog/ide-models.json does not offer 'fixture-route' to zed", drift)

    def test_combo_absent_from_registry_requires_both_ide_clients(self):
        self.assert_unknown_route_required({})

    def test_non_dict_route_requires_both_ide_clients(self):
        self.assert_unknown_route_required({"fixture-route": "invalid"})

    def test_explicit_empty_clients_exempt_ide(self):
        self.assert_client_drift({"clients": []}, False)

    def test_missing_and_null_clients_fail_closed(self):
        for omni in ({}, {"clients": None}):
            with self.subTest(omni=omni):
                self.assert_client_drift(omni, True)

    def test_route_names_for_each_client(self):
        audit = _load_module()
        surfaces = {
            "both": {"omniroute": {"clients": ["opencode", "zed"]}},
            "oc": {"omniroute": {"clients": ["opencode"]}},
            "oh": {"omniroute": {"clients": ["openhands"]}},
            "empty": {"omniroute": {"clients": []}},
            "missing": {"omniroute": {}},
            "null": {"omniroute": {"clients": None}},
            "invalid": {"omniroute": {"clients": "zed"}},
            "no-omni": {}, "bad-omni": {"omniroute": []},
        }
        doc = {"routes": {rid: {"surfaces": s} for rid, s in surfaces.items()}}
        doc["routes"]["no-surfaces"] = {}
        fallback = {"missing", "null", "invalid", "no-omni", "bad-omni", "no-surfaces"}
        self.assertEqual(audit.route_names_for_client(doc, "opencode"),
                         fallback | {"both", "oc"})
        self.assertEqual(audit.route_names_for_client(doc, "zed"), fallback | {"both"})
        self.assertEqual(audit.route_names_for_client(doc, "openhands"), {"oh"})


if __name__ == "__main__":
    unittest.main()
