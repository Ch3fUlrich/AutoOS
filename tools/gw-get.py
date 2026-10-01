import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_common import load_agent_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key = load_agent_module().client_key(ROOT)
base = "http://127.0.0.1:20128"
for path in sys.argv[1:] or ["/api/circuit-breakers"]:
    req = urllib.request.Request(base + path,
                                 headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
        print("== %s (%d bytes) ==" % (path, len(body)))
        print(body[:3000])
    except urllib.error.HTTPError as e:
        print("== %s -> HTTP %d ==" % (path, e.code))
        print(e.read(500).decode("utf-8", "replace"))
    except Exception as e:
        print("== %s -> %s ==" % (path, type(e).__name__))
