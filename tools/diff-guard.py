#!/usr/bin/env python3
"""Report-only post-run diff guard over a git revision range (rules R0..R5).

Usage:
    python3 tools/diff-guard.py --base <rev> --tip <rev> --scope <glob>
        [--scope <glob> ...] [--repo <dir>] [--json]
    python3 tools/diff-guard.py --writer-rule

Exit codes: 0 clean (no findings), 1 findings, 2 usage or git error.
`--writer-rule` prints the reviewed writer rule (diff_guard.WRITER_RULE)
plus one newline and exits 0 before any other argument is required.
Outputs one `RULE path:line text` line per finding (text clipped to 160
chars, never wrapped) or, with --json, a list of {rule, path, line, text}.
It only reads: the guard never edits anything. The rules are heuristics
that can produce false positives - the reviewer judges. See
docs/ai/diff-guard.md and docs/ai/writer-contract.md.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diff_guard import GitError, WRITER_RULE, analyze  # noqa: E402  (sibling module)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # --writer-rule short-circuits the parser: it must print the reviewed text
    # with no --base/--tip/--scope supplied, so the required-argument check
    # never runs for it. Every other invocation parses exactly as before
    # (missing arguments still exit 2 with argparse's own message).
    if "--writer-rule" in argv:
        print(WRITER_RULE)
        return 0

    parser = argparse.ArgumentParser(
        prog="diff-guard.py",
        description="report-only diff guard over a git revision range (R0-R5)",
    )
    parser.add_argument("--base", required=True, metavar="REV",
                        help="base revision (inclusive lower bound of the diff)")
    parser.add_argument("--tip", required=True, metavar="REV",
                        help="tip revision (the state being checked)")
    parser.add_argument("--scope", required=True, action="append", metavar="GLOB",
                        help="path glob the change is allowed in; repeatable")
    parser.add_argument("--repo", default=".", metavar="DIR",
                        help="git repository to inspect (default: cwd)")
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON list instead of RULE lines")
    parser.add_argument("--writer-rule", action="store_true",
                        help="print the reviewed writer rule and exit 0 "
                             "(handled before parsing: needs no other argument)")
    args = parser.parse_args(argv)

    try:
        findings = analyze(args.base, args.tip, args.scope, repo=args.repo)
    except GitError as exc:
        print("diff-guard: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(findings, ensure_ascii=True))
    else:
        for finding in findings:
            print("%s %s:%d %s" % (
                finding["rule"], finding["path"],
                finding["line"], finding["text"],
            ))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
