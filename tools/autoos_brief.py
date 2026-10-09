"""Ops brief template store (AO-WRITER-GUARDS P2)."""
from __future__ import annotations

import posixpath
import re
import unicodedata

from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "templates" / "briefs"
_PAT = re.compile(r"\{\{(\w+)\}\}")
_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
_TASK_TYPES = {"ops", "code", "docs", "infra"}
_PATH_FIELDS = {"paths", "files", "playbooks", "test_files"}
_SINGLE_PATH_FIELDS = {"keys_file", "reference_playbook", "state_path"}
_RESERVED_BASENAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)
# Unicode categories that must never appear in any value: controls,
# surrogates, private-use, unassigned, format (zero-width, bidi, soft
# hyphen), and line/paragraph separators. NUL (Cc) is included here.
_BAD_CATS = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")
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
# Base keywords without trailing ':' for start-of-line matching.
_KEYWORD_BASES = tuple(
    dict.fromkeys(k[:-1] if k.endswith(":") else k for k in _KEYWORDS)
)


def _read(name):
    return (_DIR / name).read_text(encoding="utf-8")


def list_templates():
    """Available brief template file names."""
    return ["ops.md", "ops-r3-auth.md"]


def _check_controls(field, text, allow_nl=False, allow_tab=False):
    for ch in text:
        if allow_nl and ch == "\n":
            continue
        if allow_tab and ch == "\t":
            continue
        if unicodedata.category(ch) in _BAD_CATS:
            raise ValueError(
                "%s has control char U+%04X" % (field, ord(ch))
            )


def _reject_keyword_lines(field, text):
    for line in text.splitlines():
        norm = unicodedata.normalize("NFKC", line).strip().casefold()
        if not norm:
            continue
        for kw in _KEYWORD_BASES:
            base = kw.casefold()
            if not norm.startswith(base):
                continue
            rest = norm[len(base):]
            if rest == "" or re.match(r"^\s*:", rest):
                raise ValueError(
                    "%s line starts with template keyword %r: %r"
                    % (field, kw, line.strip()[:60])
                )


def _check_single_path(field, s, allow_absolute=False):
    _check_controls(field, s)
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("%s must be ASCII-only: %r" % (field, s))
    if s != s.strip():
        raise ValueError("%s has leading/trailing whitespace: %r" % (field, s))
    if "\\" in s:
        raise ValueError("%s has backslash: %r" % (field, s))
    if re.match(r"^[A-Za-z]:", s):
        raise ValueError("%s has drive letter: %r" % (field, s))
    if any(c in s for c in "*?[]"):
        raise ValueError("%s has glob: %r" % (field, s))
    if s.endswith("/"):
        raise ValueError("%s is a directory (trailing /): %r" % (field, s))
    if s.endswith("."):
        raise ValueError("%s has trailing '.': %r" % (field, s))
    if len(s) > 200:
        raise ValueError("%s exceeds 200 chars: %r" % (field, s[:60]))
    if s.startswith("/") or s.startswith("\\"):
        if not allow_absolute:
            raise ValueError("%s is absolute: %r" % (field, s))
    parts = s.split("/")
    body = parts[1:] if (allow_absolute and s.startswith("/")) else parts
    if "" in body:
        raise ValueError("%s has empty component: %r" % (field, s))
    if "." in parts:
        raise ValueError("%s has dot component: %r" % (field, s))
    if ".." in parts:
        raise ValueError("%s has '..' component: %r" % (field, s))
    for comp in body:
        if comp != comp.strip():
            raise ValueError(
                "%s component has whitespace: %r" % (field, s))
        if comp.endswith("."):
            raise ValueError(
                "%s component has trailing '.': %r" % (field, s))
        stem = comp.split(".")[0]
        if stem.upper() in _RESERVED_BASENAMES:
            raise ValueError(
                "%s has reserved device name: %r" % (field, s))
    if posixpath.normpath(s) != s:
        raise ValueError("%s is not normalised: %r" % (field, s))
    _reject_keyword_lines(field, s)


def _validate_paths(field, value, allow_absolute=False):
    if type(value) is str:
        _check_controls(field, value)
        if "\n" in value or "\r" in value:
            raise ValueError("%s must be single-line" % field)
        if value != value.strip():
            raise ValueError(
                "%s has leading/trailing whitespace: %r" % (field, value))
        norm = []
        for raw in value.split(","):
            s = raw.strip()
            if not s:
                raise ValueError("%s has empty entry" % field)
            _check_single_path(field, s, allow_absolute)
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
    if type(value) is list:
        items = list(value)
        if len(items) == 0:
            raise ValueError("%s is empty" % field)
        norm = []
        for p in items:
            if type(p) is not str:
                raise ValueError(
                    "%s entry must be str, got %r"
                    % (field, type(p).__name__)
                )
            if not p.strip():
                raise ValueError("%s has empty/whitespace entry" % field)
            if p != p.strip():
                raise ValueError(
                    "%s entry has leading/trailing whitespace: %r"
                    % (field, p))
            s = p
            _check_controls(field, s)
            if "\n" in s or "\r" in s:
                raise ValueError("%s entry must be single-line" % field)
            _check_single_path(field, s, allow_absolute)
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


def _validate_single_path(field, value, allow_absolute=False):
    if type(value) is not str:
        raise ValueError(
            "%s must be str, got %r" % (field, type(value).__name__)
        )
    if not value.strip():
        raise ValueError("%s is empty" % field)
    if value != value.strip():
        raise ValueError(
            "%s has leading/trailing whitespace: %r" % (field, value))
    _check_controls(field, value)
    if "\n" in value or "\r" in value:
        raise ValueError("%s must be single-line" % field)
    s = value
    if "," in s:
        raise ValueError("%s must be a single path: %r" % (field, s))
    _check_single_path(field, s, allow_absolute)
    return s


def render_brief(task_type, r_level, fields):
    """Render the ops brief; R3 appends the auth clause (single pass)."""
    if type(r_level) is not str or r_level not in _RANK:
        raise ValueError("unknown r_level %r" % (r_level,))
    if type(task_type) is not str:
        raise ValueError(
            "task_type must be str, got %r"
            % (type(task_type).__name__,)
        )
    _check_controls("task_type", task_type)
    task_norm = task_type.strip().lower()
    if task_norm not in _TASK_TYPES:
        raise ValueError("unknown task_type %r" % (task_type,))
    if not (task_norm == "ops" or _RANK[r_level] >= _RANK["R2"]):
        raise ValueError(
            "no template for task_type=%r r_level=%r" % (task_type, r_level)
        )
    if type(fields) is not dict:
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
        elif type(v) is str and not v.strip():
            empty.append(k)
        elif type(v) is list and (
            len(v) == 0
            or all(
                el is None or (type(el) is str and not el.strip())
                for el in v
            )
        ):
            empty.append(k)
    if missing or unknown or empty:
        raise ValueError("missing=%s unknown=%s empty=%s" % (missing, unknown, empty))
    # Strict types: no str() coercion; lists only for path-like fields.
    # Exact str required (str subclasses rejected). Exact dict/list
    # required (subclasses, tuples, sets, generators refused).
    for k, v in fields.items():
        if k in _PATH_FIELDS or k in _SINGLE_PATH_FIELDS:
            continue
        if type(v) is not str:
            raise ValueError(
                "%s must be str, got %r"
                % (k, type(v).__name__)
            )
        if k != "invariants":
            if len(v) > 500:
                raise ValueError("%s exceeds 500 chars" % k)
            _check_controls(k, v)
            if "\n" in v or "\r" in v:
                raise ValueError("%s must be single-line" % k)
        else:
            if len(v) > 2000:
                raise ValueError("%s exceeds 2000 chars" % k)
            _check_controls(k, v, allow_nl=True, allow_tab=True)
        _reject_keyword_lines(k, v)
    path_norm = {}
    for k in _PATH_FIELDS:
        if k in fields:
            path_norm[k] = _validate_paths(k, fields[k])
    single_norm = {}
    for k in _SINGLE_PATH_FIELDS:
        if k in fields:
            single_norm[k] = _validate_single_path(
                k, fields[k], allow_absolute=(k == "keys_file"))
    # P2 files-subset fence: every file the writer is expected to change or
    # add (files, playbooks, test_files, state_path) must be a member of
    # paths, compared case-insensitively after the same normalisation. The
    # union then stays <= 3 automatically. keys_file/reference_playbook stay
    # read-only refs.
    if "paths" in path_norm:
        _allowed = {s.lower() for s in path_norm["paths"]}
        for _k in ("files", "playbooks", "test_files"):
            if _k in path_norm:
                for _s in path_norm[_k]:
                    if _s.lower() not in _allowed:
                        raise ValueError(
                            "%s entry %r not in paths" % (_k, _s))
        if "state_path" in single_norm:
            if single_norm["state_path"].lower() not in _allowed:
                raise ValueError(
                    "state_path entry %r not in paths"
                    % (single_norm["state_path"],))
    # Plain substring replacement only, single pass so a value holding
    # "{{x}}" is never re-expanded; no f-string eval, no format().
    shown = {}
    for k, v in fields.items():
        if k in _PATH_FIELDS:
            shown[k] = ", ".join(path_norm[k])
        elif k in _SINGLE_PATH_FIELDS:
            shown[k] = single_norm[k]
        elif k == "invariants":
            shown[k] = "\n".join("  - " + ln.strip() for ln in v.splitlines()) if ("\n" in v or "\r" in v) else "  - " + v.strip()
            for ln in shown[k].splitlines():
                if not ln.startswith("  - "):
                    raise ValueError("invariants render missing bullet prefix")
        else:
            shown[k] = v
    out = _PAT.sub(lambda m: shown[m.group(1)], text)
    if r_level == "R3":
        out = out.rstrip("\n") + "\n" + _read("ops-r3-auth.md")
    return out
