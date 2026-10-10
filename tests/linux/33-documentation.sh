# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Documentation ──────────────────────────────────────────────────────────
describe "documentation"

if it "every relative link in the docs resolves"; then
    out="$(python3 tests/check-links.py . 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "every docs page is linked from the index"; then
    missing=""
    for f in docs/*.md; do
        base="$(basename "$f")"
        [[ "$base" == "README.md" ]] && continue
        grep -q "$base" docs/README.md || missing+="$base "
    done
    assert_eq "$missing" ""
fi

if it "install_zed announces in dry run"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_zed ) 2>&1)"
    if [[ "$out" == *"would install Zed"* ]]; then pass
    else fail "no dry-run announcement"; fi
fi

# catalog/ide-models.json is read at install time by the Zed, OpenCode and
# OpenHands writers. Missing or malformed, each must say so in ONE line that
# names the file - no traceback, no half-written config, no stray backup.
if it "a missing or malformed catalog/ide-models.json stops the IDE writers with one clear line (ide-models)"; then
    d="$(mktemp -d)"
    printf '{ "models": [ ' >"$d/truncated.json"
    printf '{"models": [{"id": "l1-orchestrator"}]}' >"$d/no-fields.json"
    ok=1
    for catfile in "$d/missing.json" "$d/truncated.json" "$d/no-fields.json"; do
        home="$d/home-$(basename "$catfile" .json)"
        mkdir -p "$home/.config/zed" "$home/.config/opencode"
        printf '{"theme":"mine"}' >"$home/.config/zed/settings.json"
        printf '{"model": "anthropic/mine"}\n' >"$home/.config/opencode/opencode.json"
        out="$( ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT" AUTOOS_IDE_MODELS_FILE="$catfile"
                  unset OPENROUTER_API_KEY META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY
                  curl() { return 6; }
                  opencode_is_v2() { return 0; }
                  route_zed_to_proxy; echo "zed_rc=$?"
                  setup_opencode_config; echo "oc_rc=$?" ) 2>&1)"
        [[ "$out" == *"zed_rc=1"* ]] || { ok=0; echo "$catfile: zed writer did not fail: $out" >&2; }
        [[ "$(grep -c "$catfile" <<<"$out")" -ge 2 ]] || { ok=0; echo "$catfile: path not named by both writers: $out" >&2; }
        [[ "$out" == *Traceback* ]] && { ok=0; echo "$catfile: traceback: $out" >&2; }
        [[ "$(cat "$home/.config/zed/settings.json")" == '{"theme":"mine"}' ]] || { ok=0; echo "$catfile: zed settings changed" >&2; }
        [[ "$(cat "$home/.config/opencode/opencode.json")" == '{"model": "anthropic/mine"}' ]] || { ok=0; echo "$catfile: opencode config changed" >&2; }
        [[ -e "$home/.config/opencode/config.json" ]] && { ok=0; echo "$catfile: config.json written" >&2; }
        n="$(find "$home" -name '*.autoos-backup-*' | wc -l)"
        [[ "$n" == 0 ]] || { ok=0; echo "$catfile: $n stray backups" >&2; }
    done
    # OpenHands: the default LLM just gets no windows; the rest still runs.
    home="$d/home-openhands"; mkdir -p "$home"
    printf 'omniroute: REPLACE_ME\n' >"$d/keys.yml"
    out="$( ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_KEYS_FILE="$d/keys.yml" AUTOOS_OMNIROUTE_KEY=sk-fake-gw \
              AUTOOS_IDE_MODELS_FILE="$d/truncated.json"
              unset OPENROUTER_API_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY META_API_KEY
              curl() { return 6; }
              setup_openhands_config ) 2>&1)"
    llm="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); l=d.get("agent_settings", d).get("llm", {}); print(l.get("model"), l.get("max_input_tokens"))' "$home/.openhands/settings.json" 2>&1)"
    [[ "$out" == *"$d/truncated.json"* ]] || { ok=0; echo "openhands: path not named: $out" >&2; }
    [[ "$out" == *Traceback* ]] && { ok=0; echo "openhands: traceback: $out" >&2; }
    [[ "$llm" == "openai/l1-orchestrator None" ]] || { ok=0; echo "openhands default: $llm" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a broken model catalog is not reported cleanly"; fi
fi

if it "zed routing reports a failed merge instead of success"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    # A directory where the file belongs makes the python merge fail.
    rm -rf "$scratch/.config/zed/settings.json"
    mkdir "$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; route_zed_to_proxy >/dev/null 2>&1 ); rc=$?
    rm -rf "$scratch"
    assert_eq "$rc" "1"
fi

if it "existing nvim config keeps working and gains sidekick"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; install_lazyvim >/dev/null 2>&1 )
    n="$(grep -c 'lazyvim.plugins.extras.ai.sidekick' "$scratch/.config/nvim/lazyvim.json" 2>/dev/null || true)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
fi

if it "sidekick enabling announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; enable_sidekick_extra ) 2>&1)"
    if [[ "$out" == *"would enable"* && ! -e "$scratch/.config/nvim/lazyvim.json" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
    rm -rf "$scratch"
fi

# The stubs sit on a PATH of only "$stub:/usr/bin:/bin" and SYS_HOME is a
# temp dir, so a real uv/pipx/litellm on the machine running the suite can
# neither satisfy nor short-circuit these cases.
if it "litellm installer prefers uv tool install (PEP 668 safe)"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\necho "$@" >"$0.called"\n' >"$stub/uv"; chmod +x "$stub/uv"
    printf '#!/bin/sh\ntouch "$0.called"\n' >"$stub/pipx"; chmod +x "$stub/pipx"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 )
    if [[ "$(cat "$stub/uv.called" 2>/dev/null)" == "tool install litellm[proxy]" && ! -f "$stub/pipx.called" ]]; then
        rm -rf "$stub"; pass
    else rm -rf "$stub"; fail "uv tool install was not the chosen path"; fi
fi

if it "litellm installer falls back to pipx without uv"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\ntouch "$0.called"\n' >"$stub/pipx"; chmod +x "$stub/pipx"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 )
    if [[ -f "$stub/pipx.called" ]]; then rm -rf "$stub"; pass
    else rm -rf "$stub"; fail "pipx stub was not invoked"; fi
fi

if it "litellm installer reports a failed install instead of success"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\nexit 1\n' >"$stub/uv"; chmod +x "$stub/uv"
    rc=0
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 ) || rc=$?
    rm -rf "$stub"
    [[ $rc -ne 0 ]] && pass || fail "a failed install returned 0"
fi

if it "a present litellm is detected, so a second run skips"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\nexit 0\n' >"$stub/litellm"; chmod +x "$stub/litellm"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub"; custom_is_installed litellm ) && ok=1 || ok=0
    rm -rf "$stub"
    [[ $ok -eq 1 ]] && pass || fail "custom_is_installed litellm said missing"
fi

if it "env template carries placeholders only"; then
    bad="$(grep -vE '^(#|$|[A-Z_]+=REPLACE_WITH_[A-Z_]+$)' configuration/litellm/.env.example || true)"
    assert_eq "$bad" ""
fi

if it "api-keys example carries placeholders only"; then
    bad="$(grep -vE '^(#|$)' configuration/api-keys.example.yml |
        grep -vE '^[A-Za-z_]+:[[:space:]]*REPLACE_WITH_[A-Z_]+$' || true)"
    assert_eq "$bad" ""
fi

if it "opencode.jsonc is valid JSON once comments are stripped"; then
    # Every client loads this file, and a single unbalanced brace makes ALL of
    # them fall back to defaults while the suite's other assertions (which read
    # it as text) stay green. Measured 2026-09-23: a provider block was added
    # without its closing brace and nothing failed.
    out="$(python3 - <<'PY'
import json, re, sys
raw = open("opencode.jsonc", encoding="utf-8").read()
body = re.sub(r"(?m)^\s*//.*$", "", raw)
try:
    doc = json.loads(body)
except Exception as exc:
    sys.exit(f"parse error: {exc}")
if not doc.get("model"):
    sys.exit("no default model")
print("ok")
PY
)"
    assert_eq "$out" "ok"
fi

if it "the router declarations do not drift from each other"; then
    # tools/audit-router.py --offline compares combos.json against opencode.jsonc,
    # both Zed writers and the OpenHands tier profiles, and rejects any
    # combo-bypassing direct ref. Live gateway/proxy probes are the operator
    # path (no --offline); CI stays deterministic.
    out="$(python3 tools/audit-router.py --offline 2>&1)"; rc=$?
    assert_ok "$rc"
    assert_not_contains "$out" "DRIFT"
fi

if it "the IDE model lists match catalog/ide-models.json (sync-ide-models --check)"; then
    # catalog/ide-models.json is the single source for the gateway model list
    # (ids, names, windows, membership). opencode.jsonc and the OpenHands
    # tier spec + config.toml carry generated copies; --check exits 1 with a
    # diff when one drifted (fix: python3 tools/sync-ide-models.py).
    out="$(python3 tools/sync-ide-models.py --check 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "rc=$rc $(printf '%s\n' "$out" | tail -n 20)"; fi
fi

if it "the IDE model sync tool's unit tests pass (sync-ide-models)"; then
    out="$(python3 tests/test_sync_ide_models.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/registry_loader.py: the one importlib-by-path loader for tools/
# registry.py that the registry-consuming tools share (REVFIX).
if it "registry loader: the shared by-path loader and its consumers (unit tests)"; then
    out="$(python3 tests/test_registry_loader.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "every leg of a combo carries a provider prefix"; then
    bad="$(python3 - 2>&1 <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
print(" ".join(f"{c['name']}:{m}" for c in d["combos"] for m in c["models"] if "/" not in m))
PY
)"
    assert_eq "$bad" ""
fi

if it "apply sets the resilience deadline and the fast-skip breaker"; then
    ok=1
    for f in configuration/omniroute/apply.sh configuration/omniroute/apply.ps1; do
        grep -q 'maxWaitMs' "$f" || { ok=0; echo "$f never sets maxWaitMs" >&2; }
        grep -q '180000' "$f" || { ok=0; echo "$f does not use the reasoning-safe value" >&2; }
        grep -q 'providerBreaker' "$f" || { ok=0; echo "$f never sets the fast-skip breaker" >&2; }
        grep -qE 'failureThreshold.?[:=].?2|BREAKER_THRESHOLD=2' "$f" \
            || { ok=0; echo "$f does not use the 2-failure threshold" >&2; }
    done
    # A route that fails closed is named in combos.json's "omitted" and must
    # never appear as a combo (l1-orchestrator-clean since DSMAX 2026-09-27).
    # deepseek-v4.1-flash left that set on DSBACK 2026-09-28, when the operator
    # top-up made providers.deepseek available again. The set is read from the
    # rendered file, never re-pinned here (lesson PROVPIN).
    omitted="$(python3 - 2>&1 <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
by = {c["name"] for c in d["combos"]}
omitted = set(d.get("omitted", []))
print(",".join(sorted(g for g in omitted if g in by)))
PY
)"
    assert_eq "$omitted" ""
    if (( ok )); then pass; else fail "the resilience settings are not applied"; fi
fi

if it "omniroute apply.sh probe streams, so a slow first byte cannot time it out"; then
    # Muse's first byte outlives a whole-body read (measured 2026-09-28:
    # 3-32 s at minimal/low/medium, the high leg past the gateway's 30 s
    # response-start ceiling). The probe asks which leg answered, and the
    # first SSE chunk already names it.
    f="configuration/omniroute/apply.sh"
    probe="$(sed -n '/^# ─── Probe:/,/^echo "Done/p' "$f")"
    ok=1
    grep -q '"stream": True' <<<"$probe" \
        || { ok=0; echo "the probe request is not streaming" >&2; }
    grep -q 'data:' <<<"$probe" \
        || { ok=0; echo "the probe never reads an SSE chunk" >&2; }
    grep -q 'served = json.loads(resp.read()' <<<"$probe" \
        && { ok=0; echo "the probe still waits for the whole non-stream body" >&2; }
    if (( ok )); then pass; else fail "omniroute apply.sh probe streaming"; fi
fi

if it "combos.json carries no phantom legs (probe-falsified refs stay out)"; then
    # Regression gate for the 2026-09-22 finding: three legs shipped that the
    # gateway 400s on ("not available in the active live catalog"), which only
    # surfaces at chat time — simulate resolves them. Pin the falsified refs.
    report="$(python3 - 2>&1 <<'PY'
import json
banned = {
    "openrouter/gemini-3.8-flash": "bare openrouter gemini is an alias, not a provider ref",
    "deepseek/deepseek-v4.1-flash": "deepseek direct has no v4.1-flash in the live catalog",
    "gemini/gemini-3.7-flash": "3.7-flash is not in any combo (tiny free input quota)",
}
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
problems = []
for c in d["combos"]:
    for m in c["models"]:
        if m in banned:
            problems.append(f"{c['name']}:{m}")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "combos.json parses and carries the servable tiers"; then
    report="$(python3 - 2>&1 <<'PY'
import json, sys
sys.path.insert(0, "tools")
import registry
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
names = [c["name"] for c in d["combos"]]
problems = []
# Which tiers are servable / fail closed is provider state, derived from the
# registry render instead of re-pinned as a literal list here (lesson PROVPIN
# 2026-09-27: the MUSEAPI meta_api flip went servable and this pin went stale).
reg = json.load(open("catalog/ai-registry.json", encoding="utf-8"))
rendered = registry.render_omniroute(reg)
expected_names = [c["name"] for c in rendered["combos"]]
expected_omitted = [o for o in rendered.get("omitted", [])]
if not expected_names:
    problems.append("render-empty")
if names != expected_names:
    problems.append("names")
if sorted(d.get("omitted", [])) != sorted(expected_omitted):
    problems.append("omitted-set")
# "retired" is the one home of the ids a rename left behind: apply prunes
# them from the store, so a retired id must never also be a current combo.
retired = d.get("retired")
if not isinstance(retired, list) or not retired:
    problems.append("retired-empty")
else:
    for r in retired:
        if not isinstance(r, str) or not r:
            problems.append("retired-bad:" + repr(r))
        elif r in names:
            problems.append("retired:" + r)
for c in d["combos"]:
    if not c["models"]:
        problems.append(c["name"] + ":empty")
    for m in c["models"]:
        if "/" not in m:
            problems.append(c["name"] + ":" + m)
by = {c["name"]: c["models"] for c in d["combos"]}
omitted = set(d.get("omitted", []))
# A route that declares legs but has none the gateway can serve fails closed:
# never a combo, always named in "omitted" (l1-orchestrator-clean since DSMAX
# 2026-09-27; deepseek-v4.1-flash left this set on DSBACK 2026-09-28 when the
# operator top-up made providers.deepseek available again — and every future
# flip — derived, so no per-route list to keep current).
for gone in expected_omitted:
    if gone in by:
        problems.append(gone + "-should-be-omitted")
    if gone not in omitted:
        problems.append(gone + "-not-in-omitted")
# Every servable route is offered (l1-orchestrator and l1-orchestrator-free-only
# since T1FREE, spark-1.3-contributor and l1-orchestrator-paid since MUSEAPI).
for kept in expected_names:
    if kept not in by:
        problems.append(kept + "-should-be-in-combos")
# Plain muse-spark-1.3 is BLOCKED (operator 2026-09-21): no combo may carry a
# plain spark leg. The contributor variant is a different model id and is what
# the meta_api legs serve (MUSEAPI 2026-09-27).
import re as _re2
if _re2.search(r"muse-spark-1\.3(?!-contributor)", " ".join(m for c in d["combos"] for m in c["models"])):
    problems.append("plain-spark-blocked")
# *-clean = paid legs only: no free pool may train on private prompts.
# Free = contributor-free, groq / cerebras / sambanova / gemini hosts,
# mistral-code + qwen free pools. -contributor (trains by contract) is
# banned in every clean combo (a route that fails closed is not a combo at
# all, so it needs no name listed here).
# Direct-key legs (mistral-small, deepseek, openrouter paid, zen paid)
# bill past the pool on the same key, so they stay.
import re
free = re.compile(r"contributor-free|^(groq|cerebras|sambanova|gemini)/|mistral/mistral-code|/qwen")
trains = re.compile(r"-contributor$")
clean = [n for n in names if n.endswith("-clean")]
for n in clean:
    bad = [m for m in by[n] if free.search(m)]
    if bad:
        problems.append(n + "-free:" + ",".join(bad))
for n in clean:
    bad = [m for m in by[n] if trains.search(m)]
    if bad:
        problems.append(n + "-trains:" + ",".join(bad))
# *-free-only = zero paid/keyed legs (zen contributor-free counts as free).
# SHARDB: expectation moved by 7eff6020 (FREEWIRE wired probe-passed openrouter :free legs into free-only routes): a :free suffixed id is free.
paid = re.compile(r"cheaperinference|openrouter|^(deepseek|mistral)/|opencode-zen/(?!.*-free)")
for n in (n for n in names if n.endswith("-free-only")):
    bad = [m for m in by[n] if paid.search(m) and not m.endswith(":free")]
    if bad:
        problems.append(n + "-paid:" + ",".join(bad))
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "apply handles the Cloudflare UA and stays openrouter-first"; then
    ok=1
    # The UA quirk now lives once, in the registry; apply.sh only passes
    # provider_data through. Task A5a moved the read to
    # catalog/ai-registry.json.
    grep -q 'ai-registry\.json' configuration/omniroute/apply.sh || ok=0
    grep -q 'muse-code' configuration/omniroute/apply.sh && ok=0
    grep -q 'provider-specific-data' configuration/omniroute/apply.sh || ok=0
    ua="$(python3 -c "import json; p=json.load(open('catalog/ai-registry.json', encoding='utf-8'))['providers']; print(' '.join((p[n]['provider_data'] or {}).get('customUserAgent','') for n in ('groq','cerebras')))")"
    [[ "$ua" == "curl/8.7.1 curl/8.7.1" ]] || { ok=0; echo "registry UA quirk wrong: $ua" >&2; }
    if (( ok )); then pass; else fail "apply.sh is missing the provider quirks"; fi
fi

if it "provider registry drives apply, mirror and the tier maps"; then
    # catalog/ai-registry.json is the one map; the helper asserts every
    # consumer's in-memory map equals it, so a hand-edited copy or a half-done
    # registry edit fails here instead of routing a provider to a wrong name.
    out="$(python3 tests/helpers/check-provider-registry.py 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "apply --dry-run registers nothing and starts nothing"; then
    # combos.json must be byte-identical afterwards; the dry run must announce.
    before="$(cat configuration/omniroute/combos.json)"
    # Hermetic: a dead gateway port and no key file - the dry run must never
    # read the live gateway or the machine's keys (it now also lists the store
    # for the retired-combo prune).
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/nonexistent/api-keys.yml \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    assert_contains "$out" "dry run"
    # Every registry provider with an omniroute_id is announced (meta and the
    # client key carry none and are skipped), so the map is proven live.
    for id in gemini cloudflare-ai huggingface opencode-zen cheaperinference sambanova cohere; do
        assert_contains "$out" "$id"
    done
    after="$(cat configuration/omniroute/combos.json)"
    assert_eq "$after" "$before"
fi

# api-keys.example.yml says "fill in what you have", so a copied file keeps
# REPLACE_WITH_* for the rest. Those must never be registered as keys: once
# registered, "already registered" would also shadow the real key forever.
# D657-D2 (AO-DENYLEGS D2, 2026-10-08) moved the placeholder row off `gemini`:
# every google_ai_studio leg is now gated (the D-657 probe left the route with
# its Vertex spelling only), so that provider exits the plan at the
# all-legs-unavailable step and never reaches the key lookup this test is about.
# `vertex` is the provider the chains actually serve on today, so the placeholder
# rule is proved on a row apply.sh still plans.
if it "apply skips REPLACE_WITH placeholders and registers real keys"; then
    keys="$(mktemp)"
    printf 'vertex: REPLACE_WITH_VERTEX_KEY\nmistral: not-a-real-key-123\n' >"$keys"
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$keys" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    rm -f "$keys"
    assert_contains "$out" "vertex: no key in api-keys.yml, skipped"
    if grep -q "mistral: would register\|mistral already registered" <<<"$out"; then pass
    else fail "the real mistral key was not planned"; fi
fi

# A5a (routing v2 spec 3.2, D11): apply.sh's provider rows come from
# catalog/ai-registry.json's `providers` section - same row shape (name.lower, omniroute_id,
# provider_data compact JSON), same "no omniroute_id -> skip" rule, plus a
# new skip: a provider whose every leg in every route the registry marks
# unavailable is never registered (routes.<id>.unavailable_legs,
# providers.<id>.available: false). AUTOOS_REGISTRY_FILE points apply.sh at
# this fixture instead of the real catalog so the case does not drift with
# operator updates.
_a5a_provider_registry_fixture() {
    local d="$1"
    cat >"$d/ai-registry.json" <<'JSON'
{
  "providers": {
    "avail": {"omniroute_id": "avail-id", "provider_data": {"customUserAgent": "curl/8.7.1"}},
    "unused": {"omniroute_id": "unused-id"},
    "alldown": {"omniroute_id": "alldown-id"},
    "providerdown": {"omniroute_id": "providerdown-id", "available": false},
    "mixed": {"omniroute_id": "mixed-id"},
    "noomni": {}
  },
  "routes": {
    "r1": {
      "legs": ["avail/model-a", "alldown/model-b", "providerdown/model-c", "mixed/model-d"],
      "unavailable_legs": {
        "alldown/model-b": {"available": false, "$comment": "fixture: down"},
        "mixed/model-d": {"available": false}
      }
    },
    "r2": {
      "legs": ["alldown/model-e", "mixed/model-f"],
      "unavailable_legs": {
        "alldown/model-e": {"available": false}
      }
    }
  }
}
JSON
}

if it "apply.sh reads provider rows from ai-registry.json and skips a provider whose every leg is unavailable"; then
    d="$(mktemp -d)"
    _a5a_provider_registry_fixture "$d"
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/nonexistent/api-keys.yml \
        AUTOOS_REGISTRY_FILE="$d/ai-registry.json" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    rm -rf "$d"
    ok=1
    # alldown: both its legs (r1 + r2) are individually flagged - skipped.
    [[ "$out" == *"  - alldown-id: all legs unavailable (skipped)"* ]] \
        || { ok=0; echo "alldown (every leg unavailable_legs-flagged) was not skipped: $out" >&2; }
    # providerdown: no per-leg entry at all, but the provider itself carries
    # available: false - the second of spec 3.1's two down-flags.
    [[ "$out" == *"  - providerdown-id: all legs unavailable (skipped)"* ]] \
        || { ok=0; echo "providerdown (provider-wide available:false) was not skipped: $out" >&2; }
    # avail: no leg ever flagged - registered normally (no key -> "no key").
    [[ "$out" == *"  - avail-id: no key in api-keys.yml, skipped"* ]] \
        || { ok=0; echo "avail (no leg down) was wrongly all-unavailable-skipped: $out" >&2; }
    # unused: has an omniroute_id but appears in no route leg at all - "every
    # leg unavailable" is vacuously false for an unused provider, not true.
    [[ "$out" == *"  - unused-id: no key in api-keys.yml, skipped"* ]] \
        || { ok=0; echo "unused (no route leg at all) was wrongly all-unavailable-skipped: $out" >&2; }
    # mixed: one leg down (r1), one leg live (r2) - not EVERY leg, so it must
    # still be offered for registration.
    [[ "$out" == *"  - mixed-id: no key in api-keys.yml, skipped"* ]] \
        || { ok=0; echo "mixed (one live leg) was wrongly all-unavailable-skipped: $out" >&2; }
    # noomni has no omniroute_id at all - the pre-existing skip rule, never
    # even printed (the pre-existing skip rule for entries with no id).
    [[ "$out" == *"noomni"* ]] && { ok=0; echo "noomni (no omniroute_id) must never appear: $out" >&2; }
    if (( ok )); then pass; else fail "provider-level all-unavailable skip is not wired into apply.sh"; fi
fi

# Dead-vs-live is provider state, so the dead set is derived from the
# registry itself instead of re-pinned as a literal list here: a provider is
# dead iff it carries an omniroute_id, appears in at least one route leg, and
# every such leg is unavailable (tools/registry.py, the same read apply.sh
# performs) - proves the real catalog/ai-registry.json actually reaches
# apply.sh's live plan, not just the synthetic fixture above.
# SHARDB: expectation moved by 7eff6020 (FREEWIRE gave groq/openrouter servable :free legs) + c4c3654b (TORDER-OR kept the provider available).
if it "apply --dry-run against the real registry skips a provider whose every leg is dead today"; then
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/nonexistent/api-keys.yml \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    map="$(python3 - 2>&1 <<'PY'
import json, sys
sys.path.insert(0, "tools")
import registry as regmod
reg = json.load(open("catalog/ai-registry.json", encoding="utf-8"))
routes = reg.get("routes") or {}
providers = reg.get("providers") or {}
legs_by_provider = {}
for route in routes.values():
    if not isinstance(route, dict):
        continue
    for leg in route.get("legs") or []:
        try:
            pid, _ = regmod.resolve_leg(leg, reg)
        except ValueError:
            continue
        legs_by_provider.setdefault(pid, []).append((leg, route))
for name, entry in providers.items():
    provider_id = entry.get("omniroute_id")
    if not provider_id:
        continue
    legs = legs_by_provider.get(name) or []
    if legs and all(regmod._leg_is_unavailable(leg, route, reg) for leg, route in legs):
        print("dead %s" % provider_id)
    else:
        print("live %s" % provider_id)
PY
)"
    dead=""; live=""
    while read -r state oid; do
        if [[ "$state" == "dead" ]]; then dead="${dead}${oid} "
        elif [[ "$state" == "live" ]]; then live="${live}${oid} "; fi
    done <<<"$map"
    # A vacuous set proves nothing: the derivation must find dead providers
    # (e.g. cerebras, opencode-zen today) and live ones (e.g. groq,
    # openrouter, deepseek today) alike.
    if [[ -z "$dead" ]]; then fail "derived dead set is empty, the both-ways check below is vacuous"; fi
    if [[ -z "$live" ]]; then fail "derived live set is empty, the both-ways check below is vacuous"; fi
    ok=1
    for oid in $dead; do
        [[ "$out" == *"  - $oid: all legs unavailable (skipped)"* ]] \
            || { ok=0; echo "$oid (every leg unavailable in the registry) was not flagged: $out" >&2; }
    done
    # A provider with a live leg must still be offered normally, even with no
    # key - and must never be counted all-unavailable.
    for oid in $live; do
        [[ "$out" == *"  - $oid: no key in api-keys.yml, skipped"* ]] \
            || { ok=0; echo "$oid (has a live leg today) was not offered: $out" >&2; }
        [[ "$out" != *"  - $oid: all legs unavailable"* ]] \
            || { ok=0; echo "$oid (has a live leg today) was wrongly counted all-unavailable" >&2; }
    done
    if (( ok )); then pass; else fail "the real registry's dead providers do not reach apply.sh's plan"; fi
fi

# start-stack.sh sits in configuration/, one level below the repo root. A
# `/../..` root pointed at the repo's parent, so the OpenHands tier-profile
# sync was never found and every start printed "reported a problem".
if it "start-stack.sh resolves the repo root for the profile sync"; then
    expr="$(grep -m1 '_ss_root=' configuration/start-stack.sh | sed -e 's/^[^=]*=//' -e 's/\${BASH_SOURCE\[0\]}/configuration\/start-stack.sh/')"
    got="$(eval "printf '%s' $expr")"
    if [[ -f "$got/tools/sync-openhands-profiles.py" ]]; then pass
    else fail "_ss_root resolved to '$got'"; fi
fi

# tools/autoos-agent.py - dry runs only: nothing is spawned or fetched.
if it "autoos-agent pairs each tier with its own model, standalone"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io, shlex, subprocess
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
problems = []
for tier, agent in ((1, "l1-orchestrator"), (2, "l2-worker"), (3, "t3-reviewer")):
    out = subprocess.run(["python3", "tools/autoos-agent.py", "run", "--tier", str(tier), "--dry-run", "t"],
                         capture_output=True, text=True).stdout
    want = "--standalone --agent %s --model %s " % (agent, shlex.quote(oc["agents"][agent]["model"]))
    if want not in out:
        problems.append(agent)
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "autoos-agent: --clean picks the twin, undeclared models and --free --clean are refused"; then
    out="$(python3 tools/autoos-agent.py run --tier 3 --clean --dry-run t)"
    assert_contains "$out" "--model omniroute/l3-driver-clean "
    rc=0; python3 tools/autoos-agent.py run --tier 2 --model omniroute/not-a-combo --dry-run t >/dev/null 2>&1 || rc=$?
    assert_eq "$rc" "2"
    rc=0; python3 tools/autoos-agent.py run --tier 2 --free --clean --dry-run t >/dev/null 2>&1 || rc=$?
    assert_eq "$rc" "2"
fi

if it "autoos-agent --free is keyless and --isolate plans a fenced clone, never a worktree"; then
    # SEAT-PYTEST (P4) exports PYTHONPATH only where the seat python3's own user site
    # exists under $HOME and passes the safety checks, so the real HOME would decide
    # the answer (D-852: keep it off the host). A fresh empty HOME has no user site on
    # any host; only this one command sees it, so OPENCODE etc. still reach the plan.
    home="$(mktemp -d)"
    out="$(HOME="$home" AUTOOS_OMNIROUTE_KEY=never-print-this-key python3 tools/autoos-agent.py run --tier 2 --free --isolate --dry-run t)"
    rm -rf "$home"
    assert_contains "$out" "one-commit materialisation of allowed HEAD files"
    # The env the child gets, named in the plan (FF1/FF1b, D-106). Sorted and
    # padded by whatever the caller legitimately exports, so name the entries
    # that must be there instead of one exact line nobody can reproduce.
    missing=""
    leaked=""
    for want in OPENCODE_CONFIG_CONTENT XDG_DATA_HOME XDG_RUNTIME_DIR \
                XDG_CONFIG_HOME \
                GIT_TERMINAL_PROMPT GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS \
                GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM; do
        if ! grep -q "env: [^\n]*${want}" <<<"$out"; then
            missing="${missing}${want} "
        fi
    done
    for banned in AUTOOS_KEYS_FILE PYTHONPATH LD_PRELOAD GIT_SSH_COMMAND; do
        if grep -q "env: [^\n]*${banned}" <<<"$out"; then
            leaked="${leaked}${banned} "
        fi
    done
    # FF1c item 1: the plan prints names only, so the values are checked in
    # tests/test_autoos_spawner.py — GitGlobalConfigFenceTests writes a fake
    # ~/.gitconfig and a fake $XDG_CONFIG_HOME/git/config and runs a real
    # `git config --get core.sshCommand` under the worker env, and
    # ChildRuntimeDirTests asserts the private dirs are created 0700.
    assert_eq "$missing" ""
    assert_eq "$leaked" ""
    if grep -q "worktree add\|never-print-this-key\|AUTOOS_OMNIROUTE_KEY" <<<"$out"; then
        fail "free/isolated plan mentions a worktree or the gateway key"
    else pass; fi
fi

# The other half of the same gate: a HOME that does hold a qualifying user site
# earns PYTHONPATH by name. The VALUE stays pinned in tests/test_autoos_spawner.py
# (SeatUserSitePasslistTests, SeatPythonProbeTests), because the plan prints names only.
if it "autoos-agent --isolate names PYTHONPATH when the seat's user site qualifies"; then
    home="$(mktemp -d)"
    # The probe the spawner runs: -I does not ignore PYTHONUSERBASE, so scrub it.
    site="$(env -u PYTHONUSERBASE HOME="$home" python3 -c \
              'import site,sys;sys.stdout.write(site.getusersitepackages())')"
    if [[ "$site" != "$home"/* || "$(realpath -m "$site" 2>/dev/null)" != "$site" ]]; then
        rm -rf "$home"; skip "this host's temp HOME is not a probe-clean path ($site)"
    else
        mkdir -p "$site"; chmod 755 "$site"
        out="$(HOME="$home" AUTOOS_OMNIROUTE_KEY=never-print-this-key python3 tools/autoos-agent.py run --tier 2 --free --isolate --dry-run t)"
        leaked=""
        for banned in AUTOOS_KEYS_FILE LD_PRELOAD GIT_SSH_COMMAND; do
            if grep -q "env: [^\n]*${banned}" <<<"$out"; then
                leaked="${leaked}${banned} "
            fi
        done
        if grep -q "env: [^\n]*PYTHONPATH" <<<"$out" && [[ -z "$leaked" ]]; then
            pass
        else fail "qualifying site gave no PYTHONPATH, or [$leaked] leaked"
        fi
    fi
fi

# ADR 0006 card resolver, the client adapters and the depth budget: one
# unittest per routing-table row, all dry runs.
if it "autoos-agent spawner unit tests: card routing, clients, depth"; then
    out="$(python3 tests/test_autoos_spawner.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-REDACT-SPAN (2026-10-09): tools/autoos_redact.py must mask the PEM *span*
# it matched, not the whole line and not every line after an unterminated BEGIN.
# Pure in-memory strings; nothing spawned, no file read.
if it "autoos_redact span: PEM block masking keeps the report and its VERDICT (unit tests)"; then
    out="$(python3 tests/test_autoos_redact_span.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# HOSTADMISSION (lane AO-ADMISSION, 2026-10-08): the spawner asks the host for
# permission before it starts another worker - the live count from the records
# `ps` reads, MemAvailable from /proc/meminfo, both against catalog's
# host_admission. Refusal is exit 13 and the MCP tool's state "rejected"; a dry
# run is never refused. Filtered out of the suite above so a CI box that is short
# on memory reports the refusal it hit by name instead of 1262 unrelated cases.
if it "autoos-agent host admission: live workers and free memory gate the spawn"; then
    out="$(python3 tests/test_autoos_spawner.py -k admission 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# T0-FREEZE (plan v3): the sixth `ready` gate -- main CI red freezes normal
# lanes, --fixes-main + AUTOOS_FIXES_MAIN waives it. Same fakes-and-real-CLI
# shape as the spawner gate tests above; fixtures are temp repos, no CI read.
if it "ready main-CI freeze gate: red main blocks, fixes-main waives (unit tests)"; then
    out="$(python3 tests/test_ready_main_freeze.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# gwloopback (2026-09-30): how tools/autoos-agent.py picks the gateway - the
# AUTOOS_OMNIROUTE_URL override (tried alone, never rescued), the docker DNS
# name with loopback as fallback, the I/O-free import, and gateway_up() as the
# pre-check that rebinds GATEWAY. Probes are injected; no gateway is contacted.
if it "gateway selection: override, docker DNS, loopback fallback, gateway_up pre-check (unit tests)"; then
    out="$(python3 tests/test_gateway_selection.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# RESTART spec §0/§2 (lane R1): the shared inbox reader (tools/autoos_inbox.py),
# the `inbox` verb and the explicit verb->handler dispatch table. Fixtures are
# temp files; nothing is spawned and no inbox outside the sandbox is read.
if it "autoos_inbox: records, positions, late flags, the inbox verb, dispatch table (unit tests)"; then
    out="$(python3 tests/test_autoos_inbox.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# RESTART spec §1 (lane R2a): the state-card checker (tools/autoos_card.py) and
# the `card check` verb. Fixtures are temp files; the checker is read-only, so
# nothing here writes a card or reads a live run dir.
if it "autoos_card: section order, line caps, header fields, the card check verb (unit tests)"; then
    out="$(python3 tests/test_autoos_card.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# SEAT-EVIDENCE (D-260): the per-turn served-model evidence tool
# (tools/seat-model-evidence.py). Fixtures are temp SQLite dbs reproducing
# the opencode schema shape; the real example dbs are read-only and skipped
# when absent. Nothing is spawned, no db is written.
if it "seat-model-evidence: per-turn served model, exit codes, credential privacy (unit tests)"; then
    out="$(python3 tests/test_seat_model_evidence.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# RISKTIER-a (operator Q-013 / D-060, 2026-09-28): the diff risk classifier --
# the glob matcher, every policy.risk_rules shape, the sha audit draw, assess()
# against a temp git repo, and the `risk` verb. Fixtures are temp repos and
# injected runners; no gateway, no network, nothing spawned.
if it "autoos_risk: diff classifier, risk rules, audit draw, risk verb (unit tests)"; then
    out="$(python3 tests/test_autoos_risk.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-WRITER-GUARDS P1 (0007 follow-up): the pre-spawn writer guard --
# R-scale judgement plus the policy.writers allow-list. Fixtures are inline
# dicts plus the committed registry; nothing is spawned.
if it "autoos_writer_rule: R-scale judgement plus writer allow-list (unit tests)"; then
    out="$(python3 tests/test_autoos_writer_rule.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-WRITER-GUARDS P2: the ops brief template store (tools/autoos_brief.py).
if it "autoos_brief: ops brief render, R3 clause, field guards (unit tests)"; then
    out="$(python3 tests/test_autoos_brief.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-WRITER-GUARDS P3: the writer verdict ledger (tools/autoos_writer_ledger.py).
if it "autoos_writer_ledger: verdict ledger, demotion, probe, rollup (unit tests)"; then
    out="$(python3 tests/test_autoos_writer_ledger.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-WRITER-GUARDS P4a: the pure ready guards (tools/autoos_ready_guards.py).
if it "autoos_ready_guards + autoos_verdict: FILES scope, diff fence, verdict grammar (unit tests)"; then
    out="$(python3 tests/test_autoos_ready_guards.py 2>&1)" && out="$(python3 tests/test_autoos_verdict.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-WRITER-GUARDS P4c: run-death recovery (tools/autoos_recovery.py).
if it "autoos_recovery: run classify, stall clock, attempt budget, continue task, plan (unit tests)"; then
    out="$(python3 tests/test_autoos_recovery.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# AO-READY-CALLERS: a tracked file that shows the ready gate names both guard files.
if it "ready callers: tracked invocations name --brief and --report (lint)"; then
    out="$(python3 tests/test_ready_callers.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Resolver v2 (routing v2 spec section 5): pure bucket/effort tables and measure().
if it "resolver v2: bucket boundaries, effort rows, clamp (unit tests)"; then
    out="$(python3 tests/test_autoos_resolver.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/vibe_kanban_bridge.py: the board's missing dependency, capacity-pickup
# and pause logic (FLEETSPEC §6.1, D-089/D-095). Pure/fake-driven; no live Vibe
# Kanban is contacted.
if it "vibe_kanban_bridge: dependencies, readiness, pause, poll loop (unit tests)"; then
    out="$(python3 tests/test_vibe_kanban_bridge.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "resolver v2: measure() features and client_state (unit tests)"; then
    out="$(python3 tests/test_autoos_measure.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "resolver v2: track record and Beta success estimate (unit tests)"; then
    out="$(python3 tests/test_autoos_track.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# MEMGRAPH (operator D-128): the AutoOS memory-graph sync (tools/sync_memory_graph.py).
# Pure stdlib, temp-dir fixtures only; no live graph is contacted.
if it "sync_memory_graph: ledger-gated NDJSON emit, hub edges, merge-only load (unit tests)"; then
    out="$(python3 tests/test_sync_memory_graph.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/autoos_usage.py: the `usage` subcommand of autoos-agent.py reads the
# gateway call-logs (OR4). Every test injects a fake fetch, so this never
# calls the gateway.
if it "autoos_usage: gateway call-logs report, paging and aggregation (unit tests)"; then
    out="$(python3 tests/test_autoos_usage.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# T1-CREDIT fixes (CIGREEN wiring: these red-first gates cover tools/autoos_resolver.py
# credit guards and tools/autoos_usage.py spend mapping; fixtures and fake fetches only,
# never a live gateway): fix2 narrow guards/total fallback/exit 3, fix6 per-provider
# pricing, fix7 review findings R1-R5, fix10 real meter without the manage key.
if it "t1-credit fix2: narrow guards, total fallback, usage exit 3 (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix2.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix6: per-provider pricing for credit legs (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix6.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix7: review findings R1-R5 on fix6 (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix7.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix10: real meter without the manage key, paid cap, cache-read billing (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix10.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix14: stale balance ledger fails closed (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix14.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix16: never govern from old data + fail-closed fallbacks (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix16.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t1-credit fix17: present-but-unusable balance entries + overlay except wrap (unit tests)"; then
    out="$(python3 tests/test_t1_credit_fix17.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "t2-record-pin: record pin round-trip and conflict resolution (unit tests)"; then
    out="$(python3 tests/test_t2_record_pin.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "fleet-agents: foreign-repo sandboxes get the AutoOS agent definitions (unit tests)"; then
    out="$(python3 tests/test_fleet_sandbox_agents.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "deepinfra-wire-min: MiMo-V2.6-Flash writer pin, prepaid hard stop, no Claude/-clean legs (unit tests)"; then
    out="$(python3 tests/test_deepinfra_wire_min.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "gateway-key exec: child runs with the key in its env only (unit tests)"; then
    out="$(python3 tests/test_gateway_key_exec.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "review-call: one tool-less gateway review call, key only via exec (unit tests)"; then
    out="$(python3 tests/test_review_call.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "debtcap nebius: provider excluded from free/trial tiers (unit tests)"; then
    out="$(python3 tests/test_debtcap_nebius.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "gateway quota patch: loud-fail substitution for quota windows without a total (unit tests)"; then
    out="$(python3 tests/test_gateway_quota_patch.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "gateway quota patch CLI: CLI invocation, arguments, and dry-run (unit tests)"; then
    out="$(python3 tests/test_gateway_quota_patch_cli.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Credential-JSON ignore gate (operator 2026-10-01): asserts .gitignore covers
# configuration/*-credentials*.json and no credential-shaped path is tracked.
# Path and git state only; never reads a credential's contents.
if it "credential files: vertex/service-account JSONs are ignored, never tracked (unit tests)"; then
    out="$(python3 tests/test_credential_files_ignored.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Public-ids gate (AGENTS Hard Rule 1, lane PUBLIC-IDS-SCRUB 2026-10-08): no real
# GCP project id and no service-account address may sit in a tracked file. The
# id leaked through the *documentation* of the credential path, which the ignore
# gate above cannot see — it only ever looks at paths.
if it "public ids: no real GCP project id or service-account address ships (unit tests)"; then
    out="$(python3 tests/test_public_ids.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "omnigraph gateway policy: static refusal and least-privilege checks (unit tests)"; then
    out="$(python3 tests/test_gateway_policy.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: run and daily budget evaluations (unit tests)"; then
    out="$(python3 tests/test_run_budget.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: edge inputs, classification and price file (unit tests)"; then
    out="$(python3 tests/test_run_budget_more.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: spawner refusal and daily gate integration (unit tests)"; then
    out="$(python3 tests/test_run_budget_spawner.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: CLI output, exit codes and boundaries (unit tests)"; then
    out="$(python3 tests/test_run_budget_v3.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: rotate-on-last-call, day bad-row accumulation, gateway handling and provider/model pins (unit tests)"; then
    out="$(python3 tests/test_run_budget_v3b.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost guard: spawner gate numeric values, no-override rule and model sources (unit tests)"; then
    out="$(python3 tests/test_run_budget_spawner_v3.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: price table, unpriced-at-highest-flash-rate and price file (unit tests)"; then
    out="$(python3 tests/test_run_budget_prices.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: refresh script, atomic gate file, status line and config handling (unit tests)"; then
    out="$(python3 tests/test_cost_gate_refresh.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: rows pass-through and the status/staleness alarm script (unit tests)"; then
    out="$(python3 tests/test_cost_gate_refresh_more.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: default gate path rule and fail-open reason (unit tests)"; then
    out="$(python3 tests/test_cost_gate_default_path.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: Linux installer, user units and catalog entry (unit tests)"; then
    out="$(python3 tests/test_cost_gate_units.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: additional Linux tests (unit tests)"; then
    out="$(python3 tests/test_cost_gate_units_more.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: additional Linux tests 2 (unit tests)"; then
    out="$(python3 tests/test_cost_gate_units_more2.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: Windows live run (unit tests)"; then
    out="$(python3 tests/test_cost_gate_windows_run.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: Windows culture invariance and export completeness (unit tests)"; then
    out="$(python3 tests/test_cost_gate_culture.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "cost gate: Windows module, export wrapper and scheduled task (unit tests)"; then
    out="$(python3 tests/test_cost_gate_windows.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent: git subprocess calls on MCP paths do not inherit stdin (unit tests)"; then
    out="$(python3 tests/test_mcp_stdin_devnull.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# REVFIX: no unit test file may be invisible to every harness. This guard
# fails when a tests/test_*.py is not named by any suite entry point.
if it "suite wiring: every tests/test_*.py is wired into a harness"; then
    out="$(python3 tests/test_suite_wiring.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent context: fill from the session transcript (unit tests)"; then
    out="$(python3 tests/test_autoos_context.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/autoos_tokenrate.py (RESTART spec §5 metric): tokens per merged change,
# summed over EVERY transcript usage record — the fixture projects dir and the
# throwaway git repo mean this never reads the live transcripts.
if it "autoos-agent token-rate: orchestrator tokens per merge (unit tests)"; then
    out="$(python3 tests/test_autoos_tokenrate.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# heartbeat (R-heartbeat-02/03, R-pause-01, R-handoff-07 migrated into code): pause,
# unpushed/dirty branches, context fill - read-only, plus the run/spawn PAUSE refusal.
if it "autoos-agent heartbeat: pause/unpushed/dirty/context, run+spawn PAUSE refusal (unit tests)"; then
    out="$(python3 tests/test_autoos_heartbeat.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# OVERLAYHOME: the machine-wide tool_calls overlay (path resolution, legacy
# fallback, locked merge-save) - temp dirs only, never the real state dir.
if it "autoos_overlay: machine-wide overlay path, legacy fallback, locked merge-save (unit tests)"; then
    out="$(python3 tests/test_autoos_overlay.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# catalog/ai-registry.json (routing v2 spec section 3): check rules.
if it "registry.py: check rules (unit tests) and the committed registry is valid"; then
    out="$(python3 tests/test_registry.py 2>&1 && python3 tools/registry.py validate 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# registry renders (routing v2 spec 3.2 D11, task A5f): every generated file is
# gated on a fresh render; ide-models.json is byte-exact, the rest semantic.
if it "registry: no generated file drifts"; then
    tmp_ide="$(mktemp)"
    out="$(python3 tests/test_registry_render.py 2>&1 && python3 tools/registry.py render omniroute --check 2>&1 && python3 tools/registry.py render litellm --check 2>&1 && python3 tools/registry.py render ide --check 2>&1 && python3 tools/registry.py render openhands --check 2>&1 && python3 tools/registry.py render models-doc --check 2>&1 && python3 tools/registry.py render ide --out "$tmp_ide" 2>&1 && { cmp -s "$tmp_ide" catalog/ide-models.json || { echo "ide-models.json differs byte-exact from render ide (run: python3 tools/registry.py render ide --out catalog/ide-models.json)"; exit 1; }; })" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    rm -f "$tmp_ide"
fi

# ORCH-A1 phase 1: role launch profiles are rendered from the harness
# fences (tools/launch_profiles.py render --check) and the rendered matchers
# pass the profile-shape, secret-scope and always-deny contradiction tables.
if it "launch profiles: rendered from the harness fences, scope tables green (ORCH-A1 phase 1)"; then
    rc=0
    tests="$(python3 tests/test_launch_profiles.py 2>&1)" || rc=$?
    check="$(python3 tools/launch_profiles.py render --check 2>&1)" || rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s\n%s\n' "$tests" "$check" | tail -n 20)"; fi
fi

# ORCH-A1 phase 1 round 13 (routing-00 D-159): every push and dispatch
# spelling lives in tests/fixtures/push-corpus.json and is decided against the
# same rendered profiles - the real-git premise tests run git in a temporary
# directory only.
if it "push corpus: every push spelling decides as the fixture says (ORCH-A1 round 13)"; then
    out="$(python3 tests/test_push_corpus.py 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s\n' "$out" | tail -n 20)"; fi
fi

# tools/probe-toolcalls.py: tool-calling probe writes the overlay (routing v2 spec 3.1, 5.3, 10).
if it "probe-toolcalls: tool-calling probe writes the overlay (unit tests)"; then
    out="$(python3 tests/test_probe_toolcalls.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/probe-recall.py: multi-needle recall probe writes the overlay (routing v2 spec 3.1, 10).
if it "probe-recall: multi-needle recall probe writes the overlay (unit tests)"; then
    out="$(python3 tests/test_probe_recall.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/claude-cli-lag.py: version lag check, no pin (CLIPIN / D-137).
if it "claude-cli-lag: host version vs the newest release and the autoupdater state (unit tests)"; then
    out="$(python3 tests/test_claude_cli_lag.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/autoos_report.py: BRIEF/REPORT protocol parser (routing v2 spec 5.7, 8.2).
if it "autoos_report: BRIEF/REPORT protocol parser (unit tests)"; then
    out="$(python3 tests/test_autoos_report.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# MEMSPEC P1 (docs/plans/2026-09-28-memory-facade-spec.md §4, §11): the typed
# memory facade MCP - five methods, write rules, write receipts. All state is a
# temp copy of the R0 fixture; nothing real is read or written.
if it "memory facade: five methods, write rules and receipts (unit tests)"; then
    out="$(python3 tests/test_memory_facade_mcp.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# MEMSPEC P1 (docs/plans/2026-09-28-memory-facade-spec.md §5): every facade
# write appends the schema: 2 envelope agreed in the fleet console spec §4.3.
# Temp store + temp events file; nothing real is read or written.
if it "memory facade: schema 2 write events (unit tests)"; then
    out="$(python3 tests/test_memory_events.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/probe-effort.py: effort-ladder probe writes the overlay (routing v2 spec 5.5, 10).
if it "probe-effort: effort-ladder probe writes the overlay (unit tests)"; then
    out="$(python3 tests/test_probe_effort.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/probe-ledger.py: the availability ledger - measured per-leg serving
# state, so lanes stop re-probing known-good and known-dead legs. Every case
# runs against a temp state file; logs/routing/availability.json is never read.
if it "probe-ledger: availability ledger record/check/credit/dump (unit tests)"; then
    out="$(python3 tests/test_probe_ledger.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/probe-free.py: ack / tool-call / prompt-cache probe and its TSV output
# (D-657 AO-PROBE-D657). Offline: the gateway is a fake OpenAI-shape server on a
# loopback socket, the key a fake env value - no live call, no real key read.
if it "probe-free: cache probe verdicts, empty-vs-fail ack and TSV rows (unit tests)"; then
    out="$(python3 tests/test_probe_free.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/probe-rtk.py: the RTK A/B behind decision D19 (routing v2 spec section 10).
# Offline: subprocess.run is monkeypatched, so this never calls the omniroute CLI
# and never reads a manage key - the live A/B stays an operator run.
if it "probe-rtk: RTK A/B and the D19 gate (unit tests)"; then
    out="$(python3 tests/test_probe_rtk.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/skill-rules.py: the linter for one-line skill rules (routing v2 spec 8.1).
if it "skill-rules check: ids, length, source, near-duplicates (unit tests)"; then
    out="$(python3 tests/test_skill_rules.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "orchestration skill rules pass skill-rules check"; then out="$(python3 tools/skill-rules.py check 2>&1)" && pass || fail "$out"; fi

# tools/affected-tests.py: the --filter a registry change actually needs
# (lessons PROVPIN, MUSEPIN 2026-09-27: a hand-picked filter list missed the
# shell and Pester cases naming a flipped id, and CI went red twice).
if it "affected-tests: registry ids reach every shell, Pester and pytest case naming them (unit tests)"; then
    out="$(python3 tests/test_affected_tests.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/diff-guard.py: the report-only post-run diff guard (rules R0..R5) —
# it compares a base..tip range against --scope globs and reports drift
# (test files outside scope, lying builtin-tuple subclasses, framework
# references in source, skips/shrinking asserts/grown literals, dropped test
# defs, unparsable tips); it never edits anything.
if it "diff-guard: report-only drift rules R0..R5 over a git revision range (unit tests)"; then
    out="$(python3 tests/test_diff_guard.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/prepush.py: the pre-push gate — the ancestor check, CI's plan check, the run
# list derived from the changed files, CI's git env, the green/override records and
# `--check-ready` (operator D-154). CI 36529545083 shard b is why this line exists: the
# file was added and never wired, so nothing ran it until tests/test_suite_wiring.py
# refused the un-wired file.
if it "prepush: the gate refuses a push whose tests were never run (unit tests)"; then
    out="$(python3 tests/test_prepush.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/prepush_private.py: the private-pattern + gitleaks legs of that gate — the
# added lines of a push matched against a names file only the environment names, a
# hit reported as path:line and never as its text (AutoOS is a public repo).
if it "prepush-private: the private-pattern and gitleaks legs of the gate (unit tests)"; then
    out="$(python3 tests/test_prepush_private.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tests/test_no_cao.py: the no-CAO gate (operator removal lane 2026-09-29) — no CAO
# path, skill section or example-config block may ship; the file shipped unwired,
# so the gate existed but nothing ran it until tests/test_suite_wiring.py refused it.
if it "no-cao: no CAO path, section, or config ships (unit tests)"; then
    out="$(python3 tests/test_no_cao.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# FLEET-HOOKS (P0): tools/hooks/bash_guard.py is the Claude Code PreToolUse
# guard for the Bash tool (docs/ai/fleet-hooks.md) — denies an unquoted
# heredoc whose body carries a backtick/$(...) (the netplan-apply incident
# shape) and a claude --bg/-p/--print call whose double-quoted argument
# carries one; everything else, including every false-positive guard in the
# table, allows silently.
if it "bash-guard: PreToolUse heredoc + claude --bg/-p/--print guard (unit tests)"; then
    out="$(python3 tests/test_bash_guard.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "opencode bash-guard: OpenCode plugin hook guard (unit tests)"; then
    out="$(python3 tests/test_opencode_bash_guard.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l1 launcher: config and render (unit tests)"; then
    out="$(python3 tests/test_oc_l1.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l1 launcher: start (unit tests)"; then
    out="$(python3 tests/test_oc_l1_serve.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l1 launcher: status (unit tests)"; then
    out="$(python3 tests/test_oc_l1_status.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l1 launcher: bash-guard canary (unit tests)"; then
    out="$(python3 tests/test_oc_l1_canary.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l1 launcher: rendered opencode config shape (unit tests)"; then
    out="$(python3 tests/test_oc_l1_render.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "oc_l2 L2 phase lane: start, stop, inbox (unit tests)"; then
    out="$(python3 tests/test_oc_l2.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "render-opencode-container-config survives a malformed port (unit tests)"; then
    out="$(python3 tests/test_render_opencode_config.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "audit-router reads the LiteLLM master key from .env (unit tests)"; then
    out="$(python3 tests/test_audit_router_litellm_key.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "audit-router live probes retry a 503 with backoff and never a drift status (unit tests)"; then
    out="$(python3 tests/test_audit_router_probe_retry.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "audit-router's unit tests pass (registry-sourced, task A5c)"; then
    out="$(python3 tests/test_audit_router_registry.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent spawner windows prompt delivery and shim resolution (unit tests)"; then
    out="$(python3 tests/test_win_prompt_spawn.py 2>&1 && python3 tests/test_win_prompt_spawn_more.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent outside-path fence denies first and re-allows only opencode scratch"; then
    report="$(python3 - 2>&1 <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("agent", "tools/autoos-agent.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rules = m.outside_fence("/x/data")
problems = []
if rules[0] != {"action": "external_directory", "resource": "*", "effect": "deny"}:
    problems.append("first-rule")
for r in rules[1:]:
    if r["effect"] != "allow" or "opencode" not in r["resource"] or r["resource"] == "*":
        problems.append(r["resource"])
if not any(r["resource"] == "/x/data/opencode/*" for r in rules):
    problems.append("private-data-dir")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "tier depth is mandatory in opencode.jsonc agents"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
a = json.loads(text)["agents"]
def perms(n):
    return [(p["action"], p["resource"], p["effect"]) for p in a[n]["permissions"]]
t1, t2, t3 = perms("l1-orchestrator"), perms("l2-worker"), perms("t3-reviewer")
problems = []
# KEYDENY3b: both spawn-gate spellings are fenced (v2's asserted `subagent`
# action, the permission object's `task` key), each of them denying everything
# first and then allowing exactly the one child — a leaf allows none.
def gate(rules, child):
    for action in ("subagent", "task"):
        own = [(r, e) for a, r, e in rules if a == action]
        if not own or own[0] != ("*", "deny"):
            return False
        if child is None and len(own) != 1:
            return False
        if child is not None and own[-1] != (child, "allow"):
            return False
    return True
if not gate(t1, "l2-worker"):
    problems.append("t1")
if not gate(t2, "t3-reviewer"):
    problems.append("t2")
if not gate(t3, None):
    problems.append("t3-spawn-leaf")
# The leaf's fences run past these eight, but the first eight are the shape
# both sides agreed on; v2 names the shell action `shell` (a `bash` rule
# matches nothing) and the full fence set is asserted below.
if t3[:8] != [("subagent", "*", "deny"), ("task", "*", "deny"),
              ("edit", "*", "deny"), ("write", "*", "deny"), ("read", "*", "allow"),
              ("grep", "*", "allow"), ("glob", "*", "allow"), ("shell", "*", "allow")]:
    problems.append("t3-leaf")
if a["t3-reviewer"]["mode"] != "subagent":
    problems.append("t3-mode")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

# opencode v2 drops a top-level subagent_depth as an "unsupported legacy
# setting" and defaults to 1, so l2-worker answered "Subagent depth limit reached
# (1)" when l1-orchestrator had launched it (live, 2026-09-24).
if it "subagent depth lives under experimental, where opencode v2 reads it"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
problems = []
if "subagent_depth" in oc:
    problems.append("top-level-key-is-ignored")
if (oc.get("experimental") or {}).get("subagent_depth") != 2:
    problems.append("experimental.subagent_depth!=2")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

# The reviewer is a leaf: v2 names the shell action `shell` (a `bash` rule
# matches nothing), MCP write tools bypass the edit/write deny, and a leaf
# never commits or pushes. Live 2026-09-24 the old stanza let t3-reviewer commit
# through the shell and overwrite a file through serena's create_text_file.
# KEYDENY 2026-09-28: the same stanza allowed every `read`, so the read_deny_all
# fences are asserted here too — as patterns and as a decision on real paths.
# KEYDENY3 2026-09-28: the fence stopped at the `read` tool. opencode v2.0.16
# matches `grep`/`glob` against the search pattern and checks the searched path
# only through `external_directory`, and an MCP rule can only name a tool
# (resources is the literal ["*"]), so the same list now has to land on grep,
# glob and external_directory, and every serena tool that can return file bytes
# has to be off the allow list.
if it "t3-reviewer fences the shell, read, grep, glob, serena and omnigraph writes"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
harness = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
fences = harness["fences"]
t3 = [(p["action"], p["resource"], p["effect"]) for p in oc["agents"]["t3-reviewer"]["permissions"]]
problems = []
if any(act == "bash" for act, _, _ in t3):
    problems.append("bash-rule-matches-nothing-in-v2")
def last(action, resource):
    hits = [e for a, r, e in t3 if a == action and r == resource]
    return hits[-1] if hits else None
for pat in fences["bash_deny_all"] + fences["bash_deny_leaf"]:
    if last("shell", pat) != "deny":
        problems.append("shell:" + pat)
for pat in fences["read_deny_all"]:
    if last("read", pat) != "deny":
        problems.append("read:" + pat)
for pat in fences["bash_allow_all"]:
    if last("shell", pat) != "allow":
        problems.append("shell-allow:" + pat)
for pat in fences["read_allow_all"]:
    if last("read", pat) != "allow":
        problems.append("read-allow:" + pat)
# KEYDENY3: one deny list, three actions. grep/glob are matched against the
# pattern the leaf passes, external_directory against the path it searches.
for act in ("grep", "glob", "external_directory"):
    for pat in fences["read_deny_all"]:
        if last(act, pat) != "deny":
            problems.append(act + ":" + pat)
for act in ("grep", "glob"):
    for pat in fences["read_allow_all"]:
        if last(act, pat) != "allow":
            problems.append(act + "-allow:" + pat)
# KEYDENY: a fence is a decision over a path, not a list of names. Fixture
# paths only — no real key file is opened, the deny is on the name.
import fnmatch
def decide(action, value):
    verdict = None
    for a, r, e in t3:
        if a == action and fnmatch.fnmatch(value, r):
            verdict = e
    return verdict
for path in ("configuration/api-keys.yml",
             "/home/x/.config/autoos/ai-stack/client.key",
             "/home/x/.config/autoos/ai-stack/manage.key"):
    if decide("read", path) != "deny":
        problems.append("read-open:" + path)
    if decide("shell", "cat " + path) != "deny":
        problems.append("shell-open:" + path)
    # KEYDENY3: the same path decided as a search pattern (grep/glob) and as a
    # searched directory (external_directory).
    for act in ("grep", "glob", "external_directory"):
        if decide(act, path) != "deny":
            problems.append(act + "-open:" + path)
# KEYDENY2: a shell rule matches the whole command line, so the substring allow
# *api-keys.example* licensed the real key file the moment its name shared a
# line with the template — and let a copy out under an *.example* path.
for command in ("cat configuration/api-keys.yml configuration/api-keys.example.yml",
                "cp configuration/api-keys.yml /tmp/api-keys.example/x",
                "cat /tmp/api-keys.example/stolen"):
    if decide("shell", command) != "deny":
        problems.append("shell-substring-abuse:" + command)
example = "configuration/api-keys.example.yml"
if decide("read", example) != "allow":
    problems.append("read-denied-example")
for act in ("grep", "glob"):
    if decide(act, example) != "allow":
        problems.append(act + "-denied-example")
if decide("shell", "cat " + example) != "deny":
    problems.append("shell-cats-example")
# A read allow is a path, not a substring: a backup named after the template
# stays denied.
for path in ("/tmp/api-keys.example.yml.bak", "/tmp/api-keys.example/stolen"):
    if decide("read", path) != "deny":
        problems.append("read-open-near-miss:" + path)
if last("serena_*", "*") != "deny":
    problems.append("serena-writes-open")
for tool in ("omnigraph_mutate", "omnigraph_load", "omnigraph_branches_merge", "omnigraph_branches_delete", "playwright_browser_run_code_unsafe", "playwright_*", "context7_*", "autoos-agent_*"):
    if last(tool, "*") != "deny":
        problems.append(tool)
# KEYDENY3b: a leaf must have no MCP door that reads a file by URL or path. The
# deny is on the server pattern, so the check is a match, not a name lookup.
for tool in ("playwright_browser_navigate", "context7_get_library_docs"):
    if not any(e == "deny" and fnmatch.fnmatch(tool, a) for a, r, e in t3):
        problems.append("mcp-reader-open:" + tool)
allowed = [a for a, r, e in t3 if a.startswith("serena_") and e == "allow"]
writers = ("create", "replace", "insert", "rename", "delete", "edit", "write", "execute")
problems += ["serena-writer-allowed:" + a for a in allowed if any(w in a for w in writers)]
# KEYDENY3: an MCP rule cannot name a path (v2.0.16 dispatches every MCP call as
# {action:"<server>_<tool>", resources:["*"]}), so a serena tool that can return
# file bytes has to be off the allow list outright. The list is the harness's,
# never a copy — one edit moves the render and this assertion together.
raw_readers = ["serena_" + t for t in harness["mcp_servers"]["serena"]["raw_content_tools"]]
problems += ["serena-raw-reader-allowed:" + a for a in allowed if a in raw_readers]
# And the tools that return names or diagnostics, never bytes, stay usable —
# over-denying here is a defect too (a tool-less reviewer refuses the task).
for keep in ("serena_get_symbols_overview", "serena_list_dir", "serena_find_file"):
    if keep not in allowed:
        problems.append("serena-name-only-denied:" + keep)
if not allowed:
    problems.append("serena-read-tools-missing")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "opencode tiers declare matching context limits"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
oc = json.loads(text)
m = oc["providers"]["omniroute"]["models"]
# Expected models and their context limits are derived from
# catalog/ide-models.json (rendered from catalog/ai-registry.json) and the
# omitted set from configuration/omniroute/combos.json - so a provider flip
# that makes a route servable, or fails it closed, needs no re-pin here
# (lesson PROVPIN 2026-09-27).
ide = json.load(io.open("catalog/ide-models.json", encoding="utf-8"))["models"]
expected = {x["id"]: x["context"] for x in ide
            if "opencode" in ((x.get("surfaces") or {}).get("omniroute") or [])}
combos = json.load(io.open("configuration/omniroute/combos.json", encoding="utf-8"))
problems = []
for name in sorted(expected):
    ctx = expected[name]
    if name not in m or m[name]["modelID"] != name or m[name]["limit"]["context"] != ctx:
        problems.append(name)
if not expected:
    problems.append("ide-models-empty")
# A route that fails closed is omitted from the gateway, so it must not be a
# client model either; a route that is servable must be offered to the client.
for gone in combos.get("omitted", []):
    if gone in m:
        problems.append(gone + "-should-be-omitted")
for combo in combos["combos"]:
    name = combo["name"]
    if name in expected and name not in m:
        problems.append(name + "-should-be-in-client-models")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "the serve payload exposes provider status without values"; then
    ok=1
    for marker in "provider_status" "api-keys.yml"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "serve.py missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not expose provider status"; fi
fi

if it "the providers card is in the web UI"; then
    ok=1
    for marker in "cardProviders" "renderProviders" "providersSub" "chip-missing"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "providers card markers missing"; fi
fi

if it "new components name real profiles and verify commands"; then
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
problems = []
for p in sorted(glob.glob("catalog/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    profiles = set(d.get("profiles", {}).keys())
    for g in d.get("categories", []):
        for c in g.get("components", []):
            if c["id"] not in ("zed", "litellm", "opencode-cli", "omniroute", "openhands-docker"):
                continue
            for prof in c.get("profiles", []):
                if prof not in profiles:
                    problems.append(p + ":" + c["id"] + ":" + prof)
            if not c.get("verify"):
                problems.append(p + ":" + c["id"] + ":no-verify")
print(" ".join(problems))
PY
)"
    assert_eq "$bad" ""
fi

if it "ai-coding dry run plans the routing stack"; then
    out="$(bash setup.sh --profile ai-coding --dry-run --yes --no-color 2>&1)"
    ok=1
    for name in "OmniRoute gateway" "LiteLLM tier router" "OpenCode CLI" "Zed"; do
        [[ "$out" == *"$name"* ]] || { ok=0; echo "missing: $name" >&2; }
    done
    if (( ok )); then pass; else fail "routing stack missing from the plan"; fi
fi

if it "autostart and healthcheck files exist and parse"; then
    ok=1
    for f in configuration/autostart/Start-AutoOSStack.sh configuration/healthcheck.sh; do
        [[ -f "$f" ]] || { ok=0; echo "missing: $f" >&2; }
        bash -n "$f" || ok=0
    done
    for f in configuration/autostart/Start-AutoOSStack.ps1 configuration/healthcheck.ps1 \
             configuration/autostart/autoos-stack.service; do
        [[ -f "$f" ]] || { ok=0; echo "missing: $f" >&2; }
    done
    if (( ok )); then pass; else fail "autostart/healthcheck files missing or invalid"; fi
fi

if it "autostart resumes the LiteLLM fallback proxy too"; then
    # litellm cannot read its own .env: the launcher must export it, and only
    # start the proxy when down (never bounce a healthy one on every logon).
    sh_launcher="configuration/autostart/Start-AutoOSStack.sh"
    ps_launcher="configuration/autostart/Start-AutoOSStack.ps1"
    starter="configuration/litellm/start-litellm.ps1"
    ok=1
    grep -q '4000' "$sh_launcher" || { ok=0; echo "sh: no :4000 probe" >&2; }
    grep -q 'start-litellm.sh' "$sh_launcher" || { ok=0; echo "sh: starter not referenced" >&2; }
    grep -q 'PYTHONUTF8' configuration/litellm/start-litellm.sh || { ok=0; echo "sh: PYTHONUTF8 missing" >&2; }
    grep -q 'already up with the current keys' configuration/litellm/start-litellm.sh || { ok=0; echo "sh: no litellm no-op path" >&2; }
    grep -q 'start-litellm.ps1' "$ps_launcher" || { ok=0; echo "ps1: starter not referenced" >&2; }
    grep -q 'already up on 4000' "$ps_launcher" || { ok=0; echo "ps1: no litellm no-op path" >&2; }
    [[ -f "$starter" ]] || { ok=0; echo "missing: $starter" >&2; }
    grep -q '\.env' "$starter" || { ok=0; echo "starter does not read .env" >&2; }
    if (( ok )); then pass; else fail "litellm autostart wiring incomplete"; fi
fi

if it "the systemd unit is installable and opt-in"; then
    ok=1
    for key in '\[Unit\]' '\[Service\]' '\[Install\]' 'ExecStart=' 'WantedBy=default.target' 'Type=oneshot'; do
        grep -q "$key" configuration/autostart/autoos-stack.service || { ok=0; echo "missing: $key" >&2; }
    done
    grep -q 'systemctl --user enable' configuration/README.md || { ok=0; echo "not documented opt-in" >&2; }
    if (( ok )); then pass; else fail "systemd unit incomplete"; fi
fi

if it "healthcheck is log-only without --fix"; then
    ok=1
    grep -q -- '--fix' configuration/healthcheck.sh || ok=0
    # The resume call must only appear inside the --fix branch.
    body_without_fix="$(sed '/if \[\[ $FIX/,/^fi$/d' configuration/healthcheck.sh)"
    [[ "$body_without_fix" == *"Start-AutoOSStack"* ]] && ok=0
    [[ "$body_without_fix" == *"docker start"* || "$body_without_fix" == *"nohup omniroute"* ]] && ok=0
    grep -q '"401"' configuration/healthcheck.sh || ok=0
    for p in 20128 3000 4096 8777; do
        grep -q "$p" configuration/healthcheck.sh || { ok=0; echo "port $p missing" >&2; }
    done
    if (( ok )); then pass; else fail "healthcheck can start things without --fix"; fi
fi

if it "phone URLs are documented without secrets"; then
    ok=1
    for frag in "<tail-ip>:3000" "<tail-ip>:4096" "healthcheck"; do
        grep -q "$frag" docs/troubleshooting.md || { ok=0; echo "missing: $frag" >&2; }
    done
    grep -qE 'sk-[A-Za-z0-9]{10,}' docs/troubleshooting.md && ok=0
    # No machine-local IPs may be committed (repo is public).
    if grep -qE '100\.70\.|192\.168\.178\.59' docs/troubleshooting.md; then ok=0; fi
    if (( ok )); then pass; else fail "phone docs missing or leaking"; fi
fi


if it "secret key-file backups are git-ignored (api-keys.yml.autoos-backup-*, .bak-*), the example is not"; then
    ok=1
    for f in configuration/api-keys.yml configuration/api-keys.yml.autoos-backup-20260101-000000 \
             configuration/api-keys.yml.autoos-backup-20260101-000000-2 configuration/api-keys.yml.bak-20260927134910; do
        git -C "$ROOT" check-ignore -q --no-index "$f" || { ok=0; echo "not ignored: $f" >&2; }
    done
    git -C "$ROOT" check-ignore -q --no-index configuration/api-keys.example.yml && { ok=0; echo "the example file is ignored" >&2; }
    if (( ok )); then pass; else fail "a key-file backup could be committed"; fi
fi

if it "no user-facing page points anyone at the retired agent-skills repo"; then
    # asm-b1: the installer wires skills, MCP servers and keys from this
    # checkout, so a page that sends the user to clone Ch3fUlrich/agent-skills
    # describes a step that no longer exists (and a repo that is retired).
    # Dated records keep their wording — docs/plans, docs/decisions,
    # docs/research and docs/archive say what was true when they were written,
    # and CHANGELOG.md is the history. The Windows catalog entry is listed as
    # a twin to change: its installer still clones (Windows CI is off).
    bad=""
    for f in README.md AGENTS.md CLAUDE.md docs/*.md catalog/linux.json catalog/macos.json \
             lib/linux/install.sh lib/linux/serve.py; do
        [[ -f "$f" ]] || continue
        hit="$(grep -n 'Ch3fUlrich/agent-skills' "$f" || true)"
        [[ -n "$hit" ]] && bad+="$f"$'\n'"$hit"$'\n'
    done
    if [[ -z "$bad" ]]; then pass
    else fail "points at the retired repo:"$'\n'"$bad"; fi
fi
