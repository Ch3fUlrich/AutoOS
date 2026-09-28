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
