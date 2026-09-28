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
        return env[ENV_VAR]
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
