"""One verdict grammar (AO-SEAT-VERDICT-GRAMMAR): what a review seat's answer MEANS.

Measured: seats wrote `**VERDICT:** ACCEPT` and the parse found 0 refs, a blind seat was
counted anyway, and the seat's text and the record's `verdict=` field disagreed about a
yes. One owner: `parse_verdict`, `verdict_word`, `seat_prompt`. Hermetic (D-852)."""
import re

from autoos_ready_guards import _Fences  # the one markdown-fence rule (P4a fix 1)

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

_MARKDOWN = ("**", "__", "`", "*", "_")
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


_VERDICT_RE = re.compile(r"(?i)^verdict:(?P<rest>.*)$")


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
    """`{"verdict", "valid", "reason", "countable"}` for one seat's whole answer.

    Exactly ONE line, decoration normalised, must be `VERDICT: ACCEPT|REJECT|HOLD` — one
    colon, the bare word, trailing punctuation tolerated — wherever it stands (D-1515: a
    seat that reviews first and concludes last was logged as no verdict); a line inside a
    markdown fence or a `>` blockquote is quoted material and never counts. No verdict line,
    a second verdict, an unknown word (`SHIP`, `LGTM`), or the reviewer talking after the
    word on that line is invalid and says which. A seat that says IT could not see the change has voided its
    ACCEPT (P2/G2); a REJECT stands — losing a blocking finding is the worse failure — and
    a HOLD stays valid but never countable."""
    out = {"verdict": None, "valid": False, "reason": "", "countable": False}

    def invalid(reason):
        out["reason"] = reason
        return out

    lines = [ln for ln in (text or "").splitlines() if _fold_invisible(ln).strip()]
    if not lines:
        return invalid("no non-empty line to read a verdict from")
    # D-1515: the verdict line may stand anywhere (seats review first and conclude last);
    # exactly one is read, zero or two fail closed. A fenced line is quoted material
    # (a sample from the diff), never the seat's own: the one fence rule every reader
    # shares (_Fences), so an unclosed fence swallows the rest.
    fences = _Fences()
    # A `>` blockquote is quoted material the same way (seat qwen d15v r3).
    own = [ln for ln in lines
           if not fences.feed(ln) and not _fold_invisible(ln).lstrip().startswith(">")]
    # The harness can echo the prompt's grammar. It is a template, not a second
    # decision; still reject two actual decisions and malformed non-template lines.
    template = "VERDICT: " + "|".join(VERDICTS)
    found = [ln for ln in map(_normalise, own) if _VERDICT_RE.match(ln)
             and not ("|" in ln or "<" in ln or ln.upper() == template or ln.upper().startswith(template + " "))]
    if not found:
        return invalid("no verdict line in the answer: %s" % _normalise(lines[0])[:60])
    if len(found) > 1:
        return invalid("two verdicts in one answer")
    first = found[0]
    match = _VERDICT_RE.match(first)
    rest, word = match.group("rest"), match.group("rest").strip(_PUNCT)
    for bad, why in ((":" in rest, "the verdict line wants exactly one colon: %s" % rest[:60]),
                     (not word, "no verdict word after the colon: %s" % first[:60]),
                     (" " in word, "words after the verdict word: %s" % word[:60]),
                     (word.upper() not in VERDICTS, "unknown verdict word: %s" % word)):
        if bad:
            return invalid(why)
    verdict = word.upper()
    out["verdict"] = verdict
    blind = _blind_statement(text)
    if verdict == "REJECT":
        out.update(valid=True, countable=True)
        if blind:
            out["reason"] = ("says it %s, and a REJECT from a seat that could not see is "
                             "still a finding" % blind)
    elif blind and verdict == UNCOUNTABLE_TOKEN:
        out.update(valid=True, reason="says it %s: HOLD is right, no seat counted" % blind)
    elif blind:
        out["reason"] = "says it %s, so its %s is not a verdict" % (blind, verdict)
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
             "  VERDICT: " + "|".join(VERDICTS) + " — that word alone, findings underneath.",
             "REJECT only for a finding of this severity: a secret or a path outside the"
             " sandbox in the change; a stamp mismatch, the sandbox base not what this"
             " prompt quoted; the change not visible to you; a crash where a clean refusal"
             " was due. Everything else is minor — say it and ACCEPT.",
             "For every REJECT give the repro: the exact command you ran, and what it printed.",
             "Quote this line verbatim in your report, as proof of what you reviewed: %s"
             % base_line.strip()]
    return "\n".join(lines) + "\n"
