# Agent-Skills Restructure + Omnigraph Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, checkpointed) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is infra/docs-heavy; "verify" steps replace unit tests where there is no code to unit-test.

**Goal:** Restructure the `agent-skills` repo into a clean three-pillar layout, add `coding-principles` and `structured-memory` skills, switch the default memory layer from Mem0 to Omnigraph (Mem0 kept as an off-by-default fallback), add Herdr (Linux+Windows), then deploy Omnigraph locally, expose it via the OPNsense/Caddy reverse proxy, seed graphs for `homelab-server` and `agent-skills`, and update the `homelab-server` + `finance-repo` agent instructions.

**Architecture:** Phase A is pure repo restructuring (git mv + new markdown/config, no runtime). Phase B is live infra: bring up the Omnigraph + MinIO Docker stack on the coding VM, expose `omnigraph.example.org` through the existing OPNsense `os-caddy` reverse proxy (Authelia-protected), seed structured-memory graphs, and repoint two sibling repos off Mem0.

**Tech Stack:** git, Markdown, Docker Compose, Omnigraph (`omnigraph-server` + `@modernrelay/omnigraph-mcp`), MinIO (S3), OPNsense os-caddy (Dynu DNS-01 wildcard), Serena/Graphify MCP.

## Global Constraints

- Preserve git history on every move — use `git mv`, never delete+recreate.
- No personal absolute paths (`C:\Users\<you>...`, `/home/<you>/...`) or secrets in tracked files — parameterize via `${VARS}`/`.env`.
- `graphify-out/` and other generated artifacts must be untracked.
- Default MCP stack = Serena + Graphify + Omnigraph + Superpowers + Playwright. Mem0 lives only under a Docker Compose profile `mem0-fallback`, off by default.
- Herdr docs must cover **both Linux and Windows**.
- Internet exposure reuses the existing edge model (OPNsense firewall unchanged; Caddy TLS-terminates; Authelia in front). Never open a new Fritzbox/OPNsense port; only add a Caddy vhost + Unbound override to an existing VM.
- Every task ends with a commit on branch `restructure/agent-skills` (Phase A) — Phase B commits go to the relevant repo's own branch.
- **Hard checkpoints (STOP for user confirmation):** before first container `up`, before any edit under `/home/<you>/code/homelab-server` or `/home/<you>/code/finance-repo`, and before the reverse-proxy exposure task.

---

## PHASE A — Repository restructure (agent-skills only, no runtime)

### Task A1: Branch + untrack generated artifacts

**Files:**
- Modify: `.gitignore`
- Untrack: `graphify-out/`

- [ ] **Step 1:** Create the working branch.
  Run: `git -C /home/<you>/code/agent-skills checkout -b restructure/agent-skills`
- [ ] **Step 2:** Append generated-artifact ignores to `.gitignore` (Omnigraph/MinIO data too):
  ```gitignore
  # Generated graph artifacts (regenerable — never track)
  graphify-out/
  # Local infra runtime data
  infra/mcp-servers/data/
  **/omnigraph-data/
  **/minio-data/
  ```
- [ ] **Step 3:** Stop tracking the committed graph output:
  Run: `git -C /home/<you>/code/agent-skills rm -r --cached graphify-out`
- [ ] **Step 4 (verify):** `git status` shows `graphify-out/` removed from index and `.gitignore` modified; working tree still has the files on disk.
- [ ] **Step 5:** Commit.
  ```bash
  git add .gitignore && git commit -m "chore: untrack generated graphify-out, ignore infra runtime data"
  ```

### Task A2: Create the top-level taxonomy and move infra + prompts

**Files:** (all `git mv`)
- `mcp-servers/` → `infra/mcp-servers/`
- `antigravity-remote-ui/` → `infra/remote-access/antigravity-remote-ui/`
- `Gen/gen_instructions_from_repo.md` → `prompts/repo-to-spec.md`
- `prompt/how-to-big-todo.md` → `prompts/big-todo-workflow.md`
- `repositories/python-analysis-repo.md` → `prompts/examples/python-analysis-repo.md`

- [ ] **Step 1:** Make dirs: `mkdir -p infra/remote-access prompts/examples`
- [ ] **Step 2:** Move infra: `git mv mcp-servers infra/mcp-servers` and `git mv antigravity-remote-ui infra/remote-access/antigravity-remote-ui`
- [ ] **Step 3:** Move + rename prompts (three `git mv` commands as listed above), then fold `Gen/README.md`'s intro into the top of `prompts/repo-to-spec.md` and `git rm Gen/README.md`; remove now-empty `Gen/`, `prompt/`, `repositories/`.
- [ ] **Step 4 (verify):** `git status` shows renames (R) not delete+add; `ls infra prompts` matches the target tree; `find . -maxdepth 1 -type d` no longer lists `Gen prompt repositories mcp-servers antigravity-remote-ui`.
- [ ] **Step 5:** Commit `restructure: move infra and prompts into new taxonomy`.

### Task A3: De-personalize infra configs and scripts

**Files:**
- Modify: `infra/mcp-servers/README.md`, `infra/mcp-servers/config/*.json`, `infra/mcp-servers/config/serena-project.yml`, both `scripts/{linux,windows}/*` sets, any `file:///c:/Users/...` links.

- [ ] **Step 1:** Grep the moved tree for personal paths:
  Run: `grep -rInE 'C:\\\\Users\\\\<you>|/home/<you>/|<you>|file:///c:' infra prompts`
- [ ] **Step 2:** Replace each hit with a variable: `${AGENT_SKILLS_ROOT}` for the repo root and `${CODE_ROOT}` for the parent code dir; add both to `infra/mcp-servers/.env.example` with commented Linux + Windows examples.
- [ ] **Step 3 (verify):** the Step-1 grep now returns nothing.
- [ ] **Step 4:** Commit `chore: de-personalize infra paths via env vars`.

### Task A4: `coding-principles` skill + starter

**Files:**
- Create: `skills/coding-principles/SKILL.md`, `skills/coding-principles/agents/openai.yaml`, `skills/coding-principles/references/changelog-backtracking.md`
- Create: `starters/coding-principles/README.md`

- [ ] **Step 1:** Write `SKILL.md` with frontmatter (`name: coding-principles`) and one actionable section per principle — DRY/single-source, TDD (defers to `superpowers:test-driven-development` when present), single-responsibility (file-size smell), documentation (document the *why*), backtracking via `CHANGELOG.md` + `docs/decisions/` ADRs, MCP-first navigation (Serena/Graphify/Omnigraph before file reads) — each with a "red flags" table matching the repo's superpowers style.
- [ ] **Step 2:** Write `agents/openai.yaml` (Codex agent definition mirroring the html-working-documents skill's yaml shape).
- [ ] **Step 3:** Write `references/changelog-backtracking.md` — the changelog + ADR convention with a concrete example entry.
- [ ] **Step 4:** Write `starters/coding-principles/README.md` — short paste-in block referencing `skills/coding-principles/SKILL.md` for AGENTS.md/CLAUDE.md/GEMINI.md.
- [ ] **Step 5 (verify):** `SKILL.md` frontmatter parses (name+description present); no TODO/TBD placeholders (`grep -rin 'TODO\|TBD' skills/coding-principles`).
- [ ] **Step 6:** Commit `feat: add coding-principles skill and starter`.

### Task A5: `structured-memory` skill (Omnigraph protocol)

**Files:**
- Create: `skills/structured-memory/SKILL.md`, `skills/structured-memory/references/schema.md`

- [ ] **Step 1:** Write `references/schema.md` — node types (`Project, Decision, Rule, Preference, Convention, Component, Task`) and edges (`decided-in, constrains, supersedes, part-of, applies-to`), with an Omnigraph `.pg`-style schema block and a JSONL ingest example.
- [ ] **Step 2:** Write `SKILL.md` — the protocol: **session start** = query project subgraph (traversal+vector+full-text) for rules/decisions/preferences; **on durable decision / session end** = write typed nodes on a per-project branch, merge on confirm; **cross-project** = shared graph, project-scoped subgraphs, global preferences at root. Include the exact `@modernrelay/omnigraph-mcp` tool names (`schema, branches, queries, mutations, ingest`).
- [ ] **Step 3 (verify):** frontmatter parses; schema.md and SKILL.md cross-reference; no placeholders.
- [ ] **Step 4:** Commit `feat: add structured-memory skill for Omnigraph`.

### Task A6: Swap Mem0→Omnigraph in the infra stack (Mem0 as fallback profile)

**Files:**
- Modify: `infra/mcp-servers/docker-compose.yml`, `infra/mcp-servers/config/mcp-claude-code.json`, `infra/mcp-servers/config/mcp.json`, `infra/mcp-servers/config/mcp_antigravity.json`, `infra/mcp-servers/.env.example`
- Move: `infra/mcp-servers/servers/mem0-*` → `infra/mcp-servers/servers/_fallback/`
- Create: `infra/mcp-servers/servers/omnigraph/README.md`

- [ ] **Step 1:** Add `omnigraph-server` + `minio` services to `docker-compose.yml` as the default stack (named volumes `omnigraph-data`, `minio-data`; bind to `127.0.0.1`; healthchecks). Put the three `mem0-*` services under `profiles: ["mem0-fallback"]` so they never start by default.
- [ ] **Step 2:** `git mv` the `servers/mem0-*` dirs under `servers/_fallback/`; write `servers/omnigraph/README.md` (build/run + `@modernrelay/omnigraph-mcp` registration + handshake test).
- [ ] **Step 3:** In the three MCP config JSONs, remove Mem0 from the active set and register `omnigraph` (stdio `@modernrelay/omnigraph-mcp` pointing at the local server URL + bearer token from env); keep a commented Mem0 fallback entry.
- [ ] **Step 4:** Add Omnigraph/MinIO env keys to `.env.example` (`OMNIGRAPH_URL`, `OMNIGRAPH_TOKEN`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `S3_BUCKET`).
- [ ] **Step 5 (verify — static only):** `docker compose -f infra/mcp-servers/docker-compose.yml config` parses; `--profile mem0-fallback` is required to render the mem0 services (`docker compose config` without it omits them); the three JSONs are valid JSON (`python -m json.tool`).
- [ ] **Step 6:** Commit `feat: default MCP stack to Omnigraph+MinIO, Mem0 as fallback profile`.

### Task A7: Herdr remote-access docs (Linux + Windows)

**Files:**
- Create: `infra/remote-access/herdr/README.md`, `infra/remote-access/herdr/scripts/start-session.sh`, `infra/remote-access/herdr/scripts/start-session.ps1`
- Create: `infra/remote-access/README.md` (comparison table)

- [ ] **Step 1:** Write `herdr/README.md` — install on Linux/macOS (curl/brew/mise) **and Windows** (beta binary + WSL note); start/detach/reattach-over-SSH (incl. phone); agent-to-agent socket API; when to use Herdr vs tmux vs antigravity-remote-ui.
- [ ] **Step 2:** Write the two helper scripts (bash + PowerShell) that launch a named persistent Herdr session with N agent panes.
- [ ] **Step 3:** Write `infra/remote-access/README.md` with the Herdr-vs-tmux-vs-antigravity comparison table.
- [ ] **Step 4 (verify):** `bash -n start-session.sh` parses; PowerShell script has no syntax error (`pwsh -NoProfile -Command "Get-Command -Syntax"` if pwsh present, else visual check); no placeholders.
- [ ] **Step 5:** Commit `feat: add Herdr multiplexer setup (Linux+Windows)`.

### Task A8: ADRs, README rewrite, thin root instructions, CHANGELOG

**Files:**
- Create: `docs/decisions/0001-omnigraph-over-mem0.md`, `docs/decisions/0002-herdr-multiplexer.md`, `CHANGELOG.md`, `GEMINI.md`
- Modify: `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md` (new consolidated), `mcp-servers-setup` skill refs

- [ ] **Step 1:** Write the two ADRs (context / decision / consequences / fallback switch-criteria for 0001).
- [ ] **Step 2:** Rewrite `README.md` to summarize the whole repo: three pillars, skill list, infra quick-start (Omnigraph default + Mem0 fallback), remote-access options (Herdr + antigravity), links into each area, and the new tree.
- [ ] **Step 3:** Reduce `AGENTS.md`/`CLAUDE.md` to thin pointers; add matching `GEMINI.md`; each references `skills/coding-principles/`, `skills/structured-memory/`, `skills/mcp-servers-setup/`, `skills/html-working-documents/`.
- [ ] **Step 4:** Update `skills/mcp-servers-setup/SKILL.md` to replace Mem0 activation with Omnigraph structured-memory activation (+ point at the structured-memory skill); create `CHANGELOG.md` with the restructure entry; create consolidated `docs/architecture.md`.
- [ ] **Step 5 (verify):** run a relative-link check — `grep -rInoE '\]\(([^)]+)\)' README.md docs skills starters infra | ...` resolve each local path exists; no references to old dirs (`grep -rIn 'mcp-servers/\|antigravity-remote-ui/\|^Gen/\|repositories/' README.md AGENTS.md CLAUDE.md` returns only new `infra/...` paths).
- [ ] **Step 6:** Commit `docs: rewrite README, add ADRs+CHANGELOG, thin root instructions`.

### Task A9: Phase-A verification + finish branch

- [ ] **Step 1 (verify):** full-tree personal-path/secret scan returns nothing: `grep -rInE '<you>|C:\\\\Users|/home/<you>/' --exclude-dir=.git .`
- [ ] **Step 2 (verify):** `git log --oneline --stat` shows renames preserved history (R status on moved files).
- [ ] **Step 3:** Invoke `superpowers:requesting-code-review` on the branch diff; address findings.
- [ ] **Step 4:** Present branch for user review; on approval, use `superpowers:finishing-a-development-branch` to merge/PR.

---

## PHASE B — Live deployment + sibling-repo updates (CHECKPOINTED)

> Every task here touches live infra or other repos. STOP and confirm with the user before Task B1, B4, and B5.

### Task B1: Bring up Omnigraph + MinIO locally  **[CHECKPOINT before `up`]**

**Files:** `infra/mcp-servers/.env` (local, untracked), runtime only.

- [ ] **Step 1:** Copy `.env.example`→`.env`; fill MinIO creds + `OMNIGRAPH_TOKEN` (generated), set `S3_BUCKET`.
- [ ] **Step 2:** `docker compose -f infra/mcp-servers/docker-compose.yml up -d omnigraph-server minio`
- [ ] **Step 3 (verify):** `docker compose ps` shows both healthy; `curl -fsS http://127.0.0.1:<omnigraph-port>/health` OK; MinIO console reachable; create the S3 bucket if not auto-created.
- [ ] **Step 4 (verify):** MCP handshake — run `@modernrelay/omnigraph-mcp` against the local server and confirm it lists tools (`schema, branches, queries, mutations, ingest`).

### Task B2: Define the structured-memory schema in Omnigraph

- [ ] **Step 1:** Apply the `structured-memory` schema (Task A5 `schema.md`) to a `main` graph via the MCP `schema`/`mutations` tools.
- [ ] **Step 2 (verify):** `queries` returns the registered node/edge types.

### Task B3: Seed graphs for `homelab-server` and `agent-skills` from Serena + Mem0 + docs

**Source knowledge:** Serena project memories, existing Mem0 memories (`user_id=homelab-server` for Server; repo-folder names for others), and README/CLAUDE/docs in each repo.

- [ ] **Step 1:** For `agent-skills`: read Serena memories + this repo's docs; write `Project(agent-skills)` plus `Decision`/`Rule`/`Convention` nodes capturing the restructure decisions (Omnigraph-over-Mem0, Herdr, thin instructions) via MCP `ingest`.
- [ ] **Step 2:** For `homelab-server`: export existing Mem0 memories (`GET /memories?user_id=homelab-server` against the running Mem0 API, started via `--profile mem0-fallback` if needed) + read `homelab-server/CLAUDE.md`, `Server/README.md`, `Server/docs`, `Server/server/network/*`; transform into typed nodes (`Project(homelab-server)`, `Component` per service, `Rule`/`Convention` for edge/security model) and `ingest`.
- [ ] **Step 3 (verify):** graph queries for each project return the expected node counts; a vector+graph query ("how is the reverse proxy exposed?") returns the Caddy/OPNsense nodes.
- [ ] **Step 4:** Record seed provenance in `docs/decisions/` (which memories/docs were imported).

### Task B4: Repoint `homelab-server` + `finance-repo` off Mem0  **[CHECKPOINT — other repos]**

**Files:**
- Modify: `homelab-server/CLAUDE.md` (§"Memory — mem0 only", TODO.md line 116)
- Modify: `finance-repo/CLAUDE.md`, `finance-repo/AGENTS.md` (Mem0 table rows, verify block, "Mem0 Tools" sections)

- [ ] **Step 1:** In each file, replace the Mem0 memory sections with the Omnigraph structured-memory protocol (point at `agent-skills/skills/structured-memory/SKILL.md`; use project-scoped subgraph instead of `user_id`). Keep each repo's own branch; do not touch unrelated content.
- [ ] **Step 2 (verify):** `grep -rin mem0 Server finance-repo` returns only historical/CHANGELOG mentions, not active instructions; each file still validates as the agent's instruction format.
- [ ] **Step 3:** Commit on each repo's own branch (`chore: switch memory layer from Mem0 to Omnigraph structured-memory`).

### Task B5: Expose Omnigraph via OPNsense/Caddy reverse proxy  **[CHECKPOINT — internet exposure]**

**Files (in `homelab-server` repo):**
- Modify: `server/network/opnsense/caddy.d/10-services.conf` (+ mirror in `server/network/caddy/Caddyfile`)
- Reference: `server/network/opnsense/REVERSE-PROXY-AND-SECURITY.md`, `AUTHELIA-SSO.md`, Unbound `*.vm` overrides

- [ ] **Step 1:** Read `REVERSE-PROXY-AND-SECURITY.md` + `AUTHELIA-SSO.md` to confirm the current snippet names and the Authelia-protect pattern.
- [ ] **Step 2:** Add an `omnigraph.example.org` vhost pointing to `coding.example.internal:<omnigraph-port>`, wrapped in the `authelia` + `secure_headers` snippets (SSO-gated; no anonymous internet access to the memory API). Add the matching Unbound `coding.example.internal` override if the port/host is new. Mirror the entry 1:1 into `server/network/caddy/Caddyfile`.
- [ ] **Step 3 (verify):** From LAN, `curl -H` through Caddy resolves to Omnigraph behind Authelia (302 to SSO when unauthenticated); after SSO, `/health` OK. Confirm OPNsense firewall/port-forwards were NOT changed (only a vhost added).
- [ ] **Step 4:** Commit on `homelab-server`'s branch (`feat(caddy): expose omnigraph memory API behind Authelia`).

### Task B6: End-to-end verification

- [ ] **Step 1 (verify):** From a second machine/agent, connect the `omnigraph` MCP to the public `omnigraph.example.org` endpoint (through SSO/bearer) and run a cross-project memory query.
- [ ] **Step 2 (verify):** Open a fresh session in `homelab-server` and in `finance-repo`; confirm the agent loads structured memory from Omnigraph (no Mem0 calls) per the updated instructions.
- [ ] **Step 3:** Update `CHANGELOG.md` in `agent-skills` and `homelab-server`; record the deployment in `docs/decisions/`.

---

## Self-Review (against the spec)

- **Spec coverage:** three pillars (A2/A4/A5/A7), Omnigraph-over-Mem0 + fallback (A6, B1–B4), coding-principles (A4), structured-memory protocol (A5, B2–B3), Herdr Linux+Windows (A7), README summary + thin instructions (A8), de-personalization (A3), graphify-out untracked (A1), reverse-proxy exposure (B5) — all mapped. ✅
- **Placeholders:** none — `<omnigraph-port>` and `<port>` are runtime values resolved at B1 (documented), not plan gaps.
- **Naming consistency:** `mem0-fallback` profile, `${AGENT_SKILLS_ROOT}`/`${CODE_ROOT}`, node/edge type names, and file paths are used identically across tasks. ✅
