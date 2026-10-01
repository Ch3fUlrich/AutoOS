#!/usr/bin/env python3
"""Query the omniroute storage.sqlite for deepseek model interleaved_field."""
import os
import sqlite3
import sys

DB = os.path.join(os.path.expanduser("~/.omniroute"), "storage.sqlite")

def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # List all tables
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables:", tables)

    # Find tables with interleaved_field column
    for t in tables:
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
        if "interleaved_field" in cols:
            print(f"\n=== {t} (has interleaved_field) ===")
            print(f"  columns: {cols}")
            # Query for deepseek models
            model_col = "model" if "model" in cols else "model_id" if "model_id" in cols else None
            if model_col:
                rows = cur.execute(
                    f"SELECT * FROM {t} WHERE {model_col} LIKE '%deepseek%' LIMIT 10"
                ).fetchall()
                for r in rows:
                    print(f"  {r}")
            else:
                print(f"  no model/model_id column found in {cols}")
                # Show all rows
                rows = cur.execute(f"SELECT * FROM {t} LIMIT 3").fetchall()
                for r in rows:
                    print(f"  sample: {r}")

    # Also check for a synced_capabilities or model_capabilities table
    for t in tables:
        if "cap" in t.lower() or "model" in t.lower() or "sync" in t.lower():
            cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
            print(f"\n=== {t} ===")
            print(f"  columns: {cols}")
            if "interleaved_field" in cols:
                rows = cur.execute(
                    f"SELECT * FROM {t} LIMIT 5"
                ).fetchall()
                for r in rows:
                    print(f"  {r}")

    conn.close()

if __name__ == "__main__":
    main()
