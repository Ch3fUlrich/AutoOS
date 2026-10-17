#!/usr/bin/env python3
"""Streaming thinking-mode tool-call round trip for a gateway leg.

Companion to probe-reasoning.py (non-streaming). This tests the streaming
path, where the gateway may emit `reasoning_text` instead of
`reasoning_content` as the delta field name — the operator's 400 error
mentions `reasoning_text`, which the non-streaming probe did not reproduce.

Safe to log: HTTP status, key names, reasoning field lengths, tool_call
names. Never logs the key, body text, or reasoning content.

Usage:
    python3 tools/probe-reasoning-stream.py --leg deepseek/deepseek-flash
    python3 tools/probe-reasoning-stream.py --leg deepseek/deepseek-flash --variant all
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
        "stream": True,
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
        "stream": True,
    }


def stream_post(url, key, body, timeout=180):
    """Stream a request and reassemble the assistant message from SSE chunks.

    Returns (status, assistant_message_or_None, error_token_or_None).
    The assistant message is reassembled from streaming deltas: content,
    tool_calls, and any reasoning-ish field. Key names only are safe to log.
    """
    body = dict(body)
    body["stream"] = True
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "Accept": "text/event-stream"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
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

    status = resp.status
    # Reassemble the message from SSE chunks
    msg = {"role": "assistant", "content": "", "tool_calls": []}
    reasoning_fields = {}  # field_name -> accumulated text
    tool_calls = {}  # index -> {id, function: {name, arguments}}

    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").rstrip("\n\r")
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        # Content
        c = delta.get("content")
        if isinstance(c, str):
            msg["content"] += c
        # Reasoning fields — capture ALL of them by key name
        for rk in REASONING_KEYS:
            rv = delta.get(rk)
            if isinstance(rv, str) and rv:
                reasoning_fields.setdefault(rk, "")
                reasoning_fields[rk] += rv
        # Also capture any other field that looks like reasoning
        for k, v in delta.items():
            if k in REASONING_KEYS:
                continue
            if isinstance(v, str) and ("reason" in k.lower() or "think" in k.lower()):
                reasoning_fields.setdefault(k, "")
                reasoning_fields[k] += v
        # Tool calls
        tcs = delta.get("tool_calls") or []
        for tc in tcs:
            idx = tc.get("index", 0)
            tc_entry = tool_calls.setdefault(idx, {})
            if "id" in tc:
                tc_entry["id"] = tc["id"]
            fn = tc_entry.setdefault("function", {})
            tcf = tc.get("function") or {}
            if "name" in tcf:
                fn["name"] = tcf["name"]
            if "arguments" in tcf:
                fn.setdefault("arguments", "")
                fn["arguments"] += tcf["arguments"]

    # Finalize
    if not msg["content"]:
        msg.pop("content", None)
    for rk, rv in reasoning_fields.items():
        if rv:
            msg[rk] = rv
    if tool_calls:
        msg["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    else:
        msg.pop("tool_calls", None)

    return status, msg, None


def describe_message(message):
    keys = sorted(message.keys())
    parts = ["keys=%s" % (",".join(keys),)]
    for k in REASONING_KEYS:
        if k in message and message[k] is not None:
            try:
                parts.append("%s_len=%d" % (k, len(str(message[k]))))
            except Exception:
                parts.append("%s_len=?" % k)
    # Also show any non-standard reasoning fields
    for k in sorted(message.keys()):
        if k not in REASONING_KEYS and k not in ("role", "content", "tool_calls"):
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


def apply_variant(assistant_message, variant):
    msg = json.loads(json.dumps(assistant_message))
    if variant == "verbatim":
        return msg
    src = None
    for k in REASONING_KEYS:
        if k in msg and msg[k] is not None:
            src = k
            break
    if src is None:
        return msg
    if variant == "mirror":
        msg.setdefault("reasoning_text", msg[src])
        return msg
    if variant == "rename":
        if src != "reasoning_text":
            msg["reasoning_text"] = msg.pop(src)
        return msg
    return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Streaming thinking-mode tool-call round trip probe.")
    ap.add_argument("--leg", default="deepseek/deepseek-flash")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--variant", default="all",
                    choices=("verbatim", "mirror", "rename", "all"))
    ap.add_argument("--max-tokens", type=int, default=512)
    args = ap.parse_args(argv)

    agent = load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-reasoning-stream: no OmniRoute client key", file=sys.stderr)
        return 3
    if not gateway_up(args.gateway):
        print("probe-reasoning-stream: gateway not reachable at %s" % args.gateway,
              file=sys.stderr)
        return 3

    status, message, token = stream_post(
        args.gateway, key, single_call_body(args.leg, args.max_tokens))
    print("step1_stream\tstatus=%s" % status)
    if status != 200 or message is None:
        print("step1_stream\terror=%s" % (token,))
        return 0
    print("step1_stream\t%s" % describe_message(message))
    calls = message.get("tool_calls") or []
    if not calls:
        print("step1_stream\tno tool_call: round trip skipped")
        return 0
    call_id = calls[0].get("id")

    variants = ([args.variant] if args.variant != "all"
                else ["verbatim", "mirror", "rename"])
    for variant in variants:
        rt_msg = apply_variant(message, variant)
        if variant != "verbatim":
            print("roundtrip_stream_%s\t%s" % (variant, describe_message(rt_msg)))
        status, _msg, token = stream_post(
            args.gateway, key,
            round_trip_body(args.leg, rt_msg, call_id, args.max_tokens))
        print("roundtrip_stream_%s\tstatus=%s%s" % (
            variant, status, "" if status == 200 else " error=%s" % token))
        if args.variant == "all" and status == 200:
            break
        if args.variant != "all":
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
