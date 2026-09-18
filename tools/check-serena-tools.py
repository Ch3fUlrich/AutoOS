#!/usr/bin/env python3
"""Verify Serena's MCP server actually enforces AutoOS's tool exclusion policy.

AutoOS asks Serena to hide onboarding and the memory tools (write_memory,
read_memory, list_memories, edit_memory, rename_memory, delete_memory) so
durable memory stays in Omnigraph, the one place this repo's conventions say
it belongs (see CLAUDE.md's "one tool per job"), and keeps find_symbol - the
whole reason Serena is installed - available. install.sh's
ensure_serena_exclusions() and AutoOS.Install.psm1's
Set-AutoOSSerenaExclusions edit serena_config.yml to say this; this script
is the only way to confirm Serena is actually honouring it, by starting the
real MCP server over stdio and asking it, via JSON-RPC, which tools it
exposes.

This is NOT a CI test: it needs a working Serena install (or network access
to fetch one via uvx) and takes real wall-clock time to start a server
process. It is meant to be run by hand, or by the installers' post-install
probe, after every Serena upgrade or exclusion-list change - never by
tests/run-tests.sh or tests/run-tests.ps1.

Usage:
    python3 check-serena-tools.py [--command "<full command line>"]

Exit codes:
    0   OK - find_symbol is present and no onboarding/memory tool is exposed
    1   Serena is running but exposes a tool it shouldn't, or is missing one
        it must have
    2   Serena's MCP server could not be started, errored, or the 180s
        overall timeout was hit
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

        tool_names = []
        cursor = None
        next_id = 2
        while True:
            params = {"cursor": cursor} if cursor else {}
            _send(proc, {"jsonrpc": "2.0", "id": next_id, "method": "tools/list", "params": params})
            response = _wait_for_id(out_queue, next_id, deadline)
            result = response.get("result", {}) or {}
            for tool in result.get("tools", []) or []:
                name = tool.get("name")
                if name:
                    tool_names.append(name)
            cursor = result.get("nextCursor")
            next_id += 1
            if not cursor:
                break

        return tool_names
    finally:
        _terminate(proc)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--command",
        default=None,
        help="full command line to start Serena's MCP server, overriding the default",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        tool_names = collect_tool_names(args.command)
    except TimeoutError as exc:
        print(f"TIMEOUT: {exc}", file=sys.stderr)
        return 2
    except RpcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: could not start Serena's MCP server: {exc}", file=sys.stderr)
        return 2

    ok, problems = judge(tool_names)
    if not ok:
        print("FAIL: " + "; ".join(problems))
        return 1

    print(f"OK: {len(tool_names)} tools, no memory/onboarding tools, find_symbol present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
