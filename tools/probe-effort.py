#!/usr/bin/env python3
"""Probe reasoning effort rungs on free reasoning legs; write the overlay.

Spec: docs/plans/2026-09-25-routing-v2-spec.md sections 3.1 ("Measured values
never write the registry"), 5.5 (the effort axis and the reasoning output
budget) and 10 (probes, free legs only, D18; n >= 5, each probe logs its
token use).

A route's effort pick is only as good as the per-rung measurements behind
it, so this script measures every distinct free leg (the shared free-leg
rule of tools/probe_common.py; a paid or subscription leg named with --leg
is refused outright, exit 4, no request made) whose model has
``reasoning: true`` and a non-empty ``effort_ladder``. For each rung of the
ladder - a rung the model lacks is never sent, only its own ladder is
probed - it runs ``--trials`` trials (default 5; below 5 is refused without
``--allow-fewer``, spec section 10's n >= 5 bar) of one fixed built-in task
set: five small deterministic reasoning/arithmetic/string puzzles with
exact checkable answers, one answer per line, the reply's last line being
the fifth answer. A trial passes when every answer is exactly right.

Each request sends ``"reasoning_effort": <rung>`` (the field is omitted
entirely for rung "none" - that rung measures reasoning off, not a literal
"none" effort string) and the spec section 5.5 output budget: max_tokens
>= 48000 at any effort, >= 64000 at high/xhigh/max, capped by the model's
output_max. Per rung the probe records n, passes, pass_rate, the
reasoning_tokens mean (usage.completion_tokens_details.reasoning_tokens;
null when no trial reported it), the completion_tokens mean and the
latency mean (per attempt - a 429's backoff sleep is not the model's
latency).

The result lands in the git-ignored overlay logs/routing/measured.json as
overlay["models"][<model_id>]["effort"] (read-modify-write: every other
key - including context_usable written by tools/probe-recall.py - is
kept). A rung whose every trial hit a no-verdict status (401/402/403/429/
5xx/"ERR"/timeout) keeps its previous value instead of being overwritten.
Every request logs leg, rung, prompt_tokens, completion_tokens and
reasoning_tokens to stdout, with a total at the end.

Usage:
    python3 tools/probe-effort.py --dry-run
    python3 tools/probe-effort.py --leg antigravity/claude-opus-4-6-thinking
    python3 tools/probe-effort.py --route t2-worker
    python3 tools/probe-effort.py --registry catalog/ai-registry.json \\
        --overlay logs/routing/measured.json --gateway http://127.0.0.1:20128/v1/chat/completions

Exit codes: 0 the probe ran; 2 bad arguments or an unreadable registry; 3
no OmniRoute client key or the gateway is unreachable; 4 a --leg named a
non-free (paid or subscription) leg - refused, no request made.

Never prints or logs the gateway key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from registry import resolve_leg  # noqa: E402 - tools/ is on sys.path above
from probe_common import (  # noqa: E402 - tools/ is on sys.path above
    DEFAULT_GATEWAY,
    DEFAULT_OVERLAY,
    DEFAULT_REGISTRY,
    RETRY_DELAYS_S,  # re-exported: tests read it on this module
    gateway_up,
    is_no_verdict_status,
    legs_to_probe,
    load_agent_module as _load_agent_module,
    load_overlay,
    make_post,
    now_iso,
    post_with_retry,
    print_request,
    print_total,
    refused_leg,
    save_overlay,
)

# Spec section 10: a rung's pass rate means anything only from n >= 5 on;
# --allow-fewer waives the bar knowingly (debugging, a nearly-dead quota).
MIN_TRIALS = 5
DEFAULT_TRIALS = 5

# Spec section 5.5's output budget for a reasoning model: max_tokens >= 48k
# at any effort (measured: 16k is exhausted by reasoning alone), >= 64k at
# high and above, capped by output_max.
TOKENS_ANY_EFFORT = 48000
TOKENS_HIGH_EFFORT = 64000
HIGH_RUNGS = ("high", "xhigh", "max")

# Assignable, so tests need no real network wait.
_sleep = time.sleep


# ---------------------------------------------------------------------------
# The fixed task set and the request body.
# ---------------------------------------------------------------------------

# The task set is fixed on purpose: every rung of every model sees the same
# five puzzles, so a pass-rate difference between rungs is the model, not
# the task, and a test knows every answer without touching the network.
TASKS = (
    ("What is 17 * 23 + 5?", "396"),
    ("How many distinct prime numbers lie between 10 and 30?", "6"),
    ("Reverse the string 'probes' and write it in lowercase letters.", "seborp"),
    ("What is the next number in the sequence 2, 6, 12, 20, 30?", "42"),
    ("How many letters does the word 'orchestration' have?", "13"),
)

TASK_PROMPT = (
    "Answer each of the five questions below. Reply with exactly five "
    "lines, one answer per line, in order, and nothing else: no numbering, "
    "no working, no trailing text - the last line of your reply must be "
    "the answer to the fifth question.\n\n"
    + "\n".join("%d. %s" % (i, question)
                for i, (question, _answer) in enumerate(TASKS, 1))
)

PROMPT_TOKENS = len(TASK_PROMPT) // 4


def effort_body(leg, rung, max_tokens):
    """The trial request: the fixed task set plus the rung's effort.

    Rung "none" omits reasoning_effort entirely - that rung measures the
    model with reasoning off, not with a literal "none" effort string.
    """
    body = {
        "model": leg,
        "messages": [{"role": "user", "content": TASK_PROMPT}],
        "max_tokens": max_tokens,
    }
    if rung != "none":
        body["reasoning_effort"] = rung
    return body


def max_tokens_for(rung, output_max=None):
    """Spec section 5.5's output budget for `rung`, capped by `output_max`.

    A model whose output_max is below the bar gets all it can emit - the
    cap wins over the minimum (a request above output_max is refused
    upstream), and the record still shows what was actually possible.
    """
    want = TOKENS_HIGH_EFFORT if rung in HIGH_RUNGS else TOKENS_ANY_EFFORT
    if isinstance(output_max, int) and output_max > 0:
        want = min(want, output_max)
    return want


def effort_legs(registry, only_legs=(), only_routes=()):
    """``[(leg, skip_reason_or_None), ...]``: free legs with an effort axis.

    Every skip reason of the shared free-leg rule applies (tier, provider
    down, client_bound, unresolvable); on top of those, a free leg whose
    model is not a reasoning model or carries no effort_ladder is skipped -
    there is no effort axis to measure on it.
    """
    out = []
    for leg, skip in legs_to_probe(registry, only_legs=only_legs,
                                   only_routes=only_routes):
        if skip is not None:
            out.append((leg, skip))
            continue
        _provider_id, model_id = resolve_leg(leg, registry)
        model = registry.get("models", {}).get(model_id) or {}
        if not model.get("reasoning"):
            out.append((leg, "not a reasoning model"))
        elif not model.get("effort_ladder"):
            out.append((leg, "no effort_ladder"))
        else:
            out.append((leg, None))
    return out


# ---------------------------------------------------------------------------
# Scoring: the reply against the fixed answers.
# ---------------------------------------------------------------------------

def score_effort(content):
    """``(passed, note)`` for a model reply against the fixed task set.

    The reply's non-empty lines must be exactly the expected answers, in
    order - so the last line is the fifth answer and any preamble, numbering
    or trailing chatter fails the trial. Never raises.
    """
    lines = [line.strip() for line in (content or "").splitlines()
             if line.strip()]
    expected = [answer for _question, answer in TASKS]
    if lines == expected:
        return True, "%d/%d answers exact" % (len(expected), len(expected))
    if len(lines) != len(expected):
        return False, "expected %d answer lines, got %d" % (len(expected),
                                                            len(lines))
    wrong = next(i for i, (got, want) in enumerate(zip(lines, expected))
                 if got != want)
    return False, "answer %d is %r, expected %r" % (wrong + 1, lines[wrong],
                                                    expected[wrong])


# ---------------------------------------------------------------------------
# Trials, the per-rung aggregate.
# ---------------------------------------------------------------------------

class _TimedPost:
    """Wraps a post: records the last attempt's wall time, so a 429's
    backoff sleep never inflates the latency mean."""

    def __init__(self, post):
        self._post = post
        self.last_latency_s = None

    def __call__(self, body):
        started = time.monotonic()
        result = self._post(body)
        self.last_latency_s = time.monotonic() - started
        return result


def _reasoning_tokens(usage):
    """usage.completion_tokens_details.reasoning_tokens when reported."""
    details = (usage or {}).get("completion_tokens_details") or {}
    value = details.get("reasoning_tokens")
    return value if isinstance(value, int) else None


def run_effort_trial(leg, rung, post, max_tokens):
    """One trial at one rung: the fixed task set, request, score.

    Returns ``{"pass", "status", "prompt_tokens", "completion_tokens",
    "reasoning_tokens", "latency_s", "note"}``. pass is None exactly when
    the trial measured nothing (a credential or transport status): never a
    failure, never a pass.
    """
    timed = _TimedPost(post)
    status, parsed, error = post_with_retry(
        timed, effort_body(leg, rung, max_tokens), _sleep)
    if isinstance(parsed, dict):
        usage = parsed.get("usage") or {}
    else:
        usage = {}
    result = {"status": status,
              "prompt_tokens": usage.get("prompt_tokens"),
              "completion_tokens": usage.get("completion_tokens"),
              "reasoning_tokens": _reasoning_tokens(usage),
              "latency_s": timed.last_latency_s}
    if status != 200 or parsed is None:
        result["pass"] = None if is_no_verdict_status(status) else False
        result["note"] = error or "malformed or missing response body"
        return result
    try:
        content = parsed["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        result.update({"pass": False,
                       "note": "malformed response: no choices[0].message"})
        return result
    passed, note = score_effort(content)
    result.update({"pass": passed, "note": note})
    return result


def _mean(values):
    """The mean of the numeric values (None dropped); None when there are
    none. Rounded to 2 decimals to keep the overlay readable."""
    numbers = [v for v in values if isinstance(v, (int, float))]
    if not numbers:
        return None
    return round(sum(numbers) / float(len(numbers)), 2)


def aggregate_rung(trials):
    """One rung's record from its trial results.

    ``{"n", "passes", "pass_rate", "reasoning_tokens", "completion_tokens",
    "latency_s"}``: n counts only the trials that measured something (a
    credential/transport trial is not a sample); each mean averages the
    values the measured trials actually reported and is null when none did
    - e.g. a provider that never returns
    usage.completion_tokens_details.reasoning_tokens.
    """
    measured = [t for t in trials if t.get("pass") is not None]
    passes = sum(1 for t in measured if t["pass"])
    return {
        "n": len(measured),
        "passes": passes,
        "pass_rate": round(passes / float(len(measured)), 4) if measured else None,
        "reasoning_tokens": _mean([t.get("reasoning_tokens") for t in measured]),
        "completion_tokens": _mean([t.get("completion_tokens") for t in measured]),
        "latency_s": _mean([t.get("latency_s") for t in measured]),
    }


# ---------------------------------------------------------------------------
# Overlay: read-modify-write, keyed by model id (load/save in probe_common).
# ---------------------------------------------------------------------------

def record_effort(overlay, model_id, rungs, at):
    """Merge one model's measured effort rungs into `overlay`, in place.

    `rungs` maps rung -> record for the rungs that measured something; a
    rung absent from it (every trial a credential/transport status) keeps
    whatever the overlay already had. Every other overlay key - other
    models, other fields on this model such as context_usable - is kept
    as-is.
    """
    entry = overlay.setdefault("models", {}).setdefault(model_id, {})
    previous = entry.get("effort")
    merged = dict(previous.get("rungs") or {}) if isinstance(previous, dict) else {}
    merged.update(rungs)
    entry["effort"] = {"source": "probe", "at": at, "rungs": merged}
    return overlay


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt(value):
    return "-" if value is None else str(value)


def _format_rung(record):
    """A compact rendering of one rung's aggregate for the run log."""
    return ("n=%(n)d passes=%(passes)d pass_rate=%(pass_rate)s "
            "reasoning_tokens=%(reasoning_tokens)s "
            "completion_tokens=%(completion_tokens)s latency_s=%(latency_s)s"
            % {"n": record["n"], "passes": record["passes"],
               "pass_rate": _fmt(record["pass_rate"]),
               "reasoning_tokens": _fmt(record["reasoning_tokens"]),
               "completion_tokens": _fmt(record["completion_tokens"]),
               "latency_s": _fmt(record["latency_s"])})


def _rung_budget(ladder, output_max, trials):
    """The worst-case completion budget: every trial of every rung at its
    max_tokens cap (the model will usually emit far less)."""
    return sum(max_tokens_for(rung, output_max) for rung in ladder) * trials


def _print_plan(todo, registry, trials):
    """The --dry-run plan: legs, rungs, trial count, estimated token budget."""
    probeable = 0
    est_prompt = 0
    est_completion = 0
    for leg, skip in todo:
        if skip:
            print("%s\tskip\t%s" % (leg, skip))
            continue
        _provider_id, model_id = resolve_leg(leg, registry)
        model = registry.get("models", {}).get(model_id) or {}
        ladder = model.get("effort_ladder") or []
        output_max = model.get("output_max")
        prompt_est = trials * len(ladder) * PROMPT_TOKENS
        completion_est = _rung_budget(ladder, output_max, trials)
        probeable += 1
        est_prompt += prompt_est
        est_completion += completion_est
        print("%s\tprobe\trungs=%s\ttrials=%d\tmax_tokens=%s\t"
              "prompt_tokens_est=%d\tcompletion_tokens_est=%d"
              % (leg, ",".join(ladder), trials,
                 ",".join(str(max_tokens_for(rung, output_max))
                          for rung in ladder),
                 prompt_est, completion_est))
    print("plan\tlegs=%d\ttrials=%d\tprompt_tokens_est=%d\t"
          "completion_tokens_est=%d"
          % (probeable, trials, est_prompt, est_completion))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe reasoning effort rungs on free reasoning legs; "
                    "write the overlay.")
    ap.add_argument("--leg", action="append", default=[],
                    help="only this leg (repeatable); default: every distinct leg")
    ap.add_argument("--route", action="append", default=[],
                    help="only legs of this route (repeatable)")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS,
                    help="trials per rung (default %d; below %d is refused "
                         "without --allow-fewer - spec section 10's n >= 5 bar)"
                         % (DEFAULT_TRIALS, MIN_TRIALS))
    ap.add_argument("--allow-fewer", action="store_true",
                    help="permit --trials below %d, knowingly waiving the "
                         "n >= 5 bar" % MIN_TRIALS)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (legs, rungs, trial count, estimated "
                         "token budget); make no request")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--overlay", default=DEFAULT_OVERLAY)
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    args = ap.parse_args(argv)

    if args.trials < 1:
        print("probe-effort: --trials must be at least 1", file=sys.stderr)
        return 2
    if args.trials < MIN_TRIALS and not args.allow_fewer:
        print("probe-effort: --trials %d is below spec section 10's n >= 5 "
              "bar; pass --allow-fewer to waive it" % args.trials,
              file=sys.stderr)
        return 2

    try:
        with open(args.registry, encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError) as exc:
        print("probe-effort: cannot read registry %s: %s" % (args.registry, exc),
              file=sys.stderr)
        return 2

    refusal = refused_leg(registry, args.leg)
    if refusal:
        leg, tier = refusal
        print("probe-effort: refusing %s (provider tier %r): probes run on free "
              "legs only (spec section 10, D18); no request made" % (leg, tier),
              file=sys.stderr)
        return 4

    todo = effort_legs(registry, only_legs=args.leg, only_routes=args.route)

    if args.dry_run:
        _print_plan(todo, registry, args.trials)
        return 0

    agent = _load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-effort: no OmniRoute client key (export AUTOOS_OMNIROUTE_KEY or "
              "add 'omniroute:' to configuration/api-keys.yml)", file=sys.stderr)
        return 3
    if not gateway_up(args.gateway):
        print("probe-effort: gateway not reachable at %s" % args.gateway, file=sys.stderr)
        return 3

    post = make_post(args.gateway, key)
    overlay = load_overlay(args.overlay)
    total_prompt = 0
    total_completion = 0
    total_reasoning = 0
    for leg, skip in todo:
        if skip:
            print("%s\tskip\t%s" % (leg, skip))
            continue
        _provider_id, model_id = resolve_leg(leg, registry)
        model = registry.get("models", {}).get(model_id) or {}
        ladder = model.get("effort_ladder") or []
        output_max = model.get("output_max")
        rungs = {}
        for rung in ladder:
            max_tokens = max_tokens_for(rung, output_max)
            trials = []
            for _ in range(args.trials):
                result = run_effort_trial(leg, rung, post, max_tokens)
                trials.append(result)
                print_request(leg, rung, result["prompt_tokens"],
                              result["completion_tokens"],
                              result["reasoning_tokens"])
                if result["prompt_tokens"] is not None:
                    total_prompt += result["prompt_tokens"]
                if result["completion_tokens"] is not None:
                    total_completion += result["completion_tokens"]
                if result["reasoning_tokens"] is not None:
                    total_reasoning += result["reasoning_tokens"]
            record = aggregate_rung(trials)
            if record["n"] == 0:
                print("%s\tno-verdict\t%s\tno verdict: only non-200 statuses: "
                      "keeping the previous value" % (leg, rung))
                continue
            rungs[rung] = record
            print("%s\trung\t%s\t%s" % (leg, rung, _format_rung(record)))
        if rungs:
            record_effort(overlay, model_id, rungs, now_iso())
            print("%s\teffort\t%d/%d rungs measured" % (leg, len(rungs),
                                                        len(ladder)))
        else:
            print("%s\teffort\t0/%d rungs measured; overlay untouched"
                  % (leg, len(ladder)))
    save_overlay(args.overlay, overlay)
    print_total(total_prompt, total_completion, total_reasoning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
