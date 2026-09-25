#!/usr/bin/env python3
"""Unit tests for retry logic in _chat (probe retry on 503) in tools/audit-router.py.

The tests patch urllib.request.urlopen to simulate various responses and patch
mod._sleep to record sleep calls without actually sleeping.
"""

import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


def _load_module():
    """Load tools/audit-router.py via an absolute path."""
    here = Path(__file__).resolve().parent
    router_path = here.parent / "tools" / "audit-router.py"
    spec = importlib.util.spec_from_file_location("audit_router", str(router_path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class ChatRetryTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        # Record sleep calls
        self.sleep_calls = []
        self.mod._sleep = lambda sec: self.sleep_calls.append(sec)

    def _fake_response(self, status=200, body=b"{}"):
        class Resp:
            def __init__(self, status, body):
                self.status = status
                self._body = body
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def read(self, n=-1):
                return self._body
        return Resp(status, body)

    def test_success_after_two_503(self):
        # 503, 503, then 200
        side_effects = [
            urllib.error.HTTPError("http://x", 503, "unavailable", {}, io.BytesIO(b"resource pressure")),
            urllib.error.HTTPError("http://x", 503, "unavailable", {}, io.BytesIO(b"resource pressure")),
            self._fake_response(200),
        ]
        with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=side_effects):
            status, detail = self.mod._chat("http://x", "m", "k", 5)
        self.assertEqual(status, 200)
        self.assertEqual(detail, "ack")
        # urlopen called 3 times
        # all attempts consumed
        # sleep called twice with the first two delays
        self.assertEqual(self.sleep_calls, list(self.mod.RETRY_DELAYS_S[:2]))

    def test_always_503(self):
        # 503 forever, length = 1 + len(RETRY_DELAYS_S)
        side_effects = [
            urllib.error.HTTPError("http://x", 503, "unavailable", {}, io.BytesIO(b"resource pressure"))
            for _ in range(1 + len(self.mod.RETRY_DELAYS_S))
        ]
        with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=side_effects):
            status, detail = self.mod._chat("http://x", "m", "k", 5)
        # final status should be 503 and snippet from last error
        self.assertEqual(status, 503)
        self.assertIn("resource pressure", detail)
        # urlopen called expected number of times
        # all attempts consumed
        # sleep called len(RETRY_DELAYS_S) times
        self.assertEqual(self.sleep_calls, list(self.mod.RETRY_DELAYS_S))

    def test_400_and_404_no_retry(self):
        for code in (400, 404):
            side_effects = [urllib.error.HTTPError("http://x", code, "bad", {}, io.BytesIO(b"bad"))]
            with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=side_effects):
                status, detail = self.mod._chat("http://x", "m", "k", 5)
            self.assertEqual(status, code)
            self.assertEqual(len(self.sleep_calls), 0)
            self.sleep_calls.clear()

    def test_non_http_exception(self):
        def fake_urlopen(req, timeout=None):
            raise OSError("refused")
        with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            status, detail = self.mod._chat("http://x", "m", "k", 5)
        self.assertEqual(status, "ERR")
        self.assertIn("refused", detail)
        self.assertEqual(len(self.sleep_calls), 0)

    def test_success_first_try(self):
        seq = [self._fake_response(200)]
        def fake_urlopen(req, timeout=None):
            return seq.pop(0)
        with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            status, detail = self.mod._chat("http://x", "m", "k", 5)
        self.assertEqual(status, 200)
        self.assertEqual(detail, "ack")
        self.assertEqual(len(self.sleep_calls), 0)

    def test_classify_503_is_state(self):
        self.assertEqual(self.mod.classify(503), "state")


if __name__ == "__main__":
    unittest.main()
