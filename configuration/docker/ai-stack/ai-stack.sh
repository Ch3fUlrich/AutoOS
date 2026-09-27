#!/usr/bin/env bash
# Manage the AutoOS AI stack in Docker (compose.yml next to this script):
# OmniRoute gateway :20128, opencode serve :4096, OpenHands :3000.
#
#   ai-stack.sh init              env files + opencode config (idempotent)
#   ai-stack.sh up [service...]   build/pull what is missing, start, wait healthy
#   ai-stack.sh down [service...] stop the containers (kept; `up` resumes)
#   ai-stack.sh restart <service> restart one service (page-cache relief: verify says when)
#   ai-stack.sh failover on|off|status  standby router: LiteLLM serves the
#                                     gateway port, then hands it back
#   ai-stack.sh status            containers + a probe per port
#   ai-stack.sh is-active         exit 0 when the stack owns the services (marker below)
#   ai-stack.sh migrate [--yes]   native units -> containers (plan without --yes)
#   ai-stack.sh rollback [--yes]  containers -> native units (plan without --yes)
#   ai-stack.sh verify            read-only end-to-end checks; exit 1 on any FAIL
#   --dry-run                     with any command: say what would happen
#
# Files (never tracked, all mode 600 under a 700 directory):
#   ~/.config/autoos/ai-stack/stack.env      compose settings (uid, paths, RAM)
#   ~/.config/autoos/ai-stack/opencode.env   OPENCODE_PASSWORD + {env:...} keys
#   ~/.config/autoos/ai-stack/openhands.env  LLM_API_KEY (+ remote-browser pair)
#   ~/.config/autoos/ai-stack/manage.key     manage-scoped gateway key, host only
#   ~/.config/autoos/ai-stack/client.key     gateway client key (0600): the standby
#                                            router serves it as its master key
#   ~/.config/autoos/ai-stack/failover.state standby state (since + pid);
#                                            failover/ is the standby's own state dir
#   ~/.config/autoos/ai-stack/stack.active   ownership marker: written only when a
#                                            migrate completed (or a fresh host's
#                                            first `up`), removed by rollback
#   ~/.local/share/autoos/ai-stack/          omniroute/ data, opencode-home/, qoder-home/
# init only ADDS missing keys (backup first); a value you edit stays yours.
# up/migrate refuse a LAN publish address unless coding-agents-fw.service
# runs or stack.env says AUTOOS_STACK_ALLOW_LAN=1, and an AUTOOS_OMNIROUTE_PUBLIC_URL
# the gateway would exit on (not an http(s) URL).
# Design, RAM budget, rollback: docs/web-services.md (Server profile section).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
CONFIG_DIR="${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}"
DATA_DIR="${AUTOOS_AI_STACK_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/autoos/ai-stack}"
# Tests replace these with logging stubs: nothing in the suite may reach the
# live docker daemon, user manager or network (AGENTS.md §5).
DOCKER="${AUTOOS_DOCKER:-docker}"
SYSTEMCTL="${AUTOOS_SYSTEMCTL:-systemctl}"
CURL="${AUTOOS_CURL:-curl}"
OMNI_HOME="${AUTOOS_OMNIROUTE_HOME:-$HOME/.omniroute}"
OH_DIR="${AUTOOS_OPENHANDS_DIR:-$HOME/.openhands}"
KEYS_FILE="${AUTOOS_KEYS_FILE:-$REPO/configuration/api-keys.yml}"
LIT_ENV="${AUTOOS_LITELLM_DIR:-$REPO/configuration/litellm}/.env"
USER_CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
PW_FILE="${AUTOOS_OPENCODE_PASSWORD_FILE:-$USER_CFG/autoos/opencode-serve.password}"
OC_HOST_CFG="${AUTOOS_OPENCODE_HOST_CONFIG:-$USER_CFG/opencode/opencode.json}"
OMNIGRAPH_ENV="${AUTOOS_OMNIGRAPH_ENV:-$HOME/.autoos-omnigraph.env}"
STACK_ENV="$CONFIG_DIR/stack.env"
MANAGE_KEY_FILE="$CONFIG_DIR/manage.key"
# The standby router's own files (failover): client.key holds just the gateway
# client key (0600) so start-litellm.sh can serve it as its master key without
# ai-stack.sh ever printing the value; failover.state records the standby
# (since + pid); FAILOVER_DIR is the standby's own STATE_DIR, keeping the
# always-on :4000 unit untouched.
CLIENT_KEY_FILE="$CONFIG_DIR/client.key"
FAILOVER_DIR="$CONFIG_DIR/failover"
FAILOVER_STATE="$CONFIG_DIR/failover.state"
MARKER="$CONFIG_DIR/stack.active"
# Tests point both at logging stand-ins (tests/helpers/aistack_fake.sh).
REGISTER="${AUTOOS_REGISTER_AUTOSTART:-$REPO/configuration/autostart/register-autostart.sh}"
START_STACK="${AUTOOS_START_STACK:-$REPO/configuration/start-stack.sh}"
# The LiteLLM starter the standby runs through (env-only interface: the four
# AUTOOS_LITELLM_* names; never argv, never a printed value).
START_LITELLM="${AUTOOS_START_LITELLM:-$REPO/configuration/litellm/start-litellm.sh}"
# The port the liveliness probe uses while a failover command runs.
FAILOVER_PORT=""
# Filled by migrate; read by migrate_abort / restore_native.
MOVED=()
UNITS_BEFORE=()
OH_REPLACED=0
# Filled by opencode_password_sync; read by cmd_init.
OC_PW=""

DRY=0
YES=0
CMD=""
SERVICES=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes)     YES=1 ;;
        -h|--help) sed -n '2,34p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "Unknown option: $arg"; exit 2 ;;
        *) if [[ -z "$CMD" ]]; then CMD="$arg"; else SERVICES+=("$arg"); fi ;;
    esac
done
CMD="${CMD:-status}"
[[ $DRY -eq 1 ]] && echo "This is a dry run - nothing is written, started or stopped."

# shellcheck source=../../env-file.sh
. "$REPO/configuration/env-file.sh"

# ─── Small helpers ──────────────────────────────────────────────────────────
# autoos_backup_path <path> [stamp] [extension]: the name for a NEW backup.
# Without <extension>, it is <path>.autoos-backup-<stamp>, then -1, -2, ...;
# with it, it is <path><extension>, then <path>-N<extension>. The stamp has
# one-second resolution and a plain overwrite used to destroy an earlier
# same-second backup; see lib/linux/install.sh backup_path for the source of
# this rule. This script is standalone (does not source that lib), so it gets
# its own copy. <stamp> defaults to now; a test can pin it.
autoos_backup_path() {
    local path="$1" stamp="${2:-}" extension="${3:-}" base candidate n=0
    if [[ -n "$extension" ]]; then
        base="$path"
    else
        [[ -n "$stamp" ]] || stamp="$(date +%Y%m%d-%H%M%S)"
        base="$path.autoos-backup-$stamp"
    fi
    candidate="${base}${extension}"
    while [[ -e "$candidate" || -L "$candidate" ]]; do
        n=$((n + 1))
        candidate="${base}-${n}${extension}"
    done
    printf '%s\n' "$candidate"
}

# The code tree: the main checkout's parent (…/code for …/code/AutoOS), so
# every repo next to AutoOS is visible, from a lane worktree too.
default_code_dir() {
    local common
    common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [[ -n "$common" && "$common" == */.git ]]; then
        dirname "$(dirname "$common")"
    else
        dirname "$REPO"
    fi
}
CODE_DIR="${AUTOOS_CODE_DIR:-$(default_code_dir)}"

# file_has_key <file> <KEY>: a KEY= line exists, empty value included (an
# operator who blanked a value meant it).
file_has_key() {
    [[ -f "$1" ]] && grep -qE "^[[:space:]]*$2[[:space:]]*=" "$1"
}

# env_value <file> <KEY>: the literal value (env-file.sh rules), or nothing.
env_value() {
    local i
    autoos_read_env_file "$1" 2>/dev/null || return 0
    for i in "${!ENV_NAMES[@]}"; do
        if [[ "${ENV_NAMES[$i]}" == "$2" ]]; then printf '%s' "${ENV_VALUES[$i]}"; return 0; fi
    done
}

quote_value() {
    # Single quotes: compose reads the value literally (no ${} interpolation).
    if [[ "$1" == *"'"* ]]; then printf '%s' "$1"; else printf "'%s'" "$1"; fi
}

# ensure_env_file <file> <KEY> <value> [<KEY> <value>...]: read-modify-write.
# Missing keys with a value are appended; present keys are never touched;
# keys without a value are left out (an empty KEY= would override defaults).
ensure_env_file() {
    local file="$1"; shift
    local add=() lines=() key val
    while (( $# >= 2 )); do
        key="$1"; val="$2"; shift 2
        [[ -z "$val" ]] && continue
        file_has_key "$file" "$key" && continue
        # One KEY=value per line: a line break would end the value early and
        # turn the rest into a key of its own; docker's env-file format has no
        # escape for it. (A NUL cannot get here - a bash string cannot hold
        # one.) The value may be a secret: only the key is named.
        if [[ "$val" == *$'\n'* || "$val" == *$'\r'* ]]; then
            echo "  ! $key: the value holds a line break (\\n or \\r) - an env file cannot carry it; not written to $file"
            continue
        fi
        add+=("$key")
        lines+=("$key=$(quote_value "$val")")
    done
    if (( ${#add[@]} == 0 )); then
        echo "  = $file up to date (skipped)"
        return 0
    fi
    if [[ $DRY -eq 1 ]]; then
        if [[ -f "$file" ]]; then echo "  - would add ${add[*]} to $file (backup first)"
        else echo "  - would write $file (${add[*]}, mode 600)"; fi
        return 0
    fi
    mkdir -p "$(dirname "$file")"
    chmod 700 "$(dirname "$file")"
    local backup=""
    if [[ -f "$file" ]]; then
        backup="$(autoos_backup_path "$file")"
        if ! cp -p -- "$file" "$backup"; then
            rm -f -- "$backup"
            echo "  ! could not back up $file - leaving it untouched" >&2
            return 1
        fi
        chmod 600 "$backup"
    fi
    (
        umask 077
        [[ -s "$file" && -n "$(tail -c1 "$file")" ]] && printf '\n' >>"$file"
        [[ -z "$backup" ]] && printf '# AutoOS docker AI stack - see docs/web-services.md (Server profile section). Edit freely;\n# ai-stack.sh init only appends keys that are missing.\n' >>"$file"
        printf '%s\n' "${lines[@]}" >>"$file"
    )
    chmod 600 "$file"
    if [[ -n "$backup" ]]; then echo "  + $file: added ${add[*]} (backup: $backup)"
    else echo "  + wrote $file (${add[*]})"; fi
}

# keys_value <name>: read $KEYS_FILE, last uncommented `^<name>[[:space:]]*:`
# line, and parse the scalar. YAML plain/quoted, enough for this file:
#   - starts with " : up to the next " (no escapes)
#   - starts with ' : up to the next '
#   - otherwise    : up to the first # preceded by space or tab, then trim
# CR is stripped. A value starting with REPLACE_WITH_ counts as empty.
keys_value() {
    local name="$1" raw val
    [[ -f "$KEYS_FILE" ]] || return 0
    raw="$(sed -n "s/^${name}[[:space:]]*:[[:space:]]*//p" "$KEYS_FILE" \
        | grep -v '^[[:space:]]*#' | tail -n1 | tr -d '\r' || true)"
    [[ -n "$raw" ]] || return 0
    case "$raw" in
        \"*) val="${raw#\"}"; val="${val%%\"*}" ;;
        \'*) val="${raw#\'}"; val="${val%%\'*}" ;;
        *)   val="$raw"
             # cut at the first # that is preceded by a space or tab
             val="$(printf '%s' "$val" | sed -E 's/([[:space:]])#.*$/\1/')"
             # trim trailing whitespace
             val="${val%"${val##*[![:space:]]}"}"
             ;;
    esac
    [[ "$val" == REPLACE_WITH_* ]] && return 0
    printf '%s' "$val"
}

omniroute_client_key() {
    if [[ -n "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then printf '%s' "$AUTOOS_OMNIROUTE_KEY"; return; fi
    keys_value omniroute
}

# keys_add_opencode_password <value>: append `opencode_password: '<v>'` to
# KEYS_FILE (read-modify-write). A backup is made first when it exists and its
# mode is kept; a new file is 0600. A missing trailing newline is added first,
# so the new key never joins the last line. Never prints the value.
keys_add_opencode_password() {
    local val="$1" backup="" existed=0
    if [[ -f "$KEYS_FILE" ]]; then
        existed=1
        backup="$(autoos_backup_path "$KEYS_FILE")"
        if ! cp -p -- "$KEYS_FILE" "$backup"; then
            rm -f -- "$backup"
            echo "  ! could not back up $KEYS_FILE - the key was not added" >&2
            return 1
        fi
        chmod 600 "$backup"
    fi
    (
        umask 077
        [[ $existed -eq 1 && -s "$KEYS_FILE" && -n "$(tail -c1 "$KEYS_FILE")" ]] && printf '\n' >>"$KEYS_FILE"
        printf 'opencode_password: %s\n' "$(quote_value "$val")" >>"$KEYS_FILE"
    )
    [[ $existed -eq 1 ]] || chmod 600 "$KEYS_FILE"
}

# env_replace_value <file> <KEY> <value>: replace the one KEY= line in place,
# keeping every other line and comment, backing the file up first exactly as
# ensure_env_file does. Only used to move today's OPENCODE_PASSWORD line to the
# api-keys.yml value; never prints the value.
env_replace_value() {
    local file="$1" key="$2" val="$3" backup tmp line
    backup="$(autoos_backup_path "$file")"
    if ! cp -p -- "$file" "$backup"; then
        rm -f -- "$backup"
        echo "  ! could not back up $file - leaving it untouched" >&2
        return 1
    fi
    chmod 600 "$backup"
    tmp="$(mktemp "$(dirname "$file")/.autoos-env-XXXXXX")"
    if ! while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*= ]]; then
            printf '%s=%s\n' "$key" "$(quote_value "$val")"
        else
            printf '%s\n' "$line"
        fi
    done <"$file" >"$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! chmod 600 "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! mv -- "$tmp" "$file"; then
        rm -f -- "$tmp"
        return 1
    fi
}

# opencode_password_sync: make configuration/api-keys.yml's `opencode_password`
# the single source of the opencode serve Basic-auth password. Y is read from
# the yml (quotes and CR stripped; a REPLACE_WITH_* placeholder counts as
# empty). When empty it migrates the value once - from the native serve file,
# then the docker opencode.env OPENCODE_PASSWORD, then a fresh 32-char value -
# appending it to the yml. It then makes the two derived copies agree, never
# overwriting a copy newer than the yml. Sets OC_PW. Only file names, never a
# value, are printed.
opencode_password_sync() {
    local keys_env="$CONFIG_DIR/opencode.env"
    local y="" src="" val backup cur
    y="$(keys_value opencode_password)"
    if [[ "$y" == *$'\n'* || "$y" == *$'\r'* ]]; then
        echo "  ! opencode_password: the value holds a line break (\\n or \\r) - an env file cannot carry it; not used"
        return 1
    fi
    if [[ -z "$y" ]]; then
        if [[ -s "$PW_FILE" ]]; then
            val="$(tr -d '\r\n' <"$PW_FILE")"; src="$PW_FILE"
        else
            val="$(env_value "$keys_env" OPENCODE_PASSWORD)"
            [[ -n "$val" ]] && src="$keys_env OPENCODE_PASSWORD"
        fi
        if [[ -z "$val" ]]; then
            if [[ $DRY -eq 1 ]]; then val="generated"
            else val="$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 32)"; fi
            src="a fresh random value"
        fi
        if [[ $DRY -eq 1 ]]; then
            echo "  - would add opencode_password to $KEYS_FILE"
        else
            keys_add_opencode_password "$val" || return 1
            echo "  + opencode_password added to $KEYS_FILE (from $src)"
        fi
        y="$val"
    fi
    OC_PW="$y"

    # Derived copy 1: the docker opencode.env OPENCODE_PASSWORD.
    if ! file_has_key "$keys_env" OPENCODE_PASSWORD; then
        ensure_env_file "$keys_env" OPENCODE_PASSWORD "$y" || return 1
    else
        cur="$(env_value "$keys_env" OPENCODE_PASSWORD)"
        if [[ "$cur" == "$y" ]]; then
            echo "  = $keys_env opencode password up to date (skipped)"
        elif [[ -f "$KEYS_FILE" && "$keys_env" -nt "$KEYS_FILE" ]]; then
            echo "  ! $keys_env holds a newer opencode password than $KEYS_FILE - left as is; put it into opencode_password there and re-run init"
        elif [[ $DRY -eq 1 ]]; then
            echo "  - would update the opencode password in $keys_env from $KEYS_FILE"
        else
            env_replace_value "$keys_env" OPENCODE_PASSWORD "$y" || return 1
            echo "  + $keys_env opencode password updated from $KEYS_FILE"
        fi
    fi

    # Derived copy 2: the native serve password file (0600 under a 0700 dir).
    if [[ ! -s "$PW_FILE" ]]; then
        if [[ $DRY -eq 1 ]]; then
            echo "  - would write the opencode password to $PW_FILE (mode 600)"
        else
            mkdir -p "$(dirname "$PW_FILE")" || return 1
            chmod 700 "$(dirname "$PW_FILE")"
            ( umask 077; printf '%s\n' "$y" >"$PW_FILE" ) || return 1
            chmod 600 "$PW_FILE"
            echo "  + wrote the opencode password to $PW_FILE (mode 600)"
        fi
    else
        cur="$(tr -d '\r\n' <"$PW_FILE")"
        if [[ "$cur" == "$y" ]]; then
            echo "  = $PW_FILE opencode password up to date (skipped)"
        elif [[ -f "$KEYS_FILE" && "$PW_FILE" -nt "$KEYS_FILE" ]]; then
            echo "  ! $PW_FILE holds a newer opencode password than $KEYS_FILE - left as is; put it into opencode_password there and re-run init"
        elif [[ $DRY -eq 1 ]]; then
            echo "  - would update $PW_FILE opencode password from $KEYS_FILE"
        else
            backup="$(autoos_backup_path "$PW_FILE")"
            if ! cp -p -- "$PW_FILE" "$backup"; then
                rm -f -- "$backup"
                echo "  ! could not back up $PW_FILE - leaving it untouched" >&2
                return 1
            fi
            chmod 600 "$backup"
            ( umask 077; printf '%s\n' "$y" >"$PW_FILE" ) || return 1
            chmod 600 "$PW_FILE"
            echo "  + $PW_FILE opencode password updated from $KEYS_FILE"
        fi
    fi
}

# The value a name should get: this environment, then the litellm .env (the
# same sources run-opencode-serve.sh exports for the native serve).
secret_for() {
    if [[ -n "${!1:-}" ]]; then printf '%s' "${!1}"; return; fi
    env_value "$LIT_ENV" "$1"
}

running_container_env() {
    # running_container_env <container> <NAME>: carry a setting over from the
    # container this stack replaces (the remote-browser pair).
    "$DOCKER" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null \
        | sed -n "s/^$2=//p" | head -n1 || true
}

# The publish address compose will really use: an exported AUTOOS_STACK_BIND
# beats --env-file in compose's interpolation, so it wins here too - and dc
# hands compose exactly this value, so the guard and the published ports can
# never disagree.
effective_bind() {
    local b="${AUTOOS_STACK_BIND:-}"
    [[ -n "$b" ]] || b="$(env_value "$STACK_ENV" AUTOOS_STACK_BIND)"
    printf '%s' "${b:-0.0.0.0}"
}

dc() {
    AUTOOS_STACK_BIND="$(effective_bind)" \
        "$DOCKER" compose --project-name autoos-ai --env-file "$STACK_ENV" -f "$HERE/compose.yml" "$@"
}

container_running() {
    [[ "$("$DOCKER" inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

# container_state <container>: docker's own verdict - "running (healthy)",
# "exited", "no container"... (status and verify read the same words).
container_state() {
    "$DOCKER" inspect -f '{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}' "$1" 2>/dev/null || echo 'no container'
}

port_listening() {
    [[ -n "$(ss -ltnH "sport = :$1" 2>/dev/null)" ]]
}

http_code() {
    "$CURL" -s -m 5 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true
}

gateway_ok()   { [[ "$(http_code http://127.0.0.1:20128/api/health)" == 200 ]]; }
gateway_keyed() { [[ "$(http_code http://127.0.0.1:20128/v1/models)" == 401 ]]; }
opencode_ok()  { local c; c="$(http_code http://127.0.0.1:4096/)"; [[ "$c" == 200 || "$c" == 401 ]]; }
openhands_ok() { [[ "$(http_code http://127.0.0.1:3000/)" == 200 ]]; }

wait_for() {
    local _
    for _ in $(seq 1 "${2:-36}"); do "$1" && return 0; sleep 5; done
    "$1"
}

unit_active() { "$SYSTEMCTL" --user is-active --quiet "$1.service" 2>/dev/null; }
unit_known()  { "$SYSTEMCTL" --user cat "$1.service" >/dev/null 2>&1; }

service_container() {
    case "$1" in
        omniroute) echo autoos-omniroute ;;
        opencode)  echo autoos-opencode ;;
        openhands) echo openhands-app ;;
    esac
}
service_port() {
    case "$1" in
        omniroute) echo 20128 ;;
        opencode)  echo 4096 ;;
        openhands) echo 3000 ;;
    esac
}
# The native unit a service replaces (OpenHands never had one).
service_unit() {
    case "$1" in
        omniroute) echo autoos-omniroute ;;
        opencode)  echo autoos-opencode ;;
    esac
}

# ─── ownership ──────────────────────────────────────────────────────────────
# The marker, not a container, says who owns :20128/:4096/:3000. A container
# proves nothing: a failed migrate can leave one behind (stopped or not).
stack_owned() { [[ -f "$MARKER" ]]; }

write_marker() {
    mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR" || return 1
    ( umask 077; printf 'owner=docker by=%s since=%s\n' "$1" "$(date +%Y-%m-%dT%H:%M:%S%z)" >"$MARKER" ) || return 1
    echo "  + wrote $MARKER: the docker stack owns the gateway, opencode serve and OpenHands"
}

# ─── publish address ────────────────────────────────────────────────────────
# A LAN publish address is only safe behind the host firewall (DOCKER-USER
# lets only the reverse proxy in). up and migrate therefore refuse it unless
# that firewall is known to run - the system unit coding-agents-fw.service -
# or stack.env carries the explicit AUTOOS_STACK_ALLOW_LAN=1.
is_loopback() {
    case "$1" in
        127.*|::1|'[::1]'|localhost) return 0 ;;
        *) return 1 ;;
    esac
}

# Runs before every `compose up -d` (dc_up): that creates a container, or
# recreates a running one whose published bind changed. Passes once per run.
BIND_OK=0
bind_guard() {
    local bind allow
    (( BIND_OK )) && return 0
    bind="$(effective_bind)"
    if is_loopback "$bind"; then BIND_OK=1; return 0; fi
    # Only the operator's own file can accept the exposure, never an
    # inherited environment variable.
    allow="$(env_value "$STACK_ENV" AUTOOS_STACK_ALLOW_LAN)"
    if [[ "$allow" == 1 ]]; then
        echo "  = publishing on $bind: AUTOOS_STACK_ALLOW_LAN=1 in $STACK_ENV (firewall not checked)"
        BIND_OK=1
        return 0
    fi
    if "$SYSTEMCTL" is-active coding-agents-fw.service >/dev/null 2>&1; then
        echo "  = publishing on $bind: coding-agents-fw.service is active (LAN limited to the proxy)"
        BIND_OK=1
        return 0
    fi
    echo "  ! refusing to publish :20128, :4096 and :3000 on $bind: coding-agents-fw.service is not"
    echo "    active, so nothing limits LAN clients to the reverse proxy (OpenHands has no login and"
    echo "    holds the docker socket). Start that firewall unit, or set AUTOOS_STACK_BIND=127.0.0.1"
    echo "    (host only), or accept the exposure with AUTOOS_STACK_ALLOW_LAN=1 - both in $STACK_ENV"
    [[ -n "${AUTOOS_STACK_BIND:-}" ]] && echo "    (this shell exports AUTOOS_STACK_BIND=$bind, which wins over the file)"
    if [[ $DRY -eq 1 ]]; then echo "    (dry run: a real run stops here)"; return 0; fi
    return 1
}

# dc_up [args...]: `docker compose up -d`, never past a refusing bind guard.
dc_up() {
    preflight || return 1
    dc up -d "$@"
}

# ─── public URL ─────────────────────────────────────────────────────────────
# AUTOOS_OMNIROUTE_PUBLIC_URL (stack.env, or the shell - it beats the file in
# compose) is the gateway's public origin, passed to it as NEXT_PUBLIC_BASE_URL
# and OMNIROUTE_PUBLIC_BASE_URL. init never writes it. The gateway (image
# 3.8.50) validates both at startup and EXITS on a value that is not an http(s)
# URL - under `restart: unless-stopped` that is a crash loop - so a bad one is
# refused before compose runs.
omniroute_public_url() {
    local u="${AUTOOS_OMNIROUTE_PUBLIC_URL:-}"
    [[ -n "$u" ]] || u="$(env_value "$STACK_ENV" AUTOOS_OMNIROUTE_PUBLIC_URL)"
    u="${u#"${u%%[![:space:]]*}"}"
    u="${u%"${u##*[![:space:]]}"}"
    printf '%s' "$u"
}

# http(s)://host[:port][/path]: no credentials, no blanks. Anything else is
# either refused by the gateway at startup or a secret in a redirect URI.
public_url_valid() {
    [[ "$1" =~ ^https?://[^/?#@[:space:]]+([/?#][^[:space:]]*)?$ ]]
}

# public_url_shown <valid url>: what the app makes of it - query and fragment
# cut, trailing slashes dropped (its normalizeBaseUrl) - for output.
public_url_shown() {
    local u="${1%%[?#]*}"
    while [[ "$u" == */ ]]; do u="${u%/}"; done
    printf '%s' "$u"
}

# Runs with bind_guard before every `compose up` (preflight): nothing here
# echoes the value, which may be mistyped with credentials in it.
public_url_guard() {
    local u
    u="$(omniroute_public_url)"
    if [[ -z "$u" ]] || public_url_valid "$u"; then return 0; fi
    echo "  ! refusing to start: AUTOOS_OMNIROUTE_PUBLIC_URL is not an http(s)://host[:port][/path] URL without credentials or blanks."
    echo "    The gateway exits at startup on an invalid public URL, and docker would restart it in a loop."
    echo "    Fix it in $STACK_ENV, or remove it (unset and empty are the same)."
    [[ -n "${AUTOOS_OMNIROUTE_PUBLIC_URL:-}" ]] && echo "    (this shell exports AUTOOS_OMNIROUTE_PUBLIC_URL, which wins over the file)"
    if [[ $DRY -eq 1 ]]; then echo "    (dry run: a real run stops here)"; return 0; fi
    return 1
}

# preflight: everything that can refuse a `compose up`, before anything is
# built, stopped or recreated.
preflight() {
    bind_guard || return 1
    public_url_guard
}

# ─── data directories ───────────────────────────────────────────────────────
# The base compose mounts the bind-mounted state from: the environment beats
# --env-file in compose's interpolation, then stack.env, then what init writes.
stack_data_dir() {
    local d="${AUTOOS_STACK_DATA:-}"
    [[ -n "$d" ]] || d="$(env_value "$STACK_ENV" AUTOOS_STACK_DATA)"
    printf '%s' "${d:-$DATA_DIR}"
}

# ensure_data_dirs <base>: the directories compose bind-mounts from <base>,
# each created private (0700) and the operator's when missing - never changed
# when present, the gateway's own data included. A mount source that is missing
# when compose starts is created by the docker daemon as root, and the
# containers (host uid) could not write it. init calls this, and so does `up`:
# an already initialised host gets a newly added mount (qoder-home) that way.
ensure_data_dirs() {
    local base="$1" d
    for d in "$base" "$base/omniroute" "$base/opencode-home" "$base/qoder-home"; do
        if [[ -d "$d" ]]; then continue; fi
        if [[ $DRY -eq 1 ]]; then echo "  - would create $d"; continue; fi
        mkdir -p "$d"
        chmod 700 "$d"
        echo "  + created $d"
    done
}

# ─── init ───────────────────────────────────────────────────────────────────
cmd_init() {
    echo "Docker AI stack: config $CONFIG_DIR, data $DATA_DIR, code $CODE_DIR"
    if [[ ! -d "$CODE_DIR" && $DRY -eq 0 ]]; then
        echo "  ! code dir $CODE_DIR does not exist - set AUTOOS_CODE_DIR"; return 1
    fi
    local d
    ensure_data_dirs "$DATA_DIR"
    for d in "$DATA_DIR/opencode-home/.config/opencode" "$OH_DIR"; do
        if [[ -d "$d" ]]; then continue; fi
        if [[ $DRY -eq 1 ]]; then echo "  - would create $d"; else mkdir -p "$d"; echo "  + created $d"; fi
    done
    # The gateway DB, opencode sessions, the MCP caches and qodercli's home:
    # the operator's only.
    [[ $DRY -eq 1 ]] || chmod 700 "$DATA_DIR" "$DATA_DIR/omniroute" "$DATA_DIR/opencode-home" "$DATA_DIR/qoder-home"

    ensure_env_file "$STACK_ENV" \
        AUTOOS_UID "$(id -u)" AUTOOS_GID "$(id -g)" \
        AUTOOS_CODE_DIR "$CODE_DIR" AUTOOS_STACK_DATA "$DATA_DIR" \
        AUTOOS_STACK_CONFIG "$CONFIG_DIR" AUTOOS_OPENHANDS_DIR "$OH_DIR" \
        AUTOOS_STACK_BIND 0.0.0.0 \
        OMNIROUTE_MEMORY_MB 1536 OMNIROUTE_MEM_LIMIT 2560m \
        OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT 6 \
        OMNIROUTE_CHAT_ADMISSION_QUEUE_MS 60000 \
        OPENCODE_MEM_LIMIT 1536m AUTOOS_OPENHANDS_MEMORY 2g \
        || return 1

    # opencode config: derived from the host's, never hand-kept twice.
    local oc_cfg="$DATA_DIR/opencode-home/.config/opencode/opencode.json" names=()
    if [[ -f "$OC_HOST_CFG" ]]; then
        local dry_flag=()
        [[ $DRY -eq 1 ]] && dry_flag=(--dry-run)
        python3 "$REPO/tools/render-opencode-container-config.py" --source "$OC_HOST_CFG" \
            --out "$oc_cfg" --code-dir "$CODE_DIR" "${dry_flag[@]}"
        mapfile -t names < <(grep -oE '\{env:[A-Za-z_][A-Za-z0-9_]*\}' "$oc_cfg" 2>/dev/null \
            | sed -e 's/^{env://' -e 's/}$//' | sort -u)
        # The skills link (AGENTS.md §8) resolves inside the mounted tree.
        local link="$USER_CFG/opencode/skills" target
        if [[ -L "$link" ]]; then
            target="$(readlink "$link")"
            if [[ "$target" == "$CODE_DIR"/* && ! -e "$(dirname "$oc_cfg")/skills" ]]; then
                if [[ $DRY -eq 1 ]]; then echo "  - would link the skills directory ($target)"
                else ln -s "$target" "$(dirname "$oc_cfg")/skills"; echo "  + linked skills -> $target"; fi
            fi
        fi
    else
        echo "  ! no host opencode config at $OC_HOST_CFG - run: ./setup.sh --only opencode-cli --yes"
        echo "    (opencode starts with its defaults until then; re-run init afterwards)"
    fi

    local password="" client_key pairs=() n
    client_key="$(omniroute_client_key)"
    # configuration/api-keys.yml's opencode_password is the single source;
    # opencode_password_sync writes both derived copies and sets OC_PW.
    opencode_password_sync || return 1
    password="$OC_PW"
    pairs=(OPENCODE_PASSWORD "$password" AUTOOS_OMNIROUTE_KEY "$client_key")
    pairs+=(OMNIGRAPH_TOKEN "${OMNIGRAPH_TOKEN:-$(env_value "$OMNIGRAPH_ENV" OMNIGRAPH_TOKEN)}")
    for n in "${names[@]}"; do
        [[ "$n" == AUTOOS_OMNIROUTE_KEY ]] && continue
        pairs+=("$n" "$(secret_for "$n")")
    done
    ensure_env_file "$CONFIG_DIR/opencode.env" "${pairs[@]}" || return 1

    local url_pattern="${AUTOOS_OPENHANDS_SANDBOX_URL:-}" web_host="${AUTOOS_OPENHANDS_WEB_HOST:-}"
    [[ -z "$url_pattern" ]] && url_pattern="$(running_container_env openhands-app OH_SANDBOX_CONTAINER_URL_PATTERN)"
    [[ -z "$web_host" ]] && web_host="$(running_container_env openhands-app WEB_HOST)"
    ensure_env_file "$CONFIG_DIR/openhands.env" \
        LLM_API_KEY "$client_key" \
        OH_SANDBOX_CONTAINER_URL_PATTERN "$url_pattern" \
        WEB_HOST "$web_host" \
        || return 1
    [[ -z "$client_key" ]] && echo "  ! no omniroute client key (configuration/api-keys.yml) - opencode and OpenHands cannot use the gateway yet"
    return 0
}

# ─── up / down / status ─────────────────────────────────────────────────────
# compose_service_field <service> <key> [<subkey>]: the value of a key of the
# service ("image"), or of a key inside a map of it ("build" "dockerfile");
# nothing when absent. Plain awk, like compose_services: compose.yml is kept
# anchor-free so that no YAML library is needed.
compose_service_field() {
    awk -v svc="$1" -v key="$2" -v subkey="${3:-}" '
        function clean(v) {
            sub(/[[:space:]]*#.*$/, "", v)
            gsub(/["\047]/, "", v)
            sub(/^[[:space:]]+/, "", v)
            sub(/[[:space:]]+$/, "", v)
            return v
        }
        /^[^[:space:]#]/ { in_svc = 0; in_map = 0; next }
        /^  [A-Za-z0-9_.-]+:[[:space:]]*(#.*)?$/ { in_svc = ($1 == svc ":"); in_map = 0; next }
        !in_svc { next }
        /^    [A-Za-z_]+:/ {
            in_map = 0
            k = $1; sub(/:$/, "", k)
            v = $0; sub(/^    [A-Za-z_]+:[[:space:]]*/, "", v)
            if (k == key) {
                if (subkey == "") { print clean(v); exit }
                in_map = 1
            }
            next
        }
        in_map && subkey != "" && $1 == subkey ":" {
            v = $0; sub(/^[[:space:]]*[A-Za-z_]+:[[:space:]]*/, "", v)
            print clean(v); exit
        }
    ' "$HERE/compose.yml"
}

# image_source_hash <dockerfile>: a locally built layer's identity - the
# Dockerfile plus the digest of the base image it builds FROM. Built images
# carry it as the label org.autoos.<service>.source; a mismatch rebuilds, so an
# edited Dockerfile or a bumped base never keeps serving the old layer under
# the unchanged local tag.
image_source_hash() {
    local base
    base="$(sed -n 's/^FROM[[:space:]][[:space:]]*[^[:space:]]*@\(sha256:[0-9a-f]\{64\}\).*/\1/p' "$1" | head -n1)"
    { cat "$1"; printf 'base=%s\n' "$base"; } | sha256sum | cut -d' ' -f1
}

# ensure_images <service...>: a service compose builds (it has `build:`) gets
# its local image built - or rebuilt when the label above differs; every other
# service is pulled when its image is missing.
ensure_images() {
    local svc img dockerfile label want have what
    for svc in "$@"; do
        img="$(compose_service_field "$svc" image)"
        dockerfile="$(compose_service_field "$svc" build dockerfile)"
        if [[ -n "$dockerfile" ]]; then
            label="org.autoos.$svc.source"
            want="$(image_source_hash "$HERE/$dockerfile")"
            if "$DOCKER" image inspect "$img" >/dev/null 2>&1; then
                have="$("$DOCKER" image inspect -f "{{index .Config.Labels \"$label\"}}" "$img" 2>/dev/null || true)"
                [[ "$have" == "$want" ]] && continue
                what="rebuild $img ($dockerfile or its base image changed)"
            else
                what="build $img ($dockerfile)"
            fi
            if [[ $DRY -eq 1 ]]; then echo "  - would $what"; continue; fi
            echo "  + $what"
            "$DOCKER" build --label "$label=$want" -t "$img" \
                -f "$HERE/$dockerfile" "$HERE" || { echo "  ! building $img failed"; return 1; }
        else
            if "$DOCKER" image inspect "$img" >/dev/null 2>&1; then continue; fi
            if [[ $DRY -eq 1 ]]; then echo "  - would pull $img"; continue; fi
            echo "  + pulling $img"
            dc pull "$svc" || { echo "  ! pulling $img failed"; return 1; }
        fi
    done
}

attach_shared_mcp() {
    # serena-mcp publishes on 127.0.0.1 only; on the stack network opencode
    # reaches it by name. Idempotent; lost when serena-mcp is recreated, so
    # every `up` re-checks it.
    local c=serena-mcp
    container_running "$c" || return 0
    if "$DOCKER" inspect -f '{{json .NetworkSettings.Networks}}' "$c" 2>/dev/null | grep -q '"autoos-ai"'; then
        return 0
    fi
    if [[ $DRY -eq 1 ]]; then echo "  - would attach $c to the autoos-ai network"; return 0; fi
    if "$DOCKER" network connect autoos-ai "$c"; then echo "  + attached $c to the autoos-ai network"
    else echo "  ! could not attach $c - serena is unreachable from opencode"; fi
}

# A host with nothing native to move (a fresh server): the first `up` that
# gets the gateway and opencode answering from their containers claims the
# services. A host with native units only moves through migrate.
claim_fresh_host() {
    stack_owned && return 0
    is_compose_container autoos-omniroute && container_running autoos-omniroute && gateway_ok || return 0
    is_compose_container autoos-opencode && container_running autoos-opencode && opencode_ok || return 0
    unit_known autoos-omniroute && return 0
    unit_known autoos-opencode && return 0
    if "$DOCKER" inspect openhands-app >/dev/null 2>&1 && ! is_compose_container openhands-app; then return 0; fi
    write_marker up || return 0
    if [[ ! -s "$MANAGE_KEY_FILE" ]]; then
        echo "  ! no $MANAGE_KEY_FILE: apply.sh needs a key with scope 'manage' (dashboard -> API keys), saved there mode 600"
    fi
}

cmd_up() {
    local svcs=("${SERVICES[@]}") start=() svc c port unit
    (( ${#svcs[@]} )) || svcs=(omniroute opencode openhands)
    if [[ ! -f "$STACK_ENV" ]]; then
        if [[ $DRY -eq 1 ]]; then echo "  - would run init first"; else cmd_init || return 1; fi
    fi
    for svc in "${svcs[@]}"; do
        c="$(service_container "$svc")"; port="$(service_port "$svc")"
        [[ -n "$c" ]] || { echo "  ! unknown service $svc (omniroute, opencode, openhands)"; return 2; }
        if container_running "$c"; then
            if [[ "$svc" == openhands ]] && ! is_compose_container "$c"; then
                echo "  ! $svc: $c runs outside the stack - replace it with: ai-stack.sh migrate --yes"; continue
            fi
            echo "  = $svc: $c running (skipped)"; start+=("$svc"); continue
        fi
        # A registered native unit would start next to the container at the
        # next boot and fight it for the port: that host moves with migrate.
        unit="$(service_unit "$svc")"
        if [[ -n "$unit" ]] && ! stack_owned && unit_known "$unit"; then
            echo "  ! $svc: the native $unit unit is registered - not started next to it. Move it with: ai-stack.sh migrate --yes"
            continue
        fi
        if port_listening "$port"; then
            echo "  ! $svc: :$port is held by a native service - not started. Move it with: ai-stack.sh migrate --yes"
            continue
        fi
        if [[ "$svc" == openhands ]] && "$DOCKER" inspect openhands-app >/dev/null 2>&1 && ! is_compose_container openhands-app; then
            echo "  ! $svc: a stopped openhands-app from start-stack.sh exists - replace it with: ai-stack.sh migrate --yes"
            continue
        fi
        start+=("$svc")
    done
    (( ${#start[@]} )) || return 0
    # Before the images: compose up below may create these containers or
    # recreate running ones (a changed bind, a rebuilt image).
    preflight || return 1
    # A mount source added since init ran (qoder-home) must not be left to
    # docker, which would create it as root.
    ensure_data_dirs "$(stack_data_dir)"
    ensure_images "${start[@]}" || return 1
    if [[ $DRY -eq 1 ]]; then
        echo "  - would run: docker compose -p autoos-ai up -d --no-deps ${start[*]}"
        attach_shared_mcp
        return 0
    fi
    dc_up --no-deps "${start[@]}" || { echo "  ! docker compose up failed - see: $0 status"; return 1; }
    attach_shared_mcp
    for svc in "${start[@]}"; do
        case "$svc" in
            omniroute) wait_for gateway_ok && echo "  + omniroute answers on :20128" || echo "  ! omniroute did not answer - docker logs autoos-omniroute" ;;
            opencode)  wait_for opencode_ok && echo "  + opencode answers on :4096" || echo "  ! opencode did not answer - docker logs autoos-opencode" ;;
            openhands) wait_for openhands_ok && echo "  + openhands answers on :3000 (tier profiles: configuration/start-stack.sh openhands)" || echo "  ! openhands did not answer - docker logs openhands-app" ;;
        esac
    done
    claim_fresh_host
}

is_compose_container() {
    [[ "$("$DOCKER" inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$1" 2>/dev/null || true)" == autoos-ai ]]
}

cmd_down() {
    if [[ $DRY -eq 1 ]]; then echo "  - would run: docker compose -p autoos-ai stop ${SERVICES[*]}"; return 0; fi
    dc stop "${SERVICES[@]}"
}

# restart one service through the same compose wrapper as up/down (same
# project and env file via dc): no backup, no rebuild, no guard. verify names
# this as the relief when page cache holds the gateway's pressure guard.
cmd_restart() {
    local svc c
    if (( ${#SERVICES[@]} == 0 )); then
        echo "  ! restart needs a service (omniroute, opencode, openhands)"; return 2
    fi
    # Validate every name first: an unknown one must not follow a restart.
    for svc in "${SERVICES[@]}"; do
        [[ -n "$(service_container "$svc")" ]] || { echo "  ! unknown service $svc (omniroute, opencode, openhands)"; return 2; }
    done
    for svc in "${SERVICES[@]}"; do
        c="$(service_container "$svc")"
        if [[ $DRY -eq 1 ]]; then echo "  - would run: docker compose -p autoos-ai restart $svc"; continue; fi
        dc restart "$svc" || { echo "  ! docker compose restart $svc failed - see: $0 status"; return 1; }
        echo "  + $svc restarted ($c)"
    done
}

cmd_status() {
    local svc c
    if stack_owned; then echo "  owner: the docker stack ($MARKER)"
    else echo "  owner: native units (no $MARKER)"; fi
    for svc in omniroute opencode openhands; do
        c="$(service_container "$svc")"
        printf '  %-10s %-18s %s\n' "$svc" "$c" "$(container_state "$c")"
    done
    echo "  gateway :20128 /api/health -> $(http_code http://127.0.0.1:20128/api/health); keyless /v1/models -> $(http_code http://127.0.0.1:20128/v1/models) (401 expected)"
    echo "  opencode :4096 / -> $(http_code http://127.0.0.1:4096/); /api/session without password -> $(http_code http://127.0.0.1:4096/api/session) (401 expected)"
    echo "  openhands :3000 / -> $(http_code http://127.0.0.1:3000/)"
}

# Ownership, not container state. The marker is written only after every
# service answered from its container (a completed migrate, or a fresh
# host's first `up`) and removed by rollback, so a container a failed
# migrate left behind never counts. A stopped-but-owned stack stays active
# on purpose: Start-AutoOSStack.sh, start-stack.sh and apply.sh then resume
# it with `up` instead of starting the stale native copies.
cmd_is_active() {
    [[ -f "$STACK_ENV" ]] && stack_owned
}

# ─── manage key ─────────────────────────────────────────────────────────────
# The host CLI's machine token is accepted from loopback peers only, and a
# published container port sees the docker gateway as its peer: after the
# move, apply.sh manages the gateway with a manage-scoped key instead. It is
# created while the NATIVE gateway still answers (the CLI token works there).
# Without it apply.sh could never manage the container gateway, so migrate
# refuses (returns 1) before anything stops.
ensure_manage_key() {
    local hint="create a key with scope 'manage' in the dashboard (API keys), save it to $MANAGE_KEY_FILE (mode 600), re-run"
    if [[ -s "$MANAGE_KEY_FILE" ]]; then echo "  = manage key $MANAGE_KEY_FILE present (skipped)"; return 0; fi
    if [[ $DRY -eq 1 ]]; then echo "  - would create a manage-scoped gateway key into $MANAGE_KEY_FILE (0600)"; return 0; fi
    command -v omniroute >/dev/null || { echo "  ! omniroute CLI missing - $hint"; return 1; }
    local out key
    out="$(cd "$HOME" && omniroute --output json --no-color api api-keys post-api-keys \
        --body '{"name":"autoos-manage","scopes":["manage"]}' 2>/dev/null || true)"
    key="$(printf '%s' "$out" | python3 -c '
import json, sys
t = sys.stdin.read(); i = t.find("{")
try:
    print(json.loads(t[i:]).get("key", "") if i >= 0 else "")
except ValueError:
    print("")' 2>/dev/null || true)"
    if [[ -z "$key" ]]; then
        echo "  ! could not create a manage key (is the native gateway up?) - $hint"
        return 1
    fi
    mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR" || return 1
    ( umask 077; printf '%s\n' "$key" >"$MANAGE_KEY_FILE" ) || return 1
    chmod 600 "$MANAGE_KEY_FILE"
    echo "  + created the manage-scoped key autoos-manage ($MANAGE_KEY_FILE)"
}

# ─── state copies ───────────────────────────────────────────────────────────
# backup_dir <dir> <archive>: a 0600 tar.gz of <dir>. A failed run leaves no
# partial archive behind - it would pass for a good backup later.
backup_dir() {
    local dir="$1" archive="$2" stem
    stem="${archive%.tar.gz}"
    archive="$(autoos_backup_path "$stem" "" ".tar.gz")" || return 1
    if ! mkdir -p "$(dirname "$archive")" || ! chmod 700 "$(dirname "$archive")"; then return 1; fi
    if ! ( umask 077; tar -C "$(dirname "$dir")" -czf "$archive" "$(basename "$dir")" ); then
        rm -f -- "$archive"
        echo "  ! backing up $dir failed (disk full?) - the partial archive was removed"
        return 1
    fi
    chmod 600 "$archive"
    echo "  + backed up $dir to $archive"
}

# replace_dir_with_copy <src> <dest> <ts>: afterwards <dest> holds exactly
# what <src> holds. Copied into a fresh sibling first, then swapped in: an
# overlay would keep files <src> lacks, e.g. a stale storage.sqlite-wal that
# SQLite replays onto the restored database. A non-empty <dest> is moved
# aside to <dest>.autoos-backup-<ts> (then -1, -2, ... via autoos_backup_path
# if that name is taken - a PID-only fallback isn't safe against a second
# collision), never deleted.
replace_dir_with_copy() {
    local src="$1" dest="$2" ts="$3" stage aside
    aside="$(autoos_backup_path "$dest" "$ts")"
    mkdir -p "$(dirname "$dest")" || return 1
    stage="$(mktemp -d "$dest.autoos-staging-XXXXXX")" || return 1
    if ! cp -a "$src/." "$stage/"; then
        rm -rf "$stage"
        echo "  ! copying $src failed - $dest is unchanged"
        return 1
    fi
    if [[ -d "$dest" ]] && rmdir "$dest" 2>/dev/null; then
        :   # empty (fresh from init): nothing to keep
    elif [[ -e "$dest" ]]; then
        if ! mv "$dest" "$aside"; then
            rm -rf "$stage"
            echo "  ! could not move $dest aside - it is unchanged"
            return 1
        fi
        echo "  - previous $dest kept as $aside"
    fi
    if ! mv "$stage" "$dest"; then
        [[ -e "$aside" && ! -e "$dest" ]] && mv "$aside" "$dest"
        rm -rf "$stage"
        echo "  ! could not swap the copy into $dest - the previous contents are back"
        return 1
    fi
}

# ─── migrate ────────────────────────────────────────────────────────────────
migrate_plan() {
    cat <<EOF
Migration plan (native units -> docker AI stack):
  0. refuses - before anything stops - when the stack already owns the
     services (a completed migrate), when the publish address is on the LAN
     and neither coding-agents-fw.service runs nor AUTOOS_STACK_ALLOW_LAN=1
     is set, or when no manage key exists and none can be created
  1. init: env files under $CONFIG_DIR, the opencode config, images (build/pull)
  2. create a manage-scoped gateway key for the host CLI ($MANAGE_KEY_FILE)
     while the native gateway is still up
  3. stop the autoos-omniroute unit (the gateway is down from here; the
     SQLite files are quiescent from now on)
  4. back up $OMNI_HOME to $DATA_DIR/backups/omniroute-<timestamp>.tar.gz (0600)
  5. copy $OMNI_HOME (storage.sqlite + .env with STORAGE_ENCRYPTION_KEY) into
     $DATA_DIR/omniroute - the original stays as it is
  6. start the omniroute container and wait until it answers /api/health and
     refuses a keyless /v1 call with 401
  7. stop the autoos-opencode unit, start the opencode container, wait until
     it answers
  8. replace the openhands container from start-stack.sh with the compose one
     (state stays in $OH_DIR; running sandboxes keep running), wait until it
     answers
  9. only when all three answered: write $MARKER (from then on is-active is
     true), then unregister autoos-omniroute and autoos-opencode
     (register-autostart.sh --unregister --only)
  Any failure from step 3 on hands EVERY service moved so far back: the
  containers are removed (their data stays), the units start again, and a
  replaced openhands-app is recreated by start-stack.sh.
Run it with:  $0 migrate --yes
Undo it with: $0 rollback --yes
EOF
}

migrate_gateway_state() {
    # Backup, then copy (never move): $OMNI_HOME stays the rollback source.
    [[ -d "$OMNI_HOME" ]] || return 0
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    backup_dir "$OMNI_HOME" "$DATA_DIR/backups/omniroute-$ts.tar.gz" || return 1
    replace_dir_with_copy "$OMNI_HOME" "$DATA_DIR/omniroute" "$ts" || return 1
    chmod 700 "$DATA_DIR/omniroute" || return 1
    # Pid files of the native server would read as "already running".
    rm -f "$DATA_DIR/omniroute/server/.pid" "$DATA_DIR/omniroute/supervisor/.pid"
    echo "  + copied $OMNI_HOME into $DATA_DIR/omniroute"
}

stop_native() {
    # stop_native <unit> <port>: the unit when there is one, else a
    # hand-started process on the port (same takeover as register-autostart).
    local unit="$1" port="$2" pid _
    if unit_active "$unit"; then
        "$SYSTEMCTL" --user stop "$unit.service"
        echo "  - stopped the $unit unit"
    fi
    pid="$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2 || true)"
    if [[ -n "$pid" ]]; then
        if [[ "$unit" == autoos-omniroute ]]; then (cd "$HOME" && omniroute stop) >/dev/null 2>&1 || kill "$pid" 2>/dev/null || true
        else kill "$pid" 2>/dev/null || true; fi
        echo "  - stopped the hand-started process on :$port (pid $pid)"
    fi
    for _ in $(seq 1 20); do port_listening "$port" || return 0; sleep 1; done
    echo "  ! :$port is still held - is it another user's process?"
    return 1
}

# restore_native <unit> <service>: remove the service's container - a
# stopped one left behind would linger for the next run - and give the port
# back to the native unit (registered again if this run already removed it).
# Returns 1 when there is no unit to start (the service ran by hand).
restore_native() {
    local unit="$1" svc="$2"
    echo "  ! handing :$(service_port "$svc") back to $unit"
    dc rm -s -f "$svc" >/dev/null 2>&1 || true
    if unit_known "$unit"; then
        "$SYSTEMCTL" --user start "$unit.service" || true
    elif [[ " ${UNITS_BEFORE[*]} " == *" $unit "* ]]; then
        bash "$REGISTER" --only "$unit" || true
    else
        echo "  ! $unit ran by hand, without a unit - start it again with: configuration/autostart/Start-AutoOSStack.sh"
        return 1
    fi
}

moved() { [[ " ${MOVED[*]} " == *" $1 "* ]]; }

# migrate_abort: every service this run moved goes back to its native owner,
# in dependency order (the gateway first: start-stack.sh needs it).
migrate_abort() {
    echo "  ! migration failed - every service this run moved goes back to its native owner"
    # Before anything is re-registered: register-autostart skips the units
    # while the marker says docker owns them. (migrate refused to start with
    # one, so any marker here is this run's.)
    rm -f "$MARKER" 2>/dev/null || true
    if moved openhands; then dc rm -s -f openhands >/dev/null 2>&1 || true; fi
    if moved omniroute && restore_native autoos-omniroute omniroute; then
        wait_for gateway_ok || echo "  ! the native gateway does not answer yet - journalctl --user -u autoos-omniroute"
    fi
    if moved opencode; then restore_native autoos-opencode opencode || true; fi
    if [[ $OH_REPLACED -eq 1 ]]; then
        echo "  - recreating the start-stack.sh openhands-app container"
        bash "$START_STACK" openhands || echo "  ! openhands did not come back - run: configuration/start-stack.sh openhands"
    fi
    echo "  = the docker stack does not own the services (no $MARKER); backups stay in $DATA_DIR/backups"
}

cmd_migrate() {
    if [[ $YES -eq 0 || $DRY -eq 1 ]]; then
        migrate_plan
        if stack_owned; then
            echo "(already migrated: $MARKER exists - migrate --yes refuses; go back with: $0 rollback --yes)"
        fi
        [[ -d "$OMNI_HOME" ]] || echo "(no $OMNI_HOME here: nothing to carry over, 'up' starts a fresh gateway)"
        return 0
    fi
    # A second run would copy the older host state over the containers' newer one.
    if stack_owned; then
        echo "  ! already migrated ($MARKER): the containers hold the newest state, and migrating"
        echo "    again would overwrite it with the older host copy. Nothing was changed."
        echo "    To go back to the native units: $0 rollback --yes"
        return 1
    fi
    command -v "$DOCKER" >/dev/null 2>&1 || { echo "docker is not installed"; return 1; }
    "$DOCKER" compose version >/dev/null 2>&1 || { echo "docker compose v2 is required"; return 1; }
    # Everything that can refuse, refuses here - before a native service stops.
    cmd_init || return 1
    preflight || return 1
    ensure_manage_key || return 1
    ensure_images omniroute opencode openhands || return 1

    MOVED=(); UNITS_BEFORE=(); OH_REPLACED=0
    local u
    for u in autoos-omniroute autoos-opencode; do
        if unit_known "$u"; then UNITS_BEFORE+=("$u"); fi
    done

    # 1. Gateway. The unit only STOPS here; it is unregistered in step 4.
    if container_running autoos-omniroute && is_compose_container autoos-omniroute; then
        echo "  = omniroute already runs in docker (skipped)"
    else
        MOVED+=(omniroute)
        if ! stop_native autoos-omniroute 20128 || ! migrate_gateway_state; then
            migrate_abort
            return 1
        fi
        if ! dc_up --no-deps omniroute || ! wait_for gateway_ok || ! gateway_keyed; then
            echo "  ! the omniroute container did not answer /api/health, or served /v1 without a key - docker logs autoos-omniroute"
            migrate_abort
            return 1
        fi
        echo "  + omniroute container answers on :20128 and refuses keyless /v1 (401)"
    fi

    # 2. opencode serve.
    if container_running autoos-opencode && is_compose_container autoos-opencode; then
        echo "  = opencode already runs in docker (skipped)"
    else
        MOVED+=(opencode)
        if ! stop_native autoos-opencode 4096; then
            migrate_abort
            return 1
        fi
        if ! dc_up --no-deps opencode || ! wait_for opencode_ok; then
            echo "  ! the opencode container did not answer on :4096 - docker logs autoos-opencode"
            migrate_abort
            return 1
        fi
        attach_shared_mcp
        echo "  + opencode container answers on :4096"
    fi

    # 3. OpenHands: the start-stack.sh container becomes the compose one.
    #    start-stack.sh repairs the settings, syncs + pushes the tier profiles
    #    and - told a migration runs - starts the compose service.
    MOVED+=(openhands)
    if "$DOCKER" inspect openhands-app >/dev/null 2>&1 && ! is_compose_container openhands-app; then
        if ! "$DOCKER" rm -f openhands-app >/dev/null; then migrate_abort; return 1; fi
        OH_REPLACED=1
        echo "  - removed the start-stack.sh openhands-app container (state stays in $OH_DIR)"
    fi
    if ! AUTOOS_AI_STACK_MIGRATING=1 bash "$START_STACK" openhands || ! openhands_ok; then
        echo "  ! openhands did not answer from the compose service - docker logs openhands-app"
        migrate_abort
        return 1
    fi

    # 4. All three answered: claim, then let the native units go. The marker
    #    comes FIRST - it is what rollback needs - so no state exists in which
    #    the units are gone but nothing owns the services. A failure in either
    #    step aborts, and the abort removes the marker again.
    if ! write_marker migrate; then
        echo "  ! could not write $MARKER - nothing was unregistered"
        migrate_abort
        return 1
    fi
    if (( ${#UNITS_BEFORE[@]} )); then
        if ! bash "$REGISTER" --unregister --only "$(IFS=,; printf '%s' "${UNITS_BEFORE[*]}")"; then
            echo "  ! could not unregister ${UNITS_BEFORE[*]}"
            migrate_abort
            return 1
        fi
    fi
    cmd_status
}

# ─── rollback ───────────────────────────────────────────────────────────────
rollback_plan() {
    cat <<EOF
Rollback plan (docker AI stack -> native units):
  1. refuses unless the docker stack owns the services ($MARKER)
  2. back up $OMNI_HOME to $DATA_DIR/backups/omniroute-native-<ts>.tar.gz
     (0600) while the stack still runs - a failed backup changes nothing
  3. remove the compose containers (docker compose down; the bind-mounted
     data in $DATA_DIR stays)
  4. replace $OMNI_HOME with a copy of $DATA_DIR/omniroute (the container's
     state is the newest: keys, combos, usage). Copied into a fresh sibling,
     then swapped in - no stale SQLite WAL survives - and the previous
     directory is kept as $OMNI_HOME.autoos-backup-<ts>
  5. remove $MARKER, then register-autostart.sh --only
     autoos-omniroute,autoos-opencode (the units come back and start)
  6. start-stack.sh openhands (the docker run container, as before)
Run it with: $0 rollback --yes
EOF
}

cmd_rollback() {
    if [[ $YES -eq 0 || $DRY -eq 1 ]]; then rollback_plan; return 0; fi
    if ! stack_owned; then
        echo "  ! the docker stack does not own the services (no $MARKER) - nothing to roll back."
        echo "    After a failed migrate the native units already run again: $0 status"
        return 1
    fi
    local ts restore=0
    ts="$(date +%Y%m%d-%H%M%S)"
    [[ -e "$DATA_DIR/omniroute/storage.sqlite" ]] && restore=1
    if [[ $restore -eq 1 && -d "$OMNI_HOME" ]]; then
        backup_dir "$OMNI_HOME" "$DATA_DIR/backups/omniroute-native-$ts.tar.gz" || return 1
    fi
    # A foreign container on the network would make `down` fail on it.
    "$DOCKER" network disconnect autoos-ai serena-mcp >/dev/null 2>&1 || true
    if ! dc down; then
        echo "  ! docker compose down failed - starting the docker stack again"
        dc_up || echo "  ! the docker stack stays down - fix the above, then: $0 up"
        return 1
    fi
    echo "  - compose containers removed"
    if [[ $restore -eq 1 ]]; then
        if ! replace_dir_with_copy "$DATA_DIR/omniroute" "$OMNI_HOME" "$ts"; then
            # Nothing native is registered yet: bring the containers back rather
            # than leave the host without a gateway.
            echo "  ! could not restore $OMNI_HOME - starting the docker stack again"
            dc_up || echo "  ! the docker stack stays down - fix the above, then: $0 up"
            return 1
        fi
        # The container's pid files would read as "already running" natively.
        rm -f "$OMNI_HOME/server/.pid" "$OMNI_HOME/supervisor/.pid"
        echo "  + $OMNI_HOME now holds the container state"
    fi
    rm -f "$MARKER"
    echo "  - removed $MARKER: the native units own the services again"
    if ! bash "$REGISTER" --only autoos-omniroute,autoos-opencode; then
        echo "  ! registering the native units failed - re-run: $REGISTER --only autoos-omniroute,autoos-opencode"
        return 1
    fi
    bash "$START_STACK" openhands || true
}

# ─── failover: the LiteLLM standby router ────────────────────────────────
# One command moves the gateway PORT between the omniroute container and a
# LiteLLM standby, and back - clients keep their base URL (the gateway's
# published port), their key (the gateway CLIENT key) and their model names
# (combo ids: LiteLLM's rendered config.yaml names every model after the
# servable combo id). The standby binds the gateway's own bind and port with
# the client key as its master key, from its own state dir, so the always-on
# :4000 unit is untouched. The key VALUE never appears here: it travels from
# its source straight into client.key (0600) and from there only as a PATH in
# the starter's environment - never argv, never output.
failover_port() { service_port omniroute; }

# The standby answers for clients while this is 200.
failover_litellm_ok() {
    [[ "$(http_code "http://127.0.0.1:$FAILOVER_PORT/health/liveliness")" == 200 ]]
}

# Materialize client.key (0600) from the current gateway client key.
# The value is redirected, never echoed: no log or output line can carry it.
ensure_client_key_file() {
    # Rebuilt from the current key on every call: a rotated key must not leave
    # the standby serving the old one. Replaced only when it differs.
    if [[ $DRY -eq 1 ]]; then echo "  - would write the gateway client key to $CLIENT_KEY_FILE (0600)"; return 0; fi
    mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR" || return 1
    local tmp="$CLIENT_KEY_FILE.tmp-$$"
    rm -f "$tmp"
    if [[ -n "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
        ( umask 077; printf '%s\n' "${AUTOOS_OMNIROUTE_KEY}" >"$tmp" ) || { rm -f "$tmp"; return 1; }
    else
        ( umask 077; omniroute_client_key >"$tmp" ) || { rm -f "$tmp"; return 1; }
    fi
    chmod 600 "$tmp" || { rm -f "$tmp"; return 1; }
    if [[ ! -s "$tmp" ]]; then
        rm -f "$tmp"
        echo "  ! no omniroute client key (configuration/api-keys.yml) - failover needs the key the clients use"
        return 1
    fi
    if [[ -f "$CLIENT_KEY_FILE" ]] && cmp -s "$tmp" "$CLIENT_KEY_FILE"; then
        rm -f "$tmp"; chmod 600 "$CLIENT_KEY_FILE"; return 0
    fi
    mv "$tmp" "$CLIENT_KEY_FILE" || { rm -f "$tmp"; return 1; }
    chmod 600 "$CLIENT_KEY_FILE"
    echo "  + wrote the gateway client key to $CLIENT_KEY_FILE (0600)"
}

# failover_cmdline <pid>: that process's argv, one argument per line (empty
# when the pid is gone or unreadable).
failover_cmdline() {
    tr '\0' '\n' <"/proc/$1/cmdline" 2>/dev/null || true
}

# failover_litellm_name <pid>: the PROGRAM is litellm - argv[0], or the script
# in argv[1] when a venv python runs it - never "litellm" anywhere in the
# arguments, so a reused pid belongs to someone else. Same rule as
# start-litellm.sh's foreign-port check, and on its own not enough: the
# always-on :4000 proxy is the same program.
failover_litellm_name() {
    local arg
    local found=0
    while IFS= read -r arg; do
        [[ "${arg##*/}" == litellm ]] && found=1
    done < <(failover_cmdline "$1" | head -n 2)
    (( found ))
}

# failover_argv_ports <pid> <port>: that process was STARTED on <port> -
# `--port <port>` or `--port=<port>` in its own argv. The one piece of evidence
# that survives the instant the standby lets the socket go.
failover_argv_ports() {
    local prev="" arg
    while IFS= read -r arg; do
        if [[ "$arg" == "--port=$2" ]] || { [[ "$prev" == "--port" ]] && [[ "$arg" == "$2" ]]; }; then
            return 0
        fi
        prev="$arg"
    done < <(failover_cmdline "$1")
    return 1
}

# failover_listens_on <pid> <port>: ss names that pid as a holder of a
# listening socket on <port> (same-user processes only - ss hides the owner of
# anyone else's socket).
failover_listens_on() {
    local p
    while IFS= read -r p; do
        [[ "$p" == "$1" ]] && return 0
    done < <(ss -ltnpH "sport = :$2" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2)
    return 1
}

# failover_is_standby <pid>: the failover standby and nothing else, so a
# signal here can never reach the always-on :4000 proxy - which the SAME
# starter script runs, with the SAME argv shape, and which the name test alone
# would accept. Both halves must hold (review lstby2c): the program is litellm,
# AND it belongs to the failover port - listening on it now, or started with
# --port <failover port>. A stale state-file pid that today belongs to the
# :4000 proxy fails the second half and is left alone.
failover_is_standby() {
    local pid="${1:-}" port
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [[ -d "/proc/$pid" ]] || return 1
    failover_litellm_name "$pid" || return 1
    port="$(failover_port)"
    failover_argv_ports "$pid" "$port" && return 0
    failover_listens_on "$pid" "$port"
}

# Every pid listening on the gateway port that passes the standby check. `ss`
# can name several processes for one port - separate IPv4 and IPv6 lines, or
# programs sharing the socket - so EVERY pid=N is tested, not the first line of
# the whole output (review lstby2c). Prints one per line, possibly none.
failover_listener_pid() {
    local port p
    port="$(failover_port)"
    while IFS= read -r p; do
        [[ "$p" =~ ^[0-9]+$ ]] || continue
        if failover_is_standby "$p"; then printf '%s\n' "$p"; fi
    done < <(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2)
    return 0
}

# Every pid whose own argv says it is the standby: the PROGRAM is litellm (the
# same test failover_is_standby uses) AND it was STARTED on the gateway port
# (`--port <gateway>`, which the starter always passes). This is the tool-free
# half of the standby check - no `ss`, no `docker`, no `curl` - so the no-state
# gate in `off` can ask "did a failover run here, and is one still up?" without
# driving anything. A gateway-serving host must answer `off` as a true no-op,
# and a command that changes nothing cannot call a tool to prove it: the fake
# harness logs every tool call, and so would a real host's audit trail (review
# lstby2d). A live listener that somehow lacks the argv port is still caught by
# failover_listener_pid once the stop is under way. Prints one per line.
failover_argv_standby_pids() {
    local pid port
    port="$(failover_port)"
    for pid in /proc/[0-9]*; do
        pid="${pid#/proc/}"
        failover_litellm_name "$pid" || continue
        if failover_argv_ports "$pid" "$port"; then printf '%s\n' "$pid"; fi
    done
    return 0
}

# Every pid a stop may be asked of it: the state file's, any pid file in
# the standby's own state dir, plus the live listener when it passes the
# standby check. Only numbers - anything else is ignored - each once (a
# duplicate would SIGPIPE a `| head -n1` reader under pipefail + errexit).
# These are CANDIDATES, not verdicts: failover_stop_litellm re-checks every
# one, because a state file outliving its standby leaves a pid that now belongs
# to someone else - possibly the :4000 proxy.
failover_pids() {
    local p f
    local -A seen=()
    if [[ -f "$FAILOVER_STATE" ]]; then
        p="$(sed -n 's/^pid=//p' "$FAILOVER_STATE")"
        p="${p%%$'\n'*}"
        p="$(printf '%s' "$p" | tr -d '\r[:space:]')"
        if [[ "$p" =~ ^[0-9]+$ && -z "${seen[$p]:-}" ]]; then seen[$p]=1; printf '%s\n' "$p"; fi
    fi
    for f in "$FAILOVER_DIR/litellm.pid" "$FAILOVER_DIR"/*.pid; do
        [[ -f "$f" ]] || continue
        p="$(tr -d '\r[:space:]' <"$f")"
        p="${p%%$'\n'*}"
        if [[ "$p" =~ ^[0-9]+$ && -z "${seen[$p]:-}" ]]; then seen[$p]=1; printf '%s\n' "$p"; fi
    done
    while IFS= read -r p; do
        if [[ "$p" =~ ^[0-9]+$ && -z "${seen[$p]:-}" ]]; then seen[$p]=1; printf '%s\n' "$p"; fi
    done < <(failover_listener_pid)
    return 0
}

# Stop the standby and nothing else. A pid from failover_pids is signalled only
# while it passes failover_is_standby (the program is litellm AND it belongs to
# the gateway port), so a stale pid, a reused pid, a foreign listener and the
# always-on :4000 proxy are named in the output and left alone. TERM first,
# KILL after 10 s for a standby that ignores TERM; only the pids that passed the
# check are waited on, so a stale one never costs 15 s. Afterwards only the
# standby's own pid files are removed.
failover_stop_litellm() {
    local p i alive
    local -a targets=()
    local -A taken=()
    for p in $(failover_pids); do
        if failover_is_standby "$p"; then
            [[ -n "${taken[$p]:-}" ]] && continue
            taken[$p]=1
            targets+=("$p")
            kill "$p" 2>/dev/null || true
        elif [[ -d "/proc/$p" ]]; then
            echo "  ! pid $p is not litellm any more - leaving it alone"
        else
            # The pid has already exited (or was never real): not a foreign
            # program now holding the port, just a stale record. Naming the
            # difference keeps a stale state file diagnosable (review lstby2d).
            echo "  = pid $p is already gone"
        fi
    done
    if (( ${#targets[@]} )); then
        for i in $(seq 1 10); do
            alive=0
            for p in "${targets[@]}"; do
                failover_is_standby "$p" && alive=1
            done
            (( alive )) || break
            sleep 1
        done
        for p in "${targets[@]}"; do
            if failover_is_standby "$p"; then kill -KILL "$p" 2>/dev/null || true; fi
        done
        for i in $(seq 1 5); do
            alive=0
            for p in "${targets[@]}"; do
                failover_is_standby "$p" && alive=1
            done
            (( alive )) || break
            sleep 1
        done
    fi
    rm -f "$FAILOVER_DIR"/litellm.pid "$FAILOVER_DIR"/*.pid 2>/dev/null || true
}

# The raw `ss -ltnp` lines holding the gateway port (empty when the port is
# free). Printed verbatim on refusal so the operator sees who holds it:
# users:(("prog",pid=N,fd=M)).
failover_port_holder() {
    local port
    port="$(failover_port)"
    ss -ltnpH "sport = :$port" 2>/dev/null || true
}

# After the standby is stopped the gateway port must be FREE before OmniRoute
# comes back: recreating onto a busy port bind-fails ("address already in
# use", live 2026-09-27), and a bare `start` of a port-less container never
# republishes it. Names the holder, rc 1 - never a half state silently.
failover_require_port_free() {
    local holder
    holder="$(failover_port_holder)"
    if [[ -n "$holder" ]]; then
        echo "  ! :$(failover_port) is still held - not starting omniroute (it would bind-fail):"
        printf '%s\n' "$holder" | sed 's/^/    /'
        return 1
    fi
    return 0
}

# Does the omniroute container publish its service port as a NON-NULL host
# binding? `{"20128/tcp":null}` is compose's "key exists, nothing bound" answer
# and is as broken as `{}` - the gateway answers inside its own network while
# the host port stays closed. Read-only; shared by the hand-back and by `off`'s
# gateway-already-serving proof (review lstby2c/lstby2d).
gateway_publishes_port() {
    local port ports
    port="$(failover_port)"
    ports="$("$DOCKER" inspect -f '{{json .NetworkSettings.Ports}}' autoos-omniroute 2>/dev/null || true)"
    grep -qE "\"$port/tcp\"[[:space:]]*:[[:space:]]*\[[^]]*\"HostPort\"" <<<"$ports"
}

# Is the gateway itself the process holding its published port, right now?
# `off` runs after a failover may have been hand-recovered - the operator
# reopened the gateway and the state file outlived it - so a busy port is not
# automatically a reason to refuse. Port alone never proves it: the container
# must be RUNNING, the port PUBLISHED (a non-null host binding) and /api/health
# must answer. Any one of those can be true without the gateway serving
# (a foreign holder, a running container with no published port). Read-only.
failover_gateway_serves_port() {
    container_running autoos-omniroute || return 1
    gateway_publishes_port || return 1
    gateway_ok
}

# Bring OmniRoute back with a RECREATE, never a bare `start`: a stopped
# container left without published ports stays port-less (live 2026-09-27 -
# only `ai-stack.sh up omniroute` fixed it). Through dc_up, so the same
# preflight that guards every other `compose up` (bind guard, public URL)
# judges this one too - handing the port back must not be a way around them.
# Then gateway health plus a published-port check; on failure the manual fix,
# rc 1. Optional <health_tries> bounds the wait: the interrupt trap passes a
# short one, so a handed-back Ctrl-C never sits on the full 180 s.
# shellcheck disable=SC2120  # the INT/TERM trap string passes <health_tries>; shellcheck cannot see into it
failover_bring_back_omniroute() {
    local tries="${1:-36}"
    local port
    port="$(failover_port)"
    dc_up --no-deps omniroute || {
        echo "  ! docker compose up omniroute failed - fix by hand: $0 up omniroute"
        return 1
    }
    if ! wait_for gateway_ok "$tries"; then
        echo "  ! omniroute did not answer - docker logs autoos-omniroute"
        echo "  ! fix by hand: $0 up omniroute"
        return 1
    fi
    if ! gateway_publishes_port; then
        echo "  ! omniroute answers but publishes no port (:$port missing) - fix by hand: $0 up omniroute"
        return 1
    fi
    echo "  = omniroute answers on :$port again"
    return 0
}

cmd_failover_on() {
    local port host since pid
    port="$(failover_port)"
    host="$(effective_bind)"
    FAILOVER_PORT="$port"
    if [[ -f "$FAILOVER_STATE" ]]; then
        since="$(sed -n 's/^since=//p' "$FAILOVER_STATE" | head -n1)" || true
        echo "  ! failover is already on (since ${since:-unknown}) - nothing was changed; hand back first: $0 failover off"
        return 2
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - would write the gateway client key to $CLIENT_KEY_FILE (0600)"
        echo "  - would run: docker compose -p autoos-ai stop omniroute"
        printf '  - would run: AUTOOS_LITELLM_HOST=%s AUTOOS_LITELLM_PORT=%s AUTOOS_LITELLM_MASTER_KEY_FILE=%s AUTOOS_LITELLM_STATE_DIR=%s %s\n' \
            "$host" "$port" "$CLIENT_KEY_FILE" "$FAILOVER_DIR" "$START_LITELLM"
        echo "  - would wait: http://127.0.0.1:$port/health/liveliness (60 s)"
        echo "  - would write: $FAILOVER_STATE"
        return 0
    fi
    ensure_client_key_file || return 1
    dc stop omniroute || { echo "  ! could not stop the omniroute container"; return 1; }
    # From here the gateway is down: an interrupt (ctrl-C, TERM) must hand the
    # port back instead of leaving a stateless standby on it. Cleared on every
    # return below. The trap's recreate waits 30 s, not the full 180 s: an
    # interrupted command should report, not hold the terminal (review lstby2c).
    trap 'echo "  ! interrupted - handing the port back to the gateway"; failover_stop_litellm; if failover_require_port_free; then failover_bring_back_omniroute 6 || true; fi; trap - INT TERM; exit 130' INT TERM
    if ! AUTOOS_LITELLM_HOST="$host" AUTOOS_LITELLM_PORT="$port" \
        AUTOOS_LITELLM_MASTER_KEY_FILE="$CLIENT_KEY_FILE" AUTOOS_LITELLM_STATE_DIR="$FAILOVER_DIR" \
        "$START_LITELLM"; then
        echo "  ! the standby router did not start - starting the gateway again (the port is never left empty)"
        failover_stop_litellm
        if failover_require_port_free; then
            failover_bring_back_omniroute || true
        fi
        trap - INT TERM
        return 1
    fi
    if wait_for failover_litellm_ok 12; then
        since="$(date +%Y-%m-%dT%H:%M:%S%z)"
        # The listener probe answers with one line per standby pid; the state
        # file records one. Read the first without a `| head` (SIGPIPE under
        # pipefail) and fall back to "unknown" when nothing passed the check.
        pid="$(failover_listener_pid || true)"
        pid="${pid%%$'\n'*}"
        [[ "$pid" =~ ^[0-9]+$ ]] || pid="unknown"
        # No state file = a later `off` could not find the standby: roll back.
        if ! { mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR" && printf 'since=%s\npid=%s\n' "$since" "$pid" >"$FAILOVER_STATE"; }; then
            echo "  ! could not record the failover state - handing the port back to the gateway"
            # A failed printf can leave a truncated file behind, and a later
            # `off` reads that as a live failover with a pid of its own: remove
            # the path whatever survived of the write (review lstby2c).
            rm -f "$FAILOVER_STATE" 2>/dev/null || true
            failover_stop_litellm
            if failover_require_port_free; then
                failover_bring_back_omniroute || true
            fi
            trap - INT TERM
            return 1
        fi
        echo "  + failover on: LiteLLM serves :$port (since $since, litellm pid $pid)"
        trap - INT TERM
        return 0
    fi
    echo "  ! the standby router did not answer /health/liveliness on :$port - starting the gateway again (the port is never left empty)"
    failover_stop_litellm
    if failover_require_port_free; then
        failover_bring_back_omniroute || true
    fi
    trap - INT TERM
    return 1
}

# Did a failover standby ever run here, and does one still hold the gateway
# port? A litellm process STARTED on that port is the proof. The probe must be
# tool-free (failover_argv_standby_pids, not failover_listener_pid): `off` runs
# it before it knows whether anything changed, and a no-op on a gateway-serving
# host must not drive `ss`/`docker`/`curl` to find that out (review lstby2d).
# The standby's own state dir is a hint, never a gate: a starter that failed its
# `mkdir`, or an operator who cleared the config dir by hand, must not hide a
# standby that still owns the port. Deliberately NOT a short-circuit to true -
# a leftover dir with no listener is still a no-op (review lstby2d).
failover_standby_may_be_live() {
    [[ -n "$(failover_argv_standby_pids)" ]]
}

cmd_failover_off() {
    local port
    port="$(failover_port)"
    if [[ ! -f "$FAILOVER_STATE" ]] && ! failover_standby_may_be_live; then
        # No record and no standby started on the port: a true no-op, reached
        # without a single tool call (the probe above only reads /proc).
        echo "  = failover is off (skipped)"
        return 0
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - would stop the standby LiteLLM (the litellm listener on :$port)"
        echo "  - would run: docker compose -p autoos-ai up -d --no-deps omniroute"
        echo "  - would wait: the gateway on :$port (/api/health) with its port published"
        echo "  - would remove: $FAILOVER_STATE"
        return 0
    fi
    failover_stop_litellm
    if ! failover_require_port_free; then
        # A holder that proves itself the gateway (container running, the port
        # published, /api/health 200) is NOT a reason to refuse: the operator
        # already reopened the gateway by hand after a failover died, so clear
        # the stale record and report success. Without this, `off` refuses for
        # ever and `on` keeps answering "already on" (review lstby2d). A holder
        # that is not the gateway keeps today's refusal below.
        if failover_gateway_serves_port; then
            rm -f "$FAILOVER_STATE"
            echo "  + failover is off: omniroute already serves :$port - cleared the stale state"
            return 0
        fi
        if [[ -f "$FAILOVER_STATE" ]]; then
            echo "  - kept $FAILOVER_STATE: failover is still on, the gateway needs attention"
        fi
        return 1
    fi
    if ! failover_bring_back_omniroute; then
        rm -f "$FAILOVER_STATE"
        echo "  ! omniroute did not answer - docker logs autoos-omniroute"
        echo "  - removed $FAILOVER_STATE: failover is off, the gateway needs attention"
        return 1
    fi
    rm -f "$FAILOVER_STATE"
    echo "  + failover off: omniroute serves :$port again"
    return 0
}

cmd_failover_status() {
    local since pid
    if [[ ! -f "$FAILOVER_STATE" ]]; then echo "failover off"; return 0; fi
    since="$(sed -n 's/^since=//p' "$FAILOVER_STATE" | head -n1)" || true
    pid="$(sed -n 's/^pid=//p' "$FAILOVER_STATE" | head -n1)" || true
    echo "failover on since ${since:-unknown} (litellm pid ${pid:-unknown})"
    return 0
}

cmd_failover() {
    local sub="${SERVICES[0]:-}"
    case "$sub" in
        on)     cmd_failover_on ;;
        off)    cmd_failover_off ;;
        status) cmd_failover_status ;;
        "")     echo "  ! failover needs on, off or status"; return 2 ;;
        *)      echo "  ! unknown failover command: $sub (on, off, status)"; return 2 ;;
    esac
}

# ─── verify ─────────────────────────────────────────────────────────────────
# The by-hand checklist after `migrate --yes`, as one command. READ-ONLY: docker
# is only asked `inspect`, `exec <opencode> test -d` and `exec <omniroute>
# qodercli --version` (which leaves qodercli's usual log files in its HOME, the
# qoder-home mount - nothing else), curl only makes GETs and the combo probes
# (POST /v1/chat/completions, max_tokens 256; a 502/503 is retried twice,
# 10 s then 20 s, while a freshly started gateway warms up); nothing is started, stopped,
# written or printed that could be a key. One line per
# check - "  ok    <name>", "  FAIL  <name> - <why>", "  skip  <name> - <why>" -
# then the totals; exit 0 only when nothing FAILed.
#   AUTOOS_OMNIROUTE_KEY         gateway key for the combo probes (never read from
#                                a file; unset -> that check is skipped)
#   AUTOOS_VERIFY_COMBOS         space separated combo names (default below)
#   AUTOOS_VERIFY_RETRY_SLEEP    seconds before the first combo retry (default
#                                10; doubled for the second)
#   AUTOOS_VERIFY_PUBLIC_URLS    space separated public URLs that must redirect
#                                (302, the auth proxy) without credentials
#   AUTOOS_OMNIROUTE_PUBLIC_URL  the gateway's public origin (stack.env): printed as
#                                the app normalizes it, not requested
#   AUTOOS_CODE_DIR              the tree that must be visible in the containers
#                                (the variable compose.yml mounts)
V_OK=0; V_FAIL=0; V_SKIP=0
V_SVCS=(); V_CTRS=()
v_ok()   { V_OK=$((V_OK + 1));     printf '  ok    %s\n' "$1"; }
v_fail() { V_FAIL=$((V_FAIL + 1)); printf '  FAIL  %s - %s\n' "$1" "$2"; }
v_skip() { V_SKIP=$((V_SKIP + 1)); printf '  skip  %s - %s\n' "$1" "$2"; }

# verify_code <curl args...>: the HTTP status ("000": nothing answered).
# -q first, so a ~/.curlrc cannot add a header to a probe that must be keyless.
# stdin reaches curl (the combo probe hands it the key there).
verify_code() {
    local out
    out="$("$CURL" -q -s -m 10 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true)"
    printf '%s' "${out:-000}"
}
# verify_expected <code> <wanted>: the reason of a FAIL.
verify_expected() {
    if [[ "$1" == 000 ]]; then printf 'no answer, expected %s' "$2"; else printf 'HTTP %s, expected %s' "$1" "$2"; fi
}

# compose_services: "<service> <container> <profiles>" per service of
# compose.yml - the container falls back to compose's own default name, the
# profiles are comma separated or "-". Plain awk, like ensure_images: the file
# is kept anchor-free so that no YAML library is needed.
compose_services() {
    awk '
        function emit() {
            if (svc != "") printf "%s %s %s\n", svc, (cname != "" ? cname : "autoos-ai-" svc "-1"), (prof != "" ? prof : "-")
            svc = ""
        }
        function add_profile(v) {
            gsub(/[][" \047]/, "", v)
            if (v != "") prof = (prof == "" ? v : prof "," v)
        }
        /^[^[:space:]#]/ { emit(); in_svc = ($0 ~ /^services:/); next }
        !in_svc { next }
        /^  [A-Za-z0-9_.-]+:[[:space:]]*(#.*)?$/ {
            emit(); svc = $1; sub(/:$/, "", svc); cname = ""; prof = ""; in_prof = 0; next
        }
        /^    container_name:/ {
            v = $0; sub(/^    container_name:[[:space:]]*/, "", v); sub(/[[:space:]]*#.*$/, "", v)
            gsub(/["\047]/, "", v); cname = v; in_prof = 0; next
        }
        /^    profiles:/ {
            v = $0; sub(/^    profiles:[[:space:]]*/, "", v); sub(/[[:space:]]*#.*$/, "", v)
            in_prof = (v == ""); add_profile(v); next
        }
        in_prof && /^[[:space:]]+-[[:space:]]/ {
            v = $0; sub(/^[[:space:]]*-[[:space:]]*/, "", v); sub(/[[:space:]]*#.*$/, "", v); add_profile(v); next
        }
        /^    [A-Za-z_]+:/ { in_prof = 0 }
        END { emit() }
    ' "$HERE/compose.yml"
}

# verify_load_services: V_SVCS/V_CTRS = the services compose would start:
# every one without profiles, and those whose profile COMPOSE_PROFILES names
# (environment first, then stack.env - the two places compose itself reads).
verify_load_services() {
    local active="${COMPOSE_PROFILES:-}" svc ctr prof plist alist p a hit
    [[ -n "$active" ]] || active="$(env_value "$STACK_ENV" COMPOSE_PROFILES)"
    IFS=, read -ra alist <<<"$active"
    V_SVCS=(); V_CTRS=()
    while read -r svc ctr prof; do
        [[ -n "$svc" ]] || continue
        if [[ "$prof" != - ]]; then
            hit=0
            IFS=, read -ra plist <<<"$prof"
            for p in "${plist[@]}"; do
                for a in "${alist[@]}"; do
                    if [[ "$a" == "$p" || "$a" == '*' ]]; then hit=1; fi
                done
            done
            (( hit )) || continue
        fi
        V_SVCS+=("$svc"); V_CTRS+=("$ctr")
    done < <(compose_services)
    return 0
}

# verify_container <service>: its container name, or nothing when compose
# would not start that service.
verify_container() {
    local i
    for i in "${!V_SVCS[@]}"; do
        if [[ "${V_SVCS[$i]}" == "$1" ]]; then printf '%s' "${V_CTRS[$i]}"; return 0; fi
    done
    return 0
}

# The tree compose mounts into opencode and every OpenHands sandbox: the
# variable compose reads (the environment beats --env-file), then stack.env,
# then what init would have written.
verify_code_dir() {
    local d="${AUTOOS_CODE_DIR:-}"
    [[ -n "$d" ]] || d="$(env_value "$STACK_ENV" AUTOOS_CODE_DIR)"
    printf '%s' "${d:-$CODE_DIR}"
}

# 1. Every service compose starts is running and, where docker reports a
# health status, healthy.
verify_containers() {
    local i c state
    if (( ${#V_SVCS[@]} == 0 )); then v_fail "containers" "no service of $HERE/compose.yml is enabled"; return 0; fi
    for i in "${!V_SVCS[@]}"; do
        c="${V_CTRS[$i]}"
        state="$(container_state "$c")"
        case "$state" in
            running|"running (healthy)") v_ok "container $c" ;;
            *) v_fail "container $c" "$state" ;;
        esac
    done
    return 0
}

# 2. The published ports refuse a request without credentials.
verify_keyless_one() {
    local svc="$1" path="$2" port name code
    port="$(service_port "$svc")"
    name="keyless $path refused on :$port"
    if [[ -z "$(verify_container "$svc")" ]]; then v_skip "$name" "the $svc service is not enabled"; return 0; fi
    code="$(verify_code --noproxy '*' "http://127.0.0.1:$port$path")"
    if [[ "$code" == 401 ]]; then v_ok "$name"; else v_fail "$name" "$(verify_expected "$code" 401)"; fi
}

# 3. A one-word request per combo, with the gateway key. The key goes to curl
# on stdin (`-H @-` from a here-string): never on a command line, where `ps`
# shows it, and never in this script's output.
verify_combos() {
    local key="${AUTOOS_OMNIROUTE_KEY:-}" combos c url body code why wait_s attempt
    if [[ -z "$(verify_container omniroute)" ]]; then v_skip "keyed combos" "the omniroute service is not enabled"; return 0; fi
    if [[ -z "$key" ]]; then v_skip "keyed combos" "AUTOOS_OMNIROUTE_KEY is not set"; return 0; fi
    if [[ "$key" == *$'\n'* || "$key" == *$'\r'* ]]; then
        v_fail "keyed combos" "AUTOOS_OMNIROUTE_KEY holds a line break"; return 0
    fi
    read -ra combos <<<"${AUTOOS_VERIFY_COMBOS:-t2-worker-free-only t2-worker-clean}"
    if (( ${#combos[@]} == 0 )); then v_skip "keyed combos" "AUTOOS_VERIFY_COMBOS lists no combo"; return 0; fi
    url="http://127.0.0.1:$(service_port omniroute)/v1/chat/completions"
    for c in "${combos[@]}"; do
        # The name goes into a JSON body: only what a combo name can be.
        if [[ ! "$c" =~ ^[A-Za-z0-9._:/-]+$ ]]; then v_fail "combo (invalid name)" "AUTOOS_VERIFY_COMBOS entries are [A-Za-z0-9._:/-]"; continue; fi
        # max_tokens 256: a reasoning leg spends ~18 tokens thinking, and at 16-20
        # the gateway's quality check answers 502 (L0, live 2026-09-27).
        body="{\"model\":\"$c\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":256}"
        # 502/503 right after `up` is the gateway warming up (both combos answered
        # on re-probe, 2026-09-27): two retries, 10 s then 20 s. Nothing else is retried.
        wait_s="${AUTOOS_VERIFY_RETRY_SLEEP:-10}"
        # Decimal even with a leading zero: bash reads "008" as bad octal and aborts.
        if [[ "$wait_s" =~ ^[0-9]{1,3}$ ]]; then wait_s=$(( 10#$wait_s )); else wait_s=10; fi
        for attempt in 1 2 3; do
            code="$(verify_code -m 60 --noproxy '*' -X POST -H 'Content-Type: application/json' -H @- -d "$body" "$url" \
                <<<"Authorization: Bearer $key")"
            [[ "$code" == 502 || "$code" == 503 ]] && (( attempt < 3 )) || break
            sleep "$wait_s"; wait_s=$(( wait_s * 2 ))
        done
        if [[ "$code" == 200 ]]; then v_ok "combo $c"; continue; fi
        why="$(verify_expected "$code" 200)"
        [[ "$code" == 401 ]] && why="HTTP 401, the gateway did not accept AUTOOS_OMNIROUTE_KEY"
        v_fail "combo $c" "$why"
    done
    return 0
}

# 4. The code tree is where the agents look for it: inside opencode, and in
# the volumes every OpenHands sandbox gets (SANDBOX_VOLUMES: host:container[:mode]).
verify_code_dir_visible() {
    local dir oc oh vols entry found=0 list name
    dir="$(verify_code_dir)"
    oc="$(verify_container opencode)"
    if [[ -z "$oc" ]]; then
        v_skip "code dir visible in opencode" "the opencode service is not enabled"
    else
        name="code dir $dir visible in $oc"
        if "$DOCKER" exec "$oc" test -d "$dir" >/dev/null 2>&1; then v_ok "$name"
        elif container_running "$oc"; then v_fail "$name" "not a directory in the container - AUTOOS_CODE_DIR must be mounted at the same path"
        else v_fail "$name" "the container is not running"; fi
    fi
    oh="$(verify_container openhands)"
    if [[ -z "$oh" ]]; then
        v_skip "code dir in openhands sandbox volumes" "the openhands service is not enabled"
    else
        name="code dir $dir in $oh SANDBOX_VOLUMES"
        vols="$(running_container_env "$oh" SANDBOX_VOLUMES)"
        IFS=, read -ra list <<<"$vols"
        for entry in "${list[@]}"; do
            if [[ "$entry" == "$dir:$dir" || "$entry" == "$dir:$dir:"* ]]; then found=1; fi
        done
        if (( found )); then v_ok "$name"
        elif [[ -z "$vols" ]]; then v_fail "$name" "SANDBOX_VOLUMES is empty or the container does not exist"
        else v_fail "$name" "no $dir:$dir entry in SANDBOX_VOLUMES"; fi
    fi
    return 0
}

# 5. The gateway container carries qodercli: the Qoder PAT login spawns it
# there (omniroute.Dockerfile). Run the way the gateway runs it - same user,
# same HOME - so a missing binary and an unwritable HOME both show up here.
verify_omniroute_qodercli() {
    local c out name="omniroute has qodercli"
    c="$(verify_container omniroute)"
    if [[ -z "$c" ]]; then v_skip "$name" "the omniroute service is not enabled"; return 0; fi
    if ! container_running "$c"; then v_skip "$name" "$c is not running"; return 0; fi
    out="$("$DOCKER" exec "$c" qodercli --version 2>/dev/null || true)"
    out="${out%%$'\n'*}"
    if [[ "$out" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then v_ok "$name"; return 0; fi
    v_fail "$name" "qodercli --version printed no version in $c - the container runs an image without the qodercli layer, or its HOME is not writable (ai-stack.sh up omniroute rebuilds and recreates it)"
}

# 6. What the public sees: the auth proxy redirects, it never serves. No
# credentials are sent, and none that sit in a URL are echoed.
verify_public_urls() {
    local urls u code shown
    read -ra urls <<<"${AUTOOS_VERIFY_PUBLIC_URLS:-}"
    if (( ${#urls[@]} == 0 )); then v_skip "public URLs" "AUTOOS_VERIFY_PUBLIC_URLS is not set"; return 0; fi
    for u in "${urls[@]}"; do
        if [[ ! "$u" =~ ^https?://[^/?#@]+([/?#].*)?$ ]]; then
            v_fail "public URL (rejected)" "not an http(s) URL, or it carries credentials - verify sends none"; continue
        fi
        shown="${u%%[?#]*}"
        code="$(verify_code "$u")"
        if [[ "$code" == 302 ]]; then v_ok "public URL $shown"
        else v_fail "public URL $shown" "$(verify_expected "$code" "302 (the auth proxy's redirect)")"; fi
    done
    return 0
}

# 7. The public origin the gateway was told (AUTOOS_OMNIROUTE_PUBLIC_URL):
# printed the way the app normalizes it - trailing slashes dropped - or skipped
# when unset or empty. Checked, not requested: it may be a plain LAN address,
# and reachability through the proxy is what AUTOOS_VERIFY_PUBLIC_URLS is for.
# FAIL for what the gateway would exit on at startup; the value is never echoed.
verify_omniroute_public_url() {
    local u name="omniroute public URL"
    u="$(omniroute_public_url)"
    if [[ -z "$u" ]]; then v_skip "$name" "AUTOOS_OMNIROUTE_PUBLIC_URL is not set"; return 0; fi
    if ! public_url_valid "$u"; then
        v_fail "$name" "not an http(s)://host[:port][/path] URL without credentials or blanks - the gateway exits at startup on it (value not echoed)"
        return 0
    fi
    v_ok "$name $(public_url_shown "$u")"
}

# 8. configuration/healthcheck.sh is the repo's stack probe, and it is not a
# check verify can run: it appends to logs/healthcheck-<date>.log on every run
# (mkdir + tee), answers with exit 0 whatever it finds, and its --fix mode
# resumes services. Everything of it that concerns this stack (the three ports,
# docker's health verdicts) is checked above.
verify_healthcheck() {
    v_skip "healthcheck" "configuration/healthcheck.sh writes a log file and always exits 0 - not read-only, no verdict"
}

# 9. The effective admission gate and heap the running gateway process sees:
# read PID 1's environment (the entrypoint appends OMNIROUTE_MEMORY_MB to
# NODE_OPTIONS; docker exec shells only see the Dockerfile's default, so
# /proc/1 is the truth). Informational - not a pass/fail check; the image
# defaults (1 heavy in flight, 2000 ms queue) kill parallel agent workers.
verify_omniroute_admission() {
    local c name="omniroute admission" environ heavy queue heap
    c="$(verify_container omniroute)"
    if [[ -z "$c" ]]; then echo "  skip  $name - the omniroute service is not enabled"; return 0; fi
    if ! container_running "$c"; then echo "  skip  $name - $c is not running"; return 0; fi
    environ="$("$DOCKER" exec "$c" sh -c 'tr "\0" "\n" </proc/1/environ' 2>/dev/null || true)"
    if [[ -z "$environ" ]]; then echo "  = $name: could not read /proc/1/environ"; return 0; fi
    heavy="$(printf '%s\n' "$environ" | sed -n 's/^OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=//p' | tail -n1 || true)"
    queue="$(printf '%s\n' "$environ" | sed -n 's/^OMNIROUTE_CHAT_ADMISSION_QUEUE_MS=//p' | tail -n1 || true)"
    heap="$(printf '%s\n' "$environ" | sed -n 's/^NODE_OPTIONS=//p' | tail -n1 \
        | grep -oE -- '--max-old-space-size=[0-9]+' | tail -n1 | cut -d= -f2 || true)"
    heavy="${heavy:-unset (image default 1 / 2000)}"
    queue="${queue:-unset (image default 1 / 2000)}"
    heap="${heap:-unset}"
    echo "  = $name: max_heavy=$heavy, queue_ms=$queue, heap MB=$heap"
}

# 10. The cgroup memory split behind the gateway's pressure guard: the guard
# (upstream thresholds, not configurable in 3.8.50) answers 503 to every chat
# call at >= 92% current/max and only recovers below 75%, and page cache counts
# toward it - while anon (real use) can sit far lower (measured 2026-09-27:
# current 2.62G of max 2.68G, anon 0.83G, file 1.65G from per-call log
# artifacts). Informational - not a pass/fail check; one exec read, like the
# admission gate above. The image needs only cat and sed for it.
verify_omniroute_memory() {
    local c name="omniroute memory" out cur max anon cache
    local cur_mb max_mb anon_mb cache_mb pct
    c="$(verify_container omniroute)"
    if [[ -z "$c" ]]; then echo "  skip  $name - the omniroute service is not enabled"; return 0; fi
    if ! container_running "$c"; then echo "  skip  $name - $c is not running"; return 0; fi
    out="$("$DOCKER" exec "$c" sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max; sed -n "s/^\(anon\|file\) /&/p" /sys/fs/cgroup/memory.stat' 2>/dev/null || true)"
    cur="$(printf '%s\n' "$out" | sed -n '1p')"
    max="$(printf '%s\n' "$out" | sed -n '2p')"
    anon="$(printf '%s\n' "$out" | sed -n 's/^anon //p' | tail -n1)"
    cache="$(printf '%s\n' "$out" | sed -n 's/^file //p' | tail -n1)"
    if [[ ! "$cur" =~ ^[0-9]+$ || -z "$max" || ! "$anon" =~ ^[0-9]+$ || ! "$cache" =~ ^[0-9]+$ ]]; then
        echo "  = $name: could not read the cgroup counters"; return 0
    fi
    cur_mb=$(( (cur + 524288) / 1048576 ))
    anon_mb=$(( (anon + 524288) / 1048576 ))
    cache_mb=$(( (cache + 524288) / 1048576 ))
    if [[ "$max" == max ]]; then
        echo "  = $name: current $cur_mb MB, no limit, anon $anon_mb MB, reclaimable cache $cache_mb MB"
        return 0
    fi
    if [[ ! "$max" =~ ^[0-9]+$ || "$max" -eq 0 ]]; then
        echo "  = $name: could not read the cgroup counters"; return 0
    fi
    max_mb=$(( (max + 524288) / 1048576 ))
    pct=$(( cur * 100 / max ))
    echo "  = $name: current $cur_mb MB of $max_mb MB (${pct}%), anon $anon_mb MB, reclaimable cache $cache_mb MB"
    if (( cur * 100 >= max * 92 )) && (( anon * 100 < max * 75 )); then
        echo "  ! $name: page cache holds the pressure guard at ${pct}% (it answers 503 above 92%); relief: ai-stack.sh restart omniroute"
    fi
}

# The standby router holds the gateway port through the same client key, so
# clients notice nothing but slower/limited models. Informational, never a
# FAIL - the container checks above already judge the stopped gateway.
verify_failover() {
    if [[ -f "$FAILOVER_STATE" ]]; then
        echo "  = failover ON: LiteLLM serves the gateway port"
    fi
    return 0
}

cmd_verify() {
    V_OK=0; V_FAIL=0; V_SKIP=0
    verify_load_services
    verify_containers
    verify_keyless_one omniroute /v1/models
    verify_keyless_one opencode /api/session
    verify_combos
    verify_code_dir_visible
    verify_omniroute_qodercli
    verify_public_urls
    verify_omniroute_public_url
    verify_healthcheck
    verify_omniroute_admission
    verify_omniroute_memory
    verify_failover
    printf 'verify: %d ok, %d failed, %d skipped\n' "$V_OK" "$V_FAIL" "$V_SKIP"
    [[ $V_FAIL -eq 0 ]]
}

case "$CMD" in
    init)      cmd_init ;;
    up)        cmd_up ;;
    down)      cmd_down ;;
    restart)   cmd_restart ;;
    status)    cmd_status ;;
    is-active) cmd_is_active ;;
    migrate)   cmd_migrate ;;
    rollback)  cmd_rollback ;;
    failover)  cmd_failover ;;
    verify)    cmd_verify ;;
    *) echo "Unknown command: $CMD (init, up, down, restart, status, failover, is-active, migrate, rollback, verify)"; exit 2 ;;
esac
