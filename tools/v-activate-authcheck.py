#!/usr/bin/env python3
"""v-activate-authcheck.py — does the isolated gateway need a key for chat completions?

Never prints the key. Sends the same minimal request keyless and keyed and reports
status + a short body prefix, plus whether the `omniroute:` entry was found in the
repo keys file (it is git-ignored).
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keys_file import read_keys  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = os.path.join(ROOT, "configuration", "api-keys.yml")
GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20145")


def post(url, payload, key=""):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as ex:  # noqa: BLE001
        return -1, str(ex)[:300]


def main():
    keys = read_keys(KEYS)
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY") or keys.get("omniroute", "")
    print(f"keys file      : {KEYS}   (exists={os.path.isfile(KEYS)})")
    print(f"keys present   : {sorted(keys)}")
    print(f"omniroute entry: {'FOUND len=%d' % len(key) if key else 'ABSENT'}")
    print(f"gateway        : {GATEWAY}")
    payload = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "hi"}],
               "max_tokens": 8}
    for label, k in (("keyless", ""), ("keyed", key)):
        st, body = post(GATEWAY + "/v1/chat/completions", payload, k)
        print(f"chat {label:8s}: status={st} body={body!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
