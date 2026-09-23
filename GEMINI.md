# Gemini CLI Instructions

**Read [AGENTS.md](AGENTS.md) first** — skills, memory model, env vars, hard rules.
This file is *only* the Gemini/Antigravity delta. Start at the router:
`skills/repository-index/SKILL.md`.

## Antigravity notes

- **Config:** `infra/mcp-servers/config/mcp_antigravity.json`. Antigravity reads its global
  `mcp_config.json` — location and transport in `infra/mcp-servers/README.md`.
- **That config is GLOBAL**, so unlike a project-scoped `.mcp.json` it cannot pin a per-repo
  graph. **Always set `OMNIGRAPH_GRAPH_ID` to the active repository folder name (e.g. `agent-skills`, `sibling-analysis-repo`) before launching or writing memory**, or you read and write the wrong graph — silently, with no error. **Never write or push project memory to the `memory` graph/branch.**
- **Which folder name is right? Ask the server, not this file.** The graph id, the repository
  folder name and `Project.slug` are the same string by construction, so one call lists every
  valid value — and because Antigravity's MCP config is global, the HTTP form is the reliable
  one here:

  ```bash
  curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://localhost:8080/graphs
  ```

  Then confirm the pin is right before trusting recall — `Project.repository` in the graph you
  are connected to must equal `git remote get-url origin`:

  ```
  query whoami() { match { $p: Project } return { $p.slug, $p.name, $p.repository } }
  ```

  **Token-only, and all-or-nothing.** No header ⇒ 401; missing the cluster `graph_list` grant
  ⇒ 403, never an empty list; `/healthz` is the only open endpoint and carries no names. One
  token = one actor = that actor's graphs; never share it. Details:
  `skills/mcp-servers-setup/SKILL.md` → *Discovering repository / project names*.
- `.agents/AGENTS.md` in a target repo may define swarm roles (`@architect` / `@engineer` /
  `@reviewer`). `skills/swarm-orchestration/SKILL.md` is the source of truth; keep that file
  a pointer. Thresholds and model tiers are **not** in the skill — they live in
  `skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml`.
- Before changing a design choice read `docs/decisions/`; before building something read
  `docs/superpowers/specs/` — it may already be specified.

## Scratch Scripts & Filesystem Hygiene

- **NEVER create scratch scripts or query files in random repository locations or the root folder** (e.g., `scratch_query.py`).
- Always write temporary scratch scripts inside connected/designated scratch folders:
  - Antigravity session scratch: `<appDataDir>/brain/<conversation-id>/scratch/`
  - Repository scratch folder: `scratch/` or `.gemini/scratch/`
- Delete throwaway scripts when finished or keep them inside the designated scratch folder.
