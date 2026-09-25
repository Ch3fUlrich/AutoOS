#!/usr/bin/env bash
# Start the AutoOS LiteLLM fallback proxy (127.0.0.1:4000) with its keys loaded.
#
# Linux twin of start-litellm.ps1. litellm does not read
# configuration/litellm/.env by itself: it resolves os.environ/* strictly from
# the process environment, so a proxy started by hand serves every leg as
# "Missing credentials". This starter reads the flat KEY=VALUE file WITHOUT
# shell evaluation (no `. ./.env`: a value like $(...) stays a literal), skips
# empty and REPLACE_WITH_* placeholders, sets PYTHONUTF8=1 and starts the
# proxy on loopback only - it carries LITELLM_MASTER_KEY, but nothing off-host
# needs it (OmniRoute is the proxied gateway).
#
# Safe to run twice: a proxy that already runs with the current keys is left
# alone; one running with stale keys (the .env changed since it started) is
# restarted. Values are never printed, only key names.
#
#   ./configuration/litellm/start-litellm.sh              # start / converge, detached
#   ./configuration/litellm/start-litellm.sh --dry-run    # say what would happen
#   ./configuration/litellm/start-litellm.sh --foreground # for the systemd unit
#
# Key sources, in order: configuration/api-keys.yml --(mirror)-->
# configuration/litellm/.env --(this script)--> proxy process env.
# Refresh the middle step with: python3 tools/mirror-litellm-env.py
set -euo pipefail

LIT_DIR="${AUTOOS_LITELLM_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PORT="${AUTOOS_LITELLM_PORT:-4000}"
ENV_FILE="$LIT_DIR/.env"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/autoos"
LOG="$STATE_DIR/litellm.log"
UNIT="autoos-litellm.service"

DRY=0
FOREGROUND=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)    DRY=1 ;;
        --foreground) FOREGROUND=1 ;;
        -h|--help)    sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $arg"; exit 2 ;;
    esac
done
[[ $DRY -eq 1 ]] && echo "This is a dry run - nothing is started, stopped or written."

if [[ ! -f "$ENV_FILE" ]]; then
    echo "No .env in $LIT_DIR - copy .env.example first (docs/api-keys.md), then mirror keys:"
    echo "  python3 tools/mirror-litellm-env.py"
    exit 1
fi

# ─── Parse KEY=VALUE literally (never sourced) ──────────────────────────────
# shellcheck source=../env-file.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/env-file.sh"
autoos_read_env_file "$ENV_FILE"
NAMES=("${ENV_NAMES[@]}")
VALUES=("${ENV_VALUES[@]}")
if (( ${#NAMES[@]} == 0 )); then
    echo "Keys loaded into proxy env: (none - every entry is empty or a REPLACE_WITH_* placeholder)"
else
    echo "Keys loaded into proxy env: ${NAMES[*]}"
fi

# ─── Is a proxy already running, and with which keys? ───────────────────────
proxy_ok() {
    curl -sf -m 5 -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null
}

# Prints the pid listening on $PORT (same-user processes only; ss hides the
# owner of anyone else's socket, which then counts as "unknown").
listener_pid() {
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2
}

RUNNING=0
PID=""
ENVIRON_FILE=""
NO_PROC=0   # macOS & co: no /proc, so the live proxy's keys cannot be read
FOREIGN=0   # the port is held by a program that is not litellm
PROC_ROOT="${AUTOOS_PROC_ROOT:-/proc}"
if [[ -n "${AUTOOS_FAKE_LITELLM_ENVIRON:-}" ]]; then
    # Tests only: a synthetic NUL-separated environ stands in for the live
    # proxy (AGENTS.md §5 - never the machine's own processes).
    RUNNING=1
    ENVIRON_FILE="$AUTOOS_FAKE_LITELLM_ENVIRON"
elif proxy_ok || [[ -n "$(listener_pid)" ]]; then
    RUNNING=1
    PID="$(listener_pid)"
    if [[ ! -d "$PROC_ROOT" ]]; then
        NO_PROC=1
        PID=""
    elif [[ -n "$PID" ]]; then
        # Never stop a program we did not start: only a litellm listener may
        # be restarted (AGENTS.md rule 3; found in review 2026-09-25).
        tr '\0' ' ' <"$PROC_ROOT/$PID/cmdline" 2>/dev/null | grep -q litellm || FOREIGN=1
        [[ -r "$PROC_ROOT/$PID/environ" ]] && ENVIRON_FILE="$PROC_ROOT/$PID/environ"
    fi
fi
if [[ $FOREIGN -eq 1 ]]; then
    echo "Port $PORT is held by another program (pid $PID), not litellm - leaving it alone."
    echo "Free the port or set AUTOOS_LITELLM_PORT, then run this again."
    exit 1
fi

STALE=()
if [[ $RUNNING -eq 1 && -n "$ENVIRON_FILE" ]]; then
    declare -A LIVE=()
    while IFS= read -r -d '' kv; do
        LIVE["${kv%%=*}"]="${kv#*=}"
    done <"$ENVIRON_FILE"
    for i in "${!NAMES[@]}"; do
        if [[ "${LIVE[${NAMES[$i]}]-__autoos_unset__}" != "${VALUES[$i]}" ]]; then
            STALE+=("${NAMES[$i]}")
        fi
    done
fi

unit_active() {
    command -v systemctl >/dev/null && systemctl --user is-active --quiet "$UNIT" 2>/dev/null
}

PLANNED="PYTHONUTF8=1 litellm --config config.yaml --host 127.0.0.1 --port $PORT"

# ─── Decide ─────────────────────────────────────────────────────────────────
if [[ $RUNNING -eq 1 && $FOREGROUND -eq 0 ]]; then
    if [[ $NO_PROC -eq 1 ]]; then
        echo "LiteLLM proxy is up on $PORT. Stale-key restart is Linux-only (no /proc here) - restart it by hand to load new keys."
        exit 0
    fi
    if [[ -z "$ENVIRON_FILE" ]]; then
        echo "LiteLLM proxy is up on $PORT but its environment is not readable (another user?) - leaving it alone."
        exit 0
    fi
    if (( ${#STALE[@]} == 0 )); then
        echo "LiteLLM proxy already up with the current keys on $PORT - nothing to do."
        exit 0
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - would restart the proxy on $PORT (stale keys: ${STALE[*]})"
        echo "  - would start: $PLANNED"
        exit 0
    fi
    echo "Restarting the proxy on $PORT (stale keys: ${STALE[*]})..."
    if unit_active; then
        # The unit re-reads .env through this script (--foreground).
        systemctl --user restart "$UNIT"
        for _ in $(seq 1 24); do proxy_ok && break; sleep 5; done
        if proxy_ok; then echo "LiteLLM proxy up on http://127.0.0.1:$PORT (via $UNIT)"; exit 0; fi
        echo "Proxy did not answer on $PORT - see: journalctl --user -u $UNIT"
        exit 1
    fi
    [[ -n "$PID" ]] && kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 10); do [[ -z "$(listener_pid)" ]] && break; sleep 1; done
elif [[ $DRY -eq 1 ]]; then
    if [[ $FOREGROUND -eq 1 ]]; then
        echo "  - would run in the foreground: $PLANNED"
    else
        echo "  - would start: $PLANNED"
    fi
    exit 0
fi

for i in "${!NAMES[@]}"; do export "${NAMES[$i]}=${VALUES[$i]}"; done
export PYTHONUTF8=1
command -v litellm >/dev/null || { echo "litellm is not installed. Run: ./setup.sh --only litellm --yes"; exit 1; }
cd "$LIT_DIR"

if [[ $FOREGROUND -eq 1 ]]; then
    # The unit owns the proxy: a hand-started one on the same port would make
    # the bind fail, so it is stopped first (same user only).
    if [[ -n "$PID" ]]; then
        kill "$PID" 2>/dev/null || true
        for _ in $(seq 1 10); do [[ -z "$(listener_pid)" ]] && break; sleep 1; done
    fi
    # Last line of the script: exec hands the unit's main PID to litellm.
    exec litellm --config config.yaml --host 127.0.0.1 --port "$PORT"
fi

mkdir -p "$STATE_DIR"
nohup litellm --config config.yaml --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
echo "litellm start issued from $LIT_DIR (log: $LOG)"
for _ in $(seq 1 24); do proxy_ok && break; sleep 5; done
if ! proxy_ok; then
    echo "Proxy did not answer on $PORT - see $LOG"
    exit 1
fi
echo "LiteLLM proxy up on http://127.0.0.1:$PORT"
