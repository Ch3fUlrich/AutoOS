"""host.docker.internal must only count as 'resolving' when it points at this machine.

Measured 2026-10-14: on a native-Windows host without Docker Desktop the name
resolved through public DNS to an external address. A native consumer (agent-canvas)
kept the name and would have sent the gateway key to that stranger.
"""
import importlib.util
import os
import socket
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "sync_openhands_profiles",
    Path(__file__).resolve().parents[1] / "tools" / "sync-openhands-profiles.py",
)
sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync)


def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


class HdiResolvesTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AUTOOS_FAKE_HDI_RESOLVES", None)

    def _resolves(self, *ips):
        with mock.patch.object(socket, "getaddrinfo", return_value=_addrinfo(*ips)):
            return sync.container_host_resolves()

    def test_public_address_does_not_count(self):
        self.assertFalse(self._resolves("131.152.12.162"))

    def test_loopback_counts(self):
        self.assertTrue(self._resolves("127.0.0.1"))

    def test_docker_desktop_private_address_counts(self):
        self.assertTrue(self._resolves("192.168.65.254"))

    def test_native_consumer_gets_loopback_when_name_is_public(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=_addrinfo("131.152.12.162")):
            out = sync.for_consumer("http://host.docker.internal:20128/v1", "native")
        self.assertEqual(out, "http://127.0.0.1:20128/v1")

    def test_unresolvable_does_not_count(self):
        with mock.patch.object(socket, "getaddrinfo", side_effect=OSError):
            self.assertFalse(sync.container_host_resolves())


class LiveWindowTests(unittest.TestCase):
    LIVE = [
        {"id": "l1-orchestrator", "context_length": 200000},
        {"id": "l3-driver", "context_length": 131072},
        {"id": "l9-unlisted-bigger", "context_length": 999999},
    ]

    def _tiers(self):
        return [
            {"id": "omniroute-l1-orchestrator", "model": "openai/l1-orchestrator",
             "max_input_tokens": 1000000, "max_output_tokens": 32768},
            {"id": "omniroute-l3-driver", "model": "openai/l3-driver",
             "max_input_tokens": 128000, "max_output_tokens": 32768},
            {"id": "omniroute-unknown", "model": "openai/not-served",
             "max_input_tokens": 50000, "max_output_tokens": 8192},
        ]

    def test_window_follows_live_row_both_ways(self):
        out = sync.clamp_to_live_windows(self._tiers(), self.LIVE)
        by = {t["id"]: t["max_input_tokens"] for t in out}
        self.assertEqual(by["omniroute-l1-orchestrator"], 200000)   # shrunk
        self.assertEqual(by["omniroute-l3-driver"], 131072)         # grown to the real one

    def test_unlisted_model_keeps_spec_value(self):
        out = sync.clamp_to_live_windows(self._tiers(), self.LIVE)
        self.assertEqual(out[2]["max_input_tokens"], 50000)

    def test_input_is_not_mutated(self):
        tiers = self._tiers()
        sync.clamp_to_live_windows(tiers, self.LIVE)
        self.assertEqual(tiers[0]["max_input_tokens"], 1000000)

    def test_output_never_exceeds_window(self):
        live = [{"id": "l3-driver", "context_length": 16000}]
        out = sync.clamp_to_live_windows(self._tiers(), live)
        self.assertLessEqual(out[1]["max_output_tokens"], 16000 // 2)


if __name__ == "__main__":
    unittest.main()

