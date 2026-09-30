# Routing Design Proposals — Operator Item 5 (a)–(j)

**Lane:** ws-designmemo-20260930 (L2 t2-worker, L1-beta)  
**Base:** `d08f7f2` (main == origin/main)  
**Worktree:** `AutoOS-worktrees/AutoOS-ws-designmemo` · branch `L1-backlog/ws-designmemo-20260930`  
**Date:** 2026-09-30  
**Scope:** proposals + safe small implementations. `tools/autoos-*.py`, `tools/registry.py`,
`catalog/ai-registry.json` policy/leg_rules, `tools/sync-*` are L1-routing backlog territory —
proposed precisely, not implemented here. `configuration/omniroute/combos.json` is L1-alpha's
lane; referenced read-only, never edited.

Every file:line anchor below was verified by grep against the worktree at `d08f7f2`.

---

## R-coord-12 / R-coord-13 verification (operator directive)

### Check 1 — both rules present in the repo SKILL.md

Confirmed. `C:\Users\mauls\Documents\Code\AutoOS\.agents\skills\unattended-orchestration\SKILL.md`:

- **Line 110:** `- R-coord-12: L0 and L1 sessions are interactive top-level sessions, never subagent children. (why: unattended parents must be watchable and addressable; source: operator 2026-09-30, ws-omniroute run)`
- **Line 111:** `- R-coord-13: L1 and L2 follow this skill, never fix or research; they spawn L2/L3 sized to complexity. (why: orchestrators that work stop orchestrating; source: operator 2026-09-30)`

### Check 2 — skill-rules checker

```
$ python tools/skill-rules.py check
ok: 39 rules
```

`tests/test_skill_rules.py` — 26 passed, 0 failed (includes `RuleResolutionTests`).

### Check 3 — do rule-map.md and CHANGELOG.md need entries?

**rule-map.md:** Not required by the checker. `RuleResolutionTests` asserts that every *old*
R-id cited in code/tests/docs has a row in `references/rule-map.md`, and every *new* R-id
*target* in the map exists in `SKILL.md`. R-coord-12/13 are brand-new ids with no old id
mapping to them, so no row is required. The map's "Third-generation ids" (fold 2026-09-28)
and "Fourth-generation ids" (fold 2026-09-29) sections document new rules for traceability;
a "Fifth-generation ids" section noting R-coord-12/13 would be consistent but is **optional** —
the checker passes without it (measured: `ok: 39 rules`, 26 tests passed).

**CHANGELOG.md:** Not required by the checker. The repo tracks skill changes in `CHANGELOG.md`
(see entries at lines 2454, 3261, 3360 for prior rule additions). A CHANGELOG entry should be
added for R-coord-12/13 when the incident lane's R-coord-14 edit lands, so one entry covers
all three. This is a documentation task, not a checker requirement.

### Hard-constraint note

The incident lane `ws-incident` owns `SKILL.md` for R-coord-14 (L0 directive). This lane did
**not** edit `SKILL.md` — R-coord-12/13 were already present (added by the operator or a prior
lane). This doc references the file; that is all.

> **Path note:** Check 1 above cites the main-repo path for `SKILL.md`. All SKILL.md line
> numbers in items (c) and (e) are against the **worktree** copy at `d08f7f2` (consistent with
> the doc header). R-coord-12/13 sit at lines 110/111 in both the worktree and main-repo
> copies; R-worker-11 has since shifted by 2 lines in the main repo (143 vs worktree 141)
> but that does not affect any anchor cited here.

---

## Item (a) — routing v1 matrix as generated output from `select_combo --explain-all`

**Verdict:** propose `--explain-all` flag on `route` subcommand; generate the ADR 0006 matrix
from code, not prose.

### Current state

- `select_combo` (`tools/autoos_routing.py:317`) returns `(combo, reason)` for **one** card.
  It is the single decision point (ADR 0006 decision 1; `tools/autoos_routing.py:1-6`).
- `route --explain` (`tools/autoos-agent.py:7757` arg; `tools/autoos-agent.py:3820` handler)
  prints per-route explain lines for one card to stderr.
- **No `--explain-all` exists** (grep: 0 matches in `tools/`).
- The v1 matrix lives as a **static markdown table** in ADR 0006
  (`docs/decisions/0006-launch-time-routing-resolver.md:66-74`). It has already drifted:
  the ADR says `public | 128k | implement or review | credit | t2-worker / t3-driver`
  but the code at `autoos_routing.py:353-355` returns specific combos with reason codes
  (`public-credit`, `public-light-credit`) that the ADR table does not name. The ADR also
  omits the T1FREE free-fallback-leg change (`autoos_routing.py:17-18,346-350`).

### Proposal

Add `--explain-all` to the `route` subcommand parser (`tools/autoos-agent.py:7757`, alongside
the existing `--explain` flag). When set, `cmd_route` (`tools/autoos-agent.py:3820`) iterates
every valid v1 card combination — the Cartesian product of `CARD_VALUES`
(`autoos_routing.py:36-42`: 3 roles × 3 complexities × 2 ctx × 2 privacy × 2 spend = 72
combinations, many redundant after defaults) — calls `select_combo` for each, and prints a
markdown table to stdout matching the ADR 0006 §3 matrix shape:

```
| privacy | ctx   | role / complexity   | spend   | → combo             | reason              |
|---------|-------|---------------------|---------|---------------------|---------------------|
| public  | 1m    | orchestrate, or hard| any     | t1-orchestrator     | public-1m           |
| public  | 128k  | implement / standard| free-ok | t2-worker           | public-default      |
| ...     |       |                     |         |                     |                     |
```

The table is generated by enumerating `CARD_VALUES` and calling the real `select_combo`,
so it can never drift from the code. The ADR 0006 §3 table is replaced with a one-line
reference: "Generated by `python3 tools/autoos-agent.py route --explain-all`; do not edit
this table by hand."

**Diff size:** ~40 lines in `autoos-agent.py` (new `--explain-all` handler that imports
`CARD_VALUES` and `select_combo` from `autoos_routing`, iterates, formats). No change to
`select_combo` itself — no routing-contract behaviour change.

**Status:** **proposed-only** (touches `tools/autoos-agent.py`, L1-routing backlog territory).

**Why:** A prose matrix in an ADR drifted from the code within days. A generated output
makes drift impossible — the matrix IS the code's output. The operator named "drift
already proven with chain heads" as the motivation.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_routing.py` | 317–356 | `select_combo` — the function to enumerate |
| `tools/autoos_routing.py` | 36–42 | `CARD_VALUES` — the value space to iterate |
| `tools/autoos-agent.py` | 3820 | `cmd_route` — where `--explain-all` would be handled |
| `tools/autoos-agent.py` | 7757 | `--explain` arg — where `--explain-all` would be added |
| `docs/decisions/0006-launch-time-routing-resolver.md` | 66–74 | The prose matrix to replace |

---

## Item (b) — tier contract reframe: t1 = highest reasoning capability + externalized memory, NOT biggest window

**Verdict:** reframe the tier contract in docs and ADR; no routing-code change needed (the
code already routes by reasoning, not window, for most cases).

### Current state

- ADR 0006 §3 matrix (`docs/decisions/0006-launch-time-routing-resolver.md:66-74`) routes
  `ctx=1m → t1-orchestrator`, framing t1 as "the 1M window tier".
- `configuration/omniroute/combos.json:96-106` declares `t1-orchestrator` with
  `"context": "128k"` — the combo itself is NOT a 1M combo. The 1M routing
  (`autoos_routing.py:349-350`) is a card-input filter, not a combo property.
- `docs/handoff.md:9` says "Your role: t1 orchestrator" but does not define what makes t1
  distinct from t2 beyond the routing matrix.
- The `t1-orchestrator` legs (`combos.json:99-105`: gemini-3.8-flash, qwen3-235b,
  GLM-5.3-Flash, mistral-small-3.2, muse-spark-contributor) are a mix of wide-context and
  reasoning models. The tier is implicitly "wide context + strong" but the contract is
  never stated as "reasoning capability + externalized memory".

### Proposal

1. **Reframe in ADR 0006:** Add a "Tier contract" paragraph to ADR 0006 (after §3 or in §4)
   stating: "t1 = highest reasoning capability + externalized memory (the handoff state
   file, `docs/handoff.md`, and the Omnigraph memory graph), NOT the biggest window. A
   ≥128k–1M task is splittable by default; `t1-fat-context` is an explicit opt-in for
   genuinely unsplittable 1M work, not the default t1 routing." Reference
   `docs/handoff.md` as the externalized-memory contract.

2. **Reframe in `docs/handoff.md`:** Add a one-line tier definition near line 9: "t1 is the
   reasoning tier (highest capability + externalized memory via this file + Omnigraph),
   not the widest window — large tasks are split to fit 128k by default."

3. **No code change:** The routing code already handles 1M as a card input
   (`autoos_routing.py:349-350`), not as a tier property. The `select_combo` function does
   not read combo `context` — it reads card `ctx`. So the reframe is a docs/contract change,
   not a routing change. A future `t1-fat-context` combo would be a new combo id (L1-alpha's
   `combos.json` territory), proposed separately.

**Diff size:** ~15 lines across two doc files. Zero code change.

**Status:** **proposed-only** (docs change; ADR 0006 and docs/handoff.md are shared docs —
coordinate with L0 before editing, since the incident lane may touch docs too).

**Why:** The operator's framing ("NOT biggest window") corrects a misread that already
caused a routing design error: t1 was treated as "the 1M tier" when its legs are 128k. The
externalized-memory angle (state file + Omnigraph) is what actually distinguishes an
orchestrator from a worker — a worker has no durable state beyond its worktree.

### Anchors

| File | Line | What |
|---|---|---|
| `docs/decisions/0006-launch-time-routing-resolver.md` | 66–74 | The matrix that frames t1 as "1M" |
| `configuration/omniroute/combos.json` | 96–106 | `t1-orchestrator` combo (context: "128k") |
| `tools/autoos_routing.py` | 349–350 | `ctx=1m → t1-orchestrator` routing |
| `docs/handoff.md` | 9 | "Your role: t1 orchestrator" — where the contract would be stated |

---

## Item (c) — in-session compaction checkpoints at ~60–70% fill

**Verdict:** add a 60–70% checkpoint to the `state-file.md` procedure; pin DONE criteria,
track assignments, and deny-lists to files before compaction.

### Current state

- `references/state-file.md:38-44` has two compaction triggers:
  - "At half the cap, move bulky tool output into files and keep pointers." (line 40)
  - "At the cap from `policy.handoff_caps`: rewrite this file, run `l1_handoff.py`… stop." (line 41-43)
- `tools/autoos_context.py:35-42` (`DEFAULT_CAPS`) defines the handoff caps: Opus/Fable/Sonnet
  hand off at 500k (50% of 1M); Spark/Gemini at 400k (40% of 1M); 200k-class at 80k (40%).
- There is **no 60–70% checkpoint**. The jump from "half cap" (move output) to "cap" (handoff)
  leaves a gap where compaction (harness-triggered context clearing) can lose unpinned state.
- The SKILL.md compaction rule is `R-worker-11` (`SKILL.md:141`): a 'summarise, no tools' ask
  is harness compaction. The rule identifies compaction but does not say what to pin first.

### Proposal

Add a step 1.5 to the `state-file.md` procedure (between current steps 1 and 2, at line 40-41):

```markdown
2. At ~60–70% fill (autoos-agent.py context), pin the session's durable decisions to files
   agents re-read after compaction: write DONE criteria to the brief or a DONE-draft file,
   write track assignments and deny-lists to the state file (§"Decisions + why" and
   §"Open questions"), and write any unpinned deny-list (files/clients this run must not
   touch) to the state file's "Lanes" table. A compaction that clears context loses
   everything not in a file; pinning at 60–70% is early enough that the session still has
   room to write, and late enough that it captures real progress, not just the brief.
```

Renumber the existing steps 1→1 (half cap), 2→3 (cap/handoff), 3→4 (successor reads handoff).

**Diff size:** ~12 lines in `references/state-file.md`. Zero code change — the procedure is
a template lanes follow, not code that runs.

**Status:** **proposed-only** (`state-file.md` is a reference under the skill; the hard
constraint forbids editing `SKILL.md` but not `references/state-file.md`. However, the
incident lane may touch skill references for R-coord-14 — coordinate before editing.)

**Why:** The operator's framing: "eager compaction is wasteful, late is rot." The current
"at half the cap" is too early (moving output at 50% wastes the 50–60% working room); "at
the cap" is too late (a harness compaction at 65% fill loses unpinned DONE criteria). The
60–70% checkpoint is the Goldilocks zone: pin what matters while there is still room to
write, after real progress exists to pin.

### Anchors

| File | Line | What |
|---|---|---|
| `.agents/skills/unattended-orchestration/references/state-file.md` | 38–44 | The procedure to extend |
| `tools/autoos_context.py` | 35–42 | `DEFAULT_CAPS` — the cap table |
| `.agents/skills/unattended-orchestration/SKILL.md` | 141 | `R-worker-11` — the compaction rule |

---

## Item (d) — spend field: remove or implement (dead-but-accepted input is an audit trap)

**Verdict:** keep accepted (backward compat) but make the reason explicitly say "inert";
add a deprecation warning to `--explain` output.

### Current state

- `autoos_routing.py:14` documents the `spend` card field (`free-ok | credit`, default `free-ok`).
- `autoos_routing.py:24-26`: "spend=credit stays a valid value but has no combo of its own:
  the -credit chains were dropped 2026-09-23, and t2-worker / t3-driver already overflow to
  their paid legs."
- `autoos_routing.py:41` (`CARD_VALUES`), `:44` (`CARD_DEFAULTS`), `:70` (`CARD_V1_ONLY`)
  all include `spend`.
- `autoos_routing.py:155,157` (`normalize`) and `autoos_routing.py:281,288`
  (`normalize_v2` v1-compat branch) validate and carry `spend`.
- `autoos_routing.py:341` (sensitive path): "spend changes nothing here."
- `autoos_routing.py:353-355` (public path): `spend=credit` returns the **same combo** as
  `spend=free-ok`, just with reason `"public-credit"` or `"public-light-credit"` instead of
  `"public-default"` or `"public-light"`.
- ADR 0006 §3 (`docs/decisions/0006-launch-time-routing-resolver.md:61,71`) documents `spend`
  as a v1 card field and notes the `-credit` chains were dropped.

**The audit trap:** a caller passing `spend=credit` thinks they're requesting "paid-only,
no free legs." The code accepts it, validates it, and returns the same free-first combo
with a different reason label. A plan reader seeing `reason=public-credit` may believe the
route is paid-only when it is not.

### Proposal

Two-part, backward-compatible:

1. **Make the reason say "inert":** In `autoos_routing.py:353-355`, change the reason codes
   from `"public-credit"` / `"public-light-credit"` to `"public-credit-inert"` /
   `"public-light-credit-inert"`. This makes it impossible to misread the plan as "paid-only."
   The `--explain` output (`autoos-agent.py:3827`) would then print `public-credit-inert`,
   which is self-documenting.

2. **Add a deprecation note to `--explain`:** In `cmd_route` (`autoos-agent.py:3820`) or
   `route_plan_for`, when the card carries `spend=credit`, prepend an explain line:
   `"spend=credit is inert: the -credit chains were dropped 2026-09-23; this routes to the
   same combo as spend=free-ok."` This is the operator's "dead-but-accepted input is an
   audit trap" fix — the caller sees the warning in every plan and explain output.

**Do NOT remove the field:** v1 cards in the wild may carry `spend=credit`. Removing it
would break them with a `CardError`. The ADR 0006 contract (§3, line 61) names `spend` as
a v1 field; removing it is a contract change that needs the "ids are a contract" treatment
(ADR 0006 decision 2).

**Diff size:** ~8 lines in `autoos_routing.py` (rename 2 reason codes) + ~5 lines in
`autoos-agent.py` (deprecation explain line). No combo or routing-behaviour change — the
combo returned is unchanged.

**Status:** **proposed-only** (`autoos_routing.py` and `autoos-agent.py` are L1-routing
backlog territory; the reason-code rename changes plan output text, which tests may pin).

**Why:** The operator's framing: "dead-but-accepted input is an audit trap." A field that
is accepted, validated, and has zero effect is worse than one that errors — it creates a
false belief. Making the reason say "inert" is the minimum change that closes the trap
without breaking the v1 contract.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_routing.py` | 14, 24–26 | `spend` doc + inert note |
| `tools/autoos_routing.py` | 41, 44, 70 | `CARD_VALUES`, `CARD_DEFAULTS`, `CARD_V1_ONLY` |
| `tools/autoos_routing.py` | 155, 157 | `normalize` — validate and carry `spend` |
| `tools/autoos_routing.py` | 281, 288 | `normalize_v2` v1-compat branch — validate and carry `spend` |
| `tools/autoos_routing.py` | 341, 353–355 | The two paths that read `spend` (both inert) |
| `docs/decisions/0006-launch-time-routing-resolver.md` | 61, 71 | `spend` in the v1 contract |

---

## Item (e) — watchdog: thinking-trace liveness, per-combo stuck thresholds, respawn assertions

**Verdict:** propose three additions to the heartbeat/watchdog; all in L1-routing backlog
territory.

### Current state

- `tools/autoos_heartbeat.py` is read-only: `pause_state` (inbox PAUSE/RESUME) and
  `repo_branch_state` (unpushed/dirty). It has **no thinking-trace liveness signal**.
- `tools/autoos-agent.py:6393` (`_win_liveness`) checks whether a PID is alive (Windows
  process liveness via ctypes), not whether the model is producing output.
- `SKILL.md:106` (R-coord-08): "relaunches a quiet child >25 min from its handoff." The
  25-min quiet threshold is a flat rule — no per-combo adjustment.
- There is **no per-combo stuck threshold** derived from measured decode times.
- There is **no respawn assertion** that checks clone/worktree state is clean or logs
  whether the killed t2 had uncommitted writes. The cancel path
  (`tools/autoos_agent_mcp.py:1194,1231-1236`) writes `exit.json` but does not inspect the
  worktree for uncommitted writes.

### Proposal (three parts)

1. **Thinking-trace liveness:** Add a `thinking_alive` check to the heartbeat that reads
   the child's transcript JSONL (via `autoos_context.fill_from_transcript` at
   `autoos_context.py:130`) and compares the timestamp of the last assistant message to
   `now`. A reasoning model (spark, gemini-flash with thinking) may not emit content for
   minutes while thinking — the 25-min quiet rule false-positives this. The check
   distinguishes "process alive, last assistant message <10 min ago" (thinking, not stuck)
   from "process alive, last assistant message >25 min ago" (stuck). This requires the
   transcript path, which `autoos-agent.py heartbeat --transcript` already accepts
   (`autoos-agent.py` `heartbeat` subcommand; `R-handoff-07` in `rule-map.md:119`).

2. **Per-combo stuck thresholds:** Add a `stuck_threshold_minutes` field to the registry's
   `policy` section (`catalog/ai-registry.json:1483`), keyed by combo id, with values
   derived from measured decode times (e.g., `t1-orchestrator: 30`, `t3-driver: 15`).
   The heartbeat reads this and uses it instead of the flat 25-min rule. Combos with
   reasoning legs get a longer threshold; fast combos get a shorter one. This is data in
   the registry, not a hardcoded constant.

3. **Respawn assertions:** In the cancel/relaunch path (`autoos_agent_mcp.py:1194`),
   before stopping the child, run `git status --porcelain` in the child's worktree
   (cwd from `job.json` — but read the worktree path from the runner-private kill record,
   per R-orch-17, not from `job.json`). Log `uncommitted_writes: <count>` to `exit.json`.
   After stop, assert the worktree is clean (`git status --porcelain` is empty) or
   disposable (the branch is a lane branch, not main). This catches the case where a
   killed t2 had uncommitted work that is lost on respawn.

**Diff size:** ~30 lines (part 1) + ~20 lines (part 2, registry field + heartbeat read) +
~25 lines (part 3, git status check in cancel path). Each is ≤100 lines.

**Status:** **proposed-only** (all three touch `tools/autoos-*.py` and
`catalog/ai-registry.json` — L1-routing backlog territory).

**Why:** The operator's framing: "long thinking blocks false-positive the 15-min rule"
(the 25-min variant in this repo). A reasoning model thinking for 20 minutes is working,
not stuck. Per-combo thresholds from measured decode times make the watchdog adaptive.
Respawn assertions catch the silent data loss of killing a t2 with uncommitted writes —
the one outcome the operator cannot recover from.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_heartbeat.py` | 1–19, 558–633 | `pause_state` — where liveness would be added |
| `tools/autoos-agent.py` | 6393 | `_win_liveness` — PID liveness, not thinking liveness |
| `tools/autoos_context.py` | 130–161 | `fill_from_transcript` — transcript reader for thinking-trace |
| `tools/autoos_agent_mcp.py` | 1194, 1231–1236 | Cancel path — where respawn assertions go |
| `catalog/ai-registry.json` | 1483 | `policy` section — where `stuck_threshold_minutes` goes |
| `.agents/skills/unattended-orchestration/SKILL.md` | 106 | R-coord-08 — the 25-min quiet rule |

---

## Item (f) — depth budget: only control for 5 of 7 clients — add a spawn-budget COUNT per run

**Verdict:** add a `spawn_budget` (total spawn count per run) alongside the existing depth
(nesting level) limit.

### Current state

- `tools/autoos_clients.py:16-19`: "Depth budget: every child gets `AUTOOS_AGENT_DEPTH`
  (its own depth, 1 = spawned by a human or a top-level session) and
  `AUTOOS_AGENT_MAX_DEPTH`. A spawn past the max is refused — the only depth control qwen,
  gemini, codex, agy and qoder have. opencode's in-process nesting is still
  `experimental.subagent_depth`."
- `tools/autoos_clients.py:32`: `DEFAULT_MAX_DEPTH = 2`.
- `tools/autoos_clients.py:464-478` (`child_depth`): checks `cur + 1 > cap` (nesting level).
- `CLIENTS` (`autoos_clients.py:51-74`) has 7 entries: opencode, claude, qwen, gemini,
  codex, agy, qoder. The depth budget (nesting level) controls 5 of these (qwen, gemini,
  codex, agy, qoder). opencode uses `experimental.subagent_depth`; claude uses its own
  subagent mechanism.
- There is **no spawn-count budget** — a run could spawn 100 breadth-first children at
  depth 1 and exhaust quota without ever exceeding depth 2.

### Proposal

Add an environment variable `AUTOOS_AGENT_SPAWN_BUDGET` (default: unset = unlimited) and a
`--spawn-budget` flag on `run`/`spawn` (`autoos-agent.py:7690` area, alongside
`--max-depth`). Each spawn decrements a counter in the runner's state
(`logs/agents/spawn-count.json`, keyed by run id). When the count reaches the budget,
`child_depth` (or a new `spawn_budget_check`) raises `BudgetError` with a message
naming the count and the budget. This is a **run-level** limit, not a nesting-level
limit — it caps the total number of children across the whole run tree.

The budget is checked in `child_depth` (`autoos_clients.py:464`) or a sibling function,
so every spawn path (CLI `run`, MCP `spawn`) hits it. The count file is runner-private
(written by the runner, not worker-writable), per R-orch-17.

**Diff size:** ~30 lines in `autoos_clients.py` (new `spawn_budget_check` function + env
var read) + ~10 lines in `autoos-agent.py` (`--spawn-budget` arg + call). ≤100 lines total.

**Status:** **proposed-only** (`autoos_clients.py` and `autoos-agent.py` are L1-routing
backlog territory).

**Why:** The operator's framing: "only control for 5 of 7 clients." The depth limit
controls nesting (how deep), not breadth (how many). A runaway fan-out at depth 1 — 50
parallel L3 spawns — never exceeds depth 2 but exhausts quota, RAM and rate limits. A
spawn-count budget is the missing axis: depth says "how deep," spawn-budget says "how
many."

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_clients.py` | 16–19, 32 | Depth budget doc + `DEFAULT_MAX_DEPTH` |
| `tools/autoos_clients.py` | 464–478 | `child_depth` — where the budget check goes |
| `tools/autoos_clients.py` | 51–74 | `CLIENTS` — the 7 clients |
| `tools/autoos-agent.py` | 3157–3158 | `child_depth` call in `run` — where `--spawn-budget` is read |
| `tools/autoos-agent.py` | 7690 | `--max-depth` arg — where `--spawn-budget` would be added |

---

## Item (g) — `--lean`: make "client cannot honour lean AND writer card" a `select_combo` routing input instead of spawn-time exit 2

**Verdict:** move the lean-vs-writer conflict from spawn-time refusal (exit 2) to route-time
routing input, so the resolver picks a lean-capable client instead of refusing.

### Current state

- `lean_decision` (`tools/autoos-agent.py:1957-1975`): returns `(note, refusal)`. If the
  client is in `LEAN_CLIENTS` (`autoos-agent.py:273`: `("opencode", "claude", "qoder")`),
  returns `(None, None)` — lean is honoured. If the client is NOT in `LEAN_CLIENTS` and
  the route is a review, returns `(note, None)` — note it, continue (read-only). If the
  client is NOT in `LEAN_CLIENTS` and the route is a writer, returns `(None, refusal)`.
- The refusal is returned at `autoos-agent.py:6878-6880`:
  ```python
  lean_note, lean_refusal = lean_decision(client.name, route)
  if lean_refusal is not None:
      return refuse(lean_refusal)
  ```
  This is `exit 2` (bad input) — the spawn is refused before it starts.
- `--lean` arg: `autoos-agent.py:7702-7703`. `LEAN_DROP` = `("serena", "playwright", "context7")`
  (`autoos-agent.py:262`).
- The MCP `spawn` path auto-sets `lean=True` for reviews
  (`autoos_agent_mcp.py:399-400`): `if client in ("opencode", "claude") and ... card["role"] == "review": req = dict(req, lean=True)`.
- `select_combo` (`autoos_routing.py:317`) does NOT read a `lean` field — it only reads the
  five v1 card fields (role, complexity, ctx, privacy, spend) or the v2 fields.

### Proposal

Add `lean` as a routing input, not a spawn-time gate:

1. **Card field:** Add `lean` to the v2 card (`autoos_routing.py:58-69`, `CARD_V2_VALUES`/
   `CARD_V2_DEFAULTS`) as a boolean: `lean: true` means "this run must not start heavy MCP
   servers." The v1 `--lean` flag maps to `lean: true` on the card.

2. **Routing input:** In `route_plan_for` (`autoos-agent.py` ~line 3800) or the resolver
   (`autoos_resolver.py`), when `lean=true` and the planned client is NOT in `LEAN_CLIENTS`,
   the route planner either:
   - (a) **re-routes to a lean-capable client** (one in `LEAN_CLIENTS`) that can serve the
     same card, or
   - (b) **if no lean-capable client can serve the card** (e.g., a writer card that only
     qwen can serve, and qwen is not in `LEAN_CLIENTS`), the route returns
     `state: input_required` with a message explaining the conflict — the caller can drop
     `--lean` or change the client, instead of getting an exit 2.

3. **Remove the spawn-time refusal:** Delete the `lean_refusal` path at
   `autoos-agent.py:6878-6880`. The lean decision becomes a routing input, not a gate.

**Diff size:** ~20 lines in `autoos_routing.py` (new `lean` field) + ~30 lines in
`autoos-agent.py` (route-time lean handling, remove spawn-time refusal). ~50 lines total.

**Status:** **proposed-only** (`autoos-agent.py` and `autoos_routing.py` are L1-routing
backlog territory; this changes the routing contract — `select_combo` gains a new input).

**Why:** The operator's framing: "make it a `select_combo` routing input instead of a
spawn-time exit 2." A spawn-time exit 2 is a dead end — the caller gets a refusal and must
retry with different flags. A routing input lets the resolver pick a path that works:
"you asked for lean AND a writer; qwen can't do lean, but opencode can — routing to
opencode." This is the same pattern as the privacy filter (a hard filter that changes the
combo, not a refusal that stops the run).

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos-agent.py` | 1957–1975 | `lean_decision` — the function to replace |
| `tools/autoos-agent.py` | 6874–6880 | The spawn-time refusal to remove |
| `tools/autoos-agent.py` | 262, 273 | `LEAN_DROP`, `LEAN_CLIENTS` |
| `tools/autoos-agent.py` | 7702–7703 | `--lean` arg |
| `tools/autoos_routing.py` | 58–69 | `CARD_V2_VALUES`/`CARD_V2_DEFAULTS` — where `lean` goes |
| `tools/autoos_agent_mcp.py` | 399–400 | MCP auto-lean for reviews |

---

## Item (h) — observability: per-run token/context-peak metrics into `exit.json`

**Verdict:** extend `exit.json` with token total, context peak, model, and duration;
read from the child's transcript after exit.

### Current state

- `exit.json` is written by `_write_exit` (`tools/autoos_agent_mcp.py:524-532`), which opens
  the file with `O_EXCL` (first writer wins — the runner and cancel race for it).
- Normal exit (`autoos_agent_mcp.py:756`): `_write_exit(path, {"rc": rc, "ended": time.time()})`.
  That's it — just `rc` and `ended`.
- Cancel exit (`autoos_agent_mcp.py:1194,1234`): `{"cancelled": True, "rc": None, "ended": ...}`.
- Missing-client exit (`autoos_agent_mcp.py:731`): `{"rc": 3, "ended": ...}`.
- `autoos_context.py:130-161` (`fill_from_transcript`) reads the last assistant usage record
  from a transcript JSONL and returns `{"tokens": int, "model": str | None}`.
- There is **no peak-context tracking** — `fill_from_transcript` returns the last usage, not
  the max. And the transcript path is not passed to `_write_exit`.

### Proposal

Extend the normal-exit `_write_exit` call (`autoos_agent_mcp.py:756`) to include metrics
read from the child's transcript after the child exits:

```python
# after subprocess.call returns rc:
metrics = {}
transcript = job.get("transcript")  # or discover via autoos_context.discover_transcript
if transcript:
    fill = autoos_context.fill_from_transcript(open(transcript, encoding="utf-8"))
    if fill:
        metrics = {"tokens_total": fill["tokens"], "model": fill["model"]}
_write_exit(path, {"rc": rc, "ended": time.time(), **metrics})
```

For peak context: iterate all assistant usage records (not just the last) and track the
max. This is a small extension to `fill_from_transcript` or a new `peak_from_transcript`
function in `autoos_context.py` (~15 lines).

The `exit.json` schema becomes:
```json
{"rc": 0, "ended": 1234567890.0, "tokens_total": 45000, "context_peak": 52000, "model": "gemini/gemini-3.8-flash"}
```

Cancel and missing-client exits keep their current shape (no transcript to read).

**Diff size:** ~20 lines in `autoos_agent_mcp.py` (read transcript, extend `_write_exit` call)
+ ~15 lines in `autoos_context.py` (peak tracking). ≤50 lines total.

**Status:** **proposed-only** (`autoos_agent_mcp.py` and `autoos_context.py` are
`tools/autoos-*.py` — L1-routing backlog territory; the `exit.json` schema change may
break consumers that read it with a fixed schema).

**Why:** The operator's framing: "per-run token/context-peak metrics into exit.json."
Today `exit.json` carries `rc` and `ended` — an operator reading it cannot tell whether
a run used 1k or 100k tokens, or whether it hit 95% context before exiting. The
transcript has this data (every assistant message carries a `usage` object); the
runner just doesn't read it. Adding it to `exit.json` makes the run observable without
parsing the transcript.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_agent_mcp.py` | 524–532 | `_write_exit` — the writer to extend |
| `tools/autoos_agent_mcp.py` | 754–756 | Normal exit — where metrics would be added |
| `tools/autoos_agent_mcp.py` | 731 | Missing-client exit (no change needed) |
| `tools/autoos_agent_mcp.py` | 1194, 1234 | Cancel exits (no change needed) |
| `tools/autoos_context.py` | 130–161 | `fill_from_transcript` — the transcript reader |
| `tools/autoos_context.py` | 198–212 | `discover_transcript` — finding the transcript path |

---

## Item (i) — ack-probe budget (≥2048 for reasoning models) into the registry, not prose

**Verdict:** move the hardcoded `max_tokens=2048` from probe scripts into the registry's
`policy` section, keyed by reasoning flag.

### Current state

- `tools/audit-router.py:279-283`: `_chat_once` has `max_tokens: int = 2048` with a
  comment: "2048, not a few dozen: reasoning legs (spark, gemini-flash) spend tokens on
  hidden thinking before emitting content, and a small budget makes them answer 'empty
  response' — which reads as a dead leg but is a budget fault."
- `tools/audit-router.py:307`: `_chat` has `max_tokens: int = 2048`.
- `tools/probe-recall.py:166,237,303`: `max_tokens=2048` as default parameter.
- `tools/probe-toolcalls.py:116,127,184`: `max_tokens=2048` as default parameter.
- The registry's `policy` section (`catalog/ai-registry.json:1483`) has `brief_tokens`,
  `bucket_table`, `handoff_caps`, `review_counts`, `leg_rules` — but **no probe-budget
  field**.
- The registry has `reasoning: true/false` on model entries (e.g., the spark and
  gemini-flash models carry `reasoning: true`).

### Proposal

Add a `probe_budget` block to the registry's `policy` section
(`catalog/ai-registry.json:1483`):

```json
"probe_budget": {
  "default": { "max_tokens": 256 },
  "reasoning": { "max_tokens": 2048 },
  "source": "audit-router.py:280 comment — reasoning legs spend tokens on hidden thinking"
}
```

Then update the probe scripts (`audit-router.py`, `probe-recall.py`,
`probe-toolcalls.py`) to read this from the registry instead of hardcoding `2048`:
the probe reads the model's `reasoning` flag, picks `reasoning.max_tokens` or
`default.max_tokens`, and passes it to the chat call. The `source` field carries the
provenance (the comment that explains why 2048, not 256).

**Diff size:** ~10 lines in `catalog/ai-registry.json` (new `probe_budget` block) + ~15
lines across the three probe scripts (read from registry, pass to chat call). ≤50 lines.

**Status:** **proposed-only** (`catalog/ai-registry.json` policy/leg_rules and the probe
scripts are L1-routing backlog territory; `tools/sync-*` may need to carry the field
through to rendered configs).

**Why:** The operator's framing: "into the registry next to each reasoning combo, not
prose." The 2048 value is correct (measured: reasoning models need it) but it lives in a
comment in `audit-router.py:280` and as a default parameter in three scripts. A new probe
script that forgets to set `max_tokens=2048` will false-positive a reasoning leg as dead.
Making it registry data means every probe reads the same source — the registry — and a
model marked `reasoning: true` automatically gets the 2048 budget.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/audit-router.py` | 279–283 | `_chat_once` — hardcoded `max_tokens=2048` + the comment |
| `tools/audit-router.py` | 307 | `_chat` — same default |
| `tools/probe-recall.py` | 166, 237, 303 | `max_tokens=2048` defaults |
| `tools/probe-toolcalls.py` | 116, 127, 184 | `max_tokens=2048` defaults |
| `catalog/ai-registry.json` | 1483 | `policy` section — where `probe_budget` goes |

---

## Item (j) — canary: fake key-shaped string in a tracked file + alert if any agent output contains it

**Verdict:** propose a canary string + output scan; the scan can be safely implemented as a
new standalone checker, but integration into the output pipeline is L1-routing backlog.

### Current state

- `tools/autoos_redact.py` is the redaction module. `Redactor` (`autoos_redact.py:193`)
  masks known secret shapes (bearer tokens, key prefixes, env values) in text streams. It
  has `add_values` (`:209`) and `add_env` (`:214`) to add known secrets to mask.
- `autoos_redact.py:41-42`: `MASK = "***"`, `TEXT_MASK = "[autoos:redacted]"`.
- There is **no canary** — a fake key-shaped string placed in a tracked file that, if found
  in agent output, proves the agent copied a tracked-file secret into its output.
- The "decoy" reference at `autoos-agent.py:4690` is about a `git branch` decoy in a
  security audit, not a key canary.
- AGENTS.md §1: "This repository is public and has already leaked credentials once."

### Proposal

1. **Canary string:** Add a fake key-shaped string to a tracked `.example` file (e.g.,
   `configuration/api-keys.yml.example` — a file that already carries obviously fake
   values per AGENTS.md §1). The canary: `AUTOOS_CANARY_sk-or-0deadbeef00000000000000000000000000000000000000`.
   It is shaped like an OpenRouter key (`sk-or-` prefix, 48+ chars) so the existing
   redaction patterns (`autoos_redact.py:15`: `sk-or` is a known prefix) would mask it in
   output — but the point is to detect it BEFORE masking, proving the agent read a tracked
   file and copied it.

2. **Output scan:** Add a `canary_check` function to `autoos_redact.py` (or a new
   `tools/canary-check.py` script) that scans a text blob for the canary string and
   returns `(found: bool, location: str)`. The check runs:
   - In the spawner's output pump (`autoos-agent.py` Redactor usage) — if the canary
     appears in a worker's stdout/stderr, the run is failed with a canary-leak message.
   - As a standalone script: `python3 tools/canary-check.py <file-or-dir>` — scans a
     commit diff, a DONE note, or a log file for the canary.

3. **Test:** `tests/test_canary.py` — seeds the canary in a temp file, runs the scan,
   asserts detection. Also asserts the canary does NOT appear in any existing tracked
   file OTHER than the `.example` (so a real secret that happens to match the canary
   shape is not false-positive — the canary is a unique, registered string).

**Diff size:** ~30 lines for `canary_check` + ~25 lines for the test + 1 line in the
`.example` file. The standalone script + test is ≤100 lines and does not touch routing.

**Status:** **proposed-only** for the output-pump integration (touches `autoos-agent.py`
and `autoos_redact.py` — L1-routing backlog territory). The standalone `canary-check.py`
script + test could be **safely implemented** (new files, no routing-contract change), but
the brief says "implement only with reviews" — so proposed pending cross-family review.

**Why:** The operator's framing: "fake key-shaped string in a tracked file + alert if any
agent output contains it." The repo is public and leaked once. The redaction module masks
known secret shapes, but it cannot catch a secret it does not know about — an agent that
reads a tracked file (which should carry only `.example` fakes) and copies its contents
into output. The canary is a tripwire: it is a fake, it is in a tracked file, and if it
appears in output, the agent copied from a tracked file — the exact leak path that put
real secrets in the public history.

### Anchors

| File | Line | What |
|---|---|---|
| `tools/autoos_redact.py` | 193–251 | `Redactor` class — where `canary_check` would live |
| `tools/autoos_redact.py` | 41–42, 107 | `MASK`, `secret_env_values` — the existing redaction infra |
| `tools/autoos-agent.py` | 4690 | The "decoy" reference (not a canary — different concept) |
| `AGENTS.md` | §1 (line 6) | "This repository is public and has already leaked credentials once" |

---

## Summary table

| Item | Verdict | Status | Primary files |
|---|---|---|---|
| (a) routing v1 matrix as generated output | Add `--explain-all` to `route` | proposed-only | `autoos-agent.py:3820,7757`; `autoos_routing.py:317`; ADR 0006:66-74 |
| (b) tier contract reframe | Reframe in ADR + handoff.md | proposed-only | ADR 0006:66-74; `combos.json:96-106`; `autoos_routing.py:349`; `docs/handoff.md:9` |
| (c) compaction checkpoints 60–70% | Add step 1.5 to state-file.md | proposed-only | `references/state-file.md:38-44`; `autoos_context.py:35-42` |
| (d) spend field | Make reason say "inert" + deprecation warn | proposed-only | `autoos_routing.py:14,24-26,353-355`; `autoos-agent.py:3820` |
| (e) watchdog liveness | Thinking-trace + per-combo thresholds + respawn assert | proposed-only | `autoos_heartbeat.py`; `autoos-agent.py:6393`; `autoos_agent_mcp.py:1194`; `ai-registry.json:1483` |
| (f) depth budget spawn count | Add `AUTOOS_AGENT_SPAWN_BUDGET` + `--spawn-budget` | proposed-only | `autoos_clients.py:16-19,464`; `autoos-agent.py:3157,7690` |
| (g) --lean routing input | Move lean-vs-writer to routing, remove exit 2 | proposed-only | `autoos-agent.py:1957,6874-6880`; `autoos_routing.py:58-69` |
| (h) observability metrics | Extend exit.json with token/peak/model | proposed-only | `autoos_agent_mcp.py:524,756`; `autoos_context.py:130` |
| (i) ack-probe budget | Move 2048 to registry `policy.probe_budget` | proposed-only | `audit-router.py:279-283`; `ai-registry.json:1483` |
| (j) canary | Canary string + output scan + test | proposed-only | `autoos_redact.py:193`; `AGENTS.md:§1` |

All items are **proposed-only**: every implementation touches `tools/autoos-*.py`,
`catalog/ai-registry.json`, or `configuration/omniroute/combos.json` — L1-routing backlog
territory per the brief's scope limits. Each proposal is ≤100 lines; each can be
implemented by an L1-routing lane with TDD + cross-family review.

---

## Open items for L0

1. **rule-map.md / CHANGELOG for R-coord-12/13:** The checker passes without them. A
   "Fifth-generation ids" section in `references/rule-map.md` and a CHANGELOG entry are
   recommended for traceability but not required by `tools/skill-rules.py check` or
   `tests/test_skill_rules.py`. Coordinate with the incident lane (ws-incident owns
   `SKILL.md` for R-coord-14) so one CHANGELOG entry covers R-coord-12/13/14.

2. **ADR 0006 + docs/handoff.md edits (item b):** These are shared docs. The incident lane
   or L1-alpha may also touch them. L0 should route the edit to one lane to avoid a merge
   conflict.

3. **`configuration/omniroute/combos.json` (items b, e, i):** Referenced read-only here;
   L1-alpha's lane owns it. A `t1-fat-context` combo (item b), a `stuck_threshold_minutes`
   policy field (item e), and a `probe_budget` policy field (item i) all need combos or
   registry changes that L1-alpha should review.

4. **Incident lane R-coord-14:** The HARD CONSTRAINT forbids this lane from editing
   `SKILL.md`. If the checker fails after the incident lane's R-coord-14 edit, the fix
   belongs to the incident lane, not this one.

---

## Review record

- **Writer:** L2 t2-worker (ws-designmemo-20260930, model: t2-worker)
- **Cross-family review:** t3-reviewer (ses_f0d34ef2cffegJBWiB7u2jVeO7) — 2026-09-30
- **Verdict:** APPROVED-WITH-NOTES
  - All 12 key anchors + ~20 secondary anchors verified correct against worktree source.
  - R-coord-12/13 presence confirmed (SKILL.md:110-111, `ok: 39 rules`).
  - Scope compliance confirmed: all 10 items proposed-only, no code changes.
  - No contradictions between items; "Why" matches operator framing for each.
  - No unflagged routing-contract changes.
  - Two non-blocking notes fixed before commit:
    1. Item (d) `normalize`/`normalize_v2` line attribution corrected (line 281 is in
       `normalize_v2`'s v1-compat branch, not `normalize`; added `normalize`'s own spend
       lines 155/157).
    2. Added path note clarifying SKILL.md anchors are against the worktree copy (d08f7f2),
       while R-coord-12/13 are stable at 110/111 in both copies.

---

## R-coord-12/13 check result

| Check | Result |
|---|---|
| R-coord-12 present in repo `SKILL.md` | ✅ line 110, exact text quoted above |
| R-coord-13 present in repo `SKILL.md` | ✅ line 111, exact text quoted above |
| `python tools/skill-rules.py check` | ✅ `ok: 39 rules` |
| `tests/test_skill_rules.py` (26 tests) | ✅ 26 passed, 0 failed |
| `references/rule-map.md` needs entry? | ❌ not required (checker passes; optional for traceability) |
| `CHANGELOG.md` needs entry? | ❌ not required (checker passes; recommended for docs) |
