#!/usr/bin/env python3
"""MCPSETUP-WORKTREE-GRAPHIFY: graphify is UNAVAILABLE in a linked worktree.

references/graphify.md's rule wins over the older references/serena.md advice:
graphify's own hooks exit 0 in a linked worktree (graphify.hooks._WORKTREE_GUARD
compares git-dir against git-common-dir), and a graph linked or copied from the
main checkout describes another branch's code. Both references must carry one
shared bold rule verbatim, and no mcp-servers-setup reference may pair
graphify-out/ with any instruction to link it to the main checkout.

Run directly:

    python3 tests/test_skill_mcpsetup_worktree_rule.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / ".agents" / "skills" / "mcp-servers-setup" / "references"
SERENA = REFS / "serena.md"
GRAPHIFY = REFS / "graphify.md"

# The one-line rule both references share verbatim. It sits on a single line in
# each file, so this is a literal substring match, not a fuzzy one.
RULE = ("**In a linked worktree, graphify is unavailable unless the runner "
        "built `graphify-out/` for that worktree's own tree; say so. Never link "
        "it from another checkout or hand-build it.**")

# Linking language on a line naming graphify-out/: the prescriptive fix this
# rule replaces ("link it to the main checkout", ln -s / mklink recipes,
# junction/symlink advice). The rule sentence itself pairs "link" with
# graphify-out in a prohibition, so a line carrying RULE verbatim is allowed.
LINKISH = re.compile(r"(?i)\blink|ln\s+-s|mklink|symlink|junction")

# "a linked worktree" names the place, not a fix: it is neutralised before the
# scan, newlines kept so line numbers hold (graphify.md wraps it as
# "a linked\nworktree").
LINKED_WORKTREE = re.compile(r"(?i)\blinked(\s+worktrees?\b)")


def _neutralised(text):
    return LINKED_WORKTREE.sub(r"the\1", text)


class WorktreeRuleTests(unittest.TestCase):
    def test_shared_rule_is_verbatim_in_both_references(self):
        for ref in (SERENA, GRAPHIFY):
            self.assertIn(
                RULE, ref.read_text(encoding="utf-8"),
                "%s must carry the shared worktree rule verbatim" % ref.name)

    def test_no_reference_tells_you_to_link_graphify_out(self):
        refs = sorted(REFS.glob("*.md"))
        self.assertIn(SERENA, refs, "references not found under %s" % REFS)
        for ref in refs:
            text = ref.read_text(encoding="utf-8")
            for lineno, (line, scan) in enumerate(zip(
                    text.splitlines(), _neutralised(text).splitlines()), 1):
                if "graphify-out" in line and LINKISH.search(scan) \
                        and RULE not in line:
                    self.fail(
                        "%s:%d pairs graphify-out with linking advice: %r"
                        % (ref.name, lineno, line))

    def test_the_link_scan_is_not_vacuous(self):
        """The neutraliser spares only the place name, never the fix."""
        for fix in ("link `graphify-out/` to the main checkout",
                    "graphify-out/ is linked to the main checkout",
                    "mklink /J graphify-out ..\\main\\graphify-out"):
            self.assertTrue(LINKISH.search(_neutralised(fix)), fix)
        place = "`graphify-out/` is gitignored, so a linked\nworktree has none"
        self.assertFalse(LINKISH.search(_neutralised(place).splitlines()[0]))


if __name__ == "__main__":
    unittest.main()
