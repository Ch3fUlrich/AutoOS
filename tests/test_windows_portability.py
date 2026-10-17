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


def _posix_call_name(call: ast.Call) -> str | None:
    """Return the POSIX name of the call *call* (`os.chmod`, `Path.chmod`,
    `fcntl`, ...) or None when it is not a POSIX-only call."""
    if not isinstance(call.func, ast.Attribute):
        return None
    func = call.func
    value = func.value
    # os.chmod(x), os.killpg(...), etc.
    if isinstance(value, ast.Name):
        if value.id == "os" and func.attr in _OS_POSIX_FUNCS:
            return f"os.{func.attr}"
        if value.id in _POSIX_MODULES:
            return value.id
        return None
    # signal.SIGKILL(...)
    if isinstance(value, ast.Attribute) and \
            isinstance(value.value, ast.Name) and value.value.id == "signal":
        if value.attr in _SIGNAL_ATTRS:
            return f"signal.{value.attr}"
        return None
    # Path(...).chmod() - pathlib.Path methods that are POSIX-only
    if isinstance(value, ast.Call) and \
            isinstance(value.func, ast.Name) and value.func.id == "Path" and \
            func.attr == "chmod":
        return "Path.chmod"
    return None


def _posix_call_sites(node: ast.AST) -> list[tuple[ast.AST, str]]:
    """Return `(site, name)` for every POSIX-only reference in *node*.

    A site is the AST node that would have to sit in a non-Windows branch
    for the reference to be safe: the `Call` for a call, the `Attribute`
    for a bare reference, the `Constant` for a shebang string.
    """
    sites: list[tuple[ast.AST, str]] = []

    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _posix_call_name(child)
            if name:
                sites.append((child, name))

        # Bare references like `signal.SIGKILL` as a name constant (e.g. in kill)
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            if child.value.id == "signal" and child.attr in _SIGNAL_ATTRS:
                sites.append((child, f"signal.{child.attr}"))
            elif child.value.id in _POSIX_MODULES:
                sites.append((child, child.value.id))

        # String constants with shebang prefixes
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            for prefix in _SHEBANG_PREFIXES:
                if child.value.startswith(prefix):
                    sites.append((child, "shebang:" + prefix))
                    break

    return sites


def _posix_call_names(node: ast.AST) -> set[str]:
    """Return the set of POSIX-only call/attribute names found in *node*."""
    return {name for _site, name in _posix_call_sites(node)}


def _is_guarded(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
                 source_lines: list[str]) -> bool:
    """Return True if *node* looks guarded against Windows execution.

    A node is guarded if:
    - it has a @unittest.skipIf / @unittest.skipUnless decorator whose
      expression mentions os.name or sys.platform, OR
    - (for functions) the body contains a comparison of os.name / sys.platform
      followed by self.skipTest(...) or raise unittest.SkipTest, OR
    - (for functions) every POSIX-only reference it makes sits in the branch
      of a platform check that does not run on Windows: the body of
      `if os.name != "nt":` or the else of `if os.name == "nt":`.
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

        # A POSIX-only call needs no skip at all when the call itself sits in
        # the non-Windows branch of a platform check:
        #     if os.name != "nt":
        #         os.chmod(path, 0o755)
        # Every POSIX reference must be there; one that also runs on Windows
        # (no platform check, or the Windows branch) leaves the function
        # unguarded.
        sites = _posix_call_sites(node)
        if sites and all(
                _in_non_windows_branch(site, node)
                for site, _name in sites
        ):
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

    # skipIf skips when its condition holds, so it must hold on Windows;
    # skipUnless runs only when it holds, so it must fail on Windows.
    if not d.args:
        return False
    polarity = _true_on_windows(d.args[0])
    if polarity is None:
        return False
    return polarity if d.func.attr == "skipIf" else not polarity


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


def _true_on_windows(test: ast.expr) -> bool | None:
    """True when *test* holds on Windows (os.name == "nt",
    sys.platform.startswith("win"), sys.platform == "win32"), False when it
    holds everywhere else (the != / not forms), None when it is no platform
    check at all."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _true_on_windows(test.operand)
        return None if inner is None else not inner
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        # `os.name == "nt" or <anything>` is true on Windows.
        return True if any(_true_on_windows(v) is True for v in test.values) else None
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and \
            isinstance(test.left, ast.Attribute) and isinstance(test.left.value, ast.Name) and \
            isinstance(test.comparators[0], ast.Constant):
        owner, attr = test.left.value.id, test.left.attr
        value = test.comparators[0].value
        windows = ((owner, attr) == ("os", "name") and value == "nt") or \
                  ((owner, attr) == ("sys", "platform") and isinstance(value, str) and value.startswith("win"))
        if not windows:
            return None
        if isinstance(test.ops[0], ast.Eq):
            return True
        if isinstance(test.ops[0], ast.NotEq):
            return False
        return None
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) and \
            test.func.attr == "startswith" and isinstance(test.func.value, ast.Attribute) and \
            isinstance(test.func.value.value, ast.Name) and \
            (test.func.value.value.id, test.func.value.attr) == ("sys", "platform") and \
            test.args and isinstance(test.args[0], ast.Constant) and \
            str(test.args[0].value).startswith("win"):
        return True
    return None


def _find_containing_platform_check(skip_stmt: ast.AST, func_node: ast.AST) -> bool:
    """Raise _NotGuarded unless *skip_stmt* sits in the branch of an `if`
    that is taken on Windows: the body of `if os.name == "nt"` or the else of
    `if os.name != "nt"`. A skip merely inside some platform `if` is not
    enough - in the other branch it skips on POSIX and the call still runs
    on Windows."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.If):
            continue
        polarity = _true_on_windows(node.test)
        if polarity is None:
            continue
        branch = node.body if polarity else node.orelse
        if any(child is skip_stmt for stmt in branch for child in ast.walk(stmt)):
            return True
    raise _NotGuarded()


def _in_non_windows_branch(site: ast.AST, func_node: ast.AST) -> bool:
    """True when *site* (a POSIX-only reference inside *func_node*) sits in
    the branch that does not run on Windows - the body of
    `if os.name != "nt":` or the else of `if os.name == "nt"` (and the same
    for a ``sys.platform`` check) - of at least one platform check in
    *func_node*. On Windows that branch is never taken, so the reference
    cannot run there. A site in no such branch may still run on Windows and
    is not guarded."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.If):
            continue
        polarity = _true_on_windows(node.test)
        if polarity is None:
            continue
        branch = node.orelse if polarity else node.body
        if any(child is site for stmt in branch for child in ast.walk(stmt)):
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

    def test_a_skip_that_fires_off_windows_is_no_guard(self):
        # Sonnet final review: the skip must sit in the branch that is true on
        # Windows; one that skips on POSIX leaves the call running on Windows.
        wrong_direction = textwrap.dedent("""\
            import os
            import unittest

            class T(unittest.TestCase):
                def test_ne(self):
                    if os.name != "nt":
                        self.skipTest("backwards")
                    os.chmod("x", 0o755)

                def test_else(self):
                    if os.name == "nt":
                        pass
                    else:
                        self.skipTest("backwards")
                    os.chmod("x", 0o755)
        """)
        results = self._lint_source(wrong_direction)
        self.assertTrue(any("test_ne" in r for r in results), results)
        self.assertTrue(any("test_else" in r for r in results), results)

    def test_decorator_direction_matters(self):
        src = textwrap.dedent("""\
            import os
            import unittest

            class T(unittest.TestCase):
                @unittest.skipIf(os.name != "nt", "backwards")
                def test_wrong(self):
                    os.chmod("x", 0o755)

                @unittest.skipUnless(os.name != "nt", "posix only")
                def test_unless(self):
                    os.chmod("x", 0o755)

                @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "posix, not root")
                def test_or(self):
                    os.chmod("x", 0o755)
        """)
        results = self._lint_source(src)
        self.assertTrue(any("test_wrong" in r for r in results), results)
        self.assertFalse(any("test_unless" in r or "test_or" in r for r in results), results)

    def test_a_skip_in_the_windows_branch_is_a_guard(self):
        right = textwrap.dedent("""\
            import os
            import sys
            import unittest

            class T(unittest.TestCase):
                def test_else(self):
                    if os.name != "nt":
                        pass
                    else:
                        self.skipTest("posix only")
                    os.chmod("x", 0o755)

                def test_platform(self):
                    if sys.platform.startswith("win"):
                        raise unittest.SkipTest("posix only")
                    os.chmod("x", 0o755)
        """)
        self.assertEqual(self._lint_source(right), [])

    def test_unguarded_path_chmod_is_flagged(self):
        src = textwrap.dedent("""\
            from pathlib import Path
            import unittest

            class T(unittest.TestCase):
                def test_path_chmod(self):
                    Path("x").chmod(0o755)
        """)
        results = self._lint_source(src)
        self.assertTrue(any("Path.chmod" in r for r in results), results)

    def test_inline_guard_accepts_not_equals_nt(self):
        """Inline guard `if os.name != "nt": self.skipTest(...)` should be accepted."""
        src = textwrap.dedent("""\
            import os
            import unittest
            from pathlib import Path

            class T(unittest.TestCase):
                def test_path_chmod_guarded(self):
                    if os.name != "nt":
                        pass
                    else:
                        self.skipTest("posix only")
                    Path("x").chmod(0o755)
        """)
        results = self._lint_source(src)
        self.assertFalse(results, f"Expected no findings for inline-guarded Path.chmod, got: {results}")

    def test_wrong_polarity_inline_guard_is_flagged(self):
        """Inline guard with wrong polarity (skips on POSIX) should be flagged."""
        src = textwrap.dedent("""\
            import os
            import unittest
            from pathlib import Path

            class T(unittest.TestCase):
                def test_path_chmod_wrong_polarity(self):
                    if os.name != "nt":
                        self.skipTest("backwards")
                    Path("x").chmod(0o755)
        """)
        results = self._lint_source(src)
        self.assertTrue(any("test_path_chmod_wrong_polarity" in r for r in results), results)

    def test_path_chmod_with_decorator_guard(self):
        """Path.chmod with @unittest.skipIf decorator should be accepted."""
        src = textwrap.dedent("""\
            import os
            import unittest
            from pathlib import Path

            class T(unittest.TestCase):
                @unittest.skipIf(os.name == "nt", "posix only")
                def test_path_chmod_decorated(self):
                    Path("x").chmod(0o755)
        """)
        results = self._lint_source(src)
        self.assertFalse(results, f"Expected no findings for decorator-guarded Path.chmod, got: {results}")

    def test_inline_guard_call_in_not_equals_nt_branch(self):
        """Call directly in `if os.name != "nt":` branch should be ACCEPTED."""
        src = textwrap.dedent("""\
            import os
            import unittest
            class T(unittest.TestCase):
                def test_guarded_call(self):
                    if os.name != "nt":
                        os.chmod("x", 0o755)
        """)
        results = self._lint_source(src)
        self.assertFalse(results, f"Expected inline-guarded os.chmod to be accepted, got: {results}")

    def test_wrong_polarity_call_in_equals_nt_branch(self):
        """Call in `if os.name == "nt":` branch should be FLAGGED (runs on Windows)."""
        src = textwrap.dedent("""\
            import os
            import unittest
            class T(unittest.TestCase):
                def test_wrong_polarity(self):
                    if os.name == "nt":
                        os.chmod("x", 0o755)
        """)
        results = self._lint_source(src)
        self.assertTrue(any("test_wrong_polarity" in r for r in results), f"Expected wrong-polarity to be flagged, got: {results}")

    def test_path_chmod_inline_guard(self):
        """Path.chmod in `if os.name != "nt":` branch should be ACCEPTED."""
        src = textwrap.dedent("""\
            import os
            from pathlib import Path
            class T(unittest.TestCase):
                def test_path_guarded(self):
                    if os.name != "nt":
                        Path("x").chmod(0o755)
        """)
        results = self._lint_source(src)
        self.assertFalse(results, f"Expected inline-guarded Path.chmod to be accepted, got: {results}")

    def test_mutation_wrong_polarity_fails(self):
        """Mutation proof: wrong-polarity must be flagged."""
        src = textwrap.dedent("""\
            import os
            import unittest
            class T(unittest.TestCase):
                def test_mutation_target(self):
                    if os.name == "nt":
                        os.chmod("x", 0o755)
        """)
        results = self._lint_source(src)
        self.assertTrue(any("test_mutation_target" in r for r in results),
                        f"Mutation proof failed: wrong-polarity must be flagged, got: {results}")


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