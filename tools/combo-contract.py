#!/usr/bin/env python3
"""Combo contract gate (TORDER 2026-10-01).

For every omniroute combo, asserts:
  (a) rendered client limit == registry route context == combos.json context
      (via CONTEXT_LADDER floors, not exact ints: 1048576 and 1000000 both
      render "1M"; 131072 renders "128k").
  (b) order trial->free->credits->paid with paid last and no free leg after
      a paid leg (effective tier via model override else provider tier;
      subscription counts as paid; trial (if any) first).
      Paid last: a combo containing a paid leg must end paid (free-only and
      single-credit combos exempt: all-free or all-credit).
      DeepSeek last: a combo containing deepseek/deepseek-flash must end with it.
      OpenRouter: every servable openrouter leg MUST end in ":free"; every
      declared paid openrouter/* leg that survives in routes.*.legs MUST be
      gated in routes.<id>.unavailable_legs (t1-orchestrator-clean is the
      documented declared+gated exception so it stays omitted, not legless).
  (c) every leg resolves via resolve_leg; live-catalog existence is reported
      per leg when a key is available (gateway reachable) but warns-only:
      apply.ps1/apply.sh skip unknown models with a warning (exit 0), so the
      gate must not fail-closed on dead legs (huggingface/antigravity NOT_FOUND)
      or TASK8 apply -DryRun exit 0 would break. Unresolvable legs fail.
  (d) t1 routes (t1-orchestrator, -free-only, -paid, -clean) render 1M and
      every t1 leg window >= T1_MIN_WINDOW (600000, operator update 3);
      t2/t3 (t2-worker, -free-only, t3-driver, -free-only) render the 128k
      clamp (lowest implementer window, do not raise) with sub-1M legs allowed.

Exit 0 with per-combo verdicts when all fail-closed assertions pass;
exit 1 naming the failing combo/assertion otherwise.
Stdlib only.
"""
from __future__ import annotations
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
IDE_PATH = ROOT / "catalog" / "ide-models.json"

# Operator update (3): t1 leg threshold, explicit constant.
T1_MIN_WINDOW = 600000
T1_ROUTES = ("t1-orchestrator", "t1-orchestrator-free-only",
             "t1-orchestrator-paid", "t1-orchestrator-clean")
T2T3_ROUTES = ("t2-worker", "t2-worker-free-only",
               "t3-driver", "t3-driver-free-only")

CONTEXT_LADDER = [("1M", 1000000), ("512k", 512000), ("256k", 256000),
                  ("200k", 200000), ("128k", 128000), ("64k", 64000),
                  ("32k", 32000), ("16k", 16000), ("8k", 8000),
                  ("4k", 4000), ("2k", 2000), ("1k", 1000)]

def label_to_tokens(label):
    if not isinstance(label, str):
        return None
    for rung, floor in CONTEXT_LADDER:
        if label.strip().lower() == rung.lower():
            return floor
    return None

def tokens_to_label(tokens):
    for rung, floor in CONTEXT_LADDER:
        if tokens >= floor:
            return rung
    return "%dk" % max(1, int(tokens) // 1000)

def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

def resolve_leg(leg, registry):
    if not isinstance(leg, str) or "/" not in leg:
        raise ValueError("leg %r is not a provider/model string" % (leg,))
    prefix, _, model_id = leg.partition("/")
    providers = registry.get("providers") or {}
    pid = prefix if prefix in providers else None
    if pid is None:
        for cand, prov in providers.items():
            if isinstance(prov, dict) and prov.get("omniroute_id") == prefix:
                pid = cand
                break
    if pid is None:
        raise ValueError("leg %r: no provider matches prefix %r" % (leg, prefix))
    if model_id not in (registry.get("models") or {}):
        raise ValueError("leg %r: no model matches %r" % (leg, model_id))
    return pid, model_id

def registry_ref(ref, registry):
    if not isinstance(ref, str) or "/" not in ref:
        return ref
    ns, mid = ref.split("/", 1)
    providers = registry.get("providers") or {}
    if ns in providers:
        return ref
    owners = [pid for pid, p in providers.items()
              if isinstance(p, dict) and p.get("model_prefix") == ns]
    if len(owners) != 1:
        return ref
    return "%s/%s" % (owners[0], mid)

def leg_tier(registry, leg):
    rleg = registry_ref(leg, registry)
    pid, mid = resolve_leg(rleg, registry)
    model = (registry.get("models") or {}).get(mid) or {}
    prov = (registry.get("providers") or {}).get(pid) or {}
    return model.get("tier") or prov.get("tier") or "paid"

def leg_window(registry, leg):
    rleg = registry_ref(leg, registry)
    try:
        _, mid = resolve_leg(rleg, registry)
    except ValueError:
        return None
    m = (registry.get("models") or {}).get(mid) or {}
    v = m.get("context_advertised")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None

TIER_ORDER = {"trial": 0, "free": 1, "credit": 2, "paid": 3, "subscription": 3}

def main():
    registry = load_json(REGISTRY_PATH)
    combos_doc = load_json(COMBOS_PATH)
    ide_doc = load_json(IDE_PATH)
    ide_by_id = {}
    for m in (ide_doc.get("models") or []):
        if isinstance(m, dict) and m.get("id"):
            ide_by_id[m["id"]] = m
    combos_by_name = {c["name"]: c for c in combos_doc.get("combos", [])}
    routes = registry.get("routes") or {}
    failures = []
    verdicts = []
    # live catalog, warn-only
    live_ids = None
    live_note = "SKIP (no key/gateway)"
    try:
        keys_path = ROOT / "configuration" / "api-keys.yml"
        # main checkout key fallback (read-only, never printed)
        main_keys = pathlib.Path(r"C:\Users\mauls\Documents\Code\AutoOS\configuration\api-keys.yml")
        key = None
        for kp in (keys_path, main_keys):
            try:
                for line in kp.read_text(encoding="utf-8", errors="replace").splitlines():
                    t = line.strip()
                    if not t or t.startswith("#") or ":" not in t:
                        continue
                    k, v = t.split(":", 1)
                    if k.strip().lower() == "omniroute":
                        v = v.strip().strip('"').strip("'")
                        if v and not v.startswith("REPLACE_WITH_"):
                            key = v
                            break
            except OSError:
                continue
            if key:
                break
        if key:
            req = urllib.request.Request("http://127.0.0.1:20128/v1/models",
                                         headers={"Authorization": "Bearer " + key})
            import socket
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
            rows = data.get("data", [])
            live_ids = {r["id"] for r in rows if isinstance(r, dict) and "id" in r}
            live_note = "LIVE %d models" % len(live_ids)
    except Exception as exc:
        live_ids = None
        live_note = "SKIP (live unreadable: %s)" % type(exc).__name__

    for name in sorted(combos_by_name):
        combo = combos_by_name[name]
        route = routes.get(name)
        if route is None:
            failures.append("%s: combo has no routes.%s" % (name, name))
            verdicts.append("%s: FAIL no route" % name)
            continue
        per = []
        # (a) contexts via ladder
        reg_ctx = ((route.get("surfaces") or {}).get("omniroute") or {}).get("context")
        combo_label = combo.get("context")
        combo_floor = label_to_tokens(combo_label)
        if isinstance(reg_ctx, int) and combo_floor is not None:
            reg_label = tokens_to_label(reg_ctx)
            if reg_label != combo_label:
                # allow 1048576 (1M) vs 1000000 (1M): both render 1M; compare floors
                if label_to_tokens(reg_label) != combo_floor:
                    failures.append("%s: (a) registry context %r (%s) != combos label %r" % (name, reg_ctx, reg_label, combo_label))
                    per.append("a-registry-mismatch")
        ide = ide_by_id.get(name)
        if ide is not None and isinstance(reg_ctx, int):
            ide_ctx = ide.get("context")
            if isinstance(ide_ctx, int) and tokens_to_label(ide_ctx) != tokens_to_label(reg_ctx):
                failures.append("%s: (a) ide context %r != registry %r" % (name, ide_ctx, reg_ctx))
                per.append("a-ide-mismatch")
        # (b) order
        legs = combo.get("models") or []
        tiers = []
        for leg in legs:
            try:
                tiers.append(leg_tier(registry, leg))
            except ValueError as exc:
                failures.append("%s: (c) unresolvable leg %r (%s)" % (name, leg, exc))
                per.append("c-unresolvable")
                tiers.append("paid")
        order = [TIER_ORDER.get(t, 3) for t in tiers]
        if order != sorted(order):
            failures.append("%s: (b) order %s not trial->free->credits->paid %s" % (name, tiers, legs))
            per.append("b-order")
        # no free after paid
        paid_idx = [i for i, t in enumerate(tiers) if t in ("paid", "subscription")]
        free_idx = [i for i, t in enumerate(tiers) if t == "free"]
        if paid_idx and free_idx and max(paid_idx) < max(free_idx) and min(free_idx) < max(paid_idx):
            # free leg after a paid leg (free-only all-free exempt: no paid_idx)
            # credit-single all-credit exempt: no free_idx
            if not (len(paid_idx) == 0 or len(free_idx) == 0):
                # check any free after first paid
                first_paid = min(paid_idx)
                if any(i > first_paid for i in free_idx):
                    failures.append("%s: (b) free leg after paid leg %s" % (name, legs))
                    per.append("b-free-after-paid")
        # paid last (exempt all-free, all-credit)
        if tiers and any(t in ("paid", "subscription") for t in tiers):
            if tiers[-1] not in ("paid", "subscription"):
                failures.append("%s: (b) paid not last %s" % (name, tiers))
                per.append("b-paid-not-last")
        # deepseek last
        low = [l.lower() for l in legs]
        if any("deepseek/deepseek-flash" in l for l in low):
            if not low[-1].endswith("deepseek/deepseek-flash"):
                failures.append("%s: (b) deepseek not last %s" % (name, legs))
                per.append("b-deepseek-not-last")
        # openrouter servable :free
        for leg in legs:
            if leg.startswith("openrouter/") and not leg.endswith(":free"):
                failures.append("%s: (b-or) servable paid openrouter leg %r must end :free" % (name, leg))
                per.append("b-or-paid-servable")
        # (c) resolve + live warn-only
        for leg in legs:
            try:
                resolve_leg(registry_ref(leg, registry), registry)
            except ValueError as exc:
                failures.append("%s: (c) unresolvable %r" % (name, leg))
                per.append("c-unresolvable")
                continue
            if live_ids is not None:
                # gateway spelling as served
                gid = leg
                # registry legs already gateway spelling in combos; check both spellings
                if gid not in live_ids:
                    per.append("c-live-missing:%s" % leg)
        # (d) t1/t2t3
        if name in T1_ROUTES:
            if combo_label != "1M":
                failures.append("%s: (d) t1 must render 1M, got %r" % (name, combo_label))
                per.append("d-t1-context")
            for leg in legs:
                w = leg_window(registry, leg)
                if w is not None and w < T1_MIN_WINDOW:
                    failures.append("%s: (d) t1 leg %r window %r < %d" % (name, leg, w, T1_MIN_WINDOW))
                    per.append("d-t1-window")
        if name in T2T3_ROUTES:
            if combo_label != "128k":
                failures.append("%s: (d) t2/t3 must render 128k clamp, got %r" % (name, combo_label))
                per.append("d-t2t3-context")
        verdicts.append("%s: %s [%s]" % (name, "FAIL " + ",".join(per) if per and any(not p.startswith("c-live-missing") for p in per) else "PASS", live_note))
    # declared paid openrouter must be gated
    for rid, route in routes.items():
        for leg in (route.get("legs") or []):
            if leg.startswith("openrouter/") and not leg.endswith(":free"):
                un = (route.get("unavailable_legs") or {})
                e = un.get(leg)
                if not (isinstance(e, dict) and e.get("available") is False):
                    failures.append("%s: (b-or-declared) paid %r not gated in unavailable_legs" % (rid, leg))
    # t1-clean may keep declared+gated (documented exception); all other paid openrouter legs must be gone
    # (already covered: any declared paid not gated fails; t1-clean is gated so passes)
    print("\n".join(verdicts))
    if failures:
        print("\nFAILURES:")
        for f in failures:
            # never print keys; failures contain no secrets (ids only)
            print(" - " + f)
        return 1
    print("\ncontract PASS: %d combos (%s)" % (len(combos_by_name), live_note))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
