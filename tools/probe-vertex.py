#!/usr/bin/env python3
"""
probe-vertex.py — Reproduce the Vertex/Gemini "Requests ending with a model turn are not supported" 400.

Sends an agentic conversation that ends with an assistant turn carrying tool_calls
to the live OmniRoute gateway, captures the verbatim 400 (status + body), and
identifies the minimal message shape that triggers it.

Tries multiple Gemini-family models — they all share the same openaiToGeminiBase
transform, so the bug reproduces on any of them.

AutoOS lane F1-vertex  |  2026-09-30  |  branch L1-backlog/ws-f1-vertex-20260930
"""
import sys
import json
import time
import urllib.request
import urllib.error

GATEWAY = "http://127.0.0.1:20128"
# PLAIN model names — the omniroute/ prefix is only inside opencode.
MODELS = ["vertex/gemini-3.8-flash", "gemini/gemini-2.5-flash"]

def send_chat(model, messages, tools=None, stream=False, max_tokens=256):
    """Send a chat completion request to the gateway and return (status, body)."""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        return -1, str(e)

# A simple tool definition for the probe
TOOL_DEF = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "The city name."}
                },
                "required": ["city"],
            },
        },
    }
]

def test_trailing_model_turn_with_tool_calls(model):
    """Conversation ending with an assistant turn that has tool_calls — the agentic 400 shape."""
    messages = [
        {"role": "user", "content": "What is the weather in Paris?"},
        {"role": "assistant", "content": "I'll check the weather for you.", "tool_calls": [
            {"id": "call_001", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
        ]},
    ]
    return send_chat(model, messages, tools=TOOL_DEF)

def test_trailing_model_turn_plain_text(model):
    """Conversation ending with an assistant turn that has plain text (no tool_calls)."""
    messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I am doing well, thank you!"},
    ]
    return send_chat(model, messages, tools=TOOL_DEF)

def test_normal_ending_with_user(model):
    """Conversation ending with a user/tool turn — should succeed (200)."""
    messages = [
        {"role": "user", "content": "What is the weather in Paris?"},
        {"role": "assistant", "content": "I'll check the weather for you.", "tool_calls": [
            {"id": "call_001", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_001", "content": '{"temperature": 18, "condition": "cloudy"}'},
    ]
    return send_chat(model, messages, tools=TOOL_DEF)

def test_complex_agentic_shape(model):
    """Complex agentic shape — multi-turn with multiple tool calls ending with a model turn."""
    messages = [
        {"role": "system", "content": "You are a helpful assistant with access to tools."},
        {"role": "user", "content": "I need to know the weather in Paris and London, then summarize."},
        {"role": "assistant", "content": "I'll check the weather for both cities.", "tool_calls": [
            {"id": "call_001", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
            {"id": "call_002", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "London"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call_001", "content": '{"temperature": 18, "condition": "cloudy"}'},
        {"role": "tool", "tool_call_id": "call_002", "content": '{"temperature": 12, "condition": "rainy"}'},
        {"role": "user", "content": "Great, now give me a summary and check Tokyo too."},
        {"role": "assistant", "content": "Let me check Tokyo weather for you.", "tool_calls": [
            {"id": "call_003", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'}},
        ]},
    ]
    return send_chat(model, messages, tools=TOOL_DEF)

def main():
    tests = [
        ("Test 1: trailing model turn with tool_calls", test_trailing_model_turn_with_tool_calls),
        ("Test 2: trailing model turn plain text", test_trailing_model_turn_plain_text),
        ("Test 3: normal ending with user (should pass)", test_normal_ending_with_user),
        ("Test 4: complex agentic shape (7 msg, tool_calls)", test_complex_agentic_shape),
    ]

    results = []
    for model in MODELS:
        for name, test_fn in tests:
            label = f"[{model}] {name}"
            print(f"\n{'='*60}")
            print(f"  {label}")
            print(f"{'='*60}")
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(f"  Timestamp: {ts}")
            status, body = test_fn(model)
            print(f"  Status: {status}")
            if len(body) > 2000:
                print(f"  Body (truncated): {body[:2000]}...")
            else:
                print(f"  Body: {body}")
            results.append({"model": model, "test": name, "status": status,
                             "body": body[:500], "timestamp": ts})

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in results:
        marker = "400" if r["status"] == 400 else ("200" if r["status"] == 200 else str(r["status"]))
        print(f"  [{marker}] {r['model']} | {r['test']}")

    # Save results
    with open("probe-vertex-results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved to probe-vertex-results.json")

if __name__ == "__main__":
    main()
