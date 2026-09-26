#!/usr/bin/env python3
"""Audit the router stack: repo declarations vs the live gateways.

Four surfaces describe the same routing (docs/models.md "sync contract"), and
2026-09-22 proved each one can drift silently while every suite stays green:

  * a combo leg the gateway 400s on at chat time (`simulate` still resolves it)
  * a combo present in combos.json but absent from a client's model list
  * an OpenHands tier profile with no matching combo
  * a LiteLLM `*-paid` escalation group whose only leg is dead, so the
    "fallback" cannot fall back

This tool checks all four, plus one footgun that produced a misleading
"All credentials for model gemini-3.7-flash are cooling down" in a client:
a PROVIDER-QUALIFIED bare model ref (`gemini/…`) bypasses every combo and has
no fallback chain, so it binds straight to the throttled free tier.

repo_combos() sources its combo list from catalog/ai-registry.json's
render_omniroute() (task A5c, spec 3.2 phase 2) instead of reading
configuration/omniroute/combos.json directly - --combos is an explicit
override onto that old file's own shape (spec 3.2's two-phase rule: no old
catalog is deleted before every consumer has moved off it).

Usage:
    python3 tools/audit-router.py [--offline] [--json] [--registry PATH | --combos PATH]

    (default)   probe the live gateway (:20128) and LiteLLM proxy (:4000)
    --offline   only compare files; skip the network
    --json      machine-readable report

Exit codes:
    0   no drift and no phantom refs
    1   drift or a failing leg found (the report names each)
    2   unusable input (a file is missing or unparseable)

Never prints or reads a key value: only key *names* and model ids.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_TOOL_PATH = ROOT / "tools" / "registry.py"


def _load_registry_tool():
    """Import tools/registry.py by path - the same importlib-by-path
    technique tools/sync-ide-models.py's _load_registry_tool() and
    tools/registry.py's own _load_sync_router_tiers() already use."""
    spec = importlib.util.spec_from_file_location("autoos_registry", REGISTRY_TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def litellm_key(env_path=None):
    """Return the LiteLLM master key from the environment or the .env file.

    Priority:
      1. LITELLM_MASTER_KEY env var (non-empty)
      2. AUTOOS_LITELLM_API_KEY env var (non-empty)
      3. The value of LITELLM_MASTER_KEY from *env_path* (default:
         ROOT/configuration/litellm/.env), ignoring comments, blank lines, and
         placeholder values whose content starts with ``REPLACE_WITH_``.

    Never prints or logs the value.
    """
    for var in ("LITELLM_MASTER_KEY", "AUTOOS_LITELLM_API_KEY"):
        val = os.environ.get(var, "")
        if val:
            return val
    if env_path is None:
        env_path = ROOT / "configuration" / "litellm" / ".env"
    try:
        text = Path(env_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "LITELLM_MASTER_KEY":
            if value and not value.startswith("REPLACE_WITH_"):
                return value
            return ""
    return ""


# Provider-qualified refs that bypass a combo. Every one of these binds to a
# provider directly, so a throttled or unfunded connection surfaces as the
# user-visible error instead of the chain hopping. The free-tier `gemini`
# connection is the one measured to burn out (250k input-token/min cap).
FORBIDDEN_DIRECT_REFS = (
    "gemini/gemini-3.7-flash",   # free tier, tiny cap, no fallback: 429/504
)


def repo_combos(registry_path=None, combos_path=None) -> list[dict]:
    """The repo's declared combos ('name'/'strategy'/'context'/'models' per
    entry), sourced from catalog/ai-registry.json's render_omniroute()
    (task A5c, spec 3.2 phase 2) - this used to read configuration/omniroute/
    combos.json directly; tools/registry.py's render_omniroute() is already
    proven semantically equal to that file (task A4a, `registry.py render
    omniroute --check`), so this is a source change only.

    combos_path is an explicit override reading a combos.json-shaped file
    directly (never deleted, spec 3.2's two-phase rule); unset, registry_path
    (default catalog/ai-registry.json) is rendered instead.

    A registry with no `routes` key is unreadable input, not zero combos:
    raise the loud SystemExit the old combos.json read gave a missing key
    instead of letting render_omniroute() render an empty list silently.
    """
    if combos_path is not None:
        path = Path(combos_path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))["combos"]
        except (OSError, ValueError, KeyError) as exc:
            raise SystemExit(f"ERROR: cannot read {path}: {exc}")
    path = Path(registry_path) if registry_path else ROOT / "catalog" / "ai-registry.json"
    registry_tool = _load_registry_tool()
    try:
        doc = registry_tool.load(path)
        if "routes" not in doc:
            raise SystemExit(f"ERROR: cannot read routes from {path}")
        rendered = registry_tool.render_omniroute(doc)
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"ERROR: cannot read {path}: {exc}")
    return rendered["combos"]


def live_combos() -> dict[str, list] | None:
    """Combo name -> ordered leg list, straight from the gateway's store."""
    import sqlite3

    db_path = Path(os.path.expanduser("~")) / ".omniroute" / "storage.sqlite"
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(str(db_path))
        out = {}
        for name, data in db.execute("SELECT name, data FROM combos"):
            try:
                out[name] = [m["model"] for m in json.loads(data).get("models", [])]
            except (ValueError, KeyError, TypeError):
                out[name] = None
        db.close()
        return out
    except sqlite3.Error:
        return None


def opencode_models() -> set[str]:
    raw = (ROOT / "opencode.jsonc").read_text(encoding="utf-8")
    body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    doc = json.loads(body)
    return set(doc["providers"]["omniroute"]["models"])


def opencode_litellm_models() -> set[str]:
    raw = (ROOT / "opencode.jsonc").read_text(encoding="utf-8")
    body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    doc = json.loads(body)
    return set(doc["providers"]["litellm"]["models"])


def ide_models(gateway: str, surface: str) -> list[str]:
    """Ids catalog/ide-models.json (rendered from catalog/ai-registry.json)
    offers to `surface` through `gateway`."""
    doc = json.loads((ROOT / "catalog" / "ide-models.json").read_text(encoding="utf-8"))
    return [m["id"] for m in doc["models"] if surface in m["surfaces"].get(gateway, [])]


def tier_profile_ids() -> set[str]:
    doc = json.loads((ROOT / "configuration" / "openhands" / "tier-profiles.json")
                     .read_text(encoding="utf-8"))
    return {t["id"] for t in doc["tiers"]}


def openhands_route_names(registry_doc: dict) -> set[str]:
    """Route ids OpenHands actually serves: routes whose
    surfaces.omniroute.clients contains "openhands" (catalog/ai-registry.json,
    read via tools/registry.py's loader). Only these need an
    `omniroute-<name>` tier profile - opencode/zed-only pinned routes do not."""
    out = set()
    routes = registry_doc.get("routes") or {}
    for rid, route in routes.items():
        if not isinstance(route, dict):
            continue
        surfaces = route.get("surfaces") or {}
        omni = surfaces.get("omniroute") or {} if isinstance(surfaces, dict) else {}
        if not isinstance(omni, dict):
            continue
        if "openhands" in (omni.get("clients") or []):
            out.add(rid)
    return out


def tier_profile_drift(names: list[str], tier_ids: set[str],
                       registry_doc: dict) -> list[str]:
    """Tier-profile drift lines for `names`, restricted to the routes
    OpenHands actually serves (see openhands_route_names)."""
    need = openhands_route_names(registry_doc)
    return [f"tier-profiles.json lacks 'omniroute-{c}'" for c in names
            if c in need and f"omniroute-{c}" not in tier_ids]


def litellm_declared() -> list[str]:
    text = (ROOT / "configuration" / "litellm" / "config.yaml").read_text(encoding="utf-8")
    return sorted({m.group(1) for m in re.finditer(r"^\s*-\s*model_name:\s*(\S+)\s*$",
                                                   text, re.MULTILINE)})


# Probe outcomes split into two kinds, because one is ours to fix and the
# other is the account's: a 400/404 means the model ref itself is wrong
# (config drift - the phantom-leg class), while 402/429/5xx/transport errors
# are provider/balance state that the repo cannot repair. Reporting both as
# "DRIFT" made the audit cry wolf on nearly-empty paid pools.
DRIFT_STATUSES = {400, 404, "ERR"}


def classify(status) -> str:
    return "drift" if status in DRIFT_STATUSES else "state"


def probe_gateway(model: str, timeout: int = 180) -> tuple[object, str]:
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY", "")
    return _chat("http://127.0.0.1:20128", model, key, timeout)


MIN_REASONING_MAX_WAIT_MS = 120_000

# Retry delays (seconds) for transient 503 responses from the gateway.
# 503 = the gateway shedding load under host memory pressure, transient; we retry with backoff.
RETRY_DELAYS_S = (5, 15, 45)

# Assignable sleep function for easier testing/mocking.
_sleep = time.sleep

# The free promo leg stays FIRST (free when it works), so the breaker must skip
# it fast: a 403 is a permanent-class error, and at the shipped threshold of 12
# the gateway retried the dead promo on every single request (operator call
# 2026-09-23: skip after the first two 429s/403s).
MAX_FAST_SKIP_THRESHOLD = 4


def resilience_config() -> dict | None:
    """The gateway's live resilience config, or None when unreadable."""
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY", "")
    if not key:
        return None
    req = urllib.request.Request(
        "http://127.0.0.1:20128/api/resilience?include=config",
        headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except Exception:  # noqa: BLE001 - any failure means "cannot check"
        return None


def probe_litellm(model: str, timeout: int = 180) -> tuple[object, str]:
    key = litellm_key()
    return _chat("http://127.0.0.1:4000", model, key, timeout)


def _chat_once(base: str, model: str, key: str, timeout: int,
          max_tokens: int = 2048) -> tuple[object, str]:
    # 2048, not a few dozen: reasoning legs (spark, gemini-flash) spend tokens
    # on hidden thinking before emitting content, and a small budget makes them
    # answer "empty response" - which reads as a dead leg but is a budget fault
    # (docs/models.md "Muse Spark needs a real output budget").
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Reply with exactly: ack"}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        base + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.load(resp)
        return resp.status, "ack"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(160).decode("utf-8", "replace").replace("\n", " ")[:160]
    except Exception as exc:  # noqa: BLE001 - any transport failure is a finding
        return "ERR", str(exc)[:160]


def _chat(base: str, model: str, key: str, timeout: int,
          max_tokens: int = 2048) -> tuple[object, str]:
    # 503 = the gateway shedding load under host memory pressure, transient;
    # 400/404/ERR are drift and are never retried.
    delays = list(RETRY_DELAYS_S)
    while True:
        status, snippet = _chat_once(base, model, key, timeout, max_tokens)
        if status != 503 or not delays:
            return status, snippet
        _sleep(delays.pop(0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit router declarations against the live gateways.")
    ap.add_argument("--offline", action="store_true", help="skip all network probes")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--registry", default=None,
                     help="path to catalog/ai-registry.json (default: catalog/ai-registry.json)")
    ap.add_argument("--combos", default=None,
                     help="explicit override: read combos from a combos.json-shaped file instead of --registry")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    combos = repo_combos(registry_path=args.registry, combos_path=args.combos)
    names = [c["name"] for c in combos]
    drift: list[str] = []
    probes: list[dict] = []

    # 1. repo vs live gateway (a LIVE check - skipped under --offline, where it
    #    would report "unreadable" from a shell that cannot see the Windows
    #    path, e.g. the Linux suite running under WSL2)
    if not args.offline:
        live = live_combos()
        if live is None:
            drift.append("gateway store unreadable - cannot compare live combos")
        else:
            missing = sorted(set(names) - set(live))
            extra = sorted(set(live) - set(names))
            if missing:
                drift.append(f"combos in the repo but not live: {missing}")
            if extra:
                drift.append(f"combos live but not in the repo: {extra}")
            for c in combos:
                if live.get(c["name"]) != c["models"]:
                    drift.append(f"{c['name']}: live legs {live.get(c['name'])} != repo {c['models']}")

    # 2. repo vs client surfaces
    oc = opencode_models()
    for c in names:
        if c not in oc:
            drift.append(f"opencode.jsonc lacks model '{c}'")
    psm = (ROOT / "lib" / "windows" / "AutoOS.Install.psm1").read_text(encoding="utf-8")
    sh = (ROOT / "lib" / "linux" / "install.sh").read_text(encoding="utf-8")
    # Both Zed writers project catalog/ide-models.json at run time, so the
    # catalog's zed membership IS what they write (tools/sync-ide-models.py
    # --check covers the static copies).
    zed = ide_models("omniroute", "zed")
    for c in names:
        if c not in zed:
            drift.append(f"catalog/ide-models.json does not offer '{c}' to zed")
    for label, text in (("psm1", psm), ("install.sh", sh)):
        if "ide-models.json" not in text:
            drift.append(f"{label} Zed writer does not read catalog/ide-models.json")
    # auto/* are OmniRoute's built-in bootstraps; anything else a client
    # offers through the gateway must be a combo, or it cannot resolve.
    for m in ide_models("omniroute", "opencode") + zed:
        if m not in names and not m.startswith("auto"):
            drift.append(f"catalog/ide-models.json offers '{m}' through omniroute but no combo has that name")
    tp = tier_profile_ids()
    try:
        reg_path = Path(args.registry) if args.registry else ROOT / "catalog" / "ai-registry.json"
        reg_doc = _load_registry_tool().load(reg_path)
    except (OSError, ValueError):
        reg_doc = None
    if not isinstance(reg_doc, dict) or "routes" not in reg_doc:
        # Registry unreadable: fall back to requiring a profile for every
        # combo (the old, stricter rule) rather than silently skipping.
        for c in names:
            if f"omniroute-{c}" not in tp:
                drift.append(f"tier-profiles.json lacks 'omniroute-{c}'")
    else:
        drift.extend(tier_profile_drift(names, tp, reg_doc))

    # 3. forbidden direct refs anywhere in our surfaces
    surfaces = {
        "opencode.jsonc": (ROOT / "opencode.jsonc").read_text(encoding="utf-8"),
        "catalog/ide-models.json":
            (ROOT / "catalog" / "ide-models.json").read_text(encoding="utf-8"),
        "lib/windows/AutoOS.Install.psm1": psm,
        "lib/linux/install.sh": sh,
        "configuration/openhands/tier-profiles.json":
            (ROOT / "configuration" / "openhands" / "tier-profiles.json").read_text(encoding="utf-8"),
        "configuration/openhands/config.toml":
            (ROOT / "configuration" / "openhands" / "config.toml").read_text(encoding="utf-8"),
        "configuration/litellm/config.yaml":
            (ROOT / "configuration" / "litellm" / "config.yaml").read_text(encoding="utf-8"),
    }
    for ref in FORBIDDEN_DIRECT_REFS:
        # The phantom-leg test owns combos.json; these are the other surfaces.
        for label, text in surfaces.items():
            if ref in text:
                drift.append(f"{label} references a combo-bypassing ref: {ref}")

    # 3b. Claude never rides the API: a Claude-subscription holder drives those
    # models through the CLI (OAuth session), so no routing surface may name an
    # anthropic/claude MODEL. Only model-ref positions count — prose mentions
    # (autostart docs, CLI wiring) are not legs.
    claude_surfaces = dict(surfaces)
    claude_surfaces["configuration/omniroute/combos.json"] = json.dumps(combos)
    for label, text in claude_surfaces.items():
        for n, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith(("#", "//")):
                continue
            m = re.search(r"""(?ix)\bmodel(?:ID)?\s*[:=]\s*["']?([^\s"',}]+)""", s)
            if m and re.search(r"(?i)(anthropic|claude-)", m.group(1)):
                drift.append(f"{label}:{n} routes a Claude model via API: {m.group(1)}")

    # 4. gateway resilience: the local execution deadline must fit a reasoning
    #    model, and the breaker must skip a dead promoted leg fast.
    if not args.offline:
        cfg = resilience_config()
        if cfg is not None:
            max_wait = ((cfg.get("requestQueue") or {}).get("maxWaitMs"))
            probes.append({"surface": "omniroute", "model": "requestQueue.maxWaitMs",
                           "status": max_wait, "detail": "local execution deadline"})
            if isinstance(max_wait, (int, float)) and max_wait < MIN_REASONING_MAX_WAIT_MS:
                drift.append(
                    f"requestQueue.maxWaitMs={max_wait} is below "
                    f"{MIN_REASONING_MAX_WAIT_MS}: reasoning legs get killed mid-think "
                    "(PATCH /api/resilience {\"requestQueue\":{\"maxWaitMs\":180000}})")
            threshold = (((cfg.get("providerBreaker") or {}).get("apikey") or {})
                         .get("failureThreshold"))
            probes.append({"surface": "omniroute",
                           "model": "providerBreaker.apikey.failureThreshold",
                           "status": threshold, "detail": "fast-skip threshold"})
            if isinstance(threshold, (int, float)) and threshold > MAX_FAST_SKIP_THRESHOLD:
                drift.append(
                    f"providerBreaker.apikey.failureThreshold={threshold} is above "
                    f"{MAX_FAST_SKIP_THRESHOLD}: a permanently-failing promoted leg "
                    "(a 403 free promo) is retried on every request")

    # 5. live probes
    if not args.offline:
        for c in combos:
            status, detail = probe_gateway(c["name"])
            probes.append({"surface": "omniroute", "model": c["name"], "status": status,
                           "detail": detail, "kind": classify(status)})
            if classify(status) == "drift":
                drift.append(f"gateway combo {c['name']} -> {status} {detail}")
        for m in litellm_declared():
            status, detail = probe_litellm(m)
            probes.append({"surface": "litellm", "model": m, "status": status,
                           "detail": detail, "kind": classify(status)})
            if classify(status) == "drift":
                drift.append(f"litellm group {m} -> {status} {detail}")
        # A litellm group whose only legs are unfunded cannot escalate.
        for m in opencode_litellm_models() - set(litellm_declared()):
            drift.append(f"opencode declares litellm/{m} but the proxy does not serve it")

    if args.json:
        print(json.dumps({"drift": drift, "probes": probes, "combos": names}, indent=2))
    else:
        print(f"combos: {len(names)} ({', '.join(names)})")
        if probes:
            for p in probes:
                if p["surface"] == "omniroute" and p["model"] in (
                        "requestQueue.maxWaitMs",
                        "providerBreaker.apikey.failureThreshold"):
                    continue
                if p["status"] == 200:
                    flag = "ok  "
                elif p.get("kind") == "drift":
                    flag = "DRIFT"
                else:
                    flag = "state"
                print(f"  {flag:5s} {p['surface']:9s} {p['model']:24s} {p['status']} {p['detail'][:70]}")
        if drift:
            print(f"\nDRIFT ({len(drift)}):")
            for d in drift:
                print(f"  ! {d}")
        else:
            print("\nno drift (non-200 legs above are provider/balance state, not config)")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
