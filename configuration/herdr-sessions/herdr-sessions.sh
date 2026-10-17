#!/usr/bin/env bash
# herdr-sessions.sh — THE driver for autostarting Claude Code sessions in Herdr.
#
# One implementation, many hosts. A host contributes a *profile* (a few shell
# assignments) and a thin wrapper; it never contains a copy of this logic:
#
#   a multi-session host   many named sessions, restored per pane from recorded UUIDs
#   a single-session host  one session, no snapshot on a fresh host -> FALLBACK=continue
#
# Both are the same algorithm. `restore` prefers an exact snapshot and falls back
# to whatever the profile allows, so a single-session host is just a degenerate
# case of a multi-session one rather than a second program.
#
# Usage: herdr-sessions.sh <update|relaunch|snapshot|restore|status> [--profile PATH] [--dry-run]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib/herdr-lib.sh"

ACTION="${1:-}"; shift || true
PROFILE=""; DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --profile) PROFILE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
# HERDR_PROFILE lets a systemd unit select the profile without arguments.
[ -z "$PROFILE" ] && PROFILE="${HERDR_PROFILE:-}"
hs_load_profile "$PROFILE"

command -v "$HERDR_BIN" >/dev/null 2>&1 || hs_die "herdr not found at $HERDR_BIN"

# ── update ───────────────────────────────────────────────────────────────────
# Bring the handler (herdr) and the agent (claude) to their latest versions
# BEFORE the server owns a pane. Two facts force this ordering (2026-09-24):
#   * `herdr update` refuses while it is being run from inside a herdr pane
#     ("run `herdr update` outside herdr after detaching"), and a session that
#     lives 7 days inside herdr never sees a newer binary otherwise.
#   * `claude update` only swaps the versions/ symlink; a running session keeps
#     the old binary until relaunched. Boot is the one moment nothing is running.
# NEVER fatal. This is a convenience before restore, not a gate: no network at
# boot, a registry hiccup, an update that fails -- the session must still come
# back. Each step is bounded so a stalled download cannot eat the restore's
# TimeoutStartSec budget. `herdr update` is skipped while a server is live for
# the reason above; systemd only reaches this unit before herdr-server anyway.
hs_cmd_update() {
    [ "${UPDATE_ON_BOOT:-1}" = 1 ] || { hs_log "UPDATE_ON_BOOT=0 -- skipping updates"; exit 0; }
    if [ "$DRY_RUN" = 1 ]; then
        hs_log "dry-run: would run '$HERDR_BIN update' (unless a server is live) and '$CLAUDE_BIN update', each under timeout ${UPDATE_TIMEOUT_SEC}s"; exit 0
    fi
    local before after
    before="$("$HERDR_BIN" --version 2>/dev/null || echo unknown)"
    if hs_herdr pane list >/dev/null 2>&1; then
        hs_log "herdr: server live -- not updating (it refuses from inside a pane; next boot will)"
    elif timeout "$UPDATE_TIMEOUT_SEC" "$HERDR_BIN" update >/dev/null 2>&1; then
        after="$("$HERDR_BIN" --version 2>/dev/null || echo unknown)"
        hs_log "herdr: $before -> $after"
    else
        hs_log "herdr: update failed or timed out (still $before) -- continuing"
    fi
    if [ -x "$CLAUDE_BIN" ]; then
        before="$("$CLAUDE_BIN" --version 2>/dev/null | awk '{print $1}' || echo unknown)"
        if timeout "$UPDATE_TIMEOUT_SEC" "$CLAUDE_BIN" update >/dev/null 2>&1; then
            after="$("$CLAUDE_BIN" --version 2>/dev/null | awk '{print $1}' || echo unknown)"
            hs_log "claude: $before -> $after"
            if hs_stale_claude_pids | grep -q .; then
                hs_log "claude: live session(s) on the old binary -- handing off to 'relaunch' (detached)"
                hs_relaunch_detached
            fi
        else
            hs_log "claude: update failed or timed out (still $before) -- continuing"
        fi
    else
        hs_log "claude: not found at $CLAUDE_BIN -- skipping"
    fi
    exit 0
}

# ── relaunch ─────────────────────────────────────────────────────────────────
# Restart every live Claude session that is running an OUTDATED binary, in
# place, WITHOUT rebooting the host (2026-09-24). `claude update` only swaps the
# versions/ symlink; the running process keeps its old executable until it is
# relaunched. Detect it exactly: /proc/<pid>/exe of each recorded session versus
# `readlink -f $CLAUDE_BIN`. Same path = nothing to do (idempotent, safe on a
# timer). Otherwise: fresh snapshot (so the UUIDs and --rc flags are current),
# SIGTERM each stale pid, wait until herdr no longer lists an agent in that
# pane, then run the normal restore -- which resumes the same UUID, full
# conversation, never the summary. herdr itself is NOT touched here: it must
# not be updated from inside a pane, and the panes must survive the swap.
#
# MUST RUN DETACHED. The session being killed is usually the one that typed
# the command, so the unit/timer (or `systemd-run`) owns the process, never a
# Claude pane. `update` hands off to `relaunch` the same way.
hs_stale_claude_pids() {  # prints "<pid>\t<pane>" for every recorded session whose exe != current
    local cur; cur="$(readlink -f "$CLAUDE_BIN" 2>/dev/null)" || return 0
    [ -f "$STATE_FILE" ] || return 0
    python3 - "$STATE_FILE" "$cur" <<'PYEOF'
import json, os, sys
state, cur = json.load(open(sys.argv[1])), sys.argv[2]
for s in state.get("sessions", []):
    pid = s.get("pid")
    if not pid: continue
    try: exe = os.readlink(f"/proc/{pid}/exe")
    except OSError: continue
    if exe.replace(" (deleted)", "") != cur:
        print(f"{pid}\t{s.get('pane_id')}\t{exe}")
PYEOF
}
hs_cmd_relaunch() {
    hs_herdr pane list >/dev/null 2>&1 || hs_die "herdr not reachable -- nothing to relaunch"
    # Refresh the record first: the restore below resumes from it.
    [ "$DRY_RUN" = 1 ] || hs_cmd_snapshot_quiet
    local stale; stale="$(hs_stale_claude_pids)"
    if [ -z "$stale" ]; then hs_log "relaunch: every live session already runs $(readlink -f "$CLAUDE_BIN") -- nothing to do"; exit 0; fi
    local cur; cur="$(readlink -f "$CLAUDE_BIN")"
    hs_log "relaunch: current claude is $cur"
    local pid pane exe
    while IFS=$'\t' read -r pid pane exe; do
        hs_log "  pane $pane pid $pid runs $exe -> stale"
        [ "$DRY_RUN" = 1 ] && continue
        kill -TERM "$pid" 2>/dev/null || true
    done <<<"$stale"
    if [ "$DRY_RUN" = 1 ]; then hs_log "dry-run: would SIGTERM the above, wait for the pane(s) to free, then 'restore'"; exit 0; fi
    # Wait for each pane to free (agent gone), escalating once. herdr's agent
    # list is the truth restore consults, so it is the truth waited on here.
    local i
    for i in $(seq 1 60); do
        sleep 2
        if ! hs_stale_claude_pids | grep -q .; then break; fi
        [ "$i" -eq 20 ] && while IFS=$'\t' read -r pid pane exe; do kill -KILL "$pid" 2>/dev/null || true; done <<<"$(hs_stale_claude_pids)"
    done
    sleep 3
    hs_log "relaunch: pane(s) free -- restoring"
    hs_cmd_restore
}
# snapshot without the exit-0s that the standalone action uses
hs_cmd_snapshot_quiet() {
    local snapshot usable
    snapshot="$(python3 "$HERE/lib/collect_sessions.py" "$(hs_agents_json)")" || return 0
    usable="$(printf '%s' "$snapshot" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["sessions"] if s.get("session_uuid")))' 2>/dev/null || echo 0)"
    [ "$usable" -gt 0 ] || return 0
    mkdir -p "$STATE_DIR"; local tmp; tmp="$(mktemp "$STATE_DIR/.sessions.XXXXXX")"
    printf '%s\n' "$snapshot" > "$tmp" && mv -f "$tmp" "$STATE_FILE"
    hs_log "snapshot refreshed ($usable restorable)"
}

# ── snapshot ─────────────────────────────────────────────────────────────────
# Source of truth is the running process list, not transcript mtimes: an mtime
# proves a session existed, not that it was running, and one directory can hold
# several sessions (e.g. two checkouts of the same project open at once).
hs_cmd_snapshot() {
    hs_herdr agent list >/dev/null 2>&1 || { hs_log "herdr not reachable — nothing to snapshot"; exit 0; }

    local snapshot count usable
    snapshot="$(python3 "$HERE/lib/collect_sessions.py" "$(hs_agents_json)")" \
        || hs_die "snapshot collection failed"
    count="$(printf  '%s' "$snapshot" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["sessions"]))' 2>/dev/null || echo 0)"
    usable="$(printf '%s' "$snapshot" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["sessions"] if s.get("session_uuid")))' 2>/dev/null || echo 0)"

    if [ "$DRY_RUN" = 1 ]; then
        printf '%s\n' "$snapshot"; hs_log "dry-run: $count session(s), $usable restorable"; exit 0
    fi

    # Refuse to clobber. An empty result almost always means "the sessions are
    # not up yet" (the timer firing mid-boot), not "the user closed everything";
    # overwriting would erase the record the next restore depends on.
    if [ "$usable" -eq 0 ]; then
        hs_log "no restorable sessions found — keeping previous snapshot untouched"; exit 0
    fi

    mkdir -p "$STATE_DIR"
    local tmp; tmp="$(mktemp "$STATE_DIR/.sessions.XXXXXX")"
    printf '%s\n' "$snapshot" > "$tmp" && mv -f "$tmp" "$STATE_FILE"
    hs_log "recorded $usable restorable session(s) of $count -> $STATE_FILE"
}

# ── restore ──────────────────────────────────────────────────────────────────
hs_cmd_restore() {
    hs_wait_socket || hs_die "herdr server unreachable on $HERDR_SOCKET"

    local plan=""
    if [ -f "$STATE_FILE" ]; then
        plan="$(python3 "$HERE/lib/plan_restore.py" "$STATE_FILE" "$(hs_panes_json)" "$(hs_agents_json)" "$REMOTE_CONTROL")"
    else
        hs_log "no snapshot at $STATE_FILE"
    fi

    local starts; starts="$(printf '%s' "$plan" | grep -c '^START' || true)"

    # Fallback: a host with no usable snapshot yet (first boot after install, or
    # a single-session host that has never been snapshotted).
    if [ "${starts:-0}" -eq 0 ] && [ "$FALLBACK" = "continue" ]; then
        if hs_any_claude_running; then
            hs_log "a Claude agent is already running — nothing to do"; return 0
        fi
        # "first" means "the pane this profile's project lives in", falling back
        # to pane #1 only when no pane is there any more. Blindly taking pane #1
        # would resume whatever conversation that directory happens to hold.
        local pane="$FALLBACK_PANE"
        if [ "$pane" = "first" ]; then
            pane="$(hs_pane_for_cwd "$FALLBACK_CWD")"
            if [ -z "$pane" ]; then
                pane="$(hs_first_pane)"
                hs_log "no pane in $FALLBACK_CWD — using the first pane ($pane); --continue will resume that directory's conversation"
            fi
        fi
        [ -n "$pane" ] || hs_die "herdr session has no panes; cannot start an agent"
        local cmd="claude --continue -n $FALLBACK_NAME"
        [ "$REMOTE_CONTROL" = "always" ] && cmd="claude --continue --rc -n $FALLBACK_NAME"
        if [ "$DRY_RUN" = 1 ]; then hs_log "dry-run: fallback would run in $pane: $cmd"; return 0; fi
        hs_log "no snapshot — fallback: starting $FALLBACK_NAME in $pane"
        hs_start_in_pane "$pane" "$cmd"
        hs_answer_resume_prompt "$pane" "$FALLBACK_NAME"
        return 0
    fi

    [ -n "$plan" ] || { hs_log "nothing to restore"; return 0; }

    local started=() action name pane uuid rc reason
    # shellcheck disable=SC2034  # reason is plan_restore.py's 6th TSV column
    # (currently always "-"); read here to keep the field count in sync with
    # the emitter rather than silently dropping trailing columns.
    while IFS=$'\t' read -r action name pane uuid rc reason; do
        [ -z "${action:-}" ] && continue
        if [ "$action" = "SKIP" ]; then hs_log "skip $name ($pane): $uuid"; continue; fi
        local cmd="claude --resume $uuid"
        [ "$rc" = "1" ] && cmd="$cmd --rc"
        cmd="$cmd -n $name"
        if [ "$DRY_RUN" = 1 ]; then hs_log "dry-run: would run in $pane: $cmd"; continue; fi
        hs_start_in_pane "$pane" "$cmd"
        hs_log "started $name in $pane ($cmd)"
        started+=("$pane|$name")
    done <<< "$plan"

    [ "$DRY_RUN" = 1 ] && return 0
    [ "${#started[@]}" -eq 0 ] && { hs_log "nothing to start"; return 0; }

    local entry
    for entry in "${started[@]}"; do
        hs_answer_resume_prompt "${entry%%|*}" "${entry##*|}"
    done
    hs_log "restore complete"
}

hs_cmd_status() {
    hs_log "profile      : ${PROFILE:-<defaults>}"
    hs_log "herdr        : $HERDR_BIN ($(hs_herdr pane list >/dev/null 2>&1 && echo reachable || echo UNREACHABLE))"
    hs_log "state file   : $STATE_FILE $([ -f "$STATE_FILE" ] && echo '(present)' || echo '(missing)')"
    hs_log "resume mode  : $RESUME_MODE   fallback: $FALLBACK   remote-control: $REMOTE_CONTROL"
    # `&&` as the last statement would make `status` exit 1 on a host that has
    # not been snapshotted yet — the first command anyone runs on a new host.
    if [ -f "$STATE_FILE" ]; then python3 "$HERE/lib/show_state.py" "$STATE_FILE"; fi
}

case "$ACTION" in
    update)   hs_cmd_update ;;
    relaunch) hs_cmd_relaunch ;;
    snapshot) hs_cmd_snapshot ;;
    restore)  hs_cmd_restore ;;
    status)   hs_cmd_status ;;
    *) echo "Usage: $(basename "$0") <update|relaunch|snapshot|restore|status> [--profile PATH] [--dry-run]" >&2; exit 2 ;;
esac
