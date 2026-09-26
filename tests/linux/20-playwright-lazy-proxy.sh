# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Playwright MCP: the lazy proxy registration ────────────────────────────
describe "playwright lazy proxy"

# A sandbox for install_mcp_playwright: a scratch HOME, a stub `claude` that records
# every call and edits the scratch user config the way `claude mcp add|remove --scope
# user` does, and a stub `docker` that records its calls (the installer must never
# stop a container, so its log has to stay empty). Nothing here touches the real
# HOME, the real claude or the real docker.
pw_setup() {
    local tmp="$1"
    mkdir -p "$tmp/bin" "$tmp/home"
    cat >"$tmp/bin/claude" <<'EOS'
#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["PW_STUB_LOG"], "a") as fh:
    fh.write(" ".join(argv) + "\n")
cfg = os.environ["PW_STUB_CONFIG"]

def load():
    try:
        with open(cfg, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}

def save(data):
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

if argv[:2] == ["mcp", "list"]:
    for name, entry in (load().get("mcpServers") or {}).items():
        print("%s: %s %s - Connected" % (name, entry.get("command", ""), " ".join(entry.get("args", []))))
    for line in filter(None, os.environ.get("PW_STUB_EXTRA_LIST", "").split("|")):
        print(line)
elif argv[:2] == ["mcp", "add"]:
    name = argv[argv.index("--scope") + 2]
    command = argv[argv.index("--") + 1:]
    if os.environ.get("PW_STUB_ADD_FAIL_MATCH") and os.environ["PW_STUB_ADD_FAIL_MATCH"] in " ".join(command):
        sys.exit(1)
    data = load()
    data.setdefault("mcpServers", {})[name] = {"type": "stdio", "command": command[0], "args": command[1:], "env": {}}
    save(data)
elif argv[:2] == ["mcp", "remove"]:
    data = load()
    (data.get("mcpServers") or {}).pop(argv[2], None)
    save(data)
EOS
    cat >"$tmp/bin/docker" <<'EOS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$PW_STUB_DOCKER_LOG"
EOS
    chmod +x "$tmp/bin/claude" "$tmp/bin/docker"
}

# pw_seed <config> <command|-> [args...]: a user config with a serena server and
# (unless the command is "-") a playwright stdio entry.
pw_seed() {
    python3 - "$@" <<'PY'
import json, sys
path, command, args = sys.argv[1], sys.argv[2], sys.argv[3:]
data = {"numStartups": 42, "mcpServers": {"serena": {"type": "stdio", "command": "uvx", "args": ["serena"], "env": {}}}}
if command != "-":
    data["mcpServers"]["playwright"] = {"type": "stdio", "command": command, "args": args, "env": {}}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
}

# pw_seed_json <config> <json>: the playwright entry verbatim, for the odd shapes.
pw_seed_json() {
    python3 - "$1" "$2" <<'PY'
import json, sys
data = {"numStartups": 42, "mcpServers": {"playwright": json.loads(sys.argv[2])}}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
}

# pw_entry <config>: the playwright entry as "command arg arg", "-" when absent.
pw_entry() {
    python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        entry = (json.load(fh).get("mcpServers") or {}).get("playwright")
except (OSError, ValueError):
    entry = None
print("-" if entry is None else " ".join([entry.get("command", "")] + entry.get("args", [])))
PY
}

# pw_argv_json <config>: the playwright entry's command and args as one JSON list, "-" when absent
# (pw_entry joins with spaces, so it cannot tell "a b" from "a" "b").
pw_argv_json() {
    python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        entry = (json.load(fh).get("mcpServers") or {}).get("playwright")
except (OSError, ValueError):
    entry = None
print("-" if entry is None else json.dumps([entry.get("command", "")] + entry.get("args", [])))
PY
}

# pw_servers <config>: the user-scope server names, sorted and comma-joined.
pw_servers() {
    python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(",".join(sorted((json.load(fh).get("mcpServers") or {}))))
except (OSError, ValueError):
    print("-")
PY
}

# pw_run <tmp> <function> [args]: one installer function inside the sandbox. PW_DRY, PW_OS,
# PW_CONFIG_DIR, PW_ADD_FAIL and PW_EXTRA_LIST steer it; the caller captures the output.
pw_run() {
    local tmp="$1"; shift
    (
        SYS_HOME="$tmp/home"; HOME="$tmp/home"; AUTOOS_DRY_RUN="${PW_DRY:-0}"; SYS_OS="${PW_OS:-linux}"
        PATH="$tmp/bin:$PATH"
        export PW_STUB_LOG="$tmp/claude.log" PW_STUB_DOCKER_LOG="$tmp/docker.log"
        export PW_STUB_CONFIG="${PW_CONFIG:-$tmp/home/.claude.json}"
        export PW_STUB_ADD_FAIL_MATCH="${PW_ADD_FAIL:-}" PW_STUB_EXTRA_LIST="${PW_EXTRA_LIST:-}"
        unset CLAUDE_CONFIG_DIR
        [[ -z "${PW_CONFIG_DIR:-}" ]] || export CLAUDE_CONFIG_DIR="$PW_CONFIG_DIR"
        register_antigravity_mcp_server() { printf '%s %s\n' "$1" "$2" >>"$tmp/antigravity.log"; }
        "$@"
    )
}

pw_without_docker() {
    has_cmd() { [[ "$1" != docker ]] && command -v "$1" >/dev/null 2>&1; }
    install_mcp_playwright
}

pw_without_claude() {
    has_cmd() { [[ "$1" != claude ]] && command -v "$1" >/dev/null 2>&1; }
    install_mcp_playwright
}

pw_backup_fails() {
    backup_file() { return 1; }
    install_mcp_playwright
}

# pw_writes <tmp>: how many claude calls changed something (add or remove).
pw_writes() {
    local n
    n="$(grep -cE '^mcp (add|remove)' "$1/claude.log" 2>/dev/null)" || n=0
    printf '%s\n' "${n:-0}"
}

PW_DOCKER_FORM=(docker run -i --rm --init --network host mcr.microsoft.com/playwright/mcp:latest)

if it "playwright lazy proxy installer: no entry and docker present registers the proxy by absolute path"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; pw_seed "$tmp/home/.claude.json" -
    out="$(cd / && pw_run "$tmp" install_mcp_playwright 2>&1)"
    calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
    got="$(pw_entry "$tmp/home/.claude.json")"
    ag="$(cat "$tmp/antigravity.log" 2>/dev/null)"
    dk="$(cat "$tmp/docker.log" 2>/dev/null)"
    rm -rf "$tmp"
    problems=""
    [[ "$calls" == "mcp add --scope user playwright -- python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" calls=[$calls]"
    [[ "$got" == "python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" entry=[$got]"
    [[ "$ag" == 'playwright {"command": "npx", "args": ["-y", "@playwright/mcp@'* ]] || problems+=" antigravity=[$ag]"
    [[ -z "$dk" ]] || problems+=" docker was called: [$dk]"
    [[ "$out" == *"registered"* ]] || problems+=" the registration was not reported"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: an exact docker or npx entry is replaced after a backup, a second run skips"; then
    problems=""
    for form in docker npx npx-bare npx-latest; do
        tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
        case "$form" in
            docker)     pw_seed "$cfg" "${PW_DOCKER_FORM[@]}" ;;
            npx)        pw_seed "$cfg" npx -y @playwright/mcp@0.0.81 ;;
            npx-bare)   pw_seed "$cfg" npx -y @playwright/mcp ;;
            npx-latest) pw_seed "$cfg" npx -y @playwright/mcp@latest ;;
        esac
        cp "$cfg" "$tmp/seed.json"
        out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
        calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
        got="$(pw_entry "$cfg")"
        backups=( "$cfg".autoos-backup-* )
        if [[ ! -f "${backups[0]}" ]] || ! cmp -s "${backups[0]}" "$tmp/seed.json"; then problems+=" [$form] no byte-identical backup;"; fi
        [[ "$calls" == "mcp remove playwright --scope user|mcp add --scope user playwright -- python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" [$form] calls=[$calls];"
        [[ "$got" == "python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" [$form] entry=[$got];"
        [[ "$out" == *"replaced"* ]] || problems+=" [$form] the replace was not reported;"
        notices="$(printf '%s\n' "$out" | grep -c 'keep their current' || true)"
        [[ "$notices" == "1" ]] || problems+=" [$form] the running-sessions notice appeared $notices times;"
        [[ "$(pw_servers "$cfg")" == "playwright,serena" ]] || problems+=" [$form] the other servers changed: $(pw_servers "$cfg");"
        : >"$tmp/claude.log"
        out2="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
        again=( "$cfg".autoos-backup-* )
        [[ "$(pw_writes "$tmp")" == "0" && "$out2" == *"skipped"* && "${#again[@]}" == "1" ]] \
            || problems+=" [$form] the second run wrote or made another backup: $(pw_writes "$tmp") ${#again[@]};"
        [[ ! -s "$tmp/docker.log" ]] || problems+=" [$form] docker was called: $(cat "$tmp/docker.log");"
        rm -rf "$tmp"
    done
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: an entry that already is the proxy is skipped without a write"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" python3 "$ROOT/tools/playwright_mcp_lazy.py"
    cp "$cfg" "$tmp/seed.json"
    out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
    writes="$(pw_writes "$tmp")"
    same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
    backups=( "$cfg".autoos-backup-* )
    nbackups=0; [[ -e "${backups[0]}" ]] && nbackups="${#backups[@]}"
    rm -rf "$tmp"
    if [[ "$writes" == "0" && "$same" == "1" && "$nbackups" == "0" && "$out" == *"skipped"* ]]; then pass
    else fail "writes=$writes unchanged=$same backups=$nbackups out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: any other custom entry is left alone with a warning that names it"; then
    problems=""
    n=0
    while IFS= read -r entry; do
        n=$((n + 1))
        tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
        pw_seed_json "$cfg" "$entry"
        cp "$cfg" "$tmp/seed.json"
        out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
        backups=( "$cfg".autoos-backup-* )
        same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
        [[ "$(pw_writes "$tmp")" == "0" && "$same" == "1" && ! -e "${backups[0]}" ]] || problems+=" [$n] the entry was touched;"
        [[ "$out" == *"'playwright'"* && "$out" == *"custom"* ]] || problems+=" [$n] no warning naming it;"
        [[ "$out" != *"sekret-marker"* ]] || problems+=" [$n] the warning printed the entry's own arguments;"
        rm -rf "$tmp"
    done <<EOF
{"type":"stdio","command":"docker","args":["run","-i","--rm","mcr.microsoft.com/playwright/mcp:v9.9.9"],"env":{}}
{"type":"stdio","command":"npx","args":["-y","@playwright/mcp@latest","--headless"],"env":{}}
{"type":"stdio","command":"docker","args":["run","-i","--rm","--init","--network","host","mcr.microsoft.com/playwright/mcp:latest"],"env":{"TOKEN":"sekret-marker"}}
{"type":"stdio","command":"node","args":["/opt/pw/cli.js","--marker","sekret-marker"],"env":{}}
{"type":"stdio","command":"python3","args":["/elsewhere/tools/other_script.py"],"env":{}}
{"type":"http","url":"http://localhost:1/sekret-marker"}
{"type":"stdio","command":"python3","args":["/elsewhere/bin/playwright_mcp_lazy.py"],"env":{}}
{"type":"stdio","command":"python3","args":["-u","/elsewhere/tools/playwright_mcp_lazy.py"],"env":{}}
{"type":"stdio","command":"python","args":["/elsewhere/tools/playwright_mcp_lazy.py"],"env":{}}
{"type":"stdio","command":"python3","args":["/elsewhere/tools/playwright_mcp_lazy.py"],"env":{"TOKEN":"sekret-marker"}}
{"type":"stdio","command":"python3","args":["/else\twhere/tools/playwright_mcp_lazy.py"],"env":{}}
EOF
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: without docker today's npx registration is unchanged"; then
    problems=""
    tmp="$(mktemp -d)"; pw_setup "$tmp"; pw_seed "$tmp/home/.claude.json" -
    out="$(pw_run "$tmp" pw_without_docker 2>&1)"
    calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
    pkg="$(python3 -c "import json;print(json.load(open('catalog/agent-harness.json',encoding='utf-8'))['mcp_servers']['playwright']['package'])")"
    [[ "$calls" == "mcp add --scope user playwright -- npx -y $pkg" ]] || problems+=" no-entry calls=[$calls];"
    rm -rf "$tmp"
    # an existing docker-form entry is not this installer's to replace when there is no docker
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" "${PW_DOCKER_FORM[@]}"; cp "$cfg" "$tmp/seed.json"
    out="$(pw_run "$tmp" pw_without_docker 2>&1)"
    same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
    [[ "$(pw_writes "$tmp")" == "0" && "$same" == "1" ]] || problems+=" existing entry was touched;"
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: on macOS the npx registration is kept even with docker present"; then
    problems=""
    tmp="$(mktemp -d)"; pw_setup "$tmp"; pw_seed "$tmp/home/.claude.json" -
    out="$(PW_OS=macos pw_run "$tmp" install_mcp_playwright 2>&1)"
    calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
    pkg="$(python3 -c "import json;print(json.load(open('catalog/agent-harness.json',encoding='utf-8'))['mcp_servers']['playwright']['package'])")"
    [[ "$calls" == "mcp add --scope user playwright -- npx -y $pkg" ]] || problems+=" no-entry calls=[$calls];"
    rm -rf "$tmp"
    # and an existing docker-form entry is left alone there
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" "${PW_DOCKER_FORM[@]}"; cp "$cfg" "$tmp/seed.json"
    out="$(PW_OS=macos pw_run "$tmp" install_mcp_playwright 2>&1)"
    same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
    [[ "$(pw_writes "$tmp")" == "0" && "$same" == "1" ]] || problems+=" existing entry was touched;"
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: a dry run prints the plan and calls nothing"; then
    problems=""
    for form in none docker stale; do
        tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
        case "$form" in
            none) pw_seed "$cfg" - ;;
            docker) pw_seed "$cfg" "${PW_DOCKER_FORM[@]}" ;;
            stale) pw_seed "$cfg" python3 /old/AutoOS/tools/playwright_mcp_lazy.py ;;
        esac
        cp "$cfg" "$tmp/seed.json"
        out="$(PW_DRY=1 pw_run "$tmp" install_mcp_playwright 2>&1)"
        same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
        backups=( "$cfg".autoos-backup-* )
        [[ ! -s "$tmp/claude.log" && "$same" == "1" && ! -e "${backups[0]}" && ! -s "$tmp/docker.log" ]] || problems+=" [$form] a dry run called or wrote something;"
        [[ "$out" == *"would"* && "$out" == *"$ROOT/tools/playwright_mcp_lazy.py"* ]] || problems+=" [$form] no plan naming the proxy: $(printf '%s' "$out" | tail -3);"
        [[ "$form" != stale || "$out" == *"/old/AutoOS/tools/playwright_mcp_lazy.py"* ]] || problems+=" [stale] the plan does not name the old path;"
        rm -rf "$tmp"
    done
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: a failed backup stops the replace with a warning"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" "${PW_DOCKER_FORM[@]}"; cp "$cfg" "$tmp/seed.json"
    out="$(pw_run "$tmp" pw_backup_fails 2>&1)"
    same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
    writes="$(pw_writes "$tmp")"
    rm -rf "$tmp"
    if [[ "$writes" == "0" && "$same" == "1" && "$out" == *"could not back up"* ]]; then pass
    else fail "writes=$writes unchanged=$same out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: with no entry the config is backed up before the add, a second run makes no backup and no add"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" -; chmod 666 "$cfg"; cp "$cfg" "$tmp/seed.json"   # 666: a plain cp would change it under any umask
    printf 'an earlier backup' >"$cfg.autoos-backup-20200101-000000"
    out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
    problems=""; fresh=0; kept=0
    for b in "$cfg".autoos-backup-*; do
        if [[ "$b" == *-20200101-000000 ]]; then
            [[ "$(cat "$b")" == "an earlier backup" ]] && kept=1
        elif cmp -s "$b" "$tmp/seed.json"; then
            fresh=$((fresh + 1))
            [[ "$(_file_mode "$b")" == 666 ]] || problems+=" the backup lost the file mode ($(_file_mode "$b"));"
        fi
    done
    [[ "$fresh" == 1 ]] || problems+=" $fresh byte-identical new backups, want 1;"
    [[ "$kept" == 1 ]] || problems+=" the earlier backup was touched;"
    [[ "$out" == *"config backup: $cfg.autoos-backup-"* ]] || problems+=" the backup was not named in the report;"
    calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
    [[ "$calls" == "mcp add --scope user playwright -- python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" calls=[$calls];"
    [[ "$(pw_entry "$cfg")" == "python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" the proxy was not registered;"
    [[ "$(pw_servers "$cfg")" == "playwright,serena" ]] || problems+=" other servers changed: $(pw_servers "$cfg");"
    # the second run finds the proxy: nothing to do, so nothing to back up either
    : >"$tmp/claude.log"
    out2="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
    again=( "$cfg".autoos-backup-* )
    [[ "$(pw_writes "$tmp")" == "0" && "$out2" == *"skipped"* && "${#again[@]}" == "2" ]] \
        || problems+=" the second run wrote or made a backup: writes=$(pw_writes "$tmp") backups=${#again[@]};"
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: with no entry a failed backup stops the add with a warning"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"; bin="$(backup_fail_bin)"
    pw_seed "$cfg" -; cp "$cfg" "$tmp/seed.json"
    out="$(PATH="$bin:$PATH" pw_run "$tmp" install_mcp_playwright 2>&1)"
    same=0; cmp -s "$cfg" "$tmp/seed.json" && same=1
    adds="$(grep -c '^mcp add' "$tmp/claude.log" 2>/dev/null)" || adds=0
    partial="$(backup_count "$tmp/home")"
    rm -rf "$tmp" "$bin"
    if [[ "$adds" == "0" && "$same" == "1" && "$partial" == "0" && "$out" == *"could not back up"* ]]; then pass
    else fail "adds=$adds unchanged=$same partial backups=$partial out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: with no config file yet there is nothing to back up and the proxy is still registered"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"
    out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
    calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
    got="$(pw_entry "$tmp/home/.claude.json")"
    left="$(backup_count "$tmp/home")"
    rm -rf "$tmp"
    if [[ "$calls" == "mcp add --scope user playwright -- python3 $ROOT/tools/playwright_mcp_lazy.py" && "$got" == "python3 $ROOT/tools/playwright_mcp_lazy.py" && "$left" == "0" && "$out" != *"could not back up"* ]]; then pass
    else fail "calls=[$calls] entry=[$got] backups=$left out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: a proxy entry from another checkout is replaced with this checkout's path after a backup"; then
    problems=""
    for old in "/old/AutoOS/tools/playwright_mcp_lazy.py" "/old checkout/AutoOS/tools/playwright_mcp_lazy.py"; do
        tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
        pw_seed "$cfg" python3 "$old"; cp "$cfg" "$tmp/seed.json"
        out="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
        calls="$(grep -v '^mcp list$' "$tmp/claude.log" | paste -sd'|' -)"
        backups=( "$cfg".autoos-backup-* )
        if [[ ! -f "${backups[0]}" ]] || ! cmp -s "${backups[0]}" "$tmp/seed.json"; then problems+=" [$old] no byte-identical backup;"; fi
        [[ "$calls" == "mcp remove playwright --scope user|mcp add --scope user playwright -- python3 $ROOT/tools/playwright_mcp_lazy.py" ]] || problems+=" [$old] calls=[$calls];"
        [[ "$(pw_argv_json "$cfg")" == "[\"python3\", \"$ROOT/tools/playwright_mcp_lazy.py\"]" ]] || problems+=" [$old] entry=[$(pw_argv_json "$cfg")];"
        [[ "$out" == *"$old"* && "$out" == *"$ROOT/tools/playwright_mcp_lazy.py"* ]] || problems+=" [$old] the report names no old and new path;"
        [[ "$(pw_servers "$cfg")" == "playwright,serena" ]] || problems+=" [$old] the other servers changed: $(pw_servers "$cfg");"
        [[ ! -s "$tmp/docker.log" ]] || problems+=" [$old] docker was called;"
        : >"$tmp/claude.log"
        out2="$(pw_run "$tmp" install_mcp_playwright 2>&1)"
        again=( "$cfg".autoos-backup-* )
        [[ "$(pw_writes "$tmp")" == "0" && "$out2" == *"skipped"* && "${#again[@]}" == "1" ]] \
            || problems+=" [$old] the second run wrote or made another backup: $(pw_writes "$tmp") ${#again[@]};"
        rm -rf "$tmp"
    done
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: an add that fails after the remove puts the old entry back"; then
    problems=""
    for form in docker npx stale-proxy; do
        tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
        case "$form" in
            docker) old_argv=("${PW_DOCKER_FORM[@]}") ;;
            npx) old_argv=(npx -y @playwright/mcp@0.0.81) ;;
            stale-proxy) old_argv=(python3 "/old checkout/AutoOS/tools/playwright_mcp_lazy.py") ;;
        esac
        pw_seed "$cfg" "${old_argv[@]}"
        out="$(PW_ADD_FAIL="$ROOT/tools/playwright_mcp_lazy.py" pw_run "$tmp" install_mcp_playwright 2>&1)"
        got="$(pw_argv_json "$cfg")"
        want="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "${old_argv[@]}")"
        [[ "$got" == "$want" ]] || problems+=" [$form] entry after the failed add=[$got], want [$want];"
        [[ "$out" == *"put back"* ]] || problems+=" [$form] the rollback was not reported;"
        rm -rf "$tmp"
    done
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "playwright lazy proxy installer: when the put-back fails as well the restore command is printed with its path quoted"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    old="/old checkout/AutoOS/tools/playwright_mcp_lazy.py"
    pw_seed "$cfg" python3 "$old"
    out="$(PW_ADD_FAIL=playwright_mcp_lazy pw_run "$tmp" install_mcp_playwright 2>&1)"
    quoted="$(printf '%q' "$old")"
    rm -rf "$tmp"
    if [[ "$out" == *"restore it with: claude mcp add --scope user playwright -- python3 $quoted (config backup: "* ]]; then pass
    else fail "the restore command does not survive a copy and paste: $(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: a playwright server from another scope is left alone"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; cfg="$tmp/home/.claude.json"
    pw_seed "$cfg" -
    out="$(PW_EXTRA_LIST='playwright: node /somewhere/cli.js - Connected' pw_run "$tmp" install_mcp_playwright 2>&1)"
    writes="$(pw_writes "$tmp")"
    rm -rf "$tmp"
    if [[ "$writes" == "0" && "$out" == *"already registered"* ]]; then pass
    else fail "writes=$writes out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: the user config is CLAUDE_CONFIG_DIR/.claude.json, else HOME/.claude.json, a legacy .config.json first"; then
    tmp="$(mktemp -d)"
    a="$(HOME="$tmp/h"; unset CLAUDE_CONFIG_DIR; claude_user_config_file)"
    b="$(HOME="$tmp/h" CLAUDE_CONFIG_DIR="$tmp/c" claude_user_config_file)"
    mkdir -p "$tmp/h/.claude" "$tmp/c"; : >"$tmp/h/.claude/.config.json"
    c="$(HOME="$tmp/h"; unset CLAUDE_CONFIG_DIR; claude_user_config_file)"
    : >"$tmp/c/.config.json"
    d="$(HOME="$tmp/h" CLAUDE_CONFIG_DIR="$tmp/c" claude_user_config_file)"
    got="$a|$b|$c|$d"
    want="$tmp/h/.claude.json|$tmp/c/.claude.json|$tmp/h/.claude/.config.json|$tmp/c/.config.json"
    rm -rf "$tmp"
    assert_eq "$got" "$want"
fi

if it "playwright lazy proxy installer: the backup is of the file claude reads when CLAUDE_CONFIG_DIR is set"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; mkdir -p "$tmp/cfgdir"
    pw_seed "$tmp/cfgdir/.claude.json" "${PW_DOCKER_FORM[@]}"
    pw_seed "$tmp/home/.claude.json" -
    cp "$tmp/cfgdir/.claude.json" "$tmp/seed.json"
    out="$(PW_CONFIG_DIR="$tmp/cfgdir" PW_CONFIG="$tmp/cfgdir/.claude.json" pw_run "$tmp" install_mcp_playwright 2>&1)"
    backups=( "$tmp/cfgdir/.claude.json".autoos-backup-* )
    homebackups=( "$tmp/home/.claude.json".autoos-backup-* )
    ok=0
    [[ -f "${backups[0]}" ]] && cmp -s "${backups[0]}" "$tmp/seed.json" && [[ ! -e "${homebackups[0]}" ]] \
        && [[ "$(pw_entry "$tmp/cfgdir/.claude.json")" == "python3 $ROOT/tools/playwright_mcp_lazy.py" ]] && ok=1
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "the backup or the replace went to the wrong file: $(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy installer: without claude on PATH it warns and calls nothing"; then
    tmp="$(mktemp -d)"; pw_setup "$tmp"; pw_seed "$tmp/home/.claude.json" -
    out="$(pw_run "$tmp" pw_without_claude 2>&1)"; rc=$?
    calls="$(cat "$tmp/claude.log" 2>/dev/null)"
    rm -rf "$tmp"
    if [[ "$rc" == "0" && -z "$calls" && "$out" == *"claude is not on PATH"* ]]; then pass
    else fail "rc=$rc calls=[$calls] out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "playwright lazy proxy: the agent skills doc (mcp-servers-setup) records the live observation and no longer says it was never observed"; then
    got="$(python3 - "$ROOT/.agents/skills/mcp-servers-setup/SKILL.md" <<'PY'
import re, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    text = re.sub(r"\s+", " ", fh.read())       # the sentence wraps across lines
problems = []
if re.search(r"not yet observed|never (?:been )?observed", text):
    problems.append("it still says the proxy was not observed")
if "observed 2026-09-26 in a real Claude Code session" not in text:
    problems.append("no dated live observation")
if "advertises only `tools`" not in text:
    problems.append("the backend's advertised capabilities are missing")
print("; ".join(problems))
PY
)"
    if [[ -z "$got" ]]; then pass; else fail "$got"; fi
fi

