"""Run-death recovery (AO-WRITER-GUARDS P4c, writer-ops.md §5).

A writer can die mid-task two ways: the process exits without printing its
REPORT, or it goes quiet for N minutes. The spawner's answer is the same both
times — keep the worktree, re-run the SAME task on the next leg with
"continue from current diff", at most twice, then escalate to the L2/L1. That is
this module's whole job: read one run record, say what it is, and hand back the
plan (the continuation brief and where to run it). It NEVER spawns — the L2/L1
that asks is the one that calls `spawn` with `cwd=<sandbox>`, the kept worktree.

The record read is exactly what the fleet runner writes under
``<state>/agents/<run_id>/`` (tools/autoos_agent_mcp.py `spawn`/`run_job`):
``job.json`` (run_id, task, cwd, started, pid, request, argv, route),
``exit.json`` — only once the runner has ended (rc, ended, family) — and
``output.log``, whose closing lines name the sandbox:

    sandbox: <sandbox> (branch <branch>)
    review:  git -C <sandbox> diff
    take it: git fetch <sandbox> <branch>   (then: git cherry-pick <base>..FETCH_HEAD)

HERMETIC (D-852): pure file reads and writes. No git, no subprocess, no network,
no spawn. The state dir is the caller's (default ``tools/autoos_clients.state_dir``,
so ``AUTOOS_STATE_DIR`` moves the whole tree for a test) and `now` is a parameter
read through the module-level `_now` hook, so the running/stalled boundary is
decided by data a test supplies, never by a wall clock.

Fail closed everywhere: an unreadable record, a corrupt attempt file or a pid
this host cannot probe is 'unknown', and 'unknown' is 'escalate'. It is never
'completed'.

Residuals, stated rather than hidden:

* pid reuse. A dead writer's pid can be handed to some other process between the
  death and this read, and ``os.kill(pid, 0)`` then answers "alive". The run
  record carries no start time to check that against (the runner's private kill
  record does, and this module reads no private store), so a reused pid holds a
  dead run at 'running' until the stall clock says otherwise.
* a run the operator cancelled is ``exit.json`` with ``cancelled: true`` and no
  rc, which reads here as 'died' — deliberate (a cancelled writer never
  reported), but the L1 that cancelled on purpose must not ask for a plan.
* report detection reuses tools/autoos_report.py's parser on the output tail and
  falls back to a REPORT heading line. The strict protocol gate belongs to
  `ready`, not to recovery; a false 'completed' needs rc==0 on top of it.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import time
import unicodedata

import autoos_clients as clients
import autoos_report as report_parser

# writer-ops.md §5: quiet for this long is a stall; at most this many
# "continue from current diff" legs before the lane escalates.
STALL_SECS = 900
MAX_ATTEMPTS = 2
# The output tail read (a whole run's log is never loaded), and the cap on the
# ORIGINAL task in a continuation brief — the footer always survives, the task
# does not.
TAIL_BYTES = 256 * 1024
TASK_MAX_BYTES = 200 * 1024
# The head of the original task the default lane key is hashed from.
KEY_TASK_CHARS = 2000
LANE_KEY_MAX_CHARS = 120
RUN_ID_MAX_CHARS = 128
PATH_MAX_CHARS = 4096
BRANCH_MAX_CHARS = 200

STATES = ("running", "completed", "died", "stalled", "unknown")
# One path component, no separators, no leading dot: neither `..` nor `/` can
# appear, so a run id never names anything outside <state>/agents/.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,%d}" % (RUN_ID_MAX_CHARS - 1))
# A lane key is caller-supplied and, like the run id, only ever hashed into a
# file name — the shape keeps it readable in the state record's `key` field.
_LANE_KEY_RE = re.compile(r"[A-Za-z0-9._/-]{1,%d}" % LANE_KEY_MAX_CHARS)
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,%d}" % BRANCH_MAX_CHARS)
# The return contract's heading, markdown-wrapped or not, as tools/autoos-agent.py's
# REPORT_HEADING_RE reads it: the `(?![\w.])` keeps "REPORTED" and "REPORT.md" out.
_REPORT_HEADING_RE = re.compile(
    r"^\s*(?:[#>*-]+\s*)?(?:\*\*)?REPORT(?:\*\*)?(?![\w.])")
_TAKE_RE = re.compile(r"^take it:[ \t]+git fetch[ \t]+")
_REVIEW_RE = re.compile(r"^review:[ \t]+git -C[ \t]+")
_SANDBOX_RE = re.compile(r"^sandbox:[ \t]+(?P<path>\S+)[ \t]+\(branch (?P<branch>\S+)\)$")


class RecoveryError(ValueError):
    """A run id, a lane key or a record path this module will not touch."""


def _now():
    """Current epoch seconds. Module-level hook so hermetic tests can pin time."""
    return time.time()


def state_dir(state=None):
    """The state root: `state` when given, else the repo's (`AUTOOS_STATE_DIR` moves it)."""
    return state or clients.state_dir()


def agents_root(state=None):
    return os.path.join(state_dir(state), "agents")


def recovery_dir(state=None):
    """Where the per-lane attempt files live: ``<state>/recovery``."""
    return os.path.join(state_dir(state), "recovery")


def _check_run_id(run_id):
    if type(run_id) is not str or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise RecoveryError("bad run id %r (a run id names one directory under "
                            "<state>/agents, never a path)" % (run_id,))
    return run_id


def _check_key(key):
    if type(key) is not str or not _LANE_KEY_RE.fullmatch(key):
        raise RecoveryError("bad lane key %r (1-%d chars of [A-Za-z0-9._/-])"
                            % (key, LANE_KEY_MAX_CHARS))
    return key


def _check_time(value, name):
    if type(value) is bool or not isinstance(value, (int, float)):
        raise RecoveryError("%s: epoch seconds, got %r" % (name, value))
    return float(value)


def run_dir_for(run_id, state=None):
    """``<state>/agents/<run_id>`` for a validated id.

    Refused: an id that is not one path component, a directory that is a symlink
    (a link is how a record escapes the tree it is meant to be read from), and a
    directory that is not there.
    """
    path = os.path.join(agents_root(state), _check_run_id(run_id))
    if os.path.islink(path):
        raise RecoveryError("run dir %r is a symlink" % (path,))
    if not os.path.isdir(path):
        raise RecoveryError("no run dir %r" % (path,))
    return path


def _read_json_dict(path):
    """``(dict, ok)`` for a record file.

    ok is False when the file exists but is not a readable JSON object — a
    record that cannot be read is never a verdict, the caller fails closed. A
    file simply not being there is ``(None, True)``: that is the state of a run
    that has not ended, not a damaged record.
    """
    if not os.path.exists(path):
        return None, True
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None, False
    return (data, True) if type(data) is dict else (None, False)


def _read_tail(path, limit=TAIL_BYTES):
    """The last `limit` bytes of `path`, decoded leniently; "" when unreadable."""
    try:
        with io.open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def pid_alive(pid):
    """True / False for a recorded pid, None when this host cannot tell.

    POSIX: ``os.kill(pid, 0)`` — ESRCH is dead, EPERM is alive (the number is
    taken by someone else's process). Windows has no such probe: ``os.kill``
    there is TerminateProcess, so the pid is read through a process handle's own
    exit code instead, and a host with neither answers None — which classifies
    as 'unknown' and escalates rather than guessing.
    """
    if type(pid) is bool or not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        try:
            kernel32 = ctypes.windll.kernel32
        except (AttributeError, OSError):
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return None
    return True


def _valid_path(path):
    """A sandbox path from an output line, validated or None.

    Refused: anything not absolute, any ``..`` component, any control character,
    an empty or over-long path, and one starting with ``~`` (an unexpanded home
    is not a path the runner ever printed). An unparsable sandbox is None — the
    caller loses the worktree hint, it never gets a guessed path.
    """
    if type(path) is not str or not path or len(path) > PATH_MAX_CHARS:
        return None
    if any(unicodedata.category(c)[0] == "C" for c in path):
        return None
    if path.startswith("~") or not os.path.isabs(path):
        return None
    if any(part == ".." for part in path.replace("\\", "/").split("/")):
        return None
    return path


def _tokens(line):
    """`line` split the way a shell would split it (the runner `shlex.quote`'s the
    paths it prints), or None when the quoting is not parseable."""
    try:
        return shlex.split(line)
    except ValueError:
        return None


def _check_branch(branch):
    """A branch name that can be pasted into a fetch line, or None."""
    if type(branch) is not str:
        return None
    return branch if _BRANCH_RE.fullmatch(branch) and ".." not in branch else None


def _parse_sandbox(tail):
    """(sandbox, branch) from the output tail.

    Read backwards for the last line that names the worktree — ``review:`` (the
    run ended and left a diff to look at) or ``sandbox:`` (printed when the clone
    was created, so a writer that died before it could print anything still names
    its worktree) — then forward from there for the ``take it:`` line that names
    the branch to fetch. With no such pair the branch is None; every path goes
    through `_valid_path` first, so an unparsable sandbox is None.
    """
    lines = tail.splitlines()
    sandbox, after = None, 0
    for i in range(len(lines) - 1, -1, -1):
        m = _SANDBOX_RE.match(lines[i])
        if m:
            path = _valid_path(m.group("path"))
            if path is not None:
                return path, _check_branch(m.group("branch"))
            continue
        parts = _tokens(lines[i])
        if parts and _REVIEW_RE.match(lines[i]) and len(parts) == 5 and parts[4] == "diff":
            path = _valid_path(parts[3])
            if path is not None:
                sandbox, after = path, i + 1
                break
    for line in lines[after:]:
        if not _TAKE_RE.match(line):
            continue
        parts = _tokens(line)
        if not parts or len(parts) < 6 or parts[:4] != ["take", "it:", "git", "fetch"]:
            continue
        path = _valid_path(parts[4])
        if path is None:
            continue
        return path if sandbox is None else sandbox, _check_branch(parts[5])
    return sandbox, None


def _has_report(tail, task):
    """Whether the run printed its REPORT.

    Two readers, the spawner's first: tools/autoos_report.py's parser finds the
    last ``REPORT <id>`` block in the tail — the same detection the fleet runner
    uses for its `report` field — and a heading that parser cannot read
    (``REPORT:``, ``**REPORT**``) still counts, because the return contract IS a
    heading. Lines the brief itself printed never count: a client that cats its
    task back is not reporting on it.
    """
    if not tail:
        return False
    try:
        if report_parser.parse_report(tail) is not None:
            return True
    except Exception:  # noqa: BLE001 - a block the parser chokes on is no block
        pass
    quoted = {line.strip() for line in (task or "").splitlines() if line.strip()}
    for line in tail.splitlines():
        if _REPORT_HEADING_RE.match(line) and line.strip() not in quoted:
            return True
    return False


def _output_age(run_dir, now):
    """Seconds since the run last wrote to disk, None when nothing says.

    ``output.log``'s mtime is the answer. Before a worker writes at all the
    newest thing in the record is ``job.json``, so a run that has only just
    started is 'running' and not 'stalled'.
    """
    for name in ("output.log", "job.json"):
        try:
            mtime = os.stat(os.path.join(run_dir, name)).st_mtime
        except OSError:
            continue
        return max(0.0, now - mtime)
    return None


def classify_run(run_dir, now=None, stall_secs=STALL_SECS, pid_probe=None):
    """What one run record says: {state, rc, has_report, sandbox, branch,
    last_output_age}.

    `state` is one of STATES and 'unknown' is fail-closed: a record this cannot
    read is never 'completed'. With no ``exit.json`` the run is judged on its pid
    and its output age: a live pid quiet past `stall_secs` is 'stalled', a dead
    pid is 'died', a pid this host cannot probe is 'unknown'. With an
    ``exit.json`` it takes rc==0 AND a REPORT to be 'completed'; anything else
    that ended is 'died', including an exit 0 that said nothing (that is the
    exit-without-REPORT case writer-ops.md §5 names).

    `run_dir` is the record directory itself (what `run_dir_for` returns);
    `pid_probe` is the liveness hook, injectable so a test can name a pid that is
    alive, dead or somebody else's without touching the host.
    """
    if type(run_dir) is not str or not run_dir:
        raise RecoveryError("run dir %r: a path to <state>/agents/<run_id>" % (run_dir,))
    path = run_dir.rstrip(os.sep) or run_dir
    if os.path.islink(path):
        raise RecoveryError("run dir %r is a symlink" % (path,))
    if not os.path.isdir(path):
        raise RecoveryError("no run dir %r" % (path,))
    ref = _check_time(_now() if now is None else now, "now")
    stall = _check_time(stall_secs, "stall_secs")
    if stall <= 0:
        raise RecoveryError("stall_secs: a positive number of seconds, got %r" % (stall_secs,))
    probe = pid_alive if pid_probe is None else pid_probe
    if not callable(probe):
        raise RecoveryError("pid_probe must be callable")

    job, job_ok = _read_json_dict(os.path.join(path, "job.json"))
    ex, ex_ok = _read_json_dict(os.path.join(path, "exit.json"))
    tail = _read_tail(os.path.join(path, "output.log"))
    sandbox, branch = _parse_sandbox(tail)
    out = {"state": "unknown", "rc": None,
           "has_report": _has_report(tail, (job or {}).get("task") if job_ok else ""),
           "sandbox": sandbox, "branch": branch,
           "last_output_age": _output_age(path, ref)}
    if not job_ok or not ex_ok:
        return out

    if ex is not None:
        rc = ex.get("rc")
        out["rc"] = rc if type(rc) is int and type(rc) is not bool else None
        out["state"] = "completed" if (out["rc"] == 0 and out["has_report"]) else "died"
        return out

    alive = probe((job or {}).get("pid"))
    if alive is None or out["last_output_age"] is None:
        return out
    if not alive:
        out["state"] = "died"
    else:
        out["state"] = "running" if out["last_output_age"] <= stall else "stalled"
    return out


def next_action(attempts, state, max_attempts=MAX_ATTEMPTS):
    """The move for a classified run: 'none' / 'wait' / 'rerun' / 'escalate'.

    A completed run needs nothing, a running one needs patience, a died/stalled
    one gets a continuation leg while `attempts` is under `max_attempts` and the
    lane after that, and 'unknown' — a record that could not be read — goes to a
    human instead of another spawn.
    """
    if type(attempts) is bool or not isinstance(attempts, int) or attempts < 0:
        raise RecoveryError("attempts: a non-negative int, got %r" % (attempts,))
    if type(state) is not str or state not in STATES:
        raise RecoveryError("state %r: expected %s" % (state, "|".join(STATES)))
    if type(max_attempts) is bool or not isinstance(max_attempts, int) or max_attempts < 0:
        raise RecoveryError("max_attempts: a non-negative int, got %r" % (max_attempts,))
    if state == "completed":
        return "none"
    if state == "running":
        return "wait"
    if state == "unknown":
        return "escalate"
    return "rerun" if attempts < max_attempts else "escalate"


def continuation_task(original_task, attempt, sandbox=None, max_attempts=MAX_ATTEMPTS):
    """The task text for one recovery leg: the original brief plus the footer.

    The original task is capped so the footer always arrives, and the sandbox
    path lands in the text only after the same validation the reader used — a
    path that failed to parse raises here rather than being pasted into a brief.
    """
    if type(original_task) is not str:
        raise RecoveryError("original_task: str, got %s" % type(original_task).__name__)
    if type(attempt) is bool or not isinstance(attempt, int) or not 1 <= attempt <= max_attempts:
        raise RecoveryError("attempt: 1..%d, got %r" % (max_attempts, attempt))
    task = original_task
    encoded = task.encode("utf-8")
    if len(encoded) > TASK_MAX_BYTES:
        task = encoded[:TASK_MAX_BYTES].decode("utf-8", "ignore")
    where = None
    if sandbox is not None:
        where = _valid_path(sandbox)
        if where is None:
            raise RecoveryError("sandbox %r is not a usable path" % (sandbox,))
    place = (
        "The sandbox at %s holds your earlier work as commits (git log); do NOT "
        "restart; finish what is missing, run every required check, and end with "
        "the REPORT." % where if where else
        "Your earlier work is in the worktree you are given; do NOT restart; "
        "finish what is missing, run every required check, and end with the REPORT.")
    footer = ("CONTINUE FROM CURRENT DIFF (recovery attempt %d/%d): your previous run "
              "ended without a REPORT. %s" % (attempt, max_attempts, place))
    return task.rstrip("\n") + "\n\n" + footer + "\n"


def lane_key_for_task(task):
    """The default lane key: sha256 over the task's first 2000 characters."""
    if type(task) is not str:
        raise RecoveryError("task: str, got %s" % type(task).__name__)
    return hashlib.sha256(task[:KEY_TASK_CHARS].encode("utf-8")).hexdigest()


def path_of(key, state=None):
    """Where this lane's attempt record lives: ``<state>/recovery/<key-hash>.json``."""
    digest = hashlib.sha256(_check_key(key).encode("utf-8")).hexdigest()
    return os.path.join(recovery_dir(state), digest + ".json")


def _no_attempts(key):
    return {"key": key, "attempts": 0, "runs": [], "last_ts": None, "corrupt": False}


def read_attempts(key, state=None):
    """{key, attempts, runs, last_ts, corrupt} for one lane.

    No file is zero attempts. A file that is not a readable record of that shape
    is MAX_ATTEMPTS with corrupt=True: the lane escalates instead of quietly
    getting a fresh budget, and the plan says so. So is a record whose `key` names
    another lane — the file is found by this lane's digest, and one lane's run
    list is not this lane's budget. A symlink there is refused, not read — the
    attempt budget is not a file anyone may point somewhere else.
    """
    path = path_of(key, state)
    if os.path.islink(path):
        raise RecoveryError("recovery state %r is a symlink" % (path,))
    data, ok = _read_json_dict(path)
    if data is None and ok:
        return _no_attempts(key)      # the file simply is not there: zero attempts
    runs, attempts = (data or {}).get("runs"), (data or {}).get("attempts")
    if (not ok or data.get("key") != key or type(attempts) is bool
            or not isinstance(attempts, int) or attempts < 0
            or type(runs) is not list or len(runs) > attempts
            or any(type(r) is not str for r in runs)):
        return dict(_no_attempts(key), attempts=MAX_ATTEMPTS, corrupt=True)
    return {"key": key, "attempts": attempts, "runs": list(runs),
            "last_ts": data.get("last_ts"), "corrupt": False}


def record_attempt(key, run_id, state=None):
    """Record one continuation leg for this lane, idempotent per run id.

    Attempts are counted per lane, not per run: two dead writers on one lane are
    two legs, and the second death of the SAME run is still one leg — a caller
    that retries this call must not spend the budget twice. The write is atomic
    (temp + ``os.replace``) at 0600, because this file is the lane's budget. A
    record that is not a readable attempt file is refused rather than overwritten:
    repairing it here would hand the lane back the budget the corruption hid.
    """
    _check_key(key)
    _check_run_id(run_id)
    cur = read_attempts(key, state)
    if cur["corrupt"]:
        raise RecoveryError("recovery state %r is corrupt: escalate, do not re-arm"
                            % (path_of(key, state),))
    if run_id in cur["runs"]:
        return cur
    path = path_of(key, state)
    record = {"key": key, "attempts": cur["attempts"] + 1,
              "runs": cur["runs"] + [run_id], "last_ts": _now()}
    target = os.path.dirname(path)
    os.makedirs(target, mode=0o700, exist_ok=True)
    if os.name != "nt":
        # makedirs' mode is umask-masked and never applied to an existing parent.
        os.chmod(target, 0o700)
    fd, tmp = tempfile.mkstemp(dir=target, prefix=".recovery-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dict(cur, attempts=record["attempts"], runs=record["runs"],
                last_ts=record["last_ts"])


def plan(run_id, lane_key=None, state=None, now=None, stall_secs=STALL_SECS,
         pid_probe=None):
    """The recovery plan for one run, and nothing else happens.

    {action, state, attempts, sandbox, branch, continue_task, spawn_hint, rc,
    has_report, last_output_age, lane_key, state_corrupt, run_id}. Without
    `lane_key` the lane is the run's own task, hashed; with one, every writer on
    that lane shares the budget. `spawn_hint.cwd` is the kept worktree for the
    L2/L1 to hand to `spawn` — this module never calls it.
    """
    path = run_dir_for(run_id, state)
    job, job_ok = _read_json_dict(os.path.join(path, "job.json"))
    task = (job or {}).get("task")
    if type(task) is not str or not job_ok:
        task = ""
    key = lane_key_for_task(task) if lane_key is None else _check_key(lane_key)
    info = classify_run(path, now=now, stall_secs=stall_secs, pid_probe=pid_probe)
    cur = read_attempts(key, state)
    action = next_action(cur["attempts"], info["state"])
    cont, hint = None, None
    if action == "rerun":
        cont = continuation_task(task, cur["attempts"] + 1, info["sandbox"])
        hint = {"cwd": info["sandbox"],
                "note": ("spawn with cwd=<sandbox> (the kept worktree) and this "
                         "continue_task, then record_attempt(<lane_key>, <new run id>)")
                   if info["sandbox"] else
                   ("no sandbox path in the record: spawn without --isolate, or "
                    "name the kept worktree yourself, then "
                    "record_attempt(<lane_key>, <new run id>)")}
    return {"run_id": run_id, "lane_key": key, "action": action,
            "state": info["state"], "attempts": cur["attempts"], "rc": info["rc"],
            "has_report": info["has_report"], "sandbox": info["sandbox"],
            "branch": info["branch"], "last_output_age": info["last_output_age"],
            "state_corrupt": cur["corrupt"], "continue_task": cont,
            "spawn_hint": hint}


_EXIT_FOR_ACTION = {"none": 0, "wait": 0, "rerun": 3, "escalate": 4}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="autoos_recovery.py",
        description="Run-death recovery plan for one run record (plans only, never spawns).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="print the recovery plan for RUN_ID as JSON "
                                    "(exit 0 none/wait, 3 rerun, 4 escalate)")
    p.add_argument("run_id")
    p.add_argument("--lane", dest="lane", default=None,
                   help="lane key sharing the attempt budget (default: the run's task, hashed)")
    p.add_argument("--stall-secs", dest="stall_secs", type=float, default=STALL_SECS,
                   help="quiet for this long is stalled (default %d)" % STALL_SECS)
    r = sub.add_parser("record", help="record one continuation leg for a lane (exit 0)")
    r.add_argument("run_id")
    r.add_argument("--lane", dest="lane", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            out = plan(a.run_id, lane_key=a.lane, stall_secs=a.stall_secs)
            print(json.dumps(out, indent=1, sort_keys=True))
            return _EXIT_FOR_ACTION[out["action"]]
        out = record_attempt(a.lane, a.run_id)
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    except RecoveryError as exc:
        print("autoos_recovery: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
