#!/usr/bin/env python3
"""Unit tests for tools/qoder-seat.py (GW-QODER-401, router D-2494).

The adapter's whole job is to run one non-interactive, tool-less ``qodercli``
call and turn its JSON into a seat directory, so every case here runs it
against a FAKE qodercli — a small Python script written into a temp dir and
handed over with ``--qodercli``. The real client is never started by these
tests; one live smoke run against it is an operator action, not a unit test.

The fake mirrors the flag surface the real client advertises, quoted from
``qodercli --help`` (1.1.62) so a change in the real CLI has a place to be
noticed:

    -p, --print                          Print response and exit (non-interactive)
    -o, --output-format <format>         The format of the CLI output
    --tools <tools...>                   Restrict available built-in tools ("" to
                                         disable all, "default" for all; use -p
                                         or -- to separate from query)
    --model <model>                      Model for the current session.
    --no-session-persistence             Disable session persistence - sessions
                                         will not be saved to disk and cannot be
                                         resumed (only works with --print)

and it prints the shape ``-o json`` really writes — one object line preceded by
the noise line the real client emits when a requested model is unavailable
(``Model "qwen3.8-flash" is not available right now; using "efficient"
instead.``), which is why the adapter reads the LAST JSON object line.

Run from the repo root (no pytest needed):

    python3 tests/test_qoder_seat.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "qoder-seat.py"

FIRST_LINE = ("You have NO tools and no repository access. "
              "Everything you need is below. Answer in plain text only.")
PROMPT = FIRST_LINE + "\nReview the change described below and answer in plain text.\n"
BAD_FIRST_LINE = "You have tools and the repository. Answer however you like.\n" + PROMPT

# Verbatim argv the adapter must hand the client, in order. `--tools ""` is the
# client's own spelling for "no built-in tools at all".
EXPECTED_ARGV = ["-p", "-o", "json", "--tools", "", "--model", "qwen3.8-flash",
                 "--no-session-persistence"]

FAKE_SOURCE = r'''#!/usr/bin/env python3
"""A fake qodercli: records argv and stdin, prints a canned `-o json` answer.

The real client's help text is quoted here verbatim (qodercli --help, 1.1.62)
because --tools "" and --no-session-persistence are the two flags
tools/qoder-seat.py is written against:

  -p, --print                          Print response and exit (non-interactive)
  -o, --output-format <format>         The format of the CLI output
  --tools <tools...>                   Restrict available built-in tools ("" to
                                       disable all, "default" for all; use -p
                                       or -- to separate from query)
  --model <model>                      Model for the current session.
  --no-session-persistence             Disable session persistence - sessions
                                       will not be saved to disk and cannot be
                                       resumed (only works with --print)

A `mode` file beside this script picks the canned answer. Every invocation that
is not `--help` records its argv and stdin in `record.json` beside this script.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

NO_PERSISTENCE_HELP = """Usage: qodercli [options] [command] [query...]

Qoder CLI - Defaults to interactive mode. Use -p/--print for non-interactive
output.

Options:
  -m, --model <model>                  Model for the current session.
  --tools <tools...>                   Restrict available built-in tools ("" to disable all, "default" for all; use -p or -- to separate from query)
  -o, --output-format <format>         The format of the CLI output
  -p, --print                          Print response and exit (non-interactive)
"""

FULL_HELP = NO_PERSISTENCE_HELP + """  --no-session-persistence             Disable session persistence - sessions will not be saved to disk and cannot be resumed (only works with --print)
"""

NOISE = ('Model "qwen3.8-flash" is not available right now; using "efficient" '
         'instead. Use /model to choose another model.')
RESULT = "Reviewed the change.\nTEST-GAMING: no\nVERDICT: ACCEPT"
RESULT_NO_GAMING = "Reviewed the change.\nVERDICT: ACCEPT"


def read_mode():
    path = os.path.join(HERE, "mode")
    if os.path.exists(path):
        with open(path) as handle:
            return handle.read().strip() or "ok"
    return "ok"


def main(argv):
    if "-h" in argv or "--help" in argv:
        # The adapter probes the real flag surface before it builds its argv.
        if read_mode() == "help-fail":
            return 9
        if read_mode() == "help-no-persistence":
            sys.stdout.write(NO_PERSISTENCE_HELP)
        else:
            sys.stdout.write(FULL_HELP)
        return 0
    stdin = sys.stdin.buffer.read().decode("utf-8", "replace")
    with open(os.path.join(HERE, "record.json"), "w") as handle:
        json.dump({"argv": argv, "stdin": stdin}, handle)
    chosen = read_mode()
    if chosen == "nonzero":
        sys.stderr.write("Bearer " + "x" * 40)
        return 7
    if chosen in ("empty", "noise", "truncated"):
        sys.stdout.write({"empty": "", "noise": "not json", "truncated": '{"result":'}[chosen])
        return 0
    if chosen == "sleep":
        time.sleep(60)
        with open(os.path.join(HERE, "finished"), "w") as handle:
            handle.write("reached the end\n")
        sys.stdout.write(RESULT + "\n")
        return 0
    result = RESULT
    if chosen == "crlf":
        result = "Review\r\nTEST-GAMING: no\rVERDICT: ACCEPT"
    if chosen == "missing-verdict":
        result = "Review only"
    if chosen == "nonfinal-verdict":
        result = "VERDICT: APPROVE\nReview continues"
    if chosen == "lower-verdict":
        result = "verdict: pass"
    if chosen == "unknown-verdict":
        result = "VERDICT: MAYBE"
    kind = "result"
    is_error = False
    models = {"efficient": {"inputTokens": 3, "outputTokens": 7}}
    if chosen == "error":
        is_error = True
    elif chosen == "empty-result":
        result = ""
    elif chosen == "not-result":
        kind = "error"
    elif chosen == "two-models":
        models = {"efficient": {}, "qwen3.8-flash": {}}
    elif chosen == "no-gaming":
        result = RESULT_NO_GAMING
    payload = {
        "type": kind,
        "subtype": "success",
        "is_error": is_error,
        "num_turns": 1,
        "result": result,
        "stop_reason": "max_tokens" if chosen == "stop" else "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20,
                  "request_id": "req-fake-0001"},
        "modelUsage": models,
        "session_id": "sess-fake-0001",
        "uuid": "uuid-fake-0001",
    }
    sys.stdout.write(NOISE + "\n")
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0



if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


class QoderSeatCase(unittest.TestCase):
    """Shared fixture: a temp dir holding the fake qodercli and a prompt file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.prompt = self.dir / "prompt.txt"
        self.prompt.write_bytes(PROMPT.encode("utf-8"))
        self.client = self.write_fake("ok")

    # -- fixture ---------------------------------------------------------
    def write_fake(self, mode="ok"):
        """Write the fake qodercli (and its mode file); return its path.

        On POSIX a python shebang plus the exec bit runs the script directly.
        Windows has no shebang and cannot start a bare .py, so the path handed
        to --qodercli is a .cmd shim that runs the same script with the
        interpreter that is running this test. The platform branch carries the
        os.chmod itself (tests/test_windows_portability.py lint shape).
        """
        script = self.dir / "qodercli.py"
        script.write_bytes(FAKE_SOURCE.replace("\r\n", "\n").encode("utf-8"))
        self.set_mode(mode)
        if os.name == "nt":
            return str(script)
        else:
            os.chmod(script, 0o755)
            return str(script)

    def set_mode(self, mode):
        (self.dir / "mode").write_bytes(mode.encode("utf-8"))

    def seat(self, name="seat"):
        out = self.dir / name
        argv = [sys.executable, str(TOOL),
                "--prompt-file", str(self.prompt),
                "--out-dir", str(out),
                "--qodercli", str(self.client), "--item", "GW-QODER-401", "--head", "example-head"]
        return out, argv

    def run_tool(self, argv, timeout=120):
        return subprocess.run(argv, capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace")

    def record(self):
        """What the fake client was actually given."""
        with open(self.dir / "record.json", encoding="utf-8") as handle:
            return json.load(handle)

    def evidence(self, out):
        with open(out / "evidence.json", encoding="utf-8") as handle:
            return json.load(handle)

    def assert_no_seat(self, out):
        self.assertFalse((out / "review.txt").exists(),
                         "review.txt must not be written on a refused run")
        self.assertFalse((out / "evidence.json").exists(),
                         "evidence.json must not be written on a refused run")

    # -- 1. happy path ---------------------------------------------------
    def test_happy_path_writes_review_and_verbatim_evidence(self):
        out, argv = self.seat()
        argv += ["--item", "GW-QODER-401", "--head", "5ce05dd9"]
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0,
                         "stderr: %s\nstdout: %s" % (proc.stderr, proc.stdout))

        # review.txt is the client's `result`, verbatim, UTF-8, LF.
        expected = ("Reviewed the change.\nTEST-GAMING: no\nVERDICT: ACCEPT")
        self.assertEqual((out / "review.txt").read_bytes(),
                         expected.encode("utf-8"))

        ev = self.evidence(out)
        # Router D-2494: every client value verbatim, no remapping.
        self.assertEqual(ev["served_model"], "efficient")
        self.assertEqual(ev["finish_reason"], "end_turn")
        self.assertEqual(ev["request_id"], "req-fake-0001")
        self.assertEqual(ev["session_id"], "sess-fake-0001")
        self.assertEqual(ev["subtype"], "success")
        self.assertEqual(ev["num_turns"], 1)
        self.assertEqual(ev["requested_model"], "qwen3.8-flash")
        self.assertEqual(ev["client"], "qodercli")
        self.assertEqual(ev["item"], "GW-QODER-401")
        self.assertEqual(ev["head"], "5ce05dd9")
        self.assertEqual(ev["test_gaming"], "no")
        self.assertEqual(ev["verdict_line"], "VERDICT: ACCEPT")

        prompt_bytes = PROMPT.encode("utf-8")
        review_bytes = (out / "review.txt").read_bytes()
        self.assertEqual(ev["prompt_sha256"],
                         hashlib.sha256(prompt_bytes).hexdigest())
        self.assertEqual(ev["prompt_bytes"], len(prompt_bytes))
        self.assertEqual(ev["review_sha256"],
                         hashlib.sha256(review_bytes).hexdigest())
        self.assertEqual(ev["review_bytes"], len(review_bytes))
        self.assertTrue(str(ev["created"]).endswith("Z"), ev["created"])
        self.assertIsInstance(ev["secs"], (int, float))
        self.assertGreaterEqual(ev["secs"], 0)

        # One summary line naming out_dir, served_model, finish_reason,
        # verdict_line and test_gaming.
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertIn(str(out), lines[0])
        self.assertIn("efficient", lines[0])
        self.assertIn("end_turn", lines[0])
        self.assertIn("VERDICT: ACCEPT", lines[0])
        self.assertIn("test_gaming=no", lines[0])

    # -- 2. stdin + tool-less argv --------------------------------------
    def test_prompt_rides_stdin_and_the_argv_is_print_tool_less(self):
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self.record()
        self.assertEqual(rec["stdin"], PROMPT)
        self.assertEqual(rec["argv"], EXPECTED_ARGV)
        self.assertNotIn(PROMPT, " ".join(rec["argv"]))

    # -- 3. the L5 banner ------------------------------------------------
    def test_a_prompt_without_the_no_tools_banner_is_refused(self):
        bad_prompt = self.dir / "bad-prompt.txt"
        bad_prompt.write_bytes(BAD_FIRST_LINE.encode("utf-8"))
        out, argv = self.seat()
        argv[argv.index(str(self.prompt))] = str(bad_prompt)
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("first line", proc.stderr.lower())
        self.assert_no_seat(out)
        self.assertFalse(out.exists(), "a refused run creates no directory")
        self.assertFalse((self.dir / "record.json").exists(),
                         "the client must never be started on a refusal")

    # -- 4. unusable client output --------------------------------------
    def test_unusable_client_output_exits_3_and_writes_no_evidence(self):
        for mode in ("error", "empty-result", "not-result", "two-models"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                out, argv = self.seat("seat-" + mode)
                proc = self.run_tool(argv)
                self.assertEqual(proc.returncode, 3,
                                 "%s: rc=%d stderr=%s"
                                 % (mode, proc.returncode, proc.stderr))
                self.assert_no_seat(out)
                self.assertFalse(out.exists())

    # -- 5. timeout ------------------------------------------------------
    def test_a_timeout_kills_the_client_and_writes_nothing(self):
        self.set_mode("sleep")
        out, argv = self.seat()
        argv += ["--timeout", "2"]
        started = time.monotonic()
        proc = self.run_tool(argv, timeout=90)
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 1)
        self.assertLess(elapsed, 45,
                        "the adapter waited instead of killing the client")
        self.assertFalse((self.dir / "finished").exists(),
                         "the fake client was not killed")
        self.assert_no_seat(out)

    # -- 6. a review with no TEST-GAMING line ---------------------------
    def test_a_review_without_a_test_gaming_line_records_null(self):
        self.set_mode("no-gaming")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ev = self.evidence(out)
        self.assertIsNone(ev["test_gaming"],
                          "a missing TEST-GAMING line is null, never \"no\"")
        self.assertEqual(ev["verdict_line"], "VERDICT: ACCEPT")

    # -- 7. an out-dir that already holds a seat ------------------------
    def test_an_out_dir_that_already_holds_a_seat_is_refused(self):
        out, argv = self.seat()
        out.mkdir(parents=True)
        (out / "review.txt").write_bytes(b"an earlier seat\n")
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("review.txt", proc.stderr)
        # Refusing means touching nothing: the earlier seat stays byte-identical
        # (assert_no_seat would wrongly demand its deletion) and no new file
        # appears beside it.
        self.assertEqual((out / "review.txt").read_bytes(), b"an earlier seat\n")
        self.assertFalse((out / "evidence.json").exists(),
                         "evidence.json must not be written on a refused run")
        self.assertFalse((self.dir / "record.json").exists(),
                         "the client must never be started on a refusal")

    # -- extra: --no-session-persistence is passed only when advertised --
    def test_no_session_persistence_is_passed_only_when_advertised(self):
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--no-session-persistence", self.record()["argv"])

        self.set_mode("help-no-persistence")
        out, argv = self.seat("seat-no-flag")
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("--no-session-persistence", self.record()["argv"])
        self.assertEqual(self.evidence(out)["session_id"], "sess-fake-0001")



    def test_failure_shapes_exact_codes(self):
        for mode, code in (("nonzero", 1), ("empty", 3), ("noise", 3), ("truncated", 3), ("missing-verdict", 3), ("nonfinal-verdict", 3)):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                out, argv = self.seat(mode)
                proc = self.run_tool(argv)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assert_no_seat(out)
                self.assertNotIn("x" * 40, proc.stderr)

    def test_usage_argv_persistence_and_secret_free_evidence(self):
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ev = self.evidence(out)
        self.assertEqual(ev["usage"], {"input_tokens": 10, "output_tokens": 20, "request_id": "req-fake-0001"})
        self.assertEqual(ev["modelUsage"], {"efficient": {"inputTokens": 3, "outputTokens": 7}})
        self.assertEqual(ev["argv"][-len(EXPECTED_ARGV):], EXPECTED_ARGV)
        self.assertIs(ev["no_session_persistence"], True)
        self.assertNotIn(PROMPT, json.dumps(ev))
        self.assertNotIn("x" * 40, json.dumps(ev))

    def test_verbatim_result_stop_and_verdict_vocabulary(self):
        for mode in ("crlf", "stop", "lower-verdict", "unknown-verdict"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                out, argv = self.seat(mode)
                proc = self.run_tool(argv)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                ev = self.evidence(out)
                if mode == "crlf":
                    self.assertEqual((out / "review.txt").read_bytes(), b"Review\r\nTEST-GAMING: no\rVERDICT: ACCEPT")
                if mode == "stop":
                    self.assertEqual(ev["finish_reason"], "max_tokens")
                if mode == "unknown-verdict":
                    self.assertEqual(ev["verdict_parse"], "unknown")
                if mode == "lower-verdict":
                    self.assertEqual(ev["verdict_line"], "verdict: pass")

    def test_probe_failure_warns_and_records_null(self):
        self.set_mode("help-fail")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("warning", proc.stderr)
        self.assertIsNone(self.evidence(out)["no_session_persistence"])

    def test_missing_client_does_not_create_output(self):
        out, argv = self.seat()
        argv[argv.index(self.client)] = str(self.dir / "absent")
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(out.exists())

    def test_identity_required_and_nonempty(self):
        for flag in ("--item", "--head"):
            out, argv = self.seat()
            argv[argv.index(flag) + 1] = " "
            self.assertEqual(self.run_tool(argv).returncode, 2)
            self.assertFalse(out.exists())

    def test_partial_directory_recovery_and_atomic_write_failure(self):
        import importlib.util
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        spec = importlib.util.spec_from_file_location("qoder_seat", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out, argv = self.seat()
        original = Path.write_bytes

        def broken(path, data):
            if path.name == "evidence.json":
                raise OSError("synthetic disk failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", broken):
            with self.assertRaises(SystemExit) as raised:
                module.main(argv[2:])
        self.assertEqual(raised.exception.code, 1)
        self.assertFalse(out.exists())
        self.assertEqual(len(list(out.parent.glob(out.name + ".partial-*"))), 1)
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("recover", proc.stderr)
        self.assert_no_seat(out)

    def test_probe_stdin_timeout_and_direct_windows_execution(self):
        import importlib.util
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        spec = importlib.util.spec_from_file_location("qoder_seat", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "run_client", side_effect=subprocess.TimeoutExpired("help", 15)) as run:
            self.assertIsNone(module.probe_no_session_persistence(self.client))
            self.assertEqual(run.call_args.args[1], 15)
        if os.name == "nt":
            with self.assertRaises(SystemExit) as raised:
                module.spawn_prefix(str(self.dir / "unsafe.cmd"))
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
