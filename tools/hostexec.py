#!/usr/bin/env python3
"""hostexec CLI: a host-enforced deny-list + audit boundary for agent shell
access (status/L1-backlog.spec-host-shell.md, configuration/hostexec/README.md).

    hostexec.py check --policy PATH --actor X --host H -- argv...
        Print the decide() outcome for one call. Dry: never runs anything,
        never writes the audit log.

    hostexec.py log [--dir DIR] [--since 1h] [--actor X] [--decision deny]
        Read the JSONL audit log (newest last), filtered.

    hostexec.py serve --policy PATH [--state-dir DIR]
        Start the MCP-over-HTTP broker. Binds AUTOOS_EXEC_BIND (comma list,
        default 127.0.0.1) : AUTOOS_EXEC_PORT (default 8765); refuses a
        non-loopback bind unless AUTOOS_EXEC_ALLOW_LAN=1.

This file is a thin CLI over the tools/hostexec/ package (stdlib only,
except the MCP layer used by `serve`, imported lazily so `check`/`log` and
every hermetic test run without the `mcp` package installed).
"""
from __future__ import annotations

import argparse
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from hostexec import policy  # noqa: E402


def _split_policy_argv(raw_args: list[str]) -> tuple[list[str], list[str]]:
    """Split CLI args at a literal '--': before it, argparse flags; after
    it, the argv to decide on (never touched/parsed as flags)."""
    if "--" in raw_args:
        i = raw_args.index("--")
        return raw_args[:i], raw_args[i + 1:]
    return raw_args, []


def cmd_check(raw_args: list[str]) -> int:
    flags, argv = _split_policy_argv(raw_args)
    parser = argparse.ArgumentParser(prog="hostexec.py check")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--cwd", default=os.getcwd())
    ns = parser.parse_args(flags)

    try:
        pol = policy.load(ns.policy)
    except policy.PolicyError as exc:
        print(f"refused: policy did not load: {exc}", file=sys.stderr)
        return 2

    decision = policy.decide(pol, ns.actor, ns.host, argv, ns.cwd)
    if decision.allow:
        print(f"allow actor={ns.actor} host={ns.host} argv={argv!r}")
        return 0
    print(f"deny rule={decision.rule} actor={ns.actor} host={ns.host} "
          f"argv={argv!r} problems={list(decision.problems)!r}")
    return 1


def cmd_log(raw_args: list[str]) -> int:
    from hostexec import audit

    parser = argparse.ArgumentParser(prog="hostexec.py log")
    parser.add_argument("--dir", default=None, help="audit log directory (default: XDG state dir)")
    parser.add_argument("--since", default=None, help="e.g. 1h, 30m, 2d")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--decision", default=None, choices=("allow", "deny"))
    ns = parser.parse_args(raw_args)

    state_dir = ns.dir or audit.default_state_dir()
    since_seconds = audit.parse_since(ns.since) if ns.since else None
    count = 0
    for line in audit.tail(state_dir, since_seconds=since_seconds,
                            actor=ns.actor, decision=ns.decision):
        print(line)
        count += 1
    if count == 0:
        print("(no matching audit lines)", file=sys.stderr)
    return 0


def cmd_serve(raw_args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="hostexec.py serve")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--state-dir", default=None)
    ns = parser.parse_args(raw_args)

    from hostexec import server

    try:
        pol = policy.load(ns.policy)
    except policy.PolicyError as exc:
        print(f"refused to start: policy did not load: {exc}", file=sys.stderr)
        return 2

    return server.serve(pol, policy_path=ns.policy, state_dir=ns.state_dir)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        return cmd_check(rest)
    if cmd == "log":
        return cmd_log(rest)
    if cmd == "serve":
        return cmd_serve(rest)
    print(f"unknown command: {cmd!r} (expected check, log or serve)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
