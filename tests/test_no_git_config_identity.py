#!/usr/bin/env python3
"""Static guard: no test under tests/ writes a git identity with `git config`.

Incident 2026-10-12 (AO-PREPUSH-HOOK-GITENV-2): tests/test_autoos_risk.py ran
`git config user.name risk-test` through a helper that passed no env, and from a
pre-push hook (GIT_DIR inherited) the write landed in the REAL repo's config.
A test sets its identity with `-c user.name=.. -c user.email=..` or
GIT_AUTHOR_*/GIT_COMMITTER_* env, never by writing config.

Run directly:

    python3 tests/test_no_git_config_identity.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _Scan:
    NAME = re.compile(r"""config["'\s,]+(?:["']?--[\w-]+["']?[\s,]+)*["']?user\.(?:name|email)""")
    READ = re.compile(r"--(?:get|get-all|unset|unset-all|list|get-regexp)\b")


def writes_identity(text):
    """Line numbers where `text` runs `git config user.name|user.email` as a write."""
    hits = []
    for found in _Scan.NAME.finditer(text):
        span = text[found.start():found.end()]
        if not _Scan.READ.search(span):
            hits.append(text.count("\n", 0, found.start()) + 1)
    return hits


class StaticIdentityGuardTests(unittest.TestCase):
    """No test writes a git identity with `git config` (AO-PREPUSH-HOOK-GITENV-2).

    Limit, one line: a string scan - it sees literal spellings (argv list, shell
    or PowerShell command), not a key built from parts or a file written by hand.
    """

    SUFFIXES = (".py", ".sh", ".ps1", ".psm1", ".bash")

    def test_the_scan_sees_the_spellings_it_claims_to(self):
        for text in ('git config user.name "x"', 'self._git(["config", "user.email", "a"])',
                     'git -C $r config --local user.name x', 'run_git("config", "user.name", 1)',
                     'g(\n    "config",\n    "user.email", "x")'):
            self.assertTrue(writes_identity(text), text)
        for text in ('git -c user.name=t -c user.email=a commit', 'GIT_AUTHOR_NAME="t"',
                     'git config --get user.name', 'git config --unset user.email',
                     'git config core.bare true'):
            self.assertFalse(writes_identity(text), text)

    def test_no_test_writes_a_git_identity_through_git_config(self):
        tests = ROOT / "tests"
        me = Path(__file__).resolve()
        files = [p for p in sorted(tests.rglob("*")) if p.is_file()
                 and p.suffix in self.SUFFIXES and p.resolve() != me]
        self.assertIn(tests / "test_autoos_risk.py", files)
        offenders = ["%s:%d" % (p.relative_to(ROOT).as_posix(), n) for p in files
                     for n in writes_identity(p.read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual(offenders, [], "a test writes user.name/user.email with `git config` "
                         "(that write lands in the real repo when GIT_DIR is inherited): pass "
                         "`-c user.name=.. -c user.email=..` or GIT_AUTHOR_*/GIT_COMMITTER_* env")


if __name__ == "__main__":
    unittest.main(verbosity=2)
