#!/usr/bin/env python3
"""v-activate-copy-datadir.py — build an isolated DATA_DIR for the V-activate probe run.

Copies ONLY the data-dir state the gateway needs:
  - `~/.omniroute/.env`   (the STORAGE_ENCRYPTION_KEY, 63 B)
  - `~/.omniroute/storage.sqlite` — via the sqlite3 backup API, so a live WAL writer
    (the shared gateway on :20128) cannot make the copy torn.

Used so the verification gateway does not open the shared DB/WAL.
"""
import os
import sqlite3
import sys

SRC = os.path.join(os.environ["USERPROFILE"], ".omniroute")
DST = os.path.join(os.environ["TEMP"], "opencode", "v-activate-iso")


def main():
    os.makedirs(DST, exist_ok=True)
    src_env = os.path.join(SRC, ".env")
    dst_env = os.path.join(DST, ".env")
    with open(src_env, "rb") as fh:
        env = fh.read()
    with open(dst_env, "wb") as fh:
        fh.write(env)
    print(f"copied .env: {len(env)} bytes -> {dst_env}")

    src_db = os.path.join(SRC, "storage.sqlite")
    dst_db = os.path.join(DST, "storage.sqlite")
    if os.path.exists(dst_db):
        os.remove(dst_db)
    s = sqlite3.connect(f"file:{src_db.replace(os.sep, '/')}?mode=ro", uri=True)
    d = sqlite3.connect(dst_db)
    s.backup(d)
    d.close()
    s.close()
    print(f"sqlite backup: {os.path.getsize(src_db)} B -> {os.path.getsize(dst_db)} B at {dst_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
