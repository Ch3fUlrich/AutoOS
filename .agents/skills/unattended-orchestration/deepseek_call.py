#!/usr/bin/env python3
"""One DeepSeek completion: the OmniRoute combo first, then OpenRouter direct.

    deepseek_call.py PROMPT_FILE [--model deepseek-v4.1-flash] [--timeout 900]

The whole prompt file is sent as a single user message (temperature 0.1). The answer goes to
stdout; one ``served: <model> via <leg>`` line goes to stderr. Exit 0 answered, 1 every leg
failed, 2 usage/key error.

Operator rules (2026-09-25):
- Keys come from AutoOS ``configuration/api-keys.yml`` (``name: value`` lines), read here in
  the process — never from argv, never printed. ``AUTOOS_OMNIROUTE_KEY`` / ``OPENROUTER_API_KEY``
  override the file's ``omniroute:`` / ``openrouter:`` values.
- Which DeepSeek models count comes from catalog/ai-registry.json, never from this file: the
  OmniRoute combo is route ``deepseek-v4.1-flash``, and a served model passes only when it is one
  of that route's legs that ``policy.leg_rules`` allows (so V4 Pro never passes, whatever the
  spelling). The OpenRouter leg runs only while the rules allow it. A leg that serves anything
  else is a failure, so a combo that re-routed in silence cannot pass as a DeepSeek review.
- Never local Ollama. If both legs fail this exits 1 and says so; it does not go local.

File lookup: ``$AUTOOS_API_KEYS`` (explicit — a missing file fails loudly, D4), else
``$AUTOOS_ROOT/configuration/api-keys.yml``, else the first ancestor of this script (or of its
main checkout, since the file is git-ignored and so absent in every worktree) that has one, else
``~/Documents/Code/AutoOS/configuration/api-keys.yml``.
"""
from __future__ import annotations

import argparse
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
OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER_LEG = "openrouter/" + OPENROUTER_MODEL  # its spelling in policy.leg_rules
ALLOWED = ("", MODEL, OPENROUTER_MODEL)
KEYS_REL = Path("configuration") / "api-keys.yml"
REPO = Path(__file__).resolve().parents[3]         # .agents/skills/<skill>/ -> the checkout


class KeyError_(Exception):
    """No usable key file / key (exit 2)."""


class RouteError(Exception):
    """Every leg failed (exit 1)."""


class PolicyError(Exception):
    """The registry cannot be read or names no allowed DeepSeek leg (exit 2)."""


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


def load_keys() -> dict:
    env = {"omniroute": os.environ.get("AUTOOS_OMNIROUTE_KEY"),
           "openrouter": os.environ.get("OPENROUTER_API_KEY")}
    path = find_keys_file()
    if path is None and not any(env.values()):
        raise KeyError_("no api-keys.yml found (set AUTOOS_API_KEYS or AUTOOS_ROOT, or create "
                        "<AutoOS>/configuration/api-keys.yml; see AutoOS docs/api-keys.md)")
    return {k: v or (read_key(path, k) if path else None) for k, v in env.items()}


# ── policy (catalog/ai-registry.json) ──────────────────────────────────────────

def load_policy() -> tuple[list[str], bool, dict, object]:
    """(the route's allowed legs, whether the OpenRouter leg is allowed, the registry, leg_denied).

    ``$DSR_REGISTRY`` names another registry (tests). The rules are the repo's own
    ``tools/registry.leg_denied`` - first match wins, case-insensitive - so this file never
    keeps a second copy of which DeepSeek spellings are allowed."""
    path = Path(os.environ.get("DSR_REGISTRY") or REPO / "catalog" / "ai-registry.json")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        sys.path.insert(0, str(REPO / "tools"))
        from registry import leg_denied
    except (OSError, ValueError, ImportError) as e:
        raise PolicyError("cannot read the DeepSeek policy from %s: %s" % (path, e))
    route = (registry.get("routes") or {}).get(MODEL) or {}
    allowed = [leg for leg in route.get("legs") or []
               if isinstance(leg, str) and not leg_denied(leg, registry)]
    return allowed, not leg_denied(OPENROUTER_LEG, registry), registry, leg_denied


def legs(omni_model: str) -> list[tuple[str, str, str, str | None]]:
    """(name, chat url, requested model, health url) in route order. OmniRoute is asked for
    the allowed registry leg itself (``provider/model``): the route id is not an API id and
    answers 400 (L1-routing review, 2026-09-28)."""
    omni = os.environ.get("DSR_OMNIROUTE_URL", "http://127.0.0.1:20128").rstrip("/")
    orr = os.environ.get("DSR_OPENROUTER_URL", "https://openrouter.ai/api").rstrip("/")
    return [("omniroute", omni + "/v1/chat/completions", omni_model, omni + "/api/health"),
            ("openrouter", orr + "/v1/chat/completions", OPENROUTER_MODEL, None)]


def scrub(text: str, keys: dict) -> str:
    for v in keys.values():
        if v:
            text = text.replace(v, "***")
    return text


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


def served_ok(served: str, accepted: list[str], registry: dict, leg_denied,
              rules_prefix: str = "") -> bool:
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
    if leg_denied(rules_prefix + full, registry):
        return False
    return any(full.casefold() == leg.casefold() for leg in accepted)


def complete(prompt: str, timeout: float = 900, max_tokens: int = MAX_TOKENS) -> tuple[str, str, str]:
    """Return (answer, served model, leg). Raise RouteError when no leg answers."""
    keys = load_keys()
    omni_legs, openrouter_allowed, registry, leg_denied = load_policy()
    accepted = {"omniroute": omni_legs, "openrouter": [OPENROUTER_MODEL] if openrouter_allowed else []}
    prefix = {"omniroute": "", "openrouter": "openrouter/"}
    failures = []
    for name, url, model, health in legs(omni_legs[0] if omni_legs else MODEL):
        if not accepted[name]:
            failures.append("%s: no DeepSeek leg allowed by policy.leg_rules" % name)
            continue
        key = keys.get(name)
        if not key:
            failures.append("%s: no key" % name)
            continue
        if health and not healthy(health):
            failures.append("%s: gateway not reachable" % name)
            continue
        try:
            data = post(url, key, model, prompt, timeout, max_tokens)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            failures.append("%s: HTTP %s %s" % (name, e.code, detail))
            continue
        except Exception as e:  # timeout, refused, bad JSON
            failures.append("%s: %s" % (name, e))
            continue
        served = str(data.get("model") or "")
        if not served_ok(served, accepted[name], registry, leg_denied, prefix[name]):
            failures.append("%s: served %r, not one of %s" % (name, served, ", ".join(accepted[name])))
            continue
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        if not text:
            failures.append("%s: empty answer" % name)
            continue
        return text, served, name
    raise RouteError(scrub("; ".join(failures), keys) +
                     " -- not falling back to local Ollama")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt_file")
    ap.add_argument("--model", default="")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
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
    except RouteError as e:
        print("deepseek call failed: %s" % e, file=sys.stderr)
        return 1
    print("served: %s via %s" % (served, leg), file=sys.stderr)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
