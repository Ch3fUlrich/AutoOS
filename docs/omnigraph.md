# Omnigraph memory — agent contract

Durable cross-session memory lives in the self-hosted **Omnigraph** graph,
one graph per repo. This repo's graph id is `autoos`
(`OMNIGRAPH_GRAPH_ID=autoos` in `.mcp.json`). Memory is **explicit**: nothing
is auto-extracted — you decide what is durable and write it as a typed node.
A session without recall is slower, not wrong; there is no fallback layer.

Full protocol with every debugged failure mode: the `structured-memory`
skill in the sibling `agent-skills` checkout (parent directory of this
repo). What follows is the minimum contract to use it without breaking.

## Session boundaries

- **Recall first.** Prove the pin, then load the subgraph:
  ```gq
  query whoami() { match { $p: Project } return { $p.slug, $p.name } }
  ```
  The live schema's `Project` has `slug`, `name`, `path`, `summary` (there
  is no `repository`; asking for it is a type error). If `slug` is not
  `autoos`, you are attached to the wrong graph — stop and fix the pin (a
  user-scope server entry silently overrides the repo one). `0 rows except
  2 Preferences` is the shared `memory` graph, not a wipe.
- **Persist at end.** Durable decisions/rules/conventions become typed nodes
  (`Decision`/`Rule`/`Preference`/`Convention`/`Component`/`Task`), each
  edged to the `Project` hub **and** to the components/decisions it touches.
  Never write project data to the global `memory` graph.

## The dialect (verified against server v0.8.1 — GraphQL/Cypher habits fail)

- Every call is a **named** `query name() { ... }` — parens required even
  with no params. No `mutation {}` wrapper; writes dispatch via `mutate`.
- Reads: `match { $d: Decision } return { $d.slug, $d.title }`, literals
  quoted (`slug: "my-slug"`), `limit N` always with ranking ops.
- Writes: `insert Decision { slug: $slug, title: $title, ... }` (parameterize,
  never interpolate). **Edges are PascalCase on write**
  (`insert DecidedIn { from: $slug, to: $project }`), **lowerCamelCase on
  traversal** (`$d decidedIn $p`).
- One mutation is insert/update-only **or** delete-only, never both.
- Bulk NDJSON (`load`, `mode: merge` upserts by slug): nodes are
  `{"type":"<Type>","data":{"slug":"...", ...}}`, edges are
  `{"edge":"<Edge>","from":"<slug>","to":"<slug>"}`. Never `overwrite` a
  populated `main`.
- Slugs are lowercase kebab-case and stable — re-writing the same slug
  upserts; a case variant forks a duplicate. Nodes are safe to retry;
  **edges are not** (no `@key` — a retried edge insert duplicates).
- **Verify every write**: `commits_list` head before/after must move, then
  re-read the data at session end (a confirmed read-back can still revert;
  prefer inserting a new node over updating one).
- Write to `main` directly for small inserts; the sync automation owns
  device branches. Branch (`create → load → verify → merge → delete`)
  only for risky/large writes.

## Client wiring (this repo)

Every client runs the same stdio bridge, `npx -y @modernrelay/omnigraph-mcp`
at the pin in `catalog/agent-harness.json`. The bridge needs three values;
the first two missing make it **exit at start-up**, the third makes every
read fail while the client still shows it connected:

| Variable | Missing means |
|---|---|
| `OMNIGRAPH_BASE_URL` | exits: "OMNIGRAPH_BASE_URL is required" → client: "Connection closed" |
| `OMNIGRAPH_GRAPH_ID` | exits: "OMNIGRAPH_GRAPH_ID is required" (server 0.7+ has no fallback graph) |
| `OMNIGRAPH_TOKEN` | starts, `health`/`tools/list` work, every read: "missing bearer token" |

| Client | Where it is declared | Graph id / base URL | Token |
|---|---|---|---|
| Claude Code (this repo) | `.mcp.json`, project scope, approved via `.claude/settings.local.json` `enabledMcpjsonServers` | `autoos`, `${OMNIGRAPH_BASE_URL:-http://localhost:8080}` | `${OMNIGRAPH_TOKEN}` expanded from Claude's env |
| opencode | `opencode.jsonc` `mcp.servers.omnigraph` | `autoos`, `http://localhost:8080` | inherited from the process that started the opencode service |
| Zed | installer → `context_servers.omnigraph` in Zed settings | `autoos`, `http://localhost:8080` | inherited from the desktop session env |
| OpenHands | installer → `agent_settings.mcp_config.omnigraph` | `autoos`, `http://localhost:8080` | written into that (untracked) settings file from env or the env file |
| Antigravity | installer → its `mcp_config.json` | `$OMNIGRAPH_GRAPH_ID` or `autoos` | written from env when set |
| Neovim + sidekick | through the opencode CLI | as opencode | as opencode |

Zed, OpenHands, Antigravity and a global opencode config are user-scope,
so they pin `autoos` for every folder they open. The per-repo pin only
exists for Claude Code and the repo's `opencode.jsonc`.

### Where the token comes from

Never from a tracked file. The installers keep **one** per-user copy:

- **Linux/macOS:** `~/.autoos-omnigraph.env`, mode `600`, `KEY=VALUE`
  lines (`OMNIGRAPH_BASE_URL`, `OMNIGRAPH_TOKEN`; other keys you add are
  kept). It is linked as `~/.config/environment.d/60-autoos-omnigraph.conf`,
  which the systemd user manager and desktop sessions load at login, and
  sourced from `~/.bashrc`/`~/.zshrc` when `OMNIGRAPH_TOKEN` is not already
  set (marker `AutoOS:omnigraph-env`). The value comes from
  `$OMNIGRAPH_TOKEN`, else from the local `omnigraph-server` container's
  `OMNIGRAPH_SERVER_BEARER_TOKEN`, else stays what the file had.
- **Windows:** `%USERPROFILE%\.autoos-omnigraph.env` plus the
  `OMNIGRAPH_TOKEN` *user* environment variable (one named variable, read
  first, written only when it differs).

To set it by hand: add `OMNIGRAPH_TOKEN=<token from the graph server>` to
that file (`chmod 600` it), then log out and in (or `systemctl --user
daemon-reload` plus `opencode service restart`) so long-running services
pick it up.

The systemd user units `register-autostart.sh` writes for the processes that
start an omnigraph client (`autoos-opencode`, and `autoos-stack` whose
fallback starts `opencode serve`) read the file themselves:
`EnvironmentFile=-%h/.autoos-omnigraph.env`, on every start, no re-login
needed — after adding the token, `systemctl --user restart autoos-opencode`
is enough. The leading dash makes the file optional, so a machine without it
still starts. The gateway units do not get it: OmniRoute listens on the LAN
and never talks to the graph.

### Check it

```bash
python3 tools/check-omnigraph.py            # config + live: healthz, authed schema read, Project hub
python3 tools/check-omnigraph.py --offline  # config shape only (what the suite runs)
```

`configuration/healthcheck.sh` / `.ps1` run the same probe. `opencode mcp
list` asks the background service, whose cwd is `~`, so it never sees this
repo's `opencode.jsonc` and says "No MCP servers configured". Ask for this
directory instead:
`opencode api GET "/api/mcp?location%5Bdirectory%5D=$PWD"`.

### What broke on 2026-09-24

"omnigraph is not working, other sessions cannot connect" turned out to be
one real outage, two latent faults and one misleading log line:

1. **Sibling repos cannot start the bridge (the outage).** `agent-skills`
   and the homelab/invest repos launch it as `docker run … omnigraph-mcp:latest`,
   and that image did not exist on the host ("pull access denied"). Claude
   Code in those repos: `✘ Failed to connect — Connection closed`. Build it
   from `agent-skills/infra/mcp-servers/servers/omnigraph-mcp`, or switch
   those `.mcp.json` files to the npx form this repo uses. Claude Code in
   this repo was connected throughout.
2. **Zed and Antigravity had no graph id** (Zed had no base URL either):
   the bridge exits at start-up. Fixed in both installers.
3. **The token lived only in an interactive `~/.zshrc`.** Everything
   started from that shell had it, so nothing failed that day, but a
   service, a desktop app or a `bash -c` agent would show omnigraph
   connected and fail every read. Fixed by the env file above.
4. **Not a fault: `mcp connect failed server=omnigraph "Connection closed"`
   in `~/.local/share/opencode/log/opencode.log`.** Every such line on
   2026-09-24 came from a short-lived `opencode serve --stdio` whose client
   was already gone: the same run logs `InterruptError: All fibers
   interrupted` 2.5–4 s after start, *before* the failure, and the npx
   servers (3.5–4.5 s to start) were simply still starting when it was torn
   down; context7 and playwright failed the same way. Long-lived sessions
   connect. Read the run's earlier lines before blaming the server.

Not a cause, but watch for it: agent-skills' `trust_worktree.py` writes
`OMNIGRAPH_GRAPH_ID=<folder name>` (`AutoOS`) into worktree `.env` files.
Graph ids are case-sensitive, the server only has `autoos`, and nothing in
this repo reads that file; the probe warns about it.
