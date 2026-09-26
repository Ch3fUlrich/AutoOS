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
| [`references/recovery.md`](references/recovery.md) | Classifying a stopped turn (auth/limit/transient/stalled/…) and recovering it |
| [`references/layers.md`](references/layers.md) | Orchestrator → session → subagent layers, the controller's inbox channel, successor briefs |
| [`references/runner-setup.md`](references/runner-setup.md) | Adopting the runner in a new repo, its config fields, its CLI flags |
| [`references/changing-the-runner.md`](references/changing-the-runner.md) | The test suites, the PowerShell array trap, where the incident backlog lives |
| [`references/cao-runbook.md`](references/cao-runbook.md) | CAO: layers, safety mechanisms, providers, setup traps |
| [`unattended-orchestration.md`](unattended-orchestration.md) | The opencode 3-tier routing protocol (task card, clients, depth budget) this repo runs on |
| `HandoffCore.psm1`, `run_handoff_sessions.ps1`, `trust_worktree.py`, `l1_handoff.py`, `provider_windows.py`, `cao/` | The tested code — behaviour lives there, not restated here |

## Rules

One rule per line, grouped by topic: `R-<topic>-<nn>: <imperative>. (why: …; source: …)`. A
source names the test or measurement behind the rule (D20) — an unmeasured lesson is not a rule
yet. `python3 tools/skill-rules.py check` (CI) enforces the format, uniqueness and near-duplicates.
Source paths: `tests/…` is the repo's `tests/`; a bare `test_*.py` is this skill's `tests/`
(`test_autoos_spawner.py` is the repo's `tests/`); `inbox/…`, `work/…`, `done/…` and `status/…` are the
run log `logs/handoff-sessions/<date>/` (default date 2026-09-25 unless the line names another).

### spawn

- R-spawn-01: After a lane's DONE note, `claude stop <id>` it and remove the merged, clean worktree. (why: a finished session can idle for hours; source: test_l1_handoff.py, 2026-09-25)
- R-spawn-02: Run the client in its own process group; reap leftovers after it exits. (why: Serena/language servers survive a cancelled worker; source: test_autoos_spawner.py ProcessGroupTests)
- R-spawn-03: `--mcp-config`/`--allowedTools` are variadic: add another option before the prompt. (why: the prompt is silently eaten as an argument; source: daemon.log, test_autoos_spawner.py)
- R-spawn-04: Give lane Claude sessions trust_worktree.py --lane-mcp's strict per-worktree MCP config. (why: each worktree gets a private Serena; source: test_trust_worktree.py, 20:50Z)
- R-spawn-05: Pre-approve a fresh worktree with `trust_worktree.py` before its first session starts. (why: a background session can't answer a trust dialog; source: measured: three lanes blocked in 3s)
- R-spawn-06: Launch non-shell lane subagents with Agent isolation:worktree, never a raw worktree path. (why: a bare path made a subagent call EnterWorktree, stall; source: inbox/L1-routing.md 21:1xZ)
- R-spawn-07: An isolation:worktree subagent can't run pwsh/bash; use a non-isolated agent for shell work. (why: 3 of 3 isolated agents stopped before any edit; source: inbox/L1-backlog.md 19:30Z)
- R-spawn-08: The spawner reads configuration/api-keys.yml from the main checkout if a worktree lacks it. (why: it's git-ignored, absent in a fresh worktree; source: test_autoos_spawner.py KeyFileTests)
- R-spawn-09: Use qoder only as a writer (you test and commit), agy only for read-only reviews. (why: headless denies qoder shell, agy shell+writes; source: work/L1-routing/B2fix.out, B3c1.out)
- R-spawn-10: Start a worktree subagent with `git merge origin/<branch>`, then check the diff to it is empty. (why: ff fails as main moves; checkout -B refused; source: inbox/L1-routing.md 2026-09-26)
- R-spawn-11: Read your inbox right before every launch, not only while waiting. (why: 3 workers started against a 30-min-old stop order; source: inbox/L1-routing.md 21:44Z)
- R-spawn-12: Omit `--lean` for `--client qoder|agy`; the spawner refuses it with exit 2. (why: those clients start their MCP servers anyway; source: work/L1-routing/review-a3.out, 2026-09-26)
- R-spawn-13: Relaunch, never resume, a worktree subagent that stopped without changes. (why: its worktree is deleted; resumed, it works in yours; source: inbox/L1-routing.md, 2026-09-26 08:5xZ)

### review

- R-review-01: Gate a worker's sandbox diff, not its report; a NO-OP (nothing changed) exits 5. (why: workers reported completed with uncommitted files; source: work/L1-routing/C1.out, NoOpGuardTests)
- R-review-02: Treat a cheap/free reviewer's "no findings" as unproven; self-review often finds real defects. (why: measured across five lanes' DONE notes; source: 20260924/done, 20260925/done)
- R-review-03: Never let a reviewer share the writer's model family; pair across families. (why: same-family reviewers repeat the writer's blind spots; source: cao/dispatch.py, review_degraded tag)
- R-review-04: Inline run-log files into a qoder/agy task; under --isolate it cannot read outside its clone. (why: qoder asked for access, then NO-OP; source: work/L1-routing/review-c2.out, 2026-09-26)
- R-review-05: Start every review run with `--card role=review`, or a clean sandbox exits 5 (NO-OP). (why: a full 8-finding review exited 5; source: work/L1-routing/review-a3.out, 2026-09-26)

### tests

- R-tests-01: Run `-Validate` then `-DryRun` before any unattended run. (why: a mis-named lane once failed hours into a night; source: tests/Runner.Smoke.Tests.ps1)
- R-tests-02: Never run a full suite in the main checkout while lanes merge; use a guardsOnly lane. (why: a moving tree makes reds that are not real; source: measured 2026-09-04: 18 red, 9 artefacts)
- R-tests-03: A green exit code is not proof tests ran; check the reported test count too. (why: a script with no unittest.main() exits 0, runs none; source: work/L1-routing/B3a.out)
- R-tests-04: Filter tests to the touched area while working; run both full suites once per phase. (why: keeps iteration fast without skipping the gate; source: 2026-09-25 plan, lane working rules)

### gateway

- R-gateway-01: Route to free pools first, and to qoder/agy before paid DeepSeek. (why: every OpenRouter paid leg is down; source: operator 2026-09-25 20:07Z)
- R-gateway-02: Give a reasoning-model reviewer a real output budget (`max_tokens` ~48000, low effort). (why: 16k tokens is eaten by reasoning first; source: L1-HANDOFF.md, Known traps)
- R-gateway-03: Route opencode's free zen/spark legs through an opencode-launched agent, not a bare API call. (why: that leg 403s any non-opencode caller; source: done/R-merge.md, item 5)
- R-gateway-04: After a combo/id rename, confirm the live gateway's combos match the code before routing. (why: a stale gateway 400s every card/--tier route; source: done/R-merge.md, item 1)
- R-gateway-05: Match a tool-allowlist entry to its MCP wiring: `mcp__<name>__*`, plugin form otherwise. (why: the wrong prefix leaves the tool silently missing; source: mcp-servers-setup skill)
- R-gateway-06: Keep a Qwen (t3-driver-free-only) request under 7000 input tokens; send one file. (why: larger requests fail 413 at its ITPM limit; source: work/L1-routing/review-b4b5.out)
- R-gateway-07: Each heartbeat, give a subagent every probe-proposals.jsonl line newer than its leg's probe. (why: a run contradicted the record; source: test_autoos_spawner.py ProbeProposalTests)
- R-gateway-08: Have a subagent re-run probe-toolcalls.py once measured.json results are 7+ days old. (why: operator 2026-09-26: keep setups current; source: tests/test_probe_toolcalls.py)

### brief

- R-brief-01: Brief a free/cheap worker with one file and an exact spec, not several files at once. (why: given several files they fabricate completion; source: L1-HANDOFF.md, Known traps)
- R-brief-02: Write briefs/reports in the fixed BRIEF/REPORT fields, no prose; put bulky output in a file. (why: fixed fields parse and stay short; source: spec 2026-09-25 section 8.2, D9)

### git

- R-git-01: Expect a commit's attribution trailer from the harness that ran it, not the brief's line. (why: the harness's own trailer always wins; source: done/R-merge.md, S-docker-stack.md)
- R-git-02: Commit lane work under its own git identity (its own user.name/user.email). (why: keeps lane authorship distinct from the operator; source: 2026-09-25 plan, lane working rules)

### merge

- R-merge-01: Leave guards opt-in but let them gate the merge; declare one for shared state. (why: with none declared, a clean stop alone merges; source: HandoffCore.Tests.ps1)
- R-merge-02: Mark a branch already an ancestor of the base merged-elsewhere; skip it, never re-run. (why: two runners must not redo landed work; source: HandoffCore.Tests.ps1)
- R-merge-03: Merge with no fast-forward, under a global mutex, so lanes can't interleave a merge. (why: guard-gated serial merges need no hand reconciling; source: PROPOSALS-2026-09-05.md, what worked)
- R-merge-04: Merge in two stages: a lane into its orchestrator's branch, then that branch into main. (why: keeps a half-finished orchestrator run off main; source: briefs/common.md, Merge path)

### handoff

- R-handoff-01: Cap an orchestrator's context per spec section 8.3; rewrite its state file every wave. (why: makes a handoff possible at any moment; source: default, spec D16 -- unmeasured)
- R-handoff-02: Delegate reading DONE notes, plans and drafts to a subagent; don't read them all yourself. (why: a 200k orchestrator hit its 150k cap in 7 min; source: inbox/L1-main.md 19:21Z)
- R-handoff-03: Write a successor's brief from the predecessor's DONE note, never from the plan alone. (why: eight re-cuts converged on the same shape; source: references/layers.md history, 2026-09-05)
- R-handoff-04: At the cap, run l1_handoff.py --state/--out, append `handoff <name>` to the inbox, stop. (why: lets the parent relaunch you from that file; source: briefs/common.md, Always)
- R-handoff-05: A handoff is done only once the parent inbox has its line; parents watch handoff mtimes. (why: a handoff with no line sat idle 1.5 h; source: inbox/L1-routing.md 21:45Z)
- R-handoff-06: Only the coordinator asks the operator; others append `question: … | options: …` or `answered: …` to its inbox. (why: the operator was asked twice; source: operator 2026-09-26)

### host

- R-host-01: Budget a private Serena at approximately 160-200 MB and about 2s to start. (why: sizes how many can run on one host; source: work/L1-routing/Z3.md)
- R-host-02: Give every session its own Omnigraph stdio bridge rather than sharing one. (why: measured ~7 MB each, 22 MB for three; source: inbox/L1-main.md 20:34Z)
- R-host-03: Read a "shellcheck is clean" failure at exit 137 as host OOM, not a lint finding. (why: reproduced on a loaded host across five lanes; source: 20260924-25 DONE notes, five lanes)
- R-host-04: Keep the machine awake yourself before an overnight run. (why: the runner cannot change power settings; source: SKILL.md history, rule 6)

### safety

- R-safety-01: Never put a secret in a committed handoff config; read tokens at preflight only. (why: a committed config is read by every clone; source: HandoffCore.psm1, AGENTS.md rule 1)
- R-safety-02: Treat a classifier refusal as a signal: record it verbatim and stop, never work around it. (why: a background session can't negotiate a denial; source: refusals measured 2026-09-24/25)
- R-safety-03: A leaf role never spawns; only a spawning role lists the autoos-agent MCP. (why: a supervisor wanting to write code mis-decomposed; source: tests/test_agent_harness.py)

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
