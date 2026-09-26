# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Registry model reads (A5d) ─────────────────────────────────────
describe "registry model reads"

if it "registry model reads come from ai-registry.json models"; then
    # Every model read in the installers targets catalog/ai-registry.json
    # `models`; no read may still point at catalog/llm-models.json.
    bad="$(grep -n 'llm-models\.json' lib/linux/install.sh lib/windows/AutoOS.Install.psm1 || true)"
    if [[ -n "$bad" ]]; then
        fail "model reads still on llm-models.json: $bad"
    elif ! grep -q 'ai-registry\.json' lib/linux/install.sh; then
        fail "lib/linux/install.sh has no ai-registry.json read"
    elif ! grep -q 'ai-registry\.json' lib/windows/AutoOS.Install.psm1; then
        fail "lib/windows/AutoOS.Install.psm1 has no ai-registry.json read"
    else
        pass
    fi
fi

if it "registry model reads project a fixture with no llm-models.json present"; then
    if python3 - <<'PY'
import importlib.util, json, os, tempfile

def load_registry_tool():
    # The real read path: the installers import tools/registry.py by path
    # (as tools/audit-router.py does) and call legacy_models(); this test
    # loads the same module the same way, never a copy of its logic.
    spec = importlib.util.spec_from_file_location("autoos_registry", "tools/registry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
registry = load_registry_tool()
fixture = {"models": {
    "muse-spark": {
        "id": "muse-spark", "display_name": "Muse Spark 1.3 Contributor",
        "direct": {"provider": "meta", "base_url": "https://api.meta.ai/v1",
                   "model": "openai/muse-spark-1.3-contributor",
                   "npm": "@ai-sdk/openai", "reasoning_effort": "high"},
        "context_advertised": 1048576, "output_max": 131072,
        "reasoning": True, "price_in": 1e-07, "price_out": 2e-07,
        "price_cache_read": 2e-09, "default_for": "muse_key"},
    "openrouter-nemotron-ultra": {
        "id": "openrouter-nemotron-ultra", "display_name": "Nemotron 3 Ultra (Free)",
        "direct": {"provider": "openrouter",
                   "model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
        "context_advertised": 1000000, "output_max": 32768,
        "reasoning": True, "price_in": 0, "price_out": 0,
        "paid_price_in": 6e-07, "paid_price_out": 2.4e-06,
        "price_cache_read": 1.2e-07},
    "ollama-qwen2.5-coder": {
        "id": "ollama-qwen2.5-coder", "display_name": "Qwen 2.5 Coder 7B (Local)",
        "direct": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                   "model": "ollama/qwen2.5-coder:7b",
                   "npm": "@ai-sdk/openai-compatible"},
        "context_advertised": 32768, "output_max": 8192,
        "reasoning": False, "price_in": 0, "price_out": 0,
        "default_for": "fallback"},
    "command-a-03-2025": {
        "id": "command-a-03-2025", "display_name": "Command A",
        "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "price_in": 0.0, "price_out": 0.0},
}}
tmp = tempfile.mkdtemp(prefix="a5d-")
os.makedirs(os.path.join(tmp, "catalog"), exist_ok=True)
with open(os.path.join(tmp, "catalog", "ai-registry.json"), "w", encoding="utf-8") as fh:
    json.dump(fixture, fh)
assert not os.path.exists(os.path.join(tmp, "catalog", "llm-models.json")), \
    "fixture must run with no llm-models.json present"
# The real read path: ai-registry.json from disk, projected by the one
# helper the installers call -- never a copy of its logic, and a model
# without a direct block is skipped, never listed.
with open(os.path.join(tmp, "catalog", "ai-registry.json"), encoding="utf-8") as fh:
    by_id = {m["id"]: m for m in registry.legacy_models(json.load(fh))}
assert set(by_id) == {"muse-spark", "openrouter-nemotron-ultra", "ollama-qwen2.5-coder"}, \
    set(by_id)
# The installers call the one helper; no local copies remain.
for path in ("lib/linux/install.sh", "lib/windows/AutoOS.Install.psm1"):
    src = open(path, encoding="utf-8").read()
    assert "legacy_models" in src, path
    assert "def _legacy_model" not in src, path
muse = by_id["muse-spark"]
assert muse["name"] == "Muse Spark 1.3 Contributor", muse
assert (muse["context"], muse["output"]) == (1048576, 131072), muse
assert muse["direct"]["provider"] == "meta", muse
assert "openrouter_id" not in muse, muse
assert muse.get("default_for") == "muse_key", muse
ultra = by_id["openrouter-nemotron-ultra"]
assert ultra["openrouter_id"] == "nvidia/nemotron-3-ultra-550b-a55b:free", ultra
assert "direct" not in ultra, ultra
assert (ultra["paid_input_price"], ultra["paid_output_price"]) == (6e-07, 2.4e-06), ultra
assert ultra["cache_read_price"] == 1.2e-07, ultra
local = by_id["ollama-qwen2.5-coder"]
assert local["direct"]["model"] == "ollama/qwen2.5-coder:7b", local
assert (local["context"], local["output"]) == (32768, 8192), local
assert local.get("default_for") == "fallback", local
assert "reasoning" not in local, local
# The projection keeps the output identical on the real catalogs too.
llm = {m["id"]: m for m in
       json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]}
with open("catalog/ai-registry.json", encoding="utf-8") as _rf:
    new = {m["id"]: m for m in registry.legacy_models(json.load(_rf))}
assert set(new) == set(llm), (set(new) ^ set(llm))
for mid, old in sorted(llm.items()):
    assert new[mid] == old, mid
PY
    then pass; else fail "registry fixture does not project to the legacy model shape (see above)"; fi
fi

if it "resolve_ollama_base_url: OLLAMA_BASE_URL wins and is normalised to /v1"; then
    got="$(curl() { return 1; }; OLLAMA_BASE_URL="http://gpu-box:11434/" resolve_ollama_base_url)"
    assert_eq "$got" "http://gpu-box:11434/v1"
fi

if it "resolve_ollama_base_url: host.docker.internal is chosen only when Ollama answers there"; then
    got="$(unset OLLAMA_BASE_URL; curl() { [[ "$*" == *host.docker.internal:11434/api/version* ]]; }; resolve_ollama_base_url)"
    assert_eq "$got" "http://host.docker.internal:11434/v1"
fi

if it "resolve_ollama_base_url: a native Linux host (no host.docker.internal) keeps the catalog default"; then
    # Empty output = "use the catalog's 127.0.0.1", which is what a host-run
    # agent-canvas (agent-server via uvx on the host) can reach.
    got="$(unset OLLAMA_BASE_URL; curl() { return 6; }; resolve_ollama_base_url)"
    assert_eq "$got" ""
fi

if it "setup_openhands_config writes the resolved Ollama address into the profile and the keyless default"; then
    # Skipped in ambient-key environments: the repo's own api-keys.yml (read
    # as fallback when no env key exists) plus the WSL2 host env leak a
    # gateway key in, so the "keyless" default legitimately becomes the
    # gateway tier here. The hermetic key-behavior is covered by the
    # dedicated "gateway tier profiles with a key, none without" test below,
    # which unsets the gateway keys too.
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_KEYS_FILE="$tmp/nonexistent-keys.yml" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
d = sys.argv[1]
p = json.load(open(os.path.join(d, "profiles", "ollama-qwen2.5-coder.json"), encoding="utf-8"))
s = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))
print(p["base_url"], p["model"], s["agent_settings"]["llm"]["base_url"], s["agent_settings"]["llm"]["model"], "api_key" in p and p["api_key"] is None, any("timeout" in v for v in s["agent_settings"]["mcp_config"].values()))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "http://ollama:11434 ollama_chat/qwen2.5-coder:7b http://ollama:11434 ollama_chat/qwen2.5-coder:7b True False"
    # NOTE: keyless expectation vs ambient machine key — under WSL2 bash the
    # surrounding env leaks in (see the --wsl suite note).
fi

if it "setup_openhands_config defaults to the gateway with a key"; then
    # Same hermetic shape as the Ollama test. Env key wins over the repo's
    # real api-keys.yml (the suite never asserts on live system state).
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_OMNIROUTE_KEY="test-gw-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
s = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))
llm = s["agent_settings"]["llm"]
print(llm["model"], llm["base_url"], llm["api_key"], llm["reasoning_effort"])
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:20128/v1 test-gw-key high"
fi

if it "setup_openhands_config writes gateway tier profiles with a key, none without"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_OMNIROUTE_KEY="test-omni-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
t1 = json.load(open(os.path.join(d, "profiles", "omniroute-t1-orchestrator.json"), encoding="utf-8"))
t3 = json.load(open(os.path.join(d, "profiles", "omniroute-t3-driver.json"), encoding="utf-8"))
lp = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))["llm_profiles"]
print(t1["model"], t1["base_url"], t1["api_key"], t1["reasoning_effort"],
      t3["model"], t3["reasoning_effort"], t3["enable_encrypted_reasoning"],
      lp["active"], "omniroute-t1-orchestrator" in lp["profiles"])
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:20128/v1 test-omni-key high openai/t3-driver none False omniroute-t1-orchestrator True"
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" setup_openhands_config >/dev/null 2>&1
        test -e "$tmp/.openhands/profiles/omniroute-t1-orchestrator.json" && echo PRESENT || echo ABSENT
    )"
    rm -rf "$tmp"
    assert_eq "$out" "ABSENT"
fi

if it "setup_openhands_config writes litellm fallback tiers with a litellm key only"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" LITELLM_MASTER_KEY="test-lit-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
t1 = json.load(open(os.path.join(d, "profiles", "litellm-t1-orchestrator.json"), encoding="utf-8"))
lp = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))["llm_profiles"]
print(t1["model"], t1["base_url"], t1["api_key"], lp["active"],
      os.path.exists(os.path.join(d, "profiles", "omniroute-t1-orchestrator.json")))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:4000/v1 test-lit-key litellm-t1-orchestrator False"
fi

if it "tier profiles come from the spec, installer and tool agree"; then
    report="$(python3 - <<'PY'
import json
spec = json.load(open("configuration/openhands/tier-profiles.json", encoding="utf-8"))
print("%s|%s|%s|%s" % (
    ",".join(t["id"] for t in spec["tiers"]),
    spec["gateway_base_url"],
    spec["litellm_base_url"],
    ",".join(t["model"] for t in spec["tiers"])))
PY
)"
    # Pinned on purpose: the order is the OpenHands push priority (the app
    # keeps 10 profiles), so a reorder must be a deliberate, reviewed edit.
    assert_eq "$report" "omniroute-t1-orchestrator,omniroute-t2-worker,omniroute-t3-driver,omniroute-t2-orchestrator,omniroute-t2-worker-clean,omniroute-t3-driver-clean,omniroute-t4-rag,omniroute-opus-4-6,omniroute-gemini-3.8-flash,omniroute-t2-worker-free-only,omniroute-deepseek-v4.1-flash,omniroute-t3-driver-free-only,omniroute-t1-orchestrator-clean,omniroute-spark-1.3-contributor,openrouter-muse-spark-1.3-contributor,litellm-t1-orchestrator,litellm-t2-worker,litellm-t3-driver,litellm-t2-worker-free-only,litellm-t3-driver-free-only,litellm-t1-orchestrator-free-only,omniroute-t1-orchestrator-free-only|http://host.docker.internal:20128/v1|http://host.docker.internal:4000/v1|openai/t1-orchestrator,openai/t2-worker,openai/t3-driver,openai/t2-orchestrator,openai/t2-worker-clean,openai/t3-driver-clean,openai/t4-rag,openai/opus-4-6,openai/gemini-3.8-flash,openai/t2-worker-free-only,openai/deepseek-v4.1-flash,openai/t3-driver-free-only,openai/t1-orchestrator-clean,openai/spark-1.3-contributor,openrouter/meta/muse-spark-1.3-contributor,openai/t1-orchestrator,openai/t2-worker,openai/t3-driver,openai/t2-worker-free-only,openai/t3-driver-free-only,openai/t1-orchestrator-free-only,openai/t1-orchestrator-free-only"
    # The embedded installer must read the spec, never inline tiers.
    grep -q 'tier-profiles.json' lib/linux/install.sh || { fail "installer does not read the tier spec"; }
    # Generator round-trip with fixture keys (env hidden: the suite never
    # asserts on live system state).
    tmp="$(mktemp -d)"; printf 'omniroute: test-omni-key\nopenrouter: test-or-key\n' >"$tmp/api-keys.yml"
    printf 'LITELLM_MASTER_KEY=test-lit-key\n' >"$tmp/.env"
    out="$( ( unset AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY OPENROUTER_API_KEY; python3 tools/sync-openhands-profiles.py --openhands-dir "$tmp" --keys-file "$tmp/api-keys.yml" --litellm-env "$tmp/.env" ) 2>&1)"
    written="$(printf '%s' "$out" | grep -c written)"
    # A direct-provider tier (gateway: openrouter) must take its OWN key and
    # endpoint, not the gateway's - that is the effort-ladder surface.
    direct="$(python3 - "$tmp/profiles/openrouter-muse-spark-1.3-contributor.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except OSError:
    print("missing")
else:
    print("%s|%s|%s" % (d.get("api_key"), d.get("base_url"), d.get("model")))
PY
)"
    rm -rf "$tmp"
    assert_eq "$written" "22"
    assert_eq "$direct" "test-or-key|https://openrouter.ai/api/v1|openrouter/meta/muse-spark-1.3-contributor"
fi

if it "start-stack regenerates tier profiles on openhands start"; then
    ok=1
    grep -q 'sync-openhands-profiles' configuration/start-stack.ps1 || { ok=0; echo "ps1 never syncs" >&2; }
    grep -q 'sync-openhands-profiles' configuration/start-stack.sh || { ok=0; echo "sh never syncs" >&2; }
    if (( ok )); then pass; else fail "tier profiles go stale between installs"; fi
fi

