#!/usr/bin/env python3
"""Stop one lane's `opencode serve` process tree and drop its state file.

One implementation behind two entry points: `oc_l1.py stop --name LANE` (an L1
lane, or any lane whose config is on disk) and `oc_l2.py stop --lane LANE`
(R-coord-10). POSIX: the recorded PID's process group, SIGTERM then SIGKILL to
the whole group, after the PID's identity is checked against the state file.
Windows: `taskkill /F /T` (the tree walk is the OS's). The state file goes last,
so while it exists the lane still reads as "running" to anything that looks.

Idempotent: a lane with no state file is "not running" (exit 0, nothing killed).
Nothing here reads a value from the environment or the config beyond the lane's
own state-file path and port.
"""
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_l1_serve  # noqa: E402
from oc_l1_http import _read_state  # noqa: E402

KILL_WAIT_S = 5.0
KILL_HARD_WAIT_S = 2.0
NT_START_TOLERANCE_S = 2.0  # State UTC is rounded and recorded slightly after OS creation.
EXIT_OK = 0
EXIT_NOT_STOPPED = 2  # an orphan is not a stop: nonzero so an exit-code reader cannot miss it


def identity_ok(pid, state):
    """May this PID be signalled as the lane? (True, None) or (False, why).

    Identity is the recorded command line AND the kernel start time of that
    exact PID - not a token of the command line that resembles the binary's
    name. A recycled PID can inherit the dead lane's number, and a wrapper's
    `exec` chain means the live head token is not the recorded one, so the
    comparison is made over the launcher's own arguments (everything after the
    head) plus the start time, which is the one fact a recycler cannot copy.
    A state file written before the launcher recorded either fact proves
    nothing: refusing keeps the process that is not provably the lane alive and
    keeps the state that describes it. An unreadable /proc (Windows, or already
    gone) is not evidence to refuse on.
    """
    recorded = state.get("argv")
    start = state.get("start_time")
    if not isinstance(recorded, list) or not recorded or \
            not isinstance(start, int):
        return False, ("pid %d cannot be verified: the state file records no "
                       "argv/start_time - leave it running and kill it by hand "
                       "if the lane must go" % pid)
    live = oc_l1_serve.proc_argv(pid)
    if live is None:
        return True, None
    args = recorded[1:]
    if live[len(live) - len(args):] != args:
        return False, ("pid %d is not the lane's recorded command - refused to "
                       "kill it, the state file stays" % pid)
    now = oc_l1_serve.proc_starttime(pid)
    if now is not None and now != start:
        return False, ("pid %d is the lane's command but not its process: start "
                       "time %s is not the recorded %s (a recycled pid) - "
                       "refused to kill it, the state file stays"
                       % (pid, now, start))
    return True, None


def pgid(pid):
    try:
        return os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None


def _nt_alive(pid):
    """Windows: is the process still running? An exit code of STILL_ACTIVE (259)
    says so. `os.kill(pid, 0)` is no probe there - it calls TerminateProcess, and
    it still "succeeds" on a process that exited but is held open by a handle."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists; 87: no such pid
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) \
            and code.value == 259
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def _nt_identity_ok(pid, state):
    """Refuse a recycled Windows PID when both creation times are available."""
    try:
        recorded = datetime.fromisoformat(
            state["started_utc"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        return True, None  # Older states without a start retain the legacy kill path.
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return True, None  # An unavailable OS time retains the legacy behaviour.
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(created),
                                  ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return True, None  # Query failure is not evidence of PID recycling.
        live = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10000000 - 11644473600
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    if abs(live - recorded) > NT_START_TOLERANCE_S:
        return False, ("pid %d is the lane's command but not its process: start "
                       "time %s is not the recorded %s (a recycled pid) - "
                       "refused to kill it, the state file stays" % (pid, live, recorded))
    return True, None


def pid_gone(pid):
    """Is the recorded PID finished? `kill(pid, 0)` also answers for a ZOMBIE -
    killed, not yet waited for by its parent - so reading that as alive would
    report an orphan that cannot exist. Reap it when it is our own child,
    else read /proc's state letter where it exists (the same reasoning as
    tests/_oc_l1_fakes.pid_alive, which the L1 suite already needs)."""
    if os.name == "nt":
        return not _nt_alive(pid)
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return True
    except (ChildProcessError, OSError):
        pass  # not our child: its own parent reaps it
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return True
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state == "Z"


def wait_gone(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not pid_gone(pid):
        time.sleep(0.1)
    return pid_gone(pid)


def _kill_posix(pid, out):
    # start_new_session=True made the child its own group leader, so the
    # group is the tree: whatever `opencode serve` forks dies with it.
    # Anywhere else the process belongs to whoever started it - possibly
    # this very caller - and only the recorded PID may be signalled.
    group_leader = pgid(pid) == pid
    if group_leader:
        try:
            os.killpg(pid, signal.SIGTERM)
            out["killed_pids"].append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            oc_l1_serve._kill_pid(pid)
            out["killed_pids"].append(pid)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        out["killed_pids"].append(pid)
    if group_leader:
        # AO-L2-LAUNCH criterion b: the group is the whole tree, and a
        # grandchild that IGNORES SIGTERM outlives both it and the leader.
        # The lone recorded PID dying is therefore no proof the lane is gone:
        # after the graceful window, SIGKILL the ENTIRE group regardless of
        # whether the leader already died. The group's id equals the leader's
        # (now-dead) PID and the group still exists while any member does, so
        # killpg keeps reaching the survivors. A stop that signals only the
        # leader would leave that stubborn child running - the exact orphan
        # R-coord-10 forbids.
        wait_gone(pid, KILL_WAIT_S)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        wait_gone(pid, KILL_HARD_WAIT_S)
    elif not wait_gone(pid, KILL_WAIT_S):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        wait_gone(pid, KILL_HARD_WAIT_S)


def _remove_state(lane, out):
    """Remove the lane's state file; True on success (or already gone)."""
    try:
        os.remove(lane["state_file"])
        out["removed_state"].append(lane["state_file"])
    except FileNotFoundError:
        pass
    except OSError as e:
        out.update(stopped=False, orphan=False,
                   detail="the server is stopped but the state file could not be "
                          "removed: %s" % e)
        return False
    return True


def stop_lane(name, lane, state, port, remove_pidless_state=False):
    """Kill the lane's recorded process tree, then remove its state file.

    `state` is the parsed state file (or None), `port` the number to report.
    Returns the result dict: `stopped` is True only when nothing of the lane is
    left (an orphan or an unremovable state file reports False and says why).
    `remove_pidless_state`: a state file that names no pid is deleted too
    (oc_l1 stop); oc_l2 leaves it, as it always did.
    """
    out = {"lane": name, "port": port,
           "killed_pids": [], "orphan": False, "removed_state": []}
    if not isinstance(state, dict) and Path(lane["state_file"]).exists():
        out.update(stopped=False, detail="unreadable state file: %s" % lane["state_file"])
        return out
    pid = state.get("pid") if isinstance(state, dict) else None
    if not (isinstance(pid, int) and pid > 0):
        out["stopped"] = True
        out["detail"] = "no live pid recorded; nothing to kill"
        if remove_pidless_state:
            _remove_state(lane, out)
        return out
    if os.name == "nt":
        ok, why = _nt_identity_ok(pid, state)
        if not ok:
            out.update(stopped=False, orphan=True, detail=why)
            return out
        # taskkill /F /T walks the tree itself (oc_l1's own kill path).
        oc_l1_serve._kill_pid(pid)
        out["killed_pids"].append(pid)
    else:
        ok, why = identity_ok(pid, state)
        if not ok:
            out.update(stopped=False, orphan=True, detail=why)
            return out
        _kill_posix(pid, out)
    if not pid_gone(pid):
        method = "taskkill /F /T" if os.name == "nt" else "SIGKILL to its process group"
        out.update(stopped=False, orphan=True,
                   detail="pid %d is still alive after %s; "
                           "the state file stays so the lane is not reported as "
                           "stopped" % (pid, method))
        return out
    # The state file goes last: while it exists the lane is still "running"
    # to anything that reads it.
    if not _remove_state(lane, out):
        return out
    out["stopped"] = True
    out["detail"] = "stopped"
    return out


def cmd_stop(lane, args):
    """`oc_l1.py stop`: one line, exit 0 stopped / not running, 2 not stopped."""
    state_file = Path(lane["state_file"])
    if not state_file.exists():
        print("oc_l1: lane '%s' not running (no state file at %s); nothing killed"
              % (lane["name"], state_file))
        return EXIT_OK
    state = _read_state(str(state_file))
    port = (state.get("port") if isinstance(state, dict) else None) or lane["serve_port"]
    out = stop_lane(lane["name"], lane, state, port, remove_pidless_state=True)
    if not out["stopped"]:
        print("oc_l1: lane '%s' NOT stopped: %s" % (lane["name"], out["detail"]),
              file=sys.stderr)
        return EXIT_NOT_STOPPED
    killed = ", ".join(str(p) for p in out["killed_pids"]) or "none"
    print("oc_l1: lane '%s' stopped (killed pid: %s; port %s); state file removed"
          % (lane["name"], killed, port))
    return EXIT_OK
