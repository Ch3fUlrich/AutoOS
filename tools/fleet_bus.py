#!/usr/bin/env python3
"""fleet_bus.py - tailnet-only relay listener (D-2024 OCGO-FLEET-BUS, pull mode).

One stdlib HTTP server per host, bound to the tailnet address only. The bind
address comes from --bind or FLEET_BUS_BIND; the public default is loopback
because a site address is never a literal in this public repository. The
coding-side poller pulls; this side never dials out, so no round-trip is
wasted: a single GET carries everything new since the poller's cursor.

Contract (the only two routes):
  POST /inbox   JSON {"from","to","ts","nonce","text"} plus header X-Fleet-Sig
                  = HMAC-SHA256(secret, "ts|nonce|from|to|sha256(text)").
                  ts is unix epoch seconds (int); |now-ts| <= 60 or 401.
                  nonce is single-use (LRU, cap 10000) or 401.
                  body <= 4096 bytes or 413. text is scrubbed to one line
                  <= 2000 chars. -> 200 {"id","ts"} | 400 | 401 | 413.
  GET /outbox?since=<cursor>
                -> 200 {"cursor": <last id or the echo>, "records": [...]},
                records [{id,ts,from,to,text}] in insertion order after the
                cursor; unknown or empty cursor returns everything buffered
                (bounded). Reads are open: WG encryption plus the tailnet-only
                bind plus the firewall's remote-ip pin are the read path's
                auth; HMAC guards the state-changing path.

Storage: an in-memory ring (cap 1000, oldest evicted) carries the full
scrubbed text; the append-only log <logdir>/YYYY-MM-DD.jsonl carries records
WITHOUT text ({"id","ts","from","to","len"}) per D-2024 (privacy: logs never
hold message text). A restart drops undelivered bodies by design
(best-effort relay: 10 s client timeout, no auto-retry).

Secrets travel by NAME and FILE only, never in output: the HMAC secret is read
once from --secret-file (0600, owner-only) into memory. --open skips HMAC for
probe-only dry runs (15 min TTL throwaway); it must never carry live traffic.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BODY_MAX = 4096
TEXT_MAX = 2000
SKEW_S = 60
NONCE_CAP = 10000
RING_CAP = 1000
DEFAULT_PORT = 58690
# Loopback is the only safe public default: the live relay takes its tailnet
# address from FLEET_BUS_BIND (or --bind), so no site address is a literal in
# this public tree (AutoOS Hard Rule 1).
DEFAULT_BIND = "127.0.0.1"
BIND_ENV = "FLEET_BUS_BIND"


def bind_default(env=None):
    """The relay bind address: ``FLEET_BUS_BIND``, else loopback.

    The live host names its tailnet address in its own 0600 environment or
    unit file; this public tree ships no site-shaped default.
    """
    source = os.environ if env is None else env
    value = (source.get(BIND_ENV) or "").strip()
    # A set-but-empty systemd Environment= line must not become "":
    # HTTPServer binds "" to INADDR_ANY, which would expose the relay.
    return value or DEFAULT_BIND


class FleetBusError(Exception):
    """A refused request: carries the HTTP status, never a secret."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def scrub_text(text):
    """One line, at most TEXT_MAX chars. Never fails on a string."""
    return " ".join(str(text).splitlines())[:TEXT_MAX]


def canonical(ts, nonce, frm, to, text):
    """The exact bytes the HMAC covers (both sides must agree)."""
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    return "%s|%s|%s|%s|%s" % (ts, nonce, frm, to, digest)


def sign(secret, ts, nonce, frm, to, text):
    """Hex HMAC-SHA256 over the canonical form. Secret stays bytes."""
    return hmac.new(secret, canonical(ts, nonce, frm, to, text).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def check_post(secret, body, sig, now, seen):
    """Validate a parsed /inbox body. Returns the scrubbed record fields.

    Raises FleetBusError(400) for malformed bodies, (401) for bad
    signature, skew or replay. `seen` is the server's nonce LRU.
    """
    if not isinstance(body, dict):
        raise FleetBusError(400, "body must be a JSON object")
    try:
        frm, to = body["from"], body["to"]
        nonce, text = body["nonce"], body["text"]
        ts = int(float(body["ts"]))
    except (KeyError, TypeError, ValueError):
        raise FleetBusError(400, "body needs from/to/ts/nonce/text")
    for name, value, cap in (("from", frm, 64), ("to", to, 64),
                             ("nonce", nonce, 128)):
        if not isinstance(value, str) or not value or len(value) > cap:
            raise FleetBusError(400, "bad %s" % name)
    if not isinstance(body["text"], str):
        raise FleetBusError(400, "bad text")
    if abs(int(now) - ts) > SKEW_S:
        raise FleetBusError(401, "timestamp outside the skew window")
    if secret is not None:
        if not isinstance(sig, str) or not hmac.compare_digest(
                sign(secret, ts, nonce, frm, to, body["text"]), sig):
            raise FleetBusError(401, "bad signature")
    if nonce in seen:
        raise FleetBusError(401, "replayed nonce")
    seen[nonce] = None
    while len(seen) > NONCE_CAP:
        seen.popitem(last=False)
    return {"ts": ts, "from": frm, "to": to, "text": scrub_text(body["text"])}


class FleetBusStore:
    """The in-memory ring plus the metadata-only log. Guarded by one lock."""

    def __init__(self, logdir):
        self._lock = threading.Lock()
        self._records = deque(maxlen=RING_CAP)
        self._seen = OrderedDict()
        self._seq = 0
        self._logdir = logdir

    def append(self, fields):
        """Store a checked record; returns it with its id. Logs metadata only."""
        with self._lock:
            self._seq += 1
            ts_ms = int(time.time() * 1000)
            record = {"id": "%d-%06d" % (ts_ms, self._seq), "ts": fields["ts"],
                      "from": fields["from"], "to": fields["to"],
                      "text": fields["text"]}
            self._records.append(record)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            os.makedirs(self._logdir, exist_ok=True)
            with open(os.path.join(self._logdir, day + ".jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps({"id": record["id"], "ts": record["ts"],
                                    "from": record["from"], "to": record["to"],
                                    "len": len(record["text"])}) + "\n")
            return dict(record)

    def since(self, cursor):
        """Records after `cursor` (insertion order); unknown cursor -> all."""
        with self._lock:
            records = list(self._records)
        if cursor:
            ids = [r["id"] for r in records]
            if cursor in ids:
                records = records[ids.index(cursor) + 1:]
        head = records[-1]["id"] if records else cursor
        return [dict(r) for r in records], head


class FleetBusHandler(BaseHTTPRequestHandler):
    server_version = "FleetBus/1"

    def _send(self, status, doc):
        payload = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            raise FleetBusError(400, "Content-Length required")
        if length > BODY_MAX:
            raise FleetBusError(413, "body over the 4 KB cap")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise FleetBusError(400, "body must be JSON")

    def do_POST(self):  # noqa: N802 (http.server names the method)
        try:
            if urlparse(self.path).path != "/inbox":
                raise FleetBusError(404, "not found")
            body = self._read_json()
            fields = check_post(self.server.secret, body,
                                self.headers.get("X-Fleet-Sig"),
                                time.time(), self.server.nonces)
            record = self.server.store.append(fields)
            self._send(200, {"id": record["id"], "ts": record["ts"]})
        except FleetBusError as e:
            self._send(e.status, {"error": e.reason})

    def do_GET(self):  # noqa: N802 (http.server names the method)
        try:
            parts = urlparse(self.path)
            if parts.path != "/outbox":
                raise FleetBusError(404, "not found")
            since = parse_qs(parts.query).get("since", [""])[0]
            records, cursor = self.server.store.since(since)
            self._send(200, {"cursor": cursor, "records": records})
        except FleetBusError as e:
            self._send(e.status, {"error": e.reason})

    def log_message(self, *args):  # keep quiet; metadata goes to the jsonl log
        pass


class FleetBusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, secret, logdir):
        super().__init__(address, FleetBusHandler)
        self.timeout = 0.5
        self.secret = secret
        self.nonces = OrderedDict()
        self.store = FleetBusStore(logdir)

    def serve_until(self, deadline):
        """Serve until monotonic() passes `deadline` (None = forever)."""
        import time as _time
        if deadline is None:
            self.serve_forever()
            return
        while _time.monotonic() < deadline:
            self.handle_request()
        self.server_close()


def read_secret(path):
    """First line of a 0600 file. Names the failure, never the value."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            value = f.readline().strip()
    except OSError:
        raise FleetBusError(2, "secret file unreadable: %s" % path)
    if not value:
        raise FleetBusError(2, "secret file empty: %s" % path)
    return value.encode("utf-8")


def build_parser(env=None):
    ap = argparse.ArgumentParser(
        prog="fleet_bus.py",
        description="Tailnet-only relay listener (D-2024 OCGO-FLEET-BUS, pull mode).")
    ap.add_argument("--bind", default=bind_default(env),
                    help="bind address, tailnet-only (default: $%s or %s)"
                         % (BIND_ENV, DEFAULT_BIND))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="relay port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--secret-file", default=None,
                    help="0600 file holding the shared HMAC secret (required "
                         "unless --open)")
    ap.add_argument("--log-dir", default="logs/fleet-bus",
                    help="metadata-only jsonl log dir (no text is logged)")
    ap.add_argument("--ttl", type=float, default=0,
                    help="auto-exit after N seconds (0 = run forever; "
                         "throwaway dry runs use 900)")
    ap.add_argument("--open", action="store_true",
                    help="skip HMAC (probe-only dry runs, never live traffic)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    secret = None
    if not args.open:
        if not args.secret_file:
            print(json.dumps({"ok": False,
                              "error": "--secret-file is required unless --open"}))
            return 2
        try:
            secret = read_secret(args.secret_file)
        except FleetBusError as e:
            print(json.dumps({"ok": False, "error": e.reason}))
            return 2
    import time as _time
    server = FleetBusServer((args.bind, args.port), secret, args.log_dir)
    port = server.server_address[1]
    sys.stderr.write("fleet-bus: listening on %s:%d%s\n"
                     % (args.bind, port, " (open, probe-only)" if args.open else ""))
    deadline = (_time.monotonic() + args.ttl) if args.ttl > 0 else None
    try:
        server.serve_until(deadline)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
