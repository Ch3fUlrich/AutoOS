#!/usr/bin/env python3
"""
probe-cross-family.py — Cross-family review for DeepSeek thinking-mode 400.

Tests whether the reasoning 400 (cache-miss on tool-call round trip) is
DeepSeek-specific or affects other reasoning-capable model families.

For each model family:
  step 1: tool-calling request -> reasoning_content + tool_calls
  step 2: round trip with tool result, reasoning_content STRIPPED,
          tool_call_id OVERWRITTEN (forces cache miss)

A 400 mentioning "reasoning" = family-specific bug (same root cause).
A 200 or non-reasoning error = DeepSeek-specific.

Auth: reads AUTOOS_OMNIROUTE_KEY from the environment (never printed).
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
        with urllib.request.urlopen(req, timeout=90) as resp:
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


def probe(model, family, overwrite_call_id=True, reasoning_effort="high"):
    tag = "overwrite" if overwrite_call_id else "preserve"
    print(f"\n{'='*72}")
    print(f"  family={family}  model={model}  call_id={tag}  effort={reasoning_effort}")
    print(f"{'='*72}")

    # Step 1
    status1, body1 = chat(model, [USER_MSG], tools=make_tool(),
                          reasoning_effort=reasoning_effort)
    print(f"  step1 status={status1}")
    if status1 != 200:
        snippet = body1[:300] if body1 else "(empty)"
        print(f"  step1 FAILED: {snippet}")
        return {"family": family, "model": model, "tag": tag,
                "step1": status1, "result": "STEP1_FAILED", "error": snippet}

    resp1 = json.loads(body1)
    msg1 = resp1.get("choices", [{}])[0].get("message", {})
    rc1 = msg1.get("reasoning_content", "")
    tc1 = msg1.get("tool_calls", [])
    orig_ids = [tc.get("id", "?") for tc in tc1]
    print(f"  step1 reasoning_content len={len(rc1) if rc1 else 0}")
    print(f"  step1 tool_calls={len(tc1)}  ids={orig_ids}")

    if not tc1:
        print(f"  no tool_calls — model did not call the tool")
        return {"family": family, "model": model, "tag": tag,
                "step1": 200, "step1_rc_len": len(rc1) if rc1 else 0,
                "result": "NO_TOOL_CALL"}

    # Build assistant message — STRIP reasoning_content (simulating AI SDK)
    assistant_msg = {
        "role": "assistant",
        "content": msg1.get("content"),
        "tool_calls": tc1,
    }

    call_id = orig_ids[0]
    if overwrite_call_id:
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        for tc in assistant_msg["tool_calls"]:
            tc["id"] = call_id
        print(f"  overwrote tool_call_id: {orig_ids[0]} -> {call_id}")

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
        try:
            err = json.loads(body2) if body2 else {}
            err_msg = err.get("error", {}).get("message", body2[:500]) if isinstance(err, dict) else body2[:500]
        except Exception:
            err_msg = body2[:500]
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
        print(f"  PASS — reasoning defense worked (or not needed)")

    return {
        "family": family, "model": model, "tag": tag,
        "step1": 200, "step1_rc_len": len(rc1) if rc1 else 0,
        "step1_tc": len(tc1), "orig_ids": orig_ids,
        "step2": status2, "result": result,
    }


def main():
    # Cross-family models: pick 2-3 families DIFFERENT from DeepSeek
    # Use reasoning-capable models that support tool calls.
    # Model IDs verified against gateway /v1/models listing.
    # Batch 2: free-tier and alternative-provider models after credits issues
    # with openrouter paid legs.
    models = [
        # DeepSeek is the reference (known to 400 on cache miss)
        ("deepseek", "deepseek-v4.1-flash", "high"),
        # Gemini family (already passed in batch 1)
        ("gemini", "vertex/gemini-3.8-flash", None),
        # Qwen family — free tier via openrouter
        ("qwen", "openrouter/qwen/qwen3.8-27b:free", None),
        # Thinking Machines / Inkling — free tier, reasoning-capable
        ("thinkingmachines", "openrouter/thinkingmachines/inkling-small:free", None),
        # Qwen via OVH (different backend, no openrouter credits needed)
        ("qwen-ovh", "ovh/Qwen3.8-27B", None),
    ]

    results = []
    for family, model, effort in models:
        r = probe(model, family, overwrite_call_id=True, reasoning_effort=effort)
        results.append(r)
        time.sleep(2)

    print(f"\n\n{'='*72}")
    print(f"  CROSS-FAMILY SUMMARY")
    print(f"{'='*72}")
    print(f"  {'family':12s} {'model':35s} {'step1':>5s} {'rc_len':>6s} {'step2':>5s}  result")
    print(f"  {'-'*12} {'-'*35} {'-'*5} {'-'*6} {'-'*5}  {'-'*20}")
    for r in results:
        fam = r.get("family", "?")
        mod = r.get("model", "?")
        s1 = r.get("step1", "-")
        rc = r.get("step1_rc_len", "-")
        s2 = r.get("step2", "-")
        res = r.get("result", "?")
        print(f"  {fam:12s} {mod:35s} {str(s1):>5s} {str(rc):>6s} {str(s2):>5s}  {res}")

    # Analysis
    print(f"\n  Analysis:")
    deepseek_400 = any(r.get("result") == "400_REASONING" and r.get("family") == "deepseek" for r in results)
    other_400 = [r for r in results if r.get("result") == "400_REASONING" and r.get("family") != "deepseek"]
    other_pass = [r for r in results if r.get("result") == "PASS" and r.get("family") != "deepseek"]
    other_no_tool = [r for r in results if r.get("result") == "NO_TOOL_CALL" and r.get("family") != "deepseek"]

    print(f"  DeepSeek 400_REASONING: {deepseek_400}")
    print(f"  Other families 400_REASONING: {len(other_400)}  {[r['family'] for r in other_400]}")
    print(f"  Other families PASS: {len(other_pass)}  {[r['family'] for r in other_pass]}")
    print(f"  Other families NO_TOOL_CALL: {len(other_no_tool)}  {[r['family'] for r in other_no_tool]}")

    if other_400:
        print(f"\n  WARNING: 400 reasoning error is NOT DeepSeek-specific!")
    elif deepseek_400 and not other_400:
        print(f"\n  CONFIRMED: 400 reasoning error is DeepSeek-specific.")
    else:
        print(f"\n  INCONCLUSIVE: DeepSeek did not 400 in this run.")

    # Exit 0 if DeepSeek 400 reproduced AND no other family 400s
    sys.exit(0 if (deepseek_400 and not other_400) else 1)


if __name__ == "__main__":
    main()
