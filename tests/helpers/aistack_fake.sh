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
#   proc1-environ-<container>  what `docker exec <container> sh -c 'tr ... </proc/1/environ'`
#                              prints: one NAME=value per line (the admission
#                              gate verify reads OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT,
#                              OMNIROUTE_CHAT_ADMISSION_QUEUE_MS and NODE_OPTIONS)
#   cgroup-<container>         what `docker exec <container> sh -c '...memory.current...'`
#                              prints: memory.current, memory.max ("max" when
#                              unlimited), then the anon + file lines
#                              of memory.stat, one per line (the memory-split
#                              verify reads the cgroup pressure numbers)
#   id-<container>             what `docker inspect -f '{{.Id}}' <container>`
#                              prints (the memory-split verify names the
#                              cgroup scope in its reclaim hint)
#   noports-<container>  `docker inspect -f '{{json .NetworkSettings.Ports}}'`
#                              prints {} - the container publishes nothing
#   nullports-<container>      the same prints {"20128/tcp":null}: the key is
#                              there with NO host binding (docker's own "not
#                              published" shape, as broken as {} for a client)
#   omni-key-fails       the omniroute CLI cannot create the manage key
#   fail-register        register-autostart.sh fails (register.log also
#                        records marker=yes|no: ai-stack.sh's marker at the call)
#   compose-bind.log     AUTOOS_STACK_BIND as every `docker compose` call saw it
#   qoder-home-at-up.log "present"/"absent": whether <state-dir>/data/qoder-home
#                        (the sandbox's data dir) existed at each `compose up`
#   slow-sleep           the fake `sleep` really sleeps 0.5 s (a test that must
#                        signal the script mid-wait gets a window); default: no-op
#   failover-liveliness-fail  `start-litellm` spawns no standby: the
#                        /health/liveliness probe never answers (a standby
#                        that starts but never turns live)
#   fail-start-litellm   `start-litellm` exits 1 without spawning anything
#                        (a starter that fails outright)
#   ss-hold-<port>       extra `ss` output for that port, printed verbatim
#                        after the container/standby lines: a test-planted
#                        holder (a named foreign program), or a port that stays
#                        busy after the standby dies
#   standby-ignore-term  the spawned standby traps SIGTERM and ignores it, so
#                        only the KILL after the caller's grace period stops it
# The sandbox keeps its ai-stack config in <state-dir>/cfg. Like the real
# configuration/litellm/start-litellm.sh, the fake starter writes NO pid file:
# it spawns a real process whose argv is the proxy's own
# (`litellm --config config.yaml --host <h> --port <p>`, see
# fake_start_litellm) and records "<pid> <port>" per spawn in
# <state-dir>/standbys (plus the newest pid in <state-dir>/standby-pid) - never
# under cfg/, because the script under test must find the standby the live way,
# with `ss -ltnpH "sport = :20128"` plus a /proc/<pid>/cmdline check, exactly
# like the real starter's listener_pid. The port is recorded so a standby
# started on :4000 (the always-on fallback unit) is visible on :4000 only and
# can never be mistaken for the gateway-port listener.
# fake_ss reports a standby - with a users:(("litellm",pid=N,fd=3)) suffix, so
# the holder is nameable - only on its own port and only while it is alive;
# fake_curl answers http://127.0.0.1:20128/health/liveliness with 200 only while
# a 20128 standby is alive (and the fail marker above is absent). Tests kill the
# leftovers through <state-dir>/standbys (see _aistack_kill_standby in
# tests/linux/34-ai-services.sh).
# `start-litellm` (AUTOOS_START_LITELLM) records the env NAMES
# (AUTOOS_LITELLM_HOST/PORT/MASTER_KEY_FILE/STATE_DIR) in
# start-litellm-env.log, plus master-key-file=ok|missing (a readability check
# on the pointed-to file, never its content); argv lands in
# start-litellm.log through the generic logger below. `compose start <svc>`
# marks the service running.
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
            # exec <container> sh -c 'tr ... </proc/1/environ': the admission
            # gate check reads PID 1's environment. Content from a state file
            # so a test can stub any combination of vars.
            if [[ "${3:-}" == sh && "${4:-}" == -c && "${5:-}" == *"/proc/1/environ"* ]]; then
                [[ -e "$S/run-$2" ]] || return 1
                if [[ -s "$S/proc1-environ-$2" ]]; then cat "$S/proc1-environ-$2"; fi
                return 0
            fi
            # exec <container> sh -c '...memory.current...': the memory-split
            # check reads the cgroup counters. Content from a state file so a
            # test can stub any split of anon vs reclaimable cache.
            if [[ "${3:-}" == sh && "${4:-}" == -c && "${5:-}" == *"memory.current"* ]]; then
                [[ -e "$S/run-$2" ]] || return 1
                if [[ -s "$S/cgroup-$2" ]]; then cat "$S/cgroup-$2"; fi
                return 0
            fi
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
                *Ports*)
                    if [[ -e "$S/nullports-$c" ]]; then
                        echo '{"20128/tcp":null}'
                    elif [[ -e "$S/run-$c" && ! -e "$S/noports-$c" ]]; then
                        echo '{"20128/tcp":[{"HostIp":"0.0.0.0","HostPort":"20128"}]}'
                    else
                        echo '{}'
                    fi ;;
                *'{{.Id}}'*) [[ -s "$S/id-$c" ]] && cat "$S/id-$c" || printf 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789' ;;
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
                start)
                    local a c
                    for a in "$@"; do
                        [[ "$a" == -* ]] && continue
                        c="$(container_of "$a")"
                        [[ -n "$c" ]] && : >"$S/run-$c"
                    done ;;
                restart)
                    for a in "$@"; do
                        [[ "$a" == -* ]] && continue
                        c="$(container_of "$a")"
                        : >"$S/run-$c"; : >"$S/compose-$c"
                    done ;;
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

# ss -ltn[H] "sport = :PORT": a native unit, a running container, a fake
# standby started on THAT port, or a test-planted holder owns it. The standby
# line carries its pid (users:(...pid=N...), like the real ss -p) so the script
# under test can discover it; it is printed only while that process is alive.
fake_ss() {
    local port="${*: -1}"; port="${port##*:}"
    local unit c
    case "$port" in
        20128) unit=autoos-omniroute; c=autoos-omniroute ;;
        4096)  unit=autoos-opencode;  c=autoos-opencode ;;
        3000)  unit=none;             c=openhands-app ;;
        *)     unit=none;             c=none ;;
    esac
    if [[ "$unit" != none && -e "$S/active-$unit" ]] || [[ "$c" != none && -e "$S/run-$c" ]]; then
        echo "LISTEN 0 511 0.0.0.0:$port 0.0.0.0:*"
    fi
    standby_ss_lines "$port"
    if [[ -s "$S/ss-hold-$port" ]]; then
        cat "$S/ss-hold-$port"
    fi
    return 0
}

# curl: the HTTP code a probe would see, from the unit/container state.
fake_curl() {
    local url="" a want_code=0 fail_flag=0 code=000 is_post=0 data=""
    local header_files=() hf
    # Use while loop for proper argument parsing with shifts
    while (( $# )); do
        a="$1"; shift
        case "$a" in
            http://*|https://*) url="$a" ;;
            -w) want_code=1 ;;
            -sf|-f|-fsS) fail_flag=1 ;;
            -X) [[ "${1:-}" == POST ]] && is_post=1; shift ;;
            -H) header_files+=("${1:-}"); shift ;;
            -d) data="${1:-}"; shift ;;
            --max-redirs) shift ;;
            --noproxy) shift ;;
            -m) shift ;;
            -q) ;;
            -s) ;;
            -o) shift ;;
        esac
    done
    # If this is a POST to the edge webhook URL (from AISTACK_EDGE_WEBHOOK_URL),
    # record the args and return the code from AISTACK_FAKE_WEBHOOK_CODE.
    if (( is_post )) && [[ -n "${AISTACK_EDGE_WEBHOOK_URL:-}" && "$url" == "${AISTACK_EDGE_WEBHOOK_URL}" ]]; then
        # Record the call for test inspection
        printf 'url=%s\n' "$url" >>"$S/edge-webhook-call.log"
        # Find the header file (starts with '@')
        hf=""
        for f in "${header_files[@]}"; do
            [[ "$f" == @* ]] && { hf="${f#@}"; break; }
        done
        printf 'header_file=%s\n' "$hf" >>"$S/edge-webhook-call.log"
        if [[ -n "$hf" && -f "$hf" ]]; then
            cat "$hf" >>"$S/edge-webhook-call.log"
        fi
        printf 'data=%s\n' "$data" >>"$S/edge-webhook-call.log"
        # Return the code from the env var, or 200 if not set
        code="${AISTACK_FAKE_WEBHOOK_CODE:-200}"
        (( want_code )) && printf '%s' "$code"
        if [[ "$code" == 000 ]]; then return 7; fi
        if (( fail_flag )) && [[ "$code" != 2* ]]; then return 22; fi
        return 0
    fi
    up() { [[ -e "$S/active-$1" ]] || { [[ -e "$S/run-$2" ]] && [[ ! -e "$S/unhealthy-$3" ]]; }; }
    case "$url" in
        *:20128/health/liveliness*)
            if [[ ! -e "$S/failover-liveliness-fail" ]] && standby_alive 20128; then code=200; fi ;;
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

# start-litellm.sh stand-in for the failover standby (AUTOOS_START_LITELLM).
# Records the interface, never the secrets: the env NAMES (one of them points
# at a key file) plus master-key-file=ok|missing, argv through the generic
# logger above. Values - the key itself above all - are never logged.
# Like the real starter it writes NO pid file: it spawns a real listener and
# records it in <state-dir>/standbys as "<pid> <port>" (plus <state-dir>/
# standby-pid for the newest), which is what fake_ss and fake_curl read.
# With failover-liveliness-fail nothing is spawned (a standby that never turns
# live); with fail-start-litellm the starter itself fails (rc 1); with
# standby-ignore-term the standby shrugs off SIGTERM, so only the KILL after
# the 10 s grace stops it.
#
# The argv mirrors the real proxy's exactly:
#   litellm --config config.yaml --host <h> --port <p>
# `sleep` renamed with `exec -a` cannot take those arguments (it exits on an
# unknown option), and a shebang script always lands the *script path* in
# argv[1] - which is the documented venv-python shape the caller accepts by
# name. So the standby is one executable file named `litellm`, generated on
# first use. It reads nothing but its own timeout: it blocks on fd 9, a fifo
# opened read-write by its own parent, so it never sees EOF from a dead writer.
# stdout/stderr stay redirected and stdin is /dev/null - without that, every
# $(...) capture in ai-stack.sh would block for the standby's whole lifetime.
fake_start_litellm() {
    local v pid ignore_term=""
    local host="${AUTOOS_LITELLM_HOST:-127.0.0.1}" port="${AUTOOS_LITELLM_PORT:-4000}"
    for v in AUTOOS_LITELLM_HOST AUTOOS_LITELLM_PORT AUTOOS_LITELLM_MASTER_KEY_FILE AUTOOS_LITELLM_STATE_DIR; do
        if [[ -n "${!v:-}" ]]; then printf 'env:%s\n' "$v" >>"$S/start-litellm-env.log"; fi
    done
    if [[ -n "${AUTOOS_LITELLM_MASTER_KEY_FILE:-}" && -s "$AUTOOS_LITELLM_MASTER_KEY_FILE" ]]; then
        printf 'master-key-file=ok\n' >>"$S/start-litellm-env.log"
    else
        printf 'master-key-file=missing\n' >>"$S/start-litellm-env.log"
    fi
    # A standby that never turns live: nothing is spawned, so the liveliness
    # probe (fake_curl) never answers and the caller must hand the port back.
    if [[ -e "$S/failover-liveliness-fail" ]]; then return 0; fi
    if [[ -e "$S/fail-start-litellm" ]]; then return 1; fi
    [[ -e "$S/standby-ignore-term" ]] && ignore_term=1
    if [[ ! -x "$S/standby-bin/litellm" ]]; then
        mkdir -p "$S/standby-bin" || return 1
        cat >"$S/standby-bin/litellm" <<'FAKE_STANDBY'
#!/usr/bin/env bash
[[ -n "${AISTACK_STANDBY_IGNORE_TERM:-}" ]] && trap '' TERM
read -r -u 9 -t 300 _hold
FAKE_STANDBY
        chmod +x "$S/standby-bin/litellm" || return 1
    fi
    [[ -p "$S/standby-hold" ]] || mkfifo "$S/standby-hold" 2>/dev/null || true
    AISTACK_STANDBY_IGNORE_TERM="$ignore_term" \
        "$S/standby-bin/litellm" --config config.yaml --host "$host" --port "$port" \
        9<>"$S/standby-hold" </dev/null >/dev/null 2>&1 &
    pid="$!"
    printf '%s\n' "$pid" >"$S/standby-pid"
    printf '%s %s\n' "$pid" "$port" >>"$S/standbys"
    return 0
}

# Is <pid> a process that is still RUNNING? A zombie answers `kill -0` but has
# released every socket it held, so it must not count as the holder of a port:
# without the state check an exited-but-unreaped standby would keep showing up
# in fake_ss and fake_curl for as long as its reaper takes. Without /proc
# (macOS) kill -0 is all there is.
proc_running() {
    local p="$1" st
    [[ "$p" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$p" 2>/dev/null || return 1
    [[ -r "/proc/$p/stat" ]] || return 0
    st="$(sed -n 's/^.*) \([A-Za-z]\).*/\1/p' "/proc/$p/stat" 2>/dev/null)" || return 0
    [[ "$st" != Z ]]
}

# Is a fake standby of $1 alive? Reads <state-dir>/standbys ("<pid> <port>"
# per spawn); false when the file is absent or every listed process is gone.
standby_alive() {
    local want="$1" pid port
    [[ -s "$S/standbys" ]] || return 1
    while read -r pid port; do
        [[ "$port" == "$want" ]] || continue
        if proc_running "$pid"; then return 0; fi
    done <"$S/standbys"
    return 1
}

# The `ss` line for every live fake standby holding port $1, in ss -p's shape
# (users:(("litellm",pid=N,fd=3))) so the holder is nameable. A standby is
# reported only on the port it was started with: the always-on :4000 proxy
# never shows up as a listener on the gateway port.
standby_ss_lines() {
    local want="$1" pid port
    [[ -s "$S/standbys" ]] || return 0
    while read -r pid port; do
        [[ "$port" == "$want" ]] || continue
        if proc_running "$pid"; then
            echo "LISTEN 0 511 0.0.0.0:$want 0.0.0.0:* users:((\"litellm\",pid=$pid,fd=3))"
        fi
    done <"$S/standbys"
    return 0
}

case "$TOOL" in
    docker)      fake_docker "$@" ;;
    start-litellm) fake_start_litellm "$@" ;;
    systemctl)   fake_systemctl "$@" ;;
    ss)          fake_ss "$@" ;;
    curl)        fake_curl "$@" ;;
    omniroute)   fake_omniroute "$@" ;;
    register)    fake_register "$@" ;;
    start-stack) fake_start_stack "$@" ;;
    sleep)       if [[ -e "$S/slow-sleep" ]]; then exec /bin/sleep 0.5; fi; exit 0 ;;
    *) echo "aistack_fake.sh: unknown tool $TOOL" >&2; exit 2 ;;
esac
