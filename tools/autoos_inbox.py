"""Shared inbox definitions (RESTART spec §0; lane R1).

One home for what an inbox record *is*, so the card checker, the pack builder
and the `inbox` verb cannot drift apart:

    parse_file(path)         -> (records, malformed_line_numbers)
    read_records(path)       -> records, or NoTimestampedRecords
    read_since(path, since)  -> at-least-once window, file order, `(late)` flags
    parse_position("…Z#2")   -> Position
    card_last_event(path)    -> the position a session stopped reading at
    inbox_path(name)         -> <RUN>/inbox/<name>.md

The RUN dir comes from ``$AUTOOS_RUN_DIR`` only — the tools never guess it, and
an unset variable with no ``--file`` is an error, not an empty read.

Reading is **at-least-once**: resuming at a position returns every record after
it, so a record that lands in the same second as the one already read is seen
again rather than lost. Records whose timestamps are out of order are still
returned in file order and flagged `(late)`; file order is the truth, because
several sessions append to one inbox.

Nothing here writes: every function is read-only, and a torn append (a final
line with no trailing newline, from a writer that has not finished) is ignored
so the next read sees that record whole.
"""
from __future__ import annotations

import datetime
import io
import os
import re
from dataclasses import dataclass, field

# A record's leading token, exactly as an inbox writes it: `…Z`.
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z(?=$|\s)")
# A line that *looks* like a record but does not parse (the measured shape is a
# minute-precision stamp: "2026-09-27T03:55Z …"). Reported, never glued on.
_TS_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_POSITION_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:Z)?(?:#(\d+))?$")
_LAST_EVENT_RE = re.compile(r"last-event\s+(\S+)")

_RUN_DIR_ENV = "AUTOOS_RUN_DIR"


class InboxError(Exception):
    """A inbox could not be read as an inbox."""


class NoTimestampedRecords(InboxError):
    """The file has no record at all — never to be read as "no events" (§0)."""

    def __init__(self, path):
        super().__init__("no timestamped records in %s" % path)


class InboxUnreadable(InboxError):
    """The file is not there, or not readable."""

    def __init__(self, path, reason):
        super().__init__("cannot read %s: %s" % (path, reason))
        self.path = path


@dataclass(frozen=True)
class Position:
    """`<timestamp>#<ordinal>`; ordinal 0 means "before any record of that
    second", which is what a bare UTC stamp reads as (§0, at-least-once)."""

    stamp: str
    ordinal: int = 0

    def key(self):
        return (self.stamp, self.ordinal)


@dataclass
class Record:
    """One timestamp line plus its continuation lines (§0)."""

    timestamp: str          # "2026-09-25T19:21:08Z", with the Z
    ordinal: int            # 1-based among records sharing the timestamp
    line: int               # 1-based line of the timestamp line
    text: str               # the timestamp line, minus its timestamp
    continuations: list = field(default_factory=list)
    late: bool = False      # set by read_since: its timestamp is behind the run

    @property
    def position(self) -> str:
        return "%s#%d" % (self.timestamp, self.ordinal)

    @property
    def key(self):
        return (self.timestamp, self.ordinal)


def run_dir(env=None) -> str:
    """``$AUTOOS_RUN_DIR``, or an error naming the alternative."""
    env = os.environ if env is None else env
    value = env.get(_RUN_DIR_ENV) or ""
    if not value:
        raise InboxError(
            "no RUN dir: set %s or pass --file PATH" % _RUN_DIR_ENV)
    return value


def inbox_path(name: str, env=None) -> str:
    """``<RUN>/inbox/<name>.md`` — the one place a name becomes a path."""
    if not name:
        raise InboxError("name an inbox or pass --file PATH")
    return os.path.join(run_dir(env), "inbox", "%s.md" % name)


def parse_position(text: str) -> Position:
    """`2026-09-28T06:00:00Z#2`, or a bare `2026-09-28T06:00:00Z` (= #0).

    Raises ValueError for anything else, including an ordinal below 1: a stored
    position always names a record that existed.
    """
    found = _POSITION_RE.match((text or "").strip())
    if not found:
        raise ValueError("not a position: %r" % (text,))
    year, month, day, hour, minute, second, ordinal = found.groups()
    try:
        datetime.datetime(int(year), int(month), int(day),
                          int(hour), int(minute), int(second),
                          tzinfo=datetime.timezone.utc)
    except ValueError as exc:
        raise ValueError("not a position: %r (%s)" % (text, exc)) from exc
    if ordinal is not None and int(ordinal) < 1:
        raise ValueError("not a position: %r (the ordinal counts from 1)" % (text,))
    return Position("%s-%s-%sT%s:%s:%sZ" % (year, month, day,
                                            hour, minute, second),
                    int(ordinal or 0))


def _is_real_timestamp(stamp: str) -> bool:
    """True when the matched token is a real UTC second (2026-02-30 is not)."""
    try:
        datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return False
    return True


def _iter_records(lines):
    """Yield one (payload, line_number, is_record) per decision over readlines().

    The caller owns line numbering and the ordinals; this owns the record
    boundaries: a timestamp line opens a record, a line without one continues
    it, and a line that looks like a timestamp but fails the parse is malformed
    and closes the record it follows — never glued onto it (§0). The lines after
    a malformed one belong to nothing and are skipped, exactly like the leading
    header block.
    """
    current = None
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\n").rstrip("\r")
        found = _TS_RE.match(line)
        if found and _is_real_timestamp(found.group(1) + "Z"):
            if current is not None:
                yield current
            current = (Record(timestamp=found.group(1) + "Z", ordinal=0,
                               line=number,
                               text=line[found.end(1) + 1:].lstrip(" \t")),
                       number, True)
            continue
        if _TS_LIKE_RE.match(line):
            if current is not None:
                yield current
                current = None
            yield None, number, False
            continue
        if current is not None:
            current[0].continuations.append(line)
        # else: a leading (or detached) untimestamped block — not a record.
    if current is not None:
        yield current


def parse_file(path: str):
    """(records, malformed line numbers) for one inbox file.

    A final line with no trailing newline is dropped before parsing: a
    concurrent writer may still be appending it (§0).
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InboxUnreadable(path, exc) from exc
    if lines and not lines[-1].endswith("\n"):
        lines.pop()
    records = []
    malformed = []
    seen = {}
    for item, number, is_record in _iter_records(lines):
        if not is_record:
            malformed.append(number)
            continue
        record = item
        seen[record.timestamp] = seen.get(record.timestamp, 0) + 1
        record.ordinal = seen[record.timestamp]
        records.append(record)
    return records, malformed


def read_records(path: str):
    """The file's records, or NoTimestampedRecords — never an empty list."""
    records, _malformed = parse_file(path)
    if not records:
        raise NoTimestampedRecords(path)
    return records


def malformed_lines(path: str):
    """Line numbers that look like a record timestamp but do not parse."""
    return parse_file(path)[1]


def _flag_late(records):
    """Mark a record whose timestamp is lower than one that came before it."""
    highest = None
    for record in records:
        if highest is not None and record.key < highest:
            record.late = True
        highest = record.key if highest is None else max(highest, record.key)


def window(records, since=None):
    """read_since's cut applied to records already parsed (flags `late`)."""
    _flag_late(records)
    if since is None:
        return list(records)
    key = since.key() if isinstance(since, Position) else since
    for index, record in enumerate(records):
        if record.key == key:
            return records[index + 1:]
    return [record for record in records if record.key > key]


def read_since(path: str, since=None):
    """Every record after `since` (a Position, or None for all), in file order.

    Two cuts, because §0 promises at-least-once and file order is the truth:

    * the position names a record that is in the file — every record *after it
      in file order* is returned, including one whose timestamp is older than
      the position's (a late append: it was written after, so it is new).
    * the position is not in the file — a bare UTC stamp, or a position from a
      rotated inbox — so the compare is on (timestamp, ordinal): every record
      whose key is greater than the position's is returned. A bare stamp has
      ordinal 0, which reads the whole second it names.

    Either way a record whose timestamp trails the records before it is flagged
    `late`, and no record at or before the position's own key is returned twice
    when it sits before that position in the file.
    """
    return window(read_records(path), since)


def latest_position(path: str):
    """The position of the last record in the file, or None."""
    records, _malformed = parse_file(path)
    return records[-1].position if records else None


def card_last_event(path: str):
    """The `last-event <position>` a state card header carries (§1), or None.

    None is "this card has read nothing yet", which the reader treats as
    "from the start" — the safe direction for an at-least-once read.
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            head = [fh.readline() for _ in range(40)]
    except (OSError, UnicodeDecodeError) as exc:
        raise InboxUnreadable(path, exc) from exc
    for line in head:
        if not line.strip():
            continue
        if not line.lstrip().startswith("# card"):
            return None
        found = _LAST_EVENT_RE.search(line)
        return found.group(1) if found else None
    return None
