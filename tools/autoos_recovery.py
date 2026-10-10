"""Run-death recovery (AO-WRITER-GUARDS P4c, writer-ops.md §5).

A writer can die mid-task two ways: the process exits without printing its
REPORT, or it goes quiet for N minutes. The spawner's answer is the same both
times — keep the worktree, re-run the SAME task on the next leg with
"continue from current diff", at most twice, then escalate to the L2/L1. That is
this module's whole job: read one run record, say what it is, and hand back the
plan (the continuation brief and where to run it). It NEVER spawns — the L2/L1
that asks is the one that calls `spawn` with `cwd=<sandbox>`, the kept worktree.

The record read is exactly what the fleet runner writes under
``<state>/agents/<run_id>/`` (tools/autoos_agent_mcp.py `spawn`/`run_job`):
``job.json`` (run_id, task, cwd, started, pid, request, argv, route),
``exit.json`` — only once the runner has ended (rc, ended, family) — and
``output.log``, which is the SPAWNER's stream. The spawner's own lines are the
only ones this reader believes about the worktree: the header it prints before the
child starts, and the closing block it prints after the child exits:

    sandbox: <sandbox> (branch <branch>)          <- header (autoos-agent.py cmd_run)
    <the captured client transcript, from here on: worker text, never read for a
     worktree — a writer that prints its own `sandbox:`/`review:` line names no cwd>
    writer: ... / scope: ... / sandbox changes    <- closing block starts
    review:  git -C <sandbox> diff
    take it: git fetch <sandbox> <branch>   (then: git cherry-pick <base>..FETCH_HEAD)

The header is the FIRST `sandbox:` line of the tail, because the spawner prints it
before the client can type anything, and the `review:` / `take it:` pair is only
read below the closing block's first line. A line starting `> ` is also taken as
the start of the worker's stream (a launcher that prefixes its child's output
makes the boundary explicit), but nothing here depends on that marker existing:
what makes a forged line harmless is that every candidate path must additionally
be real, not a link, and sit under one of `sandbox_roots` (the same two roots
`sandbox_path_for` builds in tools/autoos-agent.py: ``<state>/sandboxes`` and
``~/fleet/sandboxes``).

HERMETIC (D-852): pure file reads and writes. No git, no subprocess, no network,
no spawn. The state dir is the caller's (default ``tools/autoos_clients.state_dir``,
so ``AUTOOS_STATE_DIR`` moves the whole tree for a test) and `now` is a parameter
read through the module-level `_now` hook, so the running/stalled boundary is
decided by data a test supplies, never by a wall clock.

Every read goes through `_open_regular`: ``O_NOFOLLOW`` (a symlinked record is a
damaged record, not a record elsewhere) and ``O_NONBLOCK`` with an ``S_ISREG``
check on the fd, so a FIFO planted at ``job.json`` / ``exit.json`` /
``output.log`` / the attempt file is refused rather than blocking this process
forever. Fail closed everywhere: an unreadable record, a corrupt attempt file, a
job.json field of the wrong type (`record_suspect` — the file is worker-writable,
so every field is type-checked before use and a misshapen record never answers
'completed'), a record with no usable job.json task — absent, unreadable, not an
object, or empty after the type guard (there is no brief to continue) — or
a pid this host cannot probe is 'unknown', and 'unknown' is 'escalate'. It is
never 'completed'. The lane key is ALWAYS caller-supplied (D-914): `plan` needs
`--lane` exactly like `record` does, and without it is a usage error (exit 2).

Residuals, stated rather than hidden:

* pid reuse. A dead writer's pid can be handed to some other process between the
  death and this read, and ``os.kill(pid, 0)`` then answers "alive". The run
  record carries no start time to check that against (the runner's private kill
  record does, and this module reads no private store), so a reused pid holds a
  dead run at 'running' until the stall clock says otherwise.
* a run the operator cancelled is ``exit.json`` with ``cancelled: true`` and no
  rc, which reads here as 'died' — deliberate (a cancelled writer never
  reported), but the L1 that cancelled on purpose must not ask for a plan.
* report detection is an ALLOWLIST of shapes, not a blacklist of markdown constructs
  (P4c-fixes4: rounds 1-3 chased one construct per round — fence, quote, comment,
  separator character — and each new one was a fresh false 'completed'). A claim is
  the heading, and a heading counts only when it is at COLUMN 0 with no leading
  whitespace at all and wears no decoration but an ATX ``#{1,6} `` prefix or markdown
  bold around the word — a list bullet ``- REPORT <id> · completed`` is the writer
  summarising its own list, not the contract (P4c-fixes5 (2)) —, is the LAST such
  heading of the worker's own output and BEFORE
  the spawner's closing block, names this run or no run (a heading may name it in
  full, or by a PREFIX of at least 15 characters — ``YYYYMMDD-HHMMSS``, the stamp a
  writer copies out of its brief and the shortest one that identifies a single run
  — P4c-fixes5 (3)), and has no further
  column-0 ``REPORT <id>`` heading of another run under it; and it only counts on a
  line that survived `_report_view`: fences, HTML comment regions (a REPORT inside
  `<!--` ... `-->` is not a claim and an unclosed `<!--` swallows the rest), whole
  block-quote blocks (a quote continues lazily over every following non-blank line,
  marker or no marker, where a blank line is ONLY ' ' and '\t' — CommonMark's own
  rule: `str.strip()` also empties U+00A0, \x0b, \x0c, U+0085, U+2028, U+3000, so a
  separator line may never close a quote — and lines are cut on '\n' ONLY, never
  `splitlines()`, which invents line breaks at those same characters), INDENTED CODE
  BLOCKS (a run of lines indented by a tab or four or more spaces, opened after a
  blank line or the start of the text), HTML BLOCKS (from the line that, after at
  most three spaces, starts `<` and a letter, '/', '!' or '?' to the next blank line
  — or, for `pre`/`script`/`style`/`textarea`, to the line carrying the matching
  closing tag, an unclosed one swallowing the rest), and the task's echoed text.
  `tools/autoos_report.py`'s parser is never asked about the unfiltered tail: it
  reads the same rebuilt text and can only REFUSE, never grant — the block it finds
  must name the run the heading named. Any separator/control character that still
  reaches the report view — any Zs other than ' ', any Zl/Zp, any Cc other than '\t'
  — makes the WHOLE report suspect: has_report False, fail closed (P4c-fixes3). What
  reaches that screen is text `_normalise_output` has already produced (P4c-fixes5
  (1)): ``output.log`` is the CLIENT's raw captured stdout, and a CLI that believes it
  owns a terminal paints its progress with colour and redraws a line in place, which
  used to read as a writer that printed control characters — finished, reported, and
  classified 'died'. The strip removes well-formed ANSI escape sequences (the CSI
  family `tools/autoos-agent.py` strips with its own `_ANSI_RE`, mirrored here because
  that launcher is a 12 000-line script this module imports none of, plus the OSC and
  nF/Fe forms a client emits), and a carriage return gets its terminal meaning: inside
  one physical '\n' line only the text after the last '\r' that is followed by
  something survives, and the line's trailing run of '\r' is dropped — a cursor parked
  at the column shows nothing and erases nothing, and that is the CRLF case too. No
  sequence the strip knows may cross a newline, and an
  unterminated `\x1b[` / `\x1b]` or a lone ESC is not a sequence at all, so the
  normaliser cannot hide a forged line break — a leftover ESC, \x0b, \x0c, NEL or
  U+2028 in a worker's line is still suspect. A
  heading that carries a run id must name THIS run, so a heading naming something
  else — including prose like ``REPORT: the field list`` — is not believed: the cost
  is one needless continuation leg on a run that wrote an unusual heading, never a
  false 'completed'. Measured over this host's own rc==0 records the same refusal
  still bites on the heading a writer prints INSTEAD of its id —
  ``REPORT — <title>, committed <sha>`` names no run a reader may match, so it reads
  'died' by design (D-938 CHECK 3): the contract wants the id, or its stamp, right
  after the word. And a heading that gives ONLY the 15-character stamp names every
  run started in that same second as well — the reader cannot tell them apart, which
  it buys back by never granting 'completed' without rc==0 on top of the claim. So is every other refusal above: a writer that reported from
  inside an indented block, an HTML block or under a forged `writer:` trailer loses
  its claim and buys a leg. The strict protocol gate belongs to `ready`, not to
  recovery; a false 'completed' needs rc==0 on top of it.
* a sandbox path inside a trusted root is accepted on the spawner's word alone.
  A worker that could both create a directory under ``<state>/sandboxes`` and
  forge the header line could name that directory instead of its own; it cannot
  name a path outside the roots, a symlink, or another run's directory that does
  not exist. The continuation is also told (in `continuation_task`'s text) what
  path it is running in.
* the lane lock is held by the open file description on both sides — ``flock`` on
  POSIX, ``msvcrt.locking`` on Windows — so a holder that dies never leaves the
  lane blocked, and the lock file itself stays put. What neither gives is a wait:
  a lock still contested after ``LOCK_RETRIES`` tries is refused, and a refused
  record escalates the lane instead of running an unrecorded leg.
* the lane key is ALWAYS caller-supplied (D-914). Rounds 7-9 of isolated seats
  found a chain of defects all rooted in deriving that key from free task text —
  key drift across continuation legs (each leg's brief carries the previous
  footer, so each leg read attempts 0 and the ``MAX_ATTEMPTS`` cap was
  bypassable), footer-only tasks collapsing onto one sha256-of-'' budget shared
  by unrelated runs, and Cf/Cc-prefixed footers moving the key again. The ship
  decision was to delete the derivation, not to patch it: `plan` refuses without
  a `--lane` (usage exit 2), the same validation `record` already applies, and a
  caller that wants a stable lane names one. The continuation footer is still cut
  before a brief is rebuilt (`original_task`, used by `continuation_task`), so a
  chain of legs never stacks footers, and a task that is nothing but a footer
  still names no work: 'unknown' + suspect, which escalates. It is recognised on a
  character CLASS, not on a literal prefix — one zero-width space before the heading
  used to hide a footer-only task from `lstrip()` and buy a leg carrying two stacked
  footers (D-914 loop-stopper).
* the CLI's exit contract (P4c-fixes3): 0 none/wait and a recorded leg, 3 rerun,
  4 escalate — and 4 for lane state too: a corrupt, contested, non-regular or
  unwritable recovery state raises `RecoveryStateError`, which `main` answers
  with the one-line escalate message. Exit 2 stays ONLY for a usage error: an
  argparse failure or a run id / lane key / `--stall-secs` the caller passed in a
  shape this module refuses to touch — the stall clock is bounded (finite,
  1 .. 7 days) because an unusable one is not a harmless default: NaN compared
  against any age is False, so `plan --stall-secs nan` read a LIVE writer as
  'stalled' and answered 'rerun', which is the plan that starts a second writer
  beside the first (P4c-fixes4 C).
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
import unicodedata

import autoos_clients as clients
import autoos_ready_guards as ready_guards
import autoos_report as report_parser

# writer-ops.md §5: quiet for this long is a stall; at most this many
# "continue from current diff" legs before the lane escalates.
STALL_SECS = 900
MAX_ATTEMPTS = 2
# `--stall-secs` is the one number that decides whether a LIVE writer gets a second
# writer running beside it, so it is bounded, not merely positive: a comparison with
# NaN is always False (`nan <= 0` included), so `stall <= 0` cannot refuse it and
# `age <= nan` then answers "not running" — a stalled/rerun verdict for a writer that
# is alive. Infinity and a span longer than a week of silence are the same kind of
# caller mistake. (P4c-fixes4 C.)
STALL_SECS_MIN = 1
STALL_SECS_MAX = 7 * 24 * 60 * 60
# The output tail read (a whole run's log is never loaded), and the cap on the
# ORIGINAL task in a continuation brief — the footer always survives, the task
# does not.
TAIL_BYTES = 256 * 1024
TASK_MAX_BYTES = 200 * 1024
# The one place the continuation footer's shape is written down (P4c-fixes7): the
# blank line plus the heading up to the attempt number, which is the only part that
# is the same on every leg. `continuation_task` appends text starting with exactly
# this, and cuts the task at its first footer before appending — the literal
# marker is the canonical shape of the cut `_footer_line_start` makes through the
# heading form (P4c D2), so a leg's own brief, which is what the next leg's
# job.json task holds (original plus one footer), is fed back in without stacking a
# second footer. The leading "\n\n" is load-bearing: a task that merely mentions
# the heading INSIDE a line of prose is not cut there.
CONTINUE_MARKER = "\n\nCONTINUE FROM CURRENT DIFF (recovery attempt "
# The same footer without the blank line that separates it from a brief — the shape
# a record wears when the separator is lost. Used to RECOGNISE a footer-only task as
# naming no work (`_usable_task`, P4c-fixes8) AND to cut a brief at a footer whose
# separator was broken by an invisible character or a CRLF (P4c D2,
# `_footer_line_start`). It is recognised through `_heading_form`, the same
# invisible/whitespace character class `_has_visible_text` refuses on, so a
# separator lost AND a heading preceded by a zero-width character or an ESC is
# still the same footer.
CONTINUE_HEADING = CONTINUE_MARKER.lstrip("\n")
LANE_KEY_MAX_CHARS = 120
RUN_ID_MAX_CHARS = 128
# A REPORT heading may name this run by a PREFIX of its id, but only from this many
# characters up: a run id begins `YYYYMMDD-HHMMSS` — exactly 15, and unique per
# second — so a prefix this long has left the ambiguous part of the stamp behind.
# Shorter than that ("20261009-18202" cuts the stamp mid-minute, and a dozen runs
# share a minute) is a prefix other runs share, which is not a claim about THIS run
# (P4c-fixes5 (3)).
RUN_ID_STAMP_MIN_CHARS = 15
PATH_MAX_CHARS = 4096
BRANCH_MAX_CHARS = 200
_INF = float("inf")          # the bound `_is_finite` compares against, no math import

STATES = ("running", "completed", "died", "stalled", "unknown")
# One path component, no separators, no leading dot: neither `..` nor `/` can
# appear, so a run id never names anything outside <state>/agents/.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,%d}" % (RUN_ID_MAX_CHARS - 1))
# A lane key is caller-supplied and, like the run id, only ever hashed into a
# file name — the shape keeps it readable in the state record's `key` field.
_LANE_KEY_RE = re.compile(r"[A-Za-z0-9._/-]{1,%d}" % LANE_KEY_MAX_CHARS)
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,%d}" % BRANCH_MAX_CHARS)
# The header is printed at run START, the closing block at run END; a launcher
# that marks its child's output uses '> ' (tools/autoos-agent.py cmd_run prints
# `writer:`, `scope:`, then the `sandbox changes (uncommitted):` summary and the
# review/take-it pair, and does not prefix the child's lines).
_WORKER_MARK = "> "
_TRAILER_MARKS = ("writer:", "scope:", "sandbox changes")
# The two roots a printed sandbox path must live under to be a directory this
# module will name as a continuation cwd — the same two `sandbox_path_for` builds
# in tools/autoos-agent.py (S4). Mirrored, not imported: that file is a
# hyphen-named launcher and this module loads none of it. A change there has to
# be repeated here, and the test asserts both halves of the pair.
FLEET_SANDBOX_SUBPATH = os.path.join("fleet", "sandboxes")
# Above this a number is not a pid on this host (Linux's default kernel.pid_max
# is 2**22), and `os.kill` answers OverflowError rather than a verdict.
PID_MAX = 2 ** 22
# How long a contended lane lock is waited for before the record is refused, on
# BOTH platforms: `LOCK_RETRIES` tries `LOCK_RETRY_SECS` apart (2 s < 3 s total).
# POSIX pays it with a non-blocking `flock` (LOCK_NB) in the same retry loop
# Windows needs anyway — a blocking flock would hang the recorder for as long as
# the holder liked.
LOCK_RETRIES = 200
LOCK_RETRY_SECS = 0.01
# The one open mode every record, log and state file is read with.
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
# The return contract's heading, markdown-wrapped or not, read the way
# tools/autoos-agent.py's REPORT_HEADING_RE reads the word — the `(?![\w.])` keeps
# "REPORTED" and "REPORT.md" out. `tail` is whatever follows the word: the protocol
# puts the run id there, and a heading that names anything else there is not this
# run's report.
# P4c-fixes4 (A/B): NO leading whitespace. A markdown heading that claims a report
# starts at column 0, and every construct that INDENTS a line (an indented code
# block, a list item's body, an HTML block's payload) is exactly where the spawner's
# trailer, a quoted example and a pasted transcript put a REPORT they did not mean.
# The reader is an allowlist now: this line is the shape a claim must have.
_REPORT_HEADING_RE = re.compile(
    r"^(?:#{1,6} )?(?:\*\*)?REPORT(?:\*\*)?(?![\w.])[ \t]*:?[ \t]*(?P<tail>.*)$")
# P4c-fixes5 (2): the decoration set above is EXACTLY two things — an ATX heading
# prefix (`#{1,6} `, the `#` run followed by its required space) and markdown bold
# around the word, which is how the real writers print it: `REPORT <id> · OK · green`
# and `**REPORT** <id> · status **completed**`. The old `[#>*-]+` class also accepted
# a LIST MARKER, so a bullet in the writer's own summary list
# (`- REPORT <id> · completed …`) read as the return contract's heading and granted
# a 'completed' to a run that never claimed one. A bullet, a `+`, a `*` alone and a
# quote `>` are not decorations the contract allows; neither is `#REPORT` with no
# space, which is not an ATX heading.
# The heading `tools/autoos_report.py`'s parser reads a block from — kept here only
# so the body under an accepted heading can be checked against it (rule 5).
_BLOCK_HEAD_RE = re.compile(r"^REPORT\s+(?P<id>\S+)")
_TAKE_RE = re.compile(r"^take it:[ \t]+git fetch[ \t]+")
_REVIEW_RE = re.compile(r"^review:[ \t]+git -C[ \t]+")
_SANDBOX_RE = re.compile(r"^sandbox:[ \t]+(?P<path>\S+)[ \t]+\(branch (?P<branch>\S+)\)$")
# A blank line is empty after ' ' and '\t' ONLY — CommonMark's own rule.
# `str.strip()` also empties U+00A0, \x0b, \x0c, U+0085, U+2028, U+3000..., so a
# line holding only one of those separators would CLOSE a block quote and hand
# back the REPORT under it as an unquoted claim (P4c-fixes3).
_BLANK_STRIP = " \t"
# The Unicode categories whose members splitlines() breaks lines at or strip()
# silently eats: separators and controls. ' ' (a Zs) and '\t' (a Cc) are the two
# a real line legitimately carries; every other member makes a report suspect.
_SUSPECT_CATS = frozenset(("Zs", "Zl", "Zp", "Cc"))
# What a task must hold besides it: the same invisible classes, widened with Cf
# (zero-width joiners, bidi overrides — characters that render nothing a writer can
# be told to work on), and judged AFTER the output normaliser has run, so ANSI
# colour cannot pass as content either (P4c-fixes7, `_has_visible_text`).
_INVISIBLE_CATS = frozenset(("Zs", "Zl", "Zp", "Cc", "Cf"))
# --- output.log normalisation (P4c-fixes5 (1)) --------------------------------
#
# ``output.log`` is the CLIENT's raw captured stdout. A CLI that believes it owns a
# terminal paints its progress with colour (CSI), sets the window title (OSC),
# selects a charset (nF/Fe) and REDRAWS a line in place with a carriage return.
# `_suspect_line` refuses every Cc but '\t', so ESC and a non-trailing CR made the
# WHOLE report suspect — a writer that finished and reported read as 'died' and
# bought two continuation legs before it escalated. `tools/autoos-agent.py` strips
# the same bytes before it reads a tail: its `_ANSI_RE` (that file :7806, applied at
# :7835 and :8835). That launcher is a 12 000-line script this module imports none
# of, so the CSI half is copied here verbatim and extended with the OSC and nF/Fe
# forms a client also emits. The agent's `|^\r` half is deliberately NOT copied:
# there it strips a CR at the head of a single line, here `_redraw_line` decides CR
# semantics for a whole physical line, which is what a progress bar needs.
_ANSI_CSI = r"\x1b\[[0-9;?]*[ -/]*[@-~]"
# Well-formed sequences ONLY, and none of them may span a line break: every final
# byte is 0x40..0x7E, the intermediates are 0x20..0x2F, and the OSC payload excludes
# BEL, ESC, '\r' and '\n'. So the strip can never eat a newline, and what it leaves
# behind — a lone ESC, an unterminated `\x1b[`/`\x1b]`, a U+2028 — is exactly what
# `_suspect_line` then rejects. An escape is not a licence to forge a line break.
_ANSI_ESCAPE_RE = re.compile(
    _ANSI_CSI                                   # CSI: colour, cursor, erase-line
    + r"|\x1b\][^\x07\x1b\r\n]*(?:\x07|\x1b\\)"  # OSC, terminated by BEL or ST
    + r"|\x1b[!-/]+[0-~]"                        # nF: ESC ( B, ESC ) 0, ESC $ B —
    #                                               charset designators: an
    #                                               intermediate, never a space, then
    #                                               a final byte
    + r"|\x1b(?:[@-Z\\^_=>0-9]|c)")              # Fe and the one Fp that ships: ESC 7,
    #                                               ESC 8 (xterm save/restore cursor),
    #                                               ESC \, ESC =, ESC >, ESC c (reset)
# What is deliberately NOT a sequence here, and stays visible for `_suspect_line`:
#   * `\x1b[` / `\x1b]` — the two introducers of a longer sequence, so an
#     unterminated one is malformed output, and its ESC must be seen, not eaten;
#   * ESC + a space, and ESC + any lowercase letter but 'c' — ECMA-48 reserves those
#     as private Fp/Fs escapes and nothing a client really prints uses them, so
#     reading `\x1bept` as `\x1be` + `pt` would silently rewrite the worker's prose. A
#     lone
#     ESC before an ordinary character is damaged output, and damaged output is
#     suspect, which is the answer that costs a leg rather than hiding a line break.
# No class above can match '\r' or '\n' (or any Cc), so the strip cannot join two
# physical lines or swallow one: it removes bytes INSIDE a line and nothing else.

# P4c-fixes4 (A/B): markdown constructs that INDENT text and are therefore no place
# for a claim. An indented code block is a run of lines indented by a tab or by this
# many spaces, and a run only becomes code when a blank line (or the start of the
# text) precedes it — an indented continuation of a paragraph is not a code block,
# but it is indented, and rule 1 of the allowlist refuses it as a heading anyway.
_CODE_INDENT = " " * 4
# An HTML block: a line that, after at most three spaces, starts with '<' followed by
# a letter, '/', '!' or '?'. It swallows its lines until the next blank line — except
# the raw-text tags below, which swallow until their matching closing tag, and except
# `<!--`, which is the comment region `_comment_scan` already owns (rule 4).
_HTML_BLOCK_RE = re.compile(r"^ {0,3}<(?P<rest>[A-Za-z/?!][^\n]*)$")
_HTML_TAG_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_SWALLOW_TAGS = ("pre", "script", "style", "textarea")
_SWALLOW_END_RES = {t: re.compile(r"</" + t, re.IGNORECASE) for t in _SWALLOW_TAGS}


class RecoveryError(ValueError):
    """A run id, a lane key or a record path this module will not touch — the
    caller's argument was wrong, which the CLI answers with the usage exit (2)."""


class RecoveryStateError(RecoveryError):
    """The lane's own state refused the record: corrupt, contested, not a
    regular file, or unwritable. This is not a caller mistake to correct — it
    needs a human — so the CLI answers it with the escalate exit (4), never 2."""


def _lines(text):
    """`text` as PHYSICAL lines: split on '\\n' only, one trailing '\\r' dropped
    so a CRLF log reads like an LF one. Never `str.splitlines()`, which also
    breaks at \\x0b, \\x0c, U+0085, U+2028, U+2029 — the very characters that
    must stay inside one line so the quote logic sees a non-blank line and the
    fail-closed screen sees a suspect one (P4c-fixes3)."""
    return [p[:-1] if p.endswith("\r") else p for p in (text or "").split("\n")]


def _is_blank(line):
    """CommonMark's blank line: nothing but spaces and tabs."""
    return line.strip(_BLANK_STRIP) == ""


def _redraw_line(line):
    """`line` as a terminal would leave it on the screen: the text after the LAST
    '\\r' that is followed by something, and the line's trailing run of '\\r'
    dropped so a CRLF log reads like an LF one.

    A client that redraws a progress bar writes '10%\\r20%\\r100%\\r' inside ONE
    physical '\\n' line; what the reader may believe is what the user saw, '100%'. A
    carriage return at the END of a line only parks the cursor at the column — it
    erases nothing and shows nothing — so the whole trailing run goes, exactly as the
    one CR of a CRLF log has always gone in `_lines`. Nothing that survives here can
    carry a claim the screen would not have shown, and no '\\r' reaches
    `_suspect_line` any more (P4c-fixes5 (1)).
    """
    body = line.rstrip("\r")
    cut = body.rfind("\r")
    return body[cut + 1:] if cut >= 0 else body


def _normalise_output(text):
    """The client's raw captured stdout as the text every reader of this module
    sees: ANSI escape sequences stripped (`_ANSI_ESCAPE_RE`), then carriage returns
    given their terminal-redraw meaning (`_redraw_line`), line by line on '\\n'
    ONLY — the same cut `_lines` uses, so normalising cannot invent or destroy a
    physical line, and a forged U+2028/U+0085/\\x0c stays inside the line it was
    written into and is still rejected as suspect.

    This runs at the one place the log enters the module (`classify_run`), so the
    report reader and the spawner's `sandbox:` / trailer reader agree on the same
    text. Idempotent: applying it to its own output changes nothing.
    """
    return "\n".join(_redraw_line(_ANSI_ESCAPE_RE.sub("", line))
                     for line in (text or "").split("\n"))


def _suspect_line(line):
    """Whether `line` carries a character the reader cannot count line breaks or
    blankness on: any Zs other than ' ', any Zl/Zp, any Cc other than '\\t'. A
    report whose lines contain one of these could be re-split or re-stripped
    into a different verdict, so the WHOLE report is read as no report.

    It is asked only of text that `_normalise_output` has already produced, which
    is what keeps its own rule intact: ESC and a bare '\\r' are no longer evidence
    against a writer that merely coloured its progress or redrew a line, but
    anything the strip could not account for — a lone ESC, a NEL, a FF, a U+2028 —
    still is (P4c-fixes5 (1))."""
    for ch in line:
        if ch == " " or ch == "\t":
            continue
        if unicodedata.category(ch) in _SUSPECT_CATS:
            return True
    return False


def _comment_scan(line, in_comment):
    """Advance the HTML-comment tracker over `line`: (state_after, touched).
    `touched` is True when any part of the line lies inside a `<!--` ... `-->`
    region or opens/closes one, so a commented-out REPORT — whole, half, or on
    the very line that ends the comment — is dropped rather than believed; an
    unclosed `<!--` keeps the region open and swallows the rest of the tail. A
    `-->` with no open comment is text, not markup. `line` here is never fence
    content: the caller runs this only on unfenced lines, so a `<!--` inside a
    code block stays code and opens nothing (the fence wins)."""
    touched = in_comment
    rest = line
    while True:
        if in_comment:
            end = rest.find("-->")
            if end < 0:
                return True, touched
            in_comment = False
            rest = rest[end + 3:]
        start = rest.find("<!--")
        if start < 0:
            return False, touched
        in_comment = True
        touched = True
        rest = rest[start + 4:]


def _now():
    """Current epoch seconds. Module-level hook so hermetic tests can pin time."""
    return time.time()


def state_dir(state=None):
    """The state root: `state` when given, else the repo's (`AUTOOS_STATE_DIR` moves it)."""
    return state or clients.state_dir()


def agents_root(state=None):
    return os.path.join(state_dir(state), "agents")


def recovery_dir(state=None):
    """Where the per-lane attempt files live: ``<state>/recovery``."""
    return os.path.join(state_dir(state), "recovery")


def sandbox_roots(state=None):
    """The only directories a printed sandbox path may be trusted inside.

    ``<state>/sandboxes`` (AutoOS's own cards, git-ignored) and
    ``~/fleet/sandboxes`` (a foreign cwd repo's, S4) — exactly the two places
    `sandbox_path_for` in tools/autoos-agent.py creates a sandbox. Nothing else
    is a worktree a writer may name for itself.
    """
    return (os.path.join(state_dir(state), "sandboxes"),
            os.path.join(os.path.expanduser("~"), FLEET_SANDBOX_SUBPATH))


def _check_run_id(run_id):
    if type(run_id) is not str or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise RecoveryError("bad run id %r (a run id names one directory under "
                            "<state>/agents, never a path)" % (run_id,))
    return run_id


def _check_key(key):
    if type(key) is not str or not _LANE_KEY_RE.fullmatch(key):
        raise RecoveryError("bad lane key %r (1-%d chars of [A-Za-z0-9._/-])"
                            % (key, LANE_KEY_MAX_CHARS))
    return key


def _is_finite(value):
    """Whether a number can be used in time arithmetic at all.

    NaN and both infinities answer False, and they do it WITHOUT a `math` import:
    every comparison with NaN is False, and nothing but ±inf sits outside
    `-inf < x < inf`. A record field or a clock reading that is one of them says
    nothing about when anything happened — and `age <= nan` is False, which is
    exactly how a live pid used to be read as 'stalled' (P4c-fixes4 C).
    """
    return -_INF < float(value) < _INF


def _is_time_number(value):
    """A value that may be used as epoch seconds: a real number (never a bool,
    which IS an int in Python) and a FINITE one. Every time this module reads comes
    from a file a writer could have written — `started`, `ended`, an mtime — and a
    NaN or an infinity in one is a damaged record, not a clock."""
    return (type(value) is not bool and isinstance(value, (int, float))
            and _is_finite(value))


def _check_time(value, name):
    if type(value) is bool or not isinstance(value, (int, float)):
        raise RecoveryError("%s: epoch seconds, got %r" % (name, value))
    if not _is_finite(value):
        raise RecoveryError("%s: a finite number of seconds, got %r" % (name, value))
    return float(value)


def _check_stall(value):
    """`stall_secs` as a usable stall clock: finite and 1 .. 7 days.

    Bounded on both ends by ONE comparison, which is what makes it refuse what
    `stall <= 0` could not: NaN fails every comparison, so `plan --stall-secs nan`
    is a usage error (exit 2) instead of 'stalled' for a live writer — and 'stalled'
    there is the answer that starts a SECOND writer beside the first one."""
    num = _check_time(value, "stall_secs")
    if not STALL_SECS_MIN <= num <= STALL_SECS_MAX:
        raise RecoveryError("stall_secs: a finite number of seconds between %d and "
                            "%d (7 days), got %r"
                            % (STALL_SECS_MIN, STALL_SECS_MAX, value))
    return num


def run_dir_for(run_id, state=None):
    """``<state>/agents/<run_id>`` for a validated id.

    Refused: an id that is not one path component, a directory that is a symlink
    (a link is how a record escapes the tree it is meant to be read from), and a
    directory that is not there.
    """
    path = os.path.join(agents_root(state), _check_run_id(run_id))
    if os.path.islink(path):
        raise RecoveryError("run dir %r is a symlink" % (path,))
    if not os.path.isdir(path):
        raise RecoveryError("no run dir %r" % (path,))
    return path


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def _open_regular(path):
    """An fd on `path` if it is a REGULAR file, else None (the caller's answer is
    'not readable', never a guess).

    One opener for every record, log and state file this module reads:
      * ``O_NOFOLLOW`` — a symlink is refused, not followed, so a planted link
        cannot move a read outside the tree the record lives in;
      * ``O_NONBLOCK`` — a FIFO at that path would block an ordinary open (and a
        plain read) forever with no writer attached, which is a hung lane, not a
        verdict; on its own it is only half the fix, since the open then succeeds
        and the read returns EOF;
      * ``S_ISREG`` on `os.fstat` of the fd, not `stat()` on the name — the type
        is judged on the thing actually opened, so nothing can be swapped in
        between the check and the open.
    Windows defines neither flag: `getattr` answers 0 and the open is plain.
    """
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        return None
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        _close(fd)
        return None
    return fd


def _read_json_dict(path):
    """``(dict, ok)`` for a record file.

    ok is False when the file exists but is not a readable JSON object — a
    record that cannot be read, a symlink, a FIFO or a directory is no verdict,
    and the caller fails closed. A file simply not being there is ``(None, True)``:
    that is the state of a run that has not ended, not a damaged record.
    """
    if not os.path.lexists(path):
        return None, True
    fd = _open_regular(path)
    if fd is None:
        return None, False
    try:
        fh = os.fdopen(fd, "r", encoding="utf-8")
    except OSError:
        _close(fd)
        return None, False
    try:
        with fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None, False
    return (data, True) if type(data) is dict else (None, False)


def _read_tail(path, limit=TAIL_BYTES):
    """The last `limit` bytes of `path`, decoded leniently; "" when unreadable."""
    fd = _open_regular(path)
    if fd is None:
        return ""
    try:
        fh = os.fdopen(fd, "rb")
    except OSError:
        _close(fd)
        return ""
    try:
        with fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def pid_alive(pid):
    """True / False for a recorded pid, None when this host cannot tell.

    A number outside 1..`PID_MAX` is not a pid this host can hold, and asking is
    not harmless: ``os.kill(2**31, 0)`` raises OverflowError, which would end the
    read with a traceback instead of a verdict. So the shape is decided first and
    the probe catches everything it can still raise.

    POSIX: ``os.kill(pid, 0)`` — ESRCH is dead, EPERM is alive (the number is
    taken by someone else's process). Windows has no such probe: ``os.kill``
    there is TerminateProcess, so the pid is read through a process handle's own
    exit code instead, and a host with neither answers None — which classifies
    as 'unknown' and escalates rather than guessing.
    """
    if (type(pid) is bool or not isinstance(pid, int)
            or not 1 <= pid <= PID_MAX):
        return None
    if os.name == "nt":
        import ctypes
        try:
            kernel32 = ctypes.windll.kernel32
        except (AttributeError, OSError):
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return None
    except (OverflowError, ValueError, TypeError):
        # Not a number this host can probe: no verdict, so no guess.
        return None
    return True


def _valid_path(path):
    """A sandbox path from an output line, validated or None.

    Refused: anything not absolute, any ``..`` component, any control character,
    an empty or over-long path, one starting with ``~`` (an unexpanded home is not
    a path the runner ever printed), and one that is only separators — ``/`` names
    every file on the machine, so it is not a worktree. This is the SHAPE test;
    `_trusted_sandbox` adds the place-on-disk test a printed path must also pass.
    """
    if type(path) is not str or not path or len(path) > PATH_MAX_CHARS:
        return None
    if any(unicodedata.category(c)[0] == "C" for c in path):
        return None
    if path.startswith("~") or not os.path.isabs(path):
        return None
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return path


def _trusted_sandbox(path, state=None):
    """A printed sandbox path, or None unless it is this host's real worktree.

    The shape test first, then the disk test: an existing directory, not a
    symlink, whose resolved path sits STRICTLY inside one of `sandbox_roots` (the
    root itself is a container of sandboxes, never one). The printed path is what
    comes back, unchanged, so the caller re-runs in the directory the spawner
    named — but only once it has been proved to be under a root the spawner is the
    only thing that writes to. A path that fails any of this is None: the plan
    loses the worktree hint and says so, instead of pointing a continuation leg at
    a directory the writer chose.
    """
    cand = _valid_path(path)
    if cand is None:
        return None
    try:
        if os.path.islink(cand) or not os.path.isdir(cand):
            return None
        real = os.path.realpath(cand)
    except OSError:
        return None
    for root in sandbox_roots(state):
        try:
            if not os.path.isdir(root):
                continue
            base = os.path.realpath(root)
        except OSError:
            continue
        # A root that IS a filesystem root would trust everything under it.
        if not base or base == os.path.dirname(base):
            continue
        if real.startswith(base + os.sep):
            return cand
    return None


def _tokens(line):
    """`line` split the way a shell would split it (the runner `shlex.quote`'s the
    paths it prints), or None when the quoting is not parseable."""
    try:
        return shlex.split(line)
    except ValueError:
        return None


def _check_branch(branch):
    """A branch name that can be pasted into a fetch line, or None."""
    if type(branch) is not str:
        return None
    return branch if _BRANCH_RE.fullmatch(branch) and ".." not in branch else None


def _worker_start(lines):
    """Index of the first line of the worker's own stream, or None.

    A launcher that prefixes the child's output ('> ') states the boundary, and
    everything above it is spawner text. tools/autoos-agent.py does not print the
    prefix — its header is simply the first `sandbox:` line, which is why
    `_parse_sandbox` reads the header area first-wins and confines every candidate
    to `sandbox_roots` rather than trusting this boundary alone.
    """
    for i, line in enumerate(lines):
        if line.startswith(_WORKER_MARK):
            return i
    return None


def _trailer_start(lines):
    """Index of the spawner's closing block, or None when the tail holds none.

    The LAST line starting `writer:` / `scope:` / `sandbox changes` — cmd_run
    prints them after the child exits, so from there to the end is again spawner
    text, and that is the only place a `review:` / `take it:` pair may come from.
    """
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(_TRAILER_MARKS):
            return i
    return None


def _parse_sandbox(tail, state=None):
    """(sandbox, branch) from the SPAWNER's lines of the output tail.

    Two places, in order: the header — the first ``sandbox: <path> (branch <b>)``
    before the worker's stream starts — and, when the header has already scrolled
    out of the read window, the closing block's ``review:`` / ``take it:`` pair.
    Anything between them is worker text and is not read for a worktree at all: a
    writer that prints ``review:  git -C / diff`` gets no cwd. Every candidate
    goes through `_trusted_sandbox`, so an unparsable path, a path outside the
    sandbox roots, a missing directory or a symlink is None — the caller loses the
    worktree hint, it never gets a guessed path.
    """
    lines = _lines(tail)
    if not lines:
        return None, None
    header_end = len(lines)
    for bound in (_worker_start(lines), _trailer_start(lines)):
        if bound is not None:
            header_end = min(header_end, bound)
    for i in range(header_end):
        m = _SANDBOX_RE.match(lines[i])
        if not m:
            continue
        path = _trusted_sandbox(m.group("path"), state)
        if path is not None:
            return path, _check_branch(m.group("branch"))
    trailer = _trailer_start(lines)
    if trailer is None:
        return None, None
    block = lines[trailer:]
    sandbox, after = None, 0
    for i in range(len(block) - 1, -1, -1):
        parts = _tokens(block[i])
        if parts and _REVIEW_RE.match(block[i]) and len(parts) == 5 and parts[4] == "diff":
            path = _trusted_sandbox(parts[3], state)
            if path is not None:
                sandbox, after = path, i + 1
                break
    for line in block[after:]:
        if not _TAKE_RE.match(line):
            continue
        parts = _tokens(line)
        if not parts or len(parts) < 6 or parts[:4] != ["take", "it:", "git", "fetch"]:
            continue
        path = _trusted_sandbox(parts[4], state)
        if path is None:
            continue
        return sandbox if sandbox is not None else path, _check_branch(parts[5])
    return sandbox, None


def _html_opener(line):
    """The HTML block `line` opens — ('blank', None) to the next blank line, or
    ('tag', name) to the matching closing tag — or None when it opens nothing.

    The raw-text tags (`pre`, `script`, `style`, `textarea`, case-insensitive) are
    the ones that hold a pasted transcript, a rendered log excerpt or a copied HTML
    page, and a REPORT inside one is data, not a claim: their region lasts to
    `</tag>`, and an unclosed one swallows the rest of the tail. Every other block
    (`<div>`, `<details>`, `</p>`, `<!DOCTYPE`, `<?xml`) lasts to the next blank
    line. A line starting `<!--` answers None HERE, because the comment region owns
    it and its end condition is `-->`, not a blank line — HTML comments keep
    behaving exactly as before (rule 4)."""
    m = _HTML_BLOCK_RE.match(line)
    if m is None:
        return None
    rest = m.group("rest")
    if rest.startswith("!--"):
        return None
    tag = _HTML_TAG_RE.match(rest)
    if tag is not None and tag.group(0).lower() in _SWALLOW_TAGS:
        return "tag", tag.group(0).lower()
    return "blank", None


def _report_view(tail, task):
    """The tail's lines that may carry a claim — an ALLOWLIST of shapes, not a list
    of the constructs that once went wrong.

    A line survives only when it is the worker's own prose at its own indentation.
    Dropped is everything inside: a fence — ONE shared rule with the `ready` guards
    (`autoos_ready_guards._Fences`: a fence opens on 3+ of the same ` or ~ after at
    most three leading spaces and closes only on the same character at least as
    long with nothing after it, so a fence that is never closed swallows the rest
    of the tail); an HTML comment region — every line any part of which lies inside
    a `<!--` ... `-->` comment (an unclosed `<!--` swallows the rest, and a `<!--`
    inside a fence stays code: the fence owns its lines and wins); a block-quote
    block, because a quote is not a claim — a block starts at a line that, once
    stripped of its leading blanks (spaces or tabs; CommonMark's own rule is at most
    three spaces), begins with '>', and it continues over EVERY following non-blank
    line until the first blank one, marked or not: a REPORT line sitting under a '>'
    line with no blank between is a lazy continuation of the quote, not a claim of
    its own; an INDENTED CODE BLOCK — a run of lines indented by a tab or by four or
    more spaces, opened right after a blank line or the start of the text (P4c-fixes4
    A: '    REPORT …' is a transcript pasted into the log, and a heading a reader has
    to indent is not the return contract); an HTML BLOCK — from the line that opens
    it to the blank line that ends it, or, for `pre`/`script`/`style`/`textarea`, to
    the line carrying its closing tag, an unclosed one swallowing the rest
    (P4c-fixes4 B: `<pre>` … REPORT … `</pre>` is quoted output); and every line that
    also occurs in the task — a client that cats its brief back has echoed the return
    contract's own REPORT line, and that is the brief's text, not a report on the
    work.
    A blank line here means ONLY ' ' and '\\t' (`_is_blank`), and both inputs are
    cut on '\\n' only (`_lines`): a U+00A0/\\x0b/\\x0c/U+0085/U+2028/U+3000 line
    neither closes the quote nor breaks a line, so the quoted REPORT stays quoted
    (P4c-fixes3).
    """
    echoed = {line.strip(_BLANK_STRIP) for line in _lines(task)
              if not _is_blank(line)}
    fences = ready_guards._Fences()
    out = []
    in_quote = False
    in_comment = False
    html = None                 # the open HTML block, if any (see `_html_opener`)
    in_indent_run = False       # the previous line was indented (a tab or 4 spaces)
    code_run = False            # and the run that line belongs to is a code block
    prev_blank = True           # the start of the text counts as a blank line
    for raw in _lines(tail):
        # The tracker must see EVERY line: its state is the fence structure, and
        # skipping a line here would re-open a block that is still shut.
        fenced = fences.feed(raw)
        if fenced:
            in_comment_here = False      # '<!--' inside a fence is code text
        else:
            in_comment, in_comment_here = _comment_scan(raw, in_comment)
        line = raw.strip(_BLANK_STRIP)
        blank = line == ""
        if blank:
            in_quote = False       # a blank line is what closes a quote block
        elif not fenced and line.startswith(">"):
            in_quote = True        # '>' inside a fence is code text, not a marker
        # The indented-code run: it opens on an indented line that a blank line (or
        # the start of the text) precedes, and every line of the run is code.
        if blank:
            in_indent_run = code_run = False
        elif raw.startswith("\t") or raw.startswith(_CODE_INDENT):
            if not in_indent_run:
                code_run, in_indent_run = prev_blank, True
        else:
            in_indent_run = code_run = False
        prev_blank = blank
        # The HTML block region: inert from its opening line to the line that ends
        # it, and a fence or a comment owns a line before it can open anything.
        if html is not None:
            inert_html = True
            if html[0] == "tag":
                if _SWALLOW_END_RES[html[1]].search(raw):
                    html = None
            elif blank:
                html = None
        else:
            inert_html = False
            if not fenced and not in_comment_here and not blank:
                opened = _html_opener(raw)
                if opened is not None:
                    inert_html = True
                    html = opened
                    # `<pre>…</pre>` on one line: the block is complete here, so only
                    # this line is inert — a report under it is the worker's own.
                    if opened[0] == "tag" and _SWALLOW_END_RES[opened[1]].search(raw):
                        html = None
        if (blank or fenced or in_comment_here or in_quote or code_run or inert_html
                or line in echoed):
            continue
        out.append(raw)
    return out


def _heading_id(match):
    """The first token of a REPORT heading: where the protocol puts the run id."""
    head = match.group("tail").split("·")[0].strip()
    return head.split()[0] if head else ""


def _names_this_run(declared, run_id):
    """Whether a heading's declared id is this run's — '' names no run (the bare
    `**REPORT**` heading form) and counts.

    A writer that copies the id out of its own brief sometimes shortens it to the
    stamp-and-slug it was given (`REPORT 20261009-195001-wg-p4c-fixes2` for the run
    `20261009-195001-wg-p4c-fixes2-qoder-d2e0c1`), and refusing that re-runs a
    writer that reported (P4c-fixes5 (3)). So a declared id counts when it is the
    run id EXACTLY, or a PREFIX of it at least `RUN_ID_STAMP_MIN_CHARS` characters
    long. Every other shape is refused, as before: another run's id — including
    another run's stamp-prefix — an id longer than this run's, a case variant, and
    an id that merely CONTAINS this one (`run-<id>`, which is what a real writer
    printed and is not a prefix).
    """
    if not declared:
        return True
    if declared == run_id:
        return True
    return (len(declared) >= RUN_ID_STAMP_MIN_CHARS
            and run_id.startswith(declared))


def _last_heading(lines):
    """(index, match) of the LAST column-0 REPORT heading in `lines`, or (None, None).

    LAST, not first and not 'any': a transcript that quotes an older run's report and
    then prints its own ends with its own, and a text that prints a claim for this run
    and a claim for another one below it ends with the other one — which is the one
    this reader must believe, and refusing it is the fail-closed answer (P4c-fixes4
    rule 5).
    """
    for i in range(len(lines) - 1, -1, -1):
        m = _REPORT_HEADING_RE.match(lines[i])
        if m:
            return i, m
    return None, None


def _body_claims_another_run(lines, run_id):
    """Whether the report body under the accepted heading holds a further column-0
    ``REPORT <id>`` heading of ANOTHER run — the shape of a text whose last word is
    somebody else's report, whatever the heading above it said."""
    for line in lines:
        m = _BLOCK_HEAD_RE.match(line)
        if m and not _names_this_run(m.group("id"), run_id):
            return True
    return False


def _has_report(tail, task, run_id):
    """Whether the run printed its REPORT — decided by an allowlist, never by asking
    a markdown reader to spot the one construct this week's probe found.

    The claim is the heading, and a heading counts only when it survived
    `_report_view` (no fence, no HTML comment, no quote, no task echo, no indented
    code block, no HTML block), sits at COLUMN 0 with no leading whitespace and no
    markdown indent, is the LAST such heading of the worker's own output BEFORE the
    spawner's closing block (`tools/autoos-agent.py` prints that trailer after the
    child exits, so a REPORT the worker printed under a line starting `writer:` /
    `scope:` / `sandbox changes` is not this run's report — and a worker that forges
    such a line loses its own claim, which costs one leg and never a false
    'completed'), names this run or no run at all, and is followed by a body holding
    no further column-0 ``REPORT <id>`` heading of another run.

    `tools/autoos_report.py`'s parser then reads the SAME rebuilt text: it never gets
    the unfiltered tail, and it can only ever refuse, never grant — a block it finds
    must name this run too, so the two readers cannot disagree about who reported.
    A parser that answers None is no obstacle (``# REPORT`` and ``**REPORT**`` are
    headings the protocol allows and the parser does not read). And any view line
    carrying a separator or control character the line rules are built on (any Zs
    other than ' ', any Zl/Zp, any Cc other than '\\t') makes the WHOLE report
    suspect: has_report False, fail closed, because a text another reader could split
    or strip differently is not a claim to believe.

    `tail` is what `_normalise_output` returned (P4c-fixes5 (1)) — ANSI stripped, CR
    redraws collapsed — so the screen a colour-blind reader would have seen is what
    the rules above are asked about, and the suspect screen only ever fires on a
    character the strip could not account for. The heading is matched by an
    allowlist of DECORATIONS too: `REPORT` at column 0, optionally behind an ATX
    ``#{1,6} `` and optionally wrapped in ``**`` — a list bullet ``- REPORT …`` is
    the writer's own prose list and is not a claim (P4c-fixes5 (2)) — and a heading
    that names a run may name it in full or by a prefix of at least
    `RUN_ID_STAMP_MIN_CHARS` characters, the stamp a writer copies out of its brief
    (P4c-fixes5 (3)).
    """
    lines = _report_view(tail, task)
    if not lines:
        return False
    if any(_suspect_line(line) for line in lines):
        return False
    trailer = _trailer_start(lines)
    if trailer is not None:
        lines = lines[:trailer]
    at, heading = _last_heading(lines)
    if heading is None or not _names_this_run(_heading_id(heading), run_id):
        return False
    if _body_claims_another_run(lines[at + 1:], run_id):
        return False
    try:
        parsed = report_parser.parse_report("\n".join(lines))
    except Exception:  # noqa: BLE001 - a block the parser chokes on is no block
        parsed = None
    return parsed is None or _names_this_run(str(parsed.get("id") or ""), run_id)


def _output_age(run_dir, now):
    """Seconds since the run last wrote to disk, None when nothing says.

    ``output.log``'s mtime is the answer. Before a worker writes at all the
    newest thing in the record is ``job.json``, so a run that has only just
    started is 'running' and not 'stalled'. A filesystem that reports a NaN or an
    infinity for mtime says nothing about age — the file is skipped, and 'no age'
    is 'unknown', which escalates; `age <= stall` on a NaN would answer 'stalled'
    for a live writer, which starts a second one (P4c-fixes4 C).
    """
    for name in ("output.log", "job.json"):
        try:
            mtime = os.stat(os.path.join(run_dir, name)).st_mtime
        except OSError:
            continue
        if not _is_time_number(mtime):
            continue
        return max(0.0, now - mtime)
    return None


def _has_visible_text(task):
    """Whether the task says anything a writer could act on.

    Truthiness is not the question: three spaces, a bare newline, a U+00A0 and an
    ANSI reset are all truthy strings, and each of them is a record that names
    no work — a footer-only brief would otherwise buy a continuation leg (P4c-
    fixes7). The judgement runs on `_normalise_output`'s text, so colour and a
    redraw do not count as content either, and then on what is left once every
    whitespace character and every Zs/Zl/Zp/Cc/Cf character is gone."""
    for ch in _normalise_output(task):
        if ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATS:
            continue
        return True
    return False


def _heading_form(text):
    """`text` reduced to the shape the continuation heading is RECOGNISED in, plus
    where each surviving character came from.

    Recognition goes through a character class, never through a literal prefix:
    every whitespace character and every member of `_INVISIBLE_CATS` (the same set
    `_has_visible_text` refuses on) is removed, and what is left is NFKC-normalised
    and casefolded one character at a time. `str.lstrip()` strips whitespace only,
    so ONE invisible character in front of a footer whose blank line was lost
    (U+200B, U+FEFF, U+200C, U+00AD, \\x00, \\x01, any Cf/Cc) used to hide the heading
    from the check and let a record that names no work buy a continuation leg whose
    brief stacked a second footer on it (D-914, the loop that never stopped).

    Returns `(form, origins)` with `origins[i]` the index in `text` of the character
    that produced `form[i]`, so a caller that found a match can ask what came BEFORE
    it in the original.
    """
    parts = []
    origins = []
    for i, ch in enumerate(text):
        if ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATS:
            continue
        folded = unicodedata.normalize("NFKC", ch).casefold()
        parts.append(folded)
        origins.extend([i] * len(folded))
    return "".join(parts), origins


# The heading in that form, computed once. `_usable_task` refuses a task whose form
# CONTAINS this with nothing visible ahead of the match — `startswith` on the form
# is the at-position-0 case of the same rule. `original_task` cuts at the FIRST
# such match that BEGINS a line (P4c D2).
CONTINUE_HEADING_FORM = _heading_form(CONTINUE_HEADING)[0]


def _footer_prefix_invisible(prefix):
    """Whether what precedes a heading match on ITS OWN LINE names nothing a writer
    could be shown: whitespace, members of `_INVISIBLE_CATS`, a complete ANSI
    sequence's residue (`_has_visible_text` already judged those), and the bytes of
    an UNTERMINATED escape attempt — ESC, its parameter/intermediate run, and one
    final byte. P4c D1: the Fe class eats ESC + the 'C' of 'CONTINUE' in
    '\\x1bCONTINUE FROM ...', and a CSI introducer does it to '\\x1b[CONTINUE FROM
    ...' — both before any strip could run — so that ESC run is damage the footer
    carries, not the brief it hides."""
    if not _has_visible_text(prefix):
        return True
    i = 0
    n = len(prefix)
    while i < n:
        ch = prefix[i]
        if ch == "\x1b":
            i += 1
            while i < n and "\x20" <= prefix[i] <= "\x3f":
                i += 1
            if i < n and "\x40" <= prefix[i] <= "\x7e":
                i += 1
            continue
        if ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATS:
            i += 1
            continue
        return False
    return True


def _footer_line_start(task):
    """Where the continuation footer that this task ENDS with begins in the source,
    or None when it holds no such footer (P4c D2).

    The heading is found through `_heading_form`, and the match is mapped back
    through `origins` to its source index: the first match that BEGINS a line —
    preceded by a line break or the start of the text, with only whitespace and
    invisible characters between — is the footer. Cutting at the LINE start also
    drops those invisible characters, which is what the literal `CONTINUE_MARKER`
    could not: a stored footer wearing a ZWSP, a BOM, a NBSP, a tab, a CRLF
    separator, or no separator at all survived the literal cut, and the rebuilt
    brief stacked a second footer on it. A match with real visible text ahead of
    it on the same line is prose that merely spells the heading out, is skipped,
    and the search continues to the next match."""
    form, origins = _heading_form(task)
    at = form.find(CONTINUE_HEADING_FORM)
    while at >= 0:
        start = origins[at]
        line_start = task.rfind("\n", 0, start) + 1
        if _footer_prefix_invisible(task[line_start:start]):
            return line_start
        at = form.find(CONTINUE_HEADING_FORM, at + 1)
    return None


def _usable_task(job, job_ok):
    """The task a record can be continued from, or None when it cannot say what
    it ran.

    ONE minimal check (D-914): ``job.json`` is a readable dict and its `task` is
    a str that still holds at least one visible character once the continuation
    footer is cut (`original_task`) and the ANSI strip and the Zs/Zl/Zp/Cc/Cf/
    whitespace removal of `_has_visible_text` have run — three spaces, an ANSI
    reset and a bare footer are all truthy strings that name no work (P4c-fixes7).
    A footer that lost the blank line separating it from its brief is the same
    record with the marker's prefix instead of its separator, and names no work
    either (P4c-fixes8) — including the variant where the heading is preceded by
    invisible characters, which only a character-class comparison recognises.
    Beyond the exact cut, a task is refused when its heading form CONTAINS the
    footer's heading form anywhere AND nothing visible comes before that match —
    the check runs on the RAW head, before `_normalise_output` (P4c D1): the ANSI
    Fe class eats ESC + the 'C' of 'CONTINUE', so a strip-first check let
    '\\x1bCONTINUE FROM ...' read as a brief. ESC is a Cc; to `_heading_form` it
    is just one more invisible character. Same content-free record, whatever
    survived the cut `original_task` now makes through the heading form. A task
    with real text before the heading is never refused on a match — prose that
    merely spells the footer out stays a brief and is cut by the exact
    marker alone.

    A continuation brief needs a brief: without a usable task the only text that
    would reach the next writer is the ``CONTINUE FROM CURRENT DIFF`` footer.
    Such a record is 'unknown' with `record_suspect`, which escalates
    (P4c-fixes6) — the same answer whether or not the caller names `--lane`."""
    if not job_ok:
        return None
    task = (job or {}).get("task")
    if type(task) is not str or not task:
        return None
    head = original_task(task)
    form, origins = _heading_form(head)
    at = form.find(CONTINUE_HEADING_FORM)
    if at >= 0 and _footer_prefix_invisible(head[:origins[at]]):
        return None
    if not _has_visible_text(head):
        return None
    return task


def _job_record_suspect(job):
    """Whether a present job.json carries a field of the WRONG type.

    job.json is worker-writable: a writer (or a bug in the runner) can put 123
    where the task string belongs. Every field this module reads is type-checked
    before use — a non-str is never fed to the echo filter or the pid probe — and
    a record that misuses a field it is supposed to carry is 'suspect': too
    damaged to answer 'completed', so it classifies 'unknown' and escalates.
    Absent fields are not suspect; a whole absent/unusable task is (`_usable_task`)
    (P4c-fixes6). `started` is judged by
    `_is_time_number`, so a NaN or an infinity there is a damaged record and not a
    clock reading (P4c-fixes4 C).
    """
    if job is None:
        return False
    for name in ("run_id", "task", "cwd"):
        if name in job and type(job[name]) is not str:
            return True
    if "pid" in job and (type(job["pid"]) is bool or not isinstance(job["pid"], int)):
        return True
    if "started" in job and not _is_time_number(job["started"]):
        return True
    return False


def _exit_record_suspect(ex):
    """Whether a present exit.json carries a time field nothing may do arithmetic
    with: `ended` is the runner's own stamp, and a NaN or infinity in it is a record
    damaged after the fact — the same refusal `_job_record_suspect` gives `started`
    (P4c-fixes4 C). A wrong-typed `rc` is not suspect: it is type-checked where it
    is read and simply never reads as 0."""
    if ex is None:
        return False
    return "ended" in ex and not _is_time_number(ex["ended"])


def classify_run(run_dir, now=None, stall_secs=STALL_SECS, pid_probe=None):
    """What one run record says: {state, rc, has_report, sandbox, branch,
    record_suspect, last_output_age}.

    `state` is one of STATES and 'unknown' is fail-closed: a record this cannot
    read is never 'completed'. A job.json field of the wrong type sets
    `record_suspect` and holds the state at 'unknown' — a record that misuses its
    own fields cannot attest to anything, not even an exit 0 with a REPORT. So does
    a record with no usable task (`_usable_task`: no readable job.json, no task in
    it, or a task that is nothing but the continuation footer) — there is nothing to
    continue, so 'died'/'rerun' would only spawn a writer holding a footer instead
    of a brief (P4c-fixes6, footer-only closed by P4c-fixes8). With no
    ``exit.json`` the run is judged on its pid and its output age: a live pid quiet
    past `stall_secs` is 'stalled', a dead pid is 'died', a pid this host cannot
    probe is 'unknown'. `stall_secs` must be a FINITE number of seconds between 1
    and 7 days — a NaN passed every one-sided comparison, and `age <= nan` is False,
    so `--stall-secs nan` answered 'stalled' for a writer that was alive and 'rerun'
    for a lane that was running; anything unusable is a `RecoveryError` (the CLI's
    usage exit 2), never a verdict. A time field a record carries that is not finite
    (`job.json`'s `started`, `exit.json`'s `ended`) makes the record suspect the same
    way a wrong type does, and an mtime that is not finite says no age at all —
    'unknown'. With an ``exit.json`` it takes rc==0 AND a REPORT to be 'completed';
    anything else that ended is 'died', including an exit 0 that said nothing (that
    is the exit-without-REPORT case writer-ops.md §5 names).

    `run_dir` is the record directory itself (what `run_dir_for` returns);
    `pid_probe` is the liveness hook, injectable so a test can name a pid that is
    alive, dead or somebody else's without touching the host.
    """
    if type(run_dir) is not str or not run_dir:
        raise RecoveryError("run dir %r: a path to <state>/agents/<run_id>" % (run_dir,))
    path = run_dir.rstrip(os.sep) or run_dir
    if os.path.islink(path):
        raise RecoveryError("run dir %r is a symlink" % (path,))
    if not os.path.isdir(path):
        raise RecoveryError("no run dir %r" % (path,))
    ref = _check_time(_now() if now is None else now, "now")
    stall = _check_stall(stall_secs)
    probe = pid_alive if pid_probe is None else pid_probe
    if not callable(probe):
        raise RecoveryError("pid_probe must be callable")

    job, job_ok = _read_json_dict(os.path.join(path, "job.json"))
    ex, ex_ok = _read_json_dict(os.path.join(path, "exit.json"))
    tail = _read_tail(os.path.join(path, "output.log"))
    # The client's raw bytes are normalised here, once, before ANY reader sees them:
    # `_has_report` and `_parse_sandbox` then work on the same text, and a writer
    # whose CLI painted its progress in colour or redrew a line is not read as a
    # writer that printed control characters (P4c-fixes5 (1)).
    tail = _normalise_output(tail)
    # The record's own state root: <state>/agents/<run_id> -> <state>. A sandbox
    # path printed in the log is trusted only inside that root, so the tree the
    # caller named decides it, not the caller's environment on top of that.
    root = os.path.dirname(os.path.dirname(path))
    sandbox, branch = _parse_sandbox(tail, root)
    # A non-str task is '' for the echo filter; a task the record cannot supply at
    # all — no readable job.json, or no task in it — makes the whole record
    # unusable, which is 'unknown' + suspect below, never a verdict and never a
    # brief built from the footer alone (P4c-fixes6).
    task = _usable_task(job, job_ok)
    echo_task = "" if task is None else task
    suspect = ((bool(job_ok) and _job_record_suspect(job))
               or (bool(ex_ok) and _exit_record_suspect(ex))
               or task is None)
    out = {"state": "unknown", "rc": None,
           "has_report": _has_report(tail, echo_task, os.path.basename(path)),
           "sandbox": sandbox, "branch": branch, "record_suspect": suspect,
           "last_output_age": _output_age(path, ref)}
    if not job_ok or not ex_ok or suspect:
        return out

    if ex is not None:
        rc = ex.get("rc")
        out["rc"] = rc if type(rc) is int and type(rc) is not bool else None
        out["state"] = "completed" if (out["rc"] == 0 and out["has_report"]) else "died"
        return out

    pid = (job or {}).get("pid")
    alive = probe(pid if (type(pid) is int and type(pid) is not bool) else None)
    if alive is None or out["last_output_age"] is None:
        return out
    if not alive:
        out["state"] = "died"
    else:
        out["state"] = "running" if out["last_output_age"] <= stall else "stalled"
    return out


def next_action(attempts, state, max_attempts=MAX_ATTEMPTS, record_suspect=False):
    """The move for a classified run: 'none' / 'wait' / 'rerun' / 'escalate'.

    A completed run needs nothing, a running one needs patience, a died/stalled
    one gets a continuation leg while `attempts` is under `max_attempts` and the
    lane after that, and 'unknown' — a record that could not be read — goes to a
    human instead of another spawn. `record_suspect` (a job.json field of the
    wrong type) escalates outright: no 'none' and no leg spent on a record that
    cannot say what it ran.
    """
    if type(attempts) is bool or not isinstance(attempts, int) or attempts < 0:
        raise RecoveryError("attempts: a non-negative int, got %r" % (attempts,))
    if type(state) is not str or state not in STATES:
        raise RecoveryError("state %r: expected %s" % (state, "|".join(STATES)))
    if type(max_attempts) is bool or not isinstance(max_attempts, int) or max_attempts < 0:
        raise RecoveryError("max_attempts: a non-negative int, got %r" % (max_attempts,))
    if type(record_suspect) is not bool:
        raise RecoveryError("record_suspect: bool, got %r" % (record_suspect,))
    if record_suspect:
        return "escalate"
    if state == "completed":
        return "none"
    if state == "running":
        return "wait"
    if state == "unknown":
        return "escalate"
    return "rerun" if attempts < max_attempts else "escalate"


def original_task(task):
    """The brief a run was REALLY given: the task cut at its first continuation
    footer, with the whitespace the footer left behind stripped.

    A recovery leg's ``job.json`` task is the previous leg's brief, which is the
    original text plus one footer (P4c-fixes7), so `continuation_task` cuts here
    before appending the next one — feeding a leg's own brief back in replaces its
    footer instead of stacking a second one. Cutting at the FIRST footer also
    collapses the nested footers a chain of legs stacks.

    The footer is found through `_heading_form`, not only through the literal
    `CONTINUE_MARKER` (P4c D2): the literal cut left every footer whose blank line
    was broken — a ZWSP, BOM, NBSP or tab in front of the heading, a CRLF
    separator, or the separator lost entirely — in the brief, and the rebuilt one
    stacked on top of it. `_footer_line_start` returns the equivalent literal
    result and these variants alike.

    A task that only mentions the heading INSIDE a line of prose keeps it: the
    footer must begin its line. A worker that forges the marker early in its own
    task is cut there, and the rebuilt brief then depends on that prefix alone.
    """
    if type(task) is not str:
        raise RecoveryError("task: str, got %s" % type(task).__name__)
    at = _footer_line_start(task)
    return task.rstrip() if at is None else task[:at].rstrip()


def continuation_task(task_text, attempt, sandbox=None, max_attempts=MAX_ATTEMPTS):
    """The task text for one recovery leg: the original brief plus ONE footer.

    The original task is capped so the footer always arrives, and the sandbox
    path lands in the text only after the same validation the reader used — a
    path that failed to parse raises here rather than being pasted into a brief.
    The input is cut with `original_task` first, so feeding a leg's own brief back
    in replaces its footer instead of stacking a second one (P4c-fixes7).
    """
    if type(task_text) is not str:
        raise RecoveryError("task_text: str, got %s" % type(task_text).__name__)
    if type(attempt) is bool or not isinstance(attempt, int) or not 1 <= attempt <= max_attempts:
        raise RecoveryError("attempt: 1..%d, got %r" % (max_attempts, attempt))
    task = original_task(task_text)
    encoded = task.encode("utf-8")
    if len(encoded) > TASK_MAX_BYTES:
        task = encoded[:TASK_MAX_BYTES].decode("utf-8", "ignore")
    where = None
    if sandbox is not None:
        where = _valid_path(sandbox)
        if where is None:
            raise RecoveryError("sandbox %r is not a usable path" % (sandbox,))
    place = (
        "The sandbox at %s holds your earlier work as commits (git log); do NOT "
        "restart; finish what is missing, run every required check, and end with "
        "the REPORT." % where if where else
        "Your earlier work is in the worktree you are given; do NOT restart; "
        "finish what is missing, run every required check, and end with the REPORT.")
    footer = (CONTINUE_MARKER + "%d/%d): your previous run "
              "ended without a REPORT. %s" % (attempt, max_attempts, place))
    # `task` is `original_task`'s output, already free of trailing whitespace, so
    # the marker's own blank line is the only separator the brief gets.
    return task + footer + "\n"


def path_of(key, state=None):
    """Where this lane's attempt record lives: ``<state>/recovery/<key-hash>.json``."""
    digest = hashlib.sha256(_check_key(key).encode("utf-8")).hexdigest()
    return os.path.join(recovery_dir(state), digest + ".json")


def _no_attempts(key):
    return {"key": key, "attempts": 0, "runs": [], "last_ts": None, "corrupt": False}


def read_attempts(key, state=None):
    """{key, attempts, runs, last_ts, corrupt} for one lane.

    No file is zero attempts. A file that is not a readable record of that shape
    is MAX_ATTEMPTS with corrupt=True: the lane escalates instead of quietly
    getting a fresh budget, and the plan says so. So is a record whose `key` names
    another lane — the file is found by this lane's digest, and one lane's run
    list is not this lane's budget. A symlink there is refused, not read — the
    attempt budget is not a file anyone may point somewhere else.
    """
    path = path_of(key, state)
    if os.path.islink(path):
        raise RecoveryStateError("recovery state %r is a symlink" % (path,))
    data, ok = _read_json_dict(path)
    if data is None and ok:
        return _no_attempts(key)      # the file simply is not there: zero attempts
    runs, attempts = (data or {}).get("runs"), (data or {}).get("attempts")
    if (not ok or data.get("key") != key or type(attempts) is bool
            or not isinstance(attempts, int) or attempts < 0
            or type(runs) is not list or len(runs) > attempts
            or any(type(r) is not str for r in runs)):
        return dict(_no_attempts(key), attempts=MAX_ATTEMPTS, corrupt=True)
    return {"key": key, "attempts": attempts, "runs": list(runs),
            "last_ts": data.get("last_ts"), "corrupt": False}


def _lock_path(key, state=None):
    """Where this lane's lock lives: ``<state>/recovery/<key-hash>.lock``."""
    digest = hashlib.sha256(_check_key(key).encode("utf-8")).hexdigest()
    return os.path.join(recovery_dir(state), digest + ".lock")


def _lock_regular(fd, path):
    """Keep `fd` only if the lock is a real file: the lock is a name in the lane's
    own directory, never a symlink to somewhere else or a pipe someone planted."""
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        _close(fd)
        raise RecoveryStateError("lane lock %r is not a regular file" % (path,))
    return fd


def _lock_open(path, flags):
    """Open the lane's lock name: a regular file at 0600, never a symlink to
    somewhere else or a pipe someone planted there."""
    try:
        return _lock_regular(os.open(path, flags, 0o600), path)
    except OSError as exc:
        raise RecoveryStateError("cannot take the lane lock %r: %s" % (path, exc))


def _acquire_lock(path):
    """Take the lane's exclusive lock; returns the fd `_release_lock` closes.

    WHY: `record_attempt` is a read-modify-write on the lane's budget. Two
    recorders of two DISTINCT dead runs both read attempts=0 and both write 1, so
    the lane silently gets more continuation legs than it was given — the one
    number that keeps a runaway writer from re-running forever.

    The lock is the same ``.lock`` name on both platforms and is held by the open
    file description, so a holder that dies — killed, crashed, or the interpreter
    exiting — loses the lock with its fds and the lane is never blocked by a stale
    file. POSIX uses ``flock`` with ``LOCK_NB``, Windows has no flock (and this
    module must stay
    importable there) so it uses ``msvcrt.locking`` on one byte of the same fd;
    both platforms then pay the same bounded retry loop.
    A lock still contested after `LOCK_RETRIES` tries raises a
    `RecoveryStateError`: refusing to record escalates the lane (the CLI's
    exit 4), which is safe, where recording anyway spends a leg nobody
    counted, which is not.
    """
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    if os.name == "nt":
        import msvcrt  # inside the platform check: no msvcrt off Windows

        for _ in range(LOCK_RETRIES):
            fd = _lock_open(path, flags)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                # Another writer holds the byte: the name stays, the lock does not.
                _close(fd)
                time.sleep(LOCK_RETRY_SECS)
                continue
            return fd
        raise RecoveryStateError("lane lock %r is held by another writer (%d tries)"
                                 % (path, LOCK_RETRIES))
    else:
        import fcntl  # inside the platform check: no fcntl on Windows

        # LOCK_NB, in the SAME bounded retry loop as Windows: a blocking
        # LOCK_EX sleeps in the kernel for as long as the holder likes, and a
        # live holder (a recorder mid-write, or one hung on a slow disk) would
        # hang `record_attempt` unboundedly — the docstring promises refusal
        # after `LOCK_RETRIES`, and an escalate-not-hang lane needs that promise
        # kept. The fd is opened once and closed on EVERY exit path.
        fd = _lock_open(path, flags)
        try:
            for _ in range(LOCK_RETRIES):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Another writer holds the description: try again, bounded.
                    time.sleep(LOCK_RETRY_SECS)
                    continue
                except OSError as exc:
                    raise RecoveryStateError("cannot take the lane lock %r: %s"
                                             % (path, exc))
                return fd
            raise RecoveryStateError("lane lock %r is held by another writer (%d tries)"
                                     % (path, LOCK_RETRIES))
        except BaseException:
            _close(fd)
            raise


def _release_lock(fd):
    """Give the lock back: closing the fd drops both the flock and the msvcrt
    byte lock, so there is no unlock call and no second platform-only import."""
    _close(fd)


def record_attempt(key, run_id, state=None):
    """Record one continuation leg for this lane, idempotent per run id.

    Attempts are counted per lane, not per run: two dead writers on one lane are
    two legs, and the second death of the SAME run is still one leg — a caller
    that retries this call must not spend the budget twice. The read and the write
    are one step under `_acquire_lock`, so two recorders cannot both see the same
    count. The write is atomic (temp + ``os.replace``) at 0600, because this file
    is the lane's budget. A record that is not a readable attempt file is refused
    rather than overwritten: repairing it here would hand the lane back the budget
    the corruption hid. A refusal of the STATE — corrupt, contested, not a regular
    file, unwritable — is a `RecoveryStateError`, which the CLI answers as
    escalate (exit 4); a refused run id or lane key stays a usage error.
    """
    _check_key(key)
    _check_run_id(run_id)
    path = path_of(key, state)
    target = os.path.dirname(path)
    try:
        os.makedirs(target, mode=0o700, exist_ok=True)
        if os.name != "nt":
            # makedirs' mode is umask-masked and never applied to an existing parent.
            os.chmod(target, 0o700)
    except OSError as exc:
        raise RecoveryStateError("cannot prepare the recovery state dir %r: %s"
                                 % (target, exc))
    lock_fd = _acquire_lock(_lock_path(key, state))
    try:
        cur = read_attempts(key, state)
        if cur["corrupt"]:
            raise RecoveryStateError("recovery state %r is corrupt: do not re-arm"
                                     % (path,))
        if run_id not in cur["runs"]:
            record = {"key": key, "attempts": cur["attempts"] + 1,
                      "runs": cur["runs"] + [run_id], "last_ts": _now()}
            try:
                fd, tmp = tempfile.mkstemp(dir=target, prefix=".recovery-",
                                           suffix=".tmp")
            except OSError as exc:
                raise RecoveryStateError("cannot write the recovery state %r: %s"
                                         % (path, exc))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, sort_keys=True)
                os.replace(tmp, path)
            except OSError as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise RecoveryStateError("cannot write the recovery state %r: %s"
                                         % (path, exc))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            cur = dict(cur, attempts=record["attempts"], runs=record["runs"],
                       last_ts=record["last_ts"])
    finally:
        _release_lock(lock_fd)
    return cur


def plan(run_id, lane_key=None, state=None, now=None, stall_secs=STALL_SECS,
         pid_probe=None):
    """The recovery plan for one run, and nothing else happens.

    {action, state, attempts, sandbox, branch, continue_task, spawn_hint, rc,
    has_report, last_output_age, lane_key, state_corrupt, record_suspect,
    run_id}. The lane key is ALWAYS caller-supplied (D-914): `lane_key` must name
    the lane whose attempt budget the run belongs to, validated exactly like
    `record` validates `--lane`. Without one this raises `RecoveryError` — the
    CLI's usage exit 2 — because the lane is never derived from free task text.
    A record whose job.json cannot supply a usable task (absent, unreadable, or
    nothing but a continuation footer) is suspect whatever its lane: an explicit
    `lane_key` buys it a budget to count on, never a brief to continue, so the
    plan escalates rather than continuing a footer. `spawn_hint.cwd` is the kept
    worktree for the L2/L1 to hand to `spawn` — this module never calls it.
    """
    if lane_key is None:
        raise RecoveryError(
            "plan: a lane key is required — the lane is never derived from the "
            "run's task text (pass --lane)")
    key = _check_key(lane_key)
    path = run_dir_for(run_id, state)
    job, job_ok = _read_json_dict(os.path.join(path, "job.json"))
    task = _usable_task(job, job_ok)
    info = classify_run(path, now=now, stall_secs=stall_secs, pid_probe=pid_probe)
    cur = read_attempts(key, state)
    action = next_action(cur["attempts"], info["state"],
                         record_suspect=info["record_suspect"])
    cont, hint = None, None
    if action == "rerun":
        cont = continuation_task(task, cur["attempts"] + 1, info["sandbox"])
        hint = {"cwd": info["sandbox"],
                "note": ("spawn with cwd=<sandbox> (the kept worktree) and this "
                         "continue_task, then record_attempt(<lane_key>, <new run id>)")
                   if info["sandbox"] else
                   ("no sandbox path in the record: spawn without --isolate, or "
                    "name the kept worktree yourself, then "
                    "record_attempt(<lane_key>, <new run id>)")}
    return {"run_id": run_id, "lane_key": key, "action": action,
            "state": info["state"], "attempts": cur["attempts"], "rc": info["rc"],
            "has_report": info["has_report"], "sandbox": info["sandbox"],
            "branch": info["branch"], "last_output_age": info["last_output_age"],
            "state_corrupt": cur["corrupt"], "record_suspect": info["record_suspect"],
            "continue_task": cont, "spawn_hint": hint}


_EXIT_FOR_ACTION = {"none": 0, "wait": 0, "rerun": 3, "escalate": 4}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="autoos_recovery.py",
        description="Run-death recovery plan for one run record (plans only, never spawns).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="print the recovery plan for RUN_ID as JSON "
                                    "(exit 0 none/wait, 3 rerun, 4 escalate)")
    p.add_argument("run_id")
    p.add_argument("--lane", dest="lane", required=True,
                   help="lane key sharing the attempt budget (required: the lane "
                        "is never derived from the task text)")
    p.add_argument("--stall-secs", dest="stall_secs", type=float, default=STALL_SECS,
                   help="quiet for this long is stalled, a finite number of seconds "
                        "between %d and %d (default %d)"
                        % (STALL_SECS_MIN, STALL_SECS_MAX, STALL_SECS))
    r = sub.add_parser("record", help="record one continuation leg for a lane "
                                      "(exit 0; 4 when the lane state is corrupt "
                                      "or contested — escalate)")
    r.add_argument("run_id")
    r.add_argument("--lane", dest="lane", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            out = plan(a.run_id, lane_key=a.lane, stall_secs=a.stall_secs)
            print(json.dumps(out, indent=1, sort_keys=True))
            if out["record_suspect"]:
                print("autoos_recovery: job.json is unreadable or carries a field "
                      "of the wrong type, so it names no task; the record is "
                      "suspected and the lane escalates",
                      file=sys.stderr)
            return _EXIT_FOR_ACTION[out["action"]]
        out = record_attempt(a.lane, a.run_id)
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    except RecoveryStateError as exc:
        # Corrupt / contested / non-regular / unwritable lane state: the spec's
        # answer is a human, not a usage message (P4c-fixes3 exit contract).
        print("autoos_recovery: %s; escalate" % exc, file=sys.stderr)
        return 4
    except RecoveryError as exc:
        # 2 stays ONLY for a usage error: an argument the caller passed in a
        # shape this module refuses to touch.
        print("autoos_recovery: %s" % exc, file=sys.stderr)
        return 2
    except BrokenPipeError:
        raise                                   # the reader went away: the shell's answer
    except KeyboardInterrupt:
        raise                                   # somebody pressed Ctrl-C: not our verdict
    except Exception:                           # noqa: BLE001 - fail closed, never a traceback
        # An unexpected fault is 'needs a human' (4), never 'nothing to do' (0)
        # or 'spend a leg' (3). SystemExit is a BaseException and stays itself.
        print("autoos_recovery: internal error; escalate", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
