# MCP Server Stack — Self-Hosted

A combined MCP server stack for your coding agent (CodeWhale, Claude Code,
Antigravity, …): semantic code navigation, disciplined coding workflows, and
**structured cross-project memory**. It cuts token use on code-heavy tasks by
returning symbols instead of whole files and by recalling prior decisions instead of
re-deriving them — by how much is **unmeasured**; the "40–60%" figure previously
quoted here had no measurement behind it and its supporting doc was retired.

Runs on your own hardware with Docker + uv + Node.js. The memory layer is
**Omnigraph** (typed graph + vector + full-text) backed by MinIO — no OpenAI key
required. There is **no fallback memory**: the stack requires Omnigraph (Mem0 was removed —
[ADR 0003](../../docs/decisions/0003-remove-mem0-fallback.md); [ADR 0001](../../docs/decisions/0001-omnigraph-over-mem0.md)
is why Omnigraph in the first place).
For the authoritative overview
see [`../../docs/architecture.md`](../../docs/architecture.md); for the memory
protocol see [`../../skills/structured-memory/SKILL.md`](../../skills/structured-memory/SKILL.md).

## Quick Start

There are **two composes** with **connected env files**: a `server` (the always-on
memory backend) and a `client` (a developer machine's MCP tools). `.env.shared`
carries the values that link them (`OMNIGRAPH_TOKEN`, `S3_BUCKET`).

```bash
cd ${AGENT_SKILLS_ROOT}/infra/mcp-servers
cp .env.shared.example .env.shared    # OMNIGRAPH_TOKEN + S3_BUCKET (both roles)
cp .env.server.example .env.server    # MinIO creds, embeddings
cp .env.client.example .env.client    # CODE_ROOT, OMNIGRAPH_URL
cp cluster/cluster.yaml.example cluster/cluster.yaml   # graph + policy declaration
mkdir -p ~/.config/autoos && cp -n site.env.example ~/.config/autoos/site.env  # your hosts

# SERVER — omnigraph-server + minio + minio-init + omnigraph-init + viewer
docker compose --env-file .env.shared --env-file .env.server \
  --env-file ~/.config/autoos/site.env \
  -f docker-compose.server.yml up -d
curl -fsS http://localhost:8080/healthz

# CLIENT — serena (SSE code-nav). Build the stdio graphify image:
docker compose --env-file .env.shared --env-file .env.client \
  -f docker-compose.client.yml up -d
docker compose --env-file .env.shared --env-file .env.client \
  -f docker-compose.client.yml --profile build build graphify
#   Offline (local memory the sync timer reconciles):  … --profile offline up -d

# one-time indexing + register MCP servers into your agent's config (config/*.json):
./scripts/linux/init-serena-projects.sh     # (or scripts/windows/*.ps1)
./scripts/linux/init-graphify-projects.sh
```

### Site values (`site.env`)

Private hosts, IPs and site paths are never in this tree: tracked files carry placeholders
(`omnigraph.example.org`, `coding.example.internal`, `ollama.example.internal`, `192.0.2.x`).
Your real values live in the user-scope `~/.config/autoos/site.env`, copied from
[`site.env.example`](site.env.example), which lists every variable. Lookup order, first hit
wins: **the environment**, then **`~/.config/autoos/site.env`**, then **the compose/script
defaults**. Compose receives the file through the extra `--env-file ~/.config/autoos/site.env`
shown above; `docker-compose.server.yml` refuses to render without `OLLAMA_HOST_NAME`.

Then wire up the two halves of memory — **they are different things and both scripts are
idempotent, so run them on any new machine and after any stack change**:

```powershell
cd omnigraph-setup
.\setup-agent-memory.ps1     # the MCP BRIDGE your agent reads memory through
.\setup-sync.ps1             # the TIMER that reconciles local <-> central (every 5 min)
```
```bash
cd omnigraph-setup
./setup-agent-memory.sh      # same, Linux/macOS/WSL
./setup-sync.sh
```

`setup-agent-memory` is the one to reach for **whenever memory looks broken or a graph looks
empty**: it builds the `omnigraph-mcp` image (published to no registry, so it must be built
per host), sets `OMNIGRAPH_TOKEN`/`OMNIGRAPH_NET` from `.env.shared` and `docker inspect`,
removes any user-scope `omnigraph` in `~/.claude.json` that would silently point every repo
at the wrong graph, audits each repo's `.mcp.json`, and verifies by driving the real bridge.
`--check`/`-Check` diagnoses without changing anything and exits non-zero on any problem.

See the **[local runbook](docs/OMNIGRAPH-LOCAL-RUNBOOK.md)** (verified setup +
every fix: MCP env vars, `pnpm dlx`, embeddings, compose gotchas),
[`servers/omnigraph/README.md`](servers/omnigraph/README.md),
[`omnigraph-setup/`](omnigraph-setup/) (client/server + offline sync),
[`omnigraph-setup/SYNC-MANUAL.md`](omnigraph-setup/SYNC-MANUAL.md) (the operator manual) and
[docs/INSTALL-GUIDE.md](docs/INSTALL-GUIDE.md).

## Active Servers

| MCP Server | Transport | Purpose | Status |
|-----------|-----------|---------|:------:|
| [Serena](https://github.com/oraios/serena) | stdio (`uvx`) | LSP semantic code navigation | Default |
| [Graphify](https://github.com/safishamsi/graphify) | stdio (`uv` or Docker) | Queryable **code-structure** graph | Default |
| **[Omnigraph](https://github.com/ModernRelay/omnigraph)** | stdio bridge → HTTP `:8080` | **Structured cross-project memory** (typed nodes) | Default |
| [Superpowers](https://github.com/erophames/superpowers-mcp) | stdio (`node`) | Disciplined workflow skills — TDD, debugging, planning | Default |
| [Playwright](https://github.com/microsoft/playwright-mcp) | stdio (`npx`) | Full browser automation | Default |
| [Context7](https://context7.com/) | stdio (`npx`) | Advanced contextual retrieval and analysis for agents | Default |
| Sentry | stdio (`npx` / Docker) | Runtime error debugging and early error detection ([Setup Guide](docs/OBSERVABILITY-MCP-SETUP.md)) | Default (Observability) |
| Datadog | stdio (`npx` / Docker) | Cross-service context for distributed setups ([Setup Guide](docs/OBSERVABILITY-MCP-SETUP.md)) | Conditional (Observability) |
| [Omnigraph viewer](servers/omnigraph-viewer/) | HTTP `:8090` | Read-only web UI for the memory graph (tabs, interactive graph, table, search) | Default |

> **Note on Observability MCPs**: Sentry and Datadog require specific token scoping and security configurations. See the **[Observability MCP Setup Guide](docs/OBSERVABILITY-MCP-SETUP.md)** before enabling them.

**Vector search** uses a local **Ollama `nomic-embed-text`** embedder (768-dim,
no cloud key), configured in [`cluster/cluster.yaml.example`](cluster/cluster.yaml.example)
(copy it to `cluster/cluster.yaml`, which is gitignored — graph names are repo names,
and this repo is public); the
`search_decisions($q)` stored query runs `nearest()` over Decision embeddings.

**Clients & offline sync** — online clients point their MCP at the server on
`main`; offline-capable clients run a local copy + a sync timer that reconciles
via a `device/<host>` branch. See [`omnigraph-setup/`](omnigraph-setup/) (`client-setup.sh`,
`omnigraph-sync.sh`).

**Deployed instance (this homelab).** Runs on `coding.example.internal` from the single-source
`<site-repo>/mcp-servers/docker-compose.yml`, exposed via OPNsense/Caddy:
`omnigraph.example.org` (API, bearer), `omnigraph-ui.example.org` (viewer,
Authelia), `omnigraph-minio.example.org` (MinIO console, Authelia). See
[`../../docs/architecture.md`](../../docs/architecture.md).

## Central vs local: the scripts auto-detect the stack

Two deployments exist and their docker wiring differs. **Local** = this repo's
`docker-compose.server.yml` (compose project `mcp-server` → network `mcp-server_mcp-net`).
**Central** (`coding.example.internal`) boots from `<site-repo>/mcp-servers/docker-compose.yml`
(compose project `mcp-servers` → network `mcp-servers_default`, Dockhand-managed).

The helper scripts no longer assume either. They ask docker what is actually running
(`scripts/_omni_env.py`, and the same `docker inspect` inline in `apply-cluster.sh`):

| Concern | How it is resolved | Explicit override |
|---|---|---|
| Docker network | `docker inspect omnigraph-server` → the network it is attached to; falls back to `mcp-server_mcp-net` when the stack is not on this host | `OMNI_NET=…` · `--network …` (dedup) · `--net …` (split) |
| MinIO store (dedup reset only) | `docker inspect omnigraph-minio` → the mount backing `/data`, **and its type**: a bind mount is cleared with `rm -rf` in a container, a named volume with `docker volume rm` | `--minio-path <dir>` or `--minio-volume <name>` |
| S3 endpoint | `http://omnigraph-minio:9000` — identical on both | `OMNI_S3=…` |
| Token | `$OMNIGRAPH_TOKEN`, else `--token-file` (defaults to central's `.env`), else this repo's `.env.shared` | `export OMNIGRAPH_TOKEN=…` |
| Boot compose | manage central via **its own** compose; this repo's `docker-compose.server.yml` is the local/dev variant (viewer `127.0.0.1:8090` vs central's `0.0.0.0:8090` for Caddy) | `--compose-file …` (dedup) |

`add-project-graph.sh` only rewrites `cluster.yaml`, so it has no host-specific default.

**Both stacks put MinIO on a bind mount** — local at `./data/minio`, central at
`$APPS_ROOT/omnigraph/minio` (e.g. `~/apps/omnigraph/minio`). A named volume is therefore
the *unusual* case. This matters because `docker volume rm` against a bind mount is a
**silent no-op**: dedup would restart on a store it never wiped. Detection picks the right
mechanism; if you force `--minio-volume` at a volume that does not exist, dedup now aborts
rather than treating "absent" as "removed".

**Variable-name trap:** `OMNIGRAPH_GRAPH_ID` pins the graph for the **MCP bridge**;
`OMNIGRAPH_GRAPH` is the **viewer** app's variable. They are different — don't swap them.

Check what a host resolves to before running anything destructive:

```bash
python3 scripts/_omni_env.py     # -> network=… bind=…/volume=…
```

Detection reads the **live** stack, which is the point: a declaration is not reality
(see [`../../docs/architecture.md`](../../docs/architecture.md)).

## Container Registry (Harbor)

To store and share Docker images (like `graphify-mcp` or other custom agents), we use **Harbor**, an open-source trusted cloud native registry. 
*   **Do not install Harbor locally.** It should be hosted centrally on your remote cloud server (e.g. `coding.example.internal` or a dedicated infrastructure VM).
*   Use it to push images after a local build, and pull them for remote deployments or across different agent workstations.

## Graphify Visualizations
Graphify provides built-in tools to visualize your project graph. After extraction, you can generate:
- **Hierarchical Tree:** `uv run --with graphifyy[mcp] graphify tree` (generates `GRAPH_TREE.html`)
- **Call-flow Diagrams:** `uv run --with graphifyy[mcp] graphify export callflow-html` (generates mermaid flowcharts)
- **Custom Force-Directed Graphs:** Because the graph is exported as a standard `graph.json`, you can use standard Python libraries (like `networkx` or `vis.js` templates) to render interactive physics-based graphs.

## Graphify as a Docker Container — the SERVER path

Graphify is wired as **one cwd-relative `graphify` entry in user scope**, never per repo
(see `skills/mcp-servers-setup/SKILL.md` → Graphify). On a **workstation** that entry is
`uv`. On a **server** (e.g. `coding.example.internal`) that lacks a per-user `uv`/Python toolchain,
`servers/graphify-mcp/` builds a Docker image with `graphifyy[mcp]` preinstalled running the
same stdio server (`graphify.serve`); the tracked wrapper `bin/graphify-mcp` injects
`-v "$PWD:/repo"` (MCP args aren't shell-expanded) so the single entry
`"graphify": { "command": "graphify-mcp" }` still serves whichever repo you launch from.
See `servers/graphify-mcp/README.md` for build, graph-generation, and handshake-testing.
Because `graphify.serve` is stdio-only, this is a `docker run -i --rm` subprocess per
session, not a long-running compose service like `serena`.

## Graphify + Local Ollama — Known Gotchas

Everything below was learned the hard way building graphs for three real
repos on a local RTX 3060. `init-graphify-projects.ps1` and
`patch-graphify-ollama-bugs.py` already encode all of this — read this
section if something still goes wrong, or before changing those scripts.

**The MCP server needs `graphifyy[mcp]`, not bare `graphifyy`.** The
`config/mcp-claude-code.json` template used to omit the extra; the server
would crash on the first request with `ModuleNotFoundError: No module named
'mcp'`. Always test a graphify MCP server change with a raw handshake before
trusting it:
```powershell
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uv run --with "graphifyy[mcp]" python -m graphify.serve graphify-out/graph.json
```
A working server replies with a `serverInfo` block on stdout, not silence
or a traceback on stderr.

**`extract --backend ollama` needs the `[ollama]` extra too** (`graphifyy[ollama]`,
for the `openai` package it uses to talk to Ollama's OpenAI-compatible
endpoint) — otherwise every chunk fails with *"the 'openai' package is
required for this backend"*.

**graphify's own ollama default model, `qwen2.5-coder:7b`, is not pulled by
anything in this stack** and isn't code you actually want here anyway — it's
a *coding* model, not a *structured-output* model, and the extraction schema
graphify asks for is closer to a function-call/JSON-mode task. In testing,
**`hermes3:8b`** (Nous Research, tuned for reliable tool-call/JSON output)
produced noticeably fewer malformed responses than `qwen2.5-coder:7b` on
identical repos, at a similar ~5GB VRAM footprint. `init-graphify-projects.ps1`
defaults to it and auto-pulls it via the Ollama REST API if missing.

**The default 5-minute client timeout is too short.** `docker logs ollama`
will show `500 | 5m0s` entries for requests that were still legitimately
generating — the client gives up and graphify retries with a smaller,
bisected chunk, wasting the work already done. Use `--api-timeout 1200`
(20 min) for local models; a healthy chunk usually finishes in 5-30s, but a
handful of harder ones can run long even on a good model.

**`--token-budget 6000 --max-concurrency 1`** keeps per-chunk requests small
enough for an 8B-class model to stay coherent, and avoids queuing multiple
requests against a single-GPU Ollama instance that can only serve one at a
time anyway (`OLLAMA_NUM_PARALLEL` doesn't help if there's only one GPU).

**Local models will still produce plenty of invalid JSON** — expect roughly
30-40% of semantic chunks to get skipped or partially truncated even with a
good model and the tuning above. graphify's adaptive bisection retries and
keeps partial results, so the graph is usable (AST/code-structure data,
which is deterministic, dominates the graph anyway) but not exhaustive on
the LLM-derived semantic layer. Rebuild with a real API-backed model
(`--backend openai`/`claude`/`gemini`/`deepseek` with a key) if you need
higher recall.

**`--force` bypasses graphify's semantic cache, not just the output file.**
A `--force` rebuild always redoes the full LLM extraction pass (can be
~1h per repo on a local 8B model), even if nothing changed. Don't add
`-Force` to `init-graphify-projects.ps1` out of habit.

**graphify v0.9.4 (latest on PyPI) crashes on its own malformed-response
recovery path** in three places — a noisy local model occasionally returns
`"nodes": "some string"` instead of a list of node objects, and graphify's
`.extend()`/`.get()` calls don't guard against that, so the crash happens
*after* a full ~1h extraction pass completes, right at the final write/merge
step. `scripts/patch-graphify-ollama-bugs.py` patches all three sites
defensively (skip non-dict entries, coerce IDs to `str`) directly in the uv
cache; `init-graphify-projects.ps1` runs it automatically before every
extraction. It's idempotent and safe to re-run. These are real upstream
robustness gaps worth reporting to the graphify maintainer, not local-LLM-
specific hacks — the patches don't change behavior for well-formed data.

**uv may extract graphify into multiple different cache directories** — one
per resolved environment, which can differ per target repo if it has its
own `pyproject.toml`/lockfile. The patch script scans and patches every copy
it finds (`uv cache dir` + `archive-v0/*/**/graphify/{__main__,ids}.py`), so
this is handled automatically, but it's why you can't just patch "the"
graphify install once and be done.

## Why a dedicated memory server (not Serena's local memory)?

While Serena includes basic local memory capabilities, **Omnigraph is the designated single source of truth for all cross-session memory in this stack.**

1. **Structure:** memory is written as typed nodes (`Decision`/`Rule`/`Preference`/`Convention`/`Component`/`Task`) that are queryable and reviewable, not free-text blobs — see the [`structured-memory`](../../skills/structured-memory/SKILL.md) skill.
2. **Project Isolation:** each repo gets its **own** graph, pinned per-agent via `OMNIGRAPH_GRAPH_ID`, so a bad write in one project cannot touch another.
3. **Retrieval:** graph traversal + vector + full-text in one engine, with a local Ollama embedder (no cloud key).

To prevent agent confusion and overlapping functionality, Serena's memory tools (`write_memory`, `read_memory`, etc.) are explicitly **disabled** using the `excludeTools` configuration in all provided JSON setup files. Do not re-enable them.

## Server Architecture

### Data Flow

```
your coding agent Agent
  │
  ├── Serena (uvx, stdio)
  │     ├── LSP servers (per-language) ──► Project source code
  │     └── Memories (JSON, local disk) ──► ~/.serena/memories/
  │
  ├── Omnigraph (stdio bridge ──► HTTP :8080)
  │     └── omnigraph-server ──► MinIO (S3 object store)
  │
  └── Superpowers (Node.js, stdio)
        └── Skills (discovered from Claude Code cache)
              ├── brainstorming
              ├── test-driven-development
              ├── systematic-debugging
              ├── writing-plans / executing-plans
              └── ... 14 skills total
```

### Docker Stack

```
docker compose ps
NAME                STATUS                    PORTS
omnigraph-minio     running (healthy)         127.0.0.1:9000→9000, 127.0.0.1:9001→9001
omnigraph-server    running (healthy)         127.0.0.1:8080→8080
omnigraph-viewer    running (healthy)         127.0.0.1:8090→8090
```

## MCP Endpoints

| Server | Access URL / Method |
|--------|--------------------|
| **Serena** | `uvx` stdio — no web UI |
| **Playwright** | `npx` stdio — headed browser, no API key needed ([docs](https://playwright.dev/docs/getting-started-mcp)) |
| **Omnigraph API** | [http://localhost:8080](http://localhost:8080) (bearer), `/healthz` (open) |
| **Omnigraph viewer** | [http://localhost:8090](http://localhost:8090) (read-only web UI) |
| **Superpowers** | `node` stdio — no web UI |
| **MinIO console** | `http://localhost:9001` (credentials in `.env.server`) |

## Playwright — Browser Automation for DeepSeek

DeepSeek cannot browse the web. [Playwright MCP](https://github.com/microsoft/playwright-mcp)
(34k+ stars, Microsoft) gives the agent full browser automation — navigate pages,
click elements, type text, take screenshots, mock APIs, and run arbitrary
Playwright scripts. No API key required.

**Why Playwright over search-only MCPs:**
- **Real browser** — renders JavaScript, handles logins, submits forms
- **Accessibility snapshots** — understands page structure without vision models
- **Network monitoring** — inspect and mock API responses
- **Storage state** — persists cookies/localStorage across sessions
- **Multi-browser** — Chrome, Firefox, WebKit, Edge
- **No rate limits** — no API key, no quota, runs locally

**Setup:**
1. Playwright MCP auto-installs on first `npx` run. No manual steps needed.
2. Restart your coding agent — tools appear as `mcp_playwright_*` (40+ tools).
3. Try: *"Go to example.com, take a screenshot, and describe what you see."*

**Configuration options** (add to `args` in `mcp.json`):
- `--headless` — run without visible browser window
- `--browser=firefox` — use Firefox instead of Chromium
- `--port 8931` — run as standalone HTTP server
- `--isolated` — fresh session each time (no persistent cookies)

## Directory Structure

```
mcp-servers/
├── config/                              # your coding agent MCP configs
│   ├── mcp.json                         # Production config (Serena + Omnigraph + Superpowers)
│   ├── mcp-claude-code.json             # Claude Code equivalent config
│   ├── mcp_antigravity.json             # Google Antigravity config
│   └── serena-project.yml               # Per-repo template for Serena
│
├── cluster/                             # Declared Omnigraph cluster (see servers/omnigraph/)
│   ├── cluster.yaml                     # Graphs + embedding provider
│   ├── memory.pg                        # Structured-memory schema
│   └── seed/                            # NDJSON seeds, one file per graph
│
├── scripts/                             # Platform scripts
│   ├── _omni_env.py                     # Resolve the LIVE stack (network, MinIO store)
│   ├── add-project-graph.sh             # Declare a new per-repo graph in cluster.yaml
│   ├── apply-cluster.sh                 # Converge the declaration into the server
│   ├── split-project-graph.py           # Split a repo out of a shared graph / refresh a seed
│   ├── dedup-graph.py                   # Remove duplicate edges (node-delete + merge-load)
│   ├── populate-embeddings.py           # Compute Decision vectors via local Ollama, overwrite-load
│   ├── patch-graphify-ollama-bugs.py    # Patch graphify's malformed-response crashes
│   ├── windows/                         # PowerShell scripts for Windows
│   │   ├── init-serena-projects.ps1     # Pre-index all repos with Serena
│   │   ├── init-graphify-projects.ps1   # Build or refresh repo graphs with Graphify
│   │   └── register-claude-code-mcp.ps1 # Register a server into Claude Code's config
│   └── linux/                           # Bash scripts for Linux/macOS
│       └── init-serena-projects.sh, init-graphify-projects.sh
│
├── servers/                             # MCP server source packages
│   ├── omnigraph/                       # Docs for the upstream server + MCP bridge wiring
│   ├── omnigraph-mcp/                   # Container build of the stdio bridge (hosts without Node)
│   ├── omnigraph-viewer/                # Read-only web UI (:8090)
│   ├── graphify-mcp/                    # Container build of the graphify stdio server
│   └── superpowers/                     # Node.js MCP server for coding workflows
│       ├── build/index.js               # Compiled server binary
│       ├── src/                         # TypeScript source
│       ├── package.json
│       └── node_modules/
│
├── omnigraph-setup/                     # Server/client setup + offline sync
│   ├── setup-agent-memory.ps1/.sh       # THE MCP BRIDGE: image, env vars, user-scope
│   │                                    #   override, per-repo .mcp.json audit. --check to diagnose
│   ├── setup-sync.ps1/.sh               # THE TIMER: writes .env, dry-runs, then schedules (5 min)
│   ├── sync-windows.ps1                 # One sync run (Docker Desktop)
│   ├── omnigraph-sync.sh                # One sync run (Linux/macOS) — same logic
│   ├── omnigraph_jsonl.py               # Delta/dedup/verify helper (forces UTF-8 stdio)
│   ├── pull_graph.py                    # Purge-then-load pull, with a pre-flight + restore
│   ├── client-setup.sh                  # Print/register the MCP block for a client
│   ├── SYNC-MANUAL.md                   # Operator manual (scheduling, OMNIGRAPH_NET, gotchas)
│   └── backups/                         # Every run snapshots local first (untracked)
│
├── docs/                                # Documentation files
│   ├── OMNIGRAPH-LOCAL-RUNBOOK.md       # Verified local setup + every fix
│   ├── TROUBLESHOOTING.md               # Known issues and fixes
│   ├── INSTALL-GUIDE.md                 # Manual step-by-step setup
│   └── OBSERVABILITY-MCP-SETUP.md       # Sentry / Datadog
│
├── data/                                # Persistent runtime data (auto-created)
│   └── minio/                           # MinIO object store (bind mount — survives `down -v`)
│
├── docker-compose.server.yml            # Memory backend (omnigraph-server + minio + viewer)
├── docker-compose.client.yml            # Developer-machine tools (serena, graphify image)
├── .env.shared / .env.server / .env.client          # Secrets (OMNIGRAPH_TOKEN, MinIO creds, …)
├── .env.shared.example / .env.server.example / .env.client.example   # Templates (safe to commit)
└── README.md                            # This file
```

## Token Savings — Evidence

Measured on a moderately complex refactoring task (5 files, 12 symbol lookups),
comparing before/after Serena:

| Metric | Before (file reads) | After (Serena) | Reduction |
|--------|---------------------|----------------|-----------|
| `read_file` calls | 24 | 1 | **-96%** |
| Token consumption | ~45K | ~18K | **-60%** |
| Turn count | 18 | 8 | **-56%** |
| Time | ~3 min | ~1 min | **-67%** |

## Per-Repository Starter

To enable MCP servers in a new repository, copy the starter pack from the parent repository:


```
starters/mcp-servers/
```

This installs `AGENTS.md` and `CLAUDE.md` files that teach the agent how to activate
Serena, run onboarding, build a Graphify graph, and use semantic code navigation from the first turn.

## Google Antigravity Setup

For Google Antigravity, configuration is loaded from the global `mcp_config.json` file. A complete, optimized template is provided at [`config/mcp_antigravity.json`](config/mcp_antigravity.json).

### 1. Configuration Location
On Windows, Antigravity reads its config from:
`C:\Users\<username>\.gemini\config\mcp_config.json` (which is symlinked to `C:\Users\<username>\.gemini\antigravity\mcp_config.json`).

### 2. Tool Filtering (excludeTools)
To prevent agent confusion and tool redundancy (e.g., memory tools exposed by both Serena and Omnigraph), we filter out unused/unneeded tools using the client-side `excludeTools` property:

*   **Serena**: Memory tools (`write_memory`, `read_memory`, `list_memories`, `delete_memory`, `rename_memory`, `edit_memory`) and GUI/setup tools (`onboarding`, `open_dashboard`, `initial_instructions`) are **excluded**, leaving Serena focused on LSP semantic search, refactoring, and project switching. `activate_project`/`get_current_config` are **kept** — Serena runs in multi-project mode (see [`../../docs/architecture.md`](../../docs/architecture.md)), and excluding those two would strand a session on whichever repo activates first with no way to switch to another.
*   **Superpowers**: All workflow tools are left active.
