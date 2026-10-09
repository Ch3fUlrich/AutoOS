"""Ops brief template store (AO-WRITER-GUARDS P2)."""
from __future__ import annotations

import re

from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "templates" / "briefs"
_PAT = re.compile(r"\{\{(\w+)\}\}")
_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
_TASK_TYPES = {"ops", "code", "docs", "infra"}
_PATH_FIELDS = {"paths", "files", "playbooks", "test_files"}
# Template section keywords: a value line starting with one of these could
# break out of its field and forge a new section when rendered.
_KEYWORDS = (
    "SECRETS:",
    "SECRETS",
    "DONE",
    "FILES",
    "GOAL:",
    "GOAL",
    "INVARIANTS",
    "ALERTS",
    "EDIT METHOD",
    "REPORT:",
    "REPORT",
    "MISSING",
    "INPUT_REQUIRED",
)


def _read(name):
    return (_DIR / name).read_text(encoding="utf-8")


def list_templates():
    """Available brief template file names."""
    return ["ops.md", "ops-r3-auth.md"]


def _reject_keyword_lines(field, text):
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        for kw in _KEYWORDS:
            if up.startswith(kw):
                raise ValueError(
                    "%s line starts with template keyword %r: %r"
                    % (field, kw, line.strip()[:60])
                )


def _check_single_path(field, s):
    parts = s.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("%s has '..' component: %r" % (field, s))
    if s.startswith("/") or s.startswith("\\"):
        raise ValueError("%s is absolute: %r" % (field, s))
    if "\\" in s:
        raise ValueError("%s has backslash: %r" % (field, s))
    if re.match(r"^[A-Za-z]:", s):
        raise ValueError("%s has drive letter: %r" % (field, s))
    if any(c in s for c in "*?[]"):
        raise ValueError("%s has glob: %r" % (field, s))
    if s.endswith("/"):
        raise ValueError("%s is a directory (trailing /): %r" % (field, s))


def _validate_paths(field, value):
    if isinstance(value, bool):
        raise ValueError("%s must be str or list, got bool" % field)
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) == 0:
            raise ValueError("%s is empty" % field)
        norm = []
        for p in items:
            if isinstance(p, bool) or not isinstance(p, str):
                raise ValueError(
                    "%s entry must be str, got %r"
                    % (field, type(p).__name__ if not isinstance(p, bool) else "bool")
                )
            if not p.strip():
                raise ValueError("%s has empty/whitespace entry" % field)
            s = p.strip()
            if "\n" in s or "\r" in s:
                raise ValueError("%s entry must be single-line" % field)
            _check_single_path(field, s)
            _reject_keyword_lines(field, s)
            norm.append(s)
        seen = set()
        for s in norm:
            k = s.lower()
            if k in seen:
                raise ValueError("%s has duplicate path %r" % (field, s))
            seen.add(k)
        if len(norm) > 3:
            raise ValueError("%s has %d entries, max 3" % (field, len(norm)))
        return norm
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("%s is empty" % field)
        if "\n" in value or "\r" in value:
            raise ValueError("%s must be single-line" % field)
        norm = []
        for raw in value.split(","):
            s = raw.strip()
            if not s:
                raise ValueError("%s has empty entry" % field)
            _check_single_path(field, s)
            _reject_keyword_lines(field, s)
            norm.append(s)
        seen = set()
        for s in norm:
            k = s.lower()
            if k in seen:
                raise ValueError("%s has duplicate path %r" % (field, s))
            seen.add(k)
        if len(norm) > 3:
            raise ValueError("%s has %d entries, max 3" % (field, len(norm)))
        return norm
    raise ValueError(
        "%s must be str or list, got %r" % (field, type(value).__name__)
    )


def render_brief(task_type, r_level, fields):
    """Render the ops brief; R3 appends the auth clause (single pass)."""
    if r_level not in _RANK:
        raise ValueError("unknown r_level %r" % (r_level,))
    if isinstance(task_type, bool) or not isinstance(task_type, str):
        raise ValueError(
            "task_type must be str, got %r"
            % (type(task_type).__name__,)
        )
    task_norm = task_type.strip().lower()
    if task_norm not in _TASK_TYPES:
        raise ValueError("unknown task_type %r" % (task_type,))
    if not (task_norm == "ops" or _RANK[r_level] >= _RANK["R2"]):
        raise ValueError(
            "no template for task_type=%r r_level=%r" % (task_type, r_level)
        )
    if not isinstance(fields, dict):
        raise ValueError("fields must be a dict, got %r" % (type(fields).__name__,))
    text = _read("ops.md")
    wants = sorted(set(_PAT.findall(text)))
    missing = [p for p in wants if p not in fields]
    unknown = [k for k in fields if k not in wants]
    empty = []
    for k in wants:
        if k not in fields:
            continue
        v = fields[k]
        if v is None:
            empty.append(k)
        elif isinstance(v, str) and not v.strip():
            empty.append(k)
        elif isinstance(v, (list, tuple)) and (
            len(v) == 0
            or all(
                el is None or (isinstance(el, str) and not el.strip())
                for el in v
            )
        ):
            empty.append(k)
    if missing or unknown or empty:
        raise ValueError("missing=%s unknown=%s empty=%s" % (missing, unknown, empty))
    # Strict types: no str() coercion; lists only for path-like fields.
    for k, v in fields.items():
        if k in _PATH_FIELDS:
            continue
        if isinstance(v, bool) or not isinstance(v, str):
            raise ValueError(
                "%s must be str, got %r"
                % (k, type(v).__name__ if not isinstance(v, bool) else "bool")
            )
        if k != "invariants" and ("\n" in v or "\r" in v):
            raise ValueError("%s must be single-line" % k)
        _reject_keyword_lines(k, v)
    path_norm = {}
    for k in _PATH_FIELDS:
        if k in fields:
            path_norm[k] = _validate_paths(k, fields[k])
    # Plain substring replacement only, single pass so a value holding
    # "{{x}}" is never re-expanded; no f-string eval, no format().
    shown = {}
    for k, v in fields.items():
        if k in _PATH_FIELDS:
            shown[k] = ", ".join(path_norm[k])
        elif k == "invariants":
            shown[k] = "\n".join("  - " + ln.strip() for ln in v.splitlines()) if ("\n" in v or "\r" in v) else "  - " + v.strip()
        else:
            shown[k] = v
    out = _PAT.sub(lambda m: shown[m.group(1)], text)
    if r_level == "R3":
        out = out.rstrip("\n") + "\n" + _read("ops-r3-auth.md")
    return out
