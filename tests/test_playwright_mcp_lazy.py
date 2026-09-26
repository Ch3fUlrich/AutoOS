#!/usr/bin/env python3
"""Tests for tools/playwright_mcp_lazy.py, the per-session Playwright MCP proxy.

The proxy is a stdio MCP server that starts its backend (a Playwright MCP
container) on the first real request and stops it when the session goes quiet.
Every test drives the real proxy as a subprocess, over stdio, against a fake
backend that speaks MCP and writes one line per event to a spawn log. No docker,
no real browser, no network: the docker case uses a stub `docker` on PATH.

Run:  python3 tests/test_playwright_mcp_lazy.py
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROXY = os.path.join(ROOT, "tools", "playwright_mcp_lazy.py")

DOCKER_DEFAULT = ["docker", "run", "-i", "--rm", "--init", "--network", "host",
                  "--name", "x", "mcr.microsoft.com/playwright/mcp:latest"]

# The fake backend. Behaviour switches come from the environment (the proxy hands
# its own environment to the backend it spawns):
#   FAKE_LOG          spawn log: one JSON object per line, event start/init/...
#   FAKE_CRASH_AFTER  N: exit without answering the Nth tools/call
#   FAKE_IGNORE_EOF   1: keep running after stdin closes (a container that lingers)
#   FAKE_VERSION      serverInfo.version (default 1.0.0)
#   FAKE_EXTRA_TOOL   1: list a fourth tool
#   FAKE_GARBAGE      1: print a non-JSON line on stdout at start
#   FAKE_NO_INIT      1: never answer initialize
#   FAKE_ROOTS        1: right after notifications/initialized, ask the client for
#                        roots/list (id FAKE_ROOTS_ID, default 1); every answer that
#                        comes back is logged as an "answer" event
#   FAKE_HOLD_TOOLS   1: do not answer tools/list until an answer to that roots/list arrived
FAKE_BACKEND = r'''
import json, os, sys, threading, time

LOG = os.environ.get("FAKE_LOG")
LOG_LOCK = threading.Lock()
OUT_LOCK = threading.Lock()
CALLS = [0]
PENDING = {}
HELD = []
ANSWERED = [False]
CRASH_AFTER = int(os.environ.get("FAKE_CRASH_AFTER") or 0)
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")


def log(event, **fields):
    if not LOG:
        return
    fields["event"] = event
    fields["pid"] = os.getpid()
    with LOG_LOCK:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(fields) + "\n")


def send_line(text):
    with OUT_LOCK:
        sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def send(obj):
    send_line(json.dumps(obj))


def reply(rid, result, **extra):
    msg = {"jsonrpc": "2.0", "id": rid, "result": result}
    msg.update(extra)
    send(msg)


def tools():
    names = ["browser_navigate", "browser_snapshot", "browser_close"]
    if os.environ.get("FAKE_EXTRA_TOOL") == "1":
        names.append("browser_extra")
    return [{"name": n, "description": n, "inputSchema": {"type": "object"}} for n in names]


def call(rid, params):
    args = params.get("arguments") or {}
    CALLS[0] += 1
    if CRASH_AFTER and CALLS[0] >= CRASH_AFTER:
        log("crash")
        os._exit(3)
    if params.get("name") == "nan":
        # Python's json.loads accepts these, a strict JSON parser does not; both are
        # lines a proxy that re-serialized or forwarded them would pass on.
        send_line('{"jsonrpc":"2.0","method":"notifications/message","params":{"data":NaN}}')
        send_line('{"jsonrpc":"2.0","id":%s,"result":{"v":-Infinity}}' % json.dumps(rid))
        reply(rid, {"content": [], "pid": os.getpid()})
        return
    if params.get("name") == "raw":
        # A deliberately non-canonical line: reversed key order, raw UTF-8, a
        # \u escape, odd spacing. Only a verbatim forwarder keeps it byte for byte.
        send_line('{"result" : {"content":[{"type":"text","text":"caf\\u00e9 é \U0001f600"}]} ,'
                  ' "id":%s,"jsonrpc":"2.0"}' % json.dumps(rid))
        return
    if args.get("notify"):
        send({"jsonrpc": "2.0", "method": "notifications/message",
              "params": {"level": "info", "data": args["notify"]}})
    asked = None
    if args.get("ask"):
        waiter = PENDING["srv-1"] = {"event": threading.Event(), "reply": None}
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
        waiter["event"].wait(10)
        asked = (waiter["reply"] or {}).get("result")
    time.sleep(float(args.get("sleep", 0)))
    big = int(args.get("big", 0))
    text = "x" * big if big else json.dumps(args, sort_keys=True)
    result = {"content": [{"type": "text", "text": text}], "isError": False,
              "pid": os.getpid(), "blob_len": len(args.get("blob", ""))}
    if asked is not None:
        result["asked"] = asked
    reply(rid, result, **{"x-extra": {"k": [1, 2, 3]}})


def handle(msg, text):
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}
    if method is None and rid is not None:
        log("answer", id=rid, result=msg.get("result"))
        ANSWERED[0] = True
        for held in HELD:
            reply(held, {"tools": tools()})
        del HELD[:]
    if method is None and rid in PENDING:
        PENDING[rid]["reply"] = msg
        PENDING[rid]["event"].set()
    elif method == "initialize":
        log("init", params=params, id=rid)
        if os.environ.get("FAKE_NO_INIT") == "1":
            return
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED else SUPPORTED[0]
        reply(rid, {"protocolVersion": version, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-playwright",
                                   "version": os.environ.get("FAKE_VERSION", "1.0.0")}})
    elif method == "notifications/initialized":
        log("initialized")
        if os.environ.get("FAKE_ROOTS") == "1":
            send({"jsonrpc": "2.0", "id": int(os.environ.get("FAKE_ROOTS_ID", "1")),
                  "method": "roots/list", "params": {"probe": os.getpid()}})
    elif method == "notifications/cancelled":
        log("cancelled", params=params)
    elif method == "tools/list":
        log("tools_list", id=rid, params=params)
        if os.environ.get("FAKE_HOLD_TOOLS") == "1" and not ANSWERED[0]:
            HELD.append(rid)
            return
        result = {"tools": tools()}
        if params.get("cursor") is not None:
            result["echoCursor"] = params["cursor"]
        reply(rid, result)
    elif method == "tools/call":
        log("call", id=rid, raw=text)
        threading.Thread(target=call, args=(rid, params), daemon=True).start()
    elif method == "ping":
        reply(rid, {})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such method"}})


log("start")
sys.stderr.write("fake-backend: stderr banner\n")
sys.stderr.flush()
if os.environ.get("FAKE_GARBAGE") == "1":
    send_line("this line is not json")
for raw in sys.stdin.buffer:
    line = raw.decode("utf-8").strip()
    if line:
        handle(json.loads(line), line)
log("eof")
if os.environ.get("FAKE_IGNORE_EOF") == "1":
    while True:
        time.sleep(1)
'''

DOCKER_STUB = r'''#!/bin/sh
# Stub docker: `run` becomes the fake backend, everything else is only logged.
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
    run) exec "$FAKE_PYTHON" "$FAKE_BACKEND" ;;
esac
exit 0
'''


def key(rid):
    return json.dumps(rid)


def _not_json(token):
    raise ValueError("%s is not JSON" % token)


def _finite(token):
    value = float(token)
    if not math.isfinite(value):
        _not_json(token)
    return value


def strict_loads(text):
    """json.loads as JSON defines it: NaN, Infinity, -Infinity and 1e999 are refused."""
    return json.loads(text, parse_constant=_not_json, parse_float=_finite)


def load_proxy_module():
    """The proxy module itself, for the tests that drive `Proxy` in-process."""
    spec = importlib.util.spec_from_file_location("playwright_mcp_lazy", PROXY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(condition, timeout=10.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


class Session(object):
    """One proxy process plus threads that collect its stdout and stderr."""

    def __init__(self, env, argv=None):
        self.proc = subprocess.Popen(argv or [sys.executable, PROXY],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env)
        self.lines = queue.Queue()
        self.raw = []          # every stdout line, as bytes
        self.messages = []     # parsed stdout lines (None when not JSON)
        self.invalid = []      # stdout lines that are not strict JSON
        self.stderr = []
        self.closed = False
        self.pumps = [threading.Thread(target=self._pump_stdout, daemon=True),
                      threading.Thread(target=self._pump_stderr, daemon=True)]
        for pump in self.pumps:
            pump.start()
        self._next_id = 100

    def _pump_stdout(self):
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw.rstrip(b"\n"))

    def _pump_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))

    def send(self, msg):
        data = msg if isinstance(msg, bytes) else json.dumps(msg).encode("utf-8")
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout=15.0):
        deadline = time.time() + timeout
        while True:
            for msg in self.messages:
                if msg is not None and predicate(msg):
                    return msg
            if time.time() > deadline:
                raise AssertionError("timed out; stdout so far: %r; stderr: %r"
                                     % (self.raw[-5:], self.stderr[-8:]))
            try:
                raw = self.lines.get(timeout=0.1)
            except queue.Empty:
                if self.proc.poll() is not None and self.lines.empty():
                    raise AssertionError("the proxy exited with %r; stderr: %r"
                                         % (self.proc.returncode, self.stderr[-8:]))
                continue
            self.raw.append(raw)
            self.messages.append(self._parse(raw))

    def _parse(self, raw):
        try:
            return strict_loads(raw.decode("utf-8"))
        except ValueError:
            self.invalid.append(raw)
            return None

    def response(self, rid, timeout=15.0):
        return self.wait_for(lambda m: isinstance(m, dict) and "method" not in m
                             and "id" in m and key(m["id"]) == key(rid), timeout)

    def request(self, method, params=None, rid=None, timeout=15.0):
        if rid is None:
            self._next_id += 1
            rid = self._next_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self.response(rid, timeout)

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)

    def initialize(self, version="2025-06-18", client=None, capabilities=None):
        params = {"protocolVersion": version,
                  "capabilities": capabilities if capabilities is not None else {},
                  "clientInfo": client or {"name": "claude-code", "version": "2.1.283"}}
        reply = self.request("initialize", params, rid=1)
        self.notify("notifications/initialized")
        return reply

    def drain(self):
        """Read whatever the proxy wrote before it exited."""
        for pump in self.pumps:
            pump.join(2)        # a stray backend that kept our pipes open must not hang the test
        while True:
            try:
                raw = self.lines.get_nowait()
            except queue.Empty:
                return
            self.raw.append(raw)
            self.messages.append(self._parse(raw))

    def close(self, timeout=15.0):
        if self.closed:
            return self.proc.returncode
        self.closed = True
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            code = None
        self.drain()
        if not any(pump.is_alive() for pump in self.pumps):
            self.proc.stdout.close()
            self.proc.stderr.close()
        return code


class LazyProxyCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pwlazy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.backend = os.path.join(self.tmp, "fake_backend.py")
        with open(self.backend, "w") as fh:
            fh.write(FAKE_BACKEND)
        self.log = os.path.join(self.tmp, "spawn.log")
        self.cache = os.path.join(self.tmp, "cache", "playwright-mcp-handshake.json")
        self.sessions = []
        self.addCleanup(self._reap)

    def _reap(self):
        for s in self.sessions:
            s.close(timeout=8)
        # a broken proxy must not leave fake backends behind
        for rec in self.events():
            if rec.get("event") == "start" and alive(rec["pid"]):
                try:
                    os.kill(rec["pid"], signal.SIGKILL)
                except OSError:
                    pass
        # every test: nothing but strict JSON, one message per line, ever reached the client
        bad = [raw for s in self.sessions for raw in s.invalid]
        if bad:
            raise AssertionError("the proxy wrote stdout lines that are not JSON: %r" % bad[:3])

    def env(self, idle=1, cmd="json", **extra):
        env = dict(os.environ)
        for name in list(env):
            if name.startswith(("AUTOOS_PLAYWRIGHT_", "FAKE_")):
                del env[name]
        env["AUTOOS_PLAYWRIGHT_IDLE_SECONDS"] = str(idle)
        env["AUTOOS_PLAYWRIGHT_MCP_CACHE"] = self.cache
        env["FAKE_LOG"] = self.log
        if cmd == "json":
            env["AUTOOS_PLAYWRIGHT_MCP_CMD"] = json.dumps([sys.executable, self.backend])
        elif cmd is not None:
            env["AUTOOS_PLAYWRIGHT_MCP_CMD"] = cmd
        env.update(extra)
        return env

    def session(self, idle=1, cmd="json", **extra):
        s = Session(self.env(idle, cmd, **extra))
        self.sessions.append(s)
        return s

    def events(self, kind=None):
        try:
            with open(self.log) as fh:
                recs = [json.loads(line) for line in fh if line.strip()]
        except IOError:
            return []
        return [r for r in recs if kind is None or r["event"] == kind]

    def starts(self):
        return [r["pid"] for r in self.events("start")]

    def prime_cache(self, **extra):
        """A cold session writes the cache; the spawn log is emptied afterwards."""
        s = self.session(idle=30, **extra)
        s.initialize()
        s.request("tools/list", {})
        self.assertEqual(s.close(), 0)
        self.assertTrue(os.path.exists(self.cache), "the cold session wrote no cache")
        open(self.log, "w").close()

    def call(self, s, args=None, rid=None, name="browser_navigate", timeout=15.0):
        return s.request("tools/call", {"name": name, "arguments": args or {}},
                         rid=rid, timeout=timeout)

    def gone(self, pid, timeout=10.0):
        return wait_until(lambda: not alive(pid), timeout)


class CacheAndLazyStart(LazyProxyCase):
    def test_cached_handshake_never_spawns_the_backend(self):
        self.prime_cache()
        s = self.session()
        init = s.initialize()
        tools = s.request("tools/list", {})
        pong = s.request("ping")
        self.assertEqual(init["result"]["serverInfo"]["name"], "fake-playwright")
        self.assertEqual(len(tools["result"]["tools"]), 3)
        self.assertEqual(pong["result"], {})
        time.sleep(0.6)
        self.assertEqual(self.starts(), [], "a cached handshake must not start the backend")

    def test_cold_cache_starts_once_and_writes_a_private_cache(self):
        self.assertFalse(os.path.exists(self.cache))
        s = self.session(idle=30)
        s.initialize()
        tools = s.request("tools/list", {})
        time.sleep(0.3)
        self.assertEqual(len(self.events("initialized")), 1,
                         "the client's own notifications/initialized must not be forwarded")
        self.assertEqual([t["name"] for t in tools["result"]["tools"]],
                         ["browser_navigate", "browser_snapshot", "browser_close"])
        self.assertEqual(len(self.starts()), 1)
        self.assertTrue(os.path.exists(self.cache))
        self.assertEqual(stat.S_IMODE(os.stat(self.cache).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.cache)),
                         [os.path.basename(self.cache)], "a temp file was left behind")
        with open(self.cache) as fh:
            self.assertIsInstance(json.load(fh), dict)
        s.close()
        second = self.session()
        second.initialize()
        self.assertEqual(len(second.request("tools/list", {})["result"]["tools"]), 3)
        second.request("ping")
        time.sleep(0.4)
        self.assertEqual(len(self.starts()), 1, "a later session must not start the backend")

    def test_tools_list_on_a_cold_cache_starts_the_backend_once(self):
        s = self.session(idle=30)
        reply = s.request("tools/list", {})
        self.assertEqual(len(reply["result"]["tools"]), 3)
        self.assertEqual(len(self.starts()), 1)
        self.assertTrue(os.path.exists(self.cache))
        self.assertEqual(len(s.request("tools/list", {})["result"]["tools"]), 3)
        self.assertEqual(len(self.starts()), 1)

    def test_tools_call_starts_lazily_and_answers_verbatim(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        s.request("tools/list", {})
        self.assertEqual(self.starts(), [])
        for rid in (7, "call-A", 9007199254740993):
            reply = self.call(s, {"url": "http://localhost:1/"}, rid=rid)
            self.assertEqual(len(self.starts()), 1)
            pid = self.starts()[0]
            self.assertEqual(reply, {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": '{"url": "http://localhost:1/"}'}],
                           "isError": False, "pid": pid, "blob_len": 0},
                "x-extra": {"k": [1, 2, 3]}})
            self.assertEqual(key(reply["id"]), key(rid))

    def test_the_backend_line_is_forwarded_byte_for_byte(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        self.call(s, name="raw", rid="r-1")
        expected = ('{"result" : {"content":[{"type":"text","text":"caf\\u00e9 é \U0001f600"}]} ,'
                    ' "id":"r-1","jsonrpc":"2.0"}').encode("utf-8")
        self.assertIn(expected, s.raw)

    def test_a_request_reaches_the_backend_byte_for_byte(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        line = ('{"params" : {"name":"browser_navigate","arguments":{"u":"caf\\u00e9 é \U0001f600"}} ,'
                ' "method":"tools/call", "id" : 12, "jsonrpc":"2.0"}')
        s.send(line.encode("utf-8"))
        s.response(12)
        self.assertEqual([r["raw"] for r in self.events("call")], [line])

    def test_a_tools_list_with_a_cursor_goes_to_the_backend(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        reply = s.request("tools/list", {"cursor": "page-2"})
        self.assertEqual(reply["result"].get("echoCursor"), "page-2")
        self.assertEqual(len(self.starts()), 1)


    def test_other_requests_are_forwarded_and_their_answers_come_back_verbatim(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        for rid, method in (("r1", "resources/list"), (77, "prompts/list")):
            reply = s.request(method, {}, rid=rid)
            self.assertEqual(reply, {"jsonrpc": "2.0", "id": rid,
                                     "error": {"code": -32601, "message": "no such method"}})
        self.assertEqual(len(self.starts()), 1)

    def test_a_request_from_the_backend_and_the_clients_answer_are_forwarded(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        s.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"ask": True}}})
        ask = s.wait_for(lambda m: m.get("method") == "roots/list")
        # the client sees a proxy-unique id, never the backend's own "srv-1" ...
        self.assertEqual({k: v for k, v in ask.items() if k != "id"},
                         {"jsonrpc": "2.0", "method": "roots/list"})
        self.assertIsInstance(ask["id"], str)
        self.assertNotEqual(ask["id"], "srv-1", "the backend's request id reached the client unchanged")
        # ... and the backend gets its answer under the id it asked with
        s.send({"jsonrpc": "2.0", "id": ask["id"], "result": {"roots": [{"uri": "file:///w"}]}})
        reply = s.response(3)
        self.assertEqual(reply["result"]["asked"], {"roots": [{"uri": "file:///w"}]})


class Startup(LazyProxyCase):
    """The backend starts on a thread of its own: the client keeps being heard meanwhile."""

    def test_a_backend_that_asks_the_client_while_it_starts_does_not_stall_the_session(self):
        self.prime_cache()
        # A changed serverInfo makes the replay fetch tools/list, and this backend holds
        # that answer back until the client has answered its roots/list.
        s = self.session(idle=30, FAKE_VERSION="2.0.0", FAKE_ROOTS="1", FAKE_HOLD_TOOLS="1",
                         AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS="4")
        s.initialize()
        s.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"n": 5}}})
        ask = s.wait_for(lambda m: m.get("method") == "roots/list", 10)
        # The start is now blocked on the client's answer. The client must still be heard:
        self.assertEqual(s.request("ping", timeout=2)["result"], {},
                         "the proxy stopped reading the client while the backend started")
        s.send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"n": 6}}})
        roots = {"roots": [{"uri": "file:///work"}]}
        s.send({"jsonrpc": "2.0", "id": ask["id"], "result": roots})
        for rid in (5, 6):
            self.assertNotIn("error", s.response(rid, 10))
        self.assertEqual(len(self.starts()), 1, "the queued request must wait for the same start")
        self.assertEqual([r["id"] for r in self.events("call")], [5, 6], "the queue lost its order")
        self.assertEqual([a["result"] for a in self.events("answer")], [roots],
                         "the client's answer did not reach the backend during its start")
        self.assertEqual(len(self.events("tools_list")), 1)

    def test_requests_queued_behind_a_failed_start_fail_together_and_the_next_one_starts_again(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_NO_INIT="1", AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS="1.5")
        s.initialize()
        for rid in (21, 22):
            s.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": "browser_navigate", "arguments": {}}})
        for rid in (21, 22):
            err = s.response(rid, 10)
            self.assertEqual(err["error"]["code"], -32000)
            self.assertIn("did not answer", err["error"]["message"])
        self.assertEqual(len(self.starts()), 1, "each queued request started a backend of its own")
        self.assertEqual(self.call(s, rid=23, timeout=10)["error"]["code"], -32000)
        self.assertEqual(len(self.starts()), 2, "a request after the failure must try again")


class StarterSurvivesBugs(unittest.TestCase):
    """The starter thread's own guard: a bug must fail the queue, not leave the proxy unable to start."""

    def test_an_unexpected_error_while_starting_fails_the_queue_and_frees_the_starter(self):
        module = load_proxy_module()
        tmp = tempfile.mkdtemp(prefix="pwlazy-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = io.BytesIO()
        with mock.patch.dict(os.environ, {"AUTOOS_PLAYWRIGHT_MCP_CACHE": os.path.join(tmp, "c", "h.json")}):
            proxy = module.Proxy(io.BytesIO(), out)

        def boom():
            raise RuntimeError("a bug in the start")
        proxy.ensure_backend = boom

        def answers():
            return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        with contextlib.redirect_stderr(io.StringIO()) as noise:
            for rid in (1, 2):
                proxy.on_client_line(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                                                 "params": {"name": "browser_navigate"}}).encode())
                self.assertTrue(wait_until(lambda: len(answers()) == rid, 5),
                                "request %d was never answered: %r" % (rid, answers()))
        self.assertEqual([a["id"] for a in answers()], [1, 2])
        for answer in answers():
            self.assertEqual(answer["error"]["code"], -32603)
        self.assertIn("internal error while starting the backend (RuntimeError)", noise.getvalue())
        self.assertTrue(wait_until(lambda: proxy.starter is None, 5), "the starter flag stayed set")


class BackendRequestIds(LazyProxyCase):
    """A request the backend sends to the client carries an id that names its backend generation."""

    ROOTS_A = {"roots": [{"uri": "file:///from-the-first-backend"}]}
    ROOTS_B = {"roots": [{"uri": "file:///from-the-second-backend"}]}

    def asks(self, s):
        return [m for m in s.messages if m and m.get("method") == "roots/list"]

    def test_a_late_answer_to_a_stopped_backends_request_never_reaches_the_next_backend(self):
        self.prime_cache()
        s = self.session(idle=1, FAKE_ROOTS="1")        # every backend asks with id 1
        s.initialize()
        pid_a = self.call(s, rid=5)["result"]["pid"]
        s.wait_for(lambda m: len(self.asks(s)) >= 1)
        self.assertTrue(self.gone(pid_a, 10), "the first backend was not stopped when idle")
        pid_b = self.call(s, rid=6)["result"]["pid"]
        self.assertNotEqual(pid_a, pid_b)
        s.wait_for(lambda m: len(self.asks(s)) >= 2)
        ask_a, ask_b = self.asks(s)[:2]
        # the client answers the stopped backend's request, then the live one's
        s.send({"jsonrpc": "2.0", "id": ask_a["id"], "result": self.ROOTS_A})
        s.send({"jsonrpc": "2.0", "id": ask_b["id"], "result": self.ROOTS_B})
        self.assertTrue(wait_until(lambda: self.events("answer"), 5))
        time.sleep(0.4)     # a wrongly forwarded answer would have arrived by now
        self.assertEqual([(a["pid"], a["id"], a["result"]) for a in self.events("answer")],
                         [(pid_b, 1, self.ROOTS_B)],
                         "only the live backend's answer may arrive, under the id it asked with")
        self.assertNotEqual(ask_a["id"], ask_b["id"], "two backends asked the client under one id")
        self.assertIsInstance(ask_b["id"], str)
        self.assertEqual(ask_b["params"], {"probe": pid_b}, "the request's parameters were lost")
        self.assertTrue(wait_until(lambda: any("gone" in l and l.startswith("playwright-lazy:")
                                               for l in s.stderr), 3),
                        "the dropped answer was not reported: %r" % s.stderr)
        self.assertEqual(len([l for l in s.stderr if "gone" in l]), 1, "expected exactly one line")
        self.assertEqual(s.request("ping")["result"], {})
        self.assertFalse([m for m in s.messages if m and m.get("id") in (ask_a["id"], ask_b["id"])
                          and "method" not in m], "the proxy replied to the client's answer")

    def test_an_answer_with_an_id_the_proxy_never_issued_is_dropped(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_ROOTS="1")
        s.initialize()
        self.call(s, rid=5)
        ask = s.wait_for(lambda m: m.get("method") == "roots/list")
        for stray in (1, "autoos-s1-99", "nonsense"):
            s.send({"jsonrpc": "2.0", "id": stray, "result": {"roots": []}})
        self.assertTrue(wait_until(lambda: len([l for l in s.stderr if "dropped" in l]) == 3, 5), s.stderr)
        self.assertEqual(s.request("ping")["result"], {})
        self.assertEqual(self.events("answer"), [], "an answer nobody asked for reached the backend")
        # the real one still goes through, once; a second copy is dropped as well
        s.send({"jsonrpc": "2.0", "id": ask["id"], "result": self.ROOTS_A})
        s.send({"jsonrpc": "2.0", "id": ask["id"], "result": self.ROOTS_B})
        self.assertTrue(wait_until(lambda: len(self.events("answer")) == 1, 5))
        time.sleep(0.3)
        self.assertEqual([a["result"] for a in self.events("answer")], [self.ROOTS_A])


class DuplicateIds(LazyProxyCase):
    """A client id that is still in flight cannot be used again.

    Two calls under one id would leave the slower one untracked, and the idle stop
    could then kill it without an error ever reaching the client."""

    def slow_call(self, s, rid, seconds):
        s.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"sleep": seconds}}})

    def refusal(self, s, rid):
        return s.wait_for(lambda m: m.get("id") == rid and "error" in m, 5)

    def test_a_second_call_with_an_id_in_flight_is_refused_and_the_first_still_completes(self):
        self.prime_cache()
        s = self.session(idle=1)
        s.initialize()
        self.slow_call(s, 7, 3.5)
        self.assertTrue(wait_until(lambda: len(self.events("call")) == 1, 10))
        pid = self.starts()[0]
        self.slow_call(s, 7, 0)        # the fast twin: its answer would end the tracking of the slow one
        refusal = self.refusal(s, 7)
        self.assertEqual(refusal["error"]["code"], -32600)
        self.assertIn("duplicate request id in flight", refusal["error"]["message"])
        time.sleep(2.6)     # the idle period is 1 s: only a call that is still tracked keeps the backend
        self.assertTrue(alive(pid), "the backend was stopped under a call that was still running")
        first = s.wait_for(lambda m: m.get("id") == 7 and "result" in m, 10)
        self.assertEqual(first["result"]["pid"], pid)
        self.assertEqual(len(self.events("call")), 1, "the duplicate was forwarded to the backend")
        self.assertEqual(len([m for m in s.messages if m and m.get("id") == 7]), 2,
                         "expected one refusal and one answer under the id")

    def test_a_duplicate_of_a_request_that_still_waits_for_the_backend_is_refused_too(self):
        self.prime_cache()
        # a changed serverInfo makes the start fetch tools/list, held until the client answers roots/list
        s = self.session(idle=30, FAKE_VERSION="2.0.0", FAKE_ROOTS="1", FAKE_HOLD_TOOLS="1",
                         AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS="8")
        s.initialize()
        self.slow_call(s, 5, 0)
        ask = s.wait_for(lambda m: m.get("method") == "roots/list", 10)
        self.slow_call(s, 5, 0)        # the first one is queued behind the start
        self.assertEqual(self.refusal(s, 5)["error"]["code"], -32600)
        s.send({"jsonrpc": "2.0", "id": ask["id"], "result": {"roots": []}})
        self.assertNotIn("error", s.wait_for(lambda m: m.get("id") == 5 and "result" in m, 10))
        self.assertEqual(len(self.events("call")), 1, "the duplicate was forwarded to the backend")

    def test_a_ping_with_an_id_in_flight_is_refused_as_well(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        self.slow_call(s, 7, 1.5)
        self.assertTrue(wait_until(lambda: len(self.events("call")) == 1, 10))
        s.send({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(self.refusal(s, 7)["error"]["code"], -32600)
        self.assertIn("result", s.wait_for(lambda m: m.get("id") == 7 and "result" in m, 10))

    def test_an_id_is_free_again_once_the_backend_has_answered(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        for _ in range(2):
            s.messages.clear()      # `response` returns the first message with the id: forget the last answer
            self.assertNotIn("error", self.call(s, rid=7))

    def test_an_id_is_free_again_once_a_cold_start_answered_locally(self):
        s = self.session(idle=30)
        for _ in range(2):      # the first initialize waits for the backend and is answered from the cache
            reply = s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "c", "version": "1"}}, rid=1)
            self.assertNotIn("error", reply)
            s.messages.clear()

    def test_an_id_is_free_again_after_a_failed_start(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_NO_INIT="1", AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS="1")
        s.initialize()
        for _ in range(2):
            s.messages.clear()
            self.assertEqual(self.call(s, rid=21, timeout=10)["error"]["code"], -32000,
                             "the id was refused as a duplicate after its request had failed")


class IdIsFreeBeforeTheReply(unittest.TestCase):
    """The id is given back before the reply that ends its request is written, so a client
    that retries the same id the moment the reply arrives is never refused."""

    def setUp(self):
        self.module = load_proxy_module()
        tmp = tempfile.mkdtemp(prefix="pwlazy-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"AUTOOS_PLAYWRIGHT_MCP_CACHE": os.path.join(tmp, "c", "h.json")}):
            self.proxy = self.module.Proxy(io.BytesIO(), io.BytesIO())
        self.free_at_reply = []
        self.replied = threading.Event()

        def spy(raw):
            message = json.loads(raw)
            with self.proxy.lock:
                self.free_at_reply.append((message["id"], not self.proxy.in_flight(self.module.id_key(message["id"]))))
            self.replied.set()
        self.proxy.write_client = spy
        self.dead = self.module.Backend(1, ["unused"], False, "unused")     # alive is False: never usable

    def ask(self, method, params, ensure_backend):
        self.proxy.ensure_backend = ensure_backend
        line = json.dumps({"jsonrpc": "2.0", "id": 31, "method": method, "params": params}).encode()
        with contextlib.redirect_stderr(io.StringIO()):
            self.proxy.on_client_line(line)
            self.assertTrue(self.replied.wait(5), "request 31 was never answered")
        self.assertEqual(self.free_at_reply, [(31, True)], "the id was still in flight when its reply was written")

    def test_a_failed_start(self):
        def refuse():
            raise self.module.StartError("no backend today")
        self.ask("tools/call", {"name": "browser_navigate"}, refuse)

    def test_a_backend_that_kept_stopping(self):
        self.ask("tools/call", {"name": "browser_navigate"}, lambda: self.dead)

    def test_an_answer_from_the_cache_after_a_cold_start(self):
        def cold_start():
            self.proxy.cache.initialize = {"protocolVersion": "2025-06-18", "capabilities": {}}
            return self.dead
        self.ask("initialize", {"protocolVersion": "2025-06-18"}, cold_start)


class Negotiation(LazyProxyCase):
    def test_the_protocol_version_follows_the_client_when_the_server_accepted_it(self):
        self.prime_cache()  # cached under 2025-06-18
        s = self.session()
        self.assertEqual(s.initialize("2025-06-18")["result"]["protocolVersion"], "2025-06-18")
        s.close()
        # 2025-03-26 was not accepted yet: the cached version is kept
        s = self.session()
        self.assertEqual(s.initialize("2025-03-26")["result"]["protocolVersion"], "2025-06-18")
        self.call(s)  # a real start negotiates 2025-03-26 with the backend
        s.close()
        self.assertEqual(self.events("init")[0]["params"]["protocolVersion"], "2025-03-26")
        # the backend accepted it, so a later session gets it back
        s = self.session()
        self.assertEqual(s.initialize("2025-03-26")["result"]["protocolVersion"], "2025-03-26")
        s.close()
        s = self.session()
        self.assertEqual(s.initialize("1999-01-01")["result"]["protocolVersion"], "2025-06-18")

    def test_a_replay_refetches_tools_only_when_the_server_info_changed(self):
        self.prime_cache()
        s = self.session()
        s.initialize()
        self.call(s)
        s.close()
        self.assertEqual(self.events("tools_list"), [], "an unchanged server must not be asked for tools again")
        open(self.log, "w").close()
        inode = os.stat(self.cache).st_ino
        s = self.session(FAKE_VERSION="2.0.0", FAKE_EXTRA_TOOL="1")
        s.initialize()
        self.assertEqual(len(s.request("tools/list", {})["result"]["tools"]), 3)  # cache first
        self.call(s)
        s.close()
        self.assertEqual(len(self.events("tools_list")), 1)
        self.assertNotEqual(os.stat(self.cache).st_ino, inode, "the cache was rewritten in place, not replaced")
        self.assertEqual(stat.S_IMODE(os.stat(self.cache).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.cache)), [os.path.basename(self.cache)])
        s = self.session()
        s.initialize()
        self.assertEqual(len(s.request("tools/list", {})["result"]["tools"]), 4)
        with open(self.cache) as fh:
            self.assertIn("2.0.0", fh.read())


class Idle(LazyProxyCase):
    def stub_docker(self):
        """A docker on PATH that only records its calls: it must stay unused here."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        dlog = os.path.join(self.tmp, "docker.log")
        path = os.path.join(bindir, "docker")
        with open(path, "w") as fh:
            fh.write(DOCKER_STUB)
        os.chmod(path, 0o755)
        return bindir, dlog

    def test_idle_stop_releases_the_backend_and_the_proxy_keeps_answering(self):
        bindir, dlog = self.stub_docker()
        self.prime_cache()
        s = self.session(idle=2, PATH=bindir + os.pathsep + os.environ["PATH"],
                         FAKE_DOCKER_LOG=dlog)
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        self.assertTrue(alive(pid))
        time.sleep(0.5)
        self.assertTrue(alive(pid), "stopped before the idle period was over")
        self.assertTrue(self.gone(pid, 10), "the backend is still running long after the idle period")
        self.assertEqual(s.request("ping")["result"], {})
        self.assertEqual(len(s.request("tools/list", {})["result"]["tools"]), 3)
        self.assertFalse(os.path.exists(dlog), "a custom backend command must not touch docker")

    def test_the_idle_timer_is_not_armed_while_a_call_is_in_flight(self):
        self.prime_cache()
        s = self.session(idle=1)
        s.initialize()
        s.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"sleep": 3.5}}})
        self.assertTrue(wait_until(lambda: len(self.starts()) == 1, 10))
        pid = self.starts()[0]
        time.sleep(2.6)
        self.assertTrue(alive(pid), "the backend was stopped under a call that was still running")
        reply = s.response(5)
        self.assertNotIn("error", reply)
        time.sleep(0.5)     # half an idle period after completion: the timer restarted with the call
        self.assertTrue(alive(pid), "the idle period must restart when the call completes")
        self.assertTrue(self.gone(pid, 10))

    def test_ping_does_not_keep_the_backend_open(self):
        self.prime_cache()
        s = self.session(idle=1)
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        deadline = time.time() + 4
        while time.time() < deadline and alive(pid):
            self.assertEqual(s.request("ping")["result"], {})
            time.sleep(0.2)
        self.assertFalse(alive(pid), "keep-alive pings answered by the proxy held the backend open")

    def test_restart_after_idle_replays_the_original_handshake(self):
        self.prime_cache()
        client = {"name": "unit-client", "version": "9.9"}
        caps = {"roots": {"listChanged": True}}
        s = self.session(idle=1)
        s.initialize("2025-03-26", client=client, capabilities=caps)
        first = self.call(s)["result"]["pid"]
        self.assertTrue(self.gone(first, 10))
        second = self.call(s)["result"]["pid"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.starts()), 2)
        inits = self.events("init")
        self.assertEqual(len(inits), 2)
        for rec in inits:
            self.assertEqual(rec["params"], {"protocolVersion": "2025-03-26",
                                             "capabilities": caps, "clientInfo": client})
            self.assertTrue(str(rec["id"]).startswith("autoos-init-"))
        self.assertEqual(len(self.events("initialized")), 2)
        for raw in s.raw:
            self.assertNotIn(b"autoos-init", raw)
            self.assertNotIn(b"autoos-tools", raw)

    def test_docker_fallback_stops_the_container_by_name(self):
        bindir, dlog = self.stub_docker()
        s = self.session(idle=1, cmd=None, PATH=bindir + os.pathsep + os.environ["PATH"],
                         FAKE_DOCKER_LOG=dlog, FAKE_PYTHON=sys.executable,
                         FAKE_BACKEND=self.backend, FAKE_IGNORE_EOF="1")
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        name = "autoos-pw-%d-1" % s.proc.pid
        self.assertTrue(self.gone(pid, 20), "the lingering backend was never terminated")
        def docker_lines():
            try:
                with open(dlog) as fh:
                    return [l.strip() for l in fh if l.strip()]
            except IOError:
                return []
        self.assertTrue(wait_until(lambda: any(l.startswith("stop ") for l in docker_lines()), 10))
        lines = docker_lines()
        self.assertEqual(lines[0], "run -i --rm --init --network host --name %s "
                                   "mcr.microsoft.com/playwright/mcp:latest" % name)
        self.assertIn("stop %s" % name, lines)


class Failures(LazyProxyCase):
    def test_a_crash_mid_request_fails_every_call_in_flight_and_the_next_restarts(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_CRASH_AFTER="2")
        s.initialize()
        s.send({"jsonrpc": "2.0", "id": "slow", "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"sleep": 6}}})
        self.assertTrue(wait_until(lambda: len(self.starts()) == 1, 10))
        time.sleep(0.3)
        s.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {}}})
        for rid in ("slow", 2):
            err = s.response(rid, 10)
            self.assertEqual(err["error"]["code"], -32000)
            self.assertEqual(err["error"]["message"], "playwright backend exited")
        reply = self.call(s, rid=3)
        self.assertNotIn("error", reply)
        self.assertEqual(len(self.starts()), 2)
        self.assertNotEqual(reply["result"]["pid"], self.starts()[0])

    def test_a_backend_that_cannot_start_fails_the_request_and_the_proxy_stays_up(self):
        self.prime_cache()
        s = self.session(cmd=json.dumps(["/nonexistent/definitely-not-a-backend"]))
        s.initialize()
        for rid in (11, 12):
            err = self.call(s, rid=rid)
            self.assertEqual(err["error"]["code"], -32000)
            self.assertIn("could not start", err["error"]["message"])
        self.assertEqual(s.request("ping")["result"], {})
        self.assertIsNone(s.proc.poll())

    def test_a_cold_cache_with_a_backend_that_cannot_start_answers_initialize_with_an_error(self):
        s = self.session(cmd=json.dumps(["/nonexistent/definitely-not-a-backend"]))
        reply = s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "c", "version": "1"}}, rid=1)
        self.assertEqual(reply["error"]["code"], -32000)
        self.assertEqual(s.request("ping")["result"], {})

    def test_a_backend_that_never_answers_initialize_times_out(self):
        self.prime_cache()
        s = self.session(FAKE_NO_INIT="1", AUTOOS_PLAYWRIGHT_HANDSHAKE_SECONDS="1")
        s.initialize()
        started = time.time()
        err = self.call(s, rid=21, timeout=20)
        self.assertLess(time.time() - started, 10)
        self.assertEqual(err["error"]["code"], -32000)
        self.assertIn("did not answer", err["error"]["message"])
        self.assertTrue(self.gone(self.starts()[0], 10), "the hung backend was left running")
        self.assertEqual(s.request("ping")["result"], {})


class Shutdown(LazyProxyCase):
    # The fake ignores EOF here, as a lingering container would: only an explicit stop
    # (5 s of grace after the EOF, then terminate) can end it, so an exit that merely
    # closes the pipes does not pass.
    def test_stdin_eof_stops_the_backend_and_exits_zero(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_IGNORE_EOF="1")
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        self.assertEqual(s.close(), 0)
        self.assertTrue(self.gone(pid, 2), "the backend outlived the session")

    def test_sigterm_stops_the_backend_and_exits_zero(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_IGNORE_EOF="1")
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        s.proc.send_signal(signal.SIGTERM)
        self.assertEqual(s.proc.wait(20), 0)
        self.assertTrue(self.gone(pid, 2), "the backend outlived the session")

    def test_sigint_stops_the_backend_and_exits_zero(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_IGNORE_EOF="1")
        s.initialize()
        pid = self.call(s)["result"]["pid"]
        s.proc.send_signal(signal.SIGINT)
        self.assertEqual(s.proc.wait(20), 0)
        self.assertTrue(self.gone(pid, 2), "the backend outlived the session")


class Streams(LazyProxyCase):
    def test_stdout_is_json_rpc_only_and_backend_stderr_stays_on_stderr(self):
        self.prime_cache()
        s = self.session(idle=30, FAKE_GARBAGE="1", AUTOOS_SECRET_PROBE="canary-value-for-the-leak-check")
        s.initialize()
        self.call(s, rid=41)
        s.request("tools/list", {})
        s.close()
        self.assertTrue(s.raw)
        for raw in s.raw:
            msg = json.loads(raw.decode("utf-8"))
            self.assertEqual(msg.get("jsonrpc"), "2.0")
            self.assertNotIn(b"stderr banner", raw)
            self.assertNotIn(b"not json", raw)
        wait_until(lambda: any("stderr banner" in l for l in s.stderr), 3)
        self.assertTrue(any(l == "fake-backend: stderr banner" for l in s.stderr))
        for line in s.stderr:
            self.assertTrue(line == "fake-backend: stderr banner" or line.startswith("playwright-lazy:"),
                            "an unprefixed line on stderr: %r" % line)
        self.assertTrue(any(l.startswith("playwright-lazy:") for l in s.stderr),
                        "the dropped non-JSON backend line was not reported")
        for text in s.stderr + [r.decode("utf-8") for r in s.raw]:
            self.assertNotIn("canary-value-for-the-leak-check", text)

    def test_nan_and_infinity_are_a_parse_error_in_both_directions(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        bad_lines = [
            b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":Infinity,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":-Infinity,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":1e999,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"arguments":{"x":NaN}}}',
        ]

        def errors():
            return [m for m in s.messages if m and m.get("id") is None and "error" in m]
        for n, line in enumerate(bad_lines, 1):
            s.send(line)
            s.wait_for(lambda m: len(errors()) >= n, 3)
        self.assertEqual([e["error"]["code"] for e in errors()], [-32700] * len(bad_lines))
        self.assertEqual(s.request("ping")["result"], {}, "the proxy did not survive")
        self.assertEqual(self.starts(), [], "a line that is not JSON must not reach a backend")
        # and a backend that prints such numbers is not repeated to the client
        reply = self.call(s, name="nan", rid=8)
        self.assertEqual(reply["result"]["content"], [])
        self.assertEqual([m.get("method") for m in s.messages if m and "method" in m], [])
        self.assertTrue(wait_until(lambda: len([l for l in s.stderr if "dropped a backend line" in l]) == 2, 3),
                        s.stderr)
        self.assertEqual(s.invalid, [])

    def test_the_backend_command_env_takes_a_json_list_or_a_quoted_string(self):
        spaced = os.path.join(self.tmp, "with space")
        os.makedirs(spaced)
        target = os.path.join(spaced, "fake backend.py")
        shutil.copy(self.backend, target)
        quoted = " ".join(shlex_quote(p) for p in (sys.executable, target))
        for value in (json.dumps([sys.executable, target]), quoted):
            s = self.session(cmd=None, AUTOOS_PLAYWRIGHT_MCP_CMD=value)
            s.initialize()
            self.assertNotIn("error", self.call(s))
            self.assertEqual(len(self.starts()), 1)
            s.close()
            open(self.log, "w").close()


class Concurrency(LazyProxyCase):
    def test_three_overlapping_slow_calls_are_all_answered(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        started = time.time()
        for rid in (61, 62, 63):
            s.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": "browser_navigate", "arguments": {"sleep": 1.5, "n": rid}}})
        for rid in (61, 62, 63):
            reply = s.response(rid, 20)
            self.assertEqual(reply["result"]["content"][0]["text"], json.dumps({"n": rid, "sleep": 1.5}, sort_keys=True))
        self.assertLess(time.time() - started, 3.8, "the calls ran one after the other")
        self.assertEqual(len(self.starts()), 1)

    def test_notifications_are_forwarded_both_ways_and_dropped_without_a_backend(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        s.notify("notifications/cancelled", {"requestId": 41, "reason": "no backend yet"})
        time.sleep(0.5)
        self.assertEqual(self.starts(), [], "a notification must not start the backend")
        self.assertEqual(self.events("cancelled"), [])
        self.call(s, {"notify": "hello"}, rid=31)
        note = s.wait_for(lambda m: m.get("method") == "notifications/message")
        self.assertEqual(note, {"jsonrpc": "2.0", "method": "notifications/message",
                                "params": {"level": "info", "data": "hello"}})
        s.notify("notifications/cancelled", {"requestId": 42, "reason": "user"})
        self.assertTrue(wait_until(lambda: len(self.events("cancelled")) == 1, 5))
        self.assertEqual(self.events("cancelled")[0]["params"], {"requestId": 42, "reason": "user"})

    def test_a_one_mebibyte_message_goes_through_both_ways(self):
        self.prime_cache()
        s = self.session(idle=30)
        s.initialize()
        mib = 1024 * 1024
        reply = self.call(s, {"big": mib, "blob": "y" * mib}, rid=51, timeout=30)
        self.assertEqual(reply["result"]["content"][0]["text"], "x" * mib)
        self.assertEqual(reply["result"]["blob_len"], mib)


class DefaultCommand(unittest.TestCase):
    def test_default_backend_command_is_the_documented_argv(self):
        module = load_proxy_module()
        self.assertEqual(module.default_backend_command("x"), DOCKER_DEFAULT)
        self.assertEqual(module.default_backend_command("autoos-pw-1-2")[-2], "autoos-pw-1-2")


def shlex_quote(text):
    import shlex
    return shlex.quote(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
