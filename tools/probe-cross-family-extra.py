#!/usr/bin/env python3
"""Quick probe for additional cross-family models (NVIDIA reasoning, Qwen QwQ)."""
import json, os, urllib.request, urllib.error, uuid

GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20128")
API_KEY = os.environ.get("AUTOOS_OMNIROUTE_KEY", "")

def chat(model, messages, tools=None):
    payload = {"model": model, "messages": messages, "stream": False}
    if tools: payload["tools"] = tools
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if API_KEY: headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(f"{GATEWAY}/v1/chat/completions", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode() if e.fp else ""
    except Exception as ex:
        return 0, str(ex)

tool = [{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]
user = {"role":"user","content":"What is the weather in Tokyo? Use the get_weather tool."}

candidates = [
    "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "together/Qwen/QwQ-32B",
]

for model in candidates:
    print(f"\n--- {model} ---")
    s, b = chat(model, [user], tools=tool)
    print(f"  step1 status={s}")
    if s == 200:
        r = json.loads(b)
        msg = r.get("choices",[{}])[0].get("message",{})
        rc = msg.get("reasoning_content","")
        tc = msg.get("tool_calls",[])
        print(f"  rc_len={len(rc) if rc else 0}  tool_calls={len(tc)}")
        if tc:
            ids = [t.get("id","?") for t in tc]
            new_id = f"call_{uuid.uuid4().hex[:8]}"
            am = {"role":"assistant","content":msg.get("content"),"tool_calls":tc}
            for t in am["tool_calls"]: t["id"] = new_id
            m2 = [user, am, {"role":"tool","tool_call_id":new_id,"content":"Sunny, 22C"}]
            s2, b2 = chat(model, m2, tools=tool)
            print(f"  step2 status={s2}")
            if s2 == 400:
                try:
                    err = json.loads(b2).get("error",{}).get("message",b2[:200])
                except Exception:
                    err = b2[:200]
                is_reasoning = "reasoning" in str(err).lower()
                print(f"  error: {str(err)[:200]}")
                print(f"  RESULT: {'400_REASONING' if is_reasoning else '400_OTHER'}")
            elif s2 == 200:
                print(f"  RESULT: PASS")
            else:
                print(f"  RESULT: ERROR_{s2}")
                print(f"  body: {b2[:200]}")
        else:
            print(f"  NO_TOOL_CALL — finish_reason={r.get('choices',[{}])[0].get('finish_reason','?')}")
    else:
        print(f"  FAILED: {b[:200]}")
