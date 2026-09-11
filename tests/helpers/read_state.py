#!/usr/bin/env python3
"""Read a claude-sessions state document and print one assertable line.

Reads stdin unless a path is given, so it works both on a snapshot's stdout and
on the file it wrote.

    read_state.py summary [path]   -> "<count>|<cwd0>|<uuid0>|<cwd1>"  (cwd-sorted)
    read_state.py cwds    [path]   -> "<cwd>|<cwd>|..."  (in document order)
    read_state.py count   [path]   -> "<count>"
"""
import json
import sys


def main():
    mode = sys.argv[1]
    doc = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else json.load(sys.stdin)
    sessions = doc.get("sessions", [])

    if mode == "count":
        print(len(sessions))
    elif mode == "cwds":
        print("|".join(s.get("cwd", "") for s in sessions))
    elif mode == "summary":
        ordered = sorted(sessions, key=lambda s: s.get("cwd", ""))
        parts = [str(len(ordered))]
        if ordered:
            parts += [ordered[0].get("cwd", ""), ordered[0].get("session_uuid", "")]
        if len(ordered) > 1:
            parts.append(ordered[1].get("cwd", ""))
        print("|".join(parts))
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
