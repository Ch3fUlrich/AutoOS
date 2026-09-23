# Agent Skills

Reusable AI-agent skills, per-repo starter adapters, and a self-hosted MCP runtime for coding
agents — so useful workflows are reused across projects instead of rediscovered in every new
repository.

**Agents: start at [`skills/repository-index/SKILL.md`](skills/repository-index/SKILL.md).** It is
the router, and it is the one file to read before deciding how to do anything else. Everything
below is orientation for humans.

```text
skills/     # Pillar 1 — reusable skills (each skill's SKILL.md is the source of truth)
starters/   # Pillar 2 — thin per-repo adapters that point at a skill
infra/      # Pillar 3 — self-hosted runtime (MCP stack, local AI, remote access)
docs/       # architecture, agent compatibility, ADRs, design specs
prompts/    # standalone prompt templates (repo→spec, big-todo workflow, deploys, examples)
webpage/    # HTML working documents produced by `html-working-documents`
```

[`docs/architecture.md`](docs/architecture.md) covers how the pieces fit;
[`CHANGELOG.md`](CHANGELOG.md) covers what changed and why.

## Pillar 1 — Skills

### The router comes first

A skill an agent does not know about is a skill it will not load — and nothing announces the
omission. The work simply comes out worse: conventions re-derived that were already written down,
whole files read where a symbol lookup would do, and everything learned in the session lost at the
end. The router exists so that never depends on an agent happening to browse `skills/`.

It answers three questions in one place:

| The router tells you | So an agent can |
|---|---|
| **Which MCP server answers which question** — omnigraph for memory, serena for code, graphify for blast radius, context7 for library docs, and what each *isn't* for | reach for the right tool instead of `Read`-ing whole files — Principle 6 of `coding-principles`, and where most of the token savings come from |
| **Which skills are always-on vs. triggered** — `coding-principles` and `structured-memory` apply to *any* task; the review swarm only when its trigger fires | stop treating the baseline as an escalation, and stop invoking heavy machinery for a one-line fix |
| **The routing decision tree** — session start (recall → activate → baseline), the task, then session end (verify → **persist**) | close the memory loop it opened, rather than letting recall shrink over time |

Triggers are **observable** ("diff touches > 3 files", "the answer would exceed ~100 lines of
markdown"), not vibes like "any complex task" — an agent can evaluate them without a judgement call.
Adding a skill without a router row means nobody routes to it, so
[`CONTRIBUTING.md`](CONTRIBUTING.md) makes the row part of adding the skill.

[`skills/SYNC.md`](skills/SYNC.md) is **not** a router — it is the vendoring ledger (upstream,
licence, sync date) for borrowed skills.

### Always-on — load for almost any task

| Skill | Purpose |
|---|---|
| [`repository-index`](skills/repository-index/SKILL.md) | **The router — read first.** Maps every MCP server and skill to the trigger that loads it. |
| [`coding-principles`](skills/coding-principles/SKILL.md) | Engineering baseline: DRY, TDD, single responsibility, document-the-why, changelog/ADR backtracking, MCP-first navigation, scratch-script hygiene. |
| [`structured-memory`](skills/structured-memory/SKILL.md) | Typed memory on Omnigraph — recall at session start, persist durable decisions at end. Read its `references/operations.md` before any query, mutation, or load. |

### Triggered by task shape

| Skill | Trigger |
|---|---|
| [`mcp-servers-setup`](skills/mcp-servers-setup/SKILL.md) | Wiring or debugging the stack; new machine; a server misbehaving. |
| [`html-working-documents`](skills/html-working-documents/SKILL.md) | The answer would exceed ~100 lines of markdown, or needs a diagram, table, or interactive view. |
| [`homelab-access`](skills/homelab-access/SKILL.md) | **Before** any SSH, `DOCKER_HOST=ssh://`, firewall, or NAS command. Cheaper than rediscovering that `ping` is blocked or that OPNsense runs `csh`. |

### Orchestration and review

| Skill | Source | Licence | Role |
|---|---|---|---|
| [`swarm-orchestration`](skills/swarm-orchestration/SKILL.md) | Internal | — | The pipeline: Architect / Engineer / Reviewer, wave caps, model economics, handoffs, checkpointing. |
| [`pr-approval-agent`](skills/pr-approval-agent/SKILL.md) | PostHog / StampHog | MIT | Deterministic deny-list and size-ceiling gates, run *before* any LLM review. |
| [`qa-swarm`](skills/qa-swarm/SKILL.md) | Paul D'Ambra | MIT | Parallel lens review (security, performance, XP) and convergence scoring. |
| [`review-triage`](skills/review-triage/SKILL.md) | Paul D'Ambra | MIT | Triage scoring, the Human Participation Gate, dual-mode narration. |
| [`no-mistakes`](skills/no-mistakes/SKILL.md) | Kun Chen | MIT | Local pre-push validation proxy loop. |
| [`babysit-prs`](skills/babysit-prs/SKILL.md) | Phil Haack | MIT | Async state tracking for CI runs and retry loops. |
| [`herdr-orchestration`](skills/herdr-orchestration/SKILL.md) | Internal | — | Driving Herdr's socket API. **Only** when work must outlive the session, a human supervises, a non-Claude agent is needed, or you wait on a long-lived process — see its §1. |
| [`unattended-orchestration`](skills/unattended-orchestration/SKILL.md) | Internal | — | **Two lanes.** *Batch*: multi-hour and overnight runs with **nobody watching**: agent sessions in isolated git worktrees, parallel lanes, automatic recovery from usage limits and API outages, guard-gated auto-merge. **Portable by design** — copy the folder into any git repo and run `-Init`; the repo is auto-detected and the agent is a swappable adapter. |

Orchestration policy is Markdown; every threshold, weight, model tier, and matrix lives in
`skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml`. If a number
appears in a `SKILL.md`, that is a bug. The reasoning is in
[ADR 0004](docs/decisions/0004-executable-orchestration-policy.md), and the pending work to make the
policy *enforceable* on Claude Code's native surface is specified in
[the design spec](docs/superpowers/specs/2026-07-30-orchestration-context-and-economics-design.md).

Each skill is self-contained: a required `SKILL.md`, optional `agents/openai.yaml` (a Codex
manifest), `references/`, `scripts/`, `assets/`. Keep reusable detail in `references/` and keep
`SKILL.md` focused on when to use the skill and how to run it.

## Pillar 2 — Starters

Thin adapters to drop a skill into another repository. Add a short pointer to the project's agent
instruction file (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, …):

```markdown
For any implementation, refactor, or bugfix, follow the skill at
`skills/coding-principles/SKILL.md`.
```

Available: [`coding-principles`](starters/coding-principles/), [`mcp-servers`](starters/mcp-servers/),
[`html-working-documents`](starters/html-working-documents/). Starters are pointers, never copies of
the workflow — see [`docs/agent-compatibility.md`](docs/agent-compatibility.md) for the native
instruction file each agent expects.

## Pillar 3 — Infrastructure (`infra/`)

A self-hosted stack that cuts token usage on code-heavy tasks and gives agents persistent,
structured memory. Runs on your own hardware; **the memory path needs no Postgres, no pgvector, and
no LLM API key.**

### MCP server stack (`infra/mcp-servers/`)

| Server | Role | Always on? |
|---|---|---|
| **[Omnigraph](https://github.com/ModernRelay/omnigraph)** | Structured cross-project memory + MinIO object store | yes — the memory layer |
| [Serena](https://github.com/oraios/serena) | LSP semantic code navigation and editing | yes |
| [Graphify](https://github.com/safishamsi/graphify) | Auto-extracted code-structure graph, for cross-module blast radius | yes |
| [Superpowers](https://github.com/erophames/superpowers-mcp) | Disciplined workflow skills | yes |
| [Context7](https://context7.com/) | Up-to-date library and framework docs | yes |
| [Omnigraph viewer](infra/mcp-servers/servers/omnigraph-viewer/) | Read-only web UI for the memory graph | server role |
| [Playwright](https://github.com/microsoft/playwright-mcp) | Browser automation, strictly for UI validation | on demand |
| Sentry · Datadog | Observability. Default and conditional respectively | **`observability` Compose profile only** — not started by default, and their payloads are untrusted input |

### Setup: two roles, pick yours

Everything lives in [`infra/mcp-servers/`](infra/mcp-servers/): **two** Compose files and **three**
env files. Always load `.env.shared` plus the one for your role.

| Role | Compose file | What it runs | Who runs it |
|---|---|---|---|
| **SERVER** | `docker-compose.server.yml` | `omnigraph-server` + `minio` + `omnigraph-viewer` | **one** always-on machine (e.g. a homelab VM) |
| **CLIENT** | `docker-compose.client.yml` | `serena`, the `graphify` image, optional offline-local memory | **every** developer machine |

`.env.shared` holds the two values that connect the roles — `OMNIGRAPH_TOKEN` and `S3_BUCKET`.
Prerequisite: Docker. All commands run from `infra/mcp-servers/`.

```bash
cd infra/mcp-servers
cp .env.shared.example .env.shared     # OMNIGRAPH_TOKEN (openssl rand -hex 32) + S3_BUCKET
cp .env.server.example .env.server     # SERVER only: MinIO creds, embeddings
cp .env.client.example .env.client     # CLIENT only: CODE_ROOT, OMNIGRAPH_URL
```

**Server** — start the memory backend:

```bash
docker compose --env-file .env.shared --env-file .env.server -f docker-compose.server.yml up -d
curl -fsS http://localhost:8080/healthz          # expect {"status":"ok",...}
```

**Client** — start Serena, build the Graphify image, then point your agent's `omnigraph` MCP at the
server (`OMNIGRAPH_URL` in `.env.client`):

```bash
docker compose --env-file .env.shared --env-file .env.client -f docker-compose.client.yml up -d
docker compose --env-file .env.shared --env-file .env.client -f docker-compose.client.yml --profile build build graphify
```

Optional profiles: `--profile offline` runs a local Omnigraph the sync timer reconciles later;
`--profile observability` starts the Sentry and Datadog MCPs.

### Memory model — the part that bites

Agents write typed `Decision / Rule / Preference / Convention / Component / Task` nodes rather than
unstructured blobs — see [`structured-memory`](skills/structured-memory/SKILL.md). Semantic recall
uses a local **Ollama `nomic-embed-text`** embedder (768-dim, no cloud key) and is *optional*:
without it, recall degrades to graph traversal and full-text rather than failing.

**There is no fallback memory layer.** Omnigraph is it — adopted in
[ADR 0001](docs/decisions/0001-omnigraph-over-mem0.md), and the Mem0 escape hatch was removed
entirely in [ADR 0003](docs/decisions/0003-remove-mem0-fallback.md) because an unexercised fallback
with a different data model is not a fallback. Insurance is backups
(`cluster/seed/*.jsonl`, `.graph-backup/` exports, MinIO), not a parallel product.

> **Per-project graph isolation.** Every repo gets its **own** graph named after the repo folder,
> pinned by `OMNIGRAPH_GRAPH_ID` in a project-scoped `.mcp.json` — **one** bridge, no more. The
> shared **`memory`** graph holds **only** global-scope `Preference`s, and project data must never
> be written there. A bridge serves exactly one graph and no tool takes a graph argument, so
> reading `memory` would need a second server; that was tried and removed on 2026-07-17, because
> `memory` holds just two globals (TDD-by-default, MCP-first navigation) already stated as
> Principles 2 and 6 of [`coding-principles`](skills/coding-principles/SKILL.md).
>
> Keep the bearer out of tracked files: use `"OMNIGRAPH_TOKEN": "${OMNIGRAPH_TOKEN}"` and export it,
> along with `OMNIGRAPH_NET` (the docker network differs per host). See this repo's own
> [`.mcp.json`](.mcp.json).
>
> **A declared graph is not live** until `infra/mcp-servers/scripts/apply-cluster.sh` runs — verify
> with `graphs_list` / `schema_get`, never by reading the config. An unapplied cluster rejects edge
> types *silently*.
>
> **Wiring all of that up is one command.** It builds the bridge image, sets both env vars, removes
> any user-scope override, audits every repo's `.mcp.json`, and proves it by driving the real bridge:
>
> ```powershell
> cd infra/mcp-servers/omnigraph-setup
> .\setup-agent-memory.ps1 -Check    # diagnose  (./setup-agent-memory.sh --check)
> .\setup-agent-memory.ps1           # fix
> ```
>
> **If a graph ever looks empty, suspect config before data.** A same-named `omnigraph` in
> `~/.claude.json` (user scope) silently outranks a repo's `.mcp.json`: on 2026-07-17 one pinned to
> `memory` made every repo read the wrong graph, and an agent nearly rebuilt an intact 135-node
> graph on top of it. `0 rows except 2 Preferences` **is** the `memory` graph.

**Client, server, and sync.** The server owns the authoritative `main`; online clients point their
MCP straight at it, and offline-capable clients run a local copy that a timer reconciles when the
network returns. Agents never manage branches themselves. See
[`infra/mcp-servers/omnigraph-setup/`](infra/mcp-servers/omnigraph-setup/).

**Deployed instance (this homelab).** Runs on `coding.example.internal`, exposed through the OPNsense/Caddy
reverse proxy: `omnigraph.example.org` (API, bearer), `omnigraph-ui.example.org` (viewer,
Authelia), `omnigraph-minio.example.org` (MinIO console, Authelia).

### Local AI stack (`infra/local-ai/`) — optional, but it powers memory search

Self-hosted inference, UI, and agents. Optional with one exception: **its Ollama is the embedder
Omnigraph uses.** Without it nothing breaks — recall just degrades — but with it, "why did we
replace the memory layer?" finds the right `Decision` without you knowing its slug. CPU-fine
(~360 ms cold, ~60 ms warm), no cloud key.

| Component | Why it earns its place |
|---|---|
| **Ollama** (`:11434`) | Serves `nomic-embed-text` → Omnigraph's `Vector(768)` search. Also runs chat/coding models locally — no key, no egress, offline-capable. |
| **LiteLLM** (`:4000`) | One OpenAI-compatible endpoint in front of many providers. Centralises keys and lets `swarm-orchestration` route roles to different models without per-tool config. |
| **OpenHands** (`:3000`) | Browser-based SWE agent in a sandboxed runtime. Treated as a *provider adapter*, not a replacement orchestrator — [ADR 0005](docs/decisions/0005-openhands-as-provider-adapter.md). |
| **Open WebUI** (`:3131`) | Chat frontend over Ollama and LiteLLM, for exploration that doesn't warrant a coding agent. |
| **ollama-agent** | Sandboxed sibling sharing the model dir — run a model in isolation without touching the serving instance. |

Setup: [`infra/local-ai/README.md`](infra/local-ai/README.md).

### Remote access and multi-agent (`infra/remote-access/`)

- **[Herdr](infra/remote-access/herdr/)** — the agent multiplexer: persist multiple agents, reattach
  over SSH or phone, agent-to-agent socket API. Supersedes raw tmux
  ([ADR 0002](docs/decisions/0002-herdr-multiplexer.md)). Use it only for the four things native
  tooling cannot do — see [`herdr-orchestration`](skills/herdr-orchestration/SKILL.md) §1.

Antigravity's chat is reachable from a phone through the **app's own remote connection**; the former
`antigravity-remote-ui` scrape-and-tunnel workaround was removed on 2026-08-25.

See [`infra/remote-access/README.md`](infra/remote-access/README.md).

### Container registry

Built images (custom MCP servers, agent environments) are pushed to a **remote self-hosted Harbor
instance** and pulled wherever needed across the infrastructure. Harbor is **not** part of this
repository and must **never** be installed on a developer machine.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for conventions on adding skills and starters. Decisions are
recorded as ADRs under [`docs/decisions/`](docs/decisions/), designs as specs under
[`docs/superpowers/specs/`](docs/superpowers/specs/), and notable changes in `CHANGELOG.md`.
