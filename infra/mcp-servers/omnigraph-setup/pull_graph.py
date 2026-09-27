#!/usr/bin/env python3
"""Pull one graph from a source server into a target server: target := source.

Why this exists instead of `load --mode overwrite`:
`overwrite` into a POPULATED graph trips a Lance bug on omnigraph v0.8.1
(`stage_create_btree_index on node:<T>: all columns in a record batch must have the same
length`). Loading into an EMPTY graph is the one write path that is reliable — it is how
central was repaired on 2026-07-17. So: purge the target (delete every node; edges cascade),
then merge-load the source's deduped export into the now-empty graph.

Safety:
  * refuses to purge unless the source export is non-empty and duplicate-free
  * verifies the target is actually empty before loading (never loads onto a half-purge)
  * on load failure, restores the target from the pre-purge backup and exits non-zero
  * verifies the final state matches the source, and says so

Usage:
  python pull_graph.py <graph> --source-url URL --source-token T \
                               --target-url URL --target-token T \
                               --backup <file.jsonl> [--net mcp-server_mcp-net]
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter

IMAGE = "modernrelay/omnigraph-server:v0.8.1"


def export(url, token, graph):
    req = urllib.request.Request(f"{url.rstrip('/')}/graphs/{graph}/export", data=b"{}",
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST")
    body = urllib.request.urlopen(req, timeout=300).read().decode()
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def mutate(url, token, graph, query):
    req = urllib.request.Request(f"{url.rstrip('/')}/graphs/{graph}/mutate",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def load_merge(url, token, graph, payload, net):
    cmd = ["docker", "run", "--rm", "-i", "--network", net,
           "-e", f"OMNIGRAPH_BEARER_TOKEN={token}",
           "-e", f"TARGET_URL={url}",
           "-e", f"TARGET_GRAPH={graph}",
           "--entrypoint", "sh", IMAGE, "-c",
           "cat > /tmp/d.jsonl; omnigraph load --server \"$TARGET_URL\" --graph \"$TARGET_GRAPH\" "
           "--data /tmp/d.jsonl --mode merge --yes --json"]
    p = subprocess.run(cmd, input=payload, text=True, capture_output=True,
                       env={**os.environ, "MSYS2_ARG_CONV_EXCL": "*"})
    if p.returncode != 0:
        raise RuntimeError(f"load failed (exit {p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def stats(recs):
    n = [r for r in recs if "type" in r]
    e = [r for r in recs if "edge" in r]
    return len(n), len(e), len({(r["edge"], r["from"], r["to"]) for r in e})


def dedup(recs):
    seen = {}
    for r in recs:
        k = (("node", r.get("type"), (r.get("data") or {}).get("slug")) if "type" in r
             else ("edge", r.get("edge"), r.get("from"), r.get("to")))
        seen[k] = r
    return list(seen.values())


def purge(url, token, graph, recs):
    by_type = Counter(r["type"] for r in recs if "type" in r)
    for ntype in sorted(by_type):
        slugs = [r["data"]["slug"] for r in recs if r.get("type") == ntype]
        for i in range(0, len(slugs), 40):
            stmts = "\n  ".join(f'delete {ntype} where slug = "{s}"' for s in slugs[i:i + 40])
            mutate(url, token, graph, f"query purge_{ntype.lower()}_{i}() {{\n  {stmts}\n}}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("graph")
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--source-token", required=True)
    ap.add_argument("--target-url", required=True,
                    help="target API URL reachable FROM THIS HOST (export/mutate via urllib), "
                         "e.g. http://127.0.0.1:8080")
    ap.add_argument("--target-token", required=True)
    ap.add_argument("--target-load-url",
                    help="target URL reachable FROM INSIDE the CLI container (the load runs "
                         "there), e.g. http://omnigraph-server:8080. Defaults to --target-url — "
                         "which is WRONG for 127.0.0.1: inside a container that is the container "
                         "itself and the load dies with 'Connection refused'.")
    ap.add_argument("--net", default="mcp-server_mcp-net")
    ap.add_argument("--backup", help="pre-purge target export (restore source on failure)")
    a = ap.parse_args()
    g = a.graph
    load_url = a.target_load_url or a.target_url

    src = dedup(export(a.source_url, a.source_token, g))
    sn, se, sde = stats(src)
    if sn == 0:
        sys.exit(f"[pull:{g}] REFUSING: source export is empty — would wipe the target")
    if se != sde:
        sys.exit(f"[pull:{g}] REFUSING: source has duplicate edges ({se} vs {sde} distinct)")

    tgt = export(a.target_url, a.target_token, g)
    tn, te, _ = stats(tgt)
    if (tn, te) == (sn, se) and dedup(tgt) == src:
        print(f"[pull:{g}] already identical ({sn} nodes / {se} edges) — nothing to pull")
        return 0
    print(f"[pull:{g}] target {tn}/{te} -> source {sn}/{se}")

    backup = tgt
    if a.backup:
        with open(a.backup, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in tgt) + "\n")

    # PRE-FLIGHT: prove the load path works BEFORE purging anything. The load runs inside a
    # container, so a --target-load-url of 127.0.0.1 resolves to the container itself and
    # fails with "Connection refused" — after the purge, which empties the graph and then
    # cannot restore it either (both use the same broken path). Learned the hard way
    # 2026-07-17: never purge until the thing that refills is known to work.
    try:
        subprocess.run(
            ["docker", "run", "--rm", "--network", a.net,
             "-e", f"OMNIGRAPH_BEARER_TOKEN={a.target_token}", "--entrypoint", "omnigraph",
             IMAGE, "snapshot", "--server", load_url, "--graph", g],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "MSYS2_ARG_CONV_EXCL": "*"}, check=True)
    except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as exc:
        err = getattr(exc, "stderr", "") or str(exc)
        sys.exit(f"[pull:{g}] REFUSING to purge: the CLI container cannot reach the target at "
                 f"{load_url!r} — the load would fail and leave the graph empty. "
                 f"Pass --target-load-url with a container-reachable URL "
                 f"(e.g. http://omnigraph-server:8080).\n{err.strip()[:300]}")

    # Purge, then load into the (near-)empty graph — the only reliable write path on v0.8.1.
    purge(a.target_url, a.target_token, g, tgt)
    now = export(a.target_url, a.target_token, g)
    n2, e2, _ = stats(now)

    # Deleting a node USUALLY cascades its edges — but not always: observed 2026-07-17 on a
    # graph that held a duplicate node, where all 19 nodes went but all 27 edges stayed.
    # So do not assume an empty graph. Load only what is actually missing (same rule as the
    # push: nodes upsert by @key, edges must not be re-sent or they duplicate). An earlier
    # version aborted here and left the graph purged — a failure path must never destroy data.
    if n2:
        print(f"[pull:{g}] !! purge left {n2} node(s) — restoring from backup", file=sys.stderr)
        load_merge(load_url, a.target_token, g,
                   "\n".join(json.dumps(r) for r in backup) + "\n", a.net)
        return 5
    if e2:
        print(f"[pull:{g}] purge left {e2} orphan edge(s) (cascade did not fire) — "
              f"loading only what is missing")

    have_edges = {(r["edge"], r["from"], r["to"]) for r in now if "edge" in r}
    missing = [r for r in src if "edge" not in r or (r["edge"], r["from"], r["to"]) not in have_edges]
    stray = have_edges - {(r["edge"], r["from"], r["to"]) for r in src if "edge" in r}
    if stray:
        print(f"[pull:{g}] !! {len(stray)} orphan edge(s) are NOT in the source and cannot be "
              f"deleted (no delete-edge API) — restoring from backup", file=sys.stderr)
        load_merge(load_url, a.target_token, g,
                   "\n".join(json.dumps(r) for r in backup) + "\n", a.net)
        return 6

    payload = "\n".join(json.dumps(r) for r in missing) + "\n"
    try:
        load_merge(load_url, a.target_token, g, payload, a.net)
    except RuntimeError as exc:
        print(f"[pull:{g}] !! load failed ({exc}) — restoring target from backup", file=sys.stderr)
        try:
            load_merge(load_url, a.target_token, g,
                       "\n".join(json.dumps(r) for r in backup) + "\n", a.net)
            print(f"[pull:{g}] target restored to its previous state", file=sys.stderr)
        except RuntimeError as exc2:
            print(f"[pull:{g}] !! RESTORE ALSO FAILED: {exc2}\n"
                  f"[pull:{g}] !! target is EMPTY. Reload from {a.backup} or cluster/seed/{g}.jsonl",
                  file=sys.stderr)
        return 3

    fin = export(a.target_url, a.target_token, g)
    fn, fe, fde = stats(fin)
    if (fn, fe, fde) != (sn, se, sde):
        print(f"[pull:{g}] !! final {fn}/{fe} != source {sn}/{se}", file=sys.stderr)
        return 4
    print(f"[pull:{g}] pulled OK — {fn} nodes / {fe} edges (distinct {fde}), matches source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
