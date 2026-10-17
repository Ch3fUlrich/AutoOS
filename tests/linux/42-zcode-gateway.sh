# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── ZCode gateway bundle (detect only) ─────────────────────────────────────
describe "zcode gateway bundle"

# zcode_case_home: prints a fresh empty home for one case - under this
# worktree's own .scratch, never the system temp dir (the run this suite lives
# in fences /tmp; a test that cannot even make its home must not reach for the
# real one either). The caller rm -rf's it when done.
zcode_case_home() {
    mkdir -p "$ROOT/.scratch"
    mktemp -d "$ROOT/.scratch/42-zcode.XXXXXX"
}

# zcode_make_bundle <root>: a ZCode remote-server bundle as the desktop app
# leaves it after connecting to this host over SSH - an executable node binary
# and the gateway entry script, both regular files.
zcode_make_bundle() {
    mkdir -p "$1"
    : >"$1/node"
    chmod +x "$1/node"
    : >"$1/zcode-server.cjs"
}

# zcode_backups <dir>: how many .autoos-backup-* files sit anywhere under it.
zcode_backups() {
    find "$1" -name '*.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '
}

# zcode_run <home> [dry-run] [root]: runs setup_zcode_gateway_env in a subshell
# pinned to that case's SYS_HOME, with the ZCode env keys cleared first (a
# global left behind by an earlier part must not steer this case) and, when a
# root is given, ZCODE_SERVER_RUNTIME_ROOT pointed somewhere non-default.
# Prints the output, returns the function's exit code.
zcode_run() {
    local home="$1" dry="${2:-0}" root="${3:-}"
    (
        SYS_HOME="$home"
        AUTOOS_DRY_RUN="$dry"
        unset ZCODE_SERVER_RUNTIME_ROOT ZCODE_SERVER_NODE ZCODE_SERVER_ENTRY
        if [[ -n "$root" ]]; then ZCODE_SERVER_RUNTIME_ROOT="$root"; fi
        setup_zcode_gateway_env
    )
}

if it "zcode gateway: a bundle at the default location reports found and writes no env file"; then
    home="$(zcode_case_home)"
    zcode_make_bundle "$home/.zcode/server"
    out="$(zcode_run "$home" 2>&1)"; rc=$?
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ "$out" == *"ZCode server bundle found (default location) - OmniRoute uses it with no env lines"* ]] \
        || problems+="[found message wrong: $out] "
    [[ ! -e "$home/.omniroute/.env" ]] || problems+="[an env file was created] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: a missing bundle prints one message, returns 0, writes nothing"; then
    home="$(zcode_case_home)"
    out="$(zcode_run "$home" 2>&1)"; rc=$?
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ "$out" == *"ZCode server bundle missing ($home/.zcode/server/node, zcode-server.cjs) - in the ZCode desktop app add this host as an SSH remote and connect once; it installs the bundle and syncs the login. Nothing was changed."* ]] \
        || problems+="[missing message wrong: $out] "
    [[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" == "1" ]] \
        || problems+="[expected exactly one line, got: $out] "
    [[ ! -e "$home/.omniroute" ]] || problems+="[nothing may be created, but .omniroute exists] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: a custom ZCODE_SERVER_RUNTIME_ROOT writes exactly two env lines"; then
    home="$(zcode_case_home)"
    broot="$home/opt/zcode-bundle"
    zcode_make_bundle "$broot"
    out="$(zcode_run "$home" 0 "$broot" 2>&1)"; rc=$?
    env_file="$home/.omniroute/.env"
    expected="ZCODE_SERVER_NODE=$broot/node
ZCODE_SERVER_ENTRY=$broot/zcode-server.cjs"
    actual="$(cat "$env_file" 2>/dev/null)"
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ -f "$env_file" ]] || problems+="[env file missing] "
    [[ "$actual" == "$expected" ]] \
        || problems+="[want $(printf '%q' "$expected"), got $(printf '%q' "$actual")] "
    mode="$(_file_mode "$env_file" 2>/dev/null || echo '?')"
    [[ "$mode" == "600" ]] || problems+="[mode is $mode, want 600] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: a second run changes nothing and creates no new backup"; then
    home="$(zcode_case_home)"
    broot="$home/opt/zcode-bundle"
    zcode_make_bundle "$broot"
    env_file="$home/.omniroute/.env"
    out1="$(zcode_run "$home" 0 "$broot" 2>&1)"; rc1=$?
    cp -- "$env_file" "$home/after-first-run.env"
    before="$(zcode_backups "$home")"
    out2="$(zcode_run "$home" 0 "$broot" 2>&1)"; rc2=$?
    after="$(zcode_backups "$home")"
    problems=""
    (( rc1 == 0 && rc2 == 0 )) || problems+="<exit first $rc1 second $rc2> "
    cmp -s "$env_file" "$home/after-first-run.env" \
        || problems+="[second run changed the bytes] "
    [[ "$after" == "$before" ]] \
        || problems+="[backup count went from $before to $after] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: an existing env file survives byte for byte and gets one backup"; then
    home="$(zcode_case_home)"
    broot="$home/opt/zcode-bundle"
    zcode_make_bundle "$broot"
    mkdir -p "$home/.omniroute"
    # No trailing newline on purpose: the writer has to add one before its own
    # line or the two keys would fuse with the last pre-existing one.
    printf 'FOO=1\nBAR=two words' >"$home/.omniroute/.env"
    printf 'FOO=1\nBAR=two words' >"$home/original.env"
    out="$(zcode_run "$home" 0 "$broot" 2>&1)"; rc=$?
    env_file="$home/.omniroute/.env"
    expected='FOO=1
BAR=two words
'"ZCODE_SERVER_NODE=$broot/node
ZCODE_SERVER_ENTRY=$broot/zcode-server.cjs
"
    actual="$(cat "$env_file"; printf x)"; actual="${actual%x}"
    backups="$(zcode_backups "$home")"
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ "$actual" == "$expected" ]] \
        || problems+="[want $(printf '%q' "$expected"), got $(printf '%q' "$actual")] "
    [[ "$backups" == "1" ]] || problems+="[backup count is $backups, want 1] "
    backup="$(find "$home" -name '*.autoos-backup-*' -type f 2>/dev/null | head -1)"
    cmp -s "$backup" "$home/original.env" \
        || problems+="[the backup is not the original bytes] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: a different existing ZCODE_SERVER_NODE is kept and named by key only"; then
    home="$(zcode_case_home)"
    broot="$home/opt/zcode-bundle"
    zcode_make_bundle "$broot"
    mkdir -p "$home/.omniroute"
    printf 'ZCODE_SERVER_NODE=OTHER\n' >"$home/.omniroute/.env"
    out="$(zcode_run "$home" 0 "$broot" 2>&1)"; rc=$?
    env_file="$home/.omniroute/.env"
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ "$out" == *"ZCODE_SERVER_NODE"* ]] \
        || problems+="[the key was never named] "
    [[ "$out" != *"OTHER"* ]] \
        || problems+="[the existing value leaked into the output] "
    grep -q '^ZCODE_SERVER_NODE=OTHER$' "$env_file" \
        || problems+="[the existing different value was overwritten] "
    [[ "$(grep -c '^ZCODE_SERVER_NODE=' "$env_file")" == "1" ]] \
        || problems+="[ZCODE_SERVER_NODE appears more than once] "
    grep -q "^ZCODE_SERVER_ENTRY=$broot/zcode-server.cjs\$" "$env_file" \
        || problems+="[the other key was not added] "
    backups="$(zcode_backups "$home")"
    [[ "$backups" == "1" ]] || problems+="[backup count is $backups, want 1] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

if it "zcode gateway: AUTOOS_DRY_RUN creates neither the directory nor the env file"; then
    home="$(zcode_case_home)"
    broot="$home/opt/zcode-bundle"
    zcode_make_bundle "$broot"
    out="$(zcode_run "$home" 1 "$broot" 2>&1)"; rc=$?
    problems=""
    (( rc == 0 )) || problems+="<exit $rc> "
    [[ ! -e "$home/.omniroute" ]] \
        || problems+="[dry run created $home/.omniroute] "
    [[ "$out" == *"would add ZCODE_SERVER_NODE="* ]] \
        || problems+="[dry run did not say what it would write: $out] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    rm -rf "$home"
fi

# Leave no scratch of ours behind: every case above rm -rf's its own home, so
# this only drops the directory this part created.
rmdir "$ROOT/.scratch" 2>/dev/null || true