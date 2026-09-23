# GEMINI.md

The working rules for this repository live in **[AGENTS.md](AGENTS.md)**. Read that file first;
it is the canonical instruction set for every agent, not a summary of one.
This file is *only* the Gemini/Antigravity delta. Start at the router:
`.agents/skills/repository-index/SKILL.md`.

## Antigravity notes

- Antigravity reads its **global** MCP config, so unlike a project-scoped
  `.mcp.json` it cannot pin a per-repo graph. **Always set
  `OMNIGRAPH_GRAPH_ID` to this repository's folder name before launching or
  writing memory**, or you read and write the wrong graph — silently, with no
  error. **Never write project memory to the `memory` graph.**
- Which name is right? Ask the server, not this file:
  `curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://localhost:8080/graphs`
  Then confirm with the `whoami` query in `CLAUDE.md` — `Project.repository`
  must equal `git remote get-url origin`.
- Token-only, all-or-nothing: no header ⇒ 401; missing grant ⇒ 403.
- Before changing a design choice, check for a `docs/decisions/` equivalent;
  before building something, check `.agents/skills/` — it may already exist.

## Scratch hygiene

- **Never** create scratch scripts in random locations or the repo root.
- Use the session scratch directory or the repository's `scratch/` folder,
  and delete throwaway scripts when finished.
- Project skills live in `.agents/skills/` (see `AGENTS.md` §8).
