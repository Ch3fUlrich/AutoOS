#!/usr/bin/env python3
"""
probe-reasoning.py — DeepSeek thinking-mode 400 reproduction probe.

Tests the gateway's reasoning defense pipeline by sending a thinking-mode
tool-call round trip with stripped reasoning_content.  Exercises multiple
model strings to map which trigger the V4 pattern match that skips the
empty-reasoning injection safety net.

Root cause (gateway v3.8.50):
  reasoningCache.ts:97   if (interleavedField === "reasoning_content") return true;
  translator/index.ts:614  !requiresExplicitReasoningReplay  ← guards the injection
  When requiresExplicitReasoningReplay is true the injection at :618 is SKIPPED;
  only the replay cache remains.  On a cold cache the assistant message has no
  reasoning_content → DeepSeek returns 400.

Usage:
  python tools/probe-reasoning.py                 # default: all variants
  python tools/probe-reasoning.py --variant strip  # strip only
  python tools/probe-reasoning.py --models deepseek-v4.1-flash,deepseek-v4-flash
"""
import argparse
import json
import os
import sys
import time
import uuid

import urllib.request
import urllib.error

GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20128")

# ── helpers ──────────────────────────────────────────────────────────────────

def chat(model, messages, tools=None, reasoning_effort=None, stream=False):
    """Send a chat completion request; return (status, body_or_error)."""
    payload = {"model": model, "messages": messages, "stream": stream}
    if tools:
        payload["tools"] = tools
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return resp.status, body
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

# ── probe ────────────────────────────────────────────────────────────────────

def probe_model(model, variant="strip", reasoning_effort=None, label=""):
    """
    Two-step probe:
      1. Send a tool-calling request → get reasoning_content + tool_calls
      2. Send a round trip with the tool result, but STRIP reasoning_content
         from the assistant message (simulating what opencode/AI-SDK does)
    """
    tag = label or model
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    print(f"\n{'='*72}")
    print(f"  model={model}  variant={variant}  effort={reasoning_effort or 'default'}")
    print(f"  tool_call_id={call_id}")
    print(f"{'='*72}")

    # Step 1: get reasoning + tool_calls
    messages1 = [USER_MSG]
    status1, body1 = chat(model, messages1, tools=make_tool(),
                         reasoning_effort=reasoning_effort)
    print(f"\n  step1 status={status1}")

    if status1 != 200:
        print(f"  step1 FAILED — cannot proceed")
        print(f"  body: {body1[:500]}")
        return {"model": model, "variant": variant,
                "step1_status": status1, "step1_error": body1[:500],
                "round_trip_status": None, "result": "STEP1_FAILED"}

    resp1 = json.loads(body1)
    choice1 = resp1.get("choices", [{}])[0]
    msg1 = choice1.get("message", {})
    rc1 = msg1.get("reasoning_content", "")
    tc1 = msg1.get("tool_calls", [])
    print(f"  step1 reasoning_content len={len(rc1) if rc1 else 0}")
    print(f"  step1 tool_calls={len(tc1)}")

    if not tc1:
        print(f"  step1: no tool_calls returned — model did not call tool")
        print(f"  finish_reason={choice1.get('finish_reason')}")
        return {"model": model, "variant": variant,
                "step1_status": status1,
                "step1_reasoning_len": len(rc1) if rc1 else 0,
                "step1_tool_calls": 0,
                "round_trip_status": None, "result": "NO_TOOL_CALL"}

    # Build assistant message for step 2
    assistant_msg = {
        "role": "assistant",
        "content": msg1.get("content"),
        "tool_calls": tc1,
    }
    # Fix tool_call_id to our unique value
    for tc in assistant_msg["tool_calls"]:
        tc["id"] = call_id
        if isinstance(tc.get("function"), dict):
            tc["function"]["arguments"] = json.dumps({"city": "Tokyo"})

    # Variant: strip reasoning_content (simulating opencode/AI-SDK behavior)
    if variant in ("strip", "no-effort", "combo"):
        pass  # reasoning_content is not included in assistant_msg
    elif variant == "preserve":
        if rc1:
            assistant_msg["reasoning_content"] = rc1

    # Step 2: round trip with tool result
    messages2 = [
        USER_MSG,
        assistant_msg,
        {"role": "tool", "tool_call_id": call_id, "content": "Sunny, 22°C"},
    ]
    status2, body2 = chat(model, messages2, tools=make_tool())
    print(f"\n  step2 (round trip) status={status2}")

    result = "PASS"
    if status2 == 400:
        err = json.loads(body2) if body2 else {}
        err_msg = err.get("error", {}).get("message", body2[:500])
        if "reasoning" in err_msg.lower():
            result = "400_REASONING"
            print(f"  *** 400 REASONING ERROR ***")
            print(f"  error: {err_msg[:300]}")
        else:
            result = "400_OTHER"
            print(f"  400 (non-reasoning): {err_msg[:300]}")
    elif status2 != 200:
        result = f"ERROR_{status2}"
        print(f"  error: {body2[:300]}")
    else:
        print(f"  PASS — reasoning defense worked")

    return {
        "model": model, "variant": variant,
        "step1_status": status1,
        "step1_reasoning_len": len(rc1) if rc1 else 0,
        "step1_tool_calls": len(tc1),
        "round_trip_status": status2,
        "result": result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="strip",
                        choices=["strip", "preserve", "no-effort", "combo"],
                        help="Reasoning variant for the round trip")
    parser.add_argument("--models", default=None,
                        help="Comma-separated model strings to test")
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "max"],
                        help="reasoning_effort to send")
    args = parser.parse_args()

    default_models = [
        "deepseek-v4.1-flash",        # combo (current, correct)
        "deepseek/deepseek-flash",    # direct provider/model
    ]
    models = args.models.split(",") if args.models else default_models

    results = []
    for model in models:
        effort = args.effort
        if args.variant == "no-effort":
            effort = None
        elif args.variant == "combo" and "v4.1" in model:
            effort = "high"
        r = probe_model(model, variant=args.variant, reasoning_effort=effort)
        results.append(r)
        time.sleep(1)

    print(f"\n\n{'='*72}")
    print(f"  SUMMARY")
    print(f"{'='*72}")
    for r in results:
        status = r.get("result", "UNKNOWN")
        rt = r.get("round_trip_status", "-")
        s1 = r.get("step1_status", "-")
        rc_len = r.get("step1_reasoning_len", "-")
        tc = r.get("step1_tool_calls", "-")
        print(f"  {r['model']:40s} step1={s1} rc={rc_len} tc={tc}  round_trip={rt}  -> {status}")

    # Exit code
    has_400 = any(r.get("result") == "400_REASONING" for r in results)
    print(f"\n  400 reasoning error reproduced: {has_400}")
    sys.exit(1 if has_400 else 0)


if __name__ == "__main__":
    main()
