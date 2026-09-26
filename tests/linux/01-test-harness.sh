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

