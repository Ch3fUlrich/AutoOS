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
import re
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

if it "route l1-orchestrator plans three legs"; then
    assert_eq "leg" "l1-orchestrator"
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

# A heredoc body is data: the `if it` below is text written into a scratch file,
# not a case, and it must not end the case that contains it (review F1).
if it "the heredoc case that greps only after the closing delimiter"; then
    cat >"$tmp/fake.sh" <<'EOS'
if it "phantom opus-4-6 is not a real case"; then
    assert_eq "x" "x"
EOS
    grep -q 'opus-4-6' "$tmp/fake.sh"
fi

# The repo's dominant idiom (review AFFFIX2): a heredoc opened *inside* a
# double-quoted command substitution. bash parses a substitution's contents as
# code, so `<<'PY'` opens a heredoc even though the outer `"` is still open —
# and the body is data, exactly like a plain heredoc's.
if it "the substitution heredoc case whose mention follows the body"; then
    out="$(python3 - 2>&1 <<'PY'
if it "phantom inside the substitution heredoc"; then
    print("opus-4-6")
PY
)"
    assert_contains "$out" "opus-4-6"
fi

# A longer id that merely starts with the queried one is a different entry.
if it "the l1-orchestrator-clean route drops the dead leg"; then
    assert_eq "class" "mid"
fi

if it "a case naming only the lite flash model"; then
    assert_contains "$out" "gemini-3.8-flash-lite"
fi

# A name with no word characters at all: the fallback term is the whole name.
if it "✓ ✗ ✗"; then
    assert_contains "$out" "zenith-route"
fi

# ... unless the name carries a comma, which no --filter string can express.
if it "✓ ✗, ✗"; then
    assert_contains "$out" "zenith-route"
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

# An here-string body is data too: the Test-Case below is text inside a variable,
# not a case (review F1).
Test-Case 'the here-string case that asserts after the closing quote' {
    $sh = @'
Test-Case 'phantom SambaNova is not a real case' {
    Pass
}
'@
    Assert-Equal $sh 'SambaNova'
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
        self.assertIn("l1-orchestrator", ROUTES)

    def test_nothing_relevant_here(self):
        self.assertTrue(True)


def test_module_level_opus_route():
    assert "opus-4-6" in ROUTES
'''

# The names the fixture cases above carry, spelled once.
HEREDOC_SH_CASE = "the heredoc case that greps only after the closing delimiter"
PHANTOM_SH_CASE = "phantom opus-4-6 is not a real case"
SUB_HEREDOC_SH_CASE = "the substitution heredoc case whose mention follows the body"
SUB_PHANTOM_SH_CASE = "phantom inside the substitution heredoc"
HEREDOC_PS_CASE = "the here-string case that asserts after the closing quote"
PHANTOM_PS_CASE = "phantom SambaNova is not a real case"
LONGER_ID_CASE = "the l1-orchestrator-clean route drops the dead leg"
LITE_ONLY_CASE = "a case naming only the lite flash model"
ZERO_TOKEN_CASE = "✓ ✗ ✗"
COMMA_CASE = "✓ ✗, ✗"
UNBAL_PHANTOM_CASE = "phantom opus-4-6 the fallback lets through"
UNBAL_REAL_CASE = "the real case that names muse-spark after it"

SH_TAB_TEXT = '''\
if it "the tab-strip case"; then
    cat <<-EOS
\tif it "phantom inside the tab heredoc"; then
\tdescribe "phantom group"
\tEOS
    assert_eq "x" "muse-spark"
fi

if it "the plain case after it"; then
    assert_eq "y" "y"
fi
'''

SH_QUOTED_TEXT = '''\
if it "the quoted-delimiter case"; then
    cat >"$f" <<"X"
if it "phantom inside the quoted heredoc"; then
describe "phantom group"
X
    assert_eq "y" "muse-spark"
fi

if it "the plain case after it"; then
    assert_eq "z" "z"
fi
'''

SH_READ_TEXT = '''\
if it "the case that reads from a here-string"; then
    read -r a b <<<"$out"
    assert_eq "z" "muse-spark"
fi
'''

# The 64 openers this shape accounts for in tests/linux (review AFFFIX2): a
# heredoc whose `<<` sits inside a double-quoted `$( … )`.
SH_SUBSTITUTION_TEXT = '''\
if it "the case whose heredoc opens inside a substitution"; then
    report="$(python3 - "$cfg" 2>&1 <<'PY'
if it "phantom in the substitution body"; then
    describe "phantom group"
PY
)"
    assert_eq "z" "muse-spark"
fi

if it "the case after it"; then
    assert_eq "y" "y"
fi
'''

# The unquoted and nested shapes of the same idiom, which the line-spanning state
# must also survive: a `( … )` group inside a `$( … )` must not be read as closing
# the substitution.
SH_SUBSTITUTION_NESTED_TEXT = '''\
if it "the case with a paren group inside the substitution"; then
    out="$(if ( has_parens ); then python3 - <<'PY'
data line
PY
fi
)"
    assert_eq "z" "muse-spark"
fi

if it "the case after it"; then
    assert_eq "y" "y"
fi
'''

# A `<<` inside `(( … ))` or `$(( … ))` is bash's shift operator, and the `=` of
# `<<=` is in the heredoc-delimiter character class, so `((x<<=1))` used to open a
# heredoc named `=1` (and `$((1<<4))` one named `4`): every line after it read as
# that heredoc's body, the file's remaining cases vanished, and nothing was said
# (review AFFFIX3, the CRITICAL). The last shape is the space-separated shift, which
# only the arithmetic state - not a `<<=` guard - can recognise.
SH_ARITH_TEXT = '''\
if it "the case that shifts in arithmetic"; then
    ((x<<=1))
    width=$((1<<4))
    mask=$((VAR<<COUNT))
    y=2
    ((y <<= 1))
    assert_eq "z" "muse-spark"
fi

if it "the case after it"; then
    assert_eq "y" "y"
fi
'''

# The fail-safe's own shape: a heredoc whose delimiter never arrives, so the scan
# reaches EOF still looking for it. Discarding the masking for this one file makes
# the phantom a case too - over-inclusion, which is what this tool is allowed to be.
SH_UNBALANCED_TEXT = '''\
if it "the case whose heredoc never closes"; then
    cat <<EOS
    assert_eq "x" "x"

if it "phantom opus-4-6 the fallback lets through"; then
    assert_eq "w" "w"
fi

if it "the real case that names muse-spark after it"; then
    assert_eq "z" "muse-spark"
fi
'''

PS_UNBALANCED_TEXT = '''\
Test-Case 'the case whose here-string never closes' {
    $t = @'
    Assert-Equal $t 'x'
}

Test-Case 'the real case that names muse-spark after it' {
    Assert-Equal $t 'muse-spark'
    Pass
}
'''

# The known boundary, deliberately unfixed (review AFFFIX3, item 3): inside a
# `$( … )` substitution a `case` pattern's `)` is at depth 1, so it pops the
# substitution frame early and this parser and bash part company from there. See
# the pinning test below for what that costs today.
SH_CASE_IN_SUB_TEXT = '''\
if it "the case whose substitution holds a case pattern"; then
    out="$(
    case "$1" in
        *) echo "pattern" ;;
    esac
    python3 - <<'PY'
if it "phantom the early close exposes"; then
    print("muse-spark")
PY
)"
    assert_contains "$out" "opus-4-6"
fi

if it "the case after it"; then
    assert_eq "y" "y"
fi
'''

PS_TEXT = '''\
Test-Case 'the interpolated here-string case' {
    $t = @"
Test-Case 'phantom inside' {
    Pass
}
Describe-Group 'phantom group'
"@
    Assert-Equal $t 'muse-spark'
    Pass
}

Test-Case 'the literal here-string case' {
    $t = @'
"@ cannot close an @' here-string
Test-Case 'phantom inside' {
'@
    Assert-Equal $t 'opus-4-6'
    Pass
}
'''

PS_FALSE_OPENER_TEXT = '''\
Test-Case 'the case that writes a trailing at sign' {
    Write-Host 'ends with an at @'
    Pass
}

Test-Case 'the case that must still be found' {
    Assert-Equal $t 'muse-spark'
    Pass
}
'''

MODELS = {"muse-spark": {"id": "muse-spark", "context_advertised": 1048576},
          "opus-4-6": {"id": "opus-4-6", "context_advertised": 200000}}
ROUTES = {"l1-orchestrator": {"id": "l1-orchestrator", "class": "top", "legs": []},
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
        result = self.tool("l1-orchestrator", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("l1-orchestrator", result.stdout)

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
        result = self.tool("muse-spark", "samba", "opus-4-6", "l1-orchestrator",
                           "--format", "filter")
        for term in filter_terms(result.stdout):
            self.assertNotIn(",", term, "a comma would split this term in --filter")
            self.assertNotIn(" ", term, "a space would split this term in $( )")

    def test_the_filter_reaches_every_affected_shell_and_pester_case(self):
        ids = ["muse-spark", "opus-4-6", "l1-orchestrator", "SambaNova"]
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

    def test_a_heredoc_body_never_ends_the_shell_case_that_contains_it(self):
        # Review F1: the `if it "phantom" ...` line is text written into a scratch
        # file. Before the fix it ended the case above it, so the mention after the
        # closing delimiter was credited to the phantom instead.
        hit = affected_in("sh", self.root, ["opus-4-6"])
        self.assertIn(HEREDOC_SH_CASE, hit)
        self.assertNotIn(PHANTOM_SH_CASE, hit)
        terms = filter_terms(self.tool("opus-4-6", "--format", "filter").stdout)
        self.assertTrue(any(t in HEREDOC_SH_CASE for t in terms),
                        "filter %r cannot select %r" % (terms, HEREDOC_SH_CASE))

    def test_a_heredoc_opened_inside_a_quoted_substitution_is_data_too(self):
        # Review AFFFIX2: `out="$(python3 - <<'PY'` never registered, because the
        # opening `"` blanked the rest of the line including `<<'PY'`. So the
        # phantom `if it` inside the body became a real case and the mention after
        # `PY\n)"` was credited to it, hiding the case that actually greps.
        hit = affected_in("sh", self.root, ["opus-4-6"])
        self.assertIn(SUB_HEREDOC_SH_CASE, hit)
        self.assertNotIn(SUB_PHANTOM_SH_CASE, hit)
        terms = filter_terms(self.tool("opus-4-6", "--format", "filter").stdout)
        self.assertTrue(any(t in SUB_HEREDOC_SH_CASE for t in terms),
                        "filter %r cannot select %r" % (terms, SUB_HEREDOC_SH_CASE))

    def test_a_here_string_body_never_ends_the_pester_case_that_contains_it(self):
        hit = affected_in("ps1", self.root, ["SambaNova"])
        self.assertIn(HEREDOC_PS_CASE, hit)
        self.assertNotIn(PHANTOM_PS_CASE, hit)
        terms = filter_terms(self.tool("SambaNova", "--format", "filter").stdout)
        self.assertTrue(any(t.lower() in HEREDOC_PS_CASE.lower() for t in terms),
                        "filter %r cannot select %r" % (terms, HEREDOC_PS_CASE))

    def test_a_dashed_id_does_not_select_a_block_naming_a_longer_id(self):
        # Review F2 / Haiku: \b treats the `-` of l1-orchestrator-clean as a
        # boundary, so querying the short id also selected the -clean block.
        hit = affected_in("sh", self.root, ["l1-orchestrator"])
        self.assertIn("route l1-orchestrator plans three legs", hit)
        self.assertNotIn(LONGER_ID_CASE, hit)

    def test_an_id_does_not_select_a_block_naming_an_id_with_a_dashed_suffix(self):
        # Querying the short id finds nothing; querying the id the block actually
        # names finds it. That is the whole of the -clean complaint (review F2).
        self.assertEqual(affected_in("sh", self.root, ["gemini-3.8-flash"]), [])
        self.assertIn(LITE_ONLY_CASE,
                      affected_in("sh", self.root, ["gemini-3.8-flash-lite"]))

    def test_id_pattern_boundaries_reject_a_longer_id_and_accept_a_derived_name(self):
        short = at.id_pattern("l1-orchestrator")
        self.assertIsNone(short.search("l1-orchestrator-clean"))
        self.assertIsNone(short.search("l1-orchestrator-paid's"))
        self.assertIsNotNone(short.search("the l1-orchestrator route"))
        self.assertIsNotNone(short.search('"l1-orchestrator"'))
        # The other direction is a miss, not over-inclusion: `omniroute-…json` is
        # the profile this route generates, and a leading `-` or a trailing `.` is
        # not part of the id.
        self.assertIsNotNone(short.search("omniroute-t1-orchestrator.json"))
        flash = at.id_pattern("gemini-3.8-flash")
        self.assertIsNone(flash.search("gemini-3.8-flash-lite"))
        self.assertIsNotNone(flash.search("gemini-3.8-flash"))

    def test_a_name_with_no_word_characters_falls_back_to_the_whole_name(self):
        # Review F2 (Sonnet): a name TOKEN cannot split yields no term at all, so
        # the case was silently unreachable. The whole name is the only term left.
        result = self.tool("zenith-route", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        terms = filter_terms(result.stdout)
        self.assertIn(ZERO_TOKEN_CASE, terms)

    def test_a_comma_in_a_wordless_name_is_reported_on_stderr_and_still_exits_zero(self):
        result = self.tool("zenith-route", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        terms = filter_terms(result.stdout)
        self.assertFalse(any(t in COMMA_CASE for t in terms),
                         "a comma-bearing name cannot be a --filter term: %r" % terms)
        self.assertIn("unfilterable", result.stderr.lower(), result.stderr)
        self.assertIn(COMMA_CASE, result.stderr)

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
        result = self.tool("opus-4-6", "l1-orchestrator", "--format", "pytest")
        nodes = result.stdout.split()
        self.assertIn("tests/test_fake_registry.py::RegistryReads::"
                      "test_route_t1_orchestrator_is_clean", nodes)
        self.assertIn("tests/test_fake_registry.py::test_module_level_opus_route", nodes)

    def test_human_format_names_the_runner_and_the_reason(self):
        result = self.tool("l1-orchestrator", "--format", "human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sh", result.stdout)
        self.assertIn("pytest", result.stdout)
        self.assertIn("l1-orchestrator", result.stdout)
        self.assertIn("route l1-orchestrator plans three legs", result.stdout)

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
        self.change(lambda d: d["routes"]["l1-orchestrator"].__setitem__("class", "mid"))
        result = self.tool("--from-diff", "HEAD", "--format", "human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("l1-orchestrator", result.stdout)
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
        self.change(lambda d: d["routes"]["l1-orchestrator"].__setitem__("class", "mid"))
        result = self.tool("--from-diff", "HEAD", "--format", "filter")
        terms = filter_terms(result.stdout)
        self.assertTrue(any(t in "route l1-orchestrator plans three legs" for t in terms),
                        result.stdout)


class RealRepoTests(unittest.TestCase):
    """The tool must work on this repository, not only on the fixture."""

    # A heredoc opened inside a command substitution: the shape the suites use
    # dozens of times (`out="$(python3 - <<'PY'`). The gap excludes `<` and newline,
    # so what it lands on is the line's first `<<` and that is an opener.
    SUB_HEREDOC = re.compile(r"""\$\([^<\n]*<<-?['"]?[A-Za-z_]""")

    def test_every_heredoc_the_real_suites_open_in_a_substitution_registers(self):
        # Review AFFFIX2: this idiom is the dominant one in tests/linux, and the
        # per-line quote reset hid every one of its bodies, so a header-shaped line
        # in a Python body cut the case above it. Measured rather than asserted per
        # file, so the count says how much of the suite the shape covers.
        openers, missed, unbalanced = 0, [], []
        for path in sorted((ROOT / "tests" / "linux").glob("*.sh")):
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.split("\n")
            masked, balanced = at.masked_lines(text, "sh")
            if not balanced:
                unbalanced.append(path.name)
            for index, line in enumerate(lines):
                if self.SUB_HEREDOC.search(line):
                    openers += 1
                    if index + 1 not in masked:
                        missed.append("%s:%d %s" % (path.name, index + 1, line.strip()))
        self.assertEqual(unbalanced, [],
                         "these suites end mid-construct, so their masking is discarded "
                         "and every phantom header in a heredoc body becomes a case")
        self.assertGreaterEqual(openers, 60,
                                "the suite no longer uses the idiom this test measures")
        self.assertEqual(missed, [],
                         "%d of %d substitution heredocs are not read as data"
                         % (len(missed), openers))

    def test_a_known_route_id_finds_the_real_shell_and_pester_cases(self):
        result = run_tool(["l1-orchestrator", "--format", "filter"])
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
        ids = ["l1-orchestrator", "muse-spark"]
        result = run_tool(ids + ["--format", "filter"])
        terms = filter_terms(result.stdout)
        tests = at.discover(ROOT)
        for t in at.affected(tests, ids):
            if t.runner in ("sh", "ps1"):
                self.assertTrue(any(x.lower() in t.name.lower() for x in terms),
                                "real %s case %r is not reachable by %r"
                                % (t.runner, t.name, terms))

    def test_a_boundary_tightening_keeps_the_case_naming_a_profile_the_route_makes(self):
        # tests/linux/34-ai-services.sh names `omniroute-t1-orchestrator`: the
        # profile file this route generates. A boundary that also rejected a
        # leading `-` would hide these cases from a flip of the very route they
        # cover - a miss, which the tool's own contract does not allow.
        hit = affected_in("sh", ROOT, ["l1-orchestrator"])
        self.assertIn("svc: profile sync pushes the tiers into a running app, "
                      "idempotently and capped", hit)
        self.assertIn("svc: profile push re-sends a profile whose key was rotated", hit)

    def test_a_boundary_tightening_drops_only_the_case_naming_a_longer_id(self):
        # This one mentions `l1-orchestrator-clean`, a different route, in a comment
        # and nothing else: reviewing it belongs to a change of that id (review F2).
        hit = affected_in("sh", ROOT, ["l1-orchestrator"])
        self.assertNotIn("apply sets the resilience deadline and the fast-skip breaker", hit)

    def test_pytest_nodes_found_in_the_real_repo_are_valid_files(self):
        result = run_tool(["registry", "--format", "pytest"])
        nodes = result.stdout.split()
        for node in nodes:
            path = node.split("::")[0]
            self.assertTrue((ROOT / path).is_file(), "node id points at no file: %s" % node)


class RegionSyntaxTests(unittest.TestCase):
    """regions() must read a heredoc / here-string body as data (review F1).

    The suites write whole fake scripts into scratch files with `cat <<EOS`, and
    PowerShell carries them in `@' … '@` here-strings. Those lines are text, so a
    header-shaped line in one (`if it "…"`, `Test-Case '…'`) or a `describe` must
    neither become a case of its own nor cut the body of the case that contains it.
    """

    def blocks(self, text, runner):
        header, group = ((at.SH_HEADER, at.SH_GROUP) if runner == "sh"
                         else (at.PS_HEADER, at.PS_GROUP))
        return {name: body for name, line, body in at.regions(text, header, group, runner)}

    def test_a_tab_strip_heredoc_body_neither_opens_a_case_nor_closes_the_real_one(self):
        blocks = self.blocks(SH_TAB_TEXT, "sh")
        self.assertEqual(list(blocks), ["the tab-strip case", "the plain case after it"])
        self.assertIn("muse-spark", blocks["the tab-strip case"])

    def test_a_quoted_heredoc_delimiter_is_matched_as_written(self):
        blocks = self.blocks(SH_QUOTED_TEXT, "sh")
        self.assertEqual(list(blocks), ["the quoted-delimiter case", "the plain case after it"])
        self.assertIn("muse-spark", blocks["the quoted-delimiter case"])

    def test_a_here_string_operator_does_not_open_a_heredoc(self):
        blocks = self.blocks(SH_READ_TEXT, "sh")
        self.assertEqual(list(blocks), ["the case that reads from a here-string"])
        self.assertIn("muse-spark", blocks["the case that reads from a here-string"])

    def test_a_heredoc_inside_a_quoted_command_substitution_bodies_the_outer_case(self):
        # bash parses a `$( … )` substitution's contents as code even when an outer
        # `"` is open, so the `<<'PY'` on that line opens a real heredoc and its
        # body is data - not code, despite the still-unbalanced `"`.
        blocks = self.blocks(SH_SUBSTITUTION_TEXT, "sh")
        outer = "the case whose heredoc opens inside a substitution"
        self.assertEqual(list(blocks), [outer, "the case after it"])
        self.assertIn("muse-spark", blocks[outer])

    def test_a_paren_group_inside_a_substitution_does_not_close_the_substitution(self):
        blocks = self.blocks(SH_SUBSTITUTION_NESTED_TEXT, "sh")
        outer = "the case with a paren group inside the substitution"
        self.assertEqual(list(blocks), [outer, "the case after it"])
        self.assertIn("muse-spark", blocks[outer])

    def test_an_interpolating_here_string_hides_its_fake_case(self):
        blocks = self.blocks(PS_TEXT, "ps1")
        self.assertEqual(list(blocks), ["the interpolated here-string case",
                                        "the literal here-string case"])
        self.assertIn("muse-spark", blocks["the interpolated here-string case"])
        self.assertIn("opus-4-6", blocks["the literal here-string case"])

    def test_a_string_that_only_ends_on_an_at_sign_is_not_a_here_string(self):
        # `'ends with an at @'` closes a single-quoted string: the apostrophe ends
        # it, it does not open a here-string.
        blocks = self.blocks(PS_FALSE_OPENER_TEXT, "ps1")
        self.assertEqual(list(blocks), ["the case that writes a trailing at sign",
                                         "the case that must still be found"])
        self.assertIn("muse-spark", blocks["the case that must still be found"])

    def test_arithmetic_shift_operators_do_not_open_a_heredoc(self):
        # Review AFFFIX3 (the CRITICAL): `<<` is a shift inside `(( … ))` and
        # `$(( … ))`, and `=` is in the delimiter charset, so `((x<<=1))` opened a
        # heredoc named `=1` and masked the rest of the file - every case after it
        # disappeared, silently.
        masked, balanced = at.masked_lines(SH_ARITH_TEXT, "sh")
        self.assertEqual(masked, set())
        self.assertTrue(balanced)
        blocks = self.blocks(SH_ARITH_TEXT, "sh")
        self.assertEqual(list(blocks), ["the case that shifts in arithmetic",
                                        "the case after it"])
        self.assertIn("muse-spark", blocks["the case that shifts in arithmetic"])

    def test_a_left_shift_assignment_is_never_a_heredoc_opener(self):
        # The `=` guard on its own, so the arithmetic state is not the only thing
        # standing between `<<=` and a whole file read as one heredoc body.
        self.assertIsNone(at.SHELL_OPENER.search("((x<<=1))"))
        self.assertIsNone(at.SHELL_OPENER.search("((y <<=1))"))
        self.assertIsNotNone(at.SHELL_OPENER.search("cat <<EOS"))
        self.assertIsNotNone(at.SHELL_OPENER.search("cat <<-EOS"))

    def test_an_unbalanced_shell_scan_drops_its_masking_and_says_so(self):
        masked, balanced = at.masked_lines(SH_UNBALANCED_TEXT, "sh")
        self.assertTrue(masked)
        self.assertFalse(balanced, "a heredoc left open at EOF must not read as balanced")
        blocks = self.blocks(SH_UNBALANCED_TEXT, "sh")
        self.assertEqual(list(blocks), ["the case whose heredoc never closes",
                                        UNBAL_PHANTOM_CASE, UNBAL_REAL_CASE])
        self.assertIn("muse-spark", blocks[UNBAL_REAL_CASE])

    def test_the_plain_fallback_never_lets_a_heredoc_group_heading_eat_a_case(self):
        # Sonnet AFFFIX3 round 4: a later, genuinely unterminated quote makes the
        # whole file fall back to plain scanning; a `describe` line inside an
        # EARLIER, well-formed heredoc must not become a group boundary then, or
        # the real case's tail (its id mention) belongs to no block at all.
        text = (
            'if it "the real case with a heredoc"; then\n'
            '    out="$(cat <<\'EOS\'\n'
            'describe "phantom group inside the heredoc"\n'
            'EOS\n'
            ')"\n'
            '    assert_contains "$out" "muse-spark"\n'
            'fi\n'
            'if it "a later case with an unterminated quote"; then\n'
            '    echo "never closed\n'
            'fi\n')
        self.assertFalse(at.masked_lines(text, "sh")[1])
        blocks = self.blocks(text, "sh")
        self.assertIn("muse-spark", blocks["the real case with a heredoc"])

    def test_an_unbalanced_here_string_scan_drops_its_masking_too(self):
        masked, balanced = at.masked_lines(PS_UNBALANCED_TEXT, "ps1")
        self.assertTrue(masked)
        self.assertFalse(balanced)
        blocks = self.blocks(PS_UNBALANCED_TEXT, "ps1")
        self.assertEqual(list(blocks), ["the case whose here-string never closes",
                                        UNBAL_REAL_CASE])

    def test_a_case_pattern_inside_a_substitution_closes_the_substitution_early(self):
        # PIN OF A KNOWN LIMIT, not an aspiration (review AFFFIX3 item 3): inside a
        # `$( … )` the `)` ending a `case` pattern is at paren depth 1, so it pops
        # the substitution frame and the `<<'PY'` two lines later is no longer read
        # as opening inside a substitution. Today the cost is a phantom case and the
        # outer case's own mention credited to it - over-inclusion in one direction
        # and a lost mention in the other. A fix would change bash grammar tracking,
        # which this round deliberately does not touch; it must flip this test on
        # purpose, with the phantom disappearing and `opus-4-6` landing back in the
        # case that greps for it.
        masked, balanced = at.masked_lines(SH_CASE_IN_SUB_TEXT, "sh")
        self.assertEqual(masked, set())
        self.assertTrue(balanced)
        blocks = self.blocks(SH_CASE_IN_SUB_TEXT, "sh")
        self.assertEqual(list(blocks), ["the case whose substitution holds a case pattern",
                                        "phantom the early close exposes",
                                        "the case after it"])
        self.assertNotIn("opus-4-6",
                         blocks["the case whose substitution holds a case pattern"])
        self.assertIn("opus-4-6", blocks["phantom the early close exposes"])


class FailSafeTests(unittest.TestCase):
    """An unfinished scan degrades one file, not the run (review AFFFIX3 item 1).

    Whole-file blindness is the failure this tool must never emit: a file whose scan
    ends with a delimiter, quote or substitution still open is re-read with plain
    header matching, and the one stderr line says so.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_fixture(self.root)
        (self.root / "tests" / "linux" / "20-unbalanced.sh").write_text(
            SH_UNBALANCED_TEXT, encoding="utf-8")

    def tool(self, *args):
        return run_tool(list(args) + ["--root", str(self.root)])

    def test_the_stray_heredoc_no_longer_blinds_the_file_that_follows(self):
        result = self.tool("muse-spark", "--format", "filter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(UNBAL_REAL_CASE, affected_in("sh", self.root, ["muse-spark"]))
        terms = filter_terms(result.stdout)
        self.assertTrue(any(t in UNBAL_REAL_CASE for t in terms),
                        "filter %r cannot select %r" % (terms, UNBAL_REAL_CASE))

    def test_the_unbalanced_file_is_named_on_stderr_and_stdout_stays_the_filter(self):
        result = self.tool("opus-4-6", "--format", "filter")
        self.assertIn("affected-tests: tests/linux/20-unbalanced.sh: unbalanced scan "
                      "at EOF, masking disabled for this file", result.stderr)
        self.assertNotIn("affected-tests", result.stdout)

    def test_the_fallback_costs_the_balanced_files_nothing(self):
        # Masking is per file: 10-fake.sh ends balanced, so its heredoc bodies keep
        # hiding their phantoms while the unbalanced one over-includes.
        hit = affected_in("sh", self.root, ["opus-4-6"])
        self.assertIn(HEREDOC_SH_CASE, hit)
        self.assertNotIn(PHANTOM_SH_CASE, hit)
        self.assertIn(UNBAL_PHANTOM_CASE, hit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
