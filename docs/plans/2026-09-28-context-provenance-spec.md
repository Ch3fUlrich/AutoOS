# Context provenance — the fleet-side rendering/query layer (FLEETSPEC-owned spec)

Status: SPEC (no implementation). One file only; composes RESTART's store,
MEMSPEC's recall scheme, and the fleet console's A4 design — replaces none of them.

Citation rule used here: every `path:line` cites a file in THIS branch and was
verified to resolve before writing. Anything that lives only on a sibling lane
is named as `sibling branch <name>, <file> §<n>` with NO `path:line` form, so a
reader can tell at a glance what resolves here and what does not.

## 1. Summary

This is a FLEETSPEC-owned rendering/query layer over RESTART's already-designed
provenance storage (`docs/plans/2026-09-28-restart-spec.md:277-319`), not a new
storage system — and its scope is smaller than the brief first suggested, in two
directions. First, the brief's premise that the fleet console has "NO section
covering lineage/diff/why/replay" is stale: the console spec on sibling branch
`L2-general/fleetspec` (`docs/plans/2026-09-28-fleet-console-spec.md` §§4.5,
8.8–8.9) already designs the fleetd-side tables (`generations`, `pack_blobs`,
`manifest_items`, `output_links`, `transcript_refs`), the lineage and
"why this decision?" views, pack diff, replay, and `GET /api/provenance/:gen_id`
— so this spec does NOT re-derive any of that, it cites it. Second, what is
genuinely still undesigned anywhere is the *file-store query mechanics*: how a
console (or a CLI, before fleetd exists) finds "all manifests for lineage X" or
"all generations that used pack P" efficiently from an append-only
`events.jsonl` plus content-addressed blobs, and the exact machine-readable
decision→generation linkage that makes "why this decision?" work off files
alone. That index (§4) plus the linkage rule (§5) plus migration order (§7) is
this spec's net-new content; everything else is composition with pointers.

## 2. What's already built (designed, cited — not re-derived)

RESTART §6 (`docs/plans/2026-09-28-restart-spec.md:277-319`) specs the whole
storage layer this spec reads from. Goal: lineage for every context a session
starts from, exact replay for 90 days, lineage proofs forever
(`docs/plans/2026-09-28-restart-spec.md:279-282`).

- **Store** (`docs/plans/2026-09-28-restart-spec.md:293-299`): `logs/context/`
  (git-ignored), module `tools/autoos_context_store.py` (new — NOT yet built in
  this branch: no `context_store`, `--replay`, or `--diff` string occurs anywhere
  under `tools/`). `blobs/<sha256>` holds each pack part once, content-addressed
  (`docs/plans/2026-09-28-restart-spec.md:295-296`); `manifests/<manifest-id>.json`
  where the id is the first 16 hex of the canonical manifest sha
  (`docs/plans/2026-09-28-restart-spec.md:297`); `events.jsonl` is append-only
  with one line per pack, spawn, or card version
  (`docs/plans/2026-09-28-restart-spec.md:298-299`).
- **Manifest fields** (`docs/plans/2026-09-28-restart-spec.md:300-306`): `type`
  (`context_pack`|`spawn`|`card`), `session`,
  `generation`/`parent` (previous manifest +1 / its id), `reason` (`cap`|`clear`|
  `crash`|`operator`|`first`), `parts` (name→blob sha) + `prefix_version`,
  `memory` (recall ids+versions; stub `[]` until MEMSPEC), `events` (first/last
  position, count), `tokens`, `created`. Canonical JSON is pinned
  (`docs/plans/2026-09-28-restart-spec.md:289-291`); blobs hold redacted bytes via
  the one secret-pattern module
  (`docs/plans/2026-09-28-restart-spec.md:284-288`, module live at
  `tools/autoos_redact.py:1-10`).
- **Carry-through** (`docs/plans/2026-09-28-restart-spec.md:307-309`): the pack's
  first line is `gen=<manifest-id>`, copied into the card header (header shape at
  `docs/plans/2026-09-28-restart-spec.md:141`); `ready` appends `gen=<id>` from the
  card when given `--card`; hand-written question/decision lines carry it by rule
  (R6). Spawn briefs get `spawn` manifests and print `gen=<id>`
  (`docs/plans/2026-09-28-restart-spec.md:310-312`); passing `card check` stores a
  `card` blob + event so any two waves diff
  (`docs/plans/2026-09-28-restart-spec.md:313-314`).
- **Retention / replay** (`docs/plans/2026-09-28-restart-spec.md:316-319`):
  `context-store prune` deletes blobs older than 90 days unreferenced by recent
  manifests; manifests and `events.jsonl` live forever; `pack --replay` rebuilds
  exact bytes (exit 1 naming a pruned blob); `pack --diff` diffs two packs part
  by part.
- **Memory slot**: the pack's memory part is the stub `memory: not wired
  (MEMSPEC)` (`docs/plans/2026-09-28-restart-spec.md:201`); memory recall itself
  is an explicit non-goal of RESTART
  (`docs/plans/2026-09-28-restart-spec.md:61-65`).
- **Pointer-not-copy precedent**: full transcripts stay in
  `~/.claude/projects/*.jsonl` and records point by file+offset. The discovery
  functions are `project_dir` (`tools/autoos_context.py:191-195`, slug-mangled
  cwd under `~/.claude/projects`) and `discover_transcript`
  (`tools/autoos_context.py:198-212`, newest `*.jsonl` by mtime, None when
  absent); usage-record iteration is `fill_from_transcript`
  (`tools/autoos_context.py:130-161`). This spec's transcript links follow the
  same pointer pattern.

MEMSPEC (inference, MEMSPEC spec not in this checkout's working tree): no
`*memspec*` doc exists under `docs/plans/` in this branch. The scheme this spec
assumes — recalls return node ids + versions, every write records a new
`version_id`, superseded versions stay readable, the facade logs which
ids+versions it served — is verified against sibling branch `L1-backlog/memspec`
(`docs/plans/2026-09-28-memory-facade-spec.md`, recall/version/event-envelope
contract) but does NOT resolve to a `path:line` here; if that lane changes the
scheme, §§3–5 below track it without changing shape (they consume ids+versions,
never graph internals).

fleetd/event-bus (inference, not in this checkout's working tree): no
`fleetd-eventbus` doc under `docs/plans/` in this branch. Sibling branch
`L2-general/fleetd-spec` (`docs/plans/2026-09-28-fleetd-eventbus-spec.md`) covers
`hosts`/`agents`/`spawn`/`send`/`inbox`/`events` and states fleetd "stores
references, not blobs" — it holds NO provenance table, so this spec defers all
provenance rows to the console spec's §4.5 and designs nothing fleetd-side.

## 3. The gap: four console views

Each view lists the query it runs, what it renders, and whether fleetd needs
anything new. Verdict up front: fleetd needs NOTHING new — the sibling console
spec's §4.5 tables plus `GET /api/provenance/:gen_id` already cover the
fleetd side (sibling branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md` §§4.5, 7.1, 8.8–8.9). What was
missing is the file-store mechanics underneath, designed in §§4–5 here.

1. **Lineage timeline** — generations 1..n for one agent identity, with the
   restart reason per generation. Query against the file store: §4 lineage scan
   (filter `events.jsonl` on `session` AND `type=context_pack` — `card`
   versions are excluded from this view, see §4 — order by `generation`), or —
   once fleetd exists — the sibling `generations` table. Renders one row per
   `context_pack` manifest:
   generation (the manifest's `generation` field verbatim, packs only), reason (RESTART's `reason` enum reused VERBATIM —
   `cap`|`clear`|`crash`|`operator`|`first` — per
   `docs/plans/2026-09-28-restart-spec.md:303`; no new enum anywhere in this
   spec), pack item count, timestamp. No new fleetd endpoint: the sibling
   `generations` table + lineage view already answer it.
2. **Pack diff** — what a successor got that its predecessor had or lacked,
   between any two manifest ids of the same lineage. Query: fetch both manifests
   (§4 point lookup), compare `parts` name→sha (added/removed/changed blobs),
   compare `memory` id+version lists, compare `events` windows. Render is the
   sibling §8.8 `▸diff` affordance; the byte-level diff itself is RESTART's
   `pack --diff` (`docs/plans/2026-09-28-restart-spec.md:318-319`). No new
   fleetd endpoint: the sibling `GET /api/provenance/:gen_id` takes a second
   `gen_id` for exactly this comparison.
3. **"Why this decision?"** — from a decision (ready line, `Q:`/`D-` entry,
   memory write) back to the generation it was made under, the pack it came
   from, and the cards/facts/events in that pack with sources and authors.
   Query: read the decision's trailing `gen=<id>` (§5) → manifest point lookup →
   `parts` blobs (cards, brief, events slice) + `memory` ids@versions resolved
   through MEMSPEC version history (inference: sibling-lane scheme, §2). Renders
   the sibling §8.9 trace (`decision → gen_id → pack → manifest_items →
   sources/authors`). No new fleetd endpoint: the sibling `output_links` table
   is the fleetd-side form of the same `gen=<id>` linkage §5 specifies for files.
4. **"Replay pack" button** — reconstruct and show the exact text an agent
   started from. Query: manifest → `parts` shas → blobs, concatenated in pack
   order. This is a pure read/reconstruction over RESTART's store
   (`docs/plans/2026-09-28-restart-spec.md:318-319`, exit 1 naming any blob lost
   to the 90-day prune at `docs/plans/2026-09-28-restart-spec.md:316-317`), not
   new storage. No new fleetd endpoint: the sibling `pack_blobs` (90d) table is
   the fleetd-side cache of the same bytes.

## 4. Query/read model

The problem: `events.jsonl` is append-only and blobs are content-addressed, so
there is no "all manifests for lineage X" lookup without scanning. The answer is
an index the console (or CLI pre-fleetd) builds from the event stream — one
forward pass, incrementally maintained — not queries against the files per view:

- **Build**: read `events.jsonl` from byte offset 0 (or from the saved cursor on
  later runs). Each line is one `context_pack`|`spawn`|`card` event carrying the
  full manifest inline (per `docs/plans/2026-09-28-restart-spec.md:298-299`).
  Cursor = `(byte_offset, line_count)` persisted beside the index; at-least-once
  re-read from the cursor is safe because manifest ids are content hashes —
  re-indexing an id is idempotent. Concretely: `manifests` overwrites the same
  key, while `lineage` and `blob_use` append a manifest_id only if it is not
  already present in that key's list — builders hold a per-key seen-set during
  a rebuild-from-cursor pass, so a re-read never duplicates a list entry.
- **Shape** (three maps, all derived — no new stored truth):
  - `lineage: session_name → [manifest_id ordered by generation]` — answers
    "all manifests for lineage X". Generation order comes from the manifest's
    `generation` field (`docs/plans/2026-09-28-restart-spec.md:302`), not file
    order (concurrent sessions interleave in one stream).
  - `manifests: manifest_id → byte_offset in events.jsonl` — O(1) point lookup
    for diff/why/replay without reparsing the stream. Full manifest JSON is
    re-read from the offset on demand; only the offset table stays resident.
    The stream is the index's source of truth — not the per-manifest files at
    `manifests/<id>.json` (`docs/plans/2026-09-28-restart-spec.md:297`) —
    because the stream gives global ordering plus a single sequential read
    path, while the manifest files are the replay/diff source of record, read
    on demand and never indexed directly. If `events.jsonl` is ever unreadable
    while manifest files survive, the index rebuilds from a scan of
    `manifests/*.json` (ordering via each manifest's `generation`/`parent`
    chain), so the stream is the fast path, not the only path.
  - `blob_use: blob_sha → [manifest_id, …]` — answers "which manifests
    referenced blob B". "Which successors share pack P's prefix" is answered by
    expanding P's own blob shas first (its `parts` map), then unioning
    `blob_use` over them — identical-prefix successors fall out of the dedup
    property of `docs/plans/2026-09-28-restart-spec.md:295-296`.
    Built by inverting each manifest's `parts` map
    (`docs/plans/2026-09-28-restart-spec.md:304`).
- **Card versions** ride the same stream: `card`-type events index under
  `lineage[session]` with their `parent` chain
  (`docs/plans/2026-09-28-restart-spec.md:313-314`), so "diff any two waves" is
  two point lookups, never a scan. View 1 (§3.1) excludes them by filtering its
  query to `type=context_pack`, so its "generation" column is the manifest's
  `generation` field (`docs/plans/2026-09-28-restart-spec.md:302`) over packs
  only, shown verbatim — numbers may skip where an intervening `card` version
  consumed a generation, and the view does not renumber them.
- **fleetd ingestion** (inference: sibling lanes own both ends) is one consumer
  of this same pass: each new event line maps to rows in the sibling §4.5 tables
  (`generations`, `manifest_items`, `pack_blobs`, `output_links`,
  `transcript_refs`) — the console spec's §5 ingestion, not designed here.
- **Cost**: one full pass is O(events); steady state is O(new lines). The index
  itself is proportional to manifests + parts references (small: 16-hex ids and
  offsets), never to blob bytes — blobs are opened only by replay/diff.

## 5. Decision linkage

RESTART's carry-through convention already answers this and is cited, not
extended in mechanism — only pinned to a machine-readable form so "why this
decision" works off files alone:

- The pack's first line is `gen=<manifest-id>` and the session copies it into
  its card header (`docs/plans/2026-09-28-restart-spec.md:307-309`, header shape
  `docs/plans/2026-09-28-restart-spec.md:141`); `ready` lines append `gen=<id>`
  from the card when passed `--card`; hand-written question/decision lines carry
  it by rule (`docs/plans/2026-09-28-restart-spec.md:307-309`).
- **The one pin this spec adds**: the linkage token is always the literal suffix
  `gen=<16-hex-manifest-id>` (the id form at
  `docs/plans/2026-09-28-restart-spec.md:297`) on decision lines — trailing the
  ready line or `Q:`/`D-` entry. The same token is carried, not as a text
  suffix, in the other two forms: mid-line in the card header
  (`| gen=<manifest-id> |`, header shape at
  `docs/plans/2026-09-28-restart-spec.md:141`) and as the JSON `gen` field value
  on a memory write (inference: the sibling MEMSPEC lane on branch
  `L1-backlog/memspec`, `docs/plans/2026-09-28-memory-facade-spec.md`
  recall/version/event-envelope contract, already records author session +
  restart generation id on every write — files-side, the `gen` field IS the
  token).
  No second id scheme, no lookup table: the token resolves through the §4
  `manifests` map directly.
- **What this deliberately does NOT add**: no signature, no ack, no backlink
  from manifest to decisions. Forward links (decision→manifest) are written at
  decision time by the decider; reverse lookup ("every decision made under pack
  P") is a text scan for the token, acceptable because it is a diagnostic query,
  not a hot path — and the fleetd side already has the indexed form (sibling
  `output_links`).

## 6. UI sketch

Style follows the sibling console spec's §8 Mobile UX convention (sibling branch
`L2-general/fleetspec`, `docs/plans/2026-09-28-fleet-console-spec.md` §8):
~40 cols, `+---+` boxes, `▸` affordances — these four views extend the sibling
§§8.8–8.9 sketches to the file-store fields (manifest ids, reasons, prune
states), they do not restyle them. Where the sibling §8.8 sketch shows
`kind: spawn|restart` with its own reason strings (e.g. `"split"`,
`"ctx 82% (RESTART)"`), the file-local views here use RESTART's own `reason`
enum verbatim (`cap`|`clear`|`crash`|`operator`|`first`); reconciling the two
vocabularies into the sibling column is owned by the §7 step-4 ingestion
mapping, not by these sketches.

```
Lineage: t2-a (4 gens, context_pack only)
+--------------------------------------+
| t2-a · lineage               [live]  |
| g-01 first    pack:ab12.. 12 items  |
| g-02 cap      pack:cd90.. 14 items  |
| g-03 crash    pack:cd90.. 14 items  |
| g-04 operator pack:ef11.. 15 items  |
| reason enum = RESTART's own five    |
+--------------------------------------+
```

```
Pack diff: g-02 vs g-04 (same lineage)
+--------------------------------------+
| diff cd90.. vs ef11..        [replay]|
| + brief     (new op note)           |
| ~ snapshot  (2 workers → 0)         |
| = prefix    (same sha, stored once) |
| mem +node#14 v1 (recall grew by 1)  |
+--------------------------------------+
```

```
Why this decision? (D-042 under g-04)
+--------------------------------------+
| D-042 ← g-04 ← pack ef11..          |
| card: lane rule (blob 9f..)         |
| fact: node#12 v3 (supersedes v2)    |
| ev:  steer "use lane X" (pos 41)    |
| src: transcript file…off 214-233   |
+--------------------------------------+
```

```
Replay pack g-04 (exact start text)
+--------------------------------------+
| pack ef11.. · 6 parts · 7.9k var    |
| [prefix][snapshot][memory][brief]   |
| [card][events 38-44]                |
| pruned: none (blobs < 90d)          |
| [copy text] [open transcript]       |
+--------------------------------------+
```

Prune state renders inline: a replay naming a blob lost to the 90-day prune
(`docs/plans/2026-09-28-restart-spec.md:316-317`) shows `pruned: <part> —
lineage kept, bytes gone` instead of failing the whole view — the manifest still
names every part by sha, which proves lineage but can no longer rebuild the
bytes (`docs/plans/2026-09-28-restart-spec.md:279-282`).

## 7. Migration

Dependency order, stated plainly — neither producer is live in this branch, and
this spec's views are pure consumers of both:

1. **REDACTMERGE landed** (module live: `tools/autoos_redact.py:1-10`) — the
   precondition RESTART sets for pack-writing
   (`docs/plans/2026-09-28-restart-spec.md:284-288`).
2. **R7 pack-writing lands** (RESTART lane R7; NOT built in this branch — no
   store module, no `--replay`/`--diff` under `tools/`). From that merge on,
   every pack/spawn/card-version emits its manifest + blobs + event line
   (`docs/plans/2026-09-28-restart-spec.md:293-319`). Nothing before R7 can use
   this spec; everything after R7 gets §§3–6 for free at file scope.
3. **MEMSPEC facade lands** (sibling branch `L1-backlog/memspec`): the pack
   `memory` stub (`docs/plans/2026-09-28-restart-spec.md:201`, `:305`) becomes
   real recall ids+versions, and memory writes carry `gen` (§5). Until then, the
   memory rows of every view render `not wired` — lineage/diff/replay still work
   (they never needed memory text), only the fact rows of "why" stay empty.
4. **fleetd lands** (gap-spec 2; sibling branch `L2-general/fleetd-spec`): §4's
   file index becomes the ingestion feed for the sibling §4.5 tables; the four
   views keep working file-locally underneath it, so a fleetd outage degrades to
   CLI (`pack --replay`/`--diff`), never to no-provenance.
5. **Console views land** (this spec + sibling A4): file-local views first
   (no fleetd needed), fleetd-backed views second. No flag day: manifests and
   `events.jsonl` are append-only and kept forever
   (`docs/plans/2026-09-28-restart-spec.md:316-317`), so the index backfills from
   day one of R7.

## 8. Open questions

Format mirrors the sibling console spec's §12 (sibling branch
`L2-general/fleetspec`, `docs/plans/2026-09-28-fleet-console-spec.md` §12):
`Q: PROVENANCE | <topic> | <question> | options: … | default: … | blocks: … |
reversible: … |`, all open (no `answer:`), all `(inference)` unless a checkout
citation says otherwise.

- `Q: PROVENANCE | index-home | Does the §4 file index live in the console/repo (rebuilt by whoever renders) or as a sidecar the pack-writer maintains | options: (a) renderer-built from events.jsonl + saved cursor, writer knows nothing (b) writer-maintained sidecar index file next to events.jsonl | default: (a) because RESTART's store section names only blobs/manifests/events.jsonl (docs/plans/2026-09-28-restart-spec.md:293-299) and a second writer-owned file is a second home for derived facts | blocks: §4 implementation lane | reversible: yes (both derive from the same stream) |` (inference; store shape cited.)
- `Q: PROVENANCE | reverse-lookup | Is the text-scan reverse lookup (§5, "every decision under pack P") acceptable at fleet scale, or must the pack-writer also emit decision→pack backlinks | options: (a) scan suffices — diagnostic query only (b) backlink events on the stream | default: (a) because the fleetd side already indexes it (sibling output_links) and the file scan is the fallback path | blocks: §5 lane scope | reversible: yes |` (inference.)
- `Q: PROVENANCE | prune-UX | When replay hits a pruned blob, does the view render partial parts + lineage proof (§6 sketch) or refuse with the missing-blob error | options: (a) partial render with the prune banner (b) hard error naming the blob, matching pack --replay's exit 1 (docs/plans/2026-09-28-restart-spec.md:318-319) | default: (a) for the console, (b) stays for the CLI — different callers, different contracts | blocks: §6 replay lane | reversible: yes |` (inference; RESTART behavior cited.)
- `Q: PROVENANCE | transcript-root | The sibling transcript_refs normalizes away $HOME (usernames must not land in rows); does the file-store index apply the same normalization to its own cached offsets/refs, or hold raw paths because it never leaves the host | options: (a) normalize at index time — indexes get copied into reports (b) raw is fine, host-local only | default: (a) because an index copied once into a report is a leak (AGENTS.md rule 1) | blocks: §4 index lane | reversible: yes |` (precedent: sibling transcript_refs rule; AGENTS.md cited by name.)
- `Q: PROVENANCE | memspec-drift | If the sibling MEMSPEC lane changes its id/version scheme, who updates §§3–5 and the sibling §4.5 manifest_items version column | options: (a) this spec's owner tracks it — provenance is the consumer (b) MEMSPEC owns the migration of every consumer | default: (a) because §§3–5 consume only opaque ids+versions and the change surface is small | blocks: §7 step 3 sequencing | reversible: yes |` (inference.)

## 9. Closing table — lanes + estimated Claude tokens

Budget mode (D-102): writers/first-reviewers are free models, Claude only
reviews/finals — every row's Claude estimate stays small; no row exceeds ~1M
weighted tokens, so none is flagged.

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| PROV-index | §4 file index: stream scan + cursor, three maps, backfill test on a synthetic events.jsonl (no live install, no fleetd) | ~120k | 1–2 days | R7 pack-writing (needs the real event shape); buildable against fixtures before that |
| PROV-link | §5 linkage pin: `gen=` suffix tests on ready/Q/D lines, memory-write `gen` passthrough once MEMSPEC lands | ~80k | 1 day | R7 for ready/card paths; MEMSPEC lane for the write path |
| PROV-views | §§3+6 four file-local views (lineage, diff render over `pack --diff`, why-trace, replay render over `pack --replay`) in the console's §8 sketch style | ~200k | 2–3 days | PROV-index; sibling A4 as the style oracle |
| PROV-ingest | §4→sibling-§4.5 ingestion mapping (event line → generations/manifest_items/pack_blobs/output_links rows); owned jointly with the fleetd lane, listed here so the seam has one home | ~150k | 1–2 days | fleetd lane (gap-spec 2) + PROV-index |
| PROV-docs | Backfill answers into §8 as R7/MEMSPEC/fleetd land; close or re-ask drifted questions | ~40k | ongoing | each producer lane as it merges |
