#!/usr/bin/env python3
"""Fake Omnigraph MCP bridge: the stdio half of the offline A5 test pair.

Speaks the same newline-delimited MCP JSON-RPC as `@modernrelay/omnigraph-mcp`
and calls the same two HTTP paths, so `tools/check_omnigraph_bridge.py` can be
tested with no npm, no network and no real server — against its `--fake-server`
stub. Contract copied from the package source (cite lines in the tool's header):

  dist/bin.js:8,13   missing OMNIGRAPH_BASE_URL / OMNIGRAPH_GRAPH_ID is exit 1
  dist/bin.js:20     OMNIGRAPH_TOKEN is optional
  chunk:55,87,119    a tool answers `{"content":[{"type":"text","text":JSON}]}`
                     with the JSON pretty-printed (`JSON.stringify(v, null, 2)`)
  SDK stdio.js:29    framing: `JSON.stringify(message) + "\\n"` on stdout

Set FAKE_BRIDGE_MODE to bend the contract for a specific test:
  exit_early    die with rc=1 before answering anything (a bridge that will not start)
  lock_noise    log an npm cache-contention line on stderr, then answer normally
  leak_token    log the bearer token on stderr (verbose npm), then answer normally
  slow          take ~40 ms to answer health
  hang          spawn a grandchild, then wedge forever ignoring SIGTERM — the shape
                of a hung `npx` that left an `npm`-started `node` behind. Its own pid
                and the grandchild's go to $FAKE_BRIDGE_PID_FILE for the test to watch.

Run from the repo root (not useful on its own):

    OMNIGRAPH_BASE_URL=http://127.0.0.1:PORT OMNIGRAPH_GRAPH_ID=autoos \\
        python3 tests/fixtures/omnigraph_bridge_stub.py
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MODE = os.environ.get("FAKE_BRIDGE_MODE", "ok")

# Long enough that a test which fails to kill it still cannot report a live
# process as dead, short enough that a leftover from a red run dies by itself.
GRANDCHILD_LIFETIME_S = 600


def reply(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(request_id, payload, is_error=False):
    """The bridge wraps every tool result as one pretty-printed text block."""
    content = [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]
    if is_error:
        content_payload = {"content": content, "isError": True}
    else:
        content_payload = {"content": content}
    reply({"jsonrpc": "2.0", "id": request_id, "result": content_payload})


def fail(request_id, text):
    result(request_id, {"error": text}, is_error=True)


def http(method, path, body=None):
    base = os.environ["OMNIGRAPH_BASE_URL"].rstrip("/")
    token = os.environ.get("OMNIGRAPH_TOKEN") or ""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def health():
    payload = http("GET", "/healthz")
    payload["sdkServerVersion"] = "0.8.0"
    return payload


def whoami(arguments):
    graph = urllib.parse.quote(os.environ["OMNIGRAPH_GRAPH_ID"], safe="")
    return http("POST", f"/graphs/{graph}/query",
                {"query": arguments.get("query", ""), "name": arguments.get("name"),
                 "branch": arguments.get("branch") or "main"})


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        reply({"jsonrpc": "2.0", "id": request_id,
               "result": {"protocolVersion": message.get("params", {}).get(
                   "protocolVersion", "2025-03-26"),
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "omnigraph-mcp", "version": "0.8.0-fake"}}})
    elif method == "notifications/initialized":
        return
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if MODE == "slow":
                time.sleep(0.04)
            payload = health() if name == "health" else whoami(arguments)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            fail(request_id, f"omnigraph request failed: {exc}")
            return
        result(request_id, payload)
    elif method == "tools/list":
        reply({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [
            {"name": "health", "description": "stub", "inputSchema": {}},
            {"name": "query", "description": "stub", "inputSchema": {}}]}})
    elif request_id is not None:
        reply({"jsonrpc": "2.0", "id": request_id,
               "error": {"code": -32601, "message": f"unknown method {method}"}})


def hang_with_a_grandchild():
    """Wedge forever, holding a descendant process open behind us.

    A separate process, not a thread: `npx` -> `npm` -> `node` has exactly this
    shape, and it is why killing the bridge's direct child is not enough. Both
    pids go to $FAKE_BRIDGE_PID_FILE so the test can watch them after the tool
    has returned.
    """
    if os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # only a killpg kills us
    grandchild = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({GRANDCHILD_LIFETIME_S})"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file = os.environ.get("FAKE_BRIDGE_PID_FILE")
    if pid_file:
        with open(pid_file, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {grandchild.pid}\n")
    while True:
        time.sleep(3600)


def main():
    if MODE == "hang":
        hang_with_a_grandchild()
        return
    if MODE == "exit_early":
        sys.stderr.write("npm ERR! code E404 The requested resource 'omnigraph-mcp' not found\n")
        sys.exit(1)
    if not os.environ.get("OMNIGRAPH_BASE_URL"):
        sys.stderr.write("OMNIGRAPH_BASE_URL is required.\n")
        sys.exit(1)
    if not os.environ.get("OMNIGRAPH_GRAPH_ID"):
        sys.stderr.write("OMNIGRAPH_GRAPH_ID is required.\n")
        sys.exit(1)
    if MODE == "lock_noise":
        sys.stderr.write("npm ERR! code EEXIST\nnpm ERR! EEXIST: file already exists, "
                         "mkdir '<cache>/_locks/staging-abcdef'  (lock contention)\n")
    elif MODE == "leak_token":
        sys.stderr.write("npm http fetch POST 200 <base>/graphs/x/query "
                         "{ authorization: 'Bearer "
                         + os.environ.get("OMNIGRAPH_TOKEN", "") + "' }\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        handle(message)


if __name__ == "__main__":
    main()
