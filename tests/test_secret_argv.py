#!/usr/bin/env python3
"""Guard: a secret value may never be expanded into a command's argv (AO-SECRET-ARGV, D-962).

Why
---
`docker run -e OMNIGRAPH_TOKEN="$TOKEN"` and `"args": ["-e", "OMNIGRAPH_TOKEN=${TOKEN}"]`
look equivalent to the names-only form, but they are not: the shell expands the
variable **before** docker is exec'd, so the secret's value lands in the child's
command line. `/proc/<pid>/cmdline` — and therefore `ps`, `top`, and
`Get-Process` — is world-readable on every Unix this repository installs to, so
any unprivileged process on the machine can read a bearer token off a running
`docker run`. The environment of that same process is not world-readable.

The rule is therefore: pass **names only**. Either `-e NAME` (docker inherits the
value from the caller's environment) or an `env` / `environment` block, which is
exactly how the root `.mcp.json` and every compose file here already do it.

What is NOT an offense
----------------------
* `-e NAME` — name only, value inherited.
* compose `environment:` list entries — `- NAME=${VAR}` is read by compose from
  the file, never exec'd into an argv.
* an `env` block — `"OMNIGRAPH_TOKEN": "${OMNIGRAPH_TOKEN}"` (colon-separated,
  no `=` after the name).
* a placeholder — `-e NAME=<bearer>` carries no expansion, so it teaches nothing
  that can leak. The docs that show it are still asked to move to the names-only
  form, but this gate hunts the shape that *runs*, not the shape that is quoted.

Ratchet
-------
`BASELINE` lists the pre-existing hits this lane was not scoped to touch, keyed by
(path, matched text) with a count. A new hit anywhere fails; so does a baseline
entry that no longer matches, which is what forces the entries to be deleted as
each owning lane fixes its file.

Run from the repo root:

    python3 tests/test_secret_argv.py
    python3 -m pytest -q tests/test_secret_argv.py
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The scan is scoped to the directories that ship commands to a real machine.
SCAN_DIRS = ("infra", ".agents", "tools", "configuration")
SCAN_EXTS = (".sh", ".ps1", ".json", ".jsonc", ".md")

# An environment-variable name that reads like a secret. Uppercase, because
# every offender in this tree is a shell/PowerShell variable name.
SECRET_NAME = "TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL"

# `-e NAME=<expansion>` or `"NAME=<expansion>"`, where the value begins with a
# variable expansion (`$`, `${`, or a backslash-escaped one inside a template
# string). An assignment prefix (`NAME="$OTHER" docker run`) is not matched: it
# reaches the child through the environment, not through argv.
OFFENSE = re.compile(
    r"""(?:-e[ \t]+["']?|["'])                     # a -e flag, or a quoted argv string
        (?P<name>[A-Z][A-Z0-9_]*)                  # the env var name ...
        =                                          # ... assigned inline
        ["']?\\?\$\{?[A-Za-z0-9_]*\}?              # ... to an expanded value
    """,
    re.VERBOSE,
)

# Pre-existing hits, owned by other lanes. Every entry must still be live, or the
# last test fails and the entry has to go.
BASELINE = {
    ("infra/mcp-servers/omnigraph-setup/omnigraph-sync.sh", '-e "OMNIGRAPH_BEARER_TOKEN=$1'): 2,
    ("infra/mcp-servers/omnigraph-setup/setup-agent-memory.sh", '-e "OMNIGRAPH_TOKEN=$TOKEN'): 1,
    ("infra/mcp-servers/omnigraph-setup/setup-agent-memory.ps1", '-e "OMNIGRAPH_TOKEN=$token'): 1,
    ("infra/mcp-servers/omnigraph-setup/sync-windows.ps1", '-e "OMNIGRAPH_BEARER_TOKEN=$token'): 2,
}


def tracked_text_files(root=ROOT):
    """Tracked paths under SCAN_DIRS with a scanned extension, NUL-safe."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    paths = []
    for rel in out.split("\0"):
        if not rel:
            continue
        if not rel.startswith(tuple(d + "/" for d in SCAN_DIRS)):
            continue
        if not rel.endswith(SCAN_EXTS):
            continue
        paths.append(rel)
    return paths


def scan_text(rel, text):
    """(line_no, matched_text) for every secret expanded into argv in `text`."""
    hits = []
    for num, line in enumerate(text.splitlines(), 1):
        for m in OFFENSE.finditer(line):
            name = m.group("name")
            if any(word in name for word in SECRET_NAME.split("|")):
                hits.append((num, m.group(0)))
    return hits


def tree_offenses(root=ROOT):
    """Every offense in the tree: list of (rel, line_no, match, first/last)."""
    found = []
    for rel in tracked_text_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for num, match in scan_text(rel, text):
            found.append((rel, num, match))
    return found


def summarize(items):
    return "\n".join("  %s:%d: %s" % (rel, num, match) for rel, num, match in items)


class SecretArgvTreeTests(unittest.TestCase):
    def test_no_secret_is_expanded_into_argv(self):
        """The shipped commands never put a secret's value into an argv."""
        extra = []
        seen = {}
        for rel, num, match in tree_offenses():
            key = (rel, match)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > BASELINE.get(key, 0):
                extra.append((rel, num, match))
        self.assertEqual(
            extra, [],
            "secrets expanded into a command argv are readable by any local user via ps; "
            "pass env var NAMES only (`-e NAME`, or an env / environment block).\n"
            + summarize(extra))

    def test_baseline_entries_are_still_live(self):
        """A fixed file must have its BASELINE entry deleted, not left to rot."""
        live = {}
        for rel, _num, match in tree_offenses():
            live[(rel, match)] = live.get((rel, match), 0) + 1
        stale = [key for key in BASELINE if live.get(key, 0) < BASELINE[key]]
        self.assertEqual(
            stale, [],
            "BASELINE rows no longer match the tree — that file is fixed; delete the row "
            "so the gate starts refusing it again:\n  " + "\n  ".join(map(str, stale)))


class SecretArgvDetectorTests(unittest.TestCase):
    """The detector itself, on synthetic text — a gate that silently stopped
    matching would otherwise turn the tree test above into a no-op."""

    def test_flags_the_argv_forms(self):
        cases = {
            'docker run -e OMNIGRAPH_BEARER_TOKEN="$OMNIGRAPH_TOKEN" img': 1,
            "docker run -e AWS_SECRET_ACCESS_KEY='$MINIO_ROOT_PASSWORD' img": 1,
            'docker run --rm -e "OMNIGRAPH_TOKEN=$token" $IMAGE': 1,
            '-e AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER" -e AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"': 2,
            '"args": ["run", "-i", "-e", "OMNIGRAPH_TOKEN=${OMNIGRAPH_TOKEN}"]': 1,
            '"OMNIGRAPH_TOKEN=${OMNIGRAPH_TOKEN}",': 1,
            '"OMNIGRAPH_TOKEN=\\${OMNIGRAPH_TOKEN}",': 1,
            '& docker run -e "API_KEY=$env:API_KEY" img': 1,
        }
        for text, want in cases.items():
            self.assertEqual(len(scan_text("x.sh", text)), want, text)

    def test_the_allowed_forms_pass(self):
        cases = (
            'docker run -e OMNIGRAPH_BEARER_TOKEN -e AWS_ACCESS_KEY_ID img',
            '"args": ["run", "-i", "--rm", "-e", "OMNIGRAPH_TOKEN", "img"]',
            '"env": { "OMNIGRAPH_TOKEN": "${OMNIGRAPH_TOKEN}" },',
            '- OMNIGRAPH_SERVER_BEARER_TOKEN=${OMNIGRAPH_TOKEN:?set in .env.shared}',
            '- AWS_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}',
            'OMNIGRAPH_BEARER_TOKEN="$OMNIGRAPH_TOKEN" docker run -e OMNIGRAPH_BEARER_TOKEN img',
            'docker run --rm -i -e OMNIGRAPH_TOKEN=<bearer> omnigraph-mcp:latest',
            'export AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER"',
            '# use -e NAME and keep the value in the environment, never in argv',
            'echo "REQUIRE_API_KEY=true is set in $omni_env (skipped)."',
            'to_append+=("REQUIRE_API_KEY=true")',
            'echo "OMNIGRAPH_TOKEN is not set"',
            '"PERPLEXITY_API_KEY": "YOUR_API_KEY_HERE",',
        )
        for text in cases:
            self.assertEqual(scan_text("x.sh", text), [], text)

    def test_only_secret_shaped_names_count(self):
        self.assertEqual(
            scan_text("x.sh", 'docker run -e OMNIGRAPH_GRAPH_ID=${GRAPH} -e S3_BUCKET=$b img'), [])

    def test_scan_dirs_and_extensions_are_the_shipped_ones(self):
        files = tracked_text_files()
        self.assertTrue(files, "the scan found no tracked file at all — the scope is broken")
        self.assertTrue(any(f.endswith(".sh") for f in files), files[:5])
        self.assertTrue(any(f.endswith(".ps1") for f in files), files[:5])
        self.assertTrue(any(f.endswith(".md") for f in files), files[:5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
