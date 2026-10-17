#!/usr/bin/env python3
"""Patch graphify's clustering crash on mixed str/int node ids (graphifyy <= 0.9.17).

Symptom: extraction succeeds (nodes + edges are current) but the clustering step dies
with `TypeError: '<' not supported between instances of 'int' and 'str'`, so every node
keeps `community: null` and the community labels silently go stale — the graph looks
fine, the clusters are just old.

Cause: `graphify/cluster.py` sorts node ids with a bare `sorted(...)`. Node ids are
normally strings, but a semantic-extraction chunk can yield a numeric id (the same
class of malformed-LLM-output this repo already patches in
patch-graphify-ollama-bugs.py). One int among thousands of strs is enough: Python 3
refuses to order int against str. Real example — sibling-analysis-repo: 14,275 str ids and
17 int ids, clustering dead.

Upstream already fixed this *partially* — some calls in the same file pass `key=str`
(`add_nodes_from(sorted(G.nodes(), key=str))`, `sorted(str(n) for n in members)`,
`tuple(sorted(map(str, nodes)))`) — but the rest were missed. This applies the same
`key=str` to the remaining node-id sorts.

Behaviour-preserving: for an all-string graph (the normal case) `key=str` is identity,
so ordering is unchanged; it only stops the crash when an int sneaks in. Sorts over
non-id values (degrees, community ids) are deliberately left alone.

Idempotent — safe to re-run (e.g. from init-graphify-projects.* before extraction).

    python scripts/patch-graphify-cluster-sort.py            # report only
    python scripts/patch-graphify-cluster-sort.py --apply
"""
import argparse
import glob
import os
import re
import sys

# (exact source text, replacement) — only sorts whose operand is a node-id set.
# Anchored on the exact source text so a changed upstream line is reported, not mangled.
#
# build.py is where the CLI actually dies first (`graphify cluster-only` ->
# build_from_json -> `for nid in sorted(node_set)`); cluster.py crashes next once the
# graph is built. Both must be patched — verified by running `python -m graphify
# cluster-only .` against sibling-analysis-repo (14,275 str ids + 17 int ids).
BUILD_PATCHES = [
    ("for nid in sorted(node_set):", "for nid in sorted(node_set, key=str):"),
]
PATCHES = [
    # _singleton_communities: {i: [n] for i, n in enumerate(sorted(G.nodes))}
    ("sorted(G.nodes)", "sorted(G.nodes, key=str)"),
    # hub splitting
    ("for hub in sorted(hub_nodes):", "for hub in sorted(hub_nodes, key=str):"),
    # final community re-index
    ("return {i: sorted(nodes) for i, nodes in enumerate(final_communities)}",
     "return {i: sorted(nodes, key=str) for i, nodes in enumerate(final_communities)}"),
    # split helpers
    ("return [[n] for n in sorted(nodes)]", "return [[n] for n in sorted(nodes, key=str)]"),
    ("return [sorted(nodes)]", "return [sorted(nodes, key=str)]"),
    ("return [sorted(v) for v in sub_communities.values()]",
     "return [sorted(v, key=str) for v in sub_communities.values()]"),
    # stable-id remap tiebreak + payload
    ("unmatched.sort(key=lambda cid: (-len(communities[cid]), tuple(sorted(communities[cid]))))",
     "unmatched.sort(key=lambda cid: (-len(communities[cid]), tuple(sorted(map(str, communities[cid])))))"),
    ("remapped[new_to_final[new_cid]] = sorted(nodes)",
     "remapped[new_to_final[new_cid]] = sorted(nodes, key=str)"),
]


def find_files(name):
    roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\uv\cache\archive-v0"),
        os.path.expanduser("~/.cache/uv/archive-v0"),
    ]
    out = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for depth in (f"*/graphify/{name}", f"*/Lib/site-packages/graphify/{name}",
                      f"*/lib/python*/site-packages/graphify/{name}"):
            out.update(glob.glob(os.path.join(root, depth)))
    return sorted(out)


def drop_pyc(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    for pyc in glob.glob(os.path.join(os.path.dirname(path), "__pycache__", f"{stem}.*.pyc")):
        try:
            os.remove(pyc)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the patches (default: report only)")
    a = ap.parse_args()

    targets = [(p, PATCHES) for p in find_files("cluster.py")] + \
              [(p, BUILD_PATCHES) for p in find_files("build.py")]
    if not targets:
        print("No graphify installs found in the uv cache — nothing to patch.")
        return 0

    total_applied = total_already = 0
    for path, patch_set in targets:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        applied, already, missing = 0, 0, []
        new = src
        for old, rep in patch_set:
            if rep in new:
                already += 1
            elif old in new:
                new = new.replace(old, rep)
                applied += 1
            else:
                missing.append(old)
        ver = re.search(r"graphifyy-([\d.]+)\.dist-info", " ".join(glob.glob(
            os.path.join(os.path.dirname(os.path.dirname(path)), "graphifyy-*.dist-info"))))
        tag = f" (v{ver.group(1)})" if ver else ""
        status = f"patched={applied} already={already}"
        if missing:
            status += f" NOT-FOUND={len(missing)}"
        print(f"  {'APPLY ' if (a.apply and applied) else 'check '}{path}{tag}: {status}")
        for m in missing:
            print(f"      ! upstream line changed, skipped: {m[:70]}")
        if a.apply and applied:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            drop_pyc(path)
        total_applied += applied
        total_already += already

    print(f"\n{len(targets)} file(s): {total_applied} patch(es) applied, {total_already} already present.")
    if not a.apply and total_applied:
        print("dry run — re-run with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
