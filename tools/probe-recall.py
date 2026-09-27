#!/usr/bin/env python3
"""Probe routing legs for multi-needle recall; write the overlay.

Spec: docs/plans/2026-09-25-routing-v2-spec.md sections 3.1 ("Measured values
never write the registry") and 10 (probes, free legs only, D18; each probe
logs its token use).

Every model in catalog/ai-registry.json starts at context_usable = 50% of
context_advertised (decision D8) until a probe measures it. This script
measures each distinct leg of every route (a "<provider>/<model>" string
exactly as written in a route's ``legs``) whose provider tier is "free";
a paid or subscription provider's leg is never probed, and naming one with
--leg is refused outright (exit 4, no request made). Sizes above the model's
context_advertised.tokens are never probed either.

A trial at size N builds a deterministic haystack (~4 characters per token,
seeded filler sentences) with 5 needles ("The access code for <name> is
<6-digit code>.") at evenly spread depths, asks the model to list every
access code with its name as a JSON object, and scores recall as the
fraction of needles returned exactly. context_usable.tokens becomes the
largest size where every trial scored >= 0.9; when the smallest probed size
already fails, nothing is written for tokens (the registry default stays in
force) and only a detail is recorded. Every non-200 status (or a transport
error) means no verdict: whatever the overlay already said is kept.

The verdict lands in the git-ignored overlay logs/routing/measured.json as
overlay["models"][<model_id>]["context_usable"] (read-modify-write: every
other key is kept), the shape tools/autoos_resolver.py's usable_context()
already reads. Every request logs leg, size, prompt_tokens and
completion_tokens (from the response usage) to stdout, with a total at the
end.

The free-leg rule, the gateway post with its retry and no-verdict statuses,
the key/gateway plumbing and the overlay read-modify-write are shared with
tools/probe-effort.py in tools/probe_common.py.

Usage:
    python3 tools/probe-recall.py --dry-run
    python3 tools/probe-recall.py --leg groq/qwen-3.8-27b --trials 3
    python3 tools/probe-recall.py --route t2-worker
    python3 tools/probe-recall.py --sizes 32000,128000
    python3 tools/probe-recall.py --registry catalog/ai-registry.json \\
        --overlay logs/routing/measured.json --gateway http://127.0.0.1:20128/v1/chat/completions

Exit codes: 0 the probe ran (a "no usable context" verdict is data, not
failure); 2 bad arguments or an unreadable registry; 3 no OmniRoute client
key or the gateway is unreachable; 4 a --leg named a non-free (paid or
subscription) leg - refused, no request made.

Never prints or logs the gateway key.
"""
from __future__ import annotations

import argparse
import json
import os
import random
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
    now_iso as _now_iso,
    post_with_retry,
    print_request,
    print_total,
    refused_leg,
    save_overlay,
)

DEFAULT_SIZES = "32000,128000,256000,500000"

# A size is usable only when every trial recalled at least this fraction.
RECALL_PASS = 0.9

# Assignable, so tests need no real network wait.
_sleep = time.sleep


# ---------------------------------------------------------------------------
# The haystack and the request body.
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4
NEEDLE_COUNT = 5
NEEDLE_NAMES = ("Mira", "Otto", "Pia", "Quinn", "Ravi")
NEEDLE_TEMPLATE = "The access code for %s is %s."

FILLER_WORDS = (
    "the", "quiet", "harbor", "rolls", "softly", "under", "pale", "morning",
    "light", "while", "small", "boats", "drift", "between", "wooden", "docks",
    "and", "gulls", "circle", "above", "old", "lighthouse", "whose", "lamp",
    "still", "shines", "each", "evening", "over", "narrow", "streets", "where",
    "shopkeepers", "sweep", "dust", "from", "their", "steps", "before",
    "opening", "shutters", "to", "a", "breeze", "that", "carries", "salt",
    "in", "from", "open", "water",
)

RECALL_INSTRUCTIONS = (
    "The document below contains five access-code lines of the form "
    "\"The access code for <name> is <6-digit code>.\". "
    "Read the entire document and reply with ONLY a JSON object that maps "
    "every name to its 6-digit access code, for example "
    "{\"Ann\": \"654321\"}.\n\n"
    "Document:\n"
)


def _filler(rng, chars):
    """Seeded filler sentences totalling about `chars` characters."""
    if chars <= 0:
        return ""
    sentences = []
    remaining = chars
    while remaining > 0:
        words = [rng.choice(FILLER_WORDS) for _ in range(rng.randint(5, 11))]
        sentence = " ".join(words).capitalize() + "."
        sentences.append(sentence)
        remaining -= len(sentence) + 1
    return "\n".join(sentences) + "\n"


def build_haystack(size_tokens, seed="0"):
    """``(haystack, needles)``: a deterministic ~size_tokens-token haystack.

    haystack is filler sentences (~CHARS_PER_TOKEN chars per token) with
    NEEDLE_COUNT needles at evenly spread depths; needles is the ground
    truth for scoring, ``[(name, code), ...]``. The same seed always yields
    the same haystack, so a verdict is reproducible and a test can know the
    codes without touching the network.
    """
    rng = random.Random(seed)
    codes = []
    while len(codes) < NEEDLE_COUNT:
        code = "%06d" % rng.randrange(1000000)
        if code not in codes:
            codes.append(code)
    needles = list(zip(NEEDLE_NAMES, codes))
    needle_lines = [NEEDLE_TEMPLATE % (name, code) for name, code in needles]
    target_chars = size_tokens * CHARS_PER_TOKEN
    filler_budget = max(0, target_chars - sum(len(line) + 1 for line in needle_lines))
    per_chunk = filler_budget // (NEEDLE_COUNT + 1)
    parts = []
    for i, line in enumerate(needle_lines):
        parts.append(_filler(rng, per_chunk))
        parts.append(line + "\n")
    parts.append(_filler(rng, per_chunk))
    return "".join(parts), needles


def recall_body(leg, haystack, max_tokens=2048):
    """The trial request: one user message, the haystack plus the question."""
    return {
        "model": leg,
        "messages": [{"role": "user", "content": RECALL_INSTRUCTIONS + haystack}],
        "max_tokens": max_tokens,
    }


# ---------------------------------------------------------------------------
# Scoring: the reply against the ground truth.
# ---------------------------------------------------------------------------

def _extract_json_object(content):
    """The JSON object in a model reply, or None when there is none.

    Accepts a bare object, a ```-fenced block, or an object embedded in
    surrounding prose (first '{' to last '}').
    """
    text = (content or "").strip()
    parsed = None
    try:
        parsed = json.loads(text)
    except ValueError:
        pass
    if isinstance(parsed, dict):
        return parsed
    if text.startswith("```"):
        body = text.split("\n", 1)[1] if "\n" in text else ""
        body = body.rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return None


def score_recall(content, needles):
    """``(recall, note)`` for a model reply against the ground truth.

    recall is the fraction of needles returned exactly: the reply's value
    for a needle's name, as a string, equals its code. A reply that does
    not parse as a JSON object scores 0.0, never an exception.
    """
    parsed = _extract_json_object(content)
    if parsed is None:
        return 0.0, "reply did not parse as a JSON object: %r" % ((content or "")[:120],)
    matched = [name for name, code in needles
               if str(parsed.get(name, "")).strip() == code]
    recall = len(matched) / float(len(needles)) if needles else 0.0
    note = "%d/%d needles returned exactly" % (len(matched), len(needles))
    missing = [name for name, _ in needles if name not in matched]
    if missing:
        note += "; missing or wrong: %s" % ", ".join(missing)
    return recall, note


# ---------------------------------------------------------------------------
# Trials, the size ladder, the verdict.
# ---------------------------------------------------------------------------

def run_trial(leg, size, post, seed="0", max_tokens=2048):
    """One trial at `size` tokens: haystack, request, score.

    Returns ``{"recall", "status", "prompt_tokens", "completion_tokens",
    "note"}``. recall is None exactly when the trial measured nothing (a
    credential or transport status): never a failure, never a pass.
    """
    haystack, needles = build_haystack(size, seed=seed)
    status, parsed, error = post_with_retry(
        post, recall_body(leg, haystack, max_tokens), _sleep)
    if isinstance(parsed, dict):
        usage = parsed.get("usage") or {}
    else:
        usage = {}
    result = {"status": status, "prompt_tokens": usage.get("prompt_tokens"),
              "completion_tokens": usage.get("completion_tokens")}
    if status != 200 or parsed is None:
        result["recall"] = None if is_no_verdict_status(status) else 0.0
        result["note"] = error or "malformed or missing response body"
        return result
    try:
        content = parsed["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        result.update({"recall": 0.0, "note": "malformed response: no choices[0].message"})
        return result
    recall, note = score_recall(content, needles)
    result.update({"recall": recall, "note": note})
    return result


def classify_ladder(outcomes):
    """The verdict for one model's size ladder, in probe order.

    Returns ``{"tokens": T or None, "detail": {size: recall or None},
    "no_verdict": bool}``. tokens is the largest size of the *leading* run
    of sizes where every measured trial had recall >= RECALL_PASS - usable
    context is an "up to" bound, so a failing size ends it even when the
    caller fed sizes beyond the failure. tokens None means no size passed
    (the smallest failed: the caller writes only a detail). no_verdict is
    True when nothing at all was measured - every trial a credential or
    transport status - and the caller keeps whatever the overlay had.
    """
    detail = {}
    for outcome in outcomes:
        measured = [r for r in outcome["recalls"] if r is not None]
        detail[str(outcome["size"])] = min(measured) if measured else None
    no_verdict = bool(outcomes) and all(
        all(r is None for r in o["recalls"]) for o in outcomes)
    tokens = None
    for outcome in outcomes:
        measured = [r for r in outcome["recalls"] if r is not None]
        if not measured or min(measured) < RECALL_PASS:
            break
        tokens = outcome["size"]
    return {"tokens": tokens, "detail": detail, "no_verdict": no_verdict}


def probe_model(leg, sizes, post, trials, max_tokens=2048):
    """Run one leg's ascending size ladder.

    Stops after the first size that did not pass: a model that lost needles
    at N tokens will not gain them at 2N, and free-leg quota is not worth
    proving that. Returns ``(outcomes, requests)`` - one
    ``{"size", "recalls", "statuses"}`` per probed size in probe order for
    classify_ladder, and the per-request token log (``{"leg", "size",
    "prompt_tokens", "completion_tokens"}`` in call order).
    """
    outcomes = []
    requests = []
    for size in sizes:
        recalls = []
        statuses = []
        for trial in range(trials):
            result = run_trial(leg, size, post, seed="%d:%d" % (size, trial),
                               max_tokens=max_tokens)
            recalls.append(result["recall"])
            statuses.append(result["status"])
            requests.append({"leg": leg, "size": size,
                             "prompt_tokens": result["prompt_tokens"],
                             "completion_tokens": result["completion_tokens"]})
        outcomes.append({"size": size, "recalls": recalls, "statuses": statuses})
        measured = [r for r in recalls if r is not None]
        if not measured or min(measured) < RECALL_PASS:
            break
    return outcomes, requests


# ---------------------------------------------------------------------------
# Overlay: read-modify-write, keyed by model id (load/save in probe_common).
# ---------------------------------------------------------------------------

def record_verdict(overlay, model_id, tokens, detail, at):
    """Merge one model's verdict into `overlay` (read-modify-write), in place.

    tokens None (the smallest size failed) still writes ``context_usable``
    but without a ``tokens`` key, so usable_context() falls back to the
    registry default while the detail keeps the measurement. Every other
    key already in `overlay` (other models, other fields on this model) is
    kept as-is.
    """
    entry = overlay.setdefault("models", {}).setdefault(model_id, {})
    measured = {"source": "probe", "at": at, "detail": detail}
    if tokens is not None:
        measured["tokens"] = tokens
    entry["context_usable"] = measured
    return overlay


def record_no_verdict(overlay, model_id, detail, at):
    """Record why no verdict was reached; never touch ``context_usable``."""
    entry = overlay.setdefault("models", {}).setdefault(model_id, {})
    entry["context_usable_last_error"] = {"detail": detail, "at": at}
    return overlay


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_sizes(text):
    """Sorted, de-duplicated ascending token sizes from a comma-separated string.

    Raises ValueError naming the bad part; never returns an empty list.
    """
    parts = [part.strip() for part in text.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError("no sizes in %r" % (text,))
    sizes = []
    for part in parts:
        try:
            size = int(part)
        except ValueError:
            raise ValueError("size %r is not an integer" % part)
        if size < 1:
            raise ValueError("size %d is not positive" % size)
        sizes.append(size)
    return sorted(set(sizes))


def _advertised_tokens(registry, model_id):
    # catalog/ai-registry.json writes context_advertised as a plain int
    # (context_usable is the {tokens, source} object); accept both shapes.
    value = (registry.get("models", {}).get(model_id) or {}).get("context_advertised")
    if isinstance(value, dict):
        value = value.get("tokens")
    return value if isinstance(value, int) else None


def _sizes_for_model(sizes, registry, model_id):
    """The probe sizes that fit the model's advertised context (all of them
    when the registry does not advertise one)."""
    advertised = _advertised_tokens(registry, model_id)
    if advertised is None:
        return list(sizes)
    return [size for size in sizes if size <= advertised]


def _format_detail(detail):
    """A compact, size-ordered rendering of the per-size recall detail."""
    items = sorted(detail.items(), key=lambda kv: int(kv[0]))
    return " ".join("%s:%s" % (size, recall) for size, recall in items)


def _print_plan(todo, registry, sizes, trials):
    """The --dry-run plan: legs, skip reasons, sizes, estimated prompt tokens."""
    probeable = 0
    est_total = 0
    for leg, skip in todo:
        if skip:
            print("%s\tskip\t%s" % (leg, skip))
            continue
        model_id = resolve_leg(leg, registry)[1]
        model_sizes = _sizes_for_model(sizes, registry, model_id)
        if not model_sizes:
            print("%s\tskip\tno probe size fits context_advertised %s"
                  % (leg, _advertised_tokens(registry, model_id)))
            continue
        estimate = sum(model_sizes) * trials
        probeable += 1
        est_total += estimate
        print("%s\tprobe\tsizes=%s\ttrials=%d\tprompt_tokens_est=%d"
              % (leg, ",".join(str(size) for size in model_sizes), trials, estimate))
    print("plan\tlegs=%d\tprompt_tokens_est=%d" % (probeable, est_total))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe routing legs for multi-needle recall; write the overlay.")
    ap.add_argument("--leg", action="append", default=[],
                    help="only this leg (repeatable); default: every distinct leg")
    ap.add_argument("--route", action="append", default=[],
                    help="only legs of this route (repeatable)")
    ap.add_argument("--trials", type=int, default=2, help="trials per size (default 2)")
    ap.add_argument("--sizes", default=DEFAULT_SIZES,
                    help="comma-separated token sizes to probe (default %s)" % DEFAULT_SIZES)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (legs, skip reasons, sizes, estimated "
                         "prompt tokens); make no request")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--overlay", default=DEFAULT_OVERLAY)
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    args = ap.parse_args(argv)

    if args.trials < 1:
        print("probe-recall: --trials must be at least 1", file=sys.stderr)
        return 2
    try:
        sizes = parse_sizes(args.sizes)
    except ValueError as exc:
        print("probe-recall: --sizes: %s" % exc, file=sys.stderr)
        return 2

    try:
        with open(args.registry, encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError) as exc:
        print("probe-recall: cannot read registry %s: %s" % (args.registry, exc),
              file=sys.stderr)
        return 2

    refusal = refused_leg(registry, args.leg)
    if refusal:
        leg, tier = refusal
        print("probe-recall: refusing %s (provider tier %r): probes run on free "
              "legs only (spec section 10, D18); no request made" % (leg, tier),
              file=sys.stderr)
        return 4

    todo = legs_to_probe(registry, only_legs=args.leg, only_routes=args.route)

    if args.dry_run:
        _print_plan(todo, registry, sizes, args.trials)
        return 0

    agent = _load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-recall: no OmniRoute client key (export AUTOOS_OMNIROUTE_KEY or "
              "add 'omniroute:' to configuration/api-keys.yml)", file=sys.stderr)
        return 3
    if not gateway_up(args.gateway):
        print("probe-recall: gateway not reachable at %s" % args.gateway, file=sys.stderr)
        return 3

    post = make_post(args.gateway, key)
    overlay = load_overlay(args.overlay)
    total_prompt = 0
    total_completion = 0
    for leg, skip in todo:
        if skip:
            print("%s\tskip\t%s" % (leg, skip))
            continue
        model_id = resolve_leg(leg, registry)[1]
        model_sizes = _sizes_for_model(sizes, registry, model_id)
        if not model_sizes:
            print("%s\tskip\tno probe size fits context_advertised %s"
                  % (leg, _advertised_tokens(registry, model_id)))
            continue
        outcomes, requests = probe_model(leg, model_sizes, post, args.trials)
        for request in requests:
            print_request(leg, request["size"], request["prompt_tokens"],
                          request["completion_tokens"])
            if request["prompt_tokens"] is not None:
                total_prompt += request["prompt_tokens"]
            if request["completion_tokens"] is not None:
                total_completion += request["completion_tokens"]
        verdict = classify_ladder(outcomes)
        at = _now_iso()
        if verdict["no_verdict"]:
            statuses = sorted({str(status) for o in outcomes for status in o["statuses"]})
            detail = ("no verdict: only non-200 statuses (%s): keeping the previous value"
                      % ", ".join(statuses))
            record_no_verdict(overlay, model_id, detail, at)
            print("%s\tno-verdict\t-\t%s" % (leg, detail))
        else:
            record_verdict(overlay, model_id, verdict["tokens"], verdict["detail"], at)
            if verdict["tokens"] is None:
                print("%s\tverdict\t-\tsmallest size failed; registry default kept" % leg)
            else:
                print("%s\tverdict\t%d\t%s" % (leg, verdict["tokens"],
                                               _format_detail(verdict["detail"])))
    save_overlay(args.overlay, overlay)
    print_total(total_prompt, total_completion)
    return 0


if __name__ == "__main__":
    sys.exit(main())
