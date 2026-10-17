import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
req = urllib.request.Request("http://127.0.0.1:20128/v1/models",
                             headers={"Authorization": "Bearer " + key})
data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
provs = sys.argv[1:]
for r in data.get("data", []):
    mid = r.get("id", "")
    if not any(mid.startswith(p) for p in provs):
        continue
    pr = r.get("pricing") or {}
    pin, pout = pr.get("input"), pr.get("output")
    free = (pin in (0, 0.0, "0")) and (pout in (0, 0.0, "0"))
    if free:
        caps = r.get("capabilities") or {}
        print("%s\tctx=%s\tout=%s\ttools=%s" % (mid, r.get("context_length"),
              r.get("max_output_tokens"), caps.get("tool_calling")))
