# Architecture

This repository has three pillars. The first two are content (skills and their
per-repo adapters); the third is a self-hosted runtime that agents connect to.

```text
skills/     ── reusable agent skills (single source of truth)
starters/   ── thin per-repo adapters that point at skills
infra/      ── self-hosted runtime
              ├── mcp-servers/    MCP stack (navigation, graph, memory, workflows)
              ├── local-ai/       optional: Ollama + LiteLLM + Open WebUI + OpenHands
              └── remote-access/  run/persist/reach agents (Herdr)
```

`local-ai` is optional but not disconnected: its **Ollama** is the `nomic-embed-text`
provider the memory graph embeds against (see the arrow in the diagram below), and its
**LiteLLM** proxy is what lets `swarm-orchestration` route roles to different models
behind one OpenAI-compatible endpoint. Details: [`../infra/local-ai/README.md`](../infra/local-ai/README.md).

## MCP server stack (`infra/mcp-servers/`)

| Server | Transport | Role |
|---|---|---|
| Serena | stdio (`uvx`) | LSP semantic code navigation (symbols, refs, refactor) |
| Graphify | stdio (`uv`/Docker) | Auto-extracted **code-structure** graph |
| **Omnigraph** | stdio bridge (`@modernrelay/omnigraph-mcp`) → HTTP `:8080` | **Structured cross-project memory** (typed nodes; graph+vector+full-text) |
| Superpowers | stdio (`node`) | Disciplined workflow skills (TDD, debugging, planning) |
| Playwright | stdio (`npx`) | Browser automation |

Memory vs code graph: **Graphify** answers "how is the code structured" (it
auto-extracts from source); **Omnigraph** answers "what did we decide and why"
(agents write typed memory explicitly). They are complementary, not redundant.

```text
Agent
 ├── Serena ─────────► source code (LSP)
 ├── Graphify ───────► graphify-out/graph.json (code structure)
 ├── Omnigraph ──────► omnigraph-server :8080 ──► MinIO (S3) :9000/:9001
 │     (structured memory: Project/Decision/Rule/Preference/Convention/...)
 │       └── vector search ──► Ollama nomic-embed-text (768-dim, cloud.example.internal)
 ├── Superpowers ────► workflow skills
 └── Playwright ─────► browser

omnigraph-viewer :8090 ──► omnigraph-server (read-only web UI over the API)
```

### Per-project graph isolation

**Each repo's memory lives in its own graph, named after the repo** — projects are
never merged, so a bad write or `load --mode overwrite` in one cannot touch another.
The shared **`memory`** graph holds **only** global-scope `Preference`s.

```text
omnigraph-server
 ├── memory          ── global-scope Preferences ONLY (house style, TDD-default)
 ├── agent-skills          ─┐
 ├── sibling-analysis-repo  │  one graph per repo; all share cluster/memory.pg
 ├── finance-repo           │  each holds its Project node + satellites
 └── homelab-server        ─┘
```

A bridge is **pinned to one graph** via `OMNIGRAPH_GRAPH_ID` and no tool takes a graph
argument, so a repo's `.mcp.json` declares **one** `omnigraph` server, pointed at its own
graph. A project-scoped agent therefore cannot read `memory` — which is fine: `memory`
holds only two global `Preference`s (TDD-by-default, MCP-first navigation), already
Principles 2 and 6 of `skills/coding-principles/SKILL.md`.

| Task | Command |
|---|---|
| Add a graph | `scripts/add-project-graph.sh <name>` then `scripts/apply-cluster.sh` |
| Converge the declaration | `scripts/apply-cluster.sh` (snapshots + verifies node count) |
| Migrate off the shared graph | `scripts/split-project-graph.py <repo> --source memory --apply` |
| Prune the shared graph | `… --source memory --prune-source` (gated on a mirror check) |
| Refresh a seed from live | `… <repo> --write-seed` |

**A declaration is not live until `apply-cluster.sh` runs.** Verify against the
server (`graphs_list`, `schema_get`), never by reading `cluster.yaml` — an
unapplied schema rejects edge types *silently*, which is how five relational edges
went missing for weeks. Seeds load into the graph matching their file name.

Runtime = the **server** compose (`-f docker-compose.server.yml`; Omnigraph + MinIO
+ viewer). There is no fallback memory layer — the stack requires Omnigraph. See
`docs/decisions/0001-omnigraph-over-mem0.md`.

### Omnigraph internals

Omnigraph is **cluster-only boot**: a declared cluster (`infra/mcp-servers/cluster/`
— `cluster.yaml` + `memory.pg` schema + Cedar `*.policy.yaml` + stored `queries/`)
is `import`+`apply`ed into the MinIO storage root (the state ledger), then
`omnigraph-server --cluster s3://…` serves it. The API is bearer-token protected;
management/data actions require the applied policy bundle. Vector columns
(`Vector(768)` on `Decision`) are populated by supplying embeddings in load data
or the `omnigraph embed` pipeline; the `search_decisions($q)` stored query does
`nearest($d.embedding, $q)`.

## Deployed topology (this homelab)

The authoritative instance runs on **`coding.example.internal`**, spun up from the single
source of truth `Server/server/coding/mcp-servers/docker-compose.yml` (which
references this repo's `cluster/` config + viewer image). It is exposed through
the OPNsense `os-caddy` reverse proxy:

| Host | Backend | Auth |
|---|---|---|
| `omnigraph.example.org` | `coding.example.internal:8080` (API/MCP) | **bearer token only** — no Authelia (SSO would block programmatic clients) |
| `omnigraph-ui.example.org` | `coding.example.internal:8090` (viewer) | Authelia SSO (admin) |
| `omnigraph-minio.example.org` | `coding.example.internal:9001` (MinIO console) | Authelia SSO (admin) |

Clients: **online** = MCP → the public API on `main`; **offline-capable** = local
stack + `omnigraph-setup/omnigraph-sync.sh` timer that pushes to a `device/<host>` branch
and merges into `main` when reachable (`infra/mcp-servers/omnigraph-setup/`).

### Two stacks, one set of scripts — detected, not assumed

The **local** stack (this repo's `docker-compose.server.yml`) is compose project
`mcp-server` → network `mcp-server_mcp-net`. **Central** (`coding.example.internal`) is project
`mcp-servers` → network `mcp-servers_default`, viewer bound `0.0.0.0:8090` for Caddy.
Both keep MinIO on a **bind mount** (local `./data/minio`; central
`$APPS_ROOT/omnigraph/minio` = `/home/<you>/apps/omnigraph/minio`).

The helper scripts (`scripts/apply-cluster.sh`, `dedup-graph.py`,
`split-project-graph.py`) therefore **ask docker** what is running rather than assuming a
host — `scripts/_omni_env.py` derives the network from `docker inspect omnigraph-server`
and the MinIO mount + **its type** from `docker inspect omnigraph-minio`, falling back to
the local values when the stack is elsewhere. `OMNI_NET` / `--network` / `--net` /
`--minio-path` / `--minio-volume` still override. `add-project-graph.sh` only edits
`cluster.yaml`, so it is host-agnostic.

Why the mount *type* matters: `docker volume rm` against a bind mount is a silent no-op,
so a dedup rebuild would restart on a store it never wiped. Probe a host with
`python3 scripts/_omni_env.py`. Also mind `OMNIGRAPH_GRAPH_ID` (the MCP **bridge**'s
graph) vs `OMNIGRAPH_GRAPH` (the **viewer**'s). Detail:
[`../infra/mcp-servers/README.md`](../infra/mcp-servers/README.md) ("Central vs local").

## Remote access (`infra/remote-access/`)

- **Herdr** — agent multiplexer: run/persist multiple agents, reattach over SSH
  or phone, agent-to-agent socket API. Recommended over raw tmux
  (`docs/decisions/0002-herdr-multiplexer.md`).

## Data & secrets

- `graphify-out/` and `infra/mcp-servers/data/` (Omnigraph/MinIO) are
  generated and **untracked**.
- No personal absolute paths or secrets in tracked files. Paths come from
  `${AGENT_SKILLS_ROOT}` / `${CODE_ROOT}` / `${SERENA_HOME}` and `.env`
  (`.env.example` is the template).
