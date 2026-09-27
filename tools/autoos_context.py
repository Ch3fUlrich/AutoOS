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
`source` the CLI reports is `policy` when the cap comes from the registry's
policy.handoff_caps, `default` when the fallback table is used.
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

# The registry file that carries policy.handoff_caps (the single source).
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "catalog" / "ai-registry.json"


def caps_from_registry(registry_dict: dict) -> list[tuple[str, int, int]]:
    """Convert policy.handoff_caps to the (substring, window, cap) row format.

    Non-"*" rows come first in file order, "*" last (the default). Each row's
    `match` list may carry multiple substrings (e.g. opus+ fable); each becomes
    its own row so cap_for's first-match-wins logic stays unchanged.

    A row is usable only when every key the schema requires is present:
    `match`, `window`, `cap_tokens` and `cap_fraction` (the schema's
    provider-independent cap formula is window * cap_fraction). A partial row
    (measured: one missing cap_fraction) is skipped, so a registry carrying
    only partial rows falls through to DEFAULT_CAPS and load_caps reports
    source 'default', never 'policy' (review R4FIX, 2026-09-27).
    """
    handoff_caps = registry_dict.get("policy", {}).get("handoff_caps", {})
    if not isinstance(handoff_caps, dict):
        return list(DEFAULT_CAPS)
    rows = []
    default_row = None
    for _key, entry in handoff_caps.items():
        if not isinstance(entry, dict):
            continue
        match = entry.get("match")
        window = entry.get("window")
        cap = entry.get("cap_tokens")
        fraction = entry.get("cap_fraction")
        if not isinstance(match, list) or not isinstance(window, int) \
                or not isinstance(cap, int) \
                or not isinstance(fraction, (int, float)) \
                or isinstance(fraction, bool):
            continue
        for substring in match:
            if not isinstance(substring, str):
                continue
            if substring == "*":
                default_row = (substring, window, cap)
            else:
                rows.append((substring, window, cap))
    if default_row is not None:
        rows.append(default_row)
    return rows if rows else list(DEFAULT_CAPS)


def _has_usable_rows(registry_dict) -> bool:
    handoff_caps = (registry_dict.get("policy") or {}).get("handoff_caps")
    if not isinstance(handoff_caps, dict):
        return False
    return any(isinstance(e, dict) and isinstance(e.get("match"), list)
               and isinstance(e.get("window"), int)
               and isinstance(e.get("cap_tokens"), int)
               and isinstance(e.get("cap_fraction"), (int, float))
               and not isinstance(e.get("cap_fraction"), bool)
               for e in handoff_caps.values())


def load_caps(path: str | Path | None = None) -> tuple[list[tuple[str, int, int]], str]:
    """Load caps from the registry file, or fall back to DEFAULT_CAPS.

    Returns (caps, source) where source is "policy" when read from the registry,
    "default" when the file is missing/unreadable/malformed. Pure aside from the
    file read itself.
    """
    path = _REGISTRY_PATH if path is None else Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            registry = json.load(fh)
        if not isinstance(registry, dict):
            return list(DEFAULT_CAPS), "default"
        caps = caps_from_registry(registry)
        # caps_from_registry falls back to DEFAULT_CAPS when no row is usable:
        # then the numbers are not the policy's, and the source must say so.
        if caps == DEFAULT_CAPS and not _has_usable_rows(registry):
            return list(DEFAULT_CAPS), "default"
        return caps, "policy"
    except (OSError, ValueError, KeyError):
        return list(DEFAULT_CAPS), "default"


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


def cap_for(model, caps=None) -> int:
    """The handoff cap for `model` from the 8.3 table, or the default row.

    Substring match on the lowercased id, first row wins. A trailing `[1m]`
    marks an explicitly 1M window: it is stripped before matching so the id
    takes its family's 1M row, and an unmatched family still falls back to the
    200k-class `"*"` row. When `caps` is None, loads from the registry's
    policy.handoff_caps (single source); when the registry is missing or
    malformed, falls back to DEFAULT_CAPS.
    """
    if caps is None:
        caps, _ = load_caps()
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
