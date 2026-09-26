# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── AI services (reboot-safe units, lane A) ────────────────────────────────
# Every case here asserts on a dry run or a pure decision: nothing is started,
# stopped, registered or written outside a mktemp directory.
describe "AI services"

# A fake litellm dir: config.yaml plus a .env that tries shell injection.
_svc_litellm_fixture() {
    local d
    d="$(mktemp -d)"
    : >"$d/config.yaml"
    cat >"$d/.env" <<'ENV'
# comment line
GROQ_API_KEY=gsk_fake_value_1
MISTRAL_API_KEY="quoted-fake-2"
META_API_KEY=REPLACE_WITH_META_KEY
EVIL_KEY=$(touch INJECTED)
EMPTY_KEY=
not a pair
ENV
    printf '%s' "$d"
}

# Review finding 2026-09-25: without /proc (macOS) the stale-key check can
# never run, and the old message blamed "another user". Say what is true.
if it "svc: start-litellm.sh says stale-key restart is Linux-only where /proc is missing"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" AUTOOS_PROC_ROOT="$d/no-proc" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"; rc=$?
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -eq 0 && "$out" == *"Linux-only"* && "$out" != *"another user"* ]]; then pass
    else fail "rc=$rc: $out"; fi
fi

# Found in review 2026-09-25: the restart path killed whatever same-user
# process held the port, litellm or not (AGENTS.md rule 3). It must refuse.
if it "svc: start-litellm.sh never kills a program on its port that is not litellm"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" bash "$ROOT/configuration/litellm/start-litellm.sh" 2>&1)"; rc=$?
    alive=0; kill -0 "$srv" 2>/dev/null && alive=1
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -ne 0 && $alive -eq 1 && "$out" == *"not litellm"* ]]; then pass
    else fail "rc=$rc alive=$alive: $out"; fi
fi

# Re-review 2026-09-25: "litellm" anywhere in the command line is not proof;
# only the program name (argv[0] or the script in argv[1]) counts.
if it "svc: start-litellm.sh leaves a program alone that merely mentions litellm in its arguments"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    mkdir -p "$d/my-litellm-docs"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" "$d/my-litellm-docs" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" bash "$ROOT/configuration/litellm/start-litellm.sh" 2>&1)"; rc=$?
    alive=0; kill -0 "$srv" 2>/dev/null && alive=1
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -ne 0 && $alive -eq 1 && "$out" == *"not litellm"* ]]; then pass
    else fail "rc=$rc alive=$alive: $out"; fi
fi

if it "svc: start-litellm.sh loads .env literally, never evaluates it"; then
    d="$(_svc_litellm_fixture)"
    out="$(cd "$d" && AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    ok=1
    [[ -e "$d/INJECTED" ]] && { ok=0; echo "the .env was evaluated" >&2; }
    [[ "$out" == *"GROQ_API_KEY"* && "$out" == *"MISTRAL_API_KEY"* && "$out" == *"EVIL_KEY"* ]] \
        || { ok=0; echo "keys not reported: $out" >&2; }
    [[ "$out" == *"META_API_KEY"* || "$out" == *"EMPTY_KEY"* ]] && { ok=0; echo "placeholder/empty loaded" >&2; }
    [[ "$out" == *"fake_value"* || "$out" == *"quoted-fake"* ]] && { ok=0; echo "a value was printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "start-litellm.sh .env parsing is unsafe"; fi
fi

if it "svc: start-litellm.sh binds loopback with PYTHONUTF8 and starts nothing in a dry run"; then
    d="$(_svc_litellm_fixture)"
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    rm -rf "$d"
    ok=1
    [[ "$out" == *"PYTHONUTF8=1 litellm --config config.yaml --host 127.0.0.1 --port 1"* ]] \
        || { ok=0; echo "planned command wrong: $out" >&2; }
    [[ "$out" == *"would start"* ]] || { ok=0; echo "dry run did not announce" >&2; }
    if (( ok )); then pass; else fail "start-litellm.sh plan is wrong"; fi
fi

if it "svc: start-litellm.sh restarts a proxy with stale keys and no-ops a current one"; then
    d="$(_svc_litellm_fixture)"
    env_now="$(mktemp)"; env_old="$(mktemp)"
    printf 'PATH=/usr/bin\0GROQ_API_KEY=gsk_fake_value_1\0MISTRAL_API_KEY=quoted-fake-2\0EVIL_KEY=$(touch INJECTED)\0PYTHONUTF8=1\0' >"$env_now"
    printf 'PATH=/usr/bin\0GROQ_API_KEY=gsk_old_value\0PYTHONUTF8=1\0' >"$env_old"
    cur="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 AUTOOS_FAKE_LITELLM_ENVIRON="$env_now" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    old="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 AUTOOS_FAKE_LITELLM_ENVIRON="$env_old" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    rm -rf "$d" "$env_now" "$env_old"
    ok=1
    [[ "$cur" == *"already up with the current keys"* ]] || { ok=0; echo "current: $cur" >&2; }
    [[ "$old" == *"would restart"* && "$old" == *"GROQ_API_KEY"* && "$old" == *"MISTRAL_API_KEY"* ]] \
        || { ok=0; echo "stale: $old" >&2; }
    [[ "$old" == *"gsk_old"* || "$old" == *"fake_value"* ]] && { ok=0; echo "a value was printed" >&2; }
    if (( ok )); then pass; else fail "stale-key convergence is wrong"; fi
fi

if it "svc: the stack launcher starts litellm through start-litellm.sh"; then
    ok=1
    grep -q 'start-litellm.sh' configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "starter not used" >&2; }
    grep -qE '^[[:space:]]*\. \./\.env|set -a' configuration/autostart/Start-AutoOSStack.sh \
        && { ok=0; echo "inline .env sourcing is still there" >&2; }
    [[ -x configuration/litellm/start-litellm.sh ]] || { ok=0; echo "starter not executable" >&2; }
    if (( ok )); then pass; else fail "launcher still sources .env inline"; fi
fi

# The client key cannot PATCH /api/resilience (403 "Invalid management
# token", measured 2026-09-24); the CLI sends the machine loopback token.
if it "svc: apply sets resilience through the omniroute CLI, not curl + client key"; then
    ok=1
    for f in configuration/omniroute/apply.sh configuration/omniroute/apply.ps1; do
        grep -q 'patch-api-resilience' "$f" || { ok=0; echo "no CLI patch: $f" >&2; }
        grep -q 'get-api-resilience' "$f" || { ok=0; echo "no CLI read: $f" >&2; }
        grep -qE 'X PATCH|Method Patch' "$f" && { ok=0; echo "still PATCHes over HTTP: $f" >&2; }
    done
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/dev/null \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    [[ "$out" == *"would set requestQueue.maxWaitMs = 180000 (omniroute api system patch-api-resilience)"* ]] \
        || { ok=0; echo "dry run: $out" >&2; }
    if (( ok )); then pass; else fail "resilience still goes through the client key"; fi
fi

# The dry run must not read the live gateway's provider list: with a gateway
# up, every provider reads "already registered" and the placeholder test
# above became machine-dependent.
if it "svc: apply --dry-run against a down gateway plans from the key file alone"; then
    keys="$(mktemp)"
    printf 'groq: REPLACE_WITH_GROQ_KEY\nmistral: not-a-real-key-123\n' >"$keys"
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$keys" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    rm -f "$keys"
    ok=1
    [[ "$out" == *"groq: no key in api-keys.yml, skipped"* ]] || { ok=0; echo "groq: $out" >&2; }
    [[ "$out" == *"mistral: would register"* ]] || { ok=0; echo "mistral: $out" >&2; }
    [[ "$out" == *"already registered"* ]] && { ok=0; echo "read the live gateway" >&2; }
    if (( ok )); then pass; else fail "apply dry run depends on the live gateway"; fi
fi

# combos.json "retired" is the one list of ids a rename or removal left in the
# gateway store (9 orphans were deleted by hand on 2026-09-25); apply prunes
# those and nothing else. The sandbox: a stand-in omniroute first on PATH that
# answers `combo list` from list.txt (rendered like the real CLI: ANSI icon,
# padded name, [strategy], status) and logs every other call to calls.log,
# plus a loopback stand-in gateway serving a static /api/health. Nothing here
# reaches the live gateway or its store.
_prune_sandbox() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/gw/api"
    printf 'ok\n' >"$d/gw/api/health"
    printf '# no keys: every provider is skipped\n' >"$d/keys.yml"
    : >"$d/calls.log"
    cat >"$d/bin/omniroute" <<'SH'
#!/usr/bin/env bash
d="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-} ${2:-}" == "combo list" ]]; then
    printf '%s\n' "$*" >>"$d/listed"
    cat "$d/list.txt"
    exit 0
fi
printf '%s\n' "$*" >>"$d/calls.log"
exit 0
SH
    chmod +x "$d/bin/omniroute"
    printf '%s\n' "$d"
}
# _prune_list <dir> <name>... - the store as `omniroute combo list` prints it.
_prune_list() {
    local d="$1" n
    shift
    printf '\n\033[1mCombos\033[0m\n' >"$d/list.txt"
    for n in "$@"; do
        printf '  \033[2m○\033[0m %-25s [%-12s] \033[32menabled\033[0m\n' "$n" priority >>"$d/list.txt"
    done
}
# _prune_apply <dir> [apply args] - apply.sh against the two stand-ins.
_prune_apply() {
    local d="$1" pid port
    shift
    read -r pid port < <(_start_test_http_server "$d/gw")
    if [[ -z "$port" ]]; then
        kill "$pid" 2>/dev/null
        echo "no stand-in gateway"
        return 1
    fi
    PATH="$d/bin:$PATH" AUTOOS_OMNIROUTE_URL="http://127.0.0.1:$port" AUTOOS_KEYS_FILE="$d/keys.yml" \
        bash "$ROOT/configuration/omniroute/apply.sh" "$@" 2>&1
    kill "$pid" 2>/dev/null
}

if it "apply prune: deletes only the retired combos the store holds, never a user-made one"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2 t2-worker my-own-combo
    out="$(_prune_apply "$d")"
    ok=1
    deletes="$(grep '^combo delete' "$d/calls.log")"
    [[ "$deletes" == "combo delete tier2 --yes" ]] || { ok=0; echo "deleted: [$deletes]" >&2; }
    grep -q 'my-own-combo' "$d/calls.log" && { ok=0; echo "the user-made combo was touched" >&2; }
    [[ -s "$d/listed" ]] || { ok=0; echo "the store was never listed" >&2; }
    [[ "$out" == *"  - tier2: retired, deleted"* ]] || { ok=0; echo "out: $out" >&2; }
    [[ "$out" == *"my-own-combo"* ]] && { ok=0; echo "the user-made combo was named" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "prune deleted something other than the retired combo"; fi
fi

if it "apply prune: --dry-run names the retired combo and deletes nothing"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2 t2-worker my-own-combo
    out="$(_prune_apply "$d" --dry-run)"
    ok=1
    [[ -s "$d/listed" ]] || { ok=0; echo "the store was never listed" >&2; }
    grep -q '^combo ' "$d/calls.log" && { ok=0; echo "dry run changed combos: $(cat "$d/calls.log")" >&2; }
    [[ "$out" == *"  - tier2: retired, would delete"* ]] || { ok=0; echo "out: $out" >&2; }
    [[ "$out" == *"retired, deleted"* ]] && { ok=0; echo "dry run claims a deletion" >&2; }
    [[ "$out" == *"my-own-combo"* ]] && { ok=0; echo "the user-made combo was named" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the prune dry run is not a dry run"; fi
fi

if it "apply prune: a second run finds no retired combos and deletes nothing"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" t2-worker my-own-combo
    out="$(_prune_apply "$d")"
    ok=1
    grep -q '^combo delete' "$d/calls.log" && { ok=0; echo "deleted: $(grep '^combo delete' "$d/calls.log")" >&2; }
    [[ "$out" == *"  = no retired combos in the store"* ]] || { ok=0; echo "out: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a clean store is not reported as clean"; fi
fi

# A down gateway must not be listed: the real CLI then falls back to reading
# the store file directly, which is how a test would reach the live store.
if it "apply prune: a down gateway is never listed and nothing is pruned"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2
    out="$(PATH="$d/bin:$PATH" AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$d/keys.yml" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    ok=1
    [[ -e "$d/listed" ]] && { ok=0; echo "a down gateway was listed" >&2; }
    grep -q '^combo ' "$d/calls.log" && { ok=0; echo "dry run changed combos: $(cat "$d/calls.log")" >&2; }
    [[ "$out" == *"Prune:"*"gateway down - the store is not read, nothing pruned"* ]] || { ok=0; echo "out: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "prune read the store behind a down gateway"; fi
fi

# A sandbox for register-autostart.sh: fake tool binaries on PATH, a temp
# unit dir, and stubs for systemctl/loginctl that only log their arguments.
# The stub reports every unit as active, so no port probing reaches the
# live machine's listeners.
_svc_reg_sandbox() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/units"
    for t in node omniroute opencode litellm; do
        printf '#!/bin/sh\nexit 0\n' >"$d/bin/$t"; chmod +x "$d/bin/$t"
    done
    printf '#!/bin/sh\necho "$*" >>"%s/systemctl.log"\nexit 0\n' "$d" >"$d/bin/fake-systemctl"
    printf '#!/bin/sh\necho "$*" >>"%s/loginctl.log"\n[ "$1" = show-user ] && echo yes\nexit 0\n' "$d" >"$d/bin/fake-loginctl"
    chmod +x "$d/bin/fake-systemctl" "$d/bin/fake-loginctl"
    printf '%s' "$d"
}
_svc_reg() {
    local d="$1"; shift
    # The docker AI stack's ownership marker lives in the operator's config:
    # point is-active at the sandbox so a migrated host cannot change these tests.
    AUTOOS_AI_STACK_CONFIG="${AUTOOS_AI_STACK_CONFIG:-$d/ai-stack}" \
    PATH="$d/bin:$PATH" AUTOOS_SYSTEMD_USER_DIR="$d/units" AUTOOS_OMNIROUTE_ENV="$d/omniroute.env" \
        AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" AUTOOS_LOGINCTL="$d/bin/fake-loginctl" \
        bash "$ROOT/configuration/autostart/register-autostart.sh" "$@" 2>&1
}

if it "svc: the omniroute unit requires a client key and gets this machine's PATH"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --render autoos-omniroute)"
    ok=1
    # The key requirement comes from ~/.omniroute/.env (operator 2026-09-24):
    # a unit Environment= line would silently override the operator's file.
    [[ "$out" == *"Environment=REQUIRE_API_KEY"* ]] && { ok=0; echo "unit overrides REQUIRE_API_KEY" >&2; }
    [[ "$out" == *"ExecStart=$d/bin/omniroute serve --no-open --port 20128"* ]] || { ok=0; echo "ExecStart: $out" >&2; }
    [[ "$out" == *"Environment=PATH=$d/bin:"* ]] || { ok=0; echo "PATH not filled" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "unfilled placeholder" >&2; }
    [[ "$out" == *"Managed by AutoOS"* ]] || { ok=0; echo "no marker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "omniroute unit render is wrong"; fi
fi

# Review finding 2026-09-25: sed's replacement treats & as "the match", so a
# path holding & rendered @OMNIROUTE@ back into the unit.
if it "svc: a unit renders a tool path that contains an ampersand intact"; then
    d="$(_svc_reg_sandbox)"
    mkdir -p "$d/r&d"
    printf '#!/bin/sh\nexit 0\n' >"$d/r&d/omniroute"; chmod +x "$d/r&d/omniroute"
    out="$(PATH="$d/r&d:$d/bin:$PATH" AUTOOS_SYSTEMD_USER_DIR="$d/units" AUTOOS_OMNIROUTE_ENV="$d/omniroute.env" \
        AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" AUTOOS_LOGINCTL="$d/bin/fake-loginctl" \
        bash "$ROOT/configuration/autostart/register-autostart.sh" --render autoos-omniroute 2>&1)"
    ok=1
    [[ "$out" == *"ExecStart=$d/r&d/omniroute serve"* ]] || { ok=0; echo "ExecStart: $(grep ExecStart <<<"$out")" >&2; }
    [[ "$(grep '^Environment=PATH=' <<<"$out")" == *":$d/r&d:"* || "$(grep '^Environment=PATH=' <<<"$out")" == *"=$d/r&d:"* ]] \
        || { ok=0; echo "PATH: $(grep 'PATH=' <<<"$out")" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "placeholder leaked back" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an & in a path corrupts the unit"; fi
fi

if it "svc: register-autostart --dry-run writes, enables and starts nothing"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --dry-run)"
    ok=1
    [[ -z "$(ls -A "$d/units")" ]] || { ok=0; echo "wrote a unit" >&2; }
    [[ -e "$d/systemctl.log" ]] && grep -qE 'enable|start|daemon-reload' "$d/systemctl.log" && { ok=0; echo "called systemctl" >&2; }
    [[ "$out" == *"would write $d/units/autoos-omniroute.service"* ]] || { ok=0; echo "not announced: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dry run had side effects"; fi
fi

if it "svc: register-autostart twice: the second run skips, a changed unit is backed up"; then
    d="$(_svc_reg_sandbox)"
    first="$(_svc_reg "$d")"
    second="$(_svc_reg "$d")"
    echo "# hand edit" >>"$d/units/autoos-omniroute.service"
    third="$(_svc_reg "$d")"
    ok=1
    [[ "$first" == *"+ autoos-omniroute: wrote"* ]] || { ok=0; echo "first: $first" >&2; }
    [[ "$second" == *"unit unchanged (skipped)"* && "$second" == *"running (skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ autoos-omniroute"* ]] && { ok=0; echo "second run changed something" >&2; }
    compgen -G "$d/units/autoos-omniroute.service.autoos-backup-*" >/dev/null || { ok=0; echo "no backup" >&2; }
    grep -q '# hand edit' "$d/units/autoos-omniroute.service" && { ok=0; echo "not converged" >&2; }
    [[ "$third" == *"replaced"* ]] || { ok=0; echo "third: $third" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "register-autostart is not idempotent"; fi
fi

# Same class as lib/linux/install.sh backup_path/backup_file (main fixed
# this there): register-autostart.sh's own unit backup used a plain
# <unit>.autoos-backup-<stamp> name with a bare cp, so two changed units in
# the same second let the second cp overwrite the first backup and destroy
# it. Pin `date` on PATH so both runs land in the same "second".
if it "backup residual: register-autostart backs up a unit twice in one second without overwriting"; then
    d="$(_svc_reg_sandbox)"
    printf '#!/bin/sh\necho 20260101-000000\n' >"$d/bin/date"; chmod +x "$d/bin/date"
    _svc_reg "$d" --only autoos-omniroute >/dev/null
    printf '# hand-edit-1\n' >>"$d/units/autoos-omniroute.service"
    contentA="$(cat "$d/units/autoos-omniroute.service")"
    second="$(_svc_reg "$d" --only autoos-omniroute)"
    printf '# hand-edit-2\n' >>"$d/units/autoos-omniroute.service"
    contentB="$(cat "$d/units/autoos-omniroute.service")"
    third="$(_svc_reg "$d" --only autoos-omniroute)"
    ok=1
    base="$d/units/autoos-omniroute.service.autoos-backup-20260101-000000"
    [[ "$second" == *"+ autoos-omniroute: replaced"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$third" == *"+ autoos-omniroute: replaced"* ]] || { ok=0; echo "third: $third" >&2; }
    [[ -f "$base" ]] || { ok=0; echo "no first backup at the plain stamp name" >&2; }
    [[ -f "$base-1" ]] || { ok=0; echo "no second backup (overwrote the first?)" >&2; }
    [[ "$(cat "$base" 2>/dev/null)" == "$contentA" ]] || { ok=0; echo "first backup lost the hand edit" >&2; }
    [[ "$(cat "$base-1" 2>/dev/null)" == "$contentB" ]] || { ok=0; echo "second backup content wrong" >&2; }
    [[ "$(find "$d/units" -name 'autoos-omniroute.service.autoos-backup-*' | wc -l | tr -d ' ')" == 2 ]] \
        || { ok=0; echo "expected exactly 2 backups" >&2; }
    grep -q 'hand-edit' "$d/units/autoos-omniroute.service" && { ok=0; echo "final unit still holds a hand edit" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "register-autostart overwrote a same-second unit backup"; fi
fi

if it "svc: register-autostart appends REQUIRE_API_KEY=true once, with a backup"; then
    d="$(_svc_reg_sandbox)"
    printf 'STORAGE_ENCRYPTION_KEY=keep-me\n' >"$d/omniroute.env"
    _svc_reg "$d" >/dev/null
    second="$(_svc_reg "$d")"
    ok=1
    grep -q '^STORAGE_ENCRYPTION_KEY=keep-me$' "$d/omniroute.env" || { ok=0; echo "existing line lost" >&2; }
    [[ "$(grep -c '^REQUIRE_API_KEY=true$' "$d/omniroute.env")" == 1 ]] || { ok=0; echo "not exactly one line" >&2; }
    compgen -G "$d/omniroute.env.autoos-backup-*" >/dev/null || { ok=0; echo "no backup" >&2; }
    [[ "$second" == *"REQUIRE_API_KEY=true is set"*"(skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    printf 'REQUIRE_API_KEY=false\n' >"$d/omniroute.env"
    third="$(_svc_reg "$d")"
    grep -q '^REQUIRE_API_KEY=false$' "$d/omniroute.env" || { ok=0; echo "overrode an explicit operator value" >&2; }
    [[ "$third" == *"left alone"* ]] || { ok=0; echo "third: $third" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "REQUIRE_API_KEY handling is wrong"; fi
fi

# Same class, the omni_env append site: two backups of ~/.omniroute/.env in
# the same second must not collide either.
if it "backup residual: register-autostart backs up omniroute.env twice in one second without overwriting"; then
    d="$(_svc_reg_sandbox)"
    printf '#!/bin/sh\necho 20260101-000000\n' >"$d/bin/date"; chmod +x "$d/bin/date"
    printf 'SOME=1\n' >"$d/omniroute.env"
    contentA="$(cat "$d/omniroute.env")"
    first="$(_svc_reg "$d" --only autoos-omniroute)"
    sed -i '/^REQUIRE_API_KEY=/d' "$d/omniroute.env"
    contentB="$(cat "$d/omniroute.env")"
    second="$(_svc_reg "$d" --only autoos-omniroute)"
    ok=1
    base="$d/omniroute.env.autoos-backup-20260101-000000"
    [[ "$first" == *"+ appended REQUIRE_API_KEY=true"* ]] || { ok=0; echo "first: $first" >&2; }
    [[ "$second" == *"+ appended REQUIRE_API_KEY=true"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ -f "$base" ]] || { ok=0; echo "no first backup at the plain stamp name" >&2; }
    [[ -f "$base-1" ]] || { ok=0; echo "no second backup (overwrote the first?)" >&2; }
    [[ "$(cat "$base" 2>/dev/null)" == "$contentA" ]] || { ok=0; echo "first backup content wrong" >&2; }
    [[ "$(cat "$base-1" 2>/dev/null)" == "$contentB" ]] || { ok=0; echo "second backup content wrong" >&2; }
    [[ "$(find "$d" -maxdepth 1 -name 'omniroute.env.autoos-backup-*' | wc -l | tr -d ' ')" == 2 ]] \
        || { ok=0; echo "expected exactly 2 backups" >&2; }
    grep -q '^REQUIRE_API_KEY=true$' "$d/omniroute.env" || { ok=0; echo "final file lost the key" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "register-autostart overwrote a same-second omniroute.env backup"; fi
fi

# register-autostart.sh keeps the old unit when its backup copy fails
# (continue at the unit-replace site): the file must be unchanged.
if it "backup residual: register-autostart leaves the unit in place when its backup copy fails"; then
    d="$(_svc_reg_sandbox)"
    _svc_reg "$d" --only autoos-omniroute >/dev/null
    printf '# hand-edit\n' >>"$d/units/autoos-omniroute.service"
    cp "$d/units/autoos-omniroute.service" "$d/units/autoos-omniroute.service.orig"
    bin="$(backup_fail_bin)"
    out="$(PATH="$bin:$PATH" _svc_reg "$d" --only autoos-omniroute 2>&1)"; rc=$?
    ok=1
    cmp -s "$d/units/autoos-omniroute.service" "$d/units/autoos-omniroute.service.orig" \
        || { ok=0; echo "the unit was replaced without a backup" >&2; }
    [[ "$out" == *"could not back up $d/units/autoos-omniroute.service"* ]] \
        || { ok=0; echo "no warning naming the unit: ${out:0:300}" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "register-autostart replaces a unit it could not back up"; fi
fi

if it "svc: register-autostart never runs a second gateway next to omniroute autostart"; then
    d="$(_svc_reg_sandbox)"
    echo "[Service]" >"$d/units/omniroute.service"
    out="$(_svc_reg "$d")"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" ]] && { ok=0; echo "wrote a second gateway" >&2; }
    [[ "$out" == *"omniroute autostart disable"* ]] || { ok=0; echo "no hint: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "two gateways would fight over :20128"; fi
fi

if it "svc: register-autostart --unregister removes only units AutoOS wrote"; then
    d="$(_svc_reg_sandbox)"
    _svc_reg "$d" >/dev/null
    printf '[Service]\nExecStart=/bin/true\n' >"$d/units/autoos-foreign.service"
    out="$(_svc_reg "$d" --unregister)"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" ]] && { ok=0; echo "our unit stayed" >&2; }
    [[ -e "$d/units/autoos-foreign.service" ]] || { ok=0; echo "removed a foreign unit" >&2; }
    grep -q 'disable --now autoos-omniroute.service' "$d/systemctl.log" || { ok=0; echo "not disabled" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "unregister is wrong"; fi
fi

if it "svc: opencode-cli (V2) writes the global provider config after install"; then
    report="$(python3 - <<'PY'
import json, io
want = {"catalog/linux.json": "setup_opencode_config",
        "catalog/macos.json": "setup_opencode_config",
        "catalog/windows.json": "Set-AutoOSOpenCodeConfig"}
bad = []
for f, fn in want.items():
    d = json.load(io.open(f, encoding="utf-8-sig"))
    comps = [c for cat in d["categories"] for c in cat["components"] if c["id"] == "opencode-cli"]
    if not comps or comps[0].get("postInstall") != fn:
        bad.append(f)
print(" ".join(bad) or "ok")
PY
)"
    assert_eq "$report" "ok"
fi

# The user's own config may be JSONC (comments, trailing commas). json.load
# used to fail on it and the writer then started from {} - every provider,
# MCP server and setting the user had was replaced wholesale.
if it "svc: the opencode writer merges into a JSONC config and never writes a key value"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    cat >"$scratch/.config/opencode/config.json" <<'JSONC'
{
  // the user's own theme and provider
  "theme": "nord",
  /* block comment */
  "provider": {
    "mine": {"options": {"baseURL": "http://example.invalid/v1"}},
  },
}
JSONC
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY CONTEXT7_API_KEY
      export OPENROUTER_API_KEY="sk-or-v1-notarealkeyatall0123456789"
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/opencode" <<'PY'
import io, json, os, sys
d = sys.argv[1]
problems = []
for name in ("config.json", "opencode.json"):
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        problems.append("missing:" + name); continue
    raw = io.open(p, encoding="utf-8").read()
    doc = json.loads(raw)
    if doc.get("theme") != "nord": problems.append(name + ":theme-lost")
    if "mine" not in doc.get("provider", {}): problems.append(name + ":provider-lost")
    if "omniroute" not in doc.get("provider", {}): problems.append(name + ":no-omniroute")
    if "notarealkey" in raw: problems.append(name + ":plaintext-key")
    orr = doc.get("provider", {}).get("openrouter", {}).get("options", {}).get("apiKey")
    if orr != "{env:OPENROUTER_API_KEY}": problems.append(name + ":openrouter=%s" % orr)
print(" ".join(problems) or "ok")
PY
)"
    rm -rf "$scratch"
    assert_eq "$report" "ok"
    fi
fi

# Measured live 2026-09-24: the harness rewrote config.json with a trailing
# newline the writer did not emit, so every re-run took a backup; and the
# seeded opencode.json was backed up on its very first write.
if it "svc: the opencode writer takes no backup on a fresh machine or a re-run"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    for _ in 1 2 3; do
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    done
    n="$(compgen -G "$scratch/.config/opencode/*.autoos-backup-*" | wc -l)"
    rm -rf "$scratch"
    assert_eq "$n" "0"
    fi
fi

# V2 (@opencode/cli) reads `providers` (package/env/settings), not V1's
# `provider`: measured 2026-09-24, serve from $HOME listed no provider at all
# with only the V1 block written. The V2 block is projected from the repo's
# opencode.jsonc (single source) into opencode.json, never into config.json
# (V1's file).
if it "svc: the opencode writer adds the V2 providers block when the CLI is V2"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      opencode_is_v2() { return 0; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/opencode" <<'PY2'
import io, json, os, re, sys
d = sys.argv[1]
problems = []
oc = json.load(io.open(os.path.join(d, "opencode.json"), encoding="utf-8"))
v1 = json.load(io.open(os.path.join(d, "config.json"), encoding="utf-8"))
repo = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
for name in ("omniroute", "litellm"):
    got = (oc.get("providers") or {}).get(name)
    if got != repo["providers"][name]:
        problems.append("v2-" + name + "-differs")
if "providers" in v1:
    problems.append("v1-file-got-v2-block")
if oc.get("model") != repo["model"]:
    problems.append("model=%s" % oc.get("model"))
print(" ".join(problems) or "ok")
PY2
)"
    rm -rf "$scratch"
    assert_eq "$report" "ok"
    fi
fi

if it "svc: the opencode writer leaves an unparseable config alone"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{ "theme": "nord", BROKEN\n' >"$scratch/.config/opencode/config.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; curl() { return 6; }
      setup_opencode_config >/dev/null 2>&1 )
    got="$(cat "$scratch/.config/opencode/config.json")"
    rm -rf "$scratch"
    assert_eq "$got" '{ "theme": "nord", BROKEN'
    fi
fi

if it "svc: opencode.json is backed up before it changes and untouched on a re-run"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{"theme": "gruvbox"}\n' >"$scratch/.config/opencode/opencode.json"
    for _ in 1 2; do
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    done
    n="$(compgen -G "$scratch/.config/opencode/opencode.json.autoos-backup-*" | wc -l)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
    fi
fi

# opencode serve (V2) answers the static UI to anyone and guards /api/* with
# HTTP Basic (user "opencode", password from OPENCODE_PASSWORD; measured on
# 2.0.16). Without the variable it invents a random password per start, so
# the phone could never log in twice. The wrapper pins one, in a 0600 file.
if it "svc: run-opencode-serve binds the LAN port with a pinned password and keys by name"; then
    d="$(mktemp -d)"
    mkdir -p "$d/lit"
    printf 'LITELLM_MASTER_KEY=sk-litellm-fake-000\nOPENROUTER_API_KEY=REPLACE_WITH_X\n' >"$d/lit/.env"
    printf 'omniroute: sk-omni-fake-111\n' >"$d/keys.yml"
    out="$(env -u AUTOOS_OMNIROUTE_KEY AUTOOS_LITELLM_DIR="$d/lit" AUTOOS_KEYS_FILE="$d/keys.yml" \
        AUTOOS_OPENCODE_PASSWORD_FILE="$d/pw" \
        bash "$ROOT/configuration/autostart/run-opencode-serve.sh" --dry-run 2>&1)"
    ok=1
    [[ "$out" == *"opencode serve --hostname 0.0.0.0 --port 4096"* ]] || { ok=0; echo "bind: $out" >&2; }
    [[ "$out" == *"LITELLM_MASTER_KEY"* && "$out" == *"AUTOOS_OMNIROUTE_KEY"* ]] || { ok=0; echo "keys: $out" >&2; }
    [[ "$out" == *"OPENROUTER_API_KEY"* ]] && { ok=0; echo "placeholder loaded" >&2; }
    [[ "$out" == *"fake-000"* || "$out" == *"fake-111"* ]] && { ok=0; echo "a value was printed" >&2; }
    [[ "$out" == *"would generate"*"$d/pw"* ]] || { ok=0; echo "password plan: $out" >&2; }
    [[ -e "$d/pw" ]] && { ok=0; echo "dry run wrote the password file" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "run-opencode-serve plan is wrong"; fi
fi

if it "svc: run-opencode-serve generates the password once, private, and reuses it"; then
    d="$(mktemp -d)"
    mkdir -p "$d/bin"
    # A fake opencode that reports whether it received a password.
    printf '#!/bin/sh\n[ -n "$OPENCODE_PASSWORD" ] && echo "pw-set $*" || echo "pw-missing $*"\n' >"$d/bin/opencode"
    chmod +x "$d/bin/opencode"
    run() { PATH="$d/bin:$PATH" AUTOOS_LITELLM_DIR="$d/none" AUTOOS_KEYS_FILE="$d/none.yml" \
        AUTOOS_OPENCODE_PASSWORD_FILE="$d/cfg/pw" bash "$ROOT/configuration/autostart/run-opencode-serve.sh" 2>&1; }
    first="$(run)"; pw1="$(cat "$d/cfg/pw" 2>/dev/null)"
    second="$(run)"; pw2="$(cat "$d/cfg/pw" 2>/dev/null)"
    mode="$(stat -c %a "$d/cfg/pw" 2>/dev/null)"
    ok=1
    [[ "$first" == *"pw-set serve --hostname 0.0.0.0 --port 4096"* ]] || { ok=0; echo "first: $first" >&2; }
    [[ ${#pw1} -ge 24 && "$pw1" == "$pw2" ]] || { ok=0; echo "password not stable" >&2; }
    [[ "$mode" == 600 ]] || { ok=0; echo "mode $mode" >&2; }
    [[ "$first$second" == *"$pw1"* ]] && { ok=0; echo "password printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "password handling is wrong"; fi
fi

if it "svc: the opencode unit runs the wrapper from this checkout"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --render autoos-opencode)"
    ok=1
    [[ "$out" == *"ExecStart=$ROOT/configuration/autostart/run-opencode-serve.sh"* ]] || { ok=0; echo "$out" >&2; }
    [[ "$out" == *"Environment=PATH=$d/bin:"* ]] || { ok=0; echo "PATH" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "unfilled placeholder" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "opencode unit render is wrong"; fi
fi

# A service started by systemd never sees a token exported from ~/.zshrc, so
# opencode's omnigraph bridge "connects" and then fails every read with
# "missing bearer token" (B-omnigraph 2026-09-24, item 6). The per-user env
# file write_omnigraph_env keeps is the one source; the leading dash makes it
# optional so a machine without it still starts. Only units that start an
# omnigraph client get it: OmniRoute listens on the LAN and never needs it.
if it "svc: omnigraph units read the per-user env file (optional), the gateways do not"; then
    d="$(_svc_reg_sandbox)"
    want='EnvironmentFile=-%h/.autoos-omnigraph.env'
    ok=1
    for u in autoos-opencode autoos-stack; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$(grep -c '^EnvironmentFile=' <<<"$out")" == 1 ]] || { ok=0; echo "$u: not exactly one EnvironmentFile= line" >&2; }
        grep -qxF "$want" <<<"$out" || { ok=0; echo "$u: missing [$want]" >&2; }
        # In [Service]: after its header, before [Install].
        svc_line="$(grep -nx '\[Service\]' <<<"$out" | cut -d: -f1)"
        env_line="$(grep -nxF "$want" <<<"$out" | cut -d: -f1)"
        inst_line="$(grep -nx '\[Install\]' <<<"$out" | cut -d: -f1)"
        [[ -n "$env_line" && "$env_line" -gt "$svc_line" && "$env_line" -lt "$inst_line" ]] \
            || { ok=0; echo "$u: EnvironmentFile= is outside [Service]" >&2; }
        # Comments may name the variable; a directive must never carry a value.
        [[ "$(grep -v '^#' <<<"$out")" == *"OMNIGRAPH_TOKEN"* ]] && { ok=0; echo "$u: a token is set in the unit text" >&2; }
    done
    for u in autoos-omniroute autoos-litellm; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$out" == *"autoos-omnigraph.env"* ]] && { ok=0; echo "$u: the LAN-facing gateway must not get the omnigraph token" >&2; }
    done
    rm -rf "$d"
    if (( ok )); then pass; else fail "units do not read ~/.autoos-omnigraph.env as expected"; fi
fi

if it "svc: an installed opencode unit gains the omnigraph line once: backed up, then skipped"; then
    d="$(_svc_reg_sandbox)"
    # What an earlier AutoOS wrote: today's render minus the new line.
    _svc_reg "$d" --render autoos-opencode | grep -v '^EnvironmentFile=' >"$d/old.service"
    cp "$d/old.service" "$d/units/autoos-opencode.service"
    first="$(_svc_reg "$d" --only autoos-opencode)"
    second="$(_svc_reg "$d" --only autoos-opencode)"
    nbak="$(find "$d/units" -name 'autoos-opencode.service.autoos-backup-*' | wc -l)"
    ok=1
    [[ "$first" == *"+ autoos-opencode: replaced"*"(backup: "* ]] || { ok=0; echo "first: $first" >&2; }
    grep -qxF 'EnvironmentFile=-%h/.autoos-omnigraph.env' "$d/units/autoos-opencode.service" \
        || { ok=0; echo "unit did not gain the line" >&2; }
    [[ "$nbak" == 1 ]] || { ok=0; echo "expected exactly one backup, found $nbak" >&2; }
    cmp -s "$d/old.service" "$(find "$d/units" -name 'autoos-opencode.service.autoos-backup-*' | head -n1)" \
        || { ok=0; echo "the backup is not the previous unit" >&2; }
    [[ "$second" == *"= autoos-opencode: unit unchanged (skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ autoos-opencode"* ]] && { ok=0; echo "second run changed something" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "adding the omnigraph line is not idempotent and backed up"; fi
fi

# systemd runs ExecStart directly: a 100644 launcher fails with 203/EXEC.
if it "svc: every script a unit or the healthcheck runs is executable in git"; then
    bad=""
    for f in configuration/autostart/Start-AutoOSStack.sh configuration/autostart/register-autostart.sh \
             configuration/autostart/run-opencode-serve.sh configuration/litellm/start-litellm.sh \
             configuration/healthcheck.sh configuration/start-stack.sh; do
        mode="$(git ls-files -s -- "$f" | cut -d' ' -f1)"
        [[ "$mode" == 100755 ]] || bad+="$f=$mode "
    done
    assert_eq "$bad" ""
fi

if it "svc: every unit template renders with this checkout and no placeholder left"; then
    d="$(_svc_reg_sandbox)"
    ok=1
    for u in autoos-omniroute autoos-litellm autoos-opencode autoos-stack; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$out" =~ @[A-Z]+@ ]] && { ok=0; echo "$u: unfilled placeholder" >&2; }
        [[ "$out" == *"%h/AutoOS"* ]] && { ok=0; echo "$u: hard-coded ~/AutoOS" >&2; }
        [[ "$out" == *"WantedBy=default.target"* ]] || { ok=0; echo "$u: not boot-wanted" >&2; }
    done
    stack="$(_svc_reg "$d" --render autoos-stack)"
    [[ "$stack" == *"ExecStart=$ROOT/configuration/autostart/Start-AutoOSStack.sh"* ]] || { ok=0; echo "stack ExecStart" >&2; }
    [[ "$stack" == *"Type=oneshot"* ]] || { ok=0; echo "stack not oneshot" >&2; }
    lit="$(_svc_reg "$d" --render autoos-litellm)"
    [[ "$lit" == *"start-litellm.sh --foreground"* ]] || { ok=0; echo "litellm ExecStart" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "unit templates do not render cleanly"; fi
fi

if it "svc: register-autostart registers the four units in dependency order"; then
    d="$(_svc_reg_sandbox)"
    _svc_reg "$d" >/dev/null
    order="$(grep -oE '^--user enable autoos-[a-z]+' "$d/systemctl.log" | awk '{print $3}' | tr '\n' ' ')"
    rm -rf "$d"
    assert_eq "$order" "autoos-omniroute autoos-litellm autoos-opencode autoos-stack "
fi

if it "svc: the stack launcher starts each service through its unit when one is installed"; then
    ok=1
    for u in autoos-omniroute autoos-litellm autoos-opencode; do
        grep -q "unit_installed $u" configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "no unit path: $u" >&2; }
        grep -q "systemctl --user start $u.service" configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "not started via unit: $u" >&2; }
    done
    grep -q 'run-opencode-serve.sh' configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "serve fallback bypasses the password wrapper" >&2; }
    grep -q '/tmp/' configuration/autostart/Start-AutoOSStack.sh && { ok=0; echo "logs to /tmp" >&2; }
    if (( ok )); then pass; else fail "launcher bypasses the units"; fi
fi

if it "svc: healthcheck --fix resumes opencode serve and litellm too"; then
    ok=1
    grep -qE 'ANY_DOWN=1' configuration/healthcheck.sh || { ok=0; echo "no combined down flag" >&2; }
    grep -qE 'OC_UP -eq 0' configuration/healthcheck.sh || { ok=0; echo "serve not in the fix condition" >&2; }
    grep -qE 'LT_UP -eq 0' configuration/healthcheck.sh || { ok=0; echo "litellm not in the fix condition" >&2; }
    grep -q '4000' configuration/healthcheck.sh || { ok=0; echo "no :4000 probe" >&2; }
    if (( ok )); then pass; else fail "healthcheck --fix leaves a service down"; fi
fi

# The profiles in ~/.openhands are read by whichever OpenHands runs with that
# directory: the docker app (host.docker.internal via --add-host) or a native
# host process such as agent-canvas, which cannot resolve that name on a
# native Linux host. Like resolve_ollama_base_url: keep it where it resolves.
if it "svc: profile sync picks the gateway host per consumer"; then
    d="$(mktemp -d)"
    # sync_base <resolves 0|1> [--consumer X]: the omniroute-t2-worker base_url.
    sync_base() {
        local resolves="$1"; shift
        rm -rf "$d/oh"
        env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key AUTOOS_FAKE_HDI_RESOLVES="$resolves" \
            python3 "$ROOT/tools/sync-openhands-profiles.py" --openhands-dir "$d/oh" \
            --keys-file "$d/none.yml" --litellm-env "$d/none.env" "$@" >/dev/null 2>&1
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_url"])' \
            "$d/oh/profiles/omniroute-t2-worker.json" 2>/dev/null
    }
    got="$(sync_base 0)|$(sync_base 0 --consumer native)|$(sync_base 1 --consumer native)"
    rm -rf "$d"
    assert_eq "$got" \
        "http://host.docker.internal:20128/v1|http://127.0.0.1:20128/v1|http://host.docker.internal:20128/v1"
fi

# The image's entrypoint runs everything as root unless SANDBOX_USER_ID names
# the host user (image default 0): ~/.openhands then fills with root-owned
# files the host-side profile sync can no longer write. A missing host dir is
# created by docker as root, so it must exist before `docker run`.
if it "svc: start-stack openhands runs as the host user, survives reboots and is capped"; then
    block="$(sed -n '/^    openhands)/,/^        ;;/p' configuration/start-stack.sh)"
    ok=1
    [[ "$block" == *'SANDBOX_USER_ID="$(id -u)"'* ]] || { ok=0; echo "no SANDBOX_USER_ID" >&2; }
    [[ "$block" == *'mkdir -p "$HOME/.openhands"'* ]] || { ok=0; echo "dir not pre-created" >&2; }
    [[ "$block" == *'--restart unless-stopped'* ]] || { ok=0; echo "not reboot-safe" >&2; }
    [[ "$block" == *'docker run -d --rm'* ]] && { ok=0; echo "--rm defeats the restart policy" >&2; }
    [[ "$block" == *'--memory'* ]] || { ok=0; echo "no memory cap" >&2; }
    [[ "$block" == *'--consumer container'* ]] || { ok=0; echo "profile consumer not named" >&2; }
    # Remote browsers need the sandbox URL pattern; opt-in, never a default.
    [[ "$block" == *'OH_SANDBOX_CONTAINER_URL_PATTERN="$AUTOOS_OPENHANDS_SANDBOX_URL"'* ]] || { ok=0; echo "no sandbox URL passthrough" >&2; }
    [[ "$block" == *'if [[ -n "${AUTOOS_OPENHANDS_SANDBOX_URL:-}" ]]'* ]] || { ok=0; echo "sandbox URL not opt-in" >&2; }
    if (( ok )); then pass; else fail "OpenHands launch is not native-Linux safe"; fi
fi

# SDK-1.36 OpenHands keeps LLM profiles in its own settings store and never
# reads profiles/*.json (measured 2026-09-24: /api/v1/settings/profiles was
# empty with 16 files synced). The push saves them through the app's API.
if it "svc: profile sync pushes the tiers into a running app, idempotently and capped"; then
    d="$(mktemp -d)"
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$d/port" "$d/req.log" >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$d/port")"
    push() { env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" --push-url "$url" 2>&1; }
    first="$(push)"
    : >"$d/req.log"
    second="$(push)"
    kill "$fake_pid" 2>/dev/null
    ok=1
    [[ "$first" == *"app settings seeded with omniroute-t1-orchestrator"* ]] || { ok=0; echo "not seeded: $first" >&2; }
    # Spec order = configuration/openhands/tier-profiles.json: the three
    # hierarchy tiers (t1 -> t2 -> t3) fill the fake's cap of 3; the red-by-
    # design t1-orchestrator-free-only is last and never takes a slot.
    for _t in omniroute-t1-orchestrator omniroute-t2-worker omniroute-t3-driver; do
        [[ "$first" == *"app profile $_t saved"* ]] || { ok=0; echo "spec order ($_t): $first" >&2; }
    done
    [[ "$first" == *"app profile omniroute-t2-orchestrator saved"* ]] && { ok=0; echo "cap 3 should stop before t2-orchestrator" >&2; }
    [[ "$first" == *"app profile omniroute-t1-orchestrator-free-only saved"* ]] && { ok=0; echo "free-only took a slot" >&2; }
    [[ "$first" == *"profile cap is reached"* ]] || { ok=0; echo "cap not reported" >&2; }
    [[ "$first" == *"FAILED"* ]] && { ok=0; echo "a push failed (StrictLLM?): $first" >&2; }
    grep -q '^POST' "$d/req.log" && grep -q '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" \
        && { ok=0; echo "second run re-posted an unchanged profile" >&2; }
    [[ "$second" == *"omniroute-t1-orchestrator skipped (up to date)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$first$second" == *"sk-fake-profile-key"* ]] && { ok=0; echo "key printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "profile push is wrong"; fi
fi

# The app keeps at most 10 profiles and never forgets one, so a renamed or
# dropped tier kept its slot forever. The push deletes the profiles AutoOS
# owns that the spec no longer lists, before it pushes. Ownership is a RECORD,
# never a name prefix (review 2026-09-25: a user's `omniroute-personal` must
# survive): the names this sync pushed (.autoos-pushed.json next to the
# profiles) plus the spec's retired_ids (the ids from before that record).
# The active profile is never deleted, and nothing is when the app does not
# say which one is active.
# _fake_app <dir> [<seed.json>]: starts the fake and sets fake_pid + url.
# Never call it inside $(...): the pid would be set in that subshell only,
# the caller's kill would miss, and the fake would outlive the test.
_fake_app() {
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$1/port" "$1/req.log" ${2:+"$2"} >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$1/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$1/port")"
}
_fake_profiles() {  # _fake_profiles <url>: the app's profile names, one per line
    python3 -c 'import json,sys,urllib.request; print("\n".join(p["name"] for p in json.load(urllib.request.urlopen(sys.argv[1] + "/api/v1/settings/profiles"))["profiles"]))' "$1"
}
_push_to() {  # _push_to <dir> <url>
    env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$1/oh" --keys-file "$1/none.yml" --litellm-env "$1/none.env" --push-url "$2" 2>&1
}
_seed_pushed() {  # _seed_pushed <dir> <name>...: records <name>s as pushed by an earlier sync
    local dir="$1"; shift
    mkdir -p "$dir/oh/profiles"
    python3 -c 'import json,sys; json.dump({n: "seeded" for n in sys.argv[2:]}, open(sys.argv[1], "w"))' \
        "$dir/oh/profiles/.autoos-pushed.json" "$@"
}

if it "svc: profile push deletes retired AutoOS profiles, never a foreign one"; then
    d="$(mktemp -d)"
    cat >"$d/seed.json" <<'JSON'
{"cap": 10, "settings": {"agent_settings_diff": {}},
 "profiles": {"omniroute-tier1": {"model": "openai/tier1"},
              "litellm-tier2": {"model": "openai/tier2"},
              "openrouter-gone": {"model": "openrouter/x"},
              "omniroute-personal": {"model": "openai/mine", "api_key": "k"},
              "my-own-profile": {"model": "openai/mine", "api_key": "k"},
              "omniroute": {"model": "openai/prefix-without-dash"}}}
JSON
    _seed_pushed "$d" openrouter-gone
    _fake_app "$d" "$d/seed.json"
    first="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    first_log="$(cat "$d/req.log")"
    : >"$d/req.log"
    second="$(_push_to "$d" "$url")"
    second_deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    ok=1
    # legacy retired_ids (tier1, litellm-tier2) and a recorded push (gone) go
    for _r in omniroute-tier1 litellm-tier2 openrouter-gone; do
        [[ "$first" == *"app profile $_r deleted (retired"* ]] || { ok=0; echo "not deleted: $_r: $first" >&2; }
        grep -qx "$_r" <<<"$after" && { ok=0; echo "still in the app: $_r" >&2; }
    done
    # an AutoOS-looking prefix is not ownership
    for _k in omniroute-personal my-own-profile omniroute; do
        grep -qx "$_k" <<<"$after" || { ok=0; echo "a foreign profile was deleted: $_k" >&2; }
        grep -q "^DELETE .*/$_k\$" <<<"$first_log" && { ok=0; echo "DELETE sent for $_k" >&2; }
    done
    grep -qx omniroute-t1-orchestrator <<<"$after" || { ok=0; echo "t1 not pushed: $after" >&2; }
    [[ "$second_deletes" == 0 ]] || { ok=0; echo "second run deleted again ($second_deletes)" >&2; }
    [[ "$second" == *"deleted"* ]] && { ok=0; echo "second run reports a delete: $second" >&2; }
    [[ "$first$second" == *"sk-fake-profile-key"* ]] && { ok=0; echo "key printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "retired profiles are not cleaned up safely"; fi
fi

if it "svc: profile push deletes retired profiles before it saves any"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}, "omniroute-tier2": {"model": "openai/tier2"}, "omniroute-tier3": {"model": "openai/tier3"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    kill "$fake_pid" 2>/dev/null
    last_delete="$(grep -n '^DELETE' "$d/req.log" | tail -1 | cut -d: -f1)"
    first_save="$(grep -n '^POST /api/v1/settings/profiles/' "$d/req.log" | head -1 | cut -d: -f1)"
    rm -rf "$d"
    ok=1
    [[ -n "$last_delete" && -n "$first_save" && "$last_delete" -lt "$first_save" ]] || { ok=0; echo "delete line $last_delete, first save line $first_save" >&2; }
    [[ "$out" == *"app profile omniroute-t3-driver saved"* ]] || { ok=0; echo "freed slots unused: $out" >&2; }
    if (( ok )); then pass; else fail "retired profiles still hold slots during the push"; fi
fi

if it "svc: profile push never deletes the active profile, even a retired one"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "settings": {"agent_settings_diff": {}}, "active": "omniroute-tier1", "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    grep -qx omniroute-tier1 <<<"$after" || { ok=0; echo "active profile gone" >&2; }
    [[ "$out" == *"omniroute-tier1 is retired but active"* ]] || { ok=0; echo "not announced: $out" >&2; }
    if (( ok )); then pass; else fail "the active profile was not protected"; fi
fi

if it "svc: profile push deletes nothing when the app does not name its active profile"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "omit_active_key": true, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    saves="$(grep -c '^POST /api/v1/settings/profiles/' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    [[ "$out" == *"does not say which profile is active - deleting nothing"* ]] || { ok=0; echo "not warned: $out" >&2; }
    (( saves > 0 )) || { ok=0; echo "the push itself stopped" >&2; }
    if (( ok )); then pass; else fail "a delete ran without knowing the active profile"; fi
fi

if it "svc: profile push re-checks the active profile right before each delete"; then
    # The fake makes omniroute-tier1 active on its 2nd profile listing - a
    # UI switch between the push's first look and its delete.
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "activate_on_list": {"call": 2, "name": "omniroute-tier1"}, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    grep -qx omniroute-tier1 <<<"$after" || { ok=0; echo "the newly active profile was deleted" >&2; }
    [[ "$out" == *"omniroute-tier1 became the active profile - left in place"* ]] || { ok=0; echo "not announced: $out" >&2; }
    if (( ok )); then pass; else fail "a profile made active mid-run was deleted"; fi
fi

# Spec order decides which tiers make the cap - also in an app that already
# holds lower-ranked AutoOS profiles from an older order (measured 2026-09-25:
# the live app held 10 in-spec profiles, 0 retired, so deleting retired ids
# alone freed nothing and t3-driver/t4-rag still never fit).
if it "svc: profile push makes room for a higher-ranked tier by removing the lowest-ranked AutoOS one"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-t1-orchestrator-free-only": {"model": "openai/t1-orchestrator-free-only"}, "omniroute-spark-1.3-contributor": {"model": "openai/spark-1.3-contributor"}, "my-own-profile": {"model": "openai/mine"}}}' >"$d/seed.json"
    _seed_pushed "$d" omniroute-t1-orchestrator-free-only omniroute-spark-1.3-contributor
    _fake_app "$d" "$d/seed.json"
    first="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url" | sort | tr '\n' ' ')"
    : >"$d/req.log"
    second="$(_push_to "$d" "$url")"
    second_deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    rm -rf "$d"
    ok=1
    # One slot is the user's; the two AutoOS slots go to the spec's top two.
    [[ "$after" == "my-own-profile omniroute-t1-orchestrator omniroute-t2-worker " ]] || { ok=0; echo "app holds: $after" >&2; }
    [[ "$first" == *"omniroute-t1-orchestrator-free-only removed to make room for omniroute-t1-orchestrator"* ]] || { ok=0; echo "eviction not announced: $first" >&2; }
    [[ "$first" == *"profile cap is reached"* ]] || { ok=0; echo "cap not reported" >&2; }
    [[ "$second_deletes" == 0 ]] || { ok=0; echo "second run evicted again ($second_deletes)" >&2; }
    if (( ok )); then pass; else fail "the cap is not filled in spec order"; fi
fi

# Rank comes from the FULL spec order: an AutoOS tier the app holds but this
# run could not build (no key for its gateway) still ranks, so it can make
# room for a higher tier. A prefix-named profile nobody recorded never does.
if it "svc: profile push evicts an unbuilt AutoOS tier below the refused one, never a foreign profile"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-personal": {"model": "openai/mine"}, "litellm-t2-worker": {"model": "openai/t2-worker"}, "omniroute-t1-orchestrator-free-only": {"model": "openai/t1-orchestrator-free-only"}}}' >"$d/seed.json"
    _seed_pushed "$d" litellm-t2-worker omniroute-t1-orchestrator-free-only
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url" | sort | tr '\n' ' ')"
    kill "$fake_pid" 2>/dev/null
    personal_deletes="$(grep -c '^DELETE .*/omniroute-personal$' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$after" == "omniroute-personal omniroute-t1-orchestrator omniroute-t2-worker " ]] || { ok=0; echo "app holds: $after" >&2; }
    [[ "$out" == *"litellm-t2-worker removed to make room for omniroute-t2-worker"* ]] || { ok=0; echo "unbuilt tier not ranked: $out" >&2; }
    [[ "$personal_deletes" == 0 ]] || { ok=0; echo "omniroute-personal DELETEd" >&2; }
    if (( ok )); then pass; else fail "eviction ranks or ownership are wrong"; fi
fi

# The app never returns a profile's key (only api_key_set), so "every other
# field matches" cannot see a rotated key - it was skipped forever (review
# finding 2026-09-25). The sync records a short hash of the last pushed key.
if it "svc: profile push re-sends a profile whose key was rotated"; then
    d="$(mktemp -d)"
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$d/port" "$d/req.log" >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$d/port")"
    push() { env AUTOOS_OMNIROUTE_KEY="$1" python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" --push-url "$url" 2>&1; }
    push sk-fake-key-one >/dev/null
    : >"$d/req.log"
    same="$(push sk-fake-key-one)"
    same_posts="$(grep -c '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" || true)"
    : >"$d/req.log"
    rotated="$(push sk-fake-key-two)"
    rotated_posts="$(grep -c '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    state_mode="$(stat -c %a "$d/oh/profiles/.autoos-pushed.json" 2>/dev/null)"
    leaked="$(grep -c 'sk-fake-key' "$d/oh/profiles/.autoos-pushed.json" 2>/dev/null || true)"
    rm -rf "$d"
    ok=1
    [[ "$same_posts" == 0 ]] || { ok=0; echo "unchanged key re-posted ($same_posts)" >&2; }
    [[ "$rotated_posts" == 1 ]] || { ok=0; echo "rotated key not pushed ($rotated_posts): $rotated" >&2; }
    [[ "$state_mode" == 600 ]] || { ok=0; echo "state file mode $state_mode" >&2; }
    [[ "$leaked" == 0 ]] || { ok=0; echo "raw key in the state file" >&2; }
    if (( ok )); then pass; else fail "a rotated key never reaches the app"; fi
fi

# The push used to run as `... | grep -v skipped || true`, which swallowed a
# failing sync (exit 2) under pipefail (review finding 2026-09-25).
if it "svc: start-stack reports a failing profile push instead of hiding it"; then
    block="$(sed -n '/^    openhands)/,/^        ;;/p' configuration/start-stack.sh)"
    ok=1
    [[ "$block" == *"skipped (up to date)\$' || true"* ]] && [[ "$block" != *"push_rc"* ]] && { ok=0; echo "push failure still swallowed" >&2; }
    [[ "$block" == *'push_rc=$?'* ]] || { ok=0; echo "push exit code not kept" >&2; }
    [[ "$block" == *'tier-profile push failed'* ]] || { ok=0; echo "no warning on a failed push" >&2; }
    if (( ok )); then pass; else fail "a failed profile push is hidden"; fi
fi

if it "svc: profile push skips cleanly when no app answers"; then
    d="$(mktemp -d)"
    out="$(env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" \
        --push-url http://127.0.0.1:1 2>&1)"; rc=$?
    rm -rf "$d"
    if [[ $rc -eq 0 && "$out" == *"push skipped"* ]]; then pass; else fail "rc=$rc $out"; fi
fi

# The browser UI shows the AI services live and offers the setup actions that
# already exist. Payload and action mapping are tested through an injected
# probe and runner: nothing is probed on this machine, nothing is started.
_svc_serve_py() {
    python3 - <<'PY'
import importlib.util, json, pathlib, sys, tempfile
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-serve-"))
spec = importlib.util.spec_from_file_location("autoos_serve", "lib/linux/serve.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["serve.py", str(root), "0", "127.0.0.1", "0"]
spec.loader.exec_module(mod)
codes = {20128: 200, 4000: 0, 4096: 401, 3000: 200}
status = mod.service_status(probe=lambda port, path: codes[port])
by = {s["id"]: s for s in status}
problems = []
for sid in ("omniroute", "litellm", "opencode", "openhands"):
    s = by.get(sid)
    if not s:
        problems.append("missing:" + sid); continue
    for k in ("name", "port", "bind", "auth", "health", "up", "actions"):
        if k not in s: problems.append(sid + ":no-" + k)
up = {k: v["up"] for k, v in by.items()}
if up != {"omniroute": True, "litellm": False, "opencode": True, "openhands": True}:
    problems.append("up=%s" % up)
if by["litellm"]["bind"] != "127.0.0.1": problems.append("litellm-bind")
if by["opencode"]["bind"] != "0.0.0.0": problems.append("opencode-bind")
if "apply-dry-run" not in by["omniroute"]["actions"]: problems.append("no-apply-dry-run")
if "start-openhands" not in by["openhands"]["actions"]: problems.append("no-start-openhands")
if "start-opencode-serve" not in by["opencode"]["actions"]: problems.append("no-start-serve")
# every advertised action is in the allowlist, with a repo script behind it
for s in status:
    for a in s["actions"]:
        if a not in mod.SERVICE_ACTIONS: problems.append("unlisted:" + a)
for key, act in mod.SERVICE_ACTIONS.items():
    argv = act["argv"]
    if argv[0] != "bash" or not (pathlib.Path("lib/linux/serve.py").resolve().parents[2] / argv[1]).is_file():
        problems.append("not-a-repo-script:" + key)
if mod.SERVICE_ACTIONS["apply-dry-run"]["live"] or "--dry-run" not in mod.SERVICE_ACTIONS["apply-dry-run"]["argv"]:
    problems.append("dry-run-action-is-live")
# the request side: unknown refused, live refused under --dry-run serving
ran = []
mod.start_service_action = lambda key: ran.append(key)
code, _ = mod.service_action_response({"action": "rm -rf /"})
if code != 400: problems.append("unknown-action=%s" % code)
code, _ = mod.service_action_response({"action": "apply-dry-run"})
if code != 202 or ran != ["apply-dry-run"]: problems.append("dry-run-not-started:%s" % code)
mod.FORCE_DRY = True
code, _ = mod.service_action_response({"action": "start-openhands"})
if code != 409 or ran != ["apply-dry-run"]: problems.append("live-action-under-dry-serve:%s" % code)
print(" ".join(problems) or "ok")
PY
}

if it "svc: the web server reports every AI service and maps actions to an allowlist"; then
    assert_eq "$(_svc_serve_py 2>&1 | tail -n 1)" "ok"
fi

if it "svc: the web UI shows service status and confirms every live action"; then
    ok=1
    grep -q 'api("/api/services")' web/index.html || { ok=0; echo "no status fetch" >&2; }
    grep -q '/api/services/action' web/index.html || { ok=0; echo "no action call" >&2; }
    body="$(sed -n '/^async function runServiceAction/,/^}/p' web/index.html)"
    [[ "$body" == *"confirm("* ]] || { ok=0; echo "live action without confirm" >&2; }
    grep -q 'id="services"' web/index.html || { ok=0; echo "no services list" >&2; }
    grep -q 'backend apply/switch not yet wired' web/index.html && { ok=0; echo "stale subtitle" >&2; }
    if (( ok )); then pass; else fail "service status/actions not wired in the page"; fi
fi

# The page is shared: the Windows server must answer the same routes.
if it "svc: the Windows server answers the same service routes"; then
    ok=1
    grep -q "'/api/services'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "no GET route" >&2; }
    grep -q "'/api/services/action'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "no POST route" >&2; }
    for a in apply-dry-run apply start-openhands start-opencode-serve resume-stack; do
        grep -q "'$a'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "missing action $a" >&2; }
    done
    if (( ok )); then pass; else fail "Windows server lacks the service routes"; fi
fi

if it "svc: docs/web-services.md lists every service port and no real host"; then
    ok=1
    for p in 20128 4096 3000 4000 8777 8080 8090 9000 9001 9121 24282 8199; do
        grep -q "| $p |" docs/web-services.md || { ok=0; echo "port $p missing" >&2; }
    done
    if grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' docs/web-services.md | grep -qvE '^(0\.0\.0\.0|127\.0\.0\.1)$'; then
        ok=0; echo "a real IP in the doc" >&2
    fi
    grep -qE '[a-z0-9-]\.(com|net|org|de)\b' docs/web-services.md && { ok=0; echo "a real domain in the doc" >&2; }
    if (( ok )); then pass; else fail "web-services.md incomplete or leaking"; fi
fi

# ─── Docker AI stack (server profile, lane S) ──────────────────────────────
# Every test name carries "aistack" so `--filter aistack` reaches all of them.
# Nothing here starts a container: docker and systemctl are stubs that log.
AISTACK="$ROOT/configuration/docker/ai-stack"

_aistack_sandbox() {
    local d t
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/home/.config/opencode" "$d/repo" "$d/code"
    # Stateful stand-ins (tests/helpers/aistack_fake.sh): docker, systemctl,
    # ss, curl, sleep, the omniroute CLI, register-autostart.sh and
    # start-stack.sh answer from files in $d and log their arguments. Nothing
    # reaches the daemon, the user manager or the network.
    for t in docker systemctl ss curl sleep omniroute register start-stack; do
        printf '#!/bin/sh\nexec bash "%s/tests/helpers/aistack_fake.sh" "%s" %s "$@"\n' "$ROOT" "$d" "$t" >"$d/bin/$t"
        chmod +x "$d/bin/$t"
    done
    mv "$d/bin/systemctl" "$d/bin/fake-systemctl"
    mv "$d/bin/register" "$d/bin/fake-register"
    mv "$d/bin/start-stack" "$d/bin/fake-start-stack"
    printf 'omniroute: sk-test-client-key\n' >"$d/repo/api-keys.yml"
    printf '%s' "$d"
}
# _aistack <sandbox> [NAME=value...] <ai-stack.sh args...>
_aistack() {
    local d="$1" extra=()
    shift
    while (( $# )) && [[ "$1" == [A-Z]*=* ]]; do extra+=("$1"); shift; done
    env -u AUTOOS_OMNIROUTE_KEY -u OMNIGRAPH_TOKEN -u AUTOOS_OPENHANDS_SANDBOX_URL -u AUTOOS_OPENHANDS_WEB_HOST \
        -u AUTOOS_AI_STACK_MIGRATING -u OMNIROUTE_API_KEY -u AUTOOS_STACK_BIND -u AUTOOS_STACK_ALLOW_LAN \
        -u AUTOOS_CURL -u AUTOOS_VERIFY_PUBLIC_URLS -u AUTOOS_VERIFY_COMBOS -u COMPOSE_PROFILES -u AUTOOS_STACK_DATA -u AUTOOS_OMNIROUTE_PUBLIC_URL \
        HOME="$d/home" PATH="$d/bin:$PATH" AUTOOS_DOCKER="$d/bin/docker" AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" \
        AUTOOS_AI_STACK_CONFIG="$d/cfg" AUTOOS_AI_STACK_DATA="$d/data" AUTOOS_CODE_DIR="$d/code" \
        AUTOOS_KEYS_FILE="$d/repo/api-keys.yml" AUTOOS_LITELLM_DIR="$d/repo" AUTOOS_OMNIROUTE_HOME="$d/home/.omniroute" \
        AUTOOS_OPENHANDS_DIR="$d/home/.openhands" XDG_CONFIG_HOME="$d/home/.config" \
        AUTOOS_REGISTER_AUTOSTART="$d/bin/fake-register" AUTOOS_START_STACK="$d/bin/fake-start-stack" \
        "${extra[@]}" bash "${_AISTACK_SH:-$AISTACK/ai-stack.sh}" "$@" 2>&1
}
# _aistack_native <sandbox>: a host before the move - both units registered
# and active, a gateway state dir, the firewall unit up, images present.
_aistack_native() {
    local d="$1" u
    for u in autoos-omniroute autoos-opencode coding-agents-fw; do : >"$d/unit-$u"; : >"$d/active-$u"; done
    mkdir -p "$d/home/.omniroute"
    printf 'STORAGE_ENCRYPTION_KEY=test\n' >"$d/home/.omniroute/.env"
    printf 'native-db\n' >"$d/home/.omniroute/storage.sqlite"
    : >"$d/image-exists"
}
# _aistack_migrated <sandbox>: a host after a completed migrate.
_aistack_migrated() {
    local d="$1" c
    mkdir -p "$d/cfg" "$d/data/omniroute"
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    for c in autoos-omniroute autoos-opencode openhands-app; do : >"$d/run-$c"; : >"$d/compose-$c"; done
    : >"$d/unit-coding-agents-fw"; : >"$d/active-coding-agents-fw"
    printf 'container-db\n' >"$d/data/omniroute/storage.sqlite"
}
# _aistack_seq <log> <needle...>: every needle appears, in this order.
_aistack_seq() {
    python3 - "$@" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read() if sys.argv[1] != "-" else sys.stdin.read()
at = 0
for needle in sys.argv[2:]:
    i = text.find(needle, at)
    if i < 0:
        print("missing (in order): %r after offset %d" % (needle, at), file=sys.stderr)
        sys.exit(1)
    at = i + len(needle)
PY
}

if it "aistack: compose template keeps the hardening contract"; then
    out="$(python3 "$ROOT/tests/helpers/check_compose.py" "$AISTACK/compose.yml" 2>&1)" && rc=0 || rc=$?
    if (( rc == 0 )); then pass; else fail "$out"; fi
fi

if it "aistack: the opencode layer builds on a digest-pinned V2 image, never V1"; then
    ok=1
    f="$AISTACK/opencode.Dockerfile"
    grep -qE '^FROM ghcr\.io/anomalyco/opencode:2\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$' "$f" || { ok=0; echo "FROM not pinned V2" >&2; }
    grep -q 'opencode-ai' "$f" && { ok=0; echo "V1 package referenced" >&2; }
    grep -qE '^RUN apk add --no-cache .*\bgit\b.*\bbash\b.*\bnodejs\b' "$f" || { ok=0; echo "agent tools missing" >&2; }
    if (( ok )); then pass; else fail "opencode layer is not pinned V2"; fi
fi

if it "aistack: the env example carries no real value"; then
    ok=1
    [[ -f "$AISTACK/stack.env.example" ]] || { ok=0; echo "missing" >&2; }
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        v="${line#*=}"
        [[ -z "$v" || "$v" == REPLACE_WITH_* || "$v" =~ ^[0-9]+[mg]?$ || "$v" == 0.0.0.0 || "$v" == /* ]] \
            || { ok=0; echo "suspicious: $line" >&2; }
        [[ "$v" == /home/* ]] && { ok=0; echo "home path: $line" >&2; }
    done <"$AISTACK/stack.env.example"
    if (( ok )); then pass; else fail "stack.env.example must stay generic"; fi
fi

if it "aistack: --dry-run init prints the plan and writes nothing"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" --dry-run init)"
    ok=1
    [[ -e "$d/cfg" || -e "$d/data" ]] && { ok=0; echo "created a directory" >&2; }
    [[ "$out" == *"dry run"* ]] || { ok=0; echo "no dry-run banner" >&2; }
    [[ "$out" == *"would write $d/cfg/stack.env"* ]] || { ok=0; echo "stack.env not announced: $out" >&2; }
    [[ "$out" == *"would write $d/cfg/opencode.env"* ]] || { ok=0; echo "opencode.env not announced" >&2; }
    [[ "$out" == *"sk-test-client-key"* ]] && { ok=0; echo "printed a key" >&2; }
    grep -qE 'compose|run|pull|build' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dry run had side effects"; fi
fi

if it "aistack: init twice: 0600 env files, the second run skips, user values survive"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"
    printf '# mine\nOPENCODE_PASSWORD=operator-chosen\nMY_EXTRA=keep\n' >"$d/cfg/opencode.env"
    first="$(_aistack "$d" init)"
    second="$(_aistack "$d" init)"
    ok=1
    for f in stack.env opencode.env openhands.env; do
        [[ "$(stat -c %a "$d/cfg/$f")" == 600 ]] || { ok=0; echo "$f not 600" >&2; }
    done
    grep -q '^OPENCODE_PASSWORD=operator-chosen$' "$d/cfg/opencode.env" || { ok=0; echo "password overwritten" >&2; }
    grep -q '^MY_EXTRA=keep$' "$d/cfg/opencode.env" || { ok=0; echo "extra line lost" >&2; }
    grep -q '^# mine$' "$d/cfg/opencode.env" || { ok=0; echo "comment lost" >&2; }
    grep -q "^AUTOOS_OMNIROUTE_KEY='sk-test-client-key'$" "$d/cfg/opencode.env" || { ok=0; echo "client key not added" >&2; }
    grep -q "^LLM_API_KEY='sk-test-client-key'$" "$d/cfg/openhands.env" || { ok=0; echo "openhands key missing" >&2; }
    compgen -G "$d/cfg/opencode.env.autoos-backup-*" >/dev/null || { ok=0; echo "no backup of the user's file" >&2; }
    grep -q "^AUTOOS_CODE_DIR='$d/code'$" "$d/cfg/stack.env" || { ok=0; echo "code dir not recorded" >&2; }
    [[ -d "$d/data/omniroute" && -d "$d/data/opencode-home" ]] || { ok=0; echo "data dirs missing" >&2; }
    [[ "$second" == *"(skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ "* ]] && { ok=0; echo "second run changed something: $second" >&2; }
    [[ "$first$second" == *"sk-test-client-key"* ]] && { ok=0; echo "printed a key" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "init is not idempotent read-modify-write"; fi
fi

# Same class as lib/linux/install.sh backup_path/backup_file (main fixed
# this there): ensure_env_file's own backup used a plain <file>.autoos-
# backup-<stamp> name with a bare cp, so two changed writes to the same env
# file in the same second let the second cp overwrite the first backup and
# destroy it. Pin `date` on PATH so both writes land in the same "second".
if it "backup residual: aistack backs up stack.env twice in one second without overwriting"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"
    printf '#!/bin/sh\necho 20260101-000000\n' >"$d/bin/date"; chmod +x "$d/bin/date"
    printf '# mine\nMY_EXTRA=keep\n' >"$d/cfg/stack.env"
    contentA="$(cat "$d/cfg/stack.env")"
    _aistack "$d" init >/dev/null
    sed -i '/^AUTOOS_UID=/d' "$d/cfg/stack.env"
    contentB="$(cat "$d/cfg/stack.env")"
    _aistack "$d" init >/dev/null
    ok=1
    base="$d/cfg/stack.env.autoos-backup-20260101-000000"
    [[ -f "$base" ]] || { ok=0; echo "no first backup at the plain stamp name" >&2; }
    [[ -f "$base-1" ]] || { ok=0; echo "no second backup (overwrote the first?)" >&2; }
    [[ "$(cat "$base" 2>/dev/null)" == "$contentA" ]] || { ok=0; echo "first backup content wrong" >&2; }
    [[ "$(cat "$base-1" 2>/dev/null)" == "$contentB" ]] || { ok=0; echo "second backup content wrong" >&2; }
    [[ "$(find "$d/cfg" -maxdepth 1 -name 'stack.env.autoos-backup-*' | wc -l | tr -d ' ')" == 2 ]] \
        || { ok=0; echo "expected exactly 2 backups" >&2; }
    grep -q '^AUTOOS_UID=' "$d/cfg/stack.env" || { ok=0; echo "final file missing the re-added key" >&2; }
    grep -q '^MY_EXTRA=keep$' "$d/cfg/stack.env" || { ok=0; echo "user's extra line lost" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "ai-stack overwrote a same-second stack.env backup"; fi
fi

# ensure_env_file returns 1 when its backup copy fails, but cmd_init's three
# call sites ignored that answer. errexit is no help: up and migrate call
# `cmd_init || return 1`, which suppresses set -e for the whole call, so the
# failed backup was swallowed and up went on to start containers on a
# half-written config. Each call site now passes the failure on - up must
# stop before docker is touched at all (backup_fail_bin breaks the copy;
# the firewall flag lets the 0.0.0.0 bind init writes pass its guard).
if it "backup residual: aistack up fails before docker when init cannot back up an env file"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"
    printf '# operator\nMY_EXTRA=keep\n' >"$d/cfg/opencode.env"
    cp "$d/cfg/opencode.env" "$d/cfg/opencode.env.orig"
    : >"$d/active-coding-agents-fw"; : >"$d/image-exists"
    bin="$(backup_fail_bin)"
    out="$(PATH="$bin:$PATH" _aistack "$d" up omniroute)"; rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "up continued after a failed backup: rc=$rc" >&2; }
    [[ "$out" == *"could not back up $d/cfg/opencode.env"* ]] || { ok=0; echo "no warning naming the file: ${out:0:300}" >&2; }
    cmp -s "$d/cfg/opencode.env" "$d/cfg/opencode.env.orig" || { ok=0; echo "the env file was modified without a backup" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    [[ ! -e "$d/docker.log" ]] || { ok=0; echo "docker was driven although init had failed" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "a failed init backup does not stop up"; fi
fi

# ai-stack.sh ensure_env_file returns 1 when its backup copy fails: init
# must stop there and leave the env file alone.
if it "backup residual: aistack init leaves the env file in place when its backup copy fails"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"
    printf '# operator\nMY_EXTRA=keep\n' >"$d/cfg/opencode.env"
    cp "$d/cfg/opencode.env" "$d/cfg/opencode.env.orig"
    bin="$(backup_fail_bin)"
    out="$(PATH="$bin:$PATH" _aistack "$d" init 2>&1)"; rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "init succeeded without a backup: rc=$rc" >&2; }
    [[ "$out" == *"could not back up $d/cfg/opencode.env"* ]] || { ok=0; echo "no warning naming the file: ${out:0:300}" >&2; }
    cmp -s "$d/cfg/opencode.env" "$d/cfg/opencode.env.orig" || { ok=0; echo "the env file was modified without a backup" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "aistack init edits an env file it could not back up"; fi
fi

# replace_dir_with_copy's own aside= (moving a non-empty dest out of the way
# before the swap) used <dest>.autoos-backup-<ts>, then a single -$$ escape
# if that name was taken - safe against a second SEPARATE process (a
# different PID) but not against a second PRE-EXISTING collision at that
# exact name. Extracted in isolation (sourcing the whole script would run its
# case-driven CLI dispatch and exit this test shell): pre-seed both names an
# old run could have left and confirm the previous dest still lands under a
# genuinely free name, never inside either.
if it "backup residual: aistack replace_dir_with_copy never lands inside a taken aside name"; then
    d="$(mktemp -d)"
    src="$d/src"; dest="$d/dest"
    mkdir -p "$src" "$dest"
    printf 'new\n' >"$src/data"
    printf 'old\n' >"$dest/data"
    ts="20260101-000000"
    base="$dest.autoos-backup-$ts"
    mkdir -p "$base"; printf 'sentinelA\n' >"$base/marker"
    mkdir -p "$base-$$"; printf 'sentinelB\n' >"$base-$$/marker"
    fn="$d/fn.sh"
    sed -n '/^autoos_backup_path()/,/^}/p;/^replace_dir_with_copy()/,/^}/p' "$AISTACK/ai-stack.sh" >"$fn"
    # shellcheck disable=SC1090  # $fn is a scratch fixture generated above, not a repo file
    ( . "$fn"; replace_dir_with_copy "$src" "$dest" "$ts" ) >/dev/null 2>&1; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "replace_dir_with_copy failed, rc=$rc" >&2; }
    [[ "$(cat "$dest/data" 2>/dev/null)" == "new" ]] || { ok=0; echo "dest not swapped to the new content" >&2; }
    [[ "$(cat "$base/marker" 2>/dev/null)" == "sentinelA" ]] || { ok=0; echo "the first taken aside name was overwritten" >&2; }
    [[ "$(cat "$base-$$/marker" 2>/dev/null)" == "sentinelB" ]] || { ok=0; echo "the second taken aside name (this process's PID) was overwritten" >&2; }
    aside_dir=""
    for cand in "$d"/dest.autoos-backup-"$ts"*; do
        [[ "$cand" == "$base" || "$cand" == "$base-$$" ]] && continue
        [[ -f "$cand/data" ]] && aside_dir="$cand"
    done
    [[ -n "$aside_dir" && "$(cat "$aside_dir/data" 2>/dev/null)" == "old" ]] \
        || { ok=0; echo "the previous dest did not land under a third, genuinely free name" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "replace_dir_with_copy's aside collided with an existing name"; fi
fi

if it "aistack: init reuses the pinned opencode-serve password so phone logins survive"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/home/.config/autoos"
    printf 'phone-pass\n' >"$d/home/.config/autoos/opencode-serve.password"
    _aistack "$d" init >/dev/null
    if grep -q "^OPENCODE_PASSWORD='phone-pass'$" "$d/cfg/opencode.env"; then pass; else fail "password not carried over"; fi
    rm -rf "$d"
fi

if it "aistack: the container opencode config reaches the gateway by name"; then
    d="$(mktemp -d)"
    mkdir -p "$d/code/repo"
    cat >"$d/src.json" <<JSON
{"model":"omniroute/t1-orchestrator",
 "provider":{"omniroute":{"options":{"baseURL":"http://127.0.0.1:20128/v1","apiKey":"{env:AUTOOS_OMNIROUTE_KEY}"}},
             "litellm":{"options":{"baseURL":"http://127.0.0.1:4000/v1"}},
             "ollama":{"options":{"baseURL":"http://127.0.0.1:11434/v1"}},
             "meta":{"options":{"baseURL":"https://api.example.invalid/v1","apiKey":"{env:META_API_KEY}"}}},
 "providers":{"omniroute":{"settings":{"baseURL":"http://localhost:20128/v1"}},"litellm":{"settings":{"baseURL":"http://127.0.0.1:4000/v1"}}},
 "mcp":{"serena":{"type":"local","command":["uvx","serena"],"enabled":true},
        "omnigraph":{"type":"local","command":["npx","-y","x"],"enabled":true,"environment":{"OMNIGRAPH_BASE_URL":"http://localhost:8080"}},
        "playwright":{"type":"local","command":["npx","-y","@playwright/mcp"],"enabled":true},
        "graphify":{"type":"local","command":["uvx","graphify"],"enabled":true}},
 "instructions":["$d/code/repo/AGENTS.md","/elsewhere/SKILL.md"]}
JSON
    out1="$(python3 "$ROOT/tools/render-opencode-container-config.py" --source "$d/src.json" --out "$d/out.json" --code-dir "$d/code" 2>&1)"
    out2="$(python3 "$ROOT/tools/render-opencode-container-config.py" --source "$d/src.json" --out "$d/out.json" --code-dir "$d/code" 2>&1)"
    got="$(python3 - "$d/out.json" "$d/code" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
p, v2, m = c["provider"], c["providers"], c["mcp"]
print(p["omniroute"]["options"]["baseURL"], v2["omniroute"]["settings"]["baseURL"],
      sorted(p), sorted(v2), m["serena"].get("type"), m["serena"].get("url"),
      m["omnigraph"]["environment"]["OMNIGRAPH_BASE_URL"], m["playwright"]["enabled"],
      m["graphify"]["enabled"], [i.replace(sys.argv[2], "CODE") for i in c["instructions"]],
      p["omniroute"]["options"]["apiKey"])
PY
)"
    assert_eq "$got|$([[ "$out1" == *"litellm"* && "$out2" == *"unchanged"* ]] && echo reported)" \
        "http://omniroute:20128/v1 http://omniroute:20128/v1 ['meta', 'omniroute'] ['omniroute'] remote http://serena-mcp:9121/sse http://host.docker.internal:8080 False True ['CODE/repo/AGENTS.md'] {env:AUTOOS_OMNIROUTE_KEY}|reported"
    rm -rf "$d"
fi

if it "aistack: server profile runs the docker stack, workstation keeps the native installs"; then
    got="$(python3 - <<'PY'
import json
comps = {c["id"]: c for g in json.load(open("catalog/linux.json", encoding="utf-8"))["categories"] for c in g["components"]}
s = comps.get("ai-stack-docker", {})
out = [
    "server" in s.get("profiles", []),
    "workstation" not in s.get("profiles", []),
    "docker" in s.get("requires", []),
    s.get("provider") == "custom" and s.get("postInstall") == "install_ai_stack",
    # The host CLI stays: apply.sh manages the container through it. The
    # docker run OpenHands (a :latest pull) is replaced by the compose one.
    "server" in comps["omniroute"]["profiles"],
    "server" not in comps["openhands-docker"]["profiles"],
    "workstation" in comps["omniroute"]["profiles"],
    "workstation" in comps["openhands-docker"]["profiles"],
]
print(" ".join(str(x) for x in out))
PY
)"
    assert_eq "$got" "True True True True True True True True"
fi

if it "aistack: installer announces the plan in dry run and detects the running stack"; then
    d="$(_aistack_sandbox)"
    ok=1
    out="$( ( AUTOOS_DRY_RUN=1; AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; AUTOOS_AI_STACK_DATA="$d/data"
              HOME="$d/home"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG AUTOOS_AI_STACK_DATA HOME; install_ai_stack ) 2>&1)"
    [[ "$out" == *"would write"*"stack.env"* ]] || { ok=0; echo "plan not shown: $out" >&2; }
    [[ -e "$d/cfg" ]] && { ok=0; echo "dry run wrote config" >&2; }
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) && { ok=0; echo "detected without a container" >&2; }
    # A leftover gateway container (a failed migrate) is not an installed stack.
    mkdir -p "$d/cfg"; : >"$d/cfg/stack.env"; : >"$d/container-exists"; : >"$d/compose-autoos-omniroute"
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) && { ok=0; echo "a stopped leftover container read as installed" >&2; }
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) || { ok=0; echo "running stack not detected" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "installer dry run / detection is wrong"; fi
fi

if it "aistack: migrate without --yes is the announced plan and touches nothing"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/home/.omniroute"; printf 'STORAGE_ENCRYPTION_KEY=x\n' >"$d/home/.omniroute/.env"
    out="$(_aistack "$d" migrate)"
    ok=1
    # Order is the contract: refuse early, stop (quiescent DB), back up, copy,
    # start, prove - every service - then claim (marker) and only then
    # disable the native units.
    python3 - "$out" <<'PY' || ok=0
import sys
out = sys.argv[1]
steps = ["refuses", "stop the autoos-omniroute unit", "back up", "copy", "start the omniroute container",
         "answers", "autoos-opencode", "openhands", "stack.active", "unregister autoos-omniroute"]
pos = [out.find(s) for s in steps]
if -1 in pos or pos != sorted(pos):
    print("plan order wrong:", list(zip(steps, pos)), file=sys.stderr)
    sys.exit(1)
PY
    [[ "$out" == *"--yes"* ]] || { ok=0; echo "no hint how to run it" >&2; }
    [[ -e "$d/data" || -e "$d/cfg" ]] && { ok=0; echo "created state" >&2; }
    [[ -e "$d/systemctl.log" ]] && { ok=0; echo "called systemctl" >&2; }
    grep -qE 'compose|rm|stop' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate plan is wrong or had side effects"; fi
fi

if it "aistack: rollback without --yes is the announced plan and touches nothing"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" rollback)"
    ok=1
    [[ "$out" == *"register-autostart.sh"* && "$out" == *"--yes"* ]] || { ok=0; echo "plan: $out" >&2; }
    [[ -e "$d/systemctl.log" ]] && { ok=0; echo "called systemctl" >&2; }
    grep -qE 'compose' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker compose" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "rollback plan is wrong or had side effects"; fi
fi

if it "aistack: register-autostart leaves the gateway and opencode units alone once docker owns them"; then
    d="$(_svc_reg_sandbox)"
    s="$(_aistack_sandbox)"
    _aistack_migrated "$s"
    out="$(AUTOOS_DOCKER="$s/bin/docker" AUTOOS_AI_STACK_CONFIG="$s/cfg" _svc_reg "$d")"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" || -e "$d/units/autoos-opencode.service" ]] && { ok=0; echo "wrote a native unit" >&2; }
    [[ -e "$d/units/autoos-litellm.service" ]] || { ok=0; echo "litellm unit should still be written" >&2; }
    [[ "$out" == *"docker AI stack"* ]] || { ok=0; echo "not explained: $out" >&2; }
    rm -rf "$d" "$s"
    if (( ok )); then pass; else fail "register-autostart would start a second gateway"; fi
fi

if it "aistack: the resume script brings up the compose stack instead of the native units"; then
    ok=1
    f="$ROOT/configuration/autostart/Start-AutoOSStack.sh"
    grep -q 'configuration/docker/ai-stack/ai-stack.sh' "$f" || { ok=0; echo "stack script not referenced" >&2; }
    grep -q '"$AI_STACK" is-active' "$f" || { ok=0; echo "no is-active branch" >&2; }
    grep -q '"$AI_STACK" up' "$f" || { ok=0; echo "no compose resume" >&2; }
    if (( ok )); then pass; else fail "Start-AutoOSStack.sh ignores the docker stack"; fi
fi

if it "aistack: apply.sh manages a containerised gateway with the manage key, never a host restart"; then
    ok=1
    f="$ROOT/configuration/omniroute/apply.sh"
    grep -q 'manage.key' "$f" || { ok=0; echo "manage key not used" >&2; }
    grep -q '"$AI_STACK" is-active' "$f" || { ok=0; echo "no stack detection" >&2; }
    grep -q '"$AI_STACK" up omniroute' "$f" || { ok=0; echo "a down container gateway is not resumed" >&2; }
    if (( ok )); then pass; else fail "apply.sh cannot manage the container"; fi
fi

# ─── Review findings 2026-09-25 (gemini-3.8-flash + deepseek-v4.1-flash) ────
# migrate/rollback/up run for real here, against the stateful fakes above.

if it "aistack: is-active needs the completed-migration marker, not a leftover container"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/compose-autoos-omniroute"; : >"$d/container-exists"
    ok=1
    _aistack "$d" is-active >/dev/null && { ok=0; echo "a stopped leftover container reads as active" >&2; }
    : >"$d/run-autoos-omniroute"
    _aistack "$d" is-active >/dev/null && { ok=0; echo "a running container without the marker reads as active" >&2; }
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    _aistack "$d" is-active >/dev/null || { ok=0; echo "the marker does not make the stack active" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "is-active trusts a container instead of the marker"; fi
fi

if it "aistack: a failed gateway move removes the container and hands :20128 back"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/unhealthy-omniroute"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    grep -q 'rm -s -f omniroute' "$d/docker.log" || { ok=0; echo "failed container not removed: $(cat "$d/docker.log")" >&2; }
    [[ -e "$d/compose-autoos-omniroute" || -e "$d/run-autoos-omniroute" ]] && { ok=0; echo "container left behind" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/unit-autoos-omniroute" ]] || { ok=0; echo "native gateway not back" >&2; }
    [[ -e "$d/active-autoos-opencode" ]] || { ok=0; echo "opencode was touched" >&2; }
    grep -q 'up -d --no-deps opencode' "$d/docker.log" && { ok=0; echo "went on to opencode" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    _aistack "$d" is-active >/dev/null && { ok=0; echo "reads as active afterwards" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a failed migrate leaves a container that reads as active"; fi
fi

if it "aistack: rollback replaces ~/.omniroute with the container state, no stale WAL survives"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    mkdir -p "$d/home/.omniroute"
    printf 'old-db\n' >"$d/home/.omniroute/storage.sqlite"
    printf 'stale-wal\n' >"$d/home/.omniroute/storage.sqlite-wal"
    printf 'stale-shm\n' >"$d/home/.omniroute/storage.sqlite-shm"
    out="$(_aistack "$d" rollback --yes)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rollback failed: $out" >&2; }
    [[ "$(cat "$d/home/.omniroute/storage.sqlite" 2>/dev/null)" == container-db ]] || { ok=0; echo "DB not restored" >&2; }
    [[ -e "$d/home/.omniroute/storage.sqlite-wal" || -e "$d/home/.omniroute/storage.sqlite-shm" ]] \
        && { ok=0; echo "stale WAL/SHM kept next to the restored DB" >&2; }
    backup="$(compgen -G "$d/data/backups/omniroute-native-*.tar.gz" | head -n1)"
    [[ -n "$backup" ]] && tar -tzf "$backup" | grep -q 'storage.sqlite-wal' || { ok=0; echo "no full backup" >&2; }
    aside="$(compgen -G "$d/home/.omniroute.autoos-backup-*" | head -n1)"
    [[ -n "$aside" && -e "$aside/storage.sqlite-wal" ]] || { ok=0; echo "previous dir not kept aside" >&2; }
    compgen -G "$d/home/.omniroute.autoos-staging-*" >/dev/null && { ok=0; echo "staging dir left" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker kept after rollback" >&2; }
    grep -q -- '--only autoos-omniroute,autoos-opencode' "$d/register.log" || { ok=0; echo "units not re-registered" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "rollback overlays the old gateway dir"; fi
fi

if it "aistack: migrate stops nothing when the manage key cannot be created"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/omni-key-fails"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate went on without a manage key" >&2; }
    [[ "$out" == *"manage.key"* ]] || { ok=0; echo "no hint: $out" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "stopped a unit" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started a container" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/active-autoos-opencode" ]] || { ok=0; echo "native service down" >&2; }
    [[ -e "$d/cfg/manage.key" ]] && { ok=0; echo "empty key file written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate without a manage key"; fi
fi

if it "aistack: migrate refuses a second run once migrated and points at rollback"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    mkdir -p "$d/home/.omniroute"; printf 'stale-host-db\n' >"$d/home/.omniroute/storage.sqlite"
    printf 'sk-manage\n' >"$d/cfg/manage.key"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    plan="$(_aistack "$d" migrate)"
    ok=1
    (( rc != 0 )) || { ok=0; echo "second migrate ran" >&2; }
    [[ "$out" == *"rollback --yes"* ]] || { ok=0; echo "no rollback hint: $out" >&2; }
    [[ "$plan" == *"already migrated"* ]] || { ok=0; echo "plan does not say so" >&2; }
    [[ "$(cat "$d/data/omniroute/storage.sqlite")" == container-db ]] || { ok=0; echo "container state overwritten" >&2; }
    grep -qE 'compose .*(up|stop|rm)' "$d/docker.log" 2>/dev/null && { ok=0; echo "touched containers" >&2; }
    [[ -e "$d/systemctl.log" ]] && grep -qE 'stop|start' "$d/systemctl.log" && { ok=0; echo "touched units" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate would overwrite newer container state"; fi
fi

if it "aistack: migrate disables the native units only after all three services answered"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/exists-openhands-app"; : >"$d/run-openhands-app"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "migrate failed: $out" >&2; }
    _aistack_seq "$d/events.log" "systemctl: --user stop autoos-omniroute.service" "docker: compose" "up -d --no-deps omniroute" \
        "systemctl: --user stop autoos-opencode.service" "up -d --no-deps opencode" "docker: rm -f openhands-app" \
        "start-stack: openhands" "register: --unregister --only autoos-omniroute,autoos-opencode" || ok=0
    [[ "$(cat "$d/start-stack.log")" == *"migrating=1"* ]] || { ok=0; echo "openhands not started as the compose service" >&2; }
    [[ -e "$d/cfg/stack.active" ]] || { ok=0; echo "no marker after a completed migrate" >&2; }
    [[ "$(cat "$d/data/omniroute/storage.sqlite")" == native-db ]] || { ok=0; echo "state not copied" >&2; }
    [[ "$(stat -c %a "$(compgen -G "$d/data/backups/omniroute-*.tar.gz" | head -n1)")" == 600 ]] || { ok=0; echo "backup not 0600" >&2; }
    _aistack "$d" is-active >/dev/null || { ok=0; echo "not active after migrate" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate order is wrong"; fi
fi

if it "aistack: an opencode failure during migrate hands the gateway back too"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/fail-up-opencode"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    grep -q 'rm -s -f omniroute' "$d/docker.log" || { ok=0; echo "gateway container kept" >&2; }
    grep -q 'rm -s -f opencode' "$d/docker.log" || { ok=0; echo "opencode container kept" >&2; }
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "a unit was unregistered" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a late failure leaves the gateway in docker"; fi
fi

if it "aistack: an openhands failure during migrate rolls the whole stack back to native"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/exists-openhands-app"; : >"$d/run-openhands-app"
    : >"$d/fail-up-openhands"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    for s in omniroute opencode openhands; do
        grep -q "rm -s -f $s" "$d/docker.log" || { ok=0; echo "$s container kept" >&2; }
    done
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    # The docker run OpenHands it replaced is recreated (start-stack.sh, native mode).
    _aistack_seq "$d/start-stack.log" "migrating=1" "migrating=0" || ok=0
    [[ -e "$d/exists-openhands-app" ]] || { ok=0; echo "docker run openhands not recreated" >&2; }
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "a unit was unregistered" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an openhands failure leaves a half-migrated host"; fi
fi

if it "aistack: up and migrate refuse a LAN bind unless the firewall unit runs or LAN is allowed"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    _aistack_reset() { rm -f "$d"/run-* "$d"/compose-* "$d/docker.log" "$d/cfg/stack.active"; }
    ok=1
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    out="$(_aistack "$d" up)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "LAN bind without firewall started" >&2; }
    [[ "$out" == *"coding-agents-fw.service"* && "$out" == *"AUTOOS_STACK_ALLOW_LAN=1"* ]] || { ok=0; echo "refusal unexplained: $out" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started containers" >&2; }
    grep -q 'is-active coding-agents-fw.service' "$d/systemctl.log" || { ok=0; echo "firewall unit not checked" >&2; }
    _aistack_reset
    : >"$d/active-coding-agents-fw"
    _aistack "$d" up >/dev/null || { ok=0; echo "refused although the firewall unit is active" >&2; }
    grep -q 'up -d --no-deps omniroute opencode openhands' "$d/docker.log" || { ok=0; echo "not started with the firewall" >&2; }
    _aistack_reset; rm -f "$d/active-coding-agents-fw"
    printf 'AUTOOS_STACK_ALLOW_LAN=1\n' >>"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "explicit AUTOOS_STACK_ALLOW_LAN=1 refused" >&2; }
    _aistack_reset
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "loopback bind refused" >&2; }
    rm -rf "$d"
    # migrate: refused before anything stops.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    rm -f "$d/active-coding-agents-fw"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "migrate ran with an open LAN bind" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "migrate stopped a unit first" >&2; }
    rm -rf "$d"
    unset -f _aistack_reset
    if (( ok )); then pass; else fail "the LAN bind is not guarded"; fi
fi

if it "aistack: the opencode image is rebuilt when its Dockerfile or base image changes"; then
    d="$(_aistack_sandbox)"
    t="$d/tree/configuration"
    mkdir -p "$t/docker/ai-stack"
    cp "$AISTACK/ai-stack.sh" "$AISTACK/compose.yml" "$AISTACK/opencode.Dockerfile" "$t/docker/ai-stack/"
    cp "$ROOT/configuration/env-file.sh" "$t/"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/image-exists"; printf 'stale\n' >"$d/opencode-label"
    ok=1
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    first="$(cat "$d/opencode-label")"
    grep -qE "^build --label org\.autoos\.opencode\.source=[0-9a-f]{64} -t autoos/opencode:" "$d/docker.log" \
        || { ok=0; echo "stale image not rebuilt: $(cat "$d/docker.log")" >&2; }
    rm -f "$d/docker.log" "$d/run-autoos-opencode"
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    grep -q '^build' "$d/docker.log" && { ok=0; echo "rebuilt an up-to-date image" >&2; }
    rm -f "$d/docker.log" "$d/run-autoos-opencode"
    printf '# a changed layer\n' >>"$t/docker/ai-stack/opencode.Dockerfile"
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    grep -q '^build' "$d/docker.log" || { ok=0; echo "a changed Dockerfile was not rebuilt" >&2; }
    [[ "$(cat "$d/opencode-label")" != "$first" ]] || { ok=0; echo "label did not change" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the opencode image never rebuilds"; fi
fi

if it "aistack: init never writes a value with a line break into an env file"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" AUTOOS_OMNIROUTE_KEY=$'sk-leak\nINJECTED=1' OMNIGRAPH_TOKEN=$'tok\rEVIL=1' init)"
    ok=1
    grep -qsE '^(INJECTED|EVIL)=' "$d/cfg/opencode.env" "$d/cfg/openhands.env" && { ok=0; echo "a line break injected a key" >&2; }
    grep -qE '^(AUTOOS_OMNIROUTE_KEY|OMNIGRAPH_TOKEN)=' "$d/cfg/opencode.env" && { ok=0; echo "unsafe value written" >&2; }
    grep -q '^LLM_API_KEY=' "$d/cfg/openhands.env" 2>/dev/null && { ok=0; echo "unsafe value written for openhands" >&2; }
    grep -q $'\r' "$d/cfg/opencode.env" && { ok=0; echo "carriage return written" >&2; }
    [[ "$out" == *"AUTOOS_OMNIROUTE_KEY"*"line break"* && "$out" == *"OMNIGRAPH_TOKEN"* ]] || { ok=0; echo "not warned: $out" >&2; }
    [[ "$out" == *"sk-leak"* || "$out" == *"tok"$'\r'* ]] && { ok=0; echo "printed the value" >&2; }
    grep -q '^OPENCODE_PASSWORD=' "$d/cfg/opencode.env" || { ok=0; echo "the safe keys were not written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an env value with a line break reaches the file"; fi
fi

if it "aistack: apply.sh hands the manage key to the omniroute CLI only"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    printf 'sk-manage-secret\n' >"$d/cfg/manage.key"
    out="$(env -u OMNIROUTE_API_KEY -u AUTOOS_OMNIROUTE_URL HOME="$d/home" PATH="$d/bin:$PATH" \
        AUTOOS_AI_STACK_CONFIG="$d/cfg" AUTOOS_KEYS_FILE="$d/repo/api-keys.yml" \
        bash "$ROOT/configuration/omniroute/apply.sh" --dry-run 2>&1)"
    ok=1
    [[ "$out" == *"manage-scoped key"* ]] || { ok=0; echo "docker mode not detected: $out" >&2; }
    grep -qx omniroute "$d/saw-manage-key" 2>/dev/null || { ok=0; echo "the CLI did not get the key" >&2; }
    grep -qx curl "$d/saw-manage-key" 2>/dev/null && { ok=0; echo "curl inherited the manage key" >&2; }
    [[ "$out" == *"sk-manage-secret"* ]] && { ok=0; echo "printed the key" >&2; }
    grep -qE '^[[:space:]]*export OMNIROUTE_API_KEY' "$ROOT/configuration/omniroute/apply.sh" && { ok=0; echo "exported script-wide" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the manage key leaks into every child process"; fi
fi

if it "aistack: init keeps the data dir and the opencode home private (0700)"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/data/opencode-home"; chmod 755 "$d/data" "$d/data/opencode-home"
    _aistack "$d" init >/dev/null
    got="$(stat -c %a "$d/data" "$d/data/opencode-home" "$d/data/omniroute" | tr '\n' ' ')"
    rm -rf "$d"
    assert_eq "$got" "700 700 700 "
fi

# Re-review 2026-09-25 (cross-company): marker order, recreate guard, the
# effective bind, the pinned FROM the rebuild hash relies on.
if it "aistack: migrate writes the marker before unregistering and drops it on a later failure"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    _aistack "$d" migrate --yes >/dev/null || { ok=0; echo "migrate failed" >&2; }
    # The stand-in logs the call, then the marker state it saw during it.
    grep -A1 -- '--unregister --only autoos-omniroute,autoos-opencode' "$d/register.log" | grep -qx 'marker=yes' \
        || { ok=0; echo "unregistered before the marker existed (rollback would refuse): $(cat "$d/register.log")" >&2; }
    rm -rf "$d"
    # The unregister fails: marker removed again, everything back to native.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/fail-register"
    _aistack "$d" migrate --yes >/dev/null && { ok=0; echo "migrate reported success" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker left after the abort" >&2; }
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    for s in omniroute opencode openhands; do
        grep -q "rm -s -f $s" "$d/docker.log" || { ok=0; echo "$s container kept" >&2; }
    done
    rm -rf "$d"
    # The marker cannot be written: nothing is unregistered, all back to native.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    mkdir -p "$d/cfg/stack.active"
    _aistack "$d" migrate --yes >/dev/null && { ok=0; echo "migrate reported success" >&2; }
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "unregistered without a marker" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/active-autoos-opencode" ]] || { ok=0; echo "not handed back" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a marker failure can orphan the host"; fi
fi

if it "aistack: up guards the bind before recreating containers that already run"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    rm -f "$d/active-coding-agents-fw"
    : >"$d/image-exists"
    out="$(_aistack "$d" up)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "up ran compose up on a LAN bind without the firewall" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "compose up reached: $(grep 'up -d' "$d/docker.log")" >&2; }
    [[ "$out" == *"coding-agents-fw.service"* ]] || { ok=0; echo "refusal unexplained: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a running container can be recreated on an unguarded bind"; fi
fi

if it "aistack: the guard and compose agree on the bind when the shell exports AUTOOS_STACK_BIND"; then
    ok=1
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    # compose would publish on the exported 0.0.0.0, not the file's loopback.
    _aistack "$d" AUTOOS_STACK_BIND=0.0.0.0 up >/dev/null && { ok=0; echo "exported LAN bind not guarded" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started containers" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    _aistack "$d" AUTOOS_STACK_BIND=127.0.0.1 up >/dev/null || { ok=0; echo "exported loopback refused" >&2; }
    [[ "$(sort -u "$d/compose-bind.log")" == "bind=127.0.0.1" ]] \
        || { ok=0; echo "compose saw another bind: $(sort -u "$d/compose-bind.log" | tr '\n' ' ')" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "file loopback refused" >&2; }
    [[ "$(sort -u "$d/compose-bind.log")" == "bind=127.0.0.1" ]] || { ok=0; echo "compose not given the file's bind" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the guarded bind and the published bind can differ"; fi
fi

if it "aistack: every FROM in opencode.Dockerfile and omniroute.Dockerfile is digest-pinned, or the rebuild hash is blind"; then
    # The rebuild label hashes the Dockerfile text: a tag-only FROM could move
    # underneath an unchanged text and the stale layer would never rebuild.
    ok=1
    for f in "$AISTACK/opencode.Dockerfile" "$AISTACK/omniroute.Dockerfile"; do
        froms="$(grep -ciE '^[[:space:]]*FROM[[:space:]]' "$f" 2>/dev/null || true)"
        pinned="$(grep -cE '^[[:space:]]*FROM[[:space:]]+[^[:space:]$]+@sha256:[0-9a-f]{64}([[:space:]]+[Aa][Ss][[:space:]]+[^[:space:]]+)?[[:space:]]*$' "$f" 2>/dev/null || true)"
        (( froms >= 1 && froms == pinned )) || { ok=0; echo "${pinned:-0} of ${froms:-0} FROM lines are @sha256:-pinned in $f" >&2; }
    done
    if (( ok )); then pass; else fail "a FROM is not digest-pinned"; fi
fi

# ─── The gateway image: OmniRoute + qodercli (omniroute.Dockerfile) ─────────
# The Qoder PAT login runs `qodercli` inside the gateway container. The
# upstream image has none (`spawn qodercli ENOENT`), so a derived layer adds
# it - built like the opencode one, from a digest-pinned base.

if it "aistack: the omniroute layer adds qodercli at an exact version on a digest-pinned base, no vendor binary"; then
    ok=1
    f="$AISTACK/omniroute.Dockerfile"
    [[ -f "$f" ]] || { ok=0; echo "omniroute.Dockerfile is missing" >&2; }
    base="$(sed -n 's/^FROM diegosouzapw\/omniroute:\([0-9][0-9.]*\)@sha256:[0-9a-f]\{64\}$/\1/p' "$f" 2>/dev/null)"
    [[ -n "$base" ]] || { ok=0; echo "FROM is not diegosouzapw/omniroute:<version>@sha256:<digest>" >&2; }
    # The local tag names the upstream version it is built on: bumped together.
    grep -qxE "    image: autoos/omniroute:${base//./\\.}-autoos[0-9]+" "$AISTACK/compose.yml" \
        || { ok=0; echo "compose.yml's image tag does not carry the FROM version [$base]" >&2; }
    grep -qxE 'RUN npm install -g @qoder-ai/qodercli@[0-9]+\.[0-9]+\.[0-9]+ && npm cache clean --force' "$f" 2>/dev/null \
        || { ok=0; echo "qodercli is not installed at an exact version, cache cleaned in the same layer" >&2; }
    grep -qx 'USER root' "$f" 2>/dev/null || { ok=0; echo "no USER root for the install" >&2; }
    [[ "$(grep -E '^USER ' "$f" 2>/dev/null | tail -n1)" == "USER node" ]] || { ok=0; echo "the image must end as USER node" >&2; }
    grep -qiE '^(COPY|ADD)[[:space:]]' "$f" 2>/dev/null && { ok=0; echo "COPY/ADD: a vendor binary would be committed; npm fetches at build" >&2; }
    if (( ok )); then pass; else fail "omniroute.Dockerfile does not add a pinned qodercli"; fi
fi

if it "aistack: init creates the qoder home, private and the operator's, and leaves the gateway data alone"; then
    d="$(_aistack_sandbox)"
    ok=1
    mkdir -p "$d/data/omniroute"
    printf 'db\n' >"$d/data/omniroute/storage.sqlite"; printf 'k=v\n' >"$d/data/omniroute/.env"
    before="$(cd "$d/data/omniroute" && cksum storage.sqlite .env)"
    out="$(_aistack "$d" --dry-run init)"
    [[ "$out" == *"would create $d/data/qoder-home"* ]] || { ok=0; echo "the dry run does not announce the qoder home" >&2; }
    [[ -e "$d/data/qoder-home" ]] && { ok=0; echo "the dry run created it" >&2; }
    _aistack "$d" init >/dev/null
    got="$(stat -c '%a %U' "$d/data/qoder-home" 2>&1)"
    [[ "$got" == "700 $(id -un)" ]] || { ok=0; echo "qoder home is not 700 and the operator's: $got" >&2; }
    out="$(_aistack "$d" init)"
    [[ "$out" == *qoder-home* ]] && { ok=0; echo "a second init touched it again: $out" >&2; }
    [[ "$(cd "$d/data/omniroute" && cksum storage.sqlite .env)" == "$before" ]] || { ok=0; echo "the gateway data changed" >&2; }
    [[ "$(LC_ALL=C ls -A "$d/data/omniroute" | tr '\n' ' ')" == ".env storage.sqlite " ]] || { ok=0; echo "files appeared in the gateway data dir" >&2; }
    # One that already exists with looser rights (made by hand) is tightened.
    chmod 755 "$d/data/qoder-home"
    _aistack "$d" init >/dev/null
    got="$(stat -c '%a' "$d/data/qoder-home" 2>&1)"
    [[ "$got" == 700 ]] || { ok=0; echo "an existing qoder home stays $got" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the qoder home is not created private, or init touched the gateway data"; fi
fi

if it "aistack: up creates the qoder home before compose starts the gateway, on a host init ran on long ago"; then
    ok=1
    # init ran long ago (stack.env exists), so up does not run it again: docker
    # would create the missing mount source as root and the gateway (host uid)
    # could not write its HOME.
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg" "$d/data/omniroute" "$d/data/opencode-home"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/image-exists"
    _aistack "$d" up omniroute >/dev/null || { ok=0; echo "up failed" >&2; }
    [[ "$(cat "$d/qoder-home-at-up.log" 2>/dev/null)" == present ]] || { ok=0; echo "compose up saw: [$(cat "$d/qoder-home-at-up.log" 2>&1)]" >&2; }
    got="$(stat -c '%a %U' "$d/data/qoder-home" 2>&1)"
    [[ "$got" == "700 $(id -un)" ]] || { ok=0; echo "qoder home: $got" >&2; }
    rm -rf "$d"
    # compose mounts AUTOOS_STACK_DATA from stack.env: that is where it is made.
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg" "$d/elsewhere/omniroute"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\nAUTOOS_STACK_DATA='%s'\n" "$d/elsewhere" >"$d/cfg/stack.env"
    : >"$d/image-exists"
    _aistack "$d" up omniroute >/dev/null || { ok=0; echo "up (stack.env data dir) failed" >&2; }
    [[ -d "$d/elsewhere/qoder-home" ]] || { ok=0; echo "not created under the AUTOOS_STACK_DATA of stack.env" >&2; }
    [[ -e "$d/data/qoder-home" ]] && { ok=0; echo "created under the default data dir, which compose does not mount" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "compose can be the one to create the qoder home"; fi
fi

if it "aistack: the omniroute image is rebuilt when its label, base digest or Dockerfile changes, and only then"; then
    d="$(_aistack_sandbox)"
    t="$d/tree/configuration"
    mkdir -p "$t/docker/ai-stack" "$d/cfg"
    cp "$AISTACK/ai-stack.sh" "$AISTACK/compose.yml" "$AISTACK/opencode.Dockerfile" "$AISTACK/omniroute.Dockerfile" "$t/docker/ai-stack/"
    cp "$ROOT/configuration/env-file.sh" "$t/"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    _omni_up() { rm -f "$d/docker.log" "$d/run-autoos-omniroute"; _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up omniroute >/dev/null; }
    _omni_builds() { grep -c '^build' "$d/docker.log" 2>/dev/null || true; }
    ok=1
    : >"$d/image-exists"; printf 'stale\n' >"$d/omniroute-label"
    _omni_up
    grep -qE "^build --label org\.autoos\.omniroute\.source=[0-9a-f]{64} -t autoos/omniroute:[^ ]+ -f $t/docker/ai-stack/omniroute\.Dockerfile " "$d/docker.log" \
        || { ok=0; echo "a stale label was not rebuilt: $(cat "$d/docker.log")" >&2; }
    [[ "$(_omni_builds)" == 1 ]] || { ok=0; echo "up omniroute built something else too" >&2; }
    [[ -e "$d/opencode-label" ]] && { ok=0; echo "the omniroute build wrote the opencode label" >&2; }
    first="$(cat "$d/omniroute-label")"
    _omni_up
    [[ "$(_omni_builds)" == 0 ]] || { ok=0; echo "rebuilt an up-to-date image" >&2; }
    # A bumped base digest, the Dockerfile text unchanged otherwise.
    sed -i -E "s/^(FROM [^ ]+@sha256:)[0-9a-f]{64}/\1$(printf '%064d' 0)/" "$t/docker/ai-stack/omniroute.Dockerfile"
    _omni_up
    [[ "$(_omni_builds)" == 1 ]] || { ok=0; echo "a bumped base digest was not rebuilt" >&2; }
    second="$(cat "$d/omniroute-label")"
    [[ "$second" != "$first" ]] || { ok=0; echo "label did not change with the digest" >&2; }
    _omni_up
    [[ "$(_omni_builds)" == 0 ]] || { ok=0; echo "rebuilt after the digest bump was built" >&2; }
    printf '# a changed layer\n' >>"$t/docker/ai-stack/omniroute.Dockerfile"
    _omni_up
    [[ "$(_omni_builds)" == 1 ]] || { ok=0; echo "a changed Dockerfile was not rebuilt" >&2; }
    [[ "$(cat "$d/omniroute-label")" != "$second" ]] || { ok=0; echo "label did not change with the text" >&2; }
    # No local image at all: built, not pulled.
    : >"$d/noimage-omniroute"
    _omni_up
    [[ "$(_omni_builds)" == 1 ]] || { ok=0; echo "a missing image was not built" >&2; }
    grep -qE '(^| )pull( |$)' "$d/docker.log" && { ok=0; echo "the image was pulled: $(cat "$d/docker.log")" >&2; }
    unset -f _omni_up _omni_builds
    rm -rf "$d"
    if (( ok )); then pass; else fail "the omniroute image does not track its Dockerfile and base"; fi
fi

if it "aistack: --dry-run up says would build or rebuild the omniroute image and builds nothing"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    ok=1
    out="$(_aistack "$d" --dry-run up omniroute)"
    [[ "$out" == *"would build autoos/omniroute:"*" (omniroute.Dockerfile)"* ]] || { ok=0; echo "no image: $out" >&2; }
    : >"$d/image-exists"; printf 'stale\n' >"$d/omniroute-label"
    out="$(_aistack "$d" --dry-run up omniroute)"
    [[ "$out" == *"would rebuild autoos/omniroute:"*" (omniroute.Dockerfile or its base image changed)"* ]] || { ok=0; echo "stale label: $out" >&2; }
    grep -q '^build' "$d/docker.log" 2>/dev/null && { ok=0; echo "a dry run built: $(cat "$d/docker.log")" >&2; }
    # Up to date (label from a real build): nothing to announce.
    _aistack "$d" up omniroute >/dev/null
    rm -f "$d/docker.log"
    out="$(_aistack "$d" --dry-run up omniroute)"
    [[ "$out" == *"would build"* || "$out" == *"would rebuild"* ]] && { ok=0; echo "an up-to-date image is announced: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the dry-run wording for the omniroute image is wrong"; fi
fi

if it "aistack: migrate builds the omniroute image before it stops anything, up before it starts the gateway"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    ok=1
    _aistack "$d" migrate --yes >/dev/null || { ok=0; echo "migrate failed" >&2; }
    _aistack_seq "$d/events.log" "docker: build --label org.autoos.omniroute.source=" "-t autoos/omniroute:" \
        "systemctl: --user stop autoos-omniroute.service" "up -d --no-deps omniroute" \
        || { ok=0; echo "the build did not come first: $(cat "$d/events.log")" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"; : >"$d/image-exists"
    _aistack "$d" up omniroute >/dev/null || { ok=0; echo "up failed" >&2; }
    _aistack_seq "$d/events.log" "docker: build --label org.autoos.omniroute.source=" "up -d --no-deps omniroute" \
        || { ok=0; echo "up started the gateway before building it: $(cat "$d/events.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the gateway can start from a stale or missing image"; fi
fi

if it "aistack: a failed omniroute build stops nothing and starts nothing"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/fail-build-omniroute"
    ok=1
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "migrate stopped a unit before the image existed" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "compose up ran" >&2; }
    [[ "$out" == *"building autoos/omniroute:"*"failed"* ]] || { ok=0; echo "not explained: $out" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"; : >"$d/image-exists"; : >"$d/fail-build-omniroute"
    _aistack "$d" up omniroute >/dev/null && { ok=0; echo "up reported success" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "up started the gateway from a failed build" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a failed image build does not stop the run"; fi
fi

if it "aistack: a failed backup leaves no partial archive and hands the service back"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    # tar that writes half an archive, then fails (disk full).
    cat >"$d/bin/tar" <<'STUB'
#!/bin/sh
prev=""
for a in "$@"; do
    case "$prev" in -czf|-f) printf 'partial' >"$a" ;; esac
    prev="$a"
done
exit 2
STUB
    chmod +x "$d/bin/tar"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    compgen -G "$d/data/backups/*.tar.gz" >/dev/null && { ok=0; echo "partial archive kept: $(ls "$d/data/backups")" >&2; }
    [[ -e "$d/active-autoos-omniroute" ]] || { ok=0; echo "gateway not handed back" >&2; }
    # rollback: the host dir is backed up before anything stops, so the same
    # failure leaves the running stack alone.
    d2="$(_aistack_sandbox)"
    _aistack_migrated "$d2"
    mkdir -p "$d2/home/.omniroute"; printf 'old-db\n' >"$d2/home/.omniroute/storage.sqlite"
    cp "$d/bin/tar" "$d2/bin/tar"
    out="$(_aistack "$d2" rollback --yes)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "rollback reported success" >&2; }
    compgen -G "$d2/data/backups/*.tar.gz" >/dev/null && { ok=0; echo "partial rollback archive kept" >&2; }
    [[ "$(cat "$d2/home/.omniroute/storage.sqlite")" == old-db ]] || { ok=0; echo "host state changed without a backup" >&2; }
    grep -qE 'compose .*(down|stop|rm)' "$d2/docker.log" 2>/dev/null && { ok=0; echo "stopped the stack before the backup" >&2; }
    [[ -e "$d2/run-autoos-omniroute" && -e "$d2/cfg/stack.active" ]] || { ok=0; echo "stack or marker gone" >&2; }
    rm -rf "$d" "$d2"
    if (( ok )); then pass; else fail "a failed backup leaves a partial archive"; fi
fi

# ─── The public URL (AUTOOS_OMNIROUTE_PUBLIC_URL) ───────────────────────────
# stack.env's AUTOOS_OMNIROUTE_PUBLIC_URL reaches the gateway as
# NEXT_PUBLIC_BASE_URL + OMNIROUTE_PUBLIC_BASE_URL (compose.yml). Unset must
# change nothing - and image 3.8.50 exits at startup on a value that is not an
# http(s) URL, so a bad one must not reach `compose up`.

if it "aistack: stack.env.example documents the public URL as a commented placeholder, nothing real"; then
    ok=1
    f="$AISTACK/stack.env.example"
    line="$(grep -nxF '# AUTOOS_OMNIROUTE_PUBLIC_URL=https://<your-omniroute-host>' "$f" | cut -d: -f1)"
    [[ -n "$line" ]] || { ok=0; echo "the commented placeholder line is missing" >&2; }
    if [[ -n "$line" ]]; then
        sed -n "$((line - 1))p" "$f" | grep -q '^# .*[Pp]ublic' || { ok=0; echo "no one-line description directly above it" >&2; }
    fi
    grep -qE '^[[:space:]]*AUTOOS_OMNIROUTE_PUBLIC_URL=' "$f" && { ok=0; echo "an active AUTOOS_OMNIROUTE_PUBLIC_URL line: the example would turn it on" >&2; }
    if (( ok )); then pass; else fail "the public URL is not a commented placeholder"; fi
fi

if it "aistack: init never invents the public URL and keeps the operator's value"; then
    # A guard: green before the feature, it fails if init ever starts writing the key.
    d="$(_aistack_sandbox)"
    ok=1
    _aistack "$d" init >/dev/null
    grep -q 'AUTOOS_OMNIROUTE_PUBLIC_URL' "$d/cfg/stack.env" && { ok=0; echo "init wrote the key: $(grep AUTOOS_OMNIROUTE_PUBLIC_URL "$d/cfg/stack.env")" >&2; }
    printf "AUTOOS_OMNIROUTE_PUBLIC_URL='https://gw.example.invalid/'\n" >>"$d/cfg/stack.env"
    _aistack "$d" init >/dev/null
    [[ "$(grep -c '^AUTOOS_OMNIROUTE_PUBLIC_URL=' "$d/cfg/stack.env")" == 1 ]] || { ok=0; echo "the key is not there exactly once" >&2; }
    grep -qxF "AUTOOS_OMNIROUTE_PUBLIC_URL='https://gw.example.invalid/'" "$d/cfg/stack.env" || { ok=0; echo "the operator's value changed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "init touches the public URL"; fi
fi

if it "aistack: up and migrate refuse an invalid public URL before anything starts or stops"; then
    ok=1
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\nAUTOOS_OMNIROUTE_PUBLIC_URL='https://opuser:oppw-secret@gw.example.invalid'\n" >"$d/cfg/stack.env"
    out="$(_aistack "$d" up omniroute)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "up accepted a URL with credentials" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "compose up ran" >&2; }
    [[ "$out" == *AUTOOS_OMNIROUTE_PUBLIC_URL* ]] || { ok=0; echo "the refusal does not name the variable: $out" >&2; }
    [[ "$out" == *oppw-secret* ]] && { ok=0; echo "the credentials were echoed" >&2; }
    # A dry run explains and carries on, like the bind guard.
    out="$(_aistack "$d" --dry-run up omniroute)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "dry run: exit $rc, not 0" >&2; }
    [[ "$out" == *"refusing"*AUTOOS_OMNIROUTE_PUBLIC_URL* || "$out" == *AUTOOS_OMNIROUTE_PUBLIC_URL*"refusing"* ]] || { ok=0; echo "dry run does not explain: $out" >&2; }
    rm -rf "$d"
    for bad in 'gw.example.invalid' 'ftp://gw.example.invalid' 'https://gw example.invalid'; do
        d="$(_aistack_sandbox)"
        mkdir -p "$d/cfg"; : >"$d/image-exists"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
        _aistack "$d" AUTOOS_OMNIROUTE_PUBLIC_URL="$bad" up omniroute >/dev/null && { ok=0; echo "up accepted [$bad] from the environment" >&2; }
        grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "compose up ran for [$bad]" >&2; }
        rm -rf "$d"
    done
    # What the gateway accepts (and nothing at all) goes through.
    for good in '' 'https://gw.example.invalid' 'https://gw.example.invalid/' 'http://gw.example.invalid:20128' 'https://gw.example.invalid/omniroute/'; do
        d="$(_aistack_sandbox)"
        mkdir -p "$d/cfg"; : >"$d/image-exists"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
        _aistack "$d" AUTOOS_OMNIROUTE_PUBLIC_URL="$good" up omniroute >/dev/null || { ok=0; echo "up refused [$good]" >&2; }
        rm -rf "$d"
    done
    # migrate: refused before a native unit stops.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    _aistack "$d" AUTOOS_OMNIROUTE_PUBLIC_URL=gw.example.invalid migrate --yes >/dev/null && { ok=0; echo "migrate accepted an invalid URL" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "migrate stopped a unit first" >&2; }
    [[ -e "$d/active-autoos-omniroute" ]] || { ok=0; echo "the native gateway is gone" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an invalid public URL reaches compose"; fi
fi

if it "aistack: the docs explain the loopback callback and what the public URL does and does not change"; then
    ok=1
    f="$ROOT/docs/web-services.md"
    for needle in 'AUTOOS_OMNIROUTE_PUBLIC_URL' 'NEXT_PUBLIC_BASE_URL' 'OMNIROUTE_PUBLIC_BASE_URL' 'ANTIGRAVITY_OAUTH_CLIENT_ID' \
                  'ssh -L 20128:127.0.0.1:20128' 'http://127.0.0.1:20128/callback' 'BASE_URL: http://localhost:20128' 'INVALID_ORIGIN'; do
        grep -qF -- "$needle" "$f" || { ok=0; echo "docs/web-services.md does not mention: $needle" >&2; }
    done
    if (( ok )); then pass; else fail "the OAuth / public URL docs are incomplete"; fi
fi

# ─── ai-stack.sh verify ─────────────────────────────────────────────────────
# The read-only end-to-end check. Docker is the usual stub; curl is a second
# stand-in wired through AUTOOS_CURL that answers from a table and logs the
# argv it received, so a test can prove what did (and did not) reach a curl
# command line.
_AISTACK_VERIFY_KEY="sk-verify-secret-0123456789abcdef"

# _aistack_verify_sandbox <sandbox>: a migrated host (three healthy
# containers, firewall unit up) plus what verify reads beyond the docker
# stub: the OpenHands container's SANDBOX_VOLUMES and the curl stand-in with
# an all-green route table.
_aistack_verify_sandbox() {
    local d="$1"
    _aistack_migrated "$d"
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/code" "$d/code" >"$d/env-openhands-app"
    printf '%s' "$_AISTACK_VERIFY_KEY" >"$d/curl-key"
    cat >"$d/bin/verify-curl" <<'STUB'
#!/usr/bin/env bash
# curl stand-in for `ai-stack.sh verify` (AUTOOS_CURL). Answers from
# <state>/curl-table, one "URL AUTH BODY CODE" line per route: AUTH is none |
# key | bad | any, BODY is - or a substring of the -d payload, the first match
# wins and no match means nothing answered. Logs its argv to curl-argv.log.
# The Authorization header is read from `-H @-` (stdin) or an inline -H and
# compared with <state>/curl-key; it is never logged, only the verdict is.
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
printf '%s\n' "$*" >>"$S/curl-argv.log"
args=("$@"); url=""; body=""; hdrs=""; want_code=0
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        -w) want_code=1 ;;
        -d) body="${args[i+1]:-}" ;;
        -H) h="${args[i+1]:-}"
            if [[ "$h" == "@-" ]]; then h="$(cat)"; fi
            hdrs+="$h"$'\n' ;;
        http://*|https://*) url="${args[i]}" ;;
    esac
done
auth=none
want="Authorization: Bearer $(cat "$S/curl-key" 2>/dev/null)"
while IFS= read -r h; do
    case "$h" in
        "$want") auth=key ;;
        Authorization:*) auth=bad ;;
    esac
done <<<"$hdrs"
code=000
while read -r t_url t_auth t_body t_code; do
    [[ "$t_url" == "$url" ]] || continue
    [[ "$t_auth" == any || "$t_auth" == "$auth" ]] || continue
    [[ "$t_body" == - || "$body" == *"$t_body"* ]] || continue
    code="$t_code"; break
done <"$S/curl-table"
printf 'url=%s auth=%s code=%s\n' "$url" "$auth" "$code" >>"$S/curl-seen.log"
if (( want_code )); then printf '%s' "$code"; fi
if [[ "$code" == 000 ]]; then exit 7; fi
exit 0
STUB
    chmod +x "$d/bin/verify-curl"
    printf '%s\n' \
        'http://127.0.0.1:20128/v1/models none - 401' \
        'http://127.0.0.1:20128/v1/models key - 200' \
        'http://127.0.0.1:20128/v1/chat/completions bad - 401' \
        'http://127.0.0.1:20128/v1/chat/completions key t2-worker-free-only 200' \
        'http://127.0.0.1:20128/v1/chat/completions key t3-driver-free-only 200' \
        'http://127.0.0.1:20128/v1/chat/completions key t2-worker-clean 200' \
        'http://127.0.0.1:4096/api/session none - 401' >"$d/curl-table"
}
# _aistack_verify_route <sandbox> <url> <auth> <body> <code>: one more route,
# ahead of the table (the first match wins).
_aistack_verify_route() {
    local d="$1"; shift
    { printf '%s %s %s %s\n' "$@"; cat "$d/curl-table"; } >"$d/curl-table.new" && mv "$d/curl-table.new" "$d/curl-table"
}
# _aistack_verify <sandbox> [NAME=value...] verify: the curl stand-in wired in.
_aistack_verify() {
    local d="$1"
    shift
    _aistack "$d" AUTOOS_CURL="$d/bin/verify-curl" "$@"
}
# _aistack_snapshot <sandbox>: a checksum of every file the run could have
# touched (the stubs' own logs and bin/ excluded).
_aistack_snapshot() {
    find "$1" \( -name bin -o -name '*.log' \) -prune -o -type f -print | sort | xargs -r cksum
}

if it "aistack: verify all green exits 0 and the summary says 0 failed"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' 'http://127.0.0.1:18082/ none - 302' >>"$d/curl-table"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" \
        AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/ http://127.0.0.1:18082/" verify)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "exit $rc, not 0" >&2; }
    grep -qx 'verify: 13 ok, 0 failed, 2 skipped' <<<"$out" || { ok=0; echo "summary: $(tail -n1 <<<"$out")" >&2; }
    grep -q '^  FAIL' <<<"$out" && { ok=0; echo "a FAIL line on a healthy stack" >&2; }
    for name in 'container autoos-omniroute' 'container autoos-opencode' 'container openhands-app' \
                'keyless /v1/models refused on :20128' 'keyless /api/session refused on :4096' \
                'combo t2-worker-free-only' 'combo t3-driver-free-only' 'combo t2-worker-clean' 'omniroute has qodercli' \
                'public URL http://127.0.0.1:18081/' 'public URL http://127.0.0.1:18082/'; do
        grep -qx "  ok    $name" <<<"$out" || { ok=0; echo "no ok line for: $name" >&2; }
    done
    grep -qE '^  ok    code dir .* visible in autoos-opencode$' <<<"$out" || { ok=0; echo "no ok for the opencode code dir" >&2; }
    grep -qE '^  ok    code dir .* in openhands-app SANDBOX_VOLUMES$' <<<"$out" || { ok=0; echo "no ok for the sandbox volumes" >&2; }
    grep -q '^  skip  healthcheck - ' <<<"$out" || { ok=0; echo "the healthcheck skip is not reported" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify is not green on a healthy stack"; fi
fi

if it "aistack: verify FAILs a keyless request that is answered 200"; then
    ok=1
    for probe in "20128 /v1/models" "4096 /api/session"; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        _aistack_verify_route "$d" "http://127.0.0.1:${probe%% *}${probe#* }" none - 200
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$probe: exit $rc, not 1" >&2; }
        grep -qE "^  FAIL  keyless ${probe#* } refused on :${probe%% *} - HTTP 200" <<<"$out" || { ok=0; echo "$probe: no FAIL line" >&2; }
        [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "$probe: not exactly one FAIL" >&2; }
        grep -q '^verify: .* 1 failed' <<<"$out" || { ok=0; echo "$probe: summary does not say 1 failed" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "a keyless 200 is not a FAIL"; fi
fi

if it "aistack: verify FAILs a container that is not running or not healthy"; then
    ok=1
    for mode in stopped unhealthy missing; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        case "$mode" in
            stopped)   rm -f "$d/run-autoos-opencode"; want='container autoos-opencode - exited' ;;
            unhealthy) : >"$d/unhealthy-omniroute"; want='container autoos-omniroute - running \(unhealthy\)' ;;
            missing)   rm -f "$d/run-openhands-app" "$d/compose-openhands-app"; want='container openhands-app - no container' ;;
        esac
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$mode: exit $rc, not 1" >&2; }
        # Check 1 runs first, so its FAIL leads. Later checks of a container that
        # is down may FAIL too (a stopped opencode cannot show its code dir).
        grep -E '^  FAIL' <<<"$out" | head -n1 | grep -qE "^  FAIL  $want\$" || { ok=0; echo "$mode: the first FAIL is not the container: $(grep '^  FAIL' <<<"$out" | head -n1)" >&2; }
        grep -qE '^verify: [0-9]+ ok, [1-9][0-9]* failed' <<<"$out" || { ok=0; echo "$mode: summary does not count a failure" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "a stopped, unhealthy or missing container is not a FAIL"; fi
fi

if it "aistack: verify skips the keyed combos without a key, and the key never reaches argv or the output"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    ok=1
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "no key: exit $rc, not 0" >&2; }
    grep -q '^  skip  keyed combos - AUTOOS_OMNIROUTE_KEY is not set' <<<"$out" || { ok=0; echo "no key: no skip line" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log")" == 0 ]] || { ok=0; echo "no key: a chat request was sent" >&2; }
    rm -f "$d/curl-argv.log" "$d/curl-seen.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "key set: exit $rc, not 0" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log")" == 3 ]] || { ok=0; echo "key set: not three chat requests" >&2; }
    # The stub compared the header it read from stdin: the key did arrive.
    [[ "$(grep -c 'chat/completions auth=key code=200' "$d/curl-seen.log")" == 3 ]] || { ok=0; echo "key set: the key did not reach curl" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "the key is on curl's command line" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" "$d/docker.log" || true)" == 0 ]] || { ok=0; echo "the key reached docker" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" <<<"$out" || true)" == 0 ]] || { ok=0; echo "the key is in the output" >&2; }
    grep -q '^  ok    combo t2-worker-clean$' <<<"$out" || { ok=0; echo "key set: no ok for a combo" >&2; }
    # A key the gateway rejects: FAIL for every combo, and still no key printed.
    rm -f "$d/curl-argv.log" "$d/curl-seen.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY=sk-verify-wrong-key-987654321 verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "wrong key: exit $rc, not 1" >&2; }
    [[ "$(grep -c '^  FAIL  combo .* - HTTP 401' <<<"$out")" == 3 ]] || { ok=0; echo "wrong key: not three FAIL lines" >&2; }
    [[ "$(grep -c -F -e 'sk-verify-wrong-key-987654321' "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "the wrong key is on curl's command line" >&2; }
    [[ "$(grep -c -F -e 'sk-verify-wrong-key-987654321' <<<"$out" || true)" == 0 ]] || { ok=0; echo "the wrong key is in the output" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the gateway key leaks, or the keyless run is not a skip"; fi
fi

if it "aistack: verify AUTOOS_VERIFY_COMBOS replaces the combo list"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    _aistack_verify_route "$d" http://127.0.0.1:20128/v1/chat/completions key alpha-combo 200
    _aistack_verify_route "$d" http://127.0.0.1:20128/v1/chat/completions key beta-combo 404
    ok=1
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_COMBOS="alpha-combo beta-combo" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "exit $rc, not 1" >&2; }
    grep -qx '  ok    combo alpha-combo' <<<"$out" || { ok=0; echo "alpha-combo not ok" >&2; }
    grep -qE '^  FAIL  combo beta-combo - HTTP 404' <<<"$out" || { ok=0; echo "beta-combo not FAIL" >&2; }
    [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "not exactly one FAIL" >&2; }
    grep -q 't2-worker-free-only' "$d/curl-argv.log" && { ok=0; echo "a default combo was still requested" >&2; }
    grep -q '"max_tokens":16' "$d/curl-argv.log" || { ok=0; echo "max_tokens 16 not sent" >&2; }
    # A name that could break out of the JSON body is refused, not sent.
    rm -f "$d/curl-argv.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_COMBOS='bad"name' verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "bad name: exit $rc, not 1" >&2; }
    grep -q '^  FAIL  combo (invalid name)' <<<"$out" || { ok=0; echo "bad name: no FAIL" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "bad name: a request was sent" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "AUTOOS_VERIFY_COMBOS is not honoured"; fi
fi

if it "aistack: verify public URLs: skipped when unset, FAIL only for the one that is not a 302"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    ok=1
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "unset: exit $rc, not 0" >&2; }
    grep -q '^  skip  public URLs - AUTOOS_VERIFY_PUBLIC_URLS is not set' <<<"$out" || { ok=0; echo "unset: no skip line" >&2; }
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' 'http://127.0.0.1:18082/ none - 200' >>"$d/curl-table"
    out="$(_aistack_verify "$d" AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/ http://127.0.0.1:18082/" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "one 200: exit $rc, not 1" >&2; }
    grep -qx '  ok    public URL http://127.0.0.1:18081/' <<<"$out" || { ok=0; echo "the 302 URL is not ok" >&2; }
    grep -qE '^  FAIL  public URL http://127.0.0.1:18082/ - HTTP 200' <<<"$out" || { ok=0; echo "the 200 URL is not FAIL" >&2; }
    [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "not exactly one FAIL" >&2; }
    # No credentials go out with a public probe, and none in the URL are echoed.
    grep -qE '(^| )(-H|-u|--user|--header)( |$)' "$d/curl-argv.log" && { ok=0; echo "a public probe carried credentials" >&2; }
    out="$(_aistack_verify "$d" AUTOOS_VERIFY_PUBLIC_URLS="http://probe-user:probe-pass@127.0.0.1:18081/" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "userinfo: exit $rc, not 1" >&2; }
    grep -q '^  FAIL  public URL (rejected)' <<<"$out" || { ok=0; echo "userinfo: no FAIL" >&2; }
    grep -q 'probe-pass' <<<"$out" && { ok=0; echo "userinfo: the password is echoed" >&2; }
    grep -q 'probe-pass' "$d/curl-argv.log" && { ok=0; echo "userinfo: the URL was requested" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "public URL checks misbehave"; fi
fi

if it "aistack: verify checks the code dir in opencode and in the OpenHands sandbox volumes"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    : >"$d/nocode-autoos-opencode"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "opencode without the tree: exit $rc, not 1" >&2; }
    grep -qE '^  FAIL  code dir .* visible in autoos-opencode - ' <<<"$out" || { ok=0; echo "opencode: no FAIL" >&2; }
    grep -qE '^  ok    code dir .* in openhands-app SANDBOX_VOLUMES$' <<<"$out" || { ok=0; echo "opencode case: openhands should stay ok" >&2; }
    rm -f "$d/nocode-autoos-opencode"
    # A volume list where the tree is one of several entries passes ...
    printf 'SANDBOX_VOLUMES=/elsewhere:/elsewhere:ro,%s:%s:rw\n' "$d/code" "$d/code" >"$d/env-openhands-app"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "several volumes: exit $rc, not 0" >&2; }
    # ... one without it, or an unset value, does not.
    for vols in 'SANDBOX_VOLUMES=/elsewhere:/elsewhere:rw' 'SANDBOX_VOLUMES=' 'LLM_MODEL=x'; do
        printf '%s\n' "$vols" >"$d/env-openhands-app"
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$vols: exit $rc, not 1" >&2; }
        grep -qE '^  FAIL  code dir .* in openhands-app SANDBOX_VOLUMES - ' <<<"$out" || { ok=0; echo "$vols: no FAIL" >&2; }
    done
    # The directory is AUTOOS_CODE_DIR - the variable compose reads - from the
    # environment first, stack.env next; nothing is hard-coded.
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/other" "$d/other" >"$d/env-openhands-app"
    out="$(_aistack_verify "$d" AUTOOS_CODE_DIR="$d/other" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "env AUTOOS_CODE_DIR: exit $rc, not 0" >&2; }
    grep -qF "exec autoos-opencode test -d $d/other" "$d/docker.log" || { ok=0; echo "env: the exec did not use AUTOOS_CODE_DIR" >&2; }
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/fromfile" "$d/fromfile" >"$d/env-openhands-app"
    printf "AUTOOS_CODE_DIR='%s'\n" "$d/fromfile" >>"$d/cfg/stack.env"
    out="$(_aistack_verify "$d" AUTOOS_CODE_DIR= verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "stack.env AUTOOS_CODE_DIR: exit $rc, not 0" >&2; }
    grep -qF "exec autoos-opencode test -d $d/fromfile" "$d/docker.log" || { ok=0; echo "stack.env: the exec did not use its AUTOOS_CODE_DIR" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the code dir check misbehaves"; fi
fi

if it "aistack: verify checks the gateway container has qodercli: ok, FAIL, or skip with a reason"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "healthy: exit $rc, not 0" >&2; }
    grep -qx '  ok    omniroute has qodercli' <<<"$out" || { ok=0; echo "healthy: no ok line" >&2; }
    grep -qx 'exec autoos-omniroute qodercli --version' "$d/docker.log" || { ok=0; echo "the check did not run qodercli --version in the gateway container" >&2; }
    # The image without the layer: the exec cannot find the binary.
    : >"$d/noqoder-autoos-omniroute"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "no qodercli: exit $rc, not 1" >&2; }
    grep -qE '^  FAIL  omniroute has qodercli - ' <<<"$out" || { ok=0; echo "no qodercli: no FAIL line: $out" >&2; }
    [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "no qodercli: not exactly one FAIL" >&2; }
    # A command that answers, but not with a version.
    rm -f "$d/noqoder-autoos-omniroute"; printf 'not a version\n' >"$d/qoder-version-autoos-omniroute"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "no version: exit $rc, not 1" >&2; }
    grep -qE '^  FAIL  omniroute has qodercli - ' <<<"$out" || { ok=0; echo "no version: no FAIL line" >&2; }
    rm -f "$d/qoder-version-autoos-omniroute"
    # A stopped gateway is the container check's FAIL; this one skips, and does not exec.
    rm -f "$d/run-autoos-omniroute" "$d/docker.log"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    grep -qx '  skip  omniroute has qodercli - autoos-omniroute is not running' <<<"$out" || { ok=0; echo "stopped: no skip line: $(grep qodercli <<<"$out")" >&2; }
    grep -q 'qodercli' "$d/docker.log" 2>/dev/null && { ok=0; echo "stopped: docker was asked to exec qodercli" >&2; }
    rm -rf "$d"
    # The gateway service is not enabled: skipped, and docker is not asked.
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    t="$d/tree/configuration"
    mkdir -p "$t/docker/ai-stack"
    cp "$AISTACK/ai-stack.sh" "$AISTACK/opencode.Dockerfile" "$AISTACK/omniroute.Dockerfile" "$t/docker/ai-stack/"
    cp "$ROOT/configuration/env-file.sh" "$t/"
    sed 's/^    container_name: autoos-omniroute$/&\n    profiles: ["gateway"]/' "$AISTACK/compose.yml" >"$t/docker/ai-stack/compose.yml"
    out="$(_AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack_verify "$d" verify)" && rc=0 || rc=$?
    grep -qx '  skip  omniroute has qodercli - the omniroute service is not enabled' <<<"$out" || { ok=0; echo "profile off: no skip line: $(grep qodercli <<<"$out")" >&2; }
    grep -q 'qodercli' "$d/docker.log" 2>/dev/null && { ok=0; echo "profile off: docker was asked about qodercli" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the qodercli check is not ok/FAIL/skip as specified"; fi
fi

if it "aistack: verify prints the public URL as the app normalizes it, skips it when unset or empty, FAILs one the gateway would refuse"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    skipline='  skip  omniroute public URL - AUTOOS_OMNIROUTE_PUBLIC_URL is not set'
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "unset: exit $rc, not 0" >&2; }
    grep -qxF "$skipline" <<<"$out" || { ok=0; echo "unset: no skip line: $(grep 'public URL' <<<"$out")" >&2; }
    # Empty - in the environment, in stack.env, or blanks only - is the same as unset.
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_PUBLIC_URL= verify)" || true
    grep -qxF "$skipline" <<<"$out" || { ok=0; echo "empty env: no skip line" >&2; }
    out="$(_aistack_verify "$d" 'AUTOOS_OMNIROUTE_PUBLIC_URL=   ' verify)" || true
    grep -qxF "$skipline" <<<"$out" || { ok=0; echo "blank env: no skip line" >&2; }
    printf "AUTOOS_OMNIROUTE_PUBLIC_URL=''\n" >>"$d/cfg/stack.env"
    out="$(_aistack_verify "$d" verify)" || true
    grep -qxF "$skipline" <<<"$out" || { ok=0; echo "empty in stack.env: no skip line" >&2; }
    # Printed the way the app normalizes it: trimmed, trailing slashes dropped, a path kept.
    for pair in 'https://gw.example.invalid|https://gw.example.invalid' 'https://gw.example.invalid/|https://gw.example.invalid' \
                'https://gw.example.invalid///|https://gw.example.invalid' '  https://gw.example.invalid/  |https://gw.example.invalid' \
                'https://gw.example.invalid/omniroute/|https://gw.example.invalid/omniroute' 'http://gw.example.invalid:20128/|http://gw.example.invalid:20128'; do
        out="$(_aistack_verify "$d" "AUTOOS_OMNIROUTE_PUBLIC_URL=${pair%%|*}" verify)" && rc=0 || rc=$?
        (( rc == 0 )) || { ok=0; echo "[${pair%%|*}]: exit $rc, not 0" >&2; }
        grep -qxF "  ok    omniroute public URL ${pair#*|}" <<<"$out" || { ok=0; echo "[${pair%%|*}]: no ok line for ${pair#*|}: $(grep 'public URL' <<<"$out")" >&2; }
    done
    # The environment beats stack.env, as it does in compose's interpolation.
    printf "AUTOOS_OMNIROUTE_PUBLIC_URL='https://from-file.example.invalid/'\n" >>"$d/cfg/stack.env"
    out="$(_aistack_verify "$d" verify)" || true
    grep -qxF '  ok    omniroute public URL https://from-file.example.invalid' <<<"$out" || { ok=0; echo "stack.env value not shown" >&2; }
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_PUBLIC_URL=https://from-env.example.invalid verify)" || true
    grep -qxF '  ok    omniroute public URL https://from-env.example.invalid' <<<"$out" || { ok=0; echo "the environment value does not win" >&2; }
    # Printing is not probing: it may be a plain LAN address, not the proxy's 302.
    grep -q 'example.invalid' "$d/curl-argv.log" 2>/dev/null && { ok=0; echo "verify requested the public URL" >&2; }
    # What the gateway would refuse at startup: FAIL, and the value is not echoed.
    for bad in 'gw.example.invalid' 'ftp://gw.example.invalid' 'https://opuser:oppw-secret@gw.example.invalid' 'https://gw example.invalid'; do
        out="$(_aistack_verify "$d" "AUTOOS_OMNIROUTE_PUBLIC_URL=$bad" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "[$bad]: exit $rc, not 1" >&2; }
        grep -qE '^  FAIL  omniroute public URL - ' <<<"$out" || { ok=0; echo "[$bad]: no FAIL line" >&2; }
        [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "[$bad]: not exactly one FAIL" >&2; }
        [[ "$out" == *oppw-secret* || "$out" == *"gw example"* ]] && { ok=0; echo "[$bad]: the value is echoed" >&2; }
    done
    rm -rf "$d"
    if (( ok )); then pass; else fail "the public URL line of verify misbehaves"; fi
fi

if it "aistack: verify follows the compose profiles: a disabled openhands is skipped, an enabled one is checked"; then
    ok=1
    for style in inline block; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        t="$d/tree/configuration"
        mkdir -p "$t/docker/ai-stack"
        cp "$AISTACK/ai-stack.sh" "$AISTACK/opencode.Dockerfile" "$t/docker/ai-stack/"
        cp "$ROOT/configuration/env-file.sh" "$t/"
        if [[ "$style" == inline ]]; then
            sed 's/^    container_name: openhands-app$/&\n    profiles: ["extras", "openhands"]/' "$AISTACK/compose.yml" >"$t/docker/ai-stack/compose.yml"
        else
            sed 's/^    container_name: openhands-app$/&\n    profiles:\n      - extras\n      - openhands/' "$AISTACK/compose.yml" >"$t/docker/ai-stack/compose.yml"
        fi
        grep -q 'profiles:' "$t/docker/ai-stack/compose.yml" || { ok=0; echo "$style: the fixture has no profiles" >&2; }
        out="$(_AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 0 )) || { ok=0; echo "$style, profile off: exit $rc, not 0" >&2; }
        grep -q 'container openhands-app' <<<"$out" && { ok=0; echo "$style, profile off: openhands-app is still checked" >&2; }
        grep -q '^  skip  code dir .*openhands' <<<"$out" || { ok=0; echo "$style, profile off: no skip for the sandbox volumes" >&2; }
        grep -q 'openhands-app' "$d/docker.log" && { ok=0; echo "$style, profile off: docker was asked about openhands-app" >&2; }
        out="$(_AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack_verify "$d" COMPOSE_PROFILES=openhands verify)" && rc=0 || rc=$?
        (( rc == 0 )) || { ok=0; echo "$style, profile on: exit $rc, not 0" >&2; }
        grep -qx '  ok    container openhands-app' <<<"$out" || { ok=0; echo "$style, profile on: openhands-app not checked" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "compose profiles are not honoured"; fi
fi

if it "aistack: verify is read-only: only inspect and exec reach docker, nothing on disk changes"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' >>"$d/curl-table"
    ok=1
    before="$(_aistack_snapshot "$d")"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "exit $rc, not 0: $out" >&2; }
    after="$(_aistack_snapshot "$d")"
    [[ "$before" == "$after" ]] || { ok=0; echo "a file changed: $(diff <(printf '%s' "$before") <(printf '%s' "$after") | head -5)" >&2; }
    [[ -s "$d/docker.log" ]] || { ok=0; echo "docker was never asked anything (a vacuous run)" >&2; }
    [[ "$(grep -cE '^(start|stop|restart|rm|up|down|run|create|compose|build|pull|network|kill|pause|unpause|cp|update)( |$)' "$d/docker.log" || true)" == 0 ]] \
        || { ok=0; echo "a mutating docker verb: $(grep -E '^(start|stop|restart|rm|up|down|run|create|compose)' "$d/docker.log" | head -3)" >&2; }
    grep -vE '^(inspect|exec) ' "$d/docker.log" | grep -q . && { ok=0; echo "docker verbs beyond inspect/exec: $(grep -vE '^(inspect|exec) ' "$d/docker.log" | head -3)" >&2; }
    grep '^exec ' "$d/docker.log" | grep -vE '^exec [^ ]+ (test -d |qodercli --version$)' | grep -q . && { ok=0; echo "an exec that is neither test -d nor qodercli --version" >&2; }
    # Nothing but docker inspect/exec: not systemctl, not ss, not the plain curl on PATH.
    grep -vE '^docker: (inspect|exec) ' "$d/events.log" | grep -q . && { ok=0; echo "another tool was called: $(grep -vE '^docker: (inspect|exec) ' "$d/events.log" | head -3)" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify changed something or ran a docker verb it must not"; fi
fi

if it "aistack: verify is listed in the unknown-command message and in --help"; then
    d="$(_aistack_sandbox)"
    ok=1
    out="$(_aistack "$d" bogus)" && rc=0 || rc=$?
    (( rc == 2 )) || { ok=0; echo "exit $rc, not 2" >&2; }
    grep -qE '^Unknown command: bogus \(.*\bverify\b.*\)' <<<"$out" || { ok=0; echo "not in the message: $out" >&2; }
    out="$(_aistack "$d" --help)"
    grep -q 'ai-stack.sh verify' <<<"$out" || { ok=0; echo "not in --help" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify is not advertised"; fi
fi

