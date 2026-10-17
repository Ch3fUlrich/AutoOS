#!/usr/bin/env bash
# client-setup.sh — configure a device to use the Omnigraph memory server.
#
#   --mode online   (default) register the omnigraph MCP against the central
#                   server; the agent works on `main` directly. Nothing to sync.
#   --mode offline  bring up a LOCAL stack and install the sync timer so the
#                   agent works on local `main` and reconciles when online.
#
#   --url    <URL>     central server URL (e.g. https://omnigraph.example.org)
#   --token  <BEARER>  central bearer token
#
# Examples:
#   ./client-setup.sh --mode online  --url https://omnigraph.example.org --token $TOK
#   ./client-setup.sh --mode offline --url https://omnigraph.example.org --token $TOK
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mode="online"; URL=""; TOKEN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) mode="$2"; shift 2;;
    --url) URL="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "need --url and --token" >&2; exit 2; }

# The bridge env contract is OMNIGRAPH_BASE_URL + OMNIGRAPH_GRAPH_ID + OMNIGRAPH_TOKEN.
# `OMNIGRAPH_URL` is the VIEWER's variable and does nothing here; a bridge without
# OMNIGRAPH_GRAPH_ID refuses to start (the server is cluster-only). Per-project isolation
# means the graph is per-repo, so this prints a project-scoped .mcp.json rather than
# registering one user-scope bridge pinned to a single graph.
#
# ONE bridge, not two. A second `omnigraph-globals` server on `memory` was removed on
# 2026-07-17: `memory` holds only two global Preferences (TDD-by-default, MCP-first nav),
# which are already Principles 2 and 6 of skills/coding-principles/SKILL.md, so a whole
# extra MCP server to re-serve two lines was duplication — and it listed every omnigraph
# tool twice in the picker.
#
# The docker form is preferred over npx: coding.example.internal has no node/npx, and docker works on
# every host that runs the stack. OMNIGRAPH_NET differs per host, so probe it rather than
# hardcoding: python3 ../scripts/_omni_env.py
register_mcp() {  # $1=BASE_URL $2=TOKEN
  cat <<JSON
Add this to the repo's .mcp.json (project scope — the graph travels with the repo).
Replace <repo-folder-name> with the repo's folder name; that IS the graph id.
Keep the bearer OUT of a tracked file — reference the env var:

  "omnigraph": {
    "command": "docker",
    "args": ["run", "-i", "--rm",
             "--network", "\${OMNIGRAPH_NET:-mcp-server_mcp-net}",
             "-e", "OMNIGRAPH_BASE_URL=$1",
             "-e", "OMNIGRAPH_GRAPH_ID=<repo-folder-name>",
             "-e", "OMNIGRAPH_TOKEN=\${OMNIGRAPH_TOKEN}",
             "omnigraph-mcp:latest"]
  }

Then export these once per machine, before launching the agent:
  export OMNIGRAPH_TOKEN=$2
  export OMNIGRAPH_NET=\$(python3 ../scripts/_omni_env.py | sed 's/.*network=\([^ ]*\).*/\1/')
Unset, \${OMNIGRAPH_TOKEN} resolves to empty and memory silently does not work.
JSON
}

case "$mode" in
  online)
    echo "== online client: use the central server's main branch directly =="
    register_mcp "$URL" "$TOKEN"
    echo "Done. The agent reads/writes central main. No sync needed while online."
    ;;
  offline)
    echo "== offline-capable client: local stack + auto-sync =="
    echo "1) Bring up the local Omnigraph stack:"
    echo "   cd $here/.."
    echo "   cp .env.shared.example .env.shared   # OMNIGRAPH_TOKEN (openssl rand -hex 32) + S3_BUCKET"
    echo "   cp .env.client.example .env.client   # CODE_ROOT, OMNIGRAPH_URL"
    echo "   docker compose --env-file .env.shared --env-file .env.client -f docker-compose.client.yml --profile offline up -d"
    echo
    echo "2) Point the agent's omnigraph MCP at the LOCAL server:"
    register_mcp "http://localhost:8080" "\$(grep '^OMNIGRAPH_TOKEN=' $here/../.env.shared | cut -d= -f2-)"
    echo
    echo "3) Sync config:"
    # NEVER clobber an existing .env — it holds live tokens. Write a .env.example
    # instead and let the operator merge. (The old version here did `cat > .env`,
    # which silently destroyed a working config and replaced LOCAL_TOKEN with a
    # placeholder, breaking every subsequent sync.)
    if [ -f "$here/.env" ]; then
      echo "   $here/.env already exists — NOT overwriting it (it holds live tokens)."
      echo "   Compare it against $here/.env.example if you need the current keys."
      target="$here/.env.example"
    else
      target="$here/.env"
    fi
    cat > "$target" <<EOF
# Sync config for omnigraph-sync.sh / sync-windows.ps1. See SYNC-MANUAL.md.
CENTRAL_URL=$URL
CENTRAL_TOKEN=$TOKEN
# Local API as seen from THIS HOST:
LOCAL_URL=http://127.0.0.1:8080
# Local API as seen from INSIDE the CLI container (compose service name).
# 127.0.0.1 there is the container itself — the load would fail with 'Connection refused'.
LOCAL_URL_CONTAINER=http://omnigraph-server:8080
LOCAL_TOKEN=<your LOCAL server's OMNIGRAPH_TOKEN — see ../.env.shared>
# Leave GRAPHS unset to sync every graph central exposes (per-project isolation).
# GRAPHS=agent-skills,sibling-analysis-repo
DEVICE=$(hostname)
EOF
    echo "   wrote $target (fill LOCAL_TOKEN)."
    echo
    echo "4) Install the 5-minute sync timer:"
    echo "   mkdir -p ~/.config/systemd/user"
    echo "   cp $here/omnigraph-sync.service $here/omnigraph-sync.timer ~/.config/systemd/user/"
    echo "   systemctl --user enable --now omnigraph-sync.timer"
    echo "   (Windows: see SYNC-MANUAL.md for the Scheduled Task — sync-windows.ps1.)"
    echo
    echo "   Verify BEFORE arming: DRY_RUN=1 $here/omnigraph-sync.sh"
    echo
    echo "The agent works on local main; omnigraph-sync reconciles with central when online."
    ;;
  *) echo "unknown --mode: $mode (online|offline)" >&2; exit 2;;
esac
