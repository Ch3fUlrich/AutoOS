# ORCH-D1 spec: unattended-orchestration SKILL.md → short index + generated role cards

Status: SPEC (L2-general, 2026-09-28). Ownership: this spec only — the real
SKILL.md split is L1-routing's job, not this spec's. Nothing under
`.agents/skills/` is touched by this spec.
Scope: three deliverables. **D1** = the split itself (index + 4 generated role
cards + SessionStart injection, §§1–4, 7). **D2** = enforced/advisory tagging of
every current rule + CI wiring (§5). **D3** = six new rules with homes and tags
(§6). Open questions §8, delivery lanes §9.
Inputs read: `.agents/skills/coding-principles/SKILL.md`,
`.agents/skills/unattended-orchestration/SKILL.md` in full (166 lines by
`wc -l`, verified 2026-09-28), `AGENTS.md`, `lib/agent_harness.py` lines 1–13.
Public repo: placeholders only, no secrets/hostnames/IPs/usernames.

Why: today every level (L0 router, L1 coord, L2 orch, L3 worker) reads the whole
166-line SKILL.md including all 33 rules, most of them for other levels. Split it
into a short index (the SKILL.md entry point, ≤150 lines) plus one generated
role card per level (≤60 lines each), so a session loads only the index + its
own card. Cards are injected at SessionStart. The HOOKS spec is not in this
checkout (sibling branch L2-general/hooks-spec, unmerged) — mechanism details
live there, not here (§4).

## 1. Index shape (≤150 lines)

What stays in SKILL.md: everything that is level-independent — the entry
narrative, the Files map, the Levels table, a compact rule roster (ids +
pointers, not full rule text), card pointers, the CAO command block (which must
stay verbatim — `.agents/skills/unattended-orchestration/tests/cao/test_cli.py`
scans this file for every `python -m cao ...` string and checks each against
the real parser (corrected 2026-09-28, Sonnet final: the writer's path dropped
the `.agents/skills/unattended-orchestration/` prefix — verified the scanner
itself at that file's `_COMMAND` regex + `SKILL.md`-glob, lines ~756–763);
`.agents/skills/unattended-orchestration/SKILL.md` lines 143–145), and a
provenance note (generated files, single source).

Before (verified 2026-09-28, `wc -l` = 166; section starts by `grep -n`):
front matter 1–4, title/intro 6–21, `## Files` 23, `## Levels (L0-L3)` 40,
`## Rules` 63 (format contract + code-migration notes 65–88, binding 90–92),
`### router (L0)` 94, `### coord (L1)` 100, `### orch (L2)` 111,
`### worker (L3)` 126, `## CAO quickstart` 139–166.

After — proposed index section list with line budgets (sum 138 ≤ 150):

| Section | Budget | Notes |
|---|---|---|
| Front matter + title + when-to-load intro | 12 | Today's lines 1–21 trimmed; keep L1-standing-orders pointer (today's lines 18–21) |
| `## Files` map | 16 | Keep full table (today's lines 25–38); it is the on-demand loader |
| `## Levels (L0-L3)` table | 22 | Keep table + depth-vs-tier note (today's lines 46–61); embedded rule mentions stay as id citations only, no new normative verbs (§5) |
| `## Rules` roster | 30 | Format contract (today's lines 65–70) + rule id roster with one-line pointers to cards; full rule text moves to `rules.yaml` + cards |
| Code-migration + binding notes | 12 | Keep condensed (today's lines 72–92); `references/rule-map.md` stays the code-pointer list |
| Card pointers (L0/L1/L2/L3 → file + when) | 8 | New; 2 lines per level |
| `## CAO quickstart` commands + provenance | 24 | Command block stays verbatim (scanner, lines 143–145); prose trimmed, rest already in `references/cao-runbook.md` |
| Generation note (source file, render command) | 14 | New; points at `rules.yaml` + `skill-rules.py render` (§3) |
| **Total** | **138** | Headroom 12 for future shared lines |

## 2. Role cards (≤60 lines each, L0/L1/L2/L3)

Each card holds: the level's Levels-table row (today's lines 46–51) in full,
that level's full rule texts (rendered from `rules.yaml`, §3), the inbox/relay
contract line it needs, and a pointer back to the index. Nothing else. Line
budgets: header + row 10, rules ~40 (3–12 rules × 3 lines), relay line + pointer
8 → ≤58.

Level → rules mapping (from `### router (L0)` line 94, `### coord (L1)` line
100, `### orch (L2)` line 111, `### worker (L3)` line 126, plus the binding
paragraph lines 90–92):

| Card | Owns (full text) | Also carries (cited, not owned) |
|---|---|---|
| L0 router | R-router-01/02/03 (lines 96–98) | Levels L0 row (line 48); inbox path `RUN/inbox/L0.md` (line 49) |
| L1 coord | R-coord-01/02/03/04/06/07/08/09 (lines 102–109) | Levels L1 row (line 49) |
| L2 orch | R-orch-01/02/04/06/08/10/11/12/13/14/15/16 (lines 113–124) | R-coord-07/08 heartbeat subset (lines 107–108) — `coord` rules bind whoever runs lanes, "so the heartbeat rules R-coord-07/R-coord-08 bind L2 as well" (lines 90–92) |
| L3 worker | R-worker-01–10 (lines 128–137) | Return contract pointer `docs/agent-protocol.md` (line 51); R-worker-06 no-spawn (line 133) |

Dedup rule, named explicitly — **index-homes-shared-rules**: a rule (or note)
that applies to all four levels lives exactly once in the index and in zero
cards; a card carries a rule iff that rule's topic is its level (plus the
heartbeat exception above, which is cited — not restated — in the L2 card).
Rationale: `coding-principles` Principle 1, one authoritative home
(`.agents/skills/coding-principles/SKILL.md` lines 17–26). The generator (§3)
enforces it mechanically: a rule tagged to all four levels renders only in the
index; CI fails a card containing index-owned text.

## 3. Generation, not hand-maintenance

Pattern: `lib/agent_harness.py` — "One generator, used by both installers. Pure
functions plus a small CLI" (line 4; module docstring lines 1–13) rendering
several consumers (check/pin/opencode/vendor/openhands subcommands, lines 6–12)
from one harness file. Same shape here: one source, many renderers.

Home: a `render` (+ extended `check`) subcommand on the existing
`tools/skill-rules.py`, not a new module. Justification: that script is already
the skill's CI-invoked checker — "python3 tools/skill-rules.py check (CI)
enforces format, uniqueness and near-duplicates"
(`.agents/skills/unattended-orchestration/SKILL.md` line 68) — so invocation
needs no new CI wiring, and one tool owning one skill's shape follows
Principle 1 (a second module would be a second home for the same facts).
Verified present: `tools/skill-rules.py`, `tests/test_skill_rules.py`.

Input file (new, name open in §8): one record per rule. Shape:

```yaml
# rules.yaml (sibling of SKILL.md; the single source §2 renders from)
- id: R-router-01
  levels: [L0]
  text: "Only L0 asks, researched, as the batched `Q:` line; ..."
  why: "a dialog blocks a background session"
  source: "common.md, inbox 05:47Z"
  enforcement: advisory
  enforced_by: null
- id: R-coord-09
  levels: [L1]
  text: "L3 spawns, routing, status: autoos-agent only, never hand-roll; ..."
  why: "hand-rolls drift from gates"
  source: "operator 04:50Z, REVGATE.record.md"
  enforcement: enforced
  enforced_by: ["tests/test_agent_harness.py::test_spawn_only_via_agent"]
```

`levels` drives card membership (multi-level allowed, e.g. R-coord-07/08 carry
`[L1, L2]` per lines 90–92); `enforcement`/`enforced_by` drive §5. `render`
writes the 4 cards + the index's `## Rules` roster; `check` validates tags,
membership, and the §5 mapping.

## 4. SessionStart injection

The session's own card (and only that card) is injected at session start; the
index is the SKILL.md the session already loads. SessionStart hook injects
additionalContext (per Claude Code's hook docs); mechanism owned by the HOOKS
spec, not re-specified here. That spec is not in this checkout (sibling branch
L2-general/hooks-spec, unmerged). Sessions without the injection keep reading
SKILL.md whole — see §7 for why both readings agree.

## 5. D2: enforced vs advisory tagging + CI (most important)

Definitions: `enforced` = mechanically checkable by a named test/lint that CI
runs; `advisory` = needs judgment, no such check exists or is possible. Every
rule below gets exactly one tag; an enforced rule names its check, an advisory
rule says why no check can cover it.

Tag home: a sibling YAML/JSON the generator reads (`rules.yaml`, `enforcement`
+ `enforced_by` fields as in §3) — not SKILL.md front matter. Front matter
would eat the §1 ≤150-line budget and mix machine data with the human entry
point (`coding-principles` Principle 3, one reason to change;
`.agents/skills/coding-principles/SKILL.md` lines 49–58).

CI mechanism — rule↔test mapping CI diffs: the mapping lives in the
`enforced_by` field itself (single home, Principle 1 — no second map file to
drift). Extended `python3 tools/skill-rules.py check` (the existing CI gate,
SKILL.md line 68) fails when (a) any rule lacks a tag, (b) an `enforced` rule
names a test that does not exist on disk (file + test function must resolve),
(c) a named test is not collected by its suite (no orphan refs), or (d) a test
listed in `enforced_by` is deleted/renamed without updating `rules.yaml` (same
check, run the other direction: collect suite inventory, diff). Level-specific
normative sentences embedded in the Levels table (lines 46–59: the 25-min quiet
relaunch, never-resume, never-execute) are not separate rules — they inherit
the tag of the canonical rule they cite (R-coord-08, R-orch-06, R-router-03),
and `check` gains a no-new-normative-verbs lint outside `## Rules`/`rules.yaml`
so stragglers cannot hide untagged.

Worked tagging of all 33 current rules (lines verified by `grep -n`):

| Rule | Line | Tag | Enforcing test/lint, or why advisory |
|---|---|---|---|
| R-router-01 | 96 | advisory | whether research was "due" vs forward-verbatim is judgment; only the `Q:` shape is lintable, not the decision |
| R-router-02 | 97 | advisory | "diagnose before declaring failure" is judgment about sufficiency of diagnosis |
| R-router-03 | 98 | advisory | what counts as "project work" needs judgment; promotable later via L0 transcript audit, no such audit exists |
| R-coord-01 | 102 | enforced (new, lane 3) | git-topology lint: lane→orch→main no-ff parents, single-merger mutex marker, ready-after-green-CI order |
| R-coord-02 | 103 | advisory | "judge it" (tests + diff vs brief + files-read) is reviewer judgment |
| R-coord-03 | 104 | advisory | role discipline (never implements); the `route` tool path is code but the rule as stated is about session behavior |
| R-coord-04 | 105 | enforced (new, lane 3) | heartbeat threshold test: ≤3 lanes + 3 readers, MemAvailable ≥3 GB, suite placement (heartbeat is code, lines 83–84) |
| R-coord-06 | 106 | advisory | cap detection is code, but "successor resumes from state alone" judges rewrite quality |
| R-coord-07 | 107 | enforced (new, lane 3) | beat-liveness check: 10-min CronCreate beat present from launch to stop, recreated after relaunch/clear |
| R-coord-08 | 108 | enforced (new, lane 3) | staleness watchdog test: status stamp freshness, WIP-commit/pong behavior, quiet-child->25-min relaunch |
| R-coord-09 | 109 | enforced (existing) | spawn-path gate: `tools/autoos-agent.py` / MCP `spawn` is the only L3 path; hand-rolls fail closed (cf. REVGATE record; code list lines 80–88) |
| R-orch-01 | 113 | advisory | terseness/one-line-per-fact is style judgment (field presence could schema-lint later — promotion candidate) |
| R-orch-02 | 114 | advisory | "one file + exact spec" sufficiency is brief-author judgment |
| R-orch-04 | 115 | advisory | inline-vs-path feeding choice depends on isolation context, judgment |
| R-orch-06 | 116 | enforced (existing shape) | runner never resumes a no-change child (relaunch path); the worktree/WIP-scope verification fragment stays advisory |
| R-orch-08 | 117 | advisory | "record refusals verbatim" verbatim-ness + lane-identity choice need judgment |
| R-orch-10 | 118 | advisory | classifying a change as privileged/installer/state-mutating needs judgment; resolver promotion possible later |
| R-orch-11 | 119 | enforced (new, lane 3) | consumer-coverage check: a route-id/return-code change must touch every consumer in the lanes.md list + catalog postInstall (CI cases cited in the rule) |
| R-orch-12 | 120 | enforced (existing) | `trust_worktree.py` approval gate blocks first session (the "three lanes blocked in 3s" incident is the gate firing) |
| R-orch-13 | 121 | advisory | "bucket-table-big" + same-family-blind-spot judgments; review happened, but sufficiency is judgment |
| R-orch-14 | 122 | advisory | "never skip a slow free reviewer" is process discipline, unobservable after the fact |
| R-orch-15 | 123 | advisory | lesson-line quality + "only a tested lesson becomes a rule" is owner judgment |
| R-orch-16 | 124 | enforced (new, lane 3) | name-equality lint on gating code: exact match required (the substring-sign-off bug is the regression test) |
| R-worker-01 | 128 | advisory | "minimal edits" + render-cell derivation need judgment (order-pin fragment lintable later) |
| R-worker-02 | 129 | advisory | failing-first authorship + platform guards are author judgment |
| R-worker-03 | 130 | advisory | "cheap" run sizing is judgment against live host state |
| R-worker-04 | 131 | advisory | fake-vs-real contract fidelity is reviewer judgment (citation presence is lintable, fidelity is not) |
| R-worker-05 | 132 | enforced (new, lane 3) | recipe lint: require `set -o pipefail`, forbid the `tail -1 && push` idiom (the "no tests ran exited 0" incident is the regression test) |
| R-worker-06 | 133 | enforced (existing) | `tests/test_agent_harness.py` (cited as the rule's source): leaf roles never spawn / lack the MCP listing |
| R-worker-07 | 134 | enforced (new, lane 3) | recipe grep lint: forbid `shellcheck tests/run-tests.sh`, require MemoryMax=2G wrapper for >2 GB jobs |
| R-worker-08 | 135 | enforced (new, lane 3) | recipe lint: detached-copy idiom required (`--no-hardlinks`/`--detach`), bare worktree `cp` forbidden |
| R-worker-09 | 136 | enforced (new, lane 3) | brief/recipe grep + MCP fence: no `activate_project` from a worktree path |
| R-worker-10 | 137 | enforced (existing shape) | redaction corpus test per new raw-data consumer (SPAWNFIX3d/REDACTFIX3 lineage); lane 3 wires any missing consumers |

Result: 14 enforced (4 existing-shape, 10 new), 19 advisory. Every new check is
a lane-3 deliverable (§9); until it lands, its rule ships tagged `enforced`
with `enforced_by` pointing at the specified new test, and `check` fails while
the test is absent — the tag is the promise, the failing check is the debt
ledger. (Applies `coding-principles` Principle 2 fail-first to process itself.)

## 6. D3: new rules (each with tag + home)

| # | Rule (imperative, one line) | Tag | Home | Why this tag/home |
|---|---|---|---|---|
| D-118 | L0 spot-checks a sample of relayed claims and records the sample | advisory | L0 card | which sample is "enough" is judgment; a lint can confirm *some* spot-check evidence exists but not sufficiency |
| D-115 | A NOT-READY override is written down (what, who, why, where) and auditable | enforced (record shape) | index (index-homes-shared-rules: every level can face an override) | state-file record schema lint checks the four fields (new, lane 4); known limit, stated: no check can observe an override that left no trace — the rule exists to make that impossible |
| D-099 | Compaction claims cite pre-compaction lines | advisory | index (all levels compact) | citation sufficiency is judgment, as with R-worker-04 |
| N-relay | Relayed policy is never typed fresh, only forwarded verbatim from A1/A2-sourced text | advisory | index (binds every relay: L0→L1→L2) | the source often lives outside the repo (chat/brief text), so no lint can diff it; verbatim-ness is reviewer judgment |
| N-compact | A worker task (brief + expected output) fits under the auto-compaction threshold | enforced (brief-time budget) | L3 card + L2 brief discipline (L2 sizes the task, L3 lives inside it) | brief token count + depth budget is measurable at brief time (cf. the 3-tier depth budget in `unattended-orchestration.md`, SKILL.md line 37); the runtime tail is unobservable upfront — stated limit |
| N-lane | One control-plane lane at a time, fleet-wide | advisory | index | likely advisory permanently: "fleet-wide" spans machines/repos with no single observable mutex, so no one test sees every host; enforcement would need a shared lock service that does not exist — until it does, this is L0/operator judgment. Promotion condition: a fleet lock exists → re-tag enforced with the lock-contention test |

## 7. Migration (additive, no disagreement)

SKILL.md keeps working as a single read for sessions without the SessionStart
injection: the index's Files map, Levels table, rule roster, and CAO block are
self-sufficient to find any rule within two reads (index → card/reference), so
nothing the old whole-file read could reach becomes unreachable. Old
whole-file reads and new indexed reads cannot disagree because they render from
the same source: `rules.yaml` generates both the index roster and the cards, so
same content, different packaging, by construction. Checked three ways in CI:
(1) render-diff — `skill-rules.py render --check` fails if tracked index/cards
differ from generated output (same mechanism as agent_harness's `check`
subcommand, `lib/agent_harness.py` lines 6–7); (2) provenance lint — every
normative sentence in a card traces to exactly one source record (cards add no
new normative content); (3) roster completeness — every rule id in `rules.yaml`
appears in the index roster and in exactly its levels' cards. Card/skill edits
land as `rules.yaml` edits with regenerated output in the same change
(`coding-principles` Principle 4, same-change docs;
`.agents/skills/coding-principles/SKILL.md` lines 65–74).

## 8. Open questions

`docs/plans/2026-09-28-fleet-console-spec.md` does not exist in this checkout
(verified: no fleet-console file under `docs/plans/`), so §12's format cannot
be cited — using the fallback format
`Q: ORCHD1 | topic | question | options: ... | default: ... | blocks: ... | reversible: ...`:

- Q: ORCHD1 | generator home | extend `tools/skill-rules.py` with `render`/`check` vs new `tools/skill-cards.py` | options: extend (one home, no new CI wiring) / new module (cleaner diff, second owner) | default: extend | blocks: lane 1 | reversible: yes (thin wrapper either way)
- Q: ORCHD1 | tag home | sibling `rules.yaml` vs SKILL.md front matter vs sibling JSON | options: YAML sibling (readable, commented) / front matter (colocated, eats index budget) / JSON (stricter, no comments) | default: YAML sibling | blocks: lanes 1, 3 | reversible: yes (mechanical conversion)
- Q: ORCHD1 | rule-map | extend `references/rule-map.md` (old→new ids, tested by `tests/test_skill_rules.py`, SKILL.md line 36) with enforced/advisory columns vs keep the mapping only in `rules.yaml` | options: extend rule-map (human-readable) / rules.yaml only (single home) | default: rules.yaml only, rule-map keeps id→code pointers | blocks: lane 3 | reversible: yes
- Q: ORCHD1 | non-Claude agents | SessionStart injection is a Claude Code hook; AGENTS.md's client table (lines 269–276) shows opencode reads project `.agents/skills` natively with no injection — is index+manual-card-load sufficient there, or does each agent need its own wiring lane | options: index suffices (cards are plain files) / per-agent wiring | default: index suffices | blocks: lane 2 acceptance | reversible: yes
- Q: ORCHD1 | card budget | hard-fail CI above 60 lines per card vs warn-only | options: fail (budgets mean something) / warn (flex for L2, the largest card) | default: fail, L2 gets the §2 exception process via index-homes-shared-rules review | blocks: lane 2 | reversible: yes

## 9. Delivery lanes

Budget mode: free writers do the drafting, Claude does finals only — estimates
below are Claude finals review/merge time, kept small. No lane exceeds ~1M
weighted tokens; nothing to flag.

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| G1 | Generator + input shape: `rules.yaml` schema, `render`/`check` in `tools/skill-rules.py`, 2-rule pilot render | ~150k weighted tokens | ~2 h | Q1–Q2 answers (§8) |
| G2 | Index rewrite (≤150-line budget, §1) + first 4 cards (≤60 lines each, §2), render-diff green | ~200k weighted tokens | ~4 h | G1, Q4–Q5 answers |
| G3 | Tagging pass (all 33 rules, §5 table) + CI wiring: `enforced_by` resolution both directions, no-new-normative-verbs lint, 10 new lints | ~250k weighted tokens | ~6 h | G1, Q3 answer |
| G4 | D3 rule wiring (§6): state-file override schema + brief-budget check, 4 advisory wordings into index/cards | ~120k weighted tokens | ~3 h | G2, G3 |
