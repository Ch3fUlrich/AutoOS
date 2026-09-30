import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
req = urllib.request.Request("http://127.0.0.1:20128/api/providers",
                             headers={"Authorization": "Bearer " + key})
data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
con = data.get("connections", data if isinstance(data, list) else [])
print("connections:", len(con))
for c in con:
    print(json.dumps({
        "provider": c.get("provider"),
        "name": c.get("name"),
        "authType": c.get("authType"),
        "isActive": c.get("isActive"),
        "testStatus": c.get("testStatus"),
        "errorCode": c.get("errorCode"),
        "lastErrorType": c.get("lastErrorType"),
        "lastError": (c.get("lastError") or "")[:160],
    }, ensure_ascii=False))
