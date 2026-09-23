#!/usr/bin/env bash
# Declare a new isolated per-project graph in cluster/cluster.yaml, then remind
# you to converge it. Idempotent: re-running for an existing graph is a no-op.
#
# cluster/cluster.yaml is GITIGNORED — copy it from cluster.yaml.example on first
# setup. That is on purpose: a graph is named after the repo it serves, so every
# name this script appends would otherwise be published by a public repository.
#
#   ./scripts/add-project-graph.sh my-new-project
#   ./scripts/apply-cluster.sh          # converge it into the live cluster
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: $0 <project-graph-name>"; exit 1; }
name="$1"
here="$(cd "$(dirname "$0")/.." && pwd)"
cfg="$here/cluster/cluster.yaml"

python3 - "$cfg" "$name" <<'PY'
import sys, re
cfg, name = sys.argv[1], sys.argv[2]
s = open(cfg).read()

# 1) add the graph block immediately before the `policies:` section
if re.search(rf'^  {re.escape(name)}:\s*$', s, re.M):
    print(f"graph '{name}' already declared — skipping graph block")
else:
    block = (f"  {name}:\n    schema: memory.pg\n"
             f"    embedding_provider: default\n    queries: queries/\n")
    s = re.sub(r'\npolicies:\n', "\n" + block + "\npolicies:\n", s, count=1)
    print(f"added graph '{name}'")

# 2) extend the project-graphs-access applies_to list with the new graph
def extend(m):
    items = [x.strip() for x in m.group(2).split(',') if x.strip()]
    if name in items:
        print(f"'{name}' already granted access — skipping")
    else:
        items.append(name)
        print(f"granted access to '{name}'")
    return m.group(1) + "[" + ", ".join(items) + "]"

s = re.sub(r'(project-graphs\.policy\.yaml\n    applies_to: )\[([^\]]*)\]',
           extend, s, count=1)

open(cfg, "w").write(s)
PY

echo "→ now run: ./scripts/apply-cluster.sh"
echo "→ then migrate any existing data out of the shared graph:"
echo "     python3 scripts/split-project-graph.py $name --apply"
echo "→ point a repo's agent at it by setting OMNIGRAPH_GRAPH_ID=$name in that repo's"
echo "  .mcp.json (OMNIGRAPH_GRAPH is the *viewer's* variable, not the MCP bridge's)."
