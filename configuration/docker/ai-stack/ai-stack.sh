#!/usr/bin/env bash
# Manage the AutoOS AI stack in Docker (compose.yml next to this script):
# OmniRoute gateway :20128, opencode serve :4096, OpenHands :3000.
#
#   ai-stack.sh init              env files + opencode config (idempotent)
#   ai-stack.sh up [service...]   build/pull what is missing, start, wait healthy
#   ai-stack.sh down [service...] stop the containers (kept; `up` resumes)
#   ai-stack.sh status            containers + a probe per port
#   ai-stack.sh is-active         exit 0 when the stack owns the services
#   ai-stack.sh migrate [--yes]   native units -> containers (plan without --yes)
#   ai-stack.sh rollback [--yes]  containers -> native units (plan without --yes)
#   --dry-run                     with any command: say what would happen
#
# Files (never tracked, all mode 600 under a 700 directory):
#   ~/.config/autoos/ai-stack/stack.env      compose settings (uid, paths, RAM)
#   ~/.config/autoos/ai-stack/opencode.env   OPENCODE_PASSWORD + {env:...} keys
#   ~/.config/autoos/ai-stack/openhands.env  LLM_API_KEY (+ remote-browser pair)
#   ~/.config/autoos/ai-stack/manage.key     manage-scoped gateway key, host only
#   ~/.local/share/autoos/ai-stack/          omniroute/ data, opencode-home/
# init only ADDS missing keys (backup first); a value you edit stays yours.
# Design, RAM budget, rollback: docs/web-services.md (Server profile section).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
CONFIG_DIR="${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}"
DATA_DIR="${AUTOOS_AI_STACK_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/autoos/ai-stack}"
# Tests replace both with logging stubs: nothing in the suite may reach the
# live docker daemon or user manager (AGENTS.md §5).
DOCKER="${AUTOOS_DOCKER:-docker}"
SYSTEMCTL="${AUTOOS_SYSTEMCTL:-systemctl}"
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
REGISTER="$REPO/configuration/autostart/register-autostart.sh"
OPENCODE_IMAGE="autoos/opencode:2.0.16-autoos1"

DRY=0
YES=0
CMD=""
SERVICES=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes)     YES=1 ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "Unknown option: $arg"; exit 2 ;;
        *) if [[ -z "$CMD" ]]; then CMD="$arg"; else SERVICES+=("$arg"); fi ;;
    esac
done
CMD="${CMD:-status}"
[[ $DRY -eq 1 ]] && echo "This is a dry run - nothing is written, started or stopped."

# shellcheck source=../../env-file.sh
. "$REPO/configuration/env-file.sh"

# ─── Small helpers ──────────────────────────────────────────────────────────
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
        backup="$file.autoos-backup-$(date +%Y%m%d-%H%M%S)"
        cp -p "$file" "$backup"
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

omniroute_client_key() {
    if [[ -n "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then printf '%s' "$AUTOOS_OMNIROUTE_KEY"; return; fi
    [[ -f "$KEYS_FILE" ]] || return 0
    local key
    key="$(sed -n 's/^omniroute[[:space:]]*:[[:space:]]*//p' "$KEYS_FILE" | head -n1 | tr -d '\r' \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
    [[ "$key" == REPLACE_WITH_* ]] || printf '%s' "$key"
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

dc() {
    "$DOCKER" compose --project-name autoos-ai --env-file "$STACK_ENV" -f "$HERE/compose.yml" "$@"
}

container_running() {
    [[ "$("$DOCKER" inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

port_listening() {
    [[ -n "$(ss -ltnH "sport = :$1" 2>/dev/null)" ]]
}

http_code() {
    curl -s -m 5 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true
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

# ─── init ───────────────────────────────────────────────────────────────────
cmd_init() {
    echo "Docker AI stack: config $CONFIG_DIR, data $DATA_DIR, code $CODE_DIR"
    if [[ ! -d "$CODE_DIR" && $DRY -eq 0 ]]; then
        echo "  ! code dir $CODE_DIR does not exist - set AUTOOS_CODE_DIR"; return 1
    fi
    local d
    for d in "$DATA_DIR/omniroute" "$DATA_DIR/opencode-home/.config/opencode" "$OH_DIR"; do
        if [[ -d "$d" ]]; then continue; fi
        if [[ $DRY -eq 1 ]]; then echo "  - would create $d"; else mkdir -p "$d"; echo "  + created $d"; fi
    done
    [[ $DRY -eq 1 ]] || chmod 700 "$DATA_DIR/omniroute"

    ensure_env_file "$STACK_ENV" \
        AUTOOS_UID "$(id -u)" AUTOOS_GID "$(id -g)" \
        AUTOOS_CODE_DIR "$CODE_DIR" AUTOOS_STACK_DATA "$DATA_DIR" \
        AUTOOS_STACK_CONFIG "$CONFIG_DIR" AUTOOS_OPENHANDS_DIR "$OH_DIR" \
        AUTOOS_STACK_BIND 0.0.0.0 \
        OMNIROUTE_MEMORY_MB 1536 OMNIROUTE_MEM_LIMIT 2560m \
        OPENCODE_MEM_LIMIT 1536m AUTOOS_OPENHANDS_MEMORY 2g

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
    [[ -s "$PW_FILE" ]] && password="$(tr -d '\r\n' <"$PW_FILE")"
    if [[ -z "$password" ]] && ! file_has_key "$CONFIG_DIR/opencode.env" OPENCODE_PASSWORD; then
        if [[ $DRY -eq 1 ]]; then password="generated"
        else password="$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 32)"; fi
    fi
    pairs=(OPENCODE_PASSWORD "$password" AUTOOS_OMNIROUTE_KEY "$client_key")
    pairs+=(OMNIGRAPH_TOKEN "${OMNIGRAPH_TOKEN:-$(env_value "$OMNIGRAPH_ENV" OMNIGRAPH_TOKEN)}")
    for n in "${names[@]}"; do
        [[ "$n" == AUTOOS_OMNIROUTE_KEY ]] && continue
        pairs+=("$n" "$(secret_for "$n")")
    done
    ensure_env_file "$CONFIG_DIR/opencode.env" "${pairs[@]}"

    local url_pattern="${AUTOOS_OPENHANDS_SANDBOX_URL:-}" web_host="${AUTOOS_OPENHANDS_WEB_HOST:-}"
    [[ -z "$url_pattern" ]] && url_pattern="$(running_container_env openhands-app OH_SANDBOX_CONTAINER_URL_PATTERN)"
    [[ -z "$web_host" ]] && web_host="$(running_container_env openhands-app WEB_HOST)"
    ensure_env_file "$CONFIG_DIR/openhands.env" \
        LLM_API_KEY "$client_key" \
        OH_SANDBOX_CONTAINER_URL_PATTERN "$url_pattern" \
        WEB_HOST "$web_host"
    [[ -z "$client_key" ]] && echo "  ! no omniroute client key (configuration/api-keys.yml) - opencode and OpenHands cannot use the gateway yet"
    return 0
}

# ─── up / down / status ─────────────────────────────────────────────────────
ensure_images() {
    local svc img
    for svc in "$@"; do
        if [[ "$svc" == opencode ]]; then
            if "$DOCKER" image inspect "$OPENCODE_IMAGE" >/dev/null 2>&1; then continue; fi
            if [[ $DRY -eq 1 ]]; then echo "  - would build $OPENCODE_IMAGE (opencode.Dockerfile)"; continue; fi
            echo "  + building $OPENCODE_IMAGE"
            dc build opencode
        else
            img="$(sed -n "/^  $svc:/,/^  [a-z]/s/^    image: //p" "$HERE/compose.yml" | head -n1)"
            if "$DOCKER" image inspect "$img" >/dev/null 2>&1; then continue; fi
            if [[ $DRY -eq 1 ]]; then echo "  - would pull $img"; continue; fi
            echo "  + pulling $img"
            dc pull "$svc"
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

cmd_up() {
    local svcs=("${SERVICES[@]}") start=() svc c port
    (( ${#svcs[@]} )) || svcs=(omniroute opencode openhands)
    if [[ ! -f "$STACK_ENV" ]]; then
        if [[ $DRY -eq 1 ]]; then echo "  - would run init first"; else cmd_init; fi
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
    ensure_images "${start[@]}"
    if [[ $DRY -eq 1 ]]; then
        echo "  - would run: docker compose -p autoos-ai up -d ${start[*]}"
        attach_shared_mcp
        return 0
    fi
    dc up -d "${start[@]}"
    attach_shared_mcp
    for svc in "${start[@]}"; do
        case "$svc" in
            omniroute) wait_for gateway_ok && echo "  + omniroute answers on :20128" || echo "  ! omniroute did not answer - docker logs autoos-omniroute" ;;
            opencode)  wait_for opencode_ok && echo "  + opencode answers on :4096" || echo "  ! opencode did not answer - docker logs autoos-opencode" ;;
            openhands) wait_for openhands_ok && echo "  + openhands answers on :3000" || echo "  ! openhands did not answer - docker logs openhands-app" ;;
        esac
    done
}

is_compose_container() {
    [[ "$("$DOCKER" inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$1" 2>/dev/null || true)" == autoos-ai ]]
}

cmd_down() {
    if [[ $DRY -eq 1 ]]; then echo "  - would run: docker compose -p autoos-ai stop ${SERVICES[*]}"; return 0; fi
    dc stop "${SERVICES[@]}"
}

cmd_status() {
    local svc c
    for svc in omniroute opencode openhands; do
        c="$(service_container "$svc")"
        printf '  %-10s %-18s %s\n' "$svc" "$c" \
            "$("$DOCKER" inspect -f '{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}' "$c" 2>/dev/null || echo 'no container')"
    done
    echo "  gateway :20128 /api/health -> $(http_code http://127.0.0.1:20128/api/health); keyless /v1/models -> $(http_code http://127.0.0.1:20128/v1/models) (401 expected)"
    echo "  opencode :4096 / -> $(http_code http://127.0.0.1:4096/); /api/session without password -> $(http_code http://127.0.0.1:4096/api/session) (401 expected)"
    echo "  openhands :3000 / -> $(http_code http://127.0.0.1:3000/)"
}

cmd_is_active() {
    [[ -f "$STACK_ENV" ]] && "$DOCKER" inspect autoos-omniroute >/dev/null 2>&1
}

# ─── manage key ─────────────────────────────────────────────────────────────
# The host CLI's machine token is accepted from loopback peers only, and a
# published container port sees the docker gateway as its peer: after the
# move, apply.sh manages the gateway with a manage-scoped key instead. It is
# created while the NATIVE gateway still answers (the CLI token works there).
ensure_manage_key() {
    if [[ -s "$MANAGE_KEY_FILE" ]]; then echo "  = manage key $MANAGE_KEY_FILE present (skipped)"; return 0; fi
    if [[ $DRY -eq 1 ]]; then echo "  - would create a manage-scoped gateway key into $MANAGE_KEY_FILE (0600)"; return 0; fi
    command -v omniroute >/dev/null || { echo "  ! omniroute CLI missing - create a key with scope 'manage' in the dashboard, save it to $MANAGE_KEY_FILE"; return 0; }
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
        echo "  ! could not create a manage key - create one with scope 'manage' in the dashboard, save it to $MANAGE_KEY_FILE"
        return 0
    fi
    mkdir -p "$CONFIG_DIR"; chmod 700 "$CONFIG_DIR"
    ( umask 077; printf '%s\n' "$key" >"$MANAGE_KEY_FILE" )
    chmod 600 "$MANAGE_KEY_FILE"
    echo "  + created the manage-scoped key autoos-manage ($MANAGE_KEY_FILE)"
}

# ─── migrate ────────────────────────────────────────────────────────────────
migrate_plan() {
    cat <<EOF
Migration plan (native units -> docker AI stack):
  1. init: env files under $CONFIG_DIR, the opencode config, images (build/pull)
  2. create a manage-scoped gateway key for the host CLI ($MANAGE_KEY_FILE)
     while the native gateway is still up
  3. back up $OMNI_HOME to $DATA_DIR/backups/omniroute-<timestamp>.tar.gz (0600)
  4. stop the autoos-omniroute unit (the gateway is down from here)
  5. copy $OMNI_HOME (storage.sqlite + .env with STORAGE_ENCRYPTION_KEY) into
     $DATA_DIR/omniroute - the original stays as it is
  6. start the omniroute container and wait until it answers /api/health and
     refuses a keyless /v1 call with 401. If not: container stopped, unit
     started again.
  7. unregister autoos-omniroute (register-autostart.sh --unregister --only)
  8. same for autoos-opencode: stop the unit, start the opencode container,
     wait until it answers, then unregister the unit (on failure: unit back)
  9. replace the openhands container from start-stack.sh with the compose one
     (state stays in $OH_DIR; running sandboxes keep running)
Run it with:  $0 migrate --yes
Undo it with: $0 rollback --yes
EOF
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

restore_native() {
    local unit="$1" svc="$2"
    echo "  ! $svc container did not come up - handing :$(service_port "$svc") back to $unit"
    dc stop "$svc" >/dev/null 2>&1 || true
    if unit_known "$unit"; then "$SYSTEMCTL" --user start "$unit.service" || true; fi
}

cmd_migrate() {
    if [[ $YES -eq 0 || $DRY -eq 1 ]]; then
        migrate_plan
        [[ -d "$OMNI_HOME" ]] || echo "(no $OMNI_HOME here: nothing to carry over, 'up' starts a fresh gateway)"
        return 0
    fi
    command -v "$DOCKER" >/dev/null 2>&1 || { echo "docker is not installed"; return 1; }
    "$DOCKER" compose version >/dev/null 2>&1 || { echo "docker compose v2 is required"; return 1; }
    cmd_init
    ensure_images omniroute opencode openhands

    # Gateway.
    if container_running autoos-omniroute; then
        echo "  = omniroute already runs in docker (skipped)"
    else
        ensure_manage_key
        if [[ -d "$OMNI_HOME" ]]; then
            mkdir -p "$DATA_DIR/backups"; chmod 700 "$DATA_DIR/backups"
            local ts backup
            ts="$(date +%Y%m%d-%H%M%S)"
            backup="$DATA_DIR/backups/omniroute-$ts.tar.gz"
            ( umask 077; tar -C "$(dirname "$OMNI_HOME")" -czf "$backup" "$(basename "$OMNI_HOME")" )
            echo "  + backed up $OMNI_HOME to $backup"
            stop_native autoos-omniroute 20128 || return 1
            if [[ -e "$DATA_DIR/omniroute/storage.sqlite" ]]; then
                mv "$DATA_DIR/omniroute" "$DATA_DIR/omniroute.autoos-backup-$ts"
                mkdir -p "$DATA_DIR/omniroute"; chmod 700 "$DATA_DIR/omniroute"
                echo "  - an earlier copy moved aside to $DATA_DIR/omniroute.autoos-backup-$ts"
            fi
            cp -a "$OMNI_HOME/." "$DATA_DIR/omniroute/"
            # Pid files of the native server would read as "already running".
            rm -f "$DATA_DIR/omniroute/server/.pid" "$DATA_DIR/omniroute/supervisor/.pid"
            echo "  + copied $OMNI_HOME into $DATA_DIR/omniroute"
        fi
        dc up -d omniroute
        if wait_for gateway_ok && gateway_keyed; then
            echo "  + omniroute container answers on :20128 and refuses keyless /v1 (401)"
        else
            restore_native autoos-omniroute omniroute
            return 1
        fi
    fi
    if [[ -f "$REGISTER" ]] && unit_known autoos-omniroute; then
        bash "$REGISTER" --unregister --only autoos-omniroute
    fi

    # opencode serve.
    if container_running autoos-opencode; then
        echo "  = opencode already runs in docker (skipped)"
    else
        stop_native autoos-opencode 4096 || return 1
        dc up -d opencode
        attach_shared_mcp
        if wait_for opencode_ok; then
            echo "  + opencode container answers on :4096"
        else
            restore_native autoos-opencode opencode
            return 1
        fi
    fi
    if [[ -f "$REGISTER" ]] && unit_known autoos-opencode; then
        bash "$REGISTER" --unregister --only autoos-opencode
    fi

    # OpenHands: the start-stack.sh container becomes the compose one.
    if "$DOCKER" inspect openhands-app >/dev/null 2>&1 && ! is_compose_container openhands-app; then
        "$DOCKER" rm -f openhands-app >/dev/null
        echo "  - removed the start-stack.sh openhands-app container (state stays in $OH_DIR)"
    fi
    # start-stack.sh repairs the settings, syncs + pushes the tier profiles
    # and, with the stack active, starts the compose service.
    bash "$REPO/configuration/start-stack.sh" openhands || echo "  ! openhands did not come up - docker logs openhands-app"
    cmd_status
}

# ─── rollback ───────────────────────────────────────────────────────────────
rollback_plan() {
    cat <<EOF
Rollback plan (docker AI stack -> native units):
  1. remove the compose containers (docker compose down; the bind-mounted
     data in $DATA_DIR stays)
  2. back up $OMNI_HOME, then copy $DATA_DIR/omniroute back into it (the
     container's state is the newest: keys, combos, usage)
  3. register-autostart.sh --only autoos-omniroute,autoos-opencode (the units
     come back and start)
  4. start-stack.sh openhands (the docker run container, as before)
Run it with: $0 rollback --yes
EOF
}

cmd_rollback() {
    if [[ $YES -eq 0 || $DRY -eq 1 ]]; then rollback_plan; return 0; fi
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    dc down
    echo "  - compose containers removed"
    if [[ -e "$DATA_DIR/omniroute/storage.sqlite" ]]; then
        if [[ -d "$OMNI_HOME" ]]; then
            mkdir -p "$DATA_DIR/backups"
            ( umask 077; tar -C "$(dirname "$OMNI_HOME")" -czf "$DATA_DIR/backups/omniroute-native-$ts.tar.gz" "$(basename "$OMNI_HOME")" )
            echo "  + backed up $OMNI_HOME to $DATA_DIR/backups/omniroute-native-$ts.tar.gz"
        fi
        mkdir -p "$OMNI_HOME"
        cp -a "$DATA_DIR/omniroute/." "$OMNI_HOME/"
        echo "  + copied the container state back into $OMNI_HOME"
    fi
    bash "$REGISTER" --only autoos-omniroute,autoos-opencode
    bash "$REPO/configuration/start-stack.sh" openhands || true
}

case "$CMD" in
    init)      cmd_init ;;
    up)        cmd_up ;;
    down)      cmd_down ;;
    status)    cmd_status ;;
    is-active) cmd_is_active ;;
    migrate)   cmd_migrate ;;
    rollback)  cmd_rollback ;;
    *) echo "Unknown command: $CMD (init, up, down, status, is-active, migrate, rollback)"; exit 2 ;;
esac
