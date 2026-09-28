# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── WSL detection ──────────────────────────────────────────────────────────
describe "MCP wiring"

if it "graphify is registered once, at user scope"; then
    # The command is cwd-relative, so one definition serves every repository its
    # own graph. A per-repo entry would pin one repo's graph for all of them.
    if grep -q "register_mcp_server graphify user" lib/linux/install.sh &&
       grep -q "graphify-out/graph.json" lib/linux/install.sh; then pass
    else fail "graphify is not a single cwd-relative user-scope entry"; fi
fi

if it "omnigraph is never registered at user scope"; then
    # A user-scope omnigraph silently wins over the per-repo one and answers
    # from the wrong graph, which looks identical to it working.
    if grep -q "register_mcp_server omnigraph" lib/linux/install.sh; then
        fail "omnigraph is being registered as a user server"
    elif grep -q "claude mcp remove omnigraph" lib/linux/install.sh; then pass
    else fail "the shadowing case is never called out"; fi
fi

if it "a project MCP server is approved, not just declared"; then
    # A tracked .mcp.json cannot approve itself; Claude Code skips an unapproved
    # project server silently.
    if grep -q "enabledMcpjsonServers" lib/linux/install.sh; then pass
    else fail "nothing writes the approval list"; fi
fi

if it "approving a server keeps the rest of the settings file"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.claude"
    printf '%s' '{"permissions":{"allow":["Bash(ls:*)"]},"enabledMcpjsonServers":["already"]}' \
        >"$tmp/.claude/settings.local.json"
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    got="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print('perm' if d.get('permissions') else 'LOST', ','.join(d.get('enabledMcpjsonServers',[])))
" "$tmp/.claude/settings.local.json")"
    rm -rf "$tmp"
    if [[ "$got" == "perm already,omnigraph" ]]; then pass
    else fail "expected the permissions block kept and omnigraph appended, got: $got"; fi
fi

if it "approving twice adds nothing the second time"; then
    tmp="$(mktemp -d)"
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    n="$(python3 -c "
import json,sys
print(len(json.load(open(sys.argv[1],encoding='utf-8')).get('enabledMcpjsonServers',[])))
" "$tmp/.claude/settings.local.json")"
    rm -rf "$tmp"
    if [[ "$n" == "1" ]]; then pass; else fail "expected 1 entry, got $n"; fi
fi

if it "no bearer token is ever invented"; then
    # This repository is public. A real-looking secret in it is a leak whether or
    # not it happens to work, and a guessed one fails as an unexplainable 401.
    if grep -qE "OMNIGRAPH_TOKEN=[\"']?[A-Za-z0-9]" lib/linux/install.sh; then
        fail "a token literal is present"
    elif grep -q "OMNIGRAPH_TOKEN is not set" lib/linux/install.sh; then pass
    else fail "a missing token is never reported"; fi
fi

# ─── Omnigraph reachability (measured 2026-09-24) ──────────────────────────
# The bridge refuses to start without OMNIGRAPH_BASE_URL and OMNIGRAPH_GRAPH_ID
# (the client only says "Connection closed"), and answers health/tools-list
# without a token, so clients show "connected" while every read fails.

if it "omnigraph config shape passes the offline probe"; then
    out="$(python3 tools/check-omnigraph.py --offline 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $out"; fi
fi

if it "omnigraph probe rejects a token value and a missing graph id"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/catalog"
    cp catalog/agent-harness.json "$tmp/catalog/"
    pin="$(python3 -c 'import json;print(json.load(open("catalog/agent-harness.json"))["mcp_servers"]["omnigraph"]["package"])')"
    printf '{"mcpServers":{"omnigraph":{"command":"npx","args":["-y","%s"],"env":{"OMNIGRAPH_BASE_URL":"http://localhost:8080","OMNIGRAPH_TOKEN":"not-a-reference"}}}}' "$pin" >"$tmp/.mcp.json"
    printf '{"mcp":{"servers":{"omnigraph":{"type":"local","command":["npx","-y","%s"],"environment":{"OMNIGRAPH_BASE_URL":"http://localhost:8080","OMNIGRAPH_TOKEN":"x"}}}}}' "$pin" >"$tmp/opencode.jsonc"
    out="$(python3 tools/check-omnigraph.py --offline --root "$tmp" 2>&1)"; rc=$?
    rm -rf "$tmp"
    ok=1
    [[ $rc -eq 1 ]] || ok=0
    for frag in "OMNIGRAPH_GRAPH_ID is empty" "never a value" "not the file"; do
        [[ "$out" == *"$frag"* ]] || { ok=0; echo "missing: $frag" >&2; }
    done
    [[ "$out" != *"not-a-reference"* ]] || ok=0
    if (( ok )); then pass; else fail "rc=$rc out=$out"; fi
fi

if it "omnigraph probe warns when a local .env names a different graph id"; then
    # agent-skills' trust_worktree.py writes OMNIGRAPH_GRAPH_ID=<folder name>
    # (here "AutoOS") into worktree .env files; graph ids are case-sensitive and
    # the server only has "autoos". Nothing in this repo reads that .env, so it
    # warns instead of failing, but it must never go unnoticed.
    tmp="$(mktemp -d)"
    cp -r catalog "$tmp/" && cp .mcp.json opencode.jsonc "$tmp/"
    printf 'OMNIGRAPH_GRAPH_ID=AutoOS\n' >"$tmp/.env"
    out="$(python3 tools/check-omnigraph.py --offline --root "$tmp" 2>&1)"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$out" == *"WARN"*"'AutoOS'"*"'autoos'"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "omnigraph live probe names a missing token, a rejected token and a missing graph"; then
    # A stub server on a random port stands in for omnigraph-server; nothing
    # leaves the machine and nothing is installed.
    stub="$(mktemp -d)"
    cat >"$stub/stub.py" <<'PY'
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _auth(self):
        return self.headers.get("Authorization") == "Bearer good-token"
    def do_GET(self):
        if self.path == "/healthz": return self._send(200, {"status": "ok", "version": "stub"})
        if not self._auth(): return self._send(401, {"error": "invalid bearer token"})
        if self.path == "/graphs/autoos/schema": return self._send(200, {"source": "node Project {}"})
        return self._send(404, {"error": "graph not found"})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if not self._auth(): return self._send(401, {"error": "invalid bearer token"})
        # /query takes `query` (server 0.8.1); `query_source` is /read's field.
        if self.path != "/graphs/autoos/query" or "query" not in body:
            return self._send(422, {"error": "missing field `query`"})
        return self._send(200, {"rows": [{"p.slug": "autoos"}]})
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
    python3 "$stub/stub.py" "$stub/port" & stub_pid=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -s "$stub/port" ]] && break; sleep 0.2; done
    base="http://127.0.0.1:$(cat "$stub/port" 2>/dev/null)"
    probe() { env -u OMNIGRAPH_TOKEN AUTOOS_OMNIGRAPH_ENV_FILE="$stub/none.env" "$@" python3 tools/check-omnigraph.py --base-url "$base" 2>&1; }
    good="$(probe OMNIGRAPH_TOKEN=good-token)"; good_rc=$?
    none="$(probe)"; none_rc=$?
    bad="$(probe OMNIGRAPH_TOKEN=wrong)"; bad_rc=$?
    nograph="$(env -u OMNIGRAPH_TOKEN OMNIGRAPH_TOKEN=good-token python3 tools/check-omnigraph.py --base-url "$base" --graph nope 2>&1)"; nograph_rc=$?
    printf 'OMNIGRAPH_TOKEN=good-token\n' >"$stub/file.env"
    fromfile="$(env -u OMNIGRAPH_TOKEN AUTOOS_OMNIGRAPH_ENV_FILE="$stub/file.env" python3 tools/check-omnigraph.py --base-url "$base" 2>&1)"; file_rc=$?
    kill "$stub_pid" 2>/dev/null; wait "$stub_pid" 2>/dev/null
    rm -rf "$stub"
    unset -f probe
    got="$good_rc$none_rc$bad_rc$nograph_rc$file_rc"
    ok=1
    [[ "$got" == "01110" ]] || ok=0
    [[ "$good" == *"project autoos"* ]] || ok=0
    [[ "$none" == *"OMNIGRAPH_TOKEN is not set"* ]] || ok=0
    [[ "$bad" == *"401"* ]] || ok=0
    [[ "$nograph" == *"'nope' does not exist"* ]] || ok=0
    [[ "$fromfile" == *"token from"* ]] || ok=0
    [[ "$good$bad$fromfile" != *"good-token"* ]] || ok=0
    if (( ok )); then pass; else fail "rc=$got good=[$good] none=[$none] bad=[$bad] nograph=[$nograph] file=[$fromfile]"; fi
fi

# The D9 bridge benchmark (SPEC-OMNI A5). CI can neither install npm packages
# nor reach a server, so both checks below run against `--fake-server` and the
# fake stdio bridge fixture — the live 16-parallel cold/warm run is the
# operator's step and its numbers go in docs/omnigraph.md.
if it "omnigraph bridge benchmark: unit tests pass offline (fake server + fake bridge)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_check_omnigraph_bridge.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

if it "omnigraph bridge benchmark: the sh wrapper drives a fake-server run to exit 0"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(tools/check-omnigraph-bridge.sh --fake-server \
                  --bridge-cmd "python3 tests/fixtures/omnigraph_bridge_stub.py" \
                  --parallel 2 --graph-id autoos --warm 2>&1)"; rc=$?
        if [[ $rc -eq 0 && "$out" == *"healthy 2/2"* && "$out" == *"whoami ok 2/2"* ]]; then pass
        else fail "rc=$rc [$out]"; fi
    fi
fi

if it "both healthchecks probe omnigraph through the live probe"; then
    ok=1
    for f in configuration/healthcheck.sh configuration/healthcheck.ps1; do
        grep -q 'check-omnigraph.py' "$f" || { ok=0; echo "$f does not run the probe" >&2; }
    done
    if (( ok )); then pass; else fail "an omnigraph probe is missing"; fi
fi

if it "zed's omnigraph context server carries the base URL and graph id the bridge requires"; then
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 route_zed_to_proxy >/dev/null 2>&1 )
    got="$(python3 -c "
import json,sys
e=json.load(open(sys.argv[1],encoding='utf-8'))['context_servers']['omnigraph'].get('env',{})
print(bool(e.get('OMNIGRAPH_BASE_URL')), e.get('OMNIGRAPH_GRAPH_ID'), 'OMNIGRAPH_TOKEN' in e)
" "$scratch/.config/zed/settings.json" 2>&1)"
    rm -rf "$scratch"
    assert_eq "$got" "True autoos False"
fi

# The omnigraph_url answer decides where every client's bridge points.
# install_agent_skills honoured it; the Zed writer hardcoded localhost:8080, so
# a machine with a remote omnigraph got Zed pointed at nothing. One helper,
# omnigraph_base_url, now derives it for both.
# zed_omni_url <answer or empty>: the OMNIGRAPH_BASE_URL route_zed_to_proxy writes.
zed_omni_url() {
    local scratch out
    scratch="$(mktemp -d)"
    (
        AUTOOS_ANSWERS=()
        [[ -z "$1" ]] || AUTOOS_ANSWERS[omnigraph_url]="$1"
        SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 route_zed_to_proxy >/dev/null 2>&1
    )
    out="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['context_servers']['omnigraph']['env']['OMNIGRAPH_BASE_URL'])
" "$scratch/.config/zed/settings.json" 2>&1)"
    rm -rf "$scratch"
    printf '%s' "$out"
}

if it "zed: the omnigraph entry uses the omnigraph_url answer"; then
    ok=1
    got="$(zed_omni_url "https://graph.example.invalid:9000/")"
    [[ "$got" == "https://graph.example.invalid:9000" ]] || { ok=0; echo "the Zed entry says [$got], not the answer without its trailing slash" >&2; }
    helper="$( ( AUTOOS_ANSWERS=(); AUTOOS_ANSWERS[omnigraph_url]="https://graph.example.invalid:9000/"; omnigraph_base_url ) 2>&1)"
    [[ "$helper" == "https://graph.example.invalid:9000" ]] || { ok=0; echo "omnigraph_base_url says [$helper]" >&2; }
    if (( ok )); then pass; else fail "the Zed omnigraph entry ignores the omnigraph_url answer"; fi
fi

# The contract is "no trailing slash", not "one slash off": consumers append
# /paths to the base, and the Windows side does .TrimEnd('/') (every trailing
# slash). A pasted "http://host//" must not survive as "http://host/".
if it "omnigraph: omnigraph_base_url strips every trailing slash and nothing else"; then
    ok=1
    while IFS='|' read -r given want; do
        got="$( ( AUTOOS_ANSWERS=(); AUTOOS_ANSWERS[omnigraph_url]="$given"; omnigraph_base_url ) 2>&1)"
        [[ "$got" == "$want" ]] || { ok=0; echo "omnigraph_url [$given]: the helper says [$got], expected [$want]" >&2; }
    done <<'CASES'
http://host|http://host
http://host/|http://host
http://host//|http://host
http://host:8080///|http://host:8080
http://host/graph//|http://host/graph
http://host//graph/|http://host//graph
/|http://localhost:8080
//|http://localhost:8080
CASES
    # ...and the Zed writer, the other consumer, writes the same stripped value.
    got="$(zed_omni_url "https://graph.example.invalid:9000//")"
    [[ "$got" == "https://graph.example.invalid:9000" ]] || { ok=0; echo "the Zed entry says [$got] for a doubled trailing slash" >&2; }
    if (( ok )); then pass; else fail "omnigraph_base_url leaves a trailing slash on the base URL"; fi
fi

if it "zed: the omnigraph entry defaults to localhost:8080"; then
    ok=1
    got="$(zed_omni_url "")"
    [[ "$got" == "http://localhost:8080" ]] || { ok=0; echo "the Zed entry says [$got]" >&2; }
    # Both consumers take the default from the one helper, so they cannot drift.
    helper="$( ( AUTOOS_ANSWERS=(); omnigraph_base_url ) 2>&1)"
    [[ "$helper" == "http://localhost:8080" ]] || { ok=0; echo "omnigraph_base_url says [$helper]" >&2; }
    [[ "$(grep -c '\$(omnigraph_base_url)' lib/linux/install.sh)" -ge 2 ]] \
        || { ok=0; echo "the helper is not called by both install_agent_skills and route_zed_to_proxy" >&2; }
    if (( ok )); then pass; else fail "the omnigraph default differs between the Zed writer and install_agent_skills"; fi
fi

if it "the omnigraph env file is private, merged, linked for systemd, and stable on a re-run"; then
    tmp="$(mktemp -d)"
    printf 'KEEP_ME=1\nOMNIGRAPH_BASE_URL=http://old.invalid\n' >"$tmp/.autoos-omnigraph.env"
    touch "$tmp/.bashrc"
    run_env() { ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN="test-token-value"; docker() { return 1; }; write_omnigraph_env "http://localhost:8080" ) 2>&1; }
    first="$(run_env)"; second="$(run_env)"
    mode="$(stat -c '%a' "$tmp/.autoos-omnigraph.env")"
    body="$(sort "$tmp/.autoos-omnigraph.env" | tr '\n' ' ')"
    link="$(readlink "$tmp/.config/environment.d/60-autoos-omnigraph.conf")"
    backups="$(ls "$tmp"/.autoos-omnigraph.env.autoos-backup-* 2>/dev/null | wc -l)"
    bmode="$(stat -c '%a' "$tmp"/.autoos-omnigraph.env.autoos-backup-* 2>/dev/null | head -1)"
    rc_blocks="$(grep -c 'AutoOS:omnigraph-env' "$tmp/.bashrc")"
    rm -rf "$tmp"
    unset -f run_env
    assert_eq "$mode|$body|${link##*/}|$backups|$bmode|$rc_blocks|$([[ "$second" == *unchanged* ]] && echo stable)|$([[ "$first$second" == *test-token-value* ]] && echo LEAK)" \
        "600|KEEP_ME=1 OMNIGRAPH_BASE_URL=http://localhost:8080 OMNIGRAPH_TOKEN=test-token-value |.autoos-omnigraph.env|1|600|1|stable|"
fi

# Review finding 2026-09-25: the rc line used to `set -a; . file`, so a value
# holding $(...) executed in every new shell. It must read the three keys
# literally, in bash AND zsh, and replace an older AutoOS line in place.
if it "the omnigraph rc line loads values literally and replaces the old sourcing line"; then
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://x$(touch %s/pwned)\nOMNIGRAPH_TOKEN=tok=with=equals\nOTHER=$(touch %s/pwned2)\n' "$tmp" "$tmp" >"$tmp/.autoos-omnigraph.env"
    # An rc file that already carries the v1 sourcing line, as an older run wrote it.
    printf '# mine\n[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env\n' >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    # Keep the env file as crafted: only the rc handling is under test here.
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN="tok=with=equals"; docker() { return 1; }
      write_omnigraph_env 'http://x$(touch '"$tmp"'/pwned)' ) >/dev/null 2>&1
    got_b="$(env -i HOME="$tmp" bash -c ". \"$tmp/.bashrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    got_z=""
    if command -v zsh >/dev/null; then
        got_z="$(env -i HOME="$tmp" zsh -f -c ". \"$tmp/.zshrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    fi
    v1="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
    v2="$(grep -c 'AutoOS:omnigraph-env-v2' "$tmp/.bashrc" || true)"
    pwned="$(ls "$tmp"/pwned* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    want='http://x$(touch '"${tmp}"'/pwned)|tok=with=equals'
    ok=1
    [[ "$pwned" == 0 ]] || { ok=0; echo "a value was executed" >&2; }
    [[ "$got_b" == "$want" ]] || { ok=0; echo "bash got: $got_b" >&2; }
    [[ -z "$got_z" || "$got_z" == "$want" ]] || { ok=0; echo "zsh got: $got_z" >&2; }
    [[ "$v1" == 0 && "$v2" == 1 ]] || { ok=0; echo "v1=$v1 v2=$v2 (old line not replaced)" >&2; }
    if (( ok )); then pass; else fail "the omnigraph rc line is not a literal reader"; fi
fi

# Re-review 2026-09-25: CRLF values, a last line without a newline, and a file
# that carries BOTH the v1 sourcing line and the v2 reader (v1 must go).
if it "the omnigraph rc reader copes with CRLF and a missing last newline, and purges v1 next to v2"; then
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://crlf\r\nOMNIGRAPH_TOKEN=last-line-no-newline' >"$tmp/.autoos-omnigraph.env"
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    printf '%s\n' "$v1" >"$tmp/.bashrc"
    # First write_omnigraph_env run adds v2 by replacing v1; then plant v1 again
    # next to v2 (a stale copy) and run once more: v1 must be purged.
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN=""; docker() { return 1; }
      replace_or_append_marked_line "$tmp/.bashrc" "AutoOS:omnigraph-env" "AutoOS:omnigraph-env-v2" "$(omnigraph_rc_line)" ) >/dev/null 2>&1
    printf '%s\n' "$v1" >>"$tmp/.bashrc"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      replace_or_append_marked_line "$tmp/.bashrc" "AutoOS:omnigraph-env" "AutoOS:omnigraph-env-v2" "unused" ) >/dev/null 2>&1
    got="$(env -i HOME="$tmp" bash -c ". \"$tmp/.bashrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    v1n="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
    v2n="$(grep -c 'AutoOS:omnigraph-env-v2' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    assert_eq "$got|$v1n|$v2n" "http://crlf|last-line-no-newline|0|1"
fi

if it "the omnigraph token falls back to the local server container, else is reported missing"; then
    tmp="$(mktemp -d)"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      unset OMNIGRAPH_TOKEN
      has_cmd() { [[ "$1" == docker ]] || command -v "$1" >/dev/null 2>&1; }
      docker() { printf 'PATH=/bin\nOMNIGRAPH_SERVER_BEARER_TOKEN=from-container\n'; }
      write_omnigraph_env "http://localhost:8080" >/dev/null 2>&1 )
    from_container="$(grep -c '^OMNIGRAPH_TOKEN=from-container$' "$tmp/.autoos-omnigraph.env")"
    rm -rf "$tmp"; tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      unset OMNIGRAPH_TOKEN
      docker() { return 1; }
      write_omnigraph_env "http://localhost:8080" ) 2>&1)"
    no_token_line="$(grep -c '^OMNIGRAPH_TOKEN=' "$tmp/.autoos-omnigraph.env")"
    rm -rf "$tmp"
    assert_eq "$from_container|$no_token_line|$([[ "$out" == *"OMNIGRAPH_TOKEN is not set"* ]] && echo warned)" "1|0|warned"
fi

if it "the omnigraph env file is announced, not written, in a dry run"; then
    tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=1; write_omnigraph_env "http://localhost:8080" ) 2>&1)"
    left="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    assert_eq "$left|$([[ "$out" == *"would write"* ]] && echo announced)" "0|announced"
fi

if it "antigravity's omnigraph entry pins a graph id (the bridge refuses to start without one)"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/Documents/code/agent-skills"
    spec="$( (
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset OMNIGRAPH_GRAPH_ID OMNIGRAPH_TOKEN
        clone_or_update() { :; }
        install_mcp_graphify() { :; }; install_mcp_serena() { :; }
        install_mcp_playwright() { :; }; install_mcp_context7() { :; }
        mcp_has_server() { return 1; }; enable_project_mcp_server() { :; }
        write_omnigraph_env() { :; }
        register_antigravity_mcp_server() { [[ "$1" == omnigraph ]] && printf '%s\n' "$2" >&3; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        install_agent_skills >/dev/null 2>&1
    ) 3>&1 )"
    rm -rf "$tmp"
    got="$(printf '%s' "$spec" | python3 -c "import json,sys;e=json.load(sys.stdin)['env'];print(e.get('OMNIGRAPH_GRAPH_ID'),'OMNIGRAPH_TOKEN' in e)" 2>&1)"
    assert_eq "$got" "autoos False"
fi

# The omnigraph_url answer is user input. install_agent_skills used to splice it
# into the SOURCE of a `python3 -c "..."` string, so a quote, a backslash or
# Python code in the answer broke the literal or ran (`' + os.system(...) + '`).
# It travels in the environment now (like the Zed writer's OMNI_BASE) and must
# arrive byte for byte. Every marker file below is what a payload would create.
if it "omnigraph: a hostile omnigraph_url answer is data, never python source"; then
    scratch="$(mktemp -d)"; ok=1
    payloads=(
        "http://x/\"; touch $scratch/pwned1; echo \""
        "http://x/'\$(touch $scratch/pwned2)"
        "http://x/'\`touch $scratch/pwned3\`"
        "http://x/' + str(__import__('os').system('touch $scratch/pwned4')) + '"
        'http://x/a\nb\\c\x41'
    )
    i=0
    for url in "${payloads[@]}"; do
        i=$((i + 1))
        (
            # AUTOOS_ROOT is an empty scratch dir: the repo's own .claude/skills
            # links are never touched.
            SYS_HOME="$scratch/home$i"; AUTOOS_ROOT="$scratch/root$i"; AUTOOS_DRY_RUN=0
            mkdir -p "$SYS_HOME/Documents/code/agent-skills" "$AUTOOS_ROOT"
            unset OMNIGRAPH_GRAPH_ID OMNIGRAPH_TOKEN
            AUTOOS_ANSWERS=(); AUTOOS_ANSWERS[omnigraph_url]="$url"
            clone_or_update() { :; }
            install_mcp_graphify() { :; }; install_mcp_serena() { :; }
            install_mcp_playwright() { :; }; install_mcp_context7() { :; }
            mcp_has_server() { return 1; }; enable_project_mcp_server() { :; }
            write_omnigraph_env() { :; }
            register_antigravity_mcp_server() { [[ "$1" == omnigraph ]] && printf '%s' "$2" >"$scratch/spec-$i.json"; return 0; }
            omnigraph_readiness() { return 0; }
            install_agent_skills >/dev/null 2>&1
        )
        if [[ ! -s "$scratch/spec-$i.json" ]]; then
            ok=0; echo "payload $i [$url]: no omnigraph spec was produced (the python source broke)" >&2
        else
            got="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["env"]["OMNIGRAPH_BASE_URL"])' "$scratch/spec-$i.json")"
            [[ "$got" == "$url" ]] || { ok=0; echo "payload $i: the config holds [$got], not [$url]" >&2; }
        fi
    done
    compgen -G "$scratch/pwned*" >/dev/null && { ok=0; echo "a payload ran: $(cd "$scratch" && ls -d pwned* | tr '\n' ' ')" >&2; }
    rm -rf "$scratch"
    if (( ok )); then pass; else fail "the omnigraph_url answer is interpolated into python source"; fi
fi

if it "Antigravity MCP config is merged, not replaced"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.gemini/config"
    printf '%s' '{"mcpServers":{"existing":{"command":"node","args":["index.js"]}}}' \
        >"$tmp/.gemini/config/mcp_config.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        register_antigravity_mcp_server "playwright" '{"command":"npx","args":["-y","@playwright/mcp"]}' >/dev/null 2>&1
    )
    got="$(python3 -c "
import json,sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
servers = d.get('mcpServers', {})
print(','.join(sorted(servers.keys())))
" "$tmp/.gemini/config/mcp_config.json")"
    rm -rf "$tmp"
    if [[ "$got" == "existing,playwright" ]]; then pass
    else fail "expected existing and playwright, got: $got"; fi
fi

if it "register_antigravity_mcp_server is idempotent and creates backup"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.gemini/config"
    printf '%s' '{"mcpServers":{"existing":{"command":"node"}}}' >"$tmp/.gemini/config/mcp_config.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        register_antigravity_mcp_server "serena" '{"command":"uvx"}' >/dev/null 2>&1
        register_antigravity_mcp_server "serena" '{"command":"uvx"}' >/dev/null 2>&1
    )
    backups=( "$tmp"/.gemini/config/mcp_config.json.autoos-backup-* )
    has_backup=0
    [[ -f "${backups[0]}" ]] && has_backup=1
    rm -rf "$tmp"
    if (( has_backup )); then pass
    else fail "backup was not created before edit"; fi
fi

if it "agent-skills links skills to Antigravity and Claude Code"; then
    tmp="$(mktemp -d)"
    # Create skill in repo's .agents/skills (new location)
    mkdir -p "$tmp/.agents/skills/test-skill"
    printf -- '---\nname: test-skill\ndescription: test\n---\n' >"$tmp/.agents/skills/test-skill/SKILL.md"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        install_agent_skills >/dev/null 2>&1
    )
    ok=1
    [[ -e "$tmp/.gemini/config/skills/test-skill/SKILL.md" ]] || ok=0
    [[ -e "$tmp/.claude/skills/test-skill/SKILL.md" ]] || ok=0
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "skills were not linked to Antigravity or Claude Code"; fi
fi

if it "repo skills link into project .claude/skills and win as skills source"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.agents/skills/demo-skill"
    printf -- '---\nname: demo-skill\ndescription: demo\n---\n' >"$tmp/.agents/skills/demo-skill/SKILL.md"
    mkdir -p "$tmp/Documents/code/agent-skills/skills/old-skill"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        install_agent_skills >/dev/null 2>&1
    )
    ok=1
    [[ -e "$tmp/.claude/skills/demo-skill/SKILL.md" ]] || ok=0
    [[ "$(AUTOOS_ROOT="$tmp" autoos_skills_source)" == "$tmp/.agents/skills" ]] || ok=0
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "vendored skills did not win or were not linked"; fi
fi

if it "install_agent_skills records a refused project-server approval for the summary"; then
    # A post-install step that refuses to touch a user's file (no backup, no
    # write) must still be counted: setup.sh folds autoos_record_failure ids
    # into its failed count and exit code, so the summary cannot read "done"
    # over a change that never happened. Both project servers approved here map
    # to one id, and the recorder is idempotent.
    tmp="$(mktemp -d)"; ok=1
    mkdir -p "$tmp/Documents/code/agent-skills/.claude" "$tmp/root/.claude"
    printf '{}\n' >"$tmp/Documents/code/agent-skills/.mcp.json"
    printf '{}\n' >"$tmp/root/.mcp.json"
    printf '{"theme":"mine"}\n' >"$tmp/Documents/code/agent-skills/.claude/settings.local.json"
    printf '{"theme":"mine"}\n' >"$tmp/root/.claude/settings.local.json"
    out="$( (
        SYS_HOME="$tmp"; AUTOOS_ROOT="$tmp/root"; AUTOOS_DRY_RUN=0
        clone_or_update() { :; }
        install_mcp_graphify() { :; }; install_mcp_serena() { :; }
        install_mcp_playwright() { :; }; install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        write_omnigraph_env() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        backup_file() { return 1; }
        install_agent_skills >/dev/null 2>&1
        printf 'recorded: %s\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    ) 2>&1 )"
    [[ "$out" == *"recorded: agent-skills"* ]] || { ok=0; echo "the refusal was not recorded: [${out:0:400}]" >&2; }
    [[ "$out" != *"agent-skills agent-skills"* ]] || { ok=0; echo "the id was recorded twice" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "install_agent_skills did not record a refused project-server approval"; fi
fi

# install_agent_skills calls four catalog postInstalls directly, and
# route_detected_clis_to_gateway calls Claude Code's — bare, those non-zero
# returns reached setup.sh's `set -euo pipefail` and ended the run. Each now
# records its own component id instead, so the summary and the exit code see it
# while the step finishes its remaining work. The Playwright double below records
# its id itself the way the real function does, so this also proves the recorder
# keeps one line for one broken wiring.
if it "install_agent_skills: a failing mcp sub-step is recorded for the summary and the step carries on"; then
    tmp="$(mktemp -d)"; ok=1
    mkdir -p "$tmp/Documents/code/agent-skills" "$tmp/root"
    out="$( (
        SYS_HOME="$tmp"; AUTOOS_ROOT="$tmp/root"; AUTOOS_DRY_RUN=0
        AUTOOS_EXTRA_FAILURES=()
        clone_or_update() { :; }
        write_omnigraph_env() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        mcp_has_server() { : >"$tmp/mark_later"; return 1; }
        install_mcp_graphify() { : >"$tmp/mark_graphify"; return 0; }
        install_mcp_serena() { : >"$tmp/mark_serena"; return 1; }
        install_mcp_playwright() { : >"$tmp/mark_playwright"; autoos_record_failure mcp-playwright; return 1; }
        install_mcp_context7() { : >"$tmp/mark_context7"; return 1; }
        install_agent_skills >/dev/null 2>&1
        step_rc=$?
        printf 'agent_skills_rc=%s\n' "$step_rc"
        printf 'recorded: %s\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    ) 2>&1 )"
    for mark in serena playwright context7 later; do
        [[ -e "$tmp/mark_$mark" ]] \
            || { ok=0; echo "the step never reached mark_$mark (a failing sub-step stopped the run)" >&2; }
    done
    [[ "$out" == *"recorded:"*"mcp-serena"* && "$out" == *"recorded:"*"mcp-playwright"* && "$out" == *"recorded:"*"mcp-context7"* ]] \
        || { ok=0; echo "a failing sub-step was not recorded: $(printf '%s\n' "$out" | grep '^recorded')" >&2; }
    [[ "$(printf '%s\n' "$out" | grep '^recorded:' | grep -o 'mcp-playwright' | wc -l | tr -d ' ')" == "1" ]] \
        || { ok=0; echo "mcp-playwright was counted twice: $(printf '%s\n' "$out" | grep '^recorded')" >&2; }
    [[ "$out" == *"agent_skills_rc=0"* ]] \
        || { ok=0; echo "install_agent_skills propagated a non-zero code (it must not: the fold reports it)" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "a failing mcp sub-step did not become a recorded failure"; fi
fi

if it "route_detected_clis_to_gateway: a failing Claude routing step is recorded for the summary"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; ok=1
    out="$( (
        SYS_HOME="$tmp"; AUTOOS_ROOT="$tmp"; AUTOOS_DRY_RUN=0; AUTOOS_EXTRA_FAILURES=()
        # A keys file of its own: the real configuration/api-keys.yml is never read.
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        unset OMNIROUTE_API_KEY AUTOOS_OMNIROUTE_KEY
        claude() { return 0; }
        has_cmd() { [[ "$1" == claude ]]; }
        route_claude_to_gateway() { : >"$tmp/mark_route"; return 1; }
        route_detected_clis_to_gateway >/dev/null 2>&1
        printf 'recorded: %s\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    ) 2>&1 )"
    [[ -e "$tmp/mark_route" ]] || { ok=0; echo "the routing step never ran: [${out:0:300}]" >&2; }
    [[ "$out" == *"recorded: claude-code"* ]] \
        || { ok=0; echo "a failing Claude routing was not recorded: $(printf '%s\n' "$out" | grep '^recorded')" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "route_claude_to_gateway's failure was not recorded"; fi
    fi
fi

# ─── OpenHands: repo skills mirrored per skill, idempotent settings writer ──
# oh_setup_run <home> <repo> [fn]: setup_openhands_config (or `fn`) in a hermetic
# subshell - scratch HOME and AUTOOS_ROOT, no gateway or provider key, no network
# - with stdout and stderr merged. AUTOOS_KEYS_FILE points at a file that does not
# exist so the repo's own keys file is never consulted.
oh_setup_run() {
    local home="$1" repo="$2" fn="${3:-setup_openhands_config}"
    mkdir -p "$home"
    (
        SYS_HOME="$home"; AUTOOS_DRY_RUN=0
        if [[ -n "$repo" ]]; then AUTOOS_ROOT="$repo"; else unset AUTOOS_ROOT; fi
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        unset AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        export AUTOOS_KEYS_FILE="$home/no-keys.yml"
        curl() { return 6; }
        "$fn"
    ) 2>&1
}

# oh_skill_repo <repo>: a scratch AUTOOS_ROOT with two skills (alpha, beta) and
# one directory without a SKILL.md (nofile) that is not a skill.
oh_skill_repo() {
    local repo="$1" n
    for n in alpha beta; do
        mkdir -p "$repo/.agents/skills/$n"
        printf -- '---\nname: %s\ndescription: demo\n---\n' "$n" >"$repo/.agents/skills/$n/SKILL.md"
    done
    mkdir -p "$repo/.agents/skills/nofile"
    printf 'not a skill\n' >"$repo/.agents/skills/nofile/README.md"
}

if it "openhands: links every repo skill into ~/.openhands/skills"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    dest="$tmp/home/.openhands/skills"; problems=""
    { [[ -d "$dest" && ! -L "$dest" ]] || problems+="[$dest is not a real directory] "; }
    for n in alpha beta; do
        [[ -L "$dest/$n" && "$(readlink "$dest/$n")" == "$tmp/repo/.agents/skills/$n" ]] \
            || problems+="[$n is not a link to the repo skill (got: $(readlink "$dest/$n" 2>/dev/null || echo none))] "
        [[ -f "$dest/$n/SKILL.md" ]] || problems+="[$n/SKILL.md unreadable through the link] "
        [[ "$out" == *"linked $n"* ]] || problems+="[no 'linked $n' line] "
    done
    { [[ ! -e "$dest/nofile" && ! -L "$dest/nofile" ]] || problems+="[nofile (no SKILL.md) was linked] "; }
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a second run reports skipped and changes nothing"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    oh_setup_run "$tmp/home" "$tmp/repo" >/dev/null
    dest="$tmp/home/.openhands/skills"
    before="$(stat -c '%y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    after="$(stat -c '%y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    problems=""
    [[ "$before" == "$after" ]] || problems+="[the skills directory or a link changed: $before -> $after] "
    grep -Eq '(^|[^[:alnum:]])(linked|repointed) [a-z]' <<<"$out" && problems+="[second run printed a linked/repointed line] "
    [[ "$out" == *skipped* ]] || problems+="[second run never says skipped] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: keeps a user's own skill"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"
    mkdir -p "$dest/alpha" "$dest/mine"
    printf 'my own alpha\n' >"$dest/alpha/SKILL.md"
    printf 'my own skill\n'  >"$dest/mine/SKILL.md"
    ln -s /nonexistent-user-skill "$dest/foreign"
    snap() { ( cd "$dest" && find alpha mine -type f -exec sha256sum {} + | sort; readlink foreign; ls -A ) ; }
    before="$(snap)"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    after="$(snap)"
    problems=""
    [[ -d "$dest/alpha" && ! -L "$dest/alpha" ]] || problems+="[the user's own alpha is no longer a real directory] "
    [[ "$(readlink "$dest/beta" 2>/dev/null)" == "$tmp/repo/.agents/skills/beta" ]] || problems+="[beta was not linked next to the user's skills] "
    # beta is the one new entry: nothing else may differ from before.
    [[ "$(grep -v '^beta$' <<<"$after")" == "$before" ]] || problems+="[the user's skills changed: $before -> $after] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: repairs a dangling link into the repo"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"; mkdir -p "$dest"
    # Ours: the exact shape link_skill_dirs creates (<checkout>/.agents/skills/<name>)
    # into a checkout that has since moved or been renamed, so it dangles.
    ln -s "$tmp/moved-checkout/.agents/skills/alpha" "$dest/alpha"
    ln -s /nonexistent-elsewhere/beta "$dest/beta"                 # not ours, dangling: hands off
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$dest/alpha")" == "$tmp/repo/.agents/skills/alpha" ]] || problems+="[alpha still points at $(readlink "$dest/alpha")] "
    [[ -f "$dest/alpha/SKILL.md" ]] || problems+="[alpha does not resolve after the repair] "
    [[ "$(readlink "$dest/beta")" == "/nonexistent-elsewhere/beta" ]] || problems+="[a foreign dangling link was rewritten to $(readlink "$dest/beta")] "
    [[ "$out" == *"repointed alpha"* ]] || problems+="[no 'repointed alpha' line] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

# Only a link this function made is ours (AGENTS.md hard rule 4): it dangles AND
# has the exact shape .../.agents/skills/<name> for the same skill. A live link,
# or a dangling one of another shape, is the user's and stays as it is - even
# when it points inside the repo's own .agents directory.
if it "openhands: a live link of your own into .agents/custom is kept"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"; mkdir -p "$dest" "$tmp/repo/.agents/custom/alpha"
    printf 'my own alpha\n' >"$tmp/repo/.agents/custom/alpha/SKILL.md"
    ln -s "$tmp/repo/.agents/custom/alpha" "$dest/alpha"           # live, inside the repo's .agents, not our shape
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$dest/alpha")" == "$tmp/repo/.agents/custom/alpha" ]] || problems+="[the user's live link now points at $(readlink "$dest/alpha")] "
    [[ "$(cat "$dest/alpha/SKILL.md" 2>/dev/null)" == "my own alpha" ]] || problems+="[alpha no longer resolves to the user's own skill] "
    [[ "$out" != *"repointed alpha"* ]] || problems+="[the live link was reported as repointed] "
    [[ "$out" == *"kept $dest/alpha"* ]] || problems+="[no 'kept ...alpha' line: the user is not told it was left alone] "
    [[ "$(readlink "$dest/beta")" == "$tmp/repo/.agents/skills/beta" ]] || problems+="[beta was not linked next to the user's link] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a live link into another checkout's skills is kept"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"; mkdir -p "$dest"
    # Another checkout with the same skills layout. One sits beside this repo,
    # one is nested under this repo's own .agents directory (a worktree kept
    # there): both are live and both have the shape we would create.
    oh_skill_repo "$tmp/other"
    oh_skill_repo "$tmp/repo/.agents/nested"
    ln -s "$tmp/repo/.agents/nested/.agents/skills/alpha" "$dest/alpha"
    ln -s "$tmp/other/.agents/skills/beta" "$dest/beta"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$dest/alpha")" == "$tmp/repo/.agents/nested/.agents/skills/alpha" ]] || problems+="[the link into the nested checkout now points at $(readlink "$dest/alpha")] "
    [[ "$(readlink "$dest/beta")" == "$tmp/other/.agents/skills/beta" ]] || problems+="[the link into the other checkout now points at $(readlink "$dest/beta")] "
    [[ "$out" != *"repointed"* ]] || problems+="[a live link was reported as repointed] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a dangling link of another shape is kept"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"; mkdir -p "$dest"
    ln -s /nonexistent/other/alpha "$dest/alpha"                    # dangling, outside the repo
    ln -s "$tmp/repo/.agents/skills/beta-renamed" "$dest/beta"      # dangling, inside the repo, not the shape of beta
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$dest/alpha")" == "/nonexistent/other/alpha" ]] || problems+="[the dangling link outside the repo now points at $(readlink "$dest/alpha")] "
    [[ "$(readlink "$dest/beta")" == "$tmp/repo/.agents/skills/beta-renamed" ]] || problems+="[the dangling link of another shape now points at $(readlink "$dest/beta")] "
    [[ "$out" != *"repointed"* ]] || problems+="[a link of another shape was reported as repointed] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: an existing whole-dir symlink is not written through"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/clone-skills" "$tmp/home/.openhands"
    ln -s "$tmp/clone-skills" "$tmp/home/.openhands/skills"     # the old whole-dir layout
    repo_before="$(ls -A "$tmp/repo/.agents/skills")"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$tmp/home/.openhands/skills")" == "$tmp/clone-skills" ]] || problems+="[the whole-dir link was changed] "
    [[ -z "$(ls -A "$tmp/clone-skills")" ]] || problems+="[links were written through it into the clone: $(ls -A "$tmp/clone-skills" | tr '\n' ' ')] "
    [[ "$(ls -A "$tmp/repo/.agents/skills")" == "$repo_before" ]] || problems+="[the repo's skills directory gained entries] "
    [[ "$(grep -c 'rm ' <<<"$out")" == 1 ]] || problems+="[expected one warning naming the rm command, got $(grep -c 'rm ' <<<"$out")] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a dry run links nothing"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    out="$( ( AUTOOS_DRY_RUN=1; link_skill_dirs "$tmp/repo/.agents/skills" "$tmp/home/.openhands/skills" ) 2>&1 )"
    problems=""
    [[ ! -e "$tmp/home" ]] || problems+="[a dry run created $tmp/home] "
    [[ "$out" == *"would link"* ]] || problems+="[no 'would link' line: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: second run of the settings writer takes no backup and reports unchanged"; then
    tmp="$(mktemp -d)"
    oh="$tmp/home/.openhands"
    # Name, size and mtime of every backup: a bare count cannot tell a skipped
    # second run from one that overwrote a same-second backup.
    oh_backups() { find "$oh" -maxdepth 1 -name 'settings.json.autoos-backup-*' -printf '%f %s %T@\n' | sort; }
    oh_setup_run "$tmp/home" "" >/dev/null
    b1="$(oh_backups)"
    sum1="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    prof1="$(stat -c '%y' "$oh/profiles/openrouter-free.json")"
    out="$(oh_setup_run "$tmp/home" "")"
    b2="$(oh_backups)"
    sum2="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    prof2="$(stat -c '%y' "$oh/profiles/openrouter-free.json")"
    problems=""
    [[ "$b1" == "$b2" ]] || problems+="[the second run took or overwrote a settings.json backup: {$b1} -> {$b2}] "
    [[ "$sum1" == "$sum2" ]] || problems+="[the second run rewrote settings.json] "
    [[ "$prof1" == "$prof2" ]] || problems+="[the second run rewrote an identical profile] "
    [[ "$out" == *unchanged* ]] || problems+="[the second run never says unchanged] "
    [[ "$out" != *"written to"* ]] || problems+="[the second run still says 'written to'] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a BOM'd settings.json keeps the user's keys"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/home/.openhands"
    printf '\xef\xbb\xbf{"custom_user_key": "keep-me", "schema_version": 2}' >"$tmp/home/.openhands/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    got="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8-sig")); print(d.get("custom_user_key"), "agent_settings" in d)' "$tmp/home/.openhands/settings.json" 2>&1)"
    rm -rf "$tmp"
    assert_eq "$got" "keep-me True"
fi

if it "openhands: a backup of settings.json never overwrites an earlier one"; then
    tmp="$(mktemp -d)"
    oh="$tmp/home/.openhands"; mkdir -p "$oh"
    seed1='{"user_key": "first original"}'
    seed2='{"user_key": "second original"}'
    printf '%s' "$seed1" >"$oh/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    printf '%s' "$seed2" >"$oh/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    have1=0; have2=0; b=""
    for b in "$oh"/settings.json.autoos-backup-*; do
        [[ -f "$b" ]] || continue
        [[ "$(cat "$b")" == "$seed1" ]] && have1=1
        [[ "$(cat "$b")" == "$seed2" ]] && have2=1
    done
    rm -rf "$tmp"
    if (( have1 && have2 )); then pass
    else fail "a backup holding the user's file was overwritten (first original kept: $have1, second original kept: $have2)"; fi
fi

# A python step that fails writes nothing, and the function must say so instead
# of printing its success line. python3 is stubbed in a subshell (the function
# lookup wins over PATH), failing only the one call under test and passing the
# rest through to the real interpreter.
if it "openhands: a failing settings writer is reported, not called written"; then
    tmp="$(mktemp -d)"
    oh="$tmp/home/.openhands"; mkdir -p "$oh"
    printf '%s' '{"user_key": "mine"}' >"$oh/settings.json"
    before="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    out="$(
        python3() {
            # The settings/profiles script: "python3 - <openhands dir> <secrets> <models>" on stdin.
            if [[ "${1:-}" == "-" && "${2:-}" == */.openhands ]]; then
                cat >/dev/null; echo "stub: the settings script failed" >&2; return 1
            fi
            command python3 "$@"
        }
        oh_setup_run "$tmp/home" ""
    )"
    after="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    problems=""
    [[ "$before" == "$after" ]] || problems+="[settings.json changed although its writer failed] "
    ! compgen -G "$oh/settings.json.autoos-backup-*" >/dev/null || problems+="[a backup was taken for a file that was not written] "
    [[ "$out" == *"not written to $oh/settings.json"* ]] || problems+="[no warning naming $oh/settings.json, output ends: $(tail -n 3 <<<"$out")] "
    [[ "$out" != *"configuration and profiles written to"* ]] || problems+="[the success line was printed after the writer failed] "
    [[ "$out" != *"configuration unchanged"* ]] || problems+="[the 'unchanged' line was printed after the writer failed] "
    [[ "$out" != *"agent-harness openhands: "* ]] || problems+="[the agent harness ran after the writer failed and may have created settings.json content of its own] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a failing agent harness is reported, not called written"; then
    tmp="$(mktemp -d)"
    out="$(
        python3() {
            if [[ "${1:-}" == */lib/agent_harness.py ]]; then
                echo "stub: the harness failed" >&2; return 1
            fi
            command python3 "$@"
        }
        oh_setup_run "$tmp/home" ""
    )"
    problems=""
    [[ "$out" == *"agent harness not applied to OpenHands (exit 1)"* ]] || problems+="[no warning for the failed agent harness, output ends: $(tail -n 3 <<<"$out")] "
    [[ "$out" != *"configuration and profiles written to"* ]] || problems+="[the success line was printed after the agent harness failed] "
    [[ "$out" != *"configuration unchanged"* ]] || problems+="[the 'unchanged' line was printed after the agent harness failed] "
    [[ "$out" == *"only partly applied"* ]] || problems+="[the final message does not say the configuration is partial, output ends: $(tail -n 3 <<<"$out")] "
    [[ -f "$tmp/home/.openhands/settings.json" ]] || problems+="[the settings writer itself no longer ran] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

# A warning alone is not a result: setup.sh folds autoos_record_failure ids into
# the summary, and an unwarned refusal leaves nothing to fold, so the component
# still reads "installed" while its harness was never applied. Both writers take
# their id from run_post_install (AUTOOS_POST_COMPONENT), because one function
# serves two catalog ids for OpenCode.
if it "a refused agent harness records OpenHands as failed"; then
    tmp="$(mktemp -d)"
    oh_report_recorded() {
        run_post_install setup_openhands_config openhands
        printf 'recorded: %s\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    }
    out="$(
        python3() {
            if [[ "${1:-}" == */lib/agent_harness.py ]]; then
                echo "stub: the harness failed" >&2; return 1
            fi
            command python3 "$@"
        }
        oh_setup_run "$tmp/home" "" oh_report_recorded
    )"
    if [[ "$out" == *"recorded: openhands"* ]]; then
        pass
    else
        fail "the refused harness was not recorded: [$(tail -n 3 <<<"$out")]"
    fi
    rm -rf "$tmp"
fi

if it "a refused agent harness records OpenCode under the id being installed, once"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"
    oc="$tmp/.config/opencode"; mkdir -p "$oc"
    printf '{"model": "anthropic/mine"}\n' >"$oc/opencode.json"
    oc_report_recorded() {
        # Two config files, so the recorder runs twice for one component: the id
        # must still be listed once, or the fold counts one component twice.
        run_post_install setup_opencode_config opencode-cli
        printf 'recorded: %s\n' "${AUTOOS_EXTRA_FAILURES[*]:-}"
    }
    out="$(
        python3() {
            if [[ "${1:-}" == */lib/agent_harness.py ]]; then
                echo "agent-harness opencode: left alone, $* is a symlink" >&2; return 1
            fi
            command python3 "$@"
        }
        ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT"
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          opencode_is_v2() { return 1; }
          oc_report_recorded ) 2>&1
    )"
    problems=""
    [[ "$out" == *"recorded: opencode-cli"* ]] || problems+="[the refused harness was not recorded: $(tail -n 3 <<<"$out")] "
    [[ "$out" != *"opencode-cli opencode-cli"* ]] || problems+="[the id was recorded twice] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "custom_is_installed detects agent-skills via .agents/skills + .mcp.json (new location)"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.agents/skills/test-skill"
    printf -- '---\nname: test-skill\ndescription: test\n---\n' >"$tmp/.agents/skills/test-skill/SKILL.md"
    cp "$tmp/../../.mcp.json" "$tmp/.mcp.json" 2>/dev/null || printf '{"mcpServers":{}}\n' >"$tmp/.mcp.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_ROOT="$tmp"
        custom_is_installed agent-skills
    )
    rc_new=$?
    rm -rf "$tmp"
    if [[ $rc_new -eq 0 ]]; then pass
    else fail "rc_new=$rc_new (new location not detected)"; fi
fi

if it "custom_is_installed falls back to old clone path for agent-skills"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/Documents/code/agent-skills/skills/test-skill"
    printf -- '---\nname: test-skill\ndescription: test\n---\n' >"$tmp/Documents/code/agent-skills/skills/test-skill/SKILL.md"
    (
        SYS_HOME="$tmp"
        # No AUTOOS_ROOT set, no .agents/skills in home
        custom_is_installed agent-skills
    )
    rc_old=$?
    rm -rf "$tmp"
    if [[ $rc_old -eq 0 ]]; then pass
    else fail "rc_old=$rc_old (old clone fallback not detected)"; fi
fi

if it "custom_is_installed does not detect agent-skills when neither location exists"; then
    tmp="$(mktemp -d)"
    (
        SYS_HOME="$tmp"
        AUTOOS_ROOT="$tmp"  # Point to temp dir without .agents/skills
        custom_is_installed agent-skills
    )
    rc_none=$?
    rm -rf "$tmp"
    if [[ $rc_none -ne 0 ]]; then pass
    else fail "rc_none=$rc_none (should not detect when neither location exists)"; fi
fi

if it "custom_is_installed detects a populated native CAO home"; then
    tmp="$(mktemp -d)"
    (
        SYS_HOME="$tmp"
        mkdir -p "$tmp/.cao/db"
        custom_is_installed wsl-agent-home
    )
    rc_full=$?
    (
        SYS_HOME="$tmp"
        rm -rf "$tmp/.cao"
        custom_is_installed wsl-agent-home
    )
    rc_empty=$?
    rm -rf "$tmp"
    if [[ $rc_full -eq 0 && $rc_empty -ne 0 ]]; then pass
    else fail "rc_full=$rc_full rc_empty=$rc_empty"; fi
fi

if it "setup_wsl_agent_home is a no-op off WSL and dry-runnable on WSL"; then
    tmp="$(mktemp -d)"
    ( SYS_HOME="$tmp" SYS_IS_WSL=0 AUTOOS_DRY_RUN=0 setup_wsl_agent_home >/dev/null 2>&1 )
    rc_off=$?
    [[ -e "$tmp/.bashrc" ]] && rc_off=99
    ( SYS_HOME="$tmp" SYS_IS_WSL=1 AUTOOS_DRY_RUN=1 setup_wsl_agent_home >/dev/null 2>&1 )
    rc_dry=$?
    [[ -e "$tmp/.bashrc" ]] && rc_dry=99
    rm -rf "$tmp"
    if [[ $rc_off -eq 0 && $rc_dry -eq 0 ]]; then pass
    else fail "rc_off=$rc_off rc_dry=$rc_dry"; fi
fi

if it "backup collision: CAO relocation preserves existing same-second backups"; then
    tmp="$(mktemp -d)"
    legacy="$tmp/.aws/cli-agent-orchestrator"
    base="$legacy.backup-20260101-000000"
    mkdir -p "$legacy/db" "$base" "$tmp/bin"
    printf 'first\n' >"$legacy/db/state"
    printf 'original backup\n' >"$base/marker"
    printf '#!/bin/sh\nprintf "%%s\\n" 20260101-000000\n' >"$tmp/bin/date"
    printf '#!/bin/sh\nexit 1\n' >"$tmp/bin/python3"
    chmod +x "$tmp/bin/date" "$tmp/bin/python3"
    (
        PATH="$tmp/bin:$PATH"
        SYS_HOME="$tmp"
        SYS_IS_WSL=1
        AUTOOS_DRY_RUN=0
        setup_wsl_agent_home >/dev/null
    )
    rc_first=$?
    printf 'second\n' >"$legacy/db/state"
    (
        PATH="$tmp/bin:$PATH"
        SYS_HOME="$tmp"
        SYS_IS_WSL=1
        AUTOOS_DRY_RUN=0
        setup_wsl_agent_home >/dev/null
    )
    rc_second=$?
    ok=1
    [[ $rc_first -eq 0 && $rc_second -eq 0 ]] || { ok=0; echo "relocation failed: first=$rc_first second=$rc_second" >&2; }
    [[ "$(cat "$base/marker" 2>/dev/null)" == 'original backup' ]] || { ok=0; echo "existing backup was changed" >&2; }
    [[ "$(cat "$base-1/db/state" 2>/dev/null)" == first ]] || { ok=0; echo "first backup is missing or wrong" >&2; }
    [[ "$(cat "$base-2/db/state" 2>/dev/null)" == second ]] || { ok=0; echo "second backup is missing or wrong" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "CAO relocation overwrote or nested a same-second backup"; fi
fi

if it "backup collision: a failed CAO backup copy leaves no partial backup and does not abort the install"; then
    tmp="$(mktemp -d)"
    legacy="$tmp/.aws/cli-agent-orchestrator"
    mkdir -p "$legacy/db" "$tmp/bin"
    printf 'live\n' >"$legacy/db/state"
    printf '#!/bin/sh\nprintf "%%s\\n" 20260101-000000\n' >"$tmp/bin/date"
    printf '#!/bin/sh\nexit 1\n' >"$tmp/bin/python3"
    # cp stand-in: leaves a partial destination behind, then fails.
    printf '#!/bin/sh\nfor a; do last="$a"; done\nmkdir -p "$last/partial"\nexit 1\n' >"$tmp/bin/cp"
    chmod +x "$tmp/bin/date" "$tmp/bin/python3" "$tmp/bin/cp"
    (
        PATH="$tmp/bin:$PATH"
        SYS_HOME="$tmp"
        SYS_IS_WSL=1
        AUTOOS_DRY_RUN=0
        setup_wsl_agent_home >/dev/null 2>&1
    )
    rc=$?
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "rc=$rc (a failed backup must not abort the install queue)" >&2; }
    [[ ! -e "$legacy.backup-20260101-000000" ]] || { ok=0; echo "partial backup left behind" >&2; }
    [[ "$(cat "$legacy/db/state" 2>/dev/null)" == live ]] || { ok=0; echo "legacy state changed" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "failed CAO backup left a partial copy or aborted"; fi
fi

# Item 4 (rv2): the FIFO probe in setup_wsl_agent_home interpolated the CAO
# path into Python SOURCE (python3 -c "import os; os.mkfifo('$cao_legacy/...')").
# A CAO home whose path contained a single quote made that a SyntaxError, the
# probe "failed", and a perfectly good native ext4 home was falsely relocated:
# live state moved and a backup taken on a host where FIFOs work fine. The path
# now goes in as argv, so the shell never parses it and Python never sees it as
# source.
if it "setup_wsl_agent_home does not falsely relocate a CAO home whose path contains a single quote"; then
    tmp="$(mktemp -d)"
    home="$tmp/it's-here"
    legacy="$home/.aws/cli-agent-orchestrator"
    mkdir -p "$legacy/db"
    printf 'live\n' >"$legacy/db/state"
    (
        SYS_HOME="$home"
        SYS_IS_WSL=1
        AUTOOS_DRY_RUN=0
        setup_wsl_agent_home >/dev/null 2>&1
    )
    rc=$?
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "rc=$rc" >&2; }
    [[ "$(cat "$legacy/db/state" 2>/dev/null)" == live ]] || { ok=0; echo "legacy state was moved or changed" >&2; }
    n_bak="$(find "$home/.aws" -maxdepth 1 -name 'cli-agent-orchestrator.backup-*' 2>/dev/null | wc -l | tr -d ' ')"
    [[ "$n_bak" == 0 ]] || { ok=0; echo "a false relocation took $n_bak backup(s)" >&2; }
    [[ ! -e "$legacy/.autoos-fifo-probe" ]] || { ok=0; echo "the FIFO probe was left behind" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "a single quote in the CAO path caused a false relocation"; fi
fi

# ─── User-scope skill targets: ~/.agents/skills and ~/.codex/skills ──

# oh_skill_repo creates a scratch AUTOOS_ROOT with two skills.
# Reused from the openhands tests above.

if it "install_agent_skills does NOT clone agent-skills repo (no clone_or_update)"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    # Create old clone to verify it's left alone
    mkdir -p "$tmp/Documents/code/agent-skills/skills/old-skill"
    printf 'old clone\n' >"$tmp/Documents/code/agent-skills/skills/old-skill/README.md"
    clone_flag="$tmp/clone_flag"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { echo called >"$clone_flag"; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills >/dev/null 2>&1
    )
    problems=""
    [[ ! -f "$clone_flag" ]] || problems+="[clone_or_update was called (should not clone)] "
    # Old clone should be left alone
    [[ -f "$tmp/Documents/code/agent-skills/skills/old-skill/README.md" ]] || problems+="[old clone was modified] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills uses repo's .agents/skills and .mcp.json (not clone)"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    # Ensure repo has .mcp.json
    cp "$tmp/../../.mcp.json" "$tmp/repo/.mcp.json" 2>/dev/null || printf '{"mcpServers":{"omnigraph":{},"autoos-agent":{}}}\n' >"$tmp/repo/.mcp.json"
    enabled_flag="$tmp/enabled_servers"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { printf '%s\n' "$2" >>"$enabled_flag"; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills >/dev/null 2>&1
    )
    problems=""
    # Should enable omnigraph and autoos-agent from repo's .mcp.json
    grep -q '^omnigraph$' "$enabled_flag" 2>/dev/null || problems+="[omnigraph not enabled from repo .mcp.json] "
    grep -q '^autoos-agent$' "$enabled_flag" 2>/dev/null || problems+="[autoos-agent not enabled from repo .mcp.json] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills links repo skills into ~/.agents/skills"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }  # no codex installed
        install_agent_skills >/dev/null 2>&1
    )
    problems=""
    dest="$tmp/.agents/skills"
    [[ -d "$dest" && ! -L "$dest" ]] || problems+="[$dest is missing or a symlink] "
    for n in alpha beta; do
        [[ -L "$dest/$n" && "$(readlink "$dest/$n")" == "$tmp/repo/.agents/skills/$n" ]] \
            || problems+="[$n not linked into $dest] "
        [[ -f "$dest/$n/SKILL.md" ]] || problems+="[$n/SKILL.md unreadable through the link] "
    done
    [[ ! -e "$dest/nofile" ]] || problems+="[nofile (no SKILL.md) was linked] "
    [[ ! -d "$tmp/.codex/skills" ]] || problems+="[.codex/skills was created even though codex is not present] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills links into ~/.codex/skills when codex is installed"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { [[ "$1" == "codex" ]] && return 0; return 1; }
        install_agent_skills >/dev/null 2>&1
    )
    problems=""
    dest="$tmp/.codex/skills"
    [[ -d "$dest" && ! -L "$dest" ]] || problems+="[$dest is missing or a symlink] "
    for n in alpha beta; do
        [[ -L "$dest/$n" && "$(readlink "$dest/$n")" == "$tmp/repo/.agents/skills/$n" ]] \
            || problems+="[$n not linked into $dest] "
        [[ -f "$dest/$n/SKILL.md" ]] || problems+="[$n/SKILL.md unreadable through the link] "
    done
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills creates no ~/.codex when codex is absent"; then
    # Creating a directory for a tool the machine does not have is worse
    # than doing nothing: without ~/.codex and without codex on PATH the
    # installer must leave ~/.codex alone (only ~/.agents/skills is shared).
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills >/dev/null 2>&1
    )
    problems=""
    [[ -e "$tmp/.codex" ]] && problems+="[~/.codex was created without codex] "
    [[ -L "$tmp/.agents/skills/alpha" ]] || problems+="[~/.agents/skills not linked, so the run did not reach the step] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills: second run reports skipped for ~/.agents/skills"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills"
    run_install() {
        (
            SYS_HOME="$tmp"
            AUTOOS_DRY_RUN=0
            AUTOOS_ROOT="$tmp/repo"
            clone_or_update() { :; }
            install_mcp_graphify() { :; }
            install_mcp_serena() { :; }
            install_mcp_playwright() { :; }
            install_mcp_context7() { :; }
            mcp_has_server() { return 1; }
            enable_project_mcp_server() { :; }
            register_antigravity_mcp_server() { :; }
            omnigraph_readiness() { return 0; }
            answer() { echo ""; }
            has_cmd() { return 1; }
            install_agent_skills 2>&1
        )
    }
    run_install >/dev/null
    dest="$tmp/.agents/skills"
    before="$(stat -c '%Y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    out="$(run_install)"
    after="$(stat -c '%Y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    problems=""
    [[ "$before" == "$after" ]] || problems+="[the skills directory or a link changed] "
    # Match skill-name-specific lines, not the "Agent skills linked to Antigravity" line
    grep -Eq '(^|[^[:alnum:]])(linked|repointed) (alpha|beta)\b' <<<"$out" && problems+="[second run printed a linked/repointed line] "
    [[ "$out" == *skipped* ]] || problems+="[second run never says skipped] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills: keeps a user's own skill in ~/.agents/skills"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills" "$tmp/.agents/skills/alpha" "$tmp/.agents/skills/mine"
    printf 'my own alpha\n' >"$tmp/.agents/skills/alpha/SKILL.md"
    printf 'my own skill\n'  >"$tmp/.agents/skills/mine/SKILL.md"
    snap() { ( cd "$tmp/.agents/skills" && find alpha mine -type f -exec sha256sum {} + | sort; ls -A ) ; }
    before="$(snap)"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills >/dev/null 2>&1
    )
    after="$(snap)"
    problems=""
    [[ -d "$tmp/.agents/skills/alpha" && ! -L "$tmp/.agents/skills/alpha" ]] \
        || problems+="[the user's own alpha is no longer a real directory] "
    [[ "$(readlink "$tmp/.agents/skills/beta" 2>/dev/null)" == "$tmp/repo/.agents/skills/beta" ]] \
        || problems+="[beta was not linked next to the user's skills] "
    [[ "$(grep -v '^beta$' <<<"$after")" == "$before" ]] || problems+="[the user's skills changed] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills: dry-run links nothing into ~/.agents/skills"; then
    tmp="$(mktemp -d)"
    oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/Documents/code/agent-skills/skills"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=1
        AUTOOS_ROOT="$tmp/repo"
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills 2>&1
    ) >/dev/null
    problems=""
    [[ ! -e "$tmp/.agents/skills" ]] || problems+="[dry run created $tmp/.agents/skills] "
    [[ ! -e "$tmp/.codex/skills" ]] || problems+="[dry run created $tmp/.codex/skills] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

# ─── keys-file lookup order (autoos_api_keys_conf) ──────────────────────────
#
# Each case asserts on the path the helper PRINTS, not on the file it created:
# every tier's file exists in every sandbox, so cat-ing one of them proves only
# that the test wrote it. The choice is the whole behaviour under test.
# All the values are dummies; nothing here reads a real key.

# oh_keys_conf <sandbox dir> -> prints the helper's verdict, returns its rc
oh_keys_conf() {
    (
        SYS_HOME="$1"
        AUTOOS_ROOT="$1"
        source lib/linux/install.sh
        autoos_api_keys_conf
    )
}

if it "keys order: ~/.config/autoos/api_keys.conf beats the agent-skills legacy file"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.config/autoos" "$tmp/configuration" "$tmp/Documents/code/agent-skills/secrets"
    printf 'omniroute=from_user_config\n' >"$tmp/.config/autoos/api_keys.conf"
    printf 'omniroute: from_repo_yml\n'  >"$tmp/configuration/api-keys.yml"
    printf 'omniroute=from_clone\n'      >"$tmp/Documents/code/agent-skills/secrets/api_keys.conf"
    got="$(oh_keys_conf "$tmp")"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$got" == *".config/autoos/api_keys.conf" ]]; then pass
    else fail "picked [$got] rc=$rc, expected the user-scope file"; fi
fi

if it "keys order: repo api-keys.yml is used before the agent-skills clone"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/configuration" "$tmp/Documents/code/agent-skills/secrets"
    printf 'omniroute: from_repo_yml\n' >"$tmp/configuration/api-keys.yml"
    printf 'omniroute=from_clone\n'     >"$tmp/Documents/code/agent-skills/secrets/api_keys.conf"
    got="$(oh_keys_conf "$tmp")"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$got" == *"configuration/api-keys.yml" ]]; then pass
    else fail "picked [$got] rc=$rc, expected the repo's api-keys.yml"; fi
fi

if it "keys order: the agent-skills clone file is still the last tier"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/Documents/code/agent-skills/secrets"
    printf 'omniroute=from_clone\n' >"$tmp/Documents/code/agent-skills/secrets/api_keys.conf"
    got="$(oh_keys_conf "$tmp")"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$got" == *"agent-skills/secrets/api_keys.conf" ]]; then pass
    else fail "picked [$got] rc=$rc, expected the retired clone's file"; fi
fi

if it "keys order: no file anywhere, so no agent-skills path is invented"; then
    tmp="$(mktemp -d)"
    got="$(oh_keys_conf "$tmp")"; rc=$?
    # Positive control in the same sandbox: an empty rc-only failure is what a
    # helper that does not exist at all looks like, so the sandbox has to be
    # able to produce a hit.
    mkdir -p "$tmp/.config/autoos"
    printf 'omniroute=from_user_config\n' >"$tmp/.config/autoos/api_keys.conf"
    found="$(oh_keys_conf "$tmp")"; frc=$?
    rm -rf "$tmp"
    if [[ $rc -ne 0 && -z "$got" && $frc -eq 0 && "$found" == *".config/autoos/api_keys.conf" ]]; then pass
    else fail "empty=${rc} [${got}] then control=${frc} [${found}]: expected a miss and then a hit"; fi
fi

if it "keys order: AUTOOS_KEYS_FILE overrides the search without touching agent-skills"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/elsewhere" "$tmp/Documents/code/agent-skills/secrets"
    printf 'omniroute: from_override\n' >"$tmp/elsewhere/keys.yml"
    printf 'omniroute=from_clone\n'     >"$tmp/Documents/code/agent-skills/secrets/api_keys.conf"
    got="$(
        SYS_HOME="$tmp" AUTOOS_ROOT="$tmp" AUTOOS_KEYS_FILE="$tmp/elsewhere/keys.yml"
        source lib/linux/install.sh
        autoos_api_keys_conf
    )"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$got" == "$tmp/elsewhere/keys.yml" ]]; then pass
    else fail "picked [$got] rc=$rc, expected the override path"; fi
fi

# The python half of the same fact: one parser, tested on its own. Wired here
# because tests/test_suite_wiring.py refuses a unit-test file no harness runs.
if it "keys parser: tools/keys_file.py unit tests pass (no agent-skills reader)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_keys_file.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

# ─── graphify: the pinned uv tool install and what sits in its way ─────────
# ~/.local/bin/graphify-mcp used to be a link AutoOS created, pointing at this
# checkout's Docker wrapper. `uv tool install` now owns that path (spec D13), so the
# step's job inverted: classify what is there and remove ONLY what AutoOS or the
# retired agent-skills clone put there, then hand the caller the verdict it decides
# on. The verdict is published in GRAPHIFY_LINK_STATE:
#   free      nothing at the path — install away
#   removed   a recognised link was deleted — install away
#   uv        uv's own tool link — usable, and --force may replace it
#   blocked   the user's own file or link — never touched, never --force over it
#
# gfy_repo_skeleton <tmp>: a checkout with the wrapper the old step linked to.
gfy_repo_skeleton() {
    mkdir -p "$1/repo/infra/mcp-servers/bin" "$1/.local/bin"
    printf '#!/bin/sh\nexit 0\n' >"$1/repo/infra/mcp-servers/bin/graphify-mcp"
    chmod +x "$1/repo/infra/mcp-servers/bin/graphify-mcp"
}

# gfy_link_shape <tmp> <shape>: put ~/.local/bin/graphify-mcp in that shape.
#   cloned     a link into the retired agent-skills clone (live or dangling)
#   wrapper    a link into this checkout's infra/mcp-servers/bin wrapper
#   ownfile    the user's own regular file
#   ownlink    the user's own link to somewhere else entirely
#   uvtool     uv's own tool link (target under the uv tools dir)
gfy_link_shape() {
    local tmp="$1" shape="$2"
    case "$shape" in
        cloned) mkdir -p "$tmp/uvtools/graphifyy/bin"
                ln -sfn "$tmp/Documents/code/agent-skills/infra/mcp-servers/bin/graphify-mcp" \
                    "$tmp/.local/bin/graphify-mcp" ;;
        wrapper) ln -sfn "$tmp/repo/infra/mcp-servers/bin/graphify-mcp" \
                    "$tmp/.local/bin/graphify-mcp" ;;
        ownfile) printf '#!/bin/sh\nexit 0\n' >"$tmp/.local/bin/graphify-mcp"
                chmod +x "$tmp/.local/bin/graphify-mcp" ;;
        ownlink) mkdir -p "$tmp/other"
                printf '#!/bin/sh\nexit 0\n' >"$tmp/other/graphify-mcp"
                chmod +x "$tmp/other/graphify-mcp"
                ln -sfn "$tmp/other/graphify-mcp" "$tmp/.local/bin/graphify-mcp" ;;
        uvtool) mkdir -p "$tmp/uvtools/graphifyy/bin"
                printf '#!/bin/sh\nexit 0\n' >"$tmp/uvtools/graphifyy/bin/graphify-mcp"
                ln -sfn "$tmp/uvtools/graphifyy/bin/graphify-mcp" \
                    "$tmp/.local/bin/graphify-mcp" ;;
    esac
}

# gfy_link_run <tmp> <dry>: the verdict, with the step's own report in front of it.
gfy_link_run() {
    (
        SYS_HOME="$1" AUTOOS_ROOT="$1/repo" AUTOOS_DRY_RUN="$2"
        uv() { [[ "${1:-} ${2:-}" == "tool dir" ]] && printf '%s/uvtools\n' "$SYS_HOME"; return 0; }
        graphify_mcp_link_prepare 2>&1
        printf 'STATE=%s\n' "${GRAPHIFY_LINK_STATE:-unset}"
    )
}

if it "graphify link prepare frees a path with nothing in it"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    out="$(gfy_link_run "$tmp" 0)"
    problems=""
    [[ "$out" == *STATE=free* ]] || problems+="[verdict: $(tail -n 1 <<<"$out")] "
    [[ -e "$tmp/.local/bin/graphify-mcp" ]] && problems+="[the step created a link itself] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify link prepare removes the agent-skills link and reports it"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" cloned
    out="$(gfy_link_run "$tmp" 0)"
    problems=""
    [[ "$out" == *STATE=removed* ]] || problems+="[verdict: $(tail -n 1 <<<"$out")] "
    [[ -e "$tmp/.local/bin/graphify-mcp" || -L "$tmp/.local/bin/graphify-mcp" ]] \
        && problems+="[the agent-skills link is still there]"
    [[ "$out" == *"removed"* ]] || problems+="[nothing reported the removal: $(head -n 1 <<<"$out")] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify link prepare removes the link to this checkout's docker wrapper"; then
    # The wrapper needs a built graphify-mcp:latest image and a docker daemon; D18
    # wants clients off Docker, and the link in the way blocks uv's own shim.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" wrapper
    out="$(gfy_link_run "$tmp" 0)"
    problems=""
    [[ "$out" == *STATE=removed* ]] || problems+="[verdict: $(tail -n 1 <<<"$out")] "
    [[ -L "$tmp/.local/bin/graphify-mcp" ]] && problems+="[the wrapper link is still there] "
    [[ -x "$tmp/repo/infra/mcp-servers/bin/graphify-mcp" ]] \
        || problems+="[it deleted the tracked wrapper instead of the link] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify link prepare leaves the user's own file and blocks the install"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" ownfile
    out="$(gfy_link_run "$tmp" 0)"
    problems=""
    [[ "$out" == *STATE=blocked* ]] || problems+="[verdict: $(tail -n 1 <<<"$out")] "
    [[ "$out" == *"alone"* ]] || problems+="[nothing named it: $(head -n 1 <<<"$out")] "
    [[ -f "$tmp/.local/bin/graphify-mcp" && ! -L "$tmp/.local/bin/graphify-mcp" ]] \
        || problems+="[the user's file was touched]"
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify link prepare leaves the user's own symlink to another target alone"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" ownlink
    out="$(gfy_link_run "$tmp" 0)"
    got="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo none)"
    rm -rf "$tmp"
    if [[ "$out" == *STATE=blocked* && "$got" == */other/graphify-mcp ]]; then pass
    else fail "verdict=$(tail -n 1 <<<"$out") link=$got"; fi
fi

if it "graphify link prepare knows uv's own tool link is not the user's file"; then
    # After a successful install the link is uv's, pointing into its tools dir.
    # Mistaking it for a user file would refuse every later update.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" uvtool
    out="$(gfy_link_run "$tmp" 0)"
    got="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo none)"
    rm -rf "$tmp"
    if [[ "$out" == *STATE=uv* && "$got" == */uvtools/graphifyy/bin/graphify-mcp ]]; then pass
    else fail "verdict=$(tail -n 1 <<<"$out") link=$got"; fi
fi

if it "graphify link prepare removes an agent-skills link whose clone is gone"; then
    # The realistic migration case: the user deleted the retired clone, which
    # leaves ~/.local/bin/graphify-mcp dangling. Resolving it is not possible and
    # not needed — the target still names where it came from.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" cloned
    rm -rf "$tmp/Documents"
    out="$(gfy_link_run "$tmp" 0)"
    got="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo absent)"
    rm -rf "$tmp"
    if [[ "$out" == *STATE=removed* && "$got" == absent ]]; then pass
    else fail "verdict=$(tail -n 1 <<<"$out") link=$got"; fi
fi

if it "graphify link prepare announces the verdict a real run then acts on"; then
    # A dry run that reads the link differently from the live branch reports
    # "removed" for a link the next run calls the user's own, or the reverse. Both
    # branches therefore reach the same verdict for every shape, and the dry run
    # touches nothing at all.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    problems=""
    for shape in free cloned wrapper ownfile ownlink uvtool; do
        rm -f "$tmp/.local/bin/graphify-mcp"
        [[ "$shape" == free ]] || gfy_link_shape "$tmp" "$shape"
        # The link shape is what the driver creates; GRAPHIFY_LINK_STATE is what
        # the installer decides. Two shapes per verdict (the clone's link and the
        # wrapper's are both recognised leftovers), so the map is explicit.
        case "$shape" in
            free) want=free ;;
            cloned | wrapper) want=removed ;;
            ownfile | ownlink) want=blocked ;;
            uvtool) want=uv ;;
        esac
        before="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo absent)"
        dry="$(gfy_link_run "$tmp" 1)"
        after_dry="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo absent)"
        live="$(gfy_link_run "$tmp" 0)"
        [[ "$before" == "$after_dry" ]] || problems+="[$shape: the dry run touched the link] "
        [[ "$dry" == *"STATE=$want"* ]] || problems+="[$shape: the dry run says $(tail -n 1 <<<"$dry")] "
        [[ "$live" == *"STATE=$want"* ]] || problems+="[$shape: the real run says $(tail -n 1 <<<"$live")] "
        case "$shape" in
            ownfile) [[ -f "$tmp/.local/bin/graphify-mcp" && ! -L "$tmp/.local/bin/graphify-mcp" ]] \
                        || problems+="[the user's file was replaced] " ;;
            ownlink) [[ "$(readlink "$tmp/.local/bin/graphify-mcp")" == "$tmp/other/graphify-mcp" ]] \
                        || problems+="[the user's link was removed] " ;;
            uvtool)  [[ "$(readlink "$tmp/.local/bin/graphify-mcp")" == "$tmp/uvtools/graphifyy/bin/graphify-mcp" ]] \
                        || problems+="[uv's own link was removed] " ;;
            cloned | wrapper) [[ ! -e "$tmp/.local/bin/graphify-mcp" && ! -L "$tmp/.local/bin/graphify-mcp" ]] \
                        || problems+="[$shape: the recognised link survived] " ;;
        esac
    done
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

# gfy_tool_run <tmp> <have> <dry> <log> <state-file>: install_graphify_tool against a
# fake uv. The fake records every argv in <log> and keeps the installed version in
# <state-file>, so `tool list` after an install reports what was just installed — a
# second run meets the real tool's contract instead of a frozen answer. <have> seeds
# that file only when it does not exist yet: a caller that runs twice wants the
# second run to read the first one's result. The pin and the distribution name come
# from the catalog, never from this file.
gfy_tool_run() {
    local tmp="$1" have="$2" dry="$3" log="$4" state="$5"
    local name pin
    name="$(mcp_package graphify | sed -e 's/\[.*//' -e 's/==.*//')"
    pin="$(mcp_package graphify | sed -e 's/.*==//')"
    [[ -f "$state" ]] || printf '%s' "$have" >"$state"
    (
        SYS_HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN="$dry"
        uv_log="$log" uv_state="$state" uv_name="$name"
        uv() {
            printf '%s\n' "$*" >>"$uv_log"
            case "${1:-} ${2:-}" in
                "tool list")
                    local v; v="$(cat "$uv_state" 2>/dev/null || true)"
                    if [[ -n "$v" ]]; then
                        printf '%s v%s\n- graphify\n- graphify-mcp\n' "$uv_name" "$v"
                    else
                        printf 'No tools installed\n'
                    fi ;;
                "tool dir") printf '%s/uvtools\n' "$SYS_HOME" ;;
                "tool install")
                    local spec="${*: -1}" v
                    [[ "$spec" == *"=="* ]] || { printf 'error: no pin\n' >&2; return 2; }
                    v="${spec##*==}"
                    printf '%s' "$v" >"$uv_state"
                    mkdir -p "$SYS_HOME/uvtools/$uv_name/bin"
                    printf '#!/bin/sh\nexit 0\n' >"$SYS_HOME/uvtools/$uv_name/bin/graphify-mcp"
                    ln -sfn "$SYS_HOME/uvtools/$uv_name/bin/graphify-mcp" \
                        "$SYS_HOME/.local/bin/graphify-mcp" ;;
            esac
            return 0
        }
        install_graphify_tool 2>&1
    )
}

if it "graphify tool install: nothing installed installs the pin without --force"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    out="$(gfy_tool_run "$tmp" "" 0 "$log" "$state")"
    pin="$(mcp_package graphify)"
    problems=""
    [[ "$(grep -c '^tool ' "$log")" == "2" ]] \
        || problems+="[uv calls: $(tr '\n' '|' <"$log")] "
    grep -qxF "tool install $pin" "$log" \
        || problems+="[planned: $(tr '\n' '|' <"$log"), expected: tool install $pin] "
    [[ "$out" == *"installed"* ]] || problems+="[nothing said installed: $out] "
    [[ "$out" != *"skipped"* ]] || problems+="[called it skipped: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: the pinned version already installed reports skipped"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    pin="$(mcp_package graphify | sed -e 's/.*==//')"
    out="$(gfy_tool_run "$tmp" "$pin" 0 "$log" "$state")"
    problems=""
    grep -q '^tool install' "$log" && problems+="[re-installed anyway: $(tr '\n' '|' <"$log")] "
    [[ "$out" == *"skipped"* ]] || problems+="[nothing said skipped: $out] "
    [[ "$out" != *"installed"* ]] || problems+="[called it installed: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: another version installs with --force and reports updated"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    pin_pkg="$(mcp_package graphify)"
    out="$(gfy_tool_run "$tmp" "0.0.1-other" 0 "$log" "$state")"
    problems=""
    grep -qxF "tool install --force $pin_pkg" "$log" \
        || problems+="[planned: $(tr '\n' '|' <"$log"), expected: tool install --force $pin_pkg] "
    [[ "$out" == *"updated"* ]] || problems+="[nothing said updated: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: the second run reports skipped, not installed"; then
    # The acceptance bar for every installer in this repo, driven through the two
    # real calls rather than a seeded state: the first installs, the fake uv
    # records the version it was given, the second must read it back and stop.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    first="$(gfy_tool_run "$tmp" "" 0 "$log" "$state")"
    second="$(gfy_tool_run "$tmp" "" 0 "$log" "$state")"
    installs="$(grep -c '^tool install' "$log" || true)"
    problems=""
    [[ "$first" == *"installed"* ]] || problems+="[the first run did not say installed: $first] "
    [[ "$second" == *"skipped"* ]] || problems+="[the second run did not say skipped: $second] "
    [[ "$second" != *"installed"* ]] || problems+="[the second run said installed: $second] "
    [[ "$installs" == "1" ]] || problems+="[$installs installs ran, expected 1] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: never force over a graphify-mcp the user owns"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" ownfile
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    out="$(gfy_tool_run "$tmp" "" 0 "$log" "$state")"
    problems=""
    grep -q '^tool install' "$log" && problems+="[installed over the user's file: $(tr '\n' '|' <"$log")] "
    grep -q -- '--force' "$log" && problems+="[forced over the user's file] "
    [[ "$out" == *"alone"* ]] || problems+="[nothing said it was left alone: $out] "
    [[ -f "$tmp/.local/bin/graphify-mcp" && ! -L "$tmp/.local/bin/graphify-mcp" ]] \
        || problems+="[the user's file is gone]"
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: a stale agent-skills link is cleared, then the pin installs"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" cloned
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    pin_pkg="$(mcp_package graphify)"
    out="$(gfy_tool_run "$tmp" "" 0 "$log" "$state")"
    problems=""
    # uv writes its own link at that path when it installs, so what must be gone is
    # the link into the retired clone — not every file named graphify-mcp.
    got="$(readlink "$tmp/.local/bin/graphify-mcp" 2>/dev/null || echo absent)"
    [[ "$got" == *"/agent-skills/"* ]] && problems+="[the agent-skills link survived: $got] "
    grep -qxF "tool install $pin_pkg" "$log" \
        || problems+="[no install ran: $(tr '\n' '|' <"$log")] "
    [[ "$out" == *"removed"* ]] || problems+="[the removal went unreported: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: uv's own link lets the update force past it"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" uvtool
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    pin_pkg="$(mcp_package graphify)"
    out="$(gfy_tool_run "$tmp" "0.0.1-other" 0 "$log" "$state")"
    problems=""
    grep -qxF "tool install --force $pin_pkg" "$log" \
        || problems+="[planned: $(tr '\n' '|' <"$log")] "
    [[ "$out" == *"updated"* ]] || problems+="[nothing said updated: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "graphify tool install: no uv on PATH is named, not a silent pass"; then
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    out="$(
        SYS_HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
        has_cmd() { [[ "$1" != uv ]]; }
        install_graphify_tool 2>&1
    )"
    if [[ "$out" == *"uv"* ]]; then pass; else fail "nothing named the missing uv: $out"; fi
fi

if it "graphify tool install: the dry run names the action the real run takes"; then
    # The plan the user reads has to be the run they get: for every version the fake
    # uv reports, the same verdict word appears in the dry run and in the real run,
    # and the dry run never asks uv to install anything.
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/uv.log"; : >"$log"; state="$tmp/uv.version"
    pin_pkg="$(mcp_package graphify)"
    pin="$(mcp_package graphify | sed -e 's/.*==//')"
    problems=""
    for have in "" "0.0.1-other" "$pin"; do
        : >"$log"
        # Each case starts from an empty bin dir and an empty uv receipt, so the
        # version below is the only thing that decides --force — not the link or
        # the install the previous case left behind.
        rm -f "$tmp/.local/bin/graphify-mcp" "$state"
        dry="$(gfy_tool_run "$tmp" "$have" 1 "$log" "$state")"
        grep -q '^tool install' "$log" && problems+="[the dry run installed for have=$have] "
        live="$(gfy_tool_run "$tmp" "$have" 0 "$log" "$state")"
        # One verb decides both branches: install / update / skip. The dry run
        # prefixes it with "would", the real run reports it in the past tense.
        case "$have" in
            "") stem=install ;;
            "$pin") stem=skipped ;;
            *) stem=updat ;;
        esac
        [[ "$dry" == *"$stem"* ]] || problems+="[have=$have, dry run: $dry] "
        [[ "$live" == *"$stem"* ]] || problems+="[have=$have, real run: $live] "
        case "$have" in
            "") grep -qxF "tool install $pin_pkg" "$log" \
                    || problems+="[live plan for a fresh host: $(tr '\n' '|' <"$log")] " ;;
            "$pin") grep -q '^tool install' "$log" \
                    && problems+="[re-installed although pinned: $(tr '\n' '|' <"$log")] " ;;
            *) grep -qxF "tool install --force $pin_pkg" "$log" \
                    || problems+="[no --force for have=$have: $(tr '\n' '|' <"$log")] " ;;
        esac
    done
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi


# ─── keys and skills source reach the writer that actually runs ───
# These drive setup_opencode_config (the postInstall path), not
# _opencode_merge_config on its own: the harness merge and the skills-source
# lookup live in the loop around it, and a candidate file the inline reader
# cannot parse is a key that silently never arrives — which reads on the
# machine exactly like a machine with no keys.
#
# Dummy values only: a test never touches a real key file, and an inherited
# real key would pass the assertion for the wrong reason, so the key env vars
# are unset in every subshell below.

# <scratch home> — runs the real writer with no skills source and no keys file.
oh_opencode_run() {
    local home="$1" root="$2"
    ( SYS_HOME="$home" AUTOOS_ROOT="$root" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      setup_opencode_config >/dev/null 2>&1 )
}

if it "opencode: api-keys.yml (the repo keys file, not agent-skills) is parsed"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo/configuration"
    printf 'openrouter: DUMMY-KEY-FOR-TESTS\n' >"$tmp/repo/configuration/api-keys.yml"
    oh_opencode_run "$tmp/home" "$tmp/repo"
    out="$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print('yes' if 'openrouter' in d.get('provider', {}) else 'no')
" "$tmp/home/.config/opencode/config.json" 2>/dev/null || echo unreadable)"
    rm -rf "$tmp"
    if [[ "$out" == "yes" ]]; then pass
    else fail "the openrouter provider is missing (config says: $out) — api-keys.yml was never read"; fi
    fi
fi

if it "opencode: an api-keys.yml REPLACE placeholder is never treated as a key"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo/configuration"
    printf 'openrouter: REPLACE_WITH_YOUR_KEY\n' >"$tmp/repo/configuration/api-keys.yml"
    oh_opencode_run "$tmp/home" "$tmp/repo"
    out="$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print('yes' if 'openrouter' in d.get('provider', {}) else 'no')
" "$tmp/home/.config/opencode/config.json" 2>/dev/null || echo unreadable)"
    rm -rf "$tmp"
    if [[ "$out" == "no" ]]; then pass
    else fail "a placeholder key was written into the config as if it were real"; fi
    fi
fi

if it "opencode: the agent harness gets this checkout's .agents/skills, not agent-skills"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo/.agents/skills/x"
    printf -- '---\nname: x\ndescription: x\n---\n' >"$tmp/repo/.agents/skills/x/SKILL.md"
    src_flag="$tmp/skills-source"; : >"$src_flag"
    ( SYS_HOME="$tmp/home" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      src_flag="$src_flag"
      python3() {
          if [[ "${1:-}" == */lib/agent_harness.py ]]; then
              local prev="" a
              for a in "$@"; do
                  [[ "$prev" == "--skills-source" ]] && printf '%s\n' "$a" >>"$src_flag"
                  prev="$a"
              done
              return 0
          fi
          command python3 "$@"
      }
      setup_opencode_config >/dev/null 2>&1 )
    got="$(head -n 1 "$src_flag" 2>/dev/null || true)"
    calls="$(wc -l <"$src_flag" 2>/dev/null | tr -d ' ')"
    rm -rf "$tmp"
    if [[ "$calls" == "0" ]]; then fail "the agent harness was never invoked — the test proves nothing"
    elif [[ "$got" == */.agents/skills ]]; then pass
    else fail "harness got [$got], expected the checkout's .agents/skills"; fi
    fi
fi

if it "opencode: no agent-skills path is invented when there is no skills source"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo"
    src_flag="$tmp/skills-source"; : >"$src_flag"
    ( SYS_HOME="$tmp/home" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      src_flag="$src_flag"
      python3() {
          if [[ "${1:-}" == */lib/agent_harness.py ]]; then
              local prev="" a
              for a in "$@"; do
                  [[ "$prev" == "--skills-source" ]] && printf '%s\n' "$a" >>"$src_flag"
                  prev="$a"
              done
              return 0
          fi
          command python3 "$@"
      }
      setup_opencode_config >/dev/null 2>&1 )
    got="$(head -n 1 "$src_flag" 2>/dev/null || true)"
    calls="$(wc -l <"$src_flag" 2>/dev/null | tr -d ' ')"
    rm -rf "$tmp"
    if [[ "$calls" == "0" ]]; then fail "the agent harness was never invoked — the test proves nothing"
    elif [[ -z "$got" ]]; then pass
    else fail "harness was handed a skills source that does not exist: [$got]"; fi
    fi
fi


# A machine mid-migration: this checkout has no vendored .agents/skills yet
# (an AutoOS clone older than the subtree) but the retired agent-skills clone
# is still on disk. Every skills link — user scope AND the per-client
# directories — has to come from that one fallback, or the same run both
# links ~/.agents/skills and silently skips Antigravity and Claude Code.
oh_clone_only() {
    local home="$1"
    mkdir -p "$home/Documents/code/agent-skills/skills/gamma"
    printf -- '---\nname: gamma\ndescription: demo\n---\n' \
        >"$home/Documents/code/agent-skills/skills/gamma/SKILL.md"
}

oh_agent_skills_run() {
    local home="$1" repo="$2"
    (
        SYS_HOME="$home"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$repo"
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        has_cmd() { return 1; }
        install_agent_skills
    )
}

if it "install_agent_skills links the retired clone's skills into every client dir"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo"
    printf '{"mcpServers":{}}' >"$tmp/repo/.mcp.json"
    oh_clone_only "$tmp"
    oh_agent_skills_run "$tmp" "$tmp/repo" >/dev/null 2>&1
    problems=""
    for dest in "$tmp/.claude/skills" "$tmp/.gemini/config/skills" "$tmp/.agents/skills"; do
        [[ -L "$dest/gamma" && "$(readlink "$dest/gamma")" == *"/agent-skills/skills/gamma" ]] \
            || problems+="[gamma not linked from the clone into $dest] "
    done
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "install_agent_skills names it when there is no skills source at all"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/repo"
    printf '{"mcpServers":{}}' >"$tmp/repo/.mcp.json"
    out="$(oh_agent_skills_run "$tmp" "$tmp/repo" 2>&1)"
    problems=""
    [[ "$out" == *"no skills to link"* ]] \
        || problems+="[nothing named the missing skills source — $(tail -n 2 <<<"$out")] "
    [[ ! -d "$tmp/.claude/skills" ]] || problems+="[an empty ~/.claude/skills was created for nothing] "
    [[ ! -d "$tmp/.gemini/config/skills" ]] || problems+="[an empty ~/.gemini/config/skills was created for nothing] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

# ─── graphify registration: the installed tool, not an on-demand uv run ────
# gfy_register_run <tmp>: install_mcp_graphify against a fake uv (installs nothing
# real) and a fake claude that records its argv and behaves like the CLI for
# `mcp list` (nothing registered yet) and `mcp add` (records and succeeds).
gfy_register_run() {
    local tmp="$1" log="$2"
    (
        SYS_HOME="$tmp" HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
        uv() {
            case "${1:-} ${2:-}" in
                "tool list") printf 'No tools installed\n' ;;
                "tool dir") printf '%s/uvtools\n' "$SYS_HOME" ;;
                "tool install")
                    # Real uv writes the tool's executable and links it into its own
                    # bin dir; the registration below is only allowed to name it once
                    # that file is there.
                    mkdir -p "$SYS_HOME/.local/bin" "$SYS_HOME/uvtools/graphifyy/bin"
                    printf '#!/bin/sh\nexit 0\n' >"$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    chmod +x "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    ln -sfn "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp" \
                        "$SYS_HOME/.local/bin/graphify-mcp" ;;
            esac
            return 0
        }
        claude() {
            printf 'claude %s\n' "$*" >>"$log"
            return 0
        }
        install_mcp_graphify 2>&1
    )
}

if it "graphify registers the pinned installed tool for Claude Code and Antigravity"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    out="$(gfy_register_run "$tmp" "$log")"
    problems=""
    grep -qxF 'claude mcp add --scope user graphify -- graphify-mcp graphify-out/graph.json' "$log" \
        || problems+="[claude got: $(tr '\n' '|' <"$log")] "
    grep -q -- '--with' "$log" && problems+="[still an on-demand uv run: $(tr '\n' '|' <"$log")] "
    got="$(python3 -c "
import json, sys
e = json.load(open(sys.argv[1], encoding='utf-8'))['mcpServers']['graphify']
print(e['command'], '|', json.dumps(e['args']))
" "$tmp/.gemini/config/mcp_config.json" 2>/dev/null || echo unreadable)"
    [[ "$got" == 'graphify-mcp | ["${workspaceFolder}/graphify-out/graph.json"]' ]] \
        || problems+="[antigravity spec: $got] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify registers once: the second run says skipped and already configured"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"; state="$tmp/uv.version"; : >"$state"
    first="$(
        SYS_HOME="$tmp" HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
        uv() {
            case "${1:-} ${2:-}" in
                "tool list") printf 'No tools installed\n' ;;
                "tool dir") printf '%s/uvtools\n' "$SYS_HOME" ;;
                "tool install")
                    printf '%s' "${*: -1}" | sed -e 's/.*==//' >"$state"
                    mkdir -p "$SYS_HOME/.local/bin" "$SYS_HOME/uvtools/graphifyy/bin"
                    printf '#!/bin/sh\nexit 0\n' >"$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    chmod +x "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    ln -sfn "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp" \
                        "$SYS_HOME/.local/bin/graphify-mcp" ;;
            esac
            return 0
        }
        claude() { return 0; }
        install_mcp_graphify 2>&1
    )"
    second="$(
        SYS_HOME="$tmp" HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN=0
        uv() {
            case "${1:-} ${2:-}" in
                "tool list") printf '%s v%s\n- graphify-mcp\n' \
                    "$(mcp_package graphify | sed -e 's/\[.*//' -e 's/==.*//')" "$(cat "$state")" ;;
                "tool dir") printf '%s/uvtools\n' "$SYS_HOME" ;;
            esac
            return 0
        }
        claude() {
            [[ "${1:-} ${2:-}" == "mcp list" ]] && printf 'graphify: command - ✓\n'
            return 0
        }
        install_mcp_graphify 2>&1
    )"
    problems=""
    [[ "$first" == *"installed"* ]] || problems+="[first run did not report an install: $first] "
    [[ "$second" == *"skipped"* ]] || problems+="[second run did not say skipped: $second] "
    [[ "$second" != *"installed"* ]] || problems+="[second run said installed: $second] "
    [[ "$second" == *"already registered"* || "$second" == *"already configured"* ]] \
        || problems+="[second run re-registered the clients: $second] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

# ─── A4b: a user-scope homelab entry that points into the retired clone ─────
# homelab is not an AutoOS component (spec D14): the only thing done here is
# removing an entry AutoOS can recognise as its own leftover — a user-scope
# entry whose PYTHONPATH or args name the retired agent-skills tree. Read, back
# up once, remove through claude's own CLI, report. Anything else is the user's.
#
# hl_cfg_write <tmp> <json mcpServers block>: write a Claude user config.
hl_cfg_write() {
    local tmp="$1" body="$2"
    mkdir -p "$tmp"
    printf '{"mcpServers":%s,"firstTimeRun":true}\n' "$body" >"$tmp/.claude.json"
}

# hl_remove_run <tmp> <dry>: remove_stale_homelab_mcp_entry with a fake claude that
# records its argv and, for `mcp remove <name> --scope user`, drops that key from
# the top-level mcpServers the way the CLI does.
hl_remove_run() {
    local tmp="$1" dry="$2"
    (
        SYS_HOME="$tmp" HOME="$tmp" AUTOOS_DRY_RUN="$dry"
        unset CLAUDE_CONFIG_DIR
        claude() {
            printf 'claude %s\n' "$*" >>"$tmp/claude.log"
            if [[ "${1:-} ${2:-}" == "mcp remove" ]]; then
                python3 - "$tmp/.claude.json" "${3:-}" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
data.get("mcpServers", {}).pop(name, None)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
            fi
            return 0
        }
        remove_stale_homelab_mcp_entry 2>&1
    )
}

if it "homelab: an agent-skills PYTHONPATH entry is backed up and removed"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"homelab":{"type":"stdio","command":"python","args":["-m","homelab_mcp"],"env":{"PYTHONPATH":"/home/u/Documents/code/agent-skills/mcp/homelab"}},"serena":{"command":"uvx" }}'
    out="$(hl_remove_run "$tmp" 0)"
    problems=""
    [[ "$out" == *"removed"* ]] || problems+="[nothing said removed: $out] "
    grep -qxF 'claude mcp remove homelab --scope user' "$tmp/claude.log" \
        || problems+="[claude got: $(tr '\n' '|' <"$tmp/claude.log")] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 \
        || problems+="[the user config was rewritten with no backup] "
    kept="$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(','.join(sorted(d.get('mcpServers', {})) + ['firstTimeRun' if 'firstTimeRun' in d else 'LOST']))
" "$tmp/.claude.json" 2>/dev/null || echo unreadable)"
    [[ "$kept" == "serena,firstTimeRun" ]] \
        || problems+="[the config lost or kept the wrong keys: $kept] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "homelab: the second run reports skipped and writes no second backup"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"homelab":{"command":"python","args":["-m","homelab_mcp"],"env":{"PYTHONPATH":"/home/u/Documents/code/agent-skills/mcp/homelab"}}}'
    first="$(hl_remove_run "$tmp" 0)"
    second="$(hl_remove_run "$tmp" 0)"
    n_backups="$(ls "$tmp"/.claude.json.autoos-backup-* 2>/dev/null | wc -l | tr -d ' ')"
    n_removes="$(grep -c 'mcp remove' "$tmp/claude.log" || true)"
    problems=""
    [[ "$first" == *"removed"* ]] || problems+="[the first run did not remove: $first] "
    [[ "$second" == *"skipped"* ]] || problems+="[the second run did not say skipped: $second] "
    [[ "$second" != *"removed"* ]] || problems+="[the second run removed again: $second] "
    [[ "$n_backups" == "1" ]] || problems+="[$n_backups backups, expected 1] "
    [[ "$n_removes" == "1" ]] || problems+="[$n_removes remove calls, expected 1] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "homelab: an agent-skills path in the args is recognised just like PYTHONPATH"; then
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"homelab":{"command":"python","args":["/home/u/Documents/code/agent-skills/mcp/homelab/server.py"]}}'
    out="$(homelab_user_entry "$tmp/.claude.json")"
    if [[ "$out" == "agent-skills" ]]; then pass
    else fail "expected agent-skills, got [$out]"; fi
    rm -rf "$tmp"
fi

if it "homelab: an entry that is not an agent-skills leftover is left alone"; then
    # The user's own homelab server: same name, nothing in it AutoOS recognises.
    # Removing it would delete a working server the operator installed by hand.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"homelab":{"command":"docker","args":["run","-i","--rm","homelab-mcp:latest"],"env":{"HOMELAB_CONFIG":"/home/u/h.yaml"}}}'
    before="$(cat "$tmp/.claude.json")"
    out="$(hl_remove_run "$tmp" 0)"
    problems=""
    [[ "$out" == *"alone"* ]] || problems+="[nothing said it was left alone: $out] "
    [[ "$out" != *"removed"* ]] || problems+="[claimed a removal it did not do: $out] "
    grep -q 'mcp remove' "$tmp/claude.log" && problems+="[removed the user's entry] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 && problems+="[a backup of a file it never changed] "
    [[ "$(cat "$tmp/.claude.json")" == "$before" ]] || problems+="[the config was rewritten] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "homelab: no user-scope entry says skipped, not failed"; then
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"serena":{"command":"uvx"}}'
    out="$(hl_remove_run "$tmp" 0)"
    if [[ "$out" == *"skipped"* ]]; then pass; else fail "expected skipped, got: $out"; fi
    rm -rf "$tmp"
fi

if it "homelab: a config outside this run's home is never touched"; then
    # The claude CLI reads $HOME, but the home this run configures is $SYS_HOME.
    # When the two differ - setup under sudo, or a harness that fakes SYS_HOME and
    # leaves HOME at the operator's real one - the file the resolver names belongs
    # to somebody else's session, and removing an entry from it edits a config
    # this run was never pointed at. Refuse, say skipped, touch nothing.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    run_home="$tmp/run"; cfg_home="$tmp/cfg"
    mkdir -p "$run_home"
    hl_cfg_write "$cfg_home" '{"homelab":{"command":"python","args":["-m","homelab_mcp"],"env":{"PYTHONPATH":"/home/u/Documents/code/agent-skills/mcp/homelab"}}}'
    before="$(cat "$cfg_home/.claude.json")"
    out="$( (
        SYS_HOME="$run_home" HOME="$cfg_home" AUTOOS_DRY_RUN=0
        unset CLAUDE_CONFIG_DIR
        claude() { printf 'claude %s\n' "$*" >>"$tmp/claude.log"; return 0; }
        remove_stale_homelab_mcp_entry 2>&1
    ) )"
    problems=""
    grep -q 'mcp remove' "$tmp/claude.log" && problems+="[removed an entry outside this run's home] "
    ls "$cfg_home"/.claude.json.autoos-backup-* >/dev/null 2>&1 && problems+="[backed up a file outside this run's home] "
    [[ "$(cat "$cfg_home/.claude.json")" == "$before" ]] || problems+="[the config outside this run's home was rewritten] "
    [[ "$out" == *"skipped"* ]] || problems+="[nothing said skipped: $out] "
    [[ "$out" != *"the stale user-scope"* ]] || problems+="[claimed a removal it did not do: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "homelab: a project-scoped entry of that name is not the user scope"; then
    # ~/.claude.json also carries per-project servers. Those belong to the repo's
    # own .mcp.json approval, never to a user-scope cleanup.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    mkdir -p "$tmp"
    printf '%s\n' '{"mcpServers":{},"projects":{"/home/u/repo":{"mcpServers":{"homelab":{"command":"python","args":["/home/u/Documents/code/agent-skills/mcp/homelab/server.py"]}}}}}' \
        >"$tmp/.claude.json"
    before="$(cat "$tmp/.claude.json")"
    out="$(hl_remove_run "$tmp" 0)"
    problems=""
    [[ "$(cat "$tmp/.claude.json")" == "$before" ]] || problems+="[the project-scoped entry was touched] "
    grep -q 'mcp remove' "$tmp/claude.log" && problems+="[removed a project-scope entry] "
    [[ "$out" == *"skipped"* ]] || problems+="[nothing said skipped: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "homelab: the dry run announces the removal and writes nothing"; then
    tmp="$(mktemp -d)"; : >"$tmp/claude.log"
    hl_cfg_write "$tmp" '{"homelab":{"command":"python","args":["-m","homelab_mcp"],"env":{"PYTHONPATH":"/home/u/Documents/code/agent-skills/mcp/homelab"}}}'
    before="$(cat "$tmp/.claude.json")"
    out="$(hl_remove_run "$tmp" 1)"
    problems=""
    [[ "$out" == *"would"* ]] || problems+="[the dry run made no announcement: $out] "
    [[ "$(cat "$tmp/.claude.json")" == "$before" ]] || problems+="[the dry run rewrote the user config] "
    [[ -s "$tmp/claude.log" ]] && problems+="[the dry run ran claude: $(tr '\n' '|' <"$tmp/claude.log")] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 && problems+="[the dry run wrote a backup] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "homelab: the removal runs on the path that wires the MCP stack up"; then
    # An orphan function nothing calls is a test fixture, not a feature: the
    # retirement cleanup belongs in install_agent_skills, next to the graphify
    # link it shares a reason with. The body is taken whole — a fixed window of
    # lines would miss the call as the function grows.
    if awk '/^install_agent_skills\(\) \{/{on=1} on{print} on && /^\}$/{exit}' \
            lib/linux/install.sh | grep -q "remove_stale_homelab_mcp_entry"; then pass
    else fail "remove_stale_homelab_mcp_entry is defined but never called by install_agent_skills"; fi
fi

if it "homelab: lib/ never names the retired tree's path or an agent-skills version"; then
    # Same discipline as the package pins: the match is on the /agent-skills/
    # path segment shape, not on a hardcoded home or clone location.
    bad=""
    grep -n 'Documents/[Cc]ode/agent-skills/mcp' lib/linux/install.sh >/dev/null 2>&1 && bad="a hardcoded clone path"
    if [[ -z "$bad" ]]; then pass; else fail "$bad"; fi
fi

# ─── A4 review fix: register graphify only over an installed tool ────────────
# install_mcp_graphify registered Claude Code and Antigravity whatever
# install_graphify_tool answered, so a machine with no uv, an outage during
# `uv tool install`, or the user's own file in the tool bin path got a
# `graphify-mcp` entry for a command that does not exist — and because
# register_mcp_server leaves a name it already sees alone, every later run
# reported "already registered" over the broken entry instead of repairing it.
# Two things follow: nothing registers until the pinned tool resolves, and an
# entry AutoOS recognises as its own previous form is replaced, once.
#
# gfy_claude <log> <cfg> <claude args...>: the claude CLI's user-scope MCP
# commands against a real JSON file the way the CLI keeps one — `mcp add` refuses
# a name that already exists, `mcp remove` refuses a name that does not, `mcp
# list` prints the "name: command - status" lines mcp_has_server parses. The stub
# is this faithful because the repair is only proven by an add that fails when the
# stale entry is still in the way.
gfy_claude() {
    local log="$1" cfg="$2"; shift 2
    printf 'claude %s\n' "$*" >>"$log"
    case "${1:-} ${2:-}" in
        "mcp list")
            python3 - "$cfg" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)
for name, entry in (data.get("mcpServers") or {}).items():
    command = entry.get("command", "") if isinstance(entry, dict) else ""
    print("%s: %s - \u2713" % (name, command))
PY
            return 0 ;;
        "mcp add")
            local name="${5:-}"
            [[ "${6:-}" == "--" ]] || return 2
            python3 - "$cfg" "$name" "${@:7}" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
argv = sys.argv[3:]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}
servers = data.setdefault("mcpServers", {})
if name in servers:
    print("MCP server %s already exists in user scope." % name, file=sys.stderr)
    sys.exit(1)
servers[name] = {"type": "stdio", "command": argv[0], "args": list(argv[1:]), "env": {}}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
            return $? ;;
        "mcp remove")
            python3 - "$cfg" "${3:-}" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    sys.exit(1)
if name not in (data.get("mcpServers") or {}):
    print("No MCP server found in user scope: %s" % name, file=sys.stderr)
    sys.exit(1)
data["mcpServers"].pop(name)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
            return $? ;;
    esac
    return 0
}

# gfy_wire_run <tmp> <verdict> [dry]: install_mcp_graphify against a fake uv and a
# fake claude that owns $tmp/.claude.json. <verdict> is what `uv tool install` does:
#   ok    writes the tool into uv's own bin dir and its link at
#         ~/.local/bin/graphify-mcp, and records the version it was given (so a
#         second run reads the pin back and skips, as the real tool does)
#   fail  exits 1 with nothing written — the network outage, or uv refusing
# Anything the caller wants in place before the run (the user's own file at the
# bin path, a seeded user-scope entry) is set up by the caller. The claude log is
# never truncated here, so a caller can count the calls across two runs.
gfy_wire_run() {
    local tmp="$1" verdict="$2" dry="${3:-0}"
    local log="$tmp/claude.log" cfg="$tmp/.claude.json" state="$tmp/uv.version"
    (
        SYS_HOME="$tmp" HOME="$tmp" AUTOOS_ROOT="$tmp/repo" AUTOOS_DRY_RUN="$dry"
        unset CLAUDE_CONFIG_DIR
        uv() {
            case "${1:-} ${2:-}" in
                "tool list")
                    if [[ -s "$state" ]]; then
                        printf '%s v%s\n- graphify\n- graphify-mcp\n' \
                            "$(mcp_package graphify | sed -e 's/\[.*//' -e 's/==.*//')" "$(cat "$state")"
                    else
                        printf 'No tools installed\n'
                    fi ;;
                "tool dir") printf '%s/uvtools\n' "$SYS_HOME" ;;
                "tool install")
                    if [[ "$verdict" == fail ]]; then
                        printf 'error: failed to fetch the package index\n' >&2
                        return 1
                    fi
                    printf '%s' "${*: -1}" | sed -e 's/.*==//' >"$state"
                    mkdir -p "$SYS_HOME/.local/bin" "$SYS_HOME/uvtools/graphifyy/bin"
                    printf '#!/bin/sh\nexit 0\n' >"$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    chmod +x "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp"
                    ln -sfn "$SYS_HOME/uvtools/graphifyy/bin/graphify-mcp" \
                        "$SYS_HOME/.local/bin/graphify-mcp" ;;
            esac
            return 0
        }
        claude() { gfy_claude "$log" "$cfg" "$@"; }
        install_mcp_graphify 2>&1
        printf 'RC=%s\n' "$?"
    )
}

# gfy_wire_entry <tmp>: the graphify block of the fake config, "command|args json".
gfy_wire_entry() {
    python3 - "$1" <<'PY' 2>/dev/null || echo missing
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    entry = (json.load(fh).get("mcpServers") or {}).get("graphify")
print("%s | %s" % (entry["command"], json.dumps(entry.get("args", []))))
PY
}

if it "graphify registers neither client when the pinned tool install failed"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    out="$(gfy_wire_run "$tmp" fail)"
    problems=""
    [[ "$out" == *RC=1* ]] || problems+="[the step reported success: $(tail -n 1 <<<"$out")] "
    grep -q 'mcp add' "$log" && problems+="[registered a command with no binary: $(tr '\n' '|' <"$log")] "
    [[ -e "$tmp/.gemini/config/mcp_config.json" ]] && problems+="[wrote the Antigravity config anyway] "
    [[ "$out" == *"not registered"* ]] || problems+="[nothing named the refusal: $out] "
    [[ -e "$tmp/.claude.json" ]] && problems+="[touched the user claude config] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify registers neither client when the tool bin path is the user's own"; then
    # The blocked link: AutoOS never replaces the file at ~/.local/bin/graphify-mcp,
    # so it never gets a graphify-mcp to point the clients at either.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    gfy_link_shape "$tmp" ownfile
    log="$tmp/claude.log"; : >"$log"
    before="$(cat "$tmp/.local/bin/graphify-mcp")"
    out="$(gfy_wire_run "$tmp" ok)"
    problems=""
    [[ "$out" == *RC=1* ]] || problems+="[the step reported success: $(tail -n 1 <<<"$out")] "
    grep -q 'mcp add' "$log" && problems+="[registered over the user's file: $(tr '\n' '|' <"$log")] "
    [[ -e "$tmp/.gemini/config/mcp_config.json" ]] && problems+="[wrote the Antigravity config anyway] "
    [[ "$out" == *"alone"* ]] || problems+="[nothing said the file is left alone: $out] "
    [[ "$(cat "$tmp/.local/bin/graphify-mcp")" == "$before" && ! -L "$tmp/.local/bin/graphify-mcp" ]] \
        || problems+="[the user's file was touched] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify registers both clients once the pinned tool resolves"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    out="$(gfy_wire_run "$tmp" ok)"
    problems=""
    [[ "$out" == *RC=0* ]] || problems+="[a good install reported failure: $out] "
    grep -qxF 'claude mcp add --scope user graphify -- graphify-mcp graphify-out/graph.json' "$log" \
        || problems+="[claude got: $(tr '\n' '|' <"$log")] "
    [[ "$(gfy_wire_entry "$tmp/.claude.json")" == \
        'graphify-mcp | ["graphify-out/graph.json"]' ]] \
        || problems+="[the user-scope entry: $(gfy_wire_entry "$tmp/.claude.json")] "
    got="$(python3 -c "
import json, sys
e = json.load(open(sys.argv[1], encoding='utf-8'))['mcpServers']['graphify']
print(e['command'], '|', json.dumps(e['args']))
" "$tmp/.gemini/config/mcp_config.json" 2>/dev/null || echo unreadable)"
    [[ "$got" == 'graphify-mcp | ["${workspaceFolder}/graphify-out/graph.json"]' ]] \
        || problems+="[antigravity spec: $got] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify repairs the uv run --with entry AutoOS wrote before"; then
    # The upgrade path: every machine an older AutoOS wired has this entry, and
    # register_mcp_server would keep reporting it "already registered" forever, so
    # the pinned tool would never reach the client that actually runs it.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    hl_cfg_write "$tmp" '{"graphify":{"type":"stdio","command":"uv","args":["--quiet","run","--with","graphifyy[mcp]==0.0.1-old","python","-m","graphify.serve","graphify-out/graph.json"]},"serena":{"command":"uvx"}}'
    out="$(gfy_wire_run "$tmp" ok)"
    problems=""
    [[ "$out" == *RC=0* ]] || problems+="[the repair reported failure: $(tail -n 1 <<<"$out")] "
    grep -qxF 'claude mcp remove graphify --scope user' "$log" \
        || problems+="[the stale entry was never removed: $(tr '\n' '|' <"$log")] "
    [[ "$(grep -n 'mcp remove' "$log" | cut -d: -f1)" -lt \
       "$(grep -n 'mcp add --scope user graphify' "$log" | cut -d: -f1)" ]] \
        || problems+="[added before removing the stale entry: $(tr '\n' '|' <"$log")] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 \
        || problems+="[the user config was rewritten with no backup] "
    [[ "$(gfy_wire_entry "$tmp/.claude.json")" == \
        'graphify-mcp | ["graphify-out/graph.json"]' ]] \
        || problems+="[the entry after the repair: $(gfy_wire_entry "$tmp/.claude.json")] "
    kept="$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(','.join(sorted(d.get('mcpServers', {})) + ['firstTimeRun' if 'firstTimeRun' in d else 'LOST']))
" "$tmp/.claude.json" 2>/dev/null || echo unreadable)"
    [[ "$kept" == "graphify,serena,firstTimeRun" ]] \
        || problems+="[the config lost or kept the wrong keys: $kept] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify repairs its own graphify-mcp entry that no longer resolves"; then
    # A checkout moved or was deleted, leaving an absolute graphify-mcp path that
    # answers to nothing. The bare pinned-tool form is what the installer writes now.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    hl_cfg_write "$tmp" '{"graphify":{"type":"stdio","command":"/home/u/old-checkout/infra/mcp-servers/bin/graphify-mcp","args":["graphify-out/graph.json"]}}'
    out="$(gfy_wire_run "$tmp" ok)"
    problems=""
    [[ "$out" == *RC=0* ]] || problems+="[the repair reported failure: $(tail -n 1 <<<"$out")] "
    grep -qxF 'claude mcp remove graphify --scope user' "$log" \
        || problems+="[the dead entry was never removed: $(tr '\n' '|' <"$log")] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 \
        || problems+="[the user config was rewritten with no backup] "
    [[ "$(gfy_wire_entry "$tmp/.claude.json")" == \
        'graphify-mcp | ["graphify-out/graph.json"]' ]] \
        || problems+="[the entry after the repair: $(gfy_wire_entry "$tmp/.claude.json")] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify leaves a user-defined graphify entry alone" ; then
    # Same name, nothing AutoOS wrote: a docker server, or the old uv run shape with
    # an env block of the user's. Removing either deletes a working server.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    hl_cfg_write "$tmp" '{"graphify":{"type":"stdio","command":"docker","args":["run","-i","--rm","my-graphify:latest"],"env":{"TOKEN":"DUMMY-SECRET-FOR-TESTS"}},"serena":{"command":"uvx"}}'
    before="$(cat "$tmp/.claude.json")"
    out="$(gfy_wire_run "$tmp" ok)"
    problems=""
    grep -q 'mcp remove' "$log" && problems+="[removed the user's entry: $(tr '\n' '|' <"$log")] "
    ls "$tmp"/.claude.json.autoos-backup-* >/dev/null 2>&1 && problems+="[a backup of a file it never changed] "
    [[ "$(cat "$tmp/.claude.json")" == "$before" ]] || problems+="[the config was rewritten] "
    [[ "$out" == *"alone"* ]] || problems+="[nothing said it was left alone: $out] "
    [[ "$out" != *DUMMY-SECRET-FOR-TESTS* ]] || problems+="[printed the entry args, which may hold a token] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify repairs once: the second run registers nothing new"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    first="$(gfy_wire_run "$tmp" ok)"
    calls_first="$(grep -cE 'claude mcp (add|remove)' "$log" || true)"
    second="$(gfy_wire_run "$tmp" ok)"
    calls="$(grep -cE 'claude mcp (add|remove)' "$log" || true)"
    n_backups="$(ls "$tmp"/.claude.json.autoos-backup-* 2>/dev/null | wc -l | tr -d ' ')"
    problems=""
    [[ "$first" == *RC=0* && "$second" == *RC=0* ]] \
        || problems+="[a run reported failure: ${first##*RC=} | ${second##*RC=}] "
    [[ "$second" == *"skipped"* ]] || problems+="[the second run did not say skipped: $second] "
    [[ "$second" != *"installed"* ]] || problems+="[the second run said installed: $second] "
    [[ "$calls" == "$calls_first" ]] \
        || problems+="[the second run wrote a client config again: $(tr '\n' '|' <"$log")] "
    [[ "$n_backups" == "0" ]] || problems+="[$n_backups backups on a config AutoOS itself wrote] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify dry run announces the tool form without installing or writing"; then
    # The plan a user reads must be the run they get: a dry run has no installed
    # binary yet, so it announces rather than refusing, and touches no config.
    if ! has_cmd python3; then skip "python3 not found"; else
    tmp="$(mktemp -d)"; gfy_repo_skeleton "$tmp"
    log="$tmp/claude.log"; : >"$log"
    out="$(gfy_wire_run "$tmp" ok 1)"
    problems=""
    [[ "$out" == *RC=0* ]] || problems+="[the dry run reported failure: $(tail -n 1 <<<"$out")] "
    [[ "$out" == *"would run: claude mcp add --scope user graphify -- graphify-mcp graphify-out/graph.json"* ]] \
        || problems+="[the claude plan is missing: $out] "
    [[ "$out" == *"would merge 'graphify' into Antigravity"* ]] \
        || problems+="[the Antigravity plan is missing: $out] "
    grep -qE 'claude mcp (add|remove)' "$log" && problems+="[the dry run wrote a client config: $(tr '\n' '|' <"$log")] "
    [[ -e "$tmp/.claude.json" ]] && problems+="[the dry run wrote the user config] "
    [[ -e "$tmp/.gemini/config/mcp_config.json" ]] && problems+="[the dry run wrote the Antigravity config] "
    [[ -e "$tmp/.local/bin/graphify-mcp" ]] && problems+="[the dry run installed the tool] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
    fi
fi

if it "graphify registration runs on the gate that decides it"; then
    # The gate lives in install_mcp_graphify, not in a helper nothing calls.
    if awk '/^install_mcp_graphify\(\)/,/^}/' lib/linux/install.sh |
            grep -q "install_graphify_tool"; then pass
    else fail "install_mcp_graphify no longer gates registration on install_graphify_tool"; fi
fi
