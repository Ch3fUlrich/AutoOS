# Design — Graphify: single user-scope entry (client) + Docker (server)

- **Date:** 2026-07-20
- **Status:** Draft for review
- **Scope:** `agent-skills` (template/guide repo) + the graphify wiring it documents for all sibling repos
- **Supersedes:** the 2026-07-19 convention "graphify belongs in project scope; keep user scope empty of it" (`mcp-claude-code.json` template comment, `mcp-servers-setup/SKILL.md`, `CLAUDE.md` precedence section, `check-graphify-scope.*`)

## Problem

Graphify is currently wired **per repo**: every repository gets a project-scoped
`graphify-docker` entry in its `.mcp.json`, mounting that one repo, plus an approval in
`.claude/settings.local.json`, and the `check-graphify-scope.*` scripts enforce this on
every repo. This produces one Docker container definition per repo and a lot of duplicated,
easily-drifted config. We want a single graphify definition per machine that just serves
whatever repo you're working in — Docker on a server, a lightweight local runner on a
workstation.

## Two constraints that shape the solution

1. **There is no npx graphify.** The npm package named `graphify` is an unrelated jQuery
   "Random Graph Generator". The real tool ships only as the Python package
   `graphifyy[mcp]` (upstream `github.com/safishamsi/graphify`), run as
   `python -m graphify.serve <graph.json>`. The lightweight local runner is therefore
   **uv/Python**, not Node.
2. **`graphify.serve` is stdio-only** — no HTTP/SSE transport. There is no shared graphify
   daemon; every MCP client spawns its own subprocess per session that reads a static
   `graph.json`. "A single instance" is achievable only as a **single config entry**, not a
   shared running service.

A stdio subprocess inherits the launch directory, so **one entry with the relative path
`graphify-out/graph.json` serves whichever repo Claude Code was started in.** This was
verified empirically: the existing user-scope entry served `agent-skills`' graph from that
repo's cwd. The bug the previous convention correctly fought was a *different* entry — a
Docker one with a **hardcoded** `-w /home/<you>/code/agent-skills` mount, which served one repo's
graph to all. **Fixed path = bug; relative path = correct per repo.** The prior docs
conflated the two.

## Goals

- One graphify definition per machine, resolving the current repo's graph by cwd.
- No graphify entry in any project `.mcp.json`.
- Docker path retained and documented for **server** setups.
- `agent-skills` documents the client/workstation model as *the* way, in README + docs +
  agent-instruction files, because it is the template/guide other repos follow.

## Non-goals

- **Omnigraph is unchanged.** It is genuinely per-repo (pinned by `OMNIGRAPH_GRAPH_ID`) and
  stays project-scoped. The checker inversion below touches only its graphify half.
- **Graph *building* is unchanged.** Each repo still has its own gitignored
  `graphify-out/graph.json`, built by `init-graphify-projects.*` / `graphify update` and kept
  fresh by graphify's git hooks. Only the *server wiring* becomes single and uniform.

## Design

### The model

Graphify is defined **once, in user scope** (`~/.claude.json`), cwd-relative:

- **Client / workstation** (decided: uv, keep current):
  ```json
  "graphify": {
    "command": "uv",
    "args": ["run","--with","graphifyy[mcp]","python","-m","graphify.serve","graphify-out/graph.json"]
  }
  ```
  No Docker, no per-repo config. Resolves `<cwd>/graphify-out/graph.json` each session.

- **Server** (Docker retained): a single cwd-driven entry via a thin `graphify-mcp` wrapper on
  `PATH`, because MCP args are not shell-expanded so a raw Docker entry cannot inject `$PWD`:
  ```sh
  #!/bin/sh
  exec docker run -i --rm -v "$PWD:/repo" graphify-mcp:latest
  ```
  registered as `"graphify": { "command": "graphify-mcp" }`. Still one entry, still
  cwd-driven. (If the server has uv/Python, the identical uv entry works with no wrapper —
  see Open Questions re: coding.example.internal.)

### Why the failure mode cannot recur

- The old bug was a **fixed-path** entry serving one graph to every repo. A **cwd-relative**
  entry serves each repo its own graph.
- With **nothing** in project scope, the user-vs-project same-name precedence trap (the
  2026-07-17 omnigraph incident) is impossible for graphify — there is exactly one definition.

## Affected surface

**Wiring (this workstation):**
- `agent-skills/.mcp.json` — remove the `graphify-docker` server + its `$comment` block.
- `agent-skills/.claude/settings.local.json` — drop `graphify-docker` from
  `enabledMcpjsonServers` (untracked; not committed).
- `~/.claude.json` — keep the existing user-scope `graphify` uv entry (already correct).
- finance-repo / Server / sibling-analysis-repo: no project graphify entry today → no wiring change.

**Docs to rewrite (agent-skills as template/guide):**
- `skills/mcp-servers-setup/SKILL.md` — replace the "per-repo, three gates" graphify section
  with the single-entry client model + Docker server model.
- `infra/mcp-servers/config/mcp-claude-code.json` — invert the "DO NOT register graphify in
  user scope" comment to "graphify **is** a single user-scope entry (client) / Docker wrapper
  (server); omnigraph stays project-scoped."
- `CLAUDE.md` — the MCP-precedence section: graphify is no longer a precedence hazard (single
  definition); keep the omnigraph precedence warning intact.
- `infra/mcp-servers/README.md` — client/server graphify guidance.
- `infra/mcp-servers/servers/graphify-mcp/README.md` — clarify the image is the **server**
  path (+ the wrapper), not per-repo project entries.
- `skills/repository-index/SKILL.md` — router row note (graphify wiring pointer).

**Scripts to invert (graphify half only):**
- `infra/mcp-servers/scripts/{windows/check-graphify-scope.ps1,linux/check-graphify-scope.sh}`:
  - USER scope: a cwd-relative `graphify` entry is now **required/OK**; flag a *hardcoded-path*
    graphify entry as bad. Keep flagging user-scope **omnigraph** as bad (unchanged).
  - Project scope: assert **no** graphify entry (was: require `graphify-docker`). Drop the
    gate-2 graphify approval logic. Leave omnigraph project-scope checks intact.
  - Gate "graph built" (`graphify-out/graph.json`) — unchanged.

**Already aligned (no change):**
- `sibling-analysis-repo/AGENTS.md`, `sibling-analysis-repo/CLAUDE.md` — the graphify docs added 2026-07-20
  already describe the user-scope, cwd-relative model.

## Verification

- Real MCP handshake from two different repos with the single user-scope entry: each returns
  its **own** repo's `god_nodes` (the check that caught the original bug).
- `check-graphify-scope.*` after inversion: green on a correctly-wired machine; `-Fix`/`--fix`
  removes stray project graphify entries and does **not** re-add them.
- A repo with no `graphify-out/graph.json` degrades cleanly (server starts, serves nothing) —
  no crash, no wrong-graph.

## Risks & mitigations

- **cwd assumption:** the model relies on the MCP client spawning the server with cwd = repo
  root. True for Claude Code (verified this session). Documented as an explicit assumption; the
  Docker server wrapper shares it via `$PWD`.
- **Reversing a 3-day-old convention:** stale references cause confusion. Mitigated by doing
  docs + scripts in the same change (full refactor) so nothing enforces the old model.

## Resolved decisions (2026-07-20)

1. **coding.example.internal runner:** Docker (it is a server). It uses the `graphify-mcp` cwd wrapper, not
   the uv entry.
2. **Wrapper location:** tracked at `infra/mcp-servers/bin/graphify-mcp` (symlinked onto the
   server's `PATH`, e.g. `/usr/local/bin/`).
3. **Post-refactor:** rebuild the `agent-skills` graph (`graphify update .`).
