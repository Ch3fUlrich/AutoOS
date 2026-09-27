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
                                        is kept. A stale entry (an answer that
                                        arrived after its question timed out,
                                        or an orphan answer with no question)
                                        carries "stale": true and a null
                                        question - never silently deleted.

"Pending" means question.json exists. An answer.json with no question.json is
stale (left by a timeout the previous run hit); it is archived at the start
of the next ask and the new question proceeds.

Exit codes:
    0  answered - the answer text went to stdout
    2  misuse   - no AUTOOS_TASK_DIR, a question already pending, empty question
    3  timeout  - no answer within --timeout; question withdrawn, run back to working
    5  io       - cannot write into AUTOOS_TASK_DIR (an --isolate fence, a
                  read-only mount, a full disk); reason on stderr, no traceback
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


def _fail_io(reason: str) -> int:
    task_dir = os.environ.get("AUTOOS_TASK_DIR", "$AUTOOS_TASK_DIR")
    print("autoos-ask: cannot write the question into %s (%s) - ask-back is "
          "unavailable in this run (an --isolate fence?); decide yourself and "
          "say so in your report" % (task_dir, reason), file=sys.stderr)
    return 5


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


def _archive_stale_answer(task_dir: str, apath: str) -> None:
    """An answer.json with no question.json is stale - archive it as qa-<n>.json
    with a 'stale' marker. Never silently delete an answer."""
    answer = _read_json(apath)
    if not isinstance(answer, dict):
        return
    try:
        _write_json(_next_qa_path(task_dir),
                    {"question": None, "answer": answer, "stale": True})
        os.remove(apath)
    except OSError:
        pass  # best effort; the main flow will hit the same wall and exit 5


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

    # "pending" means question.json exists. An orphan answer.json is stale
    # (left by a timeout the previous run hit); archive it and proceed.
    if os.path.exists(qpath):
        print("a question is already pending in %s" % task_dir, file=sys.stderr)
        return 2
    if os.path.exists(apath):
        _archive_stale_answer(task_dir, apath)

    try:
        _write_json(qpath, {"text": question, "asked": _now_iso()})
    except OSError as exc:
        return _fail_io(str(exc))

    deadline = time.monotonic() + args.timeout
    while True:
        # a present-but-unreadable answer.json counts as not there yet: keep
        # polling (only respond() writes it, atomically, but stay tolerant).
        answer = _read_json(apath)
        if isinstance(answer, dict):
            print(answer.get("text", ""))
            try:
                _write_json(_next_qa_path(task_dir),
                            {"question": _read_json(qpath), "answer": answer})
                for path in (qpath, apath):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            except OSError as exc:
                return _fail_io(str(exc))
            return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(min(args.poll, max(deadline - time.monotonic(), 0.0)))

    # timeout: withdraw the question; if an answer landed at/after the
    # deadline, archive it as stale (never leave answer.json behind - the
    # next ask would see it and refuse "already pending" forever).
    try:
        os.remove(qpath)
    except OSError:
        pass
    if os.path.exists(apath):
        _archive_stale_answer(task_dir, apath)
    print("no answer within %g s" % args.timeout, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
