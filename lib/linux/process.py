#!/usr/bin/env python3
"""Drain installer output, report honest progress, and bound the owned process."""
import json
import codecs
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time


def snapshot(event, started, percent=None):
    if not event:
        return
    event = dict(event, elapsedSeconds=int(time.monotonic() - started), percent=percent,
                 phase="installing")
    if os.environ.get("AUTOOS_PROGRESS_EVENTS") == "1":
        print("@@AUTOOS_PROGRESS " + json.dumps(event), flush=True)
    else:
        overall = int(100 * event["done"] / max(1, event["total"]))
        filled = overall // 5
        print(f"Overall [{'#' * filled}{'-' * (20-filled)}] {event['done']}/{event['total']} ({overall}%)", flush=True)
        bar = "[     working      ] percentage unavailable" if percent is None else f"[{'#' * (percent//5)}{'-' * (20-percent//5)}] {percent}%"
        print(f"Current {event['name']}: {bar} - installing ({event['elapsedSeconds']}s)", flush=True)


def run(args):
    try:
        limit = int(os.environ.get("AUTOOS_INSTALL_TIMEOUT_SECONDS", "1800"))
        if not 1 <= limit <= 86400:
            raise ValueError()
    except ValueError:
        print("AUTOOS_INSTALL_TIMEOUT_SECONDS must be between 1 and 86400.", file=sys.stderr)
        return 2
    event = json.loads(os.environ.get("AUTOOS_CURRENT_PROGRESS", "{}"))
    started = time.monotonic() - event.get('elapsedSeconds', 0)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    chunks = queue.Queue(maxsize=128)

    def read():
        try:
            while True:
                data = proc.stdout.read1(4096)
                if not data:
                    break
                chunks.put(data)
        finally:
            chunks.put(None)

    threading.Thread(target=read, daemon=True).start()
    pending, percent, next_update = "", None, 0.0
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    exit_seen = None
    try:
        while True:
            now = time.monotonic()
            if now - started >= limit and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2)
                print(f"Installer exceeded {limit}s. Run stopped; inspect logs before retrying.", file=sys.stderr, flush=True)
                return 124
            if proc.poll() is not None:
                exit_seen = exit_seen or now
                if now - exit_seen > 2:
                    break  # A detached descendant must not hold the pipe forever.
            if now >= next_update:
                snapshot(event, started, percent)
                next_update = now + 5
            try:
                chunk = chunks.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                if proc.poll() is not None:
                    break
                continue
            pending += decoder.decode(chunk)
            lines = re.split(r"[\r\n]", pending)
            pending = lines.pop()[-8192:]
            for line in lines:
                line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
                if not line.strip():
                    continue
                print("  " + line, flush=True)
                match = re.search(r"(?<!\d)(100|\d{1,2})(?:\.\d+)?\s*%", line)
                if match:
                    percent = int(match[1])
                    snapshot(event, started, percent)
        if pending:
            print("  " + pending, flush=True)
        return proc.wait(timeout=2)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.stdout.close()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
