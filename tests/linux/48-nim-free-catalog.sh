# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "NIM free catalog"

# Offline inventory tests use a loopback server and adapted public fixtures.
# No key is read or sent; no live catalog or inference calls.
if it "nim-free-catalog: keyless inventory, pagination and publisher joins (loopback tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_nim_free_catalog.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Offline failure tests use a loopback server and populated prior snapshots.
# No key is read or sent; failures must preserve bytes and suppress deltas.
if it "nim-free-catalog: failed reads and atomic snapshot safety (loopback tests)"; then
    out="$( (cd "$ROOT" && python3 tests/test_nim_free_catalog_failures.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Proposal tests launch fake check/gh children only; no live issue or label writes.
if it "nim-free-catalog: reviewed proposals, deduplication and safe issue bodies (offline tests)"; then
    # shellcheck disable=SC2015 # pass is the harness printf, not a fallible test.
    out="$( (cd "$ROOT" && python3 tests/test_nim_free_catalog_proposal.py) 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi
