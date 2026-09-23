# Agent Instructions

Reusable agent skills + per-repo starters + a self-hosted MCP runtime.
**Keep this file and starters as thin pointers — a skill's `SKILL.md` is the source of truth.**

## Start at the router

**`skills/repository-index/SKILL.md`** — the repository map: every directory, MCP server, skill, and
instruction file, each with the trigger that loads it. Read it first; it tells you which of these to
open. (`skills/SYNC.md` is the vendoring ledger, not a router.)

| Skill | Load when |
|---|---|
| `coding-principles` | **any** implementation, refactor, bugfix |
| `structured-memory` | **every** session — recall at start, persist at end |
| `structured-memory/references/operations.md` | **before** any Omnigraph query/mutate/load/sync |
| `html-working-documents` | plan / research / review / report / diagram / prototype |
| `mcp-servers-setup` | wiring or debugging the stack |
| `homelab-access` | **before** any SSH / `DOCKER_HOST=ssh://` / firewall / NAS command |
| `swarm-orchestration` | multi-file work (drives `pr-approval-agent`, `qa-swarm`, `review-triage`, `no-mistakes`, `babysit-prs`) |
| `herdr-orchestration` | agent work that must **outlive the session**, be human-supervised, use a non-Claude agent, or wait on a long-running process |
| `unattended-orchestration` | work runs for **hours or overnight with nobody watching** (headless worktrees, lanes, usage-limit recovery, guard-gated auto-merge) **OR** an interactive N-level CAO hierarchy across providers (Claude / Gemini / DeepSeek, dashboard on `:9889`, plan contract, quota ladders, cross-family review). Read §9 before touching CAO — a dozen of its failure modes are silent |

Two more places to look before you write anything:

| Path | Go here when |
|---|---|
| `docs/decisions/` | You are about to change or question a design choice — the ADR says **why** it is that way |
| `docs/superpowers/specs/` | You are about to build something — it may already be designed and approved |

## MCP servers — mandatory, not optional

These four are the **default** way to work here, not an optimisation you reach for on
big tasks. Reading whole files to find a symbol, or re-deriving what this repo already
decided, is the slow path.

| Server | Mandatory when | Instead of |
|---|---|---|
| **`serena`** | **every session** — `activate_project(<absolute path>, never a bare name)` before the first code question, then `find_symbol` / `find_referencing_symbols` / `replace_symbol_body`. **One active project per session**: subagents in other worktrees must not re-activate — see *Worktrees* in `skills/mcp-servers-setup/SKILL.md` | `Read`/`Grep` over whole files |
| **`omnigraph`** | **every session, both ends** — recall before changing code, persist the durable *why* before finishing (see Memory, below) | re-deriving decisions; a scratch note that dies with the session |
| **`graphify`** | any question spanning **more than one file** — blast radius, "what connects X to Y" | fanning reads out across modules |
| **`playwright`** | **any change to something a browser renders** — drive the page and verify before calling it done | asserting from the diff that the UI works |

One escape hatch, and it is not silent: if a server is genuinely unavailable, fall back
and **say which one and why** in your answer. A dead `OMNIGRAPH_TOKEN` looks exactly
like a repo that has no memory — the difference is only visible if you report it.

Wiring, ports, and failure signatures: `skills/mcp-servers-setup/SKILL.md`.
Full routing table (incl. `context7`, `superpowers`, observability):
`skills/repository-index/SKILL.md` §2.

## Memory

- **One graph per repo**, named after the folder (e.g., `agent-skills`, `sibling-analysis-repo`). This repo → `agent-skills`.
- **Always write and push project memory to the Omnigraph graph/branch matching the repository name (`OMNIGRAPH_GRAPH_ID=<repo-folder-name>`)**, never to `memory`.
- Pinned by `OMNIGRAPH_GRAPH_ID` in `.mcp.json`; **no tool takes a graph argument**.
- **Which names exist? `graphs_list`.** `cluster.yaml` is gitignored and purged from history —
  it is not a discovery source. **Which one is yours?** The graph whose `Project.repository`
  equals your `git remote get-url origin`. Run that check before trusting recall — it is the
  only cheap detector for a bridge pinned to the wrong repo's graph.
  Protocol: `skills/structured-memory/SKILL.md` → *Which graph fits this repository?*
- **Both are token-only.** No `Authorization` header ⇒ **401**; `/graphs` without the
  cluster-scoped `graph_list` grant ⇒ **403**, never a misleading empty list. `/healthz` is the
  one open endpoint and returns liveness, not names. One token = one actor = that actor's
  graphs, and it is **all-or-nothing** — there is no token that reveals only part of the roster
  until per-user policies are switched on. Never share it.
  Auth model + measurements: `skills/mcp-servers-setup/SKILL.md` → *Discovering repository /
  project names*.
- `memory` graph = 2 global `Preference`s only (already Principles 2 & 6 of
  `coding-principles`). **Never write or push project data to `memory`.**
- Omnigraph is the only memory layer — no fallback (ADR 0003).
- **`.gq` is GraphQuery, not GraphQL** — `query() { match {…} return {…} }`; **no** top-level
  `mutation {}` wrapper; writes are `query` blocks (`insert`/`update`/`delete`) sent via `mutate`.
  Full syntax + traps: `skills/structured-memory/SKILL.md`.

Two env vars must exist **before launch** (never committed):

| Var | Unset ⇒ | Get it from |
|---|---|---|
| `OMNIGRAPH_TOKEN` | empty bearer → memory **silently dead** | `infra/mcp-servers/.env.shared` |
| `OMNIGRAPH_NET` | wrong docker network → `fetch failed` | `python3 infra/mcp-servers/scripts/_omni_env.py` |

Both: `infra/mcp-servers/omnigraph-setup/setup-agent-memory.ps1` (or `.sh`) — `-Check` to diagnose.

> **Graph looks empty? Config bug until proven otherwise — do NOT rebuild.**
> `0 rows except 2 Preferences` **is** the `memory` graph. A same-named `omnigraph` in
> `~/.claude.json` (user scope) silently outranks `.mcp.json`. Run `setup-agent-memory -Check`.

**Declared ≠ live.** Verify against the server (`graphs_list`, `schema_get`, `docker inspect`),
never a config file — an unapplied cluster rejects edge types *silently*.

## Infrastructure (`infra/`, see `docs/architecture.md`)

| Path | What | Note |
|---|---|---|
| `mcp-servers/` | the stack + `cluster/` config + `scripts/` + `omnigraph-setup/` | manual: `omnigraph-setup/SYNC-MANUAL.md` |
| `local-ai/` | Ollama, LiteLLM, Open WebUI, OpenHands | **optional except Ollama**: serves `nomic-embed-text` (`:11434`) = Omnigraph's `Vector(768)` recall. Without it recall degrades to traversal + full-text. LiteLLM (`:4000`) = one OpenAI-compatible endpoint → `swarm-orchestration` model routing |
| `remote-access/` | Herdr multiplexer | |

Memory path needs no Postgres, pgvector, or LLM API key.

**Harbor** = self-hosted registry. Never install locally; push images to the remote instance.

## Hard rules

- **One home per fact.** A skill's `SKILL.md` owns its workflow; a config file owns its numbers; an
  ADR owns its reasoning; instruction files and starters are **pointers**. Specifically:
  `swarm-orchestration/SKILL.md` holds no thresholds — they live in
  `custom_orchestration/agent_orchestration.config.yaml`. When the same rule appears twice, collapse
  it; do not sync it by hand.
- **Line endings.** `.gitattributes` pins `*.sh`/`*.py`/units/configs to `eol=lf`; `*.ps1` to
  `crlf`. A CRLF shebang → kernel seeks `bash\r` → script cannot run. Never "fix" a script by
  re-saving as CRLF.
- **Compatibility.** Give each agent its native instruction file; keep adapters short.
  See `docs/agent-compatibility.md`.
- **Scratch script isolation (MANDATORY).** Agents (Gemini, Claude, Antigravity, etc.) must NEVER
  create scratch scripts or throwaway query files in random locations or the repository root. All
  scratch scripts MUST strictly be placed within connected scratch folders (e.g., Claude's
  `.claude/scratch/`, Antigravity's session scratch directory `<appDataDir>/brain/<conversation-id>/scratch/`,
  or the repository's dedicated `scratch/` directory).
