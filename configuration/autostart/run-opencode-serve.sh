#!/usr/bin/env bash
# Run `opencode serve` (V2 web UI + API) for the phone / LAN reverse proxy.
#
# opencode serve answers its static UI to anyone and guards /api/* with HTTP
# Basic auth: user "opencode", password from OPENCODE_PASSWORD (measured on
# @opencode/cli 2.0.16). Without that variable it invents a new random
# password on every start, so a phone could never log in twice. This wrapper
# pins one: generated once into a 0600 file, never printed.
#
# It also exports the keys the global opencode config references as {env:...}
# (setup_opencode_config writes references, never values): every entry of
# configuration/litellm/.env, read literally, plus the omniroute client key
# from configuration/api-keys.yml when AUTOOS_OMNIROUTE_KEY is not set.
#
#   ./configuration/autostart/run-opencode-serve.sh            # foreground (the unit)
#   ./configuration/autostart/run-opencode-serve.sh --detach   # background, log file
#   ./configuration/autostart/run-opencode-serve.sh --dry-run  # say what would happen
#
# Log in from the browser with user "opencode" and the password in
# ~/.config/autoos/opencode-serve.password.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HOST="${AUTOOS_OPENCODE_HOST:-0.0.0.0}"
PORT="${AUTOOS_OPENCODE_PORT:-4096}"
PW_FILE="${AUTOOS_OPENCODE_PASSWORD_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/opencode-serve.password}"
KEYS_FILE="${AUTOOS_KEYS_FILE:-$REPO/configuration/api-keys.yml}"
LIT_ENV="${AUTOOS_LITELLM_DIR:-$REPO/configuration/litellm}/.env"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/autoos/opencode-serve.log"

DRY=0
DETACH=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --detach)  DETACH=1 ;;
        -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $arg"; exit 2 ;;
    esac
done
[[ $DRY -eq 1 ]] && echo "This is a dry run - nothing is started or written."

# shellcheck source=../env-file.sh
. "$REPO/configuration/env-file.sh"

LOADED=()
if autoos_read_env_file "$LIT_ENV"; then
    for i in "${!ENV_NAMES[@]}"; do
        # The caller's environment wins (an explicit export is deliberate).
        [[ -n "${!ENV_NAMES[$i]:-}" ]] && continue
        export "${ENV_NAMES[$i]}=${ENV_VALUES[$i]}"
        LOADED+=("${ENV_NAMES[$i]}")
    done
fi
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" && -f "$KEYS_FILE" ]]; then
    key="$(sed -n 's/^omniroute[[:space:]]*:[[:space:]]*//p' "$KEYS_FILE" | head -n1 | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
    if [[ -n "$key" && "$key" != REPLACE_WITH_* ]]; then
        export AUTOOS_OMNIROUTE_KEY="$key"
        LOADED+=(AUTOOS_OMNIROUTE_KEY)
    fi
fi
if (( ${#LOADED[@]} )); then
    echo "Keys exported for opencode: ${LOADED[*]}"
else
    echo "No keys found in $LIT_ENV or $KEYS_FILE - the omniroute/litellm providers will be refused."
fi

if [[ ! -s "$PW_FILE" ]]; then
    if [[ $DRY -eq 1 ]]; then
        echo "  - would generate the serve password into $PW_FILE (mode 600)"
    else
        mkdir -p "$(dirname "$PW_FILE")"
        (
            umask 077
            head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 32 >"$PW_FILE"
        )
        chmod 600 "$PW_FILE"
        echo "Generated the serve password into $PW_FILE (user: opencode)."
    fi
else
    echo "Serve password: $PW_FILE (user: opencode)."
fi

if [[ $DRY -eq 1 ]]; then
    if [[ $DETACH -eq 1 ]]; then
        echo "  - would start in the background: opencode serve --hostname $HOST --port $PORT (log: $LOG)"
    else
        echo "  - would run: opencode serve --hostname $HOST --port $PORT"
    fi
    exit 0
fi

command -v opencode >/dev/null || { echo "opencode is not installed. Run: ./setup.sh --only opencode-cli --yes"; exit 1; }
OPENCODE_PASSWORD="$(cat "$PW_FILE")"
export OPENCODE_PASSWORD

if [[ $DETACH -eq 1 ]]; then
    mkdir -p "$(dirname "$LOG")"
    nohup opencode serve --hostname "$HOST" --port "$PORT" >>"$LOG" 2>&1 &
    echo "opencode serve started in the background on $HOST:$PORT (log: $LOG)"
    exit 0
fi
# Last line: exec hands the unit's main PID to opencode.
exec opencode serve --hostname "$HOST" --port "$PORT"
