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
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "mcp-servers-setup"
SKILL = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"

INDEX_MAX_LINES = 150
REFERENCE_MAX_LINES = 500

# Content-proof floor: the heading check alone cannot see a truncated body.
# The pre-split SKILL.md (measured at the split's parent commit, 9b5adb19^) was
# 619 lines total, 518 of them non-blank. Index + references together must stay
# at or above those counts minus a small slack for link lines rewritten during
# the split (a re-wrapped link is legitimate; a dropped section is not).
OLD_TOTAL_LINES = 619
OLD_NONBLANK_LINES = 518
LINK_REWRITE_SLACK = 12

# Per-file floors (fix round 2, 2026-10-26): the aggregate floor above lets one
# file absorb another's truncation — measured at 658 total / 546 non-blank it
# passed a 40-line truncation. Each file is pinned at its own non-blank count
# at pin time minus at most 2 lines of slack (one re-wrapped link). Counts in
# the comments are the pin-time values.
PER_FILE_NONBLANK_FLOORS = {
    "SKILL.md": 68,                  # 70 non-blank
    "context7.md": 15,               # 17
    "graphify.md": 96,               # 98 (re-pin 2026-10-29, MCPSETUP-WORKTREE-GRAPHIFY)
    "infrastructure-harbor.md": 40,  # 42
    "omnigraph.md": 46,              # 48
    "playwright.md": 81,             # 83
    "serena.md": 163,                # 165 (re-pin 2026-10-29, MCPSETUP-WORKTREE-GRAPHIFY: link block removed; was 177)
    "superpowers.md": 14,            # 16
}

# Path mentions that must resolve from the repo root (fix round 2): the skill
# lives under .agents/skills/, so a mention of its directory without that
# prefix is a dead path for anyone reading from the repo root. The needle is
# built by concatenation so this file never matches its own scan.
SKILL_PATH_NEEDLE = "skills/" + "mcp-servers-setup"
PATH_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._/-")

# Topics whose guidance lives in references/<topic>.md since the split (fix
# round 3): a pointer naming one of these topics must target that topic's
# reference file. The SKILL.md such pointers kept naming is an index now, so
# a reader following one lands a hop short of the guidance it names.
TOPIC_REFERENCES = {
    "context7": "references/context7.md",
    "graphify": "references/graphify.md",
    "omnigraph": "references/omnigraph.md",
    "playwright": "references/playwright.md",
    "serena": "references/serena.md",
    "superpowers": "references/superpowers.md",
}
TOPIC_WORD_RE = re.compile(
    r"\b(%s)\b" % "|".join(sorted(TOPIC_REFERENCES)), re.IGNORECASE)
# A link's anchor text can name topics the pointer does not target (a serena
# anchor reading "...-is-graphify-shaped-not-omnigraph-shaped"); strip anchors
# before matching so those do not read as stray topic pointers.
ANCHOR_RE = re.compile(r"#[^\s)\"]*")

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


def _nonblank_count(path):
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


def _missing_links(path):
    """Relative link targets of `path` that do not resolve to a file."""
    missing = []
    for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        if SCHEME_RE.match(target) or target.startswith("#") or target.startswith("/"):
            continue
        if not (path.parent / target.split("#")[0]).exists():
            missing.append(target)
    return missing


def _tracked_files():
    """Files git tracks, resolved against ROOT — the scope of the scan below.

    Plain `git ls-files` first. When the checkout is a worktree created on
    Windows and the suite runs under WSL, the worktree's .git file names its
    gitdir with a drive letter, which POSIX git reads as a relative path and
    rejects. Translate the drive letter to its /mnt/<drive> home and retry
    with the gitdir named explicitly.
    """
    args = ["ls-files", "-z"]
    out = subprocess.run(["git"] + args, cwd=str(ROOT), capture_output=True)
    if out.returncode != 0:
        gitfile = ROOT / ".git"
        match = re.match(
            r"gitdir:\s*([A-Za-z]):[/\\](.*)",
            gitfile.read_text(encoding="utf-8") if gitfile.is_file() else "")
        if not match or os.name == "nt":
            raise RuntimeError(
                "git ls-files failed: %s" % out.stderr.decode("utf-8", "replace"))
        gitdir = "/mnt/%s/%s" % (match.group(1).lower(),
                                 match.group(2).replace("\\", "/"))
        out = subprocess.run(
            ["git", "--git-dir=" + gitdir, "--work-tree=" + str(ROOT)] + args,
            cwd=str(ROOT), capture_output=True)
        if out.returncode != 0:
            raise RuntimeError(
                "git ls-files failed: %s" % out.stderr.decode("utf-8", "replace"))
    return [ROOT / rel for rel in out.stdout.decode("utf-8").split("\0") if rel]


def _skill_mentions(path):
    """Scan records for every skill-path mention in `path` (binary: []).

    A record is (lineno, token, tail, prefixed, named): the path token as
    spelled, the part of it after "mcp-servers-setup/" ("" when the mention
    names only the directory, trailing sentence dots stripped), whether the
    token carries the .agents/ prefix the real tree has, and the topic words
    named by the line (link anchors stripped) or by the file's own path.
    """
    data = path.read_bytes()
    if b"\0" in data:
        return []
    rel = str(path.relative_to(ROOT)).replace(os.sep, "/")
    file_topics = {t.lower() for t in TOPIC_WORD_RE.findall(rel)}
    out = []
    for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        named = file_topics | {
            t.lower() for t in TOPIC_WORD_RE.findall(ANCHOR_RE.sub("", line))}
        start = 0
        while True:
            i = line.find(SKILL_PATH_NEEDLE, start)
            if i < 0:
                break
            start = i + 1
            lo = i
            while lo > 0 and line[lo - 1] in PATH_CHARS:
                lo -= 1
            hi = i + len(SKILL_PATH_NEEDLE)
            while hi < len(line) and line[hi] in PATH_CHARS:
                hi += 1
            token = line[lo:hi]
            parts = token.split("mcp-servers-setup/", 1)
            tail = parts[1].rstrip(".") if len(parts) == 2 else ""
            out.append((lineno, token, tail,
                        line[:i].endswith(".agents/"), named))
    return out


def _dead_skill_mentions(path):
    """(lineno, token, why) triples for skill-path mentions a reader cannot
    follow from the repo root.

    Round 2 required the .agents/ prefix the real tree has. Round 3 tightens
    resolution: the part of the mention after "mcp-servers-setup/" must name
    a file that exists under the skill dir. The old check accepted any token
    that existed at the repo root, so a wrong skill path passed whenever some
    unrelated path existed - and a prefixed mention was never checked at all.
    """
    hits = []
    for lineno, token, tail, prefixed, _named in _skill_mentions(path):
        if not prefixed:
            hits.append((lineno, token,
                         "no .agents/ prefix - dead from the repo root"))
        elif not (SKILL_DIR / tail).exists():
            hits.append((lineno, token,
                         "no such file under %s"
                         % SKILL_DIR.relative_to(ROOT)))
    return hits


def _topic_pointer_problems(path):
    """(lineno, token, why) triples for pointers that name one of the topics
    whose guidance lives in references/<topic>.md yet do not target it.

    A pointer naming topic guidance must name that topic's reference file as
    its target; naming several topics accepts any one of their references.
    Topics come from the pointer's line and the file's own path - a pointer
    inside graphify-mcp is Graphify guidance even when the line omits the word.
    """
    hits = []
    for lineno, token, tail, _prefixed, named in _skill_mentions(path):
        if named and tail not in {TOPIC_REFERENCES[t] for t in named}:
            hits.append((lineno, token,
                         "names %s guidance but targets %s"
                         % ("/".join(sorted(named)), tail or "the skill dir")))
    return hits


class McpSetupSplitTests(unittest.TestCase):
    def test_index_is_at_most_150_lines(self):
        self.assertLessEqual(
            _line_count(SKILL), INDEX_MAX_LINES,
            "SKILL.md must stay an index of at most %d lines (cap for AO-SKILL-"
            "MCPSETUP-SPLIT)" % INDEX_MAX_LINES)

    def test_every_reference_file_is_at_most_500_lines(self):
        refs = sorted(REFS.rglob("*.md"))
        self.assertTrue(refs, "no reference files under %s" % REFS)
        over = [(p.name, _line_count(p)) for p in refs
                if _line_count(p) > REFERENCE_MAX_LINES]
        self.assertEqual(
            over, [],
            "reference file(s) over %d lines: %r" % (REFERENCE_MAX_LINES, over))

    def test_every_old_heading_appears_in_exactly_one_new_file(self):
        """The split was a move: no heading lost, none left duplicated."""
        new_files = [SKILL] + sorted(REFS.rglob("*.md"))
        for old in OLD_HEADINGS:
            homes = [p.name for p in new_files if old in _headings(p)]
            self.assertEqual(
                len(homes), 1,
                "old heading %r must live in exactly one new file, found %r"
                % (old, homes))

    def test_every_relative_link_in_the_index_resolves(self):
        self.assertEqual(
            _missing_links(SKILL), [],
            "SKILL.md links to file(s) that do not exist")

    def test_every_relative_link_in_a_reference_resolves(self):
        for ref in sorted(REFS.rglob("*.md")):
            self.assertEqual(
                _missing_links(ref), [],
                "%s links to file(s) that do not exist" % ref.relative_to(ROOT))

    def test_every_reference_file_is_linked_from_the_index(self):
        """No reference file may be orphaned from the SKILL.md index."""
        linked = set()
        for target in LINK_RE.findall(SKILL.read_text(encoding="utf-8")):
            if SCHEME_RE.match(target) or target.startswith("#") or target.startswith("/"):
                continue
            linked.add((SKILL.parent / target.split("#")[0]).resolve())
        orphans = [str(p.relative_to(ROOT)) for p in sorted(REFS.rglob("*.md"))
                   if p.resolve() not in linked]
        self.assertEqual(
            orphans, [],
            "reference file(s) not linked from SKILL.md: %r" % orphans)

    def test_the_split_did_not_lose_content(self):
        """Line-count floors on top of the heading check (see constants above)."""
        files = [SKILL] + sorted(REFS.rglob("*.md"))
        total = sum(_line_count(p) for p in files)
        nonblank = sum(_nonblank_count(p) for p in files)
        self.assertGreaterEqual(
            total, OLD_TOTAL_LINES - LINK_REWRITE_SLACK,
            "SKILL.md + references hold %d lines; the pre-split file held %d "
            "(slack %d) — content was lost" % (
                total, OLD_TOTAL_LINES, LINK_REWRITE_SLACK))
        self.assertGreaterEqual(
            nonblank, OLD_NONBLANK_LINES - LINK_REWRITE_SLACK,
            "SKILL.md + references hold %d non-blank lines; the pre-split file "
            "held %d (slack %d) — content was lost" % (
                nonblank, OLD_NONBLANK_LINES, LINK_REWRITE_SLACK))

    def test_each_file_keeps_its_own_nonblank_floor(self):
        """Per-file floors: the aggregate check lets one file absorb another's
        truncation, so pin SKILL.md and every reference at its own non-blank
        count minus at most 2 (PER_FILE_NONBLANK_FLOORS)."""
        actual = {"SKILL.md": SKILL}
        actual.update({p.name: p for p in REFS.rglob("*.md")})
        self.assertEqual(
            sorted(actual), sorted(PER_FILE_NONBLANK_FLOORS),
            "SKILL.md and every references/*.md need a pinned non-blank floor")
        low = {}
        for name in sorted(actual):
            count = _nonblank_count(actual[name])
            if count < PER_FILE_NONBLANK_FLOORS[name]:
                low[name] = count
        self.assertEqual(
            low, {},
            "file(s) below their pinned non-blank floor: %r (floors %r)" % (
                low, PER_FILE_NONBLANK_FLOORS))

    def test_no_dead_skill_paths_in_tracked_files(self):
        """Every skill-path mention in a tracked text file must resolve from
        the repo root (fix round 2): prose spelled the skill's home without the
        leading .agents/ segment the real tree has, a dead path for any reader
        following it from the root. The scan covers all tracked files, not just
        the skill's own — the bad spelling spread into infra docs, a script and
        a config comment. Round 3 tightens resolution to the skill dir itself:
        the mention's own tail must name a file there, because the old check
        accepted any path that existed at the repo root, so a wrong skill-path
        mention passed whenever some unrelated path existed."""
        dead = {}
        for path in _tracked_files():
            if not path.is_file():
                continue
            hits = _dead_skill_mentions(path)
            if hits:
                dead[str(path.relative_to(ROOT))] = hits
        self.assertEqual(
            dead, {},
            "skill-path mention(s) that do not resolve under the skill dir: "
            "point at .agents/skills/mcp-servers-setup/<file> as it exists "
            "there, with the .agents/ prefix - %r" % dead)

    def test_topic_pointers_target_the_topic_reference(self):
        """A pointer naming one of the six per-server topics must target that
        topic's references/<topic>.md (fix round 3): the index such pointers
        kept naming after the split is a table of contents, so a reader lands
        one hop short of the guidance the pointer claims to name. Topics are
        read from the pointer's line (link anchors stripped) and from the
        file's own path — a pointer inside graphify-mcp is Graphify guidance
        even when the line itself omits the word."""
        bad = {}
        for path in _tracked_files():
            if not path.is_file():
                continue
            hits = _topic_pointer_problems(path)
            if hits:
                bad[str(path.relative_to(ROOT))] = hits
        self.assertEqual(
            bad, {},
            "pointer(s) naming topic guidance but not targeting that "
            "topic's reference file under the skill dir: %r" % bad)


if __name__ == "__main__":
    unittest.main()
