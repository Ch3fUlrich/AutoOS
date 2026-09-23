# Omnigraph — Structured Memory Server

Omnigraph is the memory layer for this stack: a lakehouse-native
graph engine with combined graph-traversal + vector + full-text retrieval,
backed by an S3-compatible store (MinIO here). It is the source of truth for
cross-project, cross-agent memory. See
[`../../../../docs/decisions/0001-omnigraph-over-mem0.md`](../../../../docs/decisions/0001-omnigraph-over-mem0.md)
for the rationale, and the
[`structured-memory`](../../../../skills/structured-memory/SKILL.md) skill for the
usage protocol.

There is no source to build here — the server runs from the upstream image and
the MCP bridge runs via `npx`. This folder documents how they are wired into the
compose stack and the agent configs.

## Run the server (via the stack)

The `omnigraph-server` + `minio` + `minio-init` + `omnigraph-init` services are
defined in the **server** compose
[`../../docker-compose.server.yml`](../../docker-compose.server.yml):

```bash
cd infra/mcp-servers
cp .env.shared.example .env.shared    # OMNIGRAPH_TOKEN + S3_BUCKET
cp .env.server.example .env.server    # MINIO_ROOT_USER/PASSWORD, embeddings
docker compose --env-file .env.shared --env-file .env.server \
  --env-file ~/.config/autoos/site.env \
  -f docker-compose.server.yml up -d  # omnigraph-server :8080 + minio :9000/:9001 + viewer :8090
```

Verify:

```bash
curl -fsS http://localhost:8080/healthz     # server up (bypasses auth)
# authenticated call uses the bearer token:
curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://localhost:8080/graphs
```

Key facts (from Omnigraph `docs/user/deployment.md`, v0.8.1):

| Setting | Value |
|---|---|
| Image | `modernrelay/omnigraph-server:v0.8.1` (Docker Hub; the GHCR mirror is not public) |
| Bind | `OMNIGRAPH_BIND` (default `0.0.0.0:8080`) |
| Cluster source | `OMNIGRAPH_CLUSTER` — here `s3://$S3_BUCKET/cluster` |
| Auth | `OMNIGRAPH_SERVER_BEARER_TOKEN` (implicit `default` actor) |
| Storage | `AWS_*` contract → MinIO (`AWS_ENDPOINT_URL_S3=http://minio:9000`, `AWS_ALLOW_HTTP=true`, `AWS_S3_FORCE_PATH_STYLE=true`) |
| Health | `GET /healthz` (unauthenticated) |

## Cluster bootstrap & policy (important)

Omnigraph is **cluster-only boot**: the server serves a storage root only after a
cluster has been converged into it. The declared config lives in git at
[`../../cluster/`](../../cluster/) (`cluster.yaml` + `memory.pg`); the state
ledger lives in MinIO (the Terraform split). The compose stack does this
automatically via the `omnigraph-init` one-shot:

```bash
omnigraph cluster import --config /cluster --yes   # bootstrap state.json
omnigraph cluster apply  --config /cluster --yes   # create graphs + schema, write ledger
# then omnigraph-server --cluster s3://omnigraph/cluster serves it
```

`/healthz` is open; **all server-scoped/management and data-plane actions are
closed by default (HTTP 403) until an explicit policy bundle is applied** —
"the management surface is never exposed without operator opt-in". Applying that
policy bundle (granting the `default` actor access to the `memory` graph) and
expanding `memory.pg` to the full structured-memory schema is the next step; see
`skills/structured-memory/references/schema.md`. Until then the graph exists and
is reachable by direct store access
(`--store s3://omnigraph/cluster/graphs/memory.omni`) but not through the server
API.

## Embeddings (vector search)

The `memory` graph's embedding provider is **Ollama `nomic-embed-text`**
(openai-compatible, 768-dim — matches `Vector(768)` in `memory.pg`), reached as
`ollama.example.internal` via the compose `extra_hosts` entry. Configured in
`cluster/cluster.yaml`; the server resolves it at boot (`OLLAMA_DUMMY_KEY`).
For a **fully self-contained local stack**, run a local Ollama container with
`nomic-embed-text` pulled and set `OLLAMA_HOST_IP=host-gateway` in `.env.server`
(routes `ollama.example.internal` to the Docker host). Benchmarked CPU-only: ~360 ms cold,
~60 ms warm — comfortably within the 16-CPU container.

- The server calls the provider at **query time** for `nearest($v, "text")`
  auto-embedding, so vector search works even though boot doesn't require the
  endpoint (non-vector queries always work if `ollama.example.internal` is down).
- **Stored vectors are populated by supplying them in load data**, but on v0.8.1
  `load --mode merge` of hand-supplied vectors hits a Lance batch error and the
  `omnigraph embed` CLI can't target a local endpoint — so the verified path is
  to compute embeddings against the (local) Ollama and **`load --mode overwrite`**.
  Automated by [`../../scripts/populate-embeddings.py`](../../scripts/populate-embeddings.py)
  (detail in [`../../docs/OMNIGRAPH-LOCAL-RUNBOOK.md`](../../docs/OMNIGRAPH-LOCAL-RUNBOOK.md) §4).
  After populating, `search_decisions("why did we replace the memory system")`
  ranks the *omnigraph-over-mem0* decision first.
- To fall back to zero-dependency embeddings, set the provider `kind: mock` in
  `cluster.yaml` and re-apply.

Review embeddings / semantic search:
```bash
# via the stored query (server-side, uses the configured embedder)
curl -s -X POST -H "Authorization: Bearer $OMNIGRAPH_TOKEN" -H 'Content-Type: application/json' \
  -d '{"params":{"q":"how do we run agents remotely"}}' \
  http://localhost:8080/graphs/memory/queries/search_decisions
```

## Register the MCP bridge

[`@modernrelay/omnigraph-mcp`](https://www.npmjs.com/package/@modernrelay/omnigraph-mcp)
runs over stdio and exposes the graph's tools (`query`, `mutate`, `load`,
`schema_get`, `branches_*`, `snapshot`, `commits_*`) + resources. Required env:

| Var | Value |
|---|---|
| `OMNIGRAPH_BASE_URL` | the server URL (e.g. `http://omnigraph-server:8080`, `http://localhost:8080`, or the public URL) |
| `OMNIGRAPH_GRAPH_ID` | **the repo's own graph** (`<repo-folder-name>`) — required (server is cluster-only; the bridge refuses to start without it) |
| `OMNIGRAPH_TOKEN` | the bearer token |

> **One bridge = one graph — and one bridge is all you declare.** `OMNIGRAPH_GRAPH_ID`
> pins it, and no tool takes a graph argument. Under per-project isolation each repo
> points `omnigraph` at **its own** graph; `memory` is *not* the right value for a
> project any more. A project-scoped agent therefore cannot read `memory`, which is
> deliberate: it holds only two global `Preference`s (TDD-by-default, MCP-first
> navigation), already Principles 2 and 6 of `skills/coding-principles/SKILL.md`.
> Don't confuse `OMNIGRAPH_GRAPH_ID` with the **viewer's** `OMNIGRAPH_GRAPH`.

**Hosts with Node** can run it directly (see `config/mcp.json`, `config/mcp_antigravity.json`):

```json
"omnigraph": { "command": "npx", "args": ["-y", "@modernrelay/omnigraph-mcp"],
  "env": { "OMNIGRAPH_BASE_URL": "http://localhost:8080", "OMNIGRAPH_GRAPH_ID": "<repo-folder-name>", "OMNIGRAPH_TOKEN": "${OMNIGRAPH_TOKEN}" } }
```

**Prefer the docker form** where node may be absent — `coding.example.internal` has no node/npx, so an
`npx` bridge cannot start there. See this repo's own [`../../../../.mcp.json`](../../../../.mcp.json).

**Hosts without Node** (e.g. this coding VM) run the bridge in a container — build
[`../omnigraph-mcp/`](../omnigraph-mcp/) (`docker build -t omnigraph-mcp:latest
servers/omnigraph-mcp`) and use the docker-run form in `config/mcp-claude-code.json`.
For Claude Code, register it directly:

```bash
# --scope project so the graph id travels with the repo (network: `docker network ls`)
claude mcp add --scope project omnigraph -- \
  docker run -i --rm --network mcp-server_mcp-net \
  -e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080 \
  -e OMNIGRAPH_GRAPH_ID=<repo-folder-name> -e OMNIGRAPH_TOKEN=<bearer> omnigraph-mcp:latest
claude mcp get omnigraph      # -> Status: Connected
```

**Full procedure, handshake test, and tool-usage norms:**
[`../../docs/OMNIGRAPH-LOCAL-RUNBOOK.md`](../../docs/OMNIGRAPH-LOCAL-RUNBOOK.md).
