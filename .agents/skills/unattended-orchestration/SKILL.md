---
name: unattended-orchestration
description: "Portable runner and CLI orchestrator for work that runs with nobody watching: worktree-isolated lanes, usage-limit and outage recovery, guard-gated auto-merge, or CAO's interactive 3-layer hierarchy. Load when spawning unattended sessions or subagents, briefing or reviewing their work, or acting as the L1 main orchestrator (read references/main-orchestrator.md first)."
---

# Unattended Orchestration

Long-horizon agent work with **no human present**: an overnight batch, a weekend migration, a
queue of handoffs too large for one sitting. The runner starts each session, watches it, and
recovers it through a usage limit, a 529, and a session that stops early. CAO (bottom of this
file) is the interactive sibling: a live, steerable hierarchy for work you want to watch instead.

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
| [`references/cao-runbook.md`](references/cao-runbook.md) | CAO: layers, safety mechanisms, providers, setup traps |
| [`references/rule-map.md`](references/rule-map.md) | Old rule ids → new ids or code pointers (tested by `tests/test_skill_rules.py`) |
| [`unattended-orchestration.md`](unattended-orchestration.md) | The opencode 3-tier routing protocol (task card, clients, depth budget) this repo runs on |
| `HandoffCore.psm1`, `run_handoff_sessions.ps1`, `trust_worktree.py`, `l1_handoff.py`, `provider_windows.py`, `cao/` | The tested code — behaviour lives there, not restated here |

## Levels (L0-L3)

A router-led run nests four levels of depth. A session's *name* (e.g. `autoos-L1-routing`) is a
project label, not proof of its level — a launch prompt states the level; one that names none
asks before loading that level's rules.

| Level | Job | Relaunches | Asks the operator |
|---|---|---|---|
| **L0** router | the operator's own session: routes intent, tracks PAUSE/resume, is the *only* path to the operator | L1 | R-router-01 (its own rule: it researches the obvious ones, forwards the rest verbatim) |
| **L1** coordinator | one per run: launches L2s, merges lanes into main, pushes, cleans up | L2, past its context cap, from its handoff (R-coord-06) | never directly — R-router-01 |
| **L2** orchestrator | one per track/plan: owns a worktree + branch, spawns and reviews L3 | L3, never resuming a no-change stop (R-orch-06) | never directly — same channel, via L1 |
| **L3** worker / reviewer | one closed task, an explicit return contract (`docs/agent-protocol.md`) | nothing — R-worker-06 | never |

This is depth, not the model tier `unattended-orchestration.md`'s `t1`/`t2`/`t3` picks for a task
— the two axes are independent. `references/main-orchestrator.md` names a single top session
"L1" in an older 3-level scheme (its L1 ≈ this table's L2) — read whichever your brief names.

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

Mechanical rules already in code: run `python3 tools/autoos-agent.py heartbeat` (pause, unpushed,
context cap), `python3 tools/autoos_resolver.py` (leg order, TPM caps, unavailable_until),
`python3 tools/registry.py validate` (registry shape). See `references/rule-map.md` for the
full list of code-enforced rules.

A level's rules bind every session doing that job: `coord` rules bind whoever runs lanes and
merges (L1, and an L2 for its own lanes); `orch` rules bind whoever briefs or reviews workers.

### router (L0)

- R-router-01: Only L0 asks the operator, and only after research; every other session writes `question:` to the inbox. (why: a dialog blocks a background session; source: common.md Questions)
- R-router-02: Diagnose against host and route state before declaring failure. (why: first verdict is usually wrong; source: review-b3c1.out 2026-09-26T07:33Z)

### coord (L1)

- R-coord-01: Cut lanes from main; merge two-stage (lane→orch→main), no-ff, under mutex, one merger. (why: serial merges need no hand reconciling; source: HandoffCore.Tests.ps1)
- R-coord-02: Verify cheap done, judge findings yourself: tests, diff vs brief, files-read; Opus picks critical. (why: cheap done is unproven until you verify; source: review-a8.out, inbox 17:52:55Z)
- R-coord-03: Claude orchestrates and final-checks, never implements; pick writers by complexity from `route --explain`. (why: Claude rate limit stops the whole run; source: common.md Claude budget)
- R-coord-04: Hold host headroom: run `autoos-agent.py heartbeat`; <=3 lanes + 3 readers, MemAvailable >= 3 GB. (why: headroom keeps tests and builds alive; source: briefs/common.md "Host limits")
- R-coord-06: At cap (`autoos-agent.py context`, registry `handoff_caps`): rewrite state, brief successor, append handoff, stop. (why: successor resumes from state alone; source: common.md Context cap)
- R-coord-07: Heartbeat: L1/L2 run a 10-min CronCreate beat from launch to stop, recreated after relaunch or clear. (why: an idle session is retired after 8 h; source: common.md Heartbeats never stop)
- R-coord-08: Each beat pushes, rewrites status (timestamp first), checks children; read inbox before each launch. (why: stale orders launched three workers post-stop; source: common.md 15:3xZ)

### orch (L2)

- R-orch-01: Write fixed BRIEF/REPORT fields; terse one line per fact, evidence by pointer; one writer per file. (why: fixed fields parse and stay short; source: common.md Talking to other agents)
- R-orch-02: Brief cheap workers with one file, exact spec, file:line anchors. (why: several files invite fabricated completion; source: L1-HANDOFF.md Known traps)
- R-orch-04: Feed isolated workers inline or by absolute read-only path. (why: clone cannot read outside itself; source: work/L1-routing/Q1doc.out)
- R-orch-06: Relaunch, never resume, a no-change or >25-min-quiet child; verify worktree and WIP scope first. (why: resumed session works in wrong tree; source: inbox 2026-09-26, common.md 15:3xZ)
- R-orch-08: Commit lanes under lane identity; record classifier refusals verbatim and stop. (why: refusals are signals, never obstacles; source: refusals measured 2026-09-24/25)
- R-orch-10: Any change that runs sudo/root gets the Sonnet final review regardless of cheap verdict. (why: privileged ops need highest-trust gate; source: L1-backlog agysb 8b36913)
- R-orch-11: A lane that retires or re-legs a route greps its id as a DEFAULT in tests/, configuration/, lib/, start-stack.*. (why: stale ids break CI silently; source: CI 36320592493, inbox 17:09:02Z)
- R-orch-12: Approve each fresh worktree with `trust_worktree.py` before its first session. (why: background sessions cannot answer a trust dialog; source: three lanes blocked in 3s)
- R-orch-13: Before ready: cross-family review (model pinned; queue a slow free reviewer, never skip), then Sonnet. (why: same family repeats writer blind spots; source: cao/dispatch.py, HAIKU-EVAL.md)
- R-orch-14: Haiku is an extra first pass only, never the Sonnet final; record reviewer, model, verdict in the lane record. (why: Haiku caught 1 of 9 known defects; source: HAIKU-EVAL.md, L0 18:02:55Z)

### worker (L3)

- R-worker-01: Derive every render cell from registry fields; `--check` only, minimal edits. (why: --out erased 461 hand-kept lines; source: 6a61052 rejected, d1763a8)
- R-worker-02: Author portable failing-first tests: seed bug state, assert the reason, guard platform. (why: passes here, fails there without guards; source: main CI 36227152085)
- R-worker-03: Keep runs cheap: filter to the touched area, full suite once per phase, snippet-lint, real --no-cache builds. (why: broad runs OOM or measure nothing; source: CI 108372206183, 1345e9b)
- R-worker-04: Test fakes reproduce the real tool's observable contract; read the real tool first, cite its lines. (why: fake drift hides real bugs; source: L1-backlog lstby 99742a1)
- R-worker-05: Gate on `set -o pipefail` and the 'N passed' line, never `tail -1 && push`. (why: 'no tests ran' exited 0 and was pushed; source: work/L1-routing/B3a.out)
- R-worker-06: A leaf role never spawns; only a spawning role lists the autoos-agent MCP. (why: supervisor wanting to code mis-decomposed; source: tests/test_agent_harness.py)
- R-worker-07: Never shellcheck tests/run-tests.sh locally; run jobs over ~2 GB under systemd-run MemoryMax=2G. (why: its OOM killed every session twice; source: herdr-server.log 2026-09-26T15:25Z)
- R-worker-08: Mutation-test a scratch copy (`tar --exclude=.git`), never the worktree. (why: a mutation must not touch the lane's tree; source: status/L1-backlog.lane-omni.report.md)
- R-worker-09: Never call Serena `activate_project` from a worktree. (why: the one shared server re-points every session; source: briefs/common.md MCP, 2026-09-26)

## CAO quickstart

CAO (CLI Agent Orchestrator) is the interactive sibling: a live, inspectable hierarchy of agent
terminals with a web dashboard on `:9889`, for work you want to watch and steer across providers.
The commands below are kept here (not in `references/cao-runbook.md`) because
`tests/cao/test_cli.py` scans this file for every `python -m cao ...` string it advertises and
checks each one against the real parser — moving them would silently stop testing what this file
tells you to run. Everything else about CAO (layers, safety mechanisms, providers, setup traps,
proven incidents) is in [`references/cao-runbook.md`](references/cao-runbook.md).

```bash
export PYTHONPATH=<repo>/skills/unattended-orchestration
export CAO_HOME_DIR="$HOME/.cao"                    # or the CLI and the server use different homes

cao-server &                                        # once per host; nothing below works without it
python -m cao check                                 # config, credentials, server, warnings
python -m cao probe                                 # do the pools ANSWER? closes proven-dead ones
python -m cao profiles --out ./profiles             # then run the `cao install` lines it prints
python -m cao plan                                  # scaffold the plan WITH THE USER; ships invalid
python -m cao launch --phase p1                     # refuses without a valid plan
python -m cao sweep --answer                        # unblock waiting agents; run this every few minutes
python -m cao verify --phase p1 --terminal <id>     # YOU run the guard, not the agent
python -m cao verify --phase p1 --rework            # on failure: fresh agent, same phase
python -m cao resume --apply                        # after a crash or a usage limit
```

Exit codes: **0** done, **1** action needed, **2** crash/unreachable, **3** refused, **4** needs a
human.
