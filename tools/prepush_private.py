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

SKIPPED IS NEVER A FAILURE
--------------------------
No names file configured, a configured path that is not there, a file with no
real entries in it, no gitleaks installed: each prints one line saying the leg
was skipped and returns 0. This gate is an extra pair of eyes a host may or may
not have opted into; making it mandatory would refuse every push on a machine
that never asked for it, and a gate that cries wolf gets overridden. What *is*
a refusal: a names file that is configured, present, and cannot be read (that is
the one state where the check was asked for and did not happen), and a hit.

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


def load_entries(path):
    """The names file's real entries, in file order.

    Raises ``OSError``: the caller distinguishes "not there" (skipped) from
    "there and unreadable" (refused) before it ever gets here, so a failure to
    read a file that exists is the second case and must not look like the first.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        raw = handle.read().splitlines()
    stripped = [line.strip() for line in raw]
    return [line for line in stripped if line and not line.startswith("#")]


def _diff_text(repo, base):
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", "diff",
         "--no-color", "--no-ext-diff", "-U0", "%s..HEAD" % base],
        capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        return None
    return proc.stdout


def _path_of(target):
    """The ``b/``-prefixed path a diff header names, unquoted if git quoted it."""
    if target == "/dev/null":
        return None
    if target.startswith("b/"):
        target = target[2:]
    if len(target) >= 2 and target.startswith('"') and target.endswith('"'):
        return target[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return target


def added_lines(repo, base):
    """``[(path, line_no, text)]`` for the lines ``base..HEAD`` adds, or None.

    ``-U0`` asks git for nothing but the added lines; the counter still advances
    over context and deletions so the reported number is the line's position in
    the tree being pushed, whichever diff tuning the host's git config applies.
    """
    text = _diff_text(repo, base)
    if text is None:
        return None
    rows = []
    path = None
    line = 0
    for row in text.splitlines():
        if row.startswith("+++ "):
            path = _path_of(row[4:].strip())
            continue
        header = HUNK.match(row)
        if header:
            line = int(header.group(1))
            continue
        if path is None:
            continue
        if row.startswith("+"):
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
        print("prepush: private-pattern gate skipped (%s file missing)" % PATTERNS_ENV)
        return OK, []
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
    binary = resolve_gitleaks(env)
    if not binary:
        print("prepush: gitleaks skipped (%s)" % (
            "%s is not an executable" % GITLEAKS_ENV if named
            else "%s is not on PATH" % GITLEAKS_FALLBACK))
        return OK, []
    cmd = [binary, "git", "--no-banner", "--redact", "--log-opts",
           "%s..HEAD" % base, str(repo)]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=dict(env),
                              capture_output=True, text=True, errors="replace")
    except OSError as exc:
        print("prepush: gitleaks skipped (it could not be started: %s)"
              % exc.__class__.__name__)
        return OK, []
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
