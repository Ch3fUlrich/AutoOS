#!/usr/bin/env python3
"""The credential-JSON ignore gate (operator 2026-10-01).

Why
---
`configuration/vertex-credentials-<project>-<id>.json` is a real GCP
service-account key: it authenticates the `vertex` provider and it is not a
template. On 2026-10-01 the operator's live copy sat in the working tree
**untracked but UNIGNORED** - git reported it as `??`, so a single
`git add -A` (or the `git add -f` habit this repository's agents use for
handoff notes) would have committed a live credential to a public
repository. `configuration/api-keys.yml` was already ignored; credential
JSONs were not.

This test is the red-first gate for that class, and it fails closed:

1. `.gitignore` must carry the pattern `configuration/vertex-credentials-*.json`
   and the sibling `configuration/*-credentials*.json`.
2. Any credential-shaped path that actually exists on disk must be ignored
   (`git check-ignore -q`), whatever its exact name.
3. No credential-shaped path may be TRACKED (`git ls-files`). A tracked one is
   already published; this assertion is the tripwire.

It never reads the credential's contents - only its path and its git state -
so it is safe to run anywhere and tells an observer nothing about the secret.

Run from the repo root:

    python3 tests/test_credential_files_ignored.py
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CREDENTIAL_PATTERNS = (
    "configuration/vertex-credentials-*.json",
    "configuration/*-credentials*.json",
)

# Names that read like a credential and must never be tracked. Kept as globs
# so this list does not have to know any real filename.
CREDENTIAL_GLOBS = (
    "configuration/vertex-credentials-*.json",
    "configuration/*-credentials*.json",
)


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT)] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


class CredentialIgnoreTests(unittest.TestCase):
    def setUp(self):
        probe = _git("rev-parse", "--git-dir")
        if probe.returncode != 0:
            self.skipTest("not a git checkout: %s" % probe.stderr.strip())
        self.gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    def test_gitignore_carries_the_credential_patterns(self):
        for pattern in CREDENTIAL_PATTERNS:
            self.assertIn(
                pattern, self.gitignore,
                "`.gitignore` lost `%s` - a credential JSON would become "
                "committable again" % pattern)

    def test_every_credential_file_on_disk_is_ignored(self):
        found = [p for glob in CREDENTIAL_GLOBS for p in ROOT.glob(glob)]
        for path in found:
            rel = path.relative_to(ROOT).as_posix()
            ignored = _git("check-ignore", "-q", "--", rel)
            self.assertEqual(
                ignored.returncode, 0,
                "`%s` exists and is NOT ignored (git status shows it as `??`) - "
                "it must never be committable" % rel)

    def test_no_credential_file_is_tracked(self):
        tracked = _git("ls-files").stdout.splitlines()
        offenders = [
            p for p in tracked
            for glob in CREDENTIAL_GLOBS
            if Path(p).match(glob)
        ]
        self.assertEqual(
            offenders, [],
            "credential-shaped file(s) are TRACKED and therefore already "
            "published: %s" % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)
