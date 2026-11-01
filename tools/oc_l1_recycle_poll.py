"""oc_l1_recycle_poll.py - how a started lane gets measured (AO-OC-CONTEXT-RECYCLE, D-2267).

`oc_l1.py recycle` checks one lane once; nothing in it loops. This module is the glue that
makes "every lane started via oc_l1 / oc_l2 start is measured" true without an operator
hand-building a schedule:

  * `start` (oc_l1.py, and oc_l2 / the MCP through it) prints / returns the lane's recycle
    command (`recycle_command`, `HINT_PREFIX`) so the caller can schedule it - the example
    systemd timer and Windows task in configuration/oc-l1/ run exactly that command;
  * the supervisor poll paths run the same check once per poll: oc_l2 `status` / `resume` /
    `inbox` (`poll_l2`, from oc_l2.main) and the MCP `oc_status` (`poll_l1`);
  * `lane_lock` keeps two checks of one lane from overlapping (a timer tick and a poll landing
    together would otherwise both announce, or both restart): the second returns `busy`, exit 0.

Stdlib only.
"""

import contextlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HINT_PREFIX = "recycle-command: "
LOCK_NAME = "recycle.lock"
LOCK_STALE_S = 600  # a check is a few HTTP calls; a lock older than this is a crashed check
POLL_TIMEOUT_S = 120
_DEAD = ("dead", "absent", "error")


def recycle_command(name, cfg_path, tool=None):
    """The shell command that runs one recycle check of the lane (one check, no loop)."""
    tool = tool or Path(__file__).resolve().parent / "oc_l1.py"
    argv = [sys.executable, str(tool), "recycle", "--name", name, "--config", str(cfg_path)]
    return subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(map(shlex.quote, argv))


def split_hint(output):
    """(output without the hint line, the recycle command or None) of a `start` output."""
    lines = (output or "").strip().splitlines()
    cmd = next((ln[len(HINT_PREFIX):] for ln in lines if ln.startswith(HINT_PREFIX)), None)
    return "\n".join(ln for ln in lines if not ln.startswith(HINT_PREFIX)), cmd


@contextlib.contextmanager
def lane_lock(lane):
    """Yield True when this check owns the lane's lock, False when another check holds it.
    `<scratch_dir>/recycle.lock` is created exclusively and removed afterwards; a lock older
    than LOCK_STALE_S is a crashed check and is replaced. Raises OSError when the scratch
    directory cannot be used (the caller turns that into a failed check, nothing acted on)."""
    path = Path(lane["scratch_dir"]) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    got = False
    for _ in range(2):
        try:
            os.close(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            got = True
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime <= LOCK_STALE_S:
                    break
                path.unlink()
            except OSError:
                break
    try:
        yield got
    finally:
        if got:
            with contextlib.suppress(OSError):
                path.unlink()


def _summary(rc, text):
    """The check's one JSON line, reduced to what a poll answer carries."""
    res = {}
    for line in reversed((text or "").strip().splitlines()):  # stderr text may follow the JSON
        try:
            res = json.loads(line)
        except ValueError:
            continue
        if isinstance(res, dict):
            break
        res = {}
    out = {"exit_code": rc, "action": res.get("action", "error")}
    for key in ("ctx", "level", "warning", "detail", "session_id"):
        if res.get(key) is not None:
            out[key] = res[key]
    return out


def poll_l2(result, name, run, cfg_path):
    """oc_l2.main: run ONE recycle check for the lane a status / resume / inbox poll just
    answered for, and put its summary in `result["recycle"]`. `run` is oc_l2.run_oc_l1. Skipped
    for a lane the poll found gone (dead / absent / restarted / refused): there is no session to measure.
    The poll's own exit code is not affected."""
    if not isinstance(result, dict) or result.get("verdict") in _DEAD \
            or result.get("restarted") or result.get("restart_refused") or result.get("refused"):
        return
    result["recycle"] = _summary(*run("recycle", name, cfg_path))


def poll_l1(lane, env, tools_dir):
    """The MCP `oc_status` poll: one recycle check of an L1 lane through a child process (the
    MCP server's own environment is never the lane's). Returns the summary."""
    argv = [sys.executable, os.path.join(tools_dir, "oc_l1.py"), "recycle", "--name", lane]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=POLL_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return {"exit_code": 4, "action": "error", "detail": type(e).__name__}
    return _summary(r.returncode, r.stdout)
