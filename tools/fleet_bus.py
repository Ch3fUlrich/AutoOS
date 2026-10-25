#!/usr/bin/env python3
"""fleet_bus.py - tailnet-only relay listener (D-2024 OCGO-FLEET-BUS, pull mode).

One stdlib HTTP server per host, bound to the tailnet address only. The bind
address is this host's endpoint host part as the keeper hosts registry
reports it (this host is named by --host / FLEET_BUS_HOST; the registry is
read from --keeper-url / FLEET_KEEPER_URL, authenticated with the token in
the 0600 file --keeper-token-file / FLEET_KEEPER_TOKEN_FILE). --bind /
FLEET_BUS_BIND is the explicit transition override. There is no literal
default: discovery that cannot answer fails closed (exit code 3, one line,
no traceback) and never guesses an address, so loopback happens only when
the operator types --bind 127.0.0.1. The coding-side poller pulls; apart
from that one keeper GET at startup this side never dials out, so no
round-trip is wasted: a single GET carries everything new since the
poller's cursor.

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

Secrets travel by NAME and FILE only, never in output: every sending host
has its own HMAC key in a 0600 file, named by the inventory JSON
(--inventory / FLEET_BUS_INVENTORY:
{"hosts": {"<host>": {"key_file": "<path>"}}}). A POST is verified with the
key of the envelope's "from" host - an unknown from-host or an unreadable
key file is a 401 naming the path and host, never the value - and each key
file is read once into memory. --secret-file stays as the explicit
single-key override of the transition. --open skips HMAC for probe-only dry
runs (15 min TTL throwaway); it must never carry live traffic.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlunsplit

BODY_MAX = 4096
TEXT_MAX = 2000
SKEW_S = 60
NONCE_CAP = 10000
RING_CAP = 1000
DEFAULT_PORT = 58690
KEEPER_TIMEOUT_S = 5
# There is no literal default bind in this public tree (AutoOS Hard Rule 1):
# the address comes from the keeper hosts registry entry for --host, or from
# the explicit --bind / FLEET_BUS_BIND override. Loopback only happens when
# the operator types it.
BIND_ENV = "FLEET_BUS_BIND"
HOST_ENV = "FLEET_BUS_HOST"
KEEPER_URL_ENV = "FLEET_KEEPER_URL"
KEEPER_TOKEN_ENV = "FLEET_KEEPER_TOKEN_FILE"
INVENTORY_ENV = "FLEET_BUS_INVENTORY"


class FleetBusError(Exception):
    """A refused request: carries the HTTP status, never a secret.

    Startup uses it too: status 2 for a configuration error, status 3 for
    fail-closed discovery (printed as one line, never a traceback).
    """

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def bind_override(env=None):
    """The explicit transition override: ``--bind`` / ``FLEET_BUS_BIND``.

    None means "discover from the keeper registry". A set-but-empty
    systemd Environment= line is not a value: it must never become "" either,
    because HTTPServer binds "" to INADDR_ANY, which would expose the relay.
    """
    source = os.environ if env is None else env
    return (source.get(BIND_ENV) or "").strip() or None


def display_url(url):
    """The URL without userinfo, so a message never carries credentials."""
    parts = urlparse(url)
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1],
                       parts.path, parts.query, parts.fragment))


def endpoint_host(endpoint):
    """The host part of a keeper endpoint (a URL or bare host[:port])."""
    parsed = urlparse(endpoint if "://" in endpoint else "//" + endpoint)
    return (parsed.hostname or "").strip()


def read_secret(path, what="secret file"):
    """First line of a 0600 file. Names the failure, never the value."""
    try:
        if os.name != "nt" and os.stat(path).st_mode & 0o077:
            raise FleetBusError(2, "%s must be owner-only (0600): %s" % (what, path))
        with open(path, encoding="utf-8-sig") as f:
            value = f.readline().strip()
    except OSError:
        raise FleetBusError(2, "%s unreadable: %s" % (what, path))
    if not value:
        raise FleetBusError(2, "%s empty: %s" % (what, path))
    return value.encode("utf-8")


def read_keeper_token(path):
    """The keeper bearer token (0600 file). Names the path, never the value."""
    try:
        return read_secret(path, "keeper token file")
    except FleetBusError as e:
        raise FleetBusError(3, e.reason)


def fetch_keeper_hosts(keeper_url, token):
    """GET <keeper>/v1/hosts -> the registry list. The token is never printed."""
    url = keeper_url.rstrip("/") + "/v1/hosts"
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + token.decode("utf-8")})
    try:
        with urllib.request.urlopen(request, timeout=KEEPER_TIMEOUT_S) as response:
            status, raw = response.getcode(), response.read()
    except urllib.error.HTTPError as e:
        raise FleetBusError(3, "keeper answered HTTP %d: %s"
                           % (e.code, display_url(keeper_url)))
    except (urllib.error.URLError, OSError, ValueError):
        raise FleetBusError(3, "keeper unreachable: %s" % display_url(keeper_url))
    if status != 200:
        raise FleetBusError(3, "keeper answered HTTP %d: %s"
                           % (status, display_url(keeper_url)))
    try:
        doc = json.loads(raw.decode("utf-8"))
        hosts = doc["hosts"]
        if not isinstance(hosts, list):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise FleetBusError(3, "keeper hosts response is not valid JSON: %s"
                           % display_url(keeper_url))
    return hosts


def discover_bind(host, keeper_url, token_file):
    """This host's bind address: the host part of its keeper endpoint.

    Fail closed (status 3, one line) whenever the keeper cannot answer -
    unset URL/token/host, an unreachable or non-200 keeper, bad JSON, an
    unknown or unhealthy host, a missing endpoint - and never fall back to
    an IP or loopback guess.
    """
    host = (host or "").strip()
    keeper_url = (keeper_url or "").strip()
    token_file = (token_file or "").strip()
    if not keeper_url:
        raise FleetBusError(3, "keeper URL not set (--keeper-url or %s)"
                           % KEEPER_URL_ENV)
    if not token_file:
        raise FleetBusError(3, "keeper token file not set (--keeper-token-file "
                               "or %s)" % KEEPER_TOKEN_ENV)
    if not host:
        raise FleetBusError(3, "host name not set (--host or %s)" % HOST_ENV)
    entries = fetch_keeper_hosts(keeper_url, read_keeper_token(token_file))
    match = [e for e in entries if isinstance(e, dict) and e.get("host") == host]
    if not match:
        raise FleetBusError(3, "host not in the keeper registry: %s" % host)
    entry = match[0]
    if entry.get("health") != "healthy":
        raise FleetBusError(3, "host %s is not healthy in the keeper registry "
                               "(health=%s)" % (host, entry.get("health") or "unknown"))
    endpoint = entry.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise FleetBusError(3, "keeper entry for %s has no endpoint" % host)
    bind = endpoint_host(endpoint.strip())
    if not bind:
        raise FleetBusError(3, "keeper endpoint for %s names no host" % host)
    return bind


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


def load_inventory(path):
    """The per-host key map {"<host>": "<key_file>"} from the inventory JSON."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            hosts = json.load(f)["hosts"]
        if not isinstance(hosts, dict) or not hosts:
            raise ValueError
        for host, entry in hosts.items():
            if (not isinstance(host, str) or not host
                    or not isinstance(entry, dict)
                    or not isinstance(entry.get("key_file"), str)
                    or not entry.get("key_file")):
                raise ValueError
        return {host: entry["key_file"] for host, entry in hosts.items()}
    except (OSError, ValueError, KeyError, TypeError):
        raise FleetBusError(2, "inventory unreadable or malformed: %s" % path)


class Keyring:
    """The /inbox HMAC keys: per-host key files, or one explicit override.

    A key file is read once (first line, 0600) and cached in memory; an
    unknown from-host or an unreadable key file is a 401 naming the path and
    host, never the value. `override` is the transition's explicit
    single-key mode: it answers for every sender and skips the inventory.
    """

    def __init__(self, override=None, inventory=None):
        self._override = override
        self._inventory = dict(inventory or {})
        self._lock = threading.Lock()
        self._cache = {}

    def key_for(self, host):
        """The HMAC key for the envelope's "from" host. Raises (401)."""
        if self._override is not None:
            return self._override
        path = self._inventory.get(host)
        if not path:
            raise FleetBusError(401, "unknown from host: %s" % host)
        with self._lock:
            if host not in self._cache:
                try:
                    self._cache[host] = read_secret(
                        path, "key file for host %s" % host)
                except FleetBusError as e:
                    raise FleetBusError(401, e.reason)
            return self._cache[host]


def check_post(keyring, body, sig, now, seen):
    """Validate a parsed /inbox body. Returns the scrubbed record fields.

    Raises FleetBusError(400) for malformed bodies, (401) for an unknown
    from-host, a bad signature, skew or replay. `keyring` is None only in
    --open mode; `seen` is the server's nonce LRU.
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
    if keyring is not None:
        secret = keyring.key_for(frm)
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
            fields = check_post(self.server.keys, body,
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

    def __init__(self, address, keys, logdir):
        super().__init__(address, FleetBusHandler)
        self.timeout = 0.5
        self.keys = keys
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


def build_keys(args):
    """The HMAC keyring from the CLI: the per-host inventory, or the explicit
    single-key --secret-file override. Errors name paths, never values.
    """
    if args.secret_file:
        return Keyring(override=read_secret(args.secret_file))
    if args.inventory:
        return Keyring(inventory=load_inventory(args.inventory))
    raise FleetBusError(2, "one of --inventory or --secret-file is required "
                           "unless --open")


def build_parser(env=None):
    source = os.environ if env is None else env
    ap = argparse.ArgumentParser(
        prog="fleet_bus.py",
        description="Tailnet-only relay listener (D-2024 OCGO-FLEET-BUS, pull mode).")
    ap.add_argument("--bind", default=bind_override(source),
                    help="explicit bind address, the transition override "
                         "(default: the host part of this host's keeper "
                         "endpoint; $%s)" % BIND_ENV)
    ap.add_argument("--host", default=(source.get(HOST_ENV) or "").strip() or None,
                    help="this host's name in the keeper registry "
                         "(default: $%s)" % HOST_ENV)
    ap.add_argument("--keeper-url",
                    default=(source.get(KEEPER_URL_ENV) or "").strip() or None,
                    help="keeper base URL whose /v1/hosts registry supplies "
                         "the bind address (default: $%s)" % KEEPER_URL_ENV)
    ap.add_argument("--keeper-token-file",
                    default=(source.get(KEEPER_TOKEN_ENV) or "").strip() or None,
                    help="0600 file holding the keeper bearer token, never "
                         "printed (default: $%s)" % KEEPER_TOKEN_ENV)
    ap.add_argument("--inventory",
                    default=(source.get(INVENTORY_ENV) or "").strip() or None,
                    help="per-host key inventory JSON mapping each sending "
                         "host to its own 0600 key file (default: $%s)"
                         % INVENTORY_ENV)
    ap.add_argument("--secret-file", default=None,
                    help="0600 file holding ONE shared HMAC key: the explicit "
                         "single-key override of the per-host inventory")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="relay port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--log-dir", default="logs/fleet-bus",
                    help="metadata-only jsonl log dir (no text is logged)")
    ap.add_argument("--ttl", type=float, default=0,
                    help="auto-exit after N seconds (0 = run forever; "
                         "throwaway dry runs use 900)")
    ap.add_argument("--open", action="store_true",
                    help="skip HMAC (probe-only dry runs, never live traffic)")
    return ap


def main(argv=None, env=None):
    args = build_parser(env).parse_args(argv)
    try:
        keys = None
        if not args.open:
            keys = build_keys(args)
        bind = (args.bind or "").strip() or discover_bind(
            args.host, args.keeper_url, args.keeper_token_file)
    except FleetBusError as e:
        print(json.dumps({"ok": False, "error": e.reason}))
        return e.status
    import time as _time
    server = FleetBusServer((bind, args.port), keys, args.log_dir)
    port = server.server_address[1]
    sys.stderr.write("fleet-bus: listening on %s:%d%s\n"
                     % (bind, port, " (open, probe-only)" if args.open else ""))
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
