---
name: unattended-orchestration
description: "Portable runner and CLI orchestrator for work that runs with nobody watching: worktree-isolated lanes, usage-limit and outage recovery, guard-gated auto-merge. Load when spawning unattended sessions or subagents, briefing or reviewing their work, or acting as the L1 main orchestrator (read references/main-orchestrator.md first)."
---

# Unattended Orchestration

Long-horizon agent work with **no human present**: an overnight batch, a weekend migration, a
queue of handoffs too large for one sitting. The runner starts each session, watches it, and
recovers it through a usage limit, a 529, and a session that stops early.

**Use something else** when a human is present for the whole run and it finishes in one sitting
(Agent/Workflow tools — see `swarm-orchestration`), or when a human can glance at a pane
(`herdr-orchestration`). The full decision tree and adoption steps are in
[`references/runner-setup.md`](references/runner-setup.md).

**If you are the L1 main orchestrator** of a three-level hierarchy — you hold the plan, spawn L2
Opus sessions, judge what comes back — read
[`references/main-orchestrator.md`](references/main-orchestrator.md) first: the standing orders
every L1 session needs before touching a brief.

## Files

| File | Holds |
|---|---|
| [`references/main-orchestrator.md`](references/main-orchestrator.md) | L1 standing orders: navigate, spawn/brief L2, cross-family review, off-peak timing, machine checks, token discipline, evidence rules |
| [`references/l3-routing.md`](references/l3-routing.md) | Choosing an L3 leaf model (cost/time/urgency/privacy) and the leaf gate |
| [`references/lanes.md`](references/lanes.md) | Lanes, sessions, `resources`, `dependsOn`, per-session model choice |
| [`references/state-file.md`](references/state-file.md) | The status/state file template an orchestrator rewrites every wave and hands off from |
| [`references/recovery.md`](references/recovery.md) | Classifying a stopped turn (auth/limit/transient/stalled/…) and recovering it |
| [`references/layers.md`](references/layers.md) | Orchestrator → session → subagent layers, the controller's inbox channel, successor briefs |
| [`references/runner-setup.md`](references/runner-setup.md) | Adopting the runner in a new repo, its config fields, its CLI flags (`-Validate`, `-DryRun`) |
| [`references/changing-the-runner.md`](references/changing-the-runner.md) | The test suites, the PowerShell array trap, where the incident backlog lives |
| [`references/rule-map.md`](references/rule-map.md) | Old rule ids → new ids or code pointers (tested by `tests/test_skill_rules.py`) |
| [`unattended-orchestration.md`](unattended-orchestration.md) | The opencode 3-tier routing protocol (task card, clients, depth budget) this repo runs on |
| `HandoffCore.psm1`, `run_handoff_sessions.ps1`, `trust_worktree.py`, `l1_handoff.py`, `provider_windows.py` | The tested code — behaviour lives there, not restated here |

## Levels (L0-L3)

A router-led run nests four levels of depth. A session's *name* (e.g. `autoos-L1-routing`) is a
project label, not proof of its level — a launch prompt states the level; one that names none
asks before loading that level's rules.

| Level | Job | Relaunches | Asks the operator |
|---|---|---|---|
| **L0** router | the operator's own session: routes intent, tracks PAUSE/resume, is the *only* path to the operator; never executes project work, cleanups or setup — hands them to the L1 coordinator (R-router-03) | L1, when L1's status timestamp stays quiet >25 min from its handoff (R-coord-08; source: common.md "Heartbeats never stop") | R-router-01 (its own rule: it researches the obvious ones, forwards the rest verbatim) |
| **L1** coordinator | one per run: launches L2s, merges lanes into main, pushes, cleans up | L2, past its context cap (R-coord-06); or a busy L2 whose status timestamp it watches stays quiet >25 min from its handoff (R-coord-08) | never directly — appends `question: … \| options: …` to L0's inbox (`RUN/inbox/L0.md`), per R-router-01 |
| **L2** orchestrator | one per track/plan: owns a worktree + branch, spawns and reviews L3 | L3, never resuming a no-change stop (R-orch-06) | never directly — same channel, via L1 |
| **L3** worker / reviewer | one closed task, an explicit return contract (`docs/agent-protocol.md`) | nothing — R-worker-06 | never |

This is depth, not the model tier `unattended-orchestration.md`'s `t1`/`t2`/`t3` picks for a task
— the two axes are independent. *Relaunches* is a watch: each level reads the status timestamp the
level below rewrites and relaunches — never resumes (R-orch-06) — a busy child quiet >25 min from
its handoff (R-coord-08, the beat). **L0 only routes and relays: the level that owns a topic
researches it, answers it and summarizes it; L0 forwards what it cannot answer, and hands the
work it routes to the L1 coordinator — it never executes it** (R-router-01, R-router-03; source:
work/L1-routing/FOLD4.common.md "Levels").
`references/main-orchestrator.md` names a single top session "L1" in an older 3-level scheme (its
L1 ≈ this table's L2) — read whichever your brief names.

## Rules

One rule per line, grouped by level topic: `R-<topic>-NN: <imperative>. (why: <=12 words;
source: <test|sha|path|run>)`. Topics: `router` (L0), `coord` (L1), `orch` (L2), `worker`
(L3). A source names the test or measurement behind the rule — an unmeasured lesson is not a
rule yet. `python3 tools/skill-rules.py check` (CI) enforces format, uniqueness and
near-duplicates. Old ids (R-spawn-*, R-review-*, etc.) resolve via
[`references/rule-map.md`](references/rule-map.md).

Rules migrate into code over time: once a check moves into `autoos-agent` MCP
tools, `tools/autoos*.py` or the resolver, this file points to the tool instead of restating it
(source: briefs/common.md "Skill rules bind every spawned agent", operator 2026-09-26T13:43:33Z).

Every brief names this skill: the spawned session loads it, and a worker that cannot read files
gets the rules its task touches inlined (source: briefs/common.md "Skill rules bind every spawned
agent", operator 2026-09-26T13:45Z).

Mechanical rules already in code — the interface R-coord-09 mandates, and what each part gates:
`python3 tools/autoos-agent.py` (the MCP server exposes the same) `run` (MCP `spawn`) launches one
worker, `route` prints the resolver's pick for a task card, `ready` appends the ready line only
after `review-status` and the pushed sha check out, `heartbeat` reports pause/unpushed/context
fill, `ps` lists every live worker, `usage` the spend by provider and lane, `context` this
session's fill, `token-rate` orchestrator tokens per merged change (RESTART spec §5), `list` the tiers and who may spawn whom. Also in code:
`python3 tools/autoos_resolver.py` (leg order, TPM caps, unavailable_until) and
`python3 tools/registry.py validate` (registry shape). See `references/rule-map.md` for the full
list of code-enforced rules.

A level's rules bind every session doing that job: `coord` rules bind whoever runs lanes and
merges (L1, and an L2 for its own lanes — so the heartbeat rules `R-coord-07`/`R-coord-08` bind
L2 as well); `orch` rules bind whoever briefs or reviews workers.

### router (L0)

- R-router-01: Only L0 asks, researched, as the batched `Q:` line; others write `question:` to L0 or its parent's inbox. (why: a dialog blocks a background session; source: common.md, inbox 05:47Z)
- R-router-02: Diagnose against host and route state before declaring failure. (why: first verdict is usually wrong; source: review-b3c1.out 2026-09-26T07:33Z)
- R-router-03: Route, decide, ask, verify; never run project work, cleanups or setup - hand them to the L1 coordinator. (why: a router doing work stops routing; source: operator via routing-00 10:4xZ)

### coord (L1)

- R-coord-01: Cut lanes from main; merge main before spawn and CI, not between green CI and ready; lane→orch→main no-ff; one merger, mutex, freeze parent. (why: ready needs tested tip; source: 2c3e4f7)
- R-coord-02: Verify cheap done, judge it: tests, diff vs brief, files-read; no REPORT = incomplete, resume its WIP; Opus picks critical. (why: cheap done unproven; source: review-a8.out, REDACTFIX.out)
- R-coord-03: Claude orchestrates, final-checks, never implements/researches; Haiku first-passes only as Q-003's fallback; writers via `route`. (why: a Claude limit stops the run; source: common.md)
- R-coord-04: Hold headroom via `heartbeat`: ≤3 lanes + 3 readers, MemAvailable ≥3 GB, heavy suites 1/orchestrator, 2/host. (why: headroom keeps tests and builds alive; source: briefs/common.md)
- R-coord-06: At cap (`autoos-agent.py context`, registry `handoff_caps`): rewrite state, brief successor, append handoff, stop. (why: successor resumes from state alone; source: common.md Context cap)
- R-coord-07: Heartbeat: L1/L2 run a 10-min CronCreate beat from launch to stop, recreated after relaunch or clear. (why: an idle session is retired after 8 h; source: common.md Heartbeats never stop)
- R-coord-08: Beat pushes, pongs pings, WIP-commits past-beat work, stamps status, reads inbox, relaunches a quiet child >25 min. (why: stale orders ran workers post-stop; source: common.md 15:3xZ)
- R-coord-09: L3 spawns, routing, status: autoos-agent only, never hand-roll; L2 launches: the runner. (why: hand-rolls drift from gates; source: operator 04:50Z, REVGATE.record.md)
- R-coord-10: After a cancel, `ps` the lane: no runner, client or reparented child may survive. (why: a runner-only kill orphans the client's ~480 MB serve; source: SB-A D-103 2026-09-28)
- R-coord-11: MCP code loads from its cwd checkout: ff it to main, restart the MCP, probe isolated. (why: ff under a running server mixes old and new code; source: SCOPEBUS probes 1-4, 2026-09-29)
- R-coord-12: L0 and L1 sessions are interactive top-level sessions, never subagent children. (why: unattended parents must be watchable and addressable; source: operator 2026-09-30, ws-omniroute run)
- R-coord-13: L1 and L2 follow this skill, never fix or research; they spawn L2/L3 sized to complexity. (why: orchestrators that work stop orchestrating; source: operator 2026-09-30)
- R-coord-14: Never write the gateway data-dir key/config files (~/.omniroute); operator-only. (why: a lane's knob write destroyed the storage key; source: incident 2026-09-30 doc)

### orch (L2)

- R-orch-01: Write fixed BRIEF/REPORT fields; terse one line per fact, evidence by pointer; one writer per file. (why: fixed fields parse and stay short; source: common.md Talking to other agents)
- R-orch-02: Brief cheap workers with one file, exact spec, file:line anchors. (why: several files invite fabricated completion; source: L1-HANDOFF.md Known traps)
- R-orch-04: Feed isolated workers inline or by absolute read-only path. (why: clone cannot read outside itself; source: work/L1-routing/Q1doc.out)
- R-orch-06: Relaunch, never resume, a no-change child; verify worktree/WIP scope; run the old suite before new tests. (why: wrong tree; unrun WIP adds defects; source: common.md, REDACTFIX2.out)
- R-orch-08: Commit lanes under lane identity; record classifier refusals verbatim and stop. (why: refusals are signals, never obstacles; source: refusals measured 2026-09-24/25)
- R-orch-10: Privileged, installer or state-mutating changes get the Sonnet final regardless of cheap verdict. (why: a shallow first pass misses setup defects; source: agysb 8b36913, inbox 23:31:09Z)
- R-orch-11: A lane that changes a route id or return code greps every consumer (lanes.md list), catalog postInstall too. (why: stale ids and bare postInstall abort CI; source: CI 36320592493, 765f189)
- R-orch-12: Approve each fresh worktree with `trust_worktree.py` before its first session. (why: background sessions cannot answer a trust dialog; source: three lanes blocked in 3s)
- R-orch-13: Plan, spec, decision, or bucket-table-big diff (lines incl. tests): pinned cross-family review before execute/merge, then Sonnet. (why: same family shares blind spots; source: inbox 00:31Z)
- R-orch-14: Never skip a slow free reviewer; Haiku stays an extra pass; record writer, reviewer, verdict. (why: a small reviewer's no-issues is no proof; source: HAIKU-EVAL.md, review-spawnredact.md)
- R-orch-15: Send the skill owner one `lesson: … evidence=…` line per bug or surprise; only a tested lesson becomes a rule. (why: unmeasured lessons corrupt the skill; source: briefs/common.md)
- R-orch-16: Code that acts on a report (merge, ready, push) is re-audited cross-family before it gates; names match exactly. (why: a substring let notsonnet sign off; source: REVGATE2.brief, 4ff89c8)
- R-orch-17: Kill targets, run id and mode come only from a runner-private record, never job.json. (why: each fix that read job.json re-opened the hole it closed; source: SB-A2..A3 Muse/Sonnet)
- R-orch-18: Registry/routes/combos: Verify = test_registry_render.py, renderer --checks, apply filters. (why: pytest-only verify let FREEKEYS-2 pass with 3 CI reds; source: CI 36506339556, FREEKEYS-2c)
- R-orch-19: Launch change (wrapper/env/cwd): an unmocked Popen test + a live scoped spawn. (why: mocked tests were green while live spawns broke; source: SCOPEBUS, test_a_real_spawn_runs_in_its_scope)

### worker (L3)

- R-worker-01: Derive every render cell and test expectation from the registry render; pin only approved order; `--check` only, minimal edits. (why: --out erased 461 lines; source: 6a61052, f5d4e00)
- R-worker-02: Author portable failing-first tests: seed bug state, assert the reason, guard platform; a dry run plans an absent tool. (why: the CI host lacks uv; source: CI 36227152085, 36360904338)
- R-worker-03: Keep runs cheap: `affected-tests.py`'s filter, full suite once per phase, snippet-lint, real --no-cache builds. (why: broad runs OOM or measure nothing; source: CI 108372206183, 1345e9b)
- R-worker-04: Test fakes reproduce the real tool's observable contract; read the real tool first, cite its lines. (why: fake drift hides real bugs; source: L1-backlog lstby 99742a1)
- R-worker-05: Gate on `set -o pipefail` and the 'N passed' line, never `tail -1 && push`. (why: 'no tests ran' exited 0 and was pushed; source: work/L1-routing/B3a.out)
- R-worker-06: A leaf role never spawns; only a spawning role lists the autoos-agent MCP. (why: supervisor wanting to code mis-decomposed; source: tests/test_agent_harness.py)
- R-worker-07: Never shellcheck tests/run-tests.sh locally; run jobs over ~2 GB under systemd-run MemoryMax=2G. (why: its OOM killed every session twice; source: herdr-server.log 2026-09-26T15:25Z)
- R-worker-08: Verify on a detached copy (`git clone --no-hardlinks`, `worktree add --detach`; `tar` only if no test reads git); never cp a worktree. (why: copies share its index; source: ci7)
- R-worker-09: Never call Serena `activate_project` from a worktree. (why: the one shared server re-points every session; source: briefs/common.md MCP, 2026-09-26)
- R-worker-10: Accept a detector or redactor on the real output corpus; give each new raw-data consumer its own redaction test. (why: fixtures passed; a secret leaked; source: SPAWNFIX3d, REDACTFIX3)
- R-worker-11: A 'summarise, no tools' ask is harness compaction unless a leading <cross-session-message from=> wrapper marks a peer. (why: transcript export; source: D-146, SB-C2, CompactionRuleTests)
