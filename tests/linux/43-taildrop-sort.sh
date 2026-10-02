# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# D-410: ONE fetcher per host drains the shared Taildrop inbox and files the
# result by owner prefix. Everything below runs lib/linux/taildrop-sort.sh
# against a scratch tree with a fake `tailscale` on PATH, and then runs
# install_taildrop_sort twice against a scratch home - AGENTS.md §4, a second
# run must report `skipped`, never `installed`.

describe "taildrop sort"

# ─── fixtures and helpers ───────────────────────────────────────────────────

# td_fixtures <dir>: the pair the fake client delivers on every fetch - one
# name that declares an owner, one that does not.
td_fixtures() {
    mkdir -p -- "$1"
    printf 'alpha\n' >"$1/autoos__x.txt"
    printf 'beta\n' >"$1/notes.md"
}

# td_stub <dir>: install a fake `tailscale` into <dir>. The real client drains
# an inbox the whole host shares into the directory named by its last
# argument, so this does the same with the fixtures and records the call; it
# never opens a file.
td_stub() {
    cat >"$1/tailscale" <<'STUB'
#!/usr/bin/env bash
dest="${!#}"
printf 'tailscale %s\n' "$*" >>"$TDS_CALLS"
cp -R -- "$TDS_FIXTURES/." "$dest"/
exit 0
STUB
    chmod +x -- "$1/tailscale"
}

# td_sandbox [no-fixtures]: an empty sort root plus the fake client on PATH;
# the fixtures directory is left to the caller when its argument is
# no-fixtures (the hostile-name test builds names cp cannot create itself).
td_sandbox() {
    local sb mode="${1:-fixtures}"
    sb="$(mktemp -d)"
    mkdir -p -- "$sb/fixtures" "$sb/bin" "$sb/tree"
    td_stub "$sb/bin"
    if [[ "$mode" != "no-fixtures" ]]; then
        td_fixtures "$sb/fixtures"
    fi
    printf '%s\n' "$sb"
}

# td_run <sandbox>: one taildrop-sort fetch over that sandbox's fixtures.
# AUTOOS_TAILDROP_ROOT is the seam the script exposes for exactly this.
td_run() {
    local sb="$1"
    TDS_CALLS="$sb/calls" TDS_FIXTURES="$sb/fixtures" \
        PATH="$sb/bin:$PATH" AUTOOS_TAILDROP_ROOT="$sb/tree" \
        bash "$ROOT/lib/linux/taildrop-sort.sh" 2>&1
}

# ─── the sorter ─────────────────────────────────────────────────────────────

if it "taildrop-sort: an autoos__ name is filed under its owner folder"; then
    sb="$(td_sandbox)"; ok=1
    out="$(td_run "$sb")"; rc=$?
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    [[ -f "$sb/calls" ]] || { ok=0; echo "the fake client was never called: $out" >&2; }
    [[ -f "$sb/tree/autoos/x.txt" ]] || { ok=0; echo "not filed by owner: $(ls -AR "$sb/tree")" >&2; }
    [[ ! -e "$sb/tree/incoming/autoos__x.txt" ]] || { ok=0; echo "still in incoming/" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort did not file an owner-prefixed drop by owner"; fi
fi

if it "taildrop-sort: an unprefixed file goes to unsorted/ with exactly one sort.log line"; then
    sb="$(td_sandbox)"; ok=1
    out="$(td_run "$sb")"; rc=$?
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    [[ -f "$sb/tree/unsorted/notes.md" ]] || { ok=0; echo "not in unsorted/: $(ls -AR "$sb/tree")" >&2; }
    log="$(cat "$sb/tree/sort.log" 2>/dev/null || true)"
    lines="$(printf '%s\n' "$log" | grep -c . || true)"
    # One line: the owner-prefixed file logs nothing, the unprefixed one logs
    # exactly one <stamp> unsorted <name> record.
    (( lines == 1 )) || { ok=0; echo "expected one log line, got [$lines]: [$log]" >&2; }
    printf '%s\n' "$log" |
        grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z unsorted notes\.md$' \
        || { ok=0; echo "log line is not a UTC stamp + unsorted + name: [$log]" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort logged the wrong thing for an unprefixed drop"; fi
fi

if it "taildrop-sort: a name delivered twice becomes name.1, never a clobber"; then
    sb="$(td_sandbox)"; ok=1
    td_run "$sb" >/dev/null
    out="$(td_run "$sb")"; rc=$?
    (( rc == 0 )) || { ok=0; echo "second fetch rc=$rc: $out" >&2; }
    [[ -f "$sb/tree/autoos/x.txt" ]] || { ok=0; echo "the first delivery is gone" >&2; }
    [[ -f "$sb/tree/autoos/x.txt.1" ]] || { ok=0; echo "no clash name: $(ls -A "$sb/tree/autoos")" >&2; }
    [[ -f "$sb/tree/unsorted/notes.md" && -f "$sb/tree/unsorted/notes.md.1" ]] \
        || { ok=0; echo "unsorted clash: $(ls -A "$sb/tree/unsorted")" >&2; }
    n="$(grep -c . "$sb/tree/sort.log" 2>/dev/null || true)"
    (( n == 2 )) || { ok=0; echo "expected two log lines after two fetches, got [$n]" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort overwrote a file that had been delivered twice"; fi
fi

if it "taildrop-sort: hostile names ..__x and a__b/c cannot escape the sort root"; then
    # `..__x` must not be a parent-directory trick and `a__b/c` must not turn
    # the owner pattern into `owner=a, rest=b/c` and mkdir outside incoming/.
    # Nothing this run creates may land anywhere except under <sb>/tree.
    sb="$(td_sandbox no-fixtures)"; ok=1
    printf 'escape\n' >"$sb/fixtures/..__x"
    mkdir -p -- "$sb/fixtures/a__b"
    printf 'escape\n' >"$sb/fixtures/a__b/c"
    out="$(td_run "$sb")"; rc=$?
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    [[ -f "$sb/tree/unsorted/..__x" ]] || { ok=0; echo "..__x was not filed as an ordinary name: $(ls -A "$sb/tree/unsorted")" >&2; }
    [[ -f "$sb/tree/incoming/a__b/c" ]] || { ok=0; echo "a deeper fixture was moved: $(ls -AR "$sb/tree")" >&2; }
    for probe in "$sb/..__x" "$sb/c" "$sb/tree/a" "$sb/tree/..__x"; do
        [[ -e "$probe" ]] && { ok=0; echo "hostile name created $probe" >&2; }
    done
    # Everything outside the sort root must be ours: fixtures, the fake
    # client, and the call log it wrote.
    outside="$(ls -A "$sb" | LC_ALL=C sort | tr '\n' ' ')"
    [[ "$outside" == "bin calls fixtures tree " ]] \
        || { ok=0; echo "something was created outside the sort root: [$outside]" >&2; }
    top="$(ls -A "$sb/tree" | LC_ALL=C sort | tr '\n' ' ')"
    [[ "$top" == "incoming sort.log unsorted " ]] \
        || { ok=0; echo "the sort root gained a directory: [$top]" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort let a hostile name outside the sort root"; fi
fi

if it "taildrop-sort: a machine with no tailscale on PATH fetches nothing and exits 0"; then
    # A timer that errors every minute teaches people to ignore it. Only
    # mkdir and chmod can run before the check, so the PATH is narrowed to
    # exactly those two and the script is invoked by its absolute interpreter.
    ok=1; sb="$(mktemp -d)"
    mkdir -p -- "$sb/root" "$sb/narrow"
    bash_bin="$(command -v bash)"
    ln -s -- "$(command -v mkdir)" "$sb/narrow/mkdir"
    ln -s -- "$(command -v chmod)" "$sb/narrow/chmod"
    out="$(PATH="$sb/narrow" AUTOOS_TAILDROP_ROOT="$sb/root" \
           "$bash_bin" "$ROOT/lib/linux/taildrop-sort.sh" 2>&1)"; rc=$?
    (( rc == 0 )) || { ok=0; echo "rc=$rc (a missing client must not fail): $out" >&2; }
    [[ "$out" == *"taildrop-sort: tailscale is not on PATH - nothing fetched"* ]] \
        || { ok=0; echo "no missing-client line: [$out]" >&2; }
    [[ -d "$sb/root/incoming" && -d "$sb/root/unsorted" ]] \
        || { ok=0; echo "the sort tree was not created: [$(ls -A "$sb/root")]" >&2; }
    [[ -z "$(ls -A "$sb/root/incoming")" ]] || { ok=0; echo "something was fetched with no client" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort did not exit 0 on a machine without tailscale"; fi
fi

if it "taildrop-sort: the sorter never reads a moved file's content"; then
    # The bytes are not its business - it decides on names alone. Comments are
    # stripped first, and the words are matched as whole words so `tailscale`
    # and the script's own name do not answer for it.
    ok=1
    body="$(grep -Ev '^[[:space:]]*#' "$ROOT/lib/linux/taildrop-sort.sh" | sed 's/#.*//')"
    hits="$(printf '%s\n' "$body" |
        grep -En '(^|[^A-Za-z0-9_])(cat|head|tail|sha256sum)($|[^A-Za-z0-9_])' || true)"
    [[ -z "$hits" ]] || { ok=0; echo "content reader(s) in the sorter:"$'\n'"$hits" >&2; }
    if (( ok )); then pass; else fail "taildrop-sort opens the files it moves"; fi
fi

# ─── the installer ──────────────────────────────────────────────────────────

# td_install_run <sandbox>: one install_taildrop_sort against a scratch home.
# systemctl is a stub, so no unit is ever enabled for real; STATE and rc come
# back as a trailer line because INSTALL_SCRIPT_STATE cannot cross the
# subshell boundary (the pattern the claude-autostart and herdr tests use).
td_install_run() {
    local sbox="$1"
    (
        AUTOOS_ROOT="$ROOT"; SYS_HOME="$sbox/home"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
        NO_COLOR=1
        systemctl() { :; }
        INSTALL_SCRIPT_STATE=""
        rc=0
        install_taildrop_sort || rc=$?
        printf 'TDS_RESULT %s %s\n' "${INSTALL_SCRIPT_STATE:-installed}" "$rc"
    ) 2>&1
}
td_install_state() { sed -n 's/^TDS_RESULT \([a-z]*\) [0-9]*$/\1/p' <<<"$1"; }
td_install_rc() { sed -n 's/^TDS_RESULT [a-z]* \([0-9]*\)$/\1/p' <<<"$1"; }

if it "taildrop-sort: the installer lays down the script and both user units"; then
    sb="$(mktemp -d)"; ok=1
    out="$(td_install_run "$sb")"
    r1="$(td_install_rc "$out")"
    [[ "$r1" == "0" ]] || { ok=0; echo "first run rc=[$r1]: ${out:0:300}" >&2; }
    [[ "$(td_install_state "$out")" == "installed" ]] || { ok=0; echo "state=$(td_install_state "$out"): ${out:0:300}" >&2; }
    s="$sb/home/.local/share/autoos/taildrop-sort.sh"
    u="$sb/home/.config/systemd/user"
    [[ -f "$s" ]] || { ok=0; echo "the sorter was not installed to $s" >&2; }
    [[ "$(stat -c '%a' "$s" 2>/dev/null)" == "755" ]] \
        || { ok=0; echo "installed mode is [$(stat -c '%a' "$s" 2>&1)], systemd execs ExecStart directly" >&2; }
    for unit in taildrop-sort.service taildrop-sort.timer; do
        [[ -f "$u/$unit" ]] || { ok=0; echo "missing unit $unit" >&2; }
    done
    grep -q '^ExecStart=%h/.local/share/autoos/taildrop-sort.sh$' "$u/taildrop-sort.service" 2>/dev/null \
        || { ok=0; echo "the installed unit does not run the installed copy" >&2; }
    [[ "$out" == *"taildrop-sort installed"* ]] || { ok=0; echo "no install line: ${out:0:300}" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort was not installed completely"; fi
fi

if it "taildrop-sort: a second install run reports skipped and takes no backup"; then
    sb="$(mktemp -d)"; ok=1
    out1="$(td_install_run "$sb")"
    out2="$(td_install_run "$sb")"; s="$sb/home/.local/share/autoos/taildrop-sort.sh"
    r2="$(td_install_rc "$out2")"
    [[ "$r2" == "0" ]] || { ok=0; echo "second run rc=[$r2]" >&2; }
    [[ "$(td_install_state "$out2")" == "skipped" ]] \
        || { ok=0; echo "second run state=$(td_install_state "$out2"): ${out2:0:300}" >&2; }
    [[ "$out2" == *"taildrop-sort: everything is already current"* ]] \
        || { ok=0; echo "no 'already current' summary: ${out2:0:300}" >&2; }
    # Three files (the script and the two units), each reported current once.
    n="$(grep -c 'is already current' <<<"$out2" || true)"
    (( n == 4 )) || { ok=0; echo "expected 4 'already current' lines, got $n: ${out2:0:400}" >&2; }
    baks="$(find "$sb" -name '*.autoos-backup-*' | grep -c . || true)"
    (( baks == 0 )) || { ok=0; echo "an unchanged second run took $baks backup(s)" >&2; }
    [[ "$(stat -c '%a' "$s" 2>/dev/null)" == "755" ]] || { ok=0; echo "the second run disturbed the mode" >&2; }
    [[ "$out1" == *"taildrop-sort installed"* ]] || { ok=0; echo "first run installed nothing: ${out1:0:300}" >&2; }
    rm -rf -- "$sb"
    if (( ok )); then pass; else fail "taildrop-sort is not idempotent across a second run"; fi
fi