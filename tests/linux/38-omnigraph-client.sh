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

# oh_hermetic_root: A7b moved three repo-scope duties into
# install_omnigraph_client — name a shadowing user-scope omnigraph, approve this
# checkout's project MCP servers, report the retired homelab entry. They have
# their own cases in 18-mcp-wiring.sh; here the component must point at a scratch
# checkout that holds no .mcp.json (so the approve step warns and writes nothing)
# and must never ask the real Claude Code for its server list. Call it inside the
# subshell: the mcp_has_server override dies with it. tools/ is linked in because
# the wrapper step installs the tracked script it finds there — a scratch checkout
# without it is a machine where that step legitimately has nothing to copy.
OH_REPO="$(mktemp -d)/repo"
oh_hermetic_root() {
    mkdir -p "$OH_REPO"
    [[ -e "$OH_REPO/tools" ]] || ln -s "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/tools" "$OH_REPO/tools"
    AUTOOS_ROOT="$OH_REPO"
    mcp_has_server() { return 1; }
}

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

# oh_run_client <sandbox> [harness] [token]: the component, with a URL answer and
# a token, npm stubbed. Prints the UI output. The token argument defaults to a
# dummy; "-" means "the env carries no token", so the api-keys.yml path is the
# one that resolves — which is how a rotation is tested.
oh_run_client() {
    local tmp="$1" harness="${2:-}" token="${3-dummy-token-1234}"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_EXTRA_FAILURES=()
        OMNIGRAPH_CLIENT_CHANGED=0
        if [[ "$token" == - ]]; then unset OMNIGRAPH_TOKEN; else OMNIGRAPH_TOKEN="$token"; fi
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
            spec="${!#}"
            name="${spec%@*}"; version="${spec##*@}"
            [[ "$name" != "$spec" ]] || { printf 'npm stub: unpinned spec %s\n' "$spec" >&2; return 9; }
            oh_fake_npm_install "$prefix" "$name" "$version" "omnigraph-mcp"
        }
        oh_hermetic_root
        install_omnigraph_client
        printf 'CHANGED %s\nSTATE %s\nFAILURES %s\n' \
            "${OMNIGRAPH_CLIENT_CHANGED:-0}" "${INSTALL_SCRIPT_STATE:-installed}" \
            "${AUTOOS_EXTRA_FAILURES[*]-}"
    ) 2>&1
}

# oh_gate <sandbox> [token]: asks the question install_component asks BEFORE it
# would run the postInstall at all — `is_installed custom omnigraph-client` on
# this machine, with this answer and this token. Prints "current" when the gate
# would skip the component, "open" when it would run it. A rotation that the gate
# cannot see never reaches the writer, so the gate is the thing that has to be
# compared against the resolved values (A3 review, HIGH).
oh_gate() {
    local tmp="$1" token="${2-dummy-token-1234}"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        if [[ "$token" == - ]]; then unset OMNIGRAPH_TOKEN; else OMNIGRAPH_TOKEN="$token"; fi
        unset OMNIGRAPH_GRAPH_ID
        # shellcheck disable=SC2016
        AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
        is_installed custom omnigraph-client
    ) >/dev/null 2>&1 \
        && printf 'current\n' || printf 'open\n'
}

if it "omnigraph-client: a missing omnigraph_url answer skips with a hint and records no failure"; then
    tmp="$(oh_client_sandbox)"
    out="$( (
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
        AUTOOS_KEYS_FILE="$tmp/keys.yml"
        AUTOOS_ANSWERS=([omnigraph_url]="")
        OMNIGRAPH_TOKEN="dummy-token-1234"
        oh_hermetic_root
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
        oh_hermetic_root
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
        oh_hermetic_root
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

if it "omnigraph-client: the full run merges its Antigravity entry and keeps the user's own servers"; then
    # Antigravity has no project scope, so its omnigraph entry has to be a user
    # entry — the one duty A7b moved here that no other client of this graph has.
    # The config is merged (hard rule 4), and the entry carries the resolved URL.
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.gemini/config"
    printf '%s' '{"mcpServers":{"existing":{"command":"node","args":["index.js"]}}}' \
        >"$tmp/.gemini/config/mcp_config.json"
    oh_run_client "$tmp" >/dev/null
    kept="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(" ".join(sorted(d["mcpServers"])))
' "$tmp/.gemini/config/mcp_config.json" 2>&1)"
    url="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(d["mcpServers"]["omnigraph"]["env"]["OMNIGRAPH_BASE_URL"])
' "$tmp/.gemini/config/mcp_config.json" 2>&1)"
    # A second run must not rewrite the entry it already wrote.
    out2="$(oh_run_client "$tmp")"
    rm -rf "$tmp"
    ok=1
    [[ "$kept" == "existing omnigraph" ]] || { ok=0; echo "servers after the run [$kept]" >&2; }
    [[ "$url" == "https://graph.example.invalid" ]] || { ok=0; echo "the entry points at [$url]" >&2; }
    [[ "$out2" == *"already configured in Antigravity"* ]] \
        || { ok=0; echo "the second run did not report the entry as settled: [${out2:0:400}]" >&2; }
    (( ok )) && pass || fail "the Antigravity entry is not the component's own"
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

if it "omnigraph-client: a pin with an empty version is refused, not read as current"; then
    tmp="$(oh_client_sandbox)"
    harness="$(oh_harness_with_pin "")"
    src_before="$(md5sum <tools/omnigraph-mcp-autoos.sh)"
    out="$(oh_run_client "$tmp" "$harness")"
    gate="$(oh_gate "$tmp")"
    have_tree="$(ls "$tmp"/.local/share/autoos/omnigraph-mcp/lib/node_modules 2>/dev/null | wc -l)"
    src_after="$(md5sum <tools/omnigraph-mcp-autoos.sh)"
    rm -rf "$tmp" "$harness"
    ok=1
    [[ "$out" == *"carries no @version"* ]] \
        || { ok=0; echo "an empty pin was not refused: [${out:0:400}]" >&2; }
    [[ "$have_tree" == 0 ]] || { ok=0; echo "npm installed an empty-pinned spec ($have_tree trees)" >&2; }
    [[ "$gate" == open ]] || { ok=0; echo "the empty pin reads as current" >&2; }
    [[ "$src_before" == "$src_after" ]] || { ok=0; echo "the tracked wrapper changed" >&2; }
    (( ok )) && pass || fail "the empty-pin guard is wrong"
fi

if it "omnigraph-client: a symlinked wrapper is refused and nothing is written through the link"; then
    tmp="$(oh_client_sandbox)"
    oh_run_client "$tmp" >/dev/null
    wrapper="$tmp/.local/bin/omnigraph-mcp-autoos"
    src_before="$(md5sum <tools/omnigraph-mcp-autoos.sh)"
    # The shape a developer leaves behind: a link to the tracked file, whose
    # bytes match, so a content-only check calls it current forever — and a real
    # content change would be `cp`'d straight through the link into the checkout.
    rm -f "$wrapper"
    ln -s "$PWD/tools/omnigraph-mcp-autoos.sh" "$wrapper"
    gate="$(oh_gate "$tmp")"
    out="$(oh_run_client "$tmp")"
    is_link=no; [[ -L "$wrapper" ]] && is_link=yes
    src_after="$(md5sum <tools/omnigraph-mcp-autoos.sh)"
    rm -rf "$tmp"
    ok=1
    [[ "$gate" == open ]] || { ok=0; echo "a symlink counts as the installed wrapper" >&2; }
    [[ "$out" == *"link"* && "$out" == *"left alone"* ]] \
        || { ok=0; echo "the symlink was not refused by name: [${out:0:400}]" >&2; }
    [[ "$out" == *"FAILURES omnigraph-client"* ]] \
        || { ok=0; echo "the refusal was not recorded: [${out:0:400}]" >&2; }
    [[ "$is_link" == yes ]] || { ok=0; echo "AutoOS replaced the user's link without asking" >&2; }
    [[ "$src_before" == "$src_after" ]] \
        || { ok=0; echo "the wrapper was copied THROUGH the link into the tracked checkout" >&2; }
    (( ok )) && pass || fail "the copied-not-linked contract is not enforced"
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
    (( backups2 >= 1 )) || { ok=0; echo "the replacement had no backup ($backups2)" >&2; }
    (( ok )) && pass || fail "the wrapper rules are wrong"
fi

if it "omnigraph-client: a retired agent-skills token line is removed after a backup and other lines stay"; then
    tmp="$(oh_client_sandbox)"
    mine='# keep me: OMNIGRAPH_TOKEN mentions the variable but no agent-skills path'
    other='export GRAPHIFY_HOME=$HOME/Documents/Code/agent-skills/tools'
    retired='export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")'
    printf '%s\n%s\n%s\n%s\n' '# my shell' "$mine" "$other" "$retired" >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    out="$(oh_run_client "$tmp")"
    gone="$(grep -cF -- "$retired" "$tmp/.bashrc" || true)"
    gone_z="$(grep -cF -- "$retired" "$tmp/.zshrc" || true)"
    keptmine="$(grep -cF -- "$mine" "$tmp/.bashrc" || true)"
    keptother="$(grep -cF -- "$other" "$tmp/.bashrc" || true)"
    keptshell="$(grep -cF -- '# my shell' "$tmp/.bashrc" || true)"
    backups="$(ls "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | wc -l)"
    # Every edit takes its own backup, so several exist and each is a snapshot of
    # a different moment. The oldest (the names sort chronologically) is the file
    # as AutoOS found it: it must still hold the retired line and must not hold
    # the line AutoOS added — that is the proof the backup precedes the write.
    first_backup="$(ls -1 "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | sort | head -1)"
    if [[ -n "$first_backup" ]]; then
        backup_holds="$(grep -cF -- "$retired" "$first_backup" || true)"
        backup_added="$(grep -c 'AutoOS:omnigraph-env' "$first_backup" || true)"
    else
        backup_holds=0; backup_added=-1
    fi
    first_state="$(md5sum <"$tmp/.bashrc")"
    second="$(oh_run_client "$tmp")"
    second_state="$(md5sum <"$tmp/.bashrc")"
    ourlines="$(grep -cF -- "$(omnigraph_rc_marker)" "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$gone" == 0 && "$gone_z" == 0 ]] || { ok=0; echo "the retired line survived (bashrc $gone, zshrc $gone_z)" >&2; }
    [[ "$keptmine" == 1 && "$keptother" == 1 && "$keptshell" == 1 ]] \
        || { ok=0; echo "an unrecognised line was removed ($keptmine/$keptother/$keptshell)" >&2; }
    (( backups >= 1 )) || { ok=0; echo "the rc file was edited with no backup" >&2; }
    [[ "$backup_holds" == 1 ]] || { ok=0; echo "the oldest backup does not hold the retired line ($backup_holds)" >&2; }
    [[ "$backup_added" == 0 ]] || { ok=0; echo "the oldest backup already carries an AutoOS line ($backup_added): it is not the pre-edit file" >&2; }
    [[ "$out" == *"agent-skills"* ]] || { ok=0; echo "the removal was not announced" >&2; }
    [[ "$second" == *"CHANGED 0"* ]] || { ok=0; echo "the second run changed something again" >&2; }
    [[ "$second_state" == "$first_state" ]] || { ok=0; echo "the second run rewrote the rc file" >&2; }
    [[ "$ourlines" == 1 ]] || { ok=0; echo "the current rc line appears $ourlines times" >&2; }
    (( ok )) && pass || fail "the recognised-only rc removal is wrong"
fi

# ─── the skip gate: what install_component asks before the step ever runs ───

if it "omnigraph-client: a rotated token opens the skip gate and the next run lands it"; then
    tmp="$(oh_client_sandbox)"
    first="$(oh_run_client "$tmp" "" first-token-1111)"
    gate_same="$(oh_gate "$tmp" first-token-1111)"
    gate_rot="$(oh_gate "$tmp" rotated-token-2222)"
    out="$(oh_run_client "$tmp" "" rotated-token-2222)"
    tok_lines="$(grep -c '^OMNIGRAPH_TOKEN=' "$tmp/.autoos-omnigraph.env")"
    has_new="$(grep -c '^OMNIGRAPH_TOKEN=rotated-token-2222$' "$tmp/.autoos-omnigraph.env")"
    has_old="$(grep -c 'first-token-1111' "$tmp/.autoos-omnigraph.env" || true)"
    gate_after="$(oh_gate "$tmp" rotated-token-2222)"
    again="$(oh_run_client "$tmp" "" rotated-token-2222)"
    backups="$(ls "$tmp"/.autoos-omnigraph.env.autoos-backup-* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    ok=1
    [[ "$first" == *"CHANGED 1"* ]] || { ok=0; echo "a fresh machine wrote nothing: [${first:0:300}]" >&2; }
    [[ "$gate_same" == current ]] || { ok=0; echo "an unchanged machine does not read as current (gate [$gate_same])" >&2; }
    [[ "$gate_rot" == open ]] \
        || { ok=0; echo "the gate called the machine current after the token rotated - the rotation would never be written" >&2; }
    [[ "$out" == *"CHANGED 1"* ]] || { ok=0; echo "the rotating run changed nothing: [${out:0:400}]" >&2; }
    [[ "$tok_lines" == 1 && "$has_new" == 1 && "$has_old" == 0 ]] \
        || { ok=0; echo "the env file holds lines=$tok_lines new=$has_new old=$has_old" >&2; }
    (( backups >= 1 )) || { ok=0; echo "the rewritten secret file had no backup" >&2; }
    [[ "$gate_after" == current ]] || { ok=0; echo "after the rotation the gate is still open ([$gate_after])" >&2; }
    [[ "$again" == *"CHANGED 0"* ]] || { ok=0; echo "the run after the rotation changed the disk again" >&2; }
    [[ "$out" != *rotated-token-2222* && "$out" != *first-token-1111* ]] \
        || { ok=0; echo "a token value was printed" >&2; }
    (( ok )) && pass || fail "the token rotation is not visible to the skip gate"
fi

# The backup taken before the env file is rewritten holds the OLD token, which is
# still a live bearer credential on a server that has not expired it. shutil.copy2
# creates the destination with open(dst, "wb") — mode 0666 & ~umask, i.e. 0644 on
# any normal machine — and only tightens it to the source's mode AFTER the bytes
# landed. On a shared or NFS home another local user can read the token inside
# that window. So the backup must be created 0600 by the same syscall that makes
# it, and never widened afterwards (A3 final review, S1).
#
# The probe records the mode of every backup at the moment it is created, hooking
# both creation paths (builtins.open and os.open), so the assertion does not
# depend on which copy mechanism the writer uses — only on the mode the file was
# born with. 32-answer-file-templates.sh uses the same sitecustomize-shadow idiom.
if it "omnigraph-client: the env-file backup is created 0600 — the old token is never group/world readable"; then
    tmp="$(oh_client_sandbox)"
    env_file="$tmp/.autoos-omnigraph.env"
    mkdir -p "$tmp/shadow"
    cat >"$tmp/shadow/sitecustomize.py" <<'EOS'
import builtins, os

PROBE = os.environ.get("AUTOOS_MODE_PROBE", "")
_real_open = builtins.open
_real_os_open = os.open

def _is_backup(path):
    if not PROBE:
        return False
    try:
        name = os.fsdecode(path)
    except (TypeError, ValueError):
        return False
    return ".autoos-backup-" in name

def _note(path, mode):
    with _real_open(PROBE, "a", encoding="utf-8") as out:
        out.write("%o %s\n" % (mode, os.fsdecode(path)))

def probed_open(file, *args, **kw):
    f = _real_open(file, *args, **kw)
    if _is_backup(file):
        try:
            _note(file, os.fstat(f.fileno()).st_mode & 0o777)
        except OSError:
            pass
    return f

def probed_os_open(path, flags, mode=0o777, *args, **kw):
    fd = _real_os_open(path, flags, mode, *args, **kw)
    if _is_backup(path):
        try:
            _note(path, os.fstat(fd).st_mode & 0o777)
        except OSError:
            pass
    return fd

builtins.open = probed_open
os.open = probed_os_open
EOS
    (
        # The loosest umask a real home has — the one that makes a default-mode
        # creation world-readable.
        umask 022
        omnigraph_env_state "$env_file" "https://graph.example.invalid" first-token-1111 write >/dev/null 2>&1 \
            || printf 'FIRST WRITE rc=%s\n' "$?" >&2
        stat -c %Y "$env_file" >"$tmp/src_mtime"
        export PYTHONPATH="$tmp/shadow" AUTOOS_MODE_PROBE="$tmp/probe.txt"
        omnigraph_env_state "$env_file" "https://graph.example.invalid" rotated-2222 write >/dev/null 2>&1 \
            || printf 'ROTATING WRITE rc=%s\n' "$?" >&2
    )
    probe="$(cat "$tmp/probe.txt" 2>/dev/null || true)"
    loose="$(python3 -c '
import sys
loose = [l.split(None, 1)[1].strip() for l in sys.stdin
         if len(l.split(None, 1)) == 2 and int(l.split()[0], 8) & 0o077]
print(len(loose))
' <<<"$probe")"
    backups="$(ls -1 "$env_file".autoos-backup-* 2>/dev/null | wc -l)"
    backup="$(ls -1 "$env_file".autoos-backup-* 2>/dev/null | head -1)"
    backup_mode="$(stat -c '%a' "$backup" 2>/dev/null || printf 'none')"
    backup_old="$(grep -c '^OMNIGRAPH_TOKEN=first-token-1111$' "$backup" 2>/dev/null || true)"
    backup_new="$(grep -c 'rotated-2222' "$backup" 2>/dev/null || true)"
    backup_mtime="$(stat -c '%Y' "$backup" 2>/dev/null || printf 'none')"
    src_mtime="$(cat "$tmp/src_mtime" 2>/dev/null || printf 'none')"
    live_mode="$(stat -c '%a' "$env_file" 2>/dev/null || printf 'none')"
    live_new="$(grep -c '^OMNIGRAPH_TOKEN=rotated-2222$' "$env_file" 2>/dev/null || true)"
    # And the shape of the code, so a future rewrite that dodges both probed
    # creation paths (a raw os.write on an fd, say) cannot pass by accident. The
    # exclusive 0600 creation lives in the shared helper the writer imports -
    # backup_file_before_write runs the very same file (A3 review 4) - so the
    # shape is asserted across both homes, not on one of them.
    writer="$(sed -n '/^omnigraph_env_state()/,/^PY$/p' lib/linux/install.sh)"
    helper="$(cat lib/linux/secret_backup.py 2>/dev/null || printf '')"
    copy2_gone=1
    grep -q 'shutil\.copy2(' <<<"$writer" && copy2_gone=0
    excl=1
    grep -q 'os\.O_EXCL' <<<"$writer$helper" || excl=0
    imports_helper=1
    grep -q 'from secret_backup import secret_backup' <<<"$writer" || imports_helper=0
    rm -rf "$tmp"
    ok=1
    [[ -n "$probe" ]] || { ok=0; echo "the probe saw no backup being created - the assertion would be vacuous" >&2; }
    [[ "$loose" == 0 ]] || { ok=0; echo "$loose backup(s) were created with group/world bits while the old token was copied" >&2; }
    (( copy2_gone )) || { ok=0; echo "the env writer still backs up with shutil.copy2 (born at the default umask mode)" >&2; }
    (( excl )) || { ok=0; echo "nothing in the env writer's path creates its backup with O_EXCL at 0600" >&2; }
    (( imports_helper )) \
        || { ok=0; echo "the env writer re-implements the backup instead of using the one secret_backup.py helper" >&2; }
    [[ "$backups" == 1 ]] || { ok=0; echo "the rewritten secret file had $backups backups" >&2; }
    [[ "$backup_mode" == 600 ]] || { ok=0; echo "the backup ended up mode $backup_mode, not 600" >&2; }
    [[ "$live_mode" == 600 ]] || { ok=0; echo "the env file itself is mode $live_mode" >&2; }
    [[ "$backup_old" == 1 && "$backup_new" == 0 ]] \
        || { ok=0; echo "the backup holds old=$backup_old new=$backup_new - not the pre-edit bytes" >&2; }
    [[ "$backup_mtime" == "$src_mtime" ]] \
        || { ok=0; echo "the backup lost the source's times ($backup_mtime vs $src_mtime)" >&2; }
    [[ "$live_new" == 1 ]] || { ok=0; echo "the rotation did not land in the env file" >&2; }
    (( ok )) && pass || fail "the token-bearing backup is not created 0600"
fi

# ─── S1: every backup of a token-bearing file is born private ───────────────
#
# The rc files AutoOS edits can hold a bearer token: the retired agent-skills
# line, a hand-written `export OMNIGRAPH_TOKEN=...`, or the line this component is
# about to replace. backup_file copies with `cp -p`, which PRESERVES the source's
# mode - so a 0644 dotfile produced a 0644 backup, and that backup keeps the
# token after the edit has taken the token out of the rc file itself. The one
# place the secret used to be readable by every local user is then the place it
# stays readable: the copy outlives the original by design (AGENTS.md rule 5).
# So the backup of a file that carries a credential is created 0600 in the same
# syscall that creates it (O_EXCL) and never widened (A3 review 4).
#
# Same rule as the env-file test above, one file over: the probe records the mode
# at creation, so a `cp -p` followed by a `chmod 600` - right at the end, wrong
# during the window - cannot pass.

if it "omnigraph-client: a backup of a token-bearing rc file is born 0600, and a plain file's backup keeps its mode"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/shadow"
    cat >"$tmp/shadow/sitecustomize.py" <<'EOS'
import builtins, os
PROBE = os.environ.get("AUTOOS_MODE_PROBE", "")
_real_open = builtins.open
_real_os_open = os.open
def _note(path, mode):
    with _real_open(PROBE, "a", encoding="utf-8") as out:
        out.write("%o %s\n" % (mode, os.fsdecode(path)))
def probed_open(file, *args, **kw):
    f = _real_open(file, *args, **kw)
    if PROBE and ".autoos-backup-" in os.fsdecode(file):
        try:
            _note(file, os.fstat(f.fileno()).st_mode & 0o777)
        except OSError:
            pass
    return f
def probed_os_open(path, flags, mode=0o777, *args, **kw):
    fd = _real_os_open(path, flags, mode, *args, **kw)
    if PROBE and ".autoos-backup-" in os.fsdecode(path):
        try:
            _note(path, os.fstat(fd).st_mode & 0o777)
        except OSError:
            pass
    return fd
builtins.open = probed_open
os.open = probed_os_open
EOS
    secret='export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")'
    printf '# my shell\n%s\n' "$secret" >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    chmod 644 "$tmp/.bashrc" "$tmp/.zshrc"
    (
        # The mode a normal home has, i.e. the one that makes a default-mode
        # copy world-readable.
        umask 022
        export PYTHONPATH="$tmp/shadow" AUTOOS_MODE_PROBE="$tmp/probe.txt"
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=() OMNIGRAPH_CLIENT_CHANGED=0
        omnigraph_retire_rc_token_lines
    ) >/dev/null 2>&1
    probe="$(cat "$tmp/probe.txt" 2>/dev/null || true)"
    rc_probe_lines="$(printf '%s\n' "$probe" | grep -c 'autoos-backup' || true)"
    loose_birth="$(python3 -c '
import sys
n = 0
for line in sys.stdin:
    parts = line.split(None, 1)
    if len(parts) == 2 and int(parts[0], 8) & 0o077:
        n += 1
print(n)
' <<<"$probe")"
    # Every backup that still holds a token line must be private, whichever step
    # made it and whatever the rc file's own mode is now.
    loose="$(python3 -c '
import glob, os, re, sys
pat = re.compile(rb"^\s*(?:export\s+)?OMNIGRAPH_TOKEN=\S", re.M)
loose = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], ".*rc.autoos-backup-*"))):
    with open(f, "rb") as fh:
        holds = bool(pat.search(fh.read()))
    mode = os.stat(f).st_mode & 0o777
    if holds and mode & 0o077:
        loose.append("%s:%o" % (os.path.basename(f), mode))
print(" ".join(loose))
' "$tmp")"
    backed_up="$(ls -1 "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | wc -l)"
    oldest="$(ls -1 "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | sort | head -1)"
    oldest_holds="$( [[ -n "$oldest" ]] && grep -cF -- "$secret" "$oldest" || printf 0 )"
    gone="$(grep -cF -- "$secret" "$tmp/.bashrc" || true)"
    rc_mode="$(stat -c '%a' "$tmp/.bashrc")"
    # The other half of the rule: the private backup is the SECRET case only.
    # A file with nothing in it keeps the copy semantics backup_file has always
    # had - the user's own mode - because silently tightening every dotfile
    # backup is a different change with a different blast radius.
    printf '# my shell\nexport EDITOR=vim\n' >"$tmp/plain.sh"
    plain_own="$(stat -c '%a' "$tmp/plain.sh")"
    plain_backup="$( (umask 022; backup_file_before_write "$tmp/plain.sh") 2>/dev/null )"
    plain_mode="$(stat -c '%a' "$plain_backup" 2>/dev/null || printf none)"
    rm -rf "$tmp"
    ok=1
    (( backed_up >= 1 )) || { ok=0; echo "the retire step edited the rc file with no backup" >&2; }
    [[ "$oldest_holds" == 1 ]] || { ok=0; echo "the oldest backup does not hold the line that was removed" >&2; }
    [[ "$gone" == 0 ]] || { ok=0; echo "the retired line survived ($gone)" >&2; }
    [[ "$rc_mode" == 644 ]] || { ok=0; echo "the user's rc file itself was re-modeled to $rc_mode" >&2; }
    (( rc_probe_lines >= 1 )) \
        || { ok=0; echo "the probe saw no rc backup being created - the assertion would be vacuous" >&2; }
    [[ "$loose_birth" == 0 ]] || { ok=0; echo "$loose_birth backup(s) were born group/world readable" >&2; }
    [[ -z "$loose" ]] || { ok=0; echo "token-bearing backup(s) readable by others: $loose" >&2; }
    [[ "$plain_mode" == "$plain_own" ]] \
        || { ok=0; echo "a tokenless file's backup came out $plain_mode, not its own mode $plain_own" >&2; }
    (( ok )) && pass || fail "the token-bearing rc backup is not created 0600"
fi

# ─── LOW 1 (A3 review 5): the gate must ask the readers' question ────────────
#
# file_holds_omnigraph_token decides whether a backup is a secret's copy or an
# ordinary one. It required a non-space character *immediately* after the `=`,
# while every reader of these files trims whitespace around the value before
# deciding it is a token: tools/omnigraph-mcp-autoos.sh strips the leading and
# trailing whitespace (and one layer of matching quotes) round what follows the
# `=`, the rc line install.sh writes does the same, and `has_token` in
# omnigraph_env_state compares the stripped line. So a token typed as
# `OMNIGRAPH_TOKEN=   secret` - or with a tab after `export`, or indented - was
# live to the wrapper and invisible to the gate, and that file took the `cp -p`
# branch: a 0644 backup that outlives the edit which removes the line.
#
# The rule has one home, so the test is a comparison rather than a list: for each
# form the SHIPPED wrapper parses the file and reports the value it resolved, and
# the gate must agree with it - and the backup must be born 0600 exactly in the
# cases the wrapper calls live. Running the wrapper (not a re-typed copy of its
# loop) is what keeps the two definitions from drifting again unnoticed.
#
# One deliberate asymmetry: the gate does not strip quotes, so a value written as
# `""` counts as a token to it and not to the wrapper. That direction only ever
# makes the copy private; tightening it to match would re-open the leak.
oh_wrapper_value() {  # the OMNIGRAPH_TOKEN the shipped wrapper resolves from <file>
    local file="$1" home out stub_dir
    home="$(mktemp -d)"
    stub_dir="$home/.local/share/autoos/omnigraph-mcp/bin"
    mkdir -p "$stub_dir"
    # The wrapper execs the bridge at the end; the stub prints what the wrapper
    # handed it, so this observes production's own parse path.
    printf '#!/bin/sh\nprintf "%%s\\n" "${OMNIGRAPH_TOKEN-<unset>}"\n' >"$stub_dir/omnigraph-mcp"
    chmod 755 "$stub_dir/omnigraph-mcp"
    cp -- "$file" "$home/.autoos-omnigraph.env"
    out="$(env -u OMNIGRAPH_TOKEN HOME="$home" bash tools/omnigraph-mcp-autoos.sh 2>/dev/null)"
    rm -rf "$home"
    printf '%s\n' "$out"
}

if it "omnigraph-client: the secret gate trims the value the way the readers do"; then
    tmp="$(mktemp -d)"
    ok=1
    oh5_live=(
        'OMNIGRAPH_TOKEN=   secret-token-1111'
        'export OMNIGRAPH_TOKEN= secret-token-1111'
        '  OMNIGRAPH_TOKEN=secret-token-1111'
        $'\texport\tOMNIGRAPH_TOKEN= \tsecret-token-1111'
        'OMNIGRAPH_TOKEN="secret-token-1111"'
    )
    oh5_quiet=(
        'OMNIGRAPH_TOKEN='
        'export OMNIGRAPH_TOKEN=   '
        $'  OMNIGRAPH_TOKEN=\t'
        'OMNIGRAPH_TOKEN = secret-token-1111'
        'OMNIGRAPH_TOKENX=secret-token-1111'
    )
    oh5_n=0
    for form in "${oh5_live[@]}"; do
        oh5_n=$((oh5_n + 1))
        oh5_f="$tmp/live-$oh5_n"
        printf '# my shell\n%s\n' "$form" >"$oh5_f"
        chmod 644 "$oh5_f"
        oh5_verdict="$(file_holds_omnigraph_token "$oh5_f" && printf yes || printf no)"
        oh5_reader="$(oh_wrapper_value "$oh5_f")"
        oh5_backup="$( (umask 022; backup_file_before_write "$oh5_f" 2>/dev/null) || true )"
        oh5_mode="$(stat -c '%a' "$oh5_backup" 2>/dev/null || printf none)"
        [[ "$oh5_verdict" == yes ]] || { ok=0; echo "the gate missed a live token: [$form]" >&2; }
        [[ "$oh5_reader" == secret-token-1111 ]] \
            || { ok=0; echo "the wrapper itself resolved [$oh5_reader] from [$form]" >&2; }
        [[ "$oh5_mode" == 600 ]] || { ok=0; echo "a live token's backup came out $oh5_mode: [$form]" >&2; }
    done
    for form in "${oh5_quiet[@]}"; do
        oh5_n=$((oh5_n + 1))
        oh5_f="$tmp/quiet-$oh5_n"
        printf '# my shell\n%s\n' "$form" >"$oh5_f"
        chmod 644 "$oh5_f"
        oh5_verdict="$(file_holds_omnigraph_token "$oh5_f" && printf yes || printf no)"
        oh5_reader="$(oh_wrapper_value "$oh5_f")"
        oh5_backup="$( (umask 022; backup_file_before_write "$oh5_f" 2>/dev/null) || true )"
        oh5_mode="$(stat -c '%a' "$oh5_backup" 2>/dev/null || printf none)"
        [[ "$oh5_reader" == '<unset>' ]] || { ok=0; echo "the wrapper resolved a token from [$form]" >&2; }
        [[ "$oh5_verdict" == no ]] || { ok=0; echo "the gate called [$form] a secret (it would re-mode a plain file)" >&2; }
        [[ "$oh5_mode" == 644 ]] || { ok=0; echo "a tokenless file's backup came out $oh5_mode, not its own 644: [$form]" >&2; }
    done
    rm -rf "$tmp"
    (( ok )) && pass || fail "the secret gate and the readers disagree about what a token is"
fi

# The rule belongs to the backup, not to the step that happens to notice it: on a
# first run the rc file is opened for writing by append_line_once (the current
# line is missing), which is BEFORE the retire step ever runs. Testing every
# branch of the marked-line writer is what keeps a later caller from re-adding
# the leak by picking backup_file again.
if it "omnigraph-client: every rc branch backs up a token-bearing file privately before it writes"; then
    tmp="$(mktemp -d)"
    marker="$(omnigraph_rc_marker)"
    token_line='export OMNIGRAPH_TOKEN=inline-token-9999'
    stale_line='line  # AutoOS:omnigraph-env'
    # append: neither marker present. purge: current line plus a stale old one.
    # replace: only the old marker line.
    printf '%s\n' "$token_line" >"$tmp/append"
    printf '%s\n%s\nkeep  # %s\n' "$token_line" "$stale_line" "$marker" >"$tmp/purge"
    printf '%s\n%s\n' "$token_line" "$stale_line" >"$tmp/replace"
    for f in append purge replace; do chmod 644 "$tmp/$f"; done
    (
        umask 022
        SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
        append_line_once "$tmp/append" "$marker" "appended  # $marker"
        replace_or_append_marked_line "$tmp/purge" "AutoOS:omnigraph-env" "$marker" "fresh  # $marker"
        replace_or_append_marked_line "$tmp/replace" "AutoOS:omnigraph-env" "$marker" "fresh  # $marker"
    ) >/dev/null 2>&1
    readable="$(python3 -c '
import glob, os, re, sys
pat = re.compile(rb"^\s*(?:export\s+)?OMNIGRAPH_TOKEN=\S", re.M)
bad = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.autoos-backup-*"))):
    with open(f, "rb") as fh:
        holds = bool(pat.search(fh.read()))
    if holds and os.stat(f).st_mode & 0o077:
        bad.append("%s:%o" % (os.path.basename(f), os.stat(f).st_mode & 0o777))
print(" ".join(bad))
' "$tmp")"
    count="$(ls -1 "$tmp"/*.autoos-backup-* 2>/dev/null | wc -l)"
    # Each branch has to have actually written, or a branch that never ran would
    # read as "no backup needed" instead of as the defect it is.
    appended="$(grep -cF -- "appended  # $marker" "$tmp/append" || true)"
    stale_left="$(grep -cF -- "$stale_line" "$tmp/purge" "$tmp/replace" | grep -c ':0' || true)"
    keep_line="$(grep -cF -- "keep  # $marker" "$tmp/purge" || true)"
    fresh="$(grep -cF -- "fresh  # $marker" "$tmp/purge" "$tmp/replace" | tr '\n' ' ')"
    rm -rf "$tmp"
    ok=1
    (( count >= 3 )) || { ok=0; echo "the three branches made $count backup(s) - one of them wrote without backing up" >&2; }
    [[ -z "$readable" ]] || { ok=0; echo "token-bearing backup(s) readable by others: $readable" >&2; }
    [[ "$appended" == 1 ]] || { ok=0; echo "the append branch did not write ($appended current line)" >&2; }
    [[ "$stale_left" == 2 ]] || { ok=0; echo "the stale marker line survived a branch ($stale_left of 2 files clean)" >&2; }
    [[ "$keep_line" == 1 ]] || { ok=0; echo "the purge took an unrelated line with it ($keep_line)" >&2; }
    [[ "$fresh" == *":1"* ]] || { ok=0; echo "no replacement line was written: [$fresh]" >&2; }
    (( ok )) && pass || fail "one of the rc edit branches still leaks the token into a 0644 backup"
fi

# The helper's own contract, pinned: the stamp argument (the suite pins stamps
# everywhere else), the -N suffix when a name is taken, the family's name shape,
# and nothing left behind when the copy cannot be made.
if it "omnigraph-client: backup_file_before_write keeps the backup_file naming and failure contract"; then
    tmp="$(mktemp -d)"
    printf 'export OMNIGRAPH_TOKEN=inline-token-9999\n' >"$tmp/rc"
    # 0644 like a normal dotfile: cp -p would hand that mode straight to the
    # backup, which is the defect. The token inside it must not be readable.
    chmod 644 "$tmp/rc"
    out="$( (
        umask 022
        first="$(backup_file_before_write "$tmp/rc" 20200101-000000)"
        second="$(backup_file_before_write "$tmp/rc" 20200101-000000)"
        third="$(backup_file_before_write "$tmp/rc" 20200101-000000)"
        printf '%s\n%s\n%s\n' "$first" "$second" "$third"
    ) 2>/dev/null )"
    names="$(printf '%s\n' "$out" | grep -c "^$tmp/rc\.autoos-backup-20200101-000000" || true)"
    suffixes="$(printf '%s\n' "$out" | grep -cE '^.*autoos-backup-20200101-000000(-[0-9]+)?$' || true)"
    modes="$( (umask 022; for f in "$tmp"/rc.autoos-backup-*; do stat -c '%a' "$f"; done) | sort -u | tr '\n' ' ' )"
    body="$(sed -n '/^backup_file_before_write()/,/^}/p' lib/linux/install.sh)"
    # A source that cannot be read must leave nothing behind. Root reads a 0600
    # file regardless of the mode, so the seed only means something to a normal
    # user (the same guard the read-only-rc case above uses).
    if [[ "$(id -u)" == 0 ]]; then
        strays=0; refused=READ-REFUSED
        rm -rf "$tmp"
    else
        printf 'export OMNIGRAPH_TOKEN=inline-token-9999\n' >"$tmp/noread"
        chmod 000 "$tmp/noread"
        refused="$( (umask 022; backup_file_before_write "$tmp/noread" >/dev/null 2>&1 \
                        && printf 'READ-OK\n' || printf 'READ-REFUSED\n') 2>/dev/null )"
        strays="$(ls -1 "$tmp"/noread.autoos-backup-* 2>/dev/null | wc -l)"
        chmod 600 "$tmp/noread"
        rm -rf "$tmp"
    fi
    ok=1
    [[ "$names" == 3 && "$suffixes" == 3 ]] \
        || { ok=0; echo "the three calls did not get three distinct names in the family's shape: [${out:0:200}]" >&2; }
    [[ "$modes" == "600 " ]] || { ok=0; echo "a token file's backups came out [$modes], not all 600" >&2; }
    [[ "$refused" == READ-REFUSED ]] || { ok=0; echo "an unreadable file was 'backed up' anyway" >&2; }
    [[ "$strays" == 0 ]] || { ok=0; echo "a backup that could not be read was left behind ($strays partial files)" >&2; }
    [[ -n "$body" ]] || { ok=0; echo "backup_file_before_write is not defined in lib/linux/install.sh" >&2; }
    (( ok )) && pass || fail "the private backup helper does not keep the backup_file contract"
fi

# The CLI documents the stamp as optional - `usage: secret_backup.py <path>
# [stamp]` - and install.sh's own call site passes it as a possibly-empty
# argument, so a caller that leaves it off is the documented case, not a misuse.
# Reading argv[2] unguarded turned it into an IndexError traceback on stderr and
# exit 1: the shell's `|| rc=$?` then reports "could not back up" and the rc-file
# edit refuses to run at all (A3 review 5, LOW 2).
if it "omnigraph-client: secret_backup.py takes the stamp as the optional argument it documents"; then
    tmp="$(mktemp -d)"
    printf 'export OMNIGRAPH_TOKEN=inline-token-9999\n' >"$tmp/rc"
    chmod 644 "$tmp/rc"
    ok=1
    # One argument: the documented optional-stamp call.
    oh5_out="$( (umask 022; python3 lib/linux/secret_backup.py "$tmp/rc" 2>"$tmp/err1") )" && oh5_rc=0 || oh5_rc=$?
    oh5_err="$(cat "$tmp/err1")"
    [[ "$oh5_rc" == 0 ]] || { ok=0; echo "one-arg call exited $oh5_rc (traceback: ${oh5_err:0:120})" >&2; }
    [[ "$oh5_err" != *Traceback* ]] || { ok=0; echo "one-arg call printed a traceback" >&2; }
    oh5_stamp="${oh5_out#"$tmp/rc.autoos-backup-"}"
    [[ "$oh5_out" == "$tmp/rc.autoos-backup-"* && "$oh5_stamp" =~ ^[0-9]{8}-[0-9]{6}$ ]] \
        || { ok=0; echo "one-arg call named [$oh5_out] - not the family's shape with a real stamp" >&2; }
    oh5_mode="$(stat -c '%a' "$oh5_out" 2>/dev/null || printf none)"
    [[ "$oh5_mode" == 600 ]] || { ok=0; echo "the one-arg backup came out $oh5_mode, not 600" >&2; }
    cmp -s "$tmp/rc" "$oh5_out" || { ok=0; echo "the one-arg backup is not the source's bytes" >&2; }
    # The empty stamp install.sh passes when its own caller omitted it.
    oh5_out2="$( (umask 022; python3 lib/linux/secret_backup.py "$tmp/rc" "" 2>/dev/null) )" && oh5_rc2=0 || oh5_rc2=$?
    [[ "$oh5_rc2" == 0 ]] || { ok=0; echo "empty-stamp call exited $oh5_rc2" >&2; }
    [[ -f "$oh5_out2" ]] || { ok=0; echo "empty-stamp call created nothing" >&2; }
    oh5_mode2="$(stat -c '%a' "$oh5_out2" 2>/dev/null || printf none)"
    [[ "$oh5_mode2" == 600 ]] || { ok=0; echo "the empty-stamp backup came out $oh5_mode2" >&2; }
    # And the same contract through the shell helper, whose stamp is optional too.
    oh5_out3="$( (umask 022; backup_file_before_write "$tmp/rc" 2>/dev/null) || true )"
    oh5_mode3="$(stat -c '%a' "$oh5_out3" 2>/dev/null || printf none)"
    [[ -f "$oh5_out3" && "$oh5_mode3" == 600 ]] \
        || { ok=0; echo "backup_file_before_write with no stamp gave [$oh5_out3] ($oh5_mode3)" >&2; }
    # Too many arguments is still the usage error it was.
    python3 lib/linux/secret_backup.py "$tmp/rc" 20200101-000000 extra >/dev/null 2>&1 \
        && { ok=0; echo "a three-argument call was accepted" >&2; }
    oh5_count="$(ls -1 "$tmp"/rc.autoos-backup-* 2>/dev/null | wc -l)"
    (( oh5_count >= 3 )) || { ok=0; echo "only $oh5_count backup(s) exist - one call created nothing" >&2; }
    rm -rf "$tmp"
    (( ok )) && pass || fail "secret_backup.py does not handle the omitted stamp"
fi

# ─── S2: the env rewrite writes through a temp nobody else can name ─────────
#
# The rewrite staged the new bytes at a FIXED, guessable name - `<path>.tmp`,
# opened O_CREAT|O_TRUNC. Anyone who can write in the home (another user on a
# shared or NFS home, a component that ran earlier, an attacker that only needs
# to win a same-second race against two AutoOS runs) could plant a symlink there
# and turn "update my token" into "truncate and overwrite the file I chose" - with
# the token's bytes. The temp must be created unpredictably in the same directory
# (so os.replace stays atomic), 0600 by the same syscall that makes it, and
# removed if anything in between fails. lib/linux/serve.py's write_secret and the
# Claude Code settings writer already do exactly this (A3 review 4, S2).
if it "omnigraph-client: the env rewrite stages in its own temp and never follows a planted link"; then
    tmp="$(mktemp -d)"
    env_file="$tmp/.autoos-omnigraph.env"
    victim="$tmp/victim.txt"
    printf 'DO NOT TOUCH\n' >"$victim"
    (
        umask 022
        omnigraph_env_state "$env_file" "https://graph.example.invalid" first-token-1111 write >/dev/null 2>&1
    )
    ln -s "$victim" "$env_file.tmp" 2>/dev/null
    if [[ ! -L "$env_file.tmp" ]]; then
        rm -rf "$tmp"
        skip "the host refuses symlinks"
    else
        (
            umask 022
            omnigraph_env_state "$env_file" "https://graph.example.invalid" rotated-2222 write >/dev/null 2>&1
        )
        link_still="$( [[ -L "$env_file.tmp" ]] && printf yes || printf no )"
        target="$(readlink "$env_file.tmp" 2>/dev/null || printf none)"
        victim_bytes="$(cmp -s "$victim" <(printf 'DO NOT TOUCH\n') && printf same || printf changed)"
        live="$(grep -c '^OMNIGRAPH_TOKEN=rotated-2222$' "$env_file" 2>/dev/null || true)"
        live_mode="$(stat -c '%a' "$env_file" 2>/dev/null || printf none)"
        # The token file must still be a file of ours, not a link the planted name
        # turned it into - that is what `os.replace` of a hijacked temp would do.
        live_kind="$( [[ -L "$env_file" ]] && printf link \
            || { [[ -f "$env_file" ]] && printf file || printf other; } )"
        # Nothing temp-shaped may survive the write: neither the fixed `<name>.tmp`
        # (only the link this test planted sits at that name) nor an mkstemp name
        # the writer failed to clean up after itself.
        strays=()
        for f in "$tmp"/.*.tmp "$tmp"/.*autoos-tmp* "$tmp"/*.tmp "$tmp"/*autoos-tmp*; do
            [[ -e "$f" || -L "$f" ]] || continue
            [[ "$f" == "${env_file}.tmp" ]] && continue
            strays+=("${f##*/}")
        done
        stray="${strays[*]-}"
        # And the shape, so a future edit cannot reintroduce a fixed name that
        # the probe would still catch only when somebody happens to plant a link.
        writer="$(sed -n '/^omnigraph_env_state()/,/^PY$/p' lib/linux/install.sh)"
        fixed_name=1
        grep -qE 'path \+ "\.tmp"' <<<"$writer" && fixed_name=0
        mkstemp=1
        grep -q 'tempfile\.mkstemp' <<<"$writer" || mkstemp=0
        fsync=1
        grep -q 'os\.fsync' <<<"$writer" || fsync=0
        rm -rf "$tmp"
        ok=1
        [[ "$link_still" == yes && "$target" == "$tmp/victim.txt" ]] \
            || { ok=0; echo "AutoOS replaced the planted link at its own temp name (link=$link_still target=$target)" >&2; }
        [[ "$victim_bytes" == same ]] || { ok=0; echo "the rewrite went through the planted link into the victim file" >&2; }
        [[ "$live" == 1 ]] || { ok=0; echo "the rotation did not land in the env file ($live)" >&2; }
        [[ "$live_mode" == 600 ]] || { ok=0; echo "the env file is mode $live_mode" >&2; }
        [[ -z "$stray" ]] || { ok=0; echo "a temp file was left behind: $stray" >&2; }
        (( fixed_name )) || { ok=0; echo "the env writer stages the new bytes at a fixed '<path>.tmp' name again" >&2; }
        (( mkstemp )) || { ok=0; echo "the env writer does not create its temp with mkstemp (O_EXCL, unpredictable name)" >&2; }
        (( fsync )) || { ok=0; echo "the env writer does not fsync the temp before renaming it onto the token file" >&2; }
        (( ok )) && pass || fail "the env rewrite is not atomic-and-private in its temp file"
    fi
fi

# rc is how every step here reports "did the write work". A branch that assigns it
# without `local` overwrites the CALLER's rc, so a caller that had recorded its own
# failure code reads the marked-line step's value instead. The purge branch above
# declares `local rc=0`; the replace branch below must too.
if it "omnigraph-client: the marked-line writer keeps rc local in the replace branch too"; then
    tmp="$(mktemp -d)"
    marker="$(omnigraph_rc_marker)"
    printf '# my shell\nexport OMNIGRAPH_TOKEN=$(cat "$HOME/.old-token-file")\n' >"$tmp/.bashrc"
    out="$( (
        AUTOOS_DRY_RUN=0
        rc=sentinel-value
        replace_or_append_marked_line "$tmp/.bashrc" '.old-token-file' "$marker" \
            "test -r \"\$HOME/.autoos-omnigraph.env\" && . \"\$HOME/.autoos-omnigraph.env\"  # $marker"
        printf 'RC %s\n' "$rc"
    ) 2>&1)"
    body="$(sed -n '/^replace_or_append_marked_line()/,/^}/p' lib/linux/install.sh)"
    bare="$(printf '%s\n' "$body" | grep -cE '^[[:space:]]*rc=' || true)"
    ours="$(grep -cF -- "$marker" "$tmp/.bashrc" || true)"
    stale="$(grep -c '.old-token-file' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$out" == *"RC sentinel-value"* ]] \
        || { ok=0; echo "the step overwrote the caller's rc: [${out##*RC }]" >&2; }
    [[ "$bare" == 0 ]] || { ok=0; echo "$bare rc assignment(s) in the function are not declared local" >&2; }
    [[ "$ours" == 1 && "$stale" == 0 ]] || { ok=0; echo "the replace branch did not do its job ($ours/$stale)" >&2; }
    (( ok )) && pass || fail "the marked-line writer leaks rc into its caller"
fi

if it "omnigraph-client: a token rotated in api-keys.yml opens the gate; the same token keeps it closed"; then
    tmp="$(oh_client_sandbox)"
    printf 'omnigraph_token: keys-first\n' >"$tmp/keys.yml"
    oh_run_client "$tmp" "" - >/dev/null
    gate_same="$(oh_gate "$tmp" -)"
    printf 'omnigraph_token: keys-rotated\n' >"$tmp/keys.yml"
    gate_rot="$(oh_gate "$tmp" -)"
    out="$(oh_run_client "$tmp" "" -)"
    tok="$(grep -c '^OMNIGRAPH_TOKEN=keys-rotated$' "$tmp/.autoos-omnigraph.env")"
    stale="$(grep -c 'keys-first' "$tmp/.autoos-omnigraph.env" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$gate_same" == current ]] || { ok=0; echo "the keys-file token does not satisfy the gate ([$gate_same])" >&2; }
    [[ "$gate_rot" == open ]] || { ok=0; echo "a rotated keys-file token is invisible to the gate" >&2; }
    [[ "$out" == *"CHANGED 1"* ]] || { ok=0; echo "the rotating run wrote nothing: [${out:0:300}]" >&2; }
    [[ "$tok" == 1 && "$stale" == 0 ]] || { ok=0; echo "env rows new=$tok stale=$stale" >&2; }
    [[ "$out" != *keys-rotated* ]] || { ok=0; echo "the keys-file token was printed" >&2; }
    (( ok )) && pass || fail "the api-keys.yml rotation is wrong"
fi

if it "omnigraph-client: a commented or longer-suffix URL line is not current"; then
    tmp="$(oh_client_sandbox)"
    oh_run_client "$tmp" >/dev/null
    # Two shapes the old unanchored substring grep matched as "already the
    # answer's URL": a comment naming it, and a URL that merely starts with it.
    for shape in comment suffix; do
        if [[ "$shape" == comment ]]; then
            bad='# OMNIGRAPH_BASE_URL=https://graph.example.invalid'
            live='OMNIGRAPH_BASE_URL=https://somewhere-else.example'
        else
            bad='OMNIGRAPH_BASE_URL=https://graph.example.invalid.evil'
            live="$bad"
        fi
        python3 - "$tmp/.autoos-omnigraph.env" "$bad" "$live" <<'PY'
import sys
path, bad, live = sys.argv[1:4]
lines = open(path, encoding="utf-8").read().splitlines()
out = [bad if l.startswith("OMNIGRAPH_BASE_URL=") else l for l in lines]
out.append(live)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
        gate="$(oh_gate "$tmp")"
        out_run="$(oh_run_client "$tmp")"
        base_ok="$(grep -c '^OMNIGRAPH_BASE_URL=https://graph.example.invalid$' "$tmp/.autoos-omnigraph.env")"
        evil="$(grep -c 'graph.example.invalid.evil\|somewhere-else' "$tmp/.autoos-omnigraph.env" || true)"
        ok=1
        [[ "$gate" == open ]] || { ok=0; echo "[$shape] the gate read a wrong URL line as current" >&2; }
        [[ "$out_run" == *"CHANGED 1"* ]] || { ok=0; echo "[$shape] the repair run wrote nothing" >&2; }
        [[ "$base_ok" == 1 ]] || { ok=0; echo "[$shape] the answer URL is not the live line ($base_ok)" >&2; }
        [[ "$evil" == 0 ]] || { ok=0; echo "[$shape] the wrong line survived ($evil rows)" >&2; }
        [[ "$ok" == 1 ]] || break
    done
    rm -rf "$tmp"
    (( ok )) && pass || fail "an unanchored URL match still passes for current"
fi

if it "omnigraph-client: a deleted rc line, a reappeared retired line and a lost environment.d link are repaired"; then
    tmp="$(oh_client_sandbox)"
    printf '# my shell\n' >"$tmp/.bashrc"
    oh_run_client "$tmp" >/dev/null
    gate_fresh="$(oh_gate "$tmp")"
    # (1) the user's shell cleanup deleted the source line.
    grep -v 'AutoOS:omnigraph-env' "$tmp/.bashrc" >"$tmp/.bashrc.new" && mv "$tmp/.bashrc.new" "$tmp/.bashrc"
    gate_rc="$(oh_gate "$tmp")"
    out_rc="$(oh_run_client "$tmp")"
    ours="$(grep -cF -- "$(omnigraph_rc_marker)" "$tmp/.bashrc")"
    # (2) a retired agent-skills token line came back (a restored dotfile).
    retired='export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")'
    printf '%s\n' "$retired" >>"$tmp/.bashrc"
    gate_retired="$(oh_gate "$tmp")"
    out_retired="$(oh_run_client "$tmp")"
    gone="$(grep -cF -- "$retired" "$tmp/.bashrc" || true)"
    # (3) the environment.d link was removed by a cleanup.
    rm -f "$tmp/.config/environment.d/60-autoos-omnigraph.conf"
    gate_link="$(oh_gate "$tmp")"
    out_link="$(oh_run_client "$tmp")"
    link="$(readlink "$tmp/.config/environment.d/60-autoos-omnigraph.conf" 2>/dev/null || echo none)"
    gate_after="$(oh_gate "$tmp")"
    rm -rf "$tmp"
    ok=1
    [[ "$gate_fresh" == current ]] \
        || { ok=0; echo "a machine the run just configured is not current ([$gate_fresh])" >&2; }
    [[ "$gate_rc" == open ]] || { ok=0; echo "a deleted rc line is invisible to the gate" >&2; }
    [[ "$out_rc" == *"CHANGED 1"* && "$ours" == 1 ]] || { ok=0; echo "the rc line was not re-added ($ours)" >&2; }
    [[ "$gate_retired" == open ]] || { ok=0; echo "a reappeared retired line is invisible to the gate" >&2; }
    [[ "$out_retired" == *"CHANGED 1"* && "$gone" == 0 ]] || { ok=0; echo "the reappeared retired line stayed ($gone)" >&2; }
    [[ "$gate_link" == open ]] || { ok=0; echo "a missing environment.d link is invisible to the gate" >&2; }
    [[ "$out_link" == *"CHANGED 1"* ]] || { ok=0; echo "the link run reported no change: [${out_link:0:300}]" >&2; }
    [[ "$link" == *".autoos-omnigraph.env" ]] || { ok=0; echo "the link was not restored ($link)" >&2; }
    [[ "$gate_after" == current ]] || { ok=0; echo "the repaired machine is still not current" >&2; }
    (( ok )) && pass || fail "the gate does not cover every artifact it claims"
fi

if it "omnigraph-client: the retire step keeps a CRLF rc file byte-for-byte except the line it removes"; then
    tmp="$(oh_client_sandbox)"
    l1='# my shell'
    l2='export MY_DIR="$HOME/Documents/code/agent-skills/tools"'   # agent-skills, but not a token line
    l3='export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")'
    l4='alias ll="ls -alF"'
    printf '%s\r\n%s\r\n%s\r\n%s\r\n' "$l1" "$l2" "$l3" "$l4" >"$tmp/.bashrc"
    printf '%s\r\n%s\r\n' "$l1" "$l2" >"$tmp/expected"
    out="$(oh_run_client "$tmp")"
    gone="$(grep -cF -- "$l3" "$tmp/.bashrc" || true)"
    head -c "$(stat -c %s "$tmp/expected")" "$tmp/.bashrc" | cmp -s - "$tmp/expected" && kept_bytes=yes || kept_bytes=no
    crs="$(grep -c $'\r' "$tmp/.bashrc" || true)"
    crs_first_backup="$(
        first="$(ls -1 "$tmp"/.bashrc.autoos-backup-* 2>/dev/null | sort | head -1)"
        [[ -n "$first" ]] && grep -c $'\r' "$first" || printf '0\n'
    )"
    rm -rf "$tmp"
    ok=1
    [[ "$gone" == 0 ]] || { ok=0; echo "the retired line survived" >&2; }
    [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log" >&2; }
    [[ "$kept_bytes" == yes ]] \
        || { ok=0; echo "the lines around the removed one were rewritten (CRLF converted to LF)" >&2; }
    [[ "$crs" == 3 ]] || { ok=0; echo "the file holds $crs CR-terminated lines, expected 3" >&2; }
    [[ "$crs_first_backup" == 4 ]] || { ok=0; echo "the oldest backup is not the file as found ($crs_first_backup CRs)" >&2; }
    (( ok )) && pass || fail "the retire step is not newline-preserving"
fi

if it "omnigraph-client: a retire step that cannot write the rc file warns, records, and the run continues"; then
    tmp="$(oh_client_sandbox)"
    printf '# my shell\n' >"$tmp/.bashrc"
    oh_run_client "$tmp" >/dev/null
    # The rc file is now AutoOS-configured (the current line is in it), so the only
    # step left that wants to write it is the retirement of this line — and the
    # file is read-only, so that write fails.
    printf 'export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")\n' >>"$tmp/.bashrc"
    if [[ "$(id -u)" == 0 ]]; then
        rm -rf "$tmp"
        skip "root writes a read-only file, so the refusal cannot be seeded"
    else
        chmod 444 "$tmp/.bashrc"
        before="$(md5sum <"$tmp/.bashrc")"
        out="$( (
            set -euo pipefail
            SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
            OMNIGRAPH_TOKEN="dummy-token-1234" AUTOOS_KEYS_FILE="$tmp/keys.yml"
            # shellcheck disable=SC2016
            AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
            npm() { :; }
            oh_hermetic_root
            run_post_install install_omnigraph_client omnigraph-client
            printf 'RUN CONTINUED\nFAILURES %s\n' "${AUTOOS_EXTRA_FAILURES[*]-}"
        ) 2>&1)"; rc=$?
        after="$(md5sum <"$tmp/.bashrc")"
        chmod 644 "$tmp/.bashrc"
        still_there="$(grep -cF -- '/agent-skills/' "$tmp/.bashrc" || true)"
        rm -rf "$tmp"
        ok=1
        (( rc == 0 )) || { ok=0; echo "the run aborted with $rc: [${out:0:300}]" >&2; }
        [[ "$out" == *"RUN CONTINUED"* ]] || { ok=0; echo "the component took the run down: [${out:0:300}]" >&2; }
        [[ "$out" == *"FAILURES"*"omnigraph-client"* ]] \
            || { ok=0; echo "the refused repair was not recorded for the summary: [${out:0:400}]" >&2; }
        [[ "$out" != *"retired agent-skills token line(s) from"* ]] \
            || { ok=0; echo "the step claimed to have removed a line it could not write: [${out:0:400}]" >&2; }
        [[ "$out" == *"left unchanged"* ]] || { ok=0; echo "the refusal was not announced: [${out:0:400}]" >&2; }
        [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log: [${out:0:400}]" >&2; }
        [[ "$before" == "$after" && "$still_there" == 1 ]] \
            || { ok=0; echo "the unwritable file was modified anyway" >&2; }
        (( ok )) && pass || fail "an unwritable rc file is not contained"
    fi
fi

if it "omnigraph-client: an rc file that is not valid UTF-8 is still repaired and its bytes stay"; then
    tmp="$(oh_client_sandbox)"
    retired='export OMNIGRAPH_TOKEN=$(cat "$HOME/Documents/code/agent-skills/secrets/omnigraph.token")'
    # Bytes a UTF-8 decoder refuses (an old Latin-1 or cp1252 dotfile) plus CRLF
    # endings: the step must neither abort, leak a traceback, nor re-encode the
    # file it is editing one line in.
    printf '# Mein sch\xc3hen Shell\r\n%s\r\nalias ll="ls -alF"\r\n' "$retired" >"$tmp/.bashrc"
    printf '\xff\xfe trailing junk\n' >>"$tmp/.bashrc"
    printf '# Mein sch\xc3hen Shell\r\n' >"$tmp/expected"
    out="$(oh_run_client "$tmp")"
    gone="$(grep -cF -- "$retired" "$tmp/.bashrc" || true)"
    head -c "$(stat -c %s "$tmp/expected")" "$tmp/.bashrc" | cmp -s - "$tmp/expected" && bytes=yes || bytes=no
    raw="$(grep -c $'\xff\xfe' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$gone" == 0 ]] || { ok=0; echo "the retired line survived an undecodable file ($gone)" >&2; }
    [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log" >&2; }
    [[ "$bytes" == yes ]] || { ok=0; echo "the bytes before the edited line changed" >&2; }
    [[ "$raw" == 1 ]] || { ok=0; echo "the non-UTF-8 bytes were re-encoded away ($raw)" >&2; }
    (( ok )) && pass || fail "the retire step is not byte-safe"
fi

# ─── the rc-line writer (replace_or_append_marked_line) ─────────────────────
# The two heredocs this step runs used to read and write the rc file as strict
# UTF-8 text, unguarded: an undecodable dotfile aborted the run with a traceback,
# a refused write was announced as a completed one, and every CRLF neighbour was
# converted to LF. Same containment and same bytes as the retire step above.

if it "omnigraph-client: the marked-line step repairs a non-UTF-8 rc file in bytes and keeps its CRLF neighbours"; then
    tmp="$(oh_client_sandbox)"
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    printf '# Mein sch\xc3h\xc4en\r\n%s\r\nalias ll="ls -alF"\r\n\xff\xfe junk\r\n' "$v1" >"$tmp/.bashrc"
    line="$(omnigraph_rc_line)"
    printf '# Mein sch\xc3h\xc4en\r\n%s\r\nalias ll="ls -alF"\r\n\xff\xfe junk\r\n' "$line" >"$tmp/expected"
    out="$(oh_run_client "$tmp")"
    stale="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
    ours="$(grep -c 'AutoOS:omnigraph-env' "$tmp/.bashrc" || true)"
    cmp -s "$tmp/.bashrc" "$tmp/expected" && bytes=same || bytes=different
    crs="$(grep -c $'\r' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log" >&2; }
    [[ "$stale" == 0 && "$ours" == 1 ]] \
        || { ok=0; echo "stale=$stale ours=$ours (the line was not replaced in place)" >&2; }
    [[ "$bytes" == same ]] || { ok=0; echo "the file is not the byte sequence the edit should leave" >&2; }
    [[ "$crs" == 4 ]] || { ok=0; echo "the edited file holds $crs CR-terminated lines, expected 4" >&2; }
    [[ "$out" == *"replaced the"* ]] || { ok=0; echo "the replacement was not announced" >&2; }
    (( ok )) && pass || fail "the marked-line step is not byte-safe"
fi

if it "omnigraph-client: the marked-line purge of a stale line leaves every other line's bytes alone"; then
    tmp="$(oh_client_sandbox)"
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    line="$(omnigraph_rc_line)"
    printf 'junk \xff\xfe here\r\n%s\r\n%s\r\n' "$v1" "$line" >"$tmp/.bashrc"
    printf 'junk \xff\xfe here\r\n%s\r\n' "$line" >"$tmp/expected"
    out="$(oh_run_client "$tmp")"
    cmp -s "$tmp/.bashrc" "$tmp/expected" && bytes=same || bytes=different
    ours="$(grep -c 'AutoOS:omnigraph-env' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    ok=1
    [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log" >&2; }
    [[ "$ours" == 1 ]] || { ok=0; echo "the current line appears $ours times" >&2; }
    [[ "$bytes" == same ]] || { ok=0; echo "the lines around the purged one were rewritten" >&2; }
    (( ok )) && pass || fail "the marked-line purge is not byte-safe"
fi

if it "omnigraph-client: a marked-line step that cannot write the rc file warns, records, and the run continues"; then
    tmp="$(oh_client_sandbox)"
    oh_run_client "$tmp" >/dev/null
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    printf '%s\n' "$v1" >>"$tmp/.bashrc"
    if [[ "$(id -u)" == 0 ]]; then
        rm -rf "$tmp"
        skip "root writes a read-only file, so the refusal cannot be seeded"
    else
        chmod 444 "$tmp/.bashrc"
        before="$(md5sum <"$tmp/.bashrc")"
        # setup.sh's own shell options: the step must not take the run down with
        # it, and must not claim an edit it could not make.
        out="$( (
            set -euo pipefail
            SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 AUTOOS_EXTRA_FAILURES=()
            OMNIGRAPH_TOKEN="dummy-token-1234" AUTOOS_KEYS_FILE="$tmp/keys.yml"
            # shellcheck disable=SC2016
            AUTOOS_ANSWERS=([omnigraph_url]="https://graph.example.invalid")
            npm() { :; }
            oh_hermetic_root
            run_post_install install_omnigraph_client omnigraph-client
            printf 'RUN CONTINUED\nFAILURES %s\n' "${AUTOOS_EXTRA_FAILURES[*]-}"
        ) 2>&1)"; rc=$?
        after="$(md5sum <"$tmp/.bashrc")"
        chmod 644 "$tmp/.bashrc"
        still_there="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
        rm -rf "$tmp"
        ok=1
        (( rc == 0 )) || { ok=0; echo "the run aborted with $rc: [${out:0:300}]" >&2; }
        [[ "$out" == *"RUN CONTINUED"* ]] || { ok=0; echo "the step took the run down: [${out:0:300}]" >&2; }
        [[ "$out" == *"FAILURES"*"omnigraph-client"* ]] \
            || { ok=0; echo "the refused edit was not recorded for the summary: [${out:0:400}]" >&2; }
        [[ "$out" != *"replaced the"* && "$out" != *"removed the stale"* ]] \
            || { ok=0; echo "the step claimed an edit it could not write: [${out:0:400}]" >&2; }
        [[ "$out" == *"left unchanged"* ]] || { ok=0; echo "the refusal was not announced: [${out:0:400}]" >&2; }
        [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log: [${out:0:400}]" >&2; }
        [[ "$before" == "$after" && "$still_there" == 1 ]] \
            || { ok=0; echo "the unwritable file was modified anyway" >&2; }
        (( ok )) && pass || fail "an unwritable rc file is not contained by the marked-line step"
    fi
fi

if it "omnigraph-client: an older versioned rc line is replaced in place and the gate then reads current"; then
    tmp="$(oh_client_sandbox)"
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    v2='[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && while IFS= read -r _ag_l || [ -n "$_ag_l" ]; do _ag_l=${_ag_l%$'"'"'\r'"'"'}; case "$_ag_l" in OMNIGRAPH_TOKEN=*|OMNIGRAPH_BASE_URL=*|OMNIGRAPH_GRAPH_ID=*) export "${_ag_l%%=*}=${_ag_l#*=}" ;; esac; done < "$HOME/.autoos-omnigraph.env"; unset _ag_l  # AutoOS:omnigraph-env-v2'
    printf '# mine\n%s\n%s\n' "$v1" "$v2" >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    out="$(oh_run_client "$tmp")"
    line="$(omnigraph_rc_line)"
    marker="$(omnigraph_rc_marker)"
    ours="$(grep -cF -- "$line" "$tmp/.bashrc" || true)"
    olds="$(grep -c 'AutoOS:omnigraph-env' "$tmp/.bashrc" || true)"
    gate="$(oh_gate "$tmp")"
    second="$(oh_run_client "$tmp")"
    rm -rf "$tmp"
    ok=1
    [[ "$out" != *"Traceback"* ]] || { ok=0; echo "a python traceback reached the log" >&2; }
    [[ "$ours" == 1 ]] || { ok=0; echo "the current rc line appears $ours times" >&2; }
    [[ "$olds" == 1 ]] || { ok=0; echo "$olds AutoOS lines survived (marker $marker)" >&2; }
    [[ "$gate" == current ]] || { ok=0; echo "after the upgrade the gate still says: $gate" >&2; }
    [[ "$second" == *"CHANGED 0"* && "$second" == *"STATE skipped"* ]] \
        || { ok=0; echo "the upgraded machine is not stable: [${second:0:300}]" >&2; }
    (( ok )) && pass || fail "the rc-line version upgrade does not land"
fi

if it "omnigraph-client: one env file yields the same values for the rc line, the shell wrapper and PowerShell"; then
    tmp="$(mktemp -d)"
    # The rule is: whitespace round a value is not part of it, then one layer of
    # matching quotes goes. Three readers share it — the rc line install.sh
    # writes, the shell wrapper and its PowerShell twin. The env file is written
    # by hand here, which is exactly when the rule is visible: install.sh's own
    # writer emits the canonical KEY=value form.
    printf 'OMNIGRAPH_BASE_URL=  https://spaced.example  \n' >"$tmp/.autoos-omnigraph.env"
    printf 'OMNIGRAPH_TOKEN=" spaced-token "\n' >>"$tmp/.autoos-omnigraph.env"
    # Tabs count as surrounding whitespace; the whitespace a quoted value keeps
    # inside its quotes (the token above) does not.
    printf 'OMNIGRAPH_GRAPH_ID=\tgraph-tabs\t\n' >>"$tmp/.autoos-omnigraph.env"
    printf 'OTHER=nope\n' >>"$tmp/.autoos-omnigraph.env"
    want='B=[https://spaced.example] T=[ spaced-token ] G=[graph-tabs] O=[UNSET]'
    # The rc line, read the way a login shell reads it.
    mkdir -p "$tmp/.config"
    printf '# added by AutoOS\n%s\n' "$(omnigraph_rc_line)" >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    got_b="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash -c \
        '. "$HOME/.bashrc"; printf "B=[%s] T=[%s] G=[%s] O=[%s]" "${OMNIGRAPH_BASE_URL-UNSET}" "${OMNIGRAPH_TOKEN-UNSET}" "${OMNIGRAPH_GRAPH_ID-UNSET}" "${OTHER-UNSET}"' 2>&1)"
    got_z="$(env -i HOME="$tmp" PATH=/usr/bin:/bin zsh -f -c \
        '. "$HOME/.zshrc"; printf "B=[%s] T=[%s] G=[%s] O=[%s]" "${OMNIGRAPH_BASE_URL-UNSET}" "${OMNIGRAPH_TOKEN-UNSET}" "${OMNIGRAPH_GRAPH_ID-UNSET}" "${OTHER-UNSET}"' 2>&1)"
    # The shell wrapper, started the way an MCP client starts it.
    cp tools/omnigraph-mcp-autoos.sh "$tmp/wrapper.sh"; chmod 755 "$tmp/wrapper.sh"
    cat >"$tmp/wrapper_report.sh" <<'EOF'
#!/bin/sh
printf 'B=[%s] T=[%s] G=[%s] O=[%s]' "${OMNIGRAPH_BASE_URL-UNSET}" "${OMNIGRAPH_TOKEN-UNSET}" \
    "${OMNIGRAPH_GRAPH_ID-UNSET}" "${OTHER-UNSET}"
EOF
    mkdir -p "$tmp/.local/share/autoos/omnigraph-mcp/bin"
    cp "$tmp/wrapper_report.sh" "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    chmod 755 "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    got_s="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 2>&1)"
    got_p=""
    if command -v pwsh >/dev/null; then
        cp "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp" \
            "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp.cmd"
        cp tools/omnigraph-mcp-autoos.ps1 "$tmp/wrapper.ps1"
        got_p="$(env -i USERPROFILE="$tmp" HOME="$tmp" PATH="$PATH" \
            pwsh -NoProfile -ExecutionPolicy Bypass -File "$tmp/wrapper.ps1" 2>&1)"
    fi
    rm -rf "$tmp"
    ok=1
    [[ "$got_b" == "$want" ]] || { ok=0; echo "bash rc line:  [$got_b]" >&2; }
    if command -v zsh >/dev/null; then
        [[ "$got_z" == "$want" ]] || { ok=0; echo "zsh rc line:   [$got_z]" >&2; }
    fi
    [[ "$got_s" == "$want" ]] || { ok=0; echo "shell wrapper: [$got_s]" >&2; }
    [[ -z "$got_p" || "$got_p" == "$want" ]] || { ok=0; echo "powershell:    [$got_p]" >&2; }
    (( ok )) && pass || fail "the three readers of one env file disagree"
fi

if it "omnigraph-client: a hand-edited export/quoted token line is normalised instead of shadowing the resolved value"; then
    tmp="$(oh_client_sandbox)"
    oh_run_client "$tmp" >/dev/null
    printf 'export OMNIGRAPH_TOKEN="stale-hand-line"\n' >>"$tmp/.autoos-omnigraph.env"
    gate="$(oh_gate "$tmp")"
    out="$(oh_run_client "$tmp")"
    tok="$(grep -c '^OMNIGRAPH_TOKEN=dummy-token-1234$' "$tmp/.autoos-omnigraph.env")"
    rows="$(grep -c 'OMNIGRAPH_TOKEN' "$tmp/.autoos-omnigraph.env")"
    decorated="$(grep -c 'export OMNIGRAPH_TOKEN' "$tmp/.autoos-omnigraph.env" || true)"
    gate_after="$(oh_gate "$tmp")"
    rm -rf "$tmp"
    ok=1
    [[ "$gate" == open ]] || { ok=0; echo "a decorated duplicate line reads as current" >&2; }
    [[ "$out" == *"CHANGED 1"* ]] || { ok=0; echo "the decorated line was not normalised" >&2; }
    [[ "$tok" == 1 && "$rows" == 1 && "$decorated" == 0 ]] \
        || { ok=0; echo "env token rows=$tok total=$rows decorated=$decorated" >&2; }
    [[ "$gate_after" == current ]] || { ok=0; echo "the normalised file is still not current" >&2; }
    [[ "$out" != *stale-hand-line* ]] || { ok=0; echo "the file's value was printed" >&2; }
    (( ok )) && pass || fail "a hand-edited token line is left to shadow the real one"
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

if it "omnigraph-client: the wrapper reads export-prefixed, indented, quoted and CRLF lines without evaluating them"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.local/share/autoos/omnigraph-mcp/bin"
    cat >"$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp" <<'EOF'
#!/bin/sh
printf 'base=[%s]\n' "${OMNIGRAPH_BASE_URL:-}"
printf 'token=[%s]\n' "${OMNIGRAPH_TOKEN:-}"
printf 'graph=[%s]\n' "${OMNIGRAPH_GRAPH_ID:-}"
printf 'other=[%s]\n' "${OTHER_SECRET-}"
EOF
    chmod 755 "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    # A file a person edited by hand: an export prefix, an indent, one layer of
    # quotes, CRLF line endings, a comment, a key that is not one of the three,
    # and a value shaped like a command substitution.
    printf '# a comment OMNIGRAPH_TOKEN=not-this-one\r\n' >"$tmp/.autoos-omnigraph.env"
    printf 'OMNIGRAPH_TOKEN=""\r\n' >>"$tmp/.autoos-omnigraph.env"
    printf 'export OMNIGRAPH_TOKEN="quoted-token"\r\n' >>"$tmp/.autoos-omnigraph.env"
    printf '  export OMNIGRAPH_BASE_URL=https://plain.example\r\n' >>"$tmp/.autoos-omnigraph.env"
    printf "OMNIGRAPH_BASE_URL=\"\"\r\n" >>"$tmp/.autoos-omnigraph.env"
    printf "OMNIGRAPH_GRAPH_ID='single-quoted-graph'\r\n" >>"$tmp/.autoos-omnigraph.env"
    printf 'OTHER_SECRET=nope\r\n' >>"$tmp/.autoos-omnigraph.env"
    printf 'OMNIGRAPH_TOKEN=$(touch %s/pwned)\r\n' "$tmp" >>"$tmp/.autoos-omnigraph.env"
    cp tools/omnigraph-mcp-autoos.sh "$tmp/wrapper.sh"
    chmod 755 "$tmp/wrapper.sh"
    out="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 2>&1)"; rc=$?
    pwned=no; [[ -e "$tmp/pwned" ]] && pwned=yes
    rm -rf "$tmp"
    ok=1
    (( rc == 0 )) || { ok=0; echo "the wrapper exited $rc: [$out]" >&2; }
    [[ "$out" == *"token=[quoted-token]"* ]] \
        || { ok=0; echo "an export-prefixed quoted line was not parsed: [$out]" >&2; }
    [[ "$out" == *"base=[https://plain.example]"* ]] \
        || { ok=0; echo "an indented export line was not parsed: [$out]" >&2; }
    [[ "$out" == *"graph=[single-quoted-graph]"* ]] \
        || { ok=0; echo "a single-quoted line was not parsed: [$out]" >&2; }
    [[ "$pwned" == no ]] || { ok=0; echo "a value was evaluated as a command" >&2; }
    [[ "$out" != *not-this-one* && "$out" != *nope* ]] \
        || { ok=0; echo "a comment or an unlisted key reached the bridge: [$out]" >&2; }
    (( ok )) && pass || fail "the wrapper's env parsing is wrong"
fi

if it "omnigraph-client: the wrapper execs the bridge with no arguments of its own under set -u"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.local/share/autoos/omnigraph-mcp/bin"
    cat >"$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp" <<'EOF'
#!/bin/sh
printf 'argc=%s all=[%s]\n' "$#" "$*"
printf 'have_token=%s\n' "$([ -n "${OMNIGRAPH_TOKEN:-}" ] && echo yes || echo no)"
EOF
    chmod 755 "$tmp/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"
    printf 'OMNIGRAPH_BASE_URL=https://from-file.example\nOMNIGRAPH_TOKEN=secret-from-file\n' \
        >"$tmp/.autoos-omnigraph.env"
    cp tools/omnigraph-mcp-autoos.sh "$tmp/wrapper.sh"
    chmod 755 "$tmp/wrapper.sh"
    # An MCP client starts the bridge over stdio with no arguments at all, in an
    # environment that has none of these variables.
    zero="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 2>&1)"; rc=$?
    one="$(env -i HOME="$tmp" PATH=/usr/bin:/bin bash "$tmp/wrapper.sh" 'a b' --flag 2>&1)"; rc2=$?
    rm -rf "$tmp"
    ok=1
    (( rc == 0 )) || { ok=0; echo "the zero-arg start exited $rc: [$zero]" >&2; }
    [[ "$zero" == *"argc=0 all=[]"* ]] || { ok=0; echo "the bridge got arguments of its own: [$zero]" >&2; }
    [[ "$zero" == *"have_token=yes"* ]] || { ok=0; echo "the env file was not read: [$zero]" >&2; }
    (( rc2 == 0 )) || { ok=0; echo "the arg pass-through exited $rc2: [$one]" >&2; }
    [[ "$one" == *"argc=2 all=[a b --flag]"* ]] \
        || { ok=0; echo "arguments were not passed through intact: [$one]" >&2; }
    (( ok )) && pass || fail "the wrapper's zero-arg start is wrong"
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
        oh_hermetic_root
        install_omnigraph_client
    ) 2>&1)"
    files="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    ok=1
    [[ "$out" == *"would "* ]] || { ok=0; echo "nothing was announced: [${out:0:300}]" >&2; }
    [[ "$out" == *"dry run: nothing was written"* ]] \
        || { ok=0; echo "the dry run did not name itself: [${out:0:300}]" >&2; }
    # A dry run compares nothing, so it must not claim the machine is already
    # current — that message would read as "a real run has nothing to do".
    [[ "$out" != *"already installed"* ]] || { ok=0; echo "the dry run claimed the state was current" >&2; }
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
      # The host running the suite may really have OMNIGRAPH_TOKEN exported; an
      # "argument-less call keeps what the file had" case must not read it.
      unset OMNIGRAPH_TOKEN
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
# The profiles differ per catalog by design: the Linux catalog has a `server`
# profile and the operator put the component in it (2026-09-28, Q-001 lifted),
# macOS has no `server` profile at all, so its entry keeps the three it has.
profiles = {
    "catalog/linux.json": ["workstation", "ai-coding", "light", "server"],
    "catalog/macos.json": ["workstation", "ai-coding", "light"],
}
want = {
    "id": "omnigraph-client", "provider": "custom", "package": "omnigraph-client",
    "postInstall": "install_omnigraph_client", "prompt": "omnigraph_url",
    "requires": ["nodejs"],
}
problems = []
for f, want_profiles in profiles.items():
    data = json.load(open(f, encoding="utf-8"))
    found = [c for g in data["categories"] for c in g["components"] if c.get("id") == "omnigraph-client"]
    if len(found) != 1:
        problems.append(f + ": " + str(len(found)) + " entries"); continue
    c = found[0]
    for k, v in want.items():
        if c.get(k) != v:
            problems.append("%s: %s is %r, expected %r" % (f, k, c.get(k), v))
    if c.get("profiles") != want_profiles:
        problems.append("%s: profiles is %r, expected %r" % (f, c.get("profiles"), want_profiles))
    if c.get("homepage") != "https://www.npmjs.com/package/@modernrelay/omnigraph-mcp":
        problems.append(f + ": homepage is not the npm package page")
    if not c.get("notes"):
        problems.append(f + ": no notes for the plan entry")
print("\n".join(problems))
sys.exit(1 if problems else 0)
PY
)" && catalog_validate catalog/linux.json >/dev/null 2>&1 && catalog_validate catalog/macos.json >/dev/null 2>&1 \
    && pass || fail "catalog entry: ${out:0:300}"
fi

if it "omnigraph-client: the server profile plans it and a headless dry run exits 0 with no URL configured"; then
    # The Q-001 decision: a headless server gets the bridge too. Run the way an
    # unattended server is run — profile, dry run, --yes, and no omnigraph_url
    # answer anywhere (the prompt default is blank) — so this proves both halves
    # at once: the component is in the plan, and a machine with no server
    # configured reports a skip and still exits 0 rather than failing.
    out="$(bash setup.sh --profile server --dry-run --yes --no-color 2>&1)"; rc=$?
    planned="$(printf '%s\n' "$out" | grep -cE '^[[:space:]]+[0-9]+\.[[:space:]]+.*omnigraph-client' || true)"
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "exit $rc: [$(printf '%s\n' "$out" | tail -5)]" >&2; }
    [[ "$planned" == 1 ]] \
        || { ok=0; echo "the server plan does not carry omnigraph-client ($planned lines)" >&2; }
    [[ "$out" == *"omnigraph-client: skipped: no omnigraph URL"* ]] \
        || { ok=0; echo "no skip-with-hint line for the missing URL: [${out:0:300}]" >&2; }
    (( ok )) && pass || fail "omnigraph-client is not in the server profile"
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

# ─── D-1038 A3: the 0.13 HTTP contract header in the device sync scripts ────
#
# Measured: Omnigraph 0.13.0 answers 400 {"code":"api_contract_mismatch"} to
# every HTTP request (except /healthz) that lacks `omnigraph-http-api: 0.13`, so
# the direct HTTP calls in both device-sync scripts must send it. The docker
# `omnigraph` CLI/load calls are untouched — the 0.13 CLI sends the header
# itself — and so are the two exempt reads: /healthz, and the viewer's
# /api/sync-ping, which is not an Omnigraph request at all. Hermetic: static
# reads of the two scripts plus pull_graph.py and one `bash -n`; no network, no
# server.
if it "omnigraph-client: the device sync scripts send the 0.13 contract header"; then
    sync_sh='infra/mcp-servers/omnigraph-setup/omnigraph-sync.sh'
    sync_ps='infra/mcp-servers/omnigraph-setup/sync-windows.ps1'
    IMAGE_PIN='modernrelay/omnigraph-server:v0.13.0@sha256:f664cab63d746d7f1fb66d51bf2869363b481f094366853c4e7741b5e96645c1'
    ok=1
    [[ -f "$sync_sh" && -f "$sync_ps" ]] || { ok=0; echo "a sync script is missing" >&2; }
    # (a) Every curl that addresses an Omnigraph graph carries the header. Two
    # greps on purpose: a curl line without /graphs is the viewer's sync-ping,
    # which must NOT gain the header, so the demand is exactly "curl AND /graphs".
    graph_curls="$(grep 'curl' "$sync_sh" | grep '/graphs' || true)"
    n_curls="$(printf '%s\n' "$graph_curls" | grep -c . || true)"
    bare_curls="$(printf '%s\n' "$graph_curls" | grep -v 'CONTRACT_HEADER' || true)"
    n_defines="$(grep -c "^CONTRACT_HEADER='omnigraph-http-api: 0.13'$" "$sync_sh" || true)"
    ping_header="$(grep 'sync-ping' "$sync_sh" | grep -c 'CONTRACT_HEADER' || true)"
    # (b) The PowerShell twin: the header constant exists and reaches the one
    # Omnigraph request, GET /graphs in Get-GraphList.
    ps_header="$(grep -c "omnigraph-http-api" "$sync_ps" || true)"
    ps_list="$(sed -n '/^function Get-GraphList/,/^}/p' "$sync_ps")"
    ps_merged="$(printf '%s\n' "$ps_list" | grep -c 'CONTRACT_HEADERS' || true)"
    ps_exempt="$( (grep 'healthz' "$sync_ps"; grep 'sync-ping' "$sync_ps") | grep -c 'CONTRACT_HEADERS' || true)"
    # (c) The script still parses after the edits.
    bash -n "$sync_sh" 2>/dev/null || { ok=0; echo "bash -n failed on $sync_sh" >&2; }
    # (d) The default image moved to the 0.13.0 digest pin, and the old pin is
    # gone from both scripts entirely (comments included).
    sh_pin="$(grep -cF "OMNIGRAPH_IMAGE:-$IMAGE_PIN" "$sync_sh" || true)"
    ps_pin="$(grep -cF "else { '$IMAGE_PIN' }" "$sync_ps" || true)"
    old_pin="$(grep -h 'v0\.8\.1' "$sync_sh" "$sync_ps" | wc -l | tr -d '[:space:]')"
    # (e) D-1038 P2f2 W2 / F1: pull_graph.py runs the pull's own preflight and its
    # load in THIS image, so it must carry the same 0.13.0 pin — the 0.8.1 CLI
    # sends no contract header and the 0.13 server answers it 400. Asserted on the
    # assignment, not a bare grep: the file's comments still name v0.8.1 as where
    # the Lance incidents were measured.
    pull_py='infra/mcp-servers/omnigraph-setup/pull_graph.py'
    [[ -f "$pull_py" ]] || { ok=0; echo "pull_graph.py is missing" >&2; }
    pull_pin="$(grep -cF "IMAGE = \"$IMAGE_PIN\"" "$pull_py" || true)"
    pull_old_image="$(grep -cF 'IMAGE = "modernrelay/omnigraph-server:v0.8.1"' "$pull_py" || true)"
    # (f) D-1038 P2f2 W2 / F3: a kill between the purge and the load leaves the local
    # graph empty, so the script's own help has to carry the restore command.
    restore_doc="$(grep -c 'RESTORE after a kill' "$sync_sh" || true)"
    restore_cmd="$(grep -cF -- '--mode merge --yes --json' "$sync_sh" || true)"
    [[ "$n_curls" -ge 2 ]] || { ok=0; echo "only $n_curls curl line(s) address /graphs - the check would be vacuous" >&2; }
    [[ -z "$bare_curls" ]] || { ok=0; echo "curl line(s) to /graphs without the header: $bare_curls" >&2; }
    [[ "$n_defines" == 1 ]] || { ok=0; echo "the header constant is defined $n_defines time(s), expected exactly one" >&2; }
    [[ "$ping_header" == 0 ]] || { ok=0; echo "the viewer's sync-ping was given the Omnigraph header" >&2; }
    [[ "$ps_header" -ge 1 ]] || { ok=0; echo "sync-windows.ps1 never names the omnigraph-http-api header" >&2; }
    [[ "$ps_merged" == 1 ]] || { ok=0; echo "Get-GraphList sends the header $ps_merged time(s), expected 1" >&2; }
    [[ "$ps_exempt" == 0 ]] || { ok=0; echo "/healthz or the viewer ping was given the header" >&2; }
    [[ "$sh_pin" == 1 ]] || { ok=0; echo "the sh default image is not the 0.13.0 pin ($sh_pin)" >&2; }
    [[ "$ps_pin" == 1 ]] || { ok=0; echo "the ps1 default image is not the 0.13.0 pin ($ps_pin)" >&2; }
    [[ "$old_pin" == 0 ]] || { ok=0; echo "the old v0.8.1 pin still appears $old_pin time(s) in the two scripts" >&2; }
    [[ "$pull_pin" == 1 ]] || { ok=0; echo "pull_graph.py IMAGE is not the 0.13.0 pin ($pull_pin)" >&2; }
    [[ "$pull_old_image" == 0 ]] || { ok=0; echo "pull_graph.py still assigns the v0.8.1 image to IMAGE ($pull_old_image time(s))" >&2; }
    [[ "$restore_doc" == 1 ]] || { ok=0; echo "the sync header documents the restore $restore_doc time(s), expected 1" >&2; }
    [[ "$restore_cmd" -ge 1 ]] || { ok=0; echo "the sync header carries no merge-load restore command" >&2; }
    (( ok )) && pass || fail "the contract header / 0.13 image pin is not in place"
fi

# ─── D-1038 P2f2 W2 / F2: a failed graph must make the sync exit non-zero ────
#
# `if ! sync_graph "$g"; then rc=$?` reads the status of the NEGATION, which is
# always 0, so every failing graph exited the script 0 and the systemd timer /
# Windows task saw a green run forever. This is the behavioural proof, and it is
# hermetic: curl is a stub that fails like a refused connection (exit 22, the code
# `curl -f` returns on an HTTP error) and docker is a stub that reports it is
# absent, so a sync that got as far as a container is caught. 127.0.0.1:9 (discard)
# is never actually dialed — the stub answers first.
if it "omnigraph-client: omnigraph-sync.sh exits non-zero when a graph sync fails"; then
    sync_sh='infra/mcp-servers/omnigraph-setup/omnigraph-sync.sh'
    tmp="$(mktemp -d)"; bin="$tmp/bin"
    mkdir -p "$bin" "$tmp/backups"
    printf '#!/bin/sh\nexit 22\n' >"$bin/curl"
    printf '#!/bin/sh\necho "docker: command not found" >&2\nexit 127\n' >"$bin/docker"
    chmod 755 "$bin/curl" "$bin/docker"
    err="$(PATH="$bin:$PATH" \
        CENTRAL_URL='http://127.0.0.1:9' CENTRAL_TOKEN='t' LOCAL_TOKEN='t' \
        LOCAL_URL='http://127.0.0.1:9' GRAPHS='ga gb' BACKUP_DIR="$tmp/backups" \
        PYTHON='python3' timeout 60 bash "$sync_sh" 2>&1 >/dev/null)"
    rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "the sync exited $rc while both graphs failed (must be non-zero)" >&2; }
    [[ "$err" == *'[ga] sync returned'* ]] || { ok=0; echo "no per-graph verdict for ga: [${err:0:400}]" >&2; }
    [[ "$err" == *'[gb] sync returned'* ]] || { ok=0; echo "no per-graph verdict for gb (one failure must not abort the rest): [${err:0:400}]" >&2; }
    [[ "$err" != *'docker: command not found'* ]] || { ok=0; echo "the sync reached docker before it reached a verdict" >&2; }
    rm -rf "$tmp"
    (( ok )) && pass || fail "omnigraph-sync.sh does not propagate a failed graph's exit code"
fi

if it "omnigraph-client: docs name the component and the token rotation procedure"; then
    ok=1
    grep -q 'omnigraph-client' docs/omnigraph.md || { ok=0; echo "docs/omnigraph.md has no omnigraph-client section" >&2; }
    grep -qi 'Token rotation' docs/omnigraph.md || { ok=0; echo "no token-rotation heading" >&2; }
    grep -q 'omnigraph-mcp-autoos' docs/omnigraph.md || { ok=0; echo "the wrapper is undocumented" >&2; }
    grep -q 'omnigraph-client' docs/catalog.md || { ok=0; echo "docs/catalog.md does not list the component" >&2; }
    (( ok )) && pass || fail "the docs are missing"
fi
