# AI tooling audit — Windows workstation (2026-09-18, read-only)

Method: config files read with a redacting parser; versions via `--version`, `npm ls -g`,
`uv tool list`, `npm view`, PyPI JSON, `gh api .../releases/latest`, `docker ps/images/inspect`.
No install/update/config change was made. One side effect: the `@openhands/agent-canvas@1.20.0`
tarball was downloaded to the scratchpad (not installed) to read its `config/defaults.json`.
**M** = MEASURED on this host, **I** = INFERRED.

## Part 1 — Inventory

### 1a. MCP servers as wired, per client

| Client / scope | Server | Launch | Pin |
|---|---|---|---|
| Claude, user (`~/.claude.json`) | serena | `uvx --from serena-agent==1.7.0 serena start-mcp-server --context claude-code` | 1.7.0 |
| Claude, user | graphify | `uvx --from graphifyy[mcp] python -m graphify.serve graphify-out/graph.json` | **floats** |
| Claude, user | context7 | `node %APPDATA%\npm\node_modules\@upstash\context7-mcp\dist\index.js --api-key <redacted>` | global install 4.0.4 |
| Claude, user | playwright | `node %APPDATA%\npm\node_modules\@playwright\mcp\cli.js` | global install 0.0.80 |
| Claude, local (AutoOS project entry in `~/.claude.json`) | omnigraph | `docker run -i --rm --network mcp-server_mcp-net ... omnigraph-mcp:latest` (image bakes `omnigraph-mcp@0.8.0`) | 0.8.0 (via image) |
| Claude, project (`AutoOS/.mcp.json`) | omnigraph | `npx -y @modernrelay/omnigraph-mcp` | **floats → 0.10.0** |
| Claude, project (`agent-skills/.mcp.json`) | omnigraph | `docker run ... omnigraph-mcp:latest` (graph `agent-skills`) | 0.8.0 |
| Claude, project (`agent-skills/.mcp.json`) | cao-ops | `wsl -d Ubuntu bash -lc ... cao-ops-mcp-server` | uv tool 2.5.0 |
| Claude, plugins (`settings.json`) | superpowers 6.3.0 **on**; playwright plugin **off**; cq **off** | — | — |
| Claude, host built-ins | Claude_Browser pane, claude-in-chrome (`claudeInChromeDefaultEnabled: true`), WebFetch/WebSearch, scheduled-tasks, mcp-registry | — | — |
| Antigravity `agy` 1.2.5 (`~/.gemini/config/mcp_config.json`) | serena | `uvx --from serena-agent ... --project-from-cwd` (+ `excludeTools` memory list) | **floats** |
| agy | omnigraph | `npx -y @modernrelay/omnigraph-mcp` (localhost:8080, graph `memory`) | **floats → 0.10.0** |
| agy | superpowers | `node agent-skills/mcp-servers/servers/superpowers/build/index.js` | **stale path** (see Superpowers row, Part 2) |
| agy | graphify | `uv run --with graphifyy[mcp] ... agent-skills/graphify-out/graph.json` (absolute path) | floats |
| agy | playwright / context7 | `npx -y @playwright/mcp` / `npx -y @upstash/context7-mcp --api-key <redacted>` | **float** |
| agy | cao-ops | `wsl -d Ubuntu bash -c ... cao-ops-mcp-server` | 2.5.0 |
| CodeWhale 0.9.13 (`~/.codewhale/mcp.json`) | serena | `uvx --from serena-agent ...` | **floats** |
| codewhale | playwright | `npx -y @playwright/mcp@latest` | **floats (explicit @latest)** |
| codewhale | **mem0** | SSE `http://localhost:8001/sse` | container not running (M) |
| codewhale | superpowers | same stale `agent-skills/mcp-servers/...` path | stale |
| codewhale | graphify | `uv run ... research-repo/graphify-out/graph.json` (hard-wired to one repo) | floats |
| OpenHands (`~/.openhands/settings.json` agent_settings.mcp_config) | serena, graphify, omnigraph (`npx -y`, graph `autoos`), context7 (`npx -y`), playwright (`npx -y`), cao-ops | all **float** except cao-ops |
| `~/.gemini/antigravity-cli/settings.json` | no MCP entries (trusted workspaces only) | — | — |

Duplicates / floats (M):
- **D1 AutoOS: `omnigraph` defined twice for Claude** — local-scope docker entry (0.8.0) and `.mcp.json` npx entry (floats to 0.10.0). Local scope wins; `enabledMcpjsonServers: []` so the `.mcp.json` copy is dormant for Claude but is what other tools reading `.mcp.json` would use.
- **D2 The AutoOS local-scope entry passes `OMNIGRAPH_TOKEN=<value>` inline in docker `args`** — the value sits in plaintext in `~/.claude.json` and in every container's command line (`docker inspect`). Not in the repo, but pass it as `-e OMNIGRAPH_TOKEN` (name only) + `env`, as agent-skills does.
- **Floating launches:** omnigraph (agy, OpenHands, AutoOS/.mcp.json), playwright (agy, codewhale `@latest`, OpenHands), context7 (agy, OpenHands), serena (agy, codewhale, OpenHands), graphify (all four clients).
- Three `omnigraph-mcp:latest` stdio containers alive (2 h, 3 h, 26 h) — one per live Claude session; the 26 h one is probably leaked (I).

### 1b. Servers / packages

| Package | Installed | Latest (date) | Notes |
|---|---|---|---|
| serena-agent | 1.7.0 (Claude uvx pin); **uv tool 1.5.3** on PATH | 1.7.0 (2026-08-09) | uv tool copy is stale; unpinned clients resolve whatever uvx cache holds |
| graphifyy | uv tool 0.9.30; uvx cache floats | 0.9.63 (2026-09-16) | 33 patch releases behind |
| @modernrelay/omnigraph-mcp | 0.8.0 (image `omnigraph-mcp:latest`, 2 months old) | 0.10.0 (npm); repo v0.11.0 (2026-09-13) | npx clients get 0.10.0 against a 0.8.1 server (see Part 3) |
| omnigraph-server image | `modernrelay/omnigraph-server:v0.8.1` | v0.11.0 (2026-09-13) | 0.10/0.11 = coordinated upgrade + offline storage upgrade |
| @upstash/context7-mcp | 4.0.4 (npm -g) | 4.1.1 (2026-09-14) | patch: stops holding idle SSE streams |
| @playwright/mcp | 0.0.80 (npm -g) | 0.0.81 (2026-09-14) | adds WebMCP tools, 1 h idle browser close |
| superpowers | plugin 6.3.0; vendored MCP `superpowers` 0.1.0 | obra/superpowers v6.3.0 (2026-08-12) | current |
| cli-agent-orchestrator | 2.5.0 (Win uv tool + WSL) | 2.5.1 (GitHub 2026-09-11; PyPI still 2.5.0) | wait for PyPI |
| homelab-mcp | local source `agent-skills/infra/mcp-servers/servers/homelab-mcp` | — | **not wired in any client** (M) |
| mem0-mcp-selfhosted 0.3.2, mem0-mcp-server 1.0.0 | uv tools; 5 mem0 images | — | second memory layer, server down |
| @modelcontextprotocol/server-{memory,filesystem,github,postgres} | npm -g | — | not wired in any client (M); github/postgres are archived upstream |
| mcp-server-{fetch,git,time,docker,redis,jupyter} | uv tools | — | not wired in any client (M) |

### 1c. CLIs

| CLI | Installed | Latest (date) |
|---|---|---|
| claude (Win, native, autoUpdates off) | 2.1.276 | 2.1.276 (2026-09-18) — current |
| claude (WSL) | 2.1.236 | 2.1.276 |
| agy | 1.2.5 (Win), 1.2.1 (WSL) | latest not queried |
| gemini | **absent** (Win + WSL) | 0.60.0 — `~/.gemini/config/mcp_config.json` is used by agy |
| codewhale | 0.9.13 (npm ls says 0.8.60 — metadata mismatch) | 0.9.13 — current |
| opencode | WSL only, 1.18.30 | 1.18.31 (2026-09-14) |
| codex | 0.153.4 (npm -g; broken in WSL: resolves the Windows npm shim) | 0.155.0 (2026-09-18) |
| qwen-code | 0.19.9 | 0.24.0 |
| @openhands/agent-canvas | 1.19.0 → bundles agent-server **1.48.0** | 1.20.0 (2026-09-17) → bundles **1.49.1**; PyPI agent-server 1.49.2 (2026-09-17) |
| ollama | containers `ollama` + `ollama-agent` 0.20.4; legacy Windows client 0.5.11 | v0.34.2 (2026-09-15) |
| herdr | 0.9.0-preview.2026-09-08 | not queried |
| uv | 0.9.18 (Win), 0.9.6 (WSL) | 0.12.16 (2026-09-18) |
| node / npm | 24.16.0 / 11.13.0 (WSL node 18.19.1) | 26.9.0 current; 24 is LTS |
| python | 3.13.5 | — |
| git | 2.55.0.windows.3 | 2.55.0.windows.5 (2026-08-20) |
| docker | 29.7.2 | — |
| pwsh | 7.5.8 | 7.6.6 (2026-09-08) |
| gh | 2.92.0 | 2.101.0 (2026-09-15) |

### 1d. Claude skills (sources: superpowers plugin, 14 symlinks `~/.claude/skills -> agent-skills/skills`, anthropic-skills sync, built-ins)

Usage from `~/.claude.json skillUsage` (M): writing-plans 46, test-driven-development 28,
subagent-driven-development 25, systematic-debugging 22, structured-memory 20, brainstorming 19,
artifact-design 15, repository-index 12, executing-plans 10, using-superpowers 10, coding-principles 5,
homelab-access 5, html-working-documents 3, mcp-servers-setup 3, dispatching-parallel-agents 3,
code-review 1, requesting-code-review 1, receiving-code-review 1.
**Zero recorded uses:** swarm-orchestration, unattended-orchestration, herdr-orchestration, qa-swarm,
review-triage, pr-approval-agent, no-mistakes, babysit-prs, both `schedule` skills, `loop`, security-review, simplify.
Plugin usage: superpowers 2673+255, serena@inline 332, playwright plugin 163+242 (historic, now disabled), cq 1138+272 (disabled).

Duplicate skill pairs: `schedule` (cloud routines) vs `anthropic-skills:schedule` (local scheduled-tasks MCP);
`html-working-documents` vs `artifact-design`/Artifact tool vs `anthropic-skills:docs`;
superpowers `test-driven-development` vs `coding-principles` (TDD section);
superpowers `subagent-driven-development`/`dispatching-parallel-agents` vs `swarm-orchestration`/`unattended-orchestration`/`herdr-orchestration`;
superpowers `requesting-code-review` vs built-in `code-review` vs `qa-swarm`/`review-triage`/`pr-approval-agent`/`no-mistakes`.

## Part 2 — Overlap matrix

| Job | Tools doing it now | THE one | Disable / remove | Evidence |
|---|---|---|---|---|
| Durable memory | Omnigraph; mem0 (codewhale SSE + 2 uv tools + 5 images); `@modelcontextprotocol/server-memory` npm + `mcp/memory` image; Serena memories; Claude auto-memory (`MEMORY.md`); anthropic-skills:consolidate-memory | **Omnigraph** (already decided); Claude auto-memory stays for host-only preferences | Remove `mem0` from `~/.codewhale/mcp.json`; `uv tool uninstall mem0-mcp-selfhosted mem0-mcp-server`; `npm rm -g @modelcontextprotocol/server-memory`; prune mem0/`mcp/memory` images | M: mem0 wired but no container running; server-memory wired nowhere |
| Serena memory tools still exposed | This session's tool list includes `mcp__serena__write_memory/read_memory/list_memories/...` and `replace_in_files` | — | Restart sessions; add `replace_in_files` (1.7 name) to `excluded_tools` if it survives a restart | M: tool list; I: session predates the config change, `replace_content` is the older name |
| Code-symbol navigation | Serena (4 clients); host Read/Grep | **Serena**, pinned `==1.7.0` everywhere | Unpinned entries in agy/codewhale/OpenHands; stale `uv tool` serena 1.5.3 | M |
| Dependency graph | graphify (4 clients, 3 different graph paths) | **graphify**, cwd-relative, pinned | codewhale's hard-wired `research-repo/graphify-out` path; agy's hard-wired agent-skills path | M |
| Library docs | context7 (Claude, agy, OpenHands) | **context7** | nothing; pin it | M |
| Browser automation | Claude_Browser pane; claude-in-chrome; Playwright MCP (Claude user scope + agy + codewhale + OpenHands); playwright plugin (off) | Claude: **built-in pane** for dev servers, **claude-in-chrome** only for logged-in real-browser work. Other clients: **Playwright MCP** pinned | Drop `playwright` from Claude user scope (three browser stacks in one client) | M: all three present in this session; I: no per-MCP usage counters exist to rank them |
| Web fetch/search | Host WebFetch/WebSearch; `mcp-server-fetch` uv tool | **Host built-ins** | `uv tool uninstall mcp-server-fetch` (wired nowhere) | M |
| Planning/process | superpowers brainstorming/writing-plans/executing-plans; Plan agent/plan mode; coding-principles | **superpowers** (46+19+10 uses) | Trim TDD guidance out of `coding-principles` or point it at superpowers TDD | M usage |
| Orchestration | superpowers subagent-driven-dev (25) + dispatching (3); swarm-orchestration (0); unattended-orchestration (0); herdr-orchestration (0); CAO `cao-ops` MCP; Workflow tool | In-session: **superpowers subagent-driven-development**. Long-running/unattended: **unattended-orchestration** (covers CAO) | Fold `swarm-orchestration` into one of the two; unlink from `~/.claude/skills` | M usage; I on merge target |
| Review | code-review (1), superpowers requesting (1)/receiving (1), qa-swarm, review-triage, pr-approval-agent, no-mistakes, babysit-prs (all 0), security-review, simplify | **built-in `code-review`** for finding bugs; keep superpowers `receiving-code-review` (different job) | Unlink the five zero-use review skills from `~/.claude/skills` (keep in repo for other agents) | M usage |
| HTML working docs | html-working-documents (3); artifact-design (15) + Artifact tool; anthropic-skills:docs | **artifact-design + Artifact** in Claude | Unlink `html-working-documents` for Claude only | M usage |
| Scheduling | built-in `schedule` (cloud), `anthropic-skills:schedule` (local), `loop` | Pick one per need; both have 0 uses | Both trigger on "every day"; disable one if it misfires | M: 0 uses |
| Local LLM runtime | containers `ollama` (publishes :11434) and `ollama-agent` (internal only), **both 0.20.4, both mounting `C:/Apps/ollama`**; legacy Windows Ollama 0.5.11; `llama.cpp:server-cuda` image (7 GB, no container) | **one `ollama` container** | Stop/remove `ollama-agent` (its compose file `Server/server/workstation/ai/docker-compose.yml` no longer exists); uninstall legacy Windows Ollama (not running, no autostart); drop llama.cpp image unless planned | M: `docker inspect` labels, missing compose file, `tasklist` shows no Ollama process |
| OpenHands deployments | agent-canvas 1.19.0 (uvx agent-server 1.48.0); docker `ws-openhands-agent` | **agent-canvas** | `ws-openhands-agent` is not deployed (no container in `docker ps -a`) but is still defined in `agent-skills/infra/local-ai/docker-compose.yml:111` and described in `docs/superpowers/plans/2026-09-17-autoos-l2-DONE.md:45`; remove it from the compose file, or accept that it is a second OpenHands | M |
| Superpowers delivery | Claude plugin; vendored MCP (agy, codewhale) | plugin for Claude, vendored MCP for others (no per-client overlap) | Fix the path: agy/codewhale point at `agent-skills/mcp-servers/servers/superpowers/build` (leftover build only, dated July); source now lives in `infra/mcp-servers/servers/superpowers` | M |
| Dormant / wired nowhere | homelab-mcp; npm server-filesystem/github/postgres; uv mcp-server-git/time/docker/redis/jupyter; `mcp/*` images from 2025 | — | Remove or wire deliberately | M: grep of all client configs |

## Part 3 — Update proposal

| Tool | Installed | Latest | Gap | Risk / breaking notes | Rec. | Command |
|---|---|---|---|---|---|---|
| omnigraph-mcp (npx clients) | floats → 0.10.0 | 0.10.0 | **ahead** of server | v0.10.0 notes: "Coordinated upgrade required… Breaking graph vocabulary" — a 0.10 client against the 0.8.1 server is skew. https://github.com/ModernRelay/omnigraph/releases/tag/v0.10.0 | **Pin now to 0.8.0** | change args to `@modernrelay/omnigraph-mcp@0.8.0` in agy, OpenHands, `AutoOS/.mcp.json` |
| omnigraph server + mcp | 0.8.1 / 0.8.0 | v0.11.0 / 0.10.0 | 3 minors | v0.11.0: upgrade CLI, server and integrations together; v0.9/v0.10 graphs need an offline storage upgrade. https://github.com/ModernRelay/omnigraph/releases/tag/v0.11.0 | **Wait** — planned maintenance with snapshot | snapshot first, then bump image tag + Dockerfile pin together |
| OpenHands agent-server | 1.48.0 (via agent-canvas 1.19.0) | 1.49.2 (PyPI) / 1.49.1 (canvas 1.20.0) | 1 minor | **Neither release has the PowerShell fix:** PR OpenHands/software-agent-sdk#3913 is `open`, not merged; issue #5133 is `open`. 1.49.0 adds a Docker runtime mode and removes the deprecated desktop URL endpoint; 1.49.2 holds fastmcp below 4 (browser tools). https://github.com/OpenHands/software-agent-sdk/releases/tag/v1.49.0 | **Update** (keep the local workaround for #5133) | `npm i -g @openhands/agent-canvas@1.20.0` |
| opencode (WSL) | 1.18.30 | 1.18.31 | patch | ACP session and TUI auth-error fixes | Update | `opencode upgrade` (WSL) |
| serena-agent | 1.7.0 / uv tool 1.5.3 | 1.7.0 | none / stale copy | — | **Pin** in every client; remove the stale tool | `uv tool uninstall serena-agent`; args `--from serena-agent==1.7.0` in agy/codewhale/OpenHands |
| @playwright/mcp | 0.0.80 | 0.0.81 | patch | new WebMCP tools; headless browser closes after 1 h idle | Update + pin | `npm i -g @playwright/mcp@0.0.81`; `npx -y @playwright/mcp@0.0.81` elsewhere |
| context7-mcp | 4.0.4 | 4.1.1 | minor | no breaking notes | Update + pin | `npm i -g @upstash/context7-mcp@4.1.1` |
| graphifyy | 0.9.30 | 0.9.63 | 33 patches | not reviewed per patch | Update + pin | `uv tool install --force graphifyy[mcp]==0.9.63`; `--from graphifyy[mcp]==0.9.63` |
| claude (Win) | 2.1.276 | 2.1.276 | none | — | Leave | — |
| claude (WSL) | 2.1.236 | 2.1.276 | 40 builds | — | Update | `claude update` (WSL) |
| uv | 0.9.18 (Win), 0.9.6 (WSL) | 0.12.16 | 3 minors | 0.10: `uv venv` no longer silently removes existing venvs; 0.11: new TLS stack (rustls-platform-verifier; certificate behaviour changes); 0.12: new breaking-changes section. https://github.com/astral-sh/uv/releases/tag/0.11.0 | Update after checking any pinned `uv_build` upper bounds | `uv self update` |
| ollama | 0.20.4 (image 5 months old) | v0.34.2 | 14 minors | not reviewed; containers share one model store | Update after removing `ollama-agent` | `docker pull ollama/ollama:0.34.2` + pin the tag in compose |
| codex | 0.153.4 | 0.155.0 | 2 | — | Update | `npm i -g @openai/codex@0.155.0` |
| cli-agent-orchestrator | 2.5.0 | 2.5.1 (GitHub only) | patch | not on PyPI yet | Wait | — |
| gh / git / pwsh | 2.92.0 / .windows.3 / 7.5.8 | 2.101.0 / .windows.5 / 7.6.6 | minor | pwsh 7.6 is a new LTS line | Update | `winget upgrade GitHub.cli Git.Git Microsoft.PowerShell` |
| qwen-code | 0.19.9 | 0.24.0 | 5 minors | not wired in any MCP config; decide keep/remove | Remove or update | `npm rm -g @qwen-code/qwen-code` |
| legacy Windows Ollama | 0.5.11 | — | — | talks to the 0.20.4 container, prints a version-skew warning | Remove | Apps & Features → Ollama → Uninstall |
