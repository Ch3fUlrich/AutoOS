#!/usr/bin/env python3
"""Guard: the routing-registry skill stays loadable, resolvable and true.

A skill is documentation with a contract: an agent follows its links, runs the
commands it names, and trusts the paths it points at. Each of those rots
silently — a renamed script leaves a command that fails only when somebody
executes it, a moved reference breaks a link nobody clicks, and a folded-in
skill leaves two half-homes for one rule. This file asserts the filesystem
ways the routing-registry skill (which folded in and retired its predecessor)
can lie:

    * the index budget (SKILL.md <= 150 lines — it must stay an index) and the
      reference budget (each references/*.md <= 500 lines);
    * every relative markdown link resolves to a file that exists;
    * the folded-in predecessor is gone — no skill directory, no relative link
      anywhere in the repo points at it, and no prose mention names it outside
      the allowed history (the exception list in the test below);
    * every backticked repo path is copyable — a path wrapped across a line
      break evades the script-path guard below and is not a usable path;
    * every script path the skill names exists in the repo;
    * the add/modify and remove paths lose a required step while every file and
      link still resolves — each step's heading/keyword must be present.

The command ways — an apply.sh / apply.ps1 invocation naming a flag the
script's own argument handling refuses, and a `render <target> --out <path>`
claim naming the file that target's own `render <target> --help` calls "never
the real ..." — live in tests/test_skill_routing_registry_commands.py; shared
constants and helpers live in tests/routing_registry_skill_lib.py.

Run directly:

    python3 tests/test_skill_routing_registry.py
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from routing_registry_skill_lib import (  # noqa: E402
    DATE_IN_NAME_RE, INDEX, INDEX_MAX_LINES, NEEDLE, REFERENCES,
    REFERENCE_MAX_LINES, REQUIRED_STEPS, ROOT, _links, _repo_markdown,
    _script_paths, _skill_files, _tracked_files, _wrapped_repo_paths)

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

    def test_backticked_repo_paths_do_not_wrap(self):
        """Wrapped paths evade SCRIPT_RE and are not copyable repo paths.

        A wrap hides inside one backtick span or between two spans whose
        join is one path, the break may carry a list or quote marker, and
        fenced blocks must not shift span pairing. One fixture string per
        form is pinned first — a guard that cannot flag these asserts
        nothing — and the real skill must stay clean."""
        fixtures = [
            ("one span", "run `configuration/\nomniroute/apply.ps1`"),
            ("one span, list marker",
             "run `configuration/\n- omniroute/apply.ps1`"),
            ("one span, quote marker",
             "run `configuration/\n> omniroute/apply.ps1`"),
            ("one span, docs/", "see `docs/\nagents/leaf-contract.md`"),
            ("one span, .agents/",
             "see `.agents/\nskills/routing-registry/SKILL.md`"),
            ("one span, lib/", "see `lib/\nwindows/AutoOS.Ui.psm1`"),
            ("one span, .github/", "see `.github/\nworkflows/ci.yml`"),
            ("two spans", "run `configuration/`\n`omniroute/apply.ps1`"),
            ("two spans, indented", "run `logs/routing/`\n  `availability.json`"),
            ("two spans, list marker",
             "run `logs/routing/`\n- `availability.json`"),
            ("two spans, quote marker",
             "run `logs/routing/`\n> `availability.json`"),
            ("fence must not shift pairing",
             "```\necho `a` b `c\n```\nrun `configuration/"
             "\nomniroute/apply.ps1`"),
        ]
        for name, text in fixtures:
            self.assertTrue(
                _wrapped_repo_paths(text),
                "guard missed the %s wrap fixture: %r" % (name, text))
        wrapped = ["%s: %r" % (path.relative_to(ROOT), s)
                   for path in _skill_files()
                   for s in _wrapped_repo_paths(
                       path.read_text(encoding="utf-8"))]
        self.assertEqual(wrapped, [], "wrapped repo path(s): %s" % wrapped)

    def test_every_script_path_named_exists(self):
        missing = ["%s: %s" % (p.relative_to(ROOT), s)
                   for p in _skill_files()
                   for s in _script_paths(p)
                   if not (ROOT / s).is_file()]
        self.assertEqual(
            missing, [],
            "script path(s) named by the skill do not exist in the repo: %s"
            % "; ".join(missing))

    def test_add_modify_and_remove_carry_every_required_step(self):
        """The paths are procedures, not pages: a step can vanish while every
        file and link still resolves. Each required step's heading or command
        keyword must be present in the page that owns the path."""
        for name, steps in REQUIRED_STEPS.items():
            path = REFERENCES / name
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            missing = [step for step, patterns in steps
                       if not all(re.search(p, text) for p in patterns)]
            self.assertEqual(
                missing, [],
                "%s lost required step(s) — heading/keyword gone: %s"
                % (name, ", ".join(missing)))
            # The checker must be able to fail: against no text every step is
            # reported — a pattern that cannot miss asserts nothing.
            self.assertEqual(
                [step for step, patterns in steps
                 if not all(re.search(p, "") for p in patterns)],
                [step for step, _ in steps],
                "step pattern(s) match the empty string: %s" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
