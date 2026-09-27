# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── shellcheck helper: memory bound + honest verdict ───────────────────────
# Every test here runs through a STUB shellcheck on PATH and a fake meminfo,
# so none of them needs the real binary (or its memory). The helpers under
# test, run_shellcheck and shellcheck_limit_kb, sit at the top of this file.
describe "shellcheck helper"

# _sc_sandbox
# Prints a fresh temp dir holding bin/shellcheck (a stub: appends its argv and
# the address-space limit it was started under to $STUB_LOG, prints $STUB_OUT,
# exits $STUB_RC) and meminfo (4 GiB available + 1 GiB swap, so the bound is
# 90% of 5242880 KiB = 4718592 KiB = 4608 MiB whatever the real host looks like).
_sc_sandbox() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/bin"
    cat > "$d/bin/shellcheck" <<'STUB'
#!/usr/bin/env bash
{
    printf 'argv: %s\n' "$*"
    printf 'ulimit: %s\n' "$(ulimit -v)"
} >> "${STUB_LOG:-/dev/null}"
[[ -n "${STUB_OUT:-}" ]] && printf '%s\n' "$STUB_OUT"
exit "${STUB_RC:-0}"
STUB
    chmod +x "$d/bin/shellcheck"
    printf 'MemTotal: 8388608 kB\nMemFree: 1024 kB\nMemAvailable: 4194304 kB\nSwapTotal: 2097152 kB\nSwapFree: 1048576 kB\n' > "$d/meminfo"
    printf '%s\n' "$d"
}

if it "shellcheck helper: the memory limit is 90% of MemAvailable + SwapFree, read from AUTOOS_MEMINFO"; then
    d="$(mktemp -d)"
    printf 'MemTotal: 8000000 kB\nMemFree: 100 kB\nMemAvailable: 1000000 kB\nSwapTotal: 999 kB\nSwapFree: 500000 kB\n' > "$d/full"
    printf 'MemAvailable: 1000000 kB\n' > "$d/noswap"
    printf 'MemTotal: 8000000 kB\nSwapFree: 500000 kB\n' > "$d/noavail"
    ok=1
    got="$(AUTOOS_MEMINFO="$d/full" shellcheck_limit_kb)"
    [[ "$got" == 1350000 ]] || { ok=0; echo "MemAvailable 1000000 + SwapFree 500000: expected 1350000, got [$got]" >&2; }
    got="$(AUTOOS_MEMINFO="$d/noswap" shellcheck_limit_kb)"
    [[ "$got" == 900000 ]] || { ok=0; echo "no SwapFree line counts as 0: expected 900000, got [$got]" >&2; }
    got="$(AUTOOS_MEMINFO="$d/noavail" shellcheck_limit_kb)"
    [[ -z "$got" ]] || { ok=0; echo "no MemAvailable: expected no limit, got [$got]" >&2; }
    got="$(AUTOOS_MEMINFO="$d/does-not-exist" shellcheck_limit_kb)"
    [[ -z "$got" ]] || { ok=0; echo "unreadable file: expected no limit, got [$got]" >&2; }
    if [[ -r /proc/meminfo ]]; then
        got="$( unset AUTOOS_MEMINFO; shellcheck_limit_kb )"
        [[ "$got" =~ ^[1-9][0-9]*$ ]] || { ok=0; echo "default /proc/meminfo: expected a positive KiB number, got [$got]" >&2; }
    fi
    rm -rf "$d"
    if (( ok )); then pass; else fail "the limit is not 90% of MemAvailable + SwapFree"; fi
fi

if it "shellcheck helper: run_shellcheck starts shellcheck under that limit (ulimit -v) with -S warning and the files"; then
    d="$(_sc_sandbox)"
    ok=1
    ( PATH="$d/bin:$PATH" AUTOOS_MEMINFO="$d/meminfo" STUB_LOG="$d/log" run_shellcheck one.sh two.sh ) >/dev/null 2>&1
    grep -qx 'argv: -S warning one.sh two.sh' "$d/log" 2>/dev/null || { ok=0; echo "argv: $(grep '^argv' "$d/log" 2>&1)" >&2; }
    grep -qx 'ulimit: 4718592' "$d/log" 2>/dev/null || { ok=0; echo "limit: $(grep '^ulimit' "$d/log" 2>&1) (want 4718592)" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "shellcheck was not started under the computed memory limit"; fi
fi

if it "shellcheck helper: with no readable meminfo run_shellcheck sets no limit of its own"; then
    d="$(_sc_sandbox)"
    ( PATH="$d/bin:$PATH" AUTOOS_MEMINFO="$d/does-not-exist" STUB_LOG="$d/log" run_shellcheck one.sh ) >/dev/null 2>&1
    got="$(grep '^ulimit' "$d/log" 2>/dev/null)"
    rm -rf "$d"
    assert_eq "$got" "ulimit: $(ulimit -v)"
fi

if it "shellcheck helper: rc 251 and rc 137 are out-of-memory (flag set, returns 3); a finding and a clean run are not"; then
    d="$(_sc_sandbox)"
    res=""
    for spec in "251:shellcheck: out of memory" "137:" "1:a finding" "0:"; do
        want_rc="${spec%%:*}"
        res+="$( PATH="$d/bin:$PATH" AUTOOS_MEMINFO="$d/meminfo" STUB_RC="$want_rc" STUB_OUT="${spec#*:}" run_shellcheck one.sh >/dev/null 2>&1
                 echo "stub=$want_rc rc=$? oom=${SHELLCHECK_OOM:-unset}" )"$'\n'
    done
    rm -rf "$d"
    exp=$'stub=251 rc=3 oom=1\nstub=137 rc=3 oom=1\nstub=1 rc=1 oom=0\nstub=0 rc=0 oom=0\n'
    assert_eq "$res" "$exp"
fi

if it "shellcheck helper: any other rc is out-of-memory only when the output says so; a finding that quotes those words stays a finding"; then
    d="$(_sc_sandbox)"
    res=""
    for spec in "2:shellcheck: mmap: Cannot allocate memory" "2:shellcheck: out of memory (requested 2097152 bytes)" "2:could not read a.sh" "1:echo \"out of memory\""; do
        want_rc="${spec%%:*}"
        res+="$( PATH="$d/bin:$PATH" AUTOOS_MEMINFO="$d/meminfo" STUB_RC="$want_rc" STUB_OUT="${spec#*:}" run_shellcheck one.sh >/dev/null 2>&1
                 echo "rc=$? oom=${SHELLCHECK_OOM:-unset}" )"$'\n'
    done
    rm -rf "$d"
    exp=$'rc=3 oom=1\nrc=3 oom=1\nrc=2 oom=0\nrc=1 oom=0\n'
    assert_eq "$res" "$exp"
fi

# _sc_case_run <sandbox> <stub-rc> <stub-stdout> <case-filter> [NAME=value...]
# Runs THIS suite in a child process, filtered to one shellcheck case, with the
# stub first on PATH and the fake meminfo, and prints everything the child said.
# AUTOOS_SHELLCHECK_REQUIRED is cleared first so the caller's shell cannot leak
# into the verdict; pass it as a NAME=value argument when a test wants it.
_sc_case_run() {
    local d="$1" rc="$2" text="$3" filter="$4"
    shift 4
    rm -f "$d/log"
    env -u AUTOOS_SHELLCHECK_REQUIRED NO_COLOR=1 PATH="$d/bin:$PATH" AUTOOS_MEMINFO="$d/meminfo" \
        STUB_RC="$rc" STUB_OUT="$text" STUB_LOG="$d/log" "$@" \
        bash "$ROOT/tests/run-tests.sh" --filter="$filter" 2>&1
}

# _sc_verdict_is <child-output> <case-name> pass|fail|skip [text]
# True when the child reported exactly ONE result, of that kind, for that case,
# and (when given) the text appears in what it printed.
_sc_verdict_is() {
    local out="$1" name="$2" kind="$3" text="${4:-}" mark counts
    case "$kind" in
        pass) mark='✓'; counts='passed 1   failed 0   skipped 0' ;;
        fail) mark='✗'; counts='passed 0   failed 1   skipped 0' ;;
        skip) mark='-'; counts='passed 0   failed 0   skipped 1' ;;
    esac
    grep -qF -- "  $mark $name" <<<"$out" || return 1
    grep -qF -- "$counts" <<<"$out" || return 1
    [[ -z "$text" ]] || grep -qF -- "$text" <<<"$out"
}

# _sc_seen <child-output>: the result lines of a child run on one line, for failure messages.
_sc_seen() { grep -E '^ +[-✓✗] |^      |passed' <<<"$1" | tr '\n' '|'; }

_sc_oom_msg='shellcheck ran out of memory at a limit of 4608 MiB - run it in CI or on a host with more free memory (AUTOOS_SHELLCHECK_REQUIRED=1 makes this a failure)'

if it "shellcheck helper: the case SKIPS loudly, with the memory message, when shellcheck runs out of memory (rc 251)"; then
    d="$(_sc_sandbox)"
    out="$(_sc_case_run "$d" 251 'shellcheck: out of memory' 'shellcheck is clean')"
    rm -rf "$d"
    if _sc_verdict_is "$out" 'shellcheck is clean' skip "($_sc_oom_msg)"; then pass
    else fail "wanted one skip carrying the memory message, got: $(_sc_seen "$out")"; fi
fi

if it "shellcheck helper: AUTOOS_SHELLCHECK_REQUIRED=1 turns that out-of-memory skip into a failure with the same text"; then
    d="$(_sc_sandbox)"
    out="$(_sc_case_run "$d" 251 'shellcheck: out of memory' 'shellcheck is clean' AUTOOS_SHELLCHECK_REQUIRED=1)"
    rm -rf "$d"
    if _sc_verdict_is "$out" 'shellcheck is clean' fail "$_sc_oom_msg"; then pass
    else fail "wanted one failure carrying the memory message, got: $(_sc_seen "$out")"; fi
fi

if it "shellcheck helper: rc 137 with no output (the OOM killer) is the same out-of-memory skip, not an empty failure"; then
    d="$(_sc_sandbox)"
    ok=1
    out="$(_sc_case_run "$d" 137 '' 'shellcheck is clean')"
    _sc_verdict_is "$out" 'shellcheck is clean' skip "($_sc_oom_msg)" \
        || { ok=0; echo "not required: $(_sc_seen "$out")" >&2; }
    out="$(_sc_case_run "$d" 137 '' 'shellcheck is clean' AUTOOS_SHELLCHECK_REQUIRED=1)"
    _sc_verdict_is "$out" 'shellcheck is clean' fail "$_sc_oom_msg" \
        || { ok=0; echo "required: $(_sc_seen "$out")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an OOM kill is not reported as an out-of-memory verdict"; fi
fi

if it "shellcheck helper: a real finding (rc 1) FAILS with the finding, even when its source line says 'out of memory'"; then
    d="$(_sc_sandbox)"
    finding=$'In lib/linux/x.sh line 3:\necho "out of memory" $unquoted\n                     ^-- SC2086 (info): Double quote to prevent globbing and word splitting.'
    out="$(_sc_case_run "$d" 1 "$finding" 'shellcheck is clean')"
    rm -rf "$d"
    if _sc_verdict_is "$out" 'shellcheck is clean' fail 'In lib/linux/x.sh line 3:' && grep -qF 'SC2086' <<<"$out"; then pass
    else fail "wanted one failure that shows the finding, got: $(_sc_seen "$out")"; fi
fi

if it "shellcheck helper: a clean shellcheck run (rc 0) PASSES the case"; then
    d="$(_sc_sandbox)"
    out="$(_sc_case_run "$d" 0 '' 'shellcheck is clean')"
    rm -rf "$d"
    if _sc_verdict_is "$out" 'shellcheck is clean' pass; then pass
    else fail "wanted one pass, got: $(_sc_seen "$out")"; fi
fi

if it "shellcheck helper: the rescue-bootstrap case runs its own two files through the same helper and verdict"; then
    d="$(_sc_sandbox)"
    ok=1
    name='the rescue bootstrap template is shellcheck clean'
    out="$(_sc_case_run "$d" 0 '' 'template is shellcheck clean')"
    _sc_verdict_is "$out" "$name" pass \
        || { ok=0; echo "clean: $(_sc_seen "$out")" >&2; }
    grep -qx 'argv: -S warning templates/rescue-bootstrap.sh templates/ai-dispatcher.sh' "$d/log" 2>/dev/null \
        || { ok=0; echo "argv: $(grep '^argv' "$d/log" 2>&1 | tr '\n' '|')" >&2; }
    out="$(_sc_case_run "$d" 251 'shellcheck: out of memory' 'template is shellcheck clean')"
    _sc_verdict_is "$out" "$name" skip "($_sc_oom_msg)" \
        || { ok=0; echo "oom: $(_sc_seen "$out")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the rescue-bootstrap lint does not share the bounded helper and verdict"; fi
fi

if it "shellcheck helper: the case starts ONE shellcheck -S warning over setup.sh, every lib/linux/*.sh and tests/run-tests.sh"; then
    d="$(_sc_sandbox)"
    _sc_case_run "$d" 0 '' 'shellcheck is clean' >/dev/null
    want="argv: -S warning $(cd "$ROOT" && printf '%s ' setup.sh lib/linux/*.sh)tests/run-tests.sh"
    calls="$(grep -c '^argv: ' "$d/log" 2>/dev/null)"
    got="$(grep '^argv: ' "$d/log" 2>/dev/null)"
    rm -rf "$d"
    if [[ "$calls" == 1 && "$got" == "$want" ]]; then pass
    else fail "calls: [$calls] (want 1); argv: [$got] (want [$want])"; fi
fi

if it "shellcheck helper: SHELLCHECK_FILES, the suite's file set, is the file set CI lints"; then
    ok=1
    ci_yml="$ROOT/.github/workflows/ci.yml"
    grep -qF -- 'shellcheck -S warning setup.sh lib/linux/*.sh tests/run-tests.sh' "$ci_yml" \
        || { ok=0; echo "ci.yml no longer has: shellcheck -S warning setup.sh lib/linux/*.sh tests/run-tests.sh" >&2; }
    grep -qF -- 'tests/linux/*.sh' "$ci_yml" \
        || { ok=0; echo "ci.yml no longer has: shellcheck on tests/linux/*.sh parts" >&2; }
    ci_args="$(sed -n 's/^[[:space:]]*shellcheck -S warning //p' "$ci_yml" | head -n1)"
    if [[ -z "$ci_args" ]]; then
        ok=0; echo "no 'run: shellcheck -S warning ...' line found in ci.yml" >&2
    elif ! declare -p SHELLCHECK_FILES >/dev/null 2>&1; then
        ok=0; echo "SHELLCHECK_FILES is not defined" >&2
    else
        # shellcheck disable=SC2086  # deliberate: CI's shell expands the glob in that line, so must this
        want="$(cd "$ROOT" && printf '%s\n' $ci_args)"
        got="$(printf '%s\n' "${SHELLCHECK_FILES[@]}")"
        [[ "$got" == "$want" ]] || { ok=0; echo "suite lints [$(tr '\n' ' ' <<<"$got")] but CI lints [$(tr '\n' ' ' <<<"$want")]" >&2; }
    fi
    if (( ok )); then pass; else fail "the suite and .github/workflows/ci.yml lint different files"; fi
fi

