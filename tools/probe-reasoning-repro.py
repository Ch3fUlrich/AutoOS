#!/usr/bin/env python3
"""
probe-reasoning-repro.py — DeepSeek thinking-mode 400 reproduction probe.

Two-step probe:
  1. Send a tool-calling request -> get reasoning_content + tool_calls
  2. Send a round trip with the tool result, stripping reasoning_content
     from the assistant message (simulating opencode/AI-SDK behavior)

Tests both:
  - overwrite_call_id=True  (probe sets its own tool_call_id → cache miss)
  - overwrite_call_id=False (preserve original tool_call_id → cache hit)
"""
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error

GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20128")
API_KEY = os.environ.get("AUTOOS_OMNIROUTE_KEY", "")

def chat(model, messages, tools=None, reasoning_effort=None):
    payload = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return e.code, body
    except Exception as ex:
        return 0, str(ex)

def make_tool():
    return [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]

USER_MSG = {"role": "user", "content": "What is the weather in Tokyo? Use the get_weather tool."}

def probe(model, overwrite_call_id=True, reasoning_effort="high"):
    tag = "overwrite" if overwrite_call_id else "preserve"
    print(f"\n{'='*72}")
    print(f"  model={model}  call_id={tag}  effort={reasoning_effort}")
    print(f"{'='*72}")

    # Step 1
    status1, body1 = chat(model, [USER_MSG], tools=make_tool(),
                          reasoning_effort=reasoning_effort)
    print(f"  step1 status={status1}")
    if status1 != 200:
        print(f"  step1 FAILED: {body1[:300]}")
        return {"model": model, "tag": tag, "step1": status1, "result": "STEP1_FAILED"}

    resp1 = json.loads(body1)
    msg1 = resp1.get("choices", [{}])[0].get("message", {})
    rc1 = msg1.get("reasoning_content", "")
    tc1 = msg1.get("tool_calls", [])

    # Print the original tool_call_id
    orig_ids = [tc.get("id", "?") for tc in tc1]
    print(f"  step1 reasoning_content len={len(rc1) if rc1 else 0}")
    print(f"  step1 tool_calls={len(tc1)}  ids={orig_ids}")

    if not tc1:
        print(f"  no tool_calls — skipping round trip")
        return {"model": model, "tag": tag, "step1": 200, "result": "NO_TOOL_CALL"}

    # Build assistant message — STRIP reasoning_content
    assistant_msg = {
        "role": "assistant",
        "content": msg1.get("content"),
        "tool_calls": tc1,
    }
    # reasoning_content is NOT included (stripped)

    # Optionally overwrite tool_call_id
    call_id = orig_ids[0]  # default: keep original
    if overwrite_call_id:
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        for tc in assistant_msg["tool_calls"]:
            tc["id"] = call_id
        print(f"  overwrote tool_call_id: {orig_ids[0]} -> {call_id}")
    else:
        print(f"  preserved tool_call_id: {call_id}")

    # Step 2: round trip
    messages2 = [
        USER_MSG,
        assistant_msg,
        {"role": "tool", "tool_call_id": call_id, "content": "Sunny, 22C"},
    ]
    status2, body2 = chat(model, messages2, tools=make_tool())
    print(f"  step2 (round trip) status={status2}")

    result = "PASS"
    if status2 == 400:
        err = json.loads(body2) if body2 and body2.startswith("{") else {}
        err_msg = err.get("error", {}).get("message", body2[:500]) if isinstance(err, dict) else body2[:500]
        if "reasoning" in str(err_msg).lower():
            result = "400_REASONING"
            print(f"  *** 400 REASONING ERROR ***")
            print(f"  error: {str(err_msg)[:300]}")
        else:
            result = "400_OTHER"
            print(f"  400 (other): {str(err_msg)[:300]}")
    elif status2 != 200:
        result = f"ERROR_{status2}"
        print(f"  error: {body2[:300]}")
    else:
        print(f"  PASS — reasoning defense worked")

    return {
        "model": model, "tag": tag,
        "step1": 200, "step1_rc_len": len(rc1) if rc1 else 0,
        "step1_tc": len(tc1), "orig_ids": orig_ids,
        "step2": status2, "result": result,
    }

def main():
    models = ["deepseek-v4.1-flash", "deepseek/deepseek-flash"]
    results = []
    for model in models:
        for overwrite in [True, False]:
            r = probe(model, overwrite_call_id=overwrite, reasoning_effort="high")
            results.append(r)
            time.sleep(1)

    print(f"\n\n{'='*72}")
    print(f"  SUMMARY")
    print(f"{'='*72}")
    for r in results:
        s2 = r.get("step2", "-")
        res = r.get("result", "?")
        rc = r.get("step1_rc_len", "-")
        print(f"  {r['model']:35s} {r['tag']:10s} rc={rc:3}  step2={s2}  -> {res}")

    has_400 = any(r.get("result") == "400_REASONING" for r in results)
    print(f"\n  400 reasoning error reproduced: {has_400}")
    sys.exit(1 if has_400 else 0)

if __name__ == "__main__":
    main()
