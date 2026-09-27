#!/usr/bin/env python3
"""Ask-back for a spawned agent worker (routing v2 spec section 9): block
until the orchestrator answers a decision the worker cannot make itself.

The autoos-agent MCP server runs every spawned task with
AUTOOS_TASK_DIR=<run dir> (tools/autoos_agent_mcp.py run_job; the CLI
forwards its own environment to the client, so the worker sees it too). A
worker's brief tells it to run this command whenever it is blocked:

    python3 tools/autoos-ask.py "ship the fix or revert it?"

Files inside the run dir, all small JSON written atomically (tmp file +
os.replace, so a reader never sees a half-written file):

    question.json  {"text", "asked"}    written here; while it exists with no
                                        answer.json the run's state is
                                        input_required (spec section 9)
    answer.json    {"text", "answered"} written by the MCP tool
                                        respond(run_id, text); until then this
                                        helper polls for it
    qa-<n>.json    {"question", "answer"} the archived pair, n the next free
                                        number - the history of every exchange
                                        is kept

On an answer the text goes to stdout and the exit code is 0. On timeout the
question is withdrawn (the run returns to working) and the exit code is 3.
Misuse - no AUTOOS_TASK_DIR, a question already pending, an empty question -
exits 2 with the reason on stderr.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys
import time

QUESTION_FILE = "question.json"
ANSWER_FILE = "answer.json"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _read_json(path: str):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a number" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("%r must be positive" % text)
    return value


def _next_qa_path(task_dir: str) -> str:
    n = 1
    while os.path.exists(os.path.join(task_dir, "qa-%d.json" % n)):
        n += 1
    return os.path.join(task_dir, "qa-%d.json" % n)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask the orchestrator a question and wait for its answer.")
    parser.add_argument("question", help="the decision you are blocked on")
    parser.add_argument("--timeout", type=_positive, default=1800.0, metavar="SECS",
                        help="give up after this many seconds (default 1800)")
    parser.add_argument("--poll", type=_positive, default=5.0, metavar="SECS",
                        help="seconds between answer checks (default 5)")
    args = parser.parse_args(argv)

    task_dir = os.environ.get("AUTOOS_TASK_DIR")
    if not task_dir:
        print("AUTOOS_TASK_DIR is not set: ask-back needs the run directory "
              "the autoos-agent spawner exports to its workers.", file=sys.stderr)
        return 2
    if not os.path.isdir(task_dir):
        print("AUTOOS_TASK_DIR %s is not a directory" % task_dir, file=sys.stderr)
        return 2
    question = (args.question or "").strip()
    if not question:
        print("the question is empty", file=sys.stderr)
        return 2
    qpath = os.path.join(task_dir, QUESTION_FILE)
    apath = os.path.join(task_dir, ANSWER_FILE)
    if os.path.exists(qpath) or os.path.exists(apath):
        print("a question is already pending in %s" % task_dir, file=sys.stderr)
        return 2

    _write_json(qpath, {"text": question, "asked": _now_iso()})
    deadline = time.monotonic() + args.timeout
    while True:
        # a present-but-unreadable answer.json counts as not there yet: keep
        # polling (only respond() writes it, atomically, but stay tolerant).
        answer = _read_json(apath)
        if isinstance(answer, dict):
            print(answer.get("text", ""))
            _write_json(_next_qa_path(task_dir),
                        {"question": _read_json(qpath), "answer": answer})
            for path in (qpath, apath):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(min(args.poll, max(deadline - time.monotonic(), 0.0)))

    try:
        os.remove(qpath)  # withdraw the question: the run goes back to working
    except OSError:
        pass
    print("no answer within %g s" % args.timeout, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
