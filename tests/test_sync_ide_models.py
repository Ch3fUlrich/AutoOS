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


def strip_jsonc(text):
    # The suites' and tools/audit-router.py's strip: whole-line // only.
    return re.sub(r"(?m)^\s*//.*$", "", text)


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

    def test_the_1m_tier_is_1000000_everywhere(self):
        doc = json.loads(SOURCES["catalog"].read_text(encoding="utf-8"))
        for m in doc["models"]:
            if m["id"].startswith("t1-") or m["id"] == "spark-1.3-contributor":
                self.assertEqual(m["context"], 1000000, m["id"])


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
        doc = self.box.catalog()
        model(doc, "t1-orchestrator")["output"] = 40000
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
        t1 = oc["providers"]["omniroute"]["models"]["t1-orchestrator"]
        self.assertEqual(t1["limit"], {"context": 1000000, "output": 40000})
        self.assertEqual(oc["providers"]["litellm"]["models"]["t3-driver"]["limit"]["context"], 65536)
        spec = json.loads(self.box.text("tier_profiles"))
        by_id = {t["id"]: t for t in spec["tiers"]}
        self.assertEqual(by_id["omniroute-t1-orchestrator"]["max_output_tokens"], 40000)
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
            got = list(oc["providers"][gateway]["models"])
            self.assertEqual(got, want, gateway)
            for mid in want:
                entry = oc["providers"][gateway]["models"][mid]
                self.assertEqual(entry["modelID"], mid)
                self.assertEqual(entry["name"], model(doc, mid)["name"])

    def test_no_variants_or_effort_block_is_ever_generated(self):
        # A static variants block makes the whole provider unresolvable in
        # opencode (measured 2026-09-22); effort stays a Zed-only field.
        self.assertEqual(self.box.run().returncode, 0)
        oc = json.loads(strip_jsonc(self.box.text("opencode")))
        for gateway in ("omniroute", "litellm"):
            for entry in oc["providers"][gateway]["models"].values():
                self.assertEqual(set(entry), {"modelID", "name", "limit"})


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
        self.assertIn("t1-orchestrator", models)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
