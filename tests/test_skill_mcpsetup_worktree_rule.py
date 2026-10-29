#!/usr/bin/env python3
"""MCPSETUP-WORKTREE-GRAPHIFY: graphify is UNAVAILABLE in a linked worktree.

references/graphify.md's rule wins over the older references/serena.md advice:
graphify's own hooks exit 0 in a linked worktree (graphify.hooks._WORKTREE_GUARD
compares git-dir against git-common-dir), and a graph linked or copied from the
main checkout describes another branch's code. Both references must carry one
shared bold rule verbatim, and serena.md must not pair graphify-out/ with any
instruction to link it to the main checkout.

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
RULE = ("**In a linked worktree, graphify is unavailable: say so; never link "
        "or hand-build `graphify-out/` there.**")

# Linking language on a line naming graphify-out/: the prescriptive fix this
# rule replaces ("link it to the main checkout", ln -s / mklink recipes,
# junction/symlink advice). The rule sentence itself pairs "link" with
# graphify-out in a prohibition, so a line carrying RULE verbatim is allowed.
LINKISH = re.compile(r"(?i)\blink|ln\s+-s|mklink|symlink|junction")


class WorktreeRuleTests(unittest.TestCase):
    def test_shared_rule_is_verbatim_in_both_references(self):
        for ref in (SERENA, GRAPHIFY):
            self.assertIn(
                RULE, ref.read_text(encoding="utf-8"),
                "%s must carry the shared worktree rule verbatim" % ref.name)

    def test_serena_no_longer_tells_you_to_link_graphify_out(self):
        text = SERENA.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "graphify-out" in line and LINKISH.search(line) \
                    and RULE not in line:
                self.fail(
                    "serena.md:%d pairs graphify-out with linking advice: %r"
                    % (lineno, line))


if __name__ == "__main__":
    unittest.main()
