# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Run state, verification, undo ──────────────────────────────────────────
describe "state, verify and undo"

if it "macos catalog validates"; then
    out="$(catalog_validate catalog/macos.json 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "the cask column does not shift the other fields"; then
    # Each new TSV column is a chance to reintroduce the delimiter bug.
    catalog_load catalog/macos.json arm64 0
    i="$(catalog_index_of docker)"
    assert_eq "${CAT_CASK[i]}" "1"
fi

if it "a non-cask formula is flagged 0"; then
    catalog_load catalog/macos.json arm64 0
    i="$(catalog_index_of git)"
    assert_eq "${CAT_CASK[i]}" "0"
fi

if it "verify commands survive catalog loading"; then
    catalog_load catalog/linux.json x64 0
    i="$(catalog_index_of git)"
    assert_eq "${CAT_VERIFY[i]}" "git --version"
fi

if it "verification passes for something that is installed"; then
    AUTOOS_DRY_RUN=0 AUTOOS_VERIFY=1
    verify_component "bash --version" "bash" >/dev/null 2>&1
    assert_eq "$VERIFY_STATE" "verified"
fi

if it "verification reports unverified for a missing binary"; then
    AUTOOS_DRY_RUN=0 AUTOOS_VERIFY=1
    verify_component "definitely-not-a-real-binary --version" "ghost" >/dev/null 2>&1
    assert_eq "$VERIFY_STATE" "unverified"
fi

if it "--no-verify skips the check entirely"; then
    AUTOOS_VERIFY=0
    verify_component "definitely-not-a-real-binary" "ghost" >/dev/null 2>&1
    AUTOOS_VERIFY=1
    assert_eq "$VERIFY_STATE" "unchecked"
fi

if it "state survives a save/load round trip"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    AUTOOS_ANSWERS=(["omnigraph_url"]="https://example.invalid")
    autoos_state_save "$tmp" "light" "git tmux" "git" "tmux" "" >/dev/null 2>&1
    AUTOOS_ANSWERS=()
    STATE_PROFILE=""; STATE_SELECTED=""
    autoos_state_load "$tmp" >/dev/null 2>&1
    rm -f "$tmp"
    assert_eq "$STATE_PROFILE|$STATE_SELECTED|${AUTOOS_ANSWERS[omnigraph_url]:-}"               "light|git tmux|https://example.invalid"
fi

if it "a dry run saves no state"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=1
    autoos_state_save "$tmp" "light" "git" "" "" "" >/dev/null 2>&1
    AUTOOS_DRY_RUN=0
    if [[ -f "$tmp" ]]; then rm -f "$tmp"; fail "dry run wrote a state file"; else pass; fi
fi

# A post-install step that refuses to change a user's file records its id with
# autoos_record_failure; setup.sh folds those ids into the summary. The fold lives
# in lib/linux/install.sh next to the recorder, so both halves of one rule have
# one home (and the buckets are ids, not printed names: "OpenClaw Desktop CLI"
# shatters into three tokens when a list is joined for the state file).
if it "summary: a recorded post-install refusal is not also counted as installed"; then
    out="$(
        AUTOOS_EXTRA_FAILURES=(agent-skills)
        AUTOOS_RESULT_INSTALLED=(git agent-skills tmux)
        AUTOOS_RESULT_SKIPPED=(opencode-cli)
        AUTOOS_RESULT_FAILED=()
        autoos_fold_extra_failures
        printf 'installed=%s skipped=%s failed=%s list=[%s]\n' \
            "${#AUTOOS_RESULT_INSTALLED[@]}" "${#AUTOOS_RESULT_SKIPPED[@]}" \
            "${#AUTOOS_RESULT_FAILED[@]}" "${AUTOOS_RESULT_INSTALLED[*]}"
        printf 'failed=[%s]\n' "${AUTOOS_RESULT_FAILED[*]}"
    )"
    assert_eq "$out" "installed=2 skipped=1 failed=1 list=[git tmux]
failed=[agent-skills]"
fi

if it "summary: a recorded refusal that also failed its install is listed once"; then
    out="$(
        AUTOOS_EXTRA_FAILURES=(agent-skills)
        AUTOOS_RESULT_INSTALLED=()
        AUTOOS_RESULT_SKIPPED=()
        AUTOOS_RESULT_FAILED=(agent-skills)
        autoos_fold_extra_failures
        printf '%s' "${#AUTOOS_RESULT_FAILED[@]}"
    )"
    assert_eq "$out" "1"
fi

if it "summary: a recorded refusal is labelled failed (post-install)"; then
    catalog_load catalog/linux.json x64 0
    git_name="${CAT_NAME[$(catalog_index_of git)]}"
    skill_name="${CAT_NAME[$(catalog_index_of agent-skills)]}"
    out="$(
        AUTOOS_EXTRA_FAILURES=(agent-skills)
        AUTOOS_RESULT_FAILED=(agent-skills git not-a-real-id)
        for f in "${AUTOOS_RESULT_FAILED[@]}"; do printf '%s\n' "$(autoos_result_label "$f")"; done
    )"
    # One line per component: the old `for f in $failed_names` printed the words
    # of a multi-word display name as separate failures.
    assert_eq "$(printf '%s\n' "$out" | grep -c '')" "3"
    assert_eq "$(printf '%s\n' "$out" | sed -n 1p)" "$skill_name (post-install)"
    assert_eq "$(printf '%s\n' "$out" | sed -n 2p)" "$git_name"
    assert_eq "$(printf '%s\n' "$out" | sed -n 3p)" "not-a-real-id"
fi

# The fold above only matters if the run lives long enough to reach it. A
# postInstall is called bare from setup.sh's execute loop, which runs under
# `set -euo pipefail`, so one step returning non-zero killed the whole run
# mid-plan: no summary, no state file, and every component after it silently
# never installed. The real entry point is what is exercised here because the
# defect is in the call, not in the step.
if it "post-install: a failing postInstall is recorded and the run still finishes"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tree="$(mktemp -d)"
    mkdir -p "$tree/home"
    # A scratch tree: the code under test is linked, never copied-and-edited;
    # only the catalog gets two test-only components. `custom` installs nothing
    # (its work is entirely the postInstall), so the run touches nothing but $tree.
    ln -s "$ROOT/setup.sh" "$tree/setup.sh"
    ln -s "$ROOT/lib" "$tree/lib"
    cp -a catalog "$tree/catalog"
    python3 - "$tree/catalog/linux.json" <<'PY'
import json, sys
path = sys.argv[1]
doc = json.load(open(path, encoding="utf-8"))
doc["categories"][0]["components"] += [
    {"id": "postinstall-fail-demo", "name": "Demo failing step",
     "description": "test double", "provider": "custom", "package": "demo-fail",
     "profiles": [], "postInstall": "autoos_test_failing_post_install"},
    {"id": "postinstall-second-demo", "name": "Demo second component",
     "description": "test double", "provider": "custom", "package": "demo-second",
     "profiles": []},
]
json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
PY
    out="$(
        # The step is an exported shell function because a postInstall is, by
        # contract, just a named function run with no arguments (see
        # run_post_install); the fake keeps that shape.
        autoos_test_failing_post_install() { echo "DEMO post-install step ran"; return 1; }
        export -f autoos_test_failing_post_install
        HOME="$tree/home" bash "$tree/setup.sh" \
            --only postinstall-fail-demo,postinstall-second-demo \
            --yes --no-color --save-state "$tree/state.json" 2>&1
    )"; rc=$?
    saved="$(python3 -c '
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))["results"]
print("installed=[%s] failed=[%s]" % (",".join(r["installed"]), ",".join(r["failed"])))
' "$tree/state.json" 2>/dev/null || echo "no state file")"
    rm -rf "$tree"
    problems=""
    [[ "$out" == *"DEMO post-install step ran"* ]] \
        || problems+="[the fake postInstall never ran: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" == *"[2/2] Demo second component"* ]] \
        || problems+="[the run stopped at the failing step, step 2/2 never ran] "
    [[ "$out" == *"Demo failing step (post-install)"* ]] \
        || problems+="[the summary does not name the component a post-install failure] "
    # Listed once, as a failure — not also as installed.
    listed="$(printf '%s\n' "$out" | grep -c '(post-install)')"
    [[ "$listed" == "1" ]] || problems+="[the post-install failure line count is $listed, expected 1] "
    [[ "$saved" == "installed=[postinstall-second-demo] failed=[postinstall-fail-demo]" ]] \
        || problems+="[state file says: $saved] "
    (( rc == 1 )) || problems+="[exit code $rc, expected 1 (the run must not read as success)] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

# run_post_install owns the containment, so every step gets it without each one
# re-implementing it: it returns 0 whatever the step returned, warns with the
# step's own exit code, and records the component. With no component id (a step
# called from elsewhere) it records the step name, which the fold prints as-is.
if it "post-install: run_post_install contains the step's exit code and records it"; then
    out="$(
        demo_step_fails() { echo "STEP RAN"; return 3; }
        AUTOOS_EXTRA_FAILURES=()
        run_post_install demo_step_fails demo-component
        rc_own=$?
        printf 'rc=%s recorded=[%s]\n' "$rc_own" "${AUTOOS_EXTRA_FAILURES[*]:-}"
    )"
    problems=""
    [[ "$out" == *"STEP RAN"* ]] || problems+="[the step never ran: $out] "
    [[ "$out" == *"rc=0"* ]] || problems+="[run_post_install propagated a non-zero exit code] "
    [[ "$out" == *"recorded=[demo-component]"* ]] || problems+="[the component id was not recorded: $out] "
    [[ "$out" == *"demo_step_fails"* && "$out" == *"3"* ]] \
        || problems+="[no warning naming the step and its exit code: $out] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "post-install: a step with no component id records the step name"; then
    out="$(
        demo_step_bare_fails() { return 1; }
        AUTOOS_EXTRA_FAILURES=()
        run_post_install demo_step_bare_fails
        printf 'recorded=[%s]\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    )"
    assert_eq "$(printf '%s\n' "$out" | grep '^recorded=')" "recorded=[demo_step_bare_fails]"
fi

if it "undo restores a backed-up file"; then
    scratch="$(mktemp -d)"
    target="$scratch/.zshrc"
    printf 'ORIGINAL
' >"$target"
    AUTOOS_DRY_RUN=0
    append_line_once "$target" "AutoOS:test" "export X=1  # AutoOS:test" >/dev/null 2>&1
    grep -q 'AutoOS:test' "$target" || fail "setup for this test did not modify the file"
    ( SYS_HOME="$scratch"; autoos_undo 1 >/dev/null 2>&1 )
    body="$(cat "$target")"
    rm -rf "$scratch"
    assert_eq "$body" "ORIGINAL"
fi

# backup_file names its copy <file>.autoos-backup-<stamp> with ONE-SECOND
# resolution and cp overwrites, so two changing writes within a second used to
# destroy the user's original (the Windows side got Copy-AutoOSBackup for the
# same reason: base name, then -1, -2, ... never overwrite).
if it "backup: backup_file never overwrites a same-second backup"; then
    d="$(mktemp -d)"; f="$d/settings.json"; ok=1
    printf 'A' >"$f"; chmod 600 "$f"; p1="$(backup_file "$f" 20260101-000000)"; rc1=$?
    printf 'B' >"$f"; p2="$(backup_file "$f" 20260101-000000)"
    printf 'C' >"$f"; p3="$(backup_file "$f" 20260101-000000)"
    [[ "$p1" == "$f.autoos-backup-20260101-000000" ]]   || { ok=0; echo "first backup path: [$p1]" >&2; }
    [[ "$p2" == "$f.autoos-backup-20260101-000000-1" ]] || { ok=0; echo "second backup path: [$p2]" >&2; }
    [[ "$p3" == "$f.autoos-backup-20260101-000000-2" ]] || { ok=0; echo "third backup path: [$p3]" >&2; }
    (( rc1 == 0 )) || { ok=0; echo "first call rc=$rc1" >&2; }
    [[ "$(cat "$p1" 2>/dev/null)" == A && "$(cat "$p2" 2>/dev/null)" == B && "$(cat "$p3" 2>/dev/null)" == C ]] \
        || { ok=0; echo "bytes: [$(cat "$p1" 2>/dev/null)] [$(cat "$p2" 2>/dev/null)] [$(cat "$p3" 2>/dev/null)]" >&2; }
    [[ "$(stat -c '%a' "$p1" 2>/dev/null)" == 600 ]] || { ok=0; echo "the backup did not keep the file mode" >&2; }
    [[ "$(find "$d" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 3 ]] || { ok=0; echo "expected exactly 3 backups" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "backup_file overwrote or misnamed a backup"; fi
fi

# A name that is a DANGLING symlink is taken: `-e` follows the link and calls it
# free, and cp would then write through the link (GNU cp refuses; other cps
# create the link's target). backup_path tests -L too, and skips it.
if it "backup: backup_path skips a candidate name that is a dangling symlink"; then
    d="$(mktemp -d)"; f="$d/settings.json"; ok=1
    printf 'A\n' >"$f"
    base="$f.autoos-backup-20260101-000000"
    ln -s "$d/nowhere-0" "$base"; ln -s "$d/nowhere-1" "$base-1"
    [[ ! -e "$base" && -L "$base" ]] || { ok=0; echo "the fixture link does not dangle" >&2; }
    got="$(backup_path "$f" 20260101-000000)"
    [[ "$got" == "$base-2" ]] || { ok=0; echo "the next backup name is [${got##*/}], expected [settings.json.autoos-backup-20260101-000000-2]" >&2; }
    # End to end: the copy lands under the free name and touches neither link.
    p="$(backup_file "$f" 20260101-000000)"; rc=$?
    { (( rc == 0 )) && [[ "$p" == "$base-2" && -f "$p" && ! -L "$p" ]] && cmp -s "$f" "$p"; } \
        || { ok=0; echo "backup_file: rc=$rc path=[${p##*/}]" >&2; }
    [[ "$(readlink "$base")|$(readlink "$base-1")" == "$d/nowhere-0|$d/nowhere-1" ]] || { ok=0; echo "a dangling link was rewritten" >&2; }
    [[ ! -e "$d/nowhere-0" && ! -e "$d/nowhere-1" ]] || { ok=0; echo "the copy wrote through a link and created its target" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "backup_path treats a dangling symlink as a free name"; fi
fi

if it "backup: backup_file fails and prints nothing when the copy cannot be made"; then
    d="$(mktemp -d)"; ok=1
    out="$(backup_file "$d/does-not-exist" 20260101-000000 2>/dev/null)"; rc=$?
    (( rc != 0 )) || { ok=0; echo "rc=0 for a file that is not there" >&2; }
    [[ -z "$out" ]] || { ok=0; echo "printed [$out] for a backup that was not made" >&2; }
    [[ "$(find "$d" -type f | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "left a file behind" >&2; }
    # ...and the same call works once the file is there (a missing helper also "fails").
    printf 'X' >"$d/there"
    out="$(backup_file "$d/there" 20260101-000000 2>/dev/null)"; rc=$?
    (( rc == 0 )) && [[ "$out" == "$d/there.autoos-backup-20260101-000000" && -f "$out" ]] \
        || { ok=0; echo "a valid backup did not work: rc=$rc out=[$out]" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "backup_file does not report a failed copy honestly"; fi
fi

if it "backup: two changing writes in one second keep the user's original"; then
    d="$(mktemp -d)"; f="$d/.zshrc"
    printf 'ORIGINAL\n' >"$f"
    (
        AUTOOS_DRY_RUN=0
        date() { printf '20260101-000000\n'; }   # every backup lands in the same second
        append_line_once "$f" "AutoOS:one" "export ONE=1  # AutoOS:one"
        append_line_once "$f" "AutoOS:two" "export TWO=2  # AutoOS:two"
    ) >/dev/null 2>&1
    ok=1
    [[ "$(cat "$f.autoos-backup-20260101-000000" 2>/dev/null)" == "ORIGINAL" ]] \
        || { ok=0; echo "the oldest backup is [$(cat "$f.autoos-backup-20260101-000000" 2>/dev/null)], not the seed" >&2; }
    grep -q 'AutoOS:one' "$f.autoos-backup-20260101-000000-1" 2>/dev/null \
        || { ok=0; echo "the second backup (state before the second write) is missing" >&2; }
    grep -q 'AutoOS:two' "$f" || { ok=0; echo "the second write did not land" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a second write in the same second destroyed the user's original"; fi
fi

# ...-10 sorts before ...-2 by name, so "the newest backup" cannot be the last
# name: undo picks by modification time.
if it "backup: undo restores the newest backup, not the last name (-10 sorts before -2)"; then
    ok=1
    # Written one after the other (rising mtimes), and all inside one clock tick.
    for spread in rising equal; do
        scratch="$(mktemp -d)"; target="$scratch/.zshrc"; printf 'NOW\n' >"$target"
        n=0
        for sfx in "" -1 -2 -10; do
            printf 'state%s\n' "$sfx" >"$target.autoos-backup-20260101-000000$sfx"
            n=$((n+1))
            if [[ "$spread" == rising ]]; then touch -d "2026-01-01 00:00:0$n" "$target.autoos-backup-20260101-000000$sfx"
            else touch -d "2026-01-01 00:00:00" "$target.autoos-backup-20260101-000000$sfx"; fi
        done
        ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; autoos_undo 1 >/dev/null 2>&1 )
        body="$(cat "$target")"
        [[ "$body" == "state-10" ]] || { ok=0; echo "$spread mtimes: restored [$body], expected [state-10]" >&2; }
        rm -rf "$scratch"
    done
    if (( ok )); then pass; else fail "undo restored a backup that is not the newest"; fi
fi

# backup_newest broke a tie between equal mtimes with `sort -V`, which BSD and
# macOS sort do not have (the plain-sort fallback ranks -2 above -10). A `sort`
# that rejects -V the way BSD sort does must not change the answer, so the
# suffix is compared in bash. The stamp decides first: a later second beats an
# earlier second's higher counter.
if it "backup: backup_newest picks -10 over -2 on equal mtimes without sort -V"; then
    d="$(mktemp -d)"; bin="$(mktemp -d)"; ok=1
    real_sort="$(command -v sort)"
    printf '#!/bin/sh\nfor a in "$@"; do\n    case "$a" in\n        -V|--version-sort) echo "sort: invalid option -- V (test stub: a sort without version sort)" >&2; exit 2 ;;\n    esac\ndone\nexec %s "$@"\n' "$real_sort" >"$bin/sort"
    chmod +x "$bin/sort"
    for stamp in 20260101-000000 S; do
        f="$d/x-$stamp"; : >"$f"
        for sfx in "" -1 -2 -10; do
            : >"$f.autoos-backup-$stamp$sfx"; touch -d "2026-01-01 00:00:00" "$f.autoos-backup-$stamp$sfx"
        done
        got="$( PATH="$bin:$PATH"; backup_newest "$f" 2>&1 )"
        [[ "$got" == "$f.autoos-backup-$stamp-10" ]] || { ok=0; echo "stamp $stamp: the newest is [${got##*/}], expected [x-$stamp.autoos-backup-$stamp-10]" >&2; }
    done
    # A later stamp with no counter beats an earlier stamp's -10 (same mtime).
    f="$d/y"; : >"$f"
    for n in 20260101-000000 20260101-000000-10 20260101-000001; do
        : >"$f.autoos-backup-$n"; touch -d "2026-01-01 00:00:00" "$f.autoos-backup-$n"
    done
    got="$( PATH="$bin:$PATH"; backup_newest "$f" 2>&1 )"
    [[ "$got" == "$f.autoos-backup-20260101-000001" ]] || { ok=0; echo "mixed stamps: the newest is [${got##*/}], expected [y.autoos-backup-20260101-000001]" >&2; }
    # No backup at all: prints nothing, still succeeds.
    got="$( backup_newest "$d/none" 2>&1 )"; rc=$?
    { [[ -z "$got" ]] && (( rc == 0 )); } || { ok=0; echo "no backup: printed [$got] rc=$rc" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "backup_newest depends on sort -V to rank equal mtimes"; fi
fi

# backup_file returns non-zero when the copy fails (full disk, read-only
# directory). Nine call sites moved onto it and eight ignored that answer, so
# the file was then modified with NO backup - AGENTS.md hard rule 5 broken in
# exactly the case the helper detects. install_claude_autostart is the model:
# a failed backup leaves the file alone, says so, and reports failure.
#
# backup_fail_bin: prints a scratch dir holding a `cp` that refuses any
# operand named *.autoos-backup-* (creating a backup, or reading one for a
# restore) and delegates to the real cp for everything else. The copy fails
# deterministically, with no root tricks. The caller puts the dir first on PATH
# inside a subshell and removes it afterwards.
backup_fail_bin() {
    local d real; d="$(mktemp -d)"; real="$(command -v cp)"
    printf '#!/bin/sh\nfor a in "$@"; do\n    case "$a" in\n        *.autoos-backup-*) echo "cp: cannot copy $a: No space left on device (test stub)" >&2; exit 1 ;;\n    esac\ndone\nexec %s "$@"\n' "$real" >"$d/cp"
    chmod +x "$d/cp"
    printf '%s\n' "$d"
}

# backup_count <dir>: how many backups sit anywhere under <dir>.
backup_count() { find "$1" -name '*.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '; }

# backup_holds <file> <bytes>: some backup of <file> holds exactly <bytes>.
backup_holds() {
    local b
    for b in "$1".autoos-backup-*; do
        [[ -f "$b" && "$(cat "$b")" == "$2" ]] && return 0
    done
    return 1
}

if it "backup: append_line_once leaves the file alone and fails when its backup cannot be made"; then
    d="$(mktemp -d)"; f="$d/.zshrc"; bin="$(backup_fail_bin)"; ok=1
    printf 'ORIGINAL\n' >"$f"; cp "$f" "$f.orig"
    out="$( ( PATH="$bin:$PATH"; AUTOOS_DRY_RUN=0; append_line_once "$f" "AutoOS:t" "export T=1  # AutoOS:t" ) 2>&1 )"; rc=$?
    # cmp, not $(cat): command substitution strips trailing newlines, so a rewrite that only dropped the last one would pass.
    cmp -s "$f" "$f.orig" || { ok=0; echo "the file was modified without a backup: [$(cat "$f")]" >&2; }
    [[ "$out" == *"could not back up $f"* ]] || { ok=0; echo "no warning naming the file: [$out]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for a write that did not happen" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: the same call with a working cp does write, and backs up first.
    out="$( ( AUTOOS_DRY_RUN=0; append_line_once "$f" "AutoOS:t" "export T=1  # AutoOS:t" ) 2>&1 )"; rc=$?
    { grep -q 'AutoOS:t' "$f" && (( rc == 0 )) && [[ "$(backup_count "$d")" == 1 ]]; } \
        || { ok=0; echo "control run: rc=$rc backups=$(backup_count "$d") out=[$out]" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "append_line_once edits a file it could not back up"; fi
fi

if it "backup: install_agy does not run the vendor installer when a profile cannot be backed up"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    printf 'original zshrc\n' >"$home/.zshrc"; printf 'original profile\n' >"$home/.profile"
    cp "$home/.zshrc" "$home/.zshrc.orig"; cp "$home/.profile" "$home/.profile.orig"
    _agy_run() {   # _agy_run [stub-dir]
        (
            HOME="$home"; AUTOOS_DRY_RUN=0; [[ -z "${1:-}" ]] || PATH="$1:$PATH"
            curl() {
                local o="" p="" a
                for a in "$@"; do [[ "$p" == "-o" ]] && o="$a"; p="$a"; done
                printf '#!/usr/bin/env bash\ntouch "$HOME/vendor-ran"\necho "export PATH=x" >>"$HOME/.zshrc"\necho "export PATH=x" >>"$HOME/.profile"\n' >"$o"
            }
            install_agy
        ) 2>&1
    }
    out="$(_agy_run "$bin")"; rc=$?
    { cmp -s "$home/.zshrc" "$home/.zshrc.orig" && cmp -s "$home/.profile" "$home/.profile.orig"; } \
        || { ok=0; echo "a profile was edited without a backup: [$(cat "$home/.zshrc")] [$(cat "$home/.profile")]" >&2; }
    [[ ! -e "$home/vendor-ran" ]] || { ok=0; echo "the vendor installer ran although a profile could not be backed up" >&2; }
    [[ "$out" == *"could not back up $home/"* ]] || { ok=0; echo "no warning naming the profile: [${out:0:300}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for an installer that did not run" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the vendor installer runs and both profiles are kept.
    out="$(_agy_run)"; rc=$?
    { (( rc == 0 )) && [[ -e "$home/vendor-ran" && "$(backup_count "$home")" == 2 ]]; } \
        || { ok=0; echo "control run: rc=$rc backups=$(backup_count "$home") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "install_agy lets its vendor installer edit profiles it could not back up"; fi
fi

if it "backup: the Qwen Code routing does not run when settings.json cannot be backed up"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    mkdir -p "$home/.qwen"; printf '{"mine": true}\n' >"$home/.qwen/settings.json"
    cp "$home/.qwen/settings.json" "$home/.qwen/settings.json.orig"
    _qwen_run() {   # _qwen_run [stub-dir]
        (
            SYS_HOME="$home"; AUTOOS_DRY_RUN=0; OMNIROUTE_API_KEY=k1; AUTOOS_OMNIROUTE_KEY=k2
            [[ -z "${1:-}" ]] || PATH="$1:$PATH"
            has_cmd() { [[ "$1" == qwen || "$1" == omniroute ]]; }
            omniroute() { touch "$home/omniroute-ran"; printf '{"routed": true}\n' >"$SYS_HOME/.qwen/settings.json"; }
            route_detected_clis_to_gateway
        ) 2>&1
    }
    out="$(_qwen_run "$bin")"
    cmp -s "$home/.qwen/settings.json" "$home/.qwen/settings.json.orig" || { ok=0; echo "settings.json was modified without a backup: [$(cat "$home/.qwen/settings.json")]" >&2; }
    [[ ! -e "$home/omniroute-ran" ]] || { ok=0; echo "omniroute setup-qwen ran although settings.json could not be backed up" >&2; }
    [[ "$out" == *"could not back up $home/.qwen/settings.json"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the routing runs, after a backup of the original.
    out="$(_qwen_run)"
    { [[ -e "$home/omniroute-ran" && "$(backup_count "$home")" == 1 ]] \
        && [[ "$(cat "$home"/.qwen/settings.json.autoos-backup-*)" == '{"mine": true}' ]]; } \
        || { ok=0; echo "control run: backups=$(backup_count "$home") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "Qwen Code routing edits a settings.json it could not back up"; fi
fi

if it "backup: route_zed_to_proxy leaves settings.json alone and fails when its backup cannot be made"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    mkdir -p "$home/.config/zed"; printf '{"theme":"mine"}' >"$home/.config/zed/settings.json"
    cp "$home/.config/zed/settings.json" "$home/.config/zed/settings.json.orig"   # no final newline: a rewrite that ADDS one must fail too
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; route_zed_to_proxy ) 2>&1 )"; rc=$?
    cmp -s "$home/.config/zed/settings.json" "$home/.config/zed/settings.json.orig" || { ok=0; echo "settings.json was modified without a backup: [$(cat "$home/.config/zed/settings.json")]" >&2; }
    [[ "$out" == *"could not back up $home/.config/zed/settings.json"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for a routing that did not happen" >&2; }
    [[ "$out" != *"routed to OmniRoute"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the merge lands and the original is kept.
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; route_zed_to_proxy ) 2>&1 )"; rc=$?
    { (( rc == 0 )) && grep -q 'autoos-omniroute' "$home/.config/zed/settings.json" \
        && [[ "$(cat "$home"/.config/zed/settings.json.autoos-backup-*)" == '{"theme":"mine"}' ]]; } \
        || { ok=0; echo "control run: rc=$rc out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "route_zed_to_proxy edits a settings.json it could not back up"; fi
fi

if it "backup: register_antigravity_mcp_server leaves the config alone when its backup cannot be made"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    cfg="$home/.gemini/config/mcp_config.json"
    mkdir -p "${cfg%/*}"; printf '{"mcpServers": {"keep": {"command": "x"}}}\n' >"$cfg"
    before="$(cat "$cfg")"; cp "$cfg" "$cfg.orig"
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; register_antigravity_mcp_server newone '{"command":"npx"}' ) 2>&1 )"
    cmp -s "$cfg" "$cfg.orig" || { ok=0; echo "the config was modified without a backup: [$(cat "$cfg")]" >&2; }
    [[ "$out" == *"could not back up $cfg"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$out" != *"configured MCP server"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; register_antigravity_mcp_server newone '{"command":"npx"}' ) 2>&1 )"
    { grep -q '"newone"' "$cfg" && [[ "$(cat "$cfg".autoos-backup-*)" == "$before" ]]; } \
        || { ok=0; echo "control run: out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "register_antigravity_mcp_server edits a config it could not back up"; fi
fi

if it "backup: enable_project_mcp_server leaves settings.local.json alone when its backup cannot be made"; then
    repo="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    f="$repo/.claude/settings.local.json"
    mkdir -p "${f%/*}"; printf '{"theme":"mine"}\n' >"$f"; cp "$f" "$f.orig"
    out="$( ( AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; enable_project_mcp_server "$repo" omnigraph ) 2>&1 )"
    cmp -s "$f" "$f.orig" || { ok=0; echo "the file was modified without a backup: [$(cat "$f")]" >&2; }
    [[ "$out" == *"could not back up $f"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$out" != *"approved project MCP server"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$repo")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    out="$( ( AUTOOS_DRY_RUN=0; enable_project_mcp_server "$repo" omnigraph ) 2>&1 )"
    { grep -q 'omnigraph' "$f" && [[ "$(cat "$f".autoos-backup-*)" == '{"theme":"mine"}' ]]; } \
        || { ok=0; echo "control run: out=[${out:0:300}]" >&2; }
    rm -rf "$repo" "$bin"
    if (( ok )); then pass; else fail "enable_project_mcp_server edits a file it could not back up"; fi
fi

if it "backup: replace_or_append_marked_line leaves the file alone when its backup cannot be made"; then
    d="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    # Branch 1: the current line is there and a stale old-marker line must go.
    printf 'keep me\nstale line  # AutoOS:old\ncurrent line  # AutoOS:new\n' >"$d/purge"
    # Branch 2: only the old-marker line is there and it must be replaced in place.
    printf 'keep me\nstale line  # AutoOS:old\n' >"$d/replace"
    cp "$d/purge" "$d/purge.orig"; cp "$d/replace" "$d/replace.orig"
    out="$( ( PATH="$bin:$PATH"; AUTOOS_DRY_RUN=0
              replace_or_append_marked_line "$d/purge" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new"
              replace_or_append_marked_line "$d/replace" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new" ) 2>&1 )"
    cmp -s "$d/purge" "$d/purge.orig" || { ok=0; echo "the stale-line purge modified the file without a backup: [$(cat "$d/purge")]" >&2; }
    cmp -s "$d/replace" "$d/replace.orig" || { ok=0; echo "the replace modified the file without a backup: [$(cat "$d/replace")]" >&2; }
    [[ "$out" == *"could not back up $d/purge"* && "$out" == *"could not back up $d/replace"* ]] \
        || { ok=0; echo "no warning naming both files: [${out:0:500}]" >&2; }
    [[ "$out" != *"removed the stale"* && "$out" != *"replaced the"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp both edits land, each after a backup.
    ( AUTOOS_DRY_RUN=0
      replace_or_append_marked_line "$d/purge" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new"
      replace_or_append_marked_line "$d/replace" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new" ) >/dev/null 2>&1
    { ! grep -q 'AutoOS:old' "$d/purge" "$d/replace" && [[ "$(backup_count "$d")" == 2 ]]; } \
        || { ok=0; echo "control run: purge=[$(cat "$d/purge")] replace=[$(cat "$d/replace")]" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "replace_or_append_marked_line edits a file it could not back up"; fi
fi

if it "backup: setup_opencode_config leaves a config alone when its backup cannot be made"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    oc="$home/.config/opencode"; mkdir -p "$oc"
    printf '{"model": "anthropic/mine"}\n' >"$oc/opencode.json"
    printf '{"model": "anthropic/mine"}\n' >"$oc/config.json"
    for f in opencode.json config.json; do cp "$oc/$f" "$home/$f.orig"; done   # outside $oc: the stray-entries check lists it
    _oc_fail_run() {   # _oc_fail_run [stub-dir]
        ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT"
          [[ -z "${1:-}" ]] || PATH="$1:$PATH"
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          opencode_is_v2() { return 1; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config 2>&1 )
    }
    out="$(_oc_fail_run "$bin")"
    for f in opencode.json config.json; do
        cmp -s "$oc/$f" "$home/$f.orig" || { ok=0; echo "$f was modified without a backup: [$(head -c 120 "$oc/$f")]" >&2; }
    done
    [[ "$out" == *"could not back up $oc/config.json"* && "$out" == *"could not back up $oc/opencode.json"* ]] \
        || { ok=0; echo "no warning naming both files: [${out:0:500}]" >&2; }
    [[ "$(ls -A "$oc" | tr '\n' ' ')" == "config.json opencode.json " ]] || { ok=0; echo "stray entries: [$(ls -A "$oc" | tr '\n' ' ')]" >&2; }
    # Control: with a working cp both files are merged and the originals kept.
    out="$(_oc_fail_run)"
    { grep -q 'omniroute' "$oc/config.json" "$oc/opencode.json" \
        && backup_holds "$oc/config.json" '{"model": "anthropic/mine"}' \
        && backup_holds "$oc/opencode.json" '{"model": "anthropic/mine"}'; } \
        || { ok=0; echo "control run: backups=$(backup_count "$oc") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "setup_opencode_config edits a config it could not back up"; fi
    fi
fi

if it "backup: undo says restored only when the copy worked, and fails when it did not"; then
    scratch="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    target="$scratch/.zshrc"; printf 'NOW\n' >"$target"
    printf 'BEFORE\n' >"$target.autoos-backup-20260101-000000"
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; autoos_undo 1 ) 2>&1 )"; rc=$?
    [[ "$(cat "$target")" == NOW ]] || { ok=0; echo "the file changed although the restore failed: [$(cat "$target")]" >&2; }
    [[ "$out" != *"restored $target"* ]] || { ok=0; echo "claimed a restore that did not happen: [${out:0:400}]" >&2; }
    [[ "$out" == *"could not restore $target"* ]] || { ok=0; echo "no message naming the file: [${out:0:400}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for an undo that restored nothing" >&2; }
    # Control: with a working cp it restores, says so, and succeeds.
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; autoos_undo 1 ) 2>&1 )"; rc=$?
    { [[ "$(cat "$target")" == BEFORE && "$out" == *"restored $target"* ]] && (( rc == 0 )); } \
        || { ok=0; echo "control run: rc=$rc body=[$(cat "$target")] out=[${out:0:300}]" >&2; }
    rm -rf "$scratch" "$bin"
    if (( ok )); then pass; else fail "autoos_undo reports a restore that did not happen"; fi
fi

# Several originals, ONE restore failing (a read-only or vanished destination):
# the others are still restored, the failing one is named and stays as it was,
# the return code says the undo was not complete, and no line claims the failed
# file (or "everything") was restored. autoos_undo prints no summary line - the
# per-file "restored <file>" lines are the only claim, so they are counted.
if it "backup: undo restores the other files when one restore fails, names the failure and returns non-zero"; then
    scratch="$(mktemp -d)"; bin="$(mktemp -d)"; ok=1
    # A cp that refuses one destination (its last operand) and delegates for the rest.
    printf '#!/bin/sh\nfor a in "$@"; do last="$a"; done\ncase "$last" in\n    */.bashrc) echo "cp: cannot create regular file $last: Permission denied (test stub)" >&2; exit 1 ;;\nesac\nexec %s "$@"\n' "$(command -v cp)" >"$bin/cp"
    chmod +x "$bin/cp"
    # Sorted, the failing one sits in the middle: one restore comes before it, one after.
    for n in .aliases .bashrc .zshrc; do
        printf 'NOW\n' >"$scratch/$n"; printf 'BEFORE\n' >"$scratch/$n.autoos-backup-20260101-000000"
    done
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; autoos_undo 1 ) 2>&1 )"; rc=$?
    (( rc != 0 )) || { ok=0; echo "rc=0 for an undo in which a restore failed: [${out:0:300}]" >&2; }
    cmp -s "$scratch/.aliases" <(printf 'BEFORE\n') || { ok=0; echo ".aliases (before the failure) was not restored: [$(cat "$scratch/.aliases")]" >&2; }
    cmp -s "$scratch/.zshrc" <(printf 'BEFORE\n') || { ok=0; echo ".zshrc (after the failure) was not restored: [$(cat "$scratch/.zshrc")]" >&2; }
    cmp -s "$scratch/.bashrc" <(printf 'NOW\n') || { ok=0; echo "the failing file changed: [$(cat "$scratch/.bashrc")]" >&2; }
    [[ "$out" == *"could not restore $scratch/.bashrc"* ]] || { ok=0; echo "no message naming the failed file: [${out:0:400}]" >&2; }
    [[ "$out" != *"restored $scratch/.bashrc"* ]] || { ok=0; echo "claimed a restore that did not happen: [${out:0:400}]" >&2; }
    [[ "$out" == *"restored $scratch/.aliases"* && "$out" == *"restored $scratch/.zshrc"* ]] || { ok=0; echo "the restores that worked are not reported: [${out:0:400}]" >&2; }
    [[ "$(grep -c 'restored /' <<<"$out")" == 2 ]] || { ok=0; echo "expected exactly two restored lines, got $(grep -c 'restored /' <<<"$out")" >&2; }
    # Control: with a working cp the same undo restores all three and succeeds.
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; autoos_undo 1 ) 2>&1 )"; rc=$?
    { (( rc == 0 )) && [[ "$(grep -c 'restored /' <<<"$out")" == 3 ]] && cmp -s "$scratch/.bashrc" <(printf 'BEFORE\n'); } \
        || { ok=0; echo "control run: rc=$rc restored=$(grep -c 'restored /' <<<"$out") .bashrc=[$(cat "$scratch/.bashrc")]" >&2; }
    rm -rf "$scratch" "$bin"
    if (( ok )); then pass; else fail "autoos_undo stops at, or hides, a restore that failed"; fi
fi

if it "undo never uninstalls anything"; then
    # The safety property, asserted on the source rather than by removing software.
    # Lines that only PRINT a command for the operator (ui_warn/ui_muted/... messages, e.g. the
    # old-apt-package hint) are not commands the installer runs.
    if grep -vE '^[[:space:]]*ui_[a-z_]+[[:space:]]' lib/linux/install.sh | grep -qE '(apt-get remove|brew uninstall|npm uninstall)'; then
        fail "undo path contains an uninstall command"
    else pass; fi
fi

if it "configuration survives a save/load round trip"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    declare -A AUTOOS_ANSWERS=()
    AUTOOS_ANSWERS[git_user_name]="Alice Test"
    AUTOOS_ANSWERS[git_user_email]="alice@example.com"
    AUTOOS_ANSWERS[ollama_models]="nomic-embed-text"
    AUTOOS_DRY_RUN=0
    autoos_config_save "$tmp" "ai-coding" >/dev/null 2>&1
    AUTOOS_ANSWERS=()
    CONFIG_PROFILE=""
    autoos_config_load "$tmp"
    rm -f "$tmp"
    assert_eq "$CONFIG_PROFILE|${AUTOOS_ANSWERS[git_user_name]:-}|${AUTOOS_ANSWERS[git_user_email]:-}|${AUTOOS_ANSWERS[ollama_models]:-}" \
              "ai-coding|Alice Test|alice@example.com|nomic-embed-text"
fi

if it "setup.sh reads --config file and applies profile and answers"; then
    tmp="$(mktemp)"
    printf '{\n  "version": 1,\n  "profile": "light",\n  "answers": {\n    "git_user_name": "Test User"\n  }\n}\n' >"$tmp"
    out="$(bash setup.sh --config "$tmp" --dry-run --yes --no-color 2>&1)"
    rm -f "$tmp"
    if [[ "$out" == *"Using profile: light"* ]]; then pass
    else fail "did not use profile from config: $(printf '%s' "$out" | grep -i 'profile' || true)"; fi
fi

if it "serve.py and web UI provide configuration API and card"; then
    grep -q "/api/config" lib/linux/serve.py || fail "serve.py missing /api/config"
    grep -q "cardConfig" web/index.html || fail "web/index.html missing cardConfig"
    grep -q "saveConfiguration" web/index.html || fail "web/index.html missing saveConfiguration"
    pass
fi

# POST /api/config used to back up and rewrite autoos.config.json on EVERY
# save, so two identical saves produced a pile of backups of identical bytes,
# and the Linux backup name (.autoos-backup-<time_ns>) never matched the undo
# listing regex \.autoos-backup-(\d{8}-?\d{6})(?:-(\d+))?$ - it could not be
# ranked. An unchanged save must write nothing and create no backup; a changed
# one must back up under the standard second-resolution name, never overwriting.
_cfg_api_post_py() {
    python3 - <<'PY'
import atexit, importlib.util, io, json, pathlib, re, shutil, sys, tempfile, types
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-cfg-"))
atexit.register(shutil.rmtree, root, True)
spec = importlib.util.spec_from_file_location("autoos_serve", "lib/linux/serve.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["serve.py", str(root), "0", "127.0.0.1", "0"]
spec.loader.exec_module(mod)

def post(body):
    h = mod.Handler.__new__(mod.Handler)
    raw = json.dumps(body).encode()
    h.headers = {"Content-Length": str(len(raw))}
    h.rfile = io.BytesIO(raw)
    sent = {}
    h._json = lambda code, obj: sent.update(code=code, obj=obj)
    mod.Handler._post_config(h)
    return sent["code"], sent["obj"]

cfg = root / "autoos.config.json"
rx = re.compile(r"\.autoos-backup-(\d{8}-?\d{6})(?:-(\d+))?$")
problems = []

# 1: a changed save writes and backs up under the standard name...
cfg.write_text(json.dumps({"version": 1}) + "\n", encoding="utf-8")
body = {"version": 1, "profile": "workstation", "answers": {"git_user_name": "Alice"}}
code1, obj1 = post(body)
if code1 != 200 or obj1.get("ok") is not True: problems.append("changed:%s:%s" % (code1, obj1))
if obj1.get("unchanged"): problems.append("changed-claims-unchanged")
backups = sorted(p.name for p in root.glob("autoos.config.json.autoos-backup-*"))
if len(backups) != 1: problems.append("backups-after-change=%s" % backups)
elif not rx.search(backups[0]): problems.append("name-not-rankable:%s" % backups[0])
if json.loads(cfg.read_text()) != {"version": 1, "profile": "workstation", "answers": {"git_user_name": "Alice"}}:
    problems.append("content-after-change")

# 2: ...and repeating the identical save writes nothing and creates no backup.
before = cfg.read_text()
code2, obj2 = post(body)
if code2 != 200 or obj2.get("ok") is not True or obj2.get("unchanged") is not True:
    problems.append("unchanged:%s:%s" % (code2, obj2))
backups = sorted(p.name for p in root.glob("autoos.config.json.autoos-backup-*"))
if len(backups) != 1: problems.append("unchanged-added-a-backup:%s" % backups)
if cfg.read_text() != before: problems.append("unchanged-rewrote-the-file")

# 3: two changing saves inside one second keep separate backups (-1, never overwrite).
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-cfg-"))
atexit.register(shutil.rmtree, root, True)
mod.ROOT = root
cfg = root / "autoos.config.json"
mod.time = types.SimpleNamespace(strftime=lambda fmt: ".autoos-backup-20260101-000000")
cfg.write_text(json.dumps({"version": 1}) + "\n", encoding="utf-8")
post({"version": 1, "profile": "workstation"})
post({"version": 1, "profile": "workstation", "answers": {"git_user_name": "Alice"}})
backups = sorted(p.name for p in root.glob("autoos.config.json.autoos-backup-*"))
want = {"autoos.config.json.autoos-backup-20260101-000000", "autoos.config.json.autoos-backup-20260101-000000-1"}
if set(backups) != want: problems.append("same-second-backups=%s" % backups)
for name in backups:
    if not rx.search(name): problems.append("name-not-rankable:%s" % name)
if json.loads((root / backups[0]).read_text()) != {"version": 1}: problems.append("first-backup-holds-the-seed")

# 4: JSON types are strict - posting {"flag": true} over a file holding
#    {"flag": 1} is a CHANGE (a backup + a write), not an unchanged save:
#    True == 1 in Python, so a plain dict compare treats them as equal and
#    the route must compare serialized JSON, where "true" != "1".
cfg.write_text(json.dumps({"flag": 1}) + "\n", encoding="utf-8")
code4, obj4 = post({"flag": True})
if code4 != 200 or obj4.get("ok") is not True or obj4.get("unchanged"):
    problems.append("bool-vs-int-treated-as-unchanged:%s:%s" % (code4, obj4))
if json.loads(cfg.read_text()) != {"flag": True}: problems.append("bool-change-not-written")
if not any(json.loads(b.read_text()) == {"flag": 1} for b in root.glob("autoos.config.json.autoos-backup-*")):
    problems.append("int-seed-not-backed-up")
print(" ".join(problems) or "ok")
PY
}

if it "config: an unchanged POST saves nothing and creates no backup"; then
    assert_eq "$(_cfg_api_post_py 2>&1 | tail -n 1)" "ok"
fi

