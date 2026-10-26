#!/usr/bin/env python3
"""Guard: the routing-registry skill stays loadable, resolvable and true.

A skill is documentation with a contract: an agent follows its links, runs the
commands it names, and trusts the paths it points at. Each of those rots
silently — a renamed script leaves a command that fails only when somebody
executes it, a moved reference breaks a link nobody clicks, and a folded-in
skill leaves two half-homes for one rule. This asserts the four ways the
routing-registry skill (which folded in and retired combo-create) can lie:

    * the index budget (SKILL.md <= 150 lines — it must stay an index) and the
      reference budget (each references/*.md <= 500 lines);
    * every relative markdown link resolves to a file that exists;
    * combo-create is gone — no skill directory, and no relative link anywhere
      in the repo still points at it;
    * every script path the skill names exists in the repo.

Run directly:

    python3 tests/test_skill_routing_registry.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "routing-registry"
INDEX = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"

INDEX_MAX_LINES = 150
REFERENCE_MAX_LINES = 500

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
            (ROOT / ".agents" / "skills" / "combo-create").exists(),
            ".agents/skills/combo-create still exists; routing-registry "
            "replaced it and must be its only home")
        stale = ["%s -> %s" % (p.relative_to(ROOT), t)
                 for p in _repo_markdown()
                 for t in _links(p)
                 if "combo-create" in t]
        self.assertEqual(
            stale, [],
            "relative link(s) still point at the removed combo-create skill: %s"
            % "; ".join(stale))

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
