# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Shared LLM model catalogue (single source of truth) ──────────────────
describe "llm models"

if it "llm-models.json is valid and every id is unique"; then
    if python3 - <<'PY'
import json, sys
doc = json.load(open("catalog/llm-models.json", encoding="utf-8"))
models = doc["models"]
assert len(models) >= 18, f"expected >= 18 models, got {len(models)}"
ids = [m["id"] for m in models]
dupes = {i for i in ids if ids.count(i) > 1}
assert not dupes, f"duplicate ids: {dupes}"
for m in models:
    assert m.get("openrouter_id") or m.get("direct"), f"{m['id']}: neither openrouter_id nor direct"
    assert isinstance(m["context"], int) and isinstance(m["output"], int), f"{m['id']}: bad windows"
PY
    then pass; else fail "llm-models.json invalid"; fi
fi

if it "the installers project every shared model instead of hardcoding"; then
    # Single-source/DRY: no model id or price may appear as a literal in the
    # installers. Everything is projected from llm-models.json.
    if python3 - <<'PY'
import json, re, sys
models = json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]
bad = []
for path in ("lib/linux/install.sh", "lib/windows/AutoOS.Install.psm1"):
    src = open(path, encoding="utf-8").read()
    for m in models:
        if m.get("openrouter_id"):
            oid = m["openrouter_id"]
            # projected via _openrouter_models()/Get-OpenRouterModelEntry, never a literal key
            if re.search(r"""['"]""" + re.escape(oid) + r"""['"]\s*[:=]""", src):
                bad.append(f"{path}: hardcoded openrouter id {oid}")
        for price_key in ("paid_input_price", "paid_output_price"):
            if price_key in m and re.search(r"\b" + re.escape(repr(m[price_key])) + r"\b", src):
                bad.append(f"{path}: hardcoded price {m[price_key]} from {m['id']}")
if bad:
    print("\n".join(bad)); sys.exit(1)
PY
    then pass; else fail "installers hardcode shared model data (see above)"; fi
fi

if it "the openhands projector covers every shared model"; then
    if python3 - <<'PY'
import json, re, sys
models = json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]
for path in ("lib/linux/install.sh", "lib/windows/AutoOS.Install.psm1"):
    src = open(path, encoding="utf-8").read()
    calls = set()
    for call in re.findall(r"_profile_for\(([^)]*)\)", src):
        calls.update(re.findall(r"['\"]([^'\"]+)['\"]", call))
    missing = {m["id"] for m in models if m["id"] not in calls}
    if missing:
        print(f"{path}: unprojected models: {sorted(missing)}"); sys.exit(1)
PY
    then pass; else fail "openhands projector misses a shared model (see above)"; fi
fi

if it "the vendored openhands profiles match the catalog snapshot"; then
    # openhands/profiles/*.json carry a committed copy of the catalog numbers
    # (windows, prices) plus the model id and thinking flags. The installers
    # overlay fresh catalog numbers at setup (catalog wins), but a drifted
    # template would ship stale prices to anyone reading the repo, so any
    # mismatch fails here. api_key is injected at install time and absent.
    if python3 - <<'PY'
import json, glob, os, sys
models = {m["id"]: m for m in json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]}
# Legacy alias: muse-spark-1.3-contributor.json is the vendored template for
# the muse-spark catalog entry (only the contributor variant is kept).
models["muse-spark-1.3-contributor"] = models["muse-spark"]
price_dst = {"paid_input_price": "paid_input_cost_per_token",
             "paid_output_price": "paid_output_cost_per_token",
             "cache_read_price": "cache_read_cost_per_token"}
files = sorted(glob.glob("openhands/profiles/*.json"))
assert files, "no vendored profiles"
for path in files:
    name = os.path.basename(path)[:-5]
    assert name in models, f"{path}: unknown model {name}"
    m, p = models[name], json.load(open(path, encoding="utf-8"))
    want_model = ("openrouter/" + m["openrouter_id"]) if m.get("openrouter_id") else m["direct"]["model"]
    checks = {"model": want_model, "max_input_tokens": m["context"],
              "max_output_tokens": m["output"], "input_cost_per_token": m["input_price"],
              "output_cost_per_token": m["output_price"],
              "reasoning_effort": ("high" if m.get("reasoning") else "none")}
    if m.get("direct", {}).get("base_url"):
        checks["base_url"] = m["direct"]["base_url"]
    for opt, dst in price_dst.items():
        if m.get(opt) is not None:
            checks[dst] = m[opt]
    for k, v in checks.items():
        assert p.get(k) == v, f"{path}: {k}={p.get(k)!r} != catalog {v!r}"
    assert "api_key" not in p, f"{path}: must not vendor secrets"
    if not m.get("reasoning"):
        assert p.get("enable_encrypted_reasoning") is False, f"{path}: thinking not opted out"
        assert p.get("extended_thinking_budget") is None, f"{path}: thinking budget not nulled"
PY
    then pass; else fail "vendored openhands profiles drifted from catalog (see above)"; fi
fi

