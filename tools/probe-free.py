#!/usr/bin/env python3
"""Read-only free-provider probe for the 2026-09-30 free-wiring lane.

Sends each candidate leg through the live OmniRoute gateway
(http://127.0.0.1:20128/v1/chat/completions) exactly the way the gateway's own
OpenAI-compatible surface expects: the JSON "model" field is the plain leg
string ("<provider-prefix>/<model>"), never an "omniroute/" prefix.

Per candidate it runs two calls:
  - ack   : tiny chat completion, max_tokens from --ack-max-tokens (default 16)
  - tool  : one offered get_weather tool, max_tokens=1024, pass = exactly one
            tool_call named get_weather whose arguments parse with a city
            containing "paris" (the same shape tools/probe-toolcalls.py uses).

``--cache`` adds the prompt-cache probe (spec D-657 AO-PROBE-D657): two calls
that share one deterministic >= 4k-token system prefix and differ only in the
short user turn, so a hit proves prompt-prefix caching, not a whole-response
cache. The second call's usage decides: ``prompt_cache`` is true when a
cached-token field reports > 0, false when one reports 0, and unknown when the
call failed or names no such field - measured nothing, never "does not cache".

``--tsv PATH`` writes one row per leg, tab-separated columns
``model, gateway, ack, tool, cache, ack_ms, served, error``, flushed per leg so
an interrupted run keeps what it measured.

The client key is read in-memory from AUTOOS_OMNIROUTE_KEY or the checkout's
configuration/api-keys.yml (tools/autoos-agent.py:client_key) and is never
printed, logged or written anywhere.

Usage:
    python tools/probe-free.py --list
    python tools/probe-free.py --model groq/openai/gpt-oss-120b [...]
    python tools/probe-free.py --legs-file legs.txt --cache \
        --tsv logs/probe-2026-10-08.tsv --gateway-label central
    python tools/probe-free.py            # probes the built-in candidate list
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
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import load_agent_module  # noqa: E402

GATEWAY = "http://127.0.0.1:20128"
CHAT = GATEWAY + "/v1/chat/completions"
MODELS = GATEWAY + "/v1/models"

RETRY_STATUSES = (429, 503, 504)
RETRY_DELAYS_S = (60, 120)

ACK_MAX_TOKENS = 16
# A reply counts as an ack only with one of these as a word of its own.
ACK_TOKENS = frozenset(("ack", "ack_ok"))

# The cache probe's prefix: ~4 characters per token, so 4096 tokens needs
# 16384 characters - and every gateway with a documented cache minimum
# (OpenAI 1024, DeepSeek 256) is above it.
CACHE_MIN_PREFIX_TOKENS = 4096
# Two turns that differ: an identical request could be answered from a
# whole-response cache, which says nothing about prompt-prefix caching.
CACHE_TURN_FIRST = "Reply with exactly: CACHE_OK"
CACHE_TURN_SECOND = "Reply with exactly: CACHE_OK2"
TSV_HEADER = "model\tgateway\tack\ttool\tcache\tack_ms\tserved\terror"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("%s\t%s" % (now_iso(), msg), flush=True)


def chat_url(gateway):
    return gateway.rstrip("/") + "/v1/chat/completions"


def models_url(gateway):
    return gateway.rstrip("/") + "/v1/models"


def weather_tool():
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


def ack_body(leg, max_tokens=ACK_MAX_TOKENS):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "Reply with exactly: ACK_OK"}],
        "max_tokens": max_tokens,
    }


def tool_body(leg):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": 1024,
    }


def cache_prefix(min_tokens=CACHE_MIN_PREFIX_TOKENS):
    """Deterministic filler text of at least `min_tokens` tokens.

    Byte-identical on every call and in every run: a cached prefix is looked
    up by its content, so anything non-deterministic here measures nothing.
    """
    unit = "AutoOS prompt-cache probe line - deterministic filler, no meaning, answer nothing."
    text = ""
    number = 0
    while len(text) < min_tokens * 4:
        text += "%d %s\n" % (number, unit)
        number += 1
    return text


def cache_body(leg, prefix, turn, max_tokens=ACK_MAX_TOKENS):
    return {
        "model": leg,
        "messages": [{"role": "system", "content": prefix},
                     {"role": "user", "content": turn}],
        "max_tokens": max_tokens,
    }


def post(key, body, url=CHAT, timeout=180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        ms = int((time.monotonic() - started) * 1000)
        # read a bounded, scrubbed slice only to classify the failure token
        try:
            body_text = exc.read(600).decode("utf-8", "replace")
        except Exception:
            body_text = ""
        return {"status": exc.code, "ms": ms, "parsed": None,
                "err": "HTTP %d" % exc.code, "body": body_text}
    except Exception as exc:
        ms = int((time.monotonic() - started) * 1000)
        return {"status": "ERR", "ms": ms, "parsed": None,
                "err": "transport: %s" % type(exc).__name__, "body": ""}
    ms = int((time.monotonic() - started) * 1000)
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"status": status, "ms": ms, "parsed": None,
                "err": "invalid JSON body", "body": ""}
    return {"status": status, "ms": ms, "parsed": parsed, "err": None, "body": ""}


def post_retry(key, body, url=CHAT):
    delays = list(RETRY_DELAYS_S)
    while True:
        result = post(key, body, url)
        if result["status"] not in RETRY_STATUSES or not delays:
            return result
        delay = delays.pop(0)
        log("backoff %ds status=%s" % (delay, result["status"]))
        time.sleep(delay)


def usage_of(parsed):
    if not isinstance(parsed, dict):
        return None
    return parsed.get("usage")


def ack_result(parsed):
    """(verdict, note) - verdict is "ok", "fail" or "empty".

    The reply must carry the asked-for token as a word of its own: the old
    "ACK" in content.upper() scored "ACKNOWLEDGED" and "packaged" as an ack,
    which then reads as a passing leg in the TSV. "empty" is kept apart from
    "fail" (AO-PROBE-D657): a reasoning leg answers 200 with no content once
    the budget is small (measured: vertex gemini at max_tokens 16), and that
    is our budget, not the leg refusing.
    """
    if not isinstance(parsed, dict):
        return "fail", "no parsed body"
    try:
        message = parsed["choices"][0]["message"]
        content = message.get("content") or ""
        content = content if isinstance(content, str) else str(content)
    except (AttributeError, KeyError, IndexError, TypeError):
        return "fail", "malformed response"
    if not content.strip():
        return "empty", "200 with empty content (reasoning budget too small?)"
    if set(re.findall(r"[a-z0-9_]+", content.lower())) & ACK_TOKENS:
        return "ok", "ok"
    return "fail", "no ACK token in content: %r" % content[:80]


def tool_result(parsed):
    if not isinstance(parsed, dict):
        return False, 0, "no parsed body"
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False, 0, "malformed response"
    calls = message.get("tool_calls") or []
    if len(calls) != 1:
        return False, len(calls), "expected exactly one tool_call, got %d" % len(calls)
    call = calls[0]
    try:
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
    except (KeyError, TypeError, ValueError):
        return False, 1, "tool_call unparseable"
    if name != "get_weather":
        return False, 1, "tool_call named %r" % name
    city = str((args or {}).get("city", ""))
    if "paris" not in city.lower():
        return False, 1, "city %r lacks 'paris'" % city
    return True, 1, "ok"


def served_of(parsed):
    if isinstance(parsed, dict):
        return parsed.get("model")
    return None


# Each gateway spells "tokens served from cache" differently and all three
# shapes are read; the largest present is the verdict's number - they describe
# one measurement, they do not add up.
CACHE_USAGE_FIELDS = (
    ("prompt_tokens_details", "cached_tokens"),
    (None, "prompt_cache_hit_tokens"),
    (None, "cache_read_input_tokens"),
)


def cached_tokens_of(usage):
    """Cached-token count a usage block reports, or None when it names none."""
    if not isinstance(usage, dict):
        return None
    found = None
    for parent, key in CACHE_USAGE_FIELDS:
        block = usage.get(parent) if parent else usage
        value = block.get(key) if isinstance(block, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            found = value if found is None else max(found, value)
    return found


def cache_result(parsed):
    """(verdict, note) for the second call of a same-prefix pair."""
    usage = usage_of(parsed)
    if not isinstance(usage, dict):
        return "unknown", "no usage field"
    cached = cached_tokens_of(usage)
    if cached is None:
        return "unknown", "usage names no cached-token field"
    if cached > 0:
        return "true", "cached tokens %d" % cached
    return "false", "cached tokens 0"


def probe_cache(key, leg, url, max_tokens):
    """The two-call prompt-cache probe's record fields - the second call decides."""
    prefix = cache_prefix()
    first = post_retry(key, cache_body(leg, prefix, CACHE_TURN_FIRST, max_tokens), url)
    second = post_retry(key, cache_body(leg, prefix, CACHE_TURN_SECOND, max_tokens), url)
    rec = {"cache_first_status": first["status"], "cache_status": second["status"],
           "cache_ms": second["ms"], "cache_error": second["err"],
           "cache_served": served_of(second["parsed"])}
    if second["status"] != 200 or second["parsed"] is None:
        rec["prompt_cache"] = "unknown"
        rec["cache_note"] = second["err"] or "HTTP %s" % second["status"]
        return rec
    verdict, note = cache_result(second["parsed"])
    rec["prompt_cache"] = verdict
    rec["cache_note"] = note
    rec["cached_tokens"] = cached_tokens_of(usage_of(second["parsed"]))
    rec["cache_usage"] = usage_of(second["parsed"])
    return rec


def probe(key, leg, url=CHAT, ack_max_tokens=ACK_MAX_TOKENS, do_cache=False):
    rec = {"leg": leg, "at": now_iso()}
    ack = post_retry(key, ack_body(leg, ack_max_tokens), url)
    rec["ack_status"] = ack["status"]
    rec["ack_ms"] = ack["ms"]
    rec["ack_error"] = ack["err"]
    if ack["status"] == 200 and ack["parsed"] is not None:
        verdict, note = ack_result(ack["parsed"])
        rec["ack"] = verdict
        rec["ack_note"] = note
        rec["ack_usage"] = usage_of(ack["parsed"])
        rec["ack_served"] = served_of(ack["parsed"])
    else:
        rec["ack"] = "fail"
        rec["ack_note"] = ack["err"]
        rec["ack_body"] = ack.get("body", "")[:400]

    tool = post_retry(key, tool_body(leg), url)
    rec["tool_status"] = tool["status"]
    rec["tool_ms"] = tool["ms"]
    rec["tool_error"] = tool["err"]
    if tool["status"] == 200 and tool["parsed"] is not None:
        ok, calls, note = tool_result(tool["parsed"])
        rec["tool"] = "ok" if ok else "fail"
        rec["tool_calls"] = calls
        rec["tool_note"] = note
        rec["tool_usage"] = usage_of(tool["parsed"])
        rec["tool_served"] = served_of(tool["parsed"])
    else:
        rec["tool"] = "fail"
        rec["tool_calls"] = 0
        rec["tool_note"] = tool["err"]
        rec["tool_body"] = tool.get("body", "")[:400]

    if do_cache:
        rec.update(probe_cache(key, leg, url, ack_max_tokens))
    return rec


def tsv_cell(value):
    """One cell: never a tab or newline, so one leg stays one line."""
    return str(value if value not in (None, "") else "-").replace("\t", " ").replace("\n", " ")


def tsv_row(rec, gateway_label):
    error = next((rec[k] for k in ("ack_error", "tool_error", "cache_error")
                  if rec.get(k)), "")
    return "\t".join([tsv_cell(rec["leg"]), tsv_cell(gateway_label),
                      tsv_cell(rec.get("ack")), tsv_cell(rec.get("tool")),
                      tsv_cell(rec.get("prompt_cache")), tsv_cell(rec.get("ack_ms")),
                      tsv_cell(rec.get("ack_served")), tsv_cell(error)])


def list_models(key, url=MODELS):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ids = []
    for row in data.get("data", []):
        ids.append(row.get("id"))
    return ids


CANDIDATES = [
    # groq (free)
    "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-20b",
    "groq/qwen/qwen3.8-27b",
    # cerebras (expected: not in live catalog)
    "cerebras/gpt-oss-120b",
    "cerebras/qwen-3.8-27b",
    # cloudflare-ai (free)
    "cloudflare-ai/@cf/moonshotai/kimi-k2.6",
    "cloudflare-ai/@cf/zai-org/glm-4.7-flash",
    "cloudflare-ai/@cf/qwen/qwq-32b",
    "cloudflare-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    # sambanova (free)
    "sambanova/gpt-oss-120b",
    "sambanova/DeepSeek-V3.2",
    "sambanova/MiniMax-M2.7",
    # huggingface (free)
    "huggingface/zai-org/GLM-5.2",
    "huggingface/deepseek-ai/DeepSeek-V4-Flash-0731",
    "huggingface/Qwen/Qwen3.8-27B",
    # together (credit)
    "together/zai-org/GLM-5.2",
    "together/Qwen/Qwen3.8-Flash",
    # novita (free)
    "novita/zai-org/glm-5.2",
    "novita/deepseek/deepseek-v4.1-flash",
    # navy / navyai (operator says no free models - recheck)
    "navy/glm-5.2",
    "navy/deepseek-v4.1-flash",
    "navy/gpt-oss-120b",
    # bazaarlink (free)
    "bzl/glm-5.2",
    "bzl/deepseek-v4.1-flash",
    "bzl/deepseek/deepseek-v4-flash-0731free:free",
    # bluesminds (operator says no free models - recheck)
    "bm/glm-4.7",
    "bm/deepseek-chat",
    "bm/gpt-oss-20b",
    # agentrouter (free)
    "agentrouter/deepseek-v4-flash",
    # cheaperinference (wallet 402 - recheck)
    "cheaperinference/glm-5.2",
    "cheaperinference/kimi-k3",
    "cheaperinference/deepseek-v4-flash",
    # openrouter :free ids
    "openrouter/z-ai/glm-5.2:free",
    "openrouter/qwen/qwen3.8-27b:free",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/google/gemma-4-31b-it:free",
    "openrouter/thinkingmachines/inkling:free",
    "openrouter/cohere/north-mini-code:free",
    "openrouter/poolside/laguna-s-2.1:free",
    # exclusions, probed to capture the verbatim refusal
    "opencode-zen/deepseek-v4.1-flash",
    "opencode/deepseek-v4.1-flash",
    "antigravity/claude-opus-4-6-thinking",
    "devin/devin",
]


def legs_from_file(path):
    """Legs of a text file: one per line, blank lines and `#` comments ignored."""
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh
                if line.strip() and not line.lstrip().startswith("#")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--filter", default="")
    ap.add_argument("--gateway", default=GATEWAY)
    ap.add_argument("--gateway-label", default="central",
                    help="the name written in the TSV's gateway column")
    ap.add_argument("--legs-file", default="", help="file of one leg per line")
    ap.add_argument("--ack-max-tokens", type=int, default=ACK_MAX_TOKENS,
                    help="budget of the ack call and the cache probe's calls")
    ap.add_argument("--cache", action="store_true",
                    help="run the two-call prompt-cache probe per leg")
    ap.add_argument("--tsv", default="", help="write the results as TSV to PATH")
    args = ap.parse_args(argv)

    agent = load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-free: no client key", file=sys.stderr)
        return 3

    chat = chat_url(args.gateway)
    if args.list:
        ids = list_models(key, models_url(args.gateway))
        needle = args.filter.lower()
        for i in sorted(ids):
            if not needle or needle in i.lower():
                print(i)
        print("TOTAL %d" % len(ids))
        return 0

    legs = list(args.model)
    if args.legs_file:
        legs.extend(legs_from_file(args.legs_file))
    legs = legs or CANDIDATES

    tsv = None
    if args.tsv:
        tsv = open(args.tsv, "w", encoding="utf-8")
        tsv.write(TSV_HEADER + "\n")
        tsv.flush()
    try:
        for leg in legs:
            rec = probe(key, leg, chat, args.ack_max_tokens, args.cache)
            print(json.dumps(rec), flush=True)
            if tsv is not None:
                tsv.write(tsv_row(rec, args.gateway_label) + "\n")
                tsv.flush()
    finally:
        if tsv is not None:
            tsv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
