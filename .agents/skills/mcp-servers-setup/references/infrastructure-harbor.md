> Part of [mcp-servers-setup](../SKILL.md).

## Infrastructure

> ⚠️ **Dev vs server compose.** On coding.example.internal the Omnigraph stack is
> already deployed from `<site-repo>/mcp-servers/docker-compose.yml` (canonical,
> Dockhand, viewer bound to `0.0.0.0:8090` for Caddy). This repo's
> `infra/mcp-servers/docker-compose.server.yml` is the **local/dev** variant — it
> binds `127.0.0.1` and shares the project name `mcp-servers`, so running it on
> coding.example.internal clobbers the live viewer and takes
> `omnigraph-ui.example.org` down. On coding.example.internal, manage the stack from
> the canonical Server compose instead. Use the local/dev variant only for a
> standalone/dev host.

| Service | Address | Model(s) | Purpose |
|---------|---------|----------|---------|
| omnigraph-server | `:8080` | — | The memory graph (bearer-auth; `/healthz` is open) |
| MinIO | `:9000` API / `:9001` console | — | S3 store backing Omnigraph |
| omnigraph-viewer | `:8090` | — | Read-only web UI (put Authelia in front if exposed) |
| Ollama | `:11434` | `nomic-embed-text` (768-dim, CPU-fine) | Embeddings — **optional** |
| OLLAMA_KEEP_ALIVE=24h | Windows env | — | Keep models in VRAM |

Ollama is optional: without it, recall degrades to graph traversal + full-text rather
than failing. **No Qdrant, no Postgres, no LLM API key** — those were Mem0's, and Mem0
was removed entirely (ADR 0003). `bge-m3` (1024-dim) was Mem0's embedder and is *not*
this graph's: the graph is `Vector(768)`/`nomic-embed-text`, and mixing dimensions makes
`nearest()` return garbage.

### Harbor — Private Container Registry

**Convention: any container image an agent builds to share or deploy goes to
Harbor, never Docker Hub.** Harbor is the homelab registry (web UI, per-project
RBAC/robot accounts, Trivy scanning) at `harbor.example.org`, backed by the
NFS share on ollama.example.internal. Local-only throwaway builds (e.g. `docker compose --build`
for a stack that runs in place) do **not** need pushing — only images meant to be
pulled elsewhere.

```bash
# build locally, then publish to Harbor under a project namespace (create the
# project once in the UI: e.g. `agents`, `infra`)
docker build -t myimage:latest .
docker login harbor.example.org                       # admin / project user / robot token
docker tag  myimage:latest harbor.example.org/agents/myimage:latest
docker push harbor.example.org/agents/myimage:latest
# elsewhere: pull instead of rebuilding
docker pull harbor.example.org/agents/myimage:latest
```

Deployment / admin (installer, storage path, Caddy exposure, secrets) is the
single source of truth in the Server repo: `server/cloud/harbor/README.md`.

