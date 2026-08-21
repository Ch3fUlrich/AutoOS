#!/usr/bin/env python3
"""Check that every relative Markdown link in the repo resolves.

Kept as a real script rather than a heredoc inside the test suite: escaping a
regex through bash into python is how the first version of this check ended up
with a syntax error and silently reported success for everything.

Usage:  python3 tests/check-links.py [repo-root]
Exit:   0 = all links resolve, 1 = at least one is broken
"""
from __future__ import annotations

import glob
import os
import re
import sys

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "#", "mailto:")


def markdown_files(root: str) -> list[str]:
    patterns = ["README.md", "AGENTS.md", "CLAUDE.md", "docs/*.md", "**/README.md"]
    found: set[str] = set()
    for pat in patterns:
        for path in glob.glob(os.path.join(root, pat), recursive=True):
            if os.path.isfile(path) and ".git" not in path.split(os.sep):
                found.add(os.path.relpath(path, root))
    return sorted(found)


def broken_links(root: str) -> list[str]:
    problems: list[str] = []
    for rel in markdown_files(root):
        base = os.path.dirname(os.path.join(root, rel))
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        for match in LINK.finditer(text):
            target = match.group(2).strip()
            if target.startswith(SKIP_PREFIXES):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, path))):
                problems.append(f"{rel}: [{match.group(1)}] -> {target}")
    return problems


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    problems = broken_links(root)
    total = len(markdown_files(root))
    if problems:
        print(f"{len(problems)} broken link(s) across {total} file(s):")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"all relative links resolve ({total} files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
