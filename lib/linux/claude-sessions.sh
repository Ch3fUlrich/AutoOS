#!/usr/bin/env bash
# claude-sessions.sh — AutoOS driver for restoring Claude Code sessions at boot.
#
# Split of responsibility, deliberately:
#   claude_sessions.py   decides WHAT (discovery, liveness, the restore plan)
#   this file            decides WHERE (which terminal) and renders the result
#
# A restored Claude Code session is an interactive TUI. It has to land in a
# terminal a human can attach to, so this script starts nothing unless it has one
# — herdr, then tmux, then it refuses and says so. Backgrounding a TUI with nohup
# produces a process nobody can see, answer or kill, which is what the first
# implementation did (ADR 0002).
#
# Usage: ./setup.sh --claude-sessions <status|snapshot|restore|configure>
#        (or directly: claude-sessions.sh <action> [--dry-run])
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOOS_ROOT="${AUTOOS_ROOT:-$(cd "$HERE/../.." && pwd)}"
CONFIG_FILE="${AUTOOS_CONFIG_FILE:-$AUTOOS_ROOT/autoos.config.json}"
HERDR_BIN="${HERDR_BIN:-herdr}"

# shellcheck source=lib/linux/ui.sh
. "$HERE/ui.sh"
ui_init

DRY_RUN=0
ACTION="${1:-status}"; shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        *) ui_err "unknown option: $1"; exit 2 ;;
    esac
done

cs_engine() { python3 "$HERE/claude_sessions.py" "$@"; }

# ─── configuration ───────────────────────────────────────────────────────────
# The engine owns the key names and the defaults; this loop only decides who
# wins. An explicit environment value beats the config file, which is how a unit
# file, a test or an operator overrides exactly one setting without editing JSON.
cs_load_config() {
    local emitted line key
    emitted="$(AUTOOS_CONFIG_FILE="$CONFIG_FILE" cs_engine config)" || {
        ui_err "could not load the claude-autostart configuration"; return 1
    }
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        key="${line%%=*}"
        if [ -z "${!key:-}" ]; then eval "export $line"; else export "${key?}"; fi
    done <<< "$emitted"
}
cs_load_config

# ─── terminal hosts ──────────────────────────────────────────────────────────
# Each host answers four questions the restore loop asks, and nothing else:
# is it usable, where does a session go, how do I read its screen, how do I type.

cs_host_usable() {
    case "$1" in
        # `pane list` rather than `command -v`: a herdr binary with no server
        # behind it owns no panes, so it is not somewhere a session can land.
        herdr) command -v "$HERDR_BIN" >/dev/null 2>&1 && "$HERDR_BIN" pane list >/dev/null 2>&1 ;;
        # `tmux -V` rather than `command -v tmux`: a tmux that cannot run (no
        # $TERM, a broken build, a shim on PATH) is not a place a session lands.
        tmux)  command -v tmux >/dev/null 2>&1 && tmux -V >/dev/null 2>&1 ;;
        *)     return 1 ;;
    esac
}

cs_pick_host() {
    local want="${AUTOOS_CLAUDE_TERMINAL_HOST:-auto}" host
    if [ "$want" != "auto" ]; then
        cs_host_usable "$want" && { printf '%s' "$want"; return 0; }
        return 1
    fi
    for host in herdr tmux; do
        cs_host_usable "$host" && { printf '%s' "$host"; return 0; }
    done
    return 1
}

# The pane sitting in $2, else the first free pane, else nothing. Matching by cwd
# first matters because `claude --continue` resumes the most recent conversation
# for the PANE's directory, not the one we meant.
cs_herdr_target() {
    local cwd="$1"
    "$HERDR_BIN" pane list 2>/dev/null |
        cs_engine herdr-pane "$cwd" "${CS_CLAIMED_PANES:-}" 2>/dev/null || true
}

cs_target_for() {
    local host="$1" name="$2" cwd="$3"
    case "$host" in
        herdr) cs_herdr_target "$cwd" ;;
        tmux)  printf 'autoos-%s' "$name" ;;
    esac
}

cs_start_in() {
    local host="$1" target="$2" cwd="$3" cmd="$4"
    case "$host" in
        herdr)
            "$HERDR_BIN" pane run "$target" "cd $(printf '%q' "$cwd") && $cmd" >/dev/null 2>&1
            ;;
        tmux)
            # A detached session whose shell outlives claude: when the agent exits
            # the pane stays, with the scrollback still in it.
            tmux has-session -t "$target" 2>/dev/null ||
                tmux new-session -d -s "$target" -c "$cwd" >/dev/null 2>&1
            tmux send-keys -t "$target" "$cmd" Enter >/dev/null 2>&1
            ;;
    esac
}

cs_read_screen() {
    local host="$1" target="$2"
    case "$host" in
        herdr) "$HERDR_BIN" agent read "$target" --source visible --lines 20 2>/dev/null ;;
        tmux)  tmux capture-pane -p -t "$target" 2>/dev/null | tail -n 20 ;;
    esac
}

cs_send_key() {
    local host="$1" target="$2" key="$3"
    case "$host" in
        herdr) "$HERDR_BIN" pane send-keys "$target" "$key" >/dev/null 2>&1 ;;
        tmux)  tmux send-keys -t "$target" "$key" >/dev/null 2>&1 ;;
    esac
}

# Answer Claude Code's summary-vs-full resume prompt when it appears.
#
# Option 1 ("Resume from summary") compacts and discards the working context that
# made the session worth restoring, so AUTOOS_CLAUDE_RESUME_MODE=full moves the
# caret down and VERIFIES it landed on option 2 before pressing Enter: a blind
# keystroke into a prompt that never appeared types into the conversation.
cs_answer_resume_prompt() {
    local host="$1" target="$2" name="$3" i screen
    for i in $(seq 1 30); do
        screen="$(cs_read_screen "$host" "$target" || true)"
        if printf '%s' "$screen" | grep -qi 'Resume from summary'; then
            if [ "${AUTOOS_CLAUDE_RESUME_MODE:-full}" = "summary" ]; then
                cs_send_key "$host" "$target" Enter
                ui_info "$name: accepted the summary resume"
                return 0
            fi
            cs_send_key "$host" "$target" Down
            sleep 1
            if cs_read_screen "$host" "$target" | grep -q '2\. Resume full session'; then
                cs_send_key "$host" "$target" Enter
                ui_ok "$name: resumed in full, without compaction"
            else
                ui_warn "$name: could not confirm option 2 — left the prompt alone"
            fi
            return 0
        fi
        # Already past the prompt: a running agent, or one that never asked.
        if printf '%s' "$screen" | grep -qiE 'remote-control is active|⏵⏵'; then
            ui_ok "$name: resumed directly"
            return 0
        fi
        sleep 1
    done
    ui_warn "$name: no resume prompt appeared within 30s — leaving it as it is"
}

# ─── commands ────────────────────────────────────────────────────────────────

cmd_snapshot() {
    local out count
    out="$(cs_engine snapshot)" || { ui_err "snapshot failed"; return 1; }
    count="$(printf '%s' "$out" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["sessions"]))' 2>/dev/null || echo 0)"
    if (( DRY_RUN )); then
        printf '%s\n' "$out"
        ui_muted "dry-run: $count session(s) would be recorded"
        return 0
    fi
    ui_ok "recorded $count session(s)"
}

cmd_restore() {
    # A pause switch that leaves the supervisor and the snapshots in place, so
    # turning it back on restores the sessions you had rather than starting over.
    if [ "${AUTOOS_CLAUDE_ENABLED:-1}" = "0" ]; then
        ui_info "autostart is disabled in the configuration — restoring nothing"
        return 0
    fi
    if ! command -v claude >/dev/null 2>&1; then
        ui_warn "claude is not on PATH — nothing to restore"
        return 0
    fi

    local host=""
    host="$(cs_pick_host || true)"
    if [ -z "$host" ]; then
        ui_warn "no terminal host available (tried herdr, tmux) — refusing to start an interactive session nothing can attach to"
        ui_info "install herdr or tmux, or set claude_autostart.terminal_host, then run: ./setup.sh --claude-sessions restore"
        return 0
    fi
    ui_info "terminal host: $host"

    local plan starts
    plan="$(cs_engine plan)"
    starts="$(printf '%s\n' "$plan" | grep -c '^START' || true)"

    if [ "$starts" -eq 0 ]; then
        cs_restore_fallback "$host"
        return 0
    fi

    # Panes are claimed as they are used so two sessions never land in one pane.
    export CS_CLAIMED_PANES=""
    local action uuid name cwd rc target cmd
    local -a started=()
    while IFS=$'\t' read -r action uuid name cwd rc; do
        [ -n "${action:-}" ] || continue
        if [ "$action" = "SKIP" ]; then
            ui_warn "skipped $name ($cwd): $rc"
            continue
        fi

        if [ ! -d "$cwd" ]; then
            ui_warn "skipped $name ($cwd): working directory does not exist"
            continue
        fi

        target="$(cs_target_for "$host" "$name" "$cwd")"
        if [ -z "$target" ]; then
            ui_warn "skipped $name ($cwd): $host has no free pane for it"
            continue
        fi

        cmd="claude --resume $uuid"
        [ "$rc" = "1" ] && cmd="$cmd --rc"
        cmd="$cmd -n $(printf '%q' "$name")"

        if (( DRY_RUN )); then
            ui_muted "would start in $host:$target ($cwd): $cmd"
            continue
        fi

        cs_start_in "$host" "$target" "$cwd" "$cmd"
        CS_CLAIMED_PANES="${CS_CLAIMED_PANES}${target},"
        started+=("$host|$target|$name")
        ui_ok "started $name in $host:$target ($cwd)"
    done <<< "$plan"

    local entry
    for entry in ${started[@]+"${started[@]}"}; do
        IFS='|' read -r host target name <<< "$entry"
        cs_answer_resume_prompt "$host" "$target" "$name"
    done
}

cs_restore_fallback() {
    local host="$1"
    if [ "${AUTOOS_CLAUDE_FALLBACK:-continue}" != "continue" ]; then
        ui_info "nothing recorded to restore, and fallback is off"
        return 0
    fi
    if [ "$(cs_engine probe-live)" -gt 0 ]; then
        ui_info "a Claude session is already running — fallback skipped"
        return 0
    fi

    local name="${AUTOOS_CLAUDE_FALLBACK_NAME:-main}"
    local cwd="${AUTOOS_CLAUDE_FALLBACK_CWD:-}"
    [ -n "$cwd" ] || cwd="$HOME"
    local cmd="claude --continue"
    [ "${AUTOOS_CLAUDE_REMOTE_CONTROL:-snapshot}" = "always" ] && cmd="$cmd --rc"
    cmd="$cmd -n $(printf '%q' "$name")"

    local target; target="$(cs_target_for "$host" "$name" "$cwd")"
    if [ -z "$target" ]; then
        ui_warn "fallback skipped: $host has no free pane"
        return 0
    fi
    if (( DRY_RUN )); then
        ui_muted "would start the fallback in $host:$target ($cwd): $cmd"
        return 0
    fi
    ui_ok "no snapshot to restore: starting $name in $cwd"
    cs_start_in "$host" "$target" "$cwd" "$cmd"
    cs_answer_resume_prompt "$host" "$target" "$name"
}

cmd_status() {
    local host supervisor key value count=0 last="never" state_file=""
    host="$(cs_pick_host || echo 'none available')"
    # `systemctl is-active` PRINTS its answer and exits non-zero when the unit is
    # not running, so `|| echo inactive` appended a SECOND line and the screen
    # rendered "inactive" twice. `head -n1` takes the answer; `|| true` keeps a
    # missing systemctl (a container, macOS, Git Bash) from tripping `set -e`.
    supervisor="$(systemctl --user is-active claude-sessions-snapshot.timer 2>/dev/null | head -n1 || true)"
    [ -n "$supervisor" ] || supervisor="inactive"

    # The engine formats the timestamps and counts, so the shell does not
    # re-derive them with its own inline python and drift away from the card.
    while IFS=$'\t' read -r key value; do
        case "$key" in
            last_snapshot) last="$value" ;;
            tracked)       count="$value" ;;
            state_file)    state_file="$value" ;;
        esac
    done < <(cs_engine summary)

    ui_section "Claude sessions"
    # Health first, settings second: "will my sessions come back?" is answered by
    # the timer, the host and the age of the snapshot, not by the resume mode.
    ui_kv "Snapshot timer" "$supervisor" "$([ "$supervisor" = "active" ] && echo ok || echo warn)"
    ui_kv "Terminal host" "$host" "$([ "$host" = "none available" ] && echo warn || echo ok)"
    ui_kv "Last snapshot" "$last"
    ui_kv "Tracked sessions" "$count"
    ui_kv "Live processes" "$(cs_engine probe-live)"
    ui_kv "State file" "$state_file"
    ui_kv "Autostart" "$([ "${AUTOOS_CLAUDE_ENABLED:-1}" = "0" ] && echo paused || echo on)"
    ui_kv "Resume mode" "${AUTOOS_CLAUDE_RESUME_MODE:-full}"
    ui_kv "Fallback" "${AUTOOS_CLAUDE_FALLBACK:-continue}"
    ui_kv "Remote control" "${AUTOOS_CLAUDE_REMOTE_CONTROL:-snapshot}"

    # Every unhealthy state gets the command that fixes it, not just a label.
    [ "$supervisor" = "active" ] ||
        ui_info "the snapshot timer is not running — install the claude-autostart component"
    [ "$host" != "none available" ] ||
        ui_warn "no terminal host: restore will refuse until herdr or tmux is available"

    [ "$count" -gt 0 ] || { ui_info "nothing recorded yet — run: ./setup.sh --claude-sessions snapshot"; return 0; }

    ui_section "Tracked sessions"
    local name uuid cwd when branch
    while IFS=$'\t' read -r name uuid cwd when branch; do
        [ -n "${name:-}" ] || continue
        ui_kv "$name" "$when  ${uuid:0:8}…  $cwd${branch:+  ($branch)}"
    done < <(cs_engine list)
}

# Radio menus rather than free-text prompts: every one of these settings is a
# closed set, and a typo in a typed answer used to be accepted silently and then
# ignored by the code that read it.
cmd_configure() {
    ui_is_interactive || { ui_err "configure needs an interactive terminal"; return 2; }

    local resume fallback rc host interval
    ui_select_radio resume "How should a restored session resume?" "${AUTOOS_CLAUDE_RESUME_MODE:-full}" \
        "full|Resume in full|The whole conversation, with nothing compacted away|recommended" \
        "summary|Resume from a summary|Faster to load, and it discards the working context"

    ui_select_radio fallback "When there is no snapshot to restore" "${AUTOOS_CLAUDE_FALLBACK:-continue}" \
        "continue|Start one session|Runs claude --continue in the fallback directory|default" \
        "none|Do nothing|Leaves the machine alone until you start a session yourself"

    ui_select_radio rc "Remote control for restored sessions" "${AUTOOS_CLAUDE_REMOTE_CONTROL:-snapshot}" \
        "snapshot|Keep what was recorded|Restores each session the way it was running|default" \
        "always|Always enable|Every restored session gets --rc" \
        "never|Never enable|No restored session gets --rc"

    ui_select_radio host "Where should restored sessions open?" "${AUTOOS_CLAUDE_TERMINAL_HOST:-auto}" \
        "auto|Pick automatically|herdr if it is running, otherwise tmux|default" \
        "herdr|herdr only|Refuse unless a herdr server owns the panes" \
        "tmux|tmux only|Always open a detached tmux session"

    ui_ask interval "Snapshot interval in minutes" "${AUTOOS_CLAUDE_SNAPSHOT_INTERVAL_MINS:-5}"
    [[ "$interval" =~ ^[0-9]+$ ]] && (( interval >= 1 && interval <= 59 )) || {
        ui_warn "interval must be 1-59 minutes; keeping ${AUTOOS_CLAUDE_SNAPSHOT_INTERVAL_MINS:-5}"
        interval="${AUTOOS_CLAUDE_SNAPSHOT_INTERVAL_MINS:-5}"
    }

    if (( DRY_RUN )); then
        ui_muted "would save: resume_mode=$resume fallback=$fallback remote_control=$rc terminal_host=$host snapshot_interval_mins=$interval"
        return 0
    fi

    local written
    written="$(AUTOOS_CONFIG_FILE="$CONFIG_FILE" cs_engine set \
        "resume_mode=$resume" "fallback=$fallback" "remote_control=$rc" \
        "terminal_host=$host" "snapshot_interval_mins=$interval")" || {
        ui_err "could not save the configuration"; return 1
    }
    ui_ok "saved to $written"
    ui_info "re-run the installer to apply the new snapshot interval to the timer"
}

case "$ACTION" in
    snapshot)  cmd_snapshot ;;
    restore)   cmd_restore ;;
    status)    cmd_status ;;
    configure) cmd_configure ;;
    *) ui_err "usage: $(basename "$0") <snapshot|restore|status|configure> [--dry-run]"; exit 2 ;;
esac
