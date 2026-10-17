#!/usr/bin/env python3
"""Guard: a real GCP project id may not ship in a public repository (AGENTS Hard Rule 1).

Why
---
The repository is public. The operator's GCP project id leaked onto main through
the *documentation* of the Vertex credential path: a registry `$comment`, two
script headers and one handoff note named the credential file
`configuration/vertex-credentials-<project>-<keyid>.json` with the real project
id and the real service-account key id spelled out. A project id is not a
password, but it is a lookup key — it names the account, it appears in IAM
bindings, in service-account e-mails (`*@<project>.iam.gserviceaccount.com`)
and in support tickets, and the credential's filename is the one thing an
attacker needs to go look for it. History rewrite is out of scope here; the
point is that no new one may land.

Two rules over every tracked text file:

1. A GCP project-id shape — a lowercase name then a hyphen then exactly 6
   digits, which is how GCP mints ids — sitting on a line that talks about a
   project / GCP / vertex / a service account is a leak. Documentation writes
   `<gcp-project>`; nothing real belongs here.
2. `@<x>.iam.gserviceaccount.com` with a non-placeholder `<x>` is a service
   account address, which embeds the project id verbatim.

Placeholders (`<...>`, `{{...}}`, `$VAR`) are clean by construction: they carry
no 6-digit suffix, and rule 2 refuses to bind a placeholder label to the domain.

The scan is line-scoped, which is what makes it both cheap and false-positive
free: the timestamped backup names this repository writes
(`*.autoos-backup-<date>-<time>`) share the shape but never share a line with
the vocabulary.

Run from the repo root:

    python3 tests/test_public_ids.py
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# This file necessarily carries the vocabulary and the shapes it hunts.
SELF = "tests/test_public_ids.py"

PROJECT_SHAPE = re.compile(r"\b[a-z][a-z0-9-]{4,28}-[0-9]{6}\b")

# The vocabulary that makes a bare `<name>-<6 digits>` a project id rather than
# a timestamped filename.
CONTEXT = re.compile(r"project|\bgcp\b|vertex|iam\.gserviceaccount", re.I)

# Service-account addresses: the label left of .iam.gserviceaccount.com IS a
# project id. Placeholder characters are captured so they can be judged clean.
SERVICE_ACCOUNT = re.compile(r"@([\w<>$.{}-]{1,63})\.iam\.gserviceaccount\.com")

PLACEHOLDER_TOKEN = re.compile(r"<[^>]*>|\{\{[^}]*\}\}|\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]*\}")


def tracked_files(root=ROOT):
    """Every tracked path, NUL-separated so odd names survive."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def read_text(path):
    """File text, or None when it is binary / unreadable (never scanned)."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def find_leaks(text):
    """[(line_no, matched_text, rule)] for one file's text."""
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if CONTEXT.search(line):
            for m in PROJECT_SHAPE.finditer(line):
                found.append((lineno, m.group(0), "project-id shape"))
        for m in SERVICE_ACCOUNT.finditer(line):
            if not PLACEHOLDER_TOKEN.search(m.group(1)):
                found.append((lineno, m.group(0), "service-account address"))
    return found


def scan(root=ROOT, files=None):
    """Scan tracked text files; returns [(rel_path, line_no, match, rule)]."""
    paths = tracked_files(root) if files is None else files
    violations = []
    for rel in paths:
        if rel == SELF:
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        for lineno, match, rule in find_leaks(text):
            violations.append((rel, lineno, match, rule))
    return violations


class TreeGuardTests(unittest.TestCase):
    def test_no_real_gcp_project_id_in_tracked_files(self):
        v = scan()
        self.assertEqual(v, [], "real GCP project id / service-account address in "
                                "public tracked files:\n%s" %
                         "\n".join("%s:%d %r (%s)" % t for t in v))


class MatcherTests(unittest.TestCase):
    """The rules are proven against synthetic content, never a real id."""

    # Built by concatenation so this file's own lines cannot trip the scan.
    SHAPE = "sample" + "-" + "project" + "-" + "1" * 6

    def test_a_project_shape_on_project_vocabulary_is_a_leak(self):
        line = "the operator's GCP project id is %s" % self.SHAPE
        self.assertTrue(find_leaks(line), line)

    def test_a_bare_shape_without_the_vocabulary_is_not_flagged(self):
        line = "wrote settings.json.autoos-backup-20260101-000000 first"
        self.assertEqual(find_leaks(line), [])

    def test_a_placeholder_credential_path_is_clean(self):
        line = ("credential file configuration/vertex-credentials-<gcp-project>"
                "-<keyid>.json is git-ignored")
        self.assertEqual(find_leaks(line), [])

    def test_a_service_account_with_a_real_project_is_a_leak(self):
        line = "sa = runner@%s.iam.gserviceaccount.com" % self.SHAPE
        hits = find_leaks(line)
        self.assertTrue(any(r == "service-account address" for _, _, r in hits), hits)

    def test_a_service_account_with_a_placeholder_project_is_clean(self):
        line = "sa = runner@<gcp-project>.iam.gserviceaccount.com"
        self.assertEqual(find_leaks(line), [])

    def test_the_domain_alone_is_not_a_leak(self):
        line = "service accounts end in .iam.gserviceaccount.com"
        self.assertEqual(find_leaks(line), [])

    def test_a_planted_leak_is_caught_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("project %s\n" % self.SHAPE, encoding="utf-8")
            (root / "b.md").write_text("project <gcp-project>\n", encoding="utf-8")
            v = scan(root=root, files=["a.md", "b.md"])
            self.assertEqual([t[0] for t in v], ["a.md"], v)

    def test_binary_and_unreadable_files_are_skipped_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blob.bin").write_bytes(b"\xff\xfe\x00project-1" + b"1" * 5)
            self.assertEqual(scan(root=root, files=["blob.bin"]), [])


if __name__ == "__main__":
    unittest.main()
