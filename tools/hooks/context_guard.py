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

Thresholds come from the environment: `AUTOOS_CTX_HANDOFF` (default 270000) and
`AUTOOS_CTX_HARD` (default 300000); a non-integer or empty value falls back.
State lives under `$XDG_STATE_HOME/autoos/ctx-guard/`, else
`~/.local/state/autoos/ctx-guard/`.

`fill_from_usage` and `resolve_thresholds` are the shared definitions: the OpenCode
lane supervisor (tools/oc_l1_recycle.py) reuses them, plus the state dir, so both
sides agree on what "full" means.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
import time
from pathlib import Path

# Only the last 256 KB of the transcript is read — enough to hold the most
# recent assistant record while bounding the read on a many-megabyte file.
TAIL_BYTES = 256 * 1024

# The three usage fields that together fill the context window.
_USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens",
                 "cache_creation_input_tokens")

DEFAULT_HANDOFF = 270000
DEFAULT_HARD = 300000

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


def fill_from_usage(usage) -> int | None:
    """The context fill of one usage record: input + cache read + cache write.

    The one definition of "fill" - the hook's transcript scan and the OpenCode
    lane supervisor (tools/oc_l1_recycle.py) both call it. A missing field counts
    as zero; a field that is not a plain integer (or a non-dict usage) yields
    None, never a guess.
    """
    if not isinstance(usage, dict):
        return None
    total = 0
    for field in _USAGE_FIELDS:
        value = usage.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        total += value
    return total


def resolve_thresholds(handoff=None, hard=None) -> tuple[int, int]:
    """(handoff, hard) thresholds: the given value, else the environment, else the defaults."""
    return (handoff if handoff is not None else _env_int("AUTOOS_CTX_HANDOFF", DEFAULT_HANDOFF),
            hard if hard is not None else _env_int("AUTOOS_CTX_HARD", DEFAULT_HARD))


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
    return fill_from_usage(usage)


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
    """Whether to announce this fill: new/garbled state, handoff->hard, +10k growth, or a fill drop.

    An escalation is judged before the growth delta, so crossing the hard cap is
    announced even when the fill barely moved since the last handoff.
    """
    stored = read_state(session_id)
    stored_ctx = stored.get("ctx") if stored else None
    if not isinstance(stored_ctx, int) or isinstance(stored_ctx, bool):
        return True  # no state, or a garbled one: announce
    if stored.get("level") == "handoff" and level == "hard":
        return True
    # growth since the last announcement; a fill that fell (auto-compact) re-arms the guard
    return ctx >= stored_ctx + REEMIT_DELTA or ctx < stored_ctx


def clear_state(session_id: str) -> None:
    """Drop the session's announce state; a missing file is fine."""
    try:
        (state_dir() / ("%s.json" % session_id)).unlink()
    except OSError:
        pass


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
        handoff, hard = resolve_thresholds()
        if ctx < handoff:
            clear_state(session_id)  # back under the line (compaction): the next climb is announced afresh
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
    """`ready <handoff-path> [--reason cap|router|operator|planned]`.

    Validate the real card with PATH's handoff-check (or executable
    AUTOOS_HANDOFF_CHECK override), without --log or live-state discovery.
    Identity, launch time, usage and agents are null when not observed here.
    """
    if not args or args[0].startswith("--"):
        print("context_guard: ready needs <handoff-path>", file=sys.stderr)
        return 2
    if len(args) >= 2 and args[1] == "--reason" and (
            len(args) == 2 or args[2].startswith("--")):
        print("context_guard: --reason needs a value", file=sys.stderr)
        return 2
    reason = args[2] if len(args) == 3 and args[1] == "--reason" else "cap"
    if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--reason"):
        print("context_guard: ready needs <handoff-path>", file=sys.stderr)
        return 2
    if reason not in ("cap", "router", "operator", "planned"):
        print("context_guard: invalid ready reason: %s" % reason, file=sys.stderr)
        return 2
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not safe_id(session_id):
        print("context_guard: ready needs a valid CLAUDE_CODE_SESSION_ID", file=sys.stderr)
        return 2
    try:
        handoff = Path(args[0])
        card = handoff.with_suffix(".card.md")
        for label, path in (("handoff", handoff), ("card", card)):
            if not path.is_file():
                raise OSError("%s file missing: %s" % (label, path))
        raw_handoff, raw_card = handoff.read_bytes(), card.read_bytes()
        command = os.environ.get("AUTOOS_HANDOFF_CHECK") or "handoff-check"
        checker = shutil.which(command, path=os.environ.get("PATH", ""))
        if checker is None:
            raise OSError("handoff-check not found on PATH: %s" % command)
        result = subprocess.run([checker, str(card)], capture_output=True,
                                text=True, timeout=10)
        if result.returncode:
            print("context_guard: handoff-check refused ready (exit %s):\n%s%s"
                  % (result.returncode, result.stdout, result.stderr), file=sys.stderr)
            return 2
        if handoff.read_bytes() != raw_handoff or card.read_bytes() != raw_card:
            raise OSError("handoff or card changed during ready; rerun ready")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("context_guard: ready validation failed: %s" % exc, file=sys.stderr)
        return 2
    try:
        marker = {"v": 2, "session_id": session_id, "handoff": args[0],
              "card": str(card), "handoff_sha256": hashlib.sha256(raw_handoff).hexdigest(),
              "card_sha256": hashlib.sha256(raw_card).hexdigest(),
              "session_name": None, "role": None, "launched_at": None,
              "ctx_tokens": None, "reason": reason, "agents": None,
              "ts": time.time(), "cwd": os.getcwd()}
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ("%s.ready.json" % session_id)
        _write_json(destination, marker)
        try:
            if handoff.read_bytes() != raw_handoff or card.read_bytes() != raw_card:
                raise OSError("handoff or card changed during marker write; rerun ready")
        except OSError:
            destination.unlink(missing_ok=True)
            raise
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
