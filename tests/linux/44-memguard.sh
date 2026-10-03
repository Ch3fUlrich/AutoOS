# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "memguard (D-438)"

# ─── fixtures ────────────────────────────────────────────────────────────────
#
# MEMGUARD (lib/linux/memguard.sh) stops a systemd user scope from a user
# timer, so a test has exactly two hazards to remove: a real user manager, and
# writes outside this checkout. Both are stubbed here:
#
#   * each test builds a box with a bin/ directory holding a FAKE `systemctl`
#     that appends its argv and answers the read-only queries the guard makes.
#     `systemctl --user stop` is therefore recorded, never executed
#     (AGENTS.md §3: a destructive action is named and opt-in, so the real
#     `systemctl --user stop` is never called from this part).
#   * every seam - meminfo, unit list, stop log, state file - plus $HOME and
#     TMPDIR point inside that box, which lives under this working directory
#     and is removed before the verdict line.

# mg_scratch: one box per test, inside the working directory, so a failed run
# still leaves no litter outside the checkout and no state in $HOME.
mg_scratch() { mktemp -d "$ROOT/.mgtest.XXXXXX"; }

# mg_text <file>: the file joined on ';' for failure messages, empty if absent.
mg_text() {
    if [ -f "${1:-}" ]; then head -c 4000 "$1" | tr '\n' ';'; fi
    return 0
}

# mg_lines <file>: line count of a file, 0 when it was never written.
mg_lines() {
    if [ -f "${1:-}" ]; then awk 'END {print NR}' "$1"; else printf '0'; fi
}

# mg_raw <file>: the file's own text, untouched (mg_text joins lines with ';',
# which would break an anchored regex built on it). Command substitution drops
# the final newline only, so a one-line log still ends right at its content.
mg_raw() {
    if [ -f "${1:-}" ]; then cat "$1"; fi
    return 0
}

# mg_step <box>: seed the box with the systemctl stand-in. It appends every
# argv it sees, answers `--user list-units` from the box's unit list,
# `--user show` from the box's timestamp table, and records `--user stop` -
# the one call this part is allowed to observe. Two failure injections and one
# filter make the seams the guard's own claims turn on:
#   MG_LIST_FAIL=1      `list-units` dies (no user manager / bus error);
#   MG_SHOW_FAIL=<unit> `show` dies for exactly that unit (it vanished
#                        between list and show);
#   a name listed in the box's `inactive` file is not running, so the stub
#                        withholds it ONLY when the argv carries
#                        `--state=active` (with `--all` it is still shown).
mg_step() {
    local dir="$1"
    mkdir -p "$dir/bin" "$dir/home" "$dir/tmp"
    cat >"$dir/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
# Fake `systemctl --user` for the memguard part of the Linux suite.
printf '%s\n' "$*" >>"${MG_CALLS:?}"
case "${1:-} ${2:-}" in
    '--user list-units')
        if [ -n "${MG_LIST_FAIL:-}" ]; then
            printf 'Failed to connect to bus: no user manager\n' >&2
            exit 1
        fi
        src="${MG_UNITS_FILE:-}"
        [ -f "$src" ] || exit 0
        filter_active=0
        case " $* " in *' --state=active '*) filter_active=1 ;; esac
        if [ "$filter_active" -eq 1 ] && [ -f "${MG_INACTIVE_FILE:-}" ]; then
            # Only a running scope may be shown under --state=active: an
            # inactive one keeps its timestamp and would otherwise win
            # "newest" while the real hog survives.
            awk 'NR == FNR { dead[$0] = 1; next } !($1 in dead)' "$MG_INACTIVE_FILE" "$src"
        else
            cat "$src"
        fi
        ;;
    '--user show')
        if [ -n "${MG_SHOW_FAIL:-}" ] && [ "${MG_SHOW_FAIL}" = "${3:-}" ]; then
            printf 'Failed to get property: unit vanished\n' >&2
            exit 1
        fi
        ts="$(awk -v u="${3:-}" '$1 == u { print $2; exit }' "${MG_TS_FILE:-/dev/null}")"
        printf 'ActiveEnterTimestampMonotonic=%s\n' "${ts:-0}"
        ;;
    '--user stop')
        if [ -n "${MG_STOP_HOOK:-}" ]; then
            eval "$MG_STOP_HOOK"
        fi
        if [ -n "${MG_STOP_FAIL:-}" ]; then
            printf 'Failed to stop unit: permission denied\n' >&2
            exit 1
        fi
        printf '%s\n' "${3:-}" >>"${MG_STOPS:?}"
        ;;
esac
exit 0
STUB
    chmod +x "$dir/bin/systemctl"
}

# mg_run <box> [KEY=VAL ...]: run memguard.sh against a box. Every seam is
# passed exactly once: a KEY=VAL from the test replaces its default rather
# than appending a second entry for the same key, because `env A= A=x cmd`
# leaves different wins depending on the consumer. The guard's stdout (its
# log() writes every line there too, so StandardOutput=journal is true) is
# captured into the box for assertions.
mg_run() {
    local dir="$1"; shift
    local -A seams=(
        [AUTOOS_MEMGUARD_MIN_KIB]=1048576
        [AUTOOS_MEMGUARD_DISABLE]=""
        [AUTOOS_MEMGUARD_UNITS]=""
        [AUTOOS_MEMGUARD_MEMINFO]="$dir/meminfo"
        [AUTOOS_MEMGUARD_LOG]="$dir/memguard.log"
        [AUTOOS_MEMGUARD_STOP_LOG]="$dir/stop.log"
        [AUTOOS_MEMGUARD_STATE]="$dir/state"
        [MG_CALLS]="$dir/calls"
        [MG_STOPS]="$dir/stops"
        [MG_UNITS_FILE]="$dir/units"
        [MG_TS_FILE]="$dir/ts"
        [MG_INACTIVE_FILE]="$dir/inactive"
        [MG_LIST_FAIL]=""
        [MG_SHOW_FAIL]=""
        [MG_STOP_FAIL]=""
        [MG_STOP_HOOK]=""
    )
    local kv k
    for kv in "$@"; do k="${kv%%=*}"; seams["${k}"]="${kv#*=}"; done
    local -a words=()
    for k in "${!seams[@]}"; do words+=("$k=${seams[$k]}"); done
    env PATH="$dir/bin:$PATH" HOME="$dir/home" TMPDIR="$dir/tmp" "${words[@]}" \
        bash "$ROOT/lib/linux/memguard.sh" >"$dir/stdout" 2>"$dir/stderr"
}

# mg_drop <box>: the ONLY rm -rf in this part. It fires solely for a directory
# this file created - anything that is not <repo>/.mgtest.* is left alone.
mg_drop() {
    local d="${1:-}"
    case "$d" in
        "$ROOT"/.mgtest.*) [ -d "$d" ] && rm -rf -- "$d" ;;
        *) return 1 ;;
    esac
    return 0
}

# mg_install <box>: run install_memguard in a subshell against the box's HOME,
# printing the two values the idempotency contract turns on. The subshell
# re-sources what the function needs instead of relying on exported functions.
mg_install() {
    local dir="$1"
    (
        # shellcheck source=/dev/null
        . "$ROOT/lib/linux/ui.sh"
        # shellcheck source=/dev/null
        . "$ROOT/lib/linux/detect.sh"
        # shellcheck source=/dev/null
        . "$ROOT/lib/linux/install.sh"
        AUTOOS_ROOT="$ROOT"
        SYS_HOME="$dir/home"
        AUTOOS_DRY_RUN=0
        AUTOOS_SUDO=""
        export HOME="$dir/home" TMPDIR="$dir/tmp"
        PATH="$dir/bin:$PATH"
        # Exported, not merely assigned: the fake systemctl is a child
        # process, and an unexported MG_* would make it die on ${MG_CALLS:?}
        # - the calls file would stay empty and the idempotency evidence would
        # silently vanish.
        export MG_CALLS="$dir/calls" MG_STOPS="$dir/stops"
        export MG_UNITS_FILE="$dir/units" MG_TS_FILE="$dir/ts"
        mrc=0
        install_memguard || mrc=$?
        printf 'MG_STATE=[%s] MG_RC=%s\n' "${INSTALL_SCRIPT_STATE-UNSET}" "$mrc"
    )
}

# ─── behaviour ───────────────────────────────────────────────────────────────

if it "memguard: above the memory floor nothing is stopped and nothing is logged"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:    2097152 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope"
    mrc=$?
    if [ "$mrc" -eq 0 ] && [ ! -e "$sb/calls" ] && [ ! -e "$sb/stops" ] &&
        [ ! -e "$sb/stop.log" ] && [ ! -e "$sb/state" ]; then
        pass
    else
        fail "memguard above floor: rc=$mrc calls=[$(mg_text "$sb/calls")] stops=[$(mg_text "$sb/stops")] log=[$(mg_text "$sb/stop.log")]"
    fi
    mg_drop "$sb"
fi

if it "memguard: below the floor the first valid worker scope is stopped on the SECOND low sample"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    units=$'session-123.scope\nautoos-worker-run-a.scope\nautoos-worker-run-b.scope'
    # Sample one: below the floor and a candidate exists, but hysteresis has
    # seen only ONE low sample, so nothing may be stopped and nothing said.
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units"
    first_rc=$?
    first_stops="$(mg_text "$sb/stops")"
    first_lines="$(mg_lines "$sb/stop.log")"
    # Sample two, inside the 30 s window: the pair is complete, so the first
    # valid worker scope in the list is the victim.
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units"
    mrc=$?
    stops="$(mg_text "$sb/stops")"
    log="$(mg_raw "$sb/stop.log")"
    lines="$(mg_lines "$sb/stop.log")"
    stdout="$(mg_text "$sb/stdout")"
    pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 stopped autoos-worker-run-a\.scope$'
    if [ "$first_rc" -eq 0 ] && [ -z "$first_stops" ] && [ "$first_lines" -eq 0 ] &&
        [ "$mrc" -eq 0 ] && [ "$stops" = 'autoos-worker-run-a.scope;' ] &&
        [ "$lines" -eq 1 ] && [[ "$log" =~ $pat ]] &&
        [[ "$stdout" == *'stopped autoos-worker-run-a.scope'* ]]; then
        pass
    else
        fail "memguard stop: first=[rc=$first_rc stops=$first_stops lines=$first_lines] second=[rc=$mrc stops=$stops lines=$lines] log=[$log] stdout=[$stdout] calls=[$(mg_text "$sb/calls")]"
    fi
    mg_drop "$sb"
fi

if it "memguard: discovery asks for ACTIVE scopes and orders them by ActiveEnterTimestampMonotonic"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    # The oldest unit is listed FIRST: only the timestamp table can make the
    # newer one win, so a guard that trusted list order fails here.
    printf '%s\n' 'session-123.scope' 'autoos-worker-old.scope' \
        'autoos-worker-new.scope' 'autoos-worker-unstamped.scope' >"$sb/units"
    printf '%s\n' 'autoos-worker-old.scope 1000' 'autoos-worker-new.scope 9000' >"$sb/ts"
    # Empty AUTOOS_MEMGUARD_UNITS: discovery really happens, through newest_first.
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    first_rc=$?
    first_stops="$(mg_text "$sb/stops")"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    mrc=$?
    stops="$(mg_text "$sb/stops")"
    calls="$(mg_text "$sb/calls")"
    if [ "$first_rc" -eq 0 ] && [ -z "$first_stops" ] &&
        [ "$mrc" -eq 0 ] && [ "$stops" = 'autoos-worker-new.scope;' ] &&
        [[ "$calls" == *'--user list-units --state=active --no-legend --plain autoos-worker-*.scope'* ]] &&
        [[ "$calls" != *'--user list-units --all '* ]] &&
        [[ "$calls" == *'--user show autoos-worker-new.scope -p ActiveEnterTimestampMonotonic'* ]] &&
        [[ "$calls" == *'--user show autoos-worker-unstamped.scope -p ActiveEnterTimestampMonotonic'* ]]; then
        pass
    else
        fail "memguard discovery: first=[rc=$first_rc stops=$first_stops] rc=$mrc stops=[$stops] calls=[$calls]"
    fi
    mg_drop "$sb"
fi

if it "memguard: names that are not worker scopes are never stopped or killed"; then
    # Two routes to the candidate list, because they filter differently: the
    # injected seam hands whole names straight to the whitelist, discovery
    # chops them on whitespace first (awk '{print $1}').
    crafted=$'session-123.scope\nclaude-session.scope\nautoos-worker-x;rm.scope\nautoos-worker-has space.scope\nautoos-worker-../evil.scope'

    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$crafted"
    seam_rc=$?
    seam_stops="$(mg_text "$sb/stops")"
    seam_log="$(mg_raw "$sb/stop.log")"
    seam_lines="$(mg_lines "$sb/stop.log")"
    seam_calls="$(mg_text "$sb/calls")"
    mg_drop "$sb"

    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    printf '%s' "$crafted" >"$sb/units"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    disc_rc=$?
    disc_stops="$(mg_text "$sb/stops")"
    disc_log="$(mg_raw "$sb/stop.log")"
    disc_lines="$(mg_lines "$sb/stop.log")"
    disc_calls="$(mg_text "$sb/calls")"

    # The guard may not terminate a process itself: no kill/pkill/killall at
    # all, only `systemctl --user stop`. Comment lines are filtered into a
    # scratch file first - the wording of a design note must not decide this.
    grep -v '^[[:space:]]*#' "$ROOT/lib/linux/memguard.sh" >"$sb/guard-code.txt"
    grep -Eq '\b(pkill|killall|kill)\b' "$sb/guard-code.txt"
    kill_rc=$?

    nocall='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 below threshold, no agent run to stop$'
    if [ "$seam_rc" -eq 0 ] && [ -z "$seam_stops" ] && [ "$seam_lines" -eq 1 ] &&
        [[ "$seam_log" =~ $nocall ]] && [[ "$seam_calls" != *'--user stop'* ]] &&
        [ "$disc_rc" -eq 0 ] && [ -z "$disc_stops" ] && [ "$disc_lines" -eq 1 ] &&
        [[ "$disc_log" =~ $nocall ]] && [[ "$disc_calls" != *'--user stop'* ]] &&
        [ "$kill_rc" -ne 0 ]; then
        pass
    else
        fail "memguard whitelist: seam=[$seam_rc stops=$seam_stops lines=$seam_lines calls=$seam_calls log=$seam_log] discovery=[$disc_rc stops=$disc_stops lines=$disc_lines calls=$disc_calls log=$disc_log] kill-grep-rc=$kill_rc"
    fi
    mg_drop "$sb"
fi

if it "memguard: the no-run message is written once per quiet window"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=session-123.scope"
    first="$(mg_lines "$sb/stop.log")"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=session-123.scope"
    still="$(mg_lines "$sb/stop.log")"
    # Age the state file past QUIET_WINDOW_SECS (600): the window must reopen.
    printf '%s' "$(( $(date +%s) - 601 ))" >"$sb/state"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=session-123.scope"
    reopened="$(mg_lines "$sb/stop.log")"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=session-123.scope"
    held="$(mg_lines "$sb/stop.log")"
    if [ "$first" -eq 1 ] && [ "$still" -eq 1 ] && [ "$reopened" -eq 2 ] && [ "$held" -eq 2 ]; then
        pass
    else
        fail "memguard quiet window: lines were $first -> $still -> $reopened -> $held, log=[$(mg_text "$sb/stop.log")]"
    fi
    mg_drop "$sb"
fi

if it "memguard: AUTOOS_MEMGUARD_DISABLE makes the guard a no-op"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope" "AUTOOS_MEMGUARD_DISABLE=1"
    mrc=$?
    if [ "$mrc" -eq 0 ] && [ ! -e "$sb/calls" ] && [ ! -e "$sb/stops" ] &&
        [ ! -e "$sb/stop.log" ] && [ ! -e "$sb/state" ]; then
        pass
    else
        fail "memguard disabled: rc=$mrc calls=[$(mg_text "$sb/calls")] stops=[$(mg_text "$sb/stops")] log=[$(mg_text "$sb/stop.log")]"
    fi
    mg_drop "$sb"
fi

# ─── two-sample hysteresis ──────────────────────────────────────────────────
#
# ONE low sample may never stop a run (that is the whole point of the pair):
# it only records the verdict. The stop needs a second sample below the floor
# whose PREVIOUS sample was low no more than 30 s ago; a sample at/above the
# floor breaks the pair, and missing/garbled/stale state counts as "not low".

if it "memguard: one low sample records the verdict and stops nothing"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope"
    mrc=$?
    if [ "$mrc" -eq 0 ] && [ ! -e "$sb/stops" ] &&
        [ ! -e "$sb/stop.log" ] && [ -s "$sb/state" ]; then
        pass
    else
        fail "memguard one sample: rc=$mrc stops=[$(mg_text "$sb/stops")] log=[$(mg_text "$sb/stop.log")] state=[$(mg_text "$sb/state")]"
    fi
    mg_drop "$sb"
fi

if it "memguard: low then high then low never adds up to a pair"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope"
    r1=$?
    # A sample at/above the floor CLEARS the low verdict mid-pair.
    printf 'MemAvailable:    2097152 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope"
    r2=$?
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope"
    r3=$?
    stops="$(mg_text "$sb/stops")"
    lines="$(mg_lines "$sb/stop.log")"
    if [ "$r1" -eq 0 ] && [ "$r2" -eq 0 ] && [ "$r3" -eq 0 ] &&
        [ -z "$stops" ] && [ "$lines" -eq 0 ]; then
        pass
    else
        fail "memguard hysteresis: rc=$r1/$r2/$r3 stops=[$stops] lines=$lines log=[$(mg_text "$sb/stop.log")]"
    fi
    mg_drop "$sb"
fi

if it "memguard: stop attempt consumes pair; third sample stops nothing, next stop needs two fresh low samples"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    units_a="autoos-worker-run-a.scope"
    units_b="autoos-worker-run-b.scope"

    # Sample 1: first low sample for A (records verdict, stops nothing)
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units_a"
    r1=$?
    stops1="$(mg_text "$sb/stops")"

    # Sample 2: second low sample for A -> stops A, resets verdict to none
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units_a"
    r2=$?
    stops2="$(mg_text "$sb/stops")"
    state2="$(mg_raw "$sb/state")"

    # Sample 3 (10 s later, still low, with scope B listed):
    # Because state was reset to "none", prev_low is 0 -> stops NOTHING!
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units_b"
    r3=$?
    stops3="$(mg_text "$sb/stops")"

    # Two more fresh low samples:
    # Sample 3 was the 1st fresh low sample for B (recorded verdict "low").
    # Sample 4 is the 2nd fresh low sample for B -> stops B!
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units_b"
    r4=$?
    stops4="$(mg_text "$sb/stops")"
    state4="$(mg_raw "$sb/state")"

    if [ "$r1" -eq 0 ] && [ -z "$stops1" ] &&
        [ "$r2" -eq 0 ] && [ "$stops2" = 'autoos-worker-run-a.scope;' ] &&
        [[ "$state2" =~ ^none[[:space:]] ]] &&
        [ "$r3" -eq 0 ] && [ "$stops3" = 'autoos-worker-run-a.scope;' ] &&
        [ "$r4" -eq 0 ] && [ "$stops4" = 'autoos-worker-run-a.scope;autoos-worker-run-b.scope;' ] &&
        [[ "$state4" =~ ^none[[:space:]] ]]; then
        pass
    else
        fail "memguard pair consumption: r=$r1/$r2/$r3/$r4 stops=[$stops1 | $stops2 | $stops3 | $stops4] state2=[$state2] state4=[$state4]"
    fi
    mg_drop "$sb"
fi

if it "memguard: an inactive scope with a newer timestamp is never chosen"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    # The DEAD scope has the newer timestamp: it must be invisible, because
    # --all-listed units keep ActiveEnterTimestampMonotonic forever and would
    # otherwise win "newest" while the real hog runs untouched.
    printf '%s\n' 'autoos-worker-live.scope' 'autoos-worker-dead.scope' >"$sb/units"
    printf '%s\n' 'autoos-worker-live.scope 1000' 'autoos-worker-dead.scope 9000' >"$sb/ts"
    printf '%s\n' 'autoos-worker-dead.scope' >"$sb/inactive"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    first_rc=$?
    first_stops="$(mg_text "$sb/stops")"
    first_lines="$(mg_lines "$sb/stop.log")"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    mrc=$?
    stops="$(mg_text "$sb/stops")"
    calls="$(mg_text "$sb/calls")"
    # Run one records the verdict only (hysteresis owns the first sample); run
    # two stops the LIVE scope. Neither run may ever name the dead one, whose
    # newer timestamp must stay invisible under --state=active.
    if [ "$first_rc" -eq 0 ] && [ -z "$first_stops" ] && [ "$first_lines" -eq 0 ] &&
        [ "$mrc" -eq 0 ] &&
        [ "$stops" = 'autoos-worker-live.scope;' ] &&
        [[ "$calls" == *'--user list-units --state=active --no-legend --plain autoos-worker-*.scope'* ]] &&
        [[ "$calls" == *'--user show autoos-worker-live.scope -p ActiveEnterTimestampMonotonic'* ]] &&
        [[ "$calls" != *'--user show autoos-worker-dead.scope'* ]] &&
        [[ "$calls" != *'--user stop autoos-worker-dead.scope'* ]]; then
        pass
    else
        fail "memguard active-only: first=[rc=$first_rc stops=$first_stops lines=$first_lines] second=[rc=$mrc stops=$stops] calls=[$calls]"
    fi
    mg_drop "$sb"
fi

if it "memguard: a failing systemctl show stops nothing and logs one rate-limited line"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    printf '%s\n' 'autoos-worker-vanished.scope' 'autoos-worker-survivor.scope' >"$sb/units"
    printf '%s\n' 'autoos-worker-vanished.scope 9000' \
        'autoos-worker-survivor.scope 1000' >"$sb/ts"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=" "MG_SHOW_FAIL=autoos-worker-vanished.scope"
    r1=$?
    stops1="$(mg_text "$sb/stops")"
    lines1="$(mg_lines "$sb/stop.log")"
    log1="$(mg_raw "$sb/stop.log")"

    # Second tick inside quiet window: adds no line, still stops nothing
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=" "MG_SHOW_FAIL=autoos-worker-vanished.scope"
    r2=$?
    stops2="$(mg_text "$sb/stops")"
    lines2="$(mg_lines "$sb/stop.log")"
    pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 timestamp unknown for 1 unit\(s\), nothing stopped$'
    if [ "$r1" -eq 0 ] && [ "$r2" -eq 0 ] && [ -z "$stops1" ] && [ -z "$stops2" ] &&
        [ "$lines1" -eq 1 ] && [ "$lines2" -eq 1 ] && [[ "$log1" =~ $pat ]]; then
        pass
    else
        fail "memguard show failure: r1=$r1 r2=$r2 stops=[$stops1/$stops2] lines=$lines1/$lines2 log=[$log1]"
    fi
    mg_drop "$sb"
fi

if it "memguard: two units with the same timestamp is ambiguous, nothing stopped"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    printf '%s\n' 'autoos-worker-a.scope' 'autoos-worker-b.scope' >"$sb/units"
    printf '%s\n' 'autoos-worker-a.scope 5000' 'autoos-worker-b.scope 5000' >"$sb/ts"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS="
    r1=$?
    stops1="$(mg_text "$sb/stops")"
    lines1="$(mg_lines "$sb/stop.log")"
    log1="$(mg_raw "$sb/stop.log")"

    pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 newest scope ambiguous$'
    if [ "$r1" -eq 0 ] && [ -z "$stops1" ] && [ "$lines1" -eq 1 ] &&
        [[ "$log1" =~ $pat ]] && [[ "$log1" == *'ambiguous'* ]]; then
        pass
    else
        fail "memguard timestamp tie: r1=$r1 stops=[$stops1] lines=$lines1 log=[$log1]"
    fi
    mg_drop "$sb"
fi

if it "memguard: failing stop logs one rate-limited line, exits 0, no stopped line"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    units="autoos-worker-run-a.scope"

    # Sample 1: first low sample records verdict
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "MG_STOP_FAIL=1"
    r1=$?

    # Sample 2: second low sample attempts stop, fake systemctl stop fails
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "MG_STOP_FAIL=1"
    r2=$?
    stops="$(mg_text "$sb/stops")"
    lines="$(mg_lines "$sb/stop.log")"
    log="$(mg_raw "$sb/stop.log")"
    state="$(mg_raw "$sb/state")"

    pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 stop of autoos-worker-run-a\.scope failed, nothing claimed$'
    if [ "$r1" -eq 0 ] && [ "$r2" -eq 0 ] && [ -z "$stops" ] &&
        [ "$lines" -eq 1 ] && [[ "$log" =~ $pat ]] &&
        [[ "$log" != *' stopped '* ]] && [[ "$state" =~ ^none[[:space:]] ]]; then
        pass
    else
        fail "memguard stop failure: r1=$r1 r2=$r2 stops=[$stops] lines=$lines log=[$log] state=[$state]"
    fi
    mg_drop "$sb"
fi

if it "memguard: unwritable state directory exits 0 and leaves no tmp file"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    : >"$sb/not-a-dir"
    bad_state="$sb/not-a-dir/sub/memguard.state"

    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=autoos-worker-run-a.scope" "AUTOOS_MEMGUARD_STATE=$bad_state"
    mrc=$?
    tmp_left="$(find "$sb" -name '*state.tmp*' 2>/dev/null)"

    if [ "$mrc" -eq 0 ] && [ -z "$tmp_left" ]; then
        pass
    else
        fail "memguard unwritable state: rc=$mrc tmp_left=[$tmp_left]"
    fi
    mg_drop "$sb"
fi

if it "memguard: a failing list-units logs one rate-limited line, stops nothing, never aborts"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=" "MG_LIST_FAIL=1"
    r1=$?
    # The guard's line goes to the file AND stdout (the unit promises the
    # journal); mg_run re-truncates stdout on every run, so the stdout count is
    # taken right after the run that wrote it.
    lines1="$(mg_lines "$sb/stop.log")"
    stdout1="$(mg_lines "$sb/stdout")"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=" "MG_LIST_FAIL=1"
    r2=$?
    lines="$(mg_lines "$sb/stop.log")"
    stdout="$(mg_lines "$sb/stdout")"
    log="$(mg_raw "$sb/stop.log")"
    pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 list-units failed, nothing stopped$'
    if [ "$r1" -eq 0 ] && [ "$r2" -eq 0 ] && [ "$lines1" -eq 1 ] &&
        [ "$stdout1" -eq 1 ] && [ "$lines" -eq 1 ] && [ "$stdout" -eq 0 ] &&
        [ ! -e "$sb/stops" ] && [[ "$log" =~ $pat ]]; then
        pass
    else
        fail "memguard list-failure: rc=$r1/$r2 lines=$lines1/$lines stdout=$stdout1/$stdout stops=[$(mg_text "$sb/stops")] log=[$log]"
    fi
    mg_drop "$sb"
fi

if it "memguard: the stop log is bounded - past 256 KiB it rotates to one .1 generation"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    # Over the 262144-byte cap, built without any tool the suite may lack.
    yes 'x' 2>/dev/null | head -c 300000 >"$sb/stop.log" || true
    before="$(wc -c <"$sb/stop.log" | tr -d '[:space:]')"
    # Under the floor with no agent run: exactly one line, which must land in
    # a FRESH file after the old one was moved to .1.
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=session-123.scope"
    mrc=$?
    after="$(wc -c <"$sb/stop.log" | tr -d '[:space:]')"
    kept="$(wc -c <"$sb/stop.log.1" 2>/dev/null | tr -d '[:space:]')"
    lines="$(mg_lines "$sb/stop.log")"
    if [ "$mrc" -eq 0 ] && [ "$before" = "300000" ] && [ "$kept" = "300000" ] &&
        [ "$lines" -eq 1 ] && [ "$after" -gt 0 ] && [ "$after" -lt 262144 ] &&
        [ ! -e "$sb/stop.log.2" ]; then
        pass
    else
        fail "memguard log cap: rc=$mrc before=$before after=$after kept=[$kept] lines=$lines"
    fi
    mg_drop "$sb"
fi

if it "memguard: STOP_LOG under regular file parent swallows log failure, stops once, prints stdout"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    units="autoos-worker-run-a.scope"
    : >"$sb/not-a-dir"
    bad_log="$sb/not-a-dir/sub/stop.log"

    # Sample 1: first low sample records verdict "low"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STOP_LOG=$bad_log"
    r1=$?
    first_stops="$(mg_text "$sb/stops")"
    stderr1="$(mg_raw "$sb/stderr")"

    # Sample 2: second low sample stops unit; mkdir fails inside log() but is swallowed; stdout carries stopped line
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STOP_LOG=$bad_log"
    r2=$?
    stops="$(mg_text "$sb/stops")"
    stdout="$(mg_raw "$sb/stdout")"
    stderr2="$(mg_raw "$sb/stderr")"
    state="$(mg_raw "$sb/state")"

    if [ "$r1" -eq 0 ] && [ -z "$first_stops" ] && [ -z "$stderr1" ] &&
        [ "$r2" -eq 0 ] && [ "$stops" = 'autoos-worker-run-a.scope;' ] &&
        [[ "$stdout" == *'stopped autoos-worker-run-a.scope'* ]] &&
        [[ "$state" =~ ^none[[:space:]] ]] && [ -z "$stderr2" ]; then
        pass
    else
        fail "memguard uncreatable log dir: r1=$r1 r2=$r2 stops=[$stops] stdout=[$stdout] stderr=[$stderr2] state=[$state]"
    fi
    mg_drop "$sb"
fi

if it "memguard: STOP_LOG directory chmod 500 stops once, swallows log error, no second stop on third tick"; then
    if [ "$(id -u 2>/dev/null || printf 1)" -eq 0 ]; then
        skip "running as root (chmod 500 does not prevent directory writes)"
    else
        sb="$(mg_scratch)"
        mg_step "$sb"
        printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
        units="autoos-worker-run-a.scope"
        mkdir -p "$sb/ro-logdir"
        chmod 500 "$sb/ro-logdir"
        bad_log="$sb/ro-logdir/stop.log"

        # Sample 1: first low sample records verdict "low"
        mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STOP_LOG=$bad_log"
        r1=$?
        first_stops="$(mg_text "$sb/stops")"

        # Sample 2: second low sample stops unit; append to STOP_LOG fails but is swallowed; verdict reset to "none"
        mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STOP_LOG=$bad_log"
        r2=$?
        stops2="$(mg_text "$sb/stops")"
        stderr2="$(mg_raw "$sb/stderr")"

        # Sample 3: third low tick; because pair was consumed on sample 2, prev_low is 0 so no stop occurs
        mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STOP_LOG=$bad_log"
        r3=$?
        stops3="$(mg_text "$sb/stops")"

        chmod 700 "$sb/ro-logdir" 2>/dev/null || true

        if [ "$r1" -eq 0 ] && [ -z "$first_stops" ] &&
            [ "$r2" -eq 0 ] && [ "$stops2" = 'autoos-worker-run-a.scope;' ] &&
            [ -z "$stderr2" ] &&
            [ "$r3" -eq 0 ] && [ "$stops3" = 'autoos-worker-run-a.scope;' ]; then
            pass
        else
            fail "memguard chmod 500 logdir: r1=$r1 r2=$r2 r3=$r3 stops2=[$stops2] stops3=[$stops3] stderr2=[$stderr2]"
        fi
        mg_drop "$sb"
    fi
fi

if it "memguard: fail-closed: state unwritable on second tick stops nothing and logs failure"; then
    if [ "$(id -u 2>/dev/null || printf 1)" -eq 0 ]; then
        skip "running as root (chmod 500 does not prevent directory writes)"
    else
        sb="$(mg_scratch)"
        mg_step "$sb"
        printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
        units="autoos-worker-run-a.scope"
        statedir="$sb/statedir"
        mkdir -p "$statedir"
        extra_rm=""

        # If the test box filesystem does not enforce chmod 500 (e.g. DrvFs
        # under WSL), use a scratch directory in /tmp where POSIX permissions hold.
        probe="$sb/.ro-probe"
        mkdir -p "$probe"
        chmod 500 "$probe" 2>/dev/null || true
        if { : >"$probe/x"; } 2>/dev/null; then
            rm -f "$probe/x" 2>/dev/null || true
            if [ -d /tmp ] && [ -w /tmp ]; then
                statedir="$(mktemp -d /tmp/.mgstate.XXXXXX)"
                extra_rm="$statedir"
            fi
        fi
        chmod 700 "$probe" 2>/dev/null || true
        rm -rf "$probe" 2>/dev/null || true

        # Probe the effective statedir: if chmod 500 still fails to prevent writes
        # (e.g. running as root), skip cleanly.
        mkdir -p "$statedir/probe"
        chmod 500 "$statedir/probe" 2>/dev/null || true
        ro_enforced=1
        if { : >"$statedir/probe/x"; } 2>/dev/null; then
            ro_enforced=0
            rm -f "$statedir/probe/x" 2>/dev/null || true
        fi
        chmod 700 "$statedir/probe" 2>/dev/null || true
        rm -rf "$statedir/probe" 2>/dev/null || true

        if [ "$ro_enforced" -eq 0 ]; then
            if [ -n "$extra_rm" ]; then rm -rf "$extra_rm"; fi
            mg_drop "$sb"
            skip "running as root (chmod 500 does not prevent directory writes)"
        else
            state_file="$statedir/memguard.state"

            # Tick 1: first low sample records verdict "low"
            mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STATE=$state_file"
            r1=$?
            first_stops="$(mg_text "$sb/stops")"
            state1="$(mg_raw "$state_file")"

            # Make the state path unwritable for the second tick only
            chmod 500 "$statedir"

            # Tick 2: second low sample cannot record consumed pair; fail closed:
            # stop nothing, exit 0, and log the state not writable message
            mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STATE=$state_file"
            r2=$?
            stops2="$(mg_text "$sb/stops")"
            stdout2="$(mg_raw "$sb/stdout")"
            log2="$(mg_raw "$sb/stop.log")"

            chmod 700 "$statedir" 2>/dev/null || true

            pat='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z MemAvailable=500000 state not writable, nothing stopped$'
            if [ "$r1" -eq 0 ] && [ -z "$first_stops" ] && [[ "$state1" =~ ^low[[:space:]] ]] &&
                [ "$r2" -eq 0 ] && [ -z "$stops2" ] &&
                [[ "$stdout2" =~ $pat ]] && [[ "$log2" =~ $pat ]]; then
                pass
            else
                fail "memguard fail-closed: r1=$r1 r2=$r2 stops2=[$stops2] stdout2=[$stdout2] log2=[$log2]"
            fi
            if [ -n "$extra_rm" ]; then rm -rf "$extra_rm"; fi
            mg_drop "$sb"
        fi
    fi
fi

if it "memguard: state file written before stop; stop failure or unwritable state leaves verdict none"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    printf 'MemAvailable:     500000 kB\n' >"$sb/meminfo"
    units="autoos-worker-run-a.scope"
    mkdir -p "$sb/statedir"
    state_file="$sb/statedir/memguard.state"

    # Sample 1: first low sample records verdict "low"
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STATE=$state_file"
    r1=$?
    state1="$(mg_raw "$state_file")"

    # Sample 2: second low sample. In stop, fake systemctl captures that state is ALREADY
    # verdict "none", then kills state dir permissions and exits 1.
    mg_run "$sb" "AUTOOS_MEMGUARD_UNITS=$units" "AUTOOS_MEMGUARD_STATE=$state_file" \
        "MG_STOP_HOOK=cat '$state_file' >'$sb/state_during_stop'; chmod 500 '$sb/statedir'" \
        "MG_STOP_FAIL=1"
    r2=$?
    state2="$(mg_raw "$state_file")"
    state_during="$(mg_raw "$sb/state_during_stop")"

    chmod 700 "$sb/statedir" 2>/dev/null || true

    if [ "$r1" -eq 0 ] && [[ "$state1" =~ ^low[[:space:]] ]] &&
        [ "$r2" -eq 0 ] && [[ "$state_during" =~ ^none[[:space:]] ]] &&
        [[ "$state2" =~ ^none[[:space:]] ]]; then
        pass
    else
        fail "memguard state written before stop: r1=$r1 r2=$r2 state1=[$state1] state_during=[$state_during] state2=[$state2]"
    fi
    mg_drop "$sb"
fi

# ─── installer ───────────────────────────────────────────────────────────────

if it "memguard: the installer puts the script in .local/share/autoos and the double run reports installed then skipped"; then
    sb="$(mg_scratch)"
    mg_step "$sb"
    udd="$sb/home/.config/systemd/user"
    svc="$udd/memguard.service"
    tmr="$udd/memguard.timer"
    # The unit runs an INSTALLED copy, never the checkout (D-493).
    script="$sb/home/.local/share/autoos/memguard.sh"

    run1="$(mg_install "$sb" 2>&1)"
    r1=$?
    calls1="$(mg_text "$sb/calls")"
    : >"$sb/calls"
    run2="$(mg_install "$sb" 2>&1)"
    r2=$?
    calls2="$(mg_text "$sb/calls")"

    backups=""
    for f in "$udd"/*.autoos-backup-*; do
        [ -e "$f" ] && backups="$backups $f"
    done

    p=""
    [[ "$run1" == *'MG_STATE=[] MG_RC=0'* ]] || p="$p run1-state[$(printf '%s' "$run1" | tail -n 3)]"
    [[ "$run1" == *'installed memguard.service'* ]] || p="$p run1-no-service-line"
    [[ "$run1" == *"installed $script"* ]] || p="$p run1-no-script-line"
    [[ "$run1" == *'enabled memguard.timer'* ]] || p="$p run1-no-timer-enable"
    [[ "$run2" == *'MG_STATE=[skipped] MG_RC=0'* ]] || p="$p run2-state[$(printf '%s' "$run2" | tail -n 3)]"
    [[ "$run2" == *'skipped: already installed and current'* ]] || p="$p run2-no-skip-line"
    # Both halves of the copy exist after run one, byte-identical to the
    # checkout's script and executable (systemd execs ExecStart directly).
    [ -f "$script" ] || p="$p no-installed-script"
    if [ -f "$script" ]; then
        cmp -s "$script" "$ROOT/lib/linux/memguard.sh" || p="$p script-differs"
        # 755 is part of the contract (systemd execs ExecStart directly), but
        # drvfs-style filesystems record NO modes and report 777 for every
        # file, chmod included - probe with a scratch file, and only call our
        # copy wrong when that filesystem could have recorded 755 and didn't.
        mg_mode="$(stat -c '%a' "$script" 2>/dev/null || printf '?')"
        if [ "$mg_mode" != "755" ]; then
            probe="$sb/home/.autoos-mode-probe"
            : >"$probe"
            chmod 0755 "$probe" 2>/dev/null || true
            probe_mode="$(stat -c '%a' "$probe" 2>/dev/null || printf '?')"
            if [ "$probe_mode" = "755" ]; then
                # The filesystem does record modes, so 777 here is our copy.
                p="$p script-mode=$mg_mode(probe-recorded-$probe_mode)"
            fi
        fi
    fi
    [ "$r1" -eq 0 ] || p="$p rc1=$r1"
    [ "$r2" -eq 0 ] || p="$p rc2=$r2"

    if [ -f "$svc" ] && [ -f "$tmr" ]; then
        grep -Fqx "Documentation=file://$ROOT/docs/ai/memory-guard.md" "$svc" || p="$p svc-doc"
        grep -Fqx "Documentation=file://$ROOT/docs/ai/memory-guard.md" "$tmr" || p="$p tmr-doc"
        grep -Fqx 'ExecStart=%h/.local/share/autoos/memguard.sh' "$svc" || p="$p svc-exec"
        grep -Fqx 'OnUnitActiveSec=10s' "$tmr" || p="$p tmr-cadence"
        if grep -qE '@APPROOT@|@APPDIR@' "$svc" "$tmr"; then p="$p unrendered-placeholder"; fi
    else
        p="$p units-missing"
    fi
    [ -z "$backups" ] || p="$p backup-made[$backups]"

    # Idempotency evidence: run one reloads the user manager (the units are
    # new); run two must load nothing again. Both enable the timer.
    if [[ "$calls1" == *'--user daemon-reload'* ]]; then :; else p="$p run1-no-daemon-reload"; fi
    if [[ "$calls2" == *'--user daemon-reload'* ]]; then p="$p run2-reloaded-again"; fi
    if [[ "$calls2" == *'--user enable --now memguard.timer'* ]]; then :; else p="$p run2-no-timer-enable"; fi

    if [ -z "$p" ]; then pass; else fail "memguard installer:$p"; fi
    mg_drop "$sb"
fi

# ─── wiring ──────────────────────────────────────────────────────────────────

if it "memguard: catalog entry, units, docs, detect and dispatch all line up"; then
    p=""
    if command -v python3 >/dev/null 2>&1; then
        python3 "$ROOT/tests/helpers/catalog_has.py" "$ROOT/catalog/linux.json" memguard >/dev/null 2>&1 ||
            p="$p catalog-entry"
    else
        p="$p no-python3"
    fi
    [ -f "$ROOT/docs/ai/memory-guard.md" ] || p="$p docs-file"
    grep -Fq 'ai/memory-guard.md' "$ROOT/docs/README.md" || p="$p docs-index"
    grep -Fq 'ai/memory-guard.md' "$ROOT/docs/catalog.md" || p="$p docs-catalog"
    grep -Eq 'memguard\)[[:space:]]+install_memguard' "$ROOT/lib/linux/install.sh" || p="$p install-dispatch"
    # The literal $ must survive: we match the source, not expand a variable.
    # shellcheck disable=SC2016
    grep -Eq 'memguard\)[[:space:]]+\[\[ -f "\$SYS_HOME/\.local/share/autoos/memguard\.sh"' \
        "$ROOT/lib/linux/detect.sh" || p="$p detect-case"
    grep -Fqx 'Documentation=file://@APPROOT@/docs/ai/memory-guard.md' \
        "$ROOT/lib/linux/systemd/user/memguard.service" || p="$p service-doc-line"
    # The unit names the INSTALLED copy (%h = the user's home), never the
    # checkout the templates were rendered from.
    grep -Fqx 'ExecStart=%h/.local/share/autoos/memguard.sh' \
        "$ROOT/lib/linux/systemd/user/memguard.service" || p="$p service-exec-line"
    grep -Fq '.local/share/autoos/memguard.sh' "$ROOT/lib/linux/install.sh" ||
        p="$p installer-copy-path"
    # The floor is 1 GiB: a different default here makes the guard fire on an
    # idle machine, which is exactly what the design note rules out.
    # shellcheck disable=SC2016
    grep -Fq 'THRESHOLD_KIB="${AUTOOS_MEMGUARD_MIN_KIB:-1048576}"' \
        "$ROOT/lib/linux/memguard.sh" || p="$p threshold-default"
    # And a stop needs both gates: the contract shape and the character whitelist.
    grep -Fq "WORKER_SCOPE_RE='^autoos-worker-[A-Za-z0-9_.-]+\.scope\$'" \
        "$ROOT/lib/linux/memguard.sh" || p="$p scope-regex"
    grep -Fq 'QUIET_WINDOW_SECS=600' "$ROOT/lib/linux/memguard.sh" || p="$p quiet-window"
    # The claims the seat findings turn on: discovery sees RUNNING scopes
    # only, a stop needs a pair of low samples at most 30 s apart, and the
    # log file cannot grow without bound.
    grep -Fq 'list-units --state=active --no-legend --plain' \
        "$ROOT/lib/linux/memguard.sh" || p="$p active-only-list"
    grep -Fq 'LOW_SAMPLE_WINDOW_SECS=30' "$ROOT/lib/linux/memguard.sh" || p="$p hysteresis-window"
    grep -Fq 'STOP_LOG_CAP_BYTES=262144' "$ROOT/lib/linux/memguard.sh" || p="$p log-cap"
    bash -n "$ROOT/lib/linux/memguard.sh" || p="$p guard-syntax"
    bash -n "$ROOT/lib/linux/install.sh" || p="$p install-syntax"
    if [ -z "$p" ]; then pass; else fail "memguard wiring:$p"; fi
fi

# ─── item 7 of the MEMGUARD ADDENDUM: every spawn path is scoped ─────────────
#
# tools/autoos-agent.py and tools/autoos_agent_mcp.py are checked as source
# (not by installing anything): the test enumerates every subprocess spawn
# site, whitelists the scoped ones, and drives run_client/spawn/run_job far
# enough to prove an absent user bus WARNS instead of silently going
# unscoped. It never spawns a real agent.
if it "memguard: item 7 - every spawn path lands the run in an autoos-worker scope"; then
    out=""
    out="$( (cd "$ROOT" && python3 tests/test_worker_scope_spawn_paths.py) 2>&1)"
    mrc=$?
    if [ "$mrc" -eq 0 ]; then
        pass
    else
        fail "memguard item 7 suite: $(printf '%s' "$out" | tail -n 20 | tr '\n' ';')"
    fi
fi
