# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "mcp pins"

if it "mcp pins: lib/ carries no floating package spec"; then
    bad=""
    for f in lib/windows/*.psm1 lib/linux/*.sh; do
        for needle in 'serena-agent' 'graphifyy[mcp]' '@playwright/mcp' '@upstash/context7-mcp' '@modernrelay/omnigraph-mcp'; do
            if grep -qF -- "$needle" "$f"; then bad="$f: $needle"; break 2; fi
        done
    done
    if [[ -z "$bad" ]]; then pass; else fail "floating package name found in $bad"; fi
fi

if it "mcp pins: no pinned version string appears under lib/"; then
    bad="$(python3 - <<'PY'
import glob, json, os, re
h = json.load(open("catalog/agent-harness.json", encoding="utf-8"))
versions = []
for server in h["mcp_servers"].values():
    match = re.search(r"@([0-9][^@]*)$", server["package"]) or re.search(r"==(.+)$", server["package"])
    if match:
        versions.append(match.group(1))
bad = []
for path in glob.glob("lib/**/*", recursive=True):
    if not os.path.isfile(path):
        continue
    text = open(path, encoding="utf-8", errors="replace").read()
    for version in versions:
        if version in text:
            bad.append("%s: %s" % (path, version))
print("; ".join(bad))
PY
)"
    if [[ -z "$bad" ]]; then pass; else fail "pinned version found: $bad"; fi
fi

if it "mcp pins: the client excludeTools list equals serena.excluded_tools"; then
    tmp="$(mktemp -d)"
    spec_file="$tmp/spec.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        has_cmd() { return 1; }
        register_mcp_server() { return 0; }
        ensure_serena_exclusions() { return 0; }
        register_antigravity_mcp_server() { printf '%s' "$2" >"$spec_file"; }
        install_mcp_serena >/dev/null 2>&1
    )
    out="$(python3 - "$spec_file" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
want = json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"]["serena"]["excluded_tools"]
print("match" if spec.get("excludeTools") == want else "mismatch: %r" % spec.get("excludeTools"))
PY
)"
    rm -rf "$tmp"
    assert_eq "$out" "match"
fi

if it "vendored agent profiles reference existing llm profiles"; then
    if python3 - <<'PY'
import json, glob, os, sys
llm = {os.path.basename(p)[:-5] for p in glob.glob("openhands/profiles/*.json")}
# Gateway tier ids are generated at install/start time from the tier spec,
# not vendored here - but agent profiles may reference them.
spec = json.load(open("configuration/openhands/tier-profiles.json", encoding="utf-8"))
llm |= {t["id"] for t in spec["tiers"]}
for path in sorted(glob.glob("openhands/agent-profiles/*.json")):
    ref = json.load(open(path, encoding="utf-8")).get("llm_profile_ref")
    if ref is not None:
        assert ref in llm, f"{path}: llm_profile_ref {ref!r} has no vendored profile"
PY
    then pass; else fail "agent profile references missing llm profile (see above)"; fi
fi

if it "agent harness installers: the OpenHands writer calls the generator and skips role profiles"; then
    body="$(declare -f setup_openhands_config)"
    if [[ "$body" == *'agent_harness.py" openhands'* || "$body" == *'agent_harness.py openhands'* ]] \
        && [[ "$body" == *'_role_profiles'* ]]; then
        pass
    else
        fail "setup_openhands_config does not call agent_harness.py openhands and skip role profiles"
    fi
fi

if it "opencode user config carries global gateway providers without secrets"; then
    # Tier routing must work in EVERY cwd, not just checkouts carrying the
    # repo opencode.jsonc: the user config carries omniroute/litellm
    # providers with {env:} key placeholders (never values).
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - 2>&1 "$scratch/.config/opencode/config.json" <<'PY'
import io, json, re, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
p = d.get("provider", {})
problems = []
omni = p.get("omniroute", {})
if omni.get("options", {}).get("baseURL") != "http://127.0.0.1:20128/v1":
    problems.append("omni-base")
lit = p.get("litellm", {})
if lit.get("options", {}).get("baseURL") != "http://127.0.0.1:4000/v1":
    problems.append("lit-base")
for name, prov in (("omniroute", omni), ("litellm", lit)):
    key = (prov.get("options") or {}).get("apiKey", "")
    if not (key.startswith("{env:") and key.endswith("}")):
        problems.append(name + "-key-not-placeholder")
for m in ("t1-orchestrator", "t1-orchestrator-clean", "t2-worker", "t3-driver-clean", "auto/smart"):
    if m not in (omni.get("models") or {}):
        problems.append("missing:" + m)
if "t2-worker" not in (lit.get("models") or {}):
    problems.append("missing:lit-t2-worker")
if "deepseek" in p:
    problems.append("resurrected:deepseek")
meta = p.get("meta", {})
if not (meta.get("options", {}).get("apiKey", "") == "{env:META_API_KEY}"):
    problems.append("meta-key-not-placeholder")
if not (meta.get("models") or {}).keys() == {"muse-spark-1.3-contributor"}:
    problems.append("meta-models:%s" % sorted((meta.get("models") or {}).keys()))
raw = io.open(sys.argv[1], encoding="utf-8-sig").read()
if re.search(r"sk-[A-Za-z0-9]{10,}", raw):
    problems.append("secret-leak")
print(" ".join(problems))
PY
)"
    # The second run changes nothing, so it backs up nothing (a backup is
    # taken only when a run actually changes the file).
    backups="$(ls "$scratch"/.config/opencode/config.json.autoos-backup-* 2>/dev/null | wc -l)"
    rm -rf "$scratch"
    assert_eq "$report" ""
    assert_eq "$backups" "0"
    fi
fi

# AGENTS.md §4 on an EXISTING user config with the harness really applied
# (AUTOOS_ROOT set, V2 projection on). Measured 2026-09-25: run 1 merged,
# run 2 "merged" again with a fresh backup, run 3 was current - the merge
# wrote non-ASCII names as \uXXXX escapes while the harness wrote them as
# UTF-8, so the second run's byte compare always saw a change.
if it "opencode user config: the second run over an existing config writes nothing (double run)"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{"model": "anthropic/mine"}\n' >"$scratch/.config/opencode/opencode.json"
    printf '{"model": "anthropic/mine"}\n' >"$scratch/.config/opencode/config.json"
    _oc_run() {
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT"
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          opencode_is_v2() { return 0; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config 2>&1 )
    }
    # name, size, mtime_ns and sha256 of every entry: "writes nothing" means
    # not even a byte-identical rewrite, and no new backup file.
    _oc_snapshot() {
        python3 - "$scratch/.config/opencode" <<'PY'
import hashlib, os, sys
root = sys.argv[1]
for name in sorted(os.listdir(root)):
    path = os.path.join(root, name)
    st = os.lstat(path)
    digest = ""
    if os.path.isfile(path) and not os.path.islink(path):
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    print(name, st.st_size, st.st_mtime_ns, digest)
PY
    }
    first="$(_oc_run)"
    before="$(_oc_snapshot)"
    sleep 1
    second="$(_oc_run)"
    after="$(_oc_snapshot)"
    rm -rf "$scratch"
    ok=1
    [[ "$first" == *"agent-harness opencode: updated"* ]] || { ok=0; echo "harness never ran (the precondition): $first" >&2; }
    [[ "$before" == "$after" ]] || { ok=0; printf 'second run changed files:\n%s\n' "$(diff <(printf '%s\n' "$before") <(printf '%s\n' "$after"))" >&2; }
    [[ "$second" == *"merged"* || "$second" == *"written"* ]] && { ok=0; echo "second run reports a write: $second" >&2; }
    [[ "$(grep -c 'already current' <<<"$second")" == 2 ]] || { ok=0; echo "second run not current for both files: $second" >&2; }
    if (( ok )); then pass; else fail "setup_opencode_config needs two runs to converge"; fi
    fi
fi

if it "agent harness installers: the OpenCode writer calls the generator"; then
    body="$(declare -f setup_opencode_config)"
    hands="$(declare -f setup_openhands_config)"
    if { [[ "$body" == *'agent_harness.py" opencode'* ]] || [[ "$body" == *'agent_harness.py opencode'* ]]; } \
        && declare -F autoos_skills_source >/dev/null \
        && [[ "$body" == *'autoos_skills_source'* ]] \
        && [[ "$hands" == *'autoos_skills_source'* ]]; then
        pass
    else
        fail "setup_opencode_config does not call agent_harness.py opencode and use autoos_skills_source"
    fi
fi

if it "agent harness: the generator's unit tests pass"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_agent_harness.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

if it "playwright lazy proxy: the stdio proxy's unit tests pass (fake backend, no docker)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_playwright_mcp_lazy.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

if it "serena memory tools off from harness field (serena)"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = io.open("opencode.jsonc", encoding="utf-8").read()
text = re.sub(r"(?m)^\s*//.*$", "", text)
oc = json.loads(text)
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
mem = h["mcp_servers"]["serena"]["memory_tools"]
tools = oc.get("tools", {})
want = {"serena_" + t: False for t in mem}
problems = []
if tools != want:
    problems.append("tools-mismatch:%s" % sorted(tools))
body = io.open("lib/linux/install.sh", encoding="utf-8").read()
if "memory_tools" not in body:
    problems.append("writer-no-harness-field")
for t in mem:
    if "'serena_%s'" % t in body or '"serena_%s"' % t in body:
        problems.append("writer-literal:" + t)
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
    # Functional: the writer emits the tools block from the harness field.
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report2="$(python3 - "$scratch/.config/opencode/config.json" <<'PY'
import json, io, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
mem = h["mcp_servers"]["serena"]["memory_tools"]
tools = d.get("tools", {})
want = {"serena_" + t: False for t in mem}
print("ok" if tools == want else "mismatch:%s" % sorted(tools))
PY
)"
    rm -rf "$scratch"
    assert_eq "$report2" "ok"
    fi
fi

if it "zed default_model converges litellm to omniroute (zed routing)"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{"agent":{"default_model":{"provider":"autoos-litellm","model":"t3-driver-paid"}}}' >"$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="k1" AUTOOS_LITELLM_API_KEY="k2" route_zed_to_proxy >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
dm = cfg.get("agent", {}).get("default_model", {})
print("%s|%s" % (dm.get("provider"), dm.get("model")))
PY
)"
    # A default already on omniroute must survive untouched.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.config/zed"
    printf '{"agent":{"default_model":{"provider":"autoos-omniroute","model":"t2-worker"}}}' >"$scratch2/.config/zed/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="k1" AUTOOS_LITELLM_API_KEY="k2" route_zed_to_proxy >/dev/null 2>&1 )
    report2="$(python3 - "$scratch2/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
dm = cfg.get("agent", {}).get("default_model", {})
print("%s|%s" % (dm.get("provider"), dm.get("model")))
PY
)"
    rm -rf "$scratch" "$scratch2"
    assert_eq "$report" "autoos-omniroute|t1-orchestrator"
    assert_eq "$report2" "autoos-omniroute|t2-worker"
fi

