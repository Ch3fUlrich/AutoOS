#!/usr/bin/env python3
"""Shared plumbing for the routing probes (spec section 10, D18).

tools/probe-recall.py and tools/probe-effort.py both measure free legs and
write the git-ignored overlay logs/routing/measured.json (spec section 3.1:
measured values never write the registry). Everything the two probes share
lives here, once, so a routing rule cannot drift between them:

- which legs exist, which to skip, and which to refuse outright (free-leg
  selection: a paid or subscription leg is never probed);
- the gateway post with its 429/503 retry and the no-verdict statuses
  (a credential or transport failure keeps the previous overlay value);
- the key/gateway plumbing (the OmniRoute client key is read but never
  printed, logged or written anywhere);
- the overlay read-modify-write (atomic save, every other key kept);
- the per-request token-usage log lines.

Never prints or logs the gateway key.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from registry import (  # noqa: E402 - tools/ is on sys.path above
    leg_denied,
    leg_rule_for,
    resolve_leg,
    unavailable_now,
)

DEFAULT_REGISTRY = os.path.join(ROOT, "catalog", "ai-registry.json")
DEFAULT_OVERLAY = os.path.join(ROOT, "logs", "routing", "measured.json")
DEFAULT_GATEWAY = "http://127.0.0.1:20128/v1/chat/completions"

# 429/503 are transient (rate limit / load shedding); retried before a call
# is scored.
RETRY_DELAYS_S = (5, 15, 45)
RETRY_STATUSES = (429, 503)

# Only a 200 is a measurement. Every other status means "we learned nothing
# about this leg", never a verdict: keep whatever the overlay already said.
# 401/402/403 are a sign-in or credit gap, 429 a rate limit, "ERR"/timeout
# transport - and 4xx like 413 are the provider's plan limits, not the
# model's (live 2026-09-27: groq free TPM 8000 answered 413 to a 25k
# prompt, which the old 5xx/credential-only rule scored as recall 0.0).
# is_no_verdict_status() below is that rule.


# ---------------------------------------------------------------------------
# legs_to_probe / refused_leg: which legs exist, which to skip, which to
# refuse outright.
# ---------------------------------------------------------------------------

def _skip_reason(leg, routes, registry):
    """None when `leg` should be probed; else the reason it is skipped."""
    # Checked first (TOOLFIX item 1, spec D18: decided by the routing owner
    # 2026-09-27): a leg policy.leg_rules denies is never probed, even when it
    # is otherwise free and available. The rule id is named so a --dry-run line
    # says which policy gate stopped it.
    if leg_denied(leg, registry):
        rule = leg_rule_for(leg, registry) or {}
        return "policy: denied by %s" % rule.get("id", "(unnamed)")
    for route in routes.values():
        if leg in (route.get("unavailable_legs") or {}):
            return "unavailable_legs: %s" % leg
    try:
        provider_id, model_id = resolve_leg(leg, registry)
    except ValueError as exc:
        return "unresolvable: %s" % exc
    provider = registry.get("providers", {}).get(provider_id) or {}
    if provider.get("tier") != "free":
        # Free legs only (spec section 10, D18): a probe never spends paid or
        # subscription quota. A named such leg is a refusal, not a skip.
        return "tier: %s" % provider.get("tier")
    if unavailable_now(provider, datetime.now(timezone.utc)):
        until = provider.get("unavailable_until")
        if until is not None:
            return "provider %s: unavailable until %s" % (provider_id, until)
        return "provider %s: available false" % provider_id
    bound = (registry.get("models", {}).get(model_id) or {}).get("client_bound")
    if bound:
        # The gateway 403s a client-bound leg (e.g. a Zen free leg): it can
        # only be probed through that client, never through the gateway.
        return "client_bound: probe through %s" % bound
    return None


def legs_to_probe(registry, only_legs=(), only_routes=()):
    """``[(leg, skip_reason_or_None), ...]``: every distinct leg, registry order.

    Every leg named in any ``routes.*.legs``, in the order routes and then
    legs appear in the registry, deduplicated to first sight. ``only_legs`` /
    ``only_routes`` (non-empty) narrow which legs/routes are considered at
    all; a leg outside both stays unlisted rather than skipped.
    """
    routes = registry.get("routes") or {}
    only_legs = set(only_legs)
    only_routes = set(only_routes)
    order = []
    seen = set()
    for route_id, route in routes.items():
        if only_routes and route_id not in only_routes:
            continue
        for leg in route.get("legs") or []:
            if only_legs and leg not in only_legs:
                continue
            if leg not in seen:
                seen.add(leg)
                order.append(leg)
    return [(leg, _skip_reason(leg, routes, registry)) for leg in order]


def refused_leg(registry, only_legs):
    """The first leg in `only_legs` on a non-free provider, as (leg, tier).

    None when every named leg is free (or unresolvable - those surface as a
    skip reason in legs_to_probe, not a refusal: their tier is unknown).
    """
    for leg in only_legs:
        try:
            provider_id, _model_id = resolve_leg(leg, registry)
        except ValueError:
            continue
        provider = registry.get("providers", {}).get(provider_id) or {}
        if provider.get("tier") != "free":
            return leg, provider.get("tier")
    return None


# ---------------------------------------------------------------------------
# The gateway post: retry, no-verdict statuses.
# ---------------------------------------------------------------------------

def is_no_verdict_status(status):
    """True when `status` measured nothing (a verdict is never drawn from it)."""
    return status != 200


def post_with_retry(post, body, sleep):
    """Call `post(body)`, retrying 429/503 with RETRY_DELAYS_S backoff.

    `sleep` is injected - each probe keeps its own ``_sleep = time.sleep``
    module hook and passes it here - so tests need no real network wait.
    """
    delays = list(RETRY_DELAYS_S)
    while True:
        status, parsed, error = post(body)
        if status not in RETRY_STATUSES or not delays:
            return status, parsed, error
        sleep(delays.pop(0))


# ---------------------------------------------------------------------------
# Overlay: read-modify-write, atomic, keyed by model id.
# ---------------------------------------------------------------------------

def load_overlay(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_overlay(path, overlay):
    """Atomic write: a temp file in the same directory, then os.replace."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".measured-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(overlay, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# The real (network) post(), and the gateway/key plumbing around it.
# ---------------------------------------------------------------------------

def make_post(gateway_url, key, timeout=180):
    """A real `post(body) -> (status, parsed_json_or_None, error_text)`."""
    def post(body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            gateway_url, data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read()
        # Never return a provider error body or exception text: they carry
        # org/project ids and hosts, and the error reaches stdout and the
        # overlay detail. The status (or exception type) is the finding.
        except urllib.error.HTTPError as exc:
            return exc.code, None, "HTTP %d" % exc.code
        except Exception as exc:  # noqa: BLE001 - any transport failure is a finding
            return "ERR", None, "transport error: %s" % type(exc).__name__
        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            return status, None, "invalid JSON body: %s" % exc
        return status, parsed, None
    return post


def gateway_up(gateway_url, timeout=3):
    base = gateway_url.rsplit("/v1/", 1)[0] if "/v1/" in gateway_url else gateway_url
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - unreachable is unreachable
        return False


def load_agent_module():
    """tools/autoos-agent.py, loaded by path (a hyphen is not importable)."""
    agent_path = os.path.join(HERE, "autoos-agent.py")
    spec = importlib.util.spec_from_file_location("autoos_agent_for_probe", agent_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Token-usage log lines (spec section 10: every probe logs its token use).
# ---------------------------------------------------------------------------

# A column the caller does not have at all (recall has no reasoning column)
# is not the same as an unknown value (None prints as "-").
OMITTED = object()


def _num(value):
    return "-" if value is None else str(value)


def print_request(leg, label, prompt_tokens, completion_tokens,
                  reasoning_tokens=OMITTED):
    """One per-request token-usage line: leg, request, label, usage."""
    cols = [leg, "request", str(label), _num(prompt_tokens),
            _num(completion_tokens)]
    if reasoning_tokens is not OMITTED:
        cols.append(_num(reasoning_tokens))
    print("\t".join(cols))


def print_total(prompt_tokens, completion_tokens, reasoning_tokens=OMITTED):
    """The end-of-run usage total line."""
    cols = ["total", _num(prompt_tokens), _num(completion_tokens)]
    if reasoning_tokens is not OMITTED:
        cols.append(_num(reasoning_tokens))
    print("\t".join(cols))
