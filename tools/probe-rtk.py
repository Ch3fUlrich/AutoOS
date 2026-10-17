#!/usr/bin/env python3
"""A/B OmniRoute RTK against real tool output, and the D19 decision.

Spec: docs/plans/2026-09-25-routing-v2-spec.md decision D19 ("OmniRoute RTK on
tool output only after an A/B shows savings with no lost failure line. Off
until then") and section 10 ("RTK A/B | tokens and gate result with RTK off/on
on the same tasks | D19 decision").

RTK is the gateway's tool-output compressor. Turning it on saves tokens on
every tool result an agent reads, and it can also eat the one line of a test
run that says *why* it failed — which is worse than paying the tokens. This
probe settles that trade-off on measured output instead of a guess:

1. collect real tool output locally (git, grep, this repository's own test
   runs) plus synthetic failing outputs buried in passing noise, so the corpus
   contains failure lines whether or not the tree is currently green;
2. send each sample to the host CLI's RTK test endpoint
   (``omniroute --output json -q ctx rtk test --file <request>``) and read back
   the compressed text and both token counts;
3. check every failure line of the ORIGINAL still appears, verbatim, in the
   compressed text. One missing line fails the sample;
4. print a TSV row per sample and a summary, and — with ``--report`` — a
   markdown table ending in the D19 verdict line.

The verdict is deliberately conservative: **enable** only when the corpus total
saves at least 10% *and* no sample lost a failure line. A sample that never
measured (the CLI failed, or answered without a JSON object) blocks the verdict
too — an unmeasured sample proves nothing, and D19 asks for proof.

Management auth: the ``ctx rtk`` endpoint is management-scoped, so it needs the
gateway manage key — a broader credential than the client key the other probes
use. It is read from ``<config>/manage.key``, where ``<config>`` is
``$AUTOOS_AI_STACK_CONFIG`` or ``$XDG_CONFIG_HOME/autoos/ai-stack`` (the same
resolution ``configuration/docker/ai-stack/ai-stack.sh`` uses when it writes the
file), and passed **only** in the child's environment as ``OMNIROUTE_API_KEY``:
never in argv (a process list is world-readable), never printed, never logged,
never added to this process's own environment. Creating that key is an operator
step, so a host without one exits 3 and says so rather than guessing.

tools/probe_common.py holds the shared plumbing of the *gateway* probes
(recall, effort): the HTTP post, its retry table and the ``logs/routing/
measured.json`` overlay. This probe speaks neither of those — it shells out to
a CLI and writes a markdown report, not the overlay — so it imports nothing
from there and keeps the two conventions it does share: the key is never
printed, and an error text never reaches stdout (the CLI's own stderr is
discarded, not echoed, because a gateway error body can carry a host or an org
id; the status of the response is the finding).

Usage:
    python3 tools/probe-rtk.py --dry-run
    python3 tools/probe-rtk.py --report logs/routing/rtk-ab.md
    python3 tools/probe-rtk.py --corpus-dir tests/fixtures/rtk-corpus
    python3 tools/probe-rtk.py --bin omniroute --timeout 120

Write the report under the git-ignored ``logs/``: it quotes real tool output
from this checkout, which is exactly what must not be committed (AGENTS.md
rule 1). Only the D19 verdict line is meant to travel, and that belongs in the
DONE note.

Output, one line per sample (tab-separated):

    name  original_tokens  compressed_tokens  saved_pct  failure_lines  lost  lost_lines

``lost_lines`` carries up to three lost lines (each truncated to 120
characters, tabs and newlines removed), or ``did not measure: <reason>`` for a
sample that never reached a verdict, in which case the numeric columns are
``-``. Then one ``summary`` line with the totals.

Exit codes: 0 the A/B ran (a "keep RTK off" verdict is data, not failure);
2 bad arguments or an unwritable --report path; 3 no manage key, or the
omniroute CLI is unavailable.

Never prints or logs the gateway key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_BIN = "omniroute"

# One CLI call per sample, and a sample is a few hundred kilobytes at most; a
# hung gateway must not hold the probe open indefinitely.
DEFAULT_TIMEOUT_S = 120

# Long enough for a real `git diff` of a release commit, short enough that one
# request cannot swallow the gateway's admission slot.
MAX_SAMPLE_CHARS = 200_000

# Corpus collection is local commands; a hung or enormous one is skipped, not
# waited out. Far shorter than a compression request deserves.
CORPUS_TIMEOUT_S = 60

# Corpus commands, named exactly as an operator would type them.
GIT_LOG_COMMAND = "git log -n 30 --stat"
GIT_DIFF_COMMAND = "git diff HEAD~5..HEAD"
GREP_COMMAND = 'grep -rn "def " tools/ | head -400'
GREP_MAX_LINES = 400

# Test runs whose own output is the tool output under test. Absent on a branch
# that has not landed them yet, which is a skip, not a failure.
TEST_RUN_SAMPLES = (
    ("test-probe-recall", "tests/test_probe_recall.py"),
    ("test-probe-effort", "tests/test_probe_effort.py"),
)

# The D19 savings floor, in percent. Integer arithmetic decides the gate
# (see meets_savings), so 1000 -> 900 is a pass, not 9.999999999999998.
MIN_SAVED_PCT = 10

# How many lost lines a row names.
LOST_LINES_SHOWN = 3
LOST_LINE_CHARS = 120

# ---------------------------------------------------------------------------
# Failure lines.
#
# A fixed set, so "which line counted as a failure" never depends on the model
# or on RTK's own detection — the original text is graded by these patterns and
# the compressed text is checked against that grading. Each is one family's
# spelling of "something went wrong", taken from what this repository's tools
# actually print (see tests/test_probe_rtk.py for one case per pattern).
#
# FAIL/ERROR/Traceback/AssertionError/Exception/panic are case-sensitive: the
# lowercase words appear in ordinary prose and in help text. "error:" and
# "warning:" carry their colon, so they are safe case-insensitively and catch
# a compiler's "Error:" too.
# ---------------------------------------------------------------------------
FAILURE_PATTERNS = (
    ("FAIL", re.compile(r"\bFAIL")),
    ("ERROR", re.compile(r"\bERROR")),
    ("Traceback", re.compile(r"\bTraceback\b")),
    ("AssertionError", re.compile(r"\bAssertionError\b")),
    ("pytest-E", re.compile(r"^\s*E\s")),
    ("bash-cross", re.compile(r"✗")),
    ("error:", re.compile(r"\berror:", re.IGNORECASE)),
    ("warning:", re.compile(r"\bwarning:", re.IGNORECASE)),
    ("SC", re.compile(r"\bSC\d{4}\b")),
    ("Exception", re.compile(r"\bException\b")),
    ("panic", re.compile(r"\bpanic\b")),
    ("exit", re.compile(r"(?:\bexit|\brc)[=:\s]+[1-9]\d*\b")),
)


def failure_lines(text):
    """The stripped lines of `text` that report a failure."""
    lines = []
    for line in (text or "").splitlines():
        if any(pattern.search(line) for _name, pattern in FAILURE_PATTERNS):
            lines.append(line.strip())
    return lines


def find_lost_lines(original_lines, compressed_text):
    """The `original_lines` missing, verbatim, from `compressed_text`.

    Substring matching, not line matching: RTK re-wraps and re-indents freely,
    and a failure line that survives with its whitespace changed has survived.
    One that has been shortened, reworded or dropped entirely has not.
    """
    haystack = compressed_text or ""
    return [line for line in original_lines if line not in haystack]


# ---------------------------------------------------------------------------
# The omniroute CLI prints boot chatter ("Loaded env from ...") before the
# JSON body, so the object is located rather than assumed to start at byte 0.
# ---------------------------------------------------------------------------

def parse_cli_json(stdout):
    """The JSON document in `stdout`, or None when there is none.

    Everything from the first line that opens an object or array to the end is
    one document; that is what the CLI prints. A truncated or malformed body is
    None, never an exception — a probe that crashes on a bad reply cannot
    report the other nine samples.
    """
    lines = (stdout or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads("\n".join(lines[index:]))
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# The verdict.
# ---------------------------------------------------------------------------

def saved_percent(original_tokens, compressed_tokens):
    """Percent saved, or None when there is nothing to compare against."""
    if original_tokens <= 0:
        return None
    return (original_tokens - compressed_tokens) * 100.0 / original_tokens


def _format_percent(percent):
    return "-" if percent is None else "%.1f" % percent


def meets_savings(original_tokens, compressed_tokens, min_pct=MIN_SAVED_PCT):
    """True when the corpus total saved at least `min_pct` percent.

    Cross-multiplied in integers: a float percent of exactly 10 can land on
    9.999999999999998, and that rounding must not decide a keep-off-vs-enable
    question.
    """
    if original_tokens <= 0:
        return False
    return (original_tokens - compressed_tokens) * 100 >= original_tokens * min_pct


def decide_verdict(original_tokens, compressed_tokens, losing, errored):
    """The D19 line. "enable" only on savings AND a clean failure-line check."""
    reasons = []
    if original_tokens <= 0 or errored:
        if original_tokens <= 0:
            reasons.append("no measurable samples")
        if errored:
            reasons.append("%d samples did not measure" % errored)
    if original_tokens > 0 and not meets_savings(original_tokens, compressed_tokens):
        reasons.append("saved %s%% below the %d%% floor"
                       % (_format_percent(saved_percent(original_tokens,
                                                         compressed_tokens)),
                          MIN_SAVED_PCT))
    if losing:
        reasons.append("%d samples lost a failure line" % losing)
    if reasons:
        return "D19: keep RTK off (%s)" % "; ".join(reasons)
    return "D19: enable RTK on tool output"


# ---------------------------------------------------------------------------
# The corpus.
# ---------------------------------------------------------------------------

def make_sample(name, command, text):
    """One ``{name, command, text}`` sample, or None when there is nothing to send."""
    if not text or not text.strip():
        return None
    return {"name": name, "command": command, "text": text[:MAX_SAMPLE_CHARS]}


def load_corpus_dir(directory):
    """Every ``*.txt`` in `directory`, in filename order.

    A file's command is taken from a first line of the form
    ``# command: <argv>``, which is what this probe's own output can be turned
    back into; without it the command is empty. The comment line is metadata,
    so it is not part of the text sent to RTK.
    """
    samples = []
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".txt"):
            continue
        path = os.path.join(directory, entry)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        command = ""
        if lines and lines[0].startswith("# command:"):
            command = lines[0][len("# command:"):].strip()
            lines = lines[1:]
        sample = make_sample(os.path.splitext(entry)[0], command, "\n".join(lines))
        if sample:
            samples.append(sample)
    return samples


def _run(run, argv, cwd, timeout):
    """`subprocess.run` for a corpus command: the proc, or None if it never started."""
    try:
        return run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _stdout_of(proc):
    """A successful command's stdout; a failed one contributes no sample."""
    if proc is None or getattr(proc, "returncode", 1) != 0:
        return ""
    return proc.stdout or ""


def _noise(count, verb):
    """`count` unremarkable passing lines: the filler a failure hides in."""
    return ["ok %03d - %s_case_%03d ... passed" % (i, verb, i) for i in range(count)]


def _bury(failure_block, noise=300, verb="test"):
    """`failure_block` appended to a long run of passing lines."""
    lines = _noise(noise, verb)
    halfway = len(lines) // 2
    return "\n".join(lines[:halfway] + failure_block + lines[halfway:])


# Synthetic failing outputs. Real ones are only in the corpus while something
# happens to be broken, and an A/B measured on passing output alone proves
# nothing about D19 — so each shape this repository's tools emit when it fails
# is written here once, buried in 300 lines of noise.
SYNTHETIC_SAMPLES = (
    ("synthetic-unittest-fail", "python3 tests/run-tests.py", [
        "----------------------------------------------------------------------",
        "FAIL: test_guard_refuses_a_dirty_merge (tests.test_l1_handoff.GuardTests)",
        "----------------------------------------------------------------------",
        "Traceback (most recent call last):",
        '  File "tests/test_l1_handoff.py", line 42, in test_guard',
        "    self.assertEqual(rc, 5)",
        "AssertionError: 0 != 5",
        "",
        "----------------------------------------------------------------------",
        "Ran 42 tests in 1.204s",
        "",
        "FAILED (failures=1)",
    ]),
    ("synthetic-bash-suite-fail", "bash tests/run-tests.sh", [
        "  + catalog schema is valid",
        "✗ the linux suite is shellcheck clean",
        "      expected no findings, got 1:",
        "      lib/linux/install.sh:221: SC2086 (info): Double quote to prevent globbing",
        "      hint: cd \"$DIR\"",
        "  + every docs link resolves",
    ]),
    ("synthetic-pytest-assert", "pytest tests/test_autoos_resolver.py", [
        "=================================== FAILURES ===================================",
        "__________________________ test_bucket_boundary __________________________",
        "",
        "    def test_bucket_boundary():",
        ">       assert plan.bucket == \"mid\"",
        "E       assert 'cheap' == 'mid'",
        "E         - mid",
        "E         + cheap",
        "",
        "1 failed, 88 passed in 4.11s",
    ]),
    ("synthetic-shellcheck-warning", "shellcheck lib/linux/install.sh", [
        "In lib/linux/install.sh line 221:",
        '    cd $DIR',
        "       ^--^ SC2086 (info): Double quote to prevent globbing and word splitting.",
        "",
        "Did you mean: ",
        '    cd "$DIR"',
    ]),
    ("synthetic-compiler-error", "make -C src", [
        "gcc -O2 -c parser.c -o parser.o",
        "parser.c:88:12: error: 'node' undeclared (first use in this function)",
        "parser.c:90:5: warning: unused variable 'span'",
        "make: *** [Makefile:4: all] Error 1",
    ]),
)


def build_repo_corpus(root, run=None, warn=None, timeout=DEFAULT_TIMEOUT_S):
    """This checkout's real tool output, then the synthetic failures.

    Local and network-free by construction: git, grep and two test runs. A
    command that is unavailable or fails contributes nothing rather than an
    empty sample — ``git diff HEAD~5..HEAD`` needs five commits, and a branch
    cut from a shallow clone has fewer.
    """
    if run is None:
        run = subprocess.run
    note = warn or (lambda message: None)
    samples = []

    def add(sample):
        if sample:
            samples.append(sample)

    add(make_sample("git-log-stat", GIT_LOG_COMMAND,
                    _stdout_of(_run(run, ["git", "log", "-n", "30", "--stat"],
                                   root, timeout))))
    add(make_sample("git-diff", GIT_DIFF_COMMAND,
                    _stdout_of(_run(run, ["git", "diff", "HEAD~5..HEAD"],
                                   root, timeout))))

    grep = _run(run, ["grep", "-rn", "def ", "tools/"], root, timeout)
    if grep is not None:
        # The "| head -400" of GREP_COMMAND, applied here rather than by a
        # shell so the probe runs no shell at all.
        lines = (grep.stdout or "").splitlines()[:GREP_MAX_LINES]
        add(make_sample("grep-defs", GREP_COMMAND, "\n".join(lines)))

    for name, relative in TEST_RUN_SAMPLES:
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            note("corpus: %s is not present on this branch, skipped" % relative)
            continue
        proc = _run(run, [sys.executable or "python3", relative], root, timeout)
        if proc is None:
            note("corpus: %s could not be run, skipped" % relative)
            continue
        # A test run's finding is in its output whatever its exit code, and
        # unittest writes it to stderr: both streams, always kept.
        add(make_sample(name, "python3 %s" % relative,
                        (proc.stdout or "") + (proc.stderr or "")))

    for name, command, block in SYNTHETIC_SAMPLES:
        add(make_sample(name, command, _bury(block)))
    return samples


# ---------------------------------------------------------------------------
# The manage key. Child env only: never argv, never printed, never this
# process's own environment.
# ---------------------------------------------------------------------------

def manage_key_path(env=None):
    """``<config>/manage.key``, the path ai-stack.sh wrote it to."""
    environ = os.environ if env is None else env
    config = environ.get("AUTOOS_AI_STACK_CONFIG")
    if not config:
        xdg = environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
        config = os.path.join(xdg, "autoos", "ai-stack")
    return os.path.join(config, "manage.key")


def resolve_manage_key(env=None):
    """The manage key, or "" when this host has none.

    The file wins over the environment so an operator's saved key is the one
    used; the exported variable is the documented fallback for a host that has
    not run ai-stack.sh.
    """
    environ = os.environ if env is None else env
    try:
        with open(manage_key_path(environ), encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        key = ""
    return key or (environ.get("OMNIROUTE_API_KEY") or "").strip()


# ---------------------------------------------------------------------------
# One A/B request.
# ---------------------------------------------------------------------------

# Returned as `error` when the CLI is not on PATH: a different failure from "a
# sample did not measure", because no sample can be measured at all.
CLI_MISSING = "cli-missing"


def rtk_compress(sample, key, binary=DEFAULT_BIN, timeout=DEFAULT_TIMEOUT_S,
                 run=None):
    """``(payload_or_None, error_or_None)`` for one sample through RTK.

    The request is a temp file, not an argument: it carries the whole tool
    output, and argv is readable by every other process on the machine.
    ``tempfile.mkstemp`` creates it mode 600 for the same reason, and it is
    removed on every path out of here — the text can contain anything a build
    or a test printed.
    """
    if run is None:
        run = subprocess.run
    handle, path = tempfile.mkstemp(prefix="autoos-rtk-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump({"text": sample["text"], "command": sample["command"]}, fh)
        argv = [binary, "--output", "json", "-q", "ctx", "rtk", "test",
                "--file", path]
        child_env = dict(os.environ)
        child_env["OMNIROUTE_API_KEY"] = key
        try:
            proc = run(argv, capture_output=True, text=True, timeout=timeout,
                       env=child_env)
        except FileNotFoundError:
            return None, CLI_MISSING
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "%s running %s" % (type(exc).__name__, binary)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    payload = parse_cli_json(proc.stdout or "")
    if not isinstance(payload, dict):
        # proc.stderr is deliberately not quoted into the report: a gateway
        # error body carries hosts and org ids, and the report is committed.
        return None, "the CLI returned no JSON object for %s" % sample["name"]
    return payload, None


def measure_sample(sample, key, binary=DEFAULT_BIN, timeout=DEFAULT_TIMEOUT_S,
                   run=None):
    """One sample's A/B row: token counts, failure lines, and what was lost."""
    original_lines = failure_lines(sample["text"])
    row = {"name": sample["name"], "command": sample["command"],
           "original_tokens": None, "compressed_tokens": None, "saved_pct": None,
           "failure_lines": len(original_lines), "lost": [], "detection": "-",
           "techniques": "-", "rules": "-", "untouched": False, "error": None}
    payload, error = rtk_compress(sample, key, binary=binary, timeout=timeout,
                                  run=run)
    if error:
        row["error"] = error
        return row
    original = payload.get("originalTokens")
    compressed = payload.get("compressedTokens")
    if not isinstance(original, int) or not isinstance(compressed, int):
        # A guessed count would be read back as a measurement.
        row["error"] = "the response carried no token counts"
        return row
    text = payload.get("text")
    if not isinstance(text, str):
        row["error"] = "the response carried no compressed text"
        return row
    row["original_tokens"] = original
    row["compressed_tokens"] = compressed
    row["saved_pct"] = saved_percent(original, compressed)
    row["lost"] = find_lost_lines(original_lines, text)
    row["detection"] = str(payload.get("detection") or "-")
    row["untouched"] = not payload.get("compressed")
    row["techniques"] = _joined(payload.get("techniquesUsed"))
    row["rules"] = _joined(payload.get("rulesApplied"))
    return row


def _joined(value):
    """A list field of the CLI response, comma-joined; "-" when absent."""
    if isinstance(value, list) and value:
        return ",".join(str(item) for item in value)
    return "-"


# ---------------------------------------------------------------------------
# Output.
# ---------------------------------------------------------------------------

def _cell(value):
    """A single TSV field: no tabs, no newlines, no empty strings."""
    text = "-" if value is None else str(value)
    return " ".join(text.split()) or "-"


def _clip(text, limit=LOST_LINE_CHARS):
    text = _cell(text)
    return text if len(text) <= limit else text[:limit]


def format_row(row):
    """The per-sample TSV line (see the module docstring for the columns)."""
    if row["error"]:
        detail = _clip("did not measure: %s" % row["error"])
        return "\t".join([_cell(row["name"]), "-", "-", "-",
                          _cell(row["failure_lines"]), "-", detail])
    lost = " | ".join(_clip(line) for line in row["lost"][:LOST_LINES_SHOWN])
    return "\t".join([_cell(row["name"]), _cell(row["original_tokens"]),
                      _cell(row["compressed_tokens"]),
                      _format_percent(row["saved_pct"]),
                      _cell(row["failure_lines"]), _cell(len(row["lost"])),
                      _cell(lost)])


def format_summary(rows, original_tokens, compressed_tokens, losing, errored):
    percent = saved_percent(original_tokens, compressed_tokens)
    return "\t".join(["summary", "samples=%d" % len(rows),
                      "original_tokens=%s" % _cell(original_tokens or None),
                      "compressed_tokens=%s" % _cell(compressed_tokens or None),
                      "saved_pct=%s" % _format_percent(percent),
                      "losing=%d" % losing, "errored=%d" % errored])


def _md_cell(value):
    """A markdown table cell: pipes escaped, one line, backslashes left alone."""
    return _cell(value).replace("|", "\\|")


def _rtk_cell(row):
    """What RTK said it did to this sample: detection, then its techniques."""
    return "%s/%s" % (row["detection"], row["techniques"])


def render_report(rows, original_tokens, compressed_tokens, losing, errored,
                  corpus_label):
    """The markdown report, ending in the D19 verdict line."""
    percent = saved_percent(original_tokens, compressed_tokens)
    untouched = sum(1 for row in rows if row["untouched"])
    out = [
        "# RTK A/B on tool output",
        "",
        "Probe: `tools/probe-rtk.py` (stdlib only) against the host `omniroute`",
        "CLI. Decision D19 of `docs/plans/2026-09-25-routing-v2-spec.md`.",
        "",
        "- corpus: %s" % _md_cell(corpus_label),
        "- samples: %d (%d did not measure)" % (len(rows), errored),
        "- tokens: %s -> %s (%s%% saved, floor %d%%)"
        % (_cell(original_tokens or None), _cell(compressed_tokens or None),
           _format_percent(percent), MIN_SAVED_PCT),
        "- samples that lost a failure line: %d" % losing,
        "- samples RTK left uncompressed: %d" % untouched,
        "",
        "| sample | command | rtk (detection/techniques) | original | compressed | saved % | failure lines | lost |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        out.append("| %s | `%s` | %s | %s | %s | %s | %s | %s |" % (
            _md_cell(row["name"]), _md_cell(row["command"]),
            _md_cell(_rtk_cell(row)), _md_cell(row["original_tokens"]),
            _md_cell(row["compressed_tokens"]), _format_percent(row["saved_pct"]),
            _md_cell(row["failure_lines"]),
            "n/a" if row["error"] else _md_cell(len(row["lost"]))))
    out += ["", "## Lost lines", ""]
    lost_rows = [row for row in rows if row["lost"]]
    if lost_rows:
        out.append("The `rules` column is what RTK reported applying to the sample "
                   "that lost the line - that is the rule to change.")
        out.append("")
        out.append("| sample | rules | lost line |")
        out.append("|---|---|---|")
        for row in lost_rows:
            for line in row["lost"][:LOST_LINES_SHOWN]:
                out.append("| %s | %s | `%s` |"
                           % (_md_cell(row["name"]), _md_cell(row["rules"]),
                              _md_cell(_clip(line))))
    else:
        out.append("None.")
    out += ["", "## Verdict", "", "```",
            decide_verdict(original_tokens, compressed_tokens, losing, errored),
            "```", ""]
    return "\n".join(out)


def _print_plan(samples):
    """--dry-run: the corpus and its sizes, no CLI call."""
    total = 0
    for sample in samples:
        total += len(sample["text"])
        print("%s\tchars=%d\tlines=%d\t%s"
              % (_cell(sample["name"]), len(sample["text"]),
                 sample["text"].count("\n") + 1, _cell(sample["command"])))
    print("plan\tsamples=%d\tchars=%d" % (len(samples), total))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def collect_samples(args, warn):
    """The corpus: --corpus-dir when given, this checkout's output otherwise."""
    if args.corpus_dir:
        return load_corpus_dir(args.corpus_dir)
    return build_repo_corpus(ROOT, warn=warn,
                             timeout=min(args.timeout, CORPUS_TIMEOUT_S))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="A/B OmniRoute RTK on real tool output; print the D19 decision.")
    ap.add_argument("--corpus-dir",
                    help="read *.txt samples from here instead of collecting this "
                         "checkout's tool output (a '# command: ...' first line "
                         "names the command)")
    ap.add_argument("--report", metavar="PATH",
                    help="write the markdown report and the D19 verdict here")
    ap.add_argument("--bin", default=DEFAULT_BIN,
                    help="the gateway CLI to call (default %s)" % DEFAULT_BIN)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help="seconds per request (default %d)" % DEFAULT_TIMEOUT_S)
    ap.add_argument("--dry-run", action="store_true",
                    help="list the corpus samples and their sizes; make no CLI call")
    args = ap.parse_args(argv)

    def warn(message):
        print("probe-rtk: %s" % message, file=sys.stderr)

    if args.timeout < 1:
        warn("--timeout must be at least 1 second")
        return 2
    if args.corpus_dir and not os.path.isdir(args.corpus_dir):
        warn("--corpus-dir is not a directory: %s" % args.corpus_dir)
        return 2
    if args.report and not os.path.isdir(os.path.dirname(os.path.abspath(args.report))):
        warn("--report directory does not exist: %s" % args.report)
        return 2

    if args.dry_run:
        _print_plan(collect_samples(args, warn))
        return 0

    key = resolve_manage_key()
    if not key:
        warn("operator-only: create a manage-scoped key for the gateway dashboard "
             "(API keys), save it to %s (mode 600) or export OMNIROUTE_API_KEY, "
             "then re-run; no request made" % manage_key_path())
        return 3

    samples = collect_samples(args, warn)
    if not samples:
        warn("the corpus is empty; nothing to measure")

    rows = []
    for sample in samples:
        row = measure_sample(sample, key, binary=args.bin, timeout=args.timeout)
        if row["error"] == CLI_MISSING:
            warn("the %s CLI is unavailable (not on PATH, or not executable); "
                 "no A/B could be measured" % args.bin)
            return 3
        rows.append(row)
        print(format_row(row))

    total_original = sum(row["original_tokens"] or 0 for row in rows)
    total_compressed = sum(row["compressed_tokens"] or 0 for row in rows)
    losing = sum(1 for row in rows if row["lost"])
    errored = sum(1 for row in rows if row["error"])
    print(format_summary(rows, total_original, total_compressed, losing, errored))

    if args.report:
        label = args.corpus_dir or "this checkout (git, grep, test runs) + synthetic"
        report = render_report(rows, total_original, total_compressed, losing,
                               errored, _cell(label))
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(report)
        except OSError as exc:
            # type(exc).__name__ only: str(exc) would echo a full path.
            warn("--report %s could not be written (%s)"
                 % (args.report, type(exc).__name__))
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
