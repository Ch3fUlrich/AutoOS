# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "skill: routing-registry"

# .agents/skills/routing-registry: the OmniRoute routing skill (AO-ROUTING-SKILL;
# folded in and retired its predecessor). Offline: pure filesystem assertions
# over the skill's own files.
if it "routing-registry skill: budgets, links resolve, retired predecessor gone (unit tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_skill_routing_registry.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tests/test_skill_routing_registry_commands.py: the skill's apply.sh/apply.ps1
# invocations and render --out claims, checked against the appliers' own
# argument handling and the renders' own --help (AO-ROUTING-SKILL).
if it "routing-registry skill: apply flags and render --out guard (unit tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_skill_routing_registry_commands.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi
