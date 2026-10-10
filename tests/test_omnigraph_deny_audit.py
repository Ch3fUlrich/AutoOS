"""Tests for omnigraph deny audit."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENCODE_JSONC = ROOT / "opencode.jsonc"


# Inline constant from the spec, measured by Server lane/og-013 5ab59b6c0
BRIDGE_TOOLS_0_13_0 = {
    # read-only (readOnlyHint true)
    "health": True,
    "snapshot": True,
    "query": True,
    "schema_get": True,
    "branches_list": True,
    "graphs_list": True,
    "commits_list": True,
    "commits_get": True,
    "commits_changes": True,
    "changes_poll": True,
    # write (readOnlyHint false)
    "mutate": False,
    "load": False,
    "branches_create": False,
    "branches_delete": False,
    "branches_merge": False,
}


def denied_by_block(opencode_text: str) -> list[set[str]]:
    """Return a list of sets of tool names (prefix `omnigraph_` stripped) for each block.

    A block is a contiguous run of lines matching:
        { "action": "omnigraph_<n>", "resource": "*", "effect": "deny" }

    Strip // comments first. Unknown shapes raise.
    """
    import re
    # Pattern to match an omnigraph deny rule line
    pattern = re.compile(
        r'^\s*\{\s*"action"\s*:\s*"omnigraph_([\w_]+)"\s*,\s*"resource"\s*:\s*"\*"\s*,\s*"effect"\s*:\s*"deny"\s*\}\s*,?\s*$'
    )
    lines = opencode_text.splitlines()
    # Strip comments: remove everything from '//' to end of line
    cleaned_lines = [re.sub(r'//.*', '', line) for line in lines]
    blocks = []
    i = 0
    while i < len(cleaned_lines):
        match = pattern.match(cleaned_lines[i])
        if match:
            # Start a block
            current_block = set()
            while i < len(cleaned_lines) and pattern.match(cleaned_lines[i]):
                m = pattern.match(cleaned_lines[i])
                action = m.group(1)  # the part after omnigraph_
                current_block.add(action)
                i += 1
            blocks.append(current_block)
        else:
            # Check if the line contains "omnigraph_" but didn't match -> unknown shape
            if "omnigraph_" in cleaned_lines[i]:
                raise ValueError(f"Unknown shape for omnigraph rule: {cleaned_lines[i]}")
            i += 1
    return blocks


def audit(tools: dict[str, bool | None], denied_sets: list[set[str]]) -> list[str]:
    """Return list of problems.

    Per block:
        (a) denied name not in tools;
        (b) tool not readOnlyHint True (False, absent, or no annotations) and not denied.
    """
    problems = []
    for block_index, denied_set in enumerate(denied_sets):
        # (a) denied name not in tools
        for tool in denied_set:
            if tool not in tools:
                problems.append(f"omnigraph_{tool} denied but not in tools")
        # (b) tool not readOnlyHint True (False, absent, or no annotations) and not denied
        for tool, hint in tools.items():
            if hint is not True:  # False or None
                if tool not in denied_set:
                    problems.append(f"omnigraph_{tool} is write-capable (or unknown) and not denied in block {block_index}")
    return problems


class TestOmnigraphDenyAudit(unittest.TestCase):
    def test_real_repo_file_passes(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        # We expect exactly 2 blocks
        self.assertEqual(len(denied_sets), 2, f"Expected 2 blocks, got {len(denied_sets)}")
        # Each block should have the same 5 tools
        expected = {"mutate", "load", "branches_create", "branches_merge", "branches_delete"}
        for i, denied_set in enumerate(denied_sets):
            self.assertEqual(
                denied_set,
                expected,
                f"Block {i} denied set mismatch: {denied_set} != {expected}",
            )
        problems = audit(BRIDGE_TOOLS_0_13_0, denied_sets)
        self.assertEqual(problems, [], f"Expected no problems, got: {problems}")

    def test_synthetic_extra_write_tool(self):
        # Start with the real denied sets from the file
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        # Now add an extra write tool to the tools dict
        tools = dict(BRIDGE_TOOLS_0_13_0)
        tools["branches_rename"] = False  # write tool, not denied
        problems = audit(tools, denied_sets)
        # We expect a problem about branches_rename not being denied in each block
        # Since there are two blocks, we should get two similar problems? Or one per block?
        # The spec says per block: (b) tool not readOnlyHint True and not denied -> problem.
        # So for each block, we will get a problem for branches_rename.
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("branches_rename", problem)
            self.assertIn("not denied", problem)

    def test_synthetic_tool_with_none_annotations(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        tools = dict(BRIDGE_TOOLS_0_13_0)
        tools["mystery_tool"] = None  # no annotations
        problems = audit(tools, denied_sets)
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("mystery_tool", problem)
            self.assertIn("not denied", problem)

    def test_synthetic_denied_name_missing_from_tools(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        # Remove 'load' from the tools dict (as if it was renamed)
        tools = {k: v for k, v in BRIDGE_TOOLS_0_13_0.items() if k != "load"}
        problems = audit(tools, denied_sets)
        # We expect a problem for each block: denied name 'load' not in tools
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("load", problem)
            self.In("not in tools", problem)

    def test_synthetic_extra_readonly_tool_no_problem(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        tools = dict(BRIDGE_TOOLS_0_13_0)
        tools["foo_list"] = True  # read-only, extra
        problems = audit(tools, denied_sets)
        # No problems expected because the extra tool is read-only
        self.assertEqual(problems, [], f"Expected no problems, got: {problems}")

    def test_synthetic_block_missing_omnigraph_load(self):
        # We need to create an opencode text that has one block missing omnigraph_load
        # Let's take the real opencode text and remove the line for omnigraph_load from the first block.
        lines = OPENCODE_JSONC.read_text(encoding="utf-8").splitlines()
        # Find the first block (around line 230) and remove the line for omnigraph_load
        # We'll do a simple approach: remove the first occurrence of the load line in the first block.
        # But note: we must not break the block structure. Instead, we'll create a modified text.
        # We'll replace the first block's load line with a comment or remove it? We must keep the block contiguous.
        # Instead, let's create a new text that has the first block without the load line.
        # We'll do: from the start of the first block to the end of the first block, remove the load line.
        # We know the first block starts at line 230 (0-indexed line 229) and ends at line 234 (0-indexed 233).
        # Actually, let's find the block by scanning for the pattern.
        # For simplicity in the test, we'll create a synthetic opencode text that has two blocks, but the first block missing load.
        # We'll build it from the denied sets we expect.
        base_block = '''        { "action": "omnigraph_mutate", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_load", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_create", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_merge", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_delete", "resource": "*", "effect": "deny" },'''
        # First block without load
        block1 = base_block.replace(
            '        { "action": "omnigraph_load", "resource": "*", "effect": "deny" },',
            '',
        )
        # Now we have to adjust the commas? Actually, removing a line in the middle of the block might break the contiguous run.
        # We'll instead remove the line and leave an empty line? But the block must be contiguous lines matching the pattern.
        # Let's remove the line and then check that the block is still contiguous? We'll have to adjust.
        # Alternatively, we can create a block with 4 lines (missing one) and then the function should still see 4 lines as a block.
        # We'll create the first block as:
        block1_lines = [
            '        { "action": "omnigraph_mutate", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_create", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_merge", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_delete", "resource": "*", "effect": "deny" },',
        ]
        block1 = '\n'.join(block1_lines)
        block2 = base_block  # second block is intact
        # Now we need to embed these blocks in a JSONC-like text. We'll just use the two blocks as the entire text for simplicity.
        opencode_text = f"{block1}\n\n{block2}\n"
        denied_sets = denied_by_block(opencode_text)
        # We expect two blocks: first block missing 'load', second block has all five.
        self.assertEqual(len(denied_sets), 2)
        self.assertEqual(denied_sets[0], {"mutate", "branches_create", "branches_merge", "branches_delete"})
        self.assertEqual(denied_sets[1], {"mutate", "load", "branches_create", "branches_merge", "branches_delete"})
        problems = audit(BRIDGE_TOOLS_0_13_0, denied_sets)
        # We expect a problem for the first block: the tool 'load' is write-capable and not denied in block 0.
        # And no problem for the second block.
        self.assertEqual(len(problems), 1)
        self.assertIn("load", problems[0])
        self.assertIn("not denied", problems[0])
        self.assertIn("block 0", problems[0])  # or however we format the block index


if __name__ == "__main__":
    unittest.main()