---
name: structured-memory
description: "Store and retrieve durable, cross-project, cross-agent memory as typed nodes in the self-hosted Omnigraph graph — decisions, rules, preferences, conventions, components, and tasks. Use at session start to load what was already decided, and at session end (or when a durable decision is made) to persist it. Memory is explicit and reviewable: nothing is auto-extracted, so you choose what is durable and write it. Do not use for transient scratch notes or single-session state."
---

# Structured Memory (Omnigraph)

The self-hosted memory layer is **Omnigraph** — a graph store with combined
graph-traversal + vector + full-text retrieval. It is the only memory layer; there
is no fallback. Omnigraph does **not** auto-extract memories from conversation:
**you** decide what is durable and write it as a typed node. This is deliberate —
typed, reviewable memory keeps rules and decisions integrated across future builds.

Schema (node/edge types, `.pg` declaration, JSONL ingest examples):
[references/schema.md](references/schema.md).

**Operational rules & gotchas — read before any query/mutate/load/sync:**
[references/operations.md](references/operations.md) (edge casing, the D₂
insert-xor-delete rule, duplicate-edge handling, never-overwrite-shared-`main`,
lowercase slugs, `nomic-embed-text` 768-dim embeddings, remote CLI ops). These
capture failure modes already debugged — honoring them avoids re-troubleshooting.

MCP server: `@modernrelay/omnigraph-mcp`. Tools: `schema_get` (read schema
first), `query` (read GQ), `mutate` (write GQ, insert/update/delete), `load`
(bulk NDJSON upsert by `@key`), `branches_create` / `branches_merge` /
`branches_delete`, `commits_list` (verify writes), `snapshot`, `health`.

## `.gq` is GraphQuery — NOT GraphQL (read before your first query)

Omnigraph's query language is **`.gq` (GraphQuery)**. Agents trained on GraphQL or Cypher
reflexively write a `mutation { … }` wrapper or a bare `$d: Decision`, and every such call
fails at character 1 with `parse error: expected query_file` / `expected query_body` — which
looks like a broken server but is just the wrong dialect. The working shapes:

```gq
query list($lim: Int)     { match { $d: Decision } return { $d.slug, $d.title } limit 40 }
query one($s: String)     { match { $d: Decision { slug: $s } } return { $d.rationale } }
query decided($s: String) { match { $d: Decision { slug: $s } $d decidedIn $p } return { $p.slug } }
```

- Every block is a named `query name() { match { … } return { … } }` — a **bare binding, or
  a top-level `mutation { }` wrapper, is a parse error.** Writes are *also* `query` blocks; the
  verb inside (`insert` / `update` / `delete`) is what makes them writes, and they dispatch via
  **`mutate`**, not `query`.
- Edge names are **PascalCase** in `insert`/`delete` (`insert DecidedIn { from, to }`) but
  **lowerCamelCase** in traversal (`$d decidedIn $p`).
- `nearest` / `bm25` / `rrf` **order**, they don't filter — always a trailing `limit N`.
- A mutation is insert/update-only **or** delete-only, never both.
- **Verifying a write by reading the data back is NOT enough.** `affectedNodes` and a
  successful read-back can both look right and still not last: measured 2026-08-21, four
  `update`s were confirmed by immediate read-back and had reverted ~an hour later, while
  `insert`s from the same window survived (cause unproven). So check `commits_list` head
  before/after, **re-read anything important at the END of the session**, and prefer
  *inserting* a new node over *updating* one when the content must not be lost.
- **Never document a mechanism you have not falsified.** Those reverts produced a confident,
  wrong rule — "mixing `update` and `insert` drops the updates" — which a throwaway-branch
  test disproved in minutes, and which contradicted the verified rule directly above it.
  A new rule that contradicts an existing verified one is a signal to test, not to write.
  `branches_create` → probe → `branches_delete` makes that free.

Depth + every debugged failure mode: [references/operations.md](references/operations.md) rules 2–6.

## First action every session — recall

Before editing code, load prior memory for the current project:

1. Identify the project slug (the repository folder name, e.g. `agent-skills`).
2. Query the project subgraph for `Rule`, `Decision`, `Preference`, `Convention`
   nodes edged to that `Project`, plus global `Preference` nodes (`scope: global`,
   in the shared `memory` graph — needs the second bridge, see below).
   **Scope first, rank second:** narrow by graph traversal *before* invoking
   `nearest`/`bm25`/`rrf`, so ranking runs over the relevant set rather than every
   node — cheaper and more relevant. Ranking ops are ordering, not filtering, so
   they always need a trailing `limit N`.
   For a whole-graph dump (this memory is small), prefer an export over paging
   through read queries.
3. Treat the returned `Rule (must)` nodes as hard constraints and `Decision`
   nodes as settled context. Do not re-litigate accepted decisions.

If the graph has no `Project` node for the current repo yet, create one (see
Persist) — that is the signal this repo hasn't been onboarded to memory.

## When to persist

Write a node when something is **durable** — true beyond this session:

- A decision with a rationale → `Decision` (+ `DecidedIn` → `Project`).
- A hard constraint the team must follow → `Rule` (+ `ConstrainsProject`).
- A soft, overridable inclination → `Preference` (`scope: global` or project).
- A repeatable pattern → `Convention` (+ `AppliesTo`).
- A notable system part → `Component` (+ `PartOf` → `Project`).
- Planned/ongoing work worth remembering → `Task` (+ `Tracks` → `Project`).

Do **not** persist: transient reasoning, one-off file paths, or anything already
captured in code, `CHANGELOG.md`, or an ADR (link to those instead).

## Link richly — make it a graph, not a star

Two edge rules keep the graph correct and navigable:

1. **Always attach a project-specific node to its `Project`** (the hub edge above).
   A node with **no** edge to a `Project` renders as **"global"** — and *global is
   reserved for genuinely cross-project facts* (a handful of `Preference`s like
   "prefer TDD"). A project-specific `Rule`/`Decision`/`Task` showing as global is
   a **bug**: it means you forgot the hub edge. Never mark project-specific info
   global.
2. **Also link a node to the specific nodes it relates to** — not only the hub.
   Most memory is a star (everything → `Project`) because agents add just the hub
   edge; that graph is unnavigable. When you add a node, ask *"what existing
   component/decision does this touch?"* and add the relational edge too:
   - `ConstrainsComponent`: Rule → the Component it governs
   - `Affects`: Decision → the Component it changes
   - `Addresses`: Task → the Component it works on · `Implements`: Task → the Decision it realizes
   - `DependsOn`: Component → Component · `Supersedes`: Decision → the Decision it replaces

   Smell test: a new node whose **only** edge is to the `Project` is probably
   under-linked. Aim for at least one relational edge where a real relationship
   exists.

   This matches Omnigraph's own guidance: the reference cookbook model (SPIKE:
   11 node types, **24 edge types**) is richly connected — edges run *between*
   entities (`Element→Element`, `Pattern→Pattern`, `Insight→Element`), explicitly
   **not** a star. A hub-and-spoke graph where everything only edges to one hub is
   the anti-pattern. (That said, connect only relationships that genuinely exist —
   don't fabricate edges to hit a number; density should follow the information.)

## How to persist — branch, write, merge

**Pick the write first** (Omnigraph's rule, `omnigraph://best-practices/data`):

| What you're writing | Use | Why |
|---|---|---|
| One or a few nodes/edges | `mutate` | typechecked + parameterized at call time; finishes well under the proxy timeout |
| A bulk set, upserting by slug | `load` `mode: merge` | preserves rows not in the file |
| A first/clean seed of a branch | `load` `mode: overwrite` | **destructive** — never on a populated `main` |

`mode` is required — there is no default, because `overwrite` is destructive.
Keep writes small: prefer `mutate` for a handful of records over a bulk `load`.

Memory writes are reviewable like code. For anything risky or large:

1. Fork + write in **one shot**: `load` with `from: "main"`, `branch:
   "mem/<project-slug>/<short-topic>"`, `mode: "merge"`. (`from` forks the branch
   if missing; without it a missing branch is a 404.) Use stable **lowercase**
   kebab-case slugs so re-writes are idempotent. Mind the edge-casing and D₂ rules
   in [references/operations.md](references/operations.md).
2. **Verify on the branch** before it touches `main` — query it, and check the
   branch head actually moved. *A branch head identical to `main`'s means the load
   never landed and you have an empty fork.*
3. Merge into `main` with `branches_merge` (edge-de-duplicating — prefer it over a
   raw cross-store `load --merge`, which appends duplicate edges), then **delete the
   branch**. Branches are for review, not for living in: create → load → verify →
   merge → delete, same session. A week-old branch is a yellow flag — and a
   leftover one **blocks `schema apply`**, which refuses to run while any non-main
   branch exists (so a stray `device/<host>` or `mem/…` branch will break
   `scripts/apply-cluster.sh`).

**Know that a merged `Decision` is not semantically searchable.** `mode: merge`
does not compute the `@embed("rationale")` vector — and if you merge a Decision that
*had* one, it **erases** it (the upsert replaces the row). An unembedded `Decision`
is *dropped* from `nearest()` — not ranked low, absent. On v0.8.1 there is no
working way to re-embed a populated graph (both `overwrite` and vector-carrying
`merge` hit a Lance bug), so this is a known limitation, not a step you can just
run: the node stays findable by traversal and full-text, and a rebuild via
`scripts/dedup-graph.py` is the only re-embed path. Don't claim semantic search
covers a Decision you merged without checking it with a `nearest()` query. Detail +
evidence: [references/operations.md](references/operations.md) rule 10.

Supersede, don't overwrite, an accepted `Decision`: write the new `Decision` and
a `Supersedes` edge to the old one; set the old one's `status` to `superseded`.
Never `overwrite` a populated `main`.

## You do not manage device branches — sync is automatic

Always read/write the memory server your client is configured with, on `main`.
**Do not create or merge device branches yourself.** When your device is online
it uses the central `main` directly; when it is offline it uses a local `main`,
and a background service (`infra/mcp-servers/omnigraph-setup/omnigraph-sync.sh` on a timer)
creates a `device/<host>` branch, merges it into central `main`, and reconciles
back — resolving node conflicts by slug-keyed upsert. So just write durable
memory to `main`; the automation handles branching, pushing, and merging. See
`infra/mcp-servers/omnigraph-setup/README.md`.

## Cross-project model — one graph per project (hard isolation)

**Each project's memory lives in its OWN Omnigraph graph, named after the repo**
(`agent-skills`, `finance-repo`, `sibling-analysis-repo`, `site-repo`, …). Projects are
never merged into one shared store — a `load mode: overwrite` or a bad write in
one project's graph can't touch another's. The shared **`memory`** graph now
holds **only** global-scope `Preference`s (house style, TDD-default) — never
project data.

### Which graph fits this repository? — ask the cluster, never guess

`cluster.yaml` used to be the roster of graph names. It is now gitignored (it had grown
into a list of every private project this machine touches) and purged from history, so it
is **not** a discovery source. Ask the live cluster — which is also the only answer that
satisfies `as-rule-declared-is-not-live`:

| Question | Answer it with |
|---|---|
| Which repository names exist? | `graphs_list` — every `graphId` the cluster exposes, alphabetically. Needs the cluster-scoped `graph_list` action; without it you get `403 ForbiddenError`. |
| Which one is *this* repo? | The `graphId` equal to the repository folder name — then confirm with the row below. |
| Am I actually connected to it? | `Project.repository` in the graph you are pinned to must equal this repo's `git remote get-url origin`. |

Both calls need the bearer token and nothing else: without `Authorization` the server
answers **401**, and `/graphs` additionally needs the cluster-scoped `graph_list` grant or it
answers **403** rather than an empty list — so a silent empty roster is not a state you can
reach. One token maps to one actor, and an actor sees exactly the graphs granted to it. Full
auth model, the 401/403/200 measurements and the per-user path:
`skills/mcp-servers-setup/SKILL.md` → *Discovering repository / project names*.

The third row is the whole point. `graphs_list` is cluster-wide, but `query` only ever
reaches the one graph `OMNIGRAPH_GRAPH_ID` pinned, so you cannot compare URLs *across*
graphs from a project session. What you can do, in one cheap query, is prove the pin is
right:

```gq
query whoami() { match { $p: Project } return { $p.slug, $p.repository } }
```

If `repository` does not match your `origin`, the bridge is serving **another repository's
graph**, and every recall you are about to act on describes a different codebase. That is
the `~/.claude.json` user-scope override CLAUDE.md documents at length — and until this
property existed it had no cheap detector, because a graph that is merely *the wrong one*
looks exactly like a graph that was wiped.

**Backfill a graph whose `repository` is still null.** Only `agent-skills` is populated so
far; run this once from each repo, in that repo's own session, since the bridge cannot
reach another project's graph:

```gq
query set_project_repository($slug: String, $repository: String) {
  update Project set { repository: $repository } where slug = $slug
}
```

- **Point your agent at its project graph**: set `OMNIGRAPH_GRAPH_ID=<repo>` for
  the omnigraph MCP bridge (a project-scoped `.mcp.json` env, or export it before
  launching). **Always write and push project memory to the Omnigraph graph/branch matching the repository folder name (`<repo>`), and never to the shared `memory` graph.**
  If you are reading `memory` when `.mcp.json` says otherwise, a same-named `omnigraph`
  in `~/.claude.json` (user scope) is silently overriding it — `0 rows except 2
  Preferences` **is** `memory`, not a wipe. Diagnose:
  `infra/mcp-servers/omnigraph-setup/setup-agent-memory.ps1 -Check` (or `.sh --check`).
- **One bridge per repo.** `OMNIGRAPH_GRAPH_ID` pins it to exactly one graph and no tool
  (`query`/`mutate`/`load`) takes a graph argument, so a project-scoped agent cannot reach
  `memory` — deliberately. `memory` holds only two global `Preference`s (TDD-by-default,
  MCP-first navigation) and **both are already Principles 2 and 6 of
  `skills/coding-principles/SKILL.md`**, which every agent loads. Global house style belongs
  in the skills — the single source of truth (Principle 1).
- **Inside your project graph, still scope + link:** every project-specific node
  is a `Project` node's satellite — edge it to the `Project` (slug = repo folder
  name) and add relational edges (see "Link richly" above).
- **Migrate a project off the shared graph** with
  `infra/mcp-servers/scripts/split-project-graph.py <repo> --apply` (additive: it
  copies the subgraph and leaves the source intact, so verify before pruning).
- **Add a new project graph**: `infra/mcp-servers/scripts/add-project-graph.sh
  <name>` then `./scripts/apply-cluster.sh` (declared in `cluster/cluster.yaml`,
  converged into the live cluster with a snapshot + node-count verify).
- **Seeds** (`cluster/seed/<name>.jsonl`) load into the graph matching the file
  name, so a rebuild stays isolated — never re-merged. `memory.jsonl` = globals.
- **Browse any graph**: the viewer's **graph** selector switches between them.

## Multi-user

- **Humans** log in via **Authelia (SSO through Caddy)** in front of the viewer —
  that is the multi-user login. The viewer itself has no auth; never expose it
  without the proxy.
- **Agents / API** are identified by their **bearer token → named actor**. Give
  each user their own token and a personal graph (e.g. `u-alice`) that only they
  can read/write, plus read-only access to shared `memory`. Scope it with Cedar
  policy — see `cluster/users.policy.yaml.example`. Minting tokens is an admin /
  `apply-cluster.sh` step (tokens are cluster secrets, never committed).

## If Omnigraph is down

**There is no fallback memory layer** (ADR
[0003](../../docs/decisions/0003-remove-mem0-fallback.md); ADR
[0001](../../docs/decisions/0001-omnigraph-over-mem0.md) has the original
rationale). Work the session without recall — read the repo, its `CHANGELOG.md`
and ADRs — and persist once the server is back. Memory is an accelerator, not a
correctness dependency: a session without it is slower, not wrong. Don't invent a
side-channel store; that is how memory fragments.

Recovery, in order: the versioned `cluster/seed/*.jsonl` (the boot seeder
re-merges them), then a `.graph-backup/` NDJSON export, then MinIO's bind-mounted
store. Those backups are the real insurance — keep them working.

## Checklist

- [ ] Recalled project + global memory before editing.
- [ ] Honored `Rule (must)` nodes as hard constraints.
- [ ] Persisted new durable decisions/rules/conventions as typed nodes.
- [ ] Used a branch, stable slugs, and `supersedes` for changed decisions.
