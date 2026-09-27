#!/usr/bin/env python3
"""Probe every routing leg for tool-calling capability; write the overlay.

Spec: docs/plans/2026-09-25-routing-v2-spec.md sections 3.1 ("Measured values
never write the registry") and 5.3 step 1 (tool_calls = proven required for
agentic kinds) and section 10 (probes, free legs only).

catalog/ai-registry.json is the hand-edited source of truth and starts
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

Never prints or logs the gateway key, nor a provider's error body: an error
is the status, plus the one fact read out of a 400's body (see
`TOOLS_UNSUPPORTED` below).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import (  # noqa: E402 - tools/ is on sys.path above
    DEFAULT_GATEWAY,
    DEFAULT_OVERLAY,
    DEFAULT_REGISTRY,
    RETRY_DELAYS_S,  # re-exported: tests read it on this module
    _skip_reason,  # re-exported: tests read the shared leg selection here
    gateway_up,
    legs_to_probe,  # shared with probe-recall/probe-effort (policy, free-only)
    load_agent_module as _load_agent_module,
    load_overlay,
    make_post as _common_make_post,
    now_iso as _now_iso,
    post_with_retry,
    save_overlay,
)

# Statuses that mean "we learned nothing about this leg's tool-calling
# ability", never a verdict: keep whatever the overlay already said.
# 401/403: the gateway has no credentials for the provider (a sign-in gap,
# measured 2026-09-26 on antigravity/cc/cerebras) - no fact about tool calling.
# This is NOT probe_common.is_no_verdict_status(): there every non-200 is a
# no-verdict, and here an HTTP 400 that says the leg has no tools is a verdict.
NO_VERDICT_STATUSES = (401, 402, 403, 429, "ERR", "timeout")

# The one deliberate use of a provider error body. An HTTP 400 that says the
# leg has no tool/function support is a "broken" verdict here, so the body is
# read - but only inside make_post, only to choose between this fixed token
# and a bare "HTTP <code>". classify() matches this token and never reads a
# body: the body is the provider's text (org/project ids, internal hosts) and
# the error reaches stdout and the overlay detail (AGENTS.md rule 1).
TOOLS_UNSUPPORTED = "HTTP 400 (mentions tool/function support)"

# Providers write it both ways ("tool use is not supported", "tools are not
# supported"), so the plural counts.
TOOLS_MENTION = re.compile(r"(?i)\b(tool|function)s?\b")

# Assignable, so tests need no real network wait.
_sleep = time.sleep


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


def run_trial(leg, post, max_tokens=2048):
    """One trial: the single call, then (if it passed) the round trip.

    Returns {single: pass|fail|error, round: pass|fail|error|skipped,
    status, note}. `status` is always the single call's HTTP status: that is
    the call classify() judges "answered" or "broken" on. `note` is a check's
    own text or the fixed error token — never a provider body.
    """
    status, parsed, error = post_with_retry(
        post, single_call_body(leg, max_tokens), _sleep)
    if status != 200 or parsed is None:
        return {"single": "error", "round": "skipped", "status": status,
                "note": error or "malformed or missing response body"}

    ok, call, note = _check_single(parsed)
    if not ok:
        return {"single": "fail", "round": "skipped", "status": status, "note": note}

    assistant_message = parsed["choices"][0]["message"]
    rt_status, rt_parsed, rt_error = post_with_retry(
        post, round_trip_body(leg, assistant_message, call.get("id"), max_tokens),
        _sleep)
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
        # The fixed token make_post read out of the body, never the body here.
        if t["status"] == 400 and (t.get("note") or "") == TOOLS_UNSUPPORTED:
            return "broken", "HTTP 400 mentions tool/function support"

    if all(_is_no_verdict_status(t["status"]) for t in trials):
        return None, "only credential/transport errors (%s): keeping the previous value" % (
            ", ".join(str(t["status"]) for t in trials))

    return "unproven", "mixed pass/fail across %d trial(s)" % len(trials)


# ---------------------------------------------------------------------------
# Overlay: read-modify-write, never a None verdict.
# (load_overlay / save_overlay are tools/probe_common.py's.)
# ---------------------------------------------------------------------------

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
# The real (network) post(). The gateway probe, the agent-module load that
# reads the key and the timestamp are tools/probe_common.py's.
# ---------------------------------------------------------------------------

def _classify_error(exc):
    """The fixed token for a failed call whose body is allowed to carry one
    fact: an HTTP 400 that says the leg has no tool/function support.

    This is the classifier handed to tools/probe_common.py's make_post(), which
    now owns the urlopen loop. Only a 400's body is read at all; it is read
    only to pick the token, and only the token is returned — never the body,
    never an exception's text (a provider body carries org/project ids and
    internal hosts, and the error text is printed and stored in the overlay
    detail). Returning None lets the common post report the bare status.
    """
    if exc.code != 400:
        return None
    body = exc.read(500).decode("utf-8", "replace")
    if TOOLS_MENTION.search(body):
        return TOOLS_UNSUPPORTED
    return None


def make_post(gateway_url, key, timeout=180):
    """A real `post(body) -> (status, parsed_json_or_None, error_text)`.

    A thin wrapper over tools/probe_common.py's make_post: this is the one
    probe for which a 400's body is a verdict (no tool/function support), so
    it is the one caller that passes the body-reading `_classify_error` hook
    above. Everything else - the request, the transport handling, never
    returning a provider body - is the shared post's one implementation.
    """
    return _common_make_post(gateway_url, key, timeout=timeout,
                             classify_error=_classify_error)


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
