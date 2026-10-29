> Part of [mcp-servers-setup](../SKILL.md).

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
  [`infra/mcp-servers/bin/graphify-mcp`](../../../../infra/mcp-servers/bin/graphify-mcp) does it:
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
true in a worktree. Source: graphify's own hook template — `graphify.hooks` (`_WORKTREE_GUARD`,
spliced into both hook bodies), re-read 2026-10-29. The guard only applies while graphify's own
hooks are installed — another tool's hook installer can replace them (measured on the workstation
2026-10-29: Qoder tracker hooks), so check `.git/hooks/post-commit` names graphify; reinstall
with `graphify hook install`.

So the rule matches Serena's:

**In a linked worktree, graphify is unavailable: say so; never link or hand-build `graphify-out/` there.**

The hooks will not maintain a graph there, so it goes stale with nothing to tell you — the
escape hatch that is not silent.

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

