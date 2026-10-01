#!/usr/bin/env python3
"""Test variants for the reasoning_text 400 error on deepseek/deepseek-flash.

Tests four scenarios the main probe doesn't cover:
  1. --variant strip   — strip ALL reasoning fields from the round-trip
     assistant message (simulates a client that drops reasoning to save
     tokens). If DeepSeek 400s, this confirms the error scenario.
  2. --variant strip-content — strip reasoning AND content, keeping only
     tool_calls (simulates a minimal client).
  3. --combo — use the combo name deepseek-v4.1-flash as the model instead
     of the direct leg deepseek/deepseek-flash.
  4. --responses — use the /v1/responses endpoint instead of
     /v1/chat/completions.

Safe to log: status, key names, reasoning field lengths, tool_call names.
Never logs the key, body text, or reasoning content.

Usage:
    python3 tools/probe-reasoning-edge.py --variant strip
    python3 tools/probe-reasoning-edge.py --variant strip --combo
    python3 tools/probe-reasoning-edge.py --responses
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import (  # noqa: E402
    DEFAULT_GATEWAY,
    gateway_up,
    load_agent_module,
)

REASONING_KEYS = ("reasoning_content", "reasoning_text", "reasoning",
                  "thinking", "thought")
REASONING_MENTION = re.compile(r"(?i)reasoning")
TOOLS_MENTION = re.compile(r"(?i)\b(tool|function)s?\b")
COMBO_NAME = "deepseek-v4.1-flash"
RESPONSES_PATH = "/v1/responses"


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


def single_call_body(leg, max_tokens=512):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def round_trip_body(leg, assistant_message, call_id, max_tokens=512):
    return {
        "model": leg,
        "messages": [
            {"role": "user", "content": "What is the weather in Paris?"},
            assistant_message,
            {"role": "tool", "tool_call_id": call_id,
             "content": json.dumps({"temp_c": 18})},
        ],
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def responses_single_call_body(leg, max_tokens=512):
    return {
        "model": leg,
        "input": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def responses_round_trip_body(leg, output_items, call_id, max_tokens=512):
    """Responses API round trip: include previous output items in input."""
    input_items = [{"role": "user", "content": "What is the weather in Paris?"}]
    # Include the previous output items (reasoning + message + function_call)
    for item in output_items:
        input_items.append(item)
    # Add the function call output
    input_items.append({
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"temp_c": 18}),
    })
    return {
        "model": leg,
        "input": input_items,
        "tools": [weather_tool()],
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }


def post(url, key, body, timeout=180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        try:
            snippet = exc.read(500).decode("utf-8", "replace")
        except Exception:
            snippet = ""
        if REASONING_MENTION.search(snippet):
            return exc.code, None, "HTTP %d (mentions reasoning)" % exc.code
        if TOOLS_MENTION.search(snippet):
            return exc.code, None, "HTTP %d (mentions tool/function)" % exc.code
        return exc.code, None, "HTTP %d" % exc.code
    except Exception as exc:
        return "ERR", None, "transport error: %s" % type(exc).__name__
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), None
    except ValueError:
        return status, None, "invalid JSON body"


def describe_message(message):
    keys = sorted(message.keys())
    parts = ["keys=%s" % (",".join(keys),)]
    for k in REASONING_KEYS:
        if k in message and message[k] is not None:
            try:
                parts.append("%s_len=%d" % (k, len(str(message[k]))))
            except Exception:
                parts.append("%s_len=?" % k)
    calls = message.get("tool_calls") or []
    names = []
    for c in calls:
        try:
            names.append(c["function"]["name"])
        except (KeyError, TypeError):
            names.append("?")
    parts.append("tool_calls=%d[%s]" % (len(calls), ",".join(names)))
    content = message.get("content")
    parts.append("content_len=%s" % (len(str(content)) if content is not None else "null"))
    return " ".join(parts)


def strip_reasoning(message):
    """Remove ALL reasoning-ish fields from the message."""
    msg = json.loads(json.dumps(message))
    for k in list(msg.keys()):
        if k in REASONING_KEYS or "reason" in k.lower() or "think" in k.lower():
            del msg[k]
    return msg


def strip_reasoning_and_content(message):
    """Remove reasoning AND content, keeping only role + tool_calls."""
    msg = strip_reasoning(message)
    msg.pop("content", None)
    return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Edge-case probes for the reasoning_text 400 error.")
    ap.add_argument("--leg", default="deepseek/deepseek-flash")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--variant", default="strip",
                    choices=("strip", "strip-content"))
    ap.add_argument("--combo", action="store_true",
                    help="Use combo name deepseek-v4.1-flash as model")
    ap.add_argument("--responses", action="store_true",
                    help="Use /v1/responses endpoint instead of chat/completions")
    ap.add_argument("--max-tokens", type=int, default=512)
    args = ap.parse_args(argv)

    agent = load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-edge: no OmniRoute client key", file=sys.stderr)
        return 3
    if not gateway_up(args.gateway):
        print("probe-edge: gateway not reachable", file=sys.stderr)
        return 3

    model = COMBO_NAME if args.combo else args.leg
    endpoint = args.gateway
    if args.responses:
        base = args.gateway.rsplit("/v1/", 1)[0]
        endpoint = base + RESPONSES_PATH

    label = "responses" if args.responses else "chat"
    if args.combo:
        label += "+combo"

    if args.responses:
        # Responses API path
        status, parsed, token = post(endpoint, key,
                                     responses_single_call_body(model, args.max_tokens))
        print("step1_%s\tstatus=%s" % (label, status))
        if status != 200 or parsed is None:
            print("step1_%s\terror=%s" % (label, token))
            return 0
        # Extract output items from the response
        output = parsed.get("output") or []
        print("step1_%s\toutput_items=%d" % (label, len(output)))
        for i, item in enumerate(output):
            itype = item.get("type", "?")
            keys = sorted(item.keys())
            print("step1_%s\toutput[%d] type=%s keys=%s" % (label, i, itype, ",".join(keys)))
            # Check for reasoning text
            for rk in REASONING_KEYS:
                if rk in item:
                    try:
                        print("step1_%s\toutput[%d] %s_len=%d" % (label, i, rk, len(str(item[rk]))))
                    except Exception:
                        print("step1_%s\toutput[%d] %s_len=?" % (label, i, rk))
            # Check for reasoning_text in content
            content = item.get("content")
            if isinstance(content, list):
                for j, part in enumerate(content):
                    if isinstance(part, dict):
                        ptype = part.get("type", "?")
                        pkeys = sorted(part.keys())
                        print("step1_%s\toutput[%d].content[%d] type=%s keys=%s" % (label, i, j, ptype, ",".join(pkeys)))
                        for rk in ("text", "reasoning_text"):
                            if rk in part:
                                try:
                                    print("step1_%s\toutput[%d].content[%d] %s_len=%d" % (label, i, j, rk, len(str(part[rk]))))
                                except Exception:
                                    pass
        # Find the function_call item
        call_id = None
        for item in output:
            if item.get("type") == "function_call":
                call_id = item.get("call_id") or item.get("id")
                break
        if not call_id:
            print("step1_%s\tno function_call: round trip skipped" % label)
            return 0
        # For Responses API, we include the output items directly in the next input
        # Test: include output items as-is (should work if gateway replays reasoning)
        status, _parsed, token = post(endpoint, key,
                                      responses_round_trip_body(model, output, call_id, args.max_tokens))
        print("roundtrip_%s\tstatus=%s%s" % (label, status, "" if status == 200 else " error=%s" % token))
        return 0

    # Chat/completions path
    status, parsed, token = post(endpoint, key,
                                 single_call_body(model, args.max_tokens))
    print("step1_%s\tstatus=%s" % (label, status))
    if status != 200 or parsed is None:
        print("step1_%s\terror=%s" % (label, token))
        return 0
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        print("step1_%s\tmalformed" % label)
        return 0
    print("step1_%s\t%s" % (label, describe_message(message)))
    calls = message.get("tool_calls") or []
    if not calls:
        print("step1_%s\tno tool_call: round trip skipped" % label)
        return 0
    call_id = calls[0].get("id")

    if args.variant == "strip":
        rt_msg = strip_reasoning(message)
    else:
        rt_msg = strip_reasoning_and_content(message)
    print("roundtrip_%s_%s\t%s" % (label, args.variant, describe_message(rt_msg)))
    status, _parsed, token = post(
        endpoint, key,
        round_trip_body(model, rt_msg, call_id, args.max_tokens))
    print("roundtrip_%s_%s\tstatus=%s%s" % (
        label, args.variant, status, "" if status == 200 else " error=%s" % token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
