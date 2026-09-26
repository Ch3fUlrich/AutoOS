#!/usr/bin/env python3
"""Probe every routing leg for tool-calling capability; write the overlay.

Spec: docs/plans/2026-09-25-routing-v2-spec.md sections 3.1 ("Measured values
never write the registry") and 5.3 step 1 (tool_calls = proven required for
agentic kinds) and section 10 (probes, free legs only).

catalog/ai-registry.json is generated (tools/registry-convert.py) and starts
every model at tool_calls "unproven". This script sends each distinct leg
(a "<provider>/<model>" string exactly as written in a route's ``legs``) two
tool-calling trials through the OmniRoute gateway (OpenAI chat/completions
format: the "model" field is the leg string and OmniRoute routes it straight
to that leg) and records a verdict in the git-ignored overlay
logs/routing/measured.json. tools/autoos_resolver.py's filter_routes() reads
that overlay (overlay wins over the registry) when it enforces "tool_calls =
proven" for implement/debug/bulk kinds.

A trial has two checks:
  (a) single call: ask for the weather in Paris with a get_weather(city)
      tool; pass = exactly one tool_call named get_weather whose arguments
      parse as JSON with a "city" containing "paris" (case-insensitive).
  (b) round trip: continue the conversation with that assistant tool_call and
      a role "tool" result {"temp_c": 18}; pass = a final assistant message
      whose content mentions "18" and makes no further tool_calls.

Usage:
    python3 tools/probe-toolcalls.py --dry-run
    python3 tools/probe-toolcalls.py --leg deepseek/deepseek-flash --trials 5
    python3 tools/probe-toolcalls.py --route t2-worker
    python3 tools/probe-toolcalls.py --registry catalog/ai-registry.json \\
        --overlay logs/routing/measured.json --gateway http://127.0.0.1:20128/v1/chat/completions

Exit codes: 0 the probe ran (verdicts, including "broken"/"unproven", are
data, not failure); 2 bad arguments or an unreadable registry; 3 no OmniRoute
client key or the gateway is unreachable.

Never prints or logs the gateway key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from registry import resolve_leg  # noqa: E402 - tools/ is on sys.path above

DEFAULT_REGISTRY = os.path.join(ROOT, "catalog", "ai-registry.json")
DEFAULT_OVERLAY = os.path.join(ROOT, "logs", "routing", "measured.json")
DEFAULT_GATEWAY = "http://127.0.0.1:20128/v1/chat/completions"

# 429/503 are transient (rate limit / load shedding); retried before either
# call is called an error. Same shape as tools/audit-router.py's _chat retry.
RETRY_DELAYS_S = (5, 15, 45)
RETRY_STATUSES = (429, 503)

# Statuses that mean "we learned nothing about this leg's tool-calling
# ability", never a verdict: keep whatever the overlay already said.
# 401/403: the gateway has no credentials for the provider (a sign-in gap,
# measured 2026-09-26 on antigravity/cc/cerebras) - no fact about tool calling.
NO_VERDICT_STATUSES = (401, 402, 403, 429, "ERR", "timeout")

# Assignable, so tests need no real network wait.
_sleep = time.sleep


# ---------------------------------------------------------------------------
# legs_to_probe: which legs exist, and which of them to skip.
# ---------------------------------------------------------------------------

def _skip_reason(leg, routes, registry):
    """None when `leg` should be probed; else the reason it is skipped."""
    for route in routes.values():
        if leg in (route.get("unavailable_legs") or {}):
            return "unavailable_legs: %s" % leg
    try:
        provider_id, model_id = resolve_leg(leg, registry)
    except ValueError as exc:
        return "unresolvable: %s" % exc
    if (registry.get("providers", {}).get(provider_id) or {}).get("available") is False:
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


# ---------------------------------------------------------------------------
# The two trial checks and the request bodies they need.
# ---------------------------------------------------------------------------

def _weather_tool():
    return {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }


def single_call_body(leg, max_tokens=2048):
    """The first trial request: ask the weather in Paris, one tool offered."""
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [_weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def round_trip_body(leg, assistant_message, call_id, max_tokens=2048):
    """The second trial request: the tool result fed back, same tool offered."""
    return {
        "model": leg,
        "messages": [
            {"role": "user", "content": "What is the weather in Paris?"},
            assistant_message,
            {"role": "tool", "tool_call_id": call_id,
             "content": json.dumps({"temp_c": 18})},
        ],
        "tools": [_weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def _check_single(parsed):
    """(ok, tool_call_or_None, note) for the single-call check."""
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False, None, "malformed response: no choices[0].message"
    calls = message.get("tool_calls") or []
    if len(calls) != 1:
        return False, None, "expected exactly one tool_call, got %d" % len(calls)
    call = calls[0]
    try:
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"]
    except (KeyError, TypeError):
        return False, None, "tool_call missing function.name/arguments"
    if name != "get_weather":
        return False, None, "tool_call named %r, not get_weather" % (name,)
    try:
        args = json.loads(raw_args)
    except (TypeError, ValueError):
        return False, None, "arguments did not parse as JSON: %r" % (raw_args,)
    city = str((args or {}).get("city", ""))
    if "paris" not in city.lower():
        return False, None, "city %r does not contain 'paris'" % (city,)
    return True, call, "ok"


def _check_round_trip(parsed):
    """(ok, note) for the round-trip check."""
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False, "malformed response: no choices[0].message"
    if message.get("tool_calls"):
        return False, "assistant made another tool_call instead of answering"
    content = message.get("content") or ""
    if "18" not in content:
        return False, "final content does not mention 18: %r" % (content[:120],)
    return True, "ok"


def _post_with_retry(post, body):
    """Call `post(body)`, retrying 429/503 with RETRY_DELAYS_S backoff."""
    delays = list(RETRY_DELAYS_S)
    while True:
        status, parsed, error = post(body)
        if status not in RETRY_STATUSES or not delays:
            return status, parsed, error
        _sleep(delays.pop(0))


def run_trial(leg, post, max_tokens=2048):
    """One trial: the single call, then (if it passed) the round trip.

    Returns {single: pass|fail|error, round: pass|fail|error|skipped,
    status, note}. `status` is always the single call's HTTP status: that is
    the call classify() judges "answered" or "broken" on.
    """
    status, parsed, error = _post_with_retry(post, single_call_body(leg, max_tokens))
    if status != 200 or parsed is None:
        return {"single": "error", "round": "skipped", "status": status,
                "note": error or "malformed or missing response body"}

    ok, call, note = _check_single(parsed)
    if not ok:
        return {"single": "fail", "round": "skipped", "status": status, "note": note}

    assistant_message = parsed["choices"][0]["message"]
    rt_status, rt_parsed, rt_error = _post_with_retry(
        post, round_trip_body(leg, assistant_message, call.get("id"), max_tokens))
    if rt_status != 200 or rt_parsed is None:
        return {"single": "pass", "round": "error", "status": status,
                "note": "round trip: %s" % (rt_error or "HTTP %s" % (rt_status,))}

    rt_ok, rt_note = _check_round_trip(rt_parsed)
    return {"single": "pass", "round": "pass" if rt_ok else "fail",
            "status": status, "note": rt_note}


# ---------------------------------------------------------------------------
# classify: trials -> verdict.
# ---------------------------------------------------------------------------

def _is_no_verdict_status(status):
    if status in NO_VERDICT_STATUSES:
        return True
    return isinstance(status, int) and 500 <= status < 600


def classify(trials):
    """(value_or_None, detail) verdict for a leg's list of trial results."""
    if not trials:
        return None, "no trials run"

    if all(t["single"] == "pass" and t["round"] == "pass" for t in trials):
        return "proven", "all %d trial(s) passed single + round trip" % len(trials)

    answered = [t for t in trials if t["status"] == 200]
    if answered and len(answered) == len(trials) and all(
            t["single"] == "fail" for t in answered):
        return "broken", "every trial answered (HTTP 200) but produced no valid tool_call"

    for t in trials:
        if t["status"] == 400 and re.search(r"(?i)\b(tool|function)\b", t.get("note") or ""):
            return "broken", "HTTP 400 mentions tool/function support: %s" % t["note"]

    if all(_is_no_verdict_status(t["status"]) for t in trials):
        return None, "only credential/transport errors (%s): keeping the previous value" % (
            ", ".join(str(t["status"]) for t in trials))

    return "unproven", "mixed pass/fail across %d trial(s)" % len(trials)


# ---------------------------------------------------------------------------
# Overlay: read-modify-write, atomic, never a None verdict.
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


def record_verdict(overlay, leg, value, detail, trials, passes, at):
    """Merge one leg's verdict into `overlay` (read-modify-write), in place.

    `value` None never writes ``tool_calls``: it is recorded under
    ``tool_calls_last_error`` instead, and any previous ``tool_calls`` value
    is left untouched. Every other key already in `overlay` (other legs,
    other fields on this leg) is kept as-is.
    """
    legs = overlay.setdefault("legs", {})
    entry = legs.setdefault(leg, {})
    if value is None:
        entry["tool_calls_last_error"] = {
            "detail": detail, "trials": trials, "passes": passes, "at": at}
        return overlay
    entry["tool_calls"] = {"value": value, "source": "probe", "trials": trials,
                           "passes": passes, "detail": detail, "at": at}
    return overlay


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
        except urllib.error.HTTPError as exc:
            return exc.code, None, exc.read(500).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - any transport failure is a finding
            return "ERR", None, str(exc)[:500]
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


def _load_agent_module():
    """tools/autoos-agent.py, loaded by path (a hyphen is not importable)."""
    agent_path = os.path.join(HERE, "autoos-agent.py")
    spec = importlib.util.spec_from_file_location("autoos_agent_for_probe", agent_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe routing legs for tool-calling capability; write the overlay.")
    ap.add_argument("--leg", action="append", default=[],
                    help="only this leg (repeatable); default: every distinct leg")
    ap.add_argument("--route", action="append", default=[],
                    help="only legs of this route (repeatable)")
    ap.add_argument("--trials", type=int, default=3, help="trials per leg (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list legs and skip reasons; make no request")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--overlay", default=DEFAULT_OVERLAY)
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    args = ap.parse_args(argv)

    if args.trials < 1:
        print("probe-toolcalls: --trials must be at least 1", file=sys.stderr)
        return 2

    try:
        with open(args.registry, encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError) as exc:
        print("probe-toolcalls: cannot read registry %s: %s" % (args.registry, exc),
              file=sys.stderr)
        return 2

    todo = legs_to_probe(registry, only_legs=args.leg, only_routes=args.route)

    if args.dry_run:
        for leg, skip in todo:
            print("%s\t%s" % (leg, skip or "probe"))
        return 0

    agent = _load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-toolcalls: no OmniRoute client key (export AUTOOS_OMNIROUTE_KEY or "
              "add 'omniroute:' to configuration/api-keys.yml)", file=sys.stderr)
        return 3
    if not gateway_up(args.gateway):
        print("probe-toolcalls: gateway not reachable at %s" % args.gateway, file=sys.stderr)
        return 3

    post = make_post(args.gateway, key)
    overlay = load_overlay(args.overlay)
    for leg, skip in todo:
        if skip:
            print("%s\tskip\t-/%d\t%s" % (leg, args.trials, skip))
            continue
        trials = [run_trial(leg, post) for _ in range(args.trials)]
        passes = sum(1 for t in trials if t["single"] == "pass" and t["round"] == "pass")
        value, detail = classify(trials)
        overlay = record_verdict(overlay, leg, value, detail, trials, passes, _now_iso())
        print("%s\t%s\t%d/%d\t%s" % (leg, value or "no-verdict", passes, args.trials, detail))
    save_overlay(args.overlay, overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
