#!/usr/bin/env python3
"""Runner-private run records and worker-record readers (AO-SPAWNER-REMOVE S1a).

Moved unchanged out of tools/autoos-agent.py so later slices can delete the
spawner without breaking their callers: the decided-at-spawn kill store
(`kill_store_dir` / `kill_store_path` / `write_kill_record` / `read_kill_record`)
and the spawned-worker records (`workers_dir` / `worker_writer`). autoos-agent.py
re-exports every name, so its callers and the by-path loaders keep working.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autoos_clients as clients  # noqa: E402

# The checkout root, derived the way this repo's tools/ modules each derive their
# own (autoos-agent.py keeps its own ROOT for its own callers).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _iso_zulu(moment) -> str:
    return moment.astimezone(datetime.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def kill_store_dir(state: str | None = None) -> str:
    """The runner-private home of each run's decided-at-spawn record (SB-A3,
    D-103 item C; the run MODE joined it in SB-A4).

    Deliberately NOT `AUTOOS_TASK_DIR`: the run dir is handed to the worker as an
    environment variable, the worker is the same uid as everything in it, and a
    kill record the cancelled process can rewrite is a channel it gives orders
    through — which is what SB-A's `pgrp.json` and SB-A2's `job.json` group were.
    This is a sibling of the `agents/` tree, 0700 with 0600 files, written by the
    server and the runner and by nobody else.

    RESIDUAL, stated rather than papered over: a same-uid worker that goes looking
    can still find this directory (the state dir is in its environment) and write
    into it, because a file cannot be hidden from the uid that owns it. What
    closes that hole is the scope path, which is derived from the run id and
    depends on no file at all; the private store is only the fallback for a host
    with no user manager.

    SB-B merge: this moved here from the fleet server, because the CLI is the
    process that starts and re-starts the client — an attempt's group has to reach
    the same record from inside `run_client`, and a second copy of a store is a
    second place a killer's answer can come from. The server reads and writes it
    through this one implementation.

    `state` overrides the state root for a reader handed one (a recovery plan
    pointed at another tree): the store is always <root>/kill, never a second layout.
    """
    return os.path.join(state or clients.state_dir(), "kill")


def kill_store_path(run_id: str, state: str | None = None) -> str:
    """The one record file for `run_id`, named so no run id escapes the store."""
    name = os.path.basename(os.path.normpath(str(run_id or "")))
    if not name or name.startswith(".") or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("bad run id %r" % (run_id,))
    return os.path.join(kill_store_dir(state), name + ".json")


def write_kill_record(run_id: str, record: dict) -> bool:
    """Write (or merge into) the runner's private record for one run.

    SB-A4 (D-103, the rest of item C) grew this from a group record into the run's
    whole decided-at-spawn identity: `run_id`, `mode` (review|write), `scope`,
    `dry_run`, `allow_mode_only`, the spawning `lane` (AO-JOB-LANE-ID),
    `created_at`, plus the `pgid`/`start` group record and the RUNMODEL `writer`
    the run resolves at its end. It is the only
    place any of that is read from, because everything a killer or a dispatcher
    decides must come from a record the worker cannot rewrite through its own
    `job.json`.

    Merge semantics are deliberately asymmetric. The group is re-recorded by the
    runner in `run_job` and by the spawner for every client attempt it launches
    (`record_attempt_group`), so `pgid`/`start` are replaced whenever they are
    given. The decided-at-spawn fields are IMMUTABLE: a later write cannot
    re-decide the mode even by accident, and `created_at`/`run_id` are stamped on
    the record's first write only.
    """
    if os.name == "nt" or not record:
        return False
    try:
        path = kill_store_path(run_id)
        directory = kill_store_dir()
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        existing = read_kill_record(run_id) or {}
        merged = dict(existing)
        merged.setdefault("run_id", run_id)
        merged.setdefault("created_at", _iso_zulu(datetime.datetime.now(
            datetime.timezone.utc)))
        for key in ("pgid", "start", "writer", "attempt", "outside_writes",
                    "toolhome_bytes_before", "toolhome_bytes_after"):
            if record.get(key) is not None:
                merged[key] = record[key]
        for key in ("mode", "scope", "dry_run", "allow_mode_only", "lane", "item_key"):
            if record.get(key) is not None:
                merged.setdefault(key, record[key])
        tmp = "%s.tmp-%d" % (path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with io.open(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        print("autoos-agent: no kill record for %s: %s" % (run_id, exc),
              file=sys.stderr)
        return False
    return True


def read_kill_record(run_id: str, state: str | None = None):
    """The private record, or None. A run with no record is a run whose group and
    mode were never decided by a server — the direct CLI path — and a killer must
    say so rather than kill what it cannot identify. `state` reads the store of
    that state root instead of the ambient one."""
    try:
        path = kill_store_path(run_id, state)
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def workers_dir() -> str:
    override = os.environ.get("AUTOOS_WORKERS_DIR")
    if override:
        path = override
    else:
        base = None
        try:
            out = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                                  "--git-common-dir"],
                                 cwd=ROOT, capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL)
            common = out.stdout.strip()
            if out.returncode == 0 and common:
                base = os.path.dirname(common)
        except (OSError, subprocess.SubprocessError):
            base = None
        path = os.path.join(base or ROOT, "logs", "workers")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def worker_writer(run_id):
    """The writer the RUNNER recorded for `run_id`, or None when nothing was.

    The runner-private kill store and nothing else (R-orch-17): a `ps` row is read
    by whoever is deciding whether a review is independent, and both the worker
    record and job.json live in directories the worker itself can write. A run with
    no record — one started by hand, one older than the store — answers None rather
    than falling back to the ask.
    """
    if not run_id:
        return None
    writer = (read_kill_record(run_id) or {}).get("writer")
    return writer if isinstance(writer, dict) else None
