#!/usr/bin/env python3
"""Read-only free-provider probe for the 2026-09-30 free-wiring lane.

Sends each candidate leg through the live OmniRoute gateway
(http://127.0.0.1:20128/v1/chat/completions) exactly the way the gateway's own
OpenAI-compatible surface expects: the JSON "model" field is the plain leg
string ("<provider-prefix>/<model>"), never an "omniroute/" prefix.

Per candidate it runs two calls:
  - ack   : tiny chat completion, max_tokens=16
  - tool  : one offered get_weather tool, max_tokens=1024, pass = exactly one
            tool_call named get_weather whose arguments parse with a city
            containing "paris" (the same shape tools/probe-toolcalls.py uses).

The client key is read in-memory from AUTOOS_OMNIROUTE_KEY or the checkout's
configuration/api-keys.yml (tools/autoos-agent.py:client_key) and is never
printed, logged or written anywhere.

Usage:
    python tools/probe-free.py --list
    python tools/probe-free.py --model groq/openai/gpt-oss-120b [...]
    python tools/probe-free.py            # probes the built-in candidate list
"""
from __future__ import annotations

import argparse
import json
import os
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


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("%s\t%s" % (now_iso(), msg), flush=True)


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


def ack_body(leg):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "Reply with exactly: ACK_OK"}],
        "max_tokens": 16,
    }


def tool_body(leg):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": 1024,
    }


def post(key, body, timeout=180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        CHAT, data=data,
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


def post_retry(key, body):
    delays = list(RETRY_DELAYS_S)
    while True:
        result = post(key, body)
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
    """(ok, note)."""
    if not isinstance(parsed, dict):
        return False, "no parsed body"
    try:
        content = parsed["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return False, "malformed response"
    if "ACK" in content.upper():
        return True, "ok"
    return False, "no ACK in content: %r" % content[:80]


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


def probe(key, leg):
    rec = {"leg": leg, "at": now_iso()}
    ack = post_retry(key, ack_body(leg))
    rec["ack_status"] = ack["status"]
    rec["ack_ms"] = ack["ms"]
    rec["ack_error"] = ack["err"]
    if ack["status"] == 200 and ack["parsed"] is not None:
        ok, note = ack_result(ack["parsed"])
        rec["ack"] = "ok" if ok else "fail"
        rec["ack_note"] = note
        rec["ack_usage"] = usage_of(ack["parsed"])
        rec["ack_served"] = served_of(ack["parsed"])
    else:
        rec["ack"] = "fail"
        rec["ack_note"] = ack["err"]
        rec["ack_body"] = ack.get("body", "")[:400]

    tool = post_retry(key, tool_body(leg))
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
    return rec


def list_models(key):
    req = urllib.request.Request(MODELS, headers={"Authorization": "Bearer " + key})
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--filter", default="")
    args = ap.parse_args(argv)

    agent = load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-free: no client key", file=sys.stderr)
        return 3

    if args.list:
        ids = list_models(key)
        needle = args.filter.lower()
        for i in sorted(ids):
            if not needle or needle in i.lower():
                print(i)
        print("TOTAL %d" % len(ids))
        return 0

    legs = args.model or CANDIDATES
    results = []
    for leg in legs:
        rec = probe(key, leg)
        results.append(rec)
        print(json.dumps(rec), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
