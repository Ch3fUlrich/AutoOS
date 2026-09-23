#!/usr/bin/env python3
"""Copy one project's subgraph out of the shared `memory` graph into its own graph.

Per-project graph isolation (cluster.yaml) gives every repo its own graph, but the
data predating that model still lives in `memory`. This carves a single project's
subgraph out and merge-loads it into the project's graph. Additive and idempotent:
it never deletes from `memory` (use --prune-source separately, after verifying).

    python scripts/split-project-graph.py sibling-analysis-repo            # dry run
    python scripts/split-project-graph.py sibling-analysis-repo --apply

A project's subgraph = its Project node + every node joined to it by a hub edge
(DecidedIn/ConstrainsProject/AppliesTo/PartOf/Tracks) + every relational edge whose
endpoints are both inside that set. Global-scope Preferences stay in `memory`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _omni_env import LOCAL_NET, detect_network  # noqa: E402

HUB_EDGES = {"DecidedIn", "ConstrainsProject", "AppliesTo", "PartOf", "Tracks"}
HERE = Path(__file__).resolve().parent.parent  # infra/mcp-servers
IMAGE = "modernrelay/omnigraph-server:v0.8.1"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for name in (".env.shared", ".env.server"):
        path = HERE / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def export_graph(base_url: str, token: str, graph: str) -> list[dict]:
    req = urllib.request.Request(
        f"{base_url}/graphs/{graph}/export",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode()
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def partition(records: list[dict], slug: str) -> tuple[list[dict], list[dict], list[str]]:
    """Return (nodes, edges, warnings) making up `slug`'s subgraph."""
    nodes = {r["data"]["slug"]: r for r in records if "type" in r and "slug" in r.get("data", {})}
    edges = [r for r in records if "edge" in r]
    warnings: list[str] = []

    if slug not in nodes:
        raise SystemExit(f"no node with slug {slug!r} in the source graph")
    if nodes[slug].get("type") != "Project":
        raise SystemExit(f"{slug!r} is a {nodes[slug].get('type')}, not a Project")

    members = {slug}
    for e in edges:
        if e.get("edge") in HUB_EDGES and e.get("to") == slug:
            members.add(e["from"])

    keep_nodes = []
    for member in sorted(members):
        node = nodes.get(member)
        if node is None:
            warnings.append(f"edge references missing node {member!r} — skipped")
            continue
        keep_nodes.append({"type": node["type"], "data": node["data"]})

    keep_edges = [
        {"edge": e["edge"], "from": e["from"], "to": e["to"]}
        for e in edges
        if e.get("from") in members and e.get("to") in members
    ]

    # A relational edge pointing outside the project would silently vanish here.
    for e in edges:
        if e.get("from") in members and e.get("to") not in members:
            warnings.append(
                f"{e['edge']} {e['from']} -> {e['to']} leaves the project — dropped"
            )
    return keep_nodes, keep_edges, warnings


def mutate(base_url: str, token: str, graph: str, query: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}/graphs/{graph}/mutate",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def prune_source(
    base_url: str, token: str, source: str, target: str, nodes: list[dict]
) -> None:
    """Delete `nodes` from `source`, but only once every one is confirmed present, with
    the same type, in `target`. Node deletes cascade their edges, so edges need no
    separate pass. Refuses on the first mismatch rather than half-deleting."""
    mirror = {
        r["data"]["slug"]: r["type"]
        for r in export_graph(base_url, token, target)
        if "type" in r and "slug" in r.get("data", {})
    }
    missing = [f'{n["type"]} {n["data"]["slug"]!r}' for n in nodes
               if mirror.get(n["data"]["slug"]) != n["type"]]
    if missing:
        raise SystemExit(
            f"REFUSING to prune {source!r}: {len(missing)} node(s) are not mirrored in "
            f"{target!r} — deleting them would lose data:\n  " + "\n  ".join(missing[:10])
        )

    by_type: dict[str, list[str]] = {}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n["data"]["slug"])

    total = 0
    for ntype, slugs in sorted(by_type.items()):
        for i in range(0, len(slugs), 40):  # chunk: keep each mutation a sane size
            chunk = slugs[i : i + 40]
            # delete-only mutation (the D2 rule: never mix delete with insert/update)
            stmts = "\n  ".join(
                f'delete {ntype} where slug = "{s}"' for s in chunk
            )
            res = mutate(base_url, token, source,
                         f"query prune_{ntype.lower()}_{i}() {{\n  {stmts}\n}}")
            n = res.get("affected_nodes", 0)
            total += n
            print(f"    deleted {n:>3} {ntype} from {source!r}")
    print(f"  pruned {total} node(s) from {source!r} (edges cascade)")


def merge_load(graph: str, payload: str, token: str, net: str) -> None:
    """Load NDJSON via the CLI-in-container, piping on stdin (bind mounts mangle
    paths under Git Bash — see cluster/seed/README.md)."""
    cmd = [
        "docker", "run", "--rm", "-i", "--network", net,
        "-e", f"OMNIGRAPH_BEARER_TOKEN={token}",
        "-e", f"TARGET_GRAPH={graph}",
        "--entrypoint", "sh", IMAGE, "-c",
        "cat > /tmp/d.jsonl; omnigraph load --server http://omnigraph-server:8080 "
        "--graph \"$TARGET_GRAPH\" --data /tmp/d.jsonl --mode merge --yes --json",
    ]
    env = {**os.environ, "MSYS2_ARG_CONV_EXCL": "*"}
    proc = subprocess.run(cmd, input=payload, text=True, capture_output=True, env=env)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"load into {graph!r} failed (exit {proc.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="project slug (= repo folder name = target graph id)")
    ap.add_argument(
        "--source",
        help="graph to read from. Defaults to the project's OWN graph, which is the "
        "source of truth post-migration (so --write-seed refreshes from live). Pass "
        "--source memory only for the one-time carve-out of the legacy shared graph.",
    )
    ap.add_argument("--target", help="destination graph (default: same as slug)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--net", default=os.environ.get("OMNI_NET"),
                    help=f"docker network for the load container (default: auto-detected from "
                         f"the running omnigraph-server, else {LOCAL_NET})")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry-run")
    ap.add_argument(
        "--prune-source",
        action="store_true",
        help="DESTRUCTIVE: after verifying the target graph mirrors every node, delete "
        "this project's nodes from --source (edges cascade). Run only once the copy is "
        "verified; back up --source first. Requires --source != target.",
    )
    ap.add_argument(
        "--write-seed",
        action="store_true",
        help="also refresh cluster/seed/<target>.jsonl from live (embeddings stripped — "
        "they are large and regenerable via scripts/populate-embeddings.py). Do this "
        "whenever live has moved ahead of the seed, or the self-healing seed loader "
        "will merge stale values back over newer ones on the next boot.",
    )
    args = ap.parse_args()

    env = load_env()
    token = env.get("OMNIGRAPH_TOKEN") or os.environ.get("OMNIGRAPH_TOKEN", "")
    if not token:
        raise SystemExit("OMNIGRAPH_TOKEN not found in .env.shared or the environment")
    target = args.target or args.slug
    source = args.source or args.slug
    # central (coding.example.internal) is `mcp-servers_default`, local is `mcp-server_mcp-net` —
    # ask docker instead of assuming. An explicit --net/OMNI_NET wins.
    args.net = args.net or detect_network(LOCAL_NET)

    if source == target and args.apply:
        raise SystemExit(
            f"--apply with source == target ({source!r}) would reload a graph onto itself "
            "and duplicate its edges (edges are not slug-keyed). Use --write-seed alone to "
            "refresh the seed, or pass --source memory to migrate."
        )

    records = export_graph(args.base_url, token, source)
    nodes, edges, warnings = partition(records, args.slug)

    by_type: dict[str, int] = {}
    for n in nodes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    print(f"source {source!r} -> target {target!r}")
    print(f"  nodes: {len(nodes)}  ({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))})")
    print(f"  edges: {len(edges)}")
    for w in warnings:
        print(f"  WARN: {w}")

    payload = "\n".join(json.dumps(r) for r in nodes + edges) + "\n"
    out = HERE / ".graph-backup" / f"split-{target}.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    print(f"  subgraph written to {out}")

    if args.write_seed:
        seed_nodes = [
            {"type": n["type"], "data": {k: v for k, v in n["data"].items() if k != "embedding"}}
            for n in nodes
        ]
        seed = HERE / "cluster" / "seed" / f"{target}.jsonl"
        seed.write_text(
            "\n".join(json.dumps(r) for r in seed_nodes + edges) + "\n", encoding="utf-8"
        )
        print(f"  seed refreshed  -> {seed}")

    if args.prune_source:
        if source == target:
            raise SystemExit("--prune-source needs --source to differ from the target")
        print(f"\npruning {source!r} (verifying every node is mirrored in {target!r} first)…")
        prune_source(args.base_url, token, source, target, nodes)
        return

    if not args.apply:
        print("\ndry run — re-run with --apply to merge-load into the target graph")
        return
    merge_load(target, payload, token, args.net)
    print(f"\nloaded into {target!r}. Verify with: commits_list + a node count on {target!r}.")


if __name__ == "__main__":
    main()
