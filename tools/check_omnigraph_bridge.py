#!/usr/bin/env python3
"""Benchmark the Omnigraph MCP bridge, and decide D9 (npx or pre-installed).

SPEC-OMNI §B / plan row A5. D9 (docs/plans/2026-09-27-omnigraph-mcp-catalog-spec.md):
the catalog may start the bridge with `npx` only if **16 bridges started in
parallel each answer `health` in < 2 s with zero npm cache-lock errors** —
because N clients opening one repo spawn N `npx` processes that all fight over
the same npm cache. Anything else means a pre-installed, pinned bridge.

This tool starts N bridges, speaks MCP JSON-RPC to each over stdio, and reports
spawn -> `health` latency (min/median/max), the `whoami` graph check, and the
npm lock/cache errors seen on stderr. `--cold` gives every bridge one fresh
`npm_config_cache` (that is the contention case D9 is about); `--warm` primes
the default cache with one bridge, then measures the wave.

`--fake-server` starts a local stub on 127.0.0.1:<free port> that answers the
two paths below, so the logic is testable with no npm, no network and no token
(`tests/fixtures/omnigraph_bridge_stub.py` is the matching fake bridge, and CI
runs the pair). The live run against `<omnigraph-url>` is the operator step.

The HTTP paths the pinned bridge actually calls — read out of the package
source, not guessed (`npm pack` into a temp dir outside the repo, 2026-09-27):

  @modernrelay/omnigraph-mcp@0.8.0
    dist/bin.js:8,13              OMNIGRAPH_BASE_URL and OMNIGRAPH_GRAPH_ID are
                                  required; a missing one is `process.exit(1)`
                                  before any RPC, which reads as "Connection closed".
    dist/bin.js:20                OMNIGRAPH_TOKEN is passed as `token` (optional).
    dist/chunk-G3D6EKTW.js:87     the `health` tool  -> `og.health()`
    dist/chunk-G3D6EKTW.js:119    the `query` tool   -> `og.query({query,name,params,
                                  branch,snapshot})`, branch defaults to `main`
    dependency @modernrelay/omnigraph@0.8.0 (the HTTP client)
      dist/index.js:477           health(): `GET  /healthz`
      dist/index.js:485           query(): `POST /query`
      dist/index.js:159,253       buildUrl(): a graph id is required for everything
                                  except FLAT_PATHS {"/healthz","/graphs"}, which it
                                  prefixes as `/graphs/<urlencoded graphId><path>`.
      dist/index.js:203           `Authorization: Bearer <token>` when a token is set.
      dist/index.js:373,443       `rows`/`columns` are opaque: the server's row keys
                                  (`p.slug`) survive camelCase mapping untouched.
    @modelcontextprotocol/sdk@1.29.0
      dist/esm/shared/stdio.js:13,29   framing is newline-delimited JSON, and
                                       2025-03-26 is a supported protocol version.

  =>  GET  <base>/healthz                     (flat, unauthenticated)
      POST <base>/graphs/<graph-id>/query     {"query":…,"name":…,"branch":…}

Usage:
    python3 tools/check_omnigraph_bridge.py [--parallel N] [--cold | --warm]
        [--graph-id ID] [--bridge-cmd CMD] [--base-url URL] [--timeout S]
        [--limit-ms MS] [--npm-cache DIR] [--fake-server] [--fake-slug SLUG]
        [--json] [--root DIR]

The bridge command, the graph id and the base-url default come from this repo's
`.mcp.json` — nothing is spelled twice. `--bridge-cmd` overrides the command.
The token is read from `$OMNIGRAPH_TOKEN` only and never printed: every captured
stderr line is scrubbed of it before it is counted, echoed or serialised.

Exit codes:
    0   every bridge healthy, every whoami slug correct, and D9 met
    1   a bridge died, health was slow, the slug was wrong, or npm logged a
        lock/cache error (each named in the report)
    2   unusable input (no `.mcp.json` to read, an empty graph id, N < 1)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_VERSION = "2025-03-26"
WHOAMI_QUERY = "query whoami() { match { $p: Project } return { $p.slug, $p.repository } }"
WHOAMI_SLUG_KEY = "p.slug"
DEFAULT_PARALLEL = 16
DEFAULT_LIMIT_MS = 2000
DEFAULT_TIMEOUT_S = 60.0
LOCK_PATTERNS = ("EEXIST", "ENOTEMPTY", "lock")
REDACTED = "<redacted>"
FAKE_VERSION = "0.8.0-fake"


class Unusable(Exception):
    """Bad input: exit 2, never a failed benchmark."""


class BridgeError(Exception):
    """One bridge did not answer what the benchmark needs."""


def redact(text: str, token: str | None) -> str:
    """Scrub the token from anything that may reach the report."""
    if not text or not token:
        return text
    return text.replace(token, REDACTED)


# ── configuration: one home for the bridge command ────────────────────────────

def expand_env_reference(value: str) -> str:
    """The `:-` default of `${NAME:-default}` — the file's own fallback, not the host's env."""
    match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-([^}]*))?\}", (value or "").strip())
    if match is None:
        return (value or "").strip()
    return (match.group(1) or "").strip()


def read_bridge_config(root: Path) -> dict:
    """Return {cmd, base_url, graph_id} from `<root>/.mcp.json`'s omnigraph entry."""
    path = Path(root) / ".mcp.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise Unusable(f"cannot read {path}: {exc}"
                       " (pass --bridge-cmd and --graph-id to run without it)")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise Unusable(f"cannot parse {path}: {exc}")
    entry = (doc.get("mcpServers") or {}).get("omnigraph")
    if not isinstance(entry, dict) or not entry.get("command"):
        raise Unusable(f"{path}: no mcpServers.omnigraph command to benchmark")
    env = entry.get("env") or {}
    return {
        "cmd": [str(entry["command"]), *[str(a) for a in (entry.get("args") or [])]],
        "base_url": expand_env_reference(env.get("OMNIGRAPH_BASE_URL")),
        "graph_id": (env.get("OMNIGRAPH_GRAPH_ID") or "").strip(),
    }


# ── the fake server: the two paths above, nothing else ────────────────────────

class FakeOmnigraphServer:
    """A stdlib stub answering GET /healthz and POST /graphs/<id>/query offline."""

    def __init__(self, graph_id: str, slug: str | None = None):
        self.graph_id = graph_id
        self.slug = slug if slug is not None else graph_id
        self.port = 0
        self.requests_seen: list[str] = []
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "FakeOmnigraphServer":
        outer = self
        handler = _make_handler(outer)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _make_handler(server: FakeOmnigraphServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # the stub must never pollute stderr
            pass

        def _reply(self, code: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — the SDK calls GET /healthz, flat
            server.requests_seen.append(f"GET {self.path}")
            if self.path == "/healthz":
                self._reply(200, {"status": "ok", "version": FAKE_VERSION})
            else:
                self._reply(404, {"error": "not_found", "path": self.path})

        def do_POST(self):  # graph-scoped: POST /graphs/<id>/query
            server.requests_seen.append(f"POST {self.path}")
            match = re.fullmatch(r"/graphs/([^/]+)/query", self.path.split("?")[0])
            if not match:
                self._reply(404, {"error": "not_found", "path": self.path})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._reply(200, {
                "rows": [{WHOAMI_SLUG_KEY: server.slug, "p.repository": "AutoOS"}],
                "columns": [WHOAMI_SLUG_KEY, "p.repository"],
            })

    return Handler


# ── the MCP stdio client ──────────────────────────────────────────────────────

class McpStdioClient:
    """Newline-delimited JSON-RPC over one child process's stdio."""

    def __init__(self, cmd: list[str], env: dict, deadline: float, token: str | None):
        self.deadline = deadline
        self.token = token
        self.stderr_lines: list[str] = []
        self._stdout_queue: queue.Queue = queue.Queue()
        self._next_id = 0
        executable = shutil.which(cmd[0]) or cmd[0]
        try:
            self.proc = subprocess.Popen([executable, *cmd[1:]], env=env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
        except OSError as exc:
            raise BridgeError(f"cannot start bridge: {exc}")
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self):
        stream = self.proc.stdout
        for raw in iter(stream.readline, b""):
            raw = raw.strip()
            if not raw:
                continue
            try:
                self._stdout_queue.put(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                pass
        self._stdout_queue.put(None)

    def _pump_stderr(self):
        stream = self.proc.stderr
        for raw in iter(stream.readline, b""):
            line = redact(raw.decode("utf-8", "replace").rstrip(), self.token)
            if line:
                self.stderr_lines.append(line)

    def _send(self, message: dict) -> None:
        if self.proc.poll() is not None:
            raise BridgeError(f"bridge exited rc={self.proc.returncode}")
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise BridgeError(f"bridge died while writing ({exc.__class__.__name__}), "
                              f"rc={self.proc.poll()}")

    def _await(self, want_id: int) -> dict:
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError("no answer before the timeout")
            try:
                message = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                raise BridgeError("no answer before the timeout")
            if message is None:
                raise BridgeError(f"bridge exited rc={self.proc.poll()} before answering")
            if message.get("id") != want_id:
                continue
            if "error" in message:
                err = message.get("error") or {}
                raise BridgeError(redact(
                    f"JSON-RPC error {err.get('code')}: {err.get('message')}", self.token))
            return message.get("result") or {}

    def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        want = self._next_id
        self._send({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
        return self._await(want)

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call_tool(self, name: str, arguments: dict):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return tool_payload(result, self.token)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def tool_payload(result: dict, token: str | None):
    """Unwrap an MCP tool result: the bridge answers one JSON text block."""
    if result.get("isError"):
        raise BridgeError(redact(f"tool error: {content_text(result)}", token))
    text = content_text(result)
    if not text:
        raise BridgeError("tool returned no text content")
    try:
        return json.loads(text)
    except ValueError:
        raise BridgeError(redact(f"tool returned non-JSON text: {text[:120]}", token))


def content_text(result: dict) -> str:
    for item in result.get("content") or []:
        if item.get("type") == "text":
            return str(item.get("text") or "")
    return ""


def whoami_slugs(payload) -> list[str]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise BridgeError("whoami returned no rows field")
    return [str(row.get(WHOAMI_SLUG_KEY)) for row in rows
            if isinstance(row, dict) and row.get(WHOAMI_SLUG_KEY) is not None]


# ── the measurement ───────────────────────────────────────────────────────────

def count_lock_errors(lines: list[str]) -> dict:
    """Screen npm's stderr for cache contention. A hit is a signal to look at,
    so the matched lines are kept (already token-scrubbed) for the operator."""
    counts = {"total": 0, **{pattern: 0 for pattern in LOCK_PATTERNS}}
    hits = []
    for line in lines:
        matched = [p for p in LOCK_PATTERNS if p.lower() in line.lower()]
        if matched:
            counts["total"] += 1
            for pattern in matched:
                counts[pattern] += 1
            hits.append(line[:200])
    counts["lines"] = hits
    return counts


def run_bridge(index: int, cmd: list[str], env: dict, graph_id: str,
               timeout: float, limit_ms: float, token: str | None) -> dict:
    """One bridge: handshake, health (timed), whoami. Never raises."""
    started = time.monotonic()
    deadline = started + timeout
    record = {"bridge": index, "latency_ms": None, "health_ok": False,
              "whoami_ok": False, "reasons": [], "server_version": "",
              "lock_errors": count_lock_errors([])}
    client = None
    try:
        client = McpStdioClient(cmd, env, deadline, token)
        client.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "check-omnigraph-bridge", "version": "1"}})
        client.notify("notifications/initialized")
        health = client.call_tool("health", {})
        record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        record["health_ok"] = True
        record["server_version"] = str(health.get("version") or "")
        if record["latency_ms"] > limit_ms:
            record["reasons"].append(
                f"health took {record['latency_ms']} ms, over the D9 limit of {limit_ms} ms")
        slugs = whoami_slugs(client.call_tool("query", {"query": WHOAMI_QUERY,
                                                        "name": "whoami"}))
        if not slugs:
            record["reasons"].append("whoami returned no Project row")
        elif graph_id in slugs:
            record["whoami_ok"] = True
        else:
            record["reasons"].append(
                f"whoami slug {slugs[0]!r} is not the pinned graph {graph_id!r}")
    except BridgeError as exc:
        if record["latency_ms"] is None:
            record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        record["reasons"].append(str(exc))
    except Exception as exc:  # a bridge that breaks the client must fail the run, not the tool
        if record["latency_ms"] is None:
            record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        record["reasons"].append(f"unexpected {exc.__class__.__name__}: "
                                 f"{redact(str(exc), token)}")
    finally:
        if client is not None:
            record["lock_errors"] = count_lock_errors(client.stderr_lines)
            client.close()
    return record


def prime_bridge(cmd, env, graph_id, timeout, token) -> None:
    """--warm: one sequential bridge first so the measured wave hits a filled cache.

    Its own record is dropped — it measures the cache fill, not the wave."""
    run_bridge(0, cmd, env, graph_id, timeout, float("inf"), token)


def benchmark(cmd: list[str], env: dict, graph_id: str, parallel: int, timeout: float,
              limit_ms: float, warm: bool, token: str | None) -> list[dict]:
    if warm:
        prime_bridge(cmd, env, graph_id, timeout, token)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(
            lambda i: run_bridge(i, cmd, env, graph_id, timeout, limit_ms, token),
            range(1, parallel + 1)))


# ── the report ────────────────────────────────────────────────────────────────

def summarise(records: list[dict], parallel: int, mode: str, graph_id: str,
              limit_ms: float, base_url: str, bridge_cmd: list[str],
              fake_server: bool, npm_cache: str) -> dict:
    latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
    healthy = sum(1 for r in records if r["health_ok"])
    whoami_ok = sum(1 for r in records if r["whoami_ok"])
    lock_total = sum(r["lock_errors"]["total"] for r in records)
    lock_counts = {"total": lock_total,
                   **{p: sum(r["lock_errors"][p] for r in records) for p in LOCK_PATTERNS}}
    lock_counts["lines"] = [line for r in records for line in r["lock_errors"]["lines"]][:5]

    failures = [{"bridge": r["bridge"], "reason": reason}
                for r in records for reason in r["reasons"]]
    if lock_total:
        failures.extend({"bridge": r["bridge"],
                         "reason": f"npm logged {r['lock_errors']['total']} "
                                   "lock/cache error(s) on stderr"}
                        for r in records if r["lock_errors"]["total"])

    verdict = "PASS" if not failures else "FAIL"
    if not failures:
        verdict_text = (f"PASS — {parallel} bridges answered health in under "
                        f"{limit_ms} ms with 0 lock errors; npx satisfies D9")
    else:
        verdict_text = (f"FAIL — {len(failures)} problem(s); per D9 use a "
                        "pre-installed, pinned bridge instead of npx")

    return {
        "ok": not failures,
        "verdict": verdict_text,
        "mode": mode,
        "parallel": parallel,
        "fake_server": fake_server,
        "base_url": base_url,
        "bridge_cmd": bridge_cmd,
        "npm_cache": npm_cache,
        "graph_id": graph_id,
        "limit_ms": limit_ms,
        "latency_ms": ({"min": min(latencies), "median": statistics.median(latencies),
                        "max": max(latencies)} if latencies
                       else {"min": None, "median": None, "max": None}),
        "healthy": healthy,
        "whoami_ok": whoami_ok,
        "server_version": next((r["server_version"] for r in records if r["server_version"]), ""),
        "lock_errors": lock_counts,
        "failures": failures,
    }


def render_table(report: dict) -> str:
    latency = report["latency_ms"]
    lines = [
        f"omnigraph bridge benchmark: {report['parallel']} bridges in parallel, "
        f"{report['mode']} cache" + (" via fake-server" if report["fake_server"] else ""),
        f"  bridge     {' '.join(report['bridge_cmd'])}",
        f"  server     {report['base_url']}"
        + (f" (reports {report['server_version']})" if report["server_version"] else ""),
        f"  latency ms min {_num(latency['min'])}  median {_num(latency['median'])} "
        f" max {_num(latency['max'])}   (D9 limit {_num(report['limit_ms'])} ms)",
        f"  healthy {report['healthy']}/{report['parallel']}  "
        f"whoami ok {report['whoami_ok']}/{report['parallel']}  graph {report['graph_id']}",
        f"  npm lock/cache errors {report['lock_errors']['total']}  ("
        + ", ".join(f"{p} {report['lock_errors'][p]}" for p in LOCK_PATTERNS) + ")",
    ]
    for failure in report["failures"]:
        lines.append(f"  ! bridge {failure['bridge']}: {failure['reason']}")
    for line in report["lock_errors"]["lines"]:
        lines.append(f"  ~ npm stderr: {line}")
    lines.append(f"verdict {report['verdict']}")
    return "\n".join(lines)


def _num(value) -> str:
    if value is None:
        return "-"
    return f"{value:g}" if isinstance(value, float) else str(value)


# ── entry point ───────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Benchmark the Omnigraph MCP bridge (SPEC-OMNI A5, decision D9).",
        epilog="The bridge command and graph id default to this repo's .mcp.json. "
               "The token comes from $OMNIGRAPH_TOKEN only and is never printed.")
    ap.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL,
                    help=f"bridges started at once (default {DEFAULT_PARALLEL}, per D9)")
    cache = ap.add_mutually_exclusive_group()
    cache.add_argument("--cold", dest="mode", action="store_const", const="cold",
                       help="one fresh npm_config_cache shared by the wave (the contention case)")
    cache.add_argument("--warm", dest="mode", action="store_const", const="warm",
                       help="default npm cache, primed by one bridge first (default)")
    ap.add_argument("--graph-id", default=None,
                    help="expected whoami slug (default: OMNIGRAPH_GRAPH_ID in .mcp.json)")
    ap.add_argument("--base-url", default="",
                    help="server URL (default: $OMNIGRAPH_BASE_URL, then .mcp.json)")
    ap.add_argument("--bridge-cmd", default=None,
                    help="override the bridge command line (default: .mcp.json's)")
    ap.add_argument("--npm-cache", default="",
                    help="cache dir for --cold (default: a fresh temp dir)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help=f"per-bridge deadline in seconds (default {DEFAULT_TIMEOUT_S:g})")
    ap.add_argument("--limit-ms", type=float, default=DEFAULT_LIMIT_MS,
                    help=f"D9 health limit (default {DEFAULT_LIMIT_MS})")
    ap.add_argument("--fake-server", action="store_true",
                    help="answer from a local stub on 127.0.0.1 instead of a real server")
    ap.add_argument("--fake-slug", default="",
                    help="slug the fake server returns (default: the graph id)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--root", default=str(ROOT), help="repository root to read .mcp.json from")
    return ap


def resolve_settings(args, root: Path) -> dict:
    """Bridge command, graph id and base URL, each from exactly one place.

    An *absent* flag falls back to `.mcp.json`; a flag given but blank is a
    usage error — silently running the default bridge when the caller asked for
    an override is how a fake-cmd test ends up spawning real npx."""
    need_config = (args.bridge_cmd is None or args.graph_id is None
                   or (not args.base_url and not args.fake_server))
    config = read_bridge_config(root) if need_config else None
    if args.bridge_cmd is None:
        cmd = list(config["cmd"])
    else:
        cmd = shlex.split(args.bridge_cmd)
        if not cmd:
            raise Unusable("--bridge-cmd is empty after splitting")
    graph_id = (config["graph_id"] if args.graph_id is None else args.graph_id).strip()
    if not graph_id:
        raise Unusable("no graph id: .mcp.json sets no OMNIGRAPH_GRAPH_ID and --graph-id is empty")
    base_url = (args.base_url or os.environ.get("OMNIGRAPH_BASE_URL")
                or (config["base_url"] if config else "")).rstrip("/")
    if not base_url and not args.fake_server:
        raise Unusable("no server URL: pass --base-url, set $OMNIGRAPH_BASE_URL, "
                       "or use --fake-server")
    return {"cmd": cmd, "graph_id": graph_id, "base_url": base_url}


def validate(args) -> None:
    if args.parallel < 1:
        raise Unusable(f"--parallel must be >= 1, got {args.parallel}")
    if args.timeout <= 0:
        raise Unusable(f"--timeout must be > 0, got {args.timeout:g}")
    if args.limit_ms <= 0:
        raise Unusable(f"--limit-ms must be > 0, got {args.limit_ms:g}")


def make_env(base_url: str, graph_id: str, token: str | None, mode: str,
             npm_cache: str) -> dict:
    env = os.environ.copy()
    env["OMNIGRAPH_BASE_URL"] = base_url
    env["OMNIGRAPH_GRAPH_ID"] = graph_id
    if token:
        env["OMNIGRAPH_TOKEN"] = token
    else:
        env.pop("OMNIGRAPH_TOKEN", None)
    if mode == "cold":
        Path(npm_cache).mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = npm_cache
    return env


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        validate(args)
        mode = args.mode or "warm"
        settings = resolve_settings(args, Path(args.root))
    except Unusable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    token = os.environ.get("OMNIGRAPH_TOKEN") or None
    npm_cache = ""
    try:
        with contextlib.ExitStack() as stack:
            if mode == "cold":
                npm_cache = args.npm_cache or tempfile.mkdtemp(prefix="autoos-bridge-cache-")
            if args.fake_server:
                fake = stack.enter_context(
                    FakeOmnigraphServer(settings["graph_id"],
                                        args.fake_slug or settings["graph_id"]))
                base_url = fake.base_url
            else:
                base_url = settings["base_url"]
            env = make_env(base_url, settings["graph_id"], token, mode, npm_cache)
            records = benchmark(settings["cmd"], env, settings["graph_id"], args.parallel,
                                args.timeout, args.limit_ms, mode == "warm", token)
            report = summarise(records, args.parallel, mode, settings["graph_id"],
                               args.limit_ms, base_url, settings["cmd"],
                               bool(args.fake_server), npm_cache)
    except Unusable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if npm_cache and not args.npm_cache:
            shutil.rmtree(npm_cache, ignore_errors=True)

    print(json.dumps(report, indent=2) if args.json else render_table(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
