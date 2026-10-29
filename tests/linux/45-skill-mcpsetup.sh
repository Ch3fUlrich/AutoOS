# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "skill: mcp-servers-setup split"

# .agents/skills/mcp-servers-setup: SKILL.md is an index (<= 150 lines) with one
# reference file per server (AO-SKILL-MCPSETUP-SPLIT, D-2144 S7 F1) - the test
# guards the caps, that no old heading was lost, that every reference is linked
# and every link resolves, and the content floor.
if it "mcp-servers-setup split: index <=150, references <=500, no heading lost (unit tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_skill_mcpsetup_split.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# MCPSETUP-WORKTREE-GRAPHIFY (2026-10-29): in a linked worktree graphify is
# unavailable - references/graphify.md's rule wins. Both references must carry
# the one-line rule verbatim, and references/serena.md must not pair
# graphify-out/ with any instruction to link it to the main checkout.
if it "mcpsetup worktree rule: shared rule verbatim, no graphify-out link fix (unit tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_skill_mcpsetup_worktree_rule.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi
