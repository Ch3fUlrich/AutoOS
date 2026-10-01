#!/usr/bin/env python3
"""Read-only gateway catalog check with the resolved client key.

GETs /v1/models (the same call apply.sh makes with the client key) and
reports only: the HTTP status, the model count, and whether the mission
leg id is listed. No secret, no body, no ids beyond the mission leg.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import DEFAULT_GATEWAY, load_agent_module  # noqa: E402


def main(argv=None) -> int:
    leg = (argv or ["deepseek/deepseek-flash"])[0]
    agent = load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("diag-models: no client key")
        return 3
    base = DEFAULT_GATEWAY.rsplit("/v1/", 1)[0]
    req = urllib.request.Request(
        base + "/v1/models",
        headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            print("models\tstatus=%d" % resp.status)
    except Exception as exc:  # noqa: BLE001 - status is the finding
        code = getattr(exc, "code", type(exc).__name__)
        print("models\tstatus=%s" % code)
        return 0
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
        ids = [m.get("id") for m in doc.get("data", []) if isinstance(m, dict)]
    except ValueError:
        print("models\tinvalid JSON body")
        return 0
    print("models\tcount=%d leg_listed=%s" % (len(ids), leg in ids))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
