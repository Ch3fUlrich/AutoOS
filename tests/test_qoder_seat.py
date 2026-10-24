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
import contextlib
import hashlib
import io
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
    if chosen == "stderr-secrets":
        sys.stderr.write("login to https://alice:hunter2@example.invalid failed; "
                         "api_key=sk-or-v1-0123456789abcdef; Authorization: Bearer tok-abc123def456\n")
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
    if chosen == "gaming-echo":
        result = ("Reviewed the change.\nTEST-GAMING: no\n"
                  "echoed brief example above; own answer below\n"
                  "TEST-GAMING: yes\nVERDICT: ACCEPT")
    if chosen == "surrogate":
        result = "Reviewed the change. \ud800\nVERDICT: ACCEPT"
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
    # The canned stop_reason, so a truncation refusal can be driven end to end:
    # "stop"/"length" are truncated replies, "stopped" is the allowed "stop".
    stop_reason = {"stop": "max_tokens", "length": "length",
                   "stopped": "stop"}.get(chosen, "end_turn")
    usage = {"input_tokens": 10, "output_tokens": 20,
             "request_id": "req-fake-0001"}
    if chosen == "no-request-id":
        # A client that carries no request id anywhere: the field must stay null.
        usage = {"input_tokens": 10, "output_tokens": 20}
    payload = {
        "type": kind,
        "subtype": "success",
        "is_error": is_error,
        "num_turns": 1,
        "result": result,
        "stop_reason": stop_reason,
        "usage": usage,
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
        # The FULL argv, not a tail of it: spawn prefix and every flag, verbatim.
        self.assertEqual(ev["argv"], [sys.executable, str(self.client)] + EXPECTED_ARGV)
        self.assertIs(ev["no_session_persistence"], True)
        self.assertNotIn(PROMPT, json.dumps(ev))
        self.assertNotIn("x" * 40, json.dumps(ev))

    def test_evidence_keys_and_missing_client_fields_are_pinned(self):
        """The evidence contract: the exact key set, fallbacks, null-not-defaulted."""
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ev = self.evidence(out)
        self.assertEqual(sorted(ev), sorted([
            "argv", "client", "created", "finish_reason", "head", "item",
            "modelUsage", "no_session_persistence", "num_turns", "prompt_bytes",
            "prompt_sha256", "request_id", "requested_model", "review_bytes",
            "review_sha256", "secs", "served_model", "session_id", "subtype",
            "test_gaming", "usage", "verdict_line", "verdict_parse",
        ]))
        # Verbatim client fields: finish_reason is the client's own stop_reason.
        self.assertEqual(ev["finish_reason"], "end_turn")
        # The fake's top level carries no request_id, so this proves the fallback
        # to the usage envelope rather than a top-level read.
        self.assertEqual(ev["request_id"], "req-fake-0001")
        # A field absent from the client's reply stays null, never defaulted.
        self.set_mode("no-request-id")
        out2, argv2 = self.seat("seat-no-rid")
        proc2 = self.run_tool(argv2)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertIsNone(self.evidence(out2)["request_id"],
                          "a missing client field must stay null, not be invented")

    def test_verbatim_result_crlf_and_known_verdict_vocabulary(self):
        for mode in ("crlf", "lower-verdict"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                out, argv = self.seat(mode)
                proc = self.run_tool(argv)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                ev = self.evidence(out)
                if mode == "crlf":
                    self.assertEqual((out / "review.txt").read_bytes(), b"Review\r\nTEST-GAMING: no\rVERDICT: ACCEPT")
                if mode == "lower-verdict":
                    self.assertEqual(ev["verdict_line"], "verdict: pass")

    def test_a_truncated_stop_reason_refuses_with_the_verbatim_reason(self):
        """A truncated reply never publishes: non-zero, reason in the note (r3)."""
        for mode, reason, code in (("stop", "max_tokens", 3), ("length", "length", 3),
                                   ("stopped", "stop", 0)):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                out, argv = self.seat("seat-" + mode)
                proc = self.run_tool(argv)
                self.assertEqual(proc.returncode, code,
                                 "%s: rc=%d stderr=%s" % (mode, proc.returncode, proc.stderr))
                if code == 0:
                    # "end_turn" and "stop" are the two completed reasons.
                    self.assertEqual(self.evidence(out)["finish_reason"], "stop")
                    continue
                # The refusal note carries the client's own reason, verbatim,
                # and no seat directory is published.
                self.assertIn(reason, proc.stderr)
                self.assert_no_seat(out)
                self.assertFalse(out.exists(),
                                 "a truncated reply must publish no seat directory")

    def test_an_unknown_verdict_word_refuses_with_the_verbatim_line(self):
        """An unknown verdict word never publishes: non-zero, line in the note (r3)."""
        self.set_mode("unknown-verdict")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("VERDICT: MAYBE", proc.stderr,
                      "the refusal note must quote the client's verdict line verbatim")
        self.assert_no_seat(out)
        self.assertFalse(out.exists(),
                         "an unknown verdict word must publish no seat directory")

    def test_the_last_test_gaming_line_is_the_seats_own(self):
        """An echoed brief's TEST-GAMING example must not shadow the seat's own."""
        self.set_mode("gaming-echo")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.evidence(out)["test_gaming"], "yes",
                         "the LAST TEST-GAMING line is the seat's own answer; an "
                         "earlier line echoed from the brief must not win (a later "
                         "yes is the fail-closed direction)")

    def test_a_result_that_is_not_utf8_refuses_without_a_traceback(self):
        """A lone surrogate in the result is a clean refusal, not a crash."""
        self.set_mode("surrogate")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr,
                         "an unusable reply must refuse cleanly, not crash")
        self.assert_no_seat(out)
        self.assertFalse(out.exists())

    def test_client_secrets_on_a_failed_run_are_redacted_in_the_note(self):
        """URL userinfo, api_key= and Bearer secrets never reach the failure note."""
        self.set_mode("stderr-secrets")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("qodercli exited 7", proc.stderr)
        for secret in ("hunter2", "sk-or-v1-0123456789abcdef", "tok-abc123def456"):
            self.assertNotIn(secret, proc.stderr,
                             "the failure note leaked a client secret: %r" % secret)
        self.assert_no_seat(out)
        self.assertFalse(out.exists())

    def test_a_failed_persistence_probe_refuses_the_run(self):
        """A probe failure refuses the run: never execute with persistence on (r3)."""
        self.set_mode("help-fail")
        out, argv = self.seat()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("persistence", proc.stderr.lower())
        self.assert_no_seat(out)
        self.assertFalse(out.exists())
        self.assertFalse((self.dir / "record.json").exists(),
                         "the review call must never start when the persistence "
                         "probe failed: its status is unknown, not disabled")

    def test_a_client_that_survives_the_kill_is_reported_as_not_exited(self):
        """The post-kill wait is hard-bounded: no seat, exit 1, 'client did not exit'."""
        import importlib.util
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        spec = importlib.util.spec_from_file_location("qoder_seat", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class HungClient:
            """Answers the probe's --help once, then never exits - not even after the kill."""

            def __init__(self):
                self.pid = 4242
                self.returncode = None
                self.calls = 0
                self.timeouts = []

            def communicate(self, prompt=None, timeout=None):
                self.calls += 1
                self.timeouts.append(timeout)
                if prompt is not None:
                    # The review call: never completes.
                    raise subprocess.TimeoutExpired("qodercli", timeout)
                if self.calls == 1:
                    # The probe's --help.
                    return (b"--no-session-persistence  Disable session persistence\n", b"")
                # The post-kill wait expires too: the pipe was never released.
                raise subprocess.TimeoutExpired("qodercli", timeout)

        made = []

        def factory(*args, **kwargs):
            client = HungClient()
            made.append(client)
            return client

        out, argv = self.seat()
        err = io.StringIO()
        with patch.object(module.subprocess, "Popen", factory), \
                patch.object(module.os, "killpg", create=True), \
                patch.object(module.subprocess, "run"), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                module.main(argv[2:])
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("client did not exit within 10s of the kill", err.getvalue())
        # The second (post-kill) wait is bounded by the hard timeout, never None.
        self.assertEqual(made[-1].timeouts[-1], 10)
        self.assertEqual(module.KILL_WAIT, 10)
        self.assert_no_seat(out)
        self.assertFalse(out.exists(), "no seat is published for a client that ignored the kill")

    def test_a_leftover_partial_is_refused_before_the_client_starts(self):
        """A leftover .partial-* refuses before the client runs, not after it."""
        out, argv = self.seat()
        (self.dir / (out.name + ".partial-dead")).mkdir()
        proc = self.run_tool(argv)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("partial", proc.stderr)
        self.assertFalse((self.dir / "record.json").exists(),
                         "the client must not run while a leftover partial seat "
                         "waits to be recovered")
        self.assert_no_seat(out)

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
