#!/usr/bin/env python3
"""The private-pattern and gitleaks legs of the pre-push gate (F1b).

WHY THIS EXISTS
---------------
AutoOS is a **public** repository. The things that describe a real machine — a
router's address, a person's name, a share path — live in a private one, and the
only bridge between the two is a human or an agent copying text from the private
tree into a lane here. CI's publication scan cannot be that bridge: it reads the
public tree with a pattern file that lives in the private one, so on a runner it
has nothing to compare against. The check therefore has to run on the host that
can read both, and it has to run *before the push*.

WHAT IT MATCHES
---------------
Only the lines this push ADDS (``git diff -U0 <base>..HEAD``, with the line
number taken from the hunk header's new side). A literal that already sits on a
line the base carries is not new exposure, and refusing it would train everyone
to set the gate aside.

The names file is configured by the environment alone — ``AUTOOS_PRIVATE_PATTERNS``
— and never by a path written into this repository, because the path is itself a
fact about the private tree. Its entries are literals, matched case-insensitively
as substrings; one per line, blank lines and ``#`` comments ignored.

WHAT IT PRINTS
--------------
On a hit, only ``path:line: private-pattern #<entry number>`` and then the count.
Never the matched text, never the entry, never the line it came from — a refusal
that quotes the secret is a second leak, and this output is pasted into lane
reports, logs and chat. The entry number is enough to find the line in the names
file, which the person who configured it can open and the person reading a public
log cannot.

The optional ``gitleaks`` leg is asked for the same range and its own output is
printed as it came back, because gitleaks was told to ``--redact``.

ONLY AN UNCONFIGURED LEG SKIPS
------------------------------
A leg nobody opted into prints one line saying the leg was skipped and returns
0: no ``AUTOOS_PRIVATE_PATTERNS``, a file with no real entries in it, no
``AUTOOS_GITLEAKS`` and no gitleaks on ``PATH``. A leg that *was* configured and
did not happen is a refusal (1) — a hit, the names file absent or unreadable, a
named gitleaks that is not an executable, a diff or an answer that is not valid
UTF-8 — and a range the leg could not read at all is 2. AutoOS is public, so the
operator rule is that a configured gate fails closed: no verdict is never a
green.

A leg that RAN reports itself in the gate's ``results`` — ``private-pattern-gate``
or ``gitleaks``, with ``ok`` — so a green certificate records that the check
happened, and a skipped one records nothing (existing certificates are unchanged).

STATUS CODES are the gate's own: 0 green or skipped, 1 refused, 2 the leg could
not be asked (no diff). ``tools/prepush.py`` passes them straight through.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

#: Environment configuration only — never a path in this repository.
PATTERNS_ENV = "AUTOOS_PRIVATE_PATTERNS"
GITLEAKS_ENV = "AUTOOS_GITLEAKS"
#: The command name the leg falls back to when the environment names nothing.
GITLEAKS_FALLBACK = "gitleaks"

#: The name each leg reports itself under in the gate's ``results`` manifest.
PATTERN_COMMAND = "private-pattern-gate"
GITLEAKS_COMMAND = "gitleaks"

OK = 0
REFUSED = 1
COULD_NOT_RUN = 2

#: How much of gitleaks' own output a refusal echoes.
TAIL_LINES = 20
#: ``@@ -a,b +c,d @@`` — only the new side's start and length matter here.
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
#: What the diff legs return for a patch that is not valid UTF-8: neither a diff
#: nor its absence, because "could not read it" is not "nothing was added".
NOT_UTF8 = object()
#: A UTF-8 locale for both children. ``text=True`` alone decodes with the host's,
#: so under ``LC_ALL=C`` or Windows cp1252 a non-ASCII added line arrived as
#: replacement characters, matched nothing, and the leg went green on a real hit.
UTF8_LOCALE = {"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


def load_entries(path):
    """The names file's real entries, in file order.

    Raises ``OSError``: the caller has already refused a names file that is not
    there, so a failure to read one that exists must not look like the empty case.

    ``utf-8-sig`` because a hand-edited names file often opens with a BOM, and
    ``str.strip()`` keeps U+FEFF: read as plain utf-8 the BOM glued itself to
    entry #1 and that entry matched nothing, on the leg a push is certified by.
    """
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        raw = handle.read().split("\n")
    stripped = [line.strip() for line in raw]
    return [line for line in stripped if line and not line.startswith("#")]


def _diff_text(repo, base):
    # An empty base or one starting with "-" is an option, not a revision: git
    # would write the patch to a file and report success over nothing.
    if not base or base.startswith("-"):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", "diff",
             "--no-color", "--no-ext-diff", "-U0", "%s..HEAD" % base],
            capture_output=True, text=True, encoding="utf-8", errors="strict",
            env=dict(os.environ, **UTF8_LOCALE))
    except (UnicodeDecodeError, ValueError):
        return NOT_UTF8
    if proc.returncode != 0:
        return None
    return proc.stdout


def _path_of(target):
    """The ``b/``-prefixed path a diff header names, unquoted if git quoted it."""
    if target == "/dev/null":
        return None
    if len(target) >= 2 and target.startswith('"') and target.endswith('"'):
        # Git keeps the b/ prefix inside the quotes ("b/qa\"b.txt"), so the
        # prefix comes off after them or the refusal names a path not in the
        # tree. The diff runs with core.quotepath=false, so no octal escapes
        # reach here — only the quote and backslash git needs for " and \.
        target = target[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if target.startswith("b/"):
        target = target[2:]
    return target


def added_lines(repo, base):
    """``[(path, line_no, text)]`` for the lines ``base..HEAD`` adds, or None.

    ``-U0`` asks git for nothing but the added lines; the counter advances over
    context and deletions and resets at every hunk header, so the reported number
    is the line's position in the tree being pushed, whichever diff tuning the
    host's git config applies.

    The file header is read only OUTSIDE a hunk, because an added line that starts
    with ``++ `` arrives as ``+++ b/...`` — byte-identical to the next file's
    header. Reading it as one would silently re-home every later added line of the
    real file, and a scan that misses lines is a secret gate failing open.

    The diff is split on ``"\n"`` alone, the rule ``autoos_ready_guards._lines()``
    applies (a private helper of a module this script may run without, so it is
    replicated here rather than imported): ``str.splitlines()`` also breaks on
    ``\x0b \x0c \x1c \x1d \x1e \x85 U+2028 U+2029``, which git treats as ordinary
    content, and a line cut that way loses its ``'+'`` — the tail reads as context,
    the entry on it goes unseen, and the counter drifts. A CRLF patch keeps its
    trailing ``'\r'`` in the text, which none of the header tests mind.
    """
    text = _diff_text(repo, base)
    if text is NOT_UTF8 or text is None:
        return text
    rows = []
    path = None
    line = 0
    in_hunk = False
    for row in text.split("\n"):
        if row.startswith("diff --git "):
            in_hunk = False
            path = None
            continue
        header = HUNK.match(row)
        if header:
            line = int(header.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            if row.startswith("+++ "):
                path = _path_of(row[4:].strip())
            continue
        if row.startswith("+"):
            if path is not None:
                rows.append((path, line, row[1:]))
            line += 1
        elif not row.startswith("-") and not row.startswith("\\"):
            line += 1
    return rows


def scan(entries, added):
    """``[(path, line, entry_no)]`` — every added line carrying an entry.

    Literal substrings, case-insensitive; no regex, because a names file is
    written by hand and a mistyped metacharacter there must not quietly turn the
    gate into a match-on-nothing. ``entry_no`` is the entry's position among the
    file's *real* lines (1-based), which is the only way to point a reader at the
    entry without repeating it.
    """
    needles = [(no, entry.lower()) for no, entry in enumerate(entries or (), 1)
               if entry]
    hits = []
    for path, line, text in added or ():
        low = text.lower()
        for no, needle in needles:
            if needle in low:
                hits.append((path, line, no))
    return hits


def _result(command, ok):
    return {"command": command, "ok": ok, "passed": None}


def _tail(text, lines=TAIL_LINES):
    rows = text.splitlines()
    return "\n".join(rows[-lines:]) if len(rows) > lines else text


def pattern_leg(repo, base, env):
    """``(status, results)`` — the names-file check over this push's added lines."""
    path = (env.get(PATTERNS_ENV) or "").strip()
    if not path:
        print("prepush: private-pattern gate skipped (%s not set)" % PATTERNS_ENV)
        return OK, []
    if not os.path.isfile(path):
        # Configured and absent is the check not happening; a public repo's gate
        # fails closed. The path is never echoed — it is where the tree lives.
        print("prepush: refused — %s is set but the names file is missing"
              % PATTERNS_ENV)
        return REFUSED, [_result(PATTERN_COMMAND, False)]
    try:
        entries = load_entries(path)
    except OSError as exc:
        # The class, never the message: an OSError carries the path, and the
        # path is where the private tree lives.
        print("prepush: refused — the private-pattern names file could not be "
              "read (%s)" % exc.__class__.__name__)
        return REFUSED, [_result(PATTERN_COMMAND, False)]
    if not entries:
        print("prepush: private-pattern gate skipped (names file has no entries)")
        return OK, []
    added = added_lines(repo, base)
    if added is NOT_UTF8:
        print("prepush: private-pattern gave no verdict — the diff is not "
              "valid UTF-8")
        return COULD_NOT_RUN, []
    if added is None:
        print("prepush: private-pattern gate could not read the diff — no verdict")
        return COULD_NOT_RUN, []
    hits = scan(entries, added)
    for hit_path, line, entry_no in hits:
        print("%s:%d: private-pattern #%d" % (hit_path, line, entry_no))
    if hits:
        print("prepush: refused — neither the matched text nor the entry is "
              "printed; private-pattern hits: %d" % len(hits))
        return REFUSED, [_result(PATTERN_COMMAND, False)]
    print("prepush: ok   private-pattern gate (%d added line(s), %d entr%s)"
          % (len(added), len(entries), "y" if len(entries) == 1 else "ies"))
    return OK, [_result(PATTERN_COMMAND, True)]


def resolve_gitleaks(env):
    """The gitleaks to run, or None: the named one first, then the PATH."""
    named = (env.get(GITLEAKS_ENV) or "").strip()
    if named:
        candidate = os.path.abspath(named)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        return shutil.which(named)
    return shutil.which(GITLEAKS_FALLBACK)


def gitleaks_leg(repo, base, env):
    """``(status, results)`` — gitleaks over the same range, when it is installed."""
    named = (env.get(GITLEAKS_ENV) or "").strip()
    if not base or base.startswith("-"):
        print("prepush: gitleaks was not asked — the base is unusable")
        return COULD_NOT_RUN, []
    binary = resolve_gitleaks(env)
    if not binary:
        if named:
            print("prepush: refused — %s is set but is not an executable"
                  % GITLEAKS_ENV)
            return REFUSED, [_result(GITLEAKS_COMMAND, False)]
        print("prepush: gitleaks skipped (%s is not on PATH)" % GITLEAKS_FALLBACK)
        return OK, []
    cmd = [binary, "git", "--no-banner", "--redact", "--log-opts",
           "%s..HEAD" % base, str(repo)]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=dict(env, **UTF8_LOCALE),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="strict")
    except OSError as exc:
        print("prepush: gitleaks skipped (it could not be started: %s)"
              % exc.__class__.__name__)
        return OK, []
    except (UnicodeDecodeError, ValueError):
        print("prepush: refused — gitleaks answered in bytes that are not UTF-8")
        return REFUSED, [_result(GITLEAKS_COMMAND, False)]
    if proc.returncode != 0:
        print(_tail((proc.stdout or "") + (proc.stderr or "")))
        print("prepush: refused — gitleaks exit status %d" % proc.returncode)
        return REFUSED, [_result(GITLEAKS_COMMAND, False)]
    print("prepush: ok   gitleaks")
    return OK, [_result(GITLEAKS_COMMAND, True)]


def run_private_gate(repo, base, env=None):
    """``(status, results)`` — both legs, before any test of the gate runs.

    A refused pattern leg short-circuits: there is no reason to spend a scan on a
    tree that is already refused, and the refusal prints no detail to compare
    against.
    """
    env = dict(os.environ if env is None else env)
    status, results = pattern_leg(repo, base, env)
    if status:
        return status, results
    g_status, g_results = gitleaks_leg(repo, base, env)
    return g_status, results + g_results
