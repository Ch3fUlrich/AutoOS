# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── File-size gate (AO-400LOC) ─────────────────────────────────────────────
# Own part on purpose: the alternative was growing tests/linux/33-documentation.sh
# (1637 lines, baselined) — and a baselined file may shrink, never grow, which
# is exactly the rule this gate exists to enforce.
describe "file-size gate"

if it "file size: 400-line gate, baseline ratchet and exclusions (unit tests)"; then
    if out="$(python3 tests/test_check_file_size.py 2>&1)"; then pass; else fail "$(printf '%s\n' "$out" | tail -n 20)"; fi
fi
