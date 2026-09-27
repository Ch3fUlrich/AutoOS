#!/usr/bin/env python3
"""JSONL helpers for Omnigraph sync — guarantee NO node/edge duplicates.

Omnigraph nodes dedupe by @key (slug) on merge, but EDGES are not slug-keyed, so
a naive cross-store export -> load can duplicate edges. This utility dedupes and
verifies a graph export so the sync helpers can enforce "no duplicates after
merge" (see docs/REMOTE-SYNC-TEST-PLAN.md).

Record shapes (NDJSON):
  node: {"type":"<NodeType>","data":{"slug":"...", ...}}
  edge: {"edge":"<EdgeType>","from":"<slug>","to":"<slug>"}

Usage:
  cat graph.jsonl | python3 omnigraph_jsonl.py dedup  > clean.jsonl
  cat graph.jsonl | python3 omnigraph_jsonl.py verify        # exit 1 if duplicates
  cat local.jsonl | python3 omnigraph_jsonl.py pushset central.jsonl > push.jsonl
"""
import json
import sys


def node_key(rec):
    return ("node", rec.get("type"), (rec.get("data") or {}).get("slug"))


def edge_key(rec):
    return ("edge", rec.get("edge"), rec.get("from"), rec.get("to"))


def rec_key(rec):
    if "type" in rec:
        return node_key(rec)
    if "edge" in rec:
        return edge_key(rec)
    return ("other", json.dumps(rec, sort_keys=True))


def load(lines):
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            out.append((ln, json.loads(ln)))
    return out


def dedup(lines):
    # Nodes: keep LAST occurrence (most recent wins). Edges: keep one.
    seen = {}
    for ln, rec in load(lines):
        seen[rec_key(rec)] = ln
    return list(seen.values())


# Fields that legitimately differ between two copies of the "same" node and must not
# count as a change: `id` mirrors `slug`, and `embedding` is regenerable — and is *erased*
# by a merge whose record omits it, so comparing on it would push noise forever.
VOLATILE = ("embedding", "id")


def _payload(rec):
    return {k: v for k, v in (rec.get("data") or {}).items() if k not in VOLATILE}


def pushset(local_lines, central_lines):
    """The DELTA that is safe to merge-load from LOCAL onto a branch forked from CENTRAL.

    Two independent reasons this must be a delta and not the whole export:

    1. Edges have no `@key`, so merge-loading an edge central already has APPENDS a second
       copy; the branch-merge then carries the duplicate into main. Pushing everything is
       exactly how central got 2x edges on every project graph (2026-07-17).
    2. Pushing a node that is *identical* to central's still bumps that table's version on
       the device branch, and the branch-merge then fails with
       `Concurrent modification: table version N already exists for node:<Type>` — the
       merge cannot fast-forward because main is already at N. So an unchanged node is not
       harmless: it breaks the whole sync.

    Emit a node only when central lacks the slug or its payload actually differs
    (ignoring VOLATILE fields); emit an edge only when central lacks it. An empty result
    means "nothing to push" — the caller should skip the branch entirely.
    """
    c_edges = set()
    c_nodes = {}
    for _ln, r in load(central_lines):
        if "edge" in r:
            c_edges.add(rec_key(r))
        elif "type" in r:
            c_nodes[node_key(r)] = _payload(r)
    out = []
    for ln, rec in dedup_pairs(local_lines):
        if "edge" in rec:
            if rec_key(rec) in c_edges:
                continue
        elif "type" in rec:
            k = node_key(rec)
            if k in c_nodes and c_nodes[k] == _payload(rec):
                continue  # byte-identical (modulo volatile fields) — pushing it breaks the merge
        out.append(ln)
    return out


def dedup_pairs(lines):
    seen = {}
    for ln, rec in load(lines):
        seen[rec_key(rec)] = (ln, rec)
    return list(seen.values())


def verify(lines):
    counts = {}
    for _ln, rec in load(lines):
        k = rec_key(rec)
        counts[k] = counts.get(k, 0) + 1
    dup_nodes = [k for k, v in counts.items() if v > 1 and k[0] == "node"]
    dup_edges = [k for k, v in counts.items() if v > 1 and k[0] == "edge"]
    n_nodes = sum(v for k, v in counts.items() if k[0] == "node")
    n_edges = sum(v for k, v in counts.items() if k[0] == "edge")
    d_nodes = sum(1 for k in counts if k[0] == "node")
    d_edges = sum(1 for k in counts if k[0] == "edge")
    return n_nodes, d_nodes, n_edges, d_edges, dup_nodes, dup_edges


def _force_utf8_stdio():
    """Read and write UTF-8 regardless of the host locale.

    Omnigraph exports are UTF-8 (JSON always is). Python, however, decodes stdin with the
    *locale* encoding — `cp1252` on this Windows box — while the central export is opened
    with `encoding="utf-8"` a few lines up. So the SAME text arrived as two unequal
    strings, and `pushset` dutifully reported every node containing an em dash, an arrow
    or an umlaut as "changed" — 52 of sibling-analysis-repo's 135 nodes, on every single run,
    forever. Nothing was corrupted (a cp1252 decode/encode round-trip is byte-lossless),
    but each run re-pushed those nodes to central for no reason.

    Fixing this in the script rather than by exporting PYTHONIOENCODING at each call site
    keeps the guarantee with the code that needs it: this file is also run by hand, by
    systemd, and by the Task Scheduler, and only one of those would have carried the env.
    """
    for stream, extra in ((sys.stdin, {}), (sys.stdout, {"newline": "\n"}), (sys.stderr, {})):
        try:
            stream.reconfigure(encoding="utf-8", **extra)
        except (AttributeError, ValueError):
            pass  # already detached/redirected — the caller's encoding stands


def main():
    _force_utf8_stdio()
    argv = [a for a in sys.argv[1:] if a != "--allow-empty"]
    allow_empty = "--allow-empty" in sys.argv
    cmd = argv[0] if argv else "verify"
    lines = sys.stdin.readlines()
    if cmd == "dedup":
        for ln in dedup(lines):
            print(ln)
        return
    if cmd == "pushset":
        if len(argv) < 2:
            sys.exit("usage: … | omnigraph_jsonl.py pushset <central-export.jsonl>")
        with open(argv[1], encoding="utf-8") as fh:
            central = fh.readlines()
        for ln in pushset(lines, central):
            print(ln)
        return
    n_nodes, d_nodes, n_edges, d_edges, dup_nodes, dup_edges = verify(lines)
    sys.stderr.write(
        f"[verify] nodes={n_nodes} (distinct {d_nodes}) | edges={n_edges} (distinct {d_edges})\n"
    )
    # EMPTY IS NOT CLEAN. An empty body is exactly what a failed fetch looks like —
    # dead server, wrong token, wrong graph, curl error — and reporting "clean" for it
    # is how a wiped stack read as healthy on 2026-07-17 (the graphs were gone; every
    # graph still printed "clean"). Refuse to pass on no data unless told to.
    if n_nodes == 0 and n_edges == 0 and not allow_empty:
        sys.stderr.write(
            "[verify] RESULT: NO DATA — refusing to call this clean.\n"
            "[verify]   0 records is what a FAILED FETCH looks like (dead server / bad token /\n"
            "[verify]   wrong graph), not proof of a healthy graph. Check the source, or pass\n"
            "[verify]   --allow-empty if the graph is genuinely expected to be empty.\n"
        )
        sys.exit(2)
    if dup_nodes:
        sys.stderr.write(f"[verify] DUPLICATE NODES ({len(dup_nodes)}): {dup_nodes[:20]}\n")
    if dup_edges:
        sys.stderr.write(f"[verify] DUPLICATE EDGES ({len(dup_edges)}): {dup_edges[:20]}\n")
    if dup_nodes or dup_edges:
        sys.stderr.write("[verify] RESULT: DUPLICATES PRESENT\n")
        sys.exit(1)
    sys.stderr.write("[verify] RESULT: clean (no duplicates)\n")


if __name__ == "__main__":
    main()
