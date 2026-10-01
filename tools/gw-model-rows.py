import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
req = urllib.request.Request("http://127.0.0.1:20128/v1/models",
                             headers={"Authorization": "Bearer " + key})
data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
want = sys.argv[1:]
rows = data.get("data", [])
seen = set()
for row in rows:
    mid = row.get("id", "")
    if not want or any(mid.startswith(w) or mid == w for w in want):
        if mid in seen:
            continue
        seen.add(mid)
        print(json.dumps(row)[:400])
        if len(seen) >= 40:
            break
