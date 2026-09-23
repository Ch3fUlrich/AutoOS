#!/usr/bin/env python3
"""Public scrub scanner: report pattern hits without echoing content."""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def load_patterns(path):
    rules = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f.read().splitlines():
            if not raw.strip():
                continue
            if raw.strip().startswith("#"):
                continue
            if "\t" not in raw:
                continue
            name, pattern = raw.split("\t", 1)
            name = name.strip()
            pattern = pattern.strip()
            if not name:
                continue
            rules.append((name, re.compile(pattern)))
    return rules


def load_excludes(path):
    prefixes = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f.read().splitlines():
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            s = s.rstrip("/")
            if not s:
                continue
            prefixes.append(s)
    return prefixes


def is_excluded(rel_posix, prefixes):
    for p in prefixes:
        if rel_posix == p or rel_posix.startswith(p + "/"):
            return True
    return False


def scan_lines(lines, rules, display_path, out):
    found = False
    for lineno, line in enumerate(lines, start=1):
        for name, rx in rules:
            if rx.search(line):
                out.append(f"{display_path}:{lineno}: {name}")
                found = True
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--patterns",
        default=str(Path(__file__).parent / "patterns.txt"),
    )
    ap.add_argument("--git-tree", default=None)
    ap.add_argument("--exclude-file", default=None)
    ap.add_argument("paths", nargs="*", default=["."])
    args = ap.parse_args(argv)
    # Paths are reported as UTF-8 whatever the console code page is.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    rules = load_patterns(args.patterns)
    prefixes = load_excludes(args.exclude_file) if args.exclude_file else []
    cwd = os.getcwd()
    out_lines = []
    any_hit = False
    unreadable = False

    if args.git_tree is not None:
        rev = args.git_tree
        # -z: without it git C-quotes non-ASCII names, `git show` then fails,
        # and the file would be skipped as a silent false clean.
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--name-only", rev],
            cwd=cwd,
            capture_output=True,
        )
        if proc.returncode != 0:
            print("git ls-tree failed", file=sys.stderr)
            return 2
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True,
        ).stdout.strip()
        skip = {"scripts/public-scrub/patterns.txt"}
        if top:
            rel = os.path.relpath(os.path.abspath(args.patterns), top)
            skip.add(rel.replace(os.sep, "/"))
        for name in proc.stdout.decode("utf-8", errors="replace").split("\0"):
            if not name:
                continue
            if name in skip:
                continue
            if is_excluded(name, prefixes):
                continue
            show = subprocess.run(
                ["git", "show", f"{rev}:{name}"],
                cwd=cwd,
                capture_output=True,
            )
            if show.returncode != 0:
                print(f"{name}: unreadable", file=sys.stderr)
                unreadable = True
                continue
            text = show.stdout.decode("utf-8", errors="replace")
            if scan_lines(text.splitlines(), rules, name, out_lines):
                any_hit = True
    else:
        patterns_resolved = Path(args.patterns).resolve()

        def rel_posix_for(candidate):
            abs_p = os.path.abspath(candidate)
            rel = os.path.relpath(abs_p, cwd)
            return rel.replace(os.sep, "/")

        def under_git(candidate):
            parts = Path(os.path.abspath(candidate)).parts
            return ".git" in parts

        def should_skip_file(candidate):
            try:
                if Path(candidate).resolve() == patterns_resolved:
                    return True
            except OSError:
                pass
            return False

        def scan_file(candidate, display):
            nonlocal any_hit, unreadable
            if under_git(candidate):
                return
            if should_skip_file(candidate):
                return
            if is_excluded(rel_posix_for(candidate), prefixes):
                return
            try:
                with open(candidate, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                print(f"{display}: unreadable", file=sys.stderr)
                unreadable = True
                return
            if scan_lines(content.splitlines(), rules, display, out_lines):
                any_hit = True

        for given in args.paths:
            if not os.path.lexists(given):
                print(f"{given}: unreadable", file=sys.stderr)
                unreadable = True
                continue
            if os.path.isdir(given) and not os.path.islink(given):
                for dirpath, dirnames, filenames in os.walk(given):
                    dirnames[:] = [d for d in dirnames if d != ".git"]
                    if under_git(dirpath):
                        continue
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        scan_file(full, full)
            else:
                scan_file(given, given)

    for line in out_lines:
        print(line)
    # An unscanned file is never reported as clean.
    return 2 if unreadable else (1 if any_hit else 0)


if __name__ == "__main__":
    sys.exit(main())
