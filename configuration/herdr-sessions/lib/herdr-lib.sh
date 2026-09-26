# herdr-lib.sh — shared primitives for driving Herdr panes from a script.
# SOURCE this; do not execute it. (No shebang on purpose -- see above; the
# directive below only tells shellcheck which dialect to check this as.)
# shellcheck shell=bash
#
# THE SINGLE SOURCE. Every host that autostarts agents in Herdr uses these
# functions via ../herdr-sessions.sh and a profile — no host copies the logic.
# If you find yourself pasting any of this into a host directory, add a profile
# knob instead.

hs_log() { printf '%s %s: %s\n' "$(date '+%F %T')" "${HS_TAG:-herdr-sessions}" "$*"; }
hs_die() { hs_log "ERROR: $*"; exit 1; }

# ── profile ──────────────────────────────────────────────────────────────────
# A profile is plain shell: only assignments, sourced into this process. Every
# knob has a default here so a profile stays as short as the host is unusual.
hs_load_profile() {
    local p="${1:-}"
    # shellcheck disable=SC1090  # the profile path is supplied by the caller
    # (--profile/HERDR_PROFILE); there is no fixed file to point shellcheck at.
    [ -n "$p" ] && [ -f "$p" ] && . "$p"

    HERDR_BIN="${HERDR_BIN:-$HOME/.local/bin/herdr}"
    HERDR_SOCKET="${HERDR_SOCKET:-$HOME/.config/herdr/herdr.sock}"
    STATE_DIR="${STATE_DIR:-$HOME/.local/state/herdr-sessions}"
    STATE_FILE="${STATE_FILE:-$STATE_DIR/sessions.json}"

    # full   = always "Resume full session as-is" (never compact)
    # summary= accept Claude Code's recommended summary resume
    RESUME_MODE="${RESUME_MODE:-full}"

    # What to do when the snapshot has nothing usable (first boot, fresh host):
    #   continue = start ONE agent with `claude --continue` (single-session hosts)
    #   none     = do nothing
    FALLBACK="${FALLBACK:-none}"
    FALLBACK_NAME="${FALLBACK_NAME:-claude-main}"
    FALLBACK_CWD="${FALLBACK_CWD:-$HOME}"
    FALLBACK_PANE="${FALLBACK_PANE:-first}"

    # snapshot = restore --rc exactly as recorded; always/never = force it
    REMOTE_CONTROL="${REMOTE_CONTROL:-snapshot}"

    WAIT_SOCKET_SEC="${WAIT_SOCKET_SEC:-120}"
    WAIT_PROMPT_SEC="${WAIT_PROMPT_SEC:-90}"
    HS_TAG="${HS_TAG:-herdr-sessions}"

    # `update` action (runs before herdr-server at boot): self-update herdr and
    # claude. 0 disables; the timeout bounds EACH of the two downloads.
    UPDATE_ON_BOOT="${UPDATE_ON_BOOT:-1}"
    UPDATE_TIMEOUT_SEC="${UPDATE_TIMEOUT_SEC:-180}"
    CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
}

# Run `relaunch` as a transient systemd unit so it outlives the Claude session
# it is about to kill. HS_SCOPE=user hosts get a --user unit.
hs_relaunch_detached() {
    local scope=(); [ "${HS_SCOPE:-system}" = user ] && scope=(--user)
    systemd-run "${scope[@]}" --unit "herdr-sessions-relaunch-$(date +%s)" --collect --quiet \
        --setenv=HOME="$HOME" --setenv=PATH="$PATH" --setenv=HERDR_PROFILE="${PROFILE:-${HERDR_PROFILE:-}}" \
        "$HERE/herdr-sessions.sh" relaunch
}

# ── herdr plumbing ───────────────────────────────────────────────────────────
hs_herdr() { "$HERDR_BIN" "$@"; }

hs_wait_socket() {
    for _ in $(seq 1 "$WAIT_SOCKET_SEC"); do
        [ -S "$HERDR_SOCKET" ] && hs_herdr pane list >/dev/null 2>&1 && return 0
        sleep 1
    done
    hs_herdr pane list >/dev/null 2>&1
}

# `herdr agent read` returns a JSON envelope on 0.7.x and raw text on 0.8.x.
# Assuming one silently broke a polling loop across the 0.7.4 -> 0.8.2 upgrade
# (its exit condition could never be true; it spun for three days). Accept both.
hs_pane_text() {
    local out
    out="$(hs_herdr agent read "$1" --source visible --lines "${2:-20}" 2>/dev/null)" || return 1
    case "$out" in
        '{'*) printf '%s' "$out" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print((d.get("result") or {}).get("read",{}).get("text",""))
except Exception: pass' 2>/dev/null ;;
        *) printf '%s' "$out" ;;
    esac
}

hs_panes_json()  { hs_herdr pane list  2>/dev/null; }
hs_agents_json() { hs_herdr agent list 2>/dev/null; }

# Resolve either API envelope shape to a list.
hs_json_list() {
    python3 -c '
import json,sys
raw, key = sys.stdin.read(), sys.argv[1]
try:
    d = json.loads(raw); r = d.get("result") or d
    print(json.dumps(r.get(key) or (r if isinstance(r, list) else [])))
except Exception:
    print("[]")' "$1"
}

hs_first_pane() {
    hs_panes_json | hs_json_list panes | python3 -c '
import json,sys
p=json.load(sys.stdin); print(p[0]["pane_id"] if p else "")'
}

# The first pane sitting in $1, or nothing. `claude --continue` resumes "the most
# recent conversation for this directory" -- the PANE's directory -- so on a host
# with more than one pane the fallback has to pick by cwd, not by position. A
# system-scope host is exactly that case: pane w1:p1 is a bare shell in /root and
# the session lives in w2:p1.
hs_pane_for_cwd() {
    [ -n "${1:-}" ] || return 0
    hs_panes_json | hs_json_list panes | python3 -c '
import json,sys
want=sys.argv[1]
for pane in json.load(sys.stdin):
    if pane.get("cwd") == want:
        print(pane.get("pane_id") or ""); break' "$1"
}

hs_any_claude_running() {
    hs_agents_json | hs_json_list agents | python3 -c '
import json,sys
a=json.load(sys.stdin)
sys.exit(0 if any((x.get("agent") or "")=="claude" for x in a) else 1)'
}

# ── starting an agent ────────────────────────────────────────────────────────
hs_start_in_pane() { hs_herdr pane run "$1" "$2" >/dev/null 2>&1; }

# Answer Claude Code's summary-vs-full resume prompt.
# RESUME_MODE=full moves the caret down and VERIFIES it really is on option 2
# before pressing Enter — the default (option 1) compacts and discards the
# working context that made the session worth restoring, and a blind keystroke
# could answer whatever the pane happened to render instead.
hs_answer_resume_prompt() {
    local pane="$1" name="$2" txt
    for _ in $(seq 1 "$WAIT_PROMPT_SEC"); do
        txt="$(hs_pane_text "$pane" 22)"
        if printf '%s' "$txt" | grep -qi 'Resume from summary'; then
            if [ "$RESUME_MODE" = "summary" ]; then
                hs_herdr pane send-keys "$pane" Enter >/dev/null 2>&1
                hs_log "$name: accepted summary resume (RESUME_MODE=summary)"
                return 0
            fi
            hs_herdr pane send-keys "$pane" Down >/dev/null 2>&1
            sleep 2
            if hs_pane_text "$pane" 22 | grep -q '❯.*2\. Resume full session'; then
                hs_herdr pane send-keys "$pane" Enter >/dev/null 2>&1
                hs_log "$name: chose 'Resume full session as-is' (no compaction)"
            else
                hs_log "WARNING $name: caret not on option 2 — left the prompt for a human"
            fi
            return 0
        fi
        # A small session resumes straight in, with no prompt at all.
        if printf '%s' "$txt" | grep -qiE 'remote-control is active|⏵⏵'; then
            hs_log "$name: resumed directly (no prompt)"; return 0
        fi
        sleep 1
    done
    hs_log "WARNING $name: no prompt seen within ${WAIT_PROMPT_SEC}s"
}
