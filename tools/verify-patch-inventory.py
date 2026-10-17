#!/usr/bin/env python3
"""verify-patch-inventory.py — independent patch inventory for the live omniroute install.

Lane: V-activate (verification)  |  2026-09-30

Compares EVERY member of the vendor tarball `omniroute-3.8.50.tgz` against the live
npm-global install, streaming members out of the tarball in memory (tarfile) so no
temporary extraction is used (this host deletes Temp extractions).

For every file that differs (i.e. is patched) or is missing, it reports:
  - tarball sha256 / size, live sha256 / size
  - whether a sibling `*.autoos-backup-pristine-3.8.50-*` exists and whether its
    sha256 equals the tarball (certified backup)

Usage: python verify-patch-inventory.py [--json OUT]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tarfile

TARBALL = os.path.join(os.environ.get("TEMP", ""), "opencode", "packbackups", "omniroute-3.8.50.tgz")
LIVE = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "omniroute")
PREFIX = "package/"
SKIP_PREFIXES = (PREFIX + "node_modules/",)
BACKUP_RE = re.compile(r"\.autoos-backup-pristine-3\.8\.50-\d{8}-\d{6}$")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest().upper()


def sha256_stream(fh, chunk=1 << 20):
    h = hashlib.sha256()
    while True:
        b = fh.read(chunk)
        if not b:
            break
        h.update(b)
    return h.hexdigest().upper()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if not os.path.isfile(TARBALL):
        print(f"FATAL: tarball missing: {TARBALL}")
        return 2
    print(f"tarball : {TARBALL}")
    print(f"         size={os.path.getsize(TARBALL)}  sha1={sha1(TARBALL)}")
    print(f"live    : {LIVE}")
    print()

    rows = []
    missing = []
    members = 0
    with tarfile.open(TARBALL, "r:gz") as tf:
        for m in tf:
            if not m.isfile():
                continue
            name = m.name
            if not name.startswith(PREFIX):
                continue
            if name.startswith(SKIP_PREFIXES):
                continue
            members += 1
            rel = name[len(PREFIX):]
            live_path = os.path.join(LIVE, rel.replace("/", os.sep))
            with tf.extractfile(m) as fh:
                tar_hash = sha256_stream(fh)
            tar_size = m.size
            if not os.path.isfile(live_path):
                missing.append({"path": rel, "tar_hash": tar_hash, "tar_size": tar_size})
                continue
            live_size = os.path.getsize(live_path)
            live_hash = sha256_file(live_path)
            if live_hash != tar_hash or live_size != tar_size:
                rows.append({
                    "path": rel,
                    "tar_hash": tar_hash, "tar_size": tar_size,
                    "live_hash": live_hash, "live_size": live_size,
                })

    # Certified-backup check for every differing file.
    for r in rows:
        live_path = os.path.join(LIVE, r["path"].replace("/", os.sep))
        d = os.path.dirname(live_path)
        base = os.path.basename(live_path)
        cands = []
        for fn in sorted(os.listdir(d)):
            if fn.startswith(base + ".autoos-backup-pristine-3.8.50-") and BACKUP_RE.search(fn):
                cands.append(os.path.join(d, fn))
        cert = []
        for c in cands:
            cert.append({
                "file": os.path.basename(c),
                "size": os.path.getsize(c),
                "sha256": sha256_file(c),
                "equals_tarball": sha256_file(c) == r["tar_hash"],
            })
        r["pristine_backups"] = cert
        r["certified"] = any(c["equals_tarball"] for c in cert)

    print(f"members compared (excl. package/node_modules): {members}")
    print(f"differing (patched): {len(rows)}")
    print(f"missing under live:  {len(missing)}")
    print()
    print(f"{'file':<78} {'tar':>10} {'live':>10}  certified")
    for r in sorted(rows, key=lambda x: x["path"]):
        print(f"{r['path']:<78} {r['tar_size']:>10} {r['live_size']:>10}  "
              f"{'YES' if r['certified'] else 'NO'}")
        for c in r["pristine_backups"]:
            print(f"    backup {c['file']}  size={c['size']}  ==tarball={c['equals_tarball']}")
    if missing:
        print("\nMISSING under live:")
        for m in missing:
            print(f"  {m['path']} (tar size {m['tar_size']})")

    uncertified = [r for r in rows if not r["certified"]]
    print()
    print(f"PATCHED FILES LACKING A CERTIFIED PRISTINE BACKUP: {len(uncertified)}")
    for r in uncertified:
        print(f"  {r['path']}  (live size {r['live_size']}, tar size {r['tar_size']})")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "tarball": TARBALL, "tarball_sha1": sha1(TARBALL),
                "tarball_size": os.path.getsize(TARBALL),
                "live": LIVE, "members_compared": members,
                "differing": rows, "missing": missing,
                "uncertified": [r["path"] for r in uncertified],
            }, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


def sha1(path, chunk=1 << 20):
    import hashlib as _h
    h = _h.sha1()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
