# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Pre-push gate: push-time refusals (AO-PREPUSH-HOOK-GITENV-2) ───────────
# Own part on purpose: tests/linux/33-documentation.sh is baselined and may only
# shrink. Same number as 45-file-size-gate.sh - parts are keyed by their NN prefix,
# so this rides in the same shard (tests/ci-shards.txt) and needs no new entry.
describe "prepush hook gate"

if it "prepush: a pushed sha is gated, not the checkout's HEAD; a fixture git identity is refused (unit tests)"; then
    if out="$(python3 tests/test_prepush_hook.py 2>&1)"; then pass; else fail "$(printf '%s\n' "$out" | tail -n 20)"; fi
fi

if it "no test writes a git identity with 'git config' (static guard)"; then
    if out="$(python3 tests/test_no_git_config_identity.py 2>&1)"; then pass; else fail "$(printf '%s\n' "$out" | tail -n 20)"; fi
fi
