# Memory facade spec (MEMSPEC)

**Status:** SPEC ONLY — no implementation. This document is reviewed and sent to
the operator for approval before anything gets built.
**Owner:** L1-backlog (SPEC-OMNI/MEMSPEC). **Date:** 2026-09-28.
**Sources:** `routing/docs/PLAN.md` §14 (Knowledge memory) and §16 (Context
provenance); `routing/DECISIONS.md` D-037, D-038, D-039, D-042; the fleet console
spec `docs/plans/2026-09-28-fleet-console-spec.md` §4.3 (Memory events,
`schema: 2`) and §8.6 (Memory health). Design facts below come from those
sources only; anything else is marked (inference) or left as an operator
question in §13.

This spec defines one knowledge graph with hubs, a typed memory facade MCP as
its only writer, a curator that keeps it clean, and the graph used as a
cross-session event channel. Details follow in §§1–14.

## 1. Summary

One graph with hubs (Project/Domain/Host/Technology) holds durable knowledge; a
typed memory facade MCP (`recall` / `context_pack` / `remember` / `link` /
`supersede`) is the only writer; a curator (light pass ~15 min, deep pass
nightly) keeps it clean; and the graph doubles as a cross-session event
channel, emitting a `schema: 2` event on every write. Owner L1-backlog
(SPEC-OMNI/MEMSPEC). Phased: spec (this doc) → operator approval → bake-off →
build. (Satisfies D-037 facade-first, D-038 one graph, D-039 write+curate.)

## 2. Why

Omnigraph (the current engine) is not broken — the write path and sync topology
are. `PLAN.md` §14's "Why", in substance: the duplicates come from edges
without a unique key; merge-loads and blind retries after timeouts; five graphs
synced hub-and-spoke through device branches; re-applied seeds. Agents also
fail at the custom `.gq` query language, and one user-scope MCP entry once
pointed agents at the wrong graph. So this spec fixes the write path (one typed
facade, canonical ids, idempotent links) and the topology (one graph, one
central writer) without assuming the engine is at fault — which is exactly why
§9 runs a bake-off instead of declaring a winner.

## 3. Where each kind of memory lives

Reproduced from `PLAN.md` §14 (unchanged):

| Kind | Home | Lifecycle |
|---|---|---|
| Rules and instructions | CLAUDE.md / AGENTS.md / skills, kept small | changed via PR |
| Durable knowledge: decisions + why, lessons/traps, components, hosts, preferences, research findings with sources | **one knowledge graph** | written through the facade, maintained nightly |
| Activity: runs, spawns, questions, costs | the console DB | append-only; the graph links to it by id, never copies it |
| Code structure | Graphify per repo (derived) | rebuilt; graph Component nodes point to paths |
| Working state | state cards | overwritten |

Why the split: the graph stores what agents must recall across sessions; the
console DB stores what happened; Graphify derives what the code looks like;
state cards hold what is true right now. The graph links to activity rows by
id — it never copies them (D-039/D-042 lineage depends on ids, not copies).

## 4. The facade — typed MCP API

The facade is a memory service and the only writer. It is model-agnostic and is
served as an MCP server (D-037: typed facade first —
`recall`/`remember`/`link`/`supersede`/`context_pack`).

| Method | Contract |
|---|---|
| `recall(query, project?, k)` | ≤600 tokens of compact lines with ids (+ versions, §12) |
| `context_pack(project, role)` | ≤1.5k tokens for session start |
| `remember(kind, title, body, project, links, source)` | canonical id `kind:slug`; alias checks exact → normalised → similarity; upserts; reports "merged into X" on a duplicate |
| `link(a, rel, b)` | idempotent on `(a, rel, b)` — repeat calls are no-ops |
| `supersede(old, new, reason)` | closes the old fact (`valid_to`), keeps its history |

Write-side rules the facade enforces on EVERY write:

- Every node links to at least one hub (Project, Domain, Host, or Technology).
- Relation types come from a closed list.
- A body is at most 600 characters; details live behind a source pointer.
- A secret scan runs on every write.

Every write returns and records: the entity id, the NEW `version_id`, the
author session name + restart generation id (D-042 context provenance), and the
source. Superseded versions stay readable — nothing is ever hard-deleted, which
is the same rule the event schema states as "no `deleted` verb" (§5).

Read path notes (recall quality is the point of the facade):

- `recall` ranks by text + graph relevance (hub distance counts: a fact one hop
  from the project hub outranks a far one) and returns compact lines
  (`id | kind | title`, ≤600 tokens total) — agents fetch full text by id only
  when needed.
- `context_pack` is the session-start bundle: project hub facts, open
  contradictions (superseded-but-unresolved), and recent `merged`/`superseded`
  events for that project/domain — ≤1.5k tokens. What the pack contained is
  provenance (§12), so the facade logs the ids + versions it served.

## 5. The graph as a cross-session channel

Every facade write emits the event agreed with L2-general in the fleet console
spec `docs/plans/2026-09-28-fleet-console-spec.md` §4.3 ("Memory events (A1 —
agreed with L1-backlog, `schema: 2`)"). That section is the contract; this spec
does not invent a different shape. Reproduced verbatim:

```
{ id: ULID, at: <ISO UTC>,
  type: "memory.<entity_kind>.<created|updated|merged|superseded|redirected>",
  project, domain?, entity_id: <stable id>, entity_kind,
  version_id,                       // the new node version
  prev_version_id?,                 // updated | superseded only
  gen,                              // restart generation id (D-042)
  title: "<=120 chars>", actor: {kind: agent|operator|curator, id: <author session name>},
  source_ref?, supersedes?, merged_from?,
  visibility: project|global, schema: 2 }
```

Rules that go with it (from §4.3, restated by reference, not redefined here):
no `deleted` verb — an orphan prune is a `superseded` tombstone, undoable;
merged ids stay resolvable via a `redirected` event carrying `merged_from`,
and undoing a merge is an inverse split event, never a rewind; edges have their
own events `memory.edge.<linked|unlinked> {edge_id, rel (closed list),
from_id, to_id, version_id}`; no body or secret in any event, only `title`
(≤120 chars) — text is fetched by `entity_id` + `version_id`; append-only
store, at-least-once delivery on a per-subscriber cursor; health via
`memory_health()` (nodes, edges, duplicate rate, orphan share, mean hops to
hub, curator merges); `actor.id` is the author session name, never a person.

Write→visible-to-others latency is a HARD requirement, target <5 s (D-039).
The bake-off (§9) explicitly measures it — because Omnigraph has a documented
stale-index case where writes are invisible to traversals until a server
restart. Visibility is measured, not assumed.

## 6. Curator

D-039, second half: EVERY agent writes AND a curator curates.

- Light pass every ~15 min over new writes: merge duplicate notes, attach
  nodes to hubs, supersede contradicted facts.
- Deep pass nightly: similarity dedup, orphan pruning, community detection
  with ≤120-word cluster summaries, health metrics.
- Runs on a cheap bulk model (DeepSeek once available/cleared for use, else
  free-tier).

Every curator merge is logged and undoable — via the §5 event rules
(`merged` / `redirected` / inverse-split undo). This section does not restate
that schema; §5 owns it.

## 7. Graph shape

Hubs (Project, Domain, Host, Technology) keep paths short; shared
Technology/Component nodes connect projects (D-038). Health is reported to the
console: nodes, edges, duplicate rate, orphan share, mean hops to a hub — this
is exactly the facade's `memory_health()`, which the fleet console spec's
§8.6 ("Memory health") reads; fleetd computes none of it (see §4.3/§8.6 of
that spec).

## 8. Sync

One central writer, no multi-master branches (D-038). The offsite copy is a
read-only replica/backup with a TESTED restore — the restore test is a
concrete runbook item (§10, step 0), not a claim. Consequence: no agent, device,
or lane ever writes to the replica or to a branch expecting a merge back — all
writes go through the one facade, which is also what makes the §5 event stream
complete (a write the facade never saw emits no event).

## 9. Bake-off

Same facade, same data, same 30 real questions with expected answers — three
engines (from `PLAN.md` §14):

| Engine | Notes |
|---|---|
| Omnigraph as one graph | today's engine |
| A graph in the console DB | nodes/edges with unique constraints, full-text + vectors, paths/communities in code |
| Graphiti on FalkorDB | built-in resolution and superseding |

Judged on (each metric earns its place — one line on why):

| Metric | What it proves |
|---|---|
| Answer correctness | the engine answers the 30 questions right |
| Tokens per recall | recall fits the ≤600-token budget with headroom |
| Write→visible latency across sessions | the <5 s channel requirement (§5) — measured, not assumed |
| Duplicate rate after a week of writes | the write path actually dedups (the §2 disease, re-tested) |
| p95 latency | session-start `context_pack` stays fast at tail, not just median |
| RAM | the engine fits the machines it runs on |
| Ops incidents | anything the numbers miss (restarts, stuck indexes, 3 a.m. pages) |

This spec does not pick a winner — the bake-off does, after the
facade exists (facade-first, per D-037).

## 10. Migration runbook

The 7 steps from `PLAN.md` §14, expanded into an actual runbook with a dry run,
a backup plus a TESTED restore, and an explicit rollback path:

0. **Backup + test the restore first.** Full backup of all 5 graphs before
   touching anything live; restore it to a scratch location and verify counts.
   Do not proceed until the restore is proven. (This is also the §8 restore
   test.)
1. **Dry run.** Run steps 2–6 end-to-end against fixture/small-graph data
   (§11 ladder, rung R0/R1). Record expected counts. The dry run must pass
   before the real migration starts.
2. **Export the 5 graphs.**
3. **Normalise ids** to the canonical `kind:slug` form (§4).
4. **Dedup in three passes:** exact key → similarity → a cheap model verifies
   only the ambiguous cases.
5. **Make edges unique** on `(from_id, rel, to_id)`.
6. **Import into one graph** behind the facade (all writes go through
   `remember`/`link`, so alias and hub rules apply during import too).
7. **Verify:** node/edge counts against the dry-run expectations, plus the 30
   recall questions with expected answers. **Rollback path:** if verification
   fails, stop, keep serving the old graphs, and diagnose from the dry-run
   baseline — the old graphs stay read-only for 30 days regardless, so reads
   never depend on the new graph until it verifies.

## 11. Phases + data ladder

| Phase | Work | Exit criterion |
|---|---|---|
| P0 spec | this doc | operator approval |
| P1 facade skeleton | facade + `schema: 2` events wired to one engine (§5 envelope) | `recall`/`remember`/`link`/`supersede`/`context_pack` round-trip; events validate against §4.3 |
| P2 bake-off | run §9 on all three engines | decision recorded; winner named by measurement, not by this spec |
| P3 migration + curator light | §10 runbook; light pass live | counts + 30 questions verify; old graphs read-only 30 days |
| P4 curator deep + console health | deep pass, community summaries, `memory_health()` in console (§8.6 of the fleet spec) | health visible; duplicate rate and orphan share within operator-set bounds (§13 Q5) |

Data ladder — climb one rung at a time:

- **R0 synthetic/small fixture data:** proves the facade, events, and runbook
  mechanics without touching anything real. Climb when the dry run (§10 step 1)
  passes.
- **R1 a copy of one real small graph:** proves normalisation + dedup on real
  dirt. Climb when counts verify and the 30 questions pass on the copy.
- **R2 the full migration:** all 5 graphs per §10. Done when §10 step 7
  verifies; otherwise take the rollback path.

## 12. Context provenance hook (D-042)

Ownership boundary, per `routing/docs/PLAN.md` §16: this spec OWNS returning
node ids + version numbers in every `recall` / `context_pack` result and
keeping version history (superseded versions stay readable, §4). RESTART (a
separate lane) writes and hashes the context packs themselves; FLEETSPEC
stores and renders the lineage/diff/"why"/replay views. Recalls store ids +
versions, not text, so the exact text shown can be rebuilt from version
history. Nobody re-litigates who owns what: ids + versions here, packs with
RESTART, views with FLEETSPEC.

D-042 (quoted): "Context provenance: every spawn brief, context pack
(restart), state card version, memory recall (ids + versions) and included
event is recorded content-addressed and linked to the outputs made under it;
console shows lineage, pack diffs, "why this decision" and replay; packs kept
90 d, decision links forever". The `gen` field on every §5 event is this
spec's half of that link.

## 13. Operator questions

Format imitates the project's operator-question convention (`Q: … | options:
… | default: … | blocks: … | reversible: …`).

Q: default engine if something needs memory NOW, before the bake-off
completes | options: (a) Omnigraph as one graph (b) console-DB graph (c) no
facade writes until the bake-off decides | default: (a) because the engine
exists today and the facade isolates callers from the choice | blocks: P1
engine wiring | reversible: yes (facade-first means the engine is
swappable). Decided: (a), routing-00 (D-067, 2026-09-28).

Q: how long superseded versions are kept before physical cleanup | options:
(a) kept forever (b) pruned after N days | default: (a) because D-042 keeps
decision links forever and the event schema has no `deleted` verb | blocks:
storage sizing | reversible: no (deletion is irreversible).
Decided: (a), routing-00 (D-067, 2026-09-28).

Q: who reviews the curator's undo log | options: (a) log-only, undo on
demand (b) operator spot-checks weekly (c) any agent can flag a bad merge |
default: (a) because every merge is an undoable event (§5), so review can be
lazy | blocks: nothing | reversible: yes.
Decided: (a), routing-00 (D-067, 2026-09-28).

Q: whether `global` visibility events cross project boundaries by default |
options: (a) yes, `global` means every project feed (b) opt-in per project |
default: (a) because that is what the visibility flag says, and events carry
titles only (no bodies, no persons — §5), so the exposure is small | blocks:
feed subscription semantics | reversible: yes.
Decided: (a), routing-00 (D-067, 2026-09-28).

Q: health bounds that fail a phase gate (duplicate rate, orphan share) |
options: (a) operator sets numbers at P3 entry (b) fixed in this spec now |
default: (a) because only the bake-off + R1 rung produce real numbers to set
them from | blocks: P3/P4 exit criteria | reversible: yes.
Decided: (a), routing-00 (D-067, 2026-09-28).

**Build-approval timing (Q-016, tracked separately by routing-00, not part of this spec's own §13 list):** whether to start P1 (facade skeleton) + P2 (bake-off) now versus waiting for a fuller operator review is Q-016, owned by the operator via routing-00. Default if unanswered: start P1 + P2 now, P3 migration and P4 later once the bake-off has real numbers — auto-accepts 2026-09-28T23:30Z if the operator has not answered (reversible: no real graph is touched by P1/P2). This spec does not depend on Q-016's outcome — §11's phases already sequence P1/P2 before P3/P4.

## 14. Open follow-ups / non-goals

Explicitly out of scope for this spec:

- Choosing the winning engine — the bake-off's job (§9, D-037).
- The actual curator model routing — the model registry's job (§6 names the
  class of model only).
- The console's rendering of any of this (feed, hub-neighbourhood view,
  `memory_health()` display, lineage/diff/"why"/replay) — FLEETSPEC's job,
  already spec'd in the fleet console spec §§4.3, 8.6–8.9.
- Pack storage, hashing, and retention mechanics — RESTART's job (§12).

---

Operator decisions satisfied (grep-checkable): D-037 (facade-first + bake-off,
§§4, 9); D-038 (one graph with hubs, single central writer, offsite
read-only, §§7, 8); D-039 (every agent writes + curator light/deep, <5 s
channel, §§4, 5, 6); D-042 (ids + versions in recalls, version history, `gen`
on events, §§4, 5, 12). Event envelope: §5 reproduces the fleet console spec
§4.3 `schema: 2` block verbatim.
