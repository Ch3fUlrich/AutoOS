"""Tests for the report-only post-run diff guard (tools/diff_guard.py + tools/diff-guard.py).

Run directly: python tests/test_diff_guard.py

Every case drives the real CLI as a subprocess against a tiny throwaway git
repository built in a temp directory (two commits: baseline then tip), so the
tests assert on the guard's observable contract - findings, output shape and
exit codes - without ever touching the AutoOS working tree or the network.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "tools" / "diff-guard.py"

GIT = shutil.which("git")

# One finding per line: `RULE path:line text`.
_FINDING = re.compile(r"^(R\d) (\S+):(\d+) (.*)$")

BASE_LIB = '''"""Sample library."""

def add(a, b):
    return a + b
'''

BASE_TESTS = '''"""Tests for the sample library."""

def test_add():
    assert add(1, 2) == 3

def test_sub():
    assert add(1, -1) == 0
'''


def _git_env(repo: Path) -> dict:
    """Isolated git environment: no user/system config reaches a fixture."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = str(repo / "gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _write(repo: Path, path: str, content: str) -> None:
    """Write LF bytes (text-mode open would translate newlines on Windows)."""
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8"))


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_git_env(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _commit(repo: Path, message: str) -> str:
    _run_git(repo, "add", "-A")
    result = _run_git(
        repo,
        "-c", "user.name=AutoOS DiffGuard Fixture",
        "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message,
    )
    if result.returncode != 0:
        raise AssertionError(
            "fixture commit failed: " + result.stderr.decode("utf-8", "replace")
        )
    sha = _run_git(repo, "rev-parse", "HEAD")
    return sha.stdout.decode("utf-8").strip()


def _findings(proc: subprocess.CompletedProcess) -> list:
    """Parse `RULE path:line text` findings out of stdout."""
    out = []
    stdout = proc.stdout.decode("utf-8", "replace")
    for line in stdout.split("\n"):
        match = _FINDING.match(line.rstrip("\r"))
        if match:
            out.append({
                "rule": match.group(1),
                "path": match.group(2),
                "line": int(match.group(3)),
                "text": match.group(4),
            })
    return out


def _only(findings: list, rule: str) -> dict:
    """Exactly one finding of `rule` (asserts the count too)."""
    hits = [f for f in findings if f["rule"] == rule]
    if len(hits) != 1:
        raise AssertionError(
            "expected exactly one %s finding, got %r" % (rule, findings)
        )
    return hits[0]


@unittest.skipUnless(GIT, "git not available on PATH")
class DiffGuardCliTests(unittest.TestCase):
    """End-to-end behaviour of tools/diff-guard.py."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="autoos-diff-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "gitconfig").write_bytes(b"")
        _run_git(self.repo, "init", "-q")

    # -- fixture helpers ---------------------------------------------------

    def make_repo(self, baseline: dict) -> str:
        for path, content in baseline.items():
            _write(self.repo, path, content)
        return _commit(self.repo, "baseline")

    def commit_tip(self, changes: dict) -> None:
        for path, content in changes.items():
            _write(self.repo, path, content)
        _commit(self.repo, "tip")

    def run_cli(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        """Run the CLI; by default cwd is the fixture so --repo defaults to it."""
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(cwd or self.repo),
            env=_git_env(self.repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    # -- R2: builtin-basis class defining dunder equality ------------------

    def test_incident_tuple_subclass_eq_reports_r2(self) -> None:
        """The historical incident: a tuple subclass with a lying __eq__."""
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({
            "src/tuple_types.py": (
                "class _JsFilesTuple(tuple):\n"
                "    def __eq__(self, other):\n"
                "        return True\n"
            ),
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 1, proc.stderr.decode("utf-8", "replace"))
        finding = _only(_findings(proc), "R2")
        self.assertEqual(finding["path"], "src/tuple_types.py")
        self.assertEqual(finding["line"], 1)
        self.assertEqual(
            finding["text"], "class _JsFilesTuple(tuple) defines __eq__"
        )

    # -- clean runs --------------------------------------------------------

    def test_clean_diff_exits_zero(self) -> None:
        """A source change inside scope: no findings, exit 0, --repo default works."""
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({
            "src/lib.py": BASE_LIB + "\ndef mul(a, b):\n    return a * b\n",
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_findings(proc), [])
        # The same run from a foreign cwd through an explicit --repo.
        foreign = self.tmp / "elsewhere"
        foreign.mkdir()
        via_flag = self.run_cli(
            "--base", base, "--tip", "HEAD", "--scope", "src/**",
            "--repo", str(self.repo), cwd=foreign,
        )
        self.assertEqual(via_flag.returncode, 0, via_flag.stdout + via_flag.stderr)
        json_proc = self.run_cli(
            "--base", base, "--tip", "HEAD", "--scope", "src/**", "--json",
        )
        self.assertEqual(json_proc.returncode, 0)
        self.assertEqual(json.loads(json_proc.stdout.decode("utf-8")), [])

    # -- R1: test files outside --scope ------------------------------------

    def test_test_file_outside_scope_reports_r1(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({"tests/test_lib.py": BASE_TESTS + "# reviewed\n"})
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R1")
        self.assertEqual(finding["path"], "tests/test_lib.py")
        self.assertEqual(finding["text"], "test file changed outside scope (M)")

    def test_test_file_inside_scope_is_clean(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({"tests/test_lib.py": BASE_TESTS + "# reviewed\n"})
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_findings(proc), [])

    def test_scope_glob_double_star_matches_nested_paths(self) -> None:
        """`tests/**` covers any depth; a narrow pattern flags the leftover."""
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({
            "tests/test_lib.py": BASE_TESTS + "# reviewed\n",
            "tests/deep/nested/test_extra.py": "def test_nested():\n    assert True\n",
        })
        broad = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(broad.returncode, 0, broad.stdout + broad.stderr)
        narrow = self.run_cli(
            "--base", base, "--tip", "HEAD", "--scope", "tests/test_*.py",
        )
        self.assertEqual(narrow.returncode, 1, narrow.stdout)
        finding = _only(_findings(narrow), "R1")
        self.assertEqual(finding["path"], "tests/deep/nested/test_extra.py")
        self.assertEqual(finding["text"], "test file changed outside scope (A)")

    # -- R1 rename / delete -----------------------------------------------

    def test_rename_and_delete_test_files_report_r1(self) -> None:
        base = self.make_repo({
            "src/lib.py": BASE_LIB,
            "tests/test_lib.py": BASE_TESTS,
            "tests/test_redundant.py": "def test_old():\n    return None\n",
        })
        self.assertEqual(
            _run_git(self.repo, "rm", "-q", "tests/test_redundant.py").returncode, 0
        )
        moved = _run_git(
            self.repo, "mv", "tests/test_lib.py", "tests/test_lib_renamed.py"
        )
        self.assertEqual(moved.returncode, 0, moved.stderr.decode("utf-8", "replace"))
        _commit(self.repo, "rename and delete")
        proc = self.run_cli(
            "--base", base, "--tip", "HEAD", "--scope", "src/**",
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        r1_by_path = {
            f["path"]: f["text"]
            for f in _findings(proc)
            if f["rule"] == "R1"
        }
        self.assertEqual(
            r1_by_path.get("tests/test_lib_renamed.py"),
            "test file changed outside scope (R)",
        )
        self.assertEqual(
            r1_by_path.get("tests/test_redundant.py"),
            "test file changed outside scope (D)",
        )

    # -- R3: test framework references in non-test code ---------------------

    def test_import_pytest_in_source_reports_r3(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({
            "src/plugin.py": (
                "def load(name):\n"
                "    import pytest\n"
                "    return pytest\n"
            ),
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R3")
        self.assertEqual(finding["path"], "src/plugin.py")
        self.assertEqual(finding["line"], 2)
        self.assertEqual(finding["text"], "import pytest")

    # -- R4: skips, shrinking asserts, grown literals -----------------------

    def test_skip_added_reports_r4(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({
            "tests/test_lib.py": BASE_TESTS.replace(
                "def test_add():", '@unittest.skip("flaky")\ndef test_add():'
            ),
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R4")
        self.assertEqual(finding["path"], "tests/test_lib.py")
        self.assertEqual(finding["text"], '@unittest.skip("flaky")')
        tip_lines = (
            self.repo / "tests" / "test_lib.py"
        ).read_bytes().decode("utf-8").split("\n")
        self.assertEqual(
            finding["line"],
            tip_lines.index('@unittest.skip("flaky")') + 1,
        )

    def test_assertion_deleted_reports_r4(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        deleted = BASE_TESTS.replace(
            "    assert add(1, 2) == 3\n", "    return True\n"
        ).replace(
            "    assert add(1, -1) == 0\n", "    return True\n"
        )
        self.commit_tip({"tests/test_lib.py": deleted})
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R4")
        self.assertEqual(finding["text"], "assert lines removed 2 > added 0")
        base_lines = BASE_TESTS.split("\n")
        self.assertEqual(
            finding["line"], base_lines.index("    assert add(1, 2) == 3") + 1
        )

    def test_numeric_literal_growth_reports_r4(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        grown = BASE_TESTS.replace(
            "    assert add(1, 2) == 3\n", "    assert add(1, 2) == 5\n"
        )
        self.commit_tip({"tests/test_lib.py": grown})
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R4")
        self.assertEqual(finding["text"], "assert numeric literal grew: 3 -> 5")
        grown_lines = grown.split("\n")
        self.assertEqual(
            finding["line"], grown_lines.index("    assert add(1, 2) == 5") + 1
        )

    # -- R5: deleted test definitions ---------------------------------------

    def test_deleted_test_function_reports_r5(self) -> None:
        baseline = (
            '"""Tests for the sample library."""\n'
            "\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
            "\n"
            "def test_gone():\n"
            "    return 1\n"
        )
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": baseline})
        self.commit_tip({
            "tests/test_lib.py": (
                '"""Tests for the sample library."""\n'
                "\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
            ),
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        finding = _only(_findings(proc), "R5")
        self.assertEqual(finding["path"], "tests/test_lib.py")
        self.assertEqual(finding["text"], "def test_gone():")
        self.assertEqual(
            finding["line"], baseline.split("\n").index("def test_gone():") + 1
        )

    # -- R0: unparsable tip source ------------------------------------------

    def test_unparsable_source_reports_r0(self) -> None:
        """A broken tip file yields R0 and never crashes the run."""
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({"src/broken.py": "def broken(:\n"})
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        finding = _only(_findings(proc), "R0")
        self.assertEqual(finding["path"], "src/broken.py")
        self.assertEqual(finding["line"], 1)
        self.assertEqual(finding["text"], "unparsable")

    # -- exit codes, errors, output shapes -----------------------------------

    def test_bad_revision_exits_two_with_message(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        proc = self.run_cli(
            "--base", "no-such-revision-anywhere", "--tip", "HEAD",
            "--scope", "src/**",
        )
        self.assertEqual(proc.returncode, 2)
        stderr = proc.stderr.decode("utf-8", "replace")
        self.assertIn("diff-guard:", stderr)
        # Never a silent pass: no findings line may be printed on stdout.
        self.assertEqual(_findings(proc), [])

    def test_json_output_shape(self) -> None:
        """--json emits a list of {rule,path,line,text} objects."""
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({"tests/test_lib.py": BASE_TESTS + "# reviewed\n"})
        proc = self.run_cli(
            "--base", base, "--tip", "HEAD", "--scope", "src/**", "--json",
        )
        self.assertEqual(proc.returncode, 1, proc.stderr.decode("utf-8", "replace"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        entry = payload[0]
        self.assertEqual(set(entry), {"rule", "path", "line", "text"})
        self.assertIsInstance(entry["rule"], str)
        self.assertIsInstance(entry["path"], str)
        self.assertIsInstance(entry["line"], int)
        self.assertIsInstance(entry["text"], str)
        self.assertEqual(entry["rule"], "R1")
        self.assertEqual(entry["line"], 1)
        self.assertEqual(entry["text"], "test file changed outside scope (M)")

    def test_line_output_format_and_text_clipping(self) -> None:
        """Every line matches `RULE path:line text` and the text clips to 160."""
        long_name = "def test_" + ("z" * 200) + "():"
        baseline = "def test_keep():\n    return None\n\n" + long_name + "\n    return None\n"
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": baseline})
        self.commit_tip({
            "tests/test_lib.py": "def test_keep():\n    return None\n",
        })
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "tests/**")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        decoded = proc.stdout.decode("utf-8", "replace")
        raw_lines = [l.rstrip("\r") for l in decoded.split("\n") if l]
        self.assertEqual(len(raw_lines), 1, proc.stdout)
        match = _FINDING.match(raw_lines[0])
        self.assertIsNotNone(match, raw_lines[0])
        self.assertEqual(match.group(1), "R5")
        self.assertTrue(match.group(4))
        self.assertLessEqual(len(match.group(4)), 160)
        self.assertEqual(len(match.group(4)), 160)

    def test_guard_never_modifies_the_repository(self) -> None:
        base = self.make_repo({"src/lib.py": BASE_LIB, "tests/test_lib.py": BASE_TESTS})
        self.commit_tip({"tests/test_lib.py": BASE_TESTS + "# reviewed\n"})

        def snapshot() -> dict:
            files = {}
            for path in sorted(self.repo.rglob("*")):
                if path.is_file() and ".git" not in path.parts:
                    files[str(path.relative_to(self.repo))] = path.read_bytes()
            return files

        before = snapshot()
        status_before = _run_git(self.repo, "status", "--porcelain").stdout
        proc = self.run_cli("--base", base, "--tip", "HEAD", "--scope", "src/**")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(before, snapshot())
        self.assertEqual(
            status_before, _run_git(self.repo, "status", "--porcelain").stdout
        )

    # -- usage errors --------------------------------------------------------

    def test_missing_required_argument_is_usage_error(self) -> None:
        proc = self.run_cli("--tip", "HEAD")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage:", proc.stderr.decode("utf-8", "replace"))


class WriterRuleTests(unittest.TestCase):
    """The writer rule as data: `diff_guard.WRITER_RULE` + the --writer-rule flag.

    Add-only block: the DiffGuardCliTests above stay exactly as they were and
    must keep passing; nothing here depends on a git fixture either.
    """

    # The reviewed text, written out in full on purpose: the test asserting the
    # module constant equals it only proves something because of this literal.
    REVIEWED = (
        "Never change production behaviour to satisfy a test and never edit tests outside the write scope. If a test fails, report it. Do not add equality or hash overrides to subclasses of builtin types, do not detect that a test is running (no checks for the test runner, its environment variables or loaded modules), do not special-case test inputs or expected values, and do not weaken, skip or delete a test or a guard. Report the failure instead."
    )

    # -- helpers -----------------------------------------------------------

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        """Drive the real CLI as a subprocess (from the repo root: no git needed)."""
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def guard_module(self):
        """Import tools/diff_guard.py as the CLI imports it (sys.path insert)."""
        tools = str(ROOT / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import diff_guard  # noqa: E402  (sibling of tools/diff-guard.py)
        return diff_guard

    @staticmethod
    def rule_stdout(proc: subprocess.CompletedProcess) -> str:
        """stdout with CRLF normalised to LF (a piped text stream may give CRLF on Windows)."""
        return proc.stdout.decode("utf-8").replace("\r\n", "\n")

    # -- tests ---------------------------------------------------------------

    def test_writer_rule_constant_equals_the_reviewed_text(self) -> None:
        self.assertEqual(self.guard_module().WRITER_RULE, self.REVIEWED)

    def test_writer_rule_flag_alone_prints_rule_and_exits_zero(self) -> None:
        """`--writer-rule` with no other argument: exit 0, rule + LF, empty stderr."""
        proc = self.run_cli("--writer-rule")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(self.rule_stdout(proc), self.REVIEWED + "\n")
        self.assertEqual(proc.stderr, b"")

    def test_writer_rule_flag_still_prints_with_other_arguments(self) -> None:
        """Present with --base/--tip/--scope: the rule still prints (exit 0)."""
        proc = self.run_cli(
            "--base", "HEAD", "--tip", "HEAD", "--scope", "src/**",
            "--writer-rule",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(self.rule_stdout(proc), self.REVIEWED + "\n")
        self.assertEqual(proc.stderr, b"")

    def test_writer_contract_doc_contains_the_rule_verbatim(self) -> None:
        """docs/ai/writer-contract.md exists and cannot drift from the constant."""
        doc_path = ROOT / "docs" / "ai" / "writer-contract.md"
        self.assertTrue(doc_path.is_file(), "missing %s" % doc_path)
        doc = doc_path.read_bytes().decode("utf-8")
        self.assertIn(self.guard_module().WRITER_RULE, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
