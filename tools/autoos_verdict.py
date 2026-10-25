"""One verdict grammar (AO-SEAT-VERDICT-GRAMMAR): what a review seat's answer MEANS.

Measured: seats wrote `**VERDICT:** ACCEPT` and the parse found 0 refs, a blind seat was
counted anyway, and the seat's text and the record's `verdict=` field disagreed about a
yes. One owner: `parse_verdict`, `verdict_word`, `seat_prompt`. Hermetic (D-852)."""
import re


VERDICTS = ("ACCEPT", "REJECT", "HOLD")
# Transcript readers retain their historical words, but never own a second seat grammar.
LEGACY_AGENT_VERDICTS = ("ready", "fix-first", "not-ready")
LEGACY_CALL_VERDICTS = ("pass", "fail-with-findings")
TRANSCRIPT_VERDICTS = (tuple(word.lower() for word in VERDICTS)
                      + LEGACY_AGENT_VERDICTS + LEGACY_CALL_VERDICTS)
READY_TOKEN = "accept"        # the record gate's yes, lower-cased like its word list
UNCOUNTABLE_TOKEN = "HOLD"    # a stated HOLD is honest, and reviews nothing

# A seat that says IT never saw the change is no seat. Anchored on the seat's own subject
# (P2/G2): the whole-text substring used to void a verdict for "the fixture cannot see the
# row", and it voided a REJECT too — a blocking finding must not vanish (fail-closed keeps
# the finding). The object is glued to the verb so "I did not find any issue in the diff",
# the most ordinary ACCEPT prose there is, stays a review.
_BLIND_OBJECT = r"(?:the|this|that|your|no)\s+(?:change|changes|diff|diffs|patch|patches)\b"
BLIND_PHRASES = (
    r"\b(?:I|we|this seat|my seat)\b[^\n.]{0,40}?"
    r"\b(?:could not|couldn't|cannot|can't|was unable to|unable to|did not|didn't)\b"
    r"[^\n.]{0,20}?\b(?:see|seen|read|view|access|find|found)\b\s+" + _BLIND_OBJECT,
    r"\bREVIEW-DIFF\.patch\b[^\n.]{0,20}?\b(?:is|was)\b[^\n.]{0,20}?"
    r"\b(?:empty|missing|not found|not present|unreadable)\b",
    r"\bno access to the diff\b",
)
_BLIND_RES = tuple(re.compile(p, re.IGNORECASE) for p in BLIND_PHRASES)

_MARKDOWN = ("**", "__", "`", "*", "_", "~")
_PUNCT = ".,;:!?'\" "
# Decoration is stackable (`> # VERDICT: ACCEPT`) and numbered prose is a bullet too
# (`1. VERDICT: ACCEPT`, `a) VERDICT: ACCEPT`), so it is stripped in a loop, once layer
# at a time, by BOTH doors: markdown here and the trailing punctuation below.
_DECOR_RE = re.compile(r"^(?:[#>*+-]+|[0-9]+[.)]|[a-zA-Z][.)])[ \t]*")
# Zero-width and no-break space are how a seat that "did not" say it blind reads as one
# that did (or the reverse); fold them before ANY matching so the two are one spelling.
_INVISIBLE = {0x200b: "", 0x200c: "", 0x200d: "", 0x2060: "", 0xfeff: "", 0x00a0: " "}


def _fold_invisible(text):
    return (text or "").translate(_INVISIBLE)


def _strip_decoration(text):
    while True:
        stripped = _DECOR_RE.sub("", text, count=1)
        if stripped == text:
            return text
        text = stripped.strip()


_VERDICT_RE = re.compile(r"(?i)^verdict\s*:(?P<rest>.*)$")


def _normalise(line):
    """One line as BOTH doors read it (P2/G3): decoration off — heading, quote, bullet,
    list marker, bold, code ticks — then the punctuation wrapped around it. `ACCEPT.` and
    `VERDICT: ACCEPT.` therefore agree with `ACCEPT`, so an entry field and a seat's first
    line can never disagree by wearing different decoration."""
    text = _strip_decoration(_fold_invisible((line or "").strip()))
    for mark in _MARKDOWN:
        text = text.replace(mark, " ")
    return re.sub(r"[ \t]+", " ", text).strip().strip(_PUNCT).strip()


def verdict_word(value):
    """The bare verdict WORD one value states, upper-cased, or None. `**ACCEPT**` is
    `ACCEPT` — markdown was never disagreement; a word outside the grammar comes back as
    itself, so the caller's list says what `READY` means."""
    return _normalise(value).upper() or None


def _blind_statement(text):
    """What the seat says about its OWN view of the change, or None."""
    folded = _fold_invisible(text or "")
    for rx in _BLIND_RES:
        hit = rx.search(folded)
        if hit:
            return hit.group(0).strip()
    return None


def parse_verdict(text):
    """Read the shared decision scan, then apply seat-only blindness/countability rules.

    Fences, diff hunks, blockquotes and >=4-column indentation are quoted material.
    Identical decisions are allowed; any different words fail closed. Positive trailing
    prose is invalid (and still participates in conflicts); negative prose is retained.
    """
    out = {"verdict": None, "valid": False, "reason": "", "countable": False}

    def invalid(reason):
        out["reason"] = reason
        return out

    lines = [ln for ln in (text or "").splitlines() if _fold_invisible(ln).strip()]
    if not lines:
        return invalid("no non-empty line to read a verdict from")
    verdict, reason = _decision_scan(text, ())
    if reason:
        return invalid(reason)
    out["verdict"] = verdict
    blind = _blind_statement(text)
    if verdict == "REJECT":
        out.update(valid=True, countable=True)
    elif blind:
        out.update(valid=verdict == UNCOUNTABLE_TOKEN,
                   reason="says it %s, so its %s is not a countable verdict" % (blind, verdict))
    else:
        out.update(valid=True, countable=verdict != UNCOUNTABLE_TOKEN)
    return out


def seat_prompt(angle, criteria, base_line):
    """The review seat's brief: its angle, the criteria verbatim, the answer grammar.

    `base_line` is REQUIRED (P2/G1b): it is the proof of what the seat reviewed, and an
    empty one printed a template that asked for nothing — silently useless."""
    if not (base_line or "").strip():
        raise ValueError("seat_prompt needs a non-empty base_line: the seat must quote "
                         "the sandbox base it reviewed")
    lines = ["You are an independent review seat. Your angle: %s." % angle,
             "Review criteria, verbatim:", criteria, "",
             "Answer with your verdict on the FIRST line of your reply, exactly:",
             "  VERDICT: " + "|".join(VERDICTS),
             "Use that word alone, findings underneath.",
             "REJECT only for a finding of this severity: a secret or a path outside the"
             " sandbox in the change; a stamp mismatch, the sandbox base not what this"
             " prompt quoted; the change not visible to you; a crash where a clean refusal"
             " was due. Everything else is minor — say it and ACCEPT.",
             "For every REJECT give the repro: the exact command you ran, and what it printed.",
             "Quote this line verbatim in your report, as proof of what you reviewed: %s"
             % base_line.strip()]
    return "\n".join(lines) + "\n"


def _candidate(line):
    """Normalise markup before splitting the label, independent of colon placement."""
    text = _normalise(line).strip("[]()<>'\" ")
    match = _VERDICT_RE.match(text)
    return re.sub(r"(?i)\bnot\s+ready\b", "not-ready", match.group("rest").strip()) if match else None


def is_verdict_echo(line):
    """Only two distinct vocabulary words joined solely by separators are an echo."""
    rest = _candidate(line)
    if rest is None:
        return False
    words = re.findall(r"[a-z]+(?:-[a-z]+)*", rest.lower())
    choices = {word for word in words if word in TRANSCRIPT_VERDICTS}
    remainder = re.sub(r"[a-z]+(?:-[a-z]+)*", "", rest.lower())
    return (len(choices) >= 2 and all(w in TRANSCRIPT_VERDICTS or w == "or" for w in words)
            and not re.sub(r"[\s|/,<>\[\]()'\"]", "", remainder))


def is_seat_line(line):
    value, _ = _decision(line)
    return value in VERDICTS


def _decision(line):
    rest = _candidate(line)
    if rest is None or is_verdict_echo(line):
        return None, None
    rest = rest.lstrip("[]()<>'\" ")
    match = re.match(r"(?i)^([a-z]+(?:-[a-z]+)*)(?![a-z-])(.*)$", rest)
    if not match or match.group(1).lower() not in TRANSCRIPT_VERDICTS:
        return None, "unknown verdict word: " + rest[:60]
    word, tail = match.group(1).lower(), match.group(2)
    # Positive prose is not a clean approval. Retain its word for conflict detection,
    # but invalidate it even alone. Negative findings must never be discarded.
    tail = tail.strip("[]()<>'\" .,!?:; ")
    reason = "words after the verdict word (or colon)" if tail and word in ("accept", "ready", "pass") else None
    return word.upper() if word.upper() in VERDICTS else word, reason


def transcript_value(line, legacy):
    """Read one candidate with the shared grammar and the caller's vocabulary."""
    if line.lstrip().startswith(">"):
        return None
    value, reason = _decision(line)
    return value if not reason and (value in VERDICTS or value in legacy) else None


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ 	]*$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _indent_columns(line: str) -> int:
    """The line's leading indent in CommonMark columns: a tab advances to the
    next multiple of 4, so a line starting with a tab indents 4 columns."""
    col = 0
    for ch in line:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4 - (col % 4)
        else:
            break
    return col


def _decision_scan(text, legacy):
    """One quoted-material and decision policy for seats and transcript readers."""
    raws = [_ANSI_RE.sub("", line) for line in (text or "").splitlines()]
    decisions = set()
    indented = set()  # decisions inside >= 4-column indented code (never the answer)
    originals = {}
    errors = []
    fence = None               # (char, length) while a block is open
    first_marker = None
    hunk = None                # (old, new) lines left in the active hunk body
    for i, raw in enumerate(raws):
        if hunk is not None:
            old, new = hunk
            if old <= 0 and new <= 0:
                hunk = None
            elif raw.startswith(" "):
                hunk = (old - 1, new - 1)
                continue
            elif raw.startswith("-"):
                hunk = (old - 1, new)
                continue
            elif raw.startswith("+"):
                hunk = (old, new - 1)
                continue
            elif raw.startswith("\\"):
                continue  # "\ No newline at end of file" counts toward neither
            elif raw == "":
                # a blank context line that lost its single leading space to a
                # trailing-whitespace strip is still hunk content
                hunk = (old - 1, new - 1)
                continue
            else:
                hunk = None
        if fence is not None:
            m = _FENCE_CLOSE_RE.match(raw)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            continue
        if raw.startswith(("diff --git", "index ", "---", "+++")):
            continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = (int(m.group(1) or 1), int(m.group(2) or 1))
            continue
        m = _FENCE_OPEN_RE.match(raw)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            if first_marker is None:
                first_marker = i
            continue
        # RC-2 seat A: >= 4 columns of leading whitespace is an indented code
        # block in CommonMark - a paste, not the reviewer's verdict - so it
        # never counts, in any of the three public readers.
        if _indent_columns(raw) >= 4:
            # ...but it may still CONTRADICT the real verdict: an indented
            # decision never becomes the answer, yet a different one fails
            # closed (a 4-space REJECT after ACCEPT must not read as ACCEPT).
            value, _ = _decision(raw.strip())
            if value:
                indented.add(value)
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            continue
        value, reason = _decision(stripped)
        if value:
            decisions.add(value)
            originals[value] = stripped
        if reason:
            errors.append(reason)
    if fence is not None and first_marker is not None:
        return _decision_scan("\n".join(raws[:first_marker]), legacy)
    if len(decisions) > 1:
        return None, "two verdicts in one answer"
    # Only a POSITIVE answer can be undercut by an indented contradiction; a real
    # negative stays the answer whatever a pasted sample says (RC-2 seat A).
    if (decisions and indented - decisions
            and next(iter(decisions)).lower().rstrip(".!") in ("accept", "pass", "ready")):
        return None, "an indented verdict line contradicts the answer"
    if errors:
        return None, errors[0]
    if not decisions:
        return None, "no verdict line in the answer"
    value = decisions.pop()
    if value not in VERDICTS and value not in legacy:
        return None, "unknown verdict word: " + value
    if legacy == LEGACY_AGENT_VERDICTS and value in legacy:
        # Preserve historical presentation for record consumers, not decision identity.
        original = _fold_invisible(originals[value]).split(":", 1)[1].strip().strip("*_` ").strip()
        if re.fullmatch(r"(?i)(?:ready|fix-first|not[- ]ready)[.!?]?", original):
            return original, None
    return value, None


def transcript_verdict(text: str, legacy=LEGACY_CALL_VERDICTS) -> str | None:
    """Same decision scan as seats; unsupported or conflicting words yield missing/null."""
    value, reason = _decision_scan(text, legacy)
    return None if reason else value
