# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "claude autostart"

# A fixture transcript tree shaped exactly like ~/.claude/projects: one directory
# per working directory, one *.jsonl per session, with cwd and sessionId carried
# in the records themselves (ADR 0001). mtimes are set explicitly because the
# liveness window is the thing under test and git cannot preserve them.
cs_fixture_tree() {
    local root="$1"; shift
    local cwd uuid age slug dir
    while [ $# -gt 0 ]; do
        cwd="$1"; uuid="$2"; age="$3"; shift 3
        slug="$(printf '%s' "$cwd" | sed 's/[^A-Za-z0-9]/-/g')"
        dir="$root/projects/$slug"
        mkdir -p "$dir"
        printf '{"cwd": "%s", "uuid": "%s"}' "$cwd" "$uuid" |
            python3 "$ROOT/tests/helpers/make_transcript.py" "$dir/$uuid.jsonl" "$age"
    done
}

cs_engine() { python3 "$ROOT/lib/linux/claude_sessions.py" "$@"; }

if it "snapshot records every live session with the cwd read from its transcript"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/alpha 11111111-1111-1111-1111-111111111111 2 \
                           /home/u/beta  22222222-2222-2222-2222-222222222222 3
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=2 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" summary 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "2|/home/u/alpha|11111111-1111-1111-1111-111111111111|/home/u/beta" ]; then
        pass
    else
        fail "got [$got] from: $out"
    fi
fi

if it "a transcript older than the liveness window is not restored"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/fresh 11111111-1111-1111-1111-111111111111 5 \
                           /home/u/stale 22222222-2222-2222-2222-222222222222 900
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
           AUTOOS_CLAUDE_LIVENESS_WINDOW_MINS=240 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "/home/u/fresh" ]; then pass; else fail "expected only the fresh session, got [$got]"; fi
fi

if it "max_sessions keeps the most recently active, not an arbitrary slice"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/a 11111111-1111-1111-1111-111111111111 30 \
                           /home/u/b 22222222-2222-2222-2222-222222222222 2 \
                           /home/u/c 33333333-3333-3333-3333-333333333333 10
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=3 AUTOOS_CLAUDE_MAX_SESSIONS=2 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "/home/u/b|/home/u/c" ]; then pass; else fail "expected the two newest, got [$got]"; fi
fi

if it "one session is recorded once even when it left transcripts in two places"; then
    # Observed on a real machine: the same session id under two project slugs (a
    # scratchpad directory alongside the repo). Restoring it twice opens two
    # terminals fighting over one conversation.
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/proj          11111111-1111-1111-1111-111111111111 4 \
                           /home/u/proj/scratch  11111111-1111-1111-1111-111111111111 2
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    # The newer of the two wins, so the surviving record is the live one.
    if [ "$got" = "/home/u/proj/scratch" ]; then pass; else fail "expected one record, got [$got]"; fi
fi

if it "a snapshot with nothing live never clobbers a good one"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/alpha 11111111-1111-1111-1111-111111111111 2
    AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
        AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot >/dev/null 2>&1
    # The timer fires again mid-boot, before anything is up. Overwriting here is
    # what erases the record the next restore depends on.
    AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=0 \
        AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot >/dev/null 2>&1
    got="$(python3 "$ROOT/tests/helpers/read_state.py" count "$tmp/state.json" 2>/dev/null || echo ERR)"
    rm -rf "$tmp"
    if [ "$got" = "1" ]; then pass; else fail "snapshot was clobbered: [$got] session(s) left"; fi
fi

if it "plan emits one START per restorable session and SKIPs the rest with a reason"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/home/u/alpha","remote_control":true},
 {"session_uuid":"","name":"broken","cwd":"/home/u/broken","remote_control":false}]}
JSONEOF
    plan="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine plan 2>&1)"
    rm -rf "$tmp"
    starts="$(printf '%s\n' "$plan" | grep -c '^START' || true)"
    skips="$(printf '%s\n' "$plan" | grep -c '^SKIP' || true)"
    if [ "$starts" = "1" ] && [ "$skips" = "1" ] &&
       printf '%s\n' "$plan" | grep -q "START.11111111-1111-1111-1111-111111111111.alpha./home/u/alpha.1"; then
        pass
    else
        fail "unexpected plan ($starts START, $skips SKIP): $plan"
    fi
fi

if it "remote_control=never strips --rc from a session that was recorded with it"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/home/u/alpha","remote_control":true}]}
JSONEOF
    plan="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" AUTOOS_CLAUDE_REMOTE_CONTROL=never cs_engine plan 2>&1)"
    rm -rf "$tmp"
    if printf '%s\n' "$plan" | grep -q "alpha./home/u/alpha.0$"; then
        pass
    else
        fail "rc not stripped: $plan"
    fi
fi

if it "restore does nothing while autostart is disabled in the configuration"; then
    # `enabled` was in the shipped schema and the web card from the start, and no
    # code ever read it. It is a real pause switch now, or it should not be there.
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/tmp","remote_control":false}]}
JSONEOF
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh\ntouch "%s/launched"\n' "$tmp" > "$tmp/bin/claude"
    printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/tmux"
    chmod +x "$tmp/bin/claude" "$tmp/bin/tmux"
    out="$(PATH="$tmp/bin:$PATH" AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" \
           AUTOOS_CLAUDE_ENABLED=0 AUTOOS_NO_COLOR=1 \
           bash "$ROOT/lib/linux/claude-sessions.sh" restore 2>&1)"
    launched=0; [ -f "$tmp/launched" ] && launched=1
    rm -rf "$tmp"
    if [ "$launched" -eq 0 ] && printf '%s\n' "$out" | grep -qi 'disabled'; then
        pass
    else
        fail "launched=$launched out: $out"
    fi
fi

if it "restore refuses to start a TUI when no terminal host is available"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/tmp","remote_control":false}]}
JSONEOF
    # A PATH carrying a fake `claude` but neither herdr nor tmux: the session is
    # restorable, there is simply nowhere a human could ever see it (ADR 0002).
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh\ntouch "%s/launched"\n' "$tmp" > "$tmp/bin/claude"
    chmod +x "$tmp/bin/claude"
    # Host discovery runs for real (`auto`); both hosts are made unusable rather
    # than merely absent, so the result is the same on a developer box that has
    # tmux installed as on a bare one.
    printf '#!/bin/sh\nexit 1\n' > "$tmp/bin/tmux"; chmod +x "$tmp/bin/tmux"
    out="$(PATH="$tmp/bin:$PATH" HERDR_BIN="$tmp/bin/herdr-absent" \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" \
           AUTOOS_CLAUDE_TERMINAL_HOST=auto AUTOOS_NO_COLOR=1 \
           bash "$ROOT/lib/linux/claude-sessions.sh" restore 2>&1)"
    launched=0; [ -f "$tmp/launched" ] && launched=1
    rm -rf "$tmp"
    if [ "$launched" -eq 0 ] && printf '%s\n' "$out" | grep -qi 'no terminal host'; then
        pass
    else
        fail "launched=$launched out: $out"
    fi
fi

if it "an inactive snapshot timer is reported once, not twice"; then
    # `systemctl is-active` PRINTS its answer and exits non-zero when the unit is
    # not running, so `|| echo inactive` appended a second line and the status
    # screen rendered "inactive" on two lines. Found by running it under WSL.
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh
echo inactive
exit 3
' > "$tmp/bin/systemctl"
    chmod +x "$tmp/bin/systemctl"
    out="$(PATH="$tmp/bin:$PATH" AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" AUTOOS_NO_COLOR=1            bash "$ROOT/lib/linux/claude-sessions.sh" status 2>&1)"
    rm -rf "$tmp"
    n="$(printf '%s
' "$out" | grep -c 'inactive' || true)"
    if [ "$n" = "1" ]; then
        pass
    else
        fail "expected one 'inactive' line, got $n: $out"
    fi
fi

if it "a snapshot that was never taken has no age"; then
    # "never (never)" - the age of something that never happened is not a second
    # fact about it.
    tmp="$(mktemp -d)"
    out="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/missing.json" cs_engine summary 2>&1)"
    rm -rf "$tmp"
    if printf '%s
' "$out" | grep -q "^last_snapshot	never$"; then
        pass
    else
        fail "expected a bare 'never', got: $out"
    fi
fi

if it "the live-process probe answers on the real system without throwing"; then
    # Principle 9: the seam every test above uses must not be the only path that
    # is ever exercised. This runs the probe production actually takes.
    n="$(cs_engine probe-live 2>&1 || echo ERR)"
    if [[ "$n" =~ ^[0-9]+$ ]]; then pass; else fail "probe-live returned [$n]"; fi
fi

# install_claude_autostart renders each unit to a temp file and, when it differs
# from the installed one, moves it over it. That silently dropped a local edit
# of the unit (AGENTS.md hard rule 5: back a user-owned file up first).
#
# autostart_run <scratch>: one install_claude_autostart run against a scratch
# home. systemctl and loginctl are stubs, so nothing is enabled or started.
autostart_run() {
    local sb="$1"
    (
        AUTOOS_ROOT="$ROOT"; SYS_HOME="$sb/home"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
        systemctl() { return 0; }
        loginctl() { printf 'yes\n'; }
        install_claude_autostart
    ) 2>&1
}

# autostart_run_full <scratch> [dry:0|1] [plan_ids] [etc_dir]: one
# install_claude_autostart run against a scratch SYS_HOME, printing a trailer
# line for state+rc (the same pattern herdr_run uses, since
# INSTALL_SCRIPT_STATE cannot cross the subshell boundary).
autostart_run_full() {
    local sbox="$1" dryflag="${2:-0}" planids="${3:-}" etcdir="${4:-}"
    (
        AUTOOS_ROOT="$ROOT"; SYS_HOME="$sbox/home"; AUTOOS_DRY_RUN="$dryflag"; AUTOOS_SUDO=""
        PLAN_IDS="$planids"
        if [[ -n "$etcdir" ]]; then AUTOOS_ETC_SYSTEMD_SYSTEM_DIR="$etcdir"; fi
        systemctl() { return 0; }; loginctl() { printf 'yes\n'; }
        INSTALL_SCRIPT_STATE=""
        rcode=0
        install_claude_autostart || rcode=$?
        printf 'AUTOSTART_RESULT %s %s\n' "${INSTALL_SCRIPT_STATE:-installed}" "$rcode"
    ) 2>&1
}
autostart_state() { sed -n 's/^AUTOSTART_RESULT \([a-z]*\) [0-9]*$/\1/p' <<<"$1"; }
autostart_rc() { sed -n 's/^AUTOSTART_RESULT [a-z]* \([0-9]*\)$/\1/p' <<<"$1"; }

if it "claude-autostart: a changed unit is backed up before it is replaced"; then
    sb="$(mktemp -d)"; ud="$sb/home/.config/systemd/user"; u=claude-sessions-snapshot.service
    mkdir -p "$ud"; printf 'LOCAL EDIT: do not lose me\n' >"$ud/$u"
    out="$(autostart_run "$sb")"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:300}" >&2; }
    mapfile -t baks < <(find "$ud" -name '*.autoos-backup-*')
    (( ${#baks[@]} == 1 )) || { ok=0; echo "expected exactly one backup, found ${#baks[@]}" >&2; }
    [[ "${baks[0]:-}" == "$ud/$u.autoos-backup-"* ]] || { ok=0; echo "backup is named [${baks[0]:-}]" >&2; }
    [[ "$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)" == "LOCAL EDIT: do not lose me" ]] \
        || { ok=0; echo "the backup does not hold the user's edit: [$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)]" >&2; }
    if grep -q '^ExecStart=' "$ud/$u" && ! grep -q 'LOCAL EDIT' "$ud/$u"; then :
    else ok=0; echo "the new unit is not in place" >&2; fi
    [[ "$out" == *"installed $u"* ]] || { ok=0; echo "no 'installed' line: ${out:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a changed autostart unit was replaced without a backup"; fi
fi

# The model the other stop-on-backup-failure sites copy: when the unit being
# replaced cannot be backed up (backup_fail_bin's cp refuses), the install
# stops - the user's edited unit stays byte-identical, the rendered temp file is
# removed, an error names the unit, nothing is reloaded or enabled, and the
# return code says it failed. Units after the failing one are not touched either.
if it "claude-autostart: a changed unit that cannot be backed up is left as it was and the install stops"; then
    sb="$(mktemp -d)"; bin="$(backup_fail_bin)"; ud="$sb/home/.config/systemd/user"; u=claude-sessions-snapshot.service; ok=1
    mkdir -p "$ud" "$sb/tmp"; printf 'LOCAL EDIT: do not lose me\n' >"$ud/$u"; cp "$ud/$u" "$sb/unit.orig"
    out="$( ( AUTOOS_ROOT="$ROOT"; SYS_HOME="$sb/home"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
              PATH="$bin:$PATH"; export TMPDIR="$sb/tmp"
              systemctl() { printf 'systemctl %s\n' "$*" >>"$sb/systemctl.log"; return 0; }
              loginctl() { printf 'yes\n'; }
              install_claude_autostart ) 2>&1 )"; rc=$?
    (( rc != 0 )) || { ok=0; echo "rc=0 for an install that stopped: ${out:0:300}" >&2; }
    cmp -s "$ud/$u" "$sb/unit.orig" || { ok=0; echo "the edited unit was replaced without a backup: [$(cat "$ud/$u")]" >&2; }
    [[ -z "$(find "$sb/tmp" -type f)" ]] || { ok=0; echo "the rendered temp unit was left behind: $(find "$sb/tmp" -type f | tr '\n' ' ')" >&2; }
    [[ "$out" == *"could not back up $ud/$u"* && "$out" == *"left as it was"* ]] || { ok=0; echo "no error naming the unit: [${out:0:300}]" >&2; }
    [[ "$out" != *"installed "* ]] || { ok=0; echo "reported an install: [${out:0:300}]" >&2; }
    [[ "$(ls -A "$ud")" == "$u" ]] || { ok=0; echo "the install went on past the failing unit: [$(ls -A "$ud" | tr '\n' ' ')]" >&2; }
    [[ ! -e "$sb/systemctl.log" ]] || { ok=0; echo "systemctl was called after the failure: $(cat "$sb/systemctl.log")" >&2; }
    [[ "$(backup_count "$sb")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the same unit is backed up first, then replaced.
    out="$(autostart_run "$sb")"; rc=$?
    { (( rc == 0 )) && [[ "$(backup_count "$sb")" == 1 ]] && ! grep -q 'LOCAL EDIT' "$ud/$u" \
        && [[ "$(cat "$ud/$u".autoos-backup-*)" == "LOCAL EDIT: do not lose me" ]]; } \
        || { ok=0; echo "control run: rc=$rc backups=$(backup_count "$sb") out=[${out:0:300}]" >&2; }
    rm -rf "$sb" "$bin"
    if (( ok )); then pass; else fail "install_claude_autostart replaces a unit it could not back up"; fi
fi

if it 'claude-autostart: an unchanged unit takes no backup and reports "already current"'; then
    sb="$(mktemp -d)"; ud="$sb/home/.config/systemd/user"; ok=1
    autostart_run "$sb" >/dev/null                   # fresh machine: three units, no backup
    [[ "$(find "$ud" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "a fresh install took a backup" >&2; }
    before="$(cksum "$ud"/claude-sessions-*)"
    out="$(autostart_run "$sb")"; rc=$?              # second run: nothing changes
    (( rc == 0 )) || { ok=0; echo "second run rc=$rc" >&2; }
    [[ "$(grep -c 'already current' <<<"$out")" == 3 ]] || { ok=0; echo "second run not current for all three: ${out:0:400}" >&2; }
    [[ "$(find "$ud" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "an unchanged run took a backup" >&2; }
    [[ "$(cksum "$ud"/claude-sessions-*)" == "$before" ]] || { ok=0; echo "an unchanged unit was rewritten" >&2; }
    # One unit edited by hand: only that one is backed up; the other two stay untouched.
    printf '# local tweak\n' >>"$ud/claude-sessions-snapshot.timer"
    edited="$(cat "$ud/claude-sessions-snapshot.timer")"
    out="$(autostart_run "$sb")"
    mapfile -t baks < <(find "$ud" -name '*.autoos-backup-*')
    (( ${#baks[@]} == 1 )) || { ok=0; echo "expected one backup for the edited unit, found ${#baks[@]}" >&2; }
    [[ "${baks[0]:-}" == "$ud/claude-sessions-snapshot.timer.autoos-backup-"* && "$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)" == "$edited" ]] \
        || { ok=0; echo "the edited timer was not the one backed up, or its bytes differ: ${baks[0]:-none}" >&2; }
    [[ "$(grep -c 'already current' <<<"$out")" == 2 ]] || { ok=0; echo "the two untouched units are not reported current: ${out:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the autostart backup is missing for a changed unit or taken for an unchanged one"; fi
fi

if it "claude-autostart is defined for linux and windows, and not for macos"; then
    ok=1
    for f in catalog/linux.json catalog/windows.json; do
        python3 "$ROOT/tests/helpers/catalog_has.py" "$f" claude-autostart || { ok=0; echo "missing in $f" >&2; }
    done
    # macOS routes through lib/linux/install.sh, which writes systemd units. On a
    # machine with no systemd that reports `installed` and does nothing.
    if python3 "$ROOT/tests/helpers/catalog_has.py" catalog/macos.json claude-autostart; then
        ok=0; echo "macos still lists claude-autostart" >&2
    fi
    if (( ok )); then pass; else fail "catalog membership wrong"; fi
fi

if it "every claude_autostart key the web UI writes is a key the scripts read"; then
    # The first implementation wrote resume_prompt_mode/interval_minutes while the
    # scripts read resume_mode/snapshot_interval_mins, so the card saved nothing.
    if python3 "$ROOT/tests/helpers/check_config_keys.py"; then
        pass
    else
        fail "web UI and config schema disagree (see above)"
    fi
fi

if it "every key the configuration form groups is a real catalog prompt"; then
    # The form used to hardcode six fields. Three of them (git_user_name,
    # ollama_models, antigravity_url - the last since removed) were Linux-only
    # prompts, so on Windows it rendered boxes whose answers no installer
    # would ever read.
    if python3 "$ROOT/tests/helpers/check_config_sections.py"; then
        pass
    else
        fail "the configuration form groups a key no catalog asks (see above)"
    fi
fi

if it "each panel owns one job: overview chooses, system reports, configure sets"; then
    # Overview had grown to six cards covering three unrelated jobs. A card in the
    # wrong panel is how it grew the first time, so the split is pinned here.
    if python3 "$ROOT/tests/helpers/check_panels.py"; then
        pass
    else
        fail "a card is in the wrong panel (see above)"
    fi
fi

if it "the component list is compact until Details is asked for"; then
    ok=1
    grep -q 'id="detailToggle"' web/index.html || { ok=0; echo "missing: the Details toggle" >&2; }
    grep -q 'body:not(\[data-detail="on"\]) .item .item-meta{display:none}' web/index.html ||
        grep -q 'body:not(\[data-detail="on"\]) .item .item-meta,' web/index.html ||
        { ok=0; echo "missing: the compact-mode rule for .item-meta" >&2; }
    grep -q 'class="item-icon"' web/index.html || { ok=0; echo "missing: the component icon" >&2; }
    if (( ok )); then pass; else fail "the catalog is not compact by default"; fi
fi

if it "a quick install queues rather than racing another run"; then
    ok=1
    for marker in 'data-quick=' 'function queueQuickInstall' 'let quickQueue' 'id="headerProgress"'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    # The server rejects a second concurrent run with 409, so the client must not
    # start one; it has to wait for poll() to report the first has finished.
    grep -q 'if (running || !quickQueue.length) return;' web/index.html ||
        { ok=0; echo "missing: the drain guard against a concurrent run" >&2; }
    if (( ok )); then pass; else fail "quick install is not queued safely"; fi
fi

if it "the install order leaves out what is already installed"; then
    if grep -q 'if (BY_ID.get(id)?.installed) continue;' web/index.html; then
        pass
    else
        fail "the install order still lists components the run will skip"
    fi
fi

if it "the section menu is a real menu, not a button that looks like one"; then
    # A dropdown has to be openable, closable and walkable from the keyboard, or
    # it is navigation only a mouse can reach.
    ok=1
    for marker in 'id="tabMenuBtn"' 'aria-haspopup="menu"' 'role="menuitemradio"'                   'function openTabMenu' 'function closeTabMenu'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    grep -q 'e.key === "Escape"' web/index.html || { ok=0; echo "missing: Escape closes the menu" >&2; }
    grep -q 'e.key === "ArrowDown"' web/index.html || { ok=0; echo "missing: arrow-key navigation" >&2; }
    # With no tablist left, role="tabpanel" would be a lie.
    grep -q 'role="tabpanel"' web/index.html && { ok=0; echo "a tabpanel survives with no tablist" >&2; }
    if (( ok )); then pass; else fail "the section menu is not keyboard-operable"; fi
fi

if it "the page carries its own favicon"; then
    # The local server has no asset route, so every load was logging a 403 for
    # /favicon.ico; an inline data URI costs no request at all.
    if grep -q 'rel="icon" href="data:image/svg' web/index.html; then
        pass
    else
        fail "no inline favicon"
    fi
fi

if it "the reduced-motion guard for the indeterminate progress bar survives"; then
    if grep -q 'prefers-reduced-motion:reduce){.bar.indeterminate>div{animation:none}' web/index.html; then
        pass
    else
        fail "the card-chooser stylesheet dropped the reduced-motion rule again"
    fi
fi

if it "the claude card, chooser and configure affordance are present in the page"; then
    ok=1
    for marker in 'id="cardClaudeAutostart"' 'id="cardChooserBar"' 'id="claudeRefreshBtn"' 'data-configure-card'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "cardClaudeAutostart markers missing"; fi
fi

if it "no inline onclick handler is introduced in the web UI"; then
    # Inline handlers force HTML-escaping a value into a JS string context, which
    # is the wrong escaper; the page uses delegated listeners everywhere else.
    n="$(grep -c 'onclick="' web/index.html || true)"
    if [ "$n" = "0" ]; then pass; else fail "$n inline onclick handler(s) left"; fi
fi

