# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Writable gateway home ─────────────────────────────────────────────────
describe "gateway home"

if it "aistack: fork-image override is optional, config-local and last"; then
    ok=1
    for shape in absent file override directory; do
        d="$(_aistack_sandbox)"
        cfg="$d/cfg"
        [[ "$shape" != override ]] || cfg="$d/other-config"
        mkdir -p "$cfg" "$d/home/.zcode/server"
        printf "AUTOOS_STACK_BIND='0.0.0.0'\nAUTOOS_OPENCODE_PUBLISH_IP='192.0.2.10'\n" >"$cfg/stack.env"
        : >"$d/home/.zcode/server/zcode-server.cjs"
        : >"$d/image-exists"
        fork="$cfg/compose.fork-image.yml"
        : >"$d/active-coding-agents-fw"
        case "$shape" in
            file|override) : >"$fork" ;;
            directory) mkdir "$fork" ;;
        esac
        out="$(_aistack "$d" "AUTOOS_AI_STACK_CONFIG=$cfg" up omniroute)" || { ok=0; echo "$out" >&2; }
        line="$(_aistack_compose_up "$d")"
        expected="compose --project-name autoos-ai --env-file $cfg/stack.env -f $AISTACK/compose.yml -f $AISTACK/compose.loopback.yml -f $AISTACK/compose.zcode.yml"
        case "$shape" in file|override) expected+=" -f $fork" ;; esac
        [[ "$line" == "$expected up -d --no-deps omniroute" ]] || { ok=0; echo "$shape: [$line]" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "fork-image compose argument order or config selection differs"; fi
fi

if it "aistack: init announces and creates a private gateway home"; then
    d="$(_aistack_sandbox)"
    ok=1
    mkdir -p "$d/data/omniroute"
    printf 'db\n' >"$d/data/omniroute/storage.sqlite"
    printf 'k=v\n' >"$d/data/omniroute/.env"
    out="$(_aistack "$d" --dry-run init)"
    [[ "$out" == *"would create $d/data/omniroute-home"* ]] || { ok=0; echo "the dry run did not announce the gateway home: $out" >&2; }
    _aistack "$d" init >/dev/null
    [[ -d "$d/data/omniroute-home" ]] || { ok=0; echo "init did not create the gateway home" >&2; }
    [[ "$(stat -c %a "$d/data/omniroute-home")" == 700 ]] || { ok=0; echo "init gateway home not 700" >&2; }
    [[ -O "$d/data/omniroute-home" ]] || { ok=0; echo "init gateway home not owned by invoking user" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "init gateway home announcement, mode or owner"; fi
fi

if it "aistack: up creates a private gateway home on a host init ran on long ago"; then
    ok=1
    # stack.env exists, so up does not run init again.
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg" "$d/data/omniroute" "$d/data/opencode-home"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/image-exists"
    _aistack "$d" up omniroute >/dev/null || { ok=0; echo "up failed" >&2; }
    [[ -d "$d/data/omniroute-home" ]] || { ok=0; echo "up did not create the gateway home" >&2; }
    [[ "$(stat -c %a "$d/data/omniroute-home")" == 700 ]] || { ok=0; echo "up gateway home not 700" >&2; }
    [[ -O "$d/data/omniroute-home" ]] || { ok=0; echo "up gateway home not owned by invoking user" >&2; }
    rm -rf "$d"
    # compose mounts AUTOOS_STACK_DATA from stack.env: the check follows it there.
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg" "$d/elsewhere/omniroute"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\nAUTOOS_STACK_DATA='%s'\n" "$d/elsewhere" >"$d/cfg/stack.env"
    : >"$d/image-exists"
    _aistack "$d" up omniroute >/dev/null || { ok=0; echo "up (stack.env data dir) failed" >&2; }
    [[ -d "$d/elsewhere/omniroute" ]] || { ok=0; echo "the gateway data dir is not under the AUTOOS_STACK_DATA of stack.env" >&2; }
    [[ -d "$d/elsewhere/omniroute-home" ]] || { ok=0; echo "up did not create the gateway home under stack.env data dir" >&2; }
    [[ "$(stat -c %a "$d/elsewhere/omniroute-home")" == 700 ]] || { ok=0; echo "up stack.env gateway home not 700" >&2; }
    [[ -O "$d/elsewhere/omniroute-home" ]] || { ok=0; echo "up stack.env gateway home not owned by invoking user" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "up gateway home creation, mode or owner"; fi
fi
