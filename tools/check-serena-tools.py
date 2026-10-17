#!/usr/bin/env python3
"""Verify Serena's MCP server actually enforces AutoOS's tool exclusion policy.

AutoOS asks Serena to hide onboarding and the memory tools (write_memory,
read_memory, list_memories, edit_memory, rename_memory, delete_memory) so
durable memory stays in Omnigraph, the one place this repo's conventions say
it belongs (see CLAUDE.md's "one tool per job"), and keeps find_symbol - the
whole reason Serena is installed - available. install.sh's
ensure_serena_exclusions() and AutoOS.Install.psm1's
Set-AutoOSSerenaExclusions edit serena_config.yml to say this, and the shared
SSE server gets the same list from
infra/mcp-servers/config/serena-context-no-memory.yml (mounted read-only into
the container by docker-compose.client.yml; D-869); this script is the only way
to confirm Serena is actually honouring any of it, by asking a real MCP server
which tools it exposes.

Three ways to obtain the tool list, all judged by the same `judge()`:

    # a) start Serena over stdio and ask it (the installers' post-install probe)
    python3 tools/check-serena-tools.py
    python3 tools/check-serena-tools.py --command "serena start-mcp-server --context claude-code"

    # b) ask a RUNNING server over SSE - how the shared :9121 server is checked.
    #    Run this by hand (operator / L1) after the container has been recreated
    #    with the new compose file; nothing in tests/ ever calls a URL.
    python3 tools/check-serena-tools.py --url http://127.0.0.1:9121/sse

    # c) judge a captured tool list (JSON: a list of names, a list of {"name":..}
    #    tool objects, or a tools/list result / full JSON-RPC response). This is
    #    what the test suite uses - deterministic, no server, no network.
    python3 tools/check-serena-tools.py --tools-file tests/fixtures/serena/tools-with-memory.json

This is NOT a CI test: a) and b) need a working Serena install (or network
access to fetch one via uvx) and take real wall-clock time; c) needs only the
fixture. It is meant to be run by hand, or by the installers' post-install
probe, after every Serena upgrade or exclusion-list change - never by
tests/run-tests.sh or tests/run-tests.ps1.

Exit codes:
    0   OK - find_symbol is present and no onboarding/memory tool is exposed
    1   Serena is running but exposes a tool it shouldn't, or is missing one
        it must have
    2   the tool list could not be obtained: Serena's MCP server could not be
        started, errored, the file/URL was unreadable, or the 180s overall
        timeout was hit
"""

from __future__ import annotations

import argparse
import json
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 180
ONBOARDING_TOOL = "onboarding"
# "memory" alone misses list_memories ("memories", not "memory"). "memor"
# catches every current name AND any future memory-ish tool; the explicit
# set below is kept anyway so the known names are flagged by name, not only
# by coincidence of the substring.
MEMORY_MARKER = "memor"
EXPLICIT_EXCLUDED_TOOLS = frozenset({
    ONBOARDING_TOOL, "write_memory", "read_memory", "list_memories",
    "edit_memory", "rename_memory", "delete_memory",
})
REQUIRED_TOOL = "find_symbol"
# The uvx fallback installs this package unpinned - a version bump can change
# which tools Serena exposes without anyone touching this repo. Pinning it is
# tracked as a follow-up; until then, this is the one place to pin it from.
SERENA_FROM = "serena-agent==1.7.0"  # same pin as the installers' OpenHands mcp_config


class RpcError(RuntimeError):
    """The child process responded, but with something we can't use."""


def judge(tool_names):
    """Pure decision logic - no I/O, so it can be unit tested on its own.

    tool_names: an iterable of tool name strings, as reported by tools/list.
    Returns (ok, problems): ok is True iff the policy is satisfied; problems
    is a list of human-readable reasons it isn't (empty when ok is True).
    """
    names = list(tool_names)
    problems = []

    offenders = sorted(
        name for name in names
        if name in EXPLICIT_EXCLUDED_TOOLS or MEMORY_MARKER in name
    )
    if offenders:
        problems.append(
            "exposed tools that must be excluded: " + ", ".join(offenders)
        )

    if REQUIRED_TOOL not in names:
        problems.append(f"required tool missing: {REQUIRED_TOOL}")

    return (not problems, problems)


def names_from_tools_result(result):
    """Tool names in a tools/list `result` object ([] on malformed input)."""
    names = []
    for tool in (result or {}).get("tools", []) or []:
        name = tool.get("name") if isinstance(tool, dict) else None
        if name:
            names.append(name)
    return names


def collect_tools_via(request):
    """Walk tools/list pagination through `request(method, params) -> result`."""
    names = []
    cursor = None
    while True:
        result = request("tools/list", {"cursor": cursor} if cursor else {})
        names += names_from_tools_result(result)
        cursor = (result or {}).get("nextCursor")
        if not cursor:
            break
    return names


def tool_names_from_document(document):
    """Tool names from a captured tool list, in any of the shapes a dump of a
    tools/list response can legitimately have:

        ["find_symbol", ...]                       a bare name list
        [{"name": "find_symbol"}, ...]             the `tools` array itself
        {"tools": [...]}                           a tools/list result
        {"result": {"tools": [...]}}               a full JSON-RPC response
    """
    if isinstance(document, dict):
        if "result" in document:
            return tool_names_from_document(document["result"])
        if "tools" in document:
            return tool_names_from_document(document["tools"])
        raise RpcError("JSON object has neither a 'tools' nor a 'result' key")
    if isinstance(document, list):
        names = []
        for item in document:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
            else:
                raise RpcError(f"list item is neither a name nor a {{'name': ...}} object: {item!r}")
        return names
    raise RpcError(f"expected a JSON list or object, got {type(document).__name__}")


def collect_tool_names_from_file(path):
    with open(path, encoding="utf-8") as f:
        return tool_names_from_document(json.load(f))


def default_command():
    """The command to start Serena's MCP server, preferring a direct install."""
    base = [
        "start-mcp-server",
        "--context", "claude-code",
        "--open-web-dashboard", "false",
        "--enable-gui-log-window", "false",
    ]
    if shutil.which("serena"):
        return ["serena", *base]
    return ["uvx", "--from", SERENA_FROM, "serena", *base]


def _reader_thread(stdout, out_queue):
    # Runs for the life of the child's stdout pipe. Non-JSON lines (banners,
    # log noise some servers print to stdout) are dropped silently, per spec.
    # A None on the queue signals EOF so the waiter never blocks forever on a
    # child that has exited without giving us what we asked for.
    try:
        for raw in iter(stdout.readline, ""):
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            out_queue.put(message)
    finally:
        out_queue.put(None)


def _send(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _wait_for_id(out_queue, want_id, deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for a response to id={want_id}")
        try:
            message = out_queue.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError(f"timed out waiting for a response to id={want_id}")
        if message is None:
            raise RpcError("Serena's MCP server closed its output before responding")
        if message.get("id") == want_id:
            if "error" in message:
                raise RpcError(f"Serena returned an error: {message['error']}")
            return message
        # Anything else (a notification, a response to a different id) is
        # not what we're waiting for - keep draining the queue.


def _terminate(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def collect_tool_names(command_override):
    cmd = shlex.split(command_override) if command_override else default_command()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    deadline = time.monotonic() + TIMEOUT_SECONDS
    out_queue: "queue.Queue" = queue.Queue()
    reader = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue), daemon=True)
    reader.start()

    try:
        _send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "check-serena-tools", "version": "1"},
            },
        })
        _wait_for_id(out_queue, 1, deadline)

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        next_id = 2

        def request(method, params):
            nonlocal next_id
            _send(proc, {"jsonrpc": "2.0", "id": next_id, "method": method, "params": params})
            response = _wait_for_id(out_queue, next_id, deadline)
            next_id += 1
            return response.get("result", {}) or {}

        return collect_tools_via(request)
    finally:
        _terminate(proc)


class _SseRpc:
    """Minimal MCP-over-SSE client: POST JSON-RPC, read the answers off the
    event stream.

    The SSE transport is two-legged and the legs are not the request/response
    pair one would expect: the client GETs the endpoint and that stream is
    where EVERYTHING arrives - first an `endpoint` event naming the URL to POST
    to, then the JSON-RPC responses (in `message` events). So the GET has to
    stay open for the whole exchange, and a request only completes when its
    response shows up on the other connection.
    """

    def __init__(self, url, deadline):
        split = urllib.parse.urlsplit(url)
        if split.scheme not in ("http", "https"):
            raise RpcError(f"--url must be an http(s) URL of the form http://host:port/sse, got {url!r}")
        self._origin = f"{split.scheme}://{split.netloc}"
        self._deadline = deadline
        self._messages: "queue.Queue" = queue.Queue()
        self._endpoints: "queue.Queue" = queue.Queue()
        self._post_target = None
        self._next_id = 1
        request = urllib.request.Request(
            url,
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
        )
        self._stream = urllib.request.urlopen(request, timeout=self._remaining())
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _remaining(self):
        return max(1.0, self._deadline - time.monotonic())

    def _read_loop(self):
        # One SSE frame = `event:`/`data:` lines ended by a blank line; several
        # `data:` lines in a frame join with newline; a line starting with ':'
        # is a keep-alive comment. A None on the response queue is the EOF
        # marker, so a server that drops the connection can never hang the
        # waiter until the deadline.
        event = None
        data_lines = []
        try:
            for raw in self._stream:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        if event == "endpoint":
                            self._endpoints.put(payload)
                        else:
                            try:
                                self._messages.put(json.loads(payload))
                            except json.JSONDecodeError:
                                pass
                    event, data_lines = None, []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())
        except Exception:
            # `close()` drops this socket mid-read, and http.client/IO raise
            # several different types for that. Either way: the stream is over.
            pass
        finally:
            self._messages.put(None)

    def _await_post_target(self):
        try:
            endpoint = self._endpoints.get(timeout=self._remaining())
        except queue.Empty:
            raise TimeoutError("timed out waiting for the SSE 'endpoint' event")
        if not endpoint:
            raise RpcError("the SSE 'endpoint' event carried no URL")
        self._post_target = urllib.parse.urljoin(self._origin, endpoint.strip())

    def _post(self, message):
        request = urllib.request.Request(
            self._post_target,
            data=json.dumps(message).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._remaining()) as response:
            response.read()

    def request(self, method, params=None):
        if self._post_target is None:
            self._await_post_target()
        want_id = self._next_id
        self._next_id += 1
        if params is None:
            self._post({"jsonrpc": "2.0", "id": want_id, "method": method})
        else:
            self._post({"jsonrpc": "2.0", "id": want_id, "method": method, "params": params})
        response = _wait_for_id(self._messages, want_id, self._deadline)
        return response.get("result", {}) or {}

    def notify(self, method):
        self._post({"jsonrpc": "2.0", "method": method})

    def close(self):
        try:
            self._stream.close()
        except Exception:
            pass
        # Join, don't just abandon it: a daemon thread still blocked in socket
        # I/O when the interpreter finalises aborts the whole process
        # ("Fatal Python error: _enter_buffered_busy ... due to daemon threads",
        # measured 2026-10-09 against a live server). Closing the stream above
        # is what lets the reader reach its finally and exit.
        self._reader.join(timeout=10)


def collect_tool_names_from_url(url):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    client = _SseRpc(url, deadline)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "check-serena-tools", "version": "1"},
            },
        )
        client.notify("notifications/initialized")
        return collect_tools_via(client.request)
    finally:
        client.close()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--command",
        default=None,
        help="full command line to start Serena's MCP server, overriding the default",
    )
    source.add_argument(
        "--url",
        default=None,
        help="SSE endpoint of a RUNNING Serena MCP server to ask, e.g. http://127.0.0.1:9121/sse",
    )
    source.add_argument(
        "--tools-file",
        default=None,
        help="JSON file holding a captured tool list (names, tool objects, or a "
        "tools/list result / JSON-RPC response) to judge without starting anything",
    )
    return parser.parse_args(argv)


def collect_tool_names_from_args(args):
    if args.tools_file:
        return collect_tool_names_from_file(args.tools_file)
    if args.url:
        return collect_tool_names_from_url(args.url)
    return collect_tool_names(args.command)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        tool_names = collect_tool_names_from_args(args)
    except TimeoutError as exc:
        print(f"TIMEOUT: {exc}", file=sys.stderr)
        return 2
    except RpcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        # OSError: the server process could not be started, or the URL could
        # not be reached. ValueError: json.load on a file that is not JSON.
        print(f"ERROR: could not obtain Serena's tool list: {exc}", file=sys.stderr)
        return 2

    ok, problems = judge(tool_names)
    if not ok:
        print("FAIL: " + "; ".join(problems))
        return 1

    print(f"OK: {len(tool_names)} tools, no memory/onboarding tools, find_symbol present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
