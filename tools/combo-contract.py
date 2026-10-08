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
      gated in routes.<id>.unavailable_legs (l1-orchestrator-clean is the
      documented declared+gated exception so it stays omitted, not legless).
  (c) every leg resolves via resolve_leg; live-catalog existence is reported
      per leg when a key is available (gateway reachable) but warns-only:
      apply.ps1/apply.sh skip unknown models with a warning (exit 0), so the
      gate must not fail-closed on dead legs (huggingface/antigravity NOT_FOUND)
      or TASK8 apply -DryRun exit 0 would break. Unresolvable legs fail.
  (d) t1 routes (l1-orchestrator, -free-only, -paid, -clean) render 1M and
      every t1 leg window >= T1_MIN_WINDOW (600000, operator update 3);
      t2/t3 (l2-worker, -free-only, l3-driver, -free-only) render the 128k
      clamp (lowest implementer window, do not raise) with sub-1M legs allowed.
  (e) two distinct servable providers per agentic combo, except the named
      SINGLE_PROVIDER_EXEMPTIONS below (same style as
      tests/test_registry.py CLEAN_ROUTE_EXEMPTIONS): l1-orchestrator-free-only
      is deliberately single-provider (L0 D-TORDER-2 ACCEPT) and passes only
      via that exemption + its registry constraint note; a future
      single-provider route without such a note still fails.

D1 adds four D-657/D-658 checks (AO-DENYLEGS, contract only — combo *chains*
are D2's job), each its own function so one failure names its rule:

  (f) cache gate: an orchestrator-layer combo (id starting l0-/l1-/l2-) may
      hold no leg whose leg_prompt_cache is not "true"/"documented" — an
      input-heavy L0–L2 chain on an uncached leg is the cost leak D-657 prices.
  (g) provider diversity: two consecutive legs must not share a provider's
      rate-limit domain (§7b delta a); bzl/bazaarlink and ovh/ovhcloud are one
      domain each, so a combo cannot burn a 429 and fall through to the same
      429.
  (h) price gate: a leg whose effective tier is credit/paid must carry a price
      row — price_in/price_out or a price_source — else the grant is uncostable
      and bills $0 while draining invisibly (§7b delta b, fail closed).
  (i) leg bans: no qoder leg (spawner-client only, §7b), no paid deepseek
      (deepseek/*), no paid meta_api muse; agy is never position 1 (ToS risk,
      never the head).

Those four run only for the combos a reviewer has opted in: a combo id listed
in combos.json's `d657_combos`, or every combo under --strict-d657. Until D2
re-chains the routes the list is absent, which keeps main green while
`--strict-d657` still reports the full violation set.

Exit 0 with per-combo verdicts when all fail-closed assertions pass;
exit 1 naming the failing combo/assertion otherwise.
Stdlib only.
"""
from __future__ import annotations
import importlib.util
import json
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
IDE_PATH = ROOT / "catalog" / "ide-models.json"


def _load_registry_tool():
    """tools/registry.py by path — leg_prompt_cache()/leg_price() are owned
    there (one reader of a caching/price row, not a second copy here)."""
    spec = importlib.util.spec_from_file_location(
        "autoos_registry", str(ROOT / "tools" / "registry.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY_TOOL = _load_registry_tool()

# Operator update (3): t1 leg threshold, explicit constant.
T1_MIN_WINDOW = 600000
T1_ROUTES = ("l1-orchestrator", "l1-orchestrator-free-only",
             "l1-orchestrator-paid", "l1-orchestrator-clean")
T2T3_ROUTES = ("l2-worker", "l2-worker-free-only",
               "l3-driver", "l3-driver-free-only")
AGENTIC_ROUTES = ("l1-orchestrator", "l1-orchestrator-free-only",
                  "l2-worker", "l2-worker-free-only",
                  "l3-driver", "l3-driver-free-only")

# The one documented, deliberate single-provider exception (L0 D-TORDER-2
# ACCEPT 2026-10-01, same style as CLEAN_ROUTE_EXEMPTIONS): no verified
# genuinely-free live 1M tool-calling second provider exists, so
# l1-orchestrator-free-only stays single-provider on gemini only and
# l1-orchestrator carries the real load on vertex credits. Passes only via
# this entry PLUS its routes.l1-orchestrator-free-only.$comment constraint
# note (deliberately single-provider, free-ai premium_requires_purchase,
# openrouter :free <=262k, huggingface unusable, vertex credits); a future
# single-provider route without such a note still fails rule (e).
SINGLE_PROVIDER_EXEMPTIONS = {
    "l1-orchestrator-free-only": (
        "L0 D-TORDER-2 ACCEPT: deliberately single-provider "
        "(gemini/gemini-3.8-flash only); no verified genuinely-free live 1M "
        "tool-calling second provider (free-ai premium_requires_purchase; "
        "openrouter :free <=262k sub-1M; huggingface unusable); real load on "
        "l1-orchestrator vertex credits."
    ),
}

# Phrases the registry constraint note must contain (rule (e) fails if missing).
REQUIRED_SINGLE_PROVIDER_NOTE_PHRASES = (
    "deliberately single-provider",
    "premium_requires_purchase",
    "huggingface",
    "vertex",
)

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

# ---- D1 / D-657 checks (f)-(i): each is one function, each names its rule ----

# The orchestrator layers, by combo-id prefix: L0–L2 are input-heavy and run
# long-lived prefixes, so every leg of them must be a cached leg (spec §2
# "Caching: L0–L2 legs only after cache probe"; spec §8 DONE criteria).
ORCHESTRATOR_ID_PREFIXES = ("l0-", "l1-", "l2-")
CACHEABLE_VERDICTS = ("true", "documented")

# A credit/paid leg with no price row cannot be costed at all: the resolver
# refuses it (schema `price_source`), so a combo that declares it is a combo
# whose tail leg bills $0 while it drains. Compared against the leg's EFFECTIVE
# tier, the same reading rule (b) uses.
PRICE_GATE_TIERS = ("credit", "paid")

# §7b: the Qoder PAT in OmniRoute does not work, so qoder/* answers only as the
# spawner's own client — a gateway leg of a combo can never serve.
SPAWNER_ONLY_PROVIDERS = ("qoder_ai",)
# §8: paid deepseek and paid meta-api muse leave every chain; the free spellings
# (…-free, a :free suffix, another provider's copy) are not this ban. The value
# is the model-id needle that is banned ("muse"); None bans the whole provider.
BAN_PAID_TIER_PROVIDERS = {"deepseek": None, "meta_api": "muse"}
# §1/§5: agy through a gateway is the reported ban cause, so it may ride as a
# non-head leg but never opens a chain.
NEVER_HEAD_PROVIDERS = ("antigravity",)


def provider_domain(registry, leg):
    """The rate-limit domain a leg belongs to: its provider id, whatever spelling
    the leg carries — provider key, ``omniroute_id``, or a declared
    ``model_prefix``/``aliases`` namespace (bzl == bazaarlink, ovh == ovhcloud).

    Two spellings of one provider are one bucket of 429s, which is exactly what
    the diversity rule (g) counts; an unknown namespace is its own domain
    (never merged with a provider on a coincidence of spelling)."""
    prefix, sep, _ = (leg or "").partition("/")
    if not sep:
        return leg
    namespaces = {}
    for pid, provider in (registry.get("providers") or {}).items():
        if not isinstance(provider, dict):
            continue
        claimed = [pid, provider.get("omniroute_id"), provider.get("model_prefix")]
        claimed += list(provider.get("aliases") or [])
        for namespace in claimed:
            if namespace:
                namespaces.setdefault(namespace, set()).add(pid)
    owners = namespaces.get(prefix) or set()
    if prefix in owners:          # an exact provider key always wins
        return prefix
    if len(owners) == 1:          # one namespace, one provider: the alias case
        return next(iter(owners))
    try:
        pid, _ = resolve_leg(registry_ref(leg, registry), registry)
        return pid
    except ValueError:
        return prefix


def leg_resolves(registry, leg):
    """(provider_id, model_id) or None — rule (c) is the one that reports it."""
    try:
        return resolve_leg(registry_ref(leg, registry), registry)
    except ValueError:
        return None


def check_cache_gate(registry, combo_id, legs):
    """(f) an orchestrator-layer combo (l0-/l1-/l2-) has no uncached leg.

    Read per leg through tools/registry.py's leg_prompt_cache(), which answers
    the PROVIDER+MODEL verdict first — so a Vertex leg's documented caching no
    longer clears an AI-Studio leg of the same model id (the D1 judge nit)."""
    if not combo_id.startswith(ORCHESTRATOR_ID_PREFIXES):
        return []
    failures = []
    for leg in legs:
        verdict = REGISTRY_TOOL.leg_prompt_cache(registry, leg)
        if verdict not in CACHEABLE_VERDICTS:
            failures.append("%s: (f) leg %r is prompt_cache=%r, not %s"
                            % (combo_id, leg, verdict,
                               "/".join(CACHEABLE_VERDICTS)))
    return failures


def check_provider_diversity(registry, combo_id, legs):
    """(g) consecutive legs change rate-limit domain (§7b delta a).

    A fallback chain that steps from vertex_ai to vertex_ai is not a fallback:
    the first 429 predicts the second. Only neighbours are compared — a combo
    may return to a provider further down, once another domain has been tried."""
    failures = []
    for i in range(1, len(legs)):
        prev, cur = provider_domain(registry, legs[i - 1]), provider_domain(registry, legs[i])
        if prev == cur:
            failures.append("%s: (g) legs %d->%d %r then %r share provider %r"
                            % (combo_id, i, i + 1, legs[i - 1], legs[i], prev))
    return failures


def check_price_gate(registry, combo_id, legs):
    """(h) a credit/paid leg carries a price row (§7b delta b).

    "credit/paid" is the leg's EFFECTIVE tier — the model's own `tier` override,
    else its provider's, exactly as rule (b) reads it — so an OpenRouter `:free`
    row (model tier free on the paid `openrouter` provider) is not in scope, and
    a provider-tier credit grant with no price on it is. The row is the
    provider-scoped `provider_prices` entry when the model has one (one model id,
    two prices), else the model-level row. Both prices missing/0 AND no
    price_source is the uncostable grant: it bills $0 and drains invisibly, so
    the gate fails it rather than trusting the tier."""
    failures = []
    models = registry.get("models") or {}
    for leg in legs:
        resolved = leg_resolves(registry, leg)
        if resolved is None:
            continue                      # rule (c) reports an unresolvable leg
        pid, mid = resolved
        tier = leg_tier(registry, leg)
        if tier not in PRICE_GATE_TIERS:
            continue
        model = models.get(mid) or {}
        row = (model.get("provider_prices") or {}).get(pid) or model
        if REGISTRY_TOOL.leg_price(mid, pid, registry) is not None:
            continue
        source = row.get("price_source")
        if isinstance(source, str) and source.strip():
            continue
        failures.append("%s: (h) leg %r is a %s leg with no price row "
                        "(price_in/price_out both missing or 0, no price_source)"
                        % (combo_id, leg, tier))
    return failures


def check_leg_bans(registry, combo_id, legs):
    """(i) the legs D-657/§7b/§8 take out of every gateway combo.

    qoder: no leg at all (spawner client only). deepseek and meta-api muse: only
    the PAID spelling is banned (the leg's effective tier, so a free muse id
    under the same provider stays legal — §8 keeps the `…-free` legs), so a free
    DeepSeek/muse spelling under another provider is not this ban.
    agy (antigravity): never position 1."""
    failures = []
    for i, leg in enumerate(legs):
        resolved = leg_resolves(registry, leg)
        if resolved is None:
            continue
        pid, mid = resolved
        if pid in SPAWNER_ONLY_PROVIDERS:
            failures.append("%s: (i) leg %r is provider %s, which runs only as "
                            "the spawner's own client (§7b: no Qoder PAT in the "
                            "gateway)" % (combo_id, leg, pid))
            continue
        if pid in BAN_PAID_TIER_PROVIDERS and leg_tier(registry, leg) == "paid":
            needle = BAN_PAID_TIER_PROVIDERS[pid]
            if needle is None or needle in mid:
                failures.append("%s: (i) leg %r is the paid %s leg, which §8 "
                                "removes from every chain"
                                % (combo_id, leg, mid))
        if i == 0 and pid in NEVER_HEAD_PROVIDERS:
            failures.append("%s: (i) agy/antigravity leg %r is position 1; it may "
                            "only ride as a non-head leg" % (combo_id, leg))
    return failures


D657_CHECKS = (("f-cache", check_cache_gate),
               ("g-diversity", check_provider_diversity),
               ("h-price", check_price_gate),
               ("i-bans", check_leg_bans))
D657_STRICT_FLAG = "--strict-d657"
D657_COMBO_LIST_KEY = "d657_combos"


def d657_run_for(combo_id, combos_doc, strict_all):
    """Is this combo judged by (f)-(i)? --strict-d657 says all of them; else
    only the ids combos.json lists in `d657_combos` (absent = none), so the
    gate opts in combo by combo as D2 re-chains them and main stays green."""
    if strict_all:
        return True
    listed = combos_doc.get(D657_COMBO_LIST_KEY) or []
    return isinstance(listed, list) and combo_id in listed


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict_all = D657_STRICT_FLAG in argv
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
    judged = []
    # live catalog, warn-only
    live_ids = None
    live_note = "SKIP (no key/gateway)"
    try:
        keys_path = ROOT / "configuration" / "api-keys.yml"
        # Main-checkout key fallback (read-only, never printed): resolve the
        # shared git dir and take its parent - portable (no user path), and
        # exact even when this script runs from a linked worktree.
        main_keys = None
        try:
            common = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()
            if common:
                main_keys = pathlib.Path(common).resolve().parent / "configuration" / "api-keys.yml"
        except (OSError, subprocess.SubprocessError):
            pass
        key = None
        for kp in (p for p in (keys_path, main_keys) if p is not None):
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
        # (e) two distinct servable providers, except named exemption
        if name in AGENTIC_ROUTES:
            provs = set()
            for leg in legs:
                try:
                    pid, _ = resolve_leg(registry_ref(leg, registry), registry)
                    provs.add(pid)
                except ValueError:
                    pass  # rule (c) already reports unresolvable
            if len(provs) < 2:
                if name in SINGLE_PROVIDER_EXEMPTIONS:
                    note = ((routes.get(name) or {}).get("$comment") or "")
                    missing = [ph for ph in REQUIRED_SINGLE_PROVIDER_NOTE_PHRASES if ph not in note]
                    if missing:
                        failures.append("%s: (e) exemption note missing phrases %s" % (name, missing))
                        per.append("e-note-missing")
                    else:
                        per.append("e-exempted-single-provider")
                else:
                    failures.append("%s: (e) single servable provider %s without exemption" % (name, sorted(provs)))
                    per.append("e-single-provider")
        # (f)-(i) D1: only the combos opted in (combos.json `d657_combos`, or
        # every combo under --strict-d657), so D2 re-chains without red CI.
        if d657_run_for(name, combos_doc, strict_all):
            judged.append(name)
            for tag, check in D657_CHECKS:
                problems = check(registry, name, legs)
                failures.extend(problems)
                per.extend([tag] * len(problems))
        fail_tags = [x for x in per if not (x.startswith("c-live-missing") or x == "e-exempted-single-provider")]
        verdicts.append("%s: %s [%s]" % (name, "FAIL " + ",".join(per) if fail_tags else ("PASS" + (",e-exempted" if "e-exempted-single-provider" in per else "")), live_note))
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
    print("d657 (f)-(i) judged %d of %d combos [%s]"
          % (len(judged), len(combos_by_name),
             D657_STRICT_FLAG if strict_all else
             "combos.json '%s', add ids or %s" % (D657_COMBO_LIST_KEY, D657_STRICT_FLAG)))
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
