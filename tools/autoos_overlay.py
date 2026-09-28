"""The one machine-wide tool_calls overlay (measured.json) - OVERLAYHOME.

Every reader (autoos-agent.py route/run/spawn, autoos_agent_mcp.py,
propose_reprobe) and every writer (the tools/probe-*.py probes through
probe_common) resolves its path here, so there is exactly one overlay per
machine, not one per checkout.

INCIDENT 2026-09-28 12:5xZ: the overlay lived at <checkout>/logs/routing/
measured.json. The main checkout had never run a probe, so `route` skipped
every agentic leg as "tool_calls: ... unproven" and returned input_required -
it looked like a fleet-wide outage. Hence one path, a read-only fallback to the
old per-checkout file (never deleted), and a loud reason when none exists.

Path, first match wins:
  $AUTOOS_MEASURED_OVERLAY
  Windows: %LOCALAPPDATA%\\autoos\\measured.json
  else:    ${XDG_STATE_HOME:-~/.local/state}/autoos/measured.json
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import time

ENV_VAR = "AUTOOS_MEASURED_OVERLAY"


def default_path(environ=None, osname=None) -> str:
    """The machine-wide overlay path (see the module docstring for the order)."""
    env = os.environ if environ is None else environ
    name = os.name if osname is None else osname
    if env.get(ENV_VAR):
        # Expanded and made absolute: a raw "~/x" would create a literal "~"
        # directory, and a relative value a file wherever the caller stood.
        return os.path.abspath(os.path.expanduser(os.path.expandvars(env[ENV_VAR])))
    if name == "nt":
        base = env.get("LOCALAPPDATA") or os.path.join(
            env.get("USERPROFILE") or os.path.expanduser("~"), "AppData", "Local")
    else:
        base = env.get("XDG_STATE_HOME") or os.path.join(
            env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "autoos", "measured.json")


def legacy_path(root: str) -> str:
    """The pre-OVERLAYHOME per-checkout overlay: read as a fallback, never written."""
    return os.path.join(root, "logs", "routing", "measured.json")


def found(path: str, legacy: str | None) -> str | None:
    """The file a read uses: `path`, else an existing `legacy`, else None."""
    if os.path.isfile(path):
        return path
    if legacy and os.path.isfile(legacy):
        return legacy
    return None


def load(path: str, legacy: str | None = None, stream=None) -> dict:
    """The overlay at `path`, else at `legacy` (one note on `stream`), else {}."""
    used = found(path, legacy)
    if used is None:
        return {}
    if used != path:
        print("overlay: using legacy %s; run probe-toolcalls or move it to %s"
              % (used, path), file=stream if stream is not None else sys.stderr)
    with io.open(used, encoding="utf-8") as fh:
        return json.load(fh)


def save(path: str, overlay: dict) -> None:
    """Atomic write, mode 600: a temp file in the same directory, then os.replace."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".measured-", suffix=".json", dir=directory)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(overlay, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def locked(path: str):
    """Exclusive lock on <path>.lock for one read-modify-write (POSIX flock,
    Windows msvcrt byte lock). Never nest it for one path in one process."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path + ".lock", "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ~10 s; keep waiting
                    time.sleep(0.05)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


_MISSING = object()


def _apply_delta(current: dict, base: dict, updated: dict) -> None:
    """Replay into `current` what changed from `base` to `updated`, key by key,
    descending into dicts all three share - so two probes that each changed a
    different leg (or a different field of one model) both keep their change."""
    for key, value in updated.items():
        before = base.get(key, _MISSING)
        if value == before:
            continue
        now = current.get(key)
        if before is _MISSING and isinstance(value, dict):
            before = {}  # new to this writer: merge into what is there, if a dict
        if isinstance(value, dict) and isinstance(before, dict) and isinstance(now, dict):
            _apply_delta(now, before, value)
        else:
            current[key] = copy.deepcopy(value)
    for key in base:
        if key not in updated:
            current.pop(key, None)


def merge_save(path: str, updated: dict, base: dict, legacy: str | None = None,
               stream=None) -> None:
    """Locked read-modify-write: re-read the overlay (`legacy` seeds a first
    write, as in `load`), apply this writer's changes from `base` to `updated`,
    save atomically. Writers that ran at the same time lose nothing."""
    with locked(path):
        current = load(path, legacy, stream)
        _apply_delta(current, base or {}, updated)
        save(path, current)


def status(path: str, legacy: str | None, now: float | None = None) -> dict:
    """{path, present, age_hours} for `heartbeat --json`; path is the file a read uses."""
    used = found(path, legacy)
    if used is None:
        return {"path": path, "present": False, "age_hours": None}
    now = time.time() if now is None else now
    age = max(0.0, now - os.path.getmtime(used)) / 3600.0
    return {"path": used, "present": True, "age_hours": round(age, 2)}


def missing_reason(path: str) -> str:
    """The loud half of a tool_calls skip when no overlay exists at all."""
    return ("no tool_calls overlay found at %s (run tools/probe-toolcalls.py or set %s)"
            % (path, ENV_VAR))
