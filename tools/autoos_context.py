"""Context fill of the calling session (routing v2 spec sections 6.1 and 8.3).

Two pure helpers and the transcript lookup behind `autoos-agent.py context`:

    fill_from_transcript(lines) -> {"tokens", "model"} | None
    cap_for(model, caps) -> int
    discover_transcript(cwd, home) -> path | None

`fill_from_transcript` reads Claude Code transcript JSONL: the fill is the
*total input* of the last assistant record that carries a usage object -
prompt tokens plus both cache buckets, because all three occupy the window.
Malformed lines are skipped; a transcript with no usage record returns None,
and the CLI reports `unknown` rather than inventing a number.

`cap_for` is the handoff table of spec 8.3 held as data (D20): substring match
on the lowercased model id, first row wins, `"*"` is the 200k-class default. A
model id ending in `[1m]` asks for the 1M window, so it matches on its base id
and takes the family's 1M row; a family with no 1M row keeps the default. The
`source` the CLI reports is `default` until the recall probe of spec 10 lands
and a measured cap replaces this table.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Spec 8.3, exactly: (lowercased substring, window, hand off at).
# Opus 400k/1M, Fable 400k/1M, Muse Spark 300k/1M, Gemini 200k/1M,
# 200k-class default 150k.
DEFAULT_CAPS = [
    ("opus", 1000000, 400000),
    ("fable", 1000000, 400000),
    ("spark", 1000000, 300000),
    ("gemini", 1000000, 200000),
    ("*", 200000, 150000),
]

# The three usage fields that together fill the context window.
_USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens",
                 "cache_creation_input_tokens")


def fill_from_transcript(lines) -> dict | None:
    """The context fill of the last assistant usage record in a transcript.

    `lines` is any iterable of JSONL strings. Every parse failure and every
    record that is not an assistant message with a usage object is skipped.
    Returns ``{"tokens": int, "model": str | None}``, or None when no record
    carries a usage. Missing usage fields count as zero.
    """
    fill = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens = 0
        for field in _USAGE_FIELDS:
            value = usage.get(field, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tokens += int(value)
        fill = {"tokens": tokens, "model": message.get("model")}
    return fill


def cap_for(model, caps=DEFAULT_CAPS) -> int:
    """The handoff cap for `model` from the 8.3 table, or the default row.

    Substring match on the lowercased id, first row wins. A trailing `[1m]`
    marks an explicitly 1M window: it is stripped before matching so the id
    takes its family's 1M row, and an unmatched family still falls back to the
    200k-class `"*"` row.
    """
    name = str(model or "").lower()
    if name.endswith("[1m]"):
        name = name[:-4]
    default = None
    for substring, _window, cap in caps:
        if substring == "*":
            default = cap
            continue
        if substring in name:
            return cap
    if default is None:  # a caller-supplied table with no `"*"` row
        return caps[-1][2]
    return default


def project_dir(cwd, home=None) -> Path:
    """Claude Code's transcript directory for `cwd` (ADR 0001 layout)."""
    home = os.path.expanduser("~") if home is None else home
    slug = str(cwd).replace("/", "-").replace(".", "-")
    return Path(home) / ".claude" / "projects" / slug


def discover_transcript(cwd, home=None) -> str | None:
    """The newest `*.jsonl` (by mtime) in this cwd's project dir, else None.

    Spec 6.1: a session is the most recently written transcript in the
    directory Claude Code derives from the working directory. One file *is* one
    session (ADR 0001), so mtime is the only ordering fact needed here.
    """
    directory = project_dir(cwd, home)
    try:
        files = [p for p in directory.glob("*.jsonl") if p.is_file()]
    except OSError:
        return None
    if not files:
        return None
    return str(max(files, key=lambda p: p.stat().st_mtime))
