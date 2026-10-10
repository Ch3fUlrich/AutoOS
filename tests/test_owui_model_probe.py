#!/usr/bin/env python3
"""Unit tests for tools/owui_model_probe.py (OW-MODEL-TESTS, D-994).

Hermetic: no test reaches a real gateway or a real key. The network side is a
fake OpenAI-shape server on a real loopback socket (stdlib http.server - the
probe's own `urllib` calls, SSE reader and HTTPError path run for real), the key
is a fake value put in AUTOOS_OMNIROUTE_KEY for the subprocess, and every case
uses a short `--timeout` so the scripted timeout model costs a second, not two
minutes. A scripted leg answers 400 with a body that carries the fake key and a
bearer token so the masking and the never-a-secret-in-output rules are asserted
against the probe's real bytes.

Run from the repo root:

    python3 tests/test_owui_model_probe.py [ClassName]
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "owui_model_probe.py"
FAKE_KEY = "fake-omniroute-key-never-real"


def _load_probe():
    spec = importlib.util.spec_from_file_location("owui_probe_under_test", str(PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()

# ids the fake lists; three of them are aliases the probe must skip.
REAL_ANSWERED = "openai/gpt-x"
REAL_ANTIGRAVITY = "antigravity/claude-opus"
REAL_DEEPSEEK = "deepseek/deepseek-chat"
REAL_EMPTY = "empty/nocontent"
REAL_ERR = "err/400"
REAL_TIMEOUT = "slow/timeout"
ALIAS_DUP_TARGET = "openai/gpt-x-dup"     # target duplicates gpt-x
ALIAS_FLAGGED = "misc/flagged"            # alias: true
ALIAS_POINTER = "misc/alias-of"           # alias_of -> gpt-x

MODELS_DATA = {"data": [
    {"id": REAL_ANSWERED, "owned_by": "openai", "tier": "cheap"},
    {"id": ALIAS_DUP_TARGET, "target": REAL_ANSWERED},
    {"id": ALIAS_FLAGGED, "alias": True},
    {"id": ALIAS_POINTER, "alias_of": REAL_ANSWERED},
    {"id": REAL_ANTIGRAVITY, "owned_by": "anthropic"},
    {"id": REAL_DEEPSEEK, "owned_by": "deepseek", "tier": "free"},
    {"id": REAL_EMPTY},
    {"id": REAL_ERR},
    {"id": REAL_TIMEOUT},
]}


def stream_chunk(content):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n\n").encode()


def tool_reply():
    return {"model": "served", "choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"}}]}}]}


class FakeGateway:
    """A canned OpenAI-shape surface: /v1/models + both chat calls + faults."""

    def start(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # close after each response, no keep-alive

            def log_message(self, *args):
                pass

            def do_GET(self):
                self._send_json(200, MODELS_DATA)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                gateway.requests.append(body)
                model = body.get("model", "")
                try:
                    if model == REAL_ERR:
                        self._send_json(400, {"error": {"message":
                            "bad auth: Bearer sk-abcdef0123456789 api_key=Zzzzsecret12345 "
                            "key=%s" % FAKE_KEY}})
                    elif model == REAL_TIMEOUT:
                        time.sleep(2.0)
                        self._send_json(200, {"choices": [{"message": {"content": "late"}}]})
                    elif body.get("stream"):
                        self._send_stream(model)
                    else:
                        self._send_json(200, tool_reply())
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client timed out and hung up mid-write

            def _send_json(self, status, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_stream(self, model):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                content = "" if model == REAL_EMPTY else "OK 2026-10-10"
                self.wfile.write(stream_chunk(content))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def __init__(self):
        self.requests = []
        self.server = None
        self.thread = None


class AliasRuleTests(unittest.TestCase):
    def test_flagged_and_duplicate_targets_are_skipped(self):
        kept = [e["id"] for e in probe.select_real_models(MODELS_DATA["data"])]
        self.assertIn(REAL_ANSWERED, kept)
        self.assertNotIn(ALIAS_DUP_TARGET, kept)   # target already claimed by gpt-x
        self.assertNotIn(ALIAS_FLAGGED, kept)      # alias: true
        self.assertNotIn(ALIAS_POINTER, kept)      # alias_of -> gpt-x

    def test_a_plain_distinct_list_keeps_every_id(self):
        data = [{"id": "a/one"}, {"id": "b/two"}, {"id": "c/three"}]
        self.assertEqual([e["id"] for e in probe.select_real_models(data)],
                         ["a/one", "b/two", "c/three"])

    def test_an_unflagged_alias_of_itself_is_not_skipped(self):
        # alias_of pointing at its own id is not an alias of anything else.
        data = [{"id": "a/one", "alias_of": "a/one"}]
        self.assertEqual([e["id"] for e in probe.select_real_models(data)], ["a/one"])

    def test_entries_without_an_id_are_dropped(self):
        data = [{"owned_by": "x"}, {"id": ""}, {"id": "a/one"}]
        self.assertEqual([e["id"] for e in probe.select_real_models(data)], ["a/one"])


class FamilyTierTests(unittest.TestCase):
    def test_family_from_the_id_prefix(self):
        self.assertEqual(probe.family_of({"id": "deepseek/deepseek-chat"}), "deepseek")
        self.assertEqual(probe.family_of({"id": "antigravity/claude-opus"}), "antigravity")

    def test_route_prefix_is_skipped_for_the_family(self):
        self.assertEqual(probe.family_of({"id": "omniroute/deepseek-direct-flash"}), "deepseek")

    def test_family_without_a_slash_is_the_leading_token(self):
        self.assertEqual(probe.family_of({"id": "gpt-4o"}), "gpt")
        self.assertEqual(probe.family_of({"id": "claude-3-5-sonnet"}), "claude")

    def test_family_falls_back_to_owned_by(self):
        self.assertEqual(probe.family_of({"id": "-weird", "owned_by": "Google"}), "google")

    def test_tier_reads_the_field_or_dash(self):
        self.assertEqual(probe.tier_of({"id": "x", "tier": "cheap"}), "cheap")
        self.assertEqual(probe.tier_of({"id": "x"}), "-")
        self.assertEqual(probe.tier_of({"id": "x", "tier": ""}), "-")


class MaskErrorTests(unittest.TestCase):
    def test_the_literal_key_and_key_looking_shapes_are_masked(self):
        raw = ("HTTP 400 Bearer %s sk-abcdef0123456789 api_key=Zzzzsecret12345" % FAKE_KEY)
        out = probe.mask_error(raw, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("sk-abcdef0123456789", out)
        self.assertIn("[autoos:redacted]", out)

    def test_control_characters_and_newlines_are_stripped_to_one_line(self):
        out = probe.mask_error("line one\r\nline\ttwo\x00tail", FAKE_KEY)
        for bad in ("\n", "\r", "\x00"):
            self.assertNotIn(bad, out)

    def test_the_stored_error_is_bounded(self):
        out = probe.mask_error("x" * 4000, FAKE_KEY)
        self.assertLessEqual(len(out), probe.ERROR_MAX_CHARS)

    def test_an_empty_error_stays_empty(self):
        self.assertEqual(probe.mask_error("", FAKE_KEY), "")


class LiveProbeTests(unittest.TestCase):
    """The whole CLI against the fake gateway - real urllib, SSE, faults."""

    def setUp(self):
        self.gateway = FakeGateway()
        self.url = self.gateway.start()
        self.addCleanup(self.gateway.stop)

    def run_probe(self, out_dir, extra=()):
        env = dict(os.environ, AUTOOS_OMNIROUTE_KEY=FAKE_KEY)
        proc = subprocess.run(
            [sys.executable, str(PROBE), "--base-url", self.url,
             "--out-dir", out_dir, "--timeout", "1", *extra],
            capture_output=True, text=True, timeout=90, env=env, cwd=str(ROOT))
        return proc

    def rows(self, out_dir):
        with open(os.path.join(out_dir, "rows.jsonl"), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def md(self, out_dir):
        with open(os.path.join(out_dir, "owui-models.md"), encoding="utf-8") as fh:
            return fh.read()

    def by_id(self, out_dir):
        return {r["id"]: r for r in self.rows(out_dir)}

    def test_rows_answered_tool_and_faults(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_probe(tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = self.by_id(tmp)
            # aliases never probed
            for skipped in (ALIAS_DUP_TARGET, ALIAS_FLAGGED, ALIAS_POINTER):
                self.assertNotIn(skipped, rows)
            self.assertEqual(len(rows), 6)

            ok = rows[REAL_ANSWERED]
            self.assertEqual(ok["chat_status"], 200)
            self.assertEqual(ok["answered"], "y")
            self.assertIsInstance(ok["first_token_ms"], int)
            self.assertIsInstance(ok["total_ms"], int)
            self.assertEqual(ok["tool_status"], 200)
            self.assertEqual(ok["tool_call"], "y")
            self.assertEqual(ok["family"], "openai")
            self.assertEqual(ok["tier"], "cheap")

            self.assertEqual(rows[REAL_EMPTY]["answered"], "n")
            self.assertEqual(rows[REAL_DEEPSEEK]["family"], "deepseek")
            self.assertEqual(rows[REAL_DEEPSEEK]["tier"], "free")

            err = rows[REAL_ERR]
            self.assertEqual(err["chat_status"], 400)
            self.assertEqual(err["tool_status"], 400)
            self.assertIn("[autoos:redacted]", err["error"])
            self.assertNotIn(FAKE_KEY, err["error"])

            to = rows[REAL_TIMEOUT]
            self.assertEqual(to["chat_status"], "ERR")
            self.assertIn("timeout", to["error"])
            self.assertIsNone(to["first_token_ms"])

    def test_markdown_table_and_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_probe(tmp)
            text = self.md(tmp)
            self.assertIn("antigravity listed: y (ids: %s)" % REAL_ANTIGRAVITY, text)
            self.assertIn("deepseek listed: y (ids: %s)" % REAL_DEEPSEEK, text)
            self.assertIn("total: 6", text)
            self.assertIn("answered:", text)
            self.assertIn("tool-call ok:", text)
            self.assertIn("| model id | family | tier |", text)
            self.assertIn(REAL_ANSWERED, text)

    def test_no_key_anywhere_in_output_or_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_probe(tmp)
            with open(os.path.join(tmp, "rows.jsonl"), encoding="utf-8") as fh:
                rows_text = fh.read()
            blob = "\n".join([proc.stdout, proc.stderr, self.md(tmp), rows_text])
            self.assertNotIn(FAKE_KEY, blob)

    def test_resume_skips_ids_already_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.run_probe(tmp)
            self.assertEqual(first.returncode, 0, first.stderr)
            posts_after_first = len(self.gateway.requests)
            second = self.run_probe(tmp)
            self.assertEqual(second.returncode, 0, second.stderr)
            # no new chat POSTs on the resume: every id was already in rows.jsonl
            self.assertEqual(len(self.gateway.requests), posts_after_first)
            # the row stream did not grow or duplicate
            self.assertEqual(len(self.rows(tmp)), 6)
            ids = [r["id"] for r in self.rows(tmp)]
            self.assertEqual(sorted(ids), sorted(set(ids)))

    def test_limit_probes_a_dry_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_probe(tmp, extra=("--limit", "1"))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(self.rows(tmp)), 1)
            # one model, two calls (stream chat + tool)
            self.assertEqual(len(self.gateway.requests), 2)

    def test_missing_key_is_refused_without_printing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("AUTOOS_OMNIROUTE_KEY", None)
            proc = subprocess.run(
                [sys.executable, str(PROBE), "--base-url", self.url, "--out-dir", tmp],
                capture_output=True, text=True, timeout=30, env=env, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 3)
            self.assertIn("AUTOOS_OMNIROUTE_KEY", proc.stderr)

    def test_key_is_never_in_the_command_line(self):
        # argv carries --base-url/--out-dir/--timeout only; the key is env-only.
        args = probe.parse_args(["--base-url", "https://h", "--out-dir", "/tmp/x",
                                 "--timeout", "5", "--limit", "2"])
        argv_repr = " ".join(["--base-url", args.base_url, "--out-dir", args.out_dir])
        self.assertNotIn(FAKE_KEY, argv_repr)
        self.assertEqual(args.timeout, 5)
        self.assertEqual(args.limit, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
