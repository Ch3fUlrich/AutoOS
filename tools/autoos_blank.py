"""ONE invisibility predicate (AO-RECOVER-BLANK-CHARS): does this character show
nothing an agent could be told to work on? Both ready readers import THIS object —
the gate's guards (tools/autoos_ready_guards.py) and the run-recovery reader
(tools/autoos_recovery.py) — because a copy that drifts is how `CHECK 1: PASS ㅤ`
came back a passed CHECK in one and a usable brief in the other. Hermetic: stdlib
only, no I/O, no command.
"""
import unicodedata

# The Unicode categories whose members splitlines() breaks lines at, str.strip()
# silently eats, or that render nothing at all: separators and controls.
INVISIBLE_CATS = frozenset(("Zs", "Zl", "Zp", "Cc", "Cf"))
# Code points that DRAW a blank but belong to none of those categories, so neither
# `isspace()` nor a category test saw one and a heading hidden behind it read as a
# brief. A list, deliberately NOT a category: U+2801 draws dots, U+AC00 a syllable,
# a letter wearing U+FE0F a heart — all content.
BLANK_LOOKING = frozenset("".join((
    "\u3164",                     # HANGUL FILLER (Lo) — an empty box
    "\u115f", "\u1160", "\uffa0",  # the Hangul choseong/jungseong/halfwidth fillers (Lo)
    "\u2800",                     # BRAILLE PATTERN BLANK (So) — eight unlit dots
    "\u17b4", "\u17b5",           # KHMER VOWEL INHERENT AQ/AA (Mn) — vowels with no glyph
    "\u034f",                     # COMBINING GRAPHEME JOINER (Mn) — joins, draws nothing
    "\U0001d159",                 # MUSICAL SYMBOL NULL NOTEHEAD (So) — a rest
    # MONGOLIAN FREE VARIATION SELECTOR 1-4 (Mn) and VARIATION SELECTOR-1..256 (Mn):
    # each picks a form of the glyph BEFORE it and shows nothing (U+180E is a Zs).
    "".join(chr(c) for c in (0x180b, 0x180c, 0x180d, 0x180f)),
    "".join(chr(c) for c in range(0xfe00, 0xfe10)),
    "".join(chr(c) for c in range(0xe0100, 0xe01f0)),
)))


def is_invisible_char(ch):
    """Whether `ch` shows nothing a writer could be told to work on: whitespace, a
    member of `INVISIBLE_CATS` (Zs/Zl/Zp/Cc/Cf), or a blank-LOOKING code point no
    category covers. ONE predicate, and EVERY invisibility test calls it."""
    return (ch.isspace() or unicodedata.category(ch) in INVISIBLE_CATS
            or ch in BLANK_LOOKING)
