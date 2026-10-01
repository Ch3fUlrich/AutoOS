#!/usr/bin/env python3
"""
probe-vertex-isolated.py — Test the trailing model turn fix on an isolated gateway.

Sends the same agentic shapes to the isolated gateway on :20138 (which has the
patched code) and to the shared gateway on :20128 (which still has the old code
running in memory) to demonstrate before/after.

AutoOS lane F1-vertex  |  2026-09-30
"""
import json
import time
import urllib.request
import urllib.error

ISOLATED = "http://127.0.0.1:20138"   # patched code, isolated instance
SHARED   = "http://127.0.0.1:20128"   # old code still in memory (not restarted)
MODEL = "vertex/gemini-3.8-flash"

def send_chat(gateway, model, messages, tools=None, stream=False, max_tokens=256):
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": stream}
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway}/v1/chat/completions", data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)

TOOL_DEF = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get the current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
}}]

# The failing shape: conversation ends with an assistant turn carrying tool_calls
TRAILING_TOOL_CALLS = [
    {"role": "user", "content": "What is the weather in Paris?"},
    {"role": "assistant", "content": "I'll check the weather for you.", "tool_calls": [
        {"id": "call_001", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
    ]},
]

# The failing shape: conversation ends with plain text assistant turn
TRAILING_PLAIN_TEXT = [
    {"role": "user", "content": "Hello, how are you?"},
    {"role": "assistant", "content": "I am doing well, thank you!"},
]

# The passing shape: conversation ends with a tool/user turn
NORMAL_ENDING = [
    {"role": "user", "content": "What is the weather in Paris?"},
    {"role": "assistant", "content": "I'll check the weather for you.", "tool_calls": [
        {"id": "call_001", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
    ]},
    {"role": "tool", "tool_call_id": "call_001", "content": '{"temperature": 18, "condition": "cloudy"}'},
]

def main():
    tests = [
        ("Test 1: trailing model turn with tool_calls", TRAILING_TOOL_CALLS),
        ("Test 2: trailing model turn plain text", TRAILING_PLAIN_TEXT),
        ("Test 3: normal ending with user (control)", NORMAL_ENDING),
    ]

    results = []
    for gateway_name, gateway_url in [("ISOLATED (patched)", ISOLATED), ("SHARED (old)", SHARED)]:
        for name, messages in tests:
            label = f"[{gateway_name}] {name}"
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            status, body = send_chat(gateway_url, MODEL, messages, tools=TOOL_DEF)
            # Truncate body for display
            body_short = body[:500] if len(body) > 500 else body
            marker = "400" if status == 400 else ("200" if status == 200 else str(status))
            print(f"  [{marker}] {label}")
            if status != 200:
                print(f"         body: {body_short}")
            results.append({"gateway": gateway_name, "test": name, "status": status,
                            "body": body_short, "timestamp": ts})

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in results:
        marker = "400" if r["status"] == 400 else ("200" if r["status"] == 200 else str(r["status"]))
        print(f"  [{marker}] {r['gateway']:20s} | {r['test']}")

    with open("probe-vertex-isolated-results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved to probe-vertex-isolated-results.json")

if __name__ == "__main__":
    main()
