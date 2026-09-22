"""Vendored OpenHands profiles match the catalog snapshot.

Run: python tests/check-vendored.py  (cwd = repo root)
Mirrors the "vendored openhands profiles match the catalog snapshot" and
"vendored agent profiles reference existing llm profiles" cases in
tests/run-tests.sh for machines without bash/python3.
Exit non-zero with a diagnostic on any mismatch.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

models = {m["id"]: m for m in
          json.load(open(os.path.join(ROOT, "catalog", "llm-models.json"),
                         encoding="utf-8"))["models"]}
# Legacy alias: muse-spark-1.3-contributor.json is the vendored template for
# the muse-spark catalog entry (same shape as the deleted muse-spark-1.3
# alias; only the contributor variant is kept).
models["muse-spark-1.3-contributor"] = models["muse-spark"]
price_dst = {"paid_input_price": "paid_input_cost_per_token",
             "paid_output_price": "paid_output_cost_per_token",
             "cache_read_price": "cache_read_cost_per_token"}

failures = []
files = sorted(glob.glob(os.path.join(ROOT, "openhands", "profiles", "*.json")))
if not files:
    failures.append("no vendored profiles")
for path in files:
    name = os.path.basename(path)[:-5]
    if name not in models:
        failures.append(f"{path}: unknown model {name}")
        continue
    m = models[name]
    p = json.load(open(path, encoding="utf-8"))
    want_model = (("openrouter/" + m["openrouter_id"]) if m.get("openrouter_id")
                  else m["direct"]["model"])
    checks = {"model": want_model,
              "max_input_tokens": m["context"],
              "max_output_tokens": m["output"],
              "input_cost_per_token": m["input_price"],
              "output_cost_per_token": m["output_price"],
              "reasoning_effort": ("high" if m.get("reasoning") else "none")}
    if m.get("direct", {}).get("base_url"):
        checks["base_url"] = m["direct"]["base_url"]
    for opt, dst in price_dst.items():
        if m.get(opt) is not None:
            checks[dst] = m[opt]
    for k, v in checks.items():
        if p.get(k) != v:
            failures.append(f"{path}: {k}={p.get(k)!r} != catalog {v!r}")
    if "api_key" in p:
        failures.append(f"{path}: must not vendor secrets")
    if not m.get("reasoning"):
        if p.get("enable_encrypted_reasoning") is not False:
            failures.append(f"{path}: thinking not opted out")
        if p.get("extended_thinking_budget") is not None:
            failures.append(f"{path}: thinking budget not nulled")

# Gateway tier ids (omniroute-tier*, litellm-tier*) are generated at
# install/start time from configuration/openhands/tier-profiles.json, not
# vendored here - but agent profiles may reference them.
spec = json.load(open(os.path.join(ROOT, "configuration", "openhands",
                                   "tier-profiles.json"), encoding="utf-8"))
llm = {os.path.basename(p)[:-5]
       for p in glob.glob(os.path.join(ROOT, "openhands", "profiles", "*.json"))}
llm |= {t["id"] for t in spec["tiers"]}
for path in sorted(glob.glob(os.path.join(ROOT, "openhands",
                                           "agent-profiles", "*.json"))):
    ref = json.load(open(path, encoding="utf-8")).get("llm_profile_ref")
    if ref is not None and ref not in llm:
        failures.append(f"{path}: llm_profile_ref {ref!r} has no vendored profile")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print(f"vendored profiles OK ({len(files)} llm templates)")
