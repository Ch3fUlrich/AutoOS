#!/usr/bin/env python3
"""v-activate-tarball-integrity.py — certify the local pristine tarball against the
npm registry's published integrity for omniroute@3.8.50.

Computes sha512 (base64, the SRI form npm publishes) and sha1 of the local tarball so
the value can be compared, by eye, with `npm view omniroute@3.8.50 dist.integrity
dist.shasum`.
"""
import base64
import hashlib
import os

TARBALL = r"C:\Users\mauls\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz"
REGISTRY_INTEGRITY = "sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg=="
REGISTRY_SHASUM = "d7b4fce4f1b00e5e826b76855665dfae42aab97a"


def main():
    h5, h1 = hashlib.sha512(), hashlib.sha1()
    with open(TARBALL, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h5.update(b)
            h1.update(b)
    sri = "sha512-" + base64.b64encode(h5.digest()).decode()
    sha1 = h1.hexdigest()
    print(f"tarball  : {TARBALL}")
    print(f"size     : {os.path.getsize(TARBALL)} B")
    print(f"computed : {sri}")
    print(f"registry : {REGISTRY_INTEGRITY}")
    print(f"sha512 match: {sri == REGISTRY_INTEGRITY}")
    print(f"computed sha1: {sha1}")
    print(f"registry shasum: {REGISTRY_SHASUM}")
    print(f"sha1 match: {sha1 == REGISTRY_SHASUM}")
    return 0 if sri == REGISTRY_INTEGRITY and sha1 == REGISTRY_SHASUM else 1


if __name__ == "__main__":
    raise SystemExit(main())
