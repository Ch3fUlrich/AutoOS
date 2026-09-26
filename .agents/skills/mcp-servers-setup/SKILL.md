---
name: mcp-servers-setup
description: Configure and use the self-hosted MCP server stack (Serena, Graphify, Omnigraph memory, Superpowers, Playwright) for token-efficient coding, and publish container images to the private Harbor registry.
---

# MCP Servers Setup

This skill ensures proper configuration and usage of the self-hosted MCP server
stack across all repositories under your `${CODE_ROOT}` (the parent directory of
your projects).

## First Action — Always

**On every session start (first prompt), activate the Serena MCP server, recall
structured project memory from Omnigraph, and use Graphify when the repo already
has a graph:**

```
mcp_serena_activate_project(project="<ABSOLUTE path to the repo root>")   # never a bare name
# Recall typed memory (rules/decisions/preferences) for this project + global scope:
#   follow skills/structured-memory/SKILL.md (Omnigraph queries)

# If graphify-out/graph.json exists, prefer graph queries for broad structure questions
```

This loads the full project context: module structure, recent decisions,
commands, and constraints. Do this before any code changes — the memory graph is
the ground truth for what exists and how it works. Memory details:
`skills/structured-memory/SKILL.md`.

## Active MCP Servers

### Serena — Semantic Code Navigation (LSP)

`find_symbol` (90%+ vs file read), `find_referencing_symbols` (80%+), `get_symbols_overview`
(95%+), `find_declaration` (90%+). Activate by **absolute path**, then use symbolic tools:

```
mcp_serena_activate_project(project="C:/Users/<you>/Documents/Code/agent-skills")
mcp_serena_find_symbol(name_path_pattern="function_name")
```

#### Wiring — user scope only. Serena is graphify-shaped, not omnigraph-shaped

**Define Serena once in user scope; never add a per-repo project-scope entry.** It is selected by
*path* at call time and its per-repo config is `.serena/project.yml`, which is tracked — a
project-scope entry pins nothing the tracked file does not already say.

**Do not carry omnigraph's rule across.** Omnigraph *is* per-repo (pinned by
`OMNIGRAPH_GRAPH_ID`) because each repo is a distinct graph on a shared server. Serena, like
graphify, is selected at runtime.

**A duplicate does not resolve to one winner — both run.** Measured in `second-analysis-repo` 2026-09-01:
**five** Serena processes, one with `--context claude-code` (user scope) and four without
(project scope, one per session). Calls landed on a *project-scope* one, reporting `Active
context: desktop-app` and a one-project roster where the real home registers eleven. The
`desktop-app` context re-adds the tools `claude-code` exists to strip, so their schemas are paid
for twice.

> **Every path in an MCP `env` block must be absolute.** Claude Code expands `${VAR}` but **not**
> a leading `~`, so `"SERENA_HOME": "~/.serena"` is a *relative directory name*. Serena then
> builds a second home at `./~/.serena/` in the working tree: its own `serena_config.yml` (the
> global `excluded_tools` never applies, re-exposing `execute_shell_command` / `read_file` /
> `list_dir` / `search_for_pattern`), its own `logs/` where the real diagnostics go, and **237 MB**
> of partial `language_servers/` beside the 1,023 MB real home. No error is raised; it stayed
> invisible to `git status` only because that repo's `.gitignore` carried `~*`. Measured
> 2026-08-31 — it took Serena down completely.

> **"Activation succeeded" is not evidence Serena works.** `activate_project` reports every
> declared language as active **even when seven are dead**. You find out at the first symbol call:
> `The language server manager is not initialized`, naming neither the language nor the cause.
> Run the health-check instead.

#### Languages — declare only what has a call graph

**Declare only programming languages the repo actually contains.** `json`, `yaml`, `toml` and
`markdown` are never candidates however many files match — a file count says a language is worth
*testing*, never that it belongs. The bar is not "does the server start"; they all start.

> **Never declare a language whose server is not in the image — `markdown` above all.** Its
> `initialize` fails, the language-server **manager** fails with it, and every symbol tool for
> the project then errors. The repo goes dark and the message names neither cause. `marksman` is
> not in the image, and headings are a grep. This took Finance-Repo's Serena down until 2026-07-20.
> **Restart Serena after editing `project.yml`** — it holds the activated project in memory.

Measured with `serena project health-check`, one set at a time, 2026-09-01:

| Declared | Symbol selected | `find_referencing_symbols` |
|---|---|---|
| `python`, `rust` | `_start_lifecycle_maintenance` — Function | **works** |
| `+ json` | `$comment` — from `.mcp.json` | **fails** |
| `+ toml` | `build-system` — from `pyproject.toml` | **fails** |
| `+ yaml` | `name` — String, 13 matches | **fails** |

Data-format servers flood the symbol namespace with config keys and none implements
`textDocument/references`. Startup went **5 s → 16 s** with all six declared. Serena agrees — its
`ls_priorities` comment calls `0` *"experimental or secondary … also applies to non-programming
languages like `json`"*.

#### Verify with `health-check` — the failure is invisible without it

```bash
SERENA_HOME=/absolute/path/to/.serena uvx --from serena-agent==1.7.0 serena project health-check .
```

Require **both** `Health check completed successfully` **and** a `FindReferencingSymbolsTool
found N references` line; a `failed for symbol` warning above a "successful" completion is the
data-format problem above. Two traps:

- **On Windows it ends in `UnicodeEncodeError`** printing a check mark under cp1252 — *after* the
  check finished. **Do not trust the exit code**: measured `1` on 2026-09-01 (serena 1.7.0). The
  log body is the verdict.
- **Check *which* symbol it selected.** Both criteria can pass on nothing: against
  `sibling-analysis-repo` (8 languages incl. `markdown`) it chose `AGENTS.md — sibling-analysis-repo`, kind
  **Namespace** — a markdown heading — reported 1 reference and success, and never touched
  Python. If the symbol is not a Function or Class, the language list is polluted. Startup
  there: **34.4 s**.

#### Worktrees and parallel agents — three non-negotiable rules

> **1. Activate by absolute path, never a bare name.** `get_registered_project` compares the
> argument against every registered `project_name` *before* looking at paths, raising `Multiple
> projects found` when two match (`serena/config/serena_config.py:928-945`).
> `.serena/project.yml` is tracked, so every worktree carries the *same* `project_name` — the
> second worktree turns by-name activation into a hard error **for every agent at once**, main
> checkout included. An absolute path can never equal a project name. Measured 2026-08-31: five
> worktrees, five registrations, one name.
>
> **1b. Better: omit `project_name` from the tracked `project.yml`.** A configuration fix beats a
> discipline rule because it holds however an agent activates. `serena_config.py:516-517` fills a
> missing key from the **folder name** in memory at load; the only write-back
> (`_migrate_out_of_project_config_file`, `:907-912`) touches a legacy out-of-project file, not
> `.serena/project.yml`. Every worktree then self-names by its own directory.
> *Caveat — verified by reading 1.7.0 source, not by running a second worktree.* Treat it as an
> addition to rule 1, never a replacement, until tested live.
>
> **2. One Serena = one session = one worktree.** Serena is a single stdio process per *session*
> holding one `_active_project`, and `_activate_project` **shuts the previous project's language
> servers down** before switching. In-session subagents share that process, so N agents in N
> worktrees means every activation kills the previous agent's LSP mid-flight. This is process
> architecture; no configuration fixes it.
>
> **The consequence is not that worktree agents lose Serena** — that conclusion was drawn here
> once and was wrong. **The unit of worktree parallelism is a SESSION, not a subagent.** One
> `claude` process per worktree gives each its own Serena, its own `_active_project` and —
> graphify being cwd-relative — its own graph. Subagents stay in their own session's worktree,
> sharing one correctly-activated Serena. *Worktrees get sessions; subagents share the session's
> worktree.* (`herdr-orchestration` panes are how you run one session per worktree.)
>
> Three artifacts must reach the worktree; all three fail identically, by being untracked:
>
> | Requirement | Why it bites | Fix |
> |---|---|---|
> | `.serena/project.yml` **tracked** | absent, Serena autogenerates it, and autogeneration picks languages by file count — reintroducing `json`/`markdown`/`yaml` in every worktree, forever | `.serena/*` plus `!.serena/project.yml`, never a bare `.serena/` (a negation cannot re-include a file inside an excluded directory) |
> | `project_name` **omitted** | every worktree registers the same name; by-name activation raises for all agents at once | delete the key (rule 1b) |
> | `graphify-out/` **exists** | gitignored, and both hooks skip linked worktrees, so it is never created | link it to the main checkout |
>
> ```bash
> ln -s "$(git rev-parse --show-toplevel)/graphify-out" "<worktree>/graphify-out"
> cmd /c mklink /J "<worktree>\graphify-out" "<main>\graphify-out"    :: Windows, no admin
> ```
>
> Link rather than build a second graph: the hooks maintain exactly one, and a hand-built copy
> goes stale silently. **Verified it cannot eat the real graph** (2026-09-01): with a canary in
> the target, `Remove-Item -Recurse -Force <worktree>` left it intact — the junction is not
> followed. Not tested against `git worktree remove` itself, so drop the link first if you want
> belt and braces. The server only *reads* `graph.json`, so N sessions cannot race. Accept the
> caveat: the graph describes the **main checkout's** code — right for structural questions,
> wrong for code the branch restructured. Say which one you are answering from.
>
> Only when a worktree genuinely cannot have its own session does the fallback apply:
> `Glob`/`Grep`/`Read` **and `Edit`** — name `Edit` explicitly, because a fallback list omitting
> the one tool that changes files reads as "I cannot work", and a repo that also fences shell
> edits to source turns that into a hard stop (measured 2026-09-01).
>
> **Know the price: N sessions = N Serena processes**, each holding its own language servers.
> Five were alive during the 2026-09-01 measurement, and a duplicate definition doubles the count
> for free.
>
> **3. A worktree holds tracked files only.** `.env`, `.venv/` and every gitignored operator file
> are absent *by construction*. Code resolving such a file relative to its own location
> (`Path(__file__).resolve().parents[n]`) finds nothing and, if written to degrade gracefully,
> **degrades silently**. Resolve untracked config through the main checkout — a linked worktree's
> `.git` is a *file* reading `gitdir: <main>/.git/worktrees/<name>`, whose `commondir` leads back
> to the shared `.git`, so the main root is two file reads away with no subprocess:
>
> ```python
> def main_worktree_root(root: Path) -> Path | None:
>     """Main checkout behind `root`, or None if `root` is not a linked worktree."""
>     dot_git = root / ".git"
>     if not dot_git.is_file():        # a directory is an ordinary checkout; absent is no repo
>         return None
>     try:
>         pointer = dot_git.read_text(encoding="utf-8").strip()
>         if not pointer.startswith("gitdir:"):
>             return None
>         # split on the FIRST colon only: a Windows pointer is `gitdir: C:/...`
>         gitdir = Path(pointer.split(":", 1)[1].strip())
>         if not gitdir.is_absolute():
>             gitdir = root / gitdir
>         commondir = (gitdir / "commondir").read_text(encoding="utf-8").strip()
>     except OSError:
>         return None
>     return (gitdir / commondir).resolve().parent
> ```
>
> Search the worktree's own locations first so a deliberate override still wins. Do **not** copy
> credentials per worktree: `git worktree prune` orphans them and they drift. Housekeeping: keep
> `.claude/worktrees/` in `.gitignore` (not `.git/info/exclude`, which no clone inherits), and
> drop removed worktrees from `~/.serena/serena_config.yml` -> `projects`.

> **Memory tools: off, globally (closed 2026-09-18).** Omnigraph is the only memory layer. Serena's
> `onboarding`, `write_memory`, `read_memory`, `list_memories`, `edit_memory`, `rename_memory` and
> `delete_memory` are in `excluded_tools` of the **global** `~/.serena/serena_config.yml`, beside the
> file tools the host already provides. `replace_in_files` (1.7's multi-file text replace) is excluded too, for the same reason. One home for the setting, so no `project.yml` needs it.
> Verified by starting `serena-agent==1.7.0 start-mcp-server --context claude-code` over stdio: 15
> tools, with no memory or onboarding tool and `find_symbol` present. An invalid name in
> `excluded_tools` can stop Serena starting and take **all** symbol tools down, so re-run that
> check after every Serena upgrade.

### Omnigraph — Structured Cross-Project Memory

The default memory layer. Instead of unstructured blobs, write **typed** nodes
(`Decision / Rule / Preference / Convention / Component / Task`) edged to a
`Project`, and recall them via fused graph + vector + full-text queries. MCP tools:
`schema / branches / queries / mutations / ingest`.

| Action | How |
|--------|-----|
| Recall project memory | `query` for rules/decisions/preferences edged to the project |
| Persist a durable fact | `mutate` (a few nodes) or `load` `mode: merge` (bulk), on a branch, then merge |
| Global preferences | `Preference` nodes with `scope: global`, in the shared `memory` graph |

Full protocol and schema: `skills/structured-memory/SKILL.md`. **Each repo has its
own graph** named after the repo folder (`OMNIGRAPH_GRAPH_ID=<repo>`); the shared
`memory` graph holds only global-scope `Preference`s. Isolation is by graph, plus
`Project` edges inside it — never a per-call `user_id`. Omnigraph is the only memory
layer; there is no fallback (ADR 0003).

**Discovering repository / project names — token-gated, and the token *is* the access model.**

Graph id == repository folder name == `Project.slug`, by construction, so "which graphs
exist" and "which projects exist" are one question. `cluster.yaml` is **not** the answer:
it is gitignored and purged from history precisely because it had become a roster of every
private project. Ask the live cluster.

| You want | MCP tool | HTTP equivalent |
|---|---|---|
| Every repository / project name | `graphs_list` | `curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://localhost:8080/graphs` |
| This repo's own record (name + clone URL) | `query whoami() { match { $p: Project } return { $p.slug, $p.name, $p.repository } }` | `POST /graphs/<id>/query` with the same header |

**A bearer token is the only key.** Measured against the live server on 2026-08-31:

| Request | Result |
|---|---|
| no `Authorization` header | **401** |
| wrong token | **401** |
| valid `OMNIGRAPH_TOKEN` | 200 |
| `GET /healthz` | 200 — the only open endpoint, and it returns liveness alone: no names, no data |

`/graphs` is closed by default. It answers at all only because the `cluster-admin` bundle
(`cluster/cluster.policy.yaml`, `applies_to: [cluster]`) grants the `graph_list` action to
the `operators` group. Without that grant the *authenticated* call returns **403
ForbiddenError** — not an empty list, so you cannot mistake "not allowed" for "nothing here".

**Scope — one token, one actor, that actor's graphs.** The token resolves to the single
actor `default` in group `operators`; `project-graphs.policy.yaml` grants it the per-project
graphs and `memory.policy.yaml` grants it `memory`. A holder therefore sees exactly the
graphs that belong to them — today that is all of them, because there is one user. **Treat
it as all-or-nothing: never hand this token to anyone whose data is not already in these
graphs.** Per-user scoping is written but switched off — `cluster/users.policy.yaml.example`
plus the commented `users-access` bundle in `cluster.yaml`. Turning it on needs one bearer
token per user and a `scripts/apply-cluster.sh` run; until then there is no way to give
someone a token that reveals only *part* of the roster.

> An unset `OMNIGRAPH_TOKEN` does **not** surface as "denied" through the MCP bridge — it
> surfaces as `missing bearer token`, and a graph that looks empty is a config bug until
> proven otherwise. Diagnose with `omnigraph-setup/setup-agent-memory.ps1 -Check` (or `.sh --check`).

### Graphify — Project Graphs

| Tool | Purpose |
|------|---------|
| `graphify query` | Ask graph-level questions about the repo |
| `graphify path` | Trace relationships between concepts |
| `graphify explain` | Inspect a node or community in graph terms |

**Usage**:
```
graphify query "what connects the CLI setup to the MCP config?"
graphify path "apply-cluster.sh" "cluster.yaml"
```

**Rule**: Graphify does not replace Serena. Use Serena for symbol-level navigation and Graphify for broader relationship questions when a graph exists.

#### Wiring — ONE definition per machine, cwd-relative

Graphify is **stdio-only** (no shared daemon) and reads a static `graphify-out/graph.json`.
A stdio server inherits its launch directory, so **one entry with the *relative* path
`graphify-out/graph.json` serves whichever repo you started Claude Code in.** Define it
**once, in user scope** — never per repo.

There is **no npx graphify**: the npm package by that name is an unrelated jQuery toy. The
tool is Python-only (`graphifyy[mcp]`, upstream `github.com/safishamsi/graphify`), served as
`python -m graphify.serve <graph.json>`.

- **Workstation / client — this is the way.** A single user-scope entry in `~/.claude.json`:
  ```jsonc
  "graphify": {
    "command": "uv",
    "args": ["run","--with","graphifyy[mcp]","python","-m","graphify.serve","graphify-out/graph.json"]
  }
  ```
  No Docker, no per-repo `.mcp.json` entry, no approval step. `uv` resolves from cache
  (~sub-second warm); a global `pipx install graphifyy[mcp]` + `"command": "python"` is an
  option if you want to shave cold-start.

- **Server** (e.g. `coding.example.internal` — runs graphify via Docker). MCP args aren't shell-expanded, so
  a raw entry can't inject `$PWD`; the tracked cwd wrapper
  [`infra/mcp-servers/bin/graphify-mcp`](../../infra/mcp-servers/bin/graphify-mcp) does it:
  ```sh
  exec docker run -i --rm -v "$PWD:/repo" graphify-mcp:latest "$@"
  ```
  Setup: build the image (`docker build -t graphify-mcp:latest servers/graphify-mcp`), put the
  wrapper on `PATH` (`ln -s "$PWD/infra/mcp-servers/bin/graphify-mcp" /usr/local/bin/`), then
  register the one user-scope entry `"graphify": { "command": "graphify-mcp" }`.
  If graphify reports **"Failed to connect — Connection closed"**, either the image
  isn't built or its `mcp` SDK resolved to 2.x (which dropped `mcp.types.AnyUrl`, an
  import graphify 0.9.20 makes at startup). The Dockerfile pins `mcp<2` to prevent the
  latter — rebuild the image (`docker build -t graphify-mcp:latest servers/graphify-mcp`)
  to pick up the pin.

**Never give graphify a per-repo project-scope entry.** A cwd-relative single definition
already serves each repo its own graph; a **hardcoded** per-repo mount is exactly what made
one repo's graph answer for every repo (the retired `graphify-docker`, 2026-07-20). With
graphify defined in only one place, the user-vs-project same-name precedence trap cannot
arise for it at all.

**Contrast with omnigraph — do not apply one's rule to the other.** Omnigraph *is*
per-repo/project-scoped (pinned by `OMNIGRAPH_GRAPH_ID`) because each repo is a distinct graph
on a shared server. Graphify is a local static file selected by cwd, so it needs no per-repo
pin and belongs in user scope.

**`enabledMcpjsonServers` never lists graphify — by design, not by omission.** That array
approves *project-scoped* servers declared in a repo's `.mcp.json`; user-scope servers need no
approval because you configured them yourself. Graphify is user-scope only, so it appears in no
`.mcp.json` and there is nothing to approve. A graphify entry in a repo's
`.claude/settings.local.json` is therefore a **defect**, not configuration —
`scripts/{linux,windows}/check-graphify-scope.*` flags it and `--fix` removes it, deliberately
checking for it independently of the server entry, because an approval outlives the server it
approved.

**Worktrees: graphify is a main-checkout tool, and outside one it fails quietly.**
`graphify-out/` is gitignored, so — exactly like `.env` in the Serena worktree rules — a linked
worktree never has one. Two ways that bites, neither of which errors:

- **Session launched *in* the worktree.** `graphify.serve` is stdio and cwd-relative, so it
  looks for `<worktree>/graphify-out/graph.json`, which has never existed there.
- **Session launched in the main checkout while an agent works in a worktree.** A stdio server
  inherits its launch directory *once*, at start, so it keeps answering from the main
  checkout's graph — built from a different branch's code. Confident answers about the wrong
  tree.

**There is no rebuild spillover, though.** Both installed hooks refuse to run in a linked
worktree: `post-commit` and `post-checkout` each compare `git rev-parse --git-dir` against
`--git-common-dir` and `exit 0` when they differ, so N worktrees never produce N rebuilds or N
competing graphs. `post-checkout` also exits when `graphify-out/` is absent, which is always
true in a worktree. Confirmed by reading the installed hooks, 2026-09-01.

So the rule matches Serena's: **in a worktree, treat graphify as unavailable and say so** — the
escape hatch that is not silent. Do **not** hand-build a graph inside a worktree to work around
it; the hooks will not maintain it, so it goes stale with nothing to tell you.

Only the graph **file** is per-repo. Build it (no LLM/API key needed) and keep it fresh:
```bash
uv run --with graphifyy[mcp] graphify update .   # workstation; or init-graphify-projects.*
# server without uv:
docker run --rm -v "$PWD:/repo" -w /repo --entrypoint python graphify-mcp:latest -m graphify update .
```

Verify wiring — single user-scope entry present, **no** project graphify entries, graph built
(`--fix`/`-Fix` applies the safe repairs; both exit non-zero so they work in hooks/CI):
```bash
bash infra/mcp-servers/scripts/linux/check-graphify-scope.sh --fix          # Linux/WSL
```
```powershell
.\infra\mcp-servers\scripts\windows\check-graphify-scope.ps1 -Fix           # Windows
```

### Superpowers — Workflow Skills (14 skills)

| Skill | When to Use |
|-------|------------|
| `systematic-debugging` | Any bug, test failure, unexpected behavior |
| `test-driven-development` | Before writing implementation code |
| `brainstorming` | Before creative work, features, design |
| `writing-plans` | Multi-step tasks with specs |
| `requesting-code-review` | Before merging |
| `subagent-driven-development` | Independent parallel tasks |
| `verification-before-completion` | Before claiming work is done |

**Usage**:
```
mcp_superpowers_use_skill(name="systematic-debugging")
mcp_superpowers_recommend_skills(task="debug a timeout issue")
```

### Playwright — Browser Automation (UI validation, E2E)

Strictly for UI validation, screenshot testing, and E2E browser tasks against sites
you build — **not** a web-search tool. Exposes 24 tools (`browser_navigate`,
`browser_snapshot`, `browser_click`, `browser_take_screenshot`,
`browser_console_messages`, `browser_network_requests`, `browser_fill_form`, …);
screenshots return inline in the tool response, so no output volume is needed.

#### Wiring — ONE user-scope entry, serves every repo. Transport differs per host.

Defined **once in user scope** (like graphify) so it works from any repo Claude Code
launches in — no per-repo `.mcp.json` entry, no approval step. Which transport
depends on whether the host has node:

**Workstation (node present) — a global install, never `npx @latest`.**

```bash
npm i -g @playwright/mcp
claude mcp add -s user playwright -- \
  node "$(npm root -g)/@playwright/mcp/cli.js"
```

> **Never `npx @playwright/mcp@latest`.** The `@latest` tag forces a registry
> resolve on **every spawn**, and every Claude Code session spawns its own copy.
> Measured 2026-09-04: `npx @latest` **3521 ms** to first MCP response vs **392 ms**
> for the global install — **9× slower**, and npx additionally serializes on the
> shared npm cache, so the cost compounds with concurrency. That is what pushed
> `context7` past the 30 s connect deadline; see
> [`unattended-orchestration`](../unattended-orchestration/SKILL.md) for why spawn
> concurrency, not CPU, is the variable that matters. Update deliberately with
> `npm i -g @playwright/mcp@latest`.

**Server (`coding.example.internal`, no node/npx) — Microsoft's official image over Docker, behind a lazy proxy.**

A container registered directly (`docker run -i …` as the `claude mcp add` command) lives
as long as its Claude Code session, browsing or not: measured 2026-09-26, 7 live sessions
held 7 idle containers (2.5–38 MiB each, no Chromium), because Claude Code starts every
stdio server at session start and does not reconnect one that exited. So sessions get
`tools/playwright_mcp_lazy.py` instead, a stdio proxy that answers the session-start
handshake from a cache, starts the container on the first real request, stops it after
`AUTOOS_PLAYWRIGHT_IDLE_SECONDS` (default 900) without traffic and starts a fresh one on
the next request (browser state does not survive a stop).

```bash
docker pull mcr.microsoft.com/playwright/mcp:latest
# from the AutoOS checkout; ./setup.sh --only mcp-playwright registers the same entry
claude mcp add -s user playwright -- python3 "$PWD/tools/playwright_mcp_lazy.py"
```

- The proxy's default backend is `docker run -i --rm --init --network host --name
  autoos-pw-<pid>-<n> mcr.microsoft.com/playwright/mcp:latest`; `AUTOOS_PLAYWRIGHT_MCP_CMD`
  replaces it. The very first session on a machine has no handshake cache and starts one
  container to fill it (`~/.cache/autoos/playwright-mcp-handshake.json`). The proxy reads
  that file only if it is a regular file of at most 4 MiB, owned by you and not writable
  by group or others, in a directory that is too (never through a symlink); otherwise it
  logs one line to stderr and starts the backend as on a cold cache. An existing
  group-writable directory (say from a umask of 002) is refused: `chmod go-w` it.
- **`--network host`** (Linux) lets the containerized browser reach dev servers on
  the host's `localhost:PORT`, so an agent tests the site it just built with the URL
  it would naturally type. Trade-off: less network isolation; fine on a single-user
  dev host. Portable alternative: `--add-host=host.docker.internal:host-gateway` and
  browse `http://host.docker.internal:PORT`.
- **`--rm`** → ephemeral browser profile per run; **`--init`** reaps zombie browser
  processes.
- On Linux with docker the installer registers the proxy, and replaces an existing
  entry only when it is exactly the old docker form, `npx -y @playwright/mcp…`, or this
  proxy at the path of another (moved) checkout; every registration or replacement is
  preceded by a backup of the Claude config, and a failed backup stops it. macOS and
  hosts without docker keep the npx entry. Sessions that are already running keep their
  current container until they end.
- Status: tested against a fake backend (`tests/test_playwright_mcp_lazy.py`) and observed
  2026-09-26 in a real Claude Code session against the real image: with a warm cache no
  container started, the first browsing call started one (~1.5 s cold), and with
  `AUTOOS_PLAYWRIGHT_IDLE_SECONDS=20` it stopped ~20 s after the last call (default 900 s).
  The backend advertises only `tools` (no `prompts` or `resources` to force a start); the
  proxy itself holds ~13 MB RSS.

Verify — authoritative client health check, then a real browser drive:
```bash
claude mcp list                       # → playwright … ✔ Connected
# functional: initialize → tools/call browser_navigate https://example.com → snapshot
```

With the proxy, `claude mcp list` is answered from the handshake cache and no longer
proves the container runs; the functional drive does.

> **Restart to load it.** MCP servers initialize only at session start, so a running
> session won't see a newly-added `playwright` until it restarts.

> **The tool prefix follows the wiring, and briefs depend on it.** A user-scope
> server is `mcp__playwright__*`; the *same* server installed as a plugin is
> `mcp__plugin_playwright_playwright__*`. Searching the wrong one finds nothing and
> reads as "the server is down". Anything that pre-allows tools by name — an
> `--allowedTools` list, an unattended run's config — must match the wiring actually
> in force. Check with `claude mcp list`, never from memory.

### Context7 — Library Documentation

Up-to-date docs for any library, framework, SDK or CLI. Prefer it over web search,
and over your own recall — training data goes stale.

#### Wiring — global install, for the same reason as Playwright

```bash
npm i -g @upstash/context7-mcp
claude mcp add -s user context7 -- \
  node "$(npm root -g)/@upstash/context7-mcp/dist/index.js" --api-key "$CONTEXT7_API_KEY"
```

Measured 2026-09-04, same cached code, two launch paths: `npx -y` **3704 ms** vs
`node` on the global install **1316 ms**. Under 20 concurrent spawns — an ordinary
morning with a dozen sessions open — the npx form reached **34.4 s and exceeded the
30 s connect timeout**, while the global install stayed at **3.7 s**.

> **The API key sits in plain `args`**, visible to `claude mcp list` and any process
> listing. It is not in a tracked file, but treat it as exposed: rotate it if it has
> ever been printed, and never paste it into a repo config.

## Infrastructure

| Service | Address | Model(s) | Purpose |
|---------|---------|----------|---------|
| omnigraph-server | `:8080` | — | The memory graph (bearer-auth; `/healthz` is open) |
| MinIO | `:9000` API / `:9001` console | — | S3 store backing Omnigraph |
| omnigraph-viewer | `:8090` | — | Read-only web UI (put Authelia in front if exposed) |
| Ollama | `:11434` | `nomic-embed-text` (768-dim, CPU-fine) | Embeddings — **optional** |
| OLLAMA_KEEP_ALIVE=24h | Windows env | — | Keep models in VRAM |

Ollama is optional: without it, recall degrades to graph traversal + full-text rather
than failing. **No Qdrant, no Postgres, no LLM API key** — those were Mem0's, and Mem0
was removed entirely (ADR 0003). `bge-m3` (1024-dim) was Mem0's embedder and is *not*
this graph's: the graph is `Vector(768)`/`nomic-embed-text`, and mixing dimensions makes
`nearest()` return garbage.

### Harbor — Private Container Registry

**Convention: any container image an agent builds to share or deploy goes to
Harbor, never Docker Hub.** Harbor is the homelab registry (web UI, per-project
RBAC/robot accounts, Trivy scanning) at `harbor.example.org`, backed by the
NFS share on ollama.example.internal. Local-only throwaway builds (e.g. `docker compose --build`
for a stack that runs in place) do **not** need pushing — only images meant to be
pulled elsewhere.

```bash
# build locally, then publish to Harbor under a project namespace (create the
# project once in the UI: e.g. `agents`, `infra`)
docker build -t myimage:latest .
docker login harbor.example.org                       # admin / project user / robot token
docker tag  myimage:latest harbor.example.org/agents/myimage:latest
docker push harbor.example.org/agents/myimage:latest
# elsewhere: pull instead of rebuilding
docker pull harbor.example.org/agents/myimage:latest
```

Deployment / admin (installer, storage path, Caddy exposure, secrets) is the
single source of truth in the Server repo: `server/cloud/harbor/README.md`.

## Project Initialization

> ⚠️ **On coding.example.internal the Omnigraph stack is already deployed from
> `<site-repo>/mcp-servers/docker-compose.yml`** (canonical, Dockhand,
> viewer bound to `0.0.0.0:8090` for Caddy). The compose below is a **local/dev**
> variant — it binds `127.0.0.1` and shares the project name `mcp-servers`, so
> running it on coding.example.internal clobbers the live viewer and takes
> `omnigraph-ui.example.org` down. On coding.example.internal, manage the stack from the
> canonical Server compose instead. Use the below only for a standalone/dev host.

```bash
cd ${AGENT_SKILLS_ROOT}/infra/mcp-servers
docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml up -d
curl -fsS http://localhost:8080/healthz                  # server up
python3 scripts/_omni_env.py                             # what stack docker actually has
```

## Recommended Workflow

1. Start the server: `docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml up -d`
2. Activate Serena project: `mcp_serena_activate_project(project="<absolute path to the repo>")`
3. Recall memory from Omnigraph (see `skills/structured-memory/SKILL.md`)
4. Build or refresh Graphify graphs when the repo has a graph target
5. Use Serena for navigation, Omnigraph for memory, Graphify for graph queries, and Superpowers for workflows
6. End session: persist durable decisions to Omnigraph; services keep running

## Troubleshooting

```powershell
# Full health check
curl -fsS http://localhost:8080/healthz                  # omnigraph server
curl -fsS -H "Authorization: Bearer $env:OMNIGRAPH_TOKEN" http://localhost:8080/graphs

# Serena
serena project list
serena --version

# Omnigraph (open health probe, then an authenticated call)
curl -fsS http://localhost:8080/healthz
curl -fsS -H "Authorization: Bearer $env:OMNIGRAPH_TOKEN" http://localhost:8080/graphs

# Ollama (optional — embeddings only)
curl http://localhost:11434/api/tags
```
