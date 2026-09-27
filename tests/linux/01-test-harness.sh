# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Test harness self-tests ────────────────────────────────────────────────
describe "test harness"

if it "the suite exits non-zero when a syntax error stops it early"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    python3 -c "
import sys
with open(sys.argv[1]) as f:
    lines = f.readlines()
last = -1
for i, line in enumerate(lines):
    if line.rstrip('\n') == 'fi':
        last = i
if last >= 0:
    del lines[last]
    with open(sys.argv[1], 'w') as f:
        f.writelines(lines)
" "$tmp/run-tests.sh"
    bash "$tmp/run-tests.sh" --filter __no_such_test__ >/dev/null 2>&1
    rc=$?
    rm -rf "$tmp"
    if [[ $rc -ne 0 ]]; then pass; else fail "expected non-zero exit, got $rc"; fi
fi

if it "the harness refuses an unfiltered run unless AUTOOS_FULL_SUITE=1"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    err="$(env -u AUTOOS_FULL_SUITE bash "$tmp/run-tests.sh" 2>&1 >/dev/null)"
    rc=$?
    rm -rf "$tmp"
    if [[ $rc -ne 2 ]]; then
        fail "expected exit 2, got $rc; stderr: ${err:0:200}"
    elif [[ "$err" != *"AUTOOS_FULL_SUITE=1"* ]]; then
        fail "refusal must name AUTOOS_FULL_SUITE=1; stderr: ${err:0:200}"
    elif [[ "$err" != *"--filter"* ]]; then
        fail "refusal must name --filter; stderr: ${err:0:200}"
    else
        pass
    fi
fi

if it "the harness still runs a filtered suite"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    env -u AUTOOS_FULL_SUITE bash "$tmp/run-tests.sh" --filter __no_such_test__ >/dev/null 2>&1
    rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 ]]; then pass; else fail "expected exit 0 with --filter, got $rc"; fi
fi

if it "the harness does not refuse when AUTOOS_FULL_SUITE=1"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    # Stub the numbered test parts out of the copy so the explicit opt-in does
    # not run the whole suite here (the host could be OOM-sensitive).
    python3 -c "
import sys
with open(sys.argv[1]) as f:
    lines = f.readlines()
lines = [': # sourcing stubbed by 01-test-harness.sh\n'
         if line.startswith('for __part in') else line for line in lines]
with open(sys.argv[1], 'w') as f:
    f.writelines(lines)
" "$tmp/run-tests.sh"
    err="$(AUTOOS_FULL_SUITE=1 timeout 20 bash "$tmp/run-tests.sh" 2>&1 >/dev/null)"
    rc=$?
    rm -rf "$tmp"
    if [[ "$err" == *"refusing an unfiltered run"* ]]; then
        fail "AUTOOS_FULL_SUITE=1 must suppress the refusal (rc=$rc)"
    else
        pass
    fi
fi
