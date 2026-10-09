"""Ops brief template store (AO-WRITER-GUARDS P2)."""
from __future__ import annotations

import re

from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "templates" / "briefs"
_PAT = re.compile(r"\{\{(\w+)\}\}")
_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


def _read(name):
    return (_DIR / name).read_text(encoding="utf-8")


def list_templates():
    """Available brief template file names."""
    return ["ops.md", "ops-r3-auth.md"]


def render_brief(task_type, r_level, fields):
    """Render the ops brief; R3 appends the auth clause (single pass)."""
    if r_level not in _RANK:
        raise ValueError("unknown r_level %r" % (r_level,))
    if not (task_type == "ops" or _RANK[r_level] >= _RANK["R2"]):
        raise ValueError("no template for task_type=%r r_level=%r" % (task_type, r_level))
    if not isinstance(fields, dict):
        raise ValueError("fields must be a dict, got %r" % (type(fields).__name__,))
    text = _read("ops.md")
    wants = sorted(set(_PAT.findall(text)))
    missing = [p for p in wants if p not in fields]
    unknown = [k for k in fields if k not in wants]
    empty = [k for k in wants if k in fields and (
        fields[k] is None or (isinstance(fields[k], str) and not fields[k].strip())
        or (isinstance(fields[k], (list, tuple)) and len(fields[k]) == 0))]
    if missing or unknown or empty:
        raise ValueError("missing=%s unknown=%s empty=%s" % (missing, unknown, empty))
    paths = fields["paths"]
    n = len(paths) if isinstance(paths, (list, tuple)) else len(
        [s for s in str(paths).split(",") if s.strip()])
    if n > 3:
        raise ValueError("paths has %d entries, max 3" % n)
    # Plain substring replacement only, single pass so a value holding
    # "{{x}}" is never re-expanded; no f-string eval, no format().
    shown = {k: (", ".join(str(p) for p in v) if isinstance(v, (list, tuple)) else str(v))
             for k, v in fields.items()}
    out = _PAT.sub(lambda m: shown[m.group(1)], text)
    if r_level == "R3":
        out = out.rstrip("\n") + "\n" + _read("ops-r3-auth.md")
    return out
