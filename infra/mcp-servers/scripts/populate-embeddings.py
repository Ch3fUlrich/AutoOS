#!/usr/bin/env python3
"""Populate Decision vector embeddings in the local Omnigraph `memory` graph
using the LOCAL Ollama container.

See docs/OMNIGRAPH-LOCAL-RUNBOOK.md section 4 for the full rationale. In short,
none of the "obvious" paths work on omnigraph-server v0.8.1:
  * the server does NOT auto-embed on boot;
  * `load --mode merge` of hand-supplied vectors hits a Lance batch error
    ("all columns in a record batch must have the same length");
  * the `omnigraph embed` CLI's --spec provider ignores its base_url (defaults to
    OpenRouter -> 401), and --server/--cluster are rejected.
The reliable path, automated here: embed each Decision.rationale against the local
Ollama, then `load --mode overwrite` the whole graph.

IMPORTANT: `--mode overwrite` REPLACES the entire graph, so pass ALL project seeds
in one run (e.g. every file in cluster/seed/ that belongs on `main`).

Prereqs: local stack up; `docker exec ollama ollama pull nomic-embed-text`.

Example:
  cd infra/mcp-servers
  python scripts/populate-embeddings.py \
    --seeds cluster/seed/agent-skills.jsonl cluster/seed/site-repo.jsonl \
            cluster/seed/sibling-analysis-repo.jsonl cluster/seed/finance-repo.jsonl
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request


def embed(ollama, model, text, key="ollama"):
    body = json.dumps({"model": model, "input": text}).encode()
    req = urllib.request.Request(
        ollama.rstrip("/") + "/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["data"][0]["embedding"]


def read_token(env_shared):
    if os.environ.get("OMNIGRAPH_TOKEN"):
        return os.environ["OMNIGRAPH_TOKEN"]
    try:
        for line in open(env_shared, encoding="utf-8"):
            s = line.strip()
            if s.startswith("OMNIGRAPH_TOKEN="):
                return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description="Populate Decision embeddings via local Ollama.")
    ap.add_argument("--seeds", nargs="+", required=True,
                    help="ALL seed NDJSON files for the graph (overwrite replaces everything)")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--model", default="nomic-embed-text")
    ap.add_argument("--graph", default="memory")
    ap.add_argument("--server", default="http://omnigraph-server:8080",
                    help="server URL as seen from inside the CLI container")
    ap.add_argument("--image", default="modernrelay/omnigraph-server:v0.8.1")
    ap.add_argument("--network", default="mcp-server_mcp-net")
    ap.add_argument("--env-shared", default=".env.shared")
    ap.add_argument("--out", default=None, help="embedded NDJSON output path (default: temp)")
    ap.add_argument("--no-load", action="store_true", help="write embedded NDJSON only; skip load")
    a = ap.parse_args()

    records, n = [], 0
    for fn in a.seeds:
        for line in open(fn, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "Decision":
                txt = rec["data"].get("rationale") or rec["data"].get("title", "")
                rec["data"]["embedding"] = embed(a.ollama, a.model, txt)
                n += 1
            records.append(rec)
    print(f"[populate-embeddings] embedded {n} Decision nodes via {a.ollama} ({a.model})",
          file=sys.stderr)

    out = a.out or os.path.join(tempfile.gettempdir(), "omnigraph.embedded.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[populate-embeddings] wrote {len(records)} records -> {out}", file=sys.stderr)

    if a.no_load:
        return
    token = read_token(a.env_shared)
    if not token:
        sys.exit("no OMNIGRAPH_TOKEN (set env or pass --env-shared pointing at .env.shared)")
    cmd = [
        "docker", "run", "--rm", "-i", "--network", a.network,
        "-e", f"OMNIGRAPH_BEARER_TOKEN={token}",
        "-e", f"TARGET_SERVER={a.server}",
        "-e", f"TARGET_GRAPH={a.graph}",
        "--entrypoint", "sh", a.image, "-c",
        "cat > /tmp/e.jsonl; omnigraph load --server \"$TARGET_SERVER\" --graph \"$TARGET_GRAPH\" "
        "--data /tmp/e.jsonl --mode overwrite --yes --json",
    ]
    print("[populate-embeddings] overwrite-loading embedded graph via docker CLI...", file=sys.stderr)
    with open(out, "rb") as f:
        rc = subprocess.run(cmd, stdin=f).returncode
    if rc == 0:
        print("[populate-embeddings] done. Verify with search_decisions.", file=sys.stderr)
    sys.exit(rc)


if __name__ == "__main__":
    main()
