#!/usr/bin/env python3
"""Per-session Playwright MCP proxy: the browser backend runs only while it is used.

Claude Code starts every stdio MCP server at session start and keeps it for the whole
session, and it does not reconnect a stdio server that exited. A Playwright container
registered directly therefore lives as long as its session, browsing or not. This
proxy is the process Claude Code talks to instead. It stays alive for the whole
session, answers the session-start handshake from a cache, starts the real backend
(by default the official Playwright MCP docker image) on the first request that needs
it, stops it after an idle period and starts it again on the next request. A session
that never browses never starts a container.

Speaks MCP over stdio: one JSON-RPC message per line on stdin/stdout, UTF-8. Nothing
but JSON-RPC lines is ever written to stdout; diagnostics go to stderr, prefixed
`playwright-lazy:`, and never contain environment values.

Environment (all optional):
  AUTOOS_PLAYWRIGHT_MCP_CMD          backend command: a JSON list, or a shell-quoted
                                     string. Default: `docker run -i --rm --init
                                     --network host --name autoos-pw-<pid>-<n>
                                     mcr.microsoft.com/playwright/mcp:latest`
  AUTOOS_PLAYWRIGHT_IDLE_SECONDS     idle period before the backend is stopped (900)
  AUTOOS_PLAYWRIGHT_MCP_CACHE        handshake cache file (default
                                     $XDG_CACHE_HOME/autoos/playwright-mcp/handshake.json,
                                     else ~/.cache/autoos/...); written atomically, mode 600,
                                     in a directory created with mode 700. It is read only
                                     if it is a regular file (never through a symlink) of at
                                     most 4 MiB, owned by the proxy's user and not writable by
                                     group or others, in a directory that is too: otherwise it
                                     is ignored with one stderr line, as a cold cache (an
                                     existing group-writable directory, e.g. from a umask of
                                     002, is refused: chmod go-w it)
  AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS  how long the backend may take to answer the
                                     replayed handshake (60)

Choices the spec left open (each is covered by a test):
  * a `ping` answered here does not count as activity, so a client that pings on a
    timer cannot hold the backend open; any message that reaches the backend does;
  * the idle period restarts when the last in-flight request completes;
  * the cached `protocolVersion` is the newest version the backend has ever answered
    with, so a client asking for a version nobody has seen gets the backend's own latest;
  * a `tools/list` carrying a cursor, and a result carrying `nextCursor`, bypass the cache;
  * a request whose id is still in flight (queued, running, or being answered here) is
    refused at once with -32600 "duplicate request id in flight" and never forwarded:
    two answers under one id would leave the slower call untracked, and the idle stop
    could kill it without an error reaching the client.

Left as it is on purpose (reviewed 2026-09-26; each is a decision, not an oversight):
  * a JSON-RPC batch (an array on one line) is rejected with -32600: MCP >= 2025-06
    removed batching, so no conforming client sends one;
  * a client that stops reading our stdout is a broken client: nothing here works around
    it, and a failed write to it ends the session;
  * SIGKILL of the proxy leaves no container behind: the backend's stdin closes with the
    proxy, the docker client exits and `--rm` removes the container within about a
    second (measured 2026-09-25).

Python 3.8+, standard library only. Linux and macOS (the backend gets its own session
so a terminal Ctrl-C reaches the proxy, which then stops it in an orderly way).
"""
import errno
import json
import math
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time

PREFIX = "playwright-lazy:"
IMAGE = "mcr.microsoft.com/playwright/mcp:latest"
DEFAULT_IDLE_SECONDS = 900.0
DEFAULT_HANDSHAKE_SECONDS = 60.0
EOF_WAIT_SECONDS = 5.0      # after closing the backend's stdin, before terminate
TERM_WAIT_SECONDS = 3.0     # after terminate, before kill
DOCKER_STOP_SECONDS = 30.0
MAX_CACHE_BYTES = 4 * 1024 * 1024   # a handshake cache is a few KiB: anything larger is not one
BACKEND_ERROR = -32000
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603
FALLBACK_PROTOCOL = "2025-06-18"
DEFAULT_CLIENT_PARAMS = {
    "protocolVersion": FALLBACK_PROTOCOL,
    "capabilities": {},
    "clientInfo": {"name": "autoos-playwright-lazy", "version": "1"},
}
_MISSING = object()
_ASKED_ID = re.compile(r"^autoos-s(\d+)-\d+$")     # the ids the client sees on a backend's own requests

_log_lock = threading.Lock()


def log(message):
    """One diagnostic line on stderr. Never pass an environment value in here."""
    try:
        with _log_lock:
            sys.stderr.write("%s %s\n" % (PREFIX, message))
            sys.stderr.flush()
    except (OSError, ValueError):
        pass


def default_backend_command(name):
    """The backend the operator ran by hand before this proxy existed."""
    return ["docker", "run", "-i", "--rm", "--init", "--network", "host",
            "--name", name, IMAGE]


def parse_backend_command(text):
    """AUTOOS_PLAYWRIGHT_MCP_CMD: a JSON list of strings, or a shlex-quoted string."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        if text.startswith("["):
            argv = json.loads(text)
            if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv) and argv[0]:
                return argv
            return None
        argv = shlex.split(text)
    except ValueError:
        return None
    return argv or None


def env_seconds(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0:
        log("%s is not a positive number; using %g" % (name, default))
        return default
    return value


def cache_path():
    explicit = os.environ.get("AUTOOS_PLAYWRIGHT_MCP_CACHE", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".cache")
    # A directory of its own: ~/.cache/autoos is shared (lib/linux/download.sh creates it with
    # the umask mode, group-writable under umask 002) and would make every session refuse the cache.
    return os.path.join(base, "autoos", "playwright-mcp", "handshake.json")


def id_key(rid):
    return json.dumps(rid, sort_keys=True)


def encode(message):
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _not_json(token):
    raise ValueError("%s is not a JSON number" % token)


def _finite(token):
    value = float(token)
    if not math.isfinite(value):
        _not_json(token)
    return value


def loads(text):
    """json.loads for a wire message. Python's json also takes NaN, Infinity and -Infinity
    (and 1e999, which is one), and would echo them back as `{"id":NaN,...}`: not JSON,
    and a line the client cannot read. They are a parse error here."""
    return json.loads(text, parse_constant=_not_json, parse_float=_finite)


def parse_line(raw):
    try:
        return loads(raw.decode("utf-8"))
    except Exception:  # bad UTF-8, bad JSON, absurd nesting: all "not a message"
        return _MISSING


def untrusted(st, what):
    """Why the cache file or its directory cannot be believed, or None.

    The cache decides which tool definitions the model is shown: whoever can write it can
    put text in front of the model. So it must belong to the user the proxy runs as, and
    nobody else may be able to write it."""
    if st.st_uid != os.geteuid():
        return "%s belongs to another user" % what
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "%s is writable by group or others" % what
    return None


def ignore_cache(why):
    log("ignoring the handshake cache: %s" % why)


class Cache(object):
    """The backend's last `initialize` result and `tools/list` result, on disk.

    A cache that cannot be trusted or read is treated as missing: the proxy then starts
    the backend for the handshake, as on a cold start, and says why once on stderr."""

    def __init__(self, path):
        self.path = path
        self.initialize = None
        self.tools = None
        self.accepted = []      # protocol versions the backend has answered with
        self._saved = None      # the text last read or written: identical saves are skipped
        self.writable = True    # False when the directory itself is not trusted: nothing is written into it
        self._load()

    def _read(self):
        """The file's bytes, or None: missing, or not something to believe (already reported)."""
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.path, flags)      # NOFOLLOW: a symlink is refused; NONBLOCK: a FIFO cannot hang us
        except FileNotFoundError:
            return None
        except OSError as exc:
            ignore_cache("the file is a symbolic link" if exc.errno == errno.ELOOP
                         else "the file cannot be opened (%s)" % type(exc).__name__)
            return None
        try:
            st = os.fstat(fd)                   # of the opened file: nothing can be swapped in after the check
            why = None if stat.S_ISREG(st.st_mode) else "the file is not a regular file"
            why = why or untrusted(st, "the file")
            if why:
                ignore_cache(why)
                return None
            with os.fdopen(fd, "rb", closefd=False) as fh:
                data = fh.read(MAX_CACHE_BYTES + 1)
        except OSError as exc:
            ignore_cache("the file cannot be read (%s)" % type(exc).__name__)
            return None
        finally:
            os.close(fd)
        if len(data) > MAX_CACHE_BYTES:
            ignore_cache("the file is larger than %d MiB" % (MAX_CACHE_BYTES // (1024 * 1024)))
            return None
        return data

    def _load(self):
        directory = os.path.dirname(self.path) or "."
        try:
            found = os.stat(directory)          # follows a symlinked directory: the target is what counts
        except OSError:
            found = None                        # no directory yet: a cold cache, `save` creates it
        why = untrusted(found, "the cache directory") if found is not None else None
        if why:
            self.writable = False
            ignore_cache(why)
            return
        raw = self._read()
        if raw is None:
            return
        try:
            data = loads(raw.decode("utf-8"))
        except Exception:   # bad UTF-8, bad JSON, absurd nesting (RecursionError): not a cache
            ignore_cache("the file is not valid JSON")
            return
        init = data.get("initialize") if isinstance(data, dict) else None
        if not (isinstance(init, dict) and isinstance(init.get("protocolVersion"), str)):
            ignore_cache("the file holds no handshake")
            return
        self.initialize = init
        tools = data.get("tools")
        if isinstance(tools, dict) and isinstance(tools.get("tools"), list):
            self.tools = tools
        accepted = data.get("accepted")
        if isinstance(accepted, list):
            self.accepted = [v for v in accepted if isinstance(v, str)]
        if init["protocolVersion"] not in self.accepted:
            self.accepted.append(init["protocolVersion"])

    def server_changed(self, result):
        """True when `result` (a fresh initialize result) is not from the cached server."""
        return self.initialize is None or self.initialize.get("serverInfo") != result.get("serverInfo")

    def update_initialize(self, result):
        version = result.get("protocolVersion")
        if isinstance(version, str) and version not in self.accepted:
            self.accepted.append(version)
        stored = dict(result)
        if self.accepted:
            stored["protocolVersion"] = max(self.accepted)
        self.initialize = stored

    def initialize_for(self, requested):
        """The cached initialize result, speaking the client's version when the backend accepted it."""
        result = dict(self.initialize)
        if isinstance(requested, str) and requested in self.accepted:
            result["protocolVersion"] = requested
        return result

    def save(self):
        if not self.writable:
            return
        data = {"version": 1, "initialize": self.initialize, "accepted": self.accepted}
        if self.tools is not None:
            data["tools"] = self.tools
        text = json.dumps(data, sort_keys=True)
        if text == self._saved and os.path.exists(self.path):
            return
        directory = os.path.dirname(self.path) or "."
        tmp = None
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            # No tempfile module: its imports cost about a megabyte of RSS per session.
            tmp = os.path.join(directory, ".playwright-mcp-%d-%s.tmp" % (os.getpid(), os.urandom(4).hex()))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            tmp = None
            self._saved = text
        except OSError as exc:
            log("could not write the handshake cache (%s)" % type(exc).__name__)
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass


class Job(object):
    """A client request that has to wait for a backend."""

    def __init__(self, kind, rid, raw, params=None):
        self.kind = kind            # "forward", "tools_list" (cold cache) or "initialize" (cold cache)
        self.rid = rid
        self.key = id_key(rid)
        self.raw = raw
        self.params = params
        self.attempts = 0


class StartError(Exception):
    """The backend could not be started or handshaken; the message is the reason."""


class Backend(object):
    """One backend process and the bookkeeping that belongs to it alone."""

    def __init__(self, number, argv, is_default, name):
        self.number = number
        self.argv = argv
        self.is_default = is_default
        self.name = name
        self.proc = None
        self.reader = None
        self.alive = False          # its stdout has not reached EOF
        self.ready = False          # the handshake replay completed
        self.stopping = False       # a deliberate stop has begun
        self.stopped = False
        self.inflight = {}          # id_key -> the client's original id
        self.waiters = {}           # reserved id -> [Event, response or None]
        self.requests_sent = 0      # requests this backend has sent to the client so far
        self.write_lock = threading.Lock()
        self.stop_lock = threading.Lock()


class Proxy(object):
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout
        self.idle_seconds = env_seconds("AUTOOS_PLAYWRIGHT_IDLE_SECONDS", DEFAULT_IDLE_SECONDS)
        self.handshake_seconds = env_seconds("AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS", DEFAULT_HANDSHAKE_SECONDS)
        raw_cmd = os.environ.get("AUTOOS_PLAYWRIGHT_MCP_CMD")
        self.custom_command = parse_backend_command(raw_cmd)
        if raw_cmd is not None and raw_cmd.strip() and self.custom_command is None:
            log("AUTOOS_PLAYWRIGHT_MCP_CMD is not a usable command; using the default")
        self.cache = Cache(cache_path())
        self.lock = threading.RLock()       # state: backend, in-flight ids, activity clock
        self.out_lock = threading.Lock()    # one writer on the client's stdout
        self.shutdown = threading.Event()
        self.signalled = 0
        self.closing = False
        self.backend = None
        self.asked = {}                     # proxy id -> (backend, the backend's own id)
        self.queue = []                     # Jobs waiting for a backend, oldest first
        self.claimed = {}                   # id_key -> Job: queued or answered here, not yet in Backend.inflight
        self.starter = None                 # the thread that starts backends and serves the queue
        self.live = set()                   # backends whose stop has not finished
        self.sequence = 0
        self.client_params = None
        self.last_activity = time.monotonic()

    # -- client side ----------------------------------------------------------

    def write_client(self, raw):
        with self.out_lock:
            try:
                self.stdout.write(raw + b"\n")
                self.stdout.flush()
            except (OSError, ValueError):
                self.shutdown.set()

    def reply(self, rid, result):
        self.write_client(encode({"jsonrpc": "2.0", "id": rid, "result": result}))

    def reply_error(self, rid, code, message):
        self.write_client(encode({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}))

    def client_loop(self):
        try:
            while True:
                line = self.stdin.readline()
                if not line:
                    break
                raw = line.rstrip(b"\r\n")
                if raw.strip():
                    self.on_client_line(raw)
        finally:
            self.shutdown.set()

    def on_client_line(self, raw):
        msg = parse_line(raw)
        if msg is _MISSING:
            log("dropped a client line that is not JSON")
            self.reply_error(None, -32700, "parse error")
            return
        if not isinstance(msg, dict):
            log("dropped a client message that is not a JSON-RPC object (batches are not supported)")
            self.reply_error(None, INVALID_REQUEST, "invalid request: batches are not supported")
            return
        try:
            method = msg.get("method")
            if method is None:
                self.on_client_response(msg)            # the client's answer to a backend request
            elif "id" in msg:
                self.on_request(msg, raw)
            else:
                self.on_notification(msg, raw)
        except Exception as exc:  # one bad message must not take the session down
            log("internal error while handling a client message (%s)" % type(exc).__name__)
            if isinstance(msg.get("method"), str) and "id" in msg:
                self.reply_error(msg["id"], INTERNAL_ERROR, "playwright proxy internal error")

    def on_request(self, msg, raw):
        rid = msg["id"]
        method = msg["method"]
        params = msg.get("params")
        with self.lock:
            duplicate = self.in_flight(id_key(rid))
        if duplicate:
            # Two answers under one id would leave nothing tracked for the slower call, and
            # the idle stop could then kill it silently. Only this thread introduces ids,
            # so the check here and the registration in `submit` cannot race each other.
            log("refused a request whose id is already in flight")
            self.reply_error(rid, INVALID_REQUEST, "duplicate request id in flight")
            return
        if method == "initialize":
            self.on_initialize(rid, params)
            return
        if method == "ping":
            self.reply(rid, {})         # answered here; keep-alives must not hold the backend open
            return
        cursor = isinstance(params, dict) and params.get("cursor") is not None
        if method == "tools/list" and not cursor:
            if self.cache.tools is not None:
                self.reply(rid, self.cache.tools)
            else:
                self.submit(Job("tools_list", rid, raw))    # starting fetches and caches the list
            return
        self.submit(Job("forward", rid, raw))

    def on_notification(self, msg, raw):
        if msg.get("method") == "notifications/initialized":
            return          # the proxy sends its own to every backend it starts
        self.forward_or_drop(raw)

    def on_initialize(self, rid, params):
        params = params if isinstance(params, dict) else {}
        with self.lock:
            self.client_params = params
        if self.cache.initialize is not None:
            self.reply(rid, self.cache.initialize_for(params.get("protocolVersion")))
        else:
            self.submit(Job("initialize", rid, None, params))

    def on_client_response(self, msg):
        """The client's answer to a request a backend sent: back to that backend, under its own id.

        The backend's request ids are rewritten to one that names the backend generation
        (see on_backend_line), so an answer can only reach the backend that asked. It
        goes to a backend that is still replaying its handshake as well: the answer may
        be what that handshake is waiting for."""
        rid = msg.get("id")
        with self.lock:
            entry = self.asked.pop(rid, None) if isinstance(rid, str) else None
            be = entry[0] if entry is not None else None
            usable = be is not None and be.alive and not be.stopping
            if usable:
                self.last_activity = time.monotonic()
        if entry is None or not usable:
            match = _ASKED_ID.match(rid) if isinstance(rid, str) else None
            if match:       # only our own id format is quoted; a client's id may be anything
                log("dropped the client's answer to a request of backend generation %s, which is gone"
                    % match.group(1))
            else:
                log("dropped a client answer that matches no request of a backend")
            return
        msg["id"] = entry[1]
        try:
            self.send_backend(be, encode(msg))
        except StartError:
            pass

    def forward_or_drop(self, raw):
        with self.lock:
            be = self.backend
            usable = be is not None and be.alive and be.ready and not be.stopping
            if usable:
                self.last_activity = time.monotonic()
        if usable:
            try:
                self.send_backend(be, raw)
            except StartError:
                pass

    # -- starting and the queue -------------------------------------------------

    def in_flight(self, key):
        """True from the moment a request is accepted for a backend until its reply is written.
        The caller holds self.lock."""
        be = self.backend
        return key in self.claimed or (be is not None and key in be.inflight)

    def release(self, job):
        """Give the job's id back. Always before the reply that ends it is written: a client
        that retries the id the moment it sees the reply must not be refused."""
        with self.lock:
            self.claimed.pop(job.key, None)

    def submit(self, job):
        """Forward `job` at once when a backend is ready, else queue it for the starter.

        The client thread never waits for a backend: while one starts, the client's
        messages keep being read (a backend may need an answer from the client before
        it finishes its handshake), and what needs the backend waits in the queue."""
        direct = None
        with self.lock:
            be = self.backend
            if (job.kind == "forward" and not self.queue and self.starter is None
                    and be is not None and be.alive and be.ready and not be.stopping):
                be.inflight[job.key] = job.rid
                self.last_activity = time.monotonic()
                direct = be
            else:
                self.claimed[job.key] = job
                self.queue.append(job)
                if self.starter is None:
                    self.starter = threading.Thread(target=self.start_loop, daemon=True)
                    self.starter.start()
        if direct is not None:
            self.send_job(direct, job)

    def send_job(self, be, job):
        """Send a job that is already registered as in flight on `be`."""
        try:
            self.send_backend(be, job.raw)
        except StartError:
            with self.lock:
                owned = be.inflight.pop(job.key, _MISSING) is not _MISSING
            if owned:           # otherwise the exit handler already answered it
                self.reply_error(job.rid, BACKEND_ERROR, "playwright backend exited")

    def start_loop(self):
        """The starter thread: starts a backend when the queue needs one and serves the queue."""
        while True:
            with self.lock:
                if not self.queue:
                    self.starter = None
                    return
            try:
                self.serve(self.ensure_backend())
            except StartError as exc:
                self.fail_queued(BACKEND_ERROR, "playwright backend could not start: %s" % exc)
            except Exception as exc:  # the flag above must never stay set: nothing would start a backend again
                log("internal error while starting the backend (%s)" % type(exc).__name__)
                self.fail_queued(INTERNAL_ERROR, "playwright proxy internal error")

    def fail_queued(self, code, message):
        with self.lock:
            jobs, self.queue = self.queue, []
        for job in jobs:
            self.release(job)
            self.reply_error(job.rid, code, message)

    def serve(self, be):
        """Run the queued jobs on `be`, oldest first; return early when `be` is gone."""
        while True:
            with self.lock:
                if not self.queue:
                    return
                job = self.queue.pop(0)
                forwards = job.kind == "forward" or (job.kind == "tools_list" and self.cache.tools is None)
                usable = be is self.backend and be.alive and not be.stopping
                if forwards and usable:
                    self.claimed.pop(job.key, None)     # same section: the id is never untracked
                    be.inflight[job.key] = job.rid
                    self.last_activity = time.monotonic()
                elif forwards:
                    job.attempts += 1
                    if job.attempts < 3:
                        self.queue.insert(0, job)
                        return          # stopped between the start and now: start another
            if not forwards:
                self.answer_locally(job)
            elif usable:
                self.send_job(be, job)
            else:
                self.release(job)
                self.reply_error(job.rid, BACKEND_ERROR, "playwright backend could not start: it kept stopping")

    def answer_locally(self, job):
        """A cold-cache initialize or tools/list, answered from the cache the start filled."""
        self.release(job)
        if job.kind == "initialize":
            if self.cache.initialize is None:       # cannot happen after a successful start
                self.reply_error(job.rid, BACKEND_ERROR, "playwright backend gave no initialize result")
            else:
                self.reply(job.rid, self.cache.initialize_for(job.params.get("protocolVersion")))
        else:
            self.reply(job.rid, self.cache.tools)

    # -- backend side ---------------------------------------------------------

    def send_backend(self, be, raw):
        try:
            with be.write_lock:
                be.proc.stdin.write(raw + b"\n")
                be.proc.stdin.flush()
        except (OSError, ValueError):
            raise StartError("the backend closed its input")

    def ensure_backend(self):
        """Only the starter thread starts backends, so starts never race each other."""
        with self.lock:
            be = self.backend
            if be is not None and be.alive and be.ready and not be.stopping:
                return be
        be = self.start_backend()
        with self.lock:
            self.backend = be
            self.last_activity = time.monotonic()
        return be

    def start_backend(self):
        with self.lock:
            if self.closing:
                raise StartError("the proxy is shutting down")
            self.sequence += 1
            number = self.sequence
        name = "autoos-pw-%d-%d" % (os.getpid(), number)
        if self.custom_command is not None:
            argv, is_default = list(self.custom_command), False
        else:
            argv, is_default = default_backend_command(name), True
        be = Backend(number, argv, is_default, name)
        try:
            be.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=self._stderr_target(), close_fds=True,
                                       start_new_session=True)
        except (OSError, ValueError) as exc:
            raise StartError("cannot run %s (%s)" % (os.path.basename(argv[0]), describe(exc)))
        be.alive = True
        with self.lock:
            self.live.add(be)
        be.reader = threading.Thread(target=self.backend_loop, args=(be,), daemon=True)
        be.reader.start()
        log("starting the backend (%s)" % name)
        try:
            self.replay_handshake(be)
        except StartError:
            self.stop_backend(be)
            raise
        return be

    @staticmethod
    def _stderr_target():
        try:
            return sys.stderr.fileno()
        except (OSError, ValueError, AttributeError):
            return subprocess.DEVNULL

    def request_backend(self, be, rid, method, params):
        """A request of our own, answered on `be`: returns the whole response message."""
        waiter = [threading.Event(), None]
        with self.lock:
            be.waiters[rid] = waiter
        try:
            self.send_backend(be, encode({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
            if not waiter[0].wait(self.handshake_seconds):
                raise StartError("the backend did not answer %s within %g s" % (method, self.handshake_seconds))
            if waiter[1] is None:
                raise StartError("the backend exited during the handshake")
            return waiter[1]
        finally:
            with self.lock:
                be.waiters.pop(rid, None)

    def replay_handshake(self, be):
        with self.lock:
            params = self.client_params if self.client_params is not None else DEFAULT_CLIENT_PARAMS
        response = self.request_backend(be, "autoos-init-%d" % be.number, "initialize", params)
        result = response.get("result")
        if "error" in response or not isinstance(result, dict):
            raise StartError("the backend rejected the replayed initialize")
        self.send_backend(be, encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        want_tools = self.cache.tools is None or self.cache.server_changed(result)
        told = params.get("protocolVersion")
        if told in self.cache.accepted and result.get("protocolVersion") != told:
            log("the backend now speaks a different protocol version than the one the client was told")
        self.cache.update_initialize(result)
        if want_tools:
            listed = self.request_backend(be, "autoos-tools-%d" % be.number, "tools/list", {})
            tools = listed.get("result")
            if isinstance(tools, dict) and isinstance(tools.get("tools"), list) and "nextCursor" not in tools:
                self.cache.tools = tools
            else:
                self.cache.tools = None
        self.cache.save()
        with self.lock:
            be.ready = True

    def backend_loop(self, be):
        try:
            while True:
                line = be.proc.stdout.readline()
                if not line:
                    break
                raw = line.rstrip(b"\r\n")
                if raw.strip():
                    self.on_backend_line(be, raw)
        except (OSError, ValueError):
            pass
        finally:
            self.on_backend_exit(be)

    def on_backend_line(self, be, raw):
        msg = parse_line(raw)
        if not isinstance(msg, dict):
            log("dropped a backend line on stdout that is not a JSON-RPC message")
            return
        if "method" not in msg and "id" in msg:
            rid = msg["id"]
            with self.lock:
                waiter = be.waiters.get(rid) if isinstance(rid, str) else None
                if waiter is None:
                    be.inflight.pop(id_key(rid), None)
                self.last_activity = time.monotonic()
            if waiter is not None:
                waiter[1] = msg     # a reserved id: ours, never the client's
                waiter[0].set()
                return
        elif "method" in msg and "id" in msg:
            # A request of the backend's own (roots/list, sampling, ...). Its id is only
            # unique within that backend, and a late answer must never reach a later one.
            with self.lock:
                be.requests_sent += 1
                new_id = "autoos-s%d-%d" % (be.number, be.requests_sent)
                self.asked[new_id] = (be, msg["id"])
                self.last_activity = time.monotonic()
            msg["id"] = new_id
            raw = encode(msg)
        else:
            with self.lock:
                self.last_activity = time.monotonic()
        self.write_client(raw)

    def on_backend_exit(self, be):
        with self.lock:
            be.alive = False
            if self.backend is be:
                self.backend = None
            pending = list(be.inflight.values())
            be.inflight.clear()
            waiters = list(be.waiters.values())
            for key in [k for k, (owner, _) in self.asked.items() if owner is be]:
                del self.asked[key]         # nobody can answer these any more
            deliberate = be.stopping
        for waiter in waiters:
            waiter[0].set()             # response stays None: the handshake failed
        for rid in pending:
            self.reply_error(rid, BACKEND_ERROR, "playwright backend exited")
        try:
            be.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.kill(be.proc)
        if not deliberate:
            log("the backend (%s) exited on its own with code %s" % (be.name, be.proc.returncode))
            if be.is_default:
                self.docker_stop(be.name)
            with self.lock:
                self.live.discard(be)

    # -- stopping -------------------------------------------------------------

    @staticmethod
    def kill(proc):
        try:
            proc.kill()
        except OSError:
            pass

    @staticmethod
    def close_stdin(proc):
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    @staticmethod
    def wait_for_exit(proc, seconds):
        try:
            proc.wait(timeout=seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    def docker_stop(self, name):
        try:
            subprocess.run(["docker", "stop", name], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=DOCKER_STOP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            log("could not run docker stop for %s" % name)

    def stop_backend(self, be):
        """Close its stdin, give it 5 s, terminate, then kill; docker gets `docker stop` as well."""
        with be.stop_lock:
            if be.stopped:
                return
            with self.lock:
                be.stopping = True
            proc = be.proc
            closer = threading.Thread(target=self.close_stdin, args=(proc,), daemon=True)
            closer.start()
            closer.join(1.0)        # a flush into a full pipe must not hold the stop up
            if not self.wait_for_exit(proc, EOF_WAIT_SECONDS):
                try:
                    proc.terminate()
                except OSError:
                    pass
                if not self.wait_for_exit(proc, TERM_WAIT_SECONDS):
                    self.kill(proc)
                    self.wait_for_exit(proc, TERM_WAIT_SECONDS)
            if be.is_default:
                self.docker_stop(be.name)
            be.stopped = True
        if be.reader is not None and be.reader is not threading.current_thread():
            be.reader.join(2)
        with self.lock:
            self.live.discard(be)

    def idle_loop(self):
        poll = min(1.0, max(0.05, self.idle_seconds / 10.0))
        while not self.shutdown.wait(poll):
            victim = None
            with self.lock:
                be = self.backend
                if (be is not None and be.alive and be.ready and not be.stopping and not be.inflight
                        and time.monotonic() - self.last_activity >= self.idle_seconds):
                    be.stopping = True
                    self.backend = None
                    victim = be
            if victim is not None:
                log("idle for %g s: stopping the backend (%s)" % (self.idle_seconds, victim.name))
                self.stop_backend(victim)

    def stop_all(self):
        with self.lock:
            self.closing = True
            victims = list(self.live)
            self.backend = None
        for be in victims:
            self.stop_backend(be)

    def start(self):
        threading.Thread(target=self.client_loop, daemon=True).start()
        threading.Thread(target=self.idle_loop, daemon=True).start()


def describe(exc):
    """A reason for a spawn failure that names no environment value."""
    if isinstance(exc, FileNotFoundError):
        return "not found on PATH"
    if isinstance(exc, PermissionError):
        return "permission denied"
    return type(exc).__name__


def main():
    proxy = Proxy(sys.stdin.buffer, sys.stdout.buffer)

    def on_signal(signum, frame):
        proxy.signalled = signum        # only a flag: locks are not safe inside a handler

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)     # a background shell leaves SIGINT ignored
    proxy.start()
    while not proxy.signalled and not proxy.shutdown.wait(0.5):
        pass
    proxy.shutdown.set()
    proxy.stop_all()
    # No daemon thread may be mid-write when the interpreter goes away, but a client
    # that stopped reading must not keep us from leaving either.
    proxy.out_lock.acquire(timeout=2)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(0)


if __name__ == "__main__":
    main()
