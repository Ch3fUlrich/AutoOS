#!/usr/bin/env python3
"""probe-clamp.py - live check that the OmniRoute gateway CLAMPS, not rejects,
an over-cap qwen3-235b output request.

Why
---
Scaleway's ``qwen3-235b-a22b-instruct-2507`` rejects ``max_completion_tokens``
above 16384 with a 400 payload-validation error; an unclamped gateway forwards
the over-limit value and the whole request fails (a combo leg then falls
through)::

    400: max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507

The clamp patch adds one entry to the gateway's compiled static output-cap array
so this leg gets an explicit 16384 ceiling and the gateway clamps to it instead
of forwarding the over-limit value::

    {provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,
     maxOutputCap:16384,clampToModelMaxOutput:!0}

This probe sends the shape the gateway used to reject - ``max_tokens`` well
above the 16384 cap - to a scaleway qwen3-235b leg discovered in the live
catalog, and asserts the cap-limit 400 does not come back. A 200 is the clamp
working; the cap-limit 400 is the clamp missing.

House shape
-----------
Direct to the gateway (no opencode), PLAIN model names (the ``omniroute/``
prefix is only inside opencode), key from ``AUTOOS_OMNIROUTE_KEY`` or
``configuration/api-keys.yml`` read in memory - never printed, never in argv.
Candidate keys (env, then the file's ``omniroute_server`` and ``omniroute``
fields) are tried in order and the first the gateway accepts is used, so a stale
env key cannot mask a working file field or the reverse (measured 2026-10-01:
``omniroute_server`` 401s on this gateway, ``omniroute`` authenticates). On a
worktree checkout api-keys.yml is absent, so the primary checkout's copy
(derived from ``git rev-parse --git-common-dir``) is used.

Verdict / exit codes
--------------------
  PASS  the clamp leg answered 200 to an over-cap request     -> exit 0
  FAIL  the gateway returned the unclamped cap 400
        (``max_completion_tokens is limited to ...``)         -> exit 1
  SKIP  scaleway unavailable or the catalog could not be read
        (401/402/403/429, connection, timeout, no scaleway
        qwen3-235b leg)                                       -> exit 0

SKIP is honest: it means the probe learned nothing about the clamp, not that
the clamp is broken. Non-zero exit happens only on FAIL; argparse keeps its own
exit 2 for usage errors.

Usage
-----
    python3 tools/probe-clamp.py
    python3 tools/probe-clamp.py --model scaleway/qwen3-235b-a22b-instruct-2507
    python3 tools/probe-clamp.py --package-dir "$(npm root -g)/omniroute"

AutoOS lane clamp-probe | 2026-10-01 | branch L1-backlog/ws-clamp-probe-20260930
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20128")

# The vendor/gateway per-model output ceiling this probe exercises.
CAP = 16384
ABOVE_CAP = 20000
CLAMP_MODEL = "qwen3-235b-a22b-instruct-2507"
CLAMP_PREFIXES = ("scaleway/", "scw/")

# The unclamped rejection this probe must NOT see; the rate-limit signatures the
# harness asks us to back off on.
CAP_LIMIT_RE = re.compile(r"max_completion_tokens is limited to", re.I)
RATE_LIMIT_RE = re.compile(r"chat_admission_busy|rate limit exceeded", re.I)

# A non-clamped model to send the same shape to. openrouter passes a large
# max_tokens through, so a 200 there shows the request shape itself is valid and
# the clamp leg's answer is not an artefact of the probe's payload.
CONTRAST_CANDIDATES = (
    "openrouter/openai/gpt-4o-mini",
    "openrouter/qwen/qwen3-235b-a22b-2507",
    "free-ai/openai/gpt-4o-mini",
)

# The six compiled chunk copies that carry the static cap array (see
# configuration/omniroute/qwen-clamp-reapply.ps1). Read-only corroboration.
COMPILED_CHUNKS = (
    "_08_y1bx._.js", "_0o8_5h8._.js", "_0t1t5fj._.js",
    "_18ct13i._.js", "_1j_edf1._.js", "_1luyz1c._.js",
)

# Set in main(); only ever used to strip the key out of anything printed.
_KEY = None


def redact(text):
    """Remove the gateway key from `text` if it appears (never print the key)."""
    if _KEY and _KEY in text:
        return text.replace(_KEY, "<KEY>")
    return text


def safe(text, limit=300):
    """One-line, key-redacted, truncated view of a response body."""
    text = re.sub(r"\s+", " ", redact(text or "")).strip()
    return text[:limit]


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Key plumbing: env first, then api-keys.yml in memory. Never printed.
# ---------------------------------------------------------------------------

def _read_keys_from_file(path):
    """(value, label) pairs from api-keys.yml: the field the one resolver would pick first, then the
    other known client-key fields (omniroute_server, omniroute_<host>, legacy omniroute).

    All are returned because the label is a guess about which one is the client key: on the
    2026-10-01 workstation gateway the ``omniroute`` field authenticates while ``omniroute_server``
    401s, so the caller must try them rather than trust the first. Values are never printed - only
    the label.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from autoos_gateway_key import client_key_field, host_name
    from keys_file import read_keys
    keys = {k.lower(): v for k, v in read_keys(path).items()}
    fields = [client_key_field(os.environ), "omniroute_server", "omniroute_" + host_name(os.environ), "omniroute"]
    found = []
    seen = set()
    for field in fields:
        value = keys.get(field.lower())
        if value and value not in seen:
            seen.add(value)
            found.append((value, "api-keys.yml:" + field))
    return found


def _git_common_root():
    """The primary working tree's root, so a worktree finds the main api-keys.yml."""
    for extra in (["--path-format=absolute"], []):
        try:
            proc = subprocess.run(
                ["git", "-C", ROOT, "rev-parse"] + extra + ["--git-common-dir"],
                capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001 - git absent is not a probe failure
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        common = proc.stdout.strip()
        if not os.path.isabs(common):
            common = os.path.abspath(os.path.join(ROOT, common))
        if os.path.basename(common) == ".git":
            return os.path.dirname(common)
    return None


def candidate_keys(keys_file):
    """Ordered (key, label) candidates: env first, then api-keys.yml fields."""
    candidates = []
    seen = set()
    env = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    if env:
        candidates.append((env, "env AUTOOS_OMNIROUTE_KEY"))
        seen.add(env)
    paths = []
    if keys_file:
        paths.append(keys_file)
    env_file = os.environ.get("AUTOOS_KEYS_FILE")
    if env_file:
        paths.append(env_file)
    paths.append(os.path.join(ROOT, "configuration", "api-keys.yml"))
    main = _git_common_root()
    if main:
        paths.append(os.path.join(main, "configuration", "api-keys.yml"))
    for path in paths:
        for value, label in _read_keys_from_file(path):
            if value not in seen:
                seen.add(value)
                candidates.append((value, label))
    return candidates


# ---------------------------------------------------------------------------
# Gateway calls.
# ---------------------------------------------------------------------------

def _request(url, method="GET", body=None, key=None, timeout=120.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {}
    if key:
        headers["Authorization"] = "Bearer " + key
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - status is the finding
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a body-less error is still the status
            raw = ""
        return exc.code, raw
    except Exception as exc:  # noqa: BLE001 - any transport failure is a finding
        return 0, "%s: %s" % (type(exc).__name__, exc)


def list_models(gateway, key, timeout):
    return _request(gateway.rstrip("/") + "/v1/models", key=key, timeout=timeout)


def send_chat(gateway, key, model, max_tokens, timeout):
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user",
                      "content": "Reply with exactly the word OK and nothing else."}],
        "stream": False,
    }
    return _request(gateway.rstrip("/") + "/v1/chat/completions",
                    "POST", body, key=key, timeout=timeout)


def send_with_retry(gateway, key, model, max_tokens, timeout, retries, sleep_s):
    """Send, backing off (default 90 s) on an admission/rate-limit response."""
    attempt = 0
    while True:
        status, body = send_chat(gateway, key, model, max_tokens, timeout)
        limited = status == 429 or bool(RATE_LIMIT_RE.search(body))
        if not limited or attempt >= retries:
            return status, body, attempt
        print("rate-limit (UTC %s): %s; backoff %gs, retry %d/%d"
              % (utc_now(), safe(body, 160), sleep_s, attempt + 1, retries))
        time.sleep(sleep_s)
        attempt += 1


# ---------------------------------------------------------------------------
# Discovery and classification.
# ---------------------------------------------------------------------------

def discover_legs(models):
    """All live ids whose model segment is CLAMP_MODEL, and the preferred one."""
    matches = [m for m in models
               if isinstance(m, str) and m.rsplit("/", 1)[-1] == CLAMP_MODEL]
    for prefix in CLAMP_PREFIXES:
        preferred = [m for m in matches if m.startswith(prefix)]
        if preferred:
            return preferred[0], matches
    return (matches[0] if matches else None), matches


def pick_contrast(models):
    for candidate in CONTRAST_CANDIDATES:
        if candidate in models:
            return candidate
    return None


def skip_reason(status, body):
    """The verbatim, key-redacted reason a non-200/400-cap answer is a SKIP."""
    if status == 0:
        return safe(body, 160) or "connection failed"
    labels = {401: "auth", 402: "credit/quota", 403: "forbidden",
              429: "rate limited", 404: "not found"}
    label = labels.get(status)
    text = safe(body, 160)
    if label:
        return "HTTP %d (%s)%s" % (status, label, (": " + text) if text else "")
    return "HTTP %d%s" % (status, (": " + text) if text else "")


def compiled_table_evidence(package_dir):
    """(present, total) chunks carrying clampToModelMaxOutput, or None."""
    if not package_dir:
        return None
    chunks = os.path.join(package_dir, "dist", ".build", "next", "server", "chunks")
    if os.path.basename(os.path.normpath(package_dir)) == "chunks":
        chunks = package_dir
    present = total = 0
    for name in COMPILED_CHUNKS:
        path = os.path.join(chunks, name)
        if not os.path.isfile(path):
            continue
        total += 1
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                if "clampToModelMaxOutput" in handle.read():
                    present += 1
        except OSError:
            pass
    return (present, total) if total else None


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Live-verify the OmniRoute qwen3-235b output-cap clamp.")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY,
                        help="gateway base URL (default %(default)s)")
    parser.add_argument("--model", default=None,
                        help="clamp leg override (default: discovered scaleway leg)")
    parser.add_argument("--contrast-model", default=None,
                        help="non-clamped model for the contrast case")
    parser.add_argument("--max-tokens", type=int, default=ABOVE_CAP,
                        help="over-cap max_tokens to send (default %(default)s)")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--keys-file", default=None,
                        help="api-keys.yml override (in-memory; never printed)")
    parser.add_argument("--package-dir", default=None,
                        help="omniroute package dir, for read-only compiled-table evidence")
    parser.add_argument("--retries", type=int, default=2,
                        help="retries on a rate-limit/admission response")
    parser.add_argument("--retry-sleep", type=float, default=90.0,
                        help="seconds to back off per retry (the 60-120 s band)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    global _KEY

    result = {"gateway": args.gateway, "max_tokens": args.max_tokens,
              "cap": CAP, "verdict": None, "reason": None}

    candidates = candidate_keys(args.keys_file)
    if not candidates:
        result["verdict"] = "SKIP"
        result["reason"] = ("no gateway key found (AUTOOS_OMNIROUTE_KEY or "
                            "configuration/api-keys.yml)")
        return _emit(args, result)

    # Use the first candidate key the gateway accepts; a stale env key must not
    # mask a working api-keys.yml field (and vice versa).
    key_label = None
    listed_body = None
    last_status, last_body = 0, ""
    for value, label in candidates:
        _KEY = value
        last_status, last_body = list_models(args.gateway, _KEY, args.timeout)
        if last_status == 200:
            key_label = label
            listed_body = last_body
            break
    result["key_source"] = key_label
    if key_label is None:
        result["verdict"] = "SKIP"
        result["reason"] = ("could not list models: %s (tried %d key candidate(s))"
                            % (skip_reason(last_status, last_body), len(candidates)))
        return _emit(args, result)

    try:
        models = [m.get("id") for m in json.loads(listed_body).get("data", [])
                  if isinstance(m, dict)]
    except (ValueError, AttributeError):
        result["verdict"] = "SKIP"
        result["reason"] = "could not parse /v1/models"
        return _emit(args, result)

    leg = args.model or discover_legs(models)[0]
    result["leg"] = leg
    result["matches"] = discover_legs(models)[1]
    if not leg:
        result["verdict"] = "SKIP"
        result["reason"] = ("no scaleway %s leg in the live catalog" % CLAMP_MODEL)
        return _emit(args, result)

    if args.max_tokens <= CAP:
        print("warning: --max-tokens %d is not above the %d cap; this does not "
              "exercise the clamp" % (args.max_tokens, CAP))

    status, body, retries = send_with_retry(
        args.gateway, _KEY, leg, args.max_tokens, args.timeout,
        args.retries, args.retry_sleep)
    result["status"] = status
    result["cap_limit_400"] = bool(status == 400 and CAP_LIMIT_RE.search(body))
    result["response"] = safe(body, 200)
    result["retries"] = retries

    if status == 200:
        result["verdict"] = "PASS"
        result["reason"] = ("over-cap %s request answered 200; no max_completion_tokens "
                            "cap 400 (clamped, not rejected)" % CLAMP_MODEL)
    elif result["cap_limit_400"]:
        result["verdict"] = "FAIL"
        result["reason"] = ("gateway returned the unclamped cap 400: " + safe(body, 200))
    else:
        result["verdict"] = "SKIP"
        result["reason"] = ("scaleway %s leg returned %s"
                            % (CLAMP_MODEL, skip_reason(status, body)))

    contrast = args.contrast_model or pick_contrast(models)
    result["contrast_model"] = contrast
    if contrast:
        c_status, c_body = send_chat(args.gateway, _KEY, contrast,
                                     args.max_tokens, args.timeout)
        result["contrast_status"] = c_status
        result["contrast_response"] = safe(c_body, 120)

    evidence = compiled_table_evidence(args.package_dir)
    if evidence:
        result["compiled_table"] = {"present": evidence[0], "total": evidence[1]}

    return _emit(args, result)


def _emit(args, result):
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("probe-clamp - %s (UTC %s)" % (result.get("gateway"), utc_now()))
        if result.get("key_source"):
            print("key source: %s" % result["key_source"])
        if result.get("matches") is not None:
            print("catalog legs matching %s: %s"
                  % (CLAMP_MODEL, ", ".join(result["matches"]) or "(none)"))
        if result.get("leg"):
            print("clamp leg: %s (max_tokens=%d, cap=%d)"
                  % (result["leg"], result["max_tokens"], result["cap"]))
        if "status" in result:
            print("clamp-leg status: %s" % result["status"])
            print("unclamped cap 400 seen: %s" % bool(result.get("cap_limit_400")))
        if result.get("contrast_model"):
            print("contrast leg: %s status: %s"
                  % (result["contrast_model"], result.get("contrast_status")))
        if result.get("compiled_table"):
            table = result["compiled_table"]
            print("compiled-table evidence: clamp rule present in %d/%d chunks"
                  % (table["present"], table["total"]))
        print("VERDICT: %s - %s" % (result["verdict"], result["reason"]))
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
