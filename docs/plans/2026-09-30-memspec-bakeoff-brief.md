# MEMSPEC Bake-off Brief (P2)

**Author:** writer:nemotron-3.5-lightning-free  
**Created:** 2026-09-30  
**Lane:** /home/s/code/AutoOS-lanes/L1-backlog-memspec-p2 (branch L1-backlog/memspec-p2; base 851829e)  
**Status:** Skeleton up — build-only mode; engine phase gated on P1 host gate / pids dip / L1-main word  

## Scope

- Same memory facade as P1 (spec §4: 5 methods, hub rule, CLOSED_RELATIONS=(about,part_of,supersedes), MAX_BODY=600, secret-scan-first, version_id int, atomic JSON snapshot, provenance)
- Same data: R0 fixture seed (`tests/fixtures/memory/graph.json`), temp store per test
- 30 recall questions with expected-answer anchors (`tests/fixtures/memory/bakeoff-questions.json`)
- 3 candidate engines behind the same facade interface (signatures identical, guards explicit)
- 7 bake-off metrics (spec §9 table) — skeleton status `not_measured`; real measurement after engine phase

## 3 Candidate Engines (facade-first, spec §9)

| Engine | Adapter file | Current status |
|---|---|---|
| Omnigraph-as-one-graph | `tools/engine_omnigraph.py` | Skeleton: `_BUILD_ONLY_MODE=True`, all 5 methods return refusal stub |
| graph-in-console-DB | `tools/engine_console_db.py` | Skeleton: `_BUILD_ONLY_MODE=True`, all 5 methods return refusal stub |
| Graphiti-on-FalkorDB | `tools/engine_graphiti_falkordb.py` | Skeleton: `_BUILD_ONLY_MODE=True`, all 5 methods return refusal stub |

**Important:** All three adapters export the exact same 5 facade method signatures:
- `recall(query, project, k)`
- `context_pack(project, role)`
- `remember(kind, title, body, project, links, source, domain, visibility)`
- `link(a, rel, b, source)`
- `supersede(old, new, reason, source)`

Callers (MCP wrappers, test suite) never see which engine runs — they call the facade,
which delegates to the currently active adapter. In build-only mode every adapter
explicitly refuses real execution with a clear message.

## 7 Bake-off Metrics (spec §9 table)

| # | Metric Name | Definition | Unit | Why | How | Status |
|---|---|---|---|---|---|---|
| 1 | answer_correctness | Fraction of recall queries where the expected answer entity id appears in the top-k results | ratio (0–1) | validates that the facade read path returns correct facts for given queries | run all 30 bake-off questions via recall; count how many have the expected answer id in the returned lines or entity mode | not_measured |
| 2 | tokens_per_recall | Token count of the compact lines returned by a single recall call | tokens | enforces the spec:64–65 budget of ≤600 tokens per recall response | call recall for each of the 30 questions; record estimate_tokens of the lines array; verify ≤600 | not_measured |
| 3 | write_then_visible_latency | Wall-clock time from a remember() call returning successfully to the new entity being reachable via recall under the same project | seconds | measure §5 write latency; how quickly a written fact becomes readable | time remember→recall round-trip for each of 30 questions; p95 < 5s target | not_measured |
| 4 | duplicate_rate_after_week | Fraction of canonical ids that would be duplicates after a week of continuous writes (same title, varying body) | ratio (0–1) | tests the alias ladder and merge behaviour under sustained write load | not measurable in build-only stub mode; requires real engine running over time | not_measured |
| 5 | p95_context_pack_latency | p95 token-counted latency of context_pack() calls across all 30 questions' projects | tokens (estimated via estimate_tokens) | ensures session-start bundle stays within the 1.5k token budget (spec:90) | call context_pack for each project used in the 30 Qs; measure tokens; compute p95; verify ≤1500 | not_measured |
| 6 | ram_fit_machine | Whether the full graph (nodes + edges + alias) fits within the target machine's RAM limit | bool (fits / does_not_fit) | production deployments must not OOM; graph must fit on the target machine | count total bytes of node bodies + edge data + alias mappings; compare to machine RAM limit | not_measured |
| 7 | ops_incidents | Count of restart/stuck-index/page-out incidents during a bake-off run | integer count | operational stability: restarts, failed indexes, or 3am-page-out events degrade confidence | not measurable in build-only stub mode; requires real engine deployment with monitoring | not_measured |

## Method

The bake-off harness (`tools/memory_bakeoff.py`):

1. Loads the 30 questions from `tests/fixtures/memory/bakeoff-questions.json`
2. For each of the 5 facade methods, exercises the loop through all 30 questions
3. Each adapter call in build-only mode returns a refusal dict — no real engine execution
4. Metrics collection is scaffolded; real values computed after engine phase
5. Hard guard: `AUTOOS_BUILD_ONLY=1` (default) prevents real engines from running;
   `AUTOOS_BUILD_ONLY=0` requires P1 host gate / pids dip / L1-main word approval

**What will be run later (engine phase, after P1 host gate):**

- Real engine adapters (Omnigraph, console-DB, Graphiti-on-FalkorDB) with actual graph backends
- All 7 metrics measured and recorded with real values
- The 30 questions answered against real graph data
- Duplicate rate after a week of simulated writes
- Ops incidents from real deployment monitoring
- A bake-off summary report comparing the 3 engines

## Pending / Caveats

- **Build-only mode is active:** No real engine executes; all adapter calls return refusal stubs
- **P1 host gate:** The engine phase cannot start until the P1 host gate is lifted / pids dip / L1-main word confirms
- **Metrics 4, 7:** Require real engine deployment over time; cannot be measured in stub mode
- **30 questions structural test:** exactly 30 unique ids, only the 5 facade methods, non-empty expected_answer anchors — verified at import time
- **DRY / single source of truth:** metric names and definitions live in `tools/memory_metrics.py`; the brief references them verbatim
- **No facade changes:** P1 implementation is read-only; only bug fixes with failing tests first
- **Commit style:** small frequent commits with descriptive messages; conventional commit format

## Verdict

**SKELETON UP** — all deliverables are in place:

- ✅ 3 engine adapter skeletons (omnigraph, console_db, graphiti_falkordb)
- ✅ 30 recall questions with expected-answer anchors (`tests/fixtures/memory/bakeoff-questions.json`)
- ✅ 7 metrics scaffold (`tools/memory_metrics.py`)
- ✅ Runner skeleton (`tools/memory_bakeoff.py`) with build-only guard
- ✅ Bake-off brief doc (`docs/plans/2026-09-30-memspec-bakeoff-brief.md`)
- ✅ Existing test suites green: 33 + 13 + 5 tests passing
- ⏳ Engine phase pending: real execution after P1 host gate / pids dip / L1-main word