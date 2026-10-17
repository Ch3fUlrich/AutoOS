#!/usr/bin/env python3
"""Unit tests for tools/probe-rtk.py (routing v2 spec D19, section 10).

Offline by construction: every test monkeypatches ``subprocess.run``, so no
test here calls the omniroute CLI, reads a real manage key, touches the
network or runs the live probe. The corpus for the main()-driven tests is a
temp ``--corpus-dir`` of ``.txt`` samples, never this checkout's git history,
so a verdict asserted here cannot change with the repository.

What each class pins:

- FailureLineTests   - the fixed regex set, and that "lost" means a stripped
  original failure line missing from the compressed text;
- ParseCliJsonTests  - the omniroute stdout carries "Loaded env" noise before
  the JSON object;
- VerdictTests       - the D19 gate: >= 10% saved AND zero lost lines, both
  branches, including exactly 10%;
- KeyHandlingTests   - the manage key reaches the child env and nowhere else;
- TempFileTests      - the request file is 0600 while it exists and gone after;
- CorpusTests        - "# command:" handling and --dry-run making no CLI call;
- LossEndToEndTests  - one dropped AssertionError line is enough to keep RTK off.

Run from the repo root:

    python3 tests/test_probe_rtk.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "probe-rtk.py"

SENTINEL = "sk-manage-4f9c2b7a1d-not-a-real-key"

ASSERT_LINE = "AssertionError: 7 != 42 returned by compute()"
CROSS_LINE = "✗ lane merges cleanly   expected the guard to refuse"


def _load_module():
    """Load tools/probe-rtk.py by absolute path (a hyphen is not importable)."""
    spec = importlib.util.spec_from_file_location("probe_rtk", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rtk_payload(compressed_text, original_tokens, compressed_tokens,
                detection="shell", compressed=True, techniques=("strip",),
                rules=("tool-output",)):
    """The response body the omniroute CLI prints for one --file request."""
    return {
        "detection": detection,
        "text": compressed_text,
        "compressed": compressed,
        "originalTokens": original_tokens,
        "compressedTokens": compressed_tokens,
        "techniquesUsed": list(techniques),
        "rulesApplied": list(rules),
    }


def cli_stdout(payload, noise=("Loaded env from ~/.omniroute/.env",
                               "omniroute: management scope ok")):
    """A CLI stdout with the leading noise lines the real binary prints."""
    return "\n".join(list(noise) + [json.dumps(payload)]) + "\n"


class FakeRun:
    """Stands in for subprocess.run: records every call, answers per argv[0].

    The omniroute calls consume `payloads` in order (the last one repeats when
    they run out). Any other argv is corpus collection (git / grep / python3)
    and answers `corpus_stdout`, empty by default so a test never measures this
    checkout by accident.
    """

    CLI = "omniroute"

    def __init__(self, payloads=(), corpus_stdout=""):
        self.payloads = list(payloads)
        self.corpus_stdout = corpus_stdout
        self.calls = []          # every argv, in order
        self.cli_calls = []      # one entry per omniroute call, see _record

    def _record(self, argv, kwargs):
        """Inspect the request file while it still exists, and snapshot the
        parent environment so a probe that leaked the key into its own
        os.environ is caught rather than restored away by patch.dict."""
        entry = {"argv": argv, "env": dict(kwargs.get("env") or {}),
                 "environ": dict(os.environ), "request": None,
                 "mode": None, "exists": None, "path": None}
        if "--file" in argv:
            path = argv[argv.index("--file") + 1]
            entry["path"] = path
            entry["exists"] = os.path.exists(path)
            if entry["exists"]:
                entry["mode"] = os.stat(path).st_mode & 0o777
                with open(path, encoding="utf-8") as fh:
                    entry["request"] = json.load(fh)
        self.cli_calls.append(entry)
        return entry

    def reply(self, argv):
        """The CLI's stdout for one omniroute call."""
        payload = (self.payloads.pop(0) if len(self.payloads) > 1
                   else (self.payloads[0] if self.payloads else None))
        if payload is None:
            return "{}\n"
        return cli_stdout(payload)

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] != self.CLI:
            return subprocess.CompletedProcess(argv, 0, self.corpus_stdout, "")
        self._record(argv, kwargs)
        return subprocess.CompletedProcess(argv, 0, self.reply(argv), "")

    def cli_call_count(self):
        return len([c for c in self.calls if c and c[0] == self.CLI])

    def child_env_keys(self):
        return [c["env"].get("OMNIROUTE_API_KEY") for c in self.cli_calls]


class NotFoundRun(FakeRun):
    """An omniroute binary that is not on PATH (the corpus commands still work)."""

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        if argv[0] == self.CLI:
            self.calls.append(argv)
            self._record(argv, kwargs)
            raise FileNotFoundError(2, "No such file or directory")
        return super().__call__(argv, **kwargs)


class ExplodingRun(FakeRun):
    """An omniroute call that dies mid-run; the request file is already written.

    SubprocessError is what the probe catches around a process call — the real
    shapes are TimeoutExpired (a hung gateway) and an exec failure.
    """

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == self.CLI:
            self._record(argv, kwargs)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
        return subprocess.CompletedProcess(argv, 0, self.corpus_stdout, "")


class NoiseOnlyRun(FakeRun):
    """A CLI that answers with the "Loaded env" noise and no JSON object."""

    def reply(self, argv):
        return "Loaded env\nno json at all\n"


class FailingGitRun(FakeRun):
    """git exits non-zero (a shallow checkout); everything else answers blank."""

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 128, "", "fatal: bad revision")
        return subprocess.CompletedProcess(argv, 0, self.corpus_stdout, "")


class _Harness(unittest.TestCase):
    """Shared scaffolding: a corpus dir of samples and a patched subprocess."""

    maxDiff = None

    def setUp(self):
        self.mod = _load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def corpus_dir(self, samples):
        """Write {name: text} as <name>.txt; return the directory path."""
        d = self.dir / "corpus"
        d.mkdir(exist_ok=True)
        for name, text in samples.items():
            (d / ("%s.txt" % name)).write_text(text, encoding="utf-8")
        return str(d)

    def config_dir(self, key=SENTINEL):
        """An AUTOOS_AI_STACK_CONFIG dir holding a mode-600 manage.key."""
        if os.name == "nt":
            raise unittest.SkipTest("chmod mode bits; POSIX only")
        cfg = self.dir / "ai-stack"
        cfg.mkdir(exist_ok=True)
        key_file = cfg / "manage.key"
        key_file.write_text(key + "\n", encoding="utf-8")
        os.chmod(key_file, 0o600)
        return str(cfg)

    def run_main(self, argv, fake=None, environ=None):
        """main(argv) with subprocess.run faked; returns (rc, stdout, stderr)."""
        environ = environ or {}
        # The config dir is created only when the test has not named one of its
        # own: as a .get() default it would run unconditionally and rewrite the
        # caller's manage.key — clobbering the blank key the exit-3 cases need.
        if "AUTOOS_AI_STACK_CONFIG" in environ:
            cfg = environ["AUTOOS_AI_STACK_CONFIG"]
        else:
            cfg = self.config_dir()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"AUTOOS_AI_STACK_CONFIG": cfg},
                             clear=False):
            os.environ.pop("OMNIROUTE_API_KEY", None)
            for name, value in environ.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            with mock.patch.object(self.mod.subprocess, "run",
                                   fake or FakeRun()):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = self.mod.main(argv)
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def row(stdout, name):
        """One sample's TSV row, split on tabs."""
        for line in stdout.splitlines():
            if line.startswith(name + "\t"):
                return line.split("\t")
        raise AssertionError("no row for %r in:\n%s" % (name, stdout))


def sample_text(marker_lines, noise=300):
    """`noise` passing lines with `marker_lines` spread through them.

    No trailing newline: a corpus file is read back with splitlines/join, so
    this is exactly the text the probe measures.
    """
    markers = list(marker_lines)
    lines = []
    for i in range(noise):
        lines.append("ok %03d - test_case_%03d ... passed" % (i, i))
        if i % 100 == 99 and markers:
            lines.append(markers.pop(0))
    lines.extend(markers)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Failure-line detection.
# ---------------------------------------------------------------------------

class FailureLineTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_every_documented_pattern_is_caught(self):
        cases = {
            "FAIL": "FAIL\ntext\n",
            "ERROR": "ERROR something\n",
            "Traceback": "Traceback (most recent call last):\n",
            "AssertionError": ASSERT_LINE + "\n",
            "pytest-E": "E   assert 3 == 4\n",
            "bash-cross": CROSS_LINE + "\n",
            "error:": "src/main.c:12: error: undeclared identifier\n",
            "warning:": "note.cc:3: warning: unused variable\n",
            "SC": "In file a.sh, line 9: SC2086 (info): Double quote\n",
            "Exception": "ValueError: Exception raised here\n",
            "panic": "panic: runtime error: index out of range\n",
            "exit=": "the command exited with exit=1\n",
            "rc=": "runner reported rc=137 after the timeout\n",
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                got = self.mod.failure_lines(text)
                self.assertEqual(len(got), 1,
                                 "expected one failure line in %r, got %r" % (text, got))

    def test_clean_output_has_no_failure_lines(self):
        clean = ("commit 1a2b3c4\nDate: Mon Jan 1 00:00:00 2026 +0000\n"
                 "ok - 42 tests passed\nnothing to see here\n")
        self.assertEqual(self.mod.failure_lines(clean), [])

    def test_a_zero_exit_or_rc_is_not_a_failure(self):
        # "exit 0" / "rc=0" is how a successful command ends; only a non-zero
        # status is a finding.
        self.assertEqual(self.mod.failure_lines("done: exit 0\n"), [])
        self.assertEqual(self.mod.failure_lines("done: rc=0\n"), [])

    def test_lines_are_returned_stripped(self):
        got = self.mod.failure_lines("      AssertionError: padded      \n")
        self.assertEqual(got, ["AssertionError: padded"])

    def test_find_lost_lines_is_substring_matching(self):
        original = self.mod.failure_lines(ASSERT_LINE + "\n" + CROSS_LINE + "\n")
        self.assertEqual(self.mod.find_lost_lines(original, ASSERT_LINE + "\nnoise\n"),
                         [CROSS_LINE])
        self.assertEqual(
            self.mod.find_lost_lines(original, ASSERT_LINE + " then " + CROSS_LINE), [])
        self.assertEqual(self.mod.find_lost_lines([], ""), [])


# ---------------------------------------------------------------------------
# Parsing the CLI's stdout.
# ---------------------------------------------------------------------------

class ParseCliJsonTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_leading_noise_lines_are_skipped(self):
        text = ("Loaded env from ~/.omniroute/.env\n"
                "omniroute: notice: management scope ok\n"
                + json.dumps({"detection": "shell", "compressedTokens": 10}) + "\n")
        parsed = self.mod.parse_cli_json(text)
        self.assertEqual(parsed["detection"], "shell")
        self.assertEqual(parsed["compressedTokens"], 10)

    def test_a_json_array_start_is_accepted_too(self):
        self.assertEqual(self.mod.parse_cli_json("noise\n[1, 2]\n"), [1, 2])

    def test_no_json_returns_none_not_an_exception(self):
        self.assertIsNone(self.mod.parse_cli_json("Loaded env\nno object here\n"))
        self.assertIsNone(self.mod.parse_cli_json(""))

    def test_truncated_json_returns_none(self):
        self.assertIsNone(self.mod.parse_cli_json('{"a": 1\n'))


# ---------------------------------------------------------------------------
# The D19 verdict.
# ---------------------------------------------------------------------------

class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_exactly_ten_percent_enables(self):
        # Integer arithmetic: 1000 -> 900 must satisfy ">= 10%" rather than
        # miss it through a float rounding of 9.999999999999998.
        self.assertTrue(self.mod.meets_savings(1000, 900))
        self.assertEqual(self.mod.decide_verdict(1000, 900, 0, 0),
                         "D19: enable RTK on tool output")

    def test_one_token_under_ten_percent_keeps_it_off(self):
        self.assertFalse(self.mod.meets_savings(1000, 901))
        verdict = self.mod.decide_verdict(1000, 901, 0, 0)
        self.assertTrue(verdict.startswith("D19: keep RTK off ("), verdict)
        self.assertIn("9.9%", verdict)

    def test_any_lost_line_keeps_it_off_even_with_big_savings(self):
        verdict = self.mod.decide_verdict(1000, 100, 1, 0)
        self.assertTrue(verdict.startswith("D19: keep RTK off ("), verdict)
        self.assertIn("lost a failure line", verdict)

    def test_unmeasured_samples_keep_it_off(self):
        verdict = self.mod.decide_verdict(1000, 100, 0, 2)
        self.assertTrue(verdict.startswith("D19: keep RTK off ("), verdict)
        self.assertIn("2 samples did not measure", verdict)

    def test_nothing_measured_keeps_it_off(self):
        verdict = self.mod.decide_verdict(0, 0, 0, 0)
        self.assertTrue(verdict.startswith("D19: keep RTK off ("), verdict)
        self.assertIn("no measurable samples", verdict)

    def test_both_reasons_are_reported_together(self):
        verdict = self.mod.decide_verdict(1000, 990, 3, 1)
        self.assertIn("lost a failure line", verdict)
        self.assertIn("did not measure", verdict)


# ---------------------------------------------------------------------------
# The manage key: child env only.
# ---------------------------------------------------------------------------

class KeyHandlingTests(_Harness):
    def sample(self):
        return self.corpus_dir({"noise": sample_text([ASSERT_LINE])})

    def test_key_reaches_only_the_child_env(self):
        text = sample_text([ASSERT_LINE])
        fake = FakeRun([rtk_payload(text, 900, 700)])
        rc, out, err = self.run_main(["--corpus-dir", self.sample()], fake)
        self.assertEqual(rc, 0, err)
        self.assertGreater(len(fake.cli_calls), 0)
        for call in fake.cli_calls:
            self.assertEqual(call["env"].get("OMNIROUTE_API_KEY"), SENTINEL,
                             "the manage key must reach the CLI through its env")
            self.assertNotIn(SENTINEL, json.dumps(call["argv"]),
                             "the key must never appear in argv")
            self.assertNotIn(SENTINEL, json.dumps(call["request"]),
                             "the key must never appear in the request file")
            self.assertNotIn(SENTINEL, json.dumps(call["environ"]),
                             "the key must never be exported into the parent env")
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn(SENTINEL, err)

    def test_child_env_is_a_copy_of_the_parents_plus_the_key(self):
        marker = "probe-rtk-parent-marker-value"
        fake = FakeRun([rtk_payload(sample_text([]), 900, 700)])
        with mock.patch.dict(os.environ, {"AUTOOS_PROBE_RTK_TEST_MARKER": marker}):
            self.run_main(["--corpus-dir", self.sample()], fake)
        self.assertEqual(fake.cli_calls[0]["env"].get("AUTOOS_PROBE_RTK_TEST_MARKER"),
                         marker)

    def test_missing_key_exits_3_and_makes_no_call_at_all(self):
        empty = self.dir / "empty-config"
        empty.mkdir()
        fake = FakeRun()
        rc, out, err = self.run_main(["--corpus-dir", self.sample()], fake,
                                     environ={"AUTOOS_AI_STACK_CONFIG": str(empty)})
        self.assertEqual(rc, 3)
        self.assertEqual(fake.calls, [])
        self.assertEqual(out, "")
        self.assertIn("operator-only: create a manage-scoped key", err)

    def test_the_env_var_is_accepted_when_there_is_no_key_file(self):
        empty = self.dir / "no-key-file"
        empty.mkdir()
        fake = FakeRun([rtk_payload(sample_text([]), 900, 700)])
        rc, out, err = self.run_main(
            ["--corpus-dir", self.sample()], fake,
            environ={"AUTOOS_AI_STACK_CONFIG": str(empty),
                     "OMNIROUTE_API_KEY": SENTINEL})
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.child_env_keys(), [SENTINEL])
        self.assertNotIn(SENTINEL, out + err)

    def test_a_missing_cli_exits_3_without_retrying_every_sample(self):
        fake = NotFoundRun([rtk_payload("x", 10, 5)])
        d = self.corpus_dir({"a": sample_text([]), "b": sample_text([])})
        rc, _out, err = self.run_main(["--corpus-dir", d], fake)
        self.assertEqual(rc, 3)
        self.assertEqual(fake.cli_call_count(), 1)
        self.assertIn("omniroute", err)

    def test_a_blank_key_file_is_reported_as_exit_3(self):
        cfg = self.config_dir(key="   \n")
        rc, _out, err = self.run_main(["--corpus-dir", self.sample()], FakeRun(),
                                      environ={"AUTOOS_AI_STACK_CONFIG": cfg})
        self.assertEqual(rc, 3)
        self.assertIn("operator-only: create a manage-scoped key", err)


# ---------------------------------------------------------------------------
# The temporary request file, and the repo corpus.
# ---------------------------------------------------------------------------

class TempFileTests(_Harness):
    def test_request_file_is_private_while_it_exists_and_removed_after(self):
        text = sample_text([ASSERT_LINE])
        fake = FakeRun([rtk_payload(text, 900, 700)])
        rc, _out, err = self.run_main(
            ["--corpus-dir", self.corpus_dir({"a": text})], fake)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(fake.cli_calls), 1)
        call = fake.cli_calls[0]
        self.assertTrue(call["exists"], "the request file must exist for the CLI")
        self.assertEqual(call["mode"], 0o600)
        self.assertEqual(call["request"]["text"], text)
        self.assertIn("command", call["request"])
        self.assertFalse(os.path.exists(call["path"]), "the request file was left behind")

    def test_request_file_is_removed_even_when_the_cli_call_raises(self):
        fake = ExplodingRun()
        text = sample_text([])
        rc, out, err = self.run_main(
            ["--corpus-dir", self.corpus_dir({"a": text})], fake)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(fake.cli_calls[0]["path"]))
        self.assertIn("did not measure", out + err)

    def test_the_repo_corpus_commands_are_the_documented_ones(self):
        fake = FakeRun(corpus_stdout="commit 1a2b3c4\n    tools/a.py | 1 +\n")
        samples = self.mod.build_repo_corpus(str(ROOT), run=fake)
        commands = [s["command"] for s in samples]
        self.assertIn("git log -n 30 --stat", commands)
        self.assertIn("git diff HEAD~5..HEAD", commands)
        self.assertIn('grep -rn "def " tools/ | head -400', commands)
        # The 400-line cap is applied by the probe, not by a shell pipe.
        grep_call = [c for c in fake.calls if c[0] == "grep"][0]
        self.assertEqual(grep_call, ["grep", "-rn", "def ", "tools/"])

    def test_a_repo_corpus_command_that_fails_contributes_no_sample(self):
        fake = FailingGitRun()
        names = [s["name"] for s in self.mod.build_repo_corpus(str(ROOT), run=fake)]
        self.assertNotIn("git-log-stat", names)
        self.assertNotIn("git-diff", names)
        self.assertIn("synthetic-unittest-fail", names)
        self.assertIn("synthetic-shellcheck-warning", names)


# ---------------------------------------------------------------------------
# Corpus: --corpus-dir and --dry-run.
# ---------------------------------------------------------------------------

class CorpusTests(_Harness):
    def test_command_is_read_from_the_first_line(self):
        d = self.dir / "c2"
        d.mkdir()
        (d / "s1.txt").write_text("# command: make test\nline one\nFAIL\n",
                                  encoding="utf-8")
        (d / "s2.txt").write_text("no command header\n", encoding="utf-8")
        by_name = {s["name"]: s for s in self.mod.load_corpus_dir(str(d))}
        self.assertEqual(by_name["s1"]["command"], "make test")
        self.assertNotIn("# command:", by_name["s1"]["text"])
        self.assertEqual(by_name["s1"]["text"], "line one\nFAIL")
        self.assertEqual(by_name["s2"]["command"], "")

    def test_dry_run_lists_samples_and_calls_nothing(self):
        text = sample_text([ASSERT_LINE])
        fake = FakeRun([rtk_payload(text, 900, 700)])
        d = self.corpus_dir({"big": text, "small": "one line\n"})
        rc, out, err = self.run_main(["--corpus-dir", d, "--dry-run"], fake)
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.calls, [])
        self.assertIn("big", out)
        self.assertIn("small", out)
        self.assertIn(str(len(text)), out)
        self.assertIn("plan", out)

    def test_dry_run_needs_no_key(self):
        empty = self.dir / "no-config"
        empty.mkdir()
        rc, _out, err = self.run_main(
            ["--corpus-dir", self.corpus_dir({"a": "x\n"}), "--dry-run"],
            FakeRun(), environ={"AUTOOS_AI_STACK_CONFIG": str(empty)})
        self.assertEqual(rc, 0, err)

    def test_an_empty_sample_is_not_collected(self):
        d = self.dir / "c3"
        d.mkdir()
        (d / "blank.txt").write_text("   \n\n", encoding="utf-8")
        self.assertEqual(self.mod.load_corpus_dir(str(d)), [])

    def test_a_missing_corpus_dir_is_a_usage_error(self):
        rc, _out, err = self.run_main(
            ["--corpus-dir", str(self.dir / "nowhere")], FakeRun())
        self.assertEqual(rc, 2)
        self.assertIn("--corpus-dir", err)

    def test_a_nonpositive_timeout_is_a_usage_error(self):
        rc, _out, err = self.run_main(
            ["--corpus-dir", self.corpus_dir({"a": "x\n"}), "--timeout", "0"],
            FakeRun())
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# Loss, end to end.
# ---------------------------------------------------------------------------

class LossEndToEndTests(_Harness):
    def two_sample_corpus(self):
        return self.corpus_dir({"a": sample_text([ASSERT_LINE]),
                                "b": sample_text([CROSS_LINE])})

    def test_a_kept_failure_line_is_not_reported_as_lost(self):
        text_a = sample_text([ASSERT_LINE])
        text_b = sample_text([CROSS_LINE])
        fake = FakeRun([rtk_payload(text_a, 1000, 800), rtk_payload(text_b, 1000, 800)])
        rc, out, err = self.run_main(["--corpus-dir", self.two_sample_corpus()], fake)
        self.assertEqual(rc, 0, err)
        row = self.row(out, "a")
        self.assertEqual(row[1], "1000")
        self.assertEqual(row[2], "800")
        self.assertEqual(row[3], "20.0")
        self.assertEqual(row[5], "0", row)
        self.assertIn("summary", out)
        self.assertIn("losing=0", out)

    def test_a_dropped_failure_line_is_counted_and_blocks_the_verdict(self):
        compressed_a = sample_text([])          # the AssertionError line is gone
        compressed_b = sample_text([CROSS_LINE])
        payloads = [rtk_payload(compressed_a, 1000, 400),
                    rtk_payload(compressed_b, 1000, 400)]
        d = self.two_sample_corpus()
        rc, out, err = self.run_main(["--corpus-dir", d], FakeRun(payloads))
        self.assertEqual(rc, 0, err)
        row = self.row(out, "a")
        self.assertEqual(row[5], "1", row)
        self.assertIn("AssertionError", row[6])
        self.assertIn("losing=1", out)

        report = self.dir / "report-blocked.md"
        rc, _out, err = self.run_main(["--corpus-dir", d, "--report", str(report)],
                                      FakeRun(payloads))
        self.assertEqual(rc, 0, err)
        body = report.read_text(encoding="utf-8")
        self.assertIn("D19: keep RTK off (", body)
        self.assertNotIn("D19: enable RTK on tool output", body)

    def test_a_lost_line_is_truncated_to_120_chars_in_the_row(self):
        long_line = "FAIL " + "x" * 400
        d = self.corpus_dir({"a": sample_text([long_line])})
        fake = FakeRun([rtk_payload(sample_text([]), 1000, 500)])
        rc, out, _err = self.run_main(["--corpus-dir", d], fake)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.row(out, "a")[6]), 120)

    def test_a_clean_corpus_with_savings_reports_the_enable_verdict(self):
        texts = {"a": sample_text([ASSERT_LINE]), "b": sample_text([])}
        d = self.corpus_dir(texts)
        payloads = [rtk_payload(t, 1000, 900) for t in texts.values()]
        report = self.dir / "report.md"
        rc, out, err = self.run_main(["--corpus-dir", d, "--report", str(report)],
                                     FakeRun(payloads))
        self.assertEqual(rc, 0, err)
        self.assertIn("losing=0", out)
        body = report.read_text(encoding="utf-8")
        self.assertIn("D19: enable RTK on tool output", body)
        self.assertIn("| a |", body)
        self.assertIn("10.0", body)

    def test_a_malformed_cli_reply_is_an_unmeasured_sample(self):
        d = self.corpus_dir({"a": sample_text([])})
        rc, out, _err = self.run_main(["--corpus-dir", d], NoiseOnlyRun())
        self.assertEqual(rc, 0)
        self.assertIn("errored=1", out)
        self.assertIn("did not measure", self.row(out, "a")[6])

    def test_a_response_without_token_counts_is_an_unmeasured_sample(self):
        d = self.corpus_dir({"a": sample_text([])})
        payload = rtk_payload("whatever", 0, 0)
        payload.pop("originalTokens")
        rc, out, _err = self.run_main(["--corpus-dir", d], FakeRun([payload]))
        self.assertEqual(rc, 0)
        self.assertIn("errored=1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
