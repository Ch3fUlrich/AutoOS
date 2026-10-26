#!/usr/bin/env python3
"""AO-SKILL-MCPSETUP-SPLIT (D-2144 S7 F1): the mcp-servers-setup split holds.

.agents/skills/mcp-servers-setup/SKILL.md was 619 lines (cap 500). It is now an
index of at most 150 lines: per-server usage moved verbatim into
references/<server>.md (each at most 500 lines) and the Infrastructure/Harbor
section into references/infrastructure-harbor.md. The split must be a move, not
a rewrite: every heading of the pre-split file (the hard-coded list below) has
to survive in exactly one new file, and every relative link the index keeps has
to resolve to a file that exists.

Run directly:

    python3 tests/test_skill_mcpsetup_split.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "mcp-servers-setup"
SKILL = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"

INDEX_MAX_LINES = 150
REFERENCE_MAX_LINES = 500

# Every heading of the 619-line pre-split SKILL.md, verbatim (em dash = \u2014,
# spelled as an escape so this file never depends on its own encoding).
OLD_HEADINGS = [
    "# MCP Servers Setup",
    "## First Action \u2014 Always",
    "## Active MCP Servers",
    "### Serena \u2014 Semantic Code Navigation (LSP)",
    "#### Wiring \u2014 user scope only. Serena is graphify-shaped, not omnigraph-shaped",
    "#### Languages \u2014 declare only what has a call graph",
    "#### Verify with `health-check` \u2014 the failure is invisible without it",
    "#### Worktrees and parallel agents \u2014 three non-negotiable rules",
    "### Omnigraph \u2014 Structured Cross-Project Memory",
    "### Graphify \u2014 Project Graphs",
    "#### Wiring \u2014 ONE definition per machine, cwd-relative",
    "### Superpowers \u2014 Workflow Skills (14 skills)",
    "### Playwright \u2014 Browser Automation (UI validation, E2E)",
    "#### Wiring \u2014 ONE user-scope entry, serves every repo. Transport differs per host.",
    "### Context7 \u2014 Library Documentation",
    "#### Wiring \u2014 global install, for the same reason as Playwright",
    "## Infrastructure",
    "### Harbor \u2014 Private Container Registry",
    "## Project Initialization",
    "## Recommended Workflow",
    "## Troubleshooting",
]

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:")


def _headings(path):
    """Heading lines of `path`, code-fence aware, whitespace-stripped.

    Fences matter: the files carry bash/powershell blocks whose comment lines
    start with `#` and would otherwise read as headings.
    """
    out, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and re.match(r"^#{1,6}\s", line):
            out.append(line.strip())
    return out


def _line_count(path):
    return len(path.read_text(encoding="utf-8").splitlines())


class McpSetupSplitTests(unittest.TestCase):
    def test_index_is_at_most_150_lines(self):
        self.assertLessEqual(
            _line_count(SKILL), INDEX_MAX_LINES,
            "SKILL.md must stay an index of at most %d lines (cap for AO-SKILL-"
            "MCPSETUP-SPLIT)" % INDEX_MAX_LINES)

    def test_every_reference_file_is_at_most_500_lines(self):
        refs = sorted(REFS.glob("*.md"))
        self.assertTrue(refs, "no reference files under %s" % REFS)
        over = [(p.name, _line_count(p)) for p in refs
                if _line_count(p) > REFERENCE_MAX_LINES]
        self.assertEqual(
            over, [],
            "reference file(s) over %d lines: %r" % (REFERENCE_MAX_LINES, over))

    def test_every_old_heading_appears_in_exactly_one_new_file(self):
        """The split was a move: no heading lost, none left duplicated."""
        new_files = [SKILL] + sorted(REFS.glob("*.md"))
        for old in OLD_HEADINGS:
            homes = [p.name for p in new_files if old in _headings(p)]
            self.assertEqual(
                len(homes), 1,
                "old heading %r must live in exactly one new file, found %r"
                % (old, homes))

    def test_every_relative_link_in_the_index_resolves(self):
        text = SKILL.read_text(encoding="utf-8")
        missing = []
        for target in LINK_RE.findall(text):
            if SCHEME_RE.match(target) or target.startswith("#") or target.startswith("/"):
                continue
            if not (SKILL.parent / target.split("#")[0]).exists():
                missing.append(target)
        self.assertEqual(
            missing, [],
            "SKILL.md links to file(s) that do not exist: %r" % missing)


if __name__ == "__main__":
    unittest.main()
