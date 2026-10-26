#!/usr/bin/env python3
"""Guard: the routing-registry skill stays loadable, resolvable and true.

A skill is documentation with a contract: an agent follows its links, runs the
commands it names, and trusts the paths it points at. Each of those rots
silently — a renamed script leaves a command that fails only when somebody
executes it, a moved reference breaks a link nobody clicks, and a folded-in
skill leaves two half-homes for one rule. This asserts the four ways the
routing-registry skill (which folded in and retired its predecessor) can lie:

    * the index budget (SKILL.md <= 150 lines — it must stay an index) and the
      reference budget (each references/*.md <= 500 lines);
    * every relative markdown link resolves to a file that exists;
    * the folded-in predecessor is gone — no skill directory, no relative link
      anywhere in the repo points at it, and no prose mention names it outside
      the allowed history (the exception list in the test below);
    * every script path the skill names exists in the repo.

Run directly:

    python3 tests/test_skill_routing_registry.py
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "routing-registry"
INDEX = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"

INDEX_MAX_LINES = 150
REFERENCE_MAX_LINES = 500

# The removed skill's name, spelled at runtime on purpose: this checker must
# not carry the literal it forbids, or the prose scan would flag the very test
# file that performs it.
NEEDLE = "combo" + "-" + "create"

# Shape of a date as it appears in a dated history document's filename
# (2026-10-01 or 20261001).
DATE_IN_NAME_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{8})(?!\d)")

# [text](target) — the target may carry a #fragment, which is stripped before
# resolution. External links (a scheme) are not repo paths and are skipped.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# A repo-relative script path as the skill may name one: tools/…, configuration/
# …, catalog/…, tests/…, infra/… ending in a script suffix. Data paths are not
# asserted here — several name git-ignored runtime state (logs/routing/…), which
# legitimately does not exist in a checkout.
SCRIPT_RE = re.compile(
    r"(?<![\w./-])(?:tools|configuration|catalog|tests|infra)"
    r"/[A-Za-z0-9._/-]*\.(?:py|sh|ps1|psm1|mjs)\b")

MD_DIRS_SKIP = {".git", "node_modules", "third_party", "__pycache__"}


def _skill_files():
    """SKILL.md plus every reference page."""
    return ([INDEX] if INDEX.is_file() else []) + sorted(REFERENCES.glob("*.md"))


def _links(path):
    """Every relative markdown link target in `path` (fragment stripped)."""
    text = path.read_text(encoding="utf-8")
    return [t.split("#", 1)[0] for t in LINK_RE.findall(text)
            if not SCHEME_RE.match(t) and t.split("#", 1)[0]]


def _script_paths(path):
    """Every repo-relative script path named in `path`."""
    return sorted(set(SCRIPT_RE.findall(path.read_text(encoding="utf-8"))))


def _repo_markdown():
    for p in ROOT.rglob("*.md"):
        if not MD_DIRS_SKIP.intersection(p.parts):
            yield p


def _tracked_files():
    """Every git-tracked file, repo-relative — untracked scratch is out of scope.

    The index is the only thing that separates tracked history from scratch
    lying around the checkout, and the scan must not read the latter. A
    Windows-created worktree's `.git` pointer holds a `C:/...` gitdir that
    Linux git joins against the worktree and fails on, so when the pointer is
    a Windows path and its `/mnt/<drive>/` form exists, that form is passed
    explicitly as `--git-dir`.
    """
    git_dir = None
    dot = ROOT / ".git"
    if dot.is_file() and "gitdir:" in dot.read_text(
            encoding="utf-8", errors="replace"):
        git_dir = dot.read_text(encoding="utf-8", errors="replace").split(
            "gitdir:", 1)[1].strip()
        if re.match(r"[A-Za-z]:[\\/]", git_dir):
            posix = "/mnt/%s/%s" % (git_dir[0].lower(),
                                    git_dir[3:].replace("\\", "/"))
            if Path(posix).is_dir():
                git_dir = posix
    cmd = ["git"] + (["--git-dir", git_dir] if git_dir else []) \
        + ["ls-files", "-z"]
    out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, check=True)
    return [Path(p) for p in out.stdout.decode("utf-8", "replace").split("\0")
            if p]


class RoutingRegistrySkillTests(unittest.TestCase):
    def test_index_is_at_most_150_lines(self):
        self.assertTrue(INDEX.is_file(), "missing %s" % INDEX)
        lines = len(INDEX.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            lines, INDEX_MAX_LINES,
            "SKILL.md is %d lines (max %d): the index must stay an index, with "
            "the substance in references/" % (lines, INDEX_MAX_LINES))

    def test_every_reference_is_at_most_500_lines(self):
        refs = sorted(REFERENCES.glob("*.md"))
        self.assertTrue(refs, "no references/*.md under %s" % REFERENCES)
        over = ["%s:%d" % (p.name, len(p.read_text(encoding="utf-8").splitlines()))
                for p in refs
                if len(p.read_text(encoding="utf-8").splitlines())
                > REFERENCE_MAX_LINES]
        self.assertEqual(
            over, [],
            "reference page(s) over %d lines: %s" % (REFERENCE_MAX_LINES,
                                                     ", ".join(over)))

    def test_every_relative_link_resolves(self):
        broken = ["%s -> %s" % (p.relative_to(ROOT), t)
                  for p in _skill_files()
                  for t in _links(p)
                  if not (p.parent / t).exists()]
        self.assertEqual(
            broken, [],
            "dangling relative link(s) in the routing-registry skill: %s"
            % "; ".join(broken))

    def test_combo_create_is_gone(self):
        self.assertFalse(
            (ROOT / ".agents" / "skills" / NEEDLE).exists(),
            ".agents/skills/%s still exists; routing-registry "
            "replaced it and must be its only home" % NEEDLE)
        stale = ["%s -> %s" % (p.relative_to(ROOT), t)
                 for p in _repo_markdown()
                 for t in _links(p)
                 if NEEDLE in t]
        self.assertEqual(
            stale, [],
            "relative link(s) still point at the removed %s skill: %s"
            % (NEEDLE, "; ".join(stale)))
        # A prose mention is a pointer too. The allowed exceptions, listed
        # explicitly and valid nowhere else: CHANGELOG.md (the entry that
        # removed the skill may name it); a dated history doc — a file whose
        # name carries a date (2026-10-01 or 20261001); anything under logs/
        # (run logs, handoff session records).
        mentions = [
            str(rel) for rel in _tracked_files()
            if (ROOT / rel).is_file()
            and NEEDLE.encode("ascii") in (ROOT / rel).read_bytes()
            and rel.name != "CHANGELOG.md"
            and not DATE_IN_NAME_RE.search(rel.name)
            and rel.parts[:1] != ("logs",)]
        self.assertEqual(
            mentions, [],
            "prose mention(s) of the removed %s skill outside the allowed "
            "history (CHANGELOG.md / dated history docs / logs/): %s"
            % (NEEDLE, ", ".join(mentions)))

    def test_every_script_path_named_exists(self):
        missing = ["%s: %s" % (p.relative_to(ROOT), s)
                   for p in _skill_files()
                   for s in _script_paths(p)
                   if not (ROOT / s).is_file()]
        self.assertEqual(
            missing, [],
            "script path(s) named by the skill do not exist in the repo: %s"
            % "; ".join(missing))


if __name__ == "__main__":
    unittest.main(verbosity=2)
