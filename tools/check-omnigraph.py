#!/usr/bin/env python3
"""Check that Omnigraph memory is reachable the way every agent client reaches it.

A client can show omnigraph as "connected" while every call fails: the MCP
bridge answers `initialize`, `tools/list` and `health` without a bearer token
and only fails on the first real read ("missing bearer token"). And when the
bridge cannot start at all (no OMNIGRAPH_BASE_URL / OMNIGRAPH_GRAPH_ID, or a
docker image that is not built) the client reports nothing more specific than
"Connection closed". Both were measured on 2026-09-24. This tool names which
one it is.

Checks:
  config (always)
    * .mcp.json pins the catalog's bridge package, a graph id, and passes the
      token by env-var reference (`${OMNIGRAPH_TOKEN}`), never by value
    * opencode.jsonc pins the same package and graph id and carries no token
      value
    * WARN (never fails): an untracked .env names a different graph id.
      agent-skills' trust_worktree.py writes the folder name ("AutoOS"); ids
      are case-sensitive and nothing in this repo reads that file.
  live (skipped with --offline)
    * GET  <base>/healthz                      server is up
    * GET  <base>/graphs/<graph>/schema        token accepted, graph exists
    * POST <base>/graphs/<graph>/query         the graph has a Project hub

Usage:
    python3 tools/check-omnigraph.py [--offline] [--json] [--root DIR]
                                     [--base-url URL] [--graph ID]

The token comes from $OMNIGRAPH_TOKEN, else from the per-user env file
(~/.autoos-omnigraph.env, override with $AUTOOS_OMNIGRAPH_ENV_FILE). The base
URL comes from --base-url, $OMNIGRAPH_BASE_URL, that file, then
http://localhost:8080.

Exit codes:
    0   every check passed
    1   a check failed (the report names each)
    2   unusable input (a config file is missing or unparseable)

Never prints the token, only whether one was found and where.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_REF = "${OMNIGRAPH_TOKEN}"
DEFAULT_BASE = "http://localhost:8080"


class Unusable(Exception):
    pass


def load_json(path: Path, jsonc: bool = False) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise Unusable(f"cannot read {path}: {exc}")
    if jsonc:
        text = re.sub(r"(?m)^\s*//.*$", "", text)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Unusable(f"cannot parse {path}: {exc}")


def catalog_pin(root: Path) -> str:
    harness = load_json(root / "catalog" / "agent-harness.json")
    try:
        return harness["mcp_servers"]["omnigraph"]["package"]
    except (KeyError, TypeError):
        raise Unusable("catalog/agent-harness.json has no mcp_servers.omnigraph.package")


def check_config(root: Path) -> tuple[list[str], str]:
    """Return (problems, graph id declared by .mcp.json)."""
    problems: list[str] = []
    pin = catalog_pin(root)

    mcp = load_json(root / ".mcp.json")
    entry = (mcp.get("mcpServers") or {}).get("omnigraph")
    graph = ""
    if not isinstance(entry, dict):
        problems.append(".mcp.json: no mcpServers.omnigraph entry")
    else:
        if pin not in (entry.get("args") or []):
            problems.append(f".mcp.json: omnigraph does not run the catalog pin {pin}")
        env = entry.get("env") or {}
        graph = env.get("OMNIGRAPH_GRAPH_ID") or ""
        if not graph:
            problems.append(".mcp.json: OMNIGRAPH_GRAPH_ID is empty (the bridge refuses to start)")
        if not env.get("OMNIGRAPH_BASE_URL"):
            problems.append(".mcp.json: OMNIGRAPH_BASE_URL is empty (the bridge refuses to start)")
        if env.get("OMNIGRAPH_TOKEN") != TOKEN_REF:
            problems.append(f".mcp.json: OMNIGRAPH_TOKEN must be the reference {TOKEN_REF}, never a value")

    oc = load_json(root / "opencode.jsonc", jsonc=True)
    mcp_block = oc.get("mcp") or {}
    servers = mcp_block.get("servers") or {}
    ocg = servers.get("omnigraph")
    if not isinstance(ocg, dict):
        problems.append("opencode.jsonc: no mcp.servers.omnigraph entry")
    else:
        if pin not in (ocg.get("command") or []):
            problems.append(f"opencode.jsonc: omnigraph does not run the catalog pin {pin}")
        env = ocg.get("environment") or {}
        if "OMNIGRAPH_TOKEN" in env:
            problems.append("opencode.jsonc: OMNIGRAPH_TOKEN must come from the process env, not the file")
        if graph and env.get("OMNIGRAPH_GRAPH_ID") != graph:
            problems.append(
                f"opencode.jsonc: OMNIGRAPH_GRAPH_ID is {env.get('OMNIGRAPH_GRAPH_ID')!r}, .mcp.json says {graph!r}")
        if not env.get("OMNIGRAPH_BASE_URL"):
            problems.append("opencode.jsonc: OMNIGRAPH_BASE_URL is empty (the bridge refuses to start)")
    return problems, graph


def check_local_env(root: Path, graph: str) -> list[str]:
    """Warnings for a repo-root .env whose OMNIGRAPH_GRAPH_ID disagrees."""
    local = read_env_file(root / ".env").get("OMNIGRAPH_GRAPH_ID")
    if graph and local and local != graph:
        return [f".env sets OMNIGRAPH_GRAPH_ID={local!r} but .mcp.json pins {graph!r};"
                " graph ids are case-sensitive, and any tool that loads this .env"
                " would get 'graph not found'"]
    return []


def env_file_path() -> Path:
    override = os.environ.get("AUTOOS_OMNIGRAPH_ENV_FILE")
    if override:
        return Path(override)
    return Path.home() / ".autoos-omnigraph.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip("'\"")
    return values


def _request(url: str, token: str | None, body: dict | None = None, timeout: int = 10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(getattr(exc, "reason", exc))


def check_live(base: str, graph: str, token: str | None, token_source: str) -> tuple[list[str], list[str]]:
    """Return (problems, facts)."""
    problems: list[str] = []
    facts: list[str] = [f"base {base}", f"graph {graph}", f"token {token_source}"]

    code, body = _request(f"{base}/healthz", None)
    if code != 200:
        problems.append(f"healthz: {code or 'unreachable'} {body[:120]}".rstrip())
        return problems, facts
    try:
        facts.append(f"server {json.loads(body).get('version', '?')}")
    except ValueError:
        pass

    if not token:
        problems.append(
            "OMNIGRAPH_TOKEN is not set and the env file has none: every read fails as"
            " 'missing bearer token' while clients still show omnigraph connected")
        return problems, facts

    code, body = _request(f"{base}/graphs/{graph}/schema", token)
    if code == 401:
        problems.append("schema read: 401, the token was rejected (stale or wrong server)")
        return problems, facts
    if code == 404:
        problems.append(f"schema read: 404, graph {graph!r} does not exist on this server")
        return problems, facts
    if code != 200:
        problems.append(f"schema read: {code or 'unreachable'} {body[:120]}".rstrip())
        return problems, facts
    facts.append("schema read ok")

    query = "query whoami() { match { $p: Project } return { $p.slug } }"
    code, body = _request(f"{base}/graphs/{graph}/query", token, {"query": query})
    if code != 200:
        problems.append(f"Project read: {code or 'unreachable'} {body[:120]}".rstrip())
        return problems, facts
    try:
        rows = json.loads(body).get("rows") or []
    except ValueError:
        rows = []
    slugs = sorted(str(r.get("p.slug")) for r in rows if isinstance(r, dict))
    if not slugs:
        problems.append(f"graph {graph!r} has no Project hub node (empty, or the wrong graph)")
    else:
        facts.append("project " + ",".join(slugs))
    return problems, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check Omnigraph MCP wiring and the live server.")
    ap.add_argument("--offline", action="store_true", help="config checks only; skip the network")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--root", default=str(ROOT), help="repository root to check (default: this repo)")
    ap.add_argument("--base-url", default="", help="server URL (default: env, env file, localhost:8080)")
    ap.add_argument("--graph", default="", help="graph id (default: the one .mcp.json pins)")
    args = ap.parse_args(argv)

    try:
        problems, graph = check_config(Path(args.root))
    except Unusable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    warnings = check_local_env(Path(args.root), graph)
    graph = args.graph or graph
    facts: list[str] = []

    if not args.offline:
        file_values = read_env_file(env_file_path())
        token = os.environ.get("OMNIGRAPH_TOKEN") or ""
        source = "from env" if token else ""
        if not token and file_values.get("OMNIGRAPH_TOKEN"):
            token = file_values["OMNIGRAPH_TOKEN"]
            source = f"from {env_file_path()}"
        base = (args.base_url or os.environ.get("OMNIGRAPH_BASE_URL")
                or file_values.get("OMNIGRAPH_BASE_URL") or DEFAULT_BASE).rstrip("/")
        live_problems, facts = check_live(base, graph or "autoos", token or None, source or "missing")
        problems.extend(live_problems)

    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems, "warnings": warnings,
                          "facts": facts}, indent=2))
    else:
        for fact in facts:
            print(f"  {fact}")
        for warning in warnings:
            print(f"WARN {warning}")
        for problem in problems:
            print(f"FAIL {problem}")
        if not problems:
            print("omnigraph ok" + (" (config only)" if args.offline else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
