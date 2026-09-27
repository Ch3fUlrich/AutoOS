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

if it "the harness runs the tests a --filter selects, not merely exits 0"; then
    # The old form filtered on __no_such_test__ and only checked the exit
    # status. A copied harness resolves ROOT to the copy's own directory, so
    # its part glob matches nothing, it runs no test at all, and it still exits
    # 0 with "passed 0" -- the test therefore passed even when the filter
    # machinery selected nothing. Run the REAL harness against one known test
    # and assert both that test's name and a non-zero passed count, which an
    # empty run cannot fake.
    expected='linux catalog validates'
    out="$(env -u AUTOOS_FULL_SUITE bash "$ROOT/tests/run-tests.sh" --filter "$expected" 2>&1)"
    rc=$?
    if [[ $rc -ne 0 ]]; then
        fail "expected exit 0 running the '$expected' test, got $rc: ${out: -300}"
    elif [[ "$out" != *"$expected"* ]]; then
        fail "the selected test's name never appeared (a filter that ran nothing?): ${out: -300}"
    elif [[ "$out" != *"passed 1"* ]]; then
        fail "expected exactly one test to run: ${out: -300}"
    else
        pass
    fi
fi

if it "the test HTTP server creates a real port file, never mktemp -u"; then
    # `mktemp -u` only PRINTS a name and creates nothing: two callers -- or a
    # same-user process that races the same path -- can be handed the same
    # name and the second writer clobbers the first. A test that then reads
    # the port file can observe another server's port. Pin that the helper
    # asks mktemp for a real file. A PATH stub logs mktemp's arguments and
    # still returns a usable path, so the helper runs to completion either way.
    fakebin="$(mktemp -d)"
    log="$fakebin/mktemp.args"
    real_mktemp="$(command -v mktemp)"
    cat >"$fakebin/mktemp" <<EOS
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"\$MKTEMP_LOG"
case "\${1:-}" in -u) shift ;; esac
exec "$real_mktemp" "\$@"
EOS
    chmod +x "$fakebin/mktemp"
    out="$(MKTEMP_LOG="$log" PATH="$fakebin:$PATH" _start_test_http_server "$ROOT/tests/helpers/image_index_fixtures" 2>/dev/null)"
    read -r srv_pid srv_port <<<"$out"
    [[ -n "$srv_pid" ]] && kill "$srv_pid" 2>/dev/null
    mktemp_args="$(tr '\n' ' ' <"$log" 2>/dev/null)"
    rm -rf "$fakebin"
    if [[ "$mktemp_args" == *"-u"* ]]; then
        fail "mktemp was called with -u, which reserves a name and creates no file: [$mktemp_args]"
    elif [[ -z "$srv_port" ]]; then
        fail "the helper served no port (pid=[$srv_pid], port=[$srv_port], mktemp args=[$mktemp_args])"
    else
        pass
    fi
fi

if it "the harness does not refuse when AUTOOS_FULL_SUITE=1"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    # Stub the numbered test parts out of the copy so the explicit opt-in does
    # not run the whole suite here (the host could be OOM-sensitive). The stub
    # must match, or this test would run the full suite and pass vacuously.
    stubbed=0
    grep -q '^for __part in' "$tmp/run-tests.sh" \
        && sed -i 's/^for __part in.*/: # sourcing stubbed by 01-test-harness.sh/' "$tmp/run-tests.sh" \
        && stubbed=1
    if [[ $stubbed -ne 1 ]]; then
        rm -rf "$tmp"
        fail "stub anchor 'for __part in' not found in run-tests.sh"
    else
        out="$(AUTOOS_FULL_SUITE=1 timeout 20 bash "$tmp/run-tests.sh" 2>&1)"
        rc=$?
        rm -rf "$tmp"
        if [[ "$out" == *"refusing an unfiltered run"* ]]; then
            fail "AUTOOS_FULL_SUITE=1 must suppress the refusal (rc=$rc)"
        elif [[ $rc -ne 0 || "$out" != *"passed 0"* ]]; then
            fail "stubbed opt-in run should run zero tests and exit 0 (rc=$rc): ${out: -200}"
        else
            pass
        fi
    fi
fi
