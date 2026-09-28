# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── End-to-end plan stability ──────────────────────────────────────────────
describe "end-to-end (dry run only)"

# e2e_setup <setup.sh args...> — the real entry point, for a machine that has
# nothing on it. HOME is a scratch dir and SUDO_USER a name with no passwd entry,
# so SYS_HOME falls back to HOME (lib/linux/detect.sh:200; the same device as
# tests/linux/22-herdr-sessions.sh:1049). Against the developer's own home a run
# answers for that machine instead of for the plan: here
# ~/.local/bin/graphify-mcp is uv's link, the graphify post-install refuses to
# move it, and the dry run exits 1 for a reason no check here has anything to do
# with. Prints the output, returns the exit code.
e2e_setup() {
    local home out rc
    home="$(mktemp -d)"
    out="$(SUDO_USER='autoos-no-such-user-e2e' HOME="$home" \
        bash setup.sh "$@" 2>&1)"; rc=$?
    rm -rf "$home"
    printf '%s\n' "$out"
    return "$rc"
}

# ─── Memoised dry runs (WS-PART13) ───────────────────────────────────────────
# Part 13 alone was the slowest CI shard (~3m49s): 11 full `setup.sh --dry-run`
# processes with the same argv repeated (--profile ai-coding in 2 tests,
# --profile workstation in 2, --profile light in 3). memo_dry_run runs each
# unique key once and serves repeats read-only (the caller gets output + rc,
# nothing else: no new process, no HOME, no file touched).
#
# Per-test decision — a test may use the memo ONLY when it inspects nothing but
# the printed output and rc:
# - "a dry run exits cleanly" (light, real HOME): memo (output + rc only).
# - "a dry run executes no commands at all" (workstation, real HOME): memo.
# - "a dry run creates none of the files its installers would": REAL run (it
#   checks a marker file under $SYS_HOME after the run, which a cached run
#   could neither create nor prove absent).
# - the four retirement tests ("--only agent-skills ...", "--profile never
#   plans the retired agent-skills id", "from-state: ...", "the ai-coding plan
#   carries ..."): moved to tests/linux/41-end-to-end-retirement.sh, all memo
#   e2e. The ai-coding pair shares one entry; from-state's argv holds a mktemp
#   path, so its key is unique per run (always a miss, but a safe one: the file
#   it names still exists while the memo runs it).
# - "two consecutive dry runs produce the same plan": two REAL runs, bypassing
#   the memo explicitly (it tests determinism; a hit would prove nothing).
# - "a dry run for --create-usb ... leaves the filesystem untouched": REAL run
#   (it inspects a scratch cache dir after the run and discards the output).
# - "an unknown component id is rejected": memo (output + rc only).
# - "--check-catalog succeeds" / "--check-catalog validates ...": memo, sharing
#   one entry (the second is a hit; both inspect only output + rc).
# - "catalog_probe_installed identifies installed components": no setup.sh run.
# - "--list shows installed components with a checkmark": memo (unique key).
# - the guard test below uses the memo directly to pin its keying.
#
# Parts are sourced in number order into one shell (tests/run-tests.sh), so a
# helper defined here in part 13 is already defined when part 41 is sourced:
# part 41 calls memo_dry_run without redefining it. That also holds for an
# isolated `AUTOOS_TEST_PARTS=41` run — every part file is still sourced, only
# the `it` lines outside the selection are skipped.
declare -A E2E_MEMO_OUT=()
declare -A E2E_MEMO_RC=()
E2E_MEMO_RUNS=0

# memo_dry_run <outvar> <rcvar> <e2e|real> [--] <setup.sh args...>
# Run `setup.sh <args...>` once per unique key; a repeat prints the cached
# output into <outvar> and sets <rcvar> without starting setup.sh. The key is
# the full argv plus every input that can change the result: every AUTOOS_*
# variable, PATH, and the e2e|real home mode. An e2e miss runs through
# e2e_setup, which makes a FRESH scratch HOME for that key and deletes it, so
# no two keys ever share a home and no hit ever makes one. Call as a plain
# command, never inside $(...): the cache lives in this shell.
memo_dry_run() {
    local _outvar="$1" _rcvar="$2" _mode="$3"
    shift 3
    if [[ "${1:-}" == "--" ]]; then shift; fi
    local _key _v _val _a
    _key="mode=${_mode}"$'\x1f'"PATH=${PATH-}"
    while IFS= read -r _v; do
        case "$_v" in
            AUTOOS_*)
                _val="${!_v-__unset__}"
                _key+=$'\x1f'"${_v}=${_val}"
                ;;
        esac
    done < <(compgen -v | sort)
    for _a in "$@"; do
        _key+=$'\x1f'"argv:${_a}"
    done
    if [[ -n "${E2E_MEMO_OUT["$_key"]+x}" ]]; then
        printf -v "$_outvar" '%s' "${E2E_MEMO_OUT["$_key"]}"
        printf -v "$_rcvar" '%s' "${E2E_MEMO_RC["$_key"]}"
        return 0
    fi
    local _out _rc
    if [[ "$_mode" == "e2e" ]]; then
        _out="$(e2e_setup "$@")"; _rc=$?
    else
        _out="$(bash setup.sh "$@" 2>&1)"; _rc=$?
    fi
    E2E_MEMO_OUT["$_key"]="$_out"
    E2E_MEMO_RC["$_key"]="$_rc"
    E2E_MEMO_RUNS=$((E2E_MEMO_RUNS + 1))
    printf -v "$_outvar" '%s' "$_out"
    printf -v "$_rcvar" '%s' "$_rc"
    return 0
}

if it "a dry run exits cleanly"; then
    memo_dry_run out rc real -- --profile light --dry-run --yes --no-color
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $(printf '%s' "$out" | tail -5)"; fi
fi

if it "a dry run executes no commands at all"; then
    # Asserts the property directly rather than sampling mtimes, which slid with
    # the clock and made this flaky: every action must be announced as "would
    # run:", and none may appear as an executed "run:".
    memo_dry_run out rc real -- --profile workstation --dry-run --yes --no-color
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    planned="$(printf '%s' "$out" | grep -c 'would ' || true)"
    # rc too: a prompt the dry run never asks must not fail it (review 2026-09-25).
    if [[ "$rc" -eq 0 && "$executed" -eq 0 && "$planned" -gt 0 ]]; then pass
    else fail "rc=$rc executed=$executed planned=$planned (expected rc 0, 0 executed, >0 planned)"; fi
fi

if it "a dry run creates none of the files its installers would"; then
    # REAL run on purpose: this checks a marker file under $SYS_HOME after the
    # run, so it must really run (a memo hit touches nothing by design).
    marker="$SYS_HOME/.autoos-omnigraph.env"
    had_marker=0; [[ -e "$marker" ]] && had_marker=1
    bash setup.sh --only omnigraph-client --dry-run --yes --no-color >/dev/null 2>&1
    now_marker=0; [[ -e "$marker" ]] && now_marker=1
    assert_eq "$now_marker" "$had_marker"
fi

if it "two consecutive dry runs produce the same plan"; then
    # Two REAL runs on purpose, bypassing the memo explicitly: this tests
    # determinism, and a cache hit would prove nothing about the second run.
    a="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    b="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    assert_eq "$a" "$b"
fi

if it "a dry run for --create-usb (usb write plan) leaves the filesystem untouched"; then
    # REAL run on purpose: this inspects a scratch cache dir after the run and
    # discards the output, so the memo (output + rc only) does not apply.
    # Task 6 Step 6: the same property every other dry-run test in this
    # block proves, for the USB feature specifically — a scratch cache dir
    # (never the real one) must come out exactly as it went in, proving
    # usb_plan/setup.sh's --create-usb handling never called fetch_verified
    # or touched the device.
    usb_scratch_cache="$(mktemp -d)"
    before_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    AUTOOS_CACHE_DIR="$usb_scratch_cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
        bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
        --engine ventoy --usb-device /dev/sdb >/dev/null 2>&1
    after_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    assert_eq "$after_listing" "$before_listing"
    rm -rf "$usb_scratch_cache"
fi

if it "an unknown component id is rejected"; then
    memo_dry_run out rc real -- --only definitely-not-a-thing --dry-run --yes --no-color
    if [[ $rc -ne 0 && "$out" == *"Unknown component"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | tail -3)"; fi
fi

if it "--check-catalog succeeds"; then
    memo_dry_run out rc real -- --check-catalog
    assert_ok "$rc"
fi

if it "--check-catalog validates all five catalogs by type, not just component catalogs"; then
    # Regression: setup.sh used to run every catalog/*.json through the
    # component-catalog validator, which rejects images.json and
    # engines.json outright (they have no 'categories' key). Assert every
    # file is actually reported valid, not just that the overall rc is 0 —
    # rc could go green for the wrong reason (e.g. an empty glob). A memo hit
    # on the previous test's entry: same argv, same env, same real home.
    memo_dry_run out rc real -- --check-catalog
    if [[ $rc -eq 0 && "$out" == *"engines.json is valid"* && "$out" == *"images.json is valid"* \
        && "$out" == *"linux.json is valid"* && "$out" == *"macos.json is valid"* && "$out" == *"windows.json is valid"*         && "$out" == *"agent-harness.json is valid"* \
        && "$out" == *"ai-registry.json is valid"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
fi

if it "catalog_probe_installed identifies installed components"; then
    # is_installed() (lib/linux/install.sh) delegates to detect_installed_status()
    # (lib/linux/detect.sh), which for a package-manager provider runs
    # `python3 - <provider> <package> <cask>` and has THAT python3 process
    # shell out further to dpkg-query/snap/brew/npm to query the host's real
    # package database. Asserting against the real git/dpkg on this machine
    # only passes on a host with dpkg - and a stub further down that chain
    # (e.g. a fake dpkg-query) does not help either: this suite also runs on
    # Git Bash on Windows, where python3 is a native Windows build whose
    # subprocess calls cannot invoke an extension-less shebang script or a
    # .cmd stub without a real dpkg-query to fall back to. So, same style as
    # the rescue-bootstrap sandbox's stateful python3 stub below (BS_BIN):
    # stub python3 itself, on a narrowed PATH, at the exact call shape
    # detect_installed_status uses - `python3 - <provider> <package> <cask>`,
    # answering "installed"/"not-detected" straight from argv$3 (the package)
    # without touching a package database at all. That leaves
    # catalog_probe_installed's OWN mapping logic (CAT_INSTALLED[i] set from
    # the probe result) the only thing under test, deterministically on any
    # host bash can run on.
    cpi_bin="$(mktemp -d)"
    cat > "$cpi_bin/python3" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-" ]] || exit 1
case "${3:-}" in
    tmux) printf 'not-detected\n' ;;
    *)    printf 'installed\n' ;;
esac
EOS
    chmod +x "$cpi_bin/python3"

    catalog_load catalog/linux.json x64 0
    PATH="$cpi_bin:$PATH" catalog_probe_installed
    rc=$?
    git_idx="$(catalog_index_of git)"
    tmux_idx="$(catalog_index_of tmux)"
    rm -rf "$cpi_bin"
    assert_ok "$rc"
    assert_eq "${CAT_INSTALLED[git_idx]}:${CAT_INSTALLED[tmux_idx]}" "1:0"
fi

if it "--list shows installed components with a checkmark"; then
    memo_dry_run out rc real -- --list
    if [[ $rc -eq 0 && "$out" == *"✓"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | head -10)"; fi
fi

if it "memoised dry runs key on argv, AUTOOS_* env and home mode (guard)"; then
    # Guards the memo itself, with fast argv on purpose: e2e --check-catalog
    # and e2e --list, keys no other test uses (every other --check-catalog /
    # --list here runs in real mode, so the mode half of the key differs).
    # A runs; B (one flag apart) runs again with different output; C (A
    # repeated) is a hit — no new run, same output + rc; D (A's argv under a
    # different AUTOOS_* value) runs again — the env is in the key — with the
    # same output (that variable changes nothing setup.sh reads).
    _guard_runs="$E2E_MEMO_RUNS"
    AUTOOS_E2E_MEMO_GUARD="one" memo_dry_run _a_out _a_rc e2e -- --check-catalog
    _problems=""
    (( _a_rc == 0 )) || _problems+="[A exit $_a_rc] "
    [[ "$E2E_MEMO_RUNS" -eq $((_guard_runs + 1)) ]] || _problems+="[A ran $((E2E_MEMO_RUNS - _guard_runs)) times, expected 1] "
    AUTOOS_E2E_MEMO_GUARD="one" memo_dry_run _b_out _b_rc e2e -- --list
    (( _b_rc == 0 )) || _problems+="[B exit $_b_rc] "
    [[ "$E2E_MEMO_RUNS" -eq $((_guard_runs + 2)) ]] || _problems+="[B was served from A's entry] "
    [[ "$_b_out" != "$_a_out" ]] || _problems+="[one flag apart gave identical output] "
    AUTOOS_E2E_MEMO_GUARD="one" memo_dry_run _c_out _c_rc e2e -- --check-catalog
    [[ "$E2E_MEMO_RUNS" -eq $((_guard_runs + 2)) ]] || _problems+="[repeat of A ran again] "
    [[ "$_c_out" == "$_a_out" && "$_c_rc" == "$_a_rc" ]] || _problems+="[hit served different output+rc] "
    AUTOOS_E2E_MEMO_GUARD="two" memo_dry_run _d_out _d_rc e2e -- --check-catalog
    [[ "$E2E_MEMO_RUNS" -eq $((_guard_runs + 3)) ]] || _problems+="[AUTOOS_* change shared A's entry] "
    [[ "$_d_out" == "$_a_out" && "$_d_rc" == "$_a_rc" ]] || _problems+="[unrelated AUTOOS_* var changed the result] "
    if [[ -z "$_problems" ]]; then pass; else fail "$_problems"; fi
fi
