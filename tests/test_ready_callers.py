#!/usr/bin/env python3
"""AO-READY-CALLERS: a tracked file that shows the gate's `ready` subcommand being run
must name both writer-guards files with it. `ready` requires them only when the DIFF
reaches R2, so a caller copying a command line out of a doc omits them silently and is
refused at a gate it had already cleared. Every tracked line spelling the invocation
must name both flags on that line or its wrapped continuation within 3 lines; prose that
only MENTIONS the command is exempted per line with `<!-- ready-no-guards -->`, and
CHANGELOG.md is exempt wholesale — history records what a lane already ran.

Run directly:  python3 tests/test_ready_callers.py
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The invocation, assembled by concatenation: a lint matching its own source dies on it.
CALL = "autoos-agent.py" + " ready"
CALL_RE = re.compile(r"autoos-agent(?:\.py)? ready")
GUARDS = ("--brief", "--report")
EXEMPT = "<!-- ready-no-guards -->"
WINDOW = 3                      # a command line may wrap this many lines deep
HISTORY = ("CHANGELOG.md",)


def scan_lines(lines):
    """[(lineno, text, missing_flags)] for call lines naming no guards within reach."""
    hits = []
    for i, line in enumerate(lines):
        reach = lines[i:i + WINDOW + 1]
        if not CALL_RE.search(line) or any(EXEMPT in w for w in reach):
            continue
        joined = "".join(reach)
        missing = [flag for flag in GUARDS if flag not in joined]
        if missing:
            hits.append((i + 1, line, missing))
    return hits


def tracked_files():
    """Tracked paths mentioning the subcommand as (name, text); None when git cannot be
    asked, and a binary or unreadable file drops out."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    files = []
    for name in (raw.decode("utf-8", "replace") for raw in out.split(b"\0") if raw):
        try:
            data = (ROOT / name).read_bytes()
        except OSError:
            continue
        if b"\0" not in data and b"ready" in data:
            files.append((name, data.decode("utf-8", "replace")))
    return files


class ReadyCallerLint(unittest.TestCase):
    def test_what_the_scan_flags_and_what_it_lets_pass(self):
        # a doc showing the gate without its guards is what a caller copies and runs
        hits = scan_lines(["run: python3 tools/" + CALL + " --branch b --sha s"])
        self.assertEqual([h[2] for h in hits], [list(GUARDS)])
        wrapped = ["python3 tools/" + CALL + " REC --sha s \\", "--inbox i.md \\",
                   '--brief "$BRIEF" --report "$REPORT"', "--one --more --thing"]
        self.assertEqual(scan_lines(wrapped), [])           # guards on the continuation
        self.assertEqual(len(scan_lines([wrapped[0], "x", "y", "z", wrapped[2]])), 1)
        self.assertEqual(scan_lines(["the " + CALL + " gate " + EXEMPT]), [])

    def test_every_tracked_ready_caller_names_both_guards(self):
        files = tracked_files()
        if files is None:
            self.skipTest("git ls-files unavailable")
        bad = ["%s:%d missing %s: %s" % (n, no, ",".join(m), t.strip()[:80])
               for n, text in files if n not in HISTORY
               for no, t, m in scan_lines(text.splitlines())]
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
