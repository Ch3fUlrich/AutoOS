#!/usr/bin/env python3
"""Check tests/ci-shards.txt against tests/linux/ and print the CI shard matrix.

    python3 tests/ci-shards.py            check, then print {"include": [...]} for CI
    python3 tests/ci-shards.py --check    check only

Every tests/linux/NN-*.sh part must appear in exactly one shard; a part that is
missing, listed twice, or named but absent fails with exit 1 and names it. CI
runs this in its plan job, so a shard map that would drop a test silently never
reaches the shards (WS-CIPAR, 2026-09-28). Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARDS = ROOT / "tests" / "ci-shards.txt"
PARTS_DIR = ROOT / "tests" / "linux"


def parts_on_disk(parts_dir=PARTS_DIR) -> set[str]:
    return {p.name[:2] for p in parts_dir.glob("[0-9][0-9]-*.sh")}


def read_shards(path=SHARDS) -> list[tuple[str, list[str]]]:
    shards = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([a-z0-9-]+)\s+([0-9]{2}(?:,[0-9]{2})*)", line)
        if not m:
            raise ValueError("%s:%d: expected '<name> NN,NN,...', got %r" % (path.name, lineno, line))
        shards.append((m.group(1), m.group(2).split(",")))
    return shards


def problems(shards, on_disk) -> list[str]:
    out = []
    seen = {}
    names = set()
    for name, parts in shards:
        if name in names:
            out.append("shard %s is defined twice" % name)
        names.add(name)
        for part in parts:
            if part in seen:
                out.append("part %s is in shard %s and shard %s" % (part, seen[part], name))
            seen[part] = name
    for part in sorted(on_disk - set(seen)):
        out.append("part %s (tests/linux/%s-*.sh) is in no shard" % (part, part))
    for part in sorted(set(seen) - on_disk):
        out.append("shard %s names part %s, which does not exist" % (seen[part], part))
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        shards = read_shards()
    except (OSError, ValueError) as e:
        print("ci-shards: %s" % e, file=sys.stderr)
        return 1
    found = problems(shards, parts_on_disk())
    for p in found:
        print("ci-shards: %s" % p, file=sys.stderr)
    if found:
        return 1
    if "--check" not in argv:
        print(json.dumps({"include": [{"shard": n, "parts": ",".join(p)} for n, p in shards]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
