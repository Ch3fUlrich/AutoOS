#!/usr/bin/env bash
# AutoOS Linux test suite.
#
# Zero dependencies on purpose: the whole point of this repo is to run on a
# machine where nothing is installed yet, so the tests must not need bats.
#
#   bash tests/run-tests.sh            run here
#   bash tests/run-tests.sh --wsl      re-run inside WSL2 (from Windows)
#   bash tests/run-tests.sh --filter catalog
#   bash tests/run-tests.sh --filter usb,catalog   comma = OR (shard union)
#
# Environment:
#   AUTOOS_SHELLCHECK_REQUIRED=1  a shellcheck that runs out of memory FAILS the
#                                 "shellcheck is clean" cases (default: a loud skip)
#   AUTOOS_MEMINFO=<file>         meminfo that sizes shellcheck's memory limit
#                                 (default /proc/meminfo; unreadable = no limit)
#
# No test installs anything. Providers are asserted on the PLANNED command,
# never on system state.
#
# shellcheck disable=SC2034
#   Several assignments below exist only to configure the sourced libraries
#   (AUTOOS_NO_COLOR, AUTOOS_DRY_RUN, AUTOOS_VERIFY) or to stand in for
#   detection results inside subshells; the linter sees no reader for them.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! bash -n "${BASH_SOURCE[0]}"; then
    printf 'run-tests.sh: syntax error, no test was run\n' >&2
    exit 2
fi
FILTER=""
for arg in "$@"; do
    case "$arg" in
        --wsl)
            wslpath_root="$(wslpath -a "$ROOT" 2>/dev/null || echo "$ROOT")"
            exec wsl.exe -- bash "$wslpath_root/tests/run-tests.sh"
            ;;
        --filter) shift; FILTER="${1:-}" ;;
        --filter=*) FILTER="${arg#--filter=}" ;;
    esac
done

PASS=0; FAIL=0; SKIP=0
SUMMARY_PRINTED=0
CURRENT=""
FAILED_NAMES=()

trap 'rc=$?; if [[ $rc -eq 0 && $SUMMARY_PRINTED -eq 0 ]]; then printf "run-tests.sh: ended before the summary line\n" >&2; exit 1; fi' EXIT

RED=''; GREEN=''; YELLOW=''; DIM=''; RESET=''
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\033[1;38;5;167m'; GREEN=$'\033[38;5;71m'
    YELLOW=$'\033[38;5;179m'; DIM=$'\033[2;38;5;245m'; RESET=$'\033[0m'
fi

describe() {
    printf '\n%s── %s%s\n' "$DIM" "$1" "$RESET"
}

it() {
    CURRENT="$1"
    if [[ -n "$FILTER" ]]; then
        # Comma-separated OR: --filter usb,catalog runs the union, so shards
        # can be disjoint partitions executed in parallel worktrees.
        local IFS=',' _terms _t _hit=0
        read -ra _terms <<< "$FILTER"
        for _t in "${_terms[@]}"; do
            if [[ -n "$_t" && "$CURRENT" == *"$_t"* ]]; then _hit=1; break; fi
        done
        if (( _hit == 0 )); then CURRENT=""; return 1; fi
    fi
    return 0
}

pass() { PASS=$((PASS+1)); printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$CURRENT"; }
fail() {
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$CURRENT")
    printf '  %s✗%s %s\n' "$RED" "$RESET" "$CURRENT"
    printf '      %s%s%s\n' "$DIM" "$1" "$RESET"
}
skip() { SKIP=$((SKIP+1)); printf '  %s-%s %s %s(%s)%s\n' "$YELLOW" "$RESET" "$CURRENT" "$DIM" "$1" "$RESET"; }

assert_eq() {
    if [[ "$1" == "$2" ]]; then pass; else fail "expected [$2] but got [$1]"; fi
}
assert_contains() {
    if [[ "$1" == *"$2"* ]]; then pass; else fail "expected to contain [$2] in [${1:0:200}]"; fi
}
assert_not_contains() {
    if [[ "$1" != *"$2"* ]]; then pass; else fail "expected NOT to contain [$2]"; fi
}
assert_ok() {
    if [[ "$1" -eq 0 ]]; then pass; else fail "expected exit 0, got $1"; fi
}

# release_sha256_of <Release file> <filename>
# Reads the checksum for <filename> out of the SHA256 section ONLY.
# The anchoring is the point: `apt-ftparchive release` emits MD5Sum (and SHA1)
# BEFORE SHA256, so an unanchored `/^ .*Packages$/` returns the MD5 and then
# compares it against a sha256sum — a guaranteed mismatch. That went unnoticed
# because the test that used it also restricted PATH to /usr/bin:/bin while
# believing it was exercising the no-apt-ftparchive fallback; on any host with
# apt-utils (WSL2 Ubuntu, Ubuntu Desktop — where AGENTS.md §5 says this suite
# will usually run) /usr/bin/apt-ftparchive is right there and it was not.
release_sha256_of() {
    awk -v want="$2" '
        /^SHA256:/ { in_sha = 1; next }
        /^[^ ]/    { in_sha = 0 }
        in_sha && $3 == want { print $1; exit }
    ' "$1" 2>/dev/null
}

# fake_usb <fixture-name>
# Prints a synthetic `lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE`
# document for one of the named fixtures in tests/helpers/fake_usb.py, for
# assignment straight into AUTOOS_FAKE_LSBLK. Never touches the live
# machine's disks (AGENTS.md §5) — see that file for what each fixture is
# shaped like and why.
fake_usb() {
    python3 "$ROOT/tests/helpers/fake_usb.py" "$1"
}

# _start_test_http_server <directory>
# Starts a throwaway python3 http.server bound to 127.0.0.1 on an
# OS-assigned port, serving <directory>, and prints "<pid> <port>" once it
# is actually listening (polls a port file the server writes itself before
# calling serve_forever(), so there is no read-before-bound race). Task 7's
# usb_write_ventoy tests use this instead of file:// URLs: curl on a
# Windows/Git-Bash test host cannot open a file:// URL built from an MSYS
# /tmp path (confirmed: "curl: (37) Could not open file /tmp/tmp.XXXXXX/...")
# - the same class of host-specific path trap MSYS_NO_PATHCONV already
# documents elsewhere in this suite. A real loopback HTTP server sidesteps
# it entirely and behaves identically under WSL2/Linux, where this suite
# usually runs (AGENTS.md §5). The caller must `kill` the printed pid when
# done - this function never cleans up after itself.
_start_test_http_server() {
    local dir="$1" portfile pid port i
    portfile="$(mktemp -u)"
    python3 -c '
import http.server, socketserver, sys, os
os.chdir(sys.argv[1])
httpd = socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
with open(sys.argv[2], "w") as f:
    f.write(str(httpd.server_address[1]))
httpd.serve_forever()
' "$dir" "$portfile" >/dev/null 2>&1 &
    pid=$!
    port=""
    for i in $(seq 1 50); do
        if [[ -s "$portfile" ]]; then port="$(cat "$portfile")"; break; fi
        sleep 0.1
    done
    rm -f "$portfile"
    printf '%s %s\n' "$pid" "$port"
}

# Function shellcheck_limit_kb. (Keep comment lines from starting with the linter's
# name: it parses those as directives and stops with SC1073.)
# Prints the address-space limit (KiB) run_shellcheck puts on shellcheck: 90% of
# MemAvailable + SwapFree from ${AUTOOS_MEMINFO:-/proc/meminfo}. Prints nothing
# (= no limit) when that file is unreadable (macOS, Git Bash) or has no
# MemAvailable line. Why bound it at all: shellcheck's memory grows faster than
# the input (about 1.2 GB for setup.sh + lib/linux, and this 9.4k-line file alone
# went past 2.5 GB), and an OOM kill used to surface as a `fail` with an EMPTY
# message. Under `ulimit -v` shellcheck instead says "out of memory" and exits 251.
shellcheck_limit_kb() {
    local meminfo="${AUTOOS_MEMINFO:-/proc/meminfo}" key val avail="" swap=0
    [[ -r "$meminfo" ]] || return 0
    while read -r key val _; do
        case "$key" in
            MemAvailable:) avail="$val" ;;
            SwapFree:)     swap="$val" ;;
        esac
    done < "$meminfo"
    [[ "$avail" =~ ^[0-9]+$ && "$swap" =~ ^[0-9]+$ ]] || return 0
    printf '%d\n' $(( (avail + swap) * 9 / 10 ))
}

# run_shellcheck FILES...
# ONE shellcheck process over FILES, `-S warning` (what CI runs), inside a
# subshell whose `ulimit -v` is shellcheck_limit_kb. Sets, for the caller:
#   SHELLCHECK_OUT         everything shellcheck printed (stdout + stderr)
#   SHELLCHECK_OOM         1 when it ran out of memory, else 0
#   SHELLCHECK_LIMIT_MIB   the limit that was in force, empty when there was none
# and returns shellcheck's own status, EXCEPT out-of-memory, which returns 3
# (test SHELLCHECK_OOM, not the 3: shellcheck itself uses 3 for a usage error).
# Out of memory is: rc 251 (the GHC runtime's heap overflow), rc 137 (SIGKILL
# from the kernel OOM killer), or - for any rc other than 0/1 - output that says
# "out of memory" / "Cannot allocate". rc 0/1 never count as OOM from the text:
# a finding prints the offending source line, and a line that merely mentions
# "out of memory" must not turn a real finding into a skip.
run_shellcheck() {
    local limit_kb rc
    SHELLCHECK_OOM=0
    limit_kb="$(shellcheck_limit_kb)"
    if [[ "$limit_kb" =~ ^[1-9][0-9]*$ ]]; then
        SHELLCHECK_LIMIT_MIB="$(( limit_kb / 1024 ))"
        SHELLCHECK_OUT="$( ( ulimit -v "$limit_kb" 2>/dev/null; shellcheck -S warning "$@" ) 2>&1 )"; rc=$?
    else
        SHELLCHECK_LIMIT_MIB=""
        SHELLCHECK_OUT="$(shellcheck -S warning "$@" 2>&1)"; rc=$?
    fi
    case "$rc" in
        251|137) SHELLCHECK_OOM=1 ;;
        0|1)     ;;
        *)       if grep -qiE 'out of memory|cannot allocate' <<<"$SHELLCHECK_OUT"; then SHELLCHECK_OOM=1; fi ;;
    esac
    if (( SHELLCHECK_OOM )); then return 3; fi
    return "$rc"
}

# report_shellcheck RC
# Turns the result of a run_shellcheck (RC, SHELLCHECK_OOM, SHELLCHECK_OUT) into
# ONE pass / fail / skip for the case that is running. A finding is a failure with
# its first lines. Out of memory is a LOUD skip that names the limit and the way
# to make it a failure (AUTOOS_SHELLCHECK_REQUIRED=1, for a host or CI job that is
# supposed to have the memory); it is never a silent pass and never an empty fail.
report_shellcheck() {
    local rc="$1" msg
    if (( SHELLCHECK_OOM )); then
        if [[ -n "$SHELLCHECK_LIMIT_MIB" ]]; then
            msg="shellcheck ran out of memory at a limit of ${SHELLCHECK_LIMIT_MIB} MiB"
        else
            msg="shellcheck ran out of memory with no limit of its own (the host ran short)"
        fi
        msg+=" - run it in CI or on a host with more free memory (AUTOOS_SHELLCHECK_REQUIRED=1 makes this a failure)"
        if [[ "${AUTOOS_SHELLCHECK_REQUIRED:-}" == 1 ]]; then fail "$msg"; else skip "$msg"; fi
    elif (( rc == 0 )); then
        pass
    else
        fail "$(printf '%s' "$SHELLCHECK_OUT" | head -20)"
    fi
}

# ─── Load the libraries under test ──────────────────────────────────────────
cd "$ROOT" || { echo "cannot enter $ROOT" >&2; exit 1; }

# The ONE list of files the "shellcheck is clean" case lints, and it must stay the
# list CI lints (.github/workflows/ci.yml: `shellcheck -S warning setup.sh
# lib/linux/*.sh tests/run-tests.sh`; a test compares the two). One process over
# all of them on purpose, not one per file: files on one command line resolve
# each other's `source=` directives, and alone setup.sh draws 8 false SC2034.
SHELLCHECK_FILES=(setup.sh lib/linux/*.sh tests/run-tests.sh)

# shellcheck source=../lib/linux/ui.sh
. lib/linux/ui.sh
# shellcheck source=../lib/linux/detect.sh
. lib/linux/detect.sh
# shellcheck source=../lib/linux/catalog.sh
. lib/linux/catalog.sh
# shellcheck source=../lib/linux/imagecache.sh
. lib/linux/imagecache.sh
# shellcheck source=../lib/linux/install.sh
. lib/linux/install.sh
# shellcheck source=../lib/linux/download.sh
. lib/linux/download.sh
# shellcheck source=../lib/linux/usb.sh
. lib/linux/usb.sh

AUTOOS_NO_COLOR=1
ui_init

printf '%sAutoOS Linux test suite%s  (%s)\n' "$DIM" "$RESET" "$ROOT"


# ─── Split test parts ────────────────────────────────────────
# shellcheck source=/dev/null
for __part in "$ROOT"/tests/linux/[0-9][0-9]-*.sh; do . "$__part"; done

# ─── Summary ────────────────────────────────────────────────────────────────
printf '\n%s%s%s\n' "$DIM" "$(printf '─%.0s' $(seq 1 56))" "$RESET"
printf '  %spassed %d%s   %sfailed %d%s   %sskipped %d%s\n' \
    "$GREEN" "$PASS" "$RESET" \
    "$( ((FAIL)) && printf '%s' "$RED" || printf '%s' "$DIM")" "$FAIL" "$RESET" \
    "$DIM" "$SKIP" "$RESET"
SUMMARY_PRINTED=1
if (( FAIL )); then
    printf '\n  failures:\n'
    for f in "${FAILED_NAMES[@]}"; do printf '    - %s\n' "$f"; done
    exit 1
fi
exit 0
