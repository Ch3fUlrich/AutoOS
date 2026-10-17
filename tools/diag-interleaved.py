#!/usr/bin/env python3
"""Diagnose the interleaved_field for deepseek models — the key to whether
the gateway's empty-reasoning injection fires or is skipped."""
import json
import os
import sqlite3
import sys
import urllib.request

HOME = os.path.expanduser("~/.omniroute")


def query_db():
    """Query the local omniroute SQLite DB for interleaved_field."""
    for dbpath in [os.path.join(HOME, f) for f in os.listdir(HOME)
                   if f.endswith(".db")]:
        try:
            conn = sqlite3.connect(dbpath)
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for t in tables:
                cols = [c[1] for c in cur.execute(
                    f"PRAGMA table_info('{t}')").fetchall()]
                if "interleaved_field" in cols:
                    print(f"DB: {dbpath} table={t} has interleaved_field")
                    rows = cur.execute(
                        f"SELECT * FROM {t} WHERE model LIKE '%deepseek%'"
                    ).fetchall()
                    for r in rows:
                        print(f"  row: {r}")
            conn.close()
        except Exception as e:
            print(f"DB error {dbpath}: {e}", file=sys.stderr)


def query_models_dev():
    """Query models.dev API for deepseek-flash capabilities."""
    try:
        req = urllib.request.Request(
            "https://models.dev/models.json",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"models.dev fetch error: {e}", file=sys.stderr)
        return

    for model in data if isinstance(data, list) else data.get("data", []):
        mid = model.get("id", "") or model.get("name", "")
        if "deepseek" in mid.lower() and ("flash" in mid.lower()
                                          or "v4" in mid.lower()):
            interleaved = model.get("interleaved")
            print(f"models.dev: id={mid}")
            print(f"  interleaved={interleaved}")
            print(f"  reasoning={model.get('reasoning')}")
            print(f"  tool_call={model.get('tool_call')}")


def query_gateway():
    """Query the gateway's /v1/models for deepseek capabilities."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:20128/v1/models",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"gateway fetch error: {e}", file=sys.stderr)
        return

    models = data.get("data", data) if isinstance(data, dict) else data
    for m in models if isinstance(models, list) else []:
        mid = m.get("id", "")
        if "deepseek" in mid.lower() and "flash" in mid.lower():
            caps = m.get("capabilities", {})
            print(f"gateway: id={mid}")
            print(f"  capabilities={json.dumps(caps)}")
            print(f"  interleaved_field={m.get('interleaved_field')}")


if __name__ == "__main__":
    print("=== Local DB ===")
    query_db()
    print("\n=== models.dev ===")
    query_models_dev()
    print("\n=== Gateway /v1/models ===")
    query_gateway()
