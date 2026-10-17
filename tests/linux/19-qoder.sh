# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Qoder (catalog entries + MCP wiring) ───────────────────────────────────
describe "qoder"

if it "qoder catalog entries exist on linux and macos with the right shape"; then
    # catalog_validate already enforces provider/package/verify and the
    # manual-needs-homepage-and-notes rule for every entry; this pins the
    # Qoder-specific choices the task fixes: script provider + qodercli --version
    # + setup_qoder_mcp + no arch for the CLI, and manual for the desktop app
    # (no Homebrew cask and no apt/snap package exist for it).
    report="$(python3 - 2>&1 <<'PY'
import json
def comp(path, cid):
    data = json.load(open(path, encoding="utf-8"))
    for g in data["categories"]:
        for c in g["components"]:
            if c["id"] == cid:
                return c
    return None
problems = []
for path in ("catalog/linux.json", "catalog/macos.json"):
    cli = comp(path, "qodercli")
    desk = comp(path, "qoder-desktop")
    if not cli:
        problems.append(path + ":no-qodercli")
    else:
        if cli.get("provider") != "script": problems.append(path + ":cli-provider")
        if cli.get("package") != "qodercli": problems.append(path + ":cli-package")
        if cli.get("verify") != "qodercli --version": problems.append(path + ":cli-verify")
        if cli.get("postInstall") != "setup_qoder_mcp": problems.append(path + ":cli-postinstall")
        if "arch" in cli: problems.append(path + ":cli-has-arch")
    if not desk:
        problems.append(path + ":no-qoder-desktop")
    else:
        if desk.get("provider") != "manual": problems.append(path + ":desk-provider")
        if not desk.get("homepage"): problems.append(path + ":desk-homepage")
        if not desk.get("notes"): problems.append(path + ":desk-notes")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "qodercli postInstall names a shell function that exists"; then
    # postInstall is NOT schema-validated: run_post_install only warns when the
    # named function is missing, so a typo silently does nothing on a real
    # machine. Assert the catalogued name really resolves to a defined function.
    ok=1
    for path in catalog/linux.json catalog/macos.json; do
        fn="$(python3 -c "
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
for g in data['categories']:
    for c in g['components']:
        if c['id'] == 'qodercli':
            print(c.get('postInstall', ''))
" "$path")"
        [[ "$fn" == "setup_qoder_mcp" ]] || { ok=0; echo "$path postInstall=$fn" >&2; }
        declare -F "$fn" >/dev/null || { ok=0; echo "$path: $fn is not defined" >&2; }
    done
    if (( ok )); then pass; else fail "qodercli postInstall does not resolve to a defined function"; fi
fi

if it "qodercli detection rides the script provider"; then
    ok=1
    # The dispatch case itself is covered by "script dispatch covers qodercli
    # and devin-cli" above; what this pins is the detect.sh side, without which
    # an already-installed Qoder would be reinstalled instead of skipped.
    grep -q 'qodercli)        has_bin qodercli' lib/linux/detect.sh || ok=0
    # has_bin is stubbed so the test never depends on what this machine happens
    # to have installed.
    ( has_bin() { return 1; }; script_is_installed qodercli ) >/dev/null 2>&1 && ok=0
    ( has_bin() { return 0; }; script_is_installed qodercli ) >/dev/null 2>&1 || ok=0
    if (( ok )); then pass; else fail "qodercli dispatch or detection is broken"; fi
fi

if it "setup_qoder_mcp writes nothing in a dry run"; then
    tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=1; setup_qoder_mcp ) 2>&1)"
    created="$(find "$tmp" -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
    rm -rf "$tmp"
    if [[ "$out" == *"would"* && "$created" == "0" ]]; then pass
    else fail "dry run created $created file(s); out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "setup_qoder_mcp registers four servers and never omnigraph"; then
    tmp="$(mktemp -d)"
    stub="$(mktemp -d)"
    log="$tmp/calls.log"
    export AUTOOS_QODER_STUB_LOG="$log"
    # Fake qodercli: record every invocation, report nothing registered yet (so
    # every server is attempted), and never write to the fake home. Asserts on
    # the PLANNED command, not on system state (AGENTS.md section 5).
    cat >"$stub/qodercli" <<'EOS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$AUTOOS_QODER_STUB_LOG"
exit 0
EOS
    chmod +x "$stub/qodercli"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        PATH="$stub:$PATH"
        unset CONTEXT7_API_KEY
        answer() { printf ''; }
        setup_qoder_mcp >/dev/null 2>&1
    )
    added="$(grep -oE 'add-json [a-z0-9]+' "$log" 2>/dev/null | awk '{print $2}' | sort | tr '\n' ',' | sed 's/,$//')"
    # omnigraph's graph is per-repo, so it must never become a machine-global
    # user-scope entry (this repo ships it at project scope in .mcp.json).
    omni="$(grep -c 'omnigraph' "$log" 2>/dev/null || true)"
    # The pin must flow from the harness at runtime, never a literal in lib/.
    serena_pin="$(python3 -c "import json;print(json.load(open('catalog/agent-harness.json',encoding='utf-8'))['mcp_servers']['serena']['package'])")"
    pin_ok=0; grep -qF -- "$serena_pin" "$log" 2>/dev/null && pin_ok=1
    rm -rf "$tmp" "$stub"
    unset AUTOOS_QODER_STUB_LOG
    if [[ "$added" == "context7,graphify,playwright,serena" && "$omni" == "0" && "$pin_ok" == "1" ]]; then pass
    else fail "added=[$added] omnigraph_lines=$omni pin_ok=$pin_ok (expected context7,graphify,playwright,serena / 0 / 1)"; fi
fi

if it "setup_qoder_mcp skips servers that are already registered"; then
    tmp="$(mktemp -d)"
    stub="$(mktemp -d)"
    log="$tmp/calls.log"
    export AUTOOS_QODER_STUB_LOG="$log"
    # Fake qodercli whose `mcp list` reports all four as present, so a second
    # run must add nothing (idempotent - AGENTS.md section 4).
    cat >"$stub/qodercli" <<'EOS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$AUTOOS_QODER_STUB_LOG"
if [[ "${1:-}" == "mcp" && "${2:-}" == "list" ]]; then
    printf 'serena: connected\ngraphify: connected\nplaywright: connected\ncontext7: connected\n'
fi
exit 0
EOS
    chmod +x "$stub/qodercli"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        PATH="$stub:$PATH"
        unset CONTEXT7_API_KEY
        answer() { printf ''; }
        setup_qoder_mcp >/dev/null 2>&1
    )
    adds="$(grep -c 'add-json' "$log" 2>/dev/null || true)"
    rm -rf "$tmp" "$stub"
    unset AUTOOS_QODER_STUB_LOG
    if [[ "$adds" == "0" ]]; then pass
    else fail "expected 0 add-json calls when every server already exists, got $adds"; fi
fi

