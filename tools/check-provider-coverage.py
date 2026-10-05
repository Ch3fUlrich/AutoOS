#!/usr/bin/env python3
"""check-provider-coverage.py -- FREEKEYS-2 independent coverage check.

Read-only against the OmniRoute gateway. For the canonical 47-provider list
(operator names from work/L1-main/FREEKEYS2-COMPLETION-ORDER.md) it prints:

    operator provider -> gateway prefix(es) -> live model count(s) -> in a combo?

Data sources:
  * live gateway catalogue : GET {base}/v1/models
                             (client key: env AUTOOS_OMNIROUTE_KEY -- required)
  * combo usage            : <repo>/configuration/omniroute/combos.json
                             (combos[].models; `omitted` entries are noted,
                             never counted as usage)

The key value is never printed; model ids and counts are not secrets.
Exit codes: 0 ok | 2 no/placeholder key | 3 gateway unreachable | 4 combos unreadable.

Usage:
    python3 tools/check-provider-coverage.py [--json]
        [--base-url http://omniroute:20128] [--combos PATH]

Written 2026-09-30 by L2-general for routing-00's FREEKEYS-2 verification kit.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter

# Canonical 47-provider list, in the completion order's table order.
# (operator provider name, [gateway prefixes], note)
CANONICAL = [
    ("groq", ["groq"], ""),
    ("google_ai_studio", ["gemini"], ""),
    ("mistral", ["mistral"], ""),
    ("cloudflare_workers_ai", ["cloudflare-ai", "cf", "cfp"], ""),
    ("cohere", ["cohere"], ""),
    ("hugging_face", ["huggingface", "hf"], ""),
    ("cheapinference", ["cheaperinference", "cinf"], ""),
    ("cerebras", ["cerebras"], ""),
    ("SambaNova", [], "zero models -- see PROVIDER-COVERAGE.md"),
    ("deepseek", ["deepseek", "ds"], ""),
    ("meta", ["meta-api"], ""),
    ("openrouter", ["openrouter"], ""),
    ("free_ai", ["free-ai"], ""),
    ("api_airforce", ["api-airforce", "af"], ""),
    ("llm7", ["llm7"], ""),
    ("morph", ["morph"], ""),
    ("bazaarlink", ["bazaarlink", "bzl"], ""),
    ("arcee", [], "zero models -- see PROVIDER-COVERAGE.md"),
    ("bluesminds", [], "zero models -- see PROVIDER-COVERAGE.md"),
    ("agentrouter", ["agentrouter"], ""),
    ("navyai", ["navy"], ""),
    ("novita_ai", ["novita"], ""),
    ("scaleway", ["scaleway", "scw"], ""),
    ("nebius", ["nebius"], ""),
    ("deepinfra", ["deepinfra"], ""),
    ("together_ai", ["together"], ""),
    ("nscale", ["nscale"], ""),
    ("vertex_ai", ["vertex"], ""),
    ("siliconflow", ["siliconflow"], ""),
    ("sealion", ["sealion"], ""),
    ("routeway", ["routeway"], ""),
    ("requesty", ["requesty"], ""),
    ("aion_labs", ["aion"], ""),
    ("agnes_ai", ["agnes"], ""),
    ("pollinations", ["pollinations", "pol"], ""),
    ("g4f", ["g4fpoll", "g4f-pollinations"], ""),
    ("kilo", ["kilo-gateway", "kg"], ""),
    ("ainative", ["ainative"], ""),
    ("ovhcloud", ["ovhcloud", "ovh"], ""),
    ("felo", ["felo"], ""),
    ("uncloseai", ["unc"], ""),
    ("opencode", ["opencode", "oc"], ""),
    ("ai_horde", ["aihorde"], ""),
    ("zen", ["opencode-zen"], ""),
    ("z_ai", ["zc"], "GLM-5.3/5.2 legs"),
    ("devin", ["devin"], ""),
    ("qoder_pat", ["qoder"], ""),
]
assert len(CANONICAL) == 47, "canonical list drifted from 47 rows"


def get_key():
    key = (os.environ.get("AUTOOS_OMNIROUTE_KEY") or "").strip()
    if not key or key.startswith("REPLACE_WITH_"):
        print("error: AUTOOS_OMNIROUTE_KEY is not set in this environment",
              file=sys.stderr)
        sys.exit(2)
    return key


def fetch_models(base, key):
    req = urllib.request.Request(base.rstrip("/") + "/v1/models")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            doc = json.load(resp)
    except urllib.error.HTTPError as exc:
        print("error: GET %s/v1/models -> HTTP %s" % (base, exc.code),
              file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001 - report, never leak the key
        print("error: GET %s/v1/models failed: %s" % (base, exc),
              file=sys.stderr)
        sys.exit(3)
    data = doc.get("data") if isinstance(doc, dict) else doc
    ids = []
    for m in data or []:
        mid = m.get("id") if isinstance(m, dict) else None
        if isinstance(mid, str) and mid:
            ids.append(mid)
    return ids


def combo_usage(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("error: combos file unreadable (%s): %s" % (path, exc),
              file=sys.stderr)
        sys.exit(4)
    used = {}
    combos = doc.get("combos") or []
    for combo in combos:
        name = combo.get("name") or "?"
        for ref in combo.get("models") or []:
            if isinstance(ref, str) and "/" in ref:
                used.setdefault(ref.split("/", 1)[0], []).append(name)
    omitted = [r for r in doc.get("omitted") or [] if isinstance(r, str)]
    return combos, used, omitted


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://omniroute:20128")
    ap.add_argument("--combos", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "configuration", "omniroute", "combos.json"))
    ap.add_argument("--json", action="store_true",
                    help="emit only a machine-readable JSON document (no table)")
    args = ap.parse_args()

    key = get_key()
    ids = fetch_models(args.base_url, key)
    unscoped = [i for i in ids if "/" not in i]
    counts = Counter(i.split("/", 1)[0] for i in ids if "/" in i)
    combos, used, omitted = combo_usage(os.path.normpath(args.combos))

    rows = []
    for name, prefixes, note in CANONICAL:
        n = [counts.get(p, 0) for p in prefixes]
        combo_names = []
        for p in prefixes:
            combo_names.extend(used.get(p, []))
        used_any = bool(combo_names)
        rows.append({
            "provider": name, "prefixes": prefixes, "counts": n,
            "used": used_any, "combos": sorted(set(combo_names)),
            "note": note,
        })

    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    used_prefixes = sorted(used)
    zero = [r["provider"] for r in rows
            if not any(r["counts"])]
    with_models = [r["provider"] for r in rows if any(r["counts"])]
    unused = [r["provider"] for r in rows
              if any(r["counts"]) and not r["used"]]

    if args.json:
        print(json.dumps({
            "generated_at": now, "gateway": args.base_url,
            "models_total": len(ids), "prefixes_total": len(counts),
            "unscoped_ids": unscoped,
            "combos_file": os.path.normpath(args.combos),
            "combos_total": len(combos), "used_prefixes": used_prefixes,
            "providers": rows, "zero_model": zero, "unused": unused,
        }, indent=2, sort_keys=True))
        return

    w_name = max(len("operator provider"), max(len(r["provider"]) for r in rows))
    w_pref = max(len("gateway prefix(es)"),
                 max(len(", ".join(r["prefixes"]) or "(none)") for r in rows))
    w_cnt = max(len("live models"),
                max(len(", ".join(str(c) for c in r["counts"]) or "0")
                    for r in rows))
    print("# FREEKEYS-2 provider coverage check -- %s" % now)
    print("# gateway : %s/v1/models -- %d model ids, %d prefixes%s"
          % (args.base_url.rstrip("/"), len(ids), len(counts),
             (", plus %d unscoped id(s): %s" % (len(unscoped),
              ", ".join(unscoped[:3]))) if unscoped else ""))
    print("# combos  : %s -- %d combos reference %d prefixes: %s"
          % (os.path.normpath(args.combos), len(combos), len(used_prefixes),
             ", ".join(used_prefixes)))
    print()
    hdr = "%-*s  %-*s  %-*s  %-10s  %s" % (
        w_name, "operator provider", w_pref, "gateway prefix(es)",
        w_cnt, "live models", "in a combo?", "notes")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        pref = ", ".join(r["prefixes"]) or "(none)"
        cnt = ", ".join(str(c) for c in r["counts"]) or "0"
        note = r["note"]
        if not note and not any(r["counts"]):
            note = "zero models on gateway"
        print("%-*s  %-*s  %-*s  %-10s  %s" % (
            w_name, r["provider"], w_pref, pref, w_cnt, cnt,
            "yes" if r["used"] else "no", note))
    print()
    print("summary: %d providers -- %d with live models, %d zero-model (%s);"
          " %d with models but in no combo"
          % (len(rows), len(with_models), len(zero),
             ", ".join(zero) if zero else "none", len(unused)))
    used_by_47 = [r["provider"] for r in rows if r["used"]]
    print("used by >=1 combo: %d of 47 -- %s"
          % (len(used_by_47), ", ".join(used_by_47)))
    if omitted:
        print("omitted[] refs (excluded by design, not usage): %s"
              % ", ".join(omitted))

if __name__ == "__main__":
    main()
