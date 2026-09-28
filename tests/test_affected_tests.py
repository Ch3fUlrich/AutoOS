#!/usr/bin/env python3
"""Tests for tools/affected-tests.py.

The tool answers one question: given registry ids that changed (route, provider
or model ids), which shell / Pester / pytest tests name them, so the worker runs
a derived --filter instead of a hand-picked one (lessons PROVPIN, MUSEPIN: two
red CI runs came from a filter list that missed the tests naming a flipped id).

The properties these tests pin down are the ones that make the tool trustworthy:

  * every test whose block mentions an id is *reachable* by the emitted
    ``--filter`` string -- that is, some emitted term is a substring of its
    name, which is exactly how run-tests.sh and run-tests.ps1 select cases;
  * a term survives both runners' splitting: no commas, no spaces, so
    ``--filter $(python3 tools/affected-tests.py ... --format filter)`` cannot
    silently drop half the list;
  * ``--format filter`` prints the filter and nothing else, and exits 0.

Run directly:

    python3 tests/test_affected_tests.py
"""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "affected-tests.py"

_spec = importlib.util.spec_from_file_location("affected_tests", str(SCRIPT))
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)


FIXTURE_SH = '''\
# shellcheck shell=bash
# a fake suite part for the affected-tests fixture

describe "fake routing"

if it "route t1-orchestrator plans three legs"; then
    assert_eq "leg" "t1-orchestrator"
fi

if it "the registry reads muse-spark from ai-registry.json models"; then
    assert_contains "$out" "muse-spark"
fi

if it "a case that names the opus route only in its body"; then
    # the name says nothing, the body carries the id:
    grep -q 'opus-4-6' lib/linux/install.sh
fi

if it "an unrelated usb case"; then
    assert_eq "ventoy" "ventoy"
fi

if it "the auto route is a strategy, not a combo"; then
    assert_eq "auto" "auto"
fi

if it "the automatic strategy is not a combo"; then
    # only shares a prefix with the short route id above, so it is not affected
    assert_eq "automatic" "automatic"
fi
'''

FIXTURE_PS1 = '''\
# a fake windows suite for the affected-tests fixture

Describe-Group 'fake routing'

Test-Case 'the samba provider flips its route' {
    Assert-Equal $route.legs[0].provider 'SambaNova'
    Pass
}

Test-Case 'a case naming a model that no one else has MuseSpark13' {
    Assert-Equal $m 'muse-spark'
    Pass
}

Test-Case 'an unrelated detect case' {
    Pass
}
'''

FIXTURE_PY = '''\
"""a fake pytest file for the affected-tests fixture"""
import unittest


class RegistryReads(unittest.TestCase):
    def test_model_muse_spark_is_in_the_registry(self):
        self.assertIn("muse-spark", MODELS)

    def test_route_t1_orchestrator_is_clean(self):
        self.assertIn("t1-orchestrator", ROUTES)

    def test_nothing_relevant_here(self):
        self.assertTrue(True)


def test_module_level_opus_route():
    assert "opus-4-6" in ROUTES
'''

MODELS = {"muse-spark": {"id": "muse-spark", "context_advertised": 1048576},
          "opus-4-6": {"id": "opus-4-6", "context_advertised": 200000}}
ROUTES = {"t1-orchestrator": {"id": "t1-orchestrator", "class": "top", "legs": []},
          "auto": {"id": "auto", "class": "mid", "legs": []},
          "opus-4-6": {"id": "opus-4-6", "class": "top", "legs": []}}
PROVIDERS = {"samba": {"id": "samba", "trains_on_prompts": True},
             "meta": {"id": "meta", "trains_on_prompts": True}}


def registry_doc():
    """A fresh copy every time: a test that mutates an entry must not move the baseline."""
    return {"version": "test", "models": copy.deepcopy(MODELS),
            "routes": copy.deepcopy(ROUTES), "providers": copy.deepcopy(PROVIDERS),
            "clients": {}}


def write_registry(root, doc):
    (root / "catalog").mkdir(parents=True, exist_ok=True)
    (root / "catalog" / "ai-registry.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def make_fixture(root):
    """A minimal repo-shaped tree the tool can scan."""
    (root / "tests" / "linux").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "linux" / "10-fake.sh").write_text(FIXTURE_SH, encoding="utf-8")
    (root / "tests" / "run-tests.ps1").write_text(FIXTURE_PS1, encoding="utf-8")
    (root / "tests" / "test_fake_registry.py").write_text(FIXTURE_PY, encoding="utf-8")
    write_registry(root, registry_doc())


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, check=True)


def run_tool(args):
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def filter_terms(stdout):
    """The terms run-tests.sh would actually see: it splits --filter on commas."""
    text = stdout.strip()
    return [t for t in text.split(",") if t] if text else []


def affected_in(runner, root, ids):
    """The names of one runner whose block mentions one of ids."""
    return [t.name for t in at.affected(at.discover(root), ids) if t.runner == runner]


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_fixture(self.root)

    def tool(self, *args):
        return run_tool(list(args) + ["--root", str(self.root)])

    def test_an_id_in_a_test_name_is_a_hit(self):
        result = self.tool("t1-orchestrator", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("t1-orchestrator", result.stdout)

    def test_a_body_only_mention_is_still_reachable_by_the_filter(self):
        result = self.tool("opus-4-6", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        must_reach = affected_in("sh", self.root, ["opus-4-6"])
        self.assertIn("a case that names the opus route only in its body", must_reach)
        terms = filter_terms(result.stdout)
        for name in must_reach:
            self.assertTrue(any(t in name for t in terms),
                            "filter %r cannot select the affected test %r" % (terms, name))

    def test_terms_survive_both_runners_splitting(self):
        result = self.tool("muse-spark", "samba", "opus-4-6", "t1-orchestrator",
                           "--format", "filter")
        for term in filter_terms(result.stdout):
            self.assertNotIn(",", term, "a comma would split this term in --filter")
            self.assertNotIn(" ", term, "a space would split this term in $( )")

    def test_the_filter_reaches_every_affected_shell_and_pester_case(self):
        ids = ["muse-spark", "opus-4-6", "t1-orchestrator", "SambaNova"]
        result = self.tool(*ids, "--format", "filter")
        terms = filter_terms(result.stdout)
        for runner in ("sh", "ps1"):
            must_reach = affected_in(runner, self.root, ids)
            self.assertTrue(must_reach, "fixture lost its %s hits" % runner)
            for name in must_reach:
                self.assertTrue(any(t.lower() in name.lower() for t in terms),
                                "%s filter %r cannot select %r" % (runner, terms, name))

    def test_an_unrelated_id_selects_nothing_and_still_exits_zero(self):
        result = self.tool("no-such-provider-at-all", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_word_boundaries_keep_a_prefix_from_matching_a_longer_word(self):
        # The route id "auto" must not drag in the case about "automatic".
        affected = affected_in("sh", self.root, ["auto"])
        self.assertIn("the auto route is a strategy, not a combo", affected)
        self.assertNotIn("the automatic strategy is not a combo", affected)
        result = self.tool("auto", "--format", "filter")
        terms = filter_terms(result.stdout)
        self.assertTrue(any(t in "the auto route is a strategy, not a combo"
                            for t in terms), result.stdout)

    def test_a_pester_case_is_hit_by_the_registry_spelling_of_a_provider(self):
        # The registry spells the provider "SambaNova"; the suite says "samba"
        # in its name and the registry spelling only in its body.
        result = self.tool("SambaNova", "--format", "filter")
        terms = filter_terms(result.stdout)
        self.assertIn("the samba provider flips its route", affected_in("ps1", self.root,
                                                                        ["SambaNova"]))
        self.assertTrue(any(t in "the samba provider flips its route" for t in terms),
                        "term %r cannot select the Pester case" % terms)

    def test_pytest_format_emits_node_ids(self):
        result = self.tool("muse-spark", "--format", "pytest")
        self.assertEqual(result.returncode, 0, result.stderr)
        nodes = result.stdout.split()
        self.assertIn("tests/test_fake_registry.py::RegistryReads::"
                      "test_model_muse_spark_is_in_the_registry", nodes)
        self.assertNotIn("tests/test_fake_registry.py::RegistryReads::"
                         "test_nothing_relevant_here", nodes)

    def test_pytest_format_reaches_module_level_and_method_hits(self):
        result = self.tool("opus-4-6", "t1-orchestrator", "--format", "pytest")
        nodes = result.stdout.split()
        self.assertIn("tests/test_fake_registry.py::RegistryReads::"
                      "test_route_t1_orchestrator_is_clean", nodes)
        self.assertIn("tests/test_fake_registry.py::test_module_level_opus_route", nodes)

    def test_human_format_names_the_runner_and_the_reason(self):
        result = self.tool("t1-orchestrator", "--format", "human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sh", result.stdout)
        self.assertIn("pytest", result.stdout)
        self.assertIn("t1-orchestrator", result.stdout)
        self.assertIn("route t1-orchestrator plans three legs", result.stdout)

    def test_no_ids_and_no_diff_is_a_usage_error(self):
        result = self.tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("id", result.stderr.lower())


class DiffTests(unittest.TestCase):
    """--from-diff must read the ids whose registry entry actually changed."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_fixture(self.root)
        git(self.root, "init", "-q", "--initial-branch=main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")

    def change(self, mutate):
        doc = registry_doc()
        mutate(doc)
        write_registry(self.root, doc)

    def tool(self, *args):
        return run_tool(list(args) + ["--root", str(self.root)])

    def test_a_changed_value_reports_that_key(self):
        self.change(lambda d: d["routes"]["t1-orchestrator"].__setitem__("class", "mid"))
        result = self.tool("--from-diff", "HEAD", "--format", "human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("t1-orchestrator", result.stdout)
        # The untouched sibling and its tests stay out of the run.
        self.assertNotIn("opus-4-6", result.stdout)

    def test_an_added_and_a_removed_key_are_both_reported(self):
        self.change(lambda d: (d["models"].pop("muse-spark"),
                               d["providers"].__setitem__(
                                   "newproxy", {"id": "newproxy"})))
        result = self.tool("--from-diff", "HEAD", "--format", "human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("muse-spark", result.stdout)
        self.assertIn("newproxy", result.stdout)

    def test_an_unchanged_registry_reports_no_ids_and_selects_nothing(self):
        result = self.tool("--from-diff", "HEAD", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_sections_outside_routes_providers_models_are_not_ids(self):
        # `version` and `clients` change all the time; naming them would drag a
        # word like "qoder" into the filter as if it were a registry id.
        self.change(lambda d: (d.update(version="later"),
                               d["clients"].__setitem__("zzclient", {"id": "zzclient"})))
        ids = at.changed_registry_ids(self.root, "HEAD",
                                      self.root / "catalog" / "ai-registry.json")
        self.assertEqual(ids, [])

    def test_a_changed_route_selects_the_case_that_names_it(self):
        self.change(lambda d: d["routes"]["t1-orchestrator"].__setitem__("class", "mid"))
        result = self.tool("--from-diff", "HEAD", "--format", "filter")
        terms = filter_terms(result.stdout)
        self.assertTrue(any(t in "route t1-orchestrator plans three legs" for t in terms),
                        result.stdout)


class RealRepoTests(unittest.TestCase):
    """The tool must work on this repository, not only on the fixture."""

    def test_a_known_route_id_finds_the_real_shell_and_pester_cases(self):
        result = run_tool(["t1-orchestrator", "--format", "filter"])
        self.assertEqual(result.returncode, 0, result.stderr)
        terms = filter_terms(result.stdout)
        self.assertTrue(terms, "no filter emitted for a route the suites name")
        tests = at.discover(ROOT)
        corpus = {runner: [t.name for t in tests if t.runner == runner]
                  for runner in ("sh", "ps1")}
        for term in terms:
            self.assertTrue(any(term.lower() in n.lower() for names in corpus.values()
                                for n in names),
                            "inert term %r matches no test name" % term)
        reached = [n for n in corpus["sh"] if any(t in n for t in terms)]
        self.assertTrue(reached, "the filter selects no real shell case by name")

    def test_the_filter_covers_every_affected_real_case(self):
        ids = ["t1-orchestrator", "muse-spark"]
        result = run_tool(ids + ["--format", "filter"])
        terms = filter_terms(result.stdout)
        tests = at.discover(ROOT)
        for t in at.affected(tests, ids):
            if t.runner in ("sh", "ps1"):
                self.assertTrue(any(x.lower() in t.name.lower() for x in terms),
                                "real %s case %r is not reachable by %r"
                                % (t.runner, t.name, terms))

    def test_pytest_nodes_found_in_the_real_repo_are_valid_files(self):
        result = run_tool(["registry", "--format", "pytest"])
        nodes = result.stdout.split()
        for node in nodes:
            path = node.split("::")[0]
            self.assertTrue((ROOT / path).is_file(), "node id points at no file: %s" % node)


if __name__ == "__main__":
    unittest.main(verbosity=2)
