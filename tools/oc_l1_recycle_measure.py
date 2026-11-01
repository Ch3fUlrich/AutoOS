"""oc_l1_recycle_measure.py - what tools/oc_l1_recycle.py reads (AO-OC-CONTEXT-RECYCLE, D-2267).

The context fill of a lane's session, the model window the thresholds are weighed against and
the merge markers a hard-limit restart must wait for. Pure reads, no HTTP, no writes.

fill: input + cache read + cache write of the NEWEST assistant message that carries usage - the
definition context_guard owns (`fill_from_usage`; output tokens never count). Newest is decided by
the records' own timestamp (`time.created`), else their id, NEVER by list position: a server that
ignores `order=desc` would otherwise hand back the oldest record and under-report the fill. Two or
more usage records with no timestamp/id to order them by (or a tie at the top) is `bad`.
"""

import time
from datetime import datetime
from pathlib import Path

from oc_l1_render import MODEL_LIMIT, _registry_models
from context_guard import fill_from_usage

MERGE_LOCK_NAME = "merge.lock"
MERGE_LOCK_MAX_AGE_S = 4 * 3600
GIT_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
OK, NONE, BAD = "ok", "none", "bad"  # fill_of states: a number / no usage record at all / unusable


def _window(model_id, pinned=None):
    """One model's window: its pinned limit.context, else the registry's context_advertised."""
    row = _registry_models().get(str(model_id or "").rsplit("/", 1)[-1]) or {}
    return next((v for v in (pinned, row.get("context_advertised"))
                 if isinstance(v, int) and not isinstance(v, bool) and v > 0), None)


def model_window(lane):
    """The window opencode is told for this lane (see oc_l1_render): the pinned
    model.limit.context, else the registry's context_advertised, else the 128k default. A lane
    with a fallback model gets the SMALLER of the primary's and the fallback's window - it may be
    running on either, and the smaller one compacts first."""
    model = lane.get("model") or {}
    limit = model.get("limit")
    pinned = limit.get("context") if isinstance(limit, dict) else None
    wins = [_window(model.get("modelID"), pinned) or MODEL_LIMIT["context"]]
    if model.get("fallback_modelID"):
        wins.append(_window(model["fallback_modelID"]) or MODEL_LIMIT["context"])
    return min(wins)


# --- measuring ------------------------------------------------------------------


def _first(*vals):
    return next((v for v in vals if v is not None), None)


def _opencode_usage(tokens):
    """opencode's token record mapped onto the three guard fields. A field missing from `input`
    stays None (so the shared sum rejects it); a cache bucket absent altogether is zero."""
    cache = tokens.get("cache", {})
    if not isinstance(cache, dict):
        return {"input_tokens": None}
    return {
        "input_tokens": _first(tokens.get("input"), tokens.get("input_tokens")),
        "cache_read_input_tokens": _first(
            cache.get("read"), cache.get("cacheRead"), cache.get("cache_read"),
            tokens.get("cacheRead"), tokens.get("cache_read_input_tokens"), 0),
        "cache_creation_input_tokens": _first(
            cache.get("write"), cache.get("cacheWrite"), cache.get("cache_write"),
            tokens.get("cacheWrite"), tokens.get("cache_creation_input_tokens"), 0),
    }


def _ts(item):
    """The record's creation time as epoch seconds, or None."""
    info = item.get("info") if isinstance(item.get("info"), dict) else {}
    for src in (info, item):
        t = src.get("time")
        for v in ((t.get("created"), t.get("updated"), t.get("completed")) if isinstance(t, dict)
                  else (t, src.get("created"))):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return v / 1000.0 if v > 1e12 else float(v)
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass
    return None


def _id(item):
    info = item.get("info") if isinstance(item.get("info"), dict) else {}
    v = _first(info.get("id"), item.get("id"))
    return v if isinstance(v, str) and v else None


def _record(item):
    """(fill, output) of one message item; fill is an int, or None when the usage is present but
    not numeric. None for an item that is not an assistant message or carries no usage."""
    if not isinstance(item, dict):
        return None
    info = item.get("info") if isinstance(item.get("info"), dict) else {}
    if "assistant" not in (item.get("type"), item.get("role"), info.get("type"), info.get("role")):
        return None
    tokens = _first(info.get("tokens"), item.get("tokens"), info.get("usage"), item.get("usage"))
    if tokens is None:
        return None
    if not isinstance(tokens, dict):
        return (None, 0)
    fill = fill_from_usage(_opencode_usage(tokens))
    out = _first(tokens.get("output"), tokens.get("output_tokens"))
    return (fill if fill is not None and fill >= 0 else None,
            out if isinstance(out, (int, float)) and not isinstance(out, bool) else 0)


def fill_of(items):
    """(state, fill): (OK, int) for the newest measurable assistant record, (NONE, None) when no
    record carries usage (a session that has not answered yet, or only a turn still in flight),
    (BAD, None) when the newest record's usage is present but unusable or the newest cannot be
    established. An in-flight turn is a record whose mapped fields are all zero AND that carries no
    output; an all-zero record WITH output is a finished turn whose usage did not map: BAD."""
    cands = []
    for item in items if isinstance(items, list) else []:
        rec = _record(item)
        if rec is None or (rec[0] == 0 and not rec[1]):
            continue
        cands.append((item, rec))
    if not cands:
        return NONE, None
    if len(cands) > 1:
        for key in (_ts, _id):
            keys = [key(i) for i, _ in cands]
            if None not in keys:
                break
        else:
            return BAD, None
        order = sorted(range(len(cands)), key=lambda n: keys[n], reverse=True)
        if keys[order[0]] == keys[order[1]]:
            return BAD, None
        cands = [cands[order[0]]]
    fill = cands[0][1][0]
    return (OK, fill) if fill else (BAD, None)


# --- merge marker ---------------------------------------------------------------


def _git_dir(cwd):
    """The git dir of `cwd`: the first `.git` going up that is a directory or a `gitdir:` file
    (a worktree's). A `.git` entry that is neither (a stray file) is stepped over."""
    for d in (cwd, *cwd.parents):
        dot = d / ".git"
        try:
            if dot.is_dir():
                return dot
            if dot.is_file():
                first = dot.read_text(encoding="utf-8", errors="replace").strip()
                if first.lower().startswith("gitdir:"):
                    target = Path(first[7:].strip())
                    return target if target.is_absolute() else (d / target)
        except OSError:
            continue
    return None


def merge_in_progress(lane):
    """A reason string when the lane is inside a merge, else None."""
    lock = Path(lane["scratch_dir"]) / MERGE_LOCK_NAME
    try:
        if time.time() - lock.stat().st_mtime <= MERGE_LOCK_MAX_AGE_S:
            return "lane merge lock %s" % lock
    except OSError:
        pass
    gitdir = _git_dir(Path(lane["cwd"]))
    for marker in GIT_MARKERS if gitdir else ():
        if (gitdir / marker).exists():
            return "git %s in %s" % (marker, gitdir)
    return None
