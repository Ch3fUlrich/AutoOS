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

# keys_value <file> <name>: last uncommented `^<name>[[:space:]]*:` line,
# YAML plain/quoted scalar parse (enough for this file). See ai-stack.sh
# keys_value for the rule.
keys_value() {
    local file="$1" name="$2" raw val
    [[ -f "$file" ]] || return 0
    raw="$(sed -n "s/^${name}[[:space:]]*:[[:space:]]*//p" "$file" \
        | grep -v '^[[:space:]]*#' | tail -n1 | tr -d '\r')"
    [[ -n "$raw" ]] || return 0
    case "$raw" in
        \"*) val="${raw#\"}"; val="${val%%\"*}" ;;
        \'*) val="${raw#\'}"; val="${val%%\'*}" ;;
        *)   val="$raw"
             val="$(printf '%s' "$val" | sed -E 's/([[:space:]])#.*$/\1/')"
             val="${val%"${val##*[![:space:]]}"}"
             ;;
    esac
    [[ "$val" == REPLACE_WITH_* ]] && return 0
    printf '%s' "$val"
}

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
    key="$(keys_value "$KEYS_FILE" omniroute)"
    if [[ -n "$key" ]]; then
        export AUTOOS_OMNIROUTE_KEY="$key"
        LOADED+=(AUTOOS_OMNIROUTE_KEY)
    fi
fi
if (( ${#LOADED[@]} )); then
    echo "Keys exported for opencode: ${LOADED[*]}"
else
    echo "No keys found in $LIT_ENV or $KEYS_FILE - the omniroute/litellm providers will be refused."
fi

# configuration/api-keys.yml's opencode_password is the single source
# (ai-stack.sh init writes it). Fall back to the pinned file, generated once,
# when it is absent. The value is never printed - only the source is named.
KEYS_PW=""
if [[ -f "$KEYS_FILE" ]]; then
    KEYS_PW="$(keys_value "$KEYS_FILE" opencode_password)"
fi
if [[ -n "$KEYS_PW" ]]; then
    if [[ $DRY -eq 1 ]]; then
        echo "  - would use the opencode_password from $KEYS_FILE (user: opencode)."
    else
        echo "Serve password: from $KEYS_FILE (user: opencode)."
    fi
elif [[ ! -s "$PW_FILE" ]]; then
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
if [[ -n "$KEYS_PW" ]]; then
    OPENCODE_PASSWORD="$KEYS_PW"
else
    OPENCODE_PASSWORD="$(cat "$PW_FILE")"
fi
export OPENCODE_PASSWORD

# From $HOME, as the unit does: the serve process's cwd becomes its default
# project, and the caller's checkout is not the phone's project.
cd "$HOME"
if [[ $DETACH -eq 1 ]]; then
    mkdir -p "$(dirname "$LOG")"
    nohup opencode serve --hostname "$HOST" --port "$PORT" >>"$LOG" 2>&1 &
    echo "opencode serve started in the background on $HOST:$PORT (log: $LOG)"
    exit 0
fi
# Last line: exec hands the unit's main PID to opencode.
exec opencode serve --hostname "$HOST" --port "$PORT"
