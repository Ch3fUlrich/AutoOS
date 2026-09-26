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
    mkdir -p "$tmp/Documents/code/agent-skills/skills/test-skill"
    printf -- '---\nname: test-skill\ndescription: test\n---\n' >"$tmp/Documents/code/agent-skills/skills/test-skill/SKILL.md"
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

# ─── OpenHands: repo skills mirrored per skill, idempotent settings writer ──
# oh_setup_run <home> <repo>: setup_openhands_config in a hermetic subshell -
# scratch HOME and AUTOOS_ROOT, no gateway or provider key, no network - with
# stdout and stderr merged. AUTOOS_KEYS_FILE points at a file that does not
# exist so the repo's own keys file is never consulted.
oh_setup_run() {
    local home="$1" repo="$2"
    mkdir -p "$home"
    (
        SYS_HOME="$home"; AUTOOS_DRY_RUN=0
        if [[ -n "$repo" ]]; then AUTOOS_ROOT="$repo"; else unset AUTOOS_ROOT; fi
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        unset AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        export AUTOOS_KEYS_FILE="$home/no-keys.yml"
        curl() { return 6; }
        setup_openhands_config
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

if it "custom_is_installed detects agent-skills under Documents/code or Documents/Code"; then
    tmp="$(mktemp -d)"
    (
        SYS_HOME="$tmp"
        mkdir -p "$tmp/Documents/code/agent-skills"
        custom_is_installed agent-skills
    )
    rc_code=$?
    (
        SYS_HOME="$tmp"
        rm -rf "$tmp/Documents/code"
        mkdir -p "$tmp/Documents/Code/agent-skills"
        custom_is_installed agent-skills
    )
    rc_Code=$?
    rm -rf "$tmp"
    if [[ $rc_code -eq 0 && $rc_Code -eq 0 ]]; then pass
    else fail "rc_code=$rc_code rc_Code=$rc_Code"; fi
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

