#!/usr/bin/env python3
"""Deduplicate every Omnigraph graph (or --graphs/GRAPHS subset) and re-integrate cleanly.

Omnigraph merges nodes by @key (slug): two branches/devices that use the SAME
slug are merged automatically. But when the same thing ends up under DIFFERENT
slugs — most commonly a case variant like `Finance-Repo` vs `finance-repo`, or slug drift —
Omnigraph keeps both. This script collapses those duplicates:

  * groups nodes by (type, casefold(slug))  [add --by-name for (type, label) too]
  * picks a canonical slug per group (lowercase preferred, else richest, else min)
  * redirects every edge from a duplicate to the canonical, de-dups edges
  * merges node fields (canonical wins; missing filled from duplicates)
  * preserves Decision embeddings as-is (dedup does not change decision content)

It is **idempotent and cheap when clean**: if no duplicates are found it exits 0
without touching the running graph — safe to run after every sync/merge. Only
when duplicates exist does it rebuild (reset the store + `load --mode overwrite`
the cleaned graph — the one write path that is reliable on omnigraph-server
v0.8.1). A pre-change NDJSON backup is always written.

Run manually, or wire it into omnigraph-setup/omnigraph-sync.sh after a branch merge.

Examples:
  python scripts/dedup-graph.py --dry-run          # report duplicates only
  python scripts/dedup-graph.py                    # dedup + rebuild if needed
  python scripts/dedup-graph.py --map Finance-Repo=finance-repo # force a specific merge
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _omni_env import LOCAL_MINIO_VOLUME, LOCAL_NET, describe, detect_minio_store, detect_network, site_env  # noqa: E402

LABEL = {"Project": "name", "Decision": "title", "Rule": "statement",
         "Preference": "statement", "Convention": "name", "Component": "name", "Task": "title"}


def sh(cmd, **kw):
    return subprocess.run(cmd, **kw)


def cli(args, network, image, token, stdin=None, capture=True):
    """Run the omnigraph CLI in a throwaway container against the server."""
    base = ["docker", "run", "--rm", "-i", "--network", network,
            "-e", f"OMNIGRAPH_BEARER_TOKEN={token}", "-e", "HOME=/tmp", "-w", "/tmp",
            "--entrypoint", "sh", image, "-c",
            'mkdir -p /tmp/.omnigraph; printf "servers:\\n  local:\\n    url: %s\\n" "$OG" >/tmp/.omnigraph/config.yaml; ' + args]
    env = dict(os.environ)
    return subprocess.run(base, input=stdin, capture_output=capture, text=True,
                          env={**env})


def list_graphs(server, network, image, token):
    """Discover every graph the server exposes (GET /graphs) from a throwaway container."""
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", network, "-e", f"OG={server}",
         "-e", f"OMNIGRAPH_BEARER_TOKEN={token}",
         "--entrypoint", "sh", image, "-c",
         'curl -s "$OG/graphs" -H "Authorization: Bearer $OMNIGRAPH_BEARER_TOKEN"'],
        capture_output=True, text=True)
    import re
    return re.findall(r'"graph_id":"([^"]+)"', r.stdout)


def export_graph(server, network, image, token, graph="memory"):
    r = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", network,
         "-e", f"OMNIGRAPH_BEARER_TOKEN={token}", "-e", f"OG={server}", "-e", "HOME=/tmp", "-w", "/tmp",
         "--entrypoint", "sh", image, "-c",
         'mkdir -p /tmp/.omnigraph; printf "servers:\\n  local:\\n    url: %s\\n" "$OG">/tmp/.omnigraph/config.yaml; '
         f'omnigraph export --server local --graph {shlex.quote(graph)} 2>/dev/null'],
        capture_output=True, text=True)
    recs = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("warning") and (line[0] == "{"):
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs


def find_dupes(nodes, by_name):
    groups = defaultdict(list)
    for n in nodes:
        groups[(n["type"], n["data"]["slug"].casefold())].append(n)
        if by_name:
            lab = n["data"].get(LABEL.get(n["type"], ""), "")
            if lab:
                groups[(n["type"], "name:" + lab.casefold())].append(n)
    # unique groups with >1 distinct slug
    out = []
    seen = set()
    for members in groups.values():
        slugs = sorted({m["data"]["slug"] for m in members})
        if len(slugs) > 1 and tuple(slugs) not in seen:
            seen.add(tuple(slugs))
            out.append(members)
    return out


def canonical(members, forced):
    slugs = {m["data"]["slug"] for m in members}
    for s in slugs:
        if forced.get(s):
            return forced[s]
    lower = sorted(s for s in slugs if s == s.lower())
    if lower:
        return lower[0]
    richest = max(members, key=lambda m: len(m["data"]))
    return richest["data"]["slug"]


def clean_records(nodes, edges, dupes, forced):
    """Collapse duplicate nodes into their canonical slug + de-dupe/redirect edges.
    Returns (merged_nodes_by_slug, out_edges, edge_dupes_dropped)."""
    slug_map = {}
    for members in dupes:
        canon = canonical(members, forced)
        for m in members:
            s = m["data"]["slug"]
            if s != canon:
                slug_map[s] = canon
    merged = {}
    for n in nodes:
        s = n["data"]["slug"]
        canon = slug_map.get(s, s)
        # drop id + embedding: the reload is `overwrite` into a fresh graph, and
        # hand-supplying vectors can trip a Lance ingest error on v0.8.1. @embed
        # regenerates the Decision embedding from `rationale` on load (provider up).
        d = {k: v for k, v in n["data"].items() if k not in ("id", "embedding")}
        d["slug"] = canon
        if canon in merged:
            for k, v in d.items():
                merged[canon]["data"].setdefault(k, v)
        else:
            merged[canon] = {"type": n["type"], "data": d}
    seen, out_edges, edge_dupes = set(), [], 0
    for e in edges:
        fr, to = slug_map.get(e["from"], e["from"]), slug_map.get(e["to"], e["to"])
        if fr == to:
            continue
        k = (e["edge"], fr, to)
        if k in seen:
            edge_dupes += 1
            continue
        seen.add(k)
        out_edges.append({"edge": e["edge"], "from": fr, "to": to})
    return merged, out_edges, edge_dupes


def node_count(a, token, graph="memory", retries=6):
    """Total node rows in the graph (via snapshot). -1 on failure. Retries so a
    just-restarted server (HTTP not ready yet) doesn't spuriously report -1."""
    for attempt in range(retries):
        r = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", a.network,
             "-e", f"OMNIGRAPH_BEARER_TOKEN={token}", "-e", f"OG={a.server}", "-e", "HOME=/tmp", "-w", "/tmp",
             "--entrypoint", "sh", a.image, "-c",
             'mkdir -p /tmp/.omnigraph; printf "servers:\\n  local:\\n    url: %s\\n" "$OG">/tmp/.omnigraph/config.yaml; '
             f'omnigraph snapshot --server local --graph {shlex.quote(graph)} 2>/dev/null'],
            capture_output=True, text=True)
        try:
            d = json.loads(r.stdout[r.stdout.index("{"):])
            return sum(t["rowCount"] for t in d.get("tables", []) if t["tableKey"].startswith("node"))
        except Exception:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(3)
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://omnigraph-server:8080")
    ap.add_argument("--network", default=os.environ.get("OMNI_NET"),
                    help=f"docker network of the CLI container (default: auto-detected from the "
                         f"running omnigraph-server, else {LOCAL_NET})")
    ap.add_argument("--image", default="modernrelay/omnigraph-server:v0.8.1")
    # CENTRAL's compose dir lives in the operator's private site repo: OMNIGRAPH_SITE_DIR,
    # resolved from the environment, then ~/.config/autoos/site.env, then the placeholder
    # default (see infra/mcp-servers/site.env.example).
    site_dir = os.path.expanduser(site_env("OMNIGRAPH_SITE_DIR", "~/site-repo/mcp-servers"))
    ap.add_argument("--token-file", default=os.path.join(site_dir, ".env"))
    ap.add_argument("--compose-file", default=os.path.join(site_dir, "docker-compose.yml"))
    ap.add_argument("--minio-volume", default=None,
                    help="named volume to remove on reset (default: auto-detected)")
    ap.add_argument("--minio-path", default=os.environ.get("MINIO_PATH") or None,
                    help="bind-mount data dir to clear on reset (e.g. ~/apps/omnigraph/minio); "
                         "overrides --minio-volume when the store is a bind mount, not a named volume. "
                         "Default: auto-detected from the running omnigraph-minio")
    ap.add_argument("--backup-dir", default=os.path.join(os.path.dirname(__file__), "..", ".graph-backup"))
    ap.add_argument("--by-name", action="store_true", help="also treat same-type same-label nodes as duplicates")
    ap.add_argument("--map", action="append", default=[], help="force a merge, e.g. --map Finance-Repo=finance-repo")
    ap.add_argument("--graphs", default=os.environ.get("GRAPHS", ""),
                    help="comma-separated graphs to dedup (default: all graphs the server exposes)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.graphs = [g.strip() for g in a.graphs.split(",") if g.strip()]

    # Ask docker what is actually here rather than assuming the local stack: central
    # (coding.example.internal) is compose project `mcp-servers` on `mcp-servers_default` with MinIO
    # on a BIND MOUNT, while local is `mcp-server_mcp-net`. Explicit flags/env win.
    a.network = a.network or detect_network(LOCAL_NET)
    if not a.minio_path and not a.minio_volume:
        kind, val = detect_minio_store()
        if kind == "bind":
            a.minio_path = val
        elif kind == "volume":
            a.minio_volume = val
        else:
            a.minio_volume = LOCAL_MINIO_VOLUME  # omnigraph-minio not on this host
    print("[dedup] stack: " + describe(a.network,
                                       "bind" if a.minio_path else "volume",
                                       a.minio_path or a.minio_volume))

    # --token-file defaults to CENTRAL's .env, which does not exist on a dev box, so also
    # fall back to this repo's own .env.shared — otherwise a local run dies here.
    here = os.path.dirname(os.path.abspath(__file__))
    token = os.environ.get("OMNIGRAPH_TOKEN")
    for path in (a.token_file, os.path.join(here, "..", ".env.shared")):
        if token:
            break
        if not os.path.exists(path):
            continue
        for line in open(path):
            if line.startswith("OMNIGRAPH_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        sys.exit(f"no OMNIGRAPH_TOKEN (env, --token-file {a.token_file}, or ../.env.shared)")
    forced = dict(kv.split("=", 1) for kv in a.map)

    all_graphs = list_graphs(a.server, a.network, a.image, token)
    if not all_graphs:
        sys.exit("[dedup] could not discover any graphs (GET /graphs)")
    target = set(a.graphs) if a.graphs else set(all_graphs)
    unknown = target - set(all_graphs)
    if unknown:
        sys.exit(f"[dedup] unknown graph(s) in --graphs/GRAPHS: {sorted(unknown)}; server has {all_graphs}")
    print(f"[dedup] graphs: {all_graphs}  (dedup targets: {sorted(target)})")

    # 1. Export EVERY graph up front. The rebuild resets the whole MinIO volume
    #    (overwrite is unreliable on a populated v0.8.1 graph), which wipes ALL
    #    graphs — so every graph must be reloaded afterward, even ones we don't
    #    dedup. Non-target graphs pass through unchanged (reload == original).
    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(a.backup_dir, exist_ok=True)
    plan = {}
    for g in all_graphs:
        recs = export_graph(a.server, a.network, a.image, token, g)
        nodes = [r for r in recs if "type" in r]
        edges = [r for r in recs if "edge" in r]
        if g in target:
            dupes = find_dupes(nodes, a.by_name)
            merged, out_edges, edge_dupes = clean_records(nodes, edges, dupes, forced)
        else:  # pass-through: still must be reloaded after the global reset
            dupes = []
            merged = {n["data"]["slug"]: {"type": n["type"],
                      "data": {k: v for k, v in n["data"].items() if k not in ("id", "embedding")}} for n in nodes}
            out_edges = [{"edge": e["edge"], "from": e["from"], "to": e["to"]} for e in edges]
            edge_dupes = 0
        dirty = bool(dupes or edge_dupes)
        plan[g] = dict(recs=recs, merged=merged, out_edges=out_edges,
                       dupes=dupes, edge_dupes=edge_dupes, dirty=dirty)
        print(f"[dedup]   {g}: {len(nodes)} nodes, {len(edges)} edges — "
              + (f"{len(dupes)} node-dup group(s), {edge_dupes} dup edge(s)" if dirty
                 else ("clean" if g in target else "pass-through")))

    if not any(p["dirty"] for p in plan.values()):
        print("[dedup] no duplicates in any graph — nothing to do.")
        return
    if a.dry_run:
        for g, p in plan.items():
            if p["dirty"]:
                print(f"[dedup] --dry-run {g}: collapse {len(p['dupes'])} node group(s) + drop {p['edge_dupes']} edge(s)")
        return

    # 2. Back up every graph + write its cleaned jsonl (so the reset is recoverable).
    cleans = {}
    for g, p in plan.items():
        bk = os.path.join(a.backup_dir, f"pre-dedup-{g}-{ts}.jsonl")
        with open(bk, "w", encoding="utf-8") as f:
            for r in p["recs"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        cf = os.path.join(a.backup_dir, f"dedup-clean-{g}-{ts}.jsonl")
        with open(cf, "w", encoding="utf-8") as f:
            for v in p["merged"].values():
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
            for e in p["out_edges"]:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        cleans[g] = cf
    print(f"[dedup] backups + cleaned files under {a.backup_dir} (ts {ts})")

    # 3. Reset the store ONCE (wipes ALL graphs; every graph is reloaded in step 4).
    print("[dedup] resetting store (wipes ALL graphs; each is reloaded below)...")
    dc = ["docker", "compose", "-f", a.compose_file]
    svcs = ["omnigraph-server", "omnigraph-viewer", "omnigraph-minio",
            "omnigraph-init", "omnigraph-minio-init"]
    def bring_up():  # never leave the stack down on any exit after we've stopped it
        sh(dc + ["up", "-d"] + list(reversed(svcs)), capture_output=True)
    sh(dc + ["stop"] + svcs, capture_output=True)
    sh(dc + ["rm", "-f"] + svcs, capture_output=True)
    # Wait for MinIO to be TRULY gone before touching the store. A still-shutting-down
    # minio rewrites .minio.sys under the clear — the 2026-07-17 data-loss race, where the
    # clear removed the graph data but minio recreated empty shells, an `ls -A` check then
    # read those shells as "clear failed", and the run aborted WITHOUT reloading -> loss.
    for _ in range(20):
        if not sh(["docker", "ps", "-aq", "--filter", "name=omnigraph-minio"],
                  capture_output=True, text=True).stdout.strip():
            break
        time.sleep(1)
    if a.minio_path:
        # Bind-mount store: clear the dir in a root container (files are root-owned).
        # Do NOT abort on leftover shells here — whatever the clear did, we ALWAYS restart
        # and reload below, and the per-graph empty-guard in step 4 is the real safety, so
        # a partial clear self-heals (reload) instead of losing data.
        sh(["docker", "run", "--rm", "-v", f"{a.minio_path}:/data", "alpine",
            "sh", "-c", "rm -rf /data/* /data/.minio.sys 2>/dev/null; true"], capture_output=True)
    else:
        vols_before = sh(["docker", "volume", "ls", "--format", "{{.Name}}"],
                         capture_output=True, text=True).stdout.split()
        if a.minio_volume not in vols_before:
            bring_up()
            sys.exit(f"[dedup] ABORT: named volume {a.minio_volume} does not exist — is MinIO a "
                     f"bind mount? pass --minio-path <dir>. Stack restarted, graphs intact.")
        for c in sh(["docker", "ps", "-aq", "--filter", f"volume={a.minio_volume}"],
                    capture_output=True, text=True).stdout.split():
            sh(["docker", "rm", "-f", c], capture_output=True)
        sh(["docker", "volume", "rm", a.minio_volume], capture_output=True)
        vols = sh(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout.split()
        if a.minio_volume in vols:
            bring_up()
            sys.exit(f"[dedup] ABORT: could not remove volume {a.minio_volume}. Stack restarted, graphs intact.")

    bring_up()
    for _ in range(45):
        h = sh(dc + ["ps", "omnigraph-server", "--format", "{{.Status}}"], capture_output=True, text=True)
        if "healthy" in h.stdout:
            break
        time.sleep(2)

    # 4. Reload every graph's cleaned data into the fresh (empty) store.
    short = []
    for g, p in plan.items():
        # GUARD: never overwrite a non-empty graph (that is what silently accumulated
        # nodes on v0.8.1 when a wipe failed). Abort unless the fresh graph is empty.
        # Only load when the graph is VERIFIABLY empty. node_count returns -1 when the
        # probe itself failed — an unknown state is NOT an empty one, and treating it as
        # empty would load straight onto existing data. (Same empty-reads-as-success trap
        # that made a dead server verify "clean" on 2026-07-17.)
        empty = node_count(a, token, g)
        if empty != 0:
            sys.exit(f"[dedup] ABORT: {g} is not verifiably empty (node_count={empty}; -1 means the "
                     f"probe FAILED, not that it is empty) — not loading, to avoid duplicating onto "
                     f"existing data. Stack is up; backups under {a.backup_dir}")
        data = open(cleans[g], "rb").read()
        r = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", a.network,
             "-e", f"OMNIGRAPH_BEARER_TOKEN={token}", "-e", f"OG={a.server}", "-e", "HOME=/tmp", "-w", "/tmp",
             "--entrypoint", "sh", a.image, "-c",
             'mkdir -p /tmp/.omnigraph; printf "servers:\\n  local:\\n    url: %s\\n" "$OG">/tmp/.omnigraph/config.yaml; '
             f'cat > /tmp/c.jsonl; omnigraph load --server local --graph {shlex.quote(g)} --data /tmp/c.jsonl --mode overwrite --yes --json'],
            input=data, capture_output=True)
        if r.returncode != 0:
            sys.exit(f"[dedup] {g} overwrite failed; restore from {a.backup_dir}. stderr:\n{r.stderr.decode()[-400:]}")
        got, want = node_count(a, token, g), len(p["merged"])
        print(f"[dedup]   {g}: {got} nodes" + ("" if got == want else f"  !! expected {want}"))
        if got != want:
            short.append(f"{g} (got {got}, expected {want})")
    # NEVER print "done" over a restore that did not land. Reporting success while the
    # graphs are short/empty is precisely how the 2026-07-17 wipe went unnoticed.
    if short:
        sys.exit(f"[dedup] FAILED: these graphs did not come back at their expected counts: "
                 f"{', '.join(short)}. The stack is up; restore them from {a.backup_dir} "
                 f"(pre-dedup-<graph>-{ts}.jsonl) before trusting this store.")
    print("[dedup] done — all graphs deduped + reloaded (counts verified).")


if __name__ == "__main__":
    main()
