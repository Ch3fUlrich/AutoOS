"""fleet_report: the Claude Code Stop hook that files a session's status line.

The Stop hook JSON arrives on stdin (`session_id`, `transcript_path`). Only the
last 256 KB of the transcript is read (seek to the tail, first partial line
dropped); the LAST assistant record is taken, its `text` content blocks are
joined (`tool_use`/`tool_result` blocks are never copied), and the first line
starting with a fleet status keyword — DONE, MERGE-READY, BLOCKED, FAILED,
RECYCLE-READY, STOP — becomes the message. No keyword line, no name, or no
parent to report to: exit 0, no-op.

The session's name and its parent come from `FLEET_SESSION_NAME` or from the
launch records under `$FLEET_LAUNCH_DIR` (else `~/.local/state/routing/launch`).
A match appends exactly ONE JSON line — redacted and truncated to 300 chars —
to `<state>/inbox/<parent>.jsonl` with a single `os.write` under `O_APPEND`, so
two writers can never interleave half a record. `fleet_inject.py` later feeds
those lines back to the parent as `additionalContext`.

Dedupe is a marker per session: `<state>/inbox/.seen/<session_id>` holds the
last turn id that was filed, so the same turn is never appended twice. The
marker is written only after the append succeeded — a failed append retries on
the next Stop instead of silently losing the line.

Fail-silent, like every hook here: bad stdin, a garbled transcript, an unsafe
name, an unwritable state dir — exit 0 with no output, because a Stop hook must
never break the agent that called it. Nothing from the environment and nothing
from tool output is copied; the line passes through `redact()` first (this
repository is public). Every directory this hook creates is 0o700, inbox files
0o600. State: `$FLEET_STATE_DIR` else `~/.local/state/fleet`.

The shared helpers — `resolve_name`, `redact`, `state_dir`, `safe_name`,
`ensure_dir`, `atomic_write` — live here so `fleet_inject.py` can load this
module by path with importlib instead of duplicating them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Only the last 256 KB of the transcript is read — enough to hold the most
# recent assistant record while bounding the read on a many-megabyte file.
TAIL_BYTES = 256 * 1024

# The status line, after redaction, travels as both `status` and `line`.
MAX_LINE = 300

# First matching line of the assistant text; \b keeps STOPPED/ DONEX out.
STATUS_KEYWORDS = ("DONE", "MERGE-READY", "BLOCKED", "FAILED", "RECYCLE-READY", "STOP")
STATUS_RE = re.compile(r"^(%s)\b" % "|".join(STATUS_KEYWORDS))

# A name (session id, session name, parent) becomes a file name under the state
# dir: a letter/digit first, then letters, digits, dot, dash, underscore.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

# Key-like strings, most specific first and the catch-all 32+ character run
# last, so a known prefix is replaced whole instead of being chopped in half.
# These patterns stay deliberately greedy: a missed token lands in a public
# repository, while an over-eager redaction only costs a readable word.
_REDACTIONS = (
    (re.compile(r"sk[-_][A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"ghp_[A-Za-z0-9]+"), "[redacted]"),
    (re.compile(r"gho_[A-Za-z0-9]+"), "[redacted]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), "[redacted]"),
    (re.compile(r"xox[bp]-[A-Za-z0-9-]+"), "[redacted]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[redacted]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"), "[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[redacted]"),
    (re.compile(r"\b[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*=[^\s]+",
                re.IGNORECASE), "[redacted]"),
    (re.compile(r"[A-Za-z0-9_-]{32,}"), "[redacted]"),
)


def safe_name(value) -> bool:
    """True when `value` is usable as a file name under the state dir."""
    return isinstance(value, str) and _SAFE_NAME.match(value) is not None


def state_dir() -> Path:
    """The fleet state directory: `$FLEET_STATE_DIR`, else `~/.local/state/fleet`."""
    override = os.environ.get("FLEET_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "fleet"


def launch_dir() -> Path:
    """The launch-record directory: `$FLEET_LAUNCH_DIR`, else `~/.local/state/routing/launch`."""
    override = os.environ.get("FLEET_LAUNCH_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "routing" / "launch"


def resolve_name(session_id):
    """`(name, parent)` of this session.

    `FLEET_SESSION_NAME` names the session directly when set; the launch
    record whose `session_id` matches supplies `name` (when the environment
    does not) and always supplies `parent`. Either half can come back empty —
    the Stop hook needs both, the injector only its own name, so each caller
    decides what missing means to it. Everything unreadable (missing launch
    dir, garbled record) degrades to empty, never to an exception.
    """
    name = os.environ.get("FLEET_SESSION_NAME") or ""
    parent = ""
    if not isinstance(session_id, str):
        return name, parent
    try:
        paths = sorted(launch_dir().glob("*.json"))
    except OSError:
        return name, parent
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("session_id") != session_id:
            continue
        if not name:
            got = record.get("name")
            name = got if isinstance(got, str) else ""
        got = record.get("parent")
        parent = got if isinstance(got, str) else ""
        break
    return name, parent


def redact(s: str) -> str:
    """`[redacted]` in place of every key-like string in `s` (see `_REDACTIONS`).

    Applied to the status line before it is filed and again when the injector
    prints it, so a line filed by an older writer still ships scrubbed.
    """
    if not isinstance(s, str):
        return ""
    for pattern, replacement in _REDACTIONS:
        s = pattern.sub(replacement, s)
    return s


def ensure_dir(path) -> Path:
    """`path` as a 0o700 directory, creating every missing level with that mode.

    Only levels this call actually creates are chmod'ed — an existing
    directory's mode is left as its owner set it.
    """
    p = Path(path)
    missing = []
    while not p.exists():
        missing.append(p)
        p = p.parent
    for directory in reversed(missing):
        os.mkdir(str(directory), 0o700)
    return Path(path)


def atomic_write(path, data: bytes) -> None:
    """Write `data` to `path` via a temp file + rename, so a reader never sees half a file."""
    path = Path(path)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp), flags, 0o600)
        with os.fdopen(fd, "wb") as fh:
            if fh.write(data) != len(data):
                raise OSError("short write")
        os.replace(str(tmp), str(path))
    finally:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass


def assistant_tail(path):
    """`(text, turn)` of the LAST assistant record in the transcript tail, else (None, None).

    Reads only the last `TAIL_BYTES`; the byte offset there is mid-line, so the
    first split element is dropped as a partial line (but never when the whole
    file fit inside the tail). `text` joins the record's `text` content blocks —
    `tool_use`/`tool_result` content is never copied — and `turn` is the
    record's `uuid`, falling back to sha1 of `text`.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None, None
    tail_len = min(size, TAIL_BYTES)
    offset = size - tail_len
    try:
        with p.open("rb") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read(tail_len)
    except OSError:
        return None, None
    lines = data.decode("utf-8", "replace").split("\n")
    if offset:
        lines = lines[1:]
    for line in reversed(lines):
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    piece = block.get("text")
                    if isinstance(piece, str):
                        blocks.append(piece)
        elif isinstance(content, str):
            blocks.append(content)
        joined = "\n".join(blocks)
        turn = record.get("uuid")
        if not (isinstance(turn, str) and turn):
            turn = hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()
        return joined, turn
    return None, None


def status_line(text: str):
    """`(status, line)` of the first keyword line of `text`, else (None, None).

    `status` is the bare keyword; `line` is the whole matching line, redacted
    and truncated to MAX_LINE, so a token the line quotes never travels.
    """
    for raw in text.splitlines():
        line = raw.strip()
        m = STATUS_RE.match(line)
        if m:
            return m.group(1), redact(line)[:MAX_LINE]
    return None, None


def append_inbox(parent: str, record: dict) -> None:
    """Append one JSON line to the parent's inbox: a single O_APPEND `os.write`.

    The whole line plus its newline goes down in one write, so concurrent
    writers (several children reporting to one parent) cannot interleave.
    """
    inbox = ensure_dir(state_dir() / "inbox")
    payload = (json.dumps(record) + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(inbox / ("%s.jsonl" % parent)), flags, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("short write")
    finally:
        os.close(fd)


def run_hook() -> int:
    """The Stop path: stdin -> status line -> one inbox append + the seen marker.

    Any failure — bad stdin, a missing/garbled transcript, a session outside
    the fleet, an unsafe name, a duplicate turn, an unwritable state dir —
    exits 0 with no output. The append happens before the marker is written,
    so a failed append is retried on the next Stop instead of lost.
    """
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        session_id = payload.get("session_id")
        transcript_path = payload.get("transcript_path")
        if not (safe_name(session_id) and isinstance(transcript_path, str)):
            return 0
        name, parent = resolve_name(session_id)
        if not (safe_name(name) and safe_name(parent)):
            return 0
        text, turn = assistant_tail(transcript_path)
        if text is None:
            return 0
        status, line = status_line(text)
        if status is None:
            return 0
        marker = state_dir() / "inbox" / ".seen" / session_id
        try:
            seen = marker.read_bytes()
        except OSError:
            seen = None
        if seen == turn.encode("utf-8"):
            return 0
        append_inbox(parent, {
            "ts": int(time.time()),
            "from": name,
            "session_id": session_id,
            "turn": turn,
            "status": status,
            "line": line,
        })
        ensure_dir(marker.parent)
        atomic_write(marker, turn.encode("utf-8"))
    except Exception:
        return 0
    return 0


def main(argv=None) -> int:
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
