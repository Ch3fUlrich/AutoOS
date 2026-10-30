#!/usr/bin/env python3
"""AO-SKILL-MCPSETUP-SPLIT review L1/L2: link anchors and skill-path spellings.

L1: tests/test_skill_mcpsetup_split.py's `_missing_links` dropped the "#anchor"
part of a link target, so a link whose file exists but whose anchor matches no
heading read as clean. An anchor must match a GitHub heading slug of the target
file: lowercase, punctuation stripped except - and _, spaces to -, duplicate
headings suffixed -1, -2, ...

L2: `_dead_skill_mentions`' needle ("skills/" + the dir name) missed the dir
spelled without the skills/ prefix (the bare dir + /SKILL.md, the bare dir +
/references/...) and every backslash spelling. The needle is the bare dir name
now, with backslashes normalised to / first.

The skill dir's name is spelled as two fragments below (and inside every
fixture) so this file never matches its own scan in the split test.

Run directly:

    python3 tests/test_skill_mcpsetup_links.py
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

SKILL_DIR_NAME = "mcp-servers-" + "setup"

_SLUG_STRIP_RE = re.compile(r"[^\w\- ]", re.UNICODE)


def heading_slugs(headings):
    """GitHub's slug for each heading line of _headings(): lowercase,
    punctuation stripped except - and _, spaces to -, duplicates suffixed
    -1, -2, ..."""
    slugs, seen = [], {}
    for heading in headings:
        text = re.sub(r"^#{1,6}\s*", "", heading).strip().lower()
        base = _SLUG_STRIP_RE.sub("", text).replace(" ", "-")
        n = seen.get(base, 0)
        seen[base] = n + 1
        slugs.append(base if n == 0 else "%s-%d" % (base, n))
    return slugs


def exists_exact(base, rel):
    """(base / rel).exists(), with rel's spelling matched case-sensitively.

    Windows and macOS filesystems resolve `Serena.md` to serena.md, so a
    mention spelled in the wrong case passes there and is dead on Linux (CI).
    Only the spelled part is checked, one directory listing per component."""
    here = Path(base)
    for part in rel.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            here = here.parent
        elif not here.is_dir() or part not in os.listdir(here):
            return False
        else:
            here = here / part
    return here.exists()


def _helpers():
    """The split test's helpers, imported at call time: that module imports
    heading_slugs from this one at load, so a top-level import here would
    circle."""
    import test_skill_mcpsetup_split as split
    return split


class _Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path


class AnchorSlugTests(_Fixture):
    """L1: a link's #anchor must match a heading slug of its target file."""

    def test_heading_slugs_follow_github_rules(self):
        self.assertEqual(
            heading_slugs(["## Wiring \u2014 user scope only. A, B!",
                           "## Setup", "## Setup", "### a_b-C"]),
            ["wiring--user-scope-only-a-b", "setup", "setup-1", "a_b-c"])

    def test_bad_anchor_in_an_existing_file_is_reported(self):
        """Red first: _missing_links dropped the anchor, so this read clean."""
        self.write("target.md", "# Real Heading\n\ntext\n")
        source = self.write("source.md", "[bad](target.md#no-such-heading)\n")
        self.assertEqual(_helpers()._missing_links(source),
                         ["target.md#no-such-heading"])

    def test_good_anchor_and_duplicate_suffix_pass(self):
        self.write("target.md", "## Setup\n\n## Setup\n\ntext\n")
        source = self.write(
            "source.md", "[1](target.md#setup)\n[2](target.md#setup-1)\n")
        self.assertEqual(_helpers()._missing_links(source), [])

    def test_bad_same_file_anchor_is_reported(self):
        source = self.write("source.md", "# Title\n\n[bad](#missing-heading)\n")
        self.assertEqual(_helpers()._missing_links(source),
                         ["#missing-heading"])

    def test_missing_file_is_still_reported(self):
        source = self.write("source.md", "[x](ghost.md#whatever)\n")
        self.assertEqual(_helpers()._missing_links(source),
                         ["ghost.md#whatever"])


class DeadMentionTests(_Fixture):
    """L2: every spelling of the skill dir is seen - bare dir, backslashes."""

    def dead(self, line):
        return [(token, why) for _lineno, token, why
                in _helpers()._dead_skill_mentions(self.write("doc.md", line))]

    def test_bare_dir_spelling_is_reported(self):
        got = self.dead("see %s/SKILL.md\n" % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got], ["%s/SKILL.md" % SKILL_DIR_NAME])
        self.assertIn("no .agents/ prefix", got[0][1])

    def test_bare_dir_references_spelling_is_reported(self):
        got = self.dead("see %s/references/graphify.md\n" % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got],
                         ["%s/references/graphify.md" % SKILL_DIR_NAME])

    def test_backslash_canonical_spelling_resolves(self):
        self.assertEqual(
            self.dead("see .agents\\skills\\%s\\SKILL.md\n" % SKILL_DIR_NAME),
            [])

    def test_backslash_spelling_with_a_dead_tail_is_reported(self):
        got = self.dead(
            "see .agents\\skills\\%s\\references\\ghost.md\n" % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got],
                         [".agents/skills/%s/references/ghost.md"
                          % SKILL_DIR_NAME])
        self.assertIn("no such file", got[0][1])

    def test_backslash_unprefixed_spelling_is_reported(self):
        got = self.dead("see skills\\%s\\SKILL.md\n" % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got],
                         ["skills/%s/SKILL.md" % SKILL_DIR_NAME])

    def test_canonical_spelling_still_resolves(self):
        self.assertEqual(
            self.dead("see .agents/skills/%s/references/graphify.md\n"
                      % SKILL_DIR_NAME), [])

    def test_the_dir_name_alone_is_a_name_not_a_path(self):
        self.assertEqual(self.dead("the %s skill\n" % SKILL_DIR_NAME), [])

    def test_wrong_case_spelling_is_reported_even_on_a_folding_fs(self):
        """L5: exists() folds case on Windows/macOS; the check must not."""
        got = self.dead("see .agents/skills/%s/references/Graphify.md\n"
                        % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got],
                         [".agents/skills/%s/references/Graphify.md"
                          % SKILL_DIR_NAME])
        (self.dir / SKILL_DIR_NAME).mkdir()
        self.write("%s/SKILL.md" % SKILL_DIR_NAME, "")
        self.assertEqual(self.dead("see %s/SKILL.md\n" % SKILL_DIR_NAME), [])
        got = self.dead("see %s/skill.md\n" % SKILL_DIR_NAME)
        self.assertEqual([t for t, _w in got], ["%s/skill.md" % SKILL_DIR_NAME])


if __name__ == "__main__":
    unittest.main()
