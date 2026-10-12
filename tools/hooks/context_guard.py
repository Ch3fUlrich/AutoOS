"""context_guard: a Claude Code PostToolUse hook that flags context-window cap.

The hook reads the hook JSON off stdin (`transcript_path`, `session_id`, `cwd`)
and reports how full the model's context window is by summing the three fields
that occupy it — `input_tokens` plus both cache buckets — of the *last*
assistant usage record near the end of the transcript. When that fill crosses
the handoff threshold it prints one `additionalContext` line telling the agent
to write its handoff and run `ready`; past the hard threshold it says stop. It
also drops a per-session state file so the same fill is not re-announced.

The transcript can be tens of megabytes, so only the tail is read: seek to
`size - TAIL_BYTES`, drop the first partial line, and scan the bounded tail from
the end for the last assistant+usage record. Files are opened through
`Path.open` and read in bounded chunks, never iterated whole.

Everything is stdlib-only and portable (Linux + Windows) via `pathlib`. The hook
mode is deliberately fail-silent — a broken transcript, bad stdin or an
unwritable state dir exits 0 with no output, because a PostToolUse hook must
never break the agent that called it. The `ready` and `status` sub-commands are
the exception: those report errors on stderr and exit non-zero.

Thresholds come from the environment: `AUTOOS_CTX_HANDOFF` (default 230000) and
`AUTOOS_CTX_HARD` (default 250000); a non-integer or empty value falls back.
State lives under `$XDG_STATE_HOME/autoos/ctx-guard/`, else
`~/.local/state/autoos/ctx-guard/`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Only the last 256 KB of the transcript is read — enough to hold the most
# recent assistant record while bounding the read on a many-megabyte file.
TAIL_BYTES = 256 * 1024

# The three usage fields that together fill the context window.
_USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens",
                 "cache_creation_input_tokens")

DEFAULT_HANDOFF = 230000
DEFAULT_HARD = 250000

# Re-announce only once the fill has grown this much past the last emitted one,
# unless the level itself escalates.
REEMIT_DELTA = 10000

# Sentinel for a tail line that is not the assistant+usage record we want.
_SKIP = object()


# A session id becomes a file name: a letter/digit first, then letters, digits, dot, dash, underscore.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def safe_id(session_id) -> bool:
    """True when `session_id` is usable as a file name under the state dir."""
    return isinstance(session_id, str) and _SAFE_ID.match(session_id) is not None


def _write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON via a temp file + rename, so a reader never sees half a file."""
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def state_dir() -> Path:
    """The per-user state directory for the guard (XDG state home, else home)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "autoos" / "ctx-guard"


def _env_int(name: str, default: int) -> int:
    """An integer threshold from the environment, `default` if missing/garbled."""
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def _tail_ctx(line):
    """The context fill of one tail line, `_SKIP`, or None (a non-numeric usage).

    `_SKIP` means "not the record we are looking for" and the scan continues; a
    returned int is the fill, and None marks a record whose usage fields are not
    all integers — that is the last assistant record, so it yields no result.
    """
    text = line.strip()
    if not text:
        return _SKIP
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return _SKIP
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return _SKIP
    message = obj.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return _SKIP
    total = 0
    for field in _USAGE_FIELDS:
        value = usage.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        total += value
    return total


def context_tokens(path) -> int | None:
    """The context fill of the last assistant usage record in the transcript tail.

    Reads only the last `TAIL_BYTES`; the byte offset there is mid-line, so the
    first split element is dropped as a partial line (but never when the whole
    file fit inside the tail). Returns the summed fill, or None when the file is
    missing/unreadable, when no assistant+usage record is in the tail, or when
    that record's usage is non-numeric.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    tail_len = min(size, TAIL_BYTES)
    offset = size - tail_len
    try:
        with p.open("rb") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read(tail_len)
    except OSError:
        return None
    lines = data.decode("utf-8", "replace").split("\n")
    if offset:
        lines = lines[1:]
    for line in reversed(lines):
        result = _tail_ctx(line)
        if result is _SKIP:
            continue
        return result
    return None


def read_state(session_id: str):
    """The stored state dict for a session, or None (missing/garbled)."""
    path = state_dir() / ("%s.json" % session_id)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def should_emit(session_id: str, ctx: int, level: str) -> bool:
    """Whether to announce this fill: new session, handoff->hard, or +10k growth.

    An escalation is judged before the growth delta, so crossing the hard cap is
    announced even when the fill barely moved since the last handoff.
    """
    stored = read_state(session_id)
    if stored is None:
        return True
    if stored.get("level") == "handoff" and level == "hard":
        return True
    stored_ctx = stored.get("ctx")
    if isinstance(stored_ctx, int) and not isinstance(stored_ctx, bool):
        return ctx >= stored_ctx + REEMIT_DELTA
    return False


def write_state(session_id: str, state: dict) -> None:
    """Persist one session's state file (raises on an unwritable state dir)."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / ("%s.json" % session_id), state)


def emit_line(level: str, ctx: int, threshold: int, script_path) -> str:
    """The single-line `additionalContext`, naming the `ready` command to run.

    No newline is embedded: the whole message is one line, as the hook contract
    and the tests require.
    """
    n = ctx // 1000
    h = threshold // 1000
    script = str(Path(script_path).resolve())
    tail = ("then run python3 %s ready <handoff-path> (and SendMessage "
            "RECYCLE-READY <path> to your parent if your brief says so)" % script)
    if level == "hard":
        return "HARD-CAP ctx=%dk >= %dk: no new work; handoff now, %s" % (n, h, tail)
    return ("HANDOFF-NOW ctx=%dk >= %dk: finish the current step, write your "
            "handoff, commit+push, %s" % (n, h, tail))


def run_hook() -> int:
    """The PostToolUse path: read stdin, compute the fill, maybe announce.

    Any failure — bad stdin, missing fields, a missing or garbled transcript, an
    unwritable state dir — exits 0 with no output. The state file is written
    before anything is printed, so a failed write means nothing was announced.
    """
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        transcript_path = payload.get("transcript_path")
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if not (isinstance(transcript_path, str) and safe_id(session_id)
                and isinstance(cwd, str)):
            return 0
        ctx = context_tokens(transcript_path)
        if ctx is None:
            return 0
        handoff = _env_int("AUTOOS_CTX_HANDOFF", DEFAULT_HANDOFF)
        hard = _env_int("AUTOOS_CTX_HARD", DEFAULT_HARD)
        if ctx < handoff:
            return 0
        level = "hard" if ctx >= hard else "handoff"
        threshold = hard if level == "hard" else handoff
        if not should_emit(session_id, ctx, level):
            return 0
        write_state(session_id, {
            "session_id": session_id, "cwd": cwd, "ctx": ctx,
            "ts": time.time(), "level": level,
        })
        line = emit_line(level, ctx, threshold, Path(__file__))
        out = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                      "additionalContext": line}}
        print(json.dumps(out))
    except Exception:
        return 0
    return 0


def cmd_ready(args) -> int:
    """`ready <handoff-path>`: mark a session recycled, keyed by session id.

    The session id comes from `CLAUDE_CODE_SESSION_ID`; a missing id or path is
    a loud error (exit 2), unlike the fail-silent hook.
    """
    if not args:
        print("context_guard: ready needs <handoff-path>", file=sys.stderr)
        return 2
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not safe_id(session_id):
        print("context_guard: ready needs a valid CLAUDE_CODE_SESSION_ID", file=sys.stderr)
        return 2
    marker = {"session_id": session_id, "handoff": args[0],
              "ts": time.time(), "cwd": os.getcwd()}
    try:
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / ("%s.ready.json" % session_id), marker)
    except OSError as exc:
        print("context_guard: cannot write ready marker: %s" % exc, file=sys.stderr)
        return 2
    return 0


def cmd_status() -> int:
    """`status`: one line per state file; skip unreadable or garbled ones."""
    try:
        files = sorted(state_dir().glob("*.json"))
    except OSError:
        return 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        sid = str(data.get("session_id") or path.name.split(".")[0])
        if path.name.endswith(".ready.json"):
            print("%s ready handoff=%s" % (sid, data.get("handoff", "")))
        else:
            ctx = data.get("ctx")
            n = ctx // 1000 if isinstance(ctx, int) and not isinstance(ctx, bool) else "?"
            print("%s level=%s ctx=%sk cwd=%s"
                  % (sid, data.get("level", ""), n, data.get("cwd", "")))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "ready":
        return cmd_ready(argv[1:])
    if argv and argv[0] == "status":
        return cmd_status()
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
