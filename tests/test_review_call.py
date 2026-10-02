#!/usr/bin/env python3
"""REVIEWCALL (D-440): tools/review-call.py, the sanctioned gateway review call.

One tool-less chat completion to OmniRoute, run ONLY through
`tools/autoos_gateway_key.py exec`, whose child environment is the one and only
place AUTOOS_OMNIROUTE_KEY may come from. Everything here runs against a fake
gateway: an `http.server` bound to 127.0.0.1:0 in a thread that answers
`POST /v1/chat/completions` and records the request, so this suite never
touches the network.

Asserted: the Authorization header equals the fake env key (and reaches the
gateway only via `exec`), the session-tag/run-id headers match the spawner's
shape, every evidence.json field (including finish_reason and verdict,
recorded on every run), served_model taken from the response, the request
carries NO `temperature` by default (`--temperature 0.2` is sent verbatim -
measured, a temperature-0 request is cut at 64 completion tokens on this
gateway) and `max_tokens` 16000 UNLESS `--max-tokens` is given, the fake key
(and the prompt) never in stdout/stderr nor in any output file, no response
header VALUE recorded besides the correlation id, missing key -> exit 2,
non-200 -> exit 3 + error.json, a finish_reason 'length' empty answer ->
exit 4 + stderr warning + evidence.json finish_reason, and the VERDICT parse
(the tolerant D-337 rules, called directly on the loaded module: bold heading
and `**Verdict:** pass` accepted, quotes/tables/fences/diff hunks rejected,
the last qualifying line wins, an open fence fails closed).

Run directly:

    python3 tests/test_review_call.py
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
TOOL = TOOLS / "review-call.py"
GATEWAY_KEY = TOOLS / "autoos_gateway_key.py"

# The tool loaded in-process - the hyphen in the file name rules out a plain
# import, so the repo's spec_from_file_location pattern. Only the parser is
# called from here (`verdict_line`); every end-to-end case still runs the tool
# as a subprocess against FakeGateway.
_spec = importlib.util.spec_from_file_location("review_call_under_test",
                                               str(TOOL))
review_call = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_call)

FAKE_KEY = "fake-review-key-DO-NOT-LEAK-0123456789abcdef"
MODEL = "ovh/gpt-oss-120b"
SERVED_MODEL = "gateway-server/served-model-9"
PROMPT = "REVIEW-PROMPT-SENTINEL: paste of the diff to review.\nExplain it.\n"
# Two VERDICT lines: the LAST qualifying one wins (neither value outside
# pass / fail-with-findings counts - see VerdictParseTests).
ANSWER = ("first pass\nVERDICT: pass\nthen a finding\n"
          "VERDICT: fail-with-findings\n")
CORRELATION = "corr-id-abc-123"
# A response header whose VALUE must never be recorded (names only).
DIAG_HEADER_VALUE = "diag-trace-value-must-not-be-recorded"

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{6}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EXPECTED_TOKENS = {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
SUCCESS_LINES = 4


class FakeGateway:
    """A threaded http.server on 127.0.0.1:0 recording every POST it receives."""

    def __init__(self, status=200, answer=ANSWER, correlation=CORRELATION,
                 finish_reason="stop"):
        self.status = status
        self.answer = answer
        self.correlation = correlation
        self.finish_reason = finish_reason
        self.requests = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep unittest output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                record = {"path": self.path,
                          "headers": {k.lower(): v
                                      for k, v in self.headers.items()}}
                try:
                    record["body"] = json.loads(raw.decode("utf-8"))
                except ValueError:
                    record["body"] = None
                gateway.requests.append(record)
                if gateway.status == 200:
                    payload = {
                        "id": "cmpl-fake-1",
                        "object": "chat.completion",
                        "model": SERVED_MODEL,
                        "choices": [{"index": 0,
                                     "message": {"role": "assistant",
                                                 "content": gateway.answer},
                                     "finish_reason": gateway.finish_reason}],
                        "usage": dict(EXPECTED_TOKENS),
                    }
                else:
                    payload = {"error": "gateway exploded"}
                body = json.dumps(payload).encode("utf-8")
                self.send_response(gateway.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Diag-Trace", DIAG_HEADER_VALUE)
                if gateway.correlation:
                    self.send_header("X-Correlation-Id", gateway.correlation)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


class ReviewCallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.prompt_file = root / "prompt.md"
        # Written as bytes so the file's exact bytes ARE PROMPT on every
        # platform (PATH.write_text would translate \n to CRLF on Windows).
        self.prompt_file.write_bytes(PROMPT.encode("utf-8"))
        self.out_dir = root / "out"
        self.gateways = []

    def tearDown(self):
        for gateway in self.gateways:
            gateway.stop()
        self.tmp.cleanup()

    def start_gateway(self, **kwargs):
        gateway = FakeGateway(**kwargs).start()
        self.gateways.append(gateway)
        return gateway

    def env(self, with_key=True):
        env = dict(os.environ)
        for name in list(env):
            if name.startswith("AUTOOS_"):
                env.pop(name)  # never inherit key/URL/host state from this box
        env["HOME"] = str(self.root)
        env["USERPROFILE"] = str(self.root)
        env["XDG_CONFIG_HOME"] = str(self.root / ".config")
        env["LOCALAPPDATA"] = str(self.root / "AppData" / "Local")
        if with_key:
            env["AUTOOS_OMNIROUTE_KEY"] = FAKE_KEY
        return env

    def run_tool(self, gateway=None, *extra, with_key=True, via_url_env=False,
                 via_exec=False):
        cmd = [sys.executable, str(TOOL), "--model", MODEL,
               "--prompt-file", str(self.prompt_file),
               "--out-dir", str(self.out_dir)]
        env = self.env(with_key=with_key)
        if gateway is not None:
            if via_url_env:
                env["AUTOOS_OMNIROUTE_URL"] = gateway.base + "/v1/"
            else:
                cmd += ["--gateway-url", gateway.base]
        cmd += list(extra)
        if via_exec:
            # The documented interface: the key arrives only through `exec`.
            keys = self.root / "keys.yml"
            keys.write_text("omniroute_reviewws: " + FAKE_KEY + "\n",
                            encoding="utf-8")
            env["AUTOOS_HOST_NAME"] = "reviewws"
            env["AUTOOS_HOST_CONFIG"] = str(self.root / "no-such-host.yml")
            cmd = [sys.executable, str(GATEWAY_KEY), "exec", str(keys), "--"] + cmd
        return subprocess.run(cmd, env=env, capture_output=True, text=True,
                               timeout=120)

    def output_files(self):
        if not self.out_dir.is_dir():
            return []
        return sorted(p for p in self.out_dir.iterdir() if p.is_file())

    def assert_no_leak(self, result, where="captured output"):
        """The fake key and the prompt never reach any channel or file."""
        combined = result.stdout + result.stderr
        self.assertNotIn(FAKE_KEY, combined, "the key leaked into " + where)
        self.assertNotIn("REVIEW-PROMPT-SENTINEL", combined,
                         "the prompt leaked into " + where)
        for path in self.output_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(FAKE_KEY, text, "the key leaked into " + path.name)
            self.assertNotIn(DIAG_HEADER_VALUE, text,
                             "a response header VALUE leaked into " + path.name)

    def read_evidence(self):
        path = self.out_dir / "evidence.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # --- success ------------------------------------------------------------

    def test_success_writes_review_and_evidence_and_prints_only_four_lines(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, "--title", "my-review")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), SUCCESS_LINES, repr(result.stdout))
        self.assertEqual(lines[0], "review.txt: " + str(self.out_dir / "review.txt"))
        self.assertEqual(lines[1], "evidence.json: " + str(self.out_dir / "evidence.json"))
        self.assertEqual(lines[2], "model: " + SERVED_MODEL)
        self.assertEqual(lines[3],
                         "VERDICT: fail-with-findings")  # the LAST verdict

        review = (self.out_dir / "review.txt").read_text(encoding="utf-8")
        self.assertEqual(review, ANSWER)
        self.assert_no_leak(result)

        ev = self.read_evidence()
        self.assertEqual(ev["requested_model"], MODEL)
        self.assertEqual(ev["served_model"], SERVED_MODEL)  # from the response
        self.assertEqual(ev["status"], 200)
        self.assertEqual(ev["finish_reason"], "stop")  # recorded on every run
        self.assertEqual(ev["session_tag"], "review/my-review")
        self.assertRegex(ev["run_id"], RUN_ID_RE)
        self.assertEqual(ev["prompt_sha256"],
                         hashlib.sha256(PROMPT.encode("utf-8")).hexdigest())
        self.assertEqual(ev["tokens"], EXPECTED_TOKENS)
        self.assertRegex(ev["started"], UTC_RE)
        self.assertRegex(ev["finished"], UTC_RE)

        # Correlation id: the one recorded VALUE; everything else a NAME.
        self.assertEqual(ev["correlation_id"], CORRELATION)
        names = [name.lower() for name in ev["response_header_names"]]
        self.assertIn("x-correlation-id", names)
        self.assertIn("x-diag-trace", names)
        for name in ev["response_header_names"]:
            self.assertNotIn(":", name)
        # ... and the fingerprint fallback: no --title -> review/<prompt sha12>
        gateway2 = self.start_gateway()
        self.out_dir = self.root / "out2"
        result2 = self.run_tool(gateway2)
        self.assertEqual(result2.returncode, 0, result2.stdout + result2.stderr)
        self.assertEqual(self.read_evidence()["session_tag"],
                         "review/" + hashlib.sha256(
                             PROMPT.encode("utf-8")).hexdigest()[:12])
        self.assert_no_leak(result2)

    def test_the_request_is_attribution_stamped_and_tool_less(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, "--title", "stamp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        seen = gateway.requests[0]
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["headers"].get("authorization"),
                         "Bearer " + FAKE_KEY)  # the fake env key, verbatim
        run_id = self.read_evidence()["run_id"]
        self.assertEqual(seen["headers"].get("x-omniroute-session-id"),
                         "review/stamp/" + run_id)
        self.assertEqual(seen["headers"].get("x-autoos-run-id"), run_id)

        body = seen["body"]
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["messages"],
                         [{"role": "user", "content": PROMPT}])
        self.assertEqual(body["stream"], False)
        # No temperature BY DEFAULT: measured on this gateway, a
        # temperature-0 request is cut at 64 completion tokens (finish_reason
        # 'length', empty content) - hence the opt-in --temperature flag.
        self.assertNotIn("temperature", body)
        # The default max-tokens is 16000: a reasoning model used 6.2k
        # completion tokens on an 11k prompt, so the old 4096 cut it. The
        # dedicated test below pins the default AND the explicit flag.
        self.assertEqual(body["max_tokens"], 16000)
        self.assertNotIn("tools", body)
        self.assert_no_leak(result)

    def test_title_with_newline_and_colon_never_reaches_a_header(self):
        # A title a caller could paste (`bad\ntitle: x`) is NOT header-safe:
        # session_tag() must fall back to the prompt fingerprint
        # (SESSION_TAG_RE fallback) instead of carrying the raw title, so the
        # received attribution header stays charset-clean and no received
        # header value gains a newline (header injection).
        gateway = self.start_gateway()
        result = self.run_tool(gateway, "--title", "bad\ntitle: x")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        received = gateway.requests[0]["headers"]
        session_id = received.get("x-omniroute-session-id")
        self.assertIsNotNone(session_id, repr(received))
        self.assertRegex(session_id, r"^review/[A-Za-z0-9._/-]+$")
        self.assertEqual(session_id,
                         "review/" + hashlib.sha256(
                             PROMPT.encode("utf-8")).hexdigest()[:12]
                         + "/" + self.read_evidence()["run_id"])
        for name, value in received.items():
            self.assertNotIn("\n", value, "newline in received header %s" % name)
            self.assertNotIn("\r", value, "CR in received header %s" % name)
        self.assertRegex(received.get("x-autoos-run-id", ""), RUN_ID_RE)
        self.assert_no_leak(result)

    def test_temperature_is_sent_only_when_the_flag_is_given(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, "--temperature", "0.2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(gateway.requests[0]["body"]["temperature"], 0.2)
        self.assertEqual(self.read_evidence()["finish_reason"], "stop")
        self.assert_no_leak(result)

    def test_max_tokens_is_16000_unless_the_flag_is_given(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, "--title", "tok-default")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(gateway.requests[0]["body"]["max_tokens"], 16000)
        result = self.run_tool(gateway, "--title", "tok-flag",
                               "--max-tokens", "4096")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 2)
        self.assertEqual(gateway.requests[1]["body"]["max_tokens"], 4096)
        self.assert_no_leak(result)

    def test_colon_bold_verdict_is_normalised_in_stdout_and_evidence(self):
        # The measured miss: the model wrote `**Verdict:** pass` (colon INSIDE
        # the bold) and the old `startswith("VERDICT:")` scan printed
        # `VERDICT: missing`.
        gateway = self.start_gateway(answer="reviewed, looks good\n"
                                             "**Verdict:** pass\n")
        result = self.run_tool(gateway, "--title", "bold-verdict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), SUCCESS_LINES, repr(result.stdout))
        self.assertEqual(lines[3], "VERDICT: pass")  # normalised
        self.assertEqual(self.read_evidence()["verdict"], "pass")
        self.assert_no_leak(result)

    def test_autoos_omniroute_url_env_is_the_gateway_without_the_flag(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, via_url_env=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(self.read_evidence()["status"], 200)
        self.assert_no_leak(result)

    def test_exec_form_supplies_the_key_end_to_end(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, via_exec=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(gateway.requests[0]["headers"].get("authorization"),
                         "Bearer " + FAKE_KEY)
        self.assert_no_leak(result)

    # --- the two failure exits ---------------------------------------------

    def test_missing_key_exits_2_with_the_exec_message_and_sends_nothing(self):
        gateway = self.start_gateway()
        result = self.run_tool(gateway, with_key=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("run me via autoos_gateway_key.py exec -- ...",
                      result.stderr)
        self.assertEqual(gateway.requests, [])  # no call was made
        self.assertEqual(self.output_files(), [])  # nothing was written
        self.assertNotIn(FAKE_KEY, result.stdout + result.stderr)

    def test_non_200_exits_3_and_writes_only_the_error_file(self):
        gateway = self.start_gateway(status=502)
        result = self.run_tool(gateway, "--title", "bad-gw")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        names = [p.name for p in self.output_files()]
        self.assertEqual(names, ["error.json"])
        error = json.loads((self.out_dir / "error.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(error["status"], 502)
        self.assertEqual(error["requested_model"], MODEL)
        self.assertEqual(error["session_tag"], "review/bad-gw")
        self.assertRegex(error["run_id"], RUN_ID_RE)
        self.assertRegex(error["started"], UTC_RE)
        self.assertRegex(error["finished"], UTC_RE)
        self.assert_no_leak(result)

    def test_length_finish_with_empty_answer_exits_4_with_warning_and_evidence(
            self):
        # Measured on the central OmniRoute: a `"temperature": 0` request is
        # cut at 64 completion tokens - finish_reason 'length', no content.
        gateway = self.start_gateway(answer="", finish_reason="length")
        result = self.run_tool(gateway, "--title", "truncated")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")  # no success lines, no VERDICT
        self.assertIn("review-call: answer truncated/empty "
                      "(finish_reason=length)", result.stderr)
        names = [p.name for p in self.output_files()]
        self.assertEqual(names, ["evidence.json", "review.txt"])
        ev = self.read_evidence()
        self.assertEqual(ev["finish_reason"], "length")  # why it was cut
        self.assertEqual(ev["status"], 200)
        self.assertEqual(ev["served_model"], SERVED_MODEL)
        self.assertRegex(ev["finished"], UTC_RE)
        self.assert_no_leak(result)

    def test_no_verdict_line_prints_the_missing_sentinel(self):
        gateway = self.start_gateway(answer="no verdict in this answer.\n")
        result = self.run_tool(gateway, "--title", "no-verdict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), SUCCESS_LINES, repr(result.stdout))
        self.assertEqual(lines[3], "VERDICT: missing")
        self.assertIsNone(self.read_evidence()["verdict"])  # null, not a word
        self.assertEqual(self.read_evidence()["served_model"], SERVED_MODEL)
        self.assert_no_leak(result)


class VerdictParseTests(unittest.TestCase):
    """verdict_line() called DIRECTLY on the loaded module: the tolerant
    D-337 parse (copied from tools/autoos_agent_mcp.py's review_verdict /
    _verdict_value), no gateway, no subprocess.

    Every case returns the NORMALISED line: `VERDICT: pass` /
    `VERDICT: fail-with-findings` / `VERDICT: missing`.
    """

    def verdict(self, text):
        return review_call.verdict_line(text)

    def test_colon_inside_the_bold_mark_is_a_verdict(self):
        # The measured miss that produced this parser.
        self.assertEqual(self.verdict("**Verdict:** pass"), "VERDICT: pass")

    def test_bold_around_the_label_only(self):
        self.assertEqual(self.verdict("**VERDICT**: pass"), "VERDICT: pass")
        self.assertEqual(self.verdict("**verdict:** PASS"),
                         "VERDICT: pass")  # label and value are case-blind

    def test_heading_prefix_normalises_the_value(self):
        self.assertEqual(self.verdict("## VERDICT: fail-with-findings"),
                         "VERDICT: fail-with-findings")

    def test_bullet_and_bold_value_with_one_trailing_punctuation(self):
        self.assertEqual(self.verdict("- VERDICT: **pass**."),
                         "VERDICT: pass")

    def test_a_value_that_says_more_than_the_word_is_no_verdict(self):
        self.assertEqual(self.verdict("VERDICT: pass, but X"),
                         "VERDICT: missing")

    def test_a_quoted_line_is_someone_elses_text(self):
        self.assertEqual(self.verdict("> VERDICT: pass"), "VERDICT: missing")

    def test_a_table_row_is_template_syntax(self):
        self.assertEqual(self.verdict("| VERDICT: pass |"),
                         "VERDICT: missing")

    def test_a_verdict_inside_a_fence_only_is_no_verdict(self):
        self.assertEqual(self.verdict("```text\nVERDICT: pass\n```\n"),
                         "VERDICT: missing")

    def test_a_verdict_inside_a_diff_hunk_only_is_no_verdict(self):
        text = ("diff --git a/f b/f\n"
                "--- a/f\n"
                "+++ b/f\n"
                "@@ -1,3 +1,3 @@\n"
                " keep\n"
                "+VERDICT: pass\n"
                " done\n")
        self.assertEqual(self.verdict(text), "VERDICT: missing")

    def test_two_verdicts_the_last_qualifying_line_wins(self):
        text = ("VERDICT: pass\n"
                "changed my mind after re-reading the hunk\n"
                "VERDICT: fail-with-findings\n")
        self.assertEqual(self.verdict(text), "VERDICT: fail-with-findings")

    def test_an_unclosed_fence_fails_closed_to_the_earlier_verdict(self):
        text = "VERDICT: pass\n```json\n{\"a\": 1}\n"
        self.assertEqual(self.verdict(text), "VERDICT: pass")

    def test_no_verdict_at_all_reports_missing(self):
        self.assertEqual(self.verdict("looks fine to me\n"),
                         "VERDICT: missing")
        self.assertEqual(self.verdict(""), "VERDICT: missing")


class IndentedAndEmphasisVerdictTests(unittest.TestCase):
    """review_verdict() called DIRECTLY (the raw word or None): the RC-2 seat
    fixes - an indented line is a CommonMark code block, never a verdict; a
    single-emphasis label is a verdict. No gateway, no subprocess."""

    def raw(self, text):
        return review_call.review_verdict(text)

    def test_four_space_indented_verdict_is_a_paste(self):
        self.assertIsNone(self.raw("    VERDICT: pass\n"))

    def test_tab_indented_verdict_is_a_paste(self):
        self.assertIsNone(self.raw("\tVERDICT: pass\n"))

    def test_indented_fence_paste_is_never_a_verdict(self):
        # The reported shape: an indented (fence) block reads as code, and
        # its inner VERDICT line is a pasted sample, not the reviewer's word.
        self.assertIsNone(self.raw("    ```\n    VERDICT: pass\n    ```\n"))

    def test_an_indented_pass_never_flips_a_real_fail(self):
        # Last-wins must not let an indented paste overwrite the real verdict.
        text = ("VERDICT: fail-with-findings\n"
                "    VERDICT: pass\n")
        self.assertEqual(self.raw(text), "fail-with-findings")

    def test_single_asterisk_emphasis_round_the_label(self):
        self.assertEqual(self.raw("*VERDICT*: pass"), "pass")

    def test_single_underscore_emphasis_round_the_label(self):
        self.assertEqual(self.raw("_Verdict_: fail-with-findings"),
                         "fail-with-findings")

    def test_unbalanced_emphasis_stays_missing(self):
        self.assertIsNone(self.raw("*VERDICT: pass\n"))

    def test_list_item_bold_verdict_still_counts(self):
        self.assertEqual(self.raw("- **Verdict:** pass"), "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
