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
``output.log``, which is the SPAWNER's stream. The spawner's own lines are the
only ones this reader believes about the worktree: the header it prints before the
child starts, and the closing block it prints after the child exits:

    sandbox: <sandbox> (branch <branch>)          <- header (autoos-agent.py cmd_run)
    <the captured client transcript, from here on: worker text, never read for a
     worktree — a writer that prints its own `sandbox:`/`review:` line names no cwd>
    writer: ... / scope: ... / sandbox changes    <- closing block starts
    review:  git -C <sandbox> diff
    take it: git fetch <sandbox> <branch>   (then: git cherry-pick <base>..FETCH_HEAD)

The header is the FIRST `sandbox:` line of the tail, because the spawner prints it
before the client can type anything, and the `review:` / `take it:` pair is only
read below the closing block's first line. A line starting `> ` is also taken as
the start of the worker's stream (a launcher that prefixes its child's output
makes the boundary explicit), but nothing here depends on that marker existing:
what makes a forged line harmless is that every candidate path must additionally
be real, not a link, and sit under one of `sandbox_roots` (the same two roots
`sandbox_path_for` builds in tools/autoos-agent.py: ``<state>/sandboxes`` and
``~/fleet/sandboxes``).

HERMETIC (D-852): pure file reads and writes. No git, no subprocess, no network,
no spawn. The state dir is the caller's (default ``tools/autoos_clients.state_dir``,
so ``AUTOOS_STATE_DIR`` moves the whole tree for a test) and `now` is a parameter
read through the module-level `_now` hook, so the running/stalled boundary is
decided by data a test supplies, never by a wall clock.

Every read goes through `_open_regular`: ``O_NOFOLLOW`` (a symlinked record is a
damaged record, not a record elsewhere) and ``O_NONBLOCK`` with an ``S_ISREG``
check on the fd, so a FIFO planted at ``job.json`` / ``exit.json`` /
``output.log`` / the attempt file is refused rather than blocking this process
forever. Fail closed everywhere: an unreadable record, a corrupt attempt file, a
job.json field of the wrong type (`record_suspect` — the file is worker-writable,
so every field is type-checked before use and a misshapen record never answers
'completed') or
a pid this host cannot probe is 'unknown', and 'unknown' is 'escalate'. It is
never 'completed'.

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
  falls back to a REPORT heading line, both read over only the worker's own
  lines (fences, HTML comment regions — a REPORT inside `<!--` ... `-->` is not
  a claim and an unclosed `<!--` swallows the rest, whole block-quote blocks — a
  quote continues lazily over every following non-blank line, marker or no
  marker, where a blank line is ONLY ' ' and '\t' (CommonMark's own rule:
  `str.strip()` also empties U+00A0, \x0b, \x0c, U+0085, U+2028, U+3000, so a
  separator line may never close a quote) and lines are cut on '\n' ONLY (never
  `splitlines()`, which invents line breaks at those same characters), and the
  task's echoed text excluded, see `_report_view`). Any separator/control
  character that still reaches the report view — any Zs other than ' ', any
  Zl/Zp, any Cc other than '\t' — makes the WHOLE report suspect: has_report
  False, fail closed (P4c-fixes3). A heading that carries a run id must name
  THIS run, so a
  heading naming something else — including prose like ``REPORT: the field
  list`` — is not believed: the cost is one needless continuation leg on a run
  that wrote an unusual heading, never a false 'completed'. The strict protocol
  gate belongs to `ready`, not to recovery; a false 'completed' needs rc==0 on
  top of it.
* a sandbox path inside a trusted root is accepted on the spawner's word alone.
  A worker that could both create a directory under ``<state>/sandboxes`` and
  forge the header line could name that directory instead of its own; it cannot
  name a path outside the roots, a symlink, or another run's directory that does
  not exist. The continuation is also told (in `continuation_task`'s text) what
  path it is running in.
* the lane lock is held by the open file description on both sides — ``flock`` on
  POSIX, ``msvcrt.locking`` on Windows — so a holder that dies never leaves the
  lane blocked, and the lock file itself stays put. What neither gives is a wait:
  a lock still contested after ``LOCK_RETRIES`` tries is refused, and a refused
  record escalates the lane instead of running an unrecorded leg.
* the CLI's exit contract (P4c-fixes3): 0 none/wait and a recorded leg, 3 rerun,
  4 escalate — and 4 for lane state too: a corrupt, contested, non-regular or
  unwritable recovery state raises `RecoveryStateError`, which `main` answers
  with the one-line escalate message. Exit 2 stays ONLY for a usage error: an
  argparse failure or a run id / lane key the caller passed in the wrong shape.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
import unicodedata

import autoos_clients as clients
import autoos_ready_guards as ready_guards
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
# The header is printed at run START, the closing block at run END; a launcher
# that marks its child's output uses '> ' (tools/autoos-agent.py cmd_run prints
# `writer:`, `scope:`, then the `sandbox changes (uncommitted):` summary and the
# review/take-it pair, and does not prefix the child's lines).
_WORKER_MARK = "> "
_TRAILER_MARKS = ("writer:", "scope:", "sandbox changes")
# The two roots a printed sandbox path must live under to be a directory this
# module will name as a continuation cwd — the same two `sandbox_path_for` builds
# in tools/autoos-agent.py (S4). Mirrored, not imported: that file is a
# hyphen-named launcher and this module loads none of it. A change there has to
# be repeated here, and the test asserts both halves of the pair.
FLEET_SANDBOX_SUBPATH = os.path.join("fleet", "sandboxes")
# Above this a number is not a pid on this host (Linux's default kernel.pid_max
# is 2**22), and `os.kill` answers OverflowError rather than a verdict.
PID_MAX = 2 ** 22
# How long a contended lane lock is waited for before the record is refused, on
# BOTH platforms: `LOCK_RETRIES` tries `LOCK_RETRY_SECS` apart (2 s < 3 s total).
# POSIX pays it with a non-blocking `flock` (LOCK_NB) in the same retry loop
# Windows needs anyway — a blocking flock would hang the recorder for as long as
# the holder liked.
LOCK_RETRIES = 200
LOCK_RETRY_SECS = 0.01
# The one open mode every record, log and state file is read with.
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
# The return contract's heading, markdown-wrapped or not, as tools/autoos-agent.py's
# REPORT_HEADING_RE reads it: the `(?![\w.])` keeps "REPORTED" and "REPORT.md" out.
# `tail` is whatever follows the word — the protocol puts the run id there, and a
# heading that names anything else there is not this run's report.
_REPORT_HEADING_RE = re.compile(
    r"^\s*(?:[#>*-]+\s*)?(?:\*\*)?REPORT(?:\*\*)?(?![\w.])[ \t]*:?[ \t]*(?P<tail>.*)$")
_TAKE_RE = re.compile(r"^take it:[ \t]+git fetch[ \t]+")
_REVIEW_RE = re.compile(r"^review:[ \t]+git -C[ \t]+")
_SANDBOX_RE = re.compile(r"^sandbox:[ \t]+(?P<path>\S+)[ \t]+\(branch (?P<branch>\S+)\)$")
# A blank line is empty after ' ' and '\t' ONLY — CommonMark's own rule.
# `str.strip()` also empties U+00A0, \x0b, \x0c, U+0085, U+2028, U+3000..., so a
# line holding only one of those separators would CLOSE a block quote and hand
# back the REPORT under it as an unquoted claim (P4c-fixes3).
_BLANK_STRIP = " \t"
# The Unicode categories whose members splitlines() breaks lines at or strip()
# silently eats: separators and controls. ' ' (a Zs) and '\t' (a Cc) are the two
# a real line legitimately carries; every other member makes a report suspect.
_SUSPECT_CATS = frozenset(("Zs", "Zl", "Zp", "Cc"))


class RecoveryError(ValueError):
    """A run id, a lane key or a record path this module will not touch — the
    caller's argument was wrong, which the CLI answers with the usage exit (2)."""


class RecoveryStateError(RecoveryError):
    """The lane's own state refused the record: corrupt, contested, not a
    regular file, or unwritable. This is not a caller mistake to correct — it
    needs a human — so the CLI answers it with the escalate exit (4), never 2."""


def _lines(text):
    """`text` as PHYSICAL lines: split on '\\n' only, one trailing '\\r' dropped
    so a CRLF log reads like an LF one. Never `str.splitlines()`, which also
    breaks at \\x0b, \\x0c, U+0085, U+2028, U+2029 — the very characters that
    must stay inside one line so the quote logic sees a non-blank line and the
    fail-closed screen sees a suspect one (P4c-fixes3)."""
    return [p[:-1] if p.endswith("\r") else p for p in (text or "").split("\n")]


def _is_blank(line):
    """CommonMark's blank line: nothing but spaces and tabs."""
    return line.strip(_BLANK_STRIP) == ""


def _suspect_line(line):
    """Whether `line` carries a character the reader cannot count line breaks or
    blankness on: any Zs other than ' ', any Zl/Zp, any Cc other than '\\t'. A
    report whose lines contain one of these could be re-split or re-stripped
    into a different verdict, so the WHOLE report is read as no report."""
    for ch in line:
        if ch == " " or ch == "\t":
            continue
        if unicodedata.category(ch) in _SUSPECT_CATS:
            return True
    return False


def _comment_scan(line, in_comment):
    """Advance the HTML-comment tracker over `line`: (state_after, touched).
    `touched` is True when any part of the line lies inside a `<!--` ... `-->`
    region or opens/closes one, so a commented-out REPORT — whole, half, or on
    the very line that ends the comment — is dropped rather than believed; an
    unclosed `<!--` keeps the region open and swallows the rest of the tail. A
    `-->` with no open comment is text, not markup. `line` here is never fence
    content: the caller runs this only on unfenced lines, so a `<!--` inside a
    code block stays code and opens nothing (the fence wins)."""
    touched = in_comment
    rest = line
    while True:
        if in_comment:
            end = rest.find("-->")
            if end < 0:
                return True, touched
            in_comment = False
            rest = rest[end + 3:]
        start = rest.find("<!--")
        if start < 0:
            return False, touched
        in_comment = True
        touched = True
        rest = rest[start + 4:]


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


def sandbox_roots(state=None):
    """The only directories a printed sandbox path may be trusted inside.

    ``<state>/sandboxes`` (AutoOS's own cards, git-ignored) and
    ``~/fleet/sandboxes`` (a foreign cwd repo's, S4) — exactly the two places
    `sandbox_path_for` in tools/autoos-agent.py creates a sandbox. Nothing else
    is a worktree a writer may name for itself.
    """
    return (os.path.join(state_dir(state), "sandboxes"),
            os.path.join(os.path.expanduser("~"), FLEET_SANDBOX_SUBPATH))


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


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def _open_regular(path):
    """An fd on `path` if it is a REGULAR file, else None (the caller's answer is
    'not readable', never a guess).

    One opener for every record, log and state file this module reads:
      * ``O_NOFOLLOW`` — a symlink is refused, not followed, so a planted link
        cannot move a read outside the tree the record lives in;
      * ``O_NONBLOCK`` — a FIFO at that path would block an ordinary open (and a
        plain read) forever with no writer attached, which is a hung lane, not a
        verdict; on its own it is only half the fix, since the open then succeeds
        and the read returns EOF;
      * ``S_ISREG`` on `os.fstat` of the fd, not `stat()` on the name — the type
        is judged on the thing actually opened, so nothing can be swapped in
        between the check and the open.
    Windows defines neither flag: `getattr` answers 0 and the open is plain.
    """
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        return None
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        _close(fd)
        return None
    return fd


def _read_json_dict(path):
    """``(dict, ok)`` for a record file.

    ok is False when the file exists but is not a readable JSON object — a
    record that cannot be read, a symlink, a FIFO or a directory is no verdict,
    and the caller fails closed. A file simply not being there is ``(None, True)``:
    that is the state of a run that has not ended, not a damaged record.
    """
    if not os.path.lexists(path):
        return None, True
    fd = _open_regular(path)
    if fd is None:
        return None, False
    try:
        fh = os.fdopen(fd, "r", encoding="utf-8")
    except OSError:
        _close(fd)
        return None, False
    try:
        with fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None, False
    return (data, True) if type(data) is dict else (None, False)


def _read_tail(path, limit=TAIL_BYTES):
    """The last `limit` bytes of `path`, decoded leniently; "" when unreadable."""
    fd = _open_regular(path)
    if fd is None:
        return ""
    try:
        fh = os.fdopen(fd, "rb")
    except OSError:
        _close(fd)
        return ""
    try:
        with fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def pid_alive(pid):
    """True / False for a recorded pid, None when this host cannot tell.

    A number outside 1..`PID_MAX` is not a pid this host can hold, and asking is
    not harmless: ``os.kill(2**31, 0)`` raises OverflowError, which would end the
    read with a traceback instead of a verdict. So the shape is decided first and
    the probe catches everything it can still raise.

    POSIX: ``os.kill(pid, 0)`` — ESRCH is dead, EPERM is alive (the number is
    taken by someone else's process). Windows has no such probe: ``os.kill``
    there is TerminateProcess, so the pid is read through a process handle's own
    exit code instead, and a host with neither answers None — which classifies
    as 'unknown' and escalates rather than guessing.
    """
    if (type(pid) is bool or not isinstance(pid, int)
            or not 1 <= pid <= PID_MAX):
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
    except (OverflowError, ValueError, TypeError):
        # Not a number this host can probe: no verdict, so no guess.
        return None
    return True


def _valid_path(path):
    """A sandbox path from an output line, validated or None.

    Refused: anything not absolute, any ``..`` component, any control character,
    an empty or over-long path, one starting with ``~`` (an unexpanded home is not
    a path the runner ever printed), and one that is only separators — ``/`` names
    every file on the machine, so it is not a worktree. This is the SHAPE test;
    `_trusted_sandbox` adds the place-on-disk test a printed path must also pass.
    """
    if type(path) is not str or not path or len(path) > PATH_MAX_CHARS:
        return None
    if any(unicodedata.category(c)[0] == "C" for c in path):
        return None
    if path.startswith("~") or not os.path.isabs(path):
        return None
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return path


def _trusted_sandbox(path, state=None):
    """A printed sandbox path, or None unless it is this host's real worktree.

    The shape test first, then the disk test: an existing directory, not a
    symlink, whose resolved path sits STRICTLY inside one of `sandbox_roots` (the
    root itself is a container of sandboxes, never one). The printed path is what
    comes back, unchanged, so the caller re-runs in the directory the spawner
    named — but only once it has been proved to be under a root the spawner is the
    only thing that writes to. A path that fails any of this is None: the plan
    loses the worktree hint and says so, instead of pointing a continuation leg at
    a directory the writer chose.
    """
    cand = _valid_path(path)
    if cand is None:
        return None
    try:
        if os.path.islink(cand) or not os.path.isdir(cand):
            return None
        real = os.path.realpath(cand)
    except OSError:
        return None
    for root in sandbox_roots(state):
        try:
            if not os.path.isdir(root):
                continue
            base = os.path.realpath(root)
        except OSError:
            continue
        # A root that IS a filesystem root would trust everything under it.
        if not base or base == os.path.dirname(base):
            continue
        if real.startswith(base + os.sep):
            return cand
    return None


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


def _worker_start(lines):
    """Index of the first line of the worker's own stream, or None.

    A launcher that prefixes the child's output ('> ') states the boundary, and
    everything above it is spawner text. tools/autoos-agent.py does not print the
    prefix — its header is simply the first `sandbox:` line, which is why
    `_parse_sandbox` reads the header area first-wins and confines every candidate
    to `sandbox_roots` rather than trusting this boundary alone.
    """
    for i, line in enumerate(lines):
        if line.startswith(_WORKER_MARK):
            return i
    return None


def _trailer_start(lines):
    """Index of the spawner's closing block, or None when the tail holds none.

    The LAST line starting `writer:` / `scope:` / `sandbox changes` — cmd_run
    prints them after the child exits, so from there to the end is again spawner
    text, and that is the only place a `review:` / `take it:` pair may come from.
    """
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(_TRAILER_MARKS):
            return i
    return None


def _parse_sandbox(tail, state=None):
    """(sandbox, branch) from the SPAWNER's lines of the output tail.

    Two places, in order: the header — the first ``sandbox: <path> (branch <b>)``
    before the worker's stream starts — and, when the header has already scrolled
    out of the read window, the closing block's ``review:`` / ``take it:`` pair.
    Anything between them is worker text and is not read for a worktree at all: a
    writer that prints ``review:  git -C / diff`` gets no cwd. Every candidate
    goes through `_trusted_sandbox`, so an unparsable path, a path outside the
    sandbox roots, a missing directory or a symlink is None — the caller loses the
    worktree hint, it never gets a guessed path.
    """
    lines = _lines(tail)
    if not lines:
        return None, None
    header_end = len(lines)
    for bound in (_worker_start(lines), _trailer_start(lines)):
        if bound is not None:
            header_end = min(header_end, bound)
    for i in range(header_end):
        m = _SANDBOX_RE.match(lines[i])
        if not m:
            continue
        path = _trusted_sandbox(m.group("path"), state)
        if path is not None:
            return path, _check_branch(m.group("branch"))
    trailer = _trailer_start(lines)
    if trailer is None:
        return None, None
    block = lines[trailer:]
    sandbox, after = None, 0
    for i in range(len(block) - 1, -1, -1):
        parts = _tokens(block[i])
        if parts and _REVIEW_RE.match(block[i]) and len(parts) == 5 and parts[4] == "diff":
            path = _trusted_sandbox(parts[3], state)
            if path is not None:
                sandbox, after = path, i + 1
                break
    for line in block[after:]:
        if not _TAKE_RE.match(line):
            continue
        parts = _tokens(line)
        if not parts or len(parts) < 6 or parts[:4] != ["take", "it:", "git", "fetch"]:
            continue
        path = _trusted_sandbox(parts[4], state)
        if path is None:
            continue
        return sandbox if sandbox is not None else path, _check_branch(parts[5])
    return sandbox, None


def _report_view(tail, task):
    """The tail's lines that are the worker's OWN words.

    Dropped before any REPORT is looked for: fence markup and everything inside a
    fence — ONE shared rule with the `ready` guards
    (`autoos_ready_guards._Fences`: a fence opens on 3+ of the same ` or ~ after at
    most three leading spaces and closes only on the same character at least as
    long with nothing after it, so a fence that is never closed swallows the rest
    of the tail); HTML comment regions — every line any part of which lies inside
    a `<!--` ... `-->` comment is dropped on the same exclusion path (an unclosed
    `<!--` swallows the rest, and a `<!--` inside a fence stays code: the fence
    owns its lines and wins); WHOLE block-quote blocks, because a quote is not a
    claim — a block starts at a line that, once stripped of its leading blanks
    (spaces or tabs; CommonMark's own rule is at most three spaces), begins with
    '>', and it continues over EVERY following non-blank line until the first
    blank one, marked or not: a REPORT line sitting under a '>' line with no
    blank between is a lazy continuation of the quote, not a claim of its own.
    A blank line here means ONLY ' ' and '\\t' (`_is_blank`), and both inputs are
    cut on '\\n' only (`_lines`): a U+00A0/\\x0b/\\x0c/U+0085/U+2028/U+3000 line
    neither closes the quote nor breaks a line, so the quoted REPORT stays quoted
    (P4c-fixes3); and every line that also occurs in the task — a client that
    cats its brief back has echoed the return contract's own REPORT line, and
    that is the brief's text, not a report on the work.
    """
    echoed = {line.strip(_BLANK_STRIP) for line in _lines(task)
              if not _is_blank(line)}
    fences = ready_guards._Fences()
    out = []
    in_quote = False
    in_comment = False
    for raw in _lines(tail):
        # The tracker must see EVERY line: its state is the fence structure, and
        # skipping a line here would re-open a block that is still shut.
        fenced = fences.feed(raw)
        if fenced:
            in_comment_here = False      # '<!--' inside a fence is code text
        else:
            in_comment, in_comment_here = _comment_scan(raw, in_comment)
        line = raw.strip(_BLANK_STRIP)
        if not line:
            in_quote = False       # a blank line is what closes a quote block
            continue
        if not fenced and line.startswith(">"):
            in_quote = True        # '>' inside a fence is code text, not a marker
        if fenced or in_comment_here or in_quote or line in echoed:
            continue
        out.append(raw)
    return out


def _heading_id(match):
    """The first token of a REPORT heading: where the protocol puts the run id."""
    head = match.group("tail").split("·")[0].strip()
    return head.split()[0] if head else ""


def _names_this_run(declared, run_id):
    """'' names no run (the bare `**REPORT**` heading form) and counts; anything
    else must be THIS run's id, or the block is another run's report."""
    return not declared or declared == run_id


def _has_report(tail, task, run_id):
    """Whether the run printed its REPORT.

    Two readers over `_report_view`'s lines, the spawner's first:
    tools/autoos_report.py's parser finds the last ``REPORT <id>`` block, and a
    heading that parser cannot read (``# REPORT``, ``**REPORT**``) still counts,
    because the return contract IS a heading. Either way a run id the heading
    carries must be this run's. A block inside a fence, inside an HTML comment,
    inside a quote, or copied out of the task never reaches either reader — and
    any view line carrying a separator or control character the line rules are
    built on (any Zs other than ' ', any Zl/Zp, any Cc other than '\\t') makes
    the WHOLE report suspect: has_report False, fail closed, because a text
    another reader could split or strip differently is not a claim to believe.
    """
    lines = _report_view(tail, task)
    if not lines:
        return False
    if any(_suspect_line(line) for line in lines):
        return False
    try:
        parsed = report_parser.parse_report("\n".join(lines))
    except Exception:  # noqa: BLE001 - a block the parser chokes on is no block
        parsed = None
    if parsed is not None and _names_this_run(str(parsed.get("id") or ""), run_id):
        return True
    for line in lines:
        m = _REPORT_HEADING_RE.match(line)
        if m and _names_this_run(_heading_id(m), run_id):
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


def _job_record_suspect(job):
    """Whether a present job.json carries a field of the WRONG type.

    job.json is worker-writable: a writer (or a bug in the runner) can put 123
    where the task string belongs. Every field this module reads is type-checked
    before use — a non-str is never fed to the echo filter or the pid probe — and
    a record that misuses a field it is supposed to carry is 'suspect': too
    damaged to answer 'completed', so it classifies 'unknown' and escalates.
    Absent fields are not suspect (a missing job.json is an unfinished record,
    judged elsewhere); a present-but-wrong-typed one is.
    """
    if job is None:
        return False
    for name in ("run_id", "task", "cwd"):
        if name in job and type(job[name]) is not str:
            return True
    if "pid" in job and (type(job["pid"]) is bool or not isinstance(job["pid"], int)):
        return True
    if ("started" in job and (type(job["started"]) is bool
                              or not isinstance(job["started"], (int, float)))):
        return True
    return False


def classify_run(run_dir, now=None, stall_secs=STALL_SECS, pid_probe=None):
    """What one run record says: {state, rc, has_report, sandbox, branch,
    record_suspect, last_output_age}.

    `state` is one of STATES and 'unknown' is fail-closed: a record this cannot
    read is never 'completed'. A job.json field of the wrong type sets
    `record_suspect` and holds the state at 'unknown' — a record that misuses its
    own fields cannot attest to anything, not even an exit 0 with a REPORT. With
    no ``exit.json`` the run is judged on its pid
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
    # The record's own state root: <state>/agents/<run_id> -> <state>. A sandbox
    # path printed in the log is trusted only inside that root, so the tree the
    # caller named decides it, not the caller's environment on top of that.
    root = os.path.dirname(os.path.dirname(path))
    sandbox, branch = _parse_sandbox(tail, root)
    # A non-str task is '' for the echo filter (and a suspect record never
    # reaches a verdict anyway); a non-int pid is not a number the probe can
    # answer for — both are refused on type, never handed on unchecked.
    task = (job or {}).get("task") if job_ok else None
    if type(task) is not str:
        task = ""
    suspect = bool(job_ok) and _job_record_suspect(job)
    out = {"state": "unknown", "rc": None,
           "has_report": _has_report(tail, task, os.path.basename(path)),
           "sandbox": sandbox, "branch": branch, "record_suspect": suspect,
           "last_output_age": _output_age(path, ref)}
    if not job_ok or not ex_ok or suspect:
        return out

    if ex is not None:
        rc = ex.get("rc")
        out["rc"] = rc if type(rc) is int and type(rc) is not bool else None
        out["state"] = "completed" if (out["rc"] == 0 and out["has_report"]) else "died"
        return out

    pid = (job or {}).get("pid")
    alive = probe(pid if (type(pid) is int and type(pid) is not bool) else None)
    if alive is None or out["last_output_age"] is None:
        return out
    if not alive:
        out["state"] = "died"
    else:
        out["state"] = "running" if out["last_output_age"] <= stall else "stalled"
    return out


def next_action(attempts, state, max_attempts=MAX_ATTEMPTS, record_suspect=False):
    """The move for a classified run: 'none' / 'wait' / 'rerun' / 'escalate'.

    A completed run needs nothing, a running one needs patience, a died/stalled
    one gets a continuation leg while `attempts` is under `max_attempts` and the
    lane after that, and 'unknown' — a record that could not be read — goes to a
    human instead of another spawn. `record_suspect` (a job.json field of the
    wrong type) escalates outright: no 'none' and no leg spent on a record that
    cannot say what it ran.
    """
    if type(attempts) is bool or not isinstance(attempts, int) or attempts < 0:
        raise RecoveryError("attempts: a non-negative int, got %r" % (attempts,))
    if type(state) is not str or state not in STATES:
        raise RecoveryError("state %r: expected %s" % (state, "|".join(STATES)))
    if type(max_attempts) is bool or not isinstance(max_attempts, int) or max_attempts < 0:
        raise RecoveryError("max_attempts: a non-negative int, got %r" % (max_attempts,))
    if type(record_suspect) is not bool:
        raise RecoveryError("record_suspect: bool, got %r" % (record_suspect,))
    if record_suspect:
        return "escalate"
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
        raise RecoveryStateError("recovery state %r is a symlink" % (path,))
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


def _lock_path(key, state=None):
    """Where this lane's lock lives: ``<state>/recovery/<key-hash>.lock``."""
    digest = hashlib.sha256(_check_key(key).encode("utf-8")).hexdigest()
    return os.path.join(recovery_dir(state), digest + ".lock")


def _lock_regular(fd, path):
    """Keep `fd` only if the lock is a real file: the lock is a name in the lane's
    own directory, never a symlink to somewhere else or a pipe someone planted."""
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        _close(fd)
        raise RecoveryStateError("lane lock %r is not a regular file" % (path,))
    return fd


def _lock_open(path, flags):
    """Open the lane's lock name: a regular file at 0600, never a symlink to
    somewhere else or a pipe someone planted there."""
    try:
        return _lock_regular(os.open(path, flags, 0o600), path)
    except OSError as exc:
        raise RecoveryStateError("cannot take the lane lock %r: %s" % (path, exc))


def _acquire_lock(path):
    """Take the lane's exclusive lock; returns the fd `_release_lock` closes.

    WHY: `record_attempt` is a read-modify-write on the lane's budget. Two
    recorders of two DISTINCT dead runs both read attempts=0 and both write 1, so
    the lane silently gets more continuation legs than it was given — the one
    number that keeps a runaway writer from re-running forever.

    The lock is the same ``.lock`` name on both platforms and is held by the open
    file description, so a holder that dies — killed, crashed, or the interpreter
    exiting — loses the lock with its fds and the lane is never blocked by a stale
    file. POSIX uses ``flock`` with ``LOCK_NB``, Windows has no flock (and this
    module must stay
    importable there) so it uses ``msvcrt.locking`` on one byte of the same fd;
    both platforms then pay the same bounded retry loop.
    A lock still contested after `LOCK_RETRIES` tries raises a
    `RecoveryStateError`: refusing to record escalates the lane (the CLI's
    exit 4), which is safe, where recording anyway spends a leg nobody
    counted, which is not.
    """
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    if os.name == "nt":
        import msvcrt  # inside the platform check: no msvcrt off Windows

        for _ in range(LOCK_RETRIES):
            fd = _lock_open(path, flags)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                # Another writer holds the byte: the name stays, the lock does not.
                _close(fd)
                time.sleep(LOCK_RETRY_SECS)
                continue
            return fd
        raise RecoveryStateError("lane lock %r is held by another writer (%d tries)"
                                 % (path, LOCK_RETRIES))
    else:
        import fcntl  # inside the platform check: no fcntl on Windows

        # LOCK_NB, in the SAME bounded retry loop as Windows: a blocking
        # LOCK_EX sleeps in the kernel for as long as the holder likes, and a
        # live holder (a recorder mid-write, or one hung on a slow disk) would
        # hang `record_attempt` unboundedly — the docstring promises refusal
        # after `LOCK_RETRIES`, and an escalate-not-hang lane needs that promise
        # kept. The fd is opened once and closed on EVERY exit path.
        fd = _lock_open(path, flags)
        try:
            for _ in range(LOCK_RETRIES):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Another writer holds the description: try again, bounded.
                    time.sleep(LOCK_RETRY_SECS)
                    continue
                except OSError as exc:
                    raise RecoveryStateError("cannot take the lane lock %r: %s"
                                             % (path, exc))
                return fd
            raise RecoveryStateError("lane lock %r is held by another writer (%d tries)"
                                     % (path, LOCK_RETRIES))
        except BaseException:
            _close(fd)
            raise


def _release_lock(fd):
    """Give the lock back: closing the fd drops both the flock and the msvcrt
    byte lock, so there is no unlock call and no second platform-only import."""
    _close(fd)


def record_attempt(key, run_id, state=None):
    """Record one continuation leg for this lane, idempotent per run id.

    Attempts are counted per lane, not per run: two dead writers on one lane are
    two legs, and the second death of the SAME run is still one leg — a caller
    that retries this call must not spend the budget twice. The read and the write
    are one step under `_acquire_lock`, so two recorders cannot both see the same
    count. The write is atomic (temp + ``os.replace``) at 0600, because this file
    is the lane's budget. A record that is not a readable attempt file is refused
    rather than overwritten: repairing it here would hand the lane back the budget
    the corruption hid. A refusal of the STATE — corrupt, contested, not a regular
    file, unwritable — is a `RecoveryStateError`, which the CLI answers as
    escalate (exit 4); a refused run id or lane key stays a usage error.
    """
    _check_key(key)
    _check_run_id(run_id)
    path = path_of(key, state)
    target = os.path.dirname(path)
    try:
        os.makedirs(target, mode=0o700, exist_ok=True)
        if os.name != "nt":
            # makedirs' mode is umask-masked and never applied to an existing parent.
            os.chmod(target, 0o700)
    except OSError as exc:
        raise RecoveryStateError("cannot prepare the recovery state dir %r: %s"
                                 % (target, exc))
    lock_fd = _acquire_lock(_lock_path(key, state))
    try:
        cur = read_attempts(key, state)
        if cur["corrupt"]:
            raise RecoveryStateError("recovery state %r is corrupt: do not re-arm"
                                     % (path,))
        if run_id not in cur["runs"]:
            record = {"key": key, "attempts": cur["attempts"] + 1,
                      "runs": cur["runs"] + [run_id], "last_ts": _now()}
            try:
                fd, tmp = tempfile.mkstemp(dir=target, prefix=".recovery-",
                                           suffix=".tmp")
            except OSError as exc:
                raise RecoveryStateError("cannot write the recovery state %r: %s"
                                         % (path, exc))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, sort_keys=True)
                os.replace(tmp, path)
            except OSError as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise RecoveryStateError("cannot write the recovery state %r: %s"
                                         % (path, exc))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            cur = dict(cur, attempts=record["attempts"], runs=record["runs"],
                       last_ts=record["last_ts"])
    finally:
        _release_lock(lock_fd)
    return cur


def plan(run_id, lane_key=None, state=None, now=None, stall_secs=STALL_SECS,
         pid_probe=None):
    """The recovery plan for one run, and nothing else happens.

    {action, state, attempts, sandbox, branch, continue_task, spawn_hint, rc,
    has_report, last_output_age, lane_key, state_corrupt, record_suspect,
    run_id}. Without
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
    action = next_action(cur["attempts"], info["state"],
                         record_suspect=info["record_suspect"])
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
            "state_corrupt": cur["corrupt"], "record_suspect": info["record_suspect"],
            "continue_task": cont, "spawn_hint": hint}


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
    r = sub.add_parser("record", help="record one continuation leg for a lane "
                                      "(exit 0; 4 when the lane state is corrupt "
                                      "or contested — escalate)")
    r.add_argument("run_id")
    r.add_argument("--lane", dest="lane", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            out = plan(a.run_id, lane_key=a.lane, stall_secs=a.stall_secs)
            print(json.dumps(out, indent=1, sort_keys=True))
            if out["record_suspect"]:
                print("autoos_recovery: job.json carries a field of the wrong "
                      "type; the record is suspected and the lane escalates",
                      file=sys.stderr)
            return _EXIT_FOR_ACTION[out["action"]]
        out = record_attempt(a.lane, a.run_id)
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    except RecoveryStateError as exc:
        # Corrupt / contested / non-regular / unwritable lane state: the spec's
        # answer is a human, not a usage message (P4c-fixes3 exit contract).
        print("autoos_recovery: %s; escalate" % exc, file=sys.stderr)
        return 4
    except RecoveryError as exc:
        # 2 stays ONLY for a usage error: an argument the caller passed in a
        # shape this module refuses to touch.
        print("autoos_recovery: %s" % exc, file=sys.stderr)
        return 2
    except BrokenPipeError:
        raise                                   # the reader went away: the shell's answer
    except KeyboardInterrupt:
        raise                                   # somebody pressed Ctrl-C: not our verdict
    except Exception:                           # noqa: BLE001 - fail closed, never a traceback
        # An unexpected fault is 'needs a human' (4), never 'nothing to do' (0)
        # or 'spend a leg' (3). SystemExit is a BaseException and stays itself.
        print("autoos_recovery: internal error; escalate", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
