import json
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OPENCODE_JSONC = ROOT / 'opencode.jsonc'
def denied_by_block(opencode_text: str):
    pattern = re.compile(r'^\s*\{\s*"action"\s*:\s*"omnigraph_([\w_]+)"\s*,\s*"resource"\s*:\s*"\*"\s*,\s*"effect"\s*:\s*"deny"\s*\}\s*,?\s*$')
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
                action = m.group(1)
                current_block.add(action)
                i += 1
            blocks.append(current_block)
        else:
            if 'omnigraph_' in cleaned_lines[i]:
                raise ValueError(f'Unknown shape: {cleaned_lines[i]}')
            i += 1
    return blocks
def audit(tools, denied_sets):
    problems = []
    for block_index, denied_set in enumerate(denied_sets):
        for tool in denied_set:
            if tool not in tools:
                problems.append(f'omnigraph_{tool} denied but not in tools')
        for tool, hint in tools.items():
            if hint is not True:  # False or None
                if tool not in denied_set:
                    problems.append(f'omnigraph_{tool} is write-capable (or unknown) and not denied in block {block_index}')
    return problems
opencode_text = OPENCODE_JSONC.read_text(encoding='utf-8')
denied_sets = denied_by_block(opencode_text)
print('Denied sets:', denied_sets)
tools = {'health': True, 'snapshot': True, 'query': True, 'schema_get': True, 'branches_list': True, 'graphs_list': True, 'commits_list': True, 'commits_get': True, 'commits_changes': True, 'changes_poll': True, 'mutate': False, 'load': False, 'branches_create': False, 'branches_delete': False, 'branches_merge': False, 'branches_rename': False}
print('Tools keys:', list(tools.keys()))
problems = audit(tools, denied_sets)
print('Problems:', problems)
