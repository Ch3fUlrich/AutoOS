"""Read-only heartbeat check (R-heartbeat-02/03, R-pause-01 migrated into code:
briefs/common.md "Skill rules bind every spawned agent", operator
2026-09-26T13:43:33Z).

Two pure(ish) helpers behind `autoos-agent.py heartbeat` and the MCP
`heartbeat` tool:

    pause_state(inbox_path) -> {"active", "at", "text"}
    repo_branch_state(repo) -> {"unpushed": [(branch, ahead), ...], "dirty": n}

and one grouping helper, `branch_prefix`, both use to decide which unpushed
sibling branches are "owned" by the caller.

Nothing here writes anything: `repo_branch_state` runs only read-only git
plumbing (`rev-parse`, `for-each-ref`, `rev-list --count`, `status
--porcelain`); it never pushes, commits, fetches or creates a branch. A push
or a WIP-commit, when R-heartbeat-02 calls for one, is left to the caller -
this module only reports.
"""
from __future__ import annotations

import datetime
import io
import re
import string
import subprocess

# Case-sensitive, whole word: operator lines write "PAUSE"/"RESUME" in caps
# (e.g. "PAUSE NOW", "PAUSE (operator..."), never lowercase or as part of
# another word ("PAUSED" must not count).
_PAUSE_RE = re.compile(r"\bPAUSE\b")
_RESUME_RE = re.compile(r"\bRESUME\b")
# The acknowledgement markers: RESTART spec §0 names this list its one home, so
# §1's `card: stale` check (lane R2b) and §3's pack cite it rather than
# restating it. A line carrying one of these at its head reports on something
# the session already did, so it absorbs an order word the record itself closes
# (see `CLOSING_WORDS`) — it is not a blanket exemption, and `pause_state` keeps
# every order word the record leaves unclosed. The list held only `lesson:` and
# `→ done` until lane R2a added the other markers in use — a `→ main` reply
# quoting a PAUSE read as a fresh stop.
ACK_MARKERS = ("lesson:", "→ done", "→ ack", "→ relaunched", "→ operator", "→ main")
# the same markers, head-anchored *with a boundary*: mid-sentence a marker is
# only vocabulary, and a marker that is merely the prefix of a longer word
# (`→ mainline`, `→ operators`, `→ doneX`) is vocabulary too — so a marker counts
# only when what follows it is `:`, whitespace or the end of the text
# (R2a3 review, MEDIUM: `→ mainline PAUSE all lanes` was swallowed as an ack).
_MARKER_AT_HEAD_RE = re.compile(r"\A(?:%s)(?=[:\s]|$)"
                                % "|".join(re.escape(marker) for marker in ACK_MARKERS))
# The order words — one list, RESTART spec §0, and the bound on what may pose as a
# speaker. An inbox line that *gives* an order usually opens with the order word, and
# a clause ending in a colon is also exactly what the speaker shape looks like, so
# `PAUSE all lanes: → main is held` was one colon away from reading as `PAUSE all
# lanes` *speaking* — a hard stop that held nothing (R2a4, the Muse review of R2a3;
# a lost order is the one unacceptable outcome here). Case-insensitive and whole
# word, so `hold on:` is ruled out as well as `HOLD:`. Deliberately wider than the
# two words the pause filter keys on (which are case-sensitive): over-ruling a
# prefix costs a spurious order — one wasted heartbeat and a RESUME — while under-
# ruling one costs a lane running against an operator's stop.
ORDER_WORDS = ("PAUSE", "RESUME", "STOP", "HOLD", "FREEZE", "HALT", "ABORT")
_ORDER_WORD_RE = re.compile(r"\b(?:%s)\b" % "|".join(ORDER_WORDS), re.IGNORECASE)
# The closing words (RESTART spec §0) — one list beside the two above, and the only
# thing that lets an acknowledgement absorb an order word. An ack record reports on
# what already happened, so a word from this list within `_CLOSING_WINDOW` words of
# the order word says that order ended: `→ done: PAUSE lifted`,
# `→ main: PAUSE acknowledged`. Anything else after the order word is a *new* order
# wearing an ack's head — `→ done: applied R2a4 fix. PAUSE all lanes until further
# notice` — and a lost order is the one unacceptable outcome here (R2a5, the Sonnet
# review of R2a4: gating the whole record on the ack swallowed exactly that).
# Case-insensitive like ORDER_WORDS, but bounded: the exemption is narrow, the order
# is what survives. Narrow in vocabulary too (R2a6, the Muse review of R2a5) — a word
# may only close an order when it cannot also be ordinary prose *inside* an order
# sentence. `over`, `done` and `noted` sat here and are gone, because they made
# `→ done: noted. PAUSE over the weekend`, `→ done: PAUSE done by 18:00` and
# `→ main: PAUSE noted for all lanes` read as reports of a stop that never ended. What
# is left states one thing only: that the order is over.
CLOSING_WORDS = ("lifted", "ended", "cancelled", "canceled", "removed",
                 "released", "acknowledged", "acked", "cleared", "resolved")
_CLOSING_WORD_RE = re.compile(r"\b(?:%s)\b" % "|".join(CLOSING_WORDS), re.IGNORECASE)
# Which of those words only *report* an order landing, and which undo it. On a stop word
# both kinds close — `PAUSE acknowledged` and `PAUSE cancelled` each end the stop. On a
# release word they pull opposite ways: `RESUME acknowledged` says the release landed, so
# it lifts the stop, while `RESUME cancelled` says it was withdrawn, so it does not
# (R2a7, the Sonnet review of R2a6: the RESUME half has to read the same words the other
# way round). The two are a partition of `CLOSING_WORDS`, derived rather than hand-copied.
REPORTING_CLOSING_WORDS = ("acknowledged", "acked", "cleared", "resolved")
UNDOING_CLOSING_WORDS = tuple(w for w in CLOSING_WORDS
                              if w not in REPORTING_CLOSING_WORDS)
_UNDOING_CLOSING_RE = re.compile(r"\b(?:%s)\b" % "|".join(UNDOING_CLOSING_WORDS),
                                 re.IGNORECASE)
# The two reporting words that state a *release* landing and nothing else — the whole of
# what an acknowledgement may say to lift a stop (R2a8, the Muse review of R2a7:
# `→ done: we should RESUME tomorrow`, `considering RESUME options`, `RESUME pending`,
# `discussed RESUME` each matched a bare unnegated `RESUME` and un-stopped a run nobody
# had released). A subset of the reporting partition, named once here, because `cleared`
# and `resolved` are ordinary vocabulary.
RELEASE_ACK_WORDS = ("acknowledged", "acked")
assert set(RELEASE_ACK_WORDS) <= set(REPORTING_CLOSING_WORDS)
_RELEASE_ACK_RE = re.compile(r"\ARESUME\s+(?:%s)\b" % "|".join(RELEASE_ACK_WORDS))
# The word that names a time, and the shape of one: `RESUME acknowledged at 12:00` is the
# same acknowledgement as `RESUME acknowledged`, `… but ops still holding` is not.
TIME_WORDS = ("at", "on", "by")
_TIME_SHAPE_RE = re.compile(r"\A\D*\d[\w.:+\-]*\Z")
# The negation words (RESTART spec §0) — the fourth one-list rule, and the veto on the
# list above. A closing word with a negation in its sentence closes nothing:
# `PAUSE was not lifted`, `PAUSE isn't cleared`, `PAUSE never released` all report a
# stop that is *still* holding, so the order survives (R2a6). `n't` is a clitic rather
# than a word, so it is matched at the end of the word it hangs on — `isn't`, `wasn't`,
# and `won't` alike; `cannot` is on the list because it is a negation of the close
# spelled as one word, and `→ done: PAUSE cannot be lifted` is the same lost order as
# `was not lifted`.
NEGATION_WORDS = ("not", "cannot", "n't", "never", "no", "without")
_NEGATION_CLITIC = "n't"
_NEGATION_RE = re.compile(r"\b(?:%s)\b|%s\Z"
                          % ("|".join(re.escape(w) for w in NEGATION_WORDS
                                     if w != _NEGATION_CLITIC),
                             re.escape(_NEGATION_CLITIC)),
                          re.IGNORECASE)
# …and the negation that is a prefix rather than a word of its own. `un-` on a closing
# word was R2a6's case (`PAUSE unlifted`); R2a7 widens it to the prefix standing on any
# word of the window, because `PAUSE cleared, unconfirmed by ops` reports the same stop
# `PAUSE was not confirmed cleared` does. The widening is one-sided on purpose: an
# `un-`-shaped word that is only vocabulary (`until`, `units`) can veto a close and hold
# a lane one heartbeat longer, and that is the accepted cost — measured on the real
# corpus, one record changes class (`freeze cleared (2/4 units, 6.73GB)`) and no inbox
# flips.
_NEGATION_PREFIX_RE = re.compile(r"\Aun[A-Za-z]", re.IGNORECASE)
# How many words may stand between the order word and its closing word — enough to
# cover the shapes the writers use ("PAUSE was cleared at 12:00"), no further.
_CLOSING_WINDOW = 3
# …and the one marker that exempts a whole record instead of a single order word: a
# `lesson:` line reports on the code, it never addresses the run, so whatever order
# word it quotes it is not an order.
NEVER_ORDER_MARKERS = ("lesson:",)
# Punctuation an inbox writer sticks beside a word (`PAUSE, lifted`): a closing word
# is matched as a whole word, so each candidate is trimmed before the match.
_WORD_TRIM = string.punctuation + "→…“”–—"
# The speaker prefix an inbox writer puts in front of its own line, at most one
# per record. The shapes are what the real inboxes actually contain
# (logs/handoff-sessions/20260925/inbox, read-only survey): `→ done:` 579 times,
# `from <name>` 254 with no colon, `from <name>:` 79, and the operator's own
# `from L0 (operator) PAUSE NOW`. The `from` form therefore takes the colon
# optionally; a bare `<name>` needs it, so an ordinary first word is not read as
# a speaker.
# A speaker word must look like a name: letters, digits and `-`, `_`, `.`, with at
# least one letter so a bare count (`4 lanes: → main merged`) is prose, not a
# speaker. It carries no `:` (so a prefix always ends at its colon), no `→` (so the
# prefix can never swallow the marker that follows it) and no parentheses (so a
# `(<note>)` delimits the name rather than being eaten as one more word).
_SPEAKER_WORD = r"(?=[A-Za-z0-9._-]*[A-Za-z])[A-Za-z0-9._-]+"
# More than one word is allowed only while the prefix is still delimited by its own
# `(<note>)` or colon, and at most 3 words for the bare `<name>:` shape — so
# `operator on duty: → done: …` and `from L1-main relay (x): → done: …` are both
# acknowledgements (R2a3 review, LOW), while `from L1-main PAUSE all lanes, → main
# when done` is still an order: the clause after the name is the body, not speaker
# words. The `from` form keeps its colon optional, as the real inboxes write
# `from <name>` 254 times with no colon.
_SPEAKER_WORDS = _SPEAKER_WORD + r"(?:\s+" + _SPEAKER_WORD + r")*"
_SPEAKER_PAREN = r"(?:\s*\([^)]*\))?"
_SPEAKER_PREFIX_RE = re.compile(r"\A(?:from\s+%s%s\s*:|from\s+%s\s*\([^)]*\)|from\s+%s"
                                r"|(?:%s\s+){0,2}%s%s\s*:)\s+"
                                % (_SPEAKER_WORDS, _SPEAKER_PAREN, _SPEAKER_WORDS,
                                   _SPEAKER_WORD, _SPEAKER_WORD, _SPEAKER_WORD,
                                   _SPEAKER_PAREN))
# …and a prefix is bounded in length as well as in shape: past this many characters
# (its colon and its `(<note>)` counted, its trailing space not) what stands before
# the colon is a clause, not a name. The longest live speaker prefix in the real
# corpus is 30 (`from L1-backlog (relaunch #3)`).
_SPEAKER_PREFIX_MAX = 40
# What an acknowledgement check ignores at the head of a body: a BOM a writer left
# there, and any space, tab or CR remnant before the marker (R2a3 review, LOW —
# `  → done: PAUSE lifted` read as a fresh order).
_HEAD_JUNK = "\ufeff\u200b \t\r\n"


# …and what separates a head from its payload: the same junk plus the colon or comma a
# writer sticks after a marker (`→ done:`, `→ done,`).
_HEAD_SEPARATORS = _HEAD_JUNK + ":,"
# The sentence boundary (RESTART spec §0, R2a8): a record makes one claim per sentence, so
# the negation that vetoes a close is read across the sentence the order word stands in,
# not across a fixed number of words. `.`, `;`, `!`, `?` and the newline each end one.
_SENTENCE_BREAKS = ".;!?\n"
# A word run that is no word at all — the punctuation and spacing that may stand in front
# of an order word inside its sentence and still leave it the sentence's first word.
_NON_WORD_RUN_RE = re.compile(r"\A[\s\W]*\Z")


def _sentence_span(text: str, start: int, end: int):
    """(begin, past-end) of the sentence of `text` holding `text[start:end]`.

    Split on `. ; ! ?` and newline (R2a8, the Muse review of R2a7): a negation belongs to
    the sentence that made the claim, so `→ done: no merges today. PAUSE lifted` closes
    the stop — that `no` is a different claim — while `PAUSE lifted but it was never
    really confirmed by ops` does not, however far into its own sentence the negation
    stands.
    """
    begin = 0
    for char in _SENTENCE_BREAKS:
        at = text.rfind(char, 0, start)
        if at != -1 and at + 1 > begin:
            begin = at + 1
    stop = len(text)
    for char in _SENTENCE_BREAKS:
        at = text.find(char, end)
        if at != -1 and at < stop:
            stop = at
    return begin, stop


def _speaker_prefix(text: str) -> int | None:
    """The index just past `text`'s speaker prefix, or None when it has none.

    A `_SPEAKER_PREFIX_RE` match is a speaker only while it names no `ORDER_WORD`
    and stays within `_SPEAKER_PREFIX_MAX`. The order-word check reads the whole
    match, its `(<note>)` included — a note that quotes an order word is the order
    talking, not a speaker parenthesising. Rejected, nothing is stripped: the line
    keeps its own head, so a marker that merely followed the colon stops being an
    acknowledgement and the line is classified as an order (R2a4).
    """
    match = _SPEAKER_PREFIX_RE.match(text)
    if match is None:
        return None
    if len(match.group().rstrip()) > _SPEAKER_PREFIX_MAX:
        return None
    if _ORDER_WORD_RE.search(match.group()):
        return None
    return match.end()


def _ack_head(text: str):
    """(marker, payload start) for the record body `text`: the acknowledgement marker it
    opens with, or None, and the index its payload starts at.

    The marker is the one `_ack_marker` reads — at the head of the body (after any
    `_HEAD_JUNK`), or at the head after one `_speaker_prefix` — and the payload is what
    follows it, past the `:` or `,` the writer puts between them. A record with no marker
    has no head to skip, so its payload starts past its speaker prefix alone: that is the
    text a bare order is measured against (R2a8, see `_resumes`).
    """
    body = text.lstrip(_HEAD_JUNK)
    at = len(text) - len(body)
    speaker_at = _speaker_prefix(body)
    match = _MARKER_AT_HEAD_RE.match(body)
    if match is None and speaker_at is not None:
        match = _MARKER_AT_HEAD_RE.match(body[speaker_at:])
        if match is not None:
            at += speaker_at
    if match is None:
        return None, at + (speaker_at or 0)
    at += match.end()
    while at < len(text) and text[at] in _HEAD_SEPARATORS:
        at += 1
    return match.group(), at


def _ack_marker(text: str) -> str | None:
    """The acknowledgement marker `text` opens with, or None when it opens with
    nothing of the kind.

    At the head of the body (after any `_HEAD_JUNK`), or at the head after one
    `_speaker_prefix`. Anywhere else a marker is only vocabulary.
    """
    return _ack_head(text)[0]


def _acknowledgement(text: str) -> bool:
    """True when `text` (a record body, timestamp already removed) opens with an
    acknowledgement marker — at its head (after any `_HEAD_JUNK`), or at the head
    after one `_speaker_prefix`. Anywhere else in the line a marker is
    only vocabulary: `operator: PAUSE all lanes; nothing merges → main until I say
    so` is an order that happens to name `→ main` (R2a review, MEDIUM).

    An acknowledgement does not exempt the record's order words wholesale — that was
    the R2a5 bug. See `_gives_order` for what a marked record still orders.
    """
    return _ack_marker(text) is not None


def _is_negation(word: str) -> bool:
    """True when `word` (one trimmed word from a window) negates: a `NEGATION_WORDS`
    word, a clitic hanging off it (`won't`), or the `un-` prefix."""
    return _NEGATION_RE.search(word) is not None or _NEGATION_PREFIX_RE.match(word) is not None


def _order_word_is_negated(text: str, start: int, end: int) -> bool:
    """True when a negation stands anywhere in the sentence of the order word
    `text[start:end]` — `PAUSE lifted but not confirmed`, `PAUSE lifted but it was never
    really confirmed by ops`, `no RESUME given`.

    The sentence is the unit (R2a8, the Muse review of R2a7, HIGH): the veto stopped at
    `_CLOSING_WINDOW` words, so `PAUSE lifted but it was never really confirmed by ops`
    — the negation five words out — closed a stop nobody confirmed lifted. R2a7 already
    widened the veto from *before the closing word* to the *whole window*, and the window
    is simply too short for a sentence that goes on talking after its closing word. What
    bounds it now is the sentence itself, so a negation of a different claim
    (`→ done: no merges today. PAUSE lifted`) stays out of it. A negation that is only
    vocabulary (`until`, `units`) still vetoes and holds a lane one heartbeat longer —
    the accepted cost, measured on the real corpus in the R2a7 and R2a8 changelog entries.
    """
    begin, stop = _sentence_span(text, start, end)
    return any(_is_negation(word.strip(_WORD_TRIM))
               for word in text[begin:stop].split())


def _order_word_is_closed(text: str, start: int, end: int,
                          closing_re: re.Pattern = _CLOSING_WORD_RE) -> bool:
    """True when `closing_re`'s unnegated closing word stands within `_CLOSING_WINDOW`
    words after the order word `text[start:end]` — `PAUSE lifted`, `PAUSE was cleared at
    12:00`, `PAUSE, cancelled`.

    A `NEGATION_WORDS` word standing anywhere in the order word's *sentence* vetoes the
    close, and so does an `un-` prefix on any word of it: `PAUSE was not lifted`,
    `PAUSE isn't cleared`, `PAUSE unlifted`, `PAUSE lifted but not confirmed` and
    `PAUSE lifted but it was never really confirmed by ops` all report a stop that is still
    holding (R2a6, the Muse review of R2a5 — the generic words the review struck from
    `CLOSING_WORDS` are what made the first two shapes readable as closed at all; R2a7
    widened the veto to the whole window; R2a8 to the whole sentence; see
    `_order_word_is_negated`). `closing_re` is `CLOSING_WORDS` for a stop word and
    `UNDOING_CLOSING_WORDS` for a release word, which the words that only report a landing
    leave counting.
    """
    if _order_word_is_negated(text, start, end):
        return False
    for word in text[end:].split()[:_CLOSING_WINDOW]:
        if closing_re.fullmatch(word.strip(_WORD_TRIM)):
            return True
    return False


def _gives_order(text: str) -> bool:
    """True when the record body `text` gives an order rather than reporting one.

    A record with no acknowledgement marker at its head gives one wherever an
    `ORDER_WORDS` word appears — the wide reading of R2a4: an order that wears its
    own first clause as a speaker is still an order. A marked record exempts an
    order word **only** where an unnegated closing word follows it within
    `_CLOSING_WINDOW` words, so `→ done: PAUSE lifted` reports a stop that ended while
    `→ done: applied R2a4 fix. PAUSE all lanes until further notice` gives a fresh
    one (R2a5, the Sonnet review of R2a4: gating the whole record on the marker lost
    that order, and a lost order is the one unacceptable outcome here). The one
    marker in `NEVER_ORDER_MARKERS` (`lesson:`) exempts the whole record — a lesson
    reports on the code and never addresses the run.
    """
    marker = _ack_marker(text)
    if marker is not None:
        if marker in NEVER_ORDER_MARKERS:
            return False
        return any(not _order_word_is_closed(text, match.start(), match.end())
                   for match in _ORDER_WORD_RE.finditer(text))
    return _ORDER_WORD_RE.search(text) is not None


def _is_punctuation_or_time(text: str) -> bool:
    """True when `text` holds no claim — only punctuation and/or a time.

    `RESUME acknowledged`, `RESUME acked.` and `RESUME acknowledged at 12:00` all say one
    thing; `RESUME acknowledged but ops still holding` says something else, and R2a8's
    strict release is the sentence that says nothing but the acknowledgement. A `TIME_WORDS`
    word counts only while the token after it is the time it names.
    """
    tokens = text.split()
    while tokens:
        token = tokens.pop(0).strip(_WORD_TRIM)
        if not token:
            continue
        if _TIME_SHAPE_RE.match(token):
            continue
        if (token.lower() in TIME_WORDS and tokens
                and _TIME_SHAPE_RE.match(tokens[0].strip(_WORD_TRIM))):
            continue
        return False
    return True


def _release_is_acknowledged(sentence: str) -> bool:
    """True when `sentence` is exactly `RESUME` plus a `RELEASE_ACK_WORDS` word, with only
    punctuation or a time after it — what an acknowledgement must say to lift a stop."""
    match = _RELEASE_ACK_RE.match(sentence)
    return match is not None and _is_punctuation_or_time(sentence[match.end():])


def _resumes(text: str) -> bool:
    """True when the record body `text` *orders* the release, which is the only shape that
    lifts a stop (R2a8, the Muse review of R2a7, HIGH: R2a7 gated the RESUME word the way
    a PAUSE is gated — loose — so a mere mention of an unnegated, un-undone `RESUME`
    released a run: `→ done: we should RESUME tomorrow`, `→ done: considering RESUME
    options`, `→ done: RESUME pending`, `→ done: discussed RESUME`).

    Two shapes count and nothing else, both read off the shared head/negation helpers —
    there is no second copy of the rule. (a) A record with **no** acknowledgement marker
    lifts when `RESUME` is the first word of its payload or of its sentence — the
    imperative the operator writes (`operator: RESUME all lanes`, `from L0 (operator) RESUME
    now`, `work done. RESUME every lane`) — unnegated in that sentence and not undone by a
    closing word within `_CLOSING_WINDOW` words after it. (b) An acknowledgement lifts when
    its sentence says nothing but the release landing (`→ done: RESUME acknowledged`), which
    is a report, not an order, and is exactly as wide as it has to be. A `lesson:` record
    lifts nothing: it reports on the code and never addresses the run.

    The asymmetry that sets the width points the other way for a release than for a stop: a
    release wrongly refused costs one wasted heartbeat and one re-issued `RESUME`, a release
    nobody gave is the lost stop every rule here exists to prevent. So where a stop word's
    negation only vetoes its *close* (`PAUSE NOW, no launches` is a hard stop), a negated
    `RESUME` counts in no record shape, and a mention that is neither of the two shapes never
    counts either.
    """
    marker, head = _ack_head(text)
    if marker in NEVER_ORDER_MARKERS:
        return False
    for match in _RESUME_RE.finditer(text):
        begin, stop = _sentence_span(text, match.start(), match.end())
        begin = max(begin, head)
        if not _NON_WORD_RUN_RE.match(text[begin:match.start()]):
            continue  # not the sentence's first word: a mention, not an order
        sentence = text[begin:stop]
        if _order_word_is_negated(text, match.start(), match.end()):
            continue
        if marker is not None:
            if _release_is_acknowledged(sentence):
                return True
            continue
        if _order_word_is_closed(text, match.start(), match.end(), _UNDOING_CLOSING_RE):
            continue
        return True
    return False


_TIMESTAMP_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')

# The first-80-chars report the CLI/MCP print for an active pause.
_TEXT_PREVIEW = 80


def branch_prefix(branch: str) -> str:
    """The grouping prefix of a branch name: up to and including its last
    "/" (a namespaced branch, e.g. "L1-backlog/agy-tarball" ->
    "L1-backlog/"), else up to and including its last "-" (e.g.
    "worktree-agent-a957350031c1fae19" -> "worktree-agent-", grouping every
    sibling worktree lane's branch), else the whole name when it has neither.
    """
    idx = branch.rfind("/")
    if idx < 0:
        idx = branch.rfind("-")
    return branch[: idx + 1] if idx >= 0 else branch


def _parse_iso(text: str) -> datetime.datetime:
    """A minimal ISO-8601 UTC parser (mirrors autoos-agent.py's own
    `parse_now`, duplicated here so this module never imports the CLI's
    hyphenated-filename module). A naive string is read as UTC."""
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def parse_inbox_line(line: str):
    """(when, text) for one inbox line (format `<ISO time> <text>`), or None
    when the line is blank, has no text after its timestamp, or its leading
    token is not a parseable ISO-8601 timestamp.

    A reply line (one that opens with an `ACK_MARKERS` marker, the shape
    `_acknowledgement` recognises) is parsed exactly the same way — it is still
    scanned for PAUSE/RESUME below, because an acknowledgement absorbs only the
    order words the record closes (see `_gives_order`), never the record whole.
    """
    line = line.lstrip(_HEAD_JUNK).rstrip("\n").rstrip("\r")
    if not line.strip():
        return None
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        when = _parse_iso(parts[0])
    except ValueError:
        return None
    return when, parts[1]


_NONE_PAUSE = {"active": False, "at": None, "text": None}


def session_start(transcript_path: str | None):
    """The first ``timestamp`` in a session transcript (JSONL), to the second,
    or None when the file is missing or has none. A relaunch is the resume:
    pause_state() ignores a PAUSE line older than this."""
    if not transcript_path:
        return None
    try:
        with io.open(transcript_path, encoding="utf-8") as fh:
            for raw in fh:
                found = _TIMESTAMP_RE.search(raw)
                if found:
                    return _parse_iso(found.group(1).split(".")[0] + "Z")
    except (OSError, ValueError):
        return None
    return None


def pause_state(inbox_path: str | None, since=None) -> dict:
    """The newest PAUSE/RESUME state of an inbox file (R-pause-01,
    R-heartbeat-03: "a hard stop, checked every heartbeat and before every
    launch").

    PAUSE is active when the newest line whose text contains the word PAUSE
    is newer than the newest line containing the word RESUME, or there is no
    RESUME line at all. Both halves are gated on their word being an order in its own
    record — `_gives_order` for the PAUSE, `_resumes` for the RESUME, both reading the same
    head, sentence and closing-word helpers (never a second copy of the rule) and the RESUME
    read strictly on top of that (R2a7, the Sonnet review of R2a6: a bare RESUME match let
    `→ done: applied the fix already; RESUME was never issued, still holding` lift a stop
    that was never lifted; R2a8, the Muse review of R2a7: R2a7's gate still lifted on a
    *mention* — `→ done: we should RESUME tomorrow`. A RESUME lifts in exactly two shapes,
    the imperative of an unmarked record and an acknowledgement that says nothing but
    `RESUME acknowledged`). A line older than `since` (the session start, see
    session_start()) never counts — the relaunch after a pause is its resume. A
    PAUSE is skipped only where the record closes it: an acknowledgement — a body
    that opens with an `ACK_MARKERS` marker, at the head or at the head after one
    `_speaker_prefix` (see `_acknowledgement`) — exempts that order word only when an
    unnegated `CLOSING_WORDS` word follows it within `_CLOSING_WINDOW` words *and* no
    negation stands anywhere in the order word's own sentence, so `→ done: PAUSE lifted`
    and `→ main: PAUSE acknowledged` report a stop that ended, while
    `→ done: noted. PAUSE over the weekend`, `→ done: PAUSE was not lifted` and
    `→ done: PAUSE lifted but it was never really confirmed by ops` are orders still in
    force (R2a6, the Muse
    review of R2a5: the generic words `over`, `done`, `noted` closed orders nobody closed,
    and a negated closing word — `was not lifted`, `isn't cleared` — did too; R2a7 for the
    negation *after* the closing word; R2a8 for the negation past the 3-word window, see
    `_order_word_is_negated`), while
    `→ done: applied the fix. PAUSE all lanes until further notice`
    gives a fresh order and wins (R2a5, the Sonnet review of R2a4: gating the whole
    record on the marker lost that order, and a lost order is the one unacceptable
    outcome; see `_gives_order`). A `lesson:` record (`NEVER_ORDER_MARKERS`) is
    never an order at all: it reports on the code. A prefix that names an
    `ORDER_WORD` is no prefix at all, so `PAUSE all lanes: → main is held` is the
    order it reads like (R2a4). Elsewhere in the line a marker is vocabulary, not an
    acknowledgement. Lines are ordered by their own parsed timestamp, not file
    order, so an
    inbox is read correctly even if a line was appended
    out of order. A missing/unreadable inbox, or one with no PAUSE line
    (win or lose to a RESUME), is `{"active": False, "at": None, "text":
    None}`; an active pause also carries the winning line's own timestamp
    (`at`, ISO 8601 UTC) and the first 80 characters of its text (`text`).
    """
    if not inbox_path:
        return dict(_NONE_PAUSE)
    try:
        with io.open(inbox_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return dict(_NONE_PAUSE)
    newest_pause = None  # (when, text)
    newest_resume = None  # when
    for raw in lines:
        parsed = parse_inbox_line(raw)
        if parsed is None:
            continue
        when, text = parsed
        if _resumes(text) and (newest_resume is None or when > newest_resume):
            newest_resume = when
        if since is not None and when < since:
            continue
        if (_PAUSE_RE.search(text) and _gives_order(text)
                and (newest_pause is None or when > newest_pause[0])):
            newest_pause = (when, text)
    if newest_pause is None:
        return dict(_NONE_PAUSE)
    if newest_resume is not None and newest_resume > newest_pause[0]:
        return dict(_NONE_PAUSE)
    at = newest_pause[0].isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"active": True, "at": at, "text": newest_pause[1][:_TEXT_PREVIEW]}


def _git(repo: str, *args: str):
    """One read-only git plumbing call in `repo`; (stdout.strip(), returncode).
    Never raises - a bad repo or a bad ref just gets a non-zero rc."""
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    except OSError:
        return "", 1
    return r.stdout.strip(), r.returncode


_EMPTY_REPO_STATE = {"unpushed": [], "dirty": 0}


def repo_branch_state(repo: str) -> dict:
    """{"unpushed": [(branch, ahead), ...], "dirty": n} for one repo
    (R-heartbeat-02: "push every branch you own with new commits").

    `unpushed` covers two kinds of branch: every local branch whose
    configured upstream exists and is behind it (`ahead` = the count of
    `git rev-list <upstream>..<branch>`, i.e. commits the branch has that its
    own upstream does not), plus every local branch with NO upstream at all
    whose name shares the current branch's `branch_prefix()` - a sibling
    worktree lane's branch, or the current branch itself when it has never
    been pushed (`ahead` = `git rev-list <branch> --not --remotes`, commits no
    remote-tracking ref has ever seen). A branch in either group with 0 ahead
    commits is left out. `dirty` is the number of lines `git status
    --porcelain` reports (tracked and untracked changes alike).

    Never raises: a path that is not a git checkout (or has no commits yet)
    reports `{"unpushed": [], "dirty": 0}`.
    """
    current, rc = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return dict(_EMPTY_REPO_STATE)
    prefix = branch_prefix(current)
    refs_out, rc = _git(repo, "for-each-ref", "refs/heads",
                        "--format=%(refname:short)\t%(upstream:short)")
    unpushed = []
    if rc == 0:
        for line in refs_out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            branch = parts[0]
            upstream = parts[1] if len(parts) > 1 else ""
            if upstream:
                out, cnt_rc = _git(repo, "rev-list", "--count", "%s..%s" % (upstream, branch))
            elif branch.startswith(prefix):
                out, cnt_rc = _git(repo, "rev-list", "--count", branch, "--not", "--remotes")
            else:
                continue
            if cnt_rc != 0:
                continue
            try:
                n = int(out or "0")
            except ValueError:
                continue
            if n > 0:
                unpushed.append((branch, n))
    status_out, rc = _git(repo, "status", "--porcelain")
    dirty = len(status_out.splitlines()) if rc == 0 and status_out else 0
    return {"unpushed": unpushed, "dirty": dirty}
