#!/usr/bin/env python3
"""Tests for tools/autoos_context.py and the `context` subcommand (spec 6.1/8.3).

The fill helper is pure: it is fed JSONL strings and must take the last
assistant usage record, summing the three input-token fields. The cap table is
data, so every row is pinned here. The CLI is exercised through a subprocess on
fixture transcripts in a TemporaryDirectory, and transcript discovery is tested
with HOME pointed at a throwaway directory, so nothing reads the live machine.

The module imports from any cwd once `tools/` is on sys.path, which is the first
thing this file does. Run it directly, never through unittest discover:

    python3 tests/test_autoos_context.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_context as ctx  # noqa: E402


def assistant(model, usage, **extra):
    record = {"type": "assistant", "message": {"model": model, "usage": usage}}
    record.update(extra)
    return json.dumps(record)


class FillTests(unittest.TestCase):
    """fill_from_transcript: last usage wins, cache fields summed, bad lines skipped."""

    def test_last_usage_record_wins(self):
        lines = [
            assistant("claude-opus-4-6", {"input_tokens": 1}),
            assistant("claude-opus-4-6", {"input_tokens": 2}),
            assistant("claude-opus-4-6", {"input_tokens": 99}),
        ]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 99)

    def test_cache_read_and_creation_are_summed_with_input(self):
        lines = [assistant("m", {
            "input_tokens": 10,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
        })]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 60)

    def test_missing_usage_fields_count_as_zero(self):
        lines = [assistant("m", {"cache_read_input_tokens": 7})]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 7)
        lines = [assistant("m", {"cache_creation_input_tokens": 5})]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 5)

    def test_model_is_returned(self):
        lines = [assistant("claude-opus-4-6", {"input_tokens": 1})]
        self.assertEqual(ctx.fill_from_transcript(lines)["model"], "claude-opus-4-6")

    def test_malformed_lines_are_skipped(self):
        lines = [
            "not json at all",
            "",
            "   ",
            assistant("m", {"input_tokens": 3}),
            "{broken",
        ]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 3)

    def test_non_assistant_and_usage_less_records_are_ignored(self):
        lines = [
            json.dumps({"type": "user", "message": {"usage": {"input_tokens": 1}}}),
            json.dumps({"type": "assistant", "message": {"model": "m"}}),
            assistant("m", {"input_tokens": 4}),
        ]
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 4)

    def test_no_usage_record_is_none(self):
        lines = [
            "garbage",
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "assistant", "message": {"model": "m"}}),
        ]
        self.assertIsNone(ctx.fill_from_transcript(lines))

    def test_empty_input_is_none(self):
        self.assertIsNone(ctx.fill_from_transcript([]))


class CapTests(unittest.TestCase):
    """cap_for: substring match, first row wins, [1m] takes the family row."""

    def test_table_holds_the_section_8_3_rows(self):
        self.assertEqual(ctx.DEFAULT_CAPS, [
            ("opus", 1000000, 400000),
            ("fable", 1000000, 400000),
            ("spark", 1000000, 300000),
            ("gemini", 1000000, 200000),
            ("*", 200000, 150000),
        ])

    def test_named_rows(self):
        self.assertEqual(ctx.cap_for("claude-opus-4-6"), 400000)
        self.assertEqual(ctx.cap_for("fable-1"), 400000)
        self.assertEqual(ctx.cap_for("muse-spark-1.3"), 300000)
        self.assertEqual(ctx.cap_for("gemini-3.1-pro"), 200000)

    def test_sonnet_falls_through_to_the_200k_default(self):
        self.assertEqual(ctx.cap_for("claude-sonnet-4-5"), 150000)
        self.assertEqual(ctx.cap_for("some-unknown-model"), 150000)

    def test_match_is_case_insensitive_substring(self):
        self.assertEqual(ctx.cap_for("Claude-OPUS-4-6"), 400000)

    def test_first_matching_row_wins(self):
        self.assertEqual(ctx.cap_for("gemini-opus-hybrid"), 400000)

    def test_bracket_1m_takes_the_family_row(self):
        self.assertEqual(ctx.cap_for("fable[1m]"), 400000)
        self.assertEqual(ctx.cap_for("muse-spark-1.3[1m]"), 300000)
        self.assertEqual(ctx.cap_for("gemini-3.1-pro[1m]"), 200000)
        self.assertEqual(ctx.cap_for("claude-opus-4-6[1m]"), 400000)

    def test_the_caller_supplies_the_table(self):
        caps = [("small", 1000, 500), ("*", 2000, 1500)]
        self.assertEqual(ctx.cap_for("a-small-model", caps), 500)
        self.assertEqual(ctx.cap_for("other", caps), 1500)


class CliTests(unittest.TestCase):
    """The CLI over a fixture transcript: text line and --json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.transcript = self.dir / "session.jsonl"
        self.transcript.write_text("\n".join([
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            assistant("claude-opus-4-6", {
                "input_tokens": 50000,
                "cache_read_input_tokens": 100000,
                "cache_creation_input_tokens": 50000,
            }),
        ]) + "\n", encoding="utf-8")

    def run_cli(self, *args, env=None, cwd=None):
        return subprocess.run([sys.executable, str(AGENT), "context", *args],
                              capture_output=True, text=True,
                              env=env or dict(os.environ),
                              cwd=cwd, stdin=subprocess.DEVNULL)

    def test_plain_line(self):
        proc = self.run_cli("--transcript", str(self.transcript))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout.strip(),
            "context: 200000 / 400000 (50%%) model=claude-opus-4-6 transcript=%s"
            % self.transcript)

    def test_json_output_has_every_field(self):
        proc = self.run_cli("--transcript", str(self.transcript), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data, {
            "tokens": 200000,
            "cap": 400000,
            "pct": 50,
            "model": "claude-opus-4-6",
            "transcript": str(self.transcript),
            "source": "default",
        })

    def test_model_flag_overrides_the_cap(self):
        proc = self.run_cli("--transcript", str(self.transcript),
                            "--model", "gemini-3.1-pro", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["model"], "gemini-3.1-pro")
        self.assertEqual(data["cap"], 200000)
        self.assertEqual(data["pct"], 100)


class DiscoveryTests(unittest.TestCase):
    """Discovery: newest *.jsonl under ~/.claude/projects/<slug>, or unknown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.cwd = self.tmp / "proj.v1"  # a dot and slashes exercise the slug
        self.cwd.mkdir(parents=True)
        self.slug = str(self.cwd).replace("/", "-").replace(".", "-")
        self.projects = self.home / ".claude" / "projects" / self.slug
        self.projects.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home))

    def write(self, name, tokens, mtime):
        path = self.projects / name
        path.write_text(assistant("claude-opus-4-6", {"input_tokens": tokens}) + "\n",
                        encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_newest_transcript_by_mtime_is_used(self):
        old = self.write("old.jsonl", 1, 1000)
        new = self.write("new.jsonl", 2, 2000)
        proc = subprocess.run([sys.executable, str(AGENT), "context"],
                              capture_output=True, text=True, env=self.env,
                              cwd=str(self.cwd), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("transcript=%s" % new, proc.stdout)
        self.assertNotIn(str(old), proc.stdout)

    def test_no_transcript_is_unknown_and_exit_zero(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        proc = subprocess.run([sys.executable, str(AGENT), "context"],
                              capture_output=True, text=True, env=self.env,
                              cwd=str(empty), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "context: unknown (no transcript)")

    def test_transcript_without_usage_is_unknown(self):
        path = self.projects / "chat.jsonl"
        path.write_text(json.dumps({"type": "user", "message": {}}) + "\n",
                        encoding="utf-8")
        proc = subprocess.run([sys.executable, str(AGENT), "context"],
                              capture_output=True, text=True, env=self.env,
                              cwd=str(self.cwd), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "context: unknown (no usage)")


if __name__ == "__main__":
    unittest.main()
