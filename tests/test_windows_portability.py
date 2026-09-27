#!/usr/bin/env python3
"""Lint: every POSIX-only pattern in Python test files must have a Windows guard;
every .ps1/.psm1 file must have a UTF-8 BOM.

Run:
    python3 tests/test_windows_portability.py          # lint the repo
    python3 tests/test_windows_portability.py --self   # self-test with fixtures
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# POSIX-only names (stdlib modules and functions that do not exist on Windows)
# ---------------------------------------------------------------------------
# os functions that do not exist on Windows (AttributeError there) or, for
# chmod, silently ignore the POSIX mode bits a test asserts on.
_OS_POSIX_FUNCS = frozenset({
    "chmod", "killpg", "setsid", "getpgid", "fork", "mkfifo",
    "getuid", "geteuid", "getgid", "getegid",
})
# POSIX-only modules.
_POSIX_MODULES = frozenset({"fcntl", "pwd", "grp"})

# signal.* members Windows does not define (SIGTERM exists there but cannot
# reach a process group, which is what these tests use it for).
_SIGNAL_ATTRS = frozenset({"SIGKILL", "SIGTERM", "SIGALRM", "SIGPIPE", "SIGUSR1", "SIGUSR2", "SIGHUP"})

# Shebang prefixes we look for in string constants
_SHEBANG_PREFIXES = ("#!/bin/sh", "#!/usr/bin/env sh", "#!/usr/bin/env bash")


def _posix_call_names(node: ast.AST) -> set[str]:
    """Return the set of POSIX-only call/attribute names found in *node*."""
    found: set[str] = set()

    for child in ast.walk(node):
        # os.chmod(x), os.killpg(...), etc.
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if isinstance(child.func.value, ast.Name) and child.func.value.id == "os":
                if child.func.attr in _OS_POSIX_FUNCS:
                    found.add(f"os.{child.func.attr}")
            elif isinstance(child.func.value, ast.Attribute) and \
                    isinstance(child.func.value.value, ast.Name) and \
                    child.func.value.value.id == "signal":
                if child.func.value.attr in _SIGNAL_ATTRS:
                    found.add(f"signal.{child.func.value.attr}")
            elif isinstance(child.func.value, ast.Name) and \
                    child.func.value.id in _POSIX_MODULES:
                found.add(child.func.value.id)

        # Bare references like `signal.SIGKILL` as a name constant (e.g. in kill)
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            if child.value.id == "signal" and child.attr in _SIGNAL_ATTRS:
                found.add(f"signal.{child.attr}")
            if child.value.id in _POSIX_MODULES:
                found.add(child.value.id)

        # String constants with shebang prefixes
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            for prefix in _SHEBANG_PREFIXES:
                if child.value.startswith(prefix):
                    found.add("shebang:" + prefix)
                    break

    return found


def _is_guarded(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                 source_lines: list[str]) -> bool:
    """Return True if *node* looks guarded against Windows execution.

    A node is guarded if:
    - it has a @unittest.skipIf / @unittest.skipUnless decorator whose
      expression mentions os.name or sys.platform, OR
    - (for functions) the body contains a comparison of os.name / sys.platform
      followed by self.skipTest(...) or raise unittest.SkipTest.
    """
    # Check decorators
    for d in node.decorator_list:
        if _decorator_is_skip_with_platform_guard(d):
            return True

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Check body for inline guards
        _found_guard = False
        for stmt in ast.walk(node):
            if _is_skip_test_call(stmt):
                # Walk the tree above this statement (through parent links)
                # to see if it's inside an os.name/sys.platform comparison
                try:
                    _find_containing_platform_check(stmt, node)
                    _found_guard = True
                except _NotGuarded:
                    pass
        if _found_guard:
            return True

    return False


def _decorator_is_skip_with_platform_guard(d: ast.expr) -> bool:
    """Check if *d* is @unittest.skipIf(...) or @unittest.skipUnless(...)
    that mentions os.name or sys.platform."""
    if not isinstance(d, ast.Call):
        return False
    if not isinstance(d.func, ast.Attribute):
        return False
    if d.func.attr not in ("skipIf", "skipUnless"):
        return False
    if not isinstance(d.func.value, ast.Name):
        return False
    if d.func.value.id != "unittest":
        return False

    # Walk the arguments for os.name or sys.platform
    for arg_node in ast.walk(d):
        if isinstance(arg_node, ast.Attribute) and isinstance(arg_node.value, ast.Name):
            if arg_node.value.id == "os" and arg_node.attr == "name":
                return True
            if arg_node.value.id == "sys" and arg_node.attr == "platform":
                return True
    return False


def _is_skip_test_call(node: ast.AST) -> bool:
    """Return True if *node* is self.skipTest(...) or raise unittest.SkipTest."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        c = node.value
        if isinstance(c.func, ast.Attribute) and c.func.attr == "skipTest":
            return True
    if isinstance(node, ast.Raise):
        if isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Attribute):
                f = node.exc.func
                if f.attr == "SkipTest" and isinstance(f.value, ast.Name) and f.value.id == "unittest":
                    return True
            if isinstance(node.exc.func, ast.Name) and node.exc.func.id == "SkipTest":
                return True
    return False


class _NotGuarded(Exception):
    """Raised when we determine a skipTest call is NOT inside a platform guard."""


def _find_containing_platform_check(skip_stmt: ast.AST, func_node: ast.AST) -> bool:
    """Walk up from skip_stmt to find if it's inside an os.name/sys.platform check.

    Since AST nodes don't have parent links, we walk the function body tree
    looking for If statements with a platform check that contain the skip_stmt.
    We check by source position.
    """
    # Get source positions
    skip_lineno = getattr(skip_stmt, "lineno", None)
    if skip_lineno is None:
        raise _NotGuarded()

    for node in ast.walk(func_node):
        if isinstance(node, ast.If):
            cond = node.test
            if _mentions_platform(cond):
                # Check if skip_stmt is within this if's body
                for child in ast.walk(node):
                    if child is skip_stmt:
                        return True
            # Check elif/else
            if hasattr(node, "orelse") and node.orelse:
                for elif_node in node.orelse:
                    if isinstance(elif_node, ast.If) and _mentions_platform(elif_node.test):
                        for child in ast.walk(elif_node):
                            if child is skip_stmt:
                                return True

    # Also check top-level body statements
    body = getattr(func_node, "body", [])
    for stmt in body:
        if stmt is skip_stmt:
            continue
        for child in ast.walk(stmt):
            if child is skip_stmt:
                # Check if this is inside an If that mentions platform
                for parent in ast.walk(func_node):
                    if isinstance(parent, ast.If) and parent is not stmt:
                        for c2 in ast.walk(parent):
                            if c2 is skip_stmt:
                                if _mentions_platform(parent.test):
                                    return True

    raise _NotGuarded()


def _mentions_platform(node: ast.AST) -> bool:
    """Return True if the expression mentions os.name or sys.platform."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            if child.value.id == "os" and child.attr == "name":
                return True
            if child.value.id == "sys" and child.attr == "platform":
                return True
    return False


def lint_posix_guards(repo: Path | None = None) -> list[str]:
    """Check every tests/test_*.py for unguarded POSIX-only patterns.

    Returns a list of 'file:line:function' strings for each unguarded function.
    """
    repo = repo or REPO
    findings: list[str] = []
    test_dir = repo / "tests"
    for pyfile in sorted(test_dir.glob("test_*.py")):
        source = pyfile.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(pyfile))
        except SyntaxError as exc:
            findings.append(f"{pyfile.relative_to(repo)}:0:0:parse-error:{exc}")
            continue

        source_lines = source.splitlines()

        # Build class->decorator guard map (class-level guards protect methods)
        # Keyed by the node itself: two classes may share a name.
        class_guarded: dict[ast.ClassDef, bool] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_guarded[node] = _is_guarded(node, source_lines)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                posix_calls = _posix_call_names(node)
                if not posix_calls:
                    continue

                # Determine if this function is guarded
                guarded = False

                # 1. Decorated directly
                if _is_guarded(node, source_lines):
                    guarded = True

                # 2. Enclosing class is guarded
                if not guarded:
                    for parent in ast.walk(tree):
                        if isinstance(parent, ast.ClassDef):
                            for child in ast.walk(parent):
                                if child is node:
                                    if class_guarded.get(parent, False):
                                        guarded = True
                                    break

                # 3. Body contains inline guard (already checked in _is_guarded)

                if not guarded:
                    rel = pyfile.relative_to(repo)
                    findings.append(f"{rel}:{node.lineno}:{node.name}  ({', '.join(sorted(posix_calls))})")

    return findings


def lint_bom(repo: Path | None = None) -> list[str]:
    """Check every .ps1/.psm1 file in git's index has a UTF-8 BOM.

    Returns a list of file paths that are missing the BOM.
    """
    repo = repo or REPO
    findings: list[str] = []
    result = subprocess.run(
        ["git", "ls-files", "*.ps1", "*.psm1"],
        capture_output=True, text=True, cwd=repo, timeout=30,
    )
    if result.returncode != 0:
        findings.append(f"git ls-files failed: {result.stderr}")
        return findings

    for relpath in result.stdout.splitlines():
        if not relpath.strip():
            continue
        full = repo / relpath
        if not full.is_file():
            findings.append(f"{relpath} (missing from working tree)")
            continue
        with open(full, "rb") as fh:
            first_bytes = fh.read(3)
        if first_bytes != b"\xef\xbb\xbf":
            findings.append(relpath)

    return findings


# ---------------------------------------------------------------------------
# Self-test fixtures (test-first: each must fail before it passes)
# ---------------------------------------------------------------------------

_FIXTURE_UNGUARDED_SHBANG = textwrap.dedent("""\
    import os
    import unittest

    class MyTest(unittest.TestCase):
        def test_creates_sh_stub(self):
            with open("/tmp/x", "w") as fh:
                fh.write("#!/bin/sh\\n")
            result = os.system("/tmp/x --help")
            self.assertEqual(result, 0)
    """)

_FIXTURE_GUARDED_DECORATOR = textwrap.dedent("""\
    import os
    import unittest

    class MyTest(unittest.TestCase):
        @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
        def test_creates_sh_stub(self):
            with open("/tmp/x", "w") as fh:
                fh.write("#!/bin/sh\\n")
            result = os.system("/tmp/x --help")
            self.assertEqual(result, 0)
    """)

_FIXTURE_GUARDED_CLASS = textwrap.dedent("""\
    import os
    import unittest

    @unittest.skipIf(os.name == "nt", "POSIX only")
    class MyTest(unittest.TestCase):
        def test_creates_sh_stub(self):
            with open("/tmp/x", "w") as fh:
                fh.write("#!/bin/sh\\n")
            result = os.system("/tmp/x --help")
            self.assertEqual(result, 0)
    """)

_FIXTURE_GUARDED_BODY = textwrap.dedent("""\
    import os
    import unittest

    class MyTest(unittest.TestCase):
        def test_creates_sh_stub(self):
            if os.name == "nt":
                self.skipTest("sh stub; POSIX only")
            with open("/tmp/x", "w") as fh:
                fh.write("#!/bin/sh\\n")
            result = os.system("/tmp/x --help")
            self.assertEqual(result, 0)
    """)


class PosixGuardLintTests(unittest.TestCase):
    """Self-tests: fixtures that MUST be flagged / MUST be clean."""

    maxDiff = None

    def _lint_source(self, source: str) -> list[str]:
        """Run the POSIX-guard lint against *source* as if it were a test file."""
        tmp = Path(tempfile.mkdtemp())
        try:
            test_dir = tmp / "tests"
            test_dir.mkdir(parents=True)
            test_file = test_dir / "test_fixture.py"
            test_file.write_text(source, encoding="utf-8")
            return lint_posix_guards(repo=tmp)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unguarded_sh_stub_is_flagged(self):
        results = self._lint_source(_FIXTURE_UNGUARDED_SHBANG)
        self.assertTrue(
            any("test_creates_sh_stub" in r for r in results),
            f"Expected test_creates_sh_stub to be flagged, got: {results}",
        )

    def test_decorator_guard_skips(self):
        results = self._lint_source(_FIXTURE_GUARDED_DECORATOR)
        self.assertFalse(
            any("test_creates_sh_stub" in r for r in results),
            f"Expected no findings for decorated function, got: {results}",
        )

    def test_class_level_guard_skips(self):
        results = self._lint_source(_FIXTURE_GUARDED_CLASS)
        self.assertFalse(
            any("test_creates_sh_stub" in r for r in results),
            f"Expected no findings for class-guarded function, got: {results}",
        )

    def test_inline_body_guard_skips(self):
        results = self._lint_source(_FIXTURE_GUARDED_BODY)
        self.assertFalse(
            any("test_creates_sh_stub" in r for r in results),
            f"Expected no findings for inline-guarded function, got: {results}",
        )


class BomLintTests(unittest.TestCase):
    """Self-tests: BOM detection."""

    def test_file_with_bom_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.ps1"
            f.write_bytes(b"\xef\xbb\xbf# my script\n")
            subprocess.run(
                ["git", "init"], capture_output=True, cwd=tmp_path, timeout=10,
            )
            subprocess.run(
                ["git", "add", "test.ps1"], capture_output=True, cwd=tmp_path, timeout=10,
            )
            findings = lint_bom(repo=tmp_path)
            self.assertFalse(findings, f"Expected no BOM findings, got: {findings}")

    def test_file_without_bom_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.ps1"
            f.write_bytes(b"# no bom\n")
            subprocess.run(
                ["git", "init"], capture_output=True, cwd=tmp_path, timeout=10,
            )
            subprocess.run(
                ["git", "add", "test.ps1"], capture_output=True, cwd=tmp_path, timeout=10,
            )
            findings = lint_bom(repo=tmp_path)
            self.assertTrue(
                any("test.ps1" in r for r in findings),
                f"Expected test.ps1 to be flagged, got: {findings}",
            )


class RepoLintTests(unittest.TestCase):
    """Lint the actual repo (exit non-zero if any finding)."""

    def test_posix_guards_are_clean(self):
        findings = lint_posix_guards()
        if findings:
            msg = "\n".join(findings)
            self.fail(
                f"Unguarded POSIX-only patterns ({len(findings)}):\n{msg}\n\n"
                "Add @unittest.skipIf(os.name == 'nt', ...) or an inline guard."
            )

    def test_bom_is_present(self):
        findings = lint_bom()
        if findings:
            msg = "\n".join(findings)
            self.fail(
                f"Files missing UTF-8 BOM ({len(findings)}):\n{msg}\n\n"
                "Open the file in PowerShell ISE or VS Code and save with "
                "UTF-8 BOM encoding."
            )


if __name__ == "__main__":
    if "--self" in sys.argv:
        sys.argv.remove("--self")
        # Run only self-tests, skip repo lint
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        suite.addTests(loader.loadTestsFromTestCase(PosixGuardLintTests))
        suite.addTests(loader.loadTestsFromTestCase(BomLintTests))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        unittest.main()