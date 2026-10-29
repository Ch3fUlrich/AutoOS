> Part of [mcp-servers-setup](../SKILL.md).

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

**A duplicate does not resolve to one winner — both run.** Measured in `repo-b` 2026-09-01:
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
> not in the image, and headings are a grep. This took repo-c's Serena down until 2026-07-20.
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
  `repo-a` (8 languages incl. `markdown`) it chose `AGENTS.md — repo-a`, kind
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
> `claude` process per worktree gives each its own Serena and its own `_active_project`.
> Subagents stay in their own session's worktree,
> sharing one correctly-activated Serena. *Worktrees get sessions; subagents share the session's
> worktree.* (`herdr-orchestration` panes are how you run one session per worktree.)
>
> Two artifacts must reach the worktree; both fail identically, by being untracked:
>
> | Requirement | Why it bites | Fix |
> |---|---|---|
> | `.serena/project.yml` **tracked** | absent, Serena autogenerates it, and autogeneration picks languages by file count — reintroducing `json`/`markdown`/`yaml` in every worktree, forever | `.serena/*` plus `!.serena/project.yml`, never a bare `.serena/` (a negation cannot re-include a file inside an excluded directory) |
> | `project_name` **omitted** | every worktree registers the same name; by-name activation raises for all agents at once | delete the key (rule 1b) |
>
> **In a linked worktree, graphify is unavailable unless the runner provisioned `graphify-out/`; then say which tree and branch it describes. Never link or hand-build it yourself.** (reasons: [graphify.md](./graphify.md))
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

> **Memory tools: off, everywhere (closed 2026-09-18 host-side; closed 2026-10-09 on the shared SSE
> server, D-869).** Omnigraph is the only memory layer. Serena's
> `onboarding`, `write_memory`, `read_memory`, `list_memories`, `edit_memory`, `rename_memory` and
> `delete_memory` are in `excluded_tools` of the **global** `~/.serena/serena_config.yml`, beside the
> file tools the host already provides. `replace_in_files` (1.7's multi-file text replace) is excluded too, for the same reason.
> That file is host-side; the containerised server on `:9121` gets its own `/root/.serena` volume and
> never reads it, so it is started with a read-only **context** file instead —
> `infra/mcp-servers/config/serena-context-no-memory.yml` via `--context <path>`. Two things do *not*
> work there: `--mode no-memories`, because `--mode` activates a mode *dynamically* and a dynamic mode's
> exclusions never reach the *exposed* tool set of a multi-project server (`tools/list` keeps advertising
> them), and binding `serena_config.yml` into the container, because Serena re-saves that file whenever it
> registers a project. Two homes for the list (host config + container context), no `project.yml` needs it.
> Verified by starting `serena-agent==1.7.0 start-mcp-server --context claude-code` over stdio: 15
> tools, with no memory or onboarding tool and `find_symbol` present; the container by
> `python3 tools/check-serena-tools.py --url http://127.0.0.1:9121/sse` (a manual/L1 run after a recreate,
> never a CI test). An invalid name in
> `excluded_tools` can stop Serena starting and take **all** symbol tools down, so re-run that
> check after every Serena upgrade.

