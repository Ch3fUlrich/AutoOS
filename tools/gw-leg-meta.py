import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
req = urllib.request.Request("http://127.0.0.1:20128/v1/models",
                             headers={"Authorization": "Bearer " + key})
data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
rows = {r.get("id"): r for r in data.get("data", [])}

legs = [line.strip() for line in sys.stdin if line.strip()]
for leg in legs:
    r = rows.get(leg)
    if not r:
        print("%s\tMISSING" % leg)
        continue
    caps = r.get("capabilities") or {}
    pr = r.get("pricing") or {}
    print("%s\tctx=%s\tin=%s\tout=%s\ttools=%s\tprice_in=%s\tprice_out=%s\towner=%s" % (
        leg, r.get("context_length"), r.get("max_input_tokens"),
        r.get("max_output_tokens"), caps.get("tool_calling"),
        pr.get("input"), pr.get("output"), r.get("owned_by")))
