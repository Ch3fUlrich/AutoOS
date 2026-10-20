"""tests/test_fleet_bus.py - tailnet-only relay listener (D-2024 OCGO-FLEET-BUS).

unittest, stdlib only. The server runs in-process on 127.0.0.1 with an
ephemeral port; http.client drives the contract. No live network, no secrets:
the HMAC key in these tests is a fixed NOT-A-REAL-SECRET byte string.
"""

import hashlib
import hmac
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from collections import OrderedDict
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fleet_bus  # noqa: E402

FAKE_SECRET = b"NOT-A-REAL-SECRET-fleet-bus-test-only"


def post_inbox(port, body, sig=None, extra_headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if sig is not None:
        headers["X-Fleet-Sig"] = sig
    headers.update(extra_headers or {})
    conn.request("POST", "/inbox", body=json.dumps(body), headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def get_outbox(port, since=""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/outbox?since=%s" % since)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return resp.status, data


def good_body(frm="workst", to="coding", text="hello", ts=None, nonce="n1"):
    return {"from": frm, "to": to, "ts": ts if ts is not None else int(time.time()),
            "nonce": nonce, "text": text}


class FleetBusTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_test_")
        self.td = Path(self._td.name)
        self.server = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0),
            secret=FAKE_SECRET, logdir=str(self.td / "logs"))
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_until,
                                        kwargs={"deadline": None}, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=10)
        self._td.cleanup()

    def _sig(self, body):
        return fleet_bus.sign(FAKE_SECRET, body["ts"], body["nonce"],
                              body["from"], body["to"], body["text"])

    # (1) the contract: signed POST lands, poll returns it with text
    def test_signed_post_is_stored_and_polled_with_text(self):
        body = good_body()
        status, data = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 200, data)
        receipt = json.loads(data.decode("utf-8"))
        self.assertIn("id", receipt)
        status, out = get_outbox(self.port, since="")
        self.assertEqual(status, 200)
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["text"], "hello")
        self.assertEqual(out["cursor"], receipt["id"])
        # second poll with the cursor is empty: no round-trip wasted
        status, out2 = get_outbox(self.port, since=receipt["id"])
        self.assertEqual(out2["records"], [])

    # (2) integrity: bad signature and tampered text are refused
    def test_bad_signature_is_refused(self):
        body = good_body()
        status, _ = post_inbox(self.port, body, "0" * 64)
        self.assertEqual(status, 401)

    def test_tampered_text_is_refused(self):
        body = good_body(text="hello")
        sig = self._sig(body)
        body["text"] = "HELLO vandal"
        status, _ = post_inbox(self.port, body, sig)
        self.assertEqual(status, 401)

    # (3) freshness: 60 s skew window, replayed nonces refused
    def test_stale_timestamp_is_refused(self):
        body = good_body(ts=int(time.time()) - 120)
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 401)

    def test_future_timestamp_is_refused(self):
        body = good_body(ts=int(time.time()) + 120)
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 401)

    def test_replayed_nonce_is_refused(self):
        body = good_body(nonce="once-only")
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 200)
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 401)

    # (4) limits: 4 KB body cap, text scrubbed to one line <= 2000 chars
    def test_oversize_body_is_refused(self):
        body = good_body(text="x" * 5000)
        big = json.dumps(body)
        self.assertGreater(len(big.encode("utf-8")), 4096)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/inbox", body=big,
                     headers={"Content-Type": "application/json",
                              "X-Fleet-Sig": "0" * 64})
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()

    def test_text_is_scrubbed_to_one_line(self):
        body = good_body(text="line one\nline two\r\nline three" + "y" * 3000)
        status, data = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 200)
        _, out = get_outbox(self.port, since="")
        stored = out["records"][0]["text"]
        self.assertNotIn("\n", stored)
        self.assertLessEqual(len(stored), 2000)

    def test_malformed_body_is_refused(self):
        status, _ = post_inbox(self.port, {"from": "a"}, "0" * 64)
        self.assertEqual(status, 400)

    # (5) privacy: the jsonl log carries records WITHOUT text
    def test_log_records_carry_no_text(self):
        body = good_body(text="secret-words-that-must-not-land-in-logs")
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 200)
        logs = list((self.td / "logs").rglob("*.jsonl"))
        self.assertTrue(logs, "the server must append a metadata log line")
        logged = "".join(p.read_text(encoding="utf-8") for p in logs)
        self.assertNotIn("secret-words-that-must-not-land-in-logs", logged)
        row = json.loads(logged.strip().splitlines()[-1])
        self.assertNotIn("text", row)
        for key in ("id", "ts", "from", "to"):
            self.assertIn(key, row)

    # (6) cursor: unknown since returns everything buffered
    def test_unknown_cursor_returns_everything(self):
        for i, nonce in enumerate(("a", "b")):
            body = good_body(text="m%d" % i, nonce=nonce)
            post_inbox(self.port, body, self._sig(body))
        _, out = get_outbox(self.port, since="no-such-id")
        self.assertEqual([r["text"] for r in out["records"]], ["m0", "m1"])

    # (7) open mode: no secret file, no signature, probe-only
    def test_open_mode_accepts_unsigned_posts(self):
        srv = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0),
            secret=None, logdir=str(self.td / "open-logs"))
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_until,
                                  kwargs={"deadline": None}, daemon=True)
        thread.start()
        try:
            status, _ = post_inbox(port, good_body(), None)
            self.assertEqual(status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=10)


class BindConfigTest(unittest.TestCase):
    """The public tree must not carry a site address as the relay default.

    The live relay still binds to the tailnet address, but that value comes
    from the host environment (`FLEET_BUS_BIND`) or the `--bind` flag, never
    from a literal in this public repository (AutoOS Hard Rule 1).
    """

    def test_default_bind_is_loopback_not_site_shaped(self):
        self.assertEqual(fleet_bus.DEFAULT_BIND, "127.0.0.1")
        self.assertEqual(fleet_bus.bind_default({}), "127.0.0.1")

    def test_env_supplies_the_bind_address(self):
        self.assertEqual(
            fleet_bus.bind_default({"FLEET_BUS_BIND": "127.0.0.2"}),
            "127.0.0.2",
        )

    def test_an_empty_env_falls_back_to_loopback(self):
        # A set-but-empty systemd Environment= line must not become "" —
        # HTTPServer binds "" to INADDR_ANY, which would expose the relay.
        self.assertEqual(fleet_bus.bind_default({"FLEET_BUS_BIND": ""}), "127.0.0.1")
        self.assertEqual(fleet_bus.bind_default({"FLEET_BUS_BIND": "   "}), "127.0.0.1")

    def test_cli_default_reads_the_environment(self):
        args = fleet_bus.build_parser({"FLEET_BUS_BIND": "127.0.0.3"}).parse_args([])
        self.assertEqual(args.bind, "127.0.0.3")
        self.assertEqual(fleet_bus.build_parser({}).parse_args([]).bind, "127.0.0.1")

    def test_an_explicit_bind_beats_the_environment(self):
        args = fleet_bus.build_parser({"FLEET_BUS_BIND": "127.0.0.3"}).parse_args(
            ["--bind", "127.0.0.4"])
        self.assertEqual(args.bind, "127.0.0.4")


if __name__ == "__main__":
    unittest.main()
