# Omnigraph Local Runbook — verified setup + every fix

This is the **authoritative, reproducible** procedure for running the Omnigraph
structured-memory stack locally (server + MinIO + viewer), wiring the MCP bridge
into an agent, loading memory, and populating vector embeddings. Every step here
was verified end-to-end on Windows 11 (Docker Desktop, Git Bash) on 2026-07-09.

**If something breaks, read this first** — the non-obvious fixes are all captured
below so they never have to be re-derived. See also
[`../servers/omnigraph/README.md`](../servers/omnigraph/README.md) (server
internals) and [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## 1. Bring up the local stack

```bash
cd infra/mcp-servers
cp .env.shared.example .env.shared    # OMNIGRAPH_TOKEN (openssl rand -hex 32) + S3_BUCKET
cp .env.server.example .env.server    # MINIO creds, OLLAMA_HOST_IP, ports
docker compose --env-file .env.shared --env-file .env.server \
  --env-file ~/.config/autoos/site.env \
  -f docker-compose.server.yml up -d omnigraph-server omnigraph-viewer
```

Verify:

```bash
curl -fsS http://127.0.0.1:8080/healthz                    # {"status":"ok","version":"0.8.1",...}
set -a; . ./.env.shared; set +a
curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://127.0.0.1:8080/graphs
```

| Service | URL |
|---|---|
| Omnigraph API (bearer) | `http://127.0.0.1:8080` |
| Memory viewer (UI) | `http://127.0.0.1:8090` |
| MinIO console | `http://127.0.0.1:${MINIO_CONSOLE_PORT}` (default 9001) |

### Compose gotchas (already fixed in `docker-compose.server.yml`)

1. **MinIO host ports are configurable.** If `9000/9001` are taken on the host
   (e.g. a local MinIO/other process), set `MINIO_API_PORT` / `MINIO_CONSOLE_PORT`
   in `.env.server`. Omnigraph reaches MinIO over the **internal** Docker network
   (`minio:9000`), so the host publish is only for the console — a conflict never
   blocks the graph.
2. **`down -v` does NOT wipe data.** MinIO data is a **bind mount**
   (`./data/minio`), not a named volume. To truly reset the graph:
   `docker compose … down && rm -rf ./data/minio && … up`.

---

## 2. Register the MCP bridge (agent side)

The bridge package is **`@modernrelay/omnigraph-mcp`** (stdio). The env-var names
it *actually* requires (verified against v0.8.0 of the bridge) are **not** what
older docs said:

| Env var | Value | Notes |
|---|---|---|
| `OMNIGRAPH_BASE_URL` | `http://localhost:8080` | **not** `OMNIGRAPH_URL` |
| `OMNIGRAPH_TOKEN` | the bearer from `.env.shared` | |
| `OMNIGRAPH_GRAPH_ID` | `memory` | **required** — server 0.7.0+ is cluster-only |

**Launch it with `npx`** — with a healthy npm. This host's bundled npm had been
corrupted: `Class extends value #<Object> is not a constructor`, because the
nested `minipass` v3 under `minipass-pipeline` / `minipass-flush` was missing, so
they resolved the hoisted `minipass` v7 (whose export is an object, not a class).
Every `npx` fetch / `npm view` / `npm install` failed. **Fix — replace npm's
bundled package with a fresh registry tarball** (the nvm version dir is
admin-owned, so the swap needs elevation):

```bash
curl -fSL https://registry.npmjs.org/npm/-/npm-11.6.2.tgz -o npm.tgz && tar -xzf npm.tgz
# elevated:  Rename-Item …\nvm\<ver>\node_modules\npm npm.broken.bak
#            Move-Item   package …\nvm\<ver>\node_modules\npm
```

(Equivalently `nvm uninstall <ver> && nvm install <ver>`.) If npm can't be fixed,
`pnpm dlx @modernrelay/omnigraph-mcp` sidesteps npm entirely — same env vars.

Claude Code entry (in `~/.claude.json` → top-level `mcpServers`):

```json
"omnigraph": {
  "command": "npx",
  "args": ["-y", "@modernrelay/omnigraph-mcp"],
  "env": {
    "OMNIGRAPH_BASE_URL": "http://localhost:8080",
    "OMNIGRAPH_TOKEN": "<local bearer>",
    "OMNIGRAPH_GRAPH_ID": "memory"
  }
}
```

Restart the agent so it loads the server at session start.

### Handshake test (before restarting the agent)

```bash
cd infra/mcp-servers; set -a; . ./.env.shared; set +a
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | OMNIGRAPH_BASE_URL=http://localhost:8080 OMNIGRAPH_TOKEN="$OMNIGRAPH_TOKEN" \
    OMNIGRAPH_GRAPH_ID=memory npx -y @modernrelay/omnigraph-mcp
```

A healthy bridge returns a `serverInfo` block then a `tools/list`. **Actual tool
names** (v0.8.0) — note they differ from the schema/branches/queries/mutations/
ingest naming in older docs:

`health`, `snapshot`, `query` (GQ read), `mutate` (GQ write), `load` (NDJSON
bulk), `schema_get`, `snapshot`, `branches_list/create/delete/merge`,
`commits_list/get`, `graphs_list`.

Key usage norms surfaced by the bridge's own `instructions`: read
`schema_get` first; parameterize queries (never interpolate); `nearest`/`bm25`/
`rrf` need a trailing `limit N`; `load mode:"merge"` upserts by `@key`,
`"overwrite"` truncates the branch, `"append"` errors on collision.

---

## 3. Load memory data (seeds)

Seed files live in [`../cluster/seed/`](../cluster/seed/) as NDJSON
(`{"type":"NodeType","data":{…}}` and `{"edge":"EdgeType","from":"…","to":"…"}`).

**Windows load pattern — pipe via stdin, don't bind-mount.** Git Bash mangles
Windows paths in `docker run -v`; feeding the file on stdin avoids it entirely:

```bash
cd infra/mcp-servers; set -a; . ./.env.shared; set +a
docker run --rm -i --network mcp-server_mcp-net \
  -e OMNIGRAPH_BEARER_TOKEN="$OMNIGRAPH_TOKEN" \
  --entrypoint sh modernrelay/omnigraph-server:v0.8.1 -c \
  'cat > /tmp/d.jsonl; omnigraph load --server http://omnigraph-server:8080 --graph memory --data /tmp/d.jsonl --mode merge --yes --json' \
  < cluster/seed/sibling-analysis-repo.jsonl
```

- Run the CLI container **on the compose network** (`--network mcp-server_mcp-net`)
  and address the server as `http://omnigraph-server:8080` — reliable, no host
  loopback issues.
- The CLI reads the bearer from **`OMNIGRAPH_BEARER_TOKEN`** (not `OMNIGRAPH_TOKEN`).
- `--mode merge` is idempotent by slug; re-running is safe.

---

## 4. Vector embeddings via the LOCAL Ollama container

Only `Decision` nodes carry a vector (`embedding: Vector(768) @embed("rationale")`
in `cluster/memory.pg`); `search_decisions` ranks them with `nearest()`. Getting
stored vectors in is the fiddly part — here is the *only* reliable path found:

### 4a. Provider = the local Ollama container

```bash
docker exec ollama ollama pull nomic-embed-text     # 768-dim, matches Vector(768)
```

Point the graph's embedding provider at the local container by setting
**`OLLAMA_HOST_IP=host-gateway`** in `.env.server` (the compose maps
`${OLLAMA_HOST_NAME} -> ${OLLAMA_HOST_IP}` via `extra_hosts`; `host-gateway` routes to the
Docker host, where the `ollama` container publishes `:11434`). Recreate the
server so the new `extra_hosts` takes effect:

```bash
docker compose --env-file .env.shared --env-file .env.server \
  --env-file ~/.config/autoos/site.env \
  -f docker-compose.server.yml up -d omnigraph-server
```

Now **query-time** embedding (of the search string) hits the local container —
verify with `docker logs ollama --since 30s | grep /v1/embeddings` after a search.

### 4b. Populate STORED vectors — the hard-won facts

- **The server does NOT auto-embed on boot** (verified: a restart with missing
  vectors produced 0 embedding calls).
- **`load --mode merge` of vectors fails** with a Lance error
  (`all columns in a record batch must have the same length`) — a v0.8.1 quirk on
  hand-supplied vector ingest.
- **The `omnigraph embed` CLI `--spec` provider ignores its base-URL** and
  defaults to OpenRouter (401), so it can't target a local Ollama; `--server`
  and `--cluster` are rejected (`embed` is a local-store command), and `--store`
  still requires `--input`. So the CLI file-pipeline is a dead end for a custom
  local endpoint.
- **What works:** compute the vectors yourself against the local Ollama and load
  them with **`--mode overwrite`** (whole-graph replace). Overwrite ingests the
  vectors cleanly where merge does not.

Automated by [`../scripts/populate-embeddings.py`](../scripts/populate-embeddings.py):

```bash
cd infra/mcp-servers
python scripts/populate-embeddings.py \
  --seeds cluster/seed/sibling-analysis-repo.jsonl cluster/seed/finance-repo.jsonl \
  --ollama http://localhost:11434 --graph memory
```

It: (1) embeds every `Decision.rationale` via the local Ollama
(`/v1/embeddings`, `nomic-embed-text`), (2) writes a combined embedded NDJSON,
(3) `overwrite`-loads it into the running server. Then verify:

```bash
set -a; . ./.env.shared; set +a
curl -s -H "Authorization: Bearer $OMNIGRAPH_TOKEN" -H 'Content-Type: application/json' \
  -d '{"params":{"q":"how is pupil and paw behavior extracted from video"}}' \
  http://127.0.0.1:8080/graphs/memory/queries/search_decisions
```

`ba-video-classical-cv` should rank first. (The embedding spec format the CLI
*would* want, for reference: top-level `dimension`, a `provider`
`{kind,base_url,model,api_key}`, and `types: {"<Type>": {"target":"embedding",
"fields":["rationale"]}}` — but its provider can't reach a local endpoint, hence
the script above.)

---

## 5. Quick reference

```
Ports:   API 8080 · viewer 8090 · MinIO ${MINIO_API_PORT:-9000}/${MINIO_CONSOLE_PORT:-9001}
Graph:   memory   (cluster-only boot; schema in cluster/memory.pg)
Bearer:  OMNIGRAPH_TOKEN (.env.shared)   ·   CLI env name: OMNIGRAPH_BEARER_TOKEN
MCP:     npx -y @modernrelay/omnigraph-mcp   env: OMNIGRAPH_BASE_URL/TOKEN/GRAPH_ID (pnpm dlx if npm broken)
Embed:   local ollama nomic-embed-text (768d); OLLAMA_HOST_IP=host-gateway; overwrite-load
Wipe:    down && rm -rf ./data/minio && up      (down -v alone does NOT clear the bind mount)
Net:     run CLI containers with --network mcp-server_mcp-net, address omnigraph-server:8080
```
