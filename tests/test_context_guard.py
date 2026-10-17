#!/usr/bin/env python3
"""Tests for tools/hooks/context_guard.py (P1.1 automatic context recycle).

The hook is a Claude Code PostToolUse command: hook JSON on stdin
(`transcript_path`, `session_id`, `cwd`), optional JSON on stdout. Fixtures are
shaped like a real transcript record: `{"type": "assistant", "message":
{"usage": {input_tokens, cache_read_input_tokens, cache_creation_input_tokens,
output_tokens}}}`, one JSON object per line. Everything runs in a
TemporaryDirectory (transcripts and the XDG state dir); nothing reads the live
machine. Run it directly, never through unittest discover:

    python3 tests/test_context_guard.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "hooks" / "context_guard.py"
SID = "11111111-2222-3333-4444-555555555555"


def usage_line(inp=0, read=0, create=0, out=10):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "usage": {
        "input_tokens": inp, "cache_read_input_tokens": read,
        "cache_creation_input_tokens": create, "output_tokens": out}}})


class GuardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.transcript = self.tmp / "t.jsonl"
        self.addCleanup(self._tmp.cleanup)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith("AUTOOS_CTX_") and k != "CLAUDE_CODE_SESSION_ID"}
        e["XDG_STATE_HOME"] = str(self.state)
        e["HOME"] = str(self.tmp)  # default state dir must not hit the real home
        if os.name == "nt":
            e["USERPROFILE"] = str(self.tmp)  # Windows Path.home() uses USERPROFILE
        e.update(extra)
        return e

    def write(self, *lines):
        self.transcript.write_text("\n".join(lines) + "\n")

    def hook(self, payload=None, **env):
        payload = payload if payload is not None else {
            "transcript_path": str(self.transcript), "session_id": SID, "cwd": "/w/x"}
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(SCRIPT)], input=raw, text=True,
                              capture_output=True, env=self.env(**env), timeout=20)

    def ctx_text(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]

    def state_file(self, name=None):
        return self.state / "autoos" / "ctx-guard" / (name or f"{SID}.json")


class Thresholds(GuardCase):
    def test_below_handoff_is_silent(self):
        self.write(usage_line(inp=1000, read=230000, create=38999))  # 269999
        p = self.hook()
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_ctx_is_sum_of_three_fields(self):
        self.write(usage_line(inp=30000, read=190000, create=50000, out=99999))  # 270000
        p = self.hook()
        self.assertEqual(p.returncode, 0)
        t = self.ctx_text(p)
        self.assertTrue(t.startswith("HANDOFF-NOW ctx=270k >= 270k"), t)

    def test_handoff_message_names_ready_command(self):
        self.write(usage_line(read=275000))
        t = self.ctx_text(self.hook())
        self.assertIn("python3 " + str(SCRIPT.resolve()) + " ready <handoff-path>", t)
        self.assertIn("RECYCLE-READY", t)
        self.assertNotIn("\n", t)

    def test_output_is_posttooluse_hook_json(self):
        self.write(usage_line(read=275000))
        out = json.loads(self.hook().stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")

    def test_hard_cap_message_and_state_file(self):
        self.write(usage_line(read=301000))
        t = self.ctx_text(self.hook())
        self.assertIn("HARD-CAP ctx=301k >= 300k", t)
        self.assertIn("no new work; handoff now", t)
        st = json.loads(self.state_file().read_text())
        self.assertEqual((st["session_id"], st["cwd"], st["ctx"], st["level"]),
                         (SID, "/w/x", 301000, "hard"))
        self.assertIsInstance(st["ts"], (int, float, str))

    def test_exactly_hard_is_hard(self):
        self.write(usage_line(read=300000))
        self.assertIn("HARD-CAP", self.ctx_text(self.hook()))

    def test_env_override(self):
        self.write(usage_line(read=60000))
        self.assertEqual(self.hook().stdout, "")
        p = self.hook(AUTOOS_CTX_HANDOFF="50000", AUTOOS_CTX_HARD="90000")
        self.assertIn("HANDOFF-NOW ctx=60k >= 50k", self.ctx_text(p))
        p = self.hook(AUTOOS_CTX_HANDOFF="10000", AUTOOS_CTX_HARD="55000")
        self.assertIn("HARD-CAP ctx=60k >= 55k", self.ctx_text(p))

    def test_garbled_env_falls_back_to_defaults(self):
        self.write(usage_line(read=275000))
        p = self.hook(AUTOOS_CTX_HANDOFF="lots", AUTOOS_CTX_HARD="")
        self.assertIn("HANDOFF-NOW ctx=275k >= 270k", self.ctx_text(p))

    def test_last_assistant_usage_wins(self):
        self.write(usage_line(read=290000), usage_line(read=1000),
                   json.dumps({"type": "user", "message": {"content": "x"}}))
        self.assertEqual(self.hook().stdout, "")


class ReEmit(GuardCase):
    def test_reemit_only_after_10k_growth(self):
        self.write(usage_line(read=271000))
        self.assertIn("HANDOFF-NOW", self.hook().stdout)
        self.assertEqual(self.hook().stdout, "")          # same ctx
        self.write(usage_line(read=280999))
        self.assertEqual(self.hook().stdout, "")          # +9999
        self.write(usage_line(read=281000))
        self.assertIn("ctx=281k", self.ctx_text(self.hook()))  # +10000
        self.write(usage_line(read=285000))
        self.assertEqual(self.hook().stdout, "")          # +4k since the last emit

    def test_guard_rearms_after_the_fill_drops(self):
        self.write(usage_line(read=300000))
        self.assertIn("HARD-CAP", self.hook().stdout)
        self.write(usage_line(read=40000))                 # auto-compact: the fill fell below both thresholds
        self.assertEqual(self.hook().stdout, "")
        self.assertFalse(self.state_file().exists(), "state cleared once the session is back under the handoff line")
        self.write(usage_line(read=272000))
        self.assertIn("HANDOFF-NOW ctx=272k", self.ctx_text(self.hook()))

    def test_fill_drop_but_still_over_the_line_emits(self):
        self.write(usage_line(read=300000))
        self.hook()
        self.write(usage_line(read=280000))                # dropped 20k, still >= handoff
        self.assertIn("HANDOFF-NOW ctx=280k", self.ctx_text(self.hook()))

    def test_garbled_state_file_counts_as_no_state(self):
        d = self.state_file().parent
        d.mkdir(parents=True)
        self.state_file().write_text(json.dumps({"level": "hard", "ctx": "lots"}))
        self.write(usage_line(read=301000))
        self.assertIn("HARD-CAP", self.hook().stdout)

    def test_escalation_to_hard_emits_even_below_10k_growth(self):
        self.write(usage_line(read=276000))
        self.assertIn("HANDOFF-NOW", self.hook().stdout)
        self.write(usage_line(read=300500))
        self.assertIn("HARD-CAP", self.hook().stdout)
        self.assertEqual(json.loads(self.state_file().read_text())["level"], "hard")

    def test_state_is_per_session(self):
        self.write(usage_line(read=275000))
        self.assertIn("HANDOFF-NOW", self.hook().stdout)
        other = {"transcript_path": str(self.transcript), "session_id": "other", "cwd": "/w/y"}
        self.assertIn("HANDOFF-NOW", self.hook(other).stdout)

    def test_default_state_dir_under_home(self):
        self.write(usage_line(read=301000))
        env = self.env()
        env.pop("XDG_STATE_HOME")
        payload = {"transcript_path": str(self.transcript), "session_id": SID, "cwd": "/w"}
        subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, timeout=20)
        self.assertTrue((self.tmp / ".local/state/autoos/ctx-guard" / f"{SID}.json").exists())


class NeverFails(GuardCase):
    def assert_silent(self, p):
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_missing_transcript(self):
        self.assert_silent(self.hook())

    def test_garbled_transcript(self):
        self.transcript.write_bytes(b"\xff\xfe not json\n{\"type\":\"assistant\"\n\x00\x00")
        self.assert_silent(self.hook())

    def test_assistant_without_usage(self):
        self.write(json.dumps({"type": "assistant", "message": {"content": "hi"}}))
        self.assert_silent(self.hook())

    def test_non_numeric_usage(self):
        self.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": "many"}}}))
        self.assert_silent(self.hook())

    def test_session_id_cannot_escape_the_state_dir(self):
        self.write(usage_line(read=301000))
        for bad in ("../evil", "a/b", "..", ".hidden", "x" * 200, "a\\b"):
            p = self.hook({"transcript_path": str(self.transcript), "session_id": bad, "cwd": "/w"})
            self.assertEqual((p.returncode, p.stdout), (0, ""), bad)
        self.assertEqual([f.name for f in self.tmp.rglob("*.json")], [], "no state written for a bad id")

    def test_garbled_stdin(self):
        self.assert_silent(self.hook("{not json"))
        self.assert_silent(self.hook(""))
        self.assert_silent(self.hook([1, 2]))

    def test_missing_fields(self):
        self.assert_silent(self.hook({}))
        self.assert_silent(self.hook({"transcript_path": str(self.transcript)}))

    def test_unwritable_state_dir_still_exits_0(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("a file, not a dir")
        self.write(usage_line(read=301000))
        p = self.hook(XDG_STATE_HOME=str(blocker))
        self.assertEqual(p.returncode, 0)


class TailRead(GuardCase):
    def big_transcript(self, mb=50):
        """A transcript of `mb` MB: a stale over-cap record first, a sparse hole (no disk, no write time), then the tail."""
        with self.transcript.open("wb") as f:
            f.write((usage_line(read=999999) + "\n").encode())  # stale, deep in the file: must NOT be found
            f.seek(mb * 1024 * 1024)
            f.write(b"\n")
            for _ in range(20):
                f.write((json.dumps({"type": "user", "message": {"content": "x" * 400}}) + "\n").encode())
            f.write((usage_line(read=275000) + "\n").encode())
            f.write((json.dumps({"type": "user", "message": {"content": "tail"}}) + "\n").encode())

    def load(self):
        spec = importlib.util.spec_from_file_location("context_guard", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_50mb_transcript_in_process_under_100ms(self):
        self.big_transcript(50)
        self.assertGreater(self.transcript.stat().st_size, 50 * 1024 * 1024)
        mod = self.load()
        t0 = time.perf_counter()
        n = mod.context_tokens(self.transcript)
        dt = time.perf_counter() - t0
        self.assertEqual(n, 275000)
        self.assertLess(dt, 0.1, f"{dt * 1000:.1f} ms")

    def test_50mb_transcript_whole_hook_is_fast(self):
        self.big_transcript(50)
        t0 = time.perf_counter()
        p = self.hook()
        dt = time.perf_counter() - t0
        self.assertIn("HANDOFF-NOW ctx=275k", self.ctx_text(p))
        self.assertLess(dt, 1.0, f"{dt * 1000:.0f} ms incl. interpreter start")

    def test_never_reads_more_than_the_tail(self):
        self.big_transcript(5)
        mod = self.load()
        read_bytes = []
        real_open = open

        class Spy:
            def __init__(self, f):
                self.f = f

            def read(self, n=-1):
                d = self.f.read(n)
                read_bytes.append(len(d))
                return d

            def __getattr__(self, a):
                return getattr(self.f, a)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return self.f.__exit__(*a)

            def __iter__(self):
                raise AssertionError("iterating the whole file")

        orig = Path.open
        Path.open = lambda self, *a, **k: Spy(orig(self, *a, **k))
        try:
            n = mod.context_tokens(self.transcript)
        finally:
            Path.open = orig
        self.assertEqual(n, 275000)
        self.assertLessEqual(sum(read_bytes), 300 * 1024)

    def test_usage_older_than_tail_is_not_found(self):
        self.big_transcript(1)
        # drop the recent assistant record: the only usage left is >256 KB from the end
        raw = self.transcript.read_bytes()
        self.transcript.write_bytes(raw.replace(b"275000", b"000000"))
        self.assertEqual(self.hook().stdout, "")


class ReadyAndStatus(GuardCase):
    def run_cmd(self, *args, **env):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env=self.env(**env), timeout=20, cwd=str(self.tmp))

    def test_ready_writes_marker(self):
        p = self.run_cmd("ready", "/h/handoff.md", CLAUDE_CODE_SESSION_ID=SID)
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads(self.state_file(f"{SID}.ready.json").read_text())
        self.assertEqual((m["session_id"], m["handoff"], m["cwd"]), (SID, "/h/handoff.md", str(self.tmp)))
        self.assertIn("ts", m)

    def test_ready_without_session_id_fails_loudly(self):
        p = self.run_cmd("ready", "/h/handoff.md")
        self.assertNotEqual(p.returncode, 0)

    def test_ready_rejects_unsafe_session_id(self):
        p = self.run_cmd("ready", "/h/handoff.md", CLAUDE_CODE_SESSION_ID="../evil")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(list(self.tmp.rglob("*.json")), [])

    def test_state_writes_leave_no_temp_files(self):
        self.write(usage_line(read=301000))
        self.hook()
        self.assertEqual([f.name for f in self.state_file().parent.iterdir()], [f"{SID}.json"])

    def test_ready_without_path_fails_loudly(self):
        self.assertNotEqual(self.run_cmd("ready", CLAUDE_CODE_SESSION_ID=SID).returncode, 0)

    def test_status_one_line_per_state_file(self):
        self.write(usage_line(read=301000))
        self.hook()
        self.run_cmd("ready", "/h/handoff.md", CLAUDE_CODE_SESSION_ID=SID)
        p = self.run_cmd("status")
        self.assertEqual(p.returncode, 0)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2, lines)
        joined = "\n".join(lines)
        self.assertIn(SID, joined)
        self.assertIn("hard", joined)
        self.assertIn("ready", joined)
        self.assertIn("/h/handoff.md", joined)

    def test_status_empty_is_silent_exit_0(self):
        p = self.run_cmd("status")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))

    def test_status_skips_garbled_files(self):
        d = self.state_file().parent
        d.mkdir(parents=True)
        (d / "bad.json").write_text("{nope")
        self.assertEqual(self.run_cmd("status").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
