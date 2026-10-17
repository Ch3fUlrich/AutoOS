#!/usr/bin/env python3
"""v-activate-backup-table.py — for every patched file, list its backups and whether
any of them is byte-identical to the published 3.8.50 tarball (a certified revert path).

Reads the tarball members in memory (tarfile) — no Temp extraction.
"""
import hashlib
import json
import os
import sys
import tarfile

TARBALL = os.path.join(os.environ.get("TEMP", ""), "opencode", "packbackups", "omniroute-3.8.50.tgz")
LIVE = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "omniroute")

# The 19 files found differing from the tarball by verify-patch-inventory.py.
FILES = [
    "dist/.build/next/server/chunks/_04g0p_r._.js",
    "dist/.build/next/server/chunks/_08_y1bx._.js",
    "dist/.build/next/server/chunks/_0o50usg._.js",
    "dist/.build/next/server/chunks/_0o8_5h8._.js",
    "dist/.build/next/server/chunks/_0t1t5fj._.js",
    "dist/.build/next/server/chunks/_0wr-zm3._.js",
    "dist/.build/next/server/chunks/_15ose6x._.js",
    "dist/.build/next/server/chunks/_18ct13i._.js",
    "dist/.build/next/server/chunks/_1j_edf1._.js",
    "dist/.build/next/server/chunks/_1luyz1c._.js",
    "dist/.build/next/server/chunks/_1mq9y97._.js",
    "dist/.build/next/server/chunks/_1xkpq2s._.js",
    "dist/open-sse/mcp-server/server.js",
    "open-sse/config/providers/registry/scaleway/index.ts",
    "open-sse/services/contextManager.ts",
    "open-sse/translator/index.ts",
    "open-sse/translator/paramSupport.ts",
    "open-sse/translator/request/openai-responses/toResponses.ts",
    "open-sse/translator/request/openai-to-gemini.ts",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def main():
    tar = {}
    with tarfile.open(TARBALL, "r:gz") as tf:
        for rel in FILES:
            m = tf.getmember("package/" + rel)
            h = hashlib.sha256()
            with tf.extractfile(m) as fh:
                for b in iter(lambda: fh.read(1 << 20), b""):
                    h.update(b)
            tar[rel] = {"sha256": h.hexdigest().upper(), "size": m.size}

    rows = []
    for rel in FILES:
        live_path = os.path.join(LIVE, rel.replace("/", os.sep))
        d, base = os.path.dirname(live_path), os.path.basename(live_path)
        backs = []
        for fn in sorted(os.listdir(d)):
            if fn.startswith(base + ".autoos-backup-"):
                bp = os.path.join(d, fn)
                bh = sha256(bp)
                backs.append({"file": fn, "size": os.path.getsize(bp), "sha256": bh,
                              "equals_tarball": bh == tar[rel]["sha256"]})
        rows.append({
            "path": rel,
            "tar_sha256": tar[rel]["sha256"], "tar_size": tar[rel]["size"],
            "live_sha256": sha256(live_path), "live_size": os.path.getsize(live_path),
            "patched": True,
            "backups": backs,
            "certified": any(b["equals_tarball"] for b in backs),
        })

    print(f"{'file':<70} {'patched':>7} {'certified':>9}  backups")
    for r in rows:
        name = r["path"].replace("dist/.build/next/server/chunks/", "…chunks/")
        print(f"{name:<70} {'yes':>7} {'YES' if r['certified'] else 'NO':>9}")
        for b in r["backups"]:
            print(f"    {b['file']:<72} size={b['size']:>9} ==tarball={b['equals_tarball']}")
    unc = [r["path"] for r in rows if not r["certified"]]
    print(f"\npatched files: {len(rows)}   certified: {len(rows)-len(unc)}   "
          f"lacking certified backup: {len(unc)}")
    for p in unc:
        print(f"  {p}")
    out = os.path.join(os.environ["TEMP"], "opencode", "v-activate", "backup-table.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
