"""fleet_inject: the Claude Code hook that prints the parent's fleet inbox.

Runs on PostToolUse AND UserPromptSubmit. The hook JSON arrives on stdin
(`session_id`, `hook_event_name`). The session's own name is resolved through
`fleet_report.resolve_name` (this module loads `fleet_report.py` by path with
importlib from its own directory — one home for the helpers, nothing
duplicated), then the cheap `stat` comes first: when
`<state>/inbox/<name>.jsonl` is missing or has not grown past the byte offset
in `<state>/inbox/.cursor/<name>` (default 0), the hook exits 0 with no output
before reading or parsing anything.

Otherwise the bytes `[offset, size)` are read, only COMPLETE lines are kept
(a partial last line waits for its newline), each JSON line is parsed (bad
lines are skipped but still consumed so they are never re-read forever), the
cursor advances through tmp + `os.replace` BEFORE anything is printed, and one
JSON line goes to stdout:

    {"hookSpecificOutput": {"hookEventName": <event>, "additionalContext":
     "FLEET INBOX (n new):\\n<from> <status>: <line>\\n..."}}

At most 20 lines per emission; a `+k more` tail names what is left, and the
cursor stops there so the remainder is delivered by the next call. Lines are
passed through `redact()` again on the way out — defence in depth, a public
repository never sees a token even if an older writer filed one raw.

Fail-silent, like every hook here: bad stdin, a missing inbox, an unsafe name,
an unwritable cursor dir — exit 0 with no output, because a PostToolUse hook
must never break the agent that called it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# Shared helpers (resolve_name, redact, state_dir, safe_name, ensure_dir,
# atomic_write) come from fleet_report.py by path — never a copy that can drift.
def _load_report():
    """The `fleet_report` module loaded from this script's directory, or None."""
    try:
        path = Path(__file__).resolve().with_name("fleet_report.py")
        spec = importlib.util.spec_from_file_location("fleet_report", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


_REPORT = _load_report()

# Lines per emission; the rest wait for the next call ("+k more").
MAX_EMIT = 20


def read_cursor(path) -> int:
    """The stored byte offset for one inbox, 0 when missing or garbled."""
    try:
        value = int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    return value if value > 0 else 0


def take_lines(chunk: bytes):
    """`(records, extra, consumed)` over the complete lines of `chunk`.

    At most `MAX_EMIT` parsed dict records are kept; the record that overflows
    the cap and everything after it stays unread — `extra` counts the records
    that must wait for the next emission. Unparsable complete lines are
    consumed anyway, so one corrupt line cannot poison the file forever.
    `consumed` is the byte count safe to record in the cursor.
    """
    records = []
    extra = 0
    consumed = 0
    capped = False
    cursor = 0
    for raw in chunk.split(b"\n")[:-1]:  # chunk ends on \n, so [:-1] drops only the empty tail
        line_end = cursor + len(raw) + 1
        obj = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                obj = parsed
        except ValueError:
            obj = None
        if capped:
            if obj is not None:
                extra += 1
        elif obj is None:
            consumed = line_end
        elif len(records) < MAX_EMIT:
            records.append(obj)
            consumed = line_end
        else:
            capped = True
            extra += 1
        cursor = line_end
    return records, extra, consumed


def format_row(record: dict) -> str:
    """One `<from> <status>: <line>` row, re-redacted on the way out."""
    frm = record.get("from")
    frm = frm if isinstance(frm, str) and frm else "?"
    status = _REPORT.redact(record.get("status"))
    line = _REPORT.redact(record.get("line"))
    return "%s %s: %s" % (frm, status, line)


def run_hook() -> int:
    """stdin -> the inbox's unread complete lines -> one additionalContext.

    The cursor is advanced BEFORE the print, so a crash mid-emission can only
    re-deliver a line, never lose one. Any failure exits 0 with no output.
    """
    try:
        if _REPORT is None:
            return 0
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        if not isinstance(event, str) or not event:
            event = "PostToolUse"
        name, _parent = _REPORT.resolve_name(payload.get("session_id"))
        if not _REPORT.safe_name(name):
            return 0
        inbox = _REPORT.state_dir() / "inbox" / ("%s.jsonl" % name)
        try:
            size = inbox.stat().st_size
        except OSError:
            return 0
        cursor_path = _REPORT.state_dir() / "inbox" / ".cursor" / name
        offset = read_cursor(cursor_path)
        if size <= offset:
            return 0
        with inbox.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(size - offset)
        cut = data.rfind(b"\n")
        if cut < 0:
            return 0  # only a partial line: nothing is consumed until it completes
        records, extra, consumed = take_lines(data[:cut + 1])
        if consumed:
            _REPORT.ensure_dir(cursor_path.parent)
            _REPORT.atomic_write(cursor_path, str(offset + consumed).encode("utf-8"))
        if not records:
            return 0
        rows = [format_row(record) for record in records]
        context = "FLEET INBOX (%d new):\n%s" % (len(rows), "\n".join(rows))
        if extra:
            context += "\n+%d more" % extra
        out = {"hookSpecificOutput": {"hookEventName": event,
                                      "additionalContext": context}}
        print(json.dumps(out))
    except Exception:
        return 0
    return 0


def main(argv=None) -> int:
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
