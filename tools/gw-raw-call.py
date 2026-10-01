import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
url = "http://127.0.0.1:20128/v1/chat/completions"


def call(leg, tools=False, max_tokens=64):
    body = {"model": leg, "messages": [{"role": "user", "content": "Reply with exactly: ACK_OK"}],
            "max_tokens": max_tokens}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": "get_weather", "description": "w",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}}]
        body["tool_choice"] = "auto"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read(1200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(1200).decode("utf-8", "replace")
    except Exception as e:
        return "ERR", type(e).__name__


for spec in sys.argv[1:]:
    leg, _, mode = spec.partition("#")
    status, body = call(leg, tools=(mode == "tool"))
    print("### %s (%s) -> %s" % (leg, mode or "ack", status))
    print(body)
