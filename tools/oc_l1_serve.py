#!/usr/bin/env python3
"""oc_l1_serve.py - start/status for the O1-LITE lane (step A2).

Launch pipeline for tools/oc_l1.py against the opencode v2 HTTP API.
The v1 paths (/session, /event, /global/health, /doc) do not exist and
are never used here.

start
  * Refuses to run unless the lane's password_env is set (exit 2; no
    process, no state file - never invent a password).
  * Refuses a lane with an empty 'plugins' list (exit 2; no process, no
    state file): the bash-guard plugin is what denies the canary, so an
    unguarded lane can never pass and would refuse rc 5 forever (D-665).
  * Idempotent: if the state file names a live session (GET
    /api/session/{id} answers 200 and its outcome is not failed/
    interrupted), prints 'already live' and exits 0.
  * Renders the scratch config (oc_l1.render), then starts
    <opencode_bin> serve --hostname 127.0.0.1 --port <port> with cwd =
    the lane cwd and a CHILD environment that isolates XDG_* under
    scratch_dir and sets OPENCODE_CONFIG plus OPENCODE_SERVER_PASSWORD
    (child env only - never on the command line, never written to a
    file, never printed). The child's stderr is appended to
    <scratch_dir>/opencode.log so the plugin's fail-open notes are
    visible after the fact (D-665); that log is created 0600 and the
    scratch tree 0700, the modes applied at creation so no window leaves
    them world-readable (F4).
  * Polls GET /api/session/active with Basic auth (user 'opencode')
    every 0.5 s for health_timeout_s (raw lane key, default 30); on
    failure kills the child by its recorded PID and exits 4.
  * POST /api/session {title, location.directory, model{providerID,
    id}}, then POST /api/session/{id}/prompt with the relaunch line,
    the first 40 lines of the handoff, and the MCP hint line verbatim.
  * Writes the state file {name, session_id, port, pid, started_utc}
    atomically (temp file + os.replace, mode 0600 on POSIX), prints
    'started <name> session <id> port <port> pid <pid>' and exits 0,
    leaving the server running.

status
  dead (2): no state file, the server does not answer, or GET
  /api/session/{id} reports outcome failed/interrupted.
  silent (1): alive, but no NEW assistant message or tool item within
  silent_minutes (raw lane key, default 10) in
  GET /api/session/{id}/message?order=desc&limit=5.
  live (0): otherwise.

health_timeout_s / silent_minutes are read from the RAW config file:
the A1 validator (kept byte-identical in this step) only returns
resolved keys, so these optional lane keys are looked up separately.

Stdlib only; the tests never contact a real opencode.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_l1  # noqa: E402  (LaneError, render, REPO_ROOT, default_config_path)
import oc_l1_canary  # noqa: E402
import oc_l1_http  # noqa: E402
from oc_l1_http import (  # noqa: E402
    AUTH_USER,
    HOST,
    ServerDown,
    _basic_header,
    _data,
    _lane_opt,
    _num,
    _read_state,
    _request,
    _scrub,
    _wait_healthy,
    _write_state,
)

DEAD_OUTCOMES = ("failed", "interrupted")
HANDOFF_LINES = 40
CREATE_NO_WINDOW = 0x08000000
XDG_SUBDIRS = (
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_CACHE_HOME", "cache"),
)
HINT_LINE = (
    "MCP tools are available only through the built-in execute tool "
    "(code mode): call execute with a short script that invokes the "
    "tool; if a call fails, re-read the tool list from execute instead "
    "of guessing names."
)


# --- child management ---------------------------------------------------------


def _kill_pid(pid):
    """Best-effort kill of a child we started, by its recorded PID only."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
    except OSError:
        pass


def _private_dir(path):
    """Create a scratch directory 0700, with the mode applied AT creation.

    mkdir-then-chmod leaves a window in which the child's config, state and log
    sit in a directory readable by the rest of the machine; a umask of 0o077
    makes the requested mode exact instead of merely "not wider than". A
    directory an earlier, looser launcher left behind is tightened, never
    widened (F4).
    """
    if os.name == "nt":
        path.mkdir(parents=True, exist_ok=True)
        return
    old_umask = os.umask(0o077)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    finally:
        os.umask(old_umask)
    try:
        if os.stat(path).st_mode & 0o777 != 0o700:
            os.chmod(path, 0o700)
    except OSError:
        pass


def _child_stderr_log(scratch):
    """Open <scratch>/opencode.log for the child's stderr; None on any failure.

    The bash-guard plugin writes its fail-open notes to the child's stderr;
    DEVNULL made an un-loaded guard invisible (D-665). Losing the log must
    never lose the lane, so this opens best-effort and the caller falls back.

    The child's environment carries the server password and this file sits in
    the same directory, so both are created private from the first byte: no
    open-then-chmod window (F4).
    """
    log_path = scratch / "opencode.log"
    try:
        _private_dir(scratch)
        if os.name == "nt":
            return open(log_path, "ab")
        old_umask = os.umask(0o077)
        try:
            fd = os.open(str(log_path),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        finally:
            os.umask(old_umask)
    except OSError:
        return None
    try:
        # only a log an earlier, looser launcher left behind needs tightening;
        # one created here already has its mode
        if os.fstat(fd).st_mode & 0o777 != 0o600:
            os.fchmod(fd, 0o600)
    except OSError:
        pass
    try:
        return os.fdopen(fd, "ab")
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


def _spawn(lane, rendered, password):
    """Start the explicit opencode binary; child env isolated in scratch."""
    scratch = Path(lane["scratch_dir"])
    _private_dir(scratch)
    for var, sub in XDG_SUBDIRS:
        _private_dir(scratch / sub)
    env = dict(os.environ)
    for var, sub in XDG_SUBDIRS:
        env[var] = str(scratch / sub)
    env["OPENCODE_CONFIG"] = str(rendered)
    env["OPENCODE_SERVER_PASSWORD"] = password  # child env ONLY
    argv = [lane["opencode_bin"], "serve", "--hostname", HOST,
            "--port", str(lane["serve_port"])]
    err_log = _child_stderr_log(scratch)
    kwargs = {
        "cwd": lane["cwd"],
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": err_log if err_log is not None else subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(argv, **kwargs)
    finally:
        # the child holds its own inherited descriptor; the parent must not
        if err_log is not None:
            err_log.close()
    return proc


# --- start ----------------------------------------------------------------------


def _handoff_head(path):
    p = Path(path)
    if not p.is_file():
        raise oc_l1.LaneError("handoff file not found: %s" % path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[:HANDOFF_LINES])


def _create_session(port, password, lane):
    body = {
        "title": lane["name"],
        "location": {"directory": lane["cwd"]},
        "model": {"providerID": lane["model"]["provider"],
                  "id": lane["model"]["modelID"]},
    }
    status, payload = _request(port, "POST", "/api/session",
                               body=body, password=password)
    if status != 200:
        raise oc_l1.LaneError("session create failed: HTTP %d" % status)
    data = _data(payload)
    sid = data.get("id") if isinstance(data, dict) else None
    if not isinstance(sid, str) or not sid:
        raise oc_l1.LaneError("session create: no data.id in response")
    return sid


def _post_first_prompt(port, password, sid, lane):
    text = (
        "You are %s, relaunched from the handoff. Read %s first, then "
        "continue.\n\n%s\n\n%s"
        % (lane["name"], lane["handoff"], _handoff_head(lane["handoff"]),
           HINT_LINE)
    )
    status, _ = _request(port, "POST", "/api/session/%s/prompt" % sid,
                         body={"text": text}, password=password)
    if status != 200:
        raise oc_l1.LaneError("first prompt failed: HTTP %d" % status)


def cmd_start(lane, args):
    """start --name X: see the module docstring. Returns the exit code."""
    name = lane["name"]
    port = lane["serve_port"]
    password = os.environ.get(lane["password_env"])
    if not password:
        print(
            "oc_l1: error: start refused: environment variable %s is not "
            "set; set it - the password is never taken from the command "
            "line or config, and never invented" % lane["password_env"],
            file=sys.stderr,
        )
        return 2

    # D-665: a lane with no plugin runs no bash-guard, so its canary can never
    # be denied. That is a config defect, not a canary result: refuse with rc 2
    # before paying for a model call (an empty-plugin lane refused rc 5 on
    # every watcher cycle forever).
    if not lane.get("plugins"):
        print(
            "oc_l1: error: start refused: lane '%s' has an empty 'plugins' "
            "list - the bash-guard plugin is what denies the canary, so an "
            "unguarded lane can never pass; add the plugin path to the lane "
            "config" % name,
            file=sys.stderr,
        )
        return 2

    # Idempotence: a state file naming a live session wins over starting.
    state = _read_state(lane["state_file"])
    if isinstance(state, dict):
        sid = state.get("session_id")
        state_port = state.get("port") or port
        if isinstance(sid, str) and sid:
            try:
                status, payload = _request(
                    state_port, "GET", "/api/session/%s" % sid,
                    password=password)
            except ServerDown:
                status = None
            if status == 200 and isinstance(_data(payload), dict):
                if _data(payload).get("outcome") not in DEAD_OUTCOMES:
                    canary = state.get("canary")
                    if isinstance(canary, dict):
                        print(oc_l1_canary.format_canary_line(canary))
                    if not (isinstance(canary, dict) and canary.get("denied")):
                        print("UNATTENDED-REFUSED")
                        return 5
                    if state.get("prompted") is False:
                        rc = _prompt_pilot(lane, state_port, password, sid, state)
                        if rc:
                            return rc
                    print("already live: session %s port %s" % (sid, state_port))
                    return 0

    try:
        rendered = oc_l1.render(lane, oc_l1.REPO_ROOT / "opencode.jsonc")
    except oc_l1.LaneError as e:
        print("oc_l1: error: %s" % e, file=sys.stderr)
        return 2

    try:
        proc = _spawn(lane, rendered, password)
    except OSError as e:
        print("oc_l1: error: cannot start %s: %s"
              % (lane["opencode_bin"], e), file=sys.stderr)
        return 2
    pid = proc.pid

    try:
        timeout_s = _lane_opt(lane, args, "health_timeout_s", 30)
        if not _wait_healthy(port, password, timeout_s):
            print("oc_l1: error: server on 127.0.0.1:%d not healthy after "
                  "%.0fs; killed child %d" % (port, timeout_s, pid),
                  file=sys.stderr)
            _kill_pid(pid)
            return 4
        sid = _create_session(port, password, lane)
    except oc_l1.LaneError as e:
        _kill_pid(pid)
        print("oc_l1: error: %s" % _scrub(str(e), password), file=sys.stderr)
        return 4
    except ServerDown as e:
        _kill_pid(pid)
        print("oc_l1: error: server went away: %s" % e, file=sys.stderr)
        return 4

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    st = {
        "name": name,
        "session_id": sid,
        "port": port,
        "pid": pid,
        "started_utc": started,
        "prompted": False,
    }
    try:
        _write_state(lane["state_file"], st)
    except oc_l1.LaneError as e:
        _kill_pid(pid)
        print("oc_l1: error: %s" % e, file=sys.stderr)
        return 2

    # The canary runs BEFORE the pilot gets its first prompt: a pilot that is not known to be guarded
    # must never run. Not denied -> the pilot session stays IDLE (no prompt), the server stays up for
    # supervised use, exit 5.
    canary = oc_l1_canary.run_canary(port, password, lane)
    st["canary"] = canary
    try:
        _write_state(lane["state_file"], st)
    except oc_l1.LaneError as e:
        _kill_pid(pid)
        print("oc_l1: error: %s" % e, file=sys.stderr)
        return 2

    oc_l1_canary.write_heartbeat(lane, canary)
    print(oc_l1_canary.format_canary_line(canary))
    if not canary.get("denied"):
        print("UNATTENDED-REFUSED")
        return 5

    rc = _prompt_pilot(lane, port, password, sid, st, pid)
    if rc:
        return rc
    print("started %s session %s port %d pid %d" % (name, sid, port, pid))
    return 0


def _prompt_pilot(lane, port, password, sid, st, pid=None):
    """Post the first prompt (after a denied canary) and record it. Returns 0 or an exit code."""
    try:
        _post_first_prompt(port, password, sid, lane)
        st["prompted"] = True
        _write_state(lane["state_file"], st)
    except oc_l1.LaneError as e:
        if pid:
            _kill_pid(pid)  # only the child this very call started; never a PID read from a state file
        print("oc_l1: error: %s" % _scrub(str(e), password), file=sys.stderr)
        return 4
    except ServerDown as e:
        if pid:
            _kill_pid(pid)
        print("oc_l1: error: server went away: %s" % e, file=sys.stderr)
        return 4
    return 0


# --- status ---------------------------------------------------------------------


def _parse_ts(v):
    """Tolerant timestamp: epoch seconds/ms (int/float) or ISO-8601 str."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n / 1000.0 if n > 1e12 else n  # ms -> s
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _item_ts(item):
    """Newest timestamp found in an item: time/created/updated, top-level
    or under info; a dict value yields its updated/created member."""
    if not isinstance(item, dict):
        return None
    cands = [item.get(k) for k in ("time", "created", "updated")]
    info = item.get("info")
    if isinstance(info, dict):
        cands += [info.get(k) for k in ("time", "created", "updated")]
    for v in cands:
        if isinstance(v, dict):  # opencode time objects: {created, updated}
            v = v.get("updated", v.get("created"))
        t = _parse_ts(v)
        if t is not None:
            return t
    return None


def _is_progress(item):
    """A NEW assistant message or a tool item is the progress signal."""
    if not isinstance(item, dict):
        return False
    if item.get("type") == "assistant" or item.get("role") == "assistant":
        return True
    if item.get("type") == "tool":
        return True
    for key in ("parts", "content"):
        parts = item.get(key)
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "tool":
                    return True
    return False


def _newest_progress_ts(items):
    """Items come order=desc; the first progress item is the newest."""
    for item in items:
        if not _is_progress(item):
            continue
        ts = _item_ts(item)
        return ts if ts is not None else 0.0
    return None


def cmd_status(lane, args):
    """status --name X: print live | silent | dead; exit 0 / 1 / 2."""
    state = _read_state(lane["state_file"])
    if not isinstance(state, dict) or not isinstance(state.get("session_id"), str) \
            or not state.get("session_id"):
        print("dead")
        return 2
    sid = state["session_id"]
    port = state.get("port") or lane["serve_port"]
    password = os.environ.get(lane["password_env"])
    if not password:
        print("oc_l1: error: %s is not set; cannot authenticate the status "
              "check" % lane["password_env"], file=sys.stderr)
        print("dead")
        return 2

    try:
        status, payload = _request(port, "GET", "/api/session/%s" % sid,
                                   password=password)
    except ServerDown:
        print("dead")
        return 2
    if status != 200 or not isinstance(_data(payload), dict):
        print("dead")
        return 2
    if _data(payload).get("outcome") in DEAD_OUTCOMES:
        print("dead")
        return 2

    try:
        mstatus, mpayload = _request(
            port, "GET", "/api/session/%s/message?order=desc&limit=5" % sid,
            password=password)
    except ServerDown:
        print("dead")
        return 2
    if mstatus != 200:
        print("dead")
        return 2
    items = _data(mpayload)
    if isinstance(items, dict):
        items = items.get("items")
    if not isinstance(items, list):
        items = []

    silent_s = _lane_opt(lane, args, "silent_minutes", 10) * 60.0
    newest = _newest_progress_ts(items)
    if newest is None or (time.time() - newest) > silent_s:
        print("silent")
        return 1
    print("live")
    return 0
