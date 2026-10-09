"""Pure `ready` guards (AO-WRITER-GUARDS P4a, writer-ops.md §4-5): the brief's FILES
scope, the diff's file scope, the report's CHECK evidence, tool preflight, and the
decision helper for whether the guards are required at all. Every input arrives as an
argument (diff paths, injected `which`); HERMETIC (D-852): no command, nothing
installed, no writes — the only disk touch is the default `shutil.which` a caller may
inject away, and the `isdir` read on whatever that lookup returned."""
import os
import re
import shutil
import unicodedata

from autoos_writer_rule import R_LEVELS, required_r_level

# Windows device stems, mirrored from tools/autoos_brief.py (private there).
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                      | {"%s%d" % (p, i) for p in ("COM", "LPT") for i in range(1, 10)})
# ops.md's FILES line: the canonical prefix is matched at column 0 EXACTLY, RE is the
# rendered template's exact wording. Anything FILES-shaped but not this is a violation.
_CANON_PREFIX = "FILES (only these;"
_FILES_LOOK = re.compile(r"^[ \t]*FILES\b")
_FILES_RE = re.compile(r"^FILES \(only these; <= 3 files, <= 200 lines changed\):"
                       r"[ ]*(?P<paths>.*?)\.[ ]Do not touch other lines/files\.[ ]*$")
# Markdown's own fence rule, shared by BOTH readers (see _Fences): at most 3
# leading SPACES, then 3+ of the same ` or ~; `tail` is what follows on the line.
# CommonMark expands a leading TAB to 4 columns, so a tab-indented ``` is fence
# CONTENT, never an opener or closer — the indent is ' ' only, and any other
# leading whitespace character (tab included) before the backticks means
# "not a fence line".
_FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(?P<tail>.*)$")
_QUOTE_ONE = re.compile(r"^ {0,3}> ?")
# Column 0, ASCII digits only (no 'CHECK 03', no '١'), no leading list marker.
_CHECK_RE = re.compile(r"^CHECK[ \t]+(?P<n>[1-9][0-9]*)[ \t]*:[ \t]*(?P<rest>.*)$")
# The characters a body may NEVER carry: every Unicode category Zs/Zl/Zp/Cc/Cf
# (whitespace separators, line/paragraph breaks, controls and invisible format
# characters — U+00A0, U+1680, U+2000-U+200A, U+202F, U+205F, U+3000, U+200B,
# the BOM, ...) except the four a real body legitimately carries: ' ', '\t',
# '\n', '\r'. Each one is either a line break str.splitlines() would invent (so
# a line such as 'foo\x0c```' used to reach the fence reader as a bare ``` closer
# and the forged `CHECK 6: PASS` line under it counted), or an unspelled
# whitespace/format character a path or CHECK line would silently eat (a
# `files_line(['tools/a.py\xa0'])` once returned 'tools/a.py', a path the brief
# did not spell). Matching the WHOLE set by category — see _invisible_char —
# the text fails closed: refuse it outright (see _guard_text) and split on
# '\n' only (see _lines). ' ' stays legal because it is the template's own
# separator; '\t' stays legal inside a line because it can neither break a line
# nor indent a fence (' ' indent only, see _FENCE_RE).
_INVISIBLE_CATS = frozenset(("Zs", "Zl", "Zp", "Cc", "Cf"))
_ALLOWED_CHARS = frozenset(" \t\n\r")
# A verdict needs a non-empty tail after it: a bare 'PASS' proves nothing.
_VERDICT_RE = re.compile(r"^(PASS|FAIL|INPUT_REQUIRED)[ \t]+\S.*$")
REQUIRED_TOOLS = ("yamllint", "ansible-playbook", "ansible-lint", "gitleaks", "pre-commit")
# Size limits (P4a fix 4): a text input bigger than this is not a brief or a
# report, it is a payload; a path bigger than these is not a repo-relative path.
MAX_TEXT_CHARS = 1 << 20          # 1 MiB of characters
MAX_PATH_CHARS = 200
MAX_COMPONENT_CHARS = 100
MAX_PATH_COMPONENTS = 20
# Documented install step, quoted for the operator; nothing runs it here.
INSTALL_HELP = ("pipx install yamllint ansible-core ansible-lint pre-commit\n"
                "gitleaks: the vendor release binary on PATH "
                "(https://github.com/gitleaks/gitleaks/releases)")


class GuardError(ValueError):
    """The brief, the report shape, or a caller argument is not usable."""


def _invisible_char(text):
    """The first character of `text` no brief or report may carry: any Unicode
    category Zs/Zl/Zp/Cc/Cf character that is not ' ', '\\t', '\\n' or '\\r'.
    Decided by `unicodedata.category`, never by a hand-listed codepoint set, so
    an exotic separator (U+180E, a U+2000-U+200A en-dash-space, the BOM) is
    refused as surely as '\\x0c'."""
    for ch in text:
        if ch in _ALLOWED_CHARS:
            continue
        if unicodedata.category(ch) in _INVISIBLE_CATS:
            return ch
    return None


def _guard_text(name, text):
    """A brief/report body must be a usable str (P4a fix 2: a wrong type raises,
    it never reaches a line loop and dies with AttributeError/TypeError), must fit
    the size limit (fix 4) and must carry no invisible whitespace/control/format
    character (P4a rounds 3-4): one of them anywhere makes the body unjudgeable
    as physical lines or unspells a path with a character str.strip() would eat,
    so it is refused rather than read as something the author did not write."""
    if type(text) is not str:
        raise GuardError("%s must be a str, got %s" % (name, type(text).__name__))
    if len(text) > MAX_TEXT_CHARS:
        raise GuardError("%s is %d characters, over the %d limit"
                         % (name, len(text), MAX_TEXT_CHARS))
    bad = _invisible_char(text)
    if bad:
        raise GuardError("%s contains the invisible/control character %s (U+%04X, "
                         "category %s): a brief or report is read as physical "
                         "'\\n'-separated lines of spelled ASCII, never as "
                         "splitlines() output" % (name, repr(bad), ord(bad),
                                                  unicodedata.category(bad)))


def _lines(text):
    """The body's physical lines: split on '\\n' ONLY, never str.splitlines(),
    which also breaks on the characters _invisible_char refuses (that guard is
    the fence; this is the shape both readers agree on). Yields `(line, cr)` per line,
    with ONE trailing '\\r' removed so a CRLF body reads like an LF one and the
    flag says whether it was there — `brief_files` needs the flag because a
    CR-terminated FILES line is a violation it refuses rather than normalises
    away. A lone '\\r' anywhere else is content, never a line break."""
    for raw in text.split("\n"):
        if raw.endswith("\r"):
            yield raw[:-1], True
        else:
            yield raw, False


def _guard_seq(name, paths):
    """A sequence of path arguments, fail closed (P4a fix 2): a non-iterable has
    no entries to check, and a bare str/bytes IS iterable — walking it yields
    single characters, which would silently compare the wrong thing, so a string
    handed to a path-list argument is refused rather than split."""
    if (isinstance(paths, (str, bytes, bytearray)) or paths is None
            or not hasattr(paths, "__iter__")):
        raise GuardError("%s must be a sequence of str paths, got %s"
                         % (name, type(paths).__name__))
    return list(paths)


def norm_path(path):
    """Case-FOLDING comparator, used only to detect duplicate FILES entries in
    brief_files (there a case variant IS the same file). Not the scope fence:
    see exact_path, which keeps the case. Restates tools/autoos_brief.py's
    unexported `_check_single_path`; returns None for anything that is not a
    plain relative path, so every caller that reads None refuses it
    (fail closed)."""
    folded = exact_path(path)
    return folded.lower() if folded is not None else None


def exact_path(path):
    """The scope fence's comparable form: the path verbatim except one leading
    './' — nothing else is collapsed. Case, NFKC form and look-alikes are KEPT,
    because on Linux 'Tools/A.PY' and 'tools/a.py' are different files, so they
    compare unequal and become violations. Returns None when the input is not a
    plain relative path (fail closed). Spaces INSIDE a name are legal —
    'ops/host names.yml' is a path, 'not a path' is compared and usually
    violates; only the shape and the size limits below refuse a name. ASCII-only
    (brief paths are ASCII per tools/autoos_brief.py): a non-ASCII path can never
    equal an allowed ASCII path, so it is refused here and named a violation by
    scope_fence rather than silently Unicode-folded."""
    if (type(path) is not str or not path or not path.isascii()
            or path != path.strip(" ")
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in path)):
        return None
    if path.startswith("./"):
        path = path[2:]
    if len(path) > MAX_PATH_CHARS:
        return None
    if any(c in path for c in "*?[]:\\") or path.endswith(("/", ".")):
        return None
    parts = path.split("/")
    if len(parts) > MAX_PATH_COMPONENTS:
        return None
    if any(len(c) > MAX_COMPONENT_CHARS for c in parts):
        return None
    if any(p in ("", ".", "..") for p in parts):
        return None
    if any(c != c.strip(" ") or c.endswith(".") or c.split(".")[0].upper() in _RESERVED
           for c in parts):
        return None
    return path or None


def _unquote(line):
    """Drop '>' blockquote markers (with their optional space), outermost first."""
    while True:
        stripped = _QUOTE_ONE.sub("", line, count=1)
        if stripped == line:
            return line
        line = stripped


def _files_looks(line):
    """FILES-shaped at the start of the line, quoted or not."""
    return bool(_FILES_LOOK.match(line) or _FILES_LOOK.match(_unquote(line)))


class _Fences:
    """ONE markdown-fence tracker, shared by `brief_files` and `report_checks` so
    the two readers can never disagree about what is fenced (P4a fix 1 — the
    report side used to toggle on any fence line, and '```' then '~~~' then
    forged `CHECK n: PASS` lines read as unfenced).

    `feed(line)` returns True for a line that is fence markup or fenced content,
    False for a line that counts. A fence opens on 3+ of the same ` or ~ after at
    most 3 leading SPACES (its info string may follow; a TAB or any other
    whitespace-indented marker is content, never markup — see _FENCE_RE); it
    closes ONLY on a line of the same character, at least as long, with nothing
    after it. A shorter marker, the other character, a marker with trailing text,
    or a whitespace-other-than-space-indented marker is content, not a closer —
    so a fence that is never closed swallows the rest of the text."""

    def __init__(self):
        self.open = None

    def feed(self, line):
        marker = _FENCE_RE.match(line)
        fence = marker.group(1) if marker else None
        if self.open is not None:
            char, length = self.open
            if (fence and fence[0] == char and len(fence) >= length
                    and marker.group("tail") == ""):
                self.open = None
            return True
        if fence:
            self.open = (fence[0], len(fence))
            return True
        return False


def _files_item(item):
    """One comma-separated FILES entry, spelled exactly: the template's separator
    is ',' plus optional ' ', so ONLY ' ' is trimmed, and an item carrying any
    character outside printable ASCII (ord < 0x20 or > 0x7e — a TAB, a U+00A0, a
    U+2000-U+200A, an invisible format character) raises naming the item. The old
    blanket `item.strip()` ate every str.isspace() character, so
    'FILES ...: tools/a.py\\xa0. Do not touch...' handed back 'tools/a.py' — a
    path the brief never spelled. Brief paths are ASCII-only per
    tools/autoos_brief.py; anything else is refused, never normalised away."""
    for ch in item:
        if ord(ch) < 0x20 or ord(ch) > 0x7e:
            raise GuardError("FILES item %r carries the non-printable-ASCII "
                             "character %s (U+%04X, category %s): brief paths are "
                             "spelled in printable ASCII, and only ' ' separates "
                             "items" % (item, repr(ch), ord(ch),
                                        unicodedata.category(ch)))
    return item.strip(" ")


def brief_files(brief_text):
    """Normalised paths the brief allows, from its ONE canonical FILES line.

    The line must start at column 0 with `_CANON_PREFIX` in the canonical
    wording. A FILES-looking line that is indented, CR-terminated, quoted, or
    sitting inside a ``` / ~~~ fence is never used silently — it raises, as does
    a second canonical line anywhere. Lines are the physical '\\n'-separated ones
    (see _lines); a body carrying an invisible/control/format character raises
    before any of this (see _guard_text), so a 'foo\\x0c```' line can neither fake
    a fence closer here nor a second canonical FILES line, and a TAB-indented
    '```' line is fence content — a block opened above it stays open and swallows
    the FILES line under it. Fences follow ONE rule for both
    readers (see _Fences): a ``` block closes only on a ``` or longer marker with
    nothing after it, a ~~~ block only on ~~~, and a fence that never closes
    swallows the rest of the text. Duplicate DETECTION stays case-insensitive
    (same file), but the returned paths keep their case for the fence."""
    _guard_text("brief_text", brief_text)
    fences = _Fences()
    canonical = []
    for line, cr in _lines(brief_text):
        if fences.feed(line):
            if _files_looks(line):
                raise GuardError("FILES line inside a code fence is never used: %r"
                                 % line[:120])
            continue
        if not _files_looks(line):
            continue
        # A CR anywhere in a FILES line is a violation, never something to
        # normalise away: the ending ('files.\r') is a non-unix line break in a
        # brief, and an interior one ('a.py\r. Do') would reach the item split
        # below as a control character _files_item refuses.
        if (cr or "\r" in line or not line.startswith(_CANON_PREFIX)
                or not _FILES_RE.match(line)):
            raise GuardError("non-canonical FILES line: %r" % line[:120])
        canonical.append(line)
    if len(canonical) != 1:
        raise GuardError("brief has %d canonical FILES lines, expected exactly 1"
                         % len(canonical))
    raw = _FILES_RE.match(canonical[0]).group("paths")
    items = [_files_item(p) for p in raw.split(",")]
    out = [exact_path(p) for p in items]
    folded = [norm_path(p) for p in items]
    if None in out or len(out) > 3 or len(folded) != len(set(folded)):
        raise GuardError("FILES paths unusable (unparsable, duplicate, >3): %r" % raw[:120])
    return out


def scope_fence(diff_paths, allowed):
    """Paths of `diff_paths` that are not an EXACT member of `allowed` (only one
    leading './' is normalised away; case and Unicode form are part of the name).
    Renames, deletes and mode changes all arrive as paths and the caller passes
    both rename halves; an empty allowed fails closed; membership only, never a
    prefix test — a path under an allowed directory is a violation. `diff_paths`
    and `allowed` must each be a sequence: a str, bytes, None or a non-iterable is
    a GuardError, never a crash and never a silent character-by-character walk. A
    non-str ENTRY in `allowed` is a GuardError (the caller passed a broken
    allow-list); a non-str diff path is a violation named by its repr. ASCII-only
    both ways (exact_path refuses anything else): a non-ASCII diff path — 'tools/fré.py',
    or a 'tools/a.py' with an unspelled trailing U+00A0 — can never equal an
    allowed ASCII path, so it is always a violation, never a silent fold."""
    ok = set()
    for a in _guard_seq("allowed", allowed):
        if type(a) is not str:
            raise GuardError("allowed entry must be a str, got %r" % (a,))
        folded = exact_path(a)
        if folded is not None:
            ok.add(folded)
    violations = []
    for p in _guard_seq("diff_paths", diff_paths):
        if type(p) is not str:
            violations.append(repr(p))
            continue
        folded = exact_path(p)
        if folded is None or folded not in ok:
            violations.append(p)
    return violations


def _guard_required(required):
    """The CHECK ids a report must carry (P4a fix 3): a non-empty tuple/list of
    ints in 1..99 with no duplicates. Anything else is a broken caller argument,
    not an empty requirement set — an empty one would make every report `ok`."""
    if not isinstance(required, (tuple, list)):
        raise GuardError("required must be a non-empty tuple/list of ints, got %s"
                         % type(required).__name__)
    req = list(required)
    if not req:
        raise GuardError("required must not be empty")
    for n in req:
        if type(n) is not int or not 1 <= n <= 99:
            raise GuardError("required entry must be an int in 1..99, got %r" % (n,))
    if len(req) != len(set(req)):
        raise GuardError("required has duplicates: %r" % (req,))
    return req


def report_checks(report_text, required=(1, 2, 3, 4, 5, 6)):
    """{ok, missing, failed, input_required} over the report's CHECK lines.
    A CHECK line counts only at column 0: indented, '>'-quoted or list-marked
    lines are quoted output and never count, and neither does a zero-padded or
    non-ASCII digit ('CHECK 03', 'check 3', 'CHECK ١'). Lines are the physical
    '\\n'-separated ones (see _lines), never splitlines() output: a body that
    carries an invisible/control/format character — any Zs/Zl/Zp/Cc/Cf character
    outside ' \\t\\n\\r' ('foo\\x0c```' used to hand the fence reader a bare ```
    closer and let the forged `CHECK 6: PASS` line under it count; a U+00A0 or
    U+200B smuggles a line or a path that was never spelled) — raises before any
    of this: see _guard_text. Fenced output never
    counts, under the same markdown rule `brief_files` uses (_Fences: closed only
    by the same character, at least as long, with nothing after it, and indented
    by at most 3 SPACES — a TAB-indented '```' is content, so a block opened
    above it never closes; never closed means the rest of the text is swallowed).
    No line for n is missing; a FAIL, a verdict with no evidence tail, an unparsable
    verdict, or conflicting
    duplicates (the last line does not silently win) is failed. A body that is not
    a str, one over MAX_TEXT_CHARS, one carrying a control character, or a
    `required` argument that is not a non-empty tuple/list of ints in 1..99 all
    raise."""
    _guard_text("report_text", report_text)
    req = _guard_required(required)
    seen = {}
    fences = _Fences()
    for line, _ in _lines(report_text):
        if fences.feed(line):
            continue
        m = _CHECK_RE.match(line)
        if not m or int(m.group("n")) not in req:
            continue
        v = _VERDICT_RE.match(m.group("rest"))
        seen.setdefault(int(m.group("n")), set()).add(v.group(1) if v else "FAIL")
    missing = sorted(set(req) - set(seen))
    failed = sorted(n for n, vs in seen.items() if len(vs) > 1 or "FAIL" in vs)
    need = sorted(n for n in seen if seen[n] == {"INPUT_REQUIRED"})
    return {"ok": not (missing or failed or need), "missing": missing,
            "failed": failed, "input_required": need}


def _found(found):
    """A `which` hit only when it is a usable non-empty str: not a directory,
    no embedded NUL/newline. Anything else reads as missing."""
    return (type(found) is str and found != ""
            and "\x00" not in found and "\n" not in found
            and not os.path.isdir(found))


def tool_preflight(required_tools=REQUIRED_TOOLS, which=shutil.which):
    """`required_tools` `which` cannot find, in order; installs nothing. The
    lookup is injected to keep this hermetic; a bad tool name is refused and a
    lookup that raises counts as missing, never skipped — and so does a result
    that is not a non-empty str, or is a directory, or carries a NUL/newline."""
    if not callable(which):
        raise GuardError("which must be callable")
    missing = []
    for tool in required_tools:
        if type(tool) is not str or not tool.strip(" "):
            raise GuardError("bad tool name: %r" % (tool,))
        try:
            found = which(tool)
        except Exception:  # noqa: BLE001 - a broken lookup fails closed
            found = None
        if not _found(found) and tool not in missing:
            missing.append(tool)
    return missing


def preflight_report(missing):
    """{'state', 'missing', 'message'} for tool_preflight's output."""
    ms = list(missing)
    msg = ("missing tool(s): %s — STOP, report input_required, never skip a check.\n"
           "Documented install step:\n%s" % (", ".join(ms), INSTALL_HELP)) if ms else (
           "all required tools present (%s); nothing installed here"
           % ", ".join(REQUIRED_TOOLS))
    return {"state": "input_required" if ms else "ok", "missing": ms, "message": msg}


def requires_ops_guards(task_type, r_level=None, paths=(), diff_text=""):
    """Whether the ops ready guards must run for this card.

    Delegates the level to tools/autoos_writer_rule.required_r_level (one source
    of the R-scale rules, never restated here) and raises the same ValueError on
    a bad task_type. True for an `ops` card or any card whose required level —
    the declared `r_level` and the computed floor, whichever is higher — reaches
    R2, so a mislabelled docs/code card that touches ops or auth paths still
    gets the guards instead of slipping through on its label."""
    level = required_r_level(task_type, paths, diff_text)
    if r_level is not None:
        if r_level not in R_LEVELS:
            raise ValueError("unknown r_level %r" % (r_level,))
        if R_LEVELS.index(r_level) > R_LEVELS.index(level):
            level = r_level
    return task_type == "ops" or R_LEVELS.index(level) >= R_LEVELS.index("R2")
