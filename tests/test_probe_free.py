#!/usr/bin/env python3
"""Unit tests for tools/probe-free.py (AO-PROBE-D657: cache probe, TSV, budget).

No test here reaches a real gateway or reads a real key: the network side is a
fake OpenAI-shape chat server on a real loopback socket (stdlib http.server -
`post()` is never mocked, so the retry, the JSON decode and the HTTPError path
are the probe's own), and the key is a fake value put in AUTOOS_OMNIROUTE_KEY
for the subprocess. A scripted leg answers 500, never 429/503/504: those are
the probe's backoff statuses and would sleep for minutes.

Run from the repo root:

    python3 tests/test_probe_free.py [ClassName]
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "probe-free.py"
FAKE_KEY = "fake-key-never-a-secret"


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_free_under_test", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe_free = _load_probe()


def chat(content="ACK_OK", served="served-model", usage=None):
    row = {"model": served,
           "choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        row["usage"] = usage
    return row


def tool_reply():
    """The one get_weather call the probe's tool check passes on."""
    return {"model": "served-model",
            "choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "get_weather",
                                             "arguments": json.dumps({"city": "Paris"})}}]}}]}


def openai_usage(cached):
    return {"prompt_tokens": 5000, "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": cached}}


def deepseek_usage(hit):
    return {"prompt_tokens": 5000, "prompt_cache_hit_tokens": hit}


def anthropic_usage(read):
    return {"cache_read_input_tokens": read}


class FakeGateway:
    """One canned (status, payload) per arriving request, in script order."""

    def __init__(self, script=None, default=None):
        self.script = script or {}
        self.default = default or (200, chat())
        self.requests = []
        self.server = None
        self.thread = None

    def next_for(self, leg):
        queued = self.script.get(leg)
        if queued:
            return queued.pop(0)
        return self.default

    def start(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                gateway.requests.append(body)
                status, payload = gateway.next_for(body.get("model"))
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class PrefixTests(unittest.TestCase):
    def test_prefix_is_deterministic_and_at_least_4k_tokens(self):
        first, second = probe_free.cache_prefix(), probe_free.cache_prefix()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 4096 * 4)

    def test_cache_calls_share_the_prefix_and_differ_in_the_turn(self):
        prefix = probe_free.cache_prefix()
        a = probe_free.cache_body("l/x", prefix, probe_free.CACHE_TURN_FIRST)
        b = probe_free.cache_body("l/x", prefix, probe_free.CACHE_TURN_SECOND)
        self.assertEqual(a["messages"][0]["content"], b["messages"][0]["content"])
        self.assertNotEqual(a["messages"][1]["content"], b["messages"][1]["content"])


class CachedTokensOfTests(unittest.TestCase):
    def test_reads_every_spelling(self):
        self.assertEqual(probe_free.cached_tokens_of(openai_usage(4096)), 4096)
        self.assertEqual(probe_free.cached_tokens_of(deepseek_usage(7)), 7)
        self.assertEqual(probe_free.cached_tokens_of(anthropic_usage(9)), 9)

    def test_no_field_and_no_usage_are_not_zero(self):
        self.assertIsNone(probe_free.cached_tokens_of({"prompt_tokens": 1}))
        self.assertIsNone(probe_free.cached_tokens_of({}))
        self.assertIsNone(probe_free.cached_tokens_of(None))

    def test_non_integer_field_is_ignored(self):
        self.assertIsNone(probe_free.cached_tokens_of({"prompt_cache_hit_tokens": "0"}))
        self.assertIsNone(probe_free.cached_tokens_of({"cache_read_input_tokens": True}))


class CacheResultTests(unittest.TestCase):
    def test_hit_is_true(self):
        self.assertEqual(probe_free.cache_result(chat(usage=openai_usage(1024)))[0], "true")

    def test_reported_zero_is_false(self):
        self.assertEqual(probe_free.cache_result(chat(usage=deepseek_usage(0)))[0], "false")

    def test_missing_usage_or_field_is_unknown(self):
        self.assertEqual(probe_free.cache_result(chat())[0], "unknown")
        self.assertEqual(probe_free.cache_result(chat(usage={"prompt_tokens": 5}))[0], "unknown")
        self.assertEqual(probe_free.cache_result(None)[0], "unknown")


class AckResultTests(unittest.TestCase):
    def test_empty_content_is_distinct_from_a_wrong_answer(self):
        self.assertEqual(probe_free.ack_result(chat("ACK_OK"))[0], "ok")
        self.assertEqual(probe_free.ack_result(chat(""))[0], "empty")
        self.assertEqual(probe_free.ack_result(chat(None))[0], "empty")
        self.assertEqual(probe_free.ack_result(chat("the weather is sunny"))[0], "fail")
        self.assertEqual(probe_free.ack_result({})[0], "fail")
        self.assertEqual(probe_free.ack_result({"choices": "broken"})[0], "fail")

    def test_only_a_standalone_ack_token_answers(self):
        """The old substring rule scored these as an ack, so a refusing leg passed."""
        for text in ("ACKNOWLEDGED", "packaged answer", "Back to work"):
            self.assertEqual(probe_free.ack_result(chat(text))[0], "fail", text)
        for text in ("ACK_OK", "ack_ok.", "ack"):
            self.assertEqual(probe_free.ack_result(chat(text))[0], "ok", text)


class TsvTests(unittest.TestCase):
    def test_header_columns(self):
        self.assertEqual(probe_free.TSV_HEADER.split("\t"),
                         ["model", "gateway", "ack", "tool", "cache",
                          "ack_ms", "served", "error"])

    def test_row_order_and_placeholders(self):
        rec = {"leg": "l/x", "ack": "empty", "tool": "ok", "prompt_cache": "true",
               "ack_ms": 812, "ack_served": "served-model"}
        self.assertEqual(
            probe_free.tsv_row(rec, "central").split("\t"),
            ["l/x", "central", "empty", "ok", "true", "812", "served-model", "-"])

    def test_a_cell_can_neither_split_a_row_nor_widen_it(self):
        rec = {"leg": "l/x", "ack": "fail", "ack_error": "HTTP 500\nbody\there"}
        row = probe_free.tsv_row(rec, "central")
        self.assertEqual(len(row.split("\t")), 8)
        self.assertNotIn("\n", row)


class LiveProbeTests(unittest.TestCase):
    """The whole CLI against the fake gateway - the real post(), sockets, TSV."""

    def run_probe(self, gateway_url, extra=(), env=None):
        with tempfile.TemporaryDirectory() as tmp:
            self.tsv = os.path.join(tmp, "out.tsv")
            run_env = dict(os.environ, AUTOOS_OMNIROUTE_KEY=FAKE_KEY)
            run_env.update(env or {})
            proc = subprocess.run(
                [sys.executable, str(PROBE), "--gateway", gateway_url,
                 "--model", "l/x", "--cache", "--tsv", self.tsv, *extra],
                capture_output=True, text=True, timeout=120, env=run_env, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.records = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            self.stdout = proc.stdout
            with open(self.tsv, encoding="utf-8") as fh:
                self.rows = [line.split("\t") for line in fh.read().splitlines()]
            return proc

    def test_cache_hit_row(self):
        gateway = FakeGateway(script={"l/x": [
            (200, chat()), (200, tool_reply()),
            (200, chat(usage=openai_usage(0))), (200, chat(usage=anthropic_usage(4211)))]})
        url = gateway.start()
        try:
            self.run_probe(url)
        finally:
            gateway.stop()
        rec = self.records[0]
        self.assertEqual(rec["prompt_cache"], "true")
        self.assertEqual(rec["cached_tokens"], 4211)
        self.assertEqual(self.rows[0], probe_free.TSV_HEADER.split("\t"))
        self.assertEqual(self.rows[1][:5], ["l/x", "central", "ok", "ok", "true"])
        self.assertEqual(self.rows[1][6], "served-model")
        # ack, tool, then the two cache calls - one identical prefix each side
        bodies = gateway.requests
        self.assertEqual([b.get("max_tokens") for b in bodies], [16, 1024, 16, 16])
        self.assertEqual(bodies[2]["messages"][0]["content"],
                         bodies[3]["messages"][0]["content"])
        self.assertNotIn(FAKE_KEY, self.stdout + "\n".join(sum(self.rows, [])))

    def test_no_cache_field_is_unknown_and_reported_zero_is_false(self):
        gateway = FakeGateway(script={"l/x": [
            (200, chat()), (200, tool_reply()),
            (200, chat()), (200, chat(usage=deepseek_usage(0)))]})
        url = gateway.start()
        try:
            self.run_probe(url, extra=("--gateway-label", "vertex-prox",
                                       "--ack-max-tokens", "32"))
        finally:
            gateway.stop()
        self.assertEqual(self.records[0]["prompt_cache"], "false")
        self.assertEqual(self.rows[1][1], "vertex-prox")
        self.assertEqual([b["max_tokens"] for b in gateway.requests], [32, 1024, 32, 32])

    def test_failed_cache_call_is_unknown_not_false(self):
        gateway = FakeGateway(script={"l/x": [
            (200, chat()), (200, tool_reply()),
            (500, {"error": "upstream down"}), (500, {"error": "upstream down"})]})
        url = gateway.start()
        try:
            self.run_probe(url)
        finally:
            gateway.stop()
        self.assertEqual(self.records[0]["prompt_cache"], "unknown")
        self.assertEqual(self.rows[1][4], "unknown")
        self.assertEqual(self.rows[1][7], "HTTP 500")

    def test_empty_ack_is_recorded_as_empty(self):
        gateway = FakeGateway(script={"l/x": [
            (200, chat(content=None)), (200, tool_reply()),
            (200, chat(content=None, usage=openai_usage(0))),
            (200, chat(content=None, usage=openai_usage(0)))]})
        url = gateway.start()
        try:
            self.run_probe(url)
        finally:
            gateway.stop()
        self.assertEqual(self.records[0]["ack"], "empty")
        self.assertEqual(self.rows[1][2], "empty")

    def test_legs_file_names_the_legs_and_ignores_comments(self):
        gateway = FakeGateway()
        url = gateway.start()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                            encoding="utf-8") as fh:
                fh.write("# a comment\n\nl/file\n")
                path = fh.name
            self.run_probe(url, extra=("--legs-file", path))
        finally:
            gateway.stop()
            os.unlink(path)
        self.assertEqual([r["leg"] for r in self.records], ["l/x", "l/file"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
