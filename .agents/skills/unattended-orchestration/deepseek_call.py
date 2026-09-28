#!/usr/bin/env python3
"""One paid DeepSeek completion through the OmniRoute gateway, under the monthly cap.

    deepseek_call.py PROMPT_FILE [--model deepseek-v4.1-flash] [--timeout 900] [--max-tokens 4096]

Orchestrator-only: spends paid DeepSeek credit under the monthly cap. The whole prompt file is
sent as a single user message (temperature 0.1). The answer goes to stdout; one
``served: <model> via omniroute`` line goes to stderr. Exit 0 answered, 1 the gateway call
failed, 2 usage/key/registry error, 3 refused by the monthly cap.

Operator rules:
- Keys come from AutoOS ``configuration/api-keys.yml`` (``name: value`` lines), read here in
  the process — never from argv, never printed. ``AUTOOS_OMNIROUTE_KEY`` overrides the file's
  ``omniroute:`` value (2026-09-25).
- Which DeepSeek models count comes from catalog/ai-registry.json, never from this file: a
  served model passes only when it is exactly one of route ``deepseek-v4.1-flash``'s legs that
  ``policy.leg_rules`` allows, checked as served (so V4 Pro never passes, whatever the spelling).
  A gateway that re-routed in silence is a failure, not a review.
- The cap is ``providers.deepseek.monthly_cap_usd`` ($25, operator 2026-09-28). Before each call
  this month's DeepSeek spend is read from the gateway's call logs (tools/autoos_usage.py, with
  the manage key); at or above the cap, or when the spend cannot be read, the call is refused
  (exit 3). No gateway, no paid review.
- The gateway is the only leg: OpenRouter has no credit (``providers.openrouter`` is
  unavailable), and there is never a fall back to local Ollama.

File lookup: ``$AUTOOS_API_KEYS`` (explicit — a missing file fails loudly, D4), else
``$AUTOOS_ROOT/configuration/api-keys.yml``, else the first ancestor of this script (or of its
main checkout, since the file is git-ignored and so absent in every worktree) that has one, else
``~/Documents/Code/AutoOS/configuration/api-keys.yml``.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "deepseek-v4.1-flash"                      # registry route id (the policy's source)
MAX_TOKENS = 4096                                  # reasoning rungs answer an empty 502 below it
ALLOWED = ("", MODEL)
KEYS_REL = Path("configuration") / "api-keys.yml"
REPO = Path(__file__).resolve().parents[3]         # .agents/skills/<skill>/ -> the checkout
sys.path.insert(0, str(REPO / "tools"))


class KeyError_(Exception):
    """No usable key file / key (exit 2)."""


class RouteError(Exception):
    """The gateway call failed (exit 1)."""


class PolicyError(Exception):
    """The registry cannot be read or names no allowed DeepSeek leg (exit 2)."""


class CapError(Exception):
    """This month's spend is at or above the cap, or cannot be read (exit 3)."""


def check_model(model: str) -> None:
    if model not in ALLOWED:
        raise ValueError("only %s is allowed (got %r)" % (MODEL, model))


# ── keys ───────────────────────────────────────────────────────────────────────

def default_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    roots = [here, *here.parents]
    try:
        common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True, timeout=10)
        if common.returncode == 0 and common.stdout.strip():
            main = Path(common.stdout.strip()).parent
            roots += [main, *main.parents]
    except (OSError, subprocess.SubprocessError):
        pass
    roots.append(Path.home() / "Documents" / "Code" / "AutoOS")
    return [r / KEYS_REL for r in roots]


def find_keys_file() -> Path | None:
    explicit = os.environ.get("AUTOOS_API_KEYS")
    if explicit:
        if not Path(explicit).is_file():
            raise KeyError_("keys file not found: %s (AUTOOS_API_KEYS)" % explicit)
        return Path(explicit)
    cands = []
    if os.environ.get("AUTOOS_ROOT"):
        cands.append(Path(os.environ["AUTOOS_ROOT"]) / KEYS_REL)
    cands += default_candidates()
    return next((c for c in cands if c.is_file()), None)


def read_key(path: Path, name: str) -> str | None:
    # Same parse as AutoOS tools/autoos-agent.py client_key().
    for line in io.open(path, encoding="utf-8"):
        m = re.match(r"^%s\s*:\s*(.+?)\s*$" % re.escape(name), line)
        if m:
            val = m.group(1).strip("\"'")
            return None if val.startswith("REPLACE_WITH_") else val
    return None


def load_key() -> str:
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    if key:
        return key
    path = find_keys_file()
    if path is None:
        raise KeyError_("no api-keys.yml found (set AUTOOS_API_KEYS or AUTOOS_ROOT, or create "
                        "<AutoOS>/configuration/api-keys.yml; see AutoOS docs/api-keys.md)")
    key = read_key(path, "omniroute")
    if not key:
        raise KeyError_("no omniroute key in %s" % path)
    return key


# ── policy (catalog/ai-registry.json) ──────────────────────────────────────────

def load_policy() -> tuple[list[str], dict, object]:
    """(the route's allowed legs, the registry, leg_denied).

    ``$DSR_REGISTRY`` names another registry (tests). The rules are the repo's own
    ``tools/registry.leg_denied`` - first match wins, case-insensitive - so this file never
    keeps a second copy of which DeepSeek spellings are allowed."""
    path = Path(os.environ.get("DSR_REGISTRY") or REPO / "catalog" / "ai-registry.json")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        from registry import leg_denied
    except (OSError, ValueError, ImportError) as e:
        raise PolicyError("cannot read the DeepSeek policy from %s: %s" % (path, e))
    route = (registry.get("routes") or {}).get(MODEL) or {}
    allowed = [leg for leg in route.get("legs") or []
               if isinstance(leg, str) and not leg_denied(leg, registry)]
    if not allowed:
        raise PolicyError("route %s has no leg policy.leg_rules allows" % MODEL)
    return allowed, registry, leg_denied


def gateway() -> str:
    return os.environ.get("DSR_OMNIROUTE_URL", "http://127.0.0.1:20128").rstrip("/")


# ── the monthly cap (providers.deepseek.monthly_cap_usd) ───────────────────────

def usage_fetch(url, headers, timeout):
    """The call-log fetch the cap check uses; tests replace it (no network)."""
    import autoos_usage
    return autoos_usage.urllib_fetch(url, headers, timeout)


def check_cap(registry: dict, now: datetime.datetime | None = None) -> tuple[float, float]:
    """(spend, cap) when this month's DeepSeek spend is below the registry cap.

    Raises CapError at or above the cap and whenever the spend cannot be read in full: no
    cap in the registry, no manage key, the gateway unreachable or refusing, or a call-log
    walk that stopped before the month's first row (a floor is not a total)."""
    try:
        import autoos_usage as usage
        cap = usage.monthly_cap_usd(registry)
        key = usage.read_manage_key(usage.key_file_path(os.environ))
        since = usage.month_start(now or datetime.datetime.now(datetime.timezone.utc))
        rows, _pages, truncated = usage.fetch_window(usage_fetch, gateway(), key, since)
        spend = usage.paid_spend(rows, usage.prices_from_registry(registry), registry,
                                 since)["spend_usd"]
    except (ImportError, ValueError, OSError) as e:
        raise CapError("cannot read this month's DeepSeek spend: %s" % e)
    except Exception as e:  # autoos_usage.UsageError and any gateway surprise
        raise CapError("cannot read this month's DeepSeek spend: %s" % e)
    if truncated:
        raise CapError("cannot read this month's DeepSeek spend in full (call-log walk truncated)")
    if spend >= cap:
        raise CapError("DeepSeek spend $%.2f this month is at or above the $%.2f cap "
                       "(providers.deepseek.monthly_cap_usd)" % (spend, cap))
    return spend, cap


# ── the call ───────────────────────────────────────────────────────────────────

def scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def post(url: str, key: str, model: str, prompt: str, timeout: float,
         max_tokens: int = MAX_TOKENS) -> dict:
    body = json.dumps({"model": model, "temperature": 0.1, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def served_ok(served: str, accepted: list[str], registry: dict, leg_denied) -> bool:
    """`served` is exactly one of `accepted` (``provider/model`` legs, any case) and
    policy.leg_rules allows it as served. Nothing is normalised before the deny check: a dated
    snapshot, another provider or a bare id matching no single leg is not the allowed leg. A
    bare id is qualified only when exactly one accepted leg has that model part."""
    full = served.strip()
    if "/" not in full:
        same = [leg for leg in accepted if leg.split("/", 1)[-1].casefold() == full.casefold()]
        if len(same) != 1:
            return False
        full = same[0]
    if leg_denied(full, registry):
        return False
    return any(full.casefold() == leg.casefold() for leg in accepted)


def complete(prompt: str, timeout: float = 900, max_tokens: int = MAX_TOKENS) -> tuple[str, str, str]:
    """Return (answer, served model, "omniroute"). CapError before any model call when the
    cap refuses; RouteError when the gateway call fails."""
    key = load_key()
    accepted, registry, leg_denied = load_policy()
    check_cap(registry)
    base = gateway()
    if not healthy(base + "/api/health"):
        raise RouteError("omniroute: gateway not reachable -- not falling back to local Ollama")
    # The allowed registry leg itself (provider/model): the route id is not an API id and
    # answers 400 (L1-routing review, 2026-09-28).
    try:
        data = post(base + "/v1/chat/completions", key, accepted[0], prompt, timeout, max_tokens)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RouteError(scrub("omniroute: HTTP %s %s" % (e.code, detail), key))
    except Exception as e:  # timeout, refused, bad JSON
        raise RouteError(scrub("omniroute: %s" % e, key))
    served = str(data.get("model") or "")
    if not served_ok(served, accepted, registry, leg_denied):
        raise RouteError("omniroute: served %r, not one of %s" % (served, ", ".join(accepted)))
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = None
    if not text:
        raise RouteError("omniroute: empty answer")
    return text, served, "omniroute"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt_file")
    ap.add_argument("--model", default="")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    if a.max_tokens < MAX_TOKENS:
        print("--max-tokens %d is below the %d floor "
              "(reasoning rungs answer an empty 502 below it)" % (a.max_tokens, MAX_TOKENS),
              file=sys.stderr)
        return 2
    try:
        check_model(a.model)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    p = Path(a.prompt_file)
    if not p.is_file() or p.stat().st_size == 0:
        print("prompt file missing or empty: %s" % p, file=sys.stderr)
        return 2
    try:
        text, served, leg = complete(p.read_text(encoding="utf-8"), a.timeout, a.max_tokens)
    except (KeyError_, PolicyError) as e:
        print(e, file=sys.stderr)
        return 2
    except CapError as e:
        print("deepseek call refused: %s" % e, file=sys.stderr)
        return 3
    except RouteError as e:
        print("deepseek call failed: %s" % e, file=sys.stderr)
        return 1
    print("served: %s via %s" % (served, leg), file=sys.stderr)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
