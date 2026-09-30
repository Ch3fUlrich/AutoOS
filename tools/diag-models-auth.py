#!/usr/bin/env python3
"""List models from the gateway with auth."""
import os, json, urllib.request, urllib.error
GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20128")
API_KEY = os.environ.get("AUTOOS_OMNIROUTE_KEY", "")
headers = {}
if API_KEY:
    headers["Authorization"] = f"Bearer {API_KEY}"
req = urllib.request.Request(f"{GATEWAY}/v1/models", headers=headers, method="GET")
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    models = sorted([m["id"] for m in data.get("data", [])])
    for m in models:
        print(m)
    print(f"--- total: {len(models)} ---")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
except Exception as e:
    print(f"Error: {e}")
