"""tests/test_fleet_bus.py - tailnet-only relay listener (D-2024 OCGO-FLEET-BUS).

unittest, stdlib only. The server runs in-process on 127.0.0.1 with an
ephemeral port; http.client drives the contract. The keeper hosts registry is
mocked with a local http.server on 127.0.0.1 (ephemeral port) - no live
network anywhere. No secrets: the HMAC keys and the keeper token in these
tests are fixed NOT-A-REAL-* strings, and every name that is not loopback is
an example.invalid placeholder (public repo, AutoOS Hard Rule 1).
"""

import contextlib
import hashlib
import hmac
import http.client
import http.server
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from collections import OrderedDict
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fleet_bus  # noqa: E402

FAKE_SECRET = b"NOT-A-REAL-SECRET-fleet-bus-test-only"
FAKE_SECRET_OTHER = b"NOT-A-REAL-SECRET-other-host-test-only"
FAKE_TOKEN = "NOT-A-REAL-KEEPER-TOKEN"


def write_key(path, value):
    """Write a 0600 key file the way the fleet ships them; return its path."""
    path = Path(path)
    path.write_text(value + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)
    return str(path)


def write_inventory(path, hosts):
    """Write the per-host inventory JSON {"hosts": {host: {"key_file": ...}}}."""
    doc = {"hosts": {h: {"key_file": k} for h, k in hosts.items()}}
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


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


class KeeperHandler(http.server.BaseHTTPRequestHandler):
    """Mock keeper: answers every GET with the fixture's (status, body).

    A fixture that sets `location` answers a redirect, the way a compromised
    or misconfigured keeper would try to move the reader (and its bearer
    token) somewhere else.
    """

    def do_GET(self):  # noqa: N802 (http.server names the method)
        status, payload = self.server.answer
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if getattr(self.server, "location", None):
            self.send_header("Location", self.server.location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        pass


def start_keeper(answer, location=None):
    """A local mock of GET <keeper>/v1/hosts on loopback, ephemeral port."""
    server = http.server.HTTPServer(("127.0.0.1", 0), KeeperHandler)
    server.answer = answer
    server.location = location
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Answers GET with the fixture's JSON and records every request seen."""

    def do_GET(self):  # noqa: N802 (http.server names the method)
        self.server.seen.append(self.path)
        body = self.server.answer.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        pass


def start_recorder(answer):
    """A second loopback server that counts the requests it receives."""
    server = http.server.HTTPServer(("127.0.0.1", 0), RecordingHandler)
    server.answer = answer
    server.seen = []
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return (server, "http://127.0.0.1:%d" % server.server_address[1],
            server.seen)


def start_broken_keeper():
    """A loopback port that answers with a non-HTTP status line.

    http.client raises BadStatusLine (an http.client.HTTPException) on it,
    which is exactly the class of keeper failure that used to escape as a
    traceback instead of a one-line fail-closed error.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(65536)
                    conn.sendall(b"NOT-HTTP-AT-ALL\r\n\r\n")
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return listener, "http://127.0.0.1:%d" % listener.getsockname()[1]


def closed_loopback_url():
    """A loopback URL with nothing listening on it (connection refused)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return "http://127.0.0.1:%d" % port


class FleetBusTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_test_")
        self.td = Path(self._td.name)
        self.server = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0),
            keys=fleet_bus.Keyring(override=FAKE_SECRET),
            logdir=str(self.td / "logs"))
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_until,
                                        kwargs={"deadline": None}, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=10)
        self._td.cleanup()

    def _sig(self, body, secret=FAKE_SECRET):
        return fleet_bus.sign(secret, body["ts"], body["nonce"],
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

    # fix round 3 item 1: a non-finite or unrepresentable ts must be the same
    # clean refusal as any malformed field - `int(float("1e400"))` raises
    # OverflowError, which used to escape do_POST and drop the connection
    def test_a_non_finite_timestamp_is_refused_not_a_traceback(self):
        for ts in ("1e400", "1e309", '"inf"', '"-inf"', '"nan"', '"1e400"',
                   "1" + "0" * 400):
            raw = ('{"from": "workst", "to": "coding", "ts": %s, '
                   '"nonce": "n-nonfinite", "text": "hello"}' % ts)
            with self.subTest(ts=ts):
                conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                                  timeout=10)
                conn.request("POST", "/inbox", body=raw,
                             headers={"Content-Type": "application/json",
                                      "X-Fleet-Sig": "0" * 64})
                resp = conn.getresponse()
                status, data = resp.status, resp.read()
                conn.close()
                self.assertIn(status, (400, 401), data)

    # fix round 4 item 3: a lone surrogate in the text ("\ud800") cannot be
    # UTF-8-encoded, and the encode inside the canonical form used to raise
    # UnicodeEncodeError straight through do_POST - a traceback on stderr and
    # a dropped connection. It is a 400 refusal like any malformed body.
    def test_a_lone_surrogate_in_the_text_is_a_400_not_a_traceback(self):
        for i, text in enumerate(("\ud800", "a\ud800b", "x\udc00")):
            body = good_body(text=text, nonce="surrogate-%d" % i)
            with self.subTest(text=repr(text)):
                status, data = post_inbox(self.port, body, "f" * 64)
                self.assertEqual(status, 400, data)
                self.assertEqual(json.loads(data.decode("utf-8")),
                                 {"error": "bad text"})
        # the server still serves the next request
        body = good_body(text="after-surrogate", nonce="after-surrogate")
        status, _ = post_inbox(self.port, body, self._sig(body))
        self.assertEqual(status, 200)

    def test_check_post_refuses_unencodable_fields_before_signing(self):
        # every request-controlled string must be UTF-8-encodable before the
        # signature is computed over it - that encode is what used to escape
        # as a traceback through do_POST
        keys = fleet_bus.Keyring(override=FAKE_SECRET)
        for field, value in (("text", "\ud800"), ("text", "a\udc00b"),
                             ("from", "\ud800"), ("to", "\ud800"),
                             ("nonce", "\ud800")):
            body = good_body(nonce="unit-surrogate")
            body[field] = value
            with self.subTest(field=field, value=repr(value)):
                with self.assertRaises(fleet_bus.FleetBusError) as caught:
                    fleet_bus.check_post(keys, body, "0" * 64,
                                         time.time(), OrderedDict())
                self.assertEqual(caught.exception.status, 400,
                                 caught.exception.reason)

    def test_a_non_ascii_signature_is_a_401_not_a_traceback(self):
        # hmac.compare_digest refuses non-ASCII strings with a TypeError,
        # which used to escape do_POST as a traceback: garbage X-Fleet-Sig
        # values are bad signatures, and a bad signature is a 401
        body = good_body(nonce="garbage-sig")
        status, data = post_inbox(self.port, body, "\u00ff" * 64)
        self.assertEqual(status, 401, data)
        self.assertEqual(json.loads(data.decode("utf-8")),
                         {"error": "unauthorized"})

    # fix round 3 item 2: a negative Content-Length passes the size cap and
    # rfile.read(-n) then blocks to EOF - it is refused with a 400 instead
    def test_a_negative_content_length_is_refused(self):
        for value in ("-1", "-4096"):
            with self.subTest(length=value):
                conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                                  timeout=3)
                try:
                    conn.request(
                        "POST", "/inbox", body="",
                        headers={"Content-Type": "application/json",
                                 "Content-Length": value,
                                 "X-Fleet-Sig": "0" * 64})
                    resp = conn.getresponse()
                    status, data = resp.status, resp.read()
                finally:
                    conn.close()
                self.assertEqual(status, 400, data)

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

    # (7) open mode: no key source, no signature, probe-only
    def test_open_mode_accepts_unsigned_posts(self):
        srv = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0),
            keys=None, logdir=str(self.td / "open-logs"))
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


class PerHostKeyTest(unittest.TestCase):
    """Per-host key files replace the shared fleet-relay-secret.

    The inventory maps each sending host to its own 0600 key file; the POST
    is verified with the key of the envelope's "from" host. The shared
    --secret-file stays only as an explicit single-key override.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_keys_")
        self.td = Path(self._td.name)
        self.key_a = write_key(self.td / "workst.key", FAKE_SECRET.decode("utf-8"))
        self.key_b = write_key(self.td / "coding.key",
                               FAKE_SECRET_OTHER.decode("utf-8"))
        self.inventory = {"workst": self.key_a, "coding": self.key_b}
        self.addCleanup(self._td.cleanup)

    def start_server(self, keys):
        server = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0), keys=keys, logdir=str(self.td / "logs"))
        thread = threading.Thread(target=server.serve_until,
                                  kwargs={"deadline": None}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, server.server_address[1]

    def sig(self, body, secret):
        return fleet_bus.sign(secret, body["ts"], body["nonce"],
                              body["from"], body["to"], body["text"])

    def logged(self):
        """Every metadata jsonl row the server wrote, refusals included."""
        rows = []
        for path in sorted((self.td / "logs").rglob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                rows.append(json.loads(line))
        return rows

    def refusal_reasons(self):
        return " ".join(row.get("reason", "") for row in self.logged()
                        if row.get("event") == "refused")

    def test_each_sender_is_verified_with_its_own_key(self):
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        body = good_body(frm="workst", nonce="own-key")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 200)
        # the other host's key must not open this sender's envelope
        body = good_body(frm="workst", nonce="wrong-key")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET_OTHER))
        self.assertEqual(status, 401)
        body = good_body(frm="coding", nonce="coding-key")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET_OTHER))
        self.assertEqual(status, 200)

    def test_an_unknown_from_host_gets_a_generic_401_body(self):
        # fix round 2 item 4: the wire body must not enumerate host names;
        # the full reason goes to the metadata-only jsonl log
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        body = good_body(frm="stranger", nonce="stranger")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        wire = data.decode("utf-8")
        self.assertEqual(json.loads(wire), {"error": "unauthorized"})
        self.assertNotIn("stranger", wire)
        self.assertIn("stranger", self.refusal_reasons())

    def test_key_files_are_read_once(self):
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        body = good_body(frm="workst", nonce="first-read")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 200)
        # the file changed underneath: the key was read once, so the cached
        # one still verifies and the new one does not
        write_key(self.key_a, FAKE_SECRET_OTHER.decode("utf-8"))
        body = good_body(frm="workst", nonce="second-read")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 200)
        body = good_body(frm="workst", nonce="rotated-read")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET_OTHER))
        self.assertEqual(status, 401)

    @unittest.skipIf(os.name == "nt", "file modes are not enforced on Windows")
    def test_a_world_readable_key_file_is_refused(self):
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        os.chmod(self.key_a, 0o644)
        body = good_body(frm="workst", nonce="loose-mode")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        wire = data.decode("utf-8")
        self.assertEqual(json.loads(wire), {"error": "unauthorized"})
        self.assertNotIn(self.key_a, wire)                       # no path
        self.assertNotIn("workst", wire)                         # no host
        self.assertNotIn(FAKE_SECRET.decode("utf-8"), wire)      # never contents
        reason = self.refusal_reasons()
        self.assertIn("workst", reason)      # the log names the host
        self.assertIn(self.key_a, reason)    # and the path
        self.assertNotIn(FAKE_SECRET.decode("utf-8"), reason)  # never contents

    def test_a_missing_key_file_names_the_path_and_host(self):
        inventory = {"workst": str(self.td / "gone.key")}
        _, port = self.start_server(fleet_bus.Keyring(inventory=inventory))
        body = good_body(frm="workst", nonce="missing-file")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        wire = data.decode("utf-8")
        self.assertEqual(json.loads(wire), {"error": "unauthorized"})
        self.assertNotIn("gone.key", wire)   # the wire names nothing
        self.assertNotIn("workst", wire)
        reason = self.refusal_reasons()
        self.assertIn("workst", reason)      # the log does name both
        self.assertIn("gone.key", reason)

    def test_a_bad_signature_gets_the_same_generic_401_body(self):
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        body = good_body(frm="workst", nonce="bad-sig")
        status, data = post_inbox(port, body, "0" * 64)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(data.decode("utf-8")),
                         {"error": "unauthorized"})
        self.assertIn("signature", self.refusal_reasons())

    @unittest.skipIf(os.name == "nt", "file modes are not enforced on Windows")
    def test_a_non_utf8_key_file_is_a_401_not_a_dropped_connection(self):
        # fix round 2 item 2: UnicodeDecodeError from the key file must not
        # escape as a traceback and kill the connection mid-request
        binary = self.td / "binary.key"
        binary.write_bytes(b"\xff\xfe not utf-8\n")
        if os.name != "nt":
            os.chmod(binary, 0o600)
        _, port = self.start_server(
            fleet_bus.Keyring(inventory={"workst": str(binary)}))
        body = good_body(frm="workst", nonce="binary-key")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(data.decode("utf-8")),
                         {"error": "unauthorized"})
        self.assertIn(str(binary), self.refusal_reasons())

    @unittest.skipIf(os.name == "nt", "file modes are not enforced on Windows")
    def test_key_for_turns_a_decode_error_into_a_refusal(self):
        binary = self.td / "binary-unit.key"
        binary.write_bytes(b"\xff\xfe not utf-8\n")
        if os.name != "nt":
            os.chmod(binary, 0o600)
        keyring = fleet_bus.Keyring(inventory={"workst": str(binary)})
        with self.assertRaises(fleet_bus.FleetBusError) as caught:
            keyring.key_for("workst")
        self.assertEqual(caught.exception.status, 401)
        self.assertIn(str(binary), caught.exception.reason)

    def test_the_secret_file_override_stays_one_shared_key(self):
        # an explicit single key: any sender, one key, no inventory lookup
        keys = fleet_bus.Keyring(override=FAKE_SECRET,
                                 inventory={"workst": self.key_b})
        _, port = self.start_server(keys)
        body = good_body(frm="stranger", nonce="override")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 200)
        body = good_body(frm="workst", nonce="override-wrong")
        status, _ = post_inbox(port, body, self.sig(body, FAKE_SECRET_OTHER))
        self.assertEqual(status, 401)


class InventoryConfigTest(unittest.TestCase):
    """The inventory JSON and its CLI/env plumbing."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_inv_")
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_load_inventory_maps_host_to_key_file(self):
        path = write_inventory(self.td / "inventory.json",
                               {"workst": "/keys/workst.key"})
        self.assertEqual(fleet_bus.load_inventory(path),
                         {"workst": "/keys/workst.key"})

    def test_a_malformed_inventory_names_the_path(self):
        for name, text in (("list.json", "[]"),
                           ("no-hosts.json", '{"hosts": 1}'),
                           ("no-key-file.json", '{"hosts": {"a": {}}}'),
                           ("broken.json", "{not json")):
            path = self.td / name
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(fleet_bus.FleetBusError) as caught:
                fleet_bus.load_inventory(str(path))
            self.assertEqual(caught.exception.status, 2)
            self.assertIn(str(path), caught.exception.reason)

    def test_env_supplies_inventory_host_and_keeper_settings(self):
        args = fleet_bus.build_parser({
            "FLEET_BUS_INVENTORY": "/etc/fleet/inventory.json",
            "FLEET_BUS_HOST": "workst",
            "FLEET_KEEPER_URL": "https://keeper.example.invalid",
            "FLEET_KEEPER_TOKEN_FILE": "/etc/fleet/keeper-token",
        }).parse_args([])
        self.assertEqual(args.inventory, "/etc/fleet/inventory.json")
        self.assertEqual(args.host, "workst")
        self.assertEqual(args.keeper_url, "https://keeper.example.invalid")
        self.assertEqual(args.keeper_token_file, "/etc/fleet/keeper-token")

    def test_main_requires_a_key_source_unless_open(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fleet_bus.main([], env={})
        self.assertEqual(rc, 2)
        self.assertIn("--inventory", out.getvalue())

    def test_secret_file_with_inventory_is_refused(self):
        # one key source: the shared --secret-file must not silently win
        path = write_inventory(self.td / "both.json",
                               {"workst": "/keys/workst.key"})
        secret = write_key(self.td / "fleet.key", FAKE_SECRET.decode("utf-8"))
        args = fleet_bus.build_parser({}).parse_args(
            ["--inventory", path, "--secret-file", secret])
        with self.assertRaises(fleet_bus.FleetBusError) as caught:
            fleet_bus.build_keys(args)
        error = caught.exception
        self.assertEqual(error.status, 2, error.reason)
        self.assertNotIn("\n", error.reason)
        self.assertIn("--inventory", error.reason)
        self.assertIn("--secret-file", error.reason)
        self.assertNotIn(FAKE_SECRET.decode("utf-8"), error.reason)

    def test_main_refuses_secret_file_with_inventory_on_one_line(self):
        path = write_inventory(self.td / "both-main.json",
                               {"workst": "/keys/workst.key"})
        secret = write_key(self.td / "fleet-main.key",
                           FAKE_SECRET.decode("utf-8"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fleet_bus.main(
                ["--inventory", path, "--secret-file", secret], env={})
        self.assertEqual(rc, 2)
        line = out.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1, out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())
        self.assertIn("--inventory", line)
        self.assertIn("--secret-file", line)


class BindDiscoveryTest(unittest.TestCase):
    """No literal bind default: the keeper registry answers, or we fail closed.

    The bind address is this host's endpoint host part as the keeper hosts
    registry reports it; --bind / FLEET_BUS_BIND stays the explicit
    transition override and is the only way to bind loopback. Discovery that
    cannot answer fails closed with exit code 3 and one line - it never falls
    back to an IP or loopback guess (AutoOS Hard Rule 1).
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_bind_")
        self.td = Path(self._td.name)
        self.token_file = write_key(self.td / "keeper-token", FAKE_TOKEN)
        self.addCleanup(self._td.cleanup)
        self._keepers = []
        self.addCleanup(self._stop_keepers)

    def _stop_keepers(self):
        for server in self._keepers:
            server.shutdown()
            server.server_close()

    def keeper(self, answer):
        server, url = start_keeper(answer)
        self._keepers.append(server)
        return url

    def keeper_with_location(self, answer, location):
        server, url = start_keeper(answer, location=location)
        self._keepers.append(server)
        return server, url

    @staticmethod
    def hosts_doc(hosts):
        return (200, json.dumps({"hosts": hosts}))

    @staticmethod
    def entry(host, endpoint, health="healthy"):
        return {"host": host, "endpoint": endpoint, "key_id": "k-%s" % host,
                "health": health, "last_seen": 1761000000}

    def assert_fails_closed(self, call, *names):
        """The call raises a one-line status-3 error naming each `names`."""
        with self.assertRaises(fleet_bus.FleetBusError) as caught:
            call()
        error = caught.exception
        self.assertEqual(error.status, 3, error.reason)
        self.assertNotIn("\n", error.reason)
        self.assertNotIn("Traceback", error.reason)
        for name in names:
            self.assertIn(name, error.reason)
        return error

    # (1) the explicit override stays opt-in, and there is no literal default
    # (192.0.2.x here is the RFC 5737 documentation placeholder, never a site)
    def test_the_bind_override_stays_explicit(self):
        self.assertEqual(fleet_bus.bind_override({"FLEET_BUS_BIND": "192.0.2.10"}),
                         "192.0.2.10")
        self.assertIsNone(fleet_bus.bind_override({}))
        self.assertIsNone(fleet_bus.bind_override({"FLEET_BUS_BIND": "   "}))
        args = fleet_bus.build_parser({"FLEET_BUS_BIND": "192.0.2.11"}).parse_args([])
        self.assertEqual(args.bind, "192.0.2.11")
        args = fleet_bus.build_parser({"FLEET_BUS_BIND": "192.0.2.11"}).parse_args(
            ["--bind", "192.0.2.12"])
        self.assertEqual(args.bind, "192.0.2.12")

    def test_there_is_no_literal_default_bind(self):
        self.assertFalse(hasattr(fleet_bus, "DEFAULT_BIND"))
        self.assertIsNone(fleet_bus.build_parser({}).parse_args([]).bind)

    # (2) discovery: the keeper endpoint's host part is the bind address
    def test_the_keeper_endpoint_host_part_supplies_the_bind(self):
        url = self.keeper(self.hosts_doc([
            self.entry("workst", "https://relay-a.example.invalid:58690"),
        ]))
        self.assertEqual(
            fleet_bus.discover_bind("workst", url, self.token_file),
            "relay-a.example.invalid")

    def test_a_bare_host_port_endpoint_is_parsed(self):
        url = self.keeper(self.hosts_doc([
            self.entry("workst", "relay-b.example.invalid:58690"),
            self.entry("coding", "https://relay-c.example.invalid:58690/inbox"),
        ]))
        self.assertEqual(
            fleet_bus.discover_bind("workst", url, self.token_file),
            "relay-b.example.invalid")
        self.assertEqual(
            fleet_bus.discover_bind("coding", url, self.token_file),
            "relay-c.example.invalid")

    # (3) fail closed, one line, exit 3 - never a guessed address
    def test_discovery_settings_have_no_literal_defaults(self):
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", "", self.token_file),
            "FLEET_KEEPER_URL")
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", "https://keeper.example.invalid",
                                            ""),
            "FLEET_KEEPER_TOKEN_FILE")
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("", "https://keeper.example.invalid",
                                            self.token_file),
            "FLEET_BUS_HOST")

    # fix round 4 item 1: a keeper URL that is not http(s) is refused at
    # config time. A scheme-less value makes Request(...) itself raise
    # ValueError('unknown url type') outside the request try, and ftp:/file:/
    # data: URLs are not keeper reads at all (urllib's own handlers would
    # answer them) - all of it must be the one-line fail-closed status-3
    # error, never a traceback and never a request.
    def test_a_keeper_url_without_a_scheme_fails_closed(self):
        for url in ("keeper.example.invalid:8765", "keeper.example.invalid",
                    "keeper.example.invalid:8765/base"):
            with self.subTest(url=url):
                self.assert_fails_closed(
                    lambda: fleet_bus.discover_bind("workst", url,
                                                    self.token_file),
                    "keeper", "http")

    def test_a_keeper_url_scheme_other_than_http_fails_closed(self):
        for url in ("ftp://keeper.example.invalid",
                    "file:///etc/hosts",
                    "data:text/plain,NOT-A-REAL-KEEPER"):
            with self.subTest(url=url):
                self.assert_fails_closed(
                    lambda: fleet_bus.discover_bind("workst", url,
                                                    self.token_file),
                    "keeper", "http")

    def test_main_refuses_a_keeper_url_that_is_not_http_on_one_line(self):
        for url in ("keeper.example.invalid:8765", "keeper.example.invalid",
                    "ftp://keeper.example.invalid", "file:///etc/hosts",
                    "data:text/plain,NOT-A-REAL-KEEPER"):
            out = io.StringIO()
            with self.subTest(url=url), contextlib.redirect_stdout(out):
                rc = fleet_bus.main(["--open", "--host", "workst",
                                     "--keeper-url", url,
                                     "--keeper-token-file", self.token_file],
                                    env={})
            self.assertEqual(rc, 3, out.getvalue())
            line = out.getvalue().strip()
            self.assertEqual(len(line.splitlines()), 1, out.getvalue())
            self.assertNotIn("Traceback", line)
            self.assertIn("http", line)

    def test_a_request_value_error_fails_closed(self):
        # defense in depth: whatever makes Request(...) itself raise
        # ValueError ('unknown url type') must be the same one-line
        # fail-closed error as every other keeper failure
        url = self.keeper(self.hosts_doc([]))
        with mock.patch.object(urllib.request, "Request",
                               side_effect=ValueError("unknown url type")):
            self.assert_fails_closed(
                lambda: fleet_bus.discover_bind("workst", url, self.token_file),
                "keeper")

    def test_an_unhealthy_host_fails_closed(self):
        url = self.keeper(self.hosts_doc([
            self.entry("workst", None, health="degraded"),
        ]))
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "workst")

    def test_an_unknown_host_fails_closed(self):
        url = self.keeper(self.hosts_doc([
            self.entry("other", "https://relay-a.example.invalid:58690"),
        ]))
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "workst")

    def test_a_null_endpoint_fails_closed(self):
        url = self.keeper(self.hosts_doc([
            self.entry("workst", None),
        ]))
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "workst")

    def test_keeper_unreachable_fails_closed(self):
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", closed_loopback_url(),
                                            self.token_file),
            "keeper")

    def test_keeper_non_200_fails_closed(self):
        url = self.keeper((500, '{"error": "boom"}'))
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "500")

    def test_keeper_bad_json_fails_closed(self):
        for payload in ("not json at all", '{"hosts": {"a": 1}}', "[]"):
            url = self.keeper((200, payload))
            self.assert_fails_closed(
                lambda: fleet_bus.discover_bind("workst", url, self.token_file),
                "keeper")

    # fix round 2 item 2: no traceback paths out of the keeper read
    def test_a_keeper_answering_garbage_fails_closed(self):
        # http.client raises BadStatusLine (an HTTPException) on this one
        listener, url = start_broken_keeper()
        self.addCleanup(listener.close)
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "keeper")

    def test_a_malformed_keeper_endpoint_fails_closed(self):
        # urlparse's ValueError (unbalanced brackets) is one line, status 3
        for endpoint in ("http://[::1", "[::1", "http://relay-a.example.invalid:[7"):
            with self.subTest(endpoint=endpoint):
                url = self.keeper(self.hosts_doc([self.entry("workst", endpoint)]))
                self.assert_fails_closed(
                    lambda: fleet_bus.discover_bind("workst", url, self.token_file),
                    "malformed")

    @unittest.skipIf(os.name == "nt", "file modes are not enforced on Windows")
    def test_a_non_utf8_keeper_token_fails_closed(self):
        binary = self.td / "binary-token"
        binary.write_bytes(b"\xff\xfe not utf-8\n")
        if os.name != "nt":
            os.chmod(binary, 0o600)
        url = self.keeper(self.hosts_doc([]))
        error = self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, str(binary)),
            "binary-token")
        self.assertNotIn("Traceback", error.reason)

    # fix round 2 item 3: a 3xx is an answer, not an invitation - the
    # Bearer token must never be re-sent to whatever Location names
    def test_a_keeper_redirect_is_refused_and_never_followed(self):
        target, target_url, seen = start_recorder(self.hosts_doc([
            self.entry("workst", "http://127.0.0.1:58690")])[1])
        self.addCleanup(target.server_close)
        self.addCleanup(target.shutdown)
        keeper, url = self.keeper_with_location(
            (302, ""), target_url + "/v1/hosts")
        self.addCleanup(keeper.server_close)
        self.addCleanup(keeper.shutdown)
        self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, self.token_file),
            "302")
        self.assertEqual(seen, [],
                         "the redirect target must see no request at all")

    # fix round 3 item 3: the refused redirect's response is closed, not left
    # dangling with an open fp
    def test_a_refused_redirect_closes_the_response_it_refused(self):
        class FakeFP:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        fp = FakeFP()
        request = urllib.request.Request("https://keeper.example.invalid/v1/hosts")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            fleet_bus._RefuseRedirects().redirect_request(
                request, fp, 302, "Found", {},
                "https://elsewhere.example.invalid/v1/hosts")
        self.assertTrue(fp.closed)
        # the refused response is gone from the error (a None fp becomes an
        # inert BytesIO in urllib.error.HTTPError, never the open socket)
        self.assertIsNot(caught.exception.fp, fp)

    # fix round 3 item 3: the keeper read goes DIRECT - an http(s)_proxy in
    # the environment must never route the Bearer token through a third host
    def test_the_keeper_read_ignores_a_proxy_environment(self):
        dead = closed_loopback_url()
        url = self.keeper(self.hosts_doc([
            self.entry("workst", "http://127.0.0.1:58690"),
        ]))
        with mock.patch.dict(os.environ, {
                "http_proxy": dead, "https_proxy": dead, "all_proxy": dead,
                "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
                "no_proxy": "", "NO_PROXY": ""}):
            self.assertEqual(
                fleet_bus.discover_bind("workst", url, self.token_file),
                "127.0.0.1")

    def test_the_token_is_never_printed_and_stays_0600(self):
        url = self.keeper(self.hosts_doc([]))
        error = self.assert_fails_closed(
            lambda: fleet_bus.discover_bind("workst", url, str(self.td / "gone")),
            "gone")
        self.assertNotIn(FAKE_TOKEN, error.reason)
        if os.name != "nt":
            loose = write_key(self.td / "loose-token", FAKE_TOKEN)
            os.chmod(loose, 0o644)
            error = self.assert_fails_closed(
                lambda: fleet_bus.discover_bind("workst", url, loose),
                "loose-token")
            self.assertNotIn(FAKE_TOKEN, error.reason)

    # (4) the CLI wires it together: fail closed, and bind what discovery said
    def test_main_fails_closed_without_discovery_config(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fleet_bus.main(["--open"], env={})
        self.assertEqual(rc, 3)
        line = out.getvalue()
        self.assertNotIn("Traceback", line)
        self.assertIn("FLEET_KEEPER_URL", line)

    def test_main_binds_what_the_keeper_reports(self):
        url = self.keeper(self.hosts_doc([
            self.entry("workst", "http://127.0.0.1:58690"),
        ]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = fleet_bus.main([
                "--open", "--host", "workst", "--keeper-url", url,
                "--keeper-token-file", self.token_file,
                "--port", "0", "--ttl", "0.3",
                "--log-dir", str(self.td / "logs")], env={})
        self.assertEqual(rc, 0)
        self.assertIn("listening on 127.0.0.1:", err.getvalue())


class BindSafetyTest(unittest.TestCase):
    """Wildcard binds refused everywhere; a bind that cannot be taken fails closed.

    0.0.0.0, "::" and "" are the wildcard: HTTPServer would put the relay on
    every interface with its reads open. Every source - --bind, FLEET_BUS_BIND,
    or a discovered keeper endpoint - must refuse such a value instead of
    binding it. An address the OS will not hand out (unowned, unresolvable,
    wrong family) must be the same one-line fail-closed error naming the
    address, never a traceback. 192.0.2.x is the RFC 5737 documentation
    placeholder, never a site address.
    """

    # Every spelling the OS resolves to the all-zeros (wildcard) address.
    # The check must resolve the address, not compare strings: "0", "0.0",
    # "000.000.000.000" and "0x0" are 0.0.0.0 in inet_aton, and "::0" and
    # "0:0:0:0:0:0:0:0" are the IPv6 wildcard.
    WILDCARDS = ("0.0.0.0", "0", "0.0", "000.000.000.000", "0x0",
                 "::", "::0", "0:0:0:0:0:0:0:0", "")

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_safety_")
        self.td = Path(self._td.name)
        self.token_file = write_key(self.td / "keeper-token", FAKE_TOKEN)
        self.addCleanup(self._td.cleanup)

    def run_main(self, argv, env=None):
        """main() -> (rc, stdout, stderr). No traceback ever escapes."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fleet_bus.main(argv, env=env)
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        return rc, out.getvalue(), err.getvalue()

    def one_error_line(self, out_text):
        """stdout is exactly one line of JSON error output."""
        lines = out_text.strip().splitlines()
        self.assertEqual(len(lines), 1, out_text)
        self.assertIn('"ok": false', lines[0])
        return lines[0]

    def server_args(self):
        return ["--port", "0", "--ttl", "0.2",
                "--log-dir", str(self.td / "logs")]

    # (1) an address the OS refuses: one line, exit 3, never a traceback
    def test_binding_an_unowned_address_fails_with_one_line_error(self):
        rc, out, _ = self.run_main(
            ["--open", "--bind", "192.0.2.1"] + self.server_args(), env={})
        self.assertEqual(rc, 3)
        line = self.one_error_line(out)
        self.assertIn("192.0.2.1", line)  # names the address, not a secret

    # (2) the wildcard is refused from every source it can arrive through
    def test_an_explicit_wildcard_bind_is_refused(self):
        for value in self.WILDCARDS:
            with self.subTest(bind=repr(value)):
                rc, out, _ = self.run_main(
                    ["--open", "--bind", value] + self.server_args(), env={})
                self.assertEqual(rc, 2, out)
                self.assertIn("wildcard", self.one_error_line(out))

    def test_a_wildcard_bind_env_is_refused(self):
        # a set-but-blank FLEET_BUS_BIND is the wildcard "", not "no value":
        # it must never silently fall through to discovery
        for value in self.WILDCARDS + ("   ",):
            with self.subTest(env=repr(value)):
                rc, out, _ = self.run_main(
                    ["--open"] + self.server_args(),
                    env={"FLEET_BUS_BIND": value})
                self.assertEqual(rc, 2, out)
                self.assertIn("wildcard", self.one_error_line(out))

    def test_a_discovered_wildcard_endpoint_fails_closed(self):
        for endpoint in ("http://0.0.0.0:58690", "http://[::]:58690", ""):
            with self.subTest(endpoint=endpoint):
                keeper, url = start_keeper((200, json.dumps({"hosts": [
                    {"host": "workst", "endpoint": endpoint, "key_id": "k",
                     "health": "healthy", "last_seen": 1761000000}]})))
                try:
                    rc, out, _ = self.run_main(
                        ["--open", "--host", "workst", "--keeper-url", url,
                         "--keeper-token-file", self.token_file]
                        + self.server_args(), env={})
                finally:
                    keeper.shutdown()
                    keeper.server_close()
                self.assertEqual(rc, 3, out)
                self.one_error_line(out)

    def test_every_wildcard_spelling_is_refused_from_a_discovered_endpoint(self):
        for value in self.WILDCARDS:
            if not value:
                continue  # the null endpoint is its own fail-closed case above
            endpoint = ("http://[%s]:58690" % value if ":" in value
                        else "http://%s:58690" % value)
            with self.subTest(endpoint=endpoint):
                keeper, url = start_keeper((200, json.dumps({"hosts": [
                    {"host": "workst", "endpoint": endpoint, "key_id": "k",
                     "health": "healthy", "last_seen": 1761000000}]})))
                try:
                    rc, out, _ = self.run_main(
                        ["--open", "--host", "workst", "--keeper-url", url,
                         "--keeper-token-file", self.token_file]
                        + self.server_args(), env={})
                finally:
                    keeper.shutdown()
                    keeper.server_close()
                self.assertEqual(rc, 3, out)
                self.assertIn("wildcard", self.one_error_line(out))

    def test_wildcard_detection_resolves_the_address_not_the_string(self):
        for value in self.WILDCARDS:
            with self.subTest(value=value):
                self.assertTrue(fleet_bus.is_wildcard_bind(value))
        # 0x1 is the legacy spelling of the address one step past the
        # wildcard (0x0): near it and still not it (spelled in hex so this
        # public tree types no dotted-quad literal for it - Hard Rule 1)
        for value in ("127.0.0.1", "192.0.2.10", "::1", "0x1",
                      "relay-a.example.invalid"):
            with self.subTest(value=value):
                self.assertFalse(fleet_bus.is_wildcard_bind(value))

    # (3) --bind short-circuits discovery: not one keeper request is made
    def test_bind_short_circuits_discovery(self):
        with mock.patch.object(
                urllib.request, "urlopen",
                side_effect=AssertionError("keeper request attempted")), \
                mock.patch.object(
                    urllib.request.OpenerDirector, "open",
                    side_effect=AssertionError("keeper request attempted")):
            rc, _, err = self.run_main(
                ["--open", "--bind", "127.0.0.1"] + self.server_args(), env={})
        self.assertEqual(rc, 0)
        self.assertIn("listening on 127.0.0.1:", err)

    # (4) a bind value with an embedded NUL fails closed like any other bad
    # bind. inet_aton raises ValueError - not OSError - on one, and that must
    # become the same one-line refusal and exit code as the other bad-bind
    # errors, never a traceback and never a bind. A NUL is never legitimate
    # here, and a C resolver would truncate the value at it ("0\0evil" could
    # reach the OS as the wildcard "0").
    def test_a_nul_in_an_explicit_bind_is_refused(self):
        for value in ("0\x00", "0.0.0.0\x00", "127.0.0.1\x00evil"):
            with self.subTest(bind=repr(value)):
                rc, out, _ = self.run_main(
                    ["--open", "--bind", value] + self.server_args(), env={})
                self.assertEqual(rc, 2, out)  # config error, like a wildcard
                self.assertIn("wildcard", self.one_error_line(out))

    def test_a_nul_in_a_discovered_endpoint_fails_closed(self):
        keeper, url = start_keeper((200, json.dumps({"hosts": [
            {"host": "workst", "endpoint": "http://0\x00:58690", "key_id": "k",
             "health": "healthy", "last_seen": 1761000000}]})))
        try:
            rc, out, _ = self.run_main(
                ["--open", "--host", "workst", "--keeper-url", url,
                 "--keeper-token-file", self.token_file]
                + self.server_args(), env={})
        finally:
            keeper.shutdown()
            keeper.server_close()
        self.assertEqual(rc, 3, out)  # fail-closed discovery, like a wildcard
        self.assertIn("wildcard", self.one_error_line(out))

    # a lone surrogate in the bind ("\ud800") cannot be encoded: the later
    # encoding - the OS's own in getaddrinfo - raises UnicodeEncodeError
    # straight through main(). It is refused as a bad bind instead: one
    # line, the same exit code as every other bad-bind error (2 for the
    # explicit override, 3 for a discovered endpoint), never a traceback
    # and never a bind. It is NOT the wildcard and must not be
    # misdiagnosed as one.
    def test_a_lone_surrogate_in_an_explicit_bind_is_a_bad_bind_refusal(self):
        for value in ("\ud800", "a\ud800b", "x\udc00"):
            with self.subTest(bind=repr(value)):
                rc, out, _ = self.run_main(
                    ["--open", "--bind", value] + self.server_args(), env={})
                self.assertEqual(rc, 2, out)  # config error, like a wildcard
                line = self.one_error_line(out)
                self.assertIn("bad bind", line)
                self.assertNotIn("wildcard", line)

    def test_a_lone_surrogate_in_the_bind_env_is_a_bad_bind_refusal(self):
        rc, out, _ = self.run_main(
            ["--open"] + self.server_args(), env={"FLEET_BUS_BIND": "\ud800"})
        self.assertEqual(rc, 2, out)
        line = self.one_error_line(out)
        self.assertIn("bad bind", line)
        self.assertNotIn("wildcard", line)

    def test_check_bind_refuses_a_lone_surrogate_as_a_bad_bind(self):
        for value in ("\ud800", "a\ud800b", "x\udc00"):
            with self.subTest(bind=repr(value)):
                with self.assertRaises(fleet_bus.FleetBusError) as caught:
                    fleet_bus.check_bind(value, "--bind / FLEET_BUS_BIND", 2)
                error = caught.exception
                self.assertEqual(error.status, 2, error.reason)
                self.assertNotIn("\n", error.reason)
                self.assertIn("bad bind", error.reason)
                self.assertNotIn("wildcard", error.reason)

    def test_endpoint_host_refuses_a_lone_surrogate_as_a_bad_bind(self):
        for endpoint in ("\ud800", "\ud800:58690", "http://\ud800:58690",
                         "https://\ud800"):
            with self.subTest(endpoint=repr(endpoint)):
                with self.assertRaises(fleet_bus.FleetBusError) as caught:
                    fleet_bus.endpoint_host(endpoint)
                error = caught.exception
                self.assertEqual(error.status, 3, error.reason)
                self.assertNotIn("\n", error.reason)
                self.assertIn("bad bind", error.reason)

    def test_a_lone_surrogate_in_a_keeper_endpoint_host_fails_closed(self):
        keeper, url = start_keeper((200, json.dumps({"hosts": [
            {"host": "workst", "endpoint": "\ud800", "key_id": "k",
             "health": "healthy", "last_seen": 1761000000}]})))
        try:
            rc, out, _ = self.run_main(
                ["--open", "--host", "workst", "--keeper-url", url,
                 "--keeper-token-file", self.token_file]
                + self.server_args(), env={})
        finally:
            keeper.shutdown()
            keeper.server_close()
        self.assertEqual(rc, 3, out)  # fail-closed discovery, like a wildcard
        line = self.one_error_line(out)
        self.assertIn("bad bind", line)
        self.assertNotIn("wildcard", line)

    # a port past the socket layer's range (--port 70000) makes bind()
    # raise OverflowError, which is not an OSError and used to escape
    # main() as a traceback. It is the same one-line fail-closed bind
    # error (exit 3) as every other address the OS will not take.
    def test_a_port_out_of_range_fails_with_one_line_error(self):
        rc, out, _ = self.run_main(
            ["--open", "--bind", "127.0.0.1", "--port", "70000",
             "--ttl", "0.2", "--log-dir", str(self.td / "logs")], env={})
        self.assertEqual(rc, 3)
        line = self.one_error_line(out)
        self.assertIn("70000", line)  # names the port, not a secret


class RefusalLogCapTest(unittest.TestCase):
    """fix round 4 item 2: the refusal metadata log is capped per process.

    log_refusal runs on every 401 and every unauthenticated caller can
    trigger one, so the append-only jsonl write is a disk-fill lever. At
    most REFUSAL_LOG_CAP refusal rows per process, then exactly one
    "refusal log capped" row and silence - the 401 answers themselves never
    stop.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_cap_")
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def rows(self, logdir):
        rows = []
        for path in sorted(Path(logdir).rglob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                rows.append(json.loads(line))
        return rows

    def test_the_refusal_log_is_capped(self):
        self.assertEqual(fleet_bus.REFUSAL_LOG_CAP, 1000)  # the shipped cap
        logdir = self.td / "logs"
        store = fleet_bus.FleetBusStore(str(logdir))
        for i in range(fleet_bus.REFUSAL_LOG_CAP + 25):
            store.log_refusal("refusal %d" % i)
        rows = self.rows(logdir)
        refusals = [r for r in rows if r.get("event") == "refused"]
        capped = [r for r in rows if r.get("event") == "refusal log capped"]
        self.assertEqual(len(refusals), fleet_bus.REFUSAL_LOG_CAP, "the cap")
        self.assertEqual(len(capped), 1, "exactly one capped marker row")
        self.assertEqual(len(rows), fleet_bus.REFUSAL_LOG_CAP + 1)
        # further refusals after the cap drop silently: not one more row
        for i in range(10):
            store.log_refusal("refusal past the cap %d" % i)
        self.assertEqual(len(self.rows(logdir)), fleet_bus.REFUSAL_LOG_CAP + 1)

    def test_the_cap_bounds_the_unauthenticated_401_write(self):
        # the same cap through the real request path: every unauthorized POST
        # writes a refusal row, and that write must stay bounded
        logdir = self.td / "http-logs"
        server = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0), keys=fleet_bus.Keyring(override=FAKE_SECRET),
            logdir=str(logdir))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_until,
                                  kwargs={"deadline": None}, daemon=True)
        thread.start()
        try:
            with mock.patch.object(fleet_bus, "REFUSAL_LOG_CAP", 3):
                for i in range(6):
                    status, _ = post_inbox(port, good_body(nonce="cap-%d" % i),
                                           "0" * 64)
                    self.assertEqual(status, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
        rows = self.rows(logdir)
        refusals = [r for r in rows if r.get("event") == "refused"]
        capped = [r for r in rows if r.get("event") == "refusal log capped"]
        self.assertEqual(len(refusals), 3)
        self.assertEqual(len(capped), 1)
        self.assertEqual(len(rows), 4)


class RefusalLogBestEffortTest(unittest.TestCase):
    """The refusal metadata log is best-effort: the 401 answer always goes out.

    log_refusal runs on the 401 path, and its jsonl append (os.makedirs +
    open) raises OSError when the log dir is unwritable - here it sits
    under a regular file - or when the disk is full. That OSError used to
    escape do_POST and drop the response: the caller saw a dead connection
    instead of a refusal. The write is best-effort now: the drop is
    counted silently and the 401 is sent either way.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="fleet_bus_logdrop_")
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        # a regular file where the log's parent must be: every write under
        # it fails with OSError on POSIX and Windows alike (no chmod and no
        # root-sensitive permission bits involved)
        self.blocker = self.td / "blocker"
        self.blocker.write_text("not a directory\n", encoding="utf-8")
        self.logdir = str(self.blocker / "logs")
        self.server = fleet_bus.FleetBusServer(
            ("127.0.0.1", 0),
            keys=fleet_bus.Keyring(override=FAKE_SECRET),
            logdir=self.logdir)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_until,
                                        kwargs={"deadline": None}, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=10)

    def test_a_bad_signature_gets_a_clean_401_when_the_log_is_unwritable(self):
        for nonce in ("log-drop", "log-drop-again"):
            with self.subTest(nonce=nonce):
                status, data = post_inbox(self.port, good_body(nonce=nonce),
                                          "0" * 64)
                self.assertEqual(status, 401, data)
                self.assertEqual(json.loads(data.decode("utf-8")),
                                 {"error": "unauthorized"})
        # the dropped writes are counted - and silently: nothing printed,
        # nothing raised
        self.assertGreaterEqual(self.server.store._log_drops, 2)

    def test_log_refusal_swallows_the_oserror_and_counts_the_drop(self):
        store = fleet_bus.FleetBusStore(self.logdir)
        store.log_refusal("unwritable log")
        store.log_refusal("unwritable log again")
        self.assertEqual(store._log_drops, 2)
        # and the cap accounting still runs, so the write stays bounded
        # even while every one of them fails
        self.assertEqual(store._refusals, 2)


if __name__ == "__main__":
    unittest.main()
