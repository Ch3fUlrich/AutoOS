#!/usr/bin/env bash
# Stateful stand-ins for everything configuration/docker/ai-stack/ai-stack.sh
# drives, so the suite can run migrate/rollback/up for real without a docker
# daemon, a user manager or a network (AGENTS.md §5: never the live machine).
#
#   aistack_fake.sh <state-dir> <tool> [args...]
#
# tests/run-tests.sh puts one tiny wrapper per tool on PATH that execs this.
# All state is files in <state-dir>:
#   active-<unit>        native systemd --user unit (or system unit) is active
#   unit-<unit>          the unit is registered (systemctl cat succeeds)
#   run-<container>      container is running
#   compose-<container>  container exists and belongs to compose project autoos-ai
#   exists-<container>   container exists outside compose (start-stack.sh docker run)
#   container-exists     legacy: `docker inspect autoos-omniroute` succeeds
#   image-exists         every `image inspect` succeeds
#   <name>-label         the org.autoos.<name>.source label of the built image
#                        autoos/<name>:<tag> (opencode-label, omniroute-label)
#   noimage-<name>       `image inspect` of autoos/<name>:<tag> fails (never built)
#   fail-build-<name>    `docker build` of autoos/<name>:<tag> fails
#   fail-up-<service>    `compose up` of that service fails
#   unhealthy-<service>  the container runs but its HTTP probe fails (docker
#                        inspect then reports "running (unhealthy)")
#   env-<container>      what `docker inspect -f '{{range .Config.Env}}...'` prints
#                        (one NAME=value per line; nothing when the file is absent)
#   nocode-<container>   `docker exec <container> test -d <dir>` fails: the code
#                        tree is not mounted in that container
#   noqoder-<container>  `docker exec <container> qodercli --version` fails: the
#                        image has no qodercli layer
#   qoder-version-<container>  what that command prints instead of 1.1.63
#   omni-key-fails       the omniroute CLI cannot create the manage key
#   fail-register        register-autostart.sh fails (register.log also
#                        records marker=yes|no: ai-stack.sh's marker at the call)
#   compose-bind.log     AUTOOS_STACK_BIND as every `docker compose` call saw it
#   qoder-home-at-up.log "present"/"absent": whether <state-dir>/data/qoder-home
#                        (the sandbox's data dir) existed at each `compose up`
# Every call is appended to <tool>.log and, as "<tool>: <args>", to events.log
# (the cross-tool order). Arguments only - the environment is never logged;
# saw-manage-key only records WHICH tool had OMNIROUTE_API_KEY set.
set -uo pipefail

S="$1"; TOOL="$2"; shift 2
printf '%s\n' "$*" >>"$S/$TOOL.log"
printf '%s: %s\n' "$TOOL" "$*" >>"$S/events.log"
if [[ -n "${OMNIROUTE_API_KEY:-}" ]]; then printf '%s\n' "$TOOL" >>"$S/saw-manage-key"; fi
# The publish address compose would interpolate (shell env beats --env-file).
if [[ "$TOOL" == docker && "${1:-}" == compose ]]; then
    printf 'bind=%s\n' "${AUTOOS_STACK_BIND-<unset>}" >>"$S/compose-bind.log"
fi

container_of() {
    case "$1" in
        omniroute) echo autoos-omniroute ;;
        opencode)  echo autoos-opencode ;;
        openhands) echo openhands-app ;;
    esac
}

service_of() {
    case "$1" in
        autoos-omniroute) echo omniroute ;;
        autoos-opencode)  echo opencode ;;
        openhands-app)    echo openhands ;;
    esac
}

# image_name autoos/omniroute:3.8.50-autoos1 -> omniroute (the file-name stem
# of that image's state); any other reference keeps its last path element.
image_name() {
    local n="${1##*/}"
    printf '%s' "${n%%[:@]*}"
}

fake_docker() {
    local fmt="" c svc sub
    case "$1" in
        info|network|pull) return 0 ;;
        exec)
            # exec <container> qodercli --version: the gateway's CLI check.
            if [[ "${3:-}" == qodercli ]]; then
                [[ -e "$S/run-$2" ]] || return 1
                if [[ -e "$S/noqoder-$2" ]]; then
                    echo 'OCI runtime exec failed: exec: "qodercli": executable file not found in $PATH: unknown' >&2
                    return 126
                fi
                if [[ -s "$S/qoder-version-$2" ]]; then cat "$S/qoder-version-$2"; else echo 1.1.63; fi
                return 0
            fi
            # exec <container> test -d <dir>: the code tree check. True while
            # the container runs and mounts the tree.
            [[ -e "$S/run-$2" && ! -e "$S/nocode-$2" ]]
            return ;;
        build)
            local prev="" tag="" label=""
            for a in "$@"; do
                if [[ "$prev" == --label ]]; then label="${a#*=}"; fi
                if [[ "$prev" == -t ]]; then tag="$a"; fi
                prev="$a"
            done
            svc="$(image_name "$tag")"
            [[ -e "$S/fail-build-$svc" ]] && return 1
            printf '%s\n' "$label" >"$S/$svc-label"
            : >"$S/image-exists"
            return 0 ;;
        rm)
            c="${*: -1}"
            rm -f "$S/run-$c" "$S/exists-$c" "$S/compose-$c"
            return 0 ;;
        image)
            [[ "${3:-}" == -f ]] && fmt="$4"
            [[ -e "$S/image-exists" ]] || return 1
            svc="$(image_name "${*: -1}")"
            [[ -e "$S/noimage-$svc" ]] && return 1
            if [[ "$fmt" == *Labels* ]]; then
                if [[ -s "$S/$svc-label" ]]; then cat "$S/$svc-label"; else echo '<no value>'; fi
            fi
            return 0 ;;
        inspect)
            if [[ "${2:-}" == -f ]]; then fmt="$3"; c="$4"; else c="$2"; fi
            if [[ ! -e "$S/run-$c" && ! -e "$S/compose-$c" && ! -e "$S/exists-$c" ]]; then
                [[ -e "$S/container-exists" && "$c" == autoos-omniroute && -z "$fmt" ]] && return 0
                return 1
            fi
            case "$fmt" in
                *State.Running*) if [[ -e "$S/run-$c" ]]; then echo true; else echo false; fi ;;
                *compose.project*) if [[ -e "$S/compose-$c" ]]; then echo autoos-ai; else echo '<no value>'; fi ;;
                *State.Status*)
                    if [[ ! -e "$S/run-$c" ]]; then echo exited
                    elif [[ "$fmt" != *State.Health* ]]; then echo running
                    elif [[ -e "$S/unhealthy-$(service_of "$c")" ]]; then echo 'running (unhealthy)'
                    else echo 'running (healthy)'; fi ;;
                *Config.Env*) [[ -s "$S/env-$c" ]] && cat "$S/env-$c" ;;
                *Networks*) echo '{}' ;;
            esac
            return 0 ;;
        compose)
            shift
            while (( $# )); do
                case "$1" in
                    --project-name|--env-file|-f|-p) shift 2 ;;
                    *) break ;;
                esac
            done
            sub="${1:-}"; shift || true
            case "$sub" in
                version) echo "Docker Compose version v2.99.0" ;;
                up)
                    if [[ -d "$S/data/qoder-home" ]]; then echo present; else echo absent; fi >>"$S/qoder-home-at-up.log"
                    local svcs=()
                    for a in "$@"; do [[ "$a" == -* ]] || svcs+=("$a"); done
                    (( ${#svcs[@]} )) || svcs=(omniroute opencode openhands)
                    for svc in "${svcs[@]}"; do
                        [[ -e "$S/fail-up-$svc" ]] && return 1
                        c="$(container_of "$svc")"
                        : >"$S/run-$c"; : >"$S/compose-$c"
                    done ;;
                stop)
                    local svcs=("$@")
                    (( ${#svcs[@]} )) || svcs=(omniroute opencode openhands)
                    for svc in "${svcs[@]}"; do rm -f "$S/run-$(container_of "$svc")"; done ;;
                rm)
                    for a in "$@"; do
                        [[ "$a" == -* ]] && continue
                        c="$(container_of "$a")"
                        rm -f "$S/run-$c" "$S/compose-$c"
                    done ;;
                down)
                    for c in autoos-omniroute autoos-opencode openhands-app; do
                        [[ -e "$S/compose-$c" ]] && rm -f "$S/run-$c" "$S/compose-$c"
                    done ;;
            esac
            return 0 ;;
    esac
    return 0
}

fake_systemctl() {
    local args=() a unit
    for a in "$@"; do [[ "$a" == --user || "$a" == --quiet ]] || args+=("$a"); done
    unit="${args[1]:-}"; unit="${unit%.service}"
    case "${args[0]:-}" in
        is-active) [[ -e "$S/active-$unit" ]] ;;
        cat)       [[ -e "$S/unit-$unit" ]] ;;
        stop)      rm -f "$S/active-$unit" ;;
        start)     [[ -e "$S/unit-$unit" ]] && : >"$S/active-$unit" ;;
        *)         return 0 ;;
    esac
}

# ss -ltnH "sport = :PORT": a native unit or a running container holds it.
fake_ss() {
    local port="${*: -1}"; port="${port##*:}"
    local unit c
    case "$port" in
        20128) unit=autoos-omniroute; c=autoos-omniroute ;;
        4096)  unit=autoos-opencode;  c=autoos-opencode ;;
        3000)  unit=none;             c=openhands-app ;;
        *) return 0 ;;
    esac
    if [[ -e "$S/active-$unit" || -e "$S/run-$c" ]]; then
        echo "LISTEN 0 511 0.0.0.0:$port 0.0.0.0:*"
    fi
    return 0
}

# curl: the HTTP code a probe would see, from the unit/container state.
fake_curl() {
    local url="" a want_code=0 fail_flag=0 code=000
    for a in "$@"; do
        case "$a" in
            http://*) url="$a" ;;
            -w) want_code=1 ;;
            -sf|-f|-fsS) fail_flag=1 ;;
        esac
    done
    up() { [[ -e "$S/active-$1" ]] || { [[ -e "$S/run-$2" ]] && [[ ! -e "$S/unhealthy-$3" ]]; }; }
    case "$url" in
        *:20128/api/health*) up autoos-omniroute autoos-omniroute omniroute && code=200 ;;
        *:20128/v1/*)        up autoos-omniroute autoos-omniroute omniroute && code=401 ;;
        *:4096/api/*)        up autoos-opencode autoos-opencode opencode && code=401 ;;
        *:4096/*)            up autoos-opencode autoos-opencode opencode && code=200 ;;
        *:3000/*)            up none openhands-app openhands && code=200 ;;
    esac
    (( want_code )) && printf '%s' "$code"
    if [[ "$code" == 000 ]]; then return 7; fi
    if (( fail_flag )) && [[ "$code" != 2* ]]; then return 22; fi
    return 0
}

fake_omniroute() {
    if [[ "$*" == *post-api-keys* ]]; then
        [[ -e "$S/omni-key-fails" ]] && return 1
        printf 'Loaded env from ~/.omniroute/.env\n{"id":"k1","key":"sk-manage-test-key"}\n'
    fi
    return 0
}

# register-autostart.sh: --unregister --only a,b removes those units;
# --only a,b registers and starts them.
fake_register() {
    # Whether ai-stack.sh's ownership marker existed at this call (the sandbox
    # keeps its config in <state-dir>/cfg).
    if [[ -f "$S/cfg/stack.active" ]]; then echo "marker=yes" >>"$S/register.log"; else echo "marker=no" >>"$S/register.log"; fi
    [[ -e "$S/fail-register" ]] && return 1
    local unreg=0 only="" u
    while (( $# )); do
        case "$1" in
            --unregister) unreg=1 ;;
            --only) shift; only="${1:-}" ;;
        esac
        shift
    done
    IFS=',' read -ra units <<<"$only"
    for u in "${units[@]}"; do
        if (( unreg )); then rm -f "$S/unit-$u" "$S/active-$u"
        else : >"$S/unit-$u"; : >"$S/active-$u"; fi
    done
    return 0
}

# start-stack.sh openhands: the compose service while a migration runs, the
# docker run container otherwise.
fake_start_stack() {
    printf 'migrating=%s\n' "${AUTOOS_AI_STACK_MIGRATING:-0}" >>"$S/start-stack.log"
    [[ "${1:-}" == openhands ]] || return 0
    if [[ "${AUTOOS_AI_STACK_MIGRATING:-0}" == 1 ]]; then
        [[ -e "$S/fail-up-openhands" ]] && return 1
        : >"$S/run-openhands-app"; : >"$S/compose-openhands-app"
    else
        : >"$S/run-openhands-app"; : >"$S/exists-openhands-app"
    fi
    return 0
}

case "$TOOL" in
    docker)      fake_docker "$@" ;;
    systemctl)   fake_systemctl "$@" ;;
    ss)          fake_ss "$@" ;;
    curl)        fake_curl "$@" ;;
    omniroute)   fake_omniroute "$@" ;;
    register)    fake_register "$@" ;;
    start-stack) fake_start_stack "$@" ;;
    sleep)       exit 0 ;;
    *) echo "aistack_fake.sh: unknown tool $TOOL" >&2; exit 2 ;;
esac
