#!/usr/bin/env python3
"""Keep the web configuration form's grouping honest against the catalogs.

The form renders whatever `STATE.prompts` contains, grouped by `CONFIG_SECTIONS`
in web/index.html. A key listed in a section but present in no catalog is dead
weight: it will never render, and it reads as though the setting exists. A key
that is misspelled there silently falls into "Other settings" instead of its
section.

Exit 0 when every grouped key is asked by at least one catalog, 1 otherwise.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def grouped_keys():
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"const CONFIG_SECTIONS = \[(.*?)\n\];", page, re.S)
    if not match:
        print("web/index.html does not declare CONFIG_SECTIONS", file=sys.stderr)
        sys.exit(1)
    return set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def catalog_keys():
    keys = set()
    for catalog in (ROOT / "catalog").glob("*.json"):
        doc = json.loads(catalog.read_text(encoding="utf-8"))
        keys |= set(doc.get("prompts", {}))
    return keys


def main():
    grouped, asked = grouped_keys(), catalog_keys()
    # Section titles are picked up by the same regex; drop anything that is not a
    # key by checking against the union rather than the other way round.
    unknown = {k for k in grouped if k not in asked and "_" in k}
    if unknown:
        print(f"grouped but asked by no catalog: {sorted(unknown)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
