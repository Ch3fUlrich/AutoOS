# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "skill: unattended-orchestration"

# tests/test_skill_phase_batch_review.py: the unattended-orchestration skill's
# phase-batch review rule R-orch-31 and its seat/fixer templates (SKILL-PHASE-BATCH-REVIEW).
if it "unattended-orchestration skill: phase-batch review rule and templates (unit tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_skill_phase_batch_review.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi
