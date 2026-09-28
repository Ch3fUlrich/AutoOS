"""State-card checker: the §1 shape of `<RUN>/status/<name>.card.md`
(RESTART spec §1; lane R2a; §3's `Q`-thread rule cites it).

`autoos-agent.py card check <file>` is the surface a session runs; the rules
themselves live here so the pack builder (§3) and any later caller reads one
table instead of restating it.

    check_card(text)  -> [Problem(line, text), ...]   # pure
    check_file(path)  -> the same, or CardUnreadable  # read-only

What it checks (spec §1):

* the first line is the header — `# card <name> — <UTC> | gen=<id> |
  context <n>k/<cap>k | last-event <position>` — with each field present exactly
  once and each value in its shape.
* the six sections appear in order — goal, state, next, threads, traps,
  operator — each present, each inside its own cap.
* the file is at most 40 lines, and every line at most 200 characters.
* `last-event` parses as a position, through ``autoos_inbox.parse_position``:
  §0 keeps one position parser, and this module does not own a second one.
* a `threads` line whose id matches the Q-id shape (`^[Qq][-:]?\\d`, so `Q-008`
  and `q-008` but not `QUOTE-2`) carries an `asked <time>` field, so the pack's
  `open questions` section can print it (§3).

Two counting decisions §1 leaves open, settled here because a successor has to
write to the same numbers: a section's line count is its **content lines** — the
heading line and blank lines are not content, but every line in the file counts
toward the 40. So the caps (3+6+3+12+8+4) plus six headings plus the header can
overflow the total on purpose: the total is what binds a real card. A card is
never *converted* from the old status file (§1), so an unknown heading like
`## Decisions + why` is a problem, not a synonym.

Nothing here writes: a card is written only by the session it names (§1, one
writer per file), and `card check` only reads it — safe to run twice in a row.
"""
from __future__ import annotations

import io
import re

import autoos_inbox as inbox

# §1's table, in order: (section, max content lines).
SECTIONS = (
    ("goal", 3),
    ("state", 6),
    ("next", 3),
    ("threads", 12),
    ("traps", 8),
    ("operator", 4),
)
SECTION_ORDER = ("header", "goal", "state", "next", "threads", "traps", "operator")
MAX_TOTAL_LINES = 40
MAX_LINE_CHARS = 200

_HEADER_START_RE = re.compile(r"^#\s*card\s+(?P<rest>.*)$")
_CONTEXT_RE = re.compile(r"^\d+k\s*/\s*\d+k$")
# The thread id is the first field that holds text: `- Q-008 | …`, `Q-008 | …`
# or a table row `| Q-008 | …`.
_THREAD_ID_RE = re.compile(r"^\s*[-*|]?\s*(?P<id>[^\s|]+)")
_ASKED_RE = re.compile(r"\basked\s+\S+")
# The open-question id shape (§3): `Q-008`, `q-008`, `Q:008`, `Q008`. An id that
# merely begins with a Q — `QUOTE-2`, `Query-1` — is a lane, not a question.
_Q_ID_RE = re.compile(r"^[Qq][-:]?\d")

# A card saved from Windows PowerShell carries a BOM; it must still read.
_READ_ENCODING = "utf-8-sig"

_ORDER_TEXT = "order: " + ", ".join(SECTION_ORDER)
_HEADER_SHAPE = ("expected `# card <name> — <UTC> | gen=<id> | context <n>k/<cap>k "
                 "| last-event <position>`")
# (field key, the shape named in the message)
_REQUIRED_FIELDS = (("gen", "gen=<manifest-id>"),
                    ("context", "context <n>k/<cap>k"),
                    ("last-event", "last-event <position>"))


class CardUnreadable(Exception):
    """The named file is not there, is not readable or is not text."""

    def __init__(self, path, reason):
        super().__init__("cannot read %s: %s" % (path, reason))
        self.path = path


class Problem:
    """One thing wrong with a card, and the 1-based line it is on."""

    def __init__(self, line, text):
        self.line = line
        self.text = text

    def __repr__(self):
        return "Problem(line=%d, text=%r)" % (self.line, self.text)


def heading_section(line: str):
    """The section name a `## <name>` heading declares, lowercased, or None when
    the line is not a heading. A trailing `:` and a note after the name are
    ignored, so `## traps: what bit us` is `traps` and the older
    `## Goal (priority order, who set it)` is `goal`."""
    if not line.startswith("## "):
        return None
    rest = line[3:].strip()
    if not rest:
        return None
    first = rest.split(":", 1)[0].strip().split(" ")[0].strip().lower()
    return first or None


def _add(problems, line, text):
    problems.append(Problem(line, text))


def split_lines(text: str):
    """The card's lines, 1-based by position: no trailing-newline artifact, no
    carriage returns, and an empty file is zero lines."""
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return []
    return [line.rstrip("\r") for line in text.split("\n")]


def _check_header(line, problems, at):
    """Problems with the header field shapes; `at` is its line number."""
    parts = line.split("|")
    found = _HEADER_START_RE.match(parts[0].strip())
    if not found:
        _add(problems, at, "not a card header — %s" % (_HEADER_SHAPE,))
        return
    before, sep, after = found.group("rest").partition("—")
    if not sep:
        _add(problems, at, "header has no `—` between the card name and its UTC time "
                           "— %s" % (_HEADER_SHAPE,))
        return
    name, stamp = before.strip(), after.strip()
    if not name:
        _add(problems, at, "header has no card name — %s" % (_HEADER_SHAPE,))
    if not stamp or stamp != stamp.split(" ")[0]:
        _add(problems, at, "header UTC %r is not a single UTC second "
                           "(YYYY-MM-DDTHH:MM:SSZ)" % (stamp,))
    else:
        try:
            inbox.parse_position(stamp)
        except ValueError as exc:
            _add(problems, at, "header UTC %r is not a UTC second (%s)" % (stamp, exc))

    seen = {}
    for field_text in parts[1:]:
        field = field_text.strip()
        if not field:
            _add(problems, at, "header has an empty field — %s" % (_HEADER_SHAPE,))
            continue
        key, _, value = field.partition(" ")
        if key.startswith("gen="):
            seen["gen"] = seen.get("gen", 0) + 1
            if len(key) == 4:
                _add(problems, at, "header field %r has no manifest id in gen=<id>"
                     % (field,))
            elif value:
                _add(problems, at, "header gen=<id> holds a space: %r" % (field,))
        elif key == "context":
            seen["context"] = seen.get("context", 0) + 1
            if not _CONTEXT_RE.match(value):
                _add(problems, at, "header field %r is not `context <n>k/<cap>k`"
                     % (field,))
        elif key == "last-event":
            seen["last-event"] = seen.get("last-event", 0) + 1
            try:
                inbox.parse_position(value)
            except ValueError as exc:
                _add(problems, at, "header last-event %r is not a position (%s)"
                     % (value, exc))
        else:
            _add(problems, at, "header has an unexpected field %r — %s"
                 % (field, _HEADER_SHAPE))
    for key, shape in _REQUIRED_FIELDS:
        count = seen.get(key, 0)
        if not count:
            _add(problems, at, "header has no %s field" % (shape,))
        elif count > 1:
            _add(problems, at, "header has %d '%s' fields — one %s per card"
                 % (count, key, shape))


def _section_owner(lines, caps):
    """For each line, the section that owns it, as (owner, is_heading) pairs: a
    section name, `"?"` under an unknown heading, or None before the first
    heading. A heading line owns its section but is not content of it — the caps
    count content lines, which is what a successor has to fit."""
    owners = []
    current = None
    for line in lines:
        name = heading_section(line)
        if name is None:
            owners.append((current, False))
        else:
            current = name if name in caps else "?"
            owners.append((current, True))
    return owners


def _check_sections(lines, problems, header_line):
    """Presence, order, duplicates, caps, the §3 `Q` field and stray text."""
    caps = dict(SECTIONS)
    order = {name: position for position, name in enumerate(SECTION_ORDER)}
    headings = [(index, heading_section(line)) for index, line in enumerate(lines, 1)
                if heading_section(line) is not None]
    first_at, recognised = {}, []
    for index, name in headings:
        if name not in caps:
            _add(problems, index, "%r is not a card section (expected one of: %s)"
                 % (lines[index - 1], ", ".join(caps)))
            continue
        if name in first_at:
            _add(problems, index, "duplicate section '%s' (first at line %d)"
                 % (name, first_at[name]))
            continue
        first_at[name] = index
        recognised.append((index, name))

    for position in range(1, len(recognised)):
        index, name = recognised[position]
        _previous, before = recognised[position - 1]
        if order[name] < order[before]:
            _add(problems, index, "section '%s' is out of order: '%s' comes before it "
                                  "(%s)" % (name, before, _ORDER_TEXT))

    for name, _cap in SECTIONS:
        if name in first_at:
            continue
        later = [index for index, other in recognised if order[other] > order[name]]
        _add(problems, later[0] if later else max(1, len(lines)),
             "missing section '%s' (%s)" % (name, _ORDER_TEXT))

    owners = _section_owner(lines, caps)

    def content_of(name):
        return [index for index in range(1, len(lines) + 1)
                if owners[index - 1][0] == name and not owners[index - 1][1]
                and lines[index - 1].strip()]

    for name, cap in SECTIONS:
        content = content_of(name)
        if len(content) > cap:
            _add(problems, content[cap],
                 "section '%s' is over its %d-line cap (%d content lines; this is "
                 "the %dth)" % (name, cap, len(content), cap + 1))

    for index in content_of("threads"):
        line = lines[index - 1]
        found = _THREAD_ID_RE.match(line)
        thread_id = found.group("id") if found else ""
        if _Q_ID_RE.match(thread_id) and not _ASKED_RE.search(line):
            _add(problems, index, "open question '%s' has no `asked <time>` field "
                                  "(§3: the pack prints open questions)" % (thread_id,))

    for index, line in enumerate(lines, 1):
        if index == header_line or not line.strip():
            continue
        if heading_section(line) is not None:
            break
        _add(problems, index, "%r is before the first section heading — every line "
                             "belongs to a section" % (line,))


def _check_limits(lines, problems):
    if len(lines) > MAX_TOTAL_LINES:
        _add(problems, MAX_TOTAL_LINES + 1,
             "the card is %d lines, over the %d-line cap"
             % (len(lines), MAX_TOTAL_LINES))
    for index, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_CHARS:
            _add(problems, index, "%d characters, over the %d-char limit"
                 % (len(line), MAX_LINE_CHARS))


def check_card(text: str):
    """Every Problem with a card's text, sorted by line (checker order within a
    line). An empty list means the card is valid."""
    lines = split_lines(text)
    problems = []
    if not lines:
        _add(problems, 1, "the card is empty — no card header and no sections "
                          "(%s)" % (_ORDER_TEXT,))
        return problems
    header_line = next((index for index, line in enumerate(lines, 1) if line.strip()), 1)
    if header_line != 1:
        _add(problems, 1, "the header is not the first line — line 1 is blank and "
                          "the card starts on line %d" % (header_line,))
    _check_header(lines[header_line - 1], problems, header_line)
    _check_limits(lines, problems)
    _check_sections(lines, problems, header_line)
    return sorted(problems, key=lambda problem: problem.line)


def read_card(path: str):
    """The card's text, or CardUnreadable."""
    try:
        with io.open(path, encoding=_READ_ENCODING) as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise CardUnreadable(path, exc) from exc


def check_file(path: str):
    """`check_card` over the named file."""
    return check_card(read_card(path))
