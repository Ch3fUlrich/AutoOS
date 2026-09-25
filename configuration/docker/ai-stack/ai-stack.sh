#!/usr/bin/env bash
# Manage the AutoOS AI stack in Docker (compose.yml next to this script):
# OmniRoute gateway :20128, opencode serve :4096, OpenHands :3000.
#
#   ai-stack.sh init              env files + opencode config (idempotent)
#   ai-stack.sh up [service...]   build/pull what is missing, start, wait healthy
#   ai-stack.sh down [service...] stop the containers (kept; `up` resumes)
#   ai-stack.sh status            containers + a probe per port
#   ai-stack.sh is-active         exit 0 when the stack owns the services (marker below)
#   ai-stack.sh migrate [--yes]   native units -> containers (plan without --yes)
#   ai-stack.sh rollback [--yes]  containers -> native units (plan without --yes)
#   --dry-run                     with any command: say what would happen
#
# Files (never tracked, all mode 600 under a 700 directory):
#   ~/.config/autoos/ai-stack/stack.env      compose settings (uid, paths, RAM)
#   ~/.config/autoos/ai-stack/opencode.env   OPENCODE_PASSWORD + {env:...} keys
#   ~/.config/autoos/ai-stack/openhands.env  LLM_API_KEY (+ remote-browser pair)
#   ~/.config/autoos/ai-stack/manage.key     manage-scoped gateway key, host only
#   ~/.config/autoos/ai-stack/stack.active   ownership marker: written only when a
#                                            migrate completed (or a fresh host's
#                                            first `up`), removed by rollback
#   ~/.local/share/autoos/ai-stack/          omniroute/ data, opencode-home/
# init only ADDS missing keys (backup first); a value you edit stays yours.
# up/migrate refuse a LAN publish address unless coding-agents-fw.service
# runs or stack.env says AUTOOS_STACK_ALLOW_LAN=1.
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
MARKER="$CONFIG_DIR/stack.active"
# Tests point both at logging stand-ins (tests/helpers/aistack_fake.sh).
REGISTER="${AUTOOS_REGISTER_AUTOSTART:-$REPO/configuration/autostart/register-autostart.sh}"
START_STACK="${AUTOOS_START_STACK:-$REPO/configuration/start-stack.sh}"
OPENCODE_IMAGE="autoos/opencode:2.0.16-autoos1"
OPENCODE_LABEL="org.autoos.opencode.source"
# Filled by migrate; read by migrate_abort / restore_native.
MOVED=()
UNITS_BEFORE=()
OH_REPLACED=0

DRY=0
YES=0
CMD=""
SERVICES=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes)     YES=1 ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
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

bind_guard() {
    local bind allow
    bind="$(env_value "$STACK_ENV" AUTOOS_STACK_BIND)"
    bind="${bind:-0.0.0.0}"
    is_loopback "$bind" && return 0
    allow="$(env_value "$STACK_ENV" AUTOOS_STACK_ALLOW_LAN)"
    if [[ "$allow" == 1 ]]; then
        echo "  = publishing on $bind: AUTOOS_STACK_ALLOW_LAN=1 in $STACK_ENV (firewall not checked)"
        return 0
    fi
    if "$SYSTEMCTL" is-active coding-agents-fw.service >/dev/null 2>&1; then
        echo "  = publishing on $bind: coding-agents-fw.service is active (LAN limited to the proxy)"
        return 0
    fi
    echo "  ! refusing to publish :20128, :4096 and :3000 on $bind: coding-agents-fw.service is not"
    echo "    active, so nothing limits LAN clients to the reverse proxy (OpenHands has no login and"
    echo "    holds the docker socket). Start that firewall unit, or set AUTOOS_STACK_BIND=127.0.0.1"
    echo "    (host only), or accept the exposure with AUTOOS_STACK_ALLOW_LAN=1 - both in $STACK_ENV."
    if [[ $DRY -eq 1 ]]; then echo "    (dry run: a real run stops here)"; return 0; fi
    return 1
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
    # The gateway DB, opencode sessions and the MCP caches: the operator's only.
    [[ $DRY -eq 1 ]] || chmod 700 "$DATA_DIR" "$DATA_DIR/omniroute" "$DATA_DIR/opencode-home"

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
# The opencode layer's identity: opencode.Dockerfile plus the digest of the
# base image it builds FROM. Built images carry it as a label; a mismatch
# rebuilds, so an edited Dockerfile or a bumped base never keeps serving the
# old layer under the unchanged local tag.
opencode_source_hash() {
    local f="$HERE/opencode.Dockerfile" base
    base="$(sed -n 's/^FROM[[:space:]][[:space:]]*[^[:space:]]*@\(sha256:[0-9a-f]\{64\}\).*/\1/p' "$f" | head -n1)"
    { cat "$f"; printf 'base=%s\n' "$base"; } | sha256sum | cut -d' ' -f1
}

ensure_images() {
    local svc img want have what
    for svc in "$@"; do
        if [[ "$svc" == opencode ]]; then
            want="$(opencode_source_hash)"
            if "$DOCKER" image inspect "$OPENCODE_IMAGE" >/dev/null 2>&1; then
                have="$("$DOCKER" image inspect -f "{{index .Config.Labels \"$OPENCODE_LABEL\"}}" "$OPENCODE_IMAGE" 2>/dev/null || true)"
                [[ "$have" == "$want" ]] && continue
                what="rebuild $OPENCODE_IMAGE (opencode.Dockerfile or its base image changed)"
            else
                what="build $OPENCODE_IMAGE (opencode.Dockerfile)"
            fi
            if [[ $DRY -eq 1 ]]; then echo "  - would $what"; continue; fi
            echo "  + $what"
            "$DOCKER" build --label "$OPENCODE_LABEL=$want" -t "$OPENCODE_IMAGE" \
                -f "$HERE/opencode.Dockerfile" "$HERE" || { echo "  ! building $OPENCODE_IMAGE failed"; return 1; }
        else
            img="$(sed -n "/^  $svc:/,/^  [a-z]/s/^    image: //p" "$HERE/compose.yml" | head -n1)"
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
    local svcs=("${SERVICES[@]}") start=() fresh=() svc c port unit
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
        start+=("$svc"); fresh+=("$svc")
    done
    (( ${#start[@]} )) || return 0
    # Only a container that is (re)created publishes anew: the guard does not
    # stand in the way of resuming what already runs.
    if (( ${#fresh[@]} )); then bind_guard || return 1; fi
    ensure_images "${start[@]}" || return 1
    if [[ $DRY -eq 1 ]]; then
        echo "  - would run: docker compose -p autoos-ai up -d --no-deps ${start[*]}"
        attach_shared_mcp
        return 0
    fi
    dc up -d --no-deps "${start[@]}" || { echo "  ! docker compose up failed - see: $0 status"; return 1; }
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

cmd_status() {
    local svc c
    if stack_owned; then echo "  owner: the docker stack ($MARKER)"
    else echo "  owner: native units (no $MARKER)"; fi
    for svc in omniroute opencode openhands; do
        c="$(service_container "$svc")"
        printf '  %-10s %-18s %s\n' "$svc" "$c" \
            "$("$DOCKER" inspect -f '{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}' "$c" 2>/dev/null || echo 'no container')"
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
    local dir="$1" archive="$2"
    if ! mkdir -p "$(dirname "$archive")" || ! chmod 700 "$(dirname "$archive")"; then return 1; fi
    if ! ( umask 077; tar -C "$(dirname "$dir")" -czf "$archive" "$(basename "$dir")" ); then
        rm -f "$archive"
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
# aside to <dest>.autoos-backup-<ts>, never deleted.
replace_dir_with_copy() {
    local src="$1" dest="$2" ts="$3" stage aside
    aside="$dest.autoos-backup-$ts"
    [[ -e "$aside" ]] && aside="$aside-$$"
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
  9. only when all three answered: unregister autoos-omniroute and
     autoos-opencode (register-autostart.sh --unregister --only), then write
     $MARKER - from then on is-active is true
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
    if moved openhands; then dc rm -s -f openhands >/dev/null 2>&1 || true; fi
    if moved omniroute && restore_native autoos-omniroute omniroute; then
        wait_for gateway_ok || echo "  ! the native gateway does not answer yet - journalctl --user -u autoos-omniroute"
    fi
    if moved opencode; then restore_native autoos-opencode opencode || true; fi
    if [[ $OH_REPLACED -eq 1 ]]; then
        echo "  - recreating the start-stack.sh openhands-app container"
        bash "$START_STACK" openhands || echo "  ! openhands did not come back - run: configuration/start-stack.sh openhands"
    fi
    echo "  = the docker stack does not own the services ($MARKER not written); backups stay in $DATA_DIR/backups"
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
    bind_guard || return 1
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
        if ! dc up -d --no-deps omniroute || ! wait_for gateway_ok || ! gateway_keyed; then
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
        if ! dc up -d --no-deps opencode || ! wait_for opencode_ok; then
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

    # 4. All three answered: only now the native units go, then the marker.
    if (( ${#UNITS_BEFORE[@]} )); then
        if ! bash "$REGISTER" --unregister --only "$(IFS=,; printf '%s' "${UNITS_BEFORE[*]}")"; then
            echo "  ! could not unregister ${UNITS_BEFORE[*]}"
            migrate_abort
            return 1
        fi
    fi
    write_marker migrate || { echo "  ! could not write $MARKER - is-active stays false; fix and re-run migrate"; return 1; }
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
        dc up -d || true
        return 1
    fi
    echo "  - compose containers removed"
    if [[ $restore -eq 1 ]]; then
        if ! replace_dir_with_copy "$DATA_DIR/omniroute" "$OMNI_HOME" "$ts"; then
            # Nothing native is registered yet: bring the containers back rather
            # than leave the host without a gateway.
            echo "  ! could not restore $OMNI_HOME - starting the docker stack again"
            dc up -d || true
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
