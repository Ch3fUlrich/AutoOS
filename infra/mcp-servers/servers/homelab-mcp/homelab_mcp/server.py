"""MCP server exposing config-driven homelab access.

Tools are deliberately shaped around the *decision* an agent gets wrong, not
around ssh: `homelab_plan` answers "which account should I use", and
`homelab_diagnose` decodes an error that means something other than it says.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import core
from .config import ConfigError, load

app = Server("homelab")


def _cfg():
    return load()


def _text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="homelab_hosts",
            description="List configured hosts: address, roles, shell, status, tags, quirks. "
                        "Start here — never guess a hostname.",
            inputSchema={"type": "object", "properties": {
                "tag": {"type": "string", "description": "Only hosts carrying this tag"}}},
        ),
        Tool(
            name="homelab_roles",
            description="The capability matrix. Roles are NOT a hierarchy — each can do things "
                        "the others cannot, and picking wrong yields a misleading error.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="homelab_plan",
            description="Given a host and an intent, return which role/alias to use and why. "
                        "Picks the LEAST-capable role that satisfies the intent, and surfaces "
                        "warnings (host status, fleet exclusions, quirks, recorded access).",
            inputSchema={"type": "object", "properties": {
                "host": {"type": "string"},
                "intent": {"type": "string", "description": "A configured intent name"}},
                "required": ["host", "intent"]},
        ),
        Tool(
            name="homelab_run",
            description="Run a command on a host, choosing the role via the intent. Applies the "
                        "config's safety guardrails, wraps for non-POSIX login shells, and "
                        "attaches a diagnosis when the command fails.",
            inputSchema={"type": "object", "properties": {
                "host": {"type": "string"},
                "intent": {"type": "string"},
                "command": {"type": "string"},
                "reason": {"type": "string", "description": "Required for roles listed in safety.require_reason_for"},
                "timeout": {"type": "integer", "default": 60},
                "dry_run": {"type": "boolean", "default": False,
                            "description": "Return the plan and argv without connecting"}},
                "required": ["host", "intent", "command"]},
        ),
        Tool(
            name="homelab_diagnose",
            description="Decode an error message. Many homelab failures mean something other "
                        "than they say (a successful login that failed to parse; scoped-sudo "
                        "refusal that looks like a TTY problem).",
            inputSchema={"type": "object", "properties": {
                "text": {"type": "string", "description": "stderr or the error text"}},
                "required": ["text"]},
        ),
        Tool(
            name="homelab_verify",
            description="Probe every configured host+role over SSH and report which authenticate. "
                        "Read-only: runs `id -un`.",
            inputSchema={"type": "object", "properties": {
                "host": {"type": "string", "description": "Limit to one host"},
                "timeout": {"type": "integer", "default": 10}}},
        ),
        Tool(
            name="homelab_boundaries",
            description="What is deliberately NOT reachable, and what must never be targeted by "
                        "fleet-wide operations. Check before assuming something is broken or "
                        "before running anything against a whole group.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        cfg = _cfg()
    except ConfigError as exc:
        return _text({"error": str(exc)})

    a = arguments or {}
    try:
        if name == "homelab_hosts":
            tag = a.get("tag")
            hosts = [h for h in cfg.hosts.values() if not tag or tag in h.tags]
            return _text({"config": str(cfg.path), "count": len(hosts), "hosts": [
                {"name": h.name, "address": h.address, "roles": h.roles,
                 "login_user": h.login_user, "shell": h.shell, "status": h.status,
                 "tags": h.tags, "quirks": h.quirks, "notes": h.notes,
                 "forbid": h.forbid} for h in hosts]})

        if name == "homelab_roles":
            return _text({"note": "Capabilities, not a hierarchy. Pick by what the task needs.",
                          "roles": [{"name": r.name, "user": r.user, "alias_part": r.role_alias,
                                     "capabilities": r.capabilities, "sudo": r.sudo,
                                     "recorded": r.recorded, "notes": r.notes,
                                     "use_when": r.use_when} for r in cfg.roles.values()],
                          "intents": cfg.intents})

        if name == "homelab_plan":
            return _text(core.plan(cfg, a["host"], a["intent"]).as_dict())

        if name == "homelab_run":
            return _text(core.run(cfg, a["host"], a["intent"], a["command"],
                                  reason=a.get("reason", ""), timeout=int(a.get("timeout", 60)),
                                  dry_run=bool(a.get("dry_run", False))))

        if name == "homelab_diagnose":
            hits = core.diagnose(cfg, a.get("text", ""))
            return _text({"matches": hits} if hits else
                         {"matches": [], "note": "No configured pattern matched. Add one under "
                                                 "`diagnostics:` in the config so it is decoded next time."})

        if name == "homelab_verify":
            names = [a["host"]] if a.get("host") else list(cfg.hosts)
            timeout = int(a.get("timeout", 10))
            results = []
            for hn in names:
                h = cfg.host(hn)
                targets = h.roles or [None]
                for role in targets:
                    try:
                        alias = cfg.alias(hn, role)
                        user = cfg.role(role).user if role else h.login_user
                        argv = core.ssh_argv(cfg, core.Plan(hn, role, alias, user, "verify", []), "id -un")
                        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
                        results.append({"host": hn, "role": role, "alias": alias,
                                        "ok": r.returncode == 0,
                                        "as": r.stdout.strip() or None,
                                        "error": (r.stderr.strip().splitlines() or [None])[-1]
                                                 if r.returncode else None})
                    except Exception as exc:  # noqa: BLE001
                        results.append({"host": hn, "role": role, "ok": False, "error": str(exc)})
            return _text({"checked": len(results), "results": results})

        if name == "homelab_boundaries":
            return _text({"unreachable": cfg.unreachable, "fleet_exclusions": cfg.exclusions,
                          "host_prohibitions": {h.name: h.forbid for h in cfg.hosts.values() if h.forbid},
                          "note": "Unreachable entries are deliberate — not faults to debug. "
                                  "host_prohibitions are ENFORCED by homelab_plan/homelab_run."})

        return _text({"error": f"unknown tool {name!r}"})
    except ConfigError as exc:
        return _text({"error": str(exc)})
    except KeyError as exc:
        return _text({"error": f"missing required argument: {exc}"})


async def _amain() -> None:
    async with stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())


def main() -> None:
    import asyncio
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
