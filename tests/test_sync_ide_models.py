"""Tests for tools/sync-ide-models.py: catalog/ide-models.json -> client surfaces.

Run from the repo root:

    python3 tests/test_sync_ide_models.py

Every test works on temp copies of the real files; nothing in the checkout is
ever written.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "sync-ide-models.py"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
SOURCES = {
    "catalog": ROOT / "catalog" / "ide-models.json",
    "opencode": ROOT / "opencode.jsonc",
    "tier_profiles": ROOT / "configuration" / "openhands" / "tier-profiles.json",
    "openhands_toml": ROOT / "configuration" / "openhands" / "config.toml",
}
FLAGS = {
    "catalog": "--catalog",
    "opencode": "--opencode",
    "tier_profiles": "--tier-profiles",
    "openhands_toml": "--openhands-toml",
}


def route_surface(doc, route_id, gateway="omniroute"):
    return doc["routes"][route_id]["surfaces"][gateway]


def strip_jsonc(text):
    # The suites' and tools/audit-router.py's strip: whole-line // only.
    return re.sub(r"(?m)^\s*//.*$", "", text)


def managed_models(text, gateway):
    """The GENERATED part of `text`'s models map for `gateway`: the lines
    between its AUTOOS-MANAGED-START/END markers, parsed as a JSON object.

    The map also carries hand entries (direct-provider passthrough) that the
    file's own comment and the tool's docstring keep OUTSIDE the region - they
    are deliberate content, not drift, so membership is proven for the
    generated entries only."""
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None and "AUTOOS-MANAGED-START" in line and gateway in line:
            start = i
        elif start is not None and "AUTOOS-MANAGED-END" in line and gateway in line:
            end = i
            break
    assert start is not None and end is not None, "managed region missing: " + gateway
    return json.loads(strip_jsonc("{" + "\n".join(lines[start + 1:end]) + "}"))


class Sandbox:
    """Temp copies of the four files plus a runner pointed at them."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.paths = {}
        for key, src in SOURCES.items():
            dst = self.dir / src.name
            shutil.copyfile(src, dst)
            self.paths[key] = dst

    def close(self):
        self._tmp.cleanup()

    def run(self, *extra):
        args = [sys.executable, str(TOOL)]
        for key, flag in FLAGS.items():
            args += [flag, str(self.paths[key])]
        return subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8")

    def read(self, key):
        return self.paths[key].read_bytes()

    def text(self, key):
        return self.paths[key].read_text(encoding="utf-8")

    def write(self, key, text):
        self.paths[key].write_bytes(text.encode("utf-8"))

    def catalog(self):
        return json.loads(self.text("catalog"))

    def save_catalog(self, doc):
        self.write("catalog", json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    def snapshot(self):
        return {key: self.read(key) for key in self.paths}


def model(doc, model_id):
    return next(m for m in doc["models"] if m["id"] == model_id)


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.close()


class RepoTests(unittest.TestCase):
    def test_the_checkout_is_in_sync(self):
        result = subprocess.run([sys.executable, str(TOOL), "--check"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_wide_tiers_carry_the_window_their_smallest_servable_leg_takes(self):
        # PROVFIX3 finding 1 re-pins this: "1M everywhere" was the defect. A
        # route falls through to its smallest leg at any time, so the promise is
        # the narrowest advertised window among its SERVED legs.
        # CIGREEN: expectation moved by 018438ed (TORDER TASK1, t1 band 1M-only:
        # every sub-1M leg moved out of t1 into t2/t3, so on this branch every
        # served leg of t1-orchestrator and t1-orchestrator-free-only is a 1M
        # leg and the honest promise is back to 1000000).
        clamp = {"t1-orchestrator": 1000000, "t1-orchestrator-free-only": 1000000,
                 "t1-orchestrator-paid": 1000000, "spark-1.3-contributor": 1000000}
        doc = json.loads(SOURCES["catalog"].read_text(encoding="utf-8"))
        seen = set()
        for m in doc["models"]:
            if m["id"] in clamp:
                seen.add(m["id"])
                self.assertEqual(m["context"], clamp[m["id"]], m["id"])
        self.assertEqual(seen, set(clamp), "a wide tier left the catalog")


class CheckTests(SandboxCase):
    def test_check_is_clean_on_a_fresh_copy(self):
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_reports_drift_with_a_diff_and_writes_nothing(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["context"] = 262144
        self.box.save_catalog(doc)
        before = self.box.snapshot()
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("262144", result.stdout)
        self.assertIn("+++ b/", result.stdout)
        self.assertIn("DRIFT", result.stderr)
        self.assertEqual(self.box.snapshot(), before)

    def test_a_display_name_change_is_drift_in_opencode(self):
        doc = self.box.catalog()
        model(doc, "t3-driver")["name"] = "t3 renamed"
        self.box.save_catalog(doc)
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn('"name": "t3 renamed"', result.stdout)


class WriteTests(SandboxCase):
    def drift(self):
        # deepseek-v4.1-flash was the drift model until it fail-closed
        # (deepseek 402, 2026-09-27T16:4xZ) and left ide-models.json;
        # t2-worker-clean is the servable equivalent with a ladder.
        doc = self.box.catalog()
        model(doc, "t2-worker-clean")["output"] = 40000
        model(doc, "t3-driver")["context"] = 65536
        self.box.save_catalog(doc)

    def test_write_fixes_every_surface_and_a_rerun_is_byte_identical(self):
        self.drift()
        first = self.box.run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        after_first = self.box.snapshot()
        second = self.box.run()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("Already in sync", second.stdout)
        self.assertEqual(self.box.snapshot(), after_first)
        self.assertEqual(self.box.run("--check").returncode, 0)

        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        t1 = oc["providers"]["omniroute"]["models"]["t2-worker-clean"]
        # CIGREEN: expectation moved by e1da4f7a (L1-CLEAN D2 render moved the
        # -clean twins from deepseek's 1M window to the 128k trial-first head;
        # 35148c5c made ovh the head) - the drift here only changes output.
        self.assertEqual(t1["limit"], {"context": 128000, "output": 40000})
        self.assertEqual(oc["providers"]["litellm"]["models"]["t3-driver"]["limit"]["context"], 65536)
        spec = json.loads(self.box.text("tier_profiles"))
        by_id = {t["id"]: t for t in spec["tiers"]}
        self.assertEqual(by_id["omniroute-t2-worker-clean"]["max_output_tokens"], 40000)
        self.assertEqual(by_id["litellm-t3-driver"]["max_input_tokens"], 65536)
        toml = self.box.text("openhands_toml")
        section = toml.split("[llm.t3-driver]", 1)[1].split("\n[", 1)[0]
        self.assertIn("max_input_tokens = 65536", section)

    def test_everything_outside_the_managed_regions_is_untouched(self):
        original = self.box.text("opencode")
        self.drift()
        self.assertEqual(self.box.run().returncode, 0)
        new = self.box.text("opencode")

        def outside(text):
            out, inside = [], False
            for line in text.splitlines():
                if "AUTOOS-MANAGED-START" in line:
                    inside = True
                if not inside:
                    out.append(line)
                if "AUTOOS-MANAGED-END" in line:
                    inside = False
            return out

        self.assertEqual(outside(new), outside(original))

    def test_crlf_files_stay_crlf(self):
        text = self.box.text("opencode").replace("\r\n", "\n").replace("\n", "\r\n")
        self.box.write("opencode", text)
        self.drift()
        self.assertEqual(self.box.run().returncode, 0)
        raw = self.box.read("opencode")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        again = self.box.read("opencode")
        self.assertEqual(self.box.run().returncode, 0)
        self.assertEqual(self.box.read("opencode"), again)

    def test_names_stay_utf8_not_escaped(self):
        self.drift()
        self.assertEqual(self.box.run().returncode, 0)
        self.assertIn("—", self.box.text("opencode"))
        self.assertNotIn("\\u2014", self.box.text("opencode"))

    def test_generated_models_follow_catalog_order_and_membership(self):
        self.assertEqual(self.box.run().returncode, 0)
        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        doc = self.box.catalog()
        for gateway in ("omniroute", "litellm"):
            want = [m["id"] for m in doc["models"]
                    if "opencode" in m["surfaces"].get(gateway, [])]
            got = list(managed_models(self.box.text("opencode"), gateway))
            self.assertEqual(got, want, gateway)
            for mid in want:
                entry = oc["providers"][gateway]["models"][mid]
                self.assertEqual(entry["modelID"], mid)
                self.assertEqual(entry["name"], model(doc, mid)["name"])

    def test_variants_blocks_are_generated_from_effort_ladder(self):
        # A6a: models whose route's served head leg carries an effort_ladder in
        # the registry get a "variants" block with reasoningEffort labels (none
        # omitted). Models without a ladder get no variants.
        self.assertEqual(self.box.run().returncode, 0)
        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        for gateway in ("omniroute", "litellm"):
            for entry in oc["providers"][gateway]["models"].values():
                allowed = {"modelID", "name", "limit", "variants"}
                unexpected = set(entry) - allowed
                self.assertEqual(unexpected, set(),
                                 f"{gateway} {entry.get('modelID')} has unexpected keys: {unexpected}")
                if "variants" in entry:
                    self.assertIsInstance(entry["variants"], list)
                    # opencode v2.0.16 config schema (packages/schema/src/config/
                    # provider.ts:76-79): each item is {id, ...ModelOverlays};
                    # the effort rides in settings.reasoningEffort
                    # (packages/ai/src/protocols/openai-chat.ts:776-792).
                    for v in entry["variants"]:
                        self.assertEqual(set(v), {"id", "settings"}, v)
                        self.assertEqual(v["settings"], {"reasoningEffort": v["id"]})
        # CIGREEN: expectation moved by 35148c5c (trial-first clean routes head
        # ovh gpt-oss-120b, ladder low/medium/high) + e1da4f7a (the render
        # follows the served head): the -clean twins now carry low/medium/high,
        # and t1-orchestrator-free-only's served head is the gemini free leg
        # (ladder low/medium/high), so it carries variants too; t3-driver's
        # served head likewise carries low/medium/high now.
        # The gemini-3.8-flash combo heads on vertex (its gemini-3.8-flash model
        # ladder is low/medium/high) and stays the low/medium/high case.
        free = oc["providers"]["omniroute"]["models"]["gemini-3.8-flash"]
        self.assertEqual([v["id"] for v in free["variants"]],
                         ["low", "medium", "high"])
        for v in free["variants"]:
            self.assertEqual(v["settings"], {"reasoningEffort": v["id"]})
        self.assertEqual(
            [v["id"] for v in
             oc["providers"]["omniroute"]["models"]["t1-orchestrator-free-only"]["variants"]],
            ["low", "medium", "high"])
        clean = oc["providers"]["omniroute"]["models"]["t2-worker-clean"]
        self.assertEqual([v["id"] for v in clean["variants"]],
                         ["low", "medium", "high"])
        for v in clean["variants"]:
            self.assertEqual(v["settings"], {"reasoningEffort": v["id"]})
        self.assertEqual(
            [v["id"] for v in
             oc["providers"]["omniroute"]["models"]["t3-driver-clean"]["variants"]],
            ["low", "medium", "high"])
        # GLM55 2026-10-05 (operator): t3-driver heads on oc/glm-5.3-flash,
        # whose model row carries no effort ladder - the variants block drops
        # (the render's rule), and t2-worker moves with it.
        self.assertNotIn("variants",
                         oc["providers"]["omniroute"]["models"]["t3-driver"])
        self.assertNotIn("variants",
                         oc["providers"]["omniroute"]["models"]["t2-worker"])


class CommaDisciplineTests(SandboxCase):
    """The managed region sits inside a JSON object: commas are the hazard."""

    def hand_entry(self, where):
        lines = self.box.text("opencode").split("\n")
        start = next(i for i, l in enumerate(lines) if l.strip() == "// AUTOOS-MANAGED-START litellm")
        end = next(i for i, l in enumerate(lines) if l.strip() == "// AUTOOS-MANAGED-END litellm")
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        if where == "after":
            lines[end + 1:end + 1] = [indent + '"my-own": { "modelID": "my-own", "name": "mine" }']
        else:
            lines[start:start] = [indent + '"my-own": { "modelID": "my-own", "name": "mine" },']
        self.box.write("opencode", "\n".join(lines))

    def assert_parses_with_hand_entry(self):
        result = self.box.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        models = oc["providers"]["litellm"]["models"]
        self.assertIn("my-own", models)
        self.assertIn("t2-worker", models)
        self.assertEqual(self.box.run("--check").returncode, 0)

    def test_a_hand_entry_after_the_region_gets_a_comma_before_it(self):
        # The region's last entry is followed by a hand entry, so it needs a comma.
        self.hand_entry("after")
        self.assert_parses_with_hand_entry()

    def test_a_hand_entry_before_the_region_is_kept(self):
        self.hand_entry("before")
        self.assert_parses_with_hand_entry()

    def test_a_missing_comma_before_the_region_is_refused(self):
        lines = self.box.text("opencode").split("\n")
        start = next(i for i, l in enumerate(lines) if l.strip() == "// AUTOOS-MANAGED-START litellm")
        lines[start:start] = ['        "my-own": { "modelID": "my-own" }']
        self.box.write("opencode", "\n".join(lines))
        before = self.box.snapshot()
        result = self.box.run()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        # Named for what to fix, not just "does not parse".
        self.assertIn("needs its comma", result.stderr)
        self.assertEqual(self.box.snapshot(), before)


class UnusableInputTests(SandboxCase):
    def assert_refused(self, *needles):
        before = self.box.snapshot()
        for args in ((), ("--check",)):
            result = self.box.run(*args)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            for needle in needles:
                self.assertIn(needle, result.stderr)
        self.assertEqual(self.box.snapshot(), before)

    def test_a_missing_end_marker(self):
        self.box.write("opencode", self.box.text("opencode").replace(
            "// AUTOOS-MANAGED-END omniroute\n", ""))
        self.assert_refused("omniroute")

    def test_a_doubled_start_marker(self):
        text = self.box.text("opencode")
        self.box.write("opencode", text.replace(
            "// AUTOOS-MANAGED-START litellm", "// AUTOOS-MANAGED-START litellm\n// AUTOOS-MANAGED-START litellm", 1))
        self.assert_refused("litellm")

    def test_a_block_for_an_unknown_gateway(self):
        text = self.box.text("opencode")
        self.box.write("opencode", text.replace("AUTOOS-MANAGED-START litellm", "AUTOOS-MANAGED-START nope")
                       .replace("AUTOOS-MANAGED-END litellm", "AUTOOS-MANAGED-END nope"))
        self.assert_refused()

    def test_an_unparseable_catalog(self):
        self.box.write("catalog", "{ not json")
        self.assert_refused()

    def test_a_catalog_model_without_a_context(self):
        doc = self.box.catalog()
        del model(doc, "t2-worker")["context"]
        self.box.save_catalog(doc)
        self.assert_refused("t2-worker", "context")

    def test_an_unknown_surface(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["surfaces"]["omniroute"].append("vscode")
        self.box.save_catalog(doc)
        self.assert_refused("vscode")

    def test_a_duplicate_id(self):
        doc = self.box.catalog()
        doc["models"].append(dict(model(doc, "t2-worker")))
        self.box.save_catalog(doc)
        self.assert_refused("t2-worker")

    def test_a_tier_profile_for_a_model_the_catalog_does_not_offer_there(self):
        spec = json.loads(self.box.text("tier_profiles"))
        spec["tiers"].append({"id": "litellm-t4-rag", "gateway": "litellm", "model": "openai/t4-rag",
                              "max_input_tokens": 1, "max_output_tokens": 1, "reasoning": False})
        self.box.write("tier_profiles", json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
        self.assert_refused("litellm-t4-rag")

    def test_a_catalog_openhands_model_with_no_tier_profile(self):
        doc = self.box.catalog()
        model(doc, "t4-rag")["surfaces"]["litellm"].append("openhands")
        self.box.save_catalog(doc)
        self.assert_refused("litellm-t4-rag")

    def test_non_list_effort_ladder_is_refused(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["effort_ladder"] = "low"
        self.box.save_catalog(doc)
        self.assert_refused("effort_ladder")

    def test_non_string_in_effort_ladder_list_is_refused(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["effort_ladder"] = ["low", 42, "high"]
        self.box.save_catalog(doc)
        self.assert_refused("effort_ladder")

    def test_empty_effort_ladder_list_is_refused(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["effort_ladder"] = []
        self.box.save_catalog(doc)
        self.assert_refused("effort_ladder", "empty")

    def test_none_in_effort_ladder_is_refused(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["effort_ladder"] = ["none", "low", "medium"]
        self.box.save_catalog(doc)
        self.assert_refused("effort_ladder", "none")

    def test_duplicate_rungs_in_effort_ladder_is_refused(self):
        doc = self.box.catalog()
        model(doc, "t2-worker")["effort_ladder"] = ["low", "medium", "low"]
        self.box.save_catalog(doc)
        self.assert_refused("effort_ladder", "duplicate")


class TomlUnknownModelTests(SandboxCase):
    """A dev-path table naming a model the catalog does not know is the
    user's own: it is reported and left alone, and the rest still syncs."""

    def setUp(self):
        super().setUp()
        text = self.box.text("openhands_toml").replace(
            'model = "openai/t4-rag"', 'model = "openai/t9-gone"')
        # A table of the user's own, with token lines the sync would own if
        # it named a catalog model.
        text = text.replace("[llm.t4-rag]", "[llm.mine]")
        self.box.write("openhands_toml", text)
        section = text.split("[llm.mine]", 1)[1].split("\n[", 1)[0]
        self.mine = section

    def test_check_warns_and_stays_clean(self):
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARNING", result.stderr)
        self.assertIn("t9-gone", result.stderr)
        self.assertIn("[llm.mine]", result.stderr)

    def test_write_leaves_that_table_untouched_and_syncs_the_rest(self):
        doc = self.box.catalog()
        model(doc, "t3-driver")["context"] = 65536
        self.box.save_catalog(doc)
        result = self.box.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        toml = self.box.text("openhands_toml")
        self.assertEqual(toml.split("[llm.mine]", 1)[1].split("\n[", 1)[0], self.mine)
        t3 = toml.split("[llm.t3-driver]", 1)[1].split("\n[", 1)[0]
        self.assertIn("max_input_tokens = 65536", t3)


class RegistrySandbox:
    """Temp copies of catalog/ai-registry.json plus the three generated files,
    and a runner pointed at them via --registry (no --catalog): task A4c's
    retarget of tools/sync-ide-models.py's default (unflagged) data source from
    catalog/ide-models.json to a fresh render of the registry
    (tools/registry.py's render_ide(), routing v2 spec 3.2 phase 1)."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.paths = {"registry": self.dir / REGISTRY_PATH.name}
        shutil.copyfile(REGISTRY_PATH, self.paths["registry"])
        for key in ("opencode", "tier_profiles", "openhands_toml"):
            src = SOURCES[key]
            dst = self.dir / src.name
            shutil.copyfile(src, dst)
            self.paths[key] = dst

    def close(self):
        self._tmp.cleanup()

    def run(self, *extra):
        args = [
            sys.executable, str(TOOL),
            "--registry", str(self.paths["registry"]),
            "--opencode", str(self.paths["opencode"]),
            "--tier-profiles", str(self.paths["tier_profiles"]),
            "--openhands-toml", str(self.paths["openhands_toml"]),
        ]
        return subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8")

    def text(self, key):
        return self.paths[key].read_text(encoding="utf-8")

    def registry(self):
        return json.loads(self.text("registry"))

    def save_registry(self, doc):
        self.paths["registry"].write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class RegistrySourcedTests(unittest.TestCase):
    """tools/sync-ide-models.py's unflagged default now sources its model list
    from tools/registry.py's render_ide(catalog/ai-registry.json), not from
    catalog/ide-models.json directly (task A4c). --catalog stays as an explicit
    override onto the old file's shape - proven unchanged by every test above,
    which always passes --catalog (see FLAGS/Sandbox.run())."""

    def setUp(self):
        self.box = RegistrySandbox()

    def tearDown(self):
        self.box.close()

    def test_check_is_clean_on_a_fresh_registry_copy(self):
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_registry_context_change_is_drift_in_opencode(self):
        doc = self.box.registry()
        route_surface(doc, "t3-driver")["context"] = 65536
        self.box.save_registry(doc)
        result = self.box.run("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("65536", result.stdout)
        self.assertIn("DRIFT", result.stderr)

    def test_write_fixes_every_surface_from_the_registry(self):
        doc = self.box.registry()
        # t3-driver lists both gateways - render_ide() takes context/output
        # from omniroute (IDE_GATEWAYS priority), one shared value for both.
        route_surface(doc, "t3-driver", "omniroute")["context"] = 65536
        # t3-driver-paid is litellm-only, so this is the value render_ide() uses.
        route_surface(doc, "t3-driver-paid", "litellm")["output"] = 40000
        self.box.save_registry(doc)

        result = self.box.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        self.assertEqual(oc["providers"]["omniroute"]["models"]["t3-driver"]["limit"]["context"], 65536)
        self.assertEqual(oc["providers"]["litellm"]["models"]["t3-driver"]["limit"]["context"], 65536)
        self.assertEqual(oc["providers"]["litellm"]["models"]["t3-driver-paid"]["limit"]["output"], 40000)

        spec = json.loads(self.box.text("tier_profiles"))
        by_id = {t["id"]: t for t in spec["tiers"]}
        self.assertEqual(by_id["omniroute-t3-driver"]["max_input_tokens"], 65536)

        toml = self.box.text("openhands_toml")
        section = toml.split("[llm.t3-driver]", 1)[1].split("\n[", 1)[0]
        self.assertIn("max_input_tokens = 65536", section)

        second = self.box.run()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("Already in sync", second.stdout)

    def test_an_explicit_catalog_flag_still_wins_over_registry(self):
        # --catalog is the explicit escape hatch onto the old file shape (used
        # by every test in this module above); passing it alongside --registry
        # must not have the new default source silently shadow it.
        catalog_copy = self.box.dir / "ide-models-explicit.json"
        shutil.copyfile(SOURCES["catalog"], catalog_copy)
        cat_doc = json.loads(catalog_copy.read_text(encoding="utf-8"))
        model(cat_doc, "t3-driver")["context"] = 77777
        catalog_copy.write_text(json.dumps(cat_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        result = self.box.run("--catalog", str(catalog_copy))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        self.assertEqual(oc["providers"]["omniroute"]["models"]["t3-driver"]["limit"]["context"], 77777)

    def test_unresolvable_head_leg_raises_error(self):
        # A6a review: a route whose first leg resolve_leg rejects raises
        # ValueError (load_from_registry wraps it as ConfigError).
        doc = self.box.registry()
        doc["routes"]["t2-worker"]["legs"][0] = "nonesuch/bogus"
        self.box.save_registry(doc)
        result = self.box.run()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("t2-worker", result.stderr)

    def test_non_string_rung_in_registry_effort_ladder_raises_error(self):
        # A6a review: a non-string rung in the head model's effort_ladder
        # raises ValueError (load_from_registry wraps it as ConfigError). Which
        # route the tool reports first is registry order, and that moved when
        # MUSEAPI/PROVFIX3 changed the heads — so what is pinned here is that the
        # error names a route, the model and the bad rung. FREEWIRE 2026-09-30:
        # the first route serving the gemini-3.8-flash model is now the
        # gemini-3.8-flash combo itself, whose id contains a dot, so the route
        # charset includes '.', not only [a-z0-9-].
        doc = self.box.registry()
        doc["models"]["gemini-3.8-flash"]["effort_ladder"] = ["low", 99, "high"]
        self.box.save_registry(doc)
        result = self.box.run()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertRegex(result.stderr,
                         r"routes\.[\w.-]+: non-string rung 99 "
                         r"in model gemini-3\.8-flash effort_ladder")


if __name__ == "__main__":
    unittest.main(verbosity=1)
