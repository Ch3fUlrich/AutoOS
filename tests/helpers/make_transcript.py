#!/usr/bin/env python3
"""Write one fake Claude Code transcript with a controlled mtime.

Shape-compatible with a real ~/.claude/projects/<slug>/<session-id>.jsonl: the
first line is a queue-operation record (which carries no cwd), the second is the
first real record (which does). Discovery must skip the former and read the
latter — that ordering is the point of the fixture, not an accident.

    echo '{"cwd": "/home/u/alpha", "uuid": "…"}' | make_transcript.py <path> <age-minutes>

`cwd` and `uuid` arrive on stdin rather than argv because Git Bash rewrites any
POSIX-looking argument into a Windows path on its way to a native python, which
would silently change the very value under test.
"""
import json
import os
import sys
import time


def main():
    path, age = sys.argv[1], int(sys.argv[2])
    spec = json.load(sys.stdin)
    cwd, uuid = spec["cwd"], spec["uuid"]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "queue-operation", "sessionId": uuid}) + "\n")
        fh.write(json.dumps({
            "sessionId": uuid, "cwd": cwd, "gitBranch": "main",
            "type": "user", "version": "2.1.268",
        }) + "\n")

    when = time.time() - age * 60
    os.utime(path, (when, when))


if __name__ == "__main__":
    main()
