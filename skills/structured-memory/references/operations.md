# Omnigraph operations & gotchas

Hard-won operational rules for the self-hosted **Omnigraph** memory graphs — **one graph
per project** (`agent-skills`, `sibling-analysis-repo`, …) plus the shared **`memory`** graph,
which holds only global-scope `Preference`s. Following these avoids the failure modes we
already debugged (duplicate edges, corrupted merges, un-rankable search, silently-rejected
edges, a broken `main`). Read this before querying, mutating, loading, or syncing.

Server: `omnigraph-server` v0.8.1 · MCP bridge `@modernrelay/omnigraph-mcp`
(tools `schema_get`, `query`, `mutate`, `load`, `branches_*`, `commits_list`,
`snapshot`, `health`, `graphs_list`). Central: `https://omnigraph.example.org` (bearer).

## The rules

0a. **An empty-looking graph is a CONFIG bug until proven otherwise — never "rebuild" it.**
   Before concluding data is lost, confirm *which graph you are actually attached to*. A
   same-named **`omnigraph`** in `~/.claude.json` (**user scope**) silently overrides a repo's
   `.mcp.json`, and nothing reports it: the bridge connects, answers, and is simply pointed
   somewhere else. (Measured for omnigraph, and true of omnigraph — it is *not* a general law
   of same-named servers. A duplicate `serena` produces two running processes and no winner;
   see `skills/mcp-servers-setup/SKILL.md` → Serena → *Wiring*.) On 2026-07-17 a user-scope `omnigraph` pinned to `graph_id: memory` hid
   every repo's own pin. An agent saw `memory`'s contents — *every table rowCount 0 except
   2 `Preference`s* — concluded `sibling-analysis-repo` had been wiped, and started rebuilding it.
   `sibling-analysis-repo` was intact the whole time (135 nodes / 235 edges on both servers); the
   rebuild would have written a project into the globals-only graph.

   **`0 rows everywhere except 2 Preferences` is not a wipe — that is the `memory` graph.**

   ```bash
   # 1. what does the bridge actually serve? (must be empty of `omnigraph`)
   python -c "import json,pathlib;print(sorted((json.loads((pathlib.Path.home()/'.claude.json').read_text()).get('mcpServers') or {})))"
   # 2. ask the server directly, out of band — this is the ground truth
   curl -fsS -X POST $URL/graphs/<graph>/export -H "Authorization: Bearer $TOK" \
     -H 'content-type: application/json' -d '{}' | grep -c '"type"'
   ```
   Related failure shapes, all silent: an unset `OMNIGRAPH_TOKEN` → `missing bearer token`
   (looks like a broken server, is an unset variable); a wrong `OMNIGRAPH_NET` → `fetch
   failed` (the network can *exist but be empty*, so docker starts happily); an unbuilt
   `omnigraph-mcp:latest` → `pull access denied` (the image is on no registry).

0. **Declared ≠ live.** `cluster.yaml` and `memory.pg` are a *declaration*; nothing is
   real until `scripts/apply-cluster.sh` converges it into the server's state ledger.
   Verify against the server — `graphs_list` for graphs, `schema_get` for edge types —
   never by reading the config. This is not hypothetical: the declaration went unapplied
   for a long time, so five edge types (`Tracks`, `Affects`, `Addresses`, `Implements`,
   `DependsOn`) did not exist and **every write using them failed silently**, orphaning
   nodes that then rendered as "global".

1. **Read the schema first.** Call `schema_get` (or read `omnigraph://schema`)
   before any query/mutate/load. It declares node/edge types, `@key` fields,
   non-nullable props, and edge directions. Writing blind lint-fails or silently
   corrupts data. Also consult `omnigraph://best-practices/{queries,data,search}`.

   A bridge is **pinned to one graph** by `OMNIGRAPH_GRAPH_ID`, and no tool takes a graph
   argument — so `schema_get`/`query`/`mutate` always act on that graph. Reading a second
   graph (e.g. global `Preference`s in `memory`) requires a **second** MCP server entry.

2. **`.gq` is neither Cypher nor GraphQL — both habits fail at character 1.** The
   parser wants a named `query` envelope with **brace-delimited** clauses and
   `$`-prefixed variables. Cypher parentheses/bare-return and a GraphQL
   `mutation { }` wrapper both die as `parse error: expected query_file`, pointing
   at column 1 with no hint about what it wanted — easy to misread as "the server
   is broken" rather than "wrong dialect".
   - right: `query list_projects() { match { $p: Project } return { $p.slug } }`
   - wrong: `match (p:Project) return p.slug` · `query q { match (p:Project) ... }`
   - wrong: `mutation { insert Decision { ... } }` — writes are *also* `query`
     blocks; the body verb (`insert`/`update`/`delete`) is what makes them writes,
     and they dispatch through `mutate`, not `query`.

3. **Edge GQ casing is asymmetric.** `insert`/`delete` use the **PascalCase edge
   TYPE** name; **match/traversal** uses **lowerCamelCase**.
   - write: `insert DecidedIn { from: $d, to: $p }` · `delete AppliesTo where ...`
   - read: `match { $d: Decision  $d decidedIn $p }`
   - `insert decidedIn { ... }` fails with `parse error: expected type_name`.
   - Edge `data` block is `{}` when the edge has no properties — just `from`/`to`.

4. **D₂ rule — a mutation is insert/update-only OR delete-only, never both.**
   Mixing a `delete` with an `insert`/`update` in one query is rejected at parse
   time. Split delete-then-insert into two separate `mutate` calls.

5. **Parameterize; never string-interpolate.** `query q($slug: String) { ... }`
   with `params: {slug: ...}`. Ranking ops (`nearest`/`bm25`/`rrf`) require a
   trailing `limit N` — they order, they don't filter.

6. **Verify every write.** `commits_list` head before/after, or re-export and
   diff. A 504 is **not** failure — the server may commit after the proxy drops
   the response; check the head before retrying.

   **`affectedNodes` is not proof, and neither is reading the data back.**
   Measured 2026-08-21 on the `site-repo` graph: four `update` mutations each
   returned `affectedNodes: 1` **and** each was confirmed by an immediate read-back
   showing the new value — and ~an hour later all four had reverted to their
   previous values, while `insert`s from the same window survived. A commit exists
   in between that the session did not issue; `actorId` is `default` on every
   commit and `commits_get` returns metadata only, not a diff, so it could not be
   attributed. **The cause is unproven — treat it as able to recur.** Practical
   consequences: **re-read anything important at the END of a session**, not only
   after writing it, and where content must not be lost prefer **inserting a new
   node over updating an existing one**.

   **Do not promote a mechanism from correlated failures without falsifying it.**
   Those four reverts produced a confident, wrong conclusion — *"never mix `update`
   and `insert` in one mutation, the updates are silently dropped"* — which was one
   step from being committed to two repositories as permanent guidance. A short
   controlled test on a throwaway branch disproved it outright: mixing works with
   literal **and** parameterised `where` clauses, and with same-type **and**
   different-type inserts; `update` does create a commit. Note this also would have
   contradicted rule 4 above, which explicitly permits update+insert together — a
   new "rule" that contradicts an existing verified one is a signal to test, not to
   write. `branches_create` → probe → `branches_delete` makes that test free and
   safe; run it before documenting any mechanism.

   What a blind retry costs depends on *what* you wrote:
   - **Nodes are safe to retry.** Every type in `memory.pg` is `@key(slug)`, so a
     repeated `insert` of the same slug upserts rather than duplicating (verified
     2026-07-16: two identical `insert Preference` calls → one node). Omnigraph's
     own docs warn that "append-only types (`Signal`, `Claim`, `Decision`, …)
     duplicate on retry" — that taxonomy is the **reference cookbook's schema, not
     ours**; our `Decision` is slug-keyed. Don't inherit the warning blindly.
   - **Edges are NOT.** Edges have no `@key`, so a retried edge insert duplicates
     (rule 6). Verify edges before re-running anything that inserts them.
   - A **`manifest_conflict` 409** (someone committed between your snapshot pin and
     your write) and a **429** (`Retry-After`, per-actor admission control) are both
     *always* safe to retry: the write never committed, so there is no partial state.
   - `version drift … call sync_branch()` is a server-internal directive that leaked
     into the error text — **`sync_branch()` is not a tool or CLI command.** Retry;
     the next call re-pins. If it persists, write onto a fresh branch via
     `load --from main` instead, which doesn't suffer `main`'s concurrent-commit drift.

6b. **`fetch failed` on every tool, or `Lance HEAD version N ahead of manifest version M; a pending
   recovery sidecar requires rollback` on every `insert` of one node type - the STORE broke under
   concurrent writers, not your query.** Measured 2026-09-05 with three unattended sessions writing
   the `research-repo` graph at once: MinIO logged `taking drive /data offline: unable to write+read for
   30s` and `file is corrupted, Object buckets/.bloomcycle.bin`; one session's mutation died with a
   503 mid-write; every later `insert Decision` failed with the sidecar error while reads worked.
   Recovery, in order: move `/data/.minio.sys/buckets/.bloomcycle.bin` aside inside the
   `omnigraph-minio` container (MinIO recreates it); `docker restart omnigraph-minio` and wait for
   `/minio/health/cluster` = 200; `docker restart omnigraph-server` - it logs `recovery: rolling
   back sidecar`, then takes **~13 minutes** to load all graphs (AIMD throttle warnings are
   normal); its health path is **`/healthz`**, not `/health`. A batch that failed mid-way landed
   NOTHING (verified by reading the slugs back), so re-insert only what is missing - never the
   whole batch (rule 7: edges duplicate).

7. **Duplicate edges are the classic trap.** Edges are **not** slug-keyed, so a
   cross-store `load --mode merge` (device-branch merge, reconciling two clients)
   **appends** them → duplicates. There is **no API to delete an individual edge**
   (edges expose no queryable `id`; `where from=.. and to=..` doesn't parse).
   Two fixes:
   - **Client-side:** deleting a **node cascades its edges** (verified). To fix a
     node's dup edges, `delete <Type> where slug=$s` then `load --mode merge` the
     node back with its correct single edges — a fresh load onto an edge-free node
     makes no dups.
   - **Server-side (bulk):** `infra/mcp-servers/scripts/dedup-graph.py`
     (edge-aware — triggers on node OR edge dups; export → dedup by
     `(type,from,to)` → reset store → overwrite-load). Needs docker on the server
     host; runs hourly by timer on coding.example.internal.
   - **Prevent it:** prefer native `branches_merge` (edge-de-duplicating) over raw
     `load --merge` for cross-store reconciliation.

8. **Never `overwrite` a shared `main`.** It clobbers other projects' data, and
   overwrite-on-a-populated graph hits a Lance index bug on v0.8.1. Use
   `merge` / `branches_merge` on `main`; `overwrite` is only safe on a fresh store
   or a throwaway branch you own.

9. **Replace one project's subgraph on a shared `main` cleanly** (the proven
   recipe, e.g. pushing a repo's refreshed memory up): (a) `delete <Type> where
   slug=..` every child node of that project (edges cascade); (b) `load --mode
   merge` the project's node+edge subset exported from the source (fresh → zero
   dup edges). Keep the `Project` node; select the subset by slug prefix. Verify
   the two sides' node+edge sets are then identical.

10. **Slugs: lowercase kebab-case, stable.** Auto-merge keys on `slug`; a case
   variant (`Finance-Repo` vs `finance-repo`) or drift creates a **duplicate node** it can't
   collapse on its own (then needs `dedup-graph.py`). Reuse the same slug to make
   re-writes idempotent.

11. **`load --mode merge` does NOT (re)compute embeddings — merged Decisions go
    INVISIBLE to vector search.** This is Omnigraph's documented behaviour
    (`omnigraph://best-practices/search`): merge updates the `@embed("rationale")`
    source but leaves the vector stale, and a `Decision` with a null `embedding` is
    simply **dropped from `nearest()` results** — it does not rank last, it does not
    appear. Since the persist protocol tells you to merge-load, every Decision you
    write is unsearchable until embedded.

    Not theoretical — measured 2026-07-16, after a few sessions of merge-loading:

    | graph | Decisions embedded | missing |
    |---|---|---|
    | `agent-skills` | 3 | **3** |
    | `sibling-analysis-repo` | 15 | **2** |
    | `finance-repo` | 14 | 0 |

    **Worse than the upstream docs say: merge doesn't leave the vector stale, it
    ERASES it.** Omnigraph's `best-practices/search` says the source updates while
    "the embedding stays stale". Verified here 2026-07-16: merge-loading an existing
    `Decision` with a record that omits `embedding` sets the field to **null** — the
    upsert replaces the row. `omnigraph-over-mem0` had a vector, was merged with a
    corrected rationale, and dropped out of `nearest()` entirely. So editing a
    Decision's rationale silently un-indexes it.

    **On v0.8.1 there is currently NO working way to (re)embed on a populated
    graph.** Both documented paths hit the same Lance bug (verified 2026-07-16,
    both failed *staged*, leaving the graph intact):

    | attempt | result |
    |---|---|
    | `load --mode overwrite` (populated graph) | `stage_create_btree_index on node:Rule(["id"]) … all columns in a record batch must have the same length` |
    | `load --mode merge` carrying hand-supplied vectors | `LanceError(Arrow): … all columns in a record batch must have the same length` |
    | `omnigraph embed --reembed_all` | can't target a local endpoint on v0.8.1 |

    `scripts/populate-embeddings.py` therefore only works against a **fresh/empty**
    graph — which is why it succeeded at first seed and fails now. Until this is
    fixed (server upgrade, or a supported re-embed path), the options are:
    - **Accept it.** New/edited Decisions are findable by traversal and full-text,
      just not by `nearest()`. Given a graph this small, that is usually fine.
    - **Rebuild** via `scripts/dedup-graph.py`, which resets the store and reloads
      each graph into an *empty* store — the one load path that works — feeding it
      embedded data.

    Whichever you pick: **don't claim semantic search covers a Decision you merged
    without checking.** Confirm with a `nearest()` query that the slug comes back.

    **Embeddings = `nomic-embed-text` (768-dim), CPU-capable** — the provider
    declared in `cluster/cluster.yaml`, matching `Vector(768)` in `memory.pg`. Two
    clients exist and must agree on dimension: the **load-time** one that fills
    `@embed` fields, and the **query-time** one the server uses to auto-embed a
    *string* passed to `nearest($v, "text")`. Point them at different-dimension
    models and similarity search returns garbage or errors. Ollama is optional —
    without it recall degrades to graph traversal + scalar indexes rather than
    failing.

12. **You do not manage device branches.** Write durable memory to `main`; the
    sync automation (`omnigraph-setup/omnigraph-sync.sh` / `sync-windows.ps1` on a timer)
    creates `device/<host>`, merges to central `main`, and reconciles back. The
    sync **verify gate refuses to pull a central that has duplicates** — that is
    correct (it protects a clean local); clean central (rule 6) then re-sync.

13. **Remote ops from a client** use the CLI-in-container (bearer + public URL),
    e.g. `docker run --rm -i --network <compose-net> -e OMNIGRAPH_BEARER_TOKEN=…
    --entrypoint omnigraph <image> query|mutate|load|export --server <URL>
    --graph memory`. Destructive remote writes need `--yes`. You **cannot** reset
    the central store from a client (needs coding.example.internal docker access).

## Quick reference — safe write patterns

| Goal | Do |
|---|---|
| Upsert nodes idempotently | `load --mode merge` (keyed by slug) |
| Add an edge | `insert <PascalEdge> { from, to }` (once — re-running dups it) |
| Change a Decision | new `Decision` + `insert Supersedes {from:new,to:old}`; set old `status: superseded` |
| Remove a node's dup edges | `delete <Type> where slug=$s` → `load --merge` node + correct edges |
| Push a repo's memory to central | delete its child nodes on `main` → `load --merge` its fresh subset |
| Bulk clean dups | run `scripts/dedup-graph.py` on the server host |

See also: [schema.md](schema.md), `infra/mcp-servers/omnigraph-setup/README.md`,
`docs/REMOTE-SYNC-TEST-PLAN.md`.
