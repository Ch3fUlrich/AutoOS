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
  query whoami() { match { $p: Project } return { $p.slug, $p.repository } }
  ```
  If `repository` is not this repo's `git remote get-url origin`, you are
  attached to the wrong graph — stop and fix the pin (a user-scope server
  entry silently overrides the repo one). `0 rows except 2 Preferences` is
  the shared `memory` graph, not a wipe.
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

- `opencode.jsonc` pins the bridge (`@modernrelay/omnigraph-mcp`) with
  `OMNIGRAPH_GRAPH_ID=autoos`; Zed gets it as a context server from the
  installers; OpenHands carries it in `mcp_config`. Neovim+sidekick inherits
  it through the opencode CLI.
- The token comes from env (`OMNIGRAPH_TOKEN`), never from a tracked file.
