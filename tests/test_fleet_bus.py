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
from pathlib import Path

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
    """Mock keeper: answers every GET with the fixture's (status, body)."""

    def do_GET(self):  # noqa: N802 (http.server names the method)
        status, payload = self.server.answer
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        pass


def start_keeper(answer):
    """A local mock of GET <keeper>/v1/hosts on loopback, ephemeral port."""
    server = http.server.HTTPServer(("127.0.0.1", 0), KeeperHandler)
    server.answer = answer
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


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

    def test_an_unknown_from_host_is_refused(self):
        _, port = self.start_server(fleet_bus.Keyring(inventory=self.inventory))
        body = good_body(frm="stranger", nonce="stranger")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        self.assertIn("stranger", data.decode("utf-8"))

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
        reason = data.decode("utf-8")
        self.assertIn("workst", reason)      # names the host
        self.assertIn(self.key_a, reason)    # names the path
        self.assertNotIn(FAKE_SECRET.decode("utf-8"), reason)  # never contents

    def test_a_missing_key_file_names_the_path_and_host(self):
        inventory = {"workst": str(self.td / "gone.key")}
        _, port = self.start_server(fleet_bus.Keyring(inventory=inventory))
        body = good_body(frm="workst", nonce="missing-file")
        status, data = post_inbox(port, body, self.sig(body, FAKE_SECRET))
        self.assertEqual(status, 401)
        reason = data.decode("utf-8")
        self.assertIn("workst", reason)
        self.assertIn("gone.key", reason)

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


if __name__ == "__main__":
    unittest.main()
