#!/usr/bin/env python3
"""Exit 0 when a catalog declares a component id, 1 when it does not.

Usage: catalog_has.py <catalog.json> <component-id>
"""
import json
import sys


def main():
    catalog = json.load(open(sys.argv[1], encoding="utf-8"))
    wanted = sys.argv[2]
    found = any(
        component["id"] == wanted
        for group in catalog["categories"]
        for component in group["components"]
    )
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
