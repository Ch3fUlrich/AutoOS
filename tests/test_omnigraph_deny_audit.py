"""Tests for omnigraph deny audit."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENCODE_JSONC = ROOT / "opencode.jsonc"


# Inline constant from the spec, measured by Server lane/og-013 5ab59b6c0
BRIDGE_TOOLS_0_13_0 = {
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
    // comments are stripped first; unknown shapes raise.
    """
    import re
    pattern = re.compile(
        r'^\s*\{\s*"action"\s*:\s*"omnigraph_([\w_]+)"\s*,\s*"resource"\s*:\s*"\*"\s*,\s*"effect"\s*:\s*"deny"\s*\}\s*,?\s*$'
    )
    lines = opencode_text.splitlines()
    cleaned_lines = [re.sub(r'//.*', '', line) for line in lines]
    blocks = []
    i = 0
    while i < len(cleaned_lines):
        match = pattern.match(cleaned_lines[i])
        if match:
            current_block = set()
            while i < len(cleaned_lines) and pattern.match(cleaned_lines[i]):
                m = pattern.match(cleaned_lines[i])
                action = m.group(1)  # the part after omnigraph_
                current_block.add(action)
                i += 1
            blocks.append(current_block)
        else:
            if "omnigraph_" in cleaned_lines[i]:
                raise ValueError(f"Unknown shape for omnigraph rule: {cleaned_lines[i]}")
            i += 1
    return blocks


def audit(tools: dict[str, bool | None], denied_sets: list[set[str]]) -> list[str]:
    """Return list of problems. Per block:

    (a) denied name not in tools;
    (b) tool not readOnlyHint True (False, absent, or no annotations) and not denied.
    """
    problems = []
    for block_index, denied_set in enumerate(denied_sets):
        for tool in denied_set:
            if tool not in tools:
                problems.append(f"omnigraph_{tool} denied but not in tools")
        for tool, hint in tools.items():
            if hint is not True:
                if tool not in denied_set:
                    problems.append(f"omnigraph_{tool} is write-capable (or unknown) and not denied in block {block_index}")
    return problems


class TestOmnigraphDenyAudit(unittest.TestCase):
    def test_real_repo_file_passes(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        self.assertEqual(len(denied_sets), 2, f"Expected 2 blocks, got {len(denied_sets)}")
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
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        tools = dict(BRIDGE_TOOLS_0_13_0)
        tools["branches_rename"] = False  # write tool, not denied
        problems = audit(tools, denied_sets)
        # (b) fires once per block.
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
        tools = {k: v for k, v in BRIDGE_TOOLS_0_13_0.items() if k != "load"}
        problems = audit(tools, denied_sets)
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("load", problem)
            self.assertIn("not in tools", problem)

    def test_synthetic_extra_readonly_tool_no_problem(self):
        opencode_text = OPENCODE_JSONC.read_text(encoding="utf-8")
        denied_sets = denied_by_block(opencode_text)
        tools = dict(BRIDGE_TOOLS_0_13_0)
        tools["foo_list"] = True  # read-only, extra
        problems = audit(tools, denied_sets)
        self.assertEqual(problems, [], f"Expected no problems, got: {problems}")

    def test_synthetic_block_missing_omnigraph_load(self):
        # Two synthetic deny blocks where the first omits omnigraph_load, so the
        # write-capable load tool is undenied in block 0 only.
        base_block = '''        { "action": "omnigraph_mutate", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_load", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_create", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_merge", "resource": "*", "effect": "deny" },
        { "action": "omnigraph_branches_delete", "resource": "*", "effect": "deny" },'''
        block1 = base_block.replace(
            '        { "action": "omnigraph_load", "resource": "*", "effect": "deny" },',
            '',
        )
        block1_lines = [
            '        { "action": "omnigraph_mutate", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_create", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_merge", "resource": "*", "effect": "deny" },',
            '        { "action": "omnigraph_branches_delete", "resource": "*", "effect": "deny" },',
        ]
        block1 = '\n'.join(block1_lines)
        block2 = base_block  # second block is intact
        opencode_text = f"{block1}\n\n{block2}\n"
        denied_sets = denied_by_block(opencode_text)
        self.assertEqual(len(denied_sets), 2)
        self.assertEqual(denied_sets[0], {"mutate", "branches_create", "branches_merge", "branches_delete"})
        self.assertEqual(denied_sets[1], {"mutate", "load", "branches_create", "branches_merge", "branches_delete"})
        problems = audit(BRIDGE_TOOLS_0_13_0, denied_sets)
        self.assertEqual(len(problems), 1)
        self.assertIn("load", problems[0])
        self.assertIn("not denied", problems[0])
        self.assertIn("block 0", problems[0])


if __name__ == "__main__":
    unittest.main()
