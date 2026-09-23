# Agent-Skills Repository Restructure — Design

**Last Updated:** 2026-07-09
**Status:** Approved design (ready for implementation planning)
**Author:** Claude (brainstorming session with maulser)

## Objective

Restructure the `agent-skills` repository so it is coherent, discoverable, and
cross-platform, and extend it to deliver three clear pillars:

1. **Reusable agent skills** — including a new `coding-principles` skill (DRY,
   TDD, single responsibility, documentation, changelog-based backtracking,
   MCP-first navigation).
2. **A self-hosted MCP runtime** with **structured, cross-project, cross-agent
   memory** built on **Omnigraph** (replacing Mem0 as the default), combined
   with the existing local containerized servers (Serena, Graphify,
   Superpowers, Playwright).
3. **Remote / multi-agent operation** via **Herdr** (agent multiplexer) on both
   Linux and Windows, alongside the existing Antigravity remote UI.

The repository currently mixes proper `skills/` with loose directories (`Gen/`,
`prompt/`, `repositories/`, `antigravity-remote-ui/`), committed generated
output (`graphify-out/`), scattered TODO files, and Windows-only scripts with
hardcoded personal paths. This design defines a clean taxonomy and the content
additions to fix that.

## Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Restructure scope | Full clean re-org via `git mv` (history preserved) |
| 2 | Coding principles delivery | New `skills/coding-principles/` skill + starter |
| 3 | Memory layer | **Omnigraph replaces Mem0** as default; Mem0 kept as a wired-alongside, off-by-default documented fallback |
| 4 | Remote/multi-agent | **Herdr** added as primary multiplexer (Linux **and** Windows); `antigravity-remote-ui` kept as a niche |
| 5 | Root instruction files | Reduced to **thin pointers** at the skills |
| 6 | README | Rewritten to explain and summarize the whole repository |

## Key evaluation: Omnigraph vs Mem0

**Finding:** Omnigraph is a storage + retrieval + coordination engine, **not** an
automatic fact-extraction system. Its MCP server exposes `schema, branches,
queries, mutations, ingest`. Mem0 auto-extracts facts from raw conversation via
an LLM + embeddings and stores unstructured vector blobs.

**Conclusion:** For the stated goal — *"well structured, fast, efficient memory
across agents so future builds and rules are properly integrated"* — Omnigraph
is a better fit **provided** we replace Mem0's auto-extraction with an explicit
**structured-memory protocol**:

- The agent writes **typed** memory (`Decision`, `Rule`, `Preference`,
  `Convention`, `Component`, `Task`) edged to a `Project` — directly satisfying
  "rules properly integrated."
- Retrieval fuses graph traversal + vector ANN + full-text (RRF) — strictly more
  powerful than Mem0's pure vector search.
- Versioned, branchable, auditable, reversible, shared across agents/projects.

**Tradeoff (accepted):** heavier infra (`omnigraph-server` + an S3-compatible
store such as MinIO + Cedar auth) and memory is no longer automatic — it depends
on an agent-discipline protocol. That discipline is treated as a feature given
the "structured" goal. Mem0 remains available as a fallback (see below).

**Graphify is kept unchanged** — it auto-extracts *code* structure (AST + LLM
semantic layer), which Omnigraph does not. The two are complementary: Graphify =
code graph, Omnigraph = memory/decisions/rules graph.

## Target repository structure

```text
agent-skills/
├── README.md                      # rewritten: what this repo is, the 3 pillars, quick links
├── CONTRIBUTING.md
├── LICENSE
├── CHANGELOG.md                   # NEW: backtracking log (dogfoods the principle)
├── AGENTS.md / CLAUDE.md / GEMINI.md   # thin pointers → skills
│
├── skills/                        # Pillar 1: reusable skills (single source of truth)
│   ├── coding-principles/         # NEW
│   │   ├── SKILL.md
│   │   ├── agents/openai.yaml
│   │   └── references/
│   ├── structured-memory/         # NEW — Omnigraph memory protocol + schema
│   │   ├── SKILL.md
│   │   └── references/schema.md
│   ├── mcp-servers-setup/         # updated (Omnigraph replaces Mem0)
│   └── html-working-documents/    # unchanged
│
├── starters/                      # Pillar 2: per-repo adapters (thin pointers)
│   ├── coding-principles/         # NEW (README + paste-in blocks)
│   ├── mcp-servers/               # updated
│   └── html-working-documents/
│
├── infra/                         # Pillar 3: self-hosted runtime
│   ├── mcp-servers/               # was top-level mcp-servers/
│   │   ├── docker-compose.yml     # Omnigraph + MinIO default; Mem0 under a compose profile
│   │   ├── servers/               # graphify-mcp, omnigraph bridge (config); mem0-* retained under fallback dir
│   │   ├── config/                # de-personalized, ${VARS} not C:\Users\<you>
│   │   ├── scripts/{linux,windows}/
│   │   └── docs/
│   └── remote-access/
│       ├── herdr/                 # NEW — agent multiplexer (Linux + Windows)
│       └── antigravity-remote-ui/ # moved, kept as niche
│
├── docs/                          # cross-cutting docs
│   ├── agent-compatibility.md
│   ├── architecture.md            # consolidated system diagram
│   ├── decisions/                 # ADRs
│   │   ├── 0001-omnigraph-over-mem0.md
│   │   └── 0002-herdr-multiplexer.md
│   └── superpowers/specs/         # this design + future specs
│
└── prompts/                       # was Gen/ + prompt/ + repositories/
    ├── repo-to-spec.md            # was Gen/gen_instructions_from_repo.md
    ├── big-todo-workflow.md       # was prompt/how-to-big-todo.md
    └── examples/python-analysis-repo.md
```

## Migration map

| Current | Destination | Action |
|---|---|---|
| `mcp-servers/` | `infra/mcp-servers/` | `git mv` |
| `antigravity-remote-ui/` | `infra/remote-access/antigravity-remote-ui/` | `git mv` |
| `Gen/gen_instructions_from_repo.md` | `prompts/repo-to-spec.md` | `git mv` + rename |
| `Gen/README.md` | folded into `prompts/repo-to-spec.md` header | consolidate |
| `prompt/how-to-big-todo.md` | `prompts/big-todo-workflow.md` | `git mv` |
| `repositories/python-analysis-repo.md` | `prompts/examples/python-analysis-repo.md` | `git mv` |
| `mcp-servers/TODO.md` | `docs/decisions/` note or delete | consolidate |
| `antigravity-remote-ui/manual_todo.md` | fold into herdr/antigravity docs | consolidate |
| `graphify-out/` | removed from tracking | `git rm -r --cached` + `.gitignore` |
| `mcp-servers/servers/mem0-*` | `infra/mcp-servers/servers/_fallback/mem0-*` | move under fallback |

All moves preserve git history. `graphify-out/` (AST cache, `graph.json`,
`graph.html`, manifests) is regenerable and must not be tracked.

## Component designs

### coding-principles skill

`skills/coding-principles/SKILL.md` — each principle as an actionable rule with a
"red flags" table matching the repo's existing superpowers style:

- **DRY / single source of truth** — extract on the 2nd real duplication, not
  speculatively.
- **TDD** — red → green → refactor; defers to `superpowers:test-driven-development`
  where that skill is present.
- **Single responsibility** — one reason to change; file-size-as-smell heuristic.
- **Documentation** — document the *why*; keep SKILL/README in sync with code.
- **Backtracking via changelogs** — maintain `CHANGELOG.md` + `docs/decisions/`
  ADRs so any agent can reconstruct why a change happened and revert safely.
- **MCP-first navigation** — use Serena / Graphify / Omnigraph before brute-force
  file reads; include token-savings rationale.

`starters/coding-principles/README.md` provides a short paste-in block for
`AGENTS.md` / `CLAUDE.md` / `GEMINI.md` that points at the full skill.

### structured-memory skill (Omnigraph protocol)

`skills/structured-memory/SKILL.md` defines the protocol replacing auto-extraction:

- **Schema** (`references/schema.md`): node types `Project`, `Decision`, `Rule`,
  `Preference`, `Convention`, `Component`, `Task`; edges `decided-in`,
  `constrains`, `supersedes`, `part-of`, `applies-to`.
- **Session start**: query the project subgraph (traversal + vector + full-text)
  for rules/decisions/preferences and load as context.
- **On durable decision / session end**: write typed nodes; use a per-project
  branch; merge on confirmation.
- **Cross-project**: single shared graph, project-scoped subgraphs; global
  preferences at the root scope.

### infra/mcp-servers (Omnigraph default, Mem0 fallback)

- `docker-compose.yml`: default services become `omnigraph-server` + `minio`
  (S3 store). The Mem0 stack (`mem0-postgres`, `mem0-api`, `mem0-mcp`) is moved
  under a Docker Compose **profile** (e.g. `--profile mem0-fallback`) so it is
  wired alongside but **off by default**.
- MCP configs (`config/*.json`, `serena-project.yml`): register
  `@modernrelay/omnigraph-mcp`; remove Mem0 from the default set; keep a
  commented/fallback Mem0 entry.
- All hardcoded `C:\Users\<you>\...` paths replaced with env vars
  (`AGENT_SKILLS_ROOT`, `CODE_ROOT`) resolved in `.env` / script args. Linux is
  the documented primary; Windows kept at parity.
- `docs/decisions/0001-omnigraph-over-mem0.md` records rationale, the
  auto-extraction tradeoff, and the switch-back criteria for the fallback.

### Herdr integration (Linux + Windows)

`infra/remote-access/herdr/` — setup docs and helper scripts for the single Rust
binary:

- **Install**: Linux/macOS (curl / Homebrew / mise) **and Windows** (beta
  binary; documented install path and any WSL guidance).
- **Usage**: start a persistent multi-agent session, detach/reattach over SSH
  (incl. from a phone), agent-to-agent socket API.
- **Positioning**: recommended multiplexer over raw tmux; a comparison table
  (Herdr vs tmux vs antigravity-remote-ui) states which to use when.
- `antigravity-remote-ui/` retained for the distinct Antigravity IDE
  DOM-streaming case.
- `docs/decisions/0002-herdr-multiplexer.md` records the rationale.

### README rewrite

`README.md` summarizes the whole repository: the three pillars, the skill list,
the infra quick-start (Omnigraph default + Mem0 fallback), the remote-access
options (Herdr + antigravity-remote-ui), and links into each area. It reflects
the new taxonomy exactly.

### Root instruction files → thin pointers

`AGENTS.md`, `CLAUDE.md`, and a new `GEMINI.md` are reduced to short pointers
that reference `skills/coding-principles/`, `skills/structured-memory/`,
`skills/mcp-servers-setup/`, and `skills/html-working-documents/` — no duplicated
workflow content. `CLAUDE.md` retains only Claude-specific notes.

## Cross-platform & de-personalization

- No personal absolute paths or secrets in tracked files.
- Path parameterization via env vars; `scripts/{linux,windows}/` kept at parity.
- Linux documented as primary (matches the working environment); Windows peer.

## Out of scope

- Rewriting internals of Serena / Graphify / Superpowers (only their wiring).
- Building the Mem0→Omnigraph data migration tooling (documented as a manual,
  optional step; not automated in this work).
- Any change to the `html-working-documents` skill content.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Omnigraph is young (v0.8.1) and heavier to operate than Mem0 | Mem0 kept as wired-alongside fallback (compose profile) + ADR switch criteria |
| Structured memory depends on agent discipline | Made a first-class skill invoked at session boundaries |
| Herdr Windows support is beta | Document beta status + WSL fallback path |
| Large `git mv` churn | Sequenced migration; history preserved; verify build/docs links after each move |

## Success criteria

- Repository has the three-pillar taxonomy above; no loose top-level docs dirs.
- `graphify-out/` untracked; no personal paths/secrets in tracked files.
- `coding-principles` and `structured-memory` skills exist with starters.
- Default MCP stack runs Omnigraph (+MinIO); Mem0 starts only under its profile.
- Herdr setup works on Linux and Windows per docs.
- README accurately summarizes the whole repo; root instruction files are thin
  pointers.
