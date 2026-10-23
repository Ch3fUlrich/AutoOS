"""tests/test_oc_fallback.py - tests for Claude limit to OpenCode fallback."""

import importlib
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import autoos_oc_fallback as oc_fallback


class OcFallbackTests(unittest.TestCase):
    def test_claude_limit_stop(self):
        for marker in oc_fallback.CLAUDE_LIMIT_MARKERS:
            assert oc_fallback.claude_limit_stop("claude", f"prefix {marker} suffix") is True
        assert oc_fallback.claude_limit_stop("claude", "Error: 429", is_rate_limit=lambda s: "429" in s) is True
        assert oc_fallback.claude_limit_stop("opencode", "usage limit reached") is False
        assert oc_fallback.claude_limit_stop("opencode", "Error: 429", is_rate_limit=lambda s: "429" in s) is False
        assert oc_fallback.claude_limit_stop("claude", None) is False
        assert oc_fallback.claude_limit_stop("claude", "normal error", is_rate_limit=lambda s: False) is False

    def test_pick_fallback_model(self):
        assert oc_fallback.pick_fallback_model({}) == "mimo-v2.6-flash"
        assert oc_fallback.pick_fallback_model({"AUTOOS_OPENCODE_FALLBACK_MODEL": "muse-spark-1.3"}) == "muse-spark-1.3"
        with self.assertRaises(ValueError):
            oc_fallback.pick_fallback_model({"AUTOOS_OPENCODE_FALLBACK_MODEL": "gpt-4"})

    def test_opencode_url(self):
        assert oc_fallback.opencode_url({}) == "http://127.0.0.1:4096"
        assert oc_fallback.opencode_url({"AUTOOS_OPENCODE_URL": "http://127.0.0.1:5000"}) == "http://127.0.0.1:5000"
        with self.assertRaises(ValueError):
            oc_fallback.opencode_url({"AUTOOS_OPENCODE_URL": "ftp://127.0.0.1:4096"})

    def test_opencode_healthy(self):
        resp_ok = mock.Mock(status=200)
        opener_ok = mock.Mock(return_value=resp_ok)
        ok, msg = oc_fallback.opencode_healthy("http://127.0.0.1:4096", opener=opener_ok)
        assert ok is True
        resp_ok.close.assert_called_once()
        opener_ok.assert_called_once_with("http://127.0.0.1:4096/global/health", timeout=3.0)

        opener_err = mock.Mock(side_effect=urllib.error.URLError("connection refused"))
        ok, msg = oc_fallback.opencode_healthy("http://127.0.0.1:4096", opener=opener_err)
        assert ok is False
        assert "connection refused" in msg
        assert "Traceback" not in msg

        err_503 = urllib.error.HTTPError(
            "http://127.0.0.1:4096/global/health", 503, "Unavailable", {}, None
        )
        opener_503 = mock.Mock(side_effect=err_503)
        ok, _ = oc_fallback.opencode_healthy("http://127.0.0.1:4096", opener=opener_503)
        assert ok is False

        err_401 = urllib.error.HTTPError(
            "http://127.0.0.1:4096/global/health", 401, "Unauthorized", {}, None
        )
        ok, _ = oc_fallback.opencode_healthy("http://127.0.0.1:4096", opener=mock.Mock(side_effect=err_401))
        assert ok is True

    def test_opencode_healthy_type_error(self):
        opener_type_err = mock.Mock(side_effect=TypeError("transport failure"))
        ok, msg = oc_fallback.opencode_healthy("http://127.0.0.1:4096", opener=opener_type_err)
        assert ok is False
        assert "transport failure" in msg
        assert opener_type_err.call_count == 1

    def test_fallback_decision(self):
        assert oc_fallback.fallback_decision("opencode", "usage limit reached") == {"switch": False}
        resp_ok = mock.Mock(status=200)
        opener_ok = mock.Mock(return_value=resp_ok)
        dec = oc_fallback.fallback_decision("claude", "usage limit reached", env={}, opener=opener_ok)
        assert dec == {
            "switch": True,
            "client": "opencode",
            "model": "mimo-v2.6-flash",
            "opencode_model": "omniroute/mimo-v2.6-flash",
            "free": False,
            "url": "http://127.0.0.1:4096",
        }
        resp_ok.close.assert_called_once()

        opener_down = mock.Mock(side_effect=urllib.error.URLError("connection refused"))
        dec_down = oc_fallback.fallback_decision("claude", "usage limit reached", env={}, opener=opener_down)
        assert dec_down["switch"] is False
        assert dec_down["refused"] is True
        assert dec_down["rc"] == 15
        assert "not retrying Claude" in dec_down["message"]

    def test_agent_hook_exists(self):
        src = (ROOT / "tools" / "autoos-agent.py").read_text(encoding="utf-8")
        assert "fallback_decision(" in src
        agent_mod = importlib.import_module("autoos-agent")
        assert getattr(agent_mod, "EXIT_OC_FALLBACK_DOWN", None) == 15
        assert oc_fallback.EXIT_OC_FALLBACK_DOWN == 15


if __name__ == "__main__":
    unittest.main()
