#!/usr/bin/env bash
# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── omnigraph-client (A3) ──────────────────────────────────────────────────
#
# The component the operator runs on a machine that talks to a shared Omnigraph
# server: it writes ~/.autoos-omnigraph.env, pre-installs the pinned bridge into
# a private npm prefix (measured: npx start-up is 6.7-9.3 s, spec D9), installs
# the `omnigraph-mcp-autoos` wrapper that reads the env file itself, and retires
# the rc-file token line that used to read from the retired agent-skills tree.
#
# Every case is hermetic: SYS_HOME is a temp dir, npm is a shell function that
# lands the same tree `npm install -g --prefix` lands, and the token is a dummy
# literal. Nothing here reaches a network or a real graph server.

describe "omnigraph-client"

# oh_pin <pin>: run the component in a sandbox with a URL answer and a token.
# The pin argument replaces the harness file so a "different pin" case does not
# have to rewrite a version literal into lib/ (the mcp-pins test forbids that).
oh_client_sandbox() {
    mktemp -d
}

# oh_fake_npm_install <prefix> <name> <version> <bin-name>
# The observable contract of `npm install -g --prefix P <pkg>`: the package tree
# under P/lib/node_modules and one executable under P/bin (verified against the
# registry metadata for @modernrelay/omnigraph-mcp@0.8.0: bin omnigraph-mcp ->
# dist/bin.js).
oh_fake_npm_install() {
    local prefix="$1" name="$2" version="$3" bin="$4"
    mkdir -p "$prefix/lib/node_modules/$name" "$prefix/bin"
    printf '{"name":"%s","version":"%s","bin":{"%s":"dist/bin.js"}}\n' \
        "$name" "$version" "$bin" >"$prefix/lib/node_modules/$name/package.json"
    mkdir -p "$prefix/lib/node_modules/$name/dist"
    printf '#!/usr/bin/env node\n// fake bridge\n' >"$prefix/lib/node_modules/$name/dist/bin.js"
    printf '#!/bin/sh\nprintf "bridge\\n"\n' >"$prefix/bin/$bin"
    chmod 755 "$prefix/bin/$bin"
}

# oh_harness_with_pin <version> <name>: a temp agent-harness.json carrying one pin.
oh_harness_with_pin() {
    local version="$1" name="${2:-@modernrelay/omnigraph-mcp}" tmp file
    tmp="$(mktemp -d)"
    file="$tmp/agent-harness.json"
    python3 -c '
import json, sys
src, dst, pin, name = sys.argv[1:5]
data = json.load(open(src, encoding="utf-8"))
data["mcp_servers"]["omnigraph"]["package"] = name + "@" + pin
json.dump(data, open(dst, "w", encoding="utf-8"))
' catalog/agent-harness.json "$file" "$version" "$name"
    printf '%s\n' "$file"
}

# oh_run_client <sandbox> [setup lines...]: the component, with a URL answer and a
# token, npm stubbed. Prints the UI output.
oh_run_client() {
    local tmp="$1" harness="${2:-}"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_EXTRA_FAILURES=()
        OMNIGRAPH_CLIENT_CHANGED=0
        OMNIGRAPH_TOKEN="dummy-token-1234"
        unset OMNIGRAPH_GRAPH_ID
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        [[ -n "$harness" ]] && AUTOOS_HARNESS="$harness"
        # shellcheck disable=SC2016
        AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
        npm() {
            local prefix="" arg next=0 spec name version
            for arg in "$@"; do
                if (( next )); then prefix="$arg"; next=0; continue; fi
                case "$arg" in
                    --prefix) next=1 ;;
                    --prefix=*) prefix="${arg#--prefix=}" ;;
                esac
            done
            [[ -n "$prefix" ]] || { printf 'npm stub: no --prefix\n' >&2; return 9; }
            # The last argument is the package spec, and the tree npm lands for
            # @scope/name@1.2.3 carries exactly that version - the pin guard is
            # only tested if the stub honours it too.
            spec="${@: -1}"
            name="${spec%@*}"; version="${spec##*@}"
            [[ "$name" != "$spec" ]] || { printf 'npm stub: unpinned spec %s\n' "$spec" >&2; return 9; }
            oh_fake_npm_install "$prefix" "$name" "$version" "omnigraph-mcp"
        }
        install_omnigraph_client
        printf 'CHANGED %s\nSTATE %s\nFAILURES %s\n' \
            "${OMNIGRAPH_CLIENT_CHANGED:-0}" "${INSTALL_SCRIPT_STATE:-installed}" \
            "${AUTOOS_EXTRA_FAILURES[*]-}"
    ) 2>&1
}

if it "omnigraph-client: a missing omnigraph_url answer skips with a hint and records no failure"; then
    tmp="$(oh_client_sandbox)"
    out="$( (
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        AUTOOS_ANSWERS=([omnigraph_url]="")
        OMNIGRAPH_TOKEN="dummy-token-1234"
        install_omnigraph_client
        printf 'STATE %s\nFAILURES [%s]\n' "${INSTALL_SCRIPT_STATE:-installed}" "${AUTOOS_EXTRA_FAILURES[*]-}"
    ) 2>&1)"; rc=$?
    files="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    ok=1
    (( rc == 0 )) || { ok=0; echo "the step returned $rc, not 0" >&2; }
    [[ "$out" == *"omnigraph-client: skipped: no omnigraph URL"* ]] || { ok=0; echo "no skip line: [${out:0:300}]" >&2; }
    [[ "$out" == *"omnigraph_url"* ]] || { ok=0; echo "the hint does not name omnigraph_url" >&2; }
    [[ "$out" == *"STATE skipped"* ]] || { ok=0; echo "the step did not report skipped: $out" >&2; }
    [[ "$out" == *"FAILURES []"* ]] || { ok=0; echo "a skip recorded a failure: $out" >&2; }
    [[ "$files" == 0 ]] || { ok=0; echo "the skip touched the disk ($files files)" >&2; }
    (( ok )) && pass || fail "missing-URL skip is wrong"
fi

if it "omnigraph-client: a missing token skips with a hint naming both places and records no failure"; then
    tmp="$(oh_client_sandbox)"
    out="$( (
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        # shellcheck disable=SC2016
        AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
        unset OMNIGRAPH_TOKEN
        install_omnigraph_client
        printf 'STATE %s\nFAILURES [%s]\n' "${INSTALL_SCRIPT_STATE:-installed}" "${AUTOOS_EXTRA_FAILURES[*]-}"
    ) 2>&1)"; rc=$?
    files="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    ok=1
    (( rc == 0 )) || { ok=0; echo "the step returned $rc, not 0" >&2; }
    [[ "$out" == *"omnigraph-client: skipped: no omnigraph token"* ]] || { ok=0; echo "no skip line: [${out:0:300}]" >&2; }
    [[ "$out" == *"OMNIGRAPH_TOKEN"* && "$out" == *"omnigraph_token"* ]] \
        || { ok=0; echo "the hint names neither OMNIGRAPH_TOKEN nor the omnigraph_token key" >&2; }
    [[ "$out" == *"STATE skipped"* && "$out" == *"FAILURES []"* ]] || { ok=0; echo "state/failures wrong: $out" >&2; }
    [[ "$files" == 0 ]] || { ok=0; echo "the skip touched the disk ($files files)" >&2; }
    (( ok )) && pass || fail "missing-token skip is wrong"
fi

if it "omnigraph-client: the token comes from api-keys.yml when the env carries none, and is never printed"; then
    tmp="$(oh_client_sandbox)"
    printf 'omnigraph_token: from-keys-file\n' >"$tmp/keys.yml"
    out="$( (
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        # shellcheck disable=SC2016
        AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
        unset OMNIGRAPH_TOKEN
        install_omnigraph_client >/dev/null
    ) 2>&1)"
    envline="$(grep -c '^OMNIGRAPH_TOKEN=from-keys-file$' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$envline" == 1 ]] || { ok=0; echo "the keys-file token did not reach the env file (count $envline)" >&2; }
    [[ "$out" != *"from-keys-file"* ]] || { ok=0; echo "the token value was printed" >&2; }
    (( ok )) && pass || fail "the keys-file token path is wrong"
fi

if it "omnigraph-client: a full run writes the env file at 600, the pinned bridge, and a 755 wrapper"; then
    tmp="$(oh_client_sandbox)"
    out="$(oh_run_client "$tmp")"
    mode="$(stat -c '%a' "$tmp/.autoos-omnigraph.env" 2>/dev/null || echo missing)"
    base="$(grep -c '^OMNIGRAPH_BASE_URL=https://graph.example.invalid$' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    tok="$(grep -c '^OMNIGRAPH_TOKEN=' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    link="$(readlink "$tmp/.config/environment.d/60-autoos-omnigraph.conf" 2>/dev/null || echo none)"
    bridge="$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    wmode="$(stat -c '%a' "$tmp/.local/bin/omnigraph-mcp-autoos" 2>/dev/null || echo missing)"
    wtype="file"; [[ -L "$tmp/.local/bin/omnigraph-mcp-autoos" ]] && wtype="link"
    srcdiff="$(cmp -s tools/omnigraph-mcp-autoos.sh "$tmp/.local/bin/omnigraph-mcp-autoos" && echo same || echo differs)"
    rm -rf "$tmp"
    ok=1
    [[ "$mode" == 600 ]] || { ok=0; echo "env file mode [$mode]" >&2; }
    [[ "$base" == 1 && "$tok" == 1 ]] || { ok=0; echo "env file rows [$base/$tok]" >&2; }
    [[ "$link" == *".autoos-omnigraph.env" ]] || { ok=0; echo "environment.d link [$link]" >&2; }
    [[ "$out" == *"0.8.0"* ]] || { ok=0; echo "the run never named the pinned version: [${out:0:400}]" >&2; }
    [[ "$wmode" == 755 && "$wtype" == file && "$srcdiff" == same ]] \
        || { ok=0; echo "wrapper mode=[$wmode] type=[$wtype] content=[$srcdiff]" >&2; }
    [[ "$out" == *"CHANGED 1"* ]] || { ok=0; echo "the run did not report a change: $out" >&2; }
    [[ "$out" != *"FAILURES dummy"* ]] || { ok=0; echo "a token leaked into the failure list" >&2; }
    (( ok )) && pass || fail "the full run is wrong ($bridge)"
fi

if it "omnigraph-client: the second run reports every step skipped or unchanged"; then
    tmp="$(oh_client_sandbox)"
    first="$(oh_run_client "$tmp")"
    second="$(oh_run_client "$tmp")"
    rm -rf "$tmp"
    ok=1
    [[ "$first" == *"CHANGED 1"* ]] || { ok=0; echo "the first run did not change anything" >&2; }
    [[ "$second" == *"CHANGED 0"* ]] || { ok=0; echo "the second run changed the disk again" >&2; }
    [[ "$second" == *"STATE skipped"* ]] || { ok=0; echo "the second run is not skipped: [${second:0:300}]" >&2; }
    [[ "$second" == *"already installed"* ]] || { ok=0; echo "the bridge step did not say skipped: [${second:0:400}]" >&2; }
    [[ "$second" == *"wrapper unchanged"* ]] || { ok=0; echo "the wrapper step did not say unchanged" >&2; }
    [[ "$second" == *"env file unchanged"* ]] || { ok=0; echo "the env step did not say unchanged" >&2; }
    (( ok )) && pass || fail "the second run is not idempotent"
fi

if it "omnigraph-client: a bridge at another pin is reinstalled, a bridge at the pin is skipped"; then
    tmp="$(oh_client_sandbox)"
    harness="$(oh_harness_with_pin 0.8.0)"
    out1="$(oh_run_client "$tmp" "$harness")"
    # Now the harness moves the pin; the installed tree still carries 0.8.0.
    harness9="$(oh_harness_with_pin 0.9.9)"
    out2="$(oh_run_client "$tmp" "$harness9")"
    out3="$(oh_run_client "$tmp" "$harness9")"
    rm -rf "$tmp" "$harness" "$harness9"
    ok=1
    [[ "$out1" == *"CHANGED 1"* ]] || { ok=0; echo "the first install changed nothing" >&2; }
    [[ "$out2" == *"reinstalling"* && "$out2" == *"CHANGED 1"* ]] \
        || { ok=0; echo "a different pin did not trigger a reinstall: [${out2:0:400}]" >&2; }
    [[ "$out3" == *"already installed"* && "$out3" == *"CHANGED 0"* ]] \
        || { ok=0; echo "the reinstalled bridge was not recognised as current: [${out3:0:400}]" >&2; }
    (( ok )) && pass || fail "the pin guard is wrong"
fi

if it "omnigraph-client: an AutoOS wrapper is updated after a backup; the user's own file is left alone"; then
    tmp="$(oh_client_sandbox)"
    mkdir -p "$tmp/.local/bin"
    printf '#!/bin/sh\n# mine, nothing to do with AutoOS\nexit 7\n' >"$tmp/.local/bin/omnigraph-mcp-autoos"
    chmod 700 "$tmp/.local/bin/omnigraph-mcp-autoos"
    out="$(oh_run_client "$tmp")"
    kept="$(cmp -s "$tmp/.local/bin/omnigraph-mcp-autoos" <(printf '#!/bin/sh\n# mine, nothing to do with AutoOS\nexit 7\n') && echo same || echo changed)"
    mode="$(stat -c '%a' "$tmp/.local/bin/omnigraph-mcp-autoos")"
    backups="$(ls "$tmp"/.local/bin/omnigraph-mcp-autoos.autoos-backup-* 2>/dev/null | wc -l)"
    # Second component of the case: our own older copy IS replaced, with a backup.
    printf '#!/usr/bin/env bash\n# AutoOS:omnigraph-mcp-autoos\n# an older AutoOS copy\n' >"$tmp/.local/bin/omnigraph-mcp-autoos"
    out2="$(oh_run_client "$tmp")"
    current="$(cmp -s tools/omnigraph-mcp-autoos.sh "$tmp/.local/bin/omnigraph-mcp-autoos" && echo same || echo differs)"
    backups2="$(ls "$tmp"/.local/bin/omnigraph-mcp-autoos.autoos-backup-* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    ok=1
    [[ "$kept" == same && "$mode" == 700 ]] || { ok=0; echo "the user's own wrapper was touched ($kept, mode $mode)" >&2; }
    [[ "$out" == *"left alone"* ]] || { ok=0; echo "the refusal was not announced: [${out:0:400}]" >&2; }
    [[ "$out" == *"FAILURES omnigraph-client"* ]] \
        || { ok=0; echo "the refusal was not recorded for the summary: [${out:0:400}]" >&2; }
    [[ "$backups" == 0 ]] || { ok=0; echo "a backup was made for a file that was not changed" >&2; }
    [[ "$current" == same && "$out2" == *"updated"* ]] \
        || { ok=0; echo "our own older wrapper was not replaced: [${out2:0:400}] [$current]" >&2; }
    (( backups2 >= 2 )) || { ok=0; echo "the replacement had no backup ($backups2)" >&2; }
    (( ok )) && pass || fail "the wrapper rules are wrong"
fi

if it "omnigraph-client: a retired agent-skills token line is removed after a backup and other lines stay"; then
    tmp="$(oh_client_sandbox)"
    mine='# keep me: OMNIGRAPH_TOKEN mentions the variable but no agent-skills path'
    other='export GRAPHIFY_HOME=$HOME/Documents/Code/agent-skills/tools'
    printf '%s\n%s\n%s\n%s\n' \
        '# my shell' "$mine" "$other" \
        'export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")' \
        >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    before="$(md5sum <"$tmp/.bashrc")"
    out="$(oh_run_client "$tmp")"
    gone="$(grep -c 'agent-skills/secrets/omnigraph.token' "$tmp/.bashrc" || true)"
    gone_z="$(grep -c 'agent-skills/secrets/omnigraph.token' "$tmp/.zshrc" || true)"
    keptmine="$(grep -cF -- 'keep me: OMNIGRAPH_TOKEN' "$tmp/.bashrc" || true)"
    keptother="$(grep -cF -- "$other" "$tmp/.bashrc" || true)"
    backups="$(ls "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | wc -l)"
    restore="$(cat "$tmp"/.bashrc.autoos-backup-* | md5sum)"
    second="$(oh_run_client "$tmp")"
    lines2="$(wc -l <"$tmp/.bashrc")"
    rm -rf "$tmp"
    ok=1
    [[ "$gone" == 0 && "$gone_z" == 0 ]] || { ok=0; echo "the retired line survived (bashrc $gone, zshrc $gone_z)" >&2; }
    [[ "$keptmine" == 1 && "$keptother" == 1 ]] || { ok=0; echo "an unrecognised line was removed ($keptmine/$keptother)" >&2; }
    (( backups >= 1 )) || { ok=0; echo "the rc file was edited with no backup" >&2; }
    [[ "$(printf '%s\n' "$restore" | tr -d ' \n-')" == "$(printf '%s\n' "$before" | tr -d ' \n-')" ]] \
        || { ok=0; echo "the backup is not the original file" >&2; }
    [[ "$out" == *"agent-skills"* ]] || { ok=0; echo "the removal was not announced" >&2; }
    [[ "$second" == *"CHANGED 0"* && "$lines2" == 3 ]] \
        || { ok=0; echo "the second run changed the rc file again ($lines2 lines)" >&2; }
    (( ok )) && pass || fail "the recognised-only rc removal is wrong"
fi

if it "omnigraph-client: the wrapper reads the env file itself, keeps a value already in the env, and never echoes the token"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.local/share/autoos/omnigraph-mcp/bin"
    # A bridge that reports which variables it sees, never their values.
    cat >"$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp" <<'EOF'
#!/bin/sh
printf 'base=%s\n' "${OMNIGRAPH_BASE_URL:-}"
printf 'have_token=%s\n' "$([ -n "${OMNIGRAPH_TOKEN:-}" ] && echo yes || echo no)"
printf 'graph=%s\n' "${OMNIGRAPH_GRAPH_ID:-}"
printf 'len=%s\n' "${#OMNIGRAPH_TOKEN}"
EOF
    chmod 755 "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    printf 'OMNIGRAPH_BASE_URL=https://from-file.example\nOMNIGRAPH_TOKEN=secret-from-file\nOMNIGRAPH_GRAPH_ID=graphfromfile\nOTHER_SECRET=nope\n' \
        >"$tmp/.autoos-omnigraph.env"
    cp tools/omnigraph-mcp-autoos.sh "$tmp/wrapper.sh"
    chmod 755 "$tmp/wrapper.sh"
    plain="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 2>&1)"
    wins="$(env -i HOME="$tmp" PATH=/usr/bin:/bin OMNIGRAPH_TOKEN=from-the-env OMNIGRAPH_BASE_URL=https://from-env.example \
        bash "$tmp/wrapper.sh" 2>&1)"
    untouched="$(env -i HOME="$tmp" PATH=/usr/bin:/bin OMNIGRAPH_GRAPH_ID=env-graph \
        bash "$tmp/wrapper.sh" 2>&1)"
    rm -rf "$tmp"
    ok=1
    [[ "$plain" == *"base=https://from-file.example"* ]] || { ok=0; echo "the env file was not read: [$plain]" >&2; }
    [[ "$plain" == *"have_token=yes"* && "$plain" == *"len=16"* ]] || { ok=0; echo "the token did not reach the bridge: [$plain]" >&2; }
    [[ "$plain" == *"graph=graphfromfile"* ]] || { ok=0; echo "OMNIGRAPH_GRAPH_ID was not read: [$plain]" >&2; }
    [[ "$plain" != *"nope"* ]] || { ok=0; echo "a key outside the three was exported" >&2; }
    [[ "$wins" == *"base=https://from-env.example"* && "$wins" == *"len=12"* ]] \
        || { ok=0; echo "the caller's env did not win: [$wins]" >&2; }
    [[ "$untouched" == *"graph=env-graph"* ]] || { ok=0; echo "the env overrode a value the file does not hold" >&2; }
    (( ok )) && pass || fail "the wrapper's env handling is wrong"
fi

if it "omnigraph-client: the wrapper exits 127 with a one-line hint when the bridge is missing"; then
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=https://from-file.example\nOMNIGRAPH_TOKEN=secret-from-file\n' >"$tmp/.autoos-omnigraph.env"
    cp tools/omnigraph-mcp-autoos.sh "$tmp/wrapper.sh"
    out="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 2>&1)"; rc=$?
    hint_lines="$(printf '%s\n' "$out" | grep -c . || true)"
    rm -rf "$tmp"
    ok=1
    (( rc == 127 )) || { ok=0; echo "exit was $rc, not 127" >&2; }
    [[ "$out" == *"omnigraph-client"* ]] || { ok=0; echo "the hint does not name the component to re-run: [$out]" >&2; }
    [[ "$hint_lines" == 1 ]] || { ok=0; echo "the hint is not one line ($hint_lines): [$out]" >&2; }
    [[ "$out" != *"secret-from-file"* ]] || { ok=0; echo "the hint echoed the token" >&2; }
    (( ok )) && pass || fail "the missing-bridge path is wrong"
fi

if it "omnigraph-client: a dry run announces and writes nothing"; then
    tmp="$(oh_client_sandbox)"
    out="$( (
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=1 AUTOOS_EXTRA_FAILURES=()
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        OMNIGRAPH_TOKEN="dummy-token-1234"
        # shellcheck disable=SC2016
        AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
        npm() { printf 'NPM RAN\n'; }
        install_omnigraph_client
    ) 2>&1)"
    files="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    ok=1
    [[ "$out" == *"would "* ]] || { ok=0; echo "nothing was announced: [${out:0:300}]" >&2; }
    [[ "$out" != *"NPM RAN"* ]] || { ok=0; echo "npm ran in a dry run" >&2; }
    [[ "$files" == 0 ]] || { ok=0; echo "the dry run wrote $files files" >&2; }
    (( ok )) && pass || fail "the dry run is not clean"
fi

if it "omnigraph-client: write_omnigraph_env takes the token as an argument and other callers keep the env behaviour"; then
    tmp="$(mktemp -d)"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      unset OMNIGRAPH_TOKEN
      docker() { return 1; }
      write_omnigraph_env "https://arg.example" "token-from-argument" >/dev/null 2>&1 )
    arg="$(grep -c '^OMNIGRAPH_TOKEN=token-from-argument$' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      docker() { return 1; }
      write_omnigraph_env "https://env.example" >/dev/null 2>&1 )
    caller="$(grep -c '^OMNIGRAPH_TOKEN=token-from-argument$' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    base="$(grep -c '^OMNIGRAPH_BASE_URL=https://env.example$' "$tmp/.autoos-omnigraph.env" 2>/dev/null || true)"
    rm -rf "$tmp"
    assert_eq "$arg|$caller|$base" "1|1|1"
fi

if it "omnigraph-client: linux and macos carry the component and the catalogs still validate"; then
    out="$(python3 - <<'PY'
import json, sys
want = {
    "id": "omnigraph-client", "provider": "custom", "package": "omnigraph-client",
    "postInstall": "install_omnigraph_client", "prompt": "omnigraph_url",
    "requires": ["nodejs"], "profiles": ["workstation", "ai-coding", "light"],
}
problems = []
for f in ("catalog/linux.json", "catalog/macos.json"):
    data = json.load(open(f, encoding="utf-8"))
    found = [c for g in data["categories"] for c in g["components"] if c.get("id") == "omnigraph-client"]
    if len(found) != 1:
        problems.append(f + ": " + str(len(found)) + " entries"); continue
    c = found[0]
    for k, v in want.items():
        if c.get(k) != v:
            problems.append("%s: %s is %r, expected %r" % (f, k, c.get(k), v))
    if c.get("homepage") != "https://www.npmjs.com/package/@modernrelay/omnigraph-mcp":
        problems.append(f + ": homepage is not the npm package page")
    if not c.get("notes"):
        problems.append(f + ": no notes for the plan entry")
    if "server" in c.get("profiles", []):
        problems.append(f + ": the server profile is on hold")
print("\n".join(problems))
sys.exit(1 if problems else 0)
PY
)" && catalog_validate catalog/linux.json >/dev/null 2>&1 && catalog_validate catalog/macos.json >/dev/null 2>&1 \
    && pass || fail "catalog entry: ${out:0:300}"
fi

if it "omnigraph-client: the api-keys template carries a commented omnigraph_token placeholder"; then
    ok=1
    grep -qE '^#?[[:space:]]*omnigraph_token:[[:space:]]*REPLACE_WITH_OMNIGRAPH_TOKEN[[:space:]]*$' \
        configuration/api-keys.example.yml \
        || { ok=0; echo "no omnigraph_token placeholder line" >&2; }
    grep -qE '^[[:space:]]*omnigraph_token:' configuration/api-keys.example.yml \
        && { ok=0; echo "the key is live, not commented out" >&2; }
    grep -B3 -E 'omnigraph_token:[[:space:]]*REPLACE_WITH_OMNIGRAPH_TOKEN' configuration/api-keys.example.yml \
        | grep -qi 'graph server' || { ok=0; echo "the comment does not say who issues the token" >&2; }
    (( ok )) && pass || fail "api-keys.example.yml is wrong"
fi

if it "omnigraph-client: docs name the component and the token rotation procedure"; then
    ok=1
    grep -q 'omnigraph-client' docs/omnigraph.md || { ok=0; echo "docs/omnigraph.md has no omnigraph-client section" >&2; }
    grep -qi 'Token rotation' docs/omnigraph.md || { ok=0; echo "no token-rotation heading" >&2; }
    grep -q 'omnigraph-mcp-autoos' docs/omnigraph.md || { ok=0; echo "the wrapper is undocumented" >&2; }
    grep -q 'omnigraph-client' docs/catalog.md || { ok=0; echo "docs/catalog.md does not list the component" >&2; }
    (( ok )) && pass || fail "the docs are missing"
fi
