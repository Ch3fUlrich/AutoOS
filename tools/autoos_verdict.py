"""One verdict grammar (AO-SEAT-VERDICT-GRAMMAR): what a review seat's answer MEANS.

Measured: seats wrote `**VERDICT:** ACCEPT` and the parse found 0 refs, a blind seat was
counted anyway, and the seat's text and the record's `verdict=` field disagreed about a
yes. One owner: `parse_verdict`, `verdict_word`, `seat_prompt`. Hermetic (D-852)."""
import re

VERDICTS = ("ACCEPT", "REJECT", "HOLD")
READY_TOKEN = "accept"        # the record gate's yes, lower-cased like its word list
UNCOUNTABLE_TOKEN = "HOLD"    # a stated HOLD is honest, and reviews nothing

# A seat that never read the change is no seat, whatever its first line looks like.
BLIND_PHRASES = ("could not see", "cannot see", "unable to see", "no access to the diff",
                 "REVIEW-DIFF.patch is empty", "REVIEW-DIFF.patch is missing",
                 "REVIEW-DIFF.patch is not found")

_MARKDOWN = ("**", "__", "`", "*", "_")
_PUNCT = ".,;:!?'\" "
_BULLET_RE = re.compile(r"^[#>*+-]+[ \t]*")
_VERDICT_RE = re.compile(r"(?i)^verdict:(?P<rest>.*)$")


def _normalise(line):
    """One line with its markdown decoration off: bullet or heading, bold, code ticks."""
    text = _BULLET_RE.sub("", (line or "").strip(), count=1)
    for mark in _MARKDOWN:
        text = text.replace(mark, " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def verdict_word(value):
    """The bare verdict WORD one value states, upper-cased, or None. `**ACCEPT**` is
    `ACCEPT` — markdown was never disagreement; a word outside the grammar comes back as
    itself, so the caller's list says what `READY` means."""
    return _normalise(value).upper() or None


def parse_verdict(text):
    """`{"verdict", "valid", "reason", "countable"}` for one seat's whole answer.

    The FIRST non-empty line, decoration normalised, must be `VERDICT: ACCEPT|REJECT|HOLD`
    — one colon, the bare word, trailing punctuation tolerated. A verdict further down, a
    second verdict, an unknown word (`SHIP`, `LGTM`), or the reviewer talking after it
    is invalid and says which. A seat that could not see the change is invalid EVEN IF its
    line parses, except a HOLD — the right answer blind: valid, never countable."""
    out = {"verdict": None, "valid": False, "reason": "", "countable": False}

    def invalid(reason):
        out["reason"] = reason
        return out

    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return invalid("no non-empty line to read a verdict from")
    first = _normalise(lines[0])
    match = _VERDICT_RE.match(first)
    others = [ln for ln in map(_normalise, lines[1:]) if _VERDICT_RE.match(ln)]
    if not match:
        return invalid("verdict is not on the first non-empty line" if others
                        else "first line is not a verdict line: %s" % first[:60])
    if others:
        return invalid("two verdicts in one answer")
    rest, word = match.group("rest"), match.group("rest").strip(_PUNCT)
    for bad, why in ((":" in rest, "the verdict line wants exactly one colon: %s" % rest[:60]),
                     (not word, "no verdict word after the colon: %s" % first[:60]),
                     (" " in word, "words after the verdict word: %s" % word[:60]),
                     (word.upper() not in VERDICTS, "unknown verdict word: %s" % word)):
        if bad:
            return invalid(why)
    out["verdict"] = word.upper()
    blind = next((p for p in BLIND_PHRASES if p.lower() in (text or "").lower()), None)
    if not blind:
        out.update(valid=True, countable=out["verdict"] != UNCOUNTABLE_TOKEN)
    elif out["verdict"] == UNCOUNTABLE_TOKEN:
        out.update(valid=True, reason="says it %s: HOLD is right, no seat counted" % blind)
    else:
        out["reason"] = "says it %s, so its %s is not a verdict" % (blind, out["verdict"])
    return out


def seat_prompt(angle, criteria, base_line=""):
    """The review seat's brief: its angle, the criteria verbatim, the answer grammar."""
    lines = ["You are an independent review seat. Your angle: %s." % angle,
             "Review criteria, verbatim:", criteria, "",
             "Answer with your verdict on the FIRST line of your reply, exactly:",
             "  VERDICT: ACCEPT|REJECT|HOLD — that word alone, findings underneath.",
             "REJECT only for a finding of this severity: a secret or a path outside the"
             " sandbox in the change; a stamp mismatch, the sandbox base not what this"
             " prompt quoted; the change not visible to you; a crash where a clean refusal"
             " was due. Everything else is minor — say it and ACCEPT.",
             "For every REJECT give the repro: the exact command you ran, and what it printed."]
    if base_line:
        lines.append("Quote this line verbatim in your report, as proof of what you reviewed:"
                     " %s" % base_line)
    return "\n".join(lines) + "\n"
