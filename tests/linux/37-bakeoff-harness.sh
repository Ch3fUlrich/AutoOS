# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Bake-off harness contract tests ──────────────────────────────────────
describe "bake-off harness"

if it "all bake-off harness contract tests pass"; then
    python3 tests/test_bakeoff_harness.py
fi