# Changelog

All notable changes to this repository are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed - `unattended-orchestration`: HandoffCore.psm1 restored; -Status recounts refusals (2026-09-17, evening)

- `3df910e` committed an older copy of `HandoffCore.psm1` (188 lines fewer): it dropped
  `Resolve-HandoffSessionLauncher`, `Test-HandoffStalled` and `Test-HandoffStallBudgetExceeded`,
  which the driver calls on every lane start, and the section-only refusal counter. Every lane
  launched against it crashed at once ("The term 'Resolve-HandoffSessionLauncher' is not
  recognized"), and 15 of the 91 core assertions were red at HEAD. The module is restored to
  its previous version with that commit's one addition, the session-limit banner parser in
  `Classify-HandoffFailure`, re-applied on top. 91 assertions pass again.
- `-Status` recounts refusals from each session's DONE note (in the worktree while it runs; on
  the base branch in the main checkout after cleanup) instead of printing the number the state
  file stored when the merge happened; a value it could not recount carries a trailing `?`, and
  `last_error` is hidden once a session has settled. Before: A3 5, D1 23, L2 34 beside notes
  saying 2, 0, 0. After: 2, 0, 0.

### Fixed - `unattended-orchestration`: -Cleanup unlinks a worktree's junctions before removing it (2026-09-17)

- `git worktree remove --force` on Windows leaves a junction's reparse point, and
  therefore the worktree folder, behind. After one -Cleanup, five empty folders
  each still held a live `data` or `others` junction into the main checkout - the
  session's link into the family's real data, sitting in a folder anyone might
  `rm -rf`. `Remove-Worktree` now unlinks every junction (depth 1) with a
  reparse-point-only `rmdir` first, then removes the worktree, then removes the
  folder only if it is empty. The junction's target is never touched; the log
  names each unlink.

### Changed — homelab: per-host prohibitions are enforced, root is not an agent capability (2026-09-17)

An audit of every host found the single agent key authorised for three accounts on each VM —
one of them `NOPASSWD: ALL` — and for **root on the Proxmox/NFS host and the OPNsense firewall**.
"Scoped" was decorative. The repair is a host-side account model (Server repo,
`setup-agent-accounts.yml`); this repo's part is making the MCP server able to express it.

- **`hosts[].forbid`** — intents and command patterns a host refuses outright. Unlike
  `exclusions` (a warning) it is **enforced** by `homelab_plan` and `homelab_run`. This is the
  right shape for "the storage host is never updated": a global `deny_pattern` for `apt` would
  have blocked updates everywhere. `homelab_boundaries` now lists prohibitions per host.
- **Bare-word patterns match tokens, not substrings.** `"apt"` no longer refuses
  `grep capture`, `"pve"` no longer refuses `pvesm status`. Multi-token patterns keep substring
  semantics on purpose (`rm -rf /` must still trip on `rm -rf /home`). Found by probing the live
  config, which had exactly those false positives.
- **`homelab-access` skill** gains *Root is not an agent capability*: root-needing work is
  automation's (own identity) or a human's, declared under `unreachable`.
- **Plan review** appended to `docs/superpowers/plans/2026-09-09-homelab-mcp-server.md`:
  Section 3 is replaced (it re-granted root through `docker_socket` and `docker-compose *`,
  relied on sudoers `!` negations, and missed that Semaphore and the offsite dedup depended on
  the accounts it deleted); Sections 1–2 adopted with the reason corrected.
- The site-fact guard test caught a comment naming an example host while this was written —
  it derives its banned list from the config, so it works for any site, and it worked here.

29 tests pass. Site config: `storage` (the Proxmox host; OMV is gone) forbids updates,
restarts and `/proc` inspection by agents; the OMV "safe-subset apt" rule is deleted, not
softened.

### Fixed - `unattended-orchestration`: the refusal counter reads only the note's Refused section (2026-09-17)

- `Get-HandoffRefusalCount` counted every line of a DONE note containing "refused"
  or "refusal". In a repository whose house rule is "refuse rather than estimate"
  a clean session's note carried 47 such lines about the *application* refusing
  input, and `-Status` showed it as the worst session of the run (34) while its
  Refused section said nothing was refused. The counter now reads the `## Refused`
  section alone when the note has one (a `###` subheading does not end it), treats
  "not a refusal" / "neither a refusal" as disclaimers, and adds the number from a
  counted sentence ("Two commands were refused" is two). Verified against nine real
  notes from the second-analysis-repo run: A3 = 2, every other session 0. The second-analysis-repo
  report collector applies the same rule (`count_classifier_refusals`).
- Observation from the same run, for the next design pass: a host stop (not a
  usage limit, not a crash) ended a session at 84 % of its own full suite with its
  DONE note written but uncommitted. The recovery that worked: commit the note
  unchanged with a dated controller note where the suite result would have been,
  drop the operator's stop marker so the runner does not resume a finished
  session, relaunch one runner for the remaining lanes; the pre-start check that
  honours a DONE note under a stop marker (36d4b5d) is what made this a
  guards-only pass instead of a resumed session.

### Added - `unattended-orchestration`: a second, gated, multi-provider lane (2026-09-10)

The CAO lane, in Python beside the PowerShell batch runner. `python -m cao` gains
`check`, `probe`, `plan`, `launch`, `verify`, `profiles`, `sweep` and `resume`.
493 tests: 487 pass in WSL, 482 on Windows -- the difference is shell tests that
skip where `bash` cannot reach the filesystem under test.
Decisions and their costs: [ADR 0008](docs/decisions/0008-cao-orchestration-lane.md).
Measurements: `docs/superpowers/plans/2026-09-10-cao-orchestration-lane/EVIDENCE.md`.

- **The planning gate is a file, not an instruction.** `cao launch` refuses
  without a `PLAN.lock.json` whose schema cannot express "I did not ask" — it
  needs a question the *user* answered, or a documented assumption. Unfilled
  `<placeholders>` are rejected, because the first scaffold validated unedited.
- **Depth is configurable and generated.** `cao.maxDepth` regenerates one profile
  per level; verified at 2/3/4/5.
- **Three model families**, with quota fallback per *pool* and review pairing per
  *family* — `claude-opus-4-6` via `agy` is extra capacity and an invalid reviewer
  for Claude code. Never decline a pool: it shrinks the budget without improving
  the model.
- **Orchestrators are never cheap.** L1 Fable 5.1 / Opus 5, spawners Opus, leaves
  anything. Enforced, and it caught our own config on the first run.
- **ext4 worktrees**: `git status` 3.021 s -> 0.146 s on a Windows host, provisioned
  and trusted by the launch path itself.
- **Safety, as mechanism rather than instruction.** An agent gets a disposable
  worktree and `plan_launch` refuses to hand it the main checkout. `cao verify`
  runs the phase's own guard and is the ONLY thing that releases a lease, so a
  claim never closes a phase; `--rework` respawns with the guard's output
  verbatim, capped by `resume.maxAttempts` before escalating with the lease held.
  `verify --terminal <id>` goes one step further and compares what the agent
  *claimed* with what was measured: a failure after `status=blocked` is ordinary,
  a failure after `status=ok` means that agent's reports are unreliable on every
  phase it touched. A guard that could not run never accuses anyone.
- **Proven end to end, live.** One phase through the real lane on 2026-09-11
  found six defects, five of which no unit test could reach: CAO reports
  `completed` for a killed agent; the contradiction check fed a strict parser a
  19,930-character ANSI pane and could never have fired; stripping that ANSI
  welded words together; inspecting a phase spent its rework budget; every
  rework collided with the failed attempt's session name and so could never
  launch; and `probe` knew a pool was dead while routing kept dispatching into
  it. All six are fixed and pinned by tests. EVIDENCE §13.
- **Preflight is honest about readiness.** `cao check` used to print three
  available pools and exit 0 with no CAO server running; it now probes the
  server and exits 1 with the command that starts it. `cao sweep` reports
  worktrees whose session is gone — with their dirty state, and never removing
  one. Every `python -m cao …` string in the lane's code and docs is parsed by a
  test, after the refusal message an operator sees when stuck was found naming
  a flag that does not exist.
- **A quota wall has three meanings.** `rate_limit_error` cools the pool for an
  hour; `billing_error` / `credit balance too low` closes it outright (waiting
  cannot help); `overloaded_error` / 529 changes nothing, because closing a pool
  over seconds of congestion declines capacity that was never gone. The wordings
  are counted in the shipped binaries, not guessed.
- Profiles and sessions are **project-scoped** (`cao_<project>_L2_supervisor`).
  CAO's profile store is global, so unscoped names from one repo silently
  overwrote another's.

Nine defects in the previous integration were measured and fixed, every one of them
silent — profiles that declared a `provider` CAO does not recognise (falling back to
an uninstalled `kiro_cli`), a profile store nothing reads, `CAO_HOME_DIR` unset so
the CLI and server used different homes, every agent pinned to the wrong Omnigraph
graph, and tool restrictions written in provider-native names that CAO maps to
nothing, thereby blocking everything.

Two conclusions of our own were wrong and are corrected in place: tool restrictions
*are* enforceable (through CAO's universal vocabulary), and an agent's evidence is a
claim rather than a measurement — a worker reported an `od -c` dump that did not
match the file it had just correctly written, and its supervisor relayed it.

**Known limits, deliberately not papered over.** Quota-detection patterns are
taken from the strings the vendors ship rather than from a limit observed live
(counted with `strings` in Claude Code 2.1.236 and the native `agy` binary), so
`budget.check` still fails *open*. Depth is policed reactively between sweeps,
not prevented.

*Corrected 2026-09-11:* this entry previously said `agy` was unusable inside CAO on
a Windows host. The diagnosis was wrong. The WSL `agy` on PATH was a shim that
`execv`s the Windows binary, and a Windows process cannot run with a Linux-only
cwd — measured, the same shim was signed in under `/mnt/c` and "not signed in"
under ext4, which is where worktrees live. Installing the native Linux binary
fixes it: `agy` 1.2.1 authenticates and runs in an ext4 worktree. `setup_cao.py`
now detects the flavour and says which one is on PATH.


### Changed - `unattended-orchestration`: what a three-day run added (2026-09-08)

The research-repo run finished: six sessions, four lanes, one usage-limit resume, one host shutdown
mid-session, a store failure under three concurrent writers, and a guards-only final lane green
on a fresh worktree at the merged head. Three more pieces landed from it, each tested:

- **`{{inbox}}`** - the controller's only channel INTO a running background session is a file the
  brief tells the session to read at every decision point (a session polled a silent three-hour
  compute run every 25 minutes while the controller held the diagnosis).
- **Self-explanatory resumes** - `Build-HandoffResumePrompt` names the reason (limit, transient,
  session-hours cap, host restart, early stop), the time, and the fact a resumed session cannot see:
  its subagents died with the previous process.
- **`MCP_TIMEOUT` in the worktree's `settings.local.json`** - Serena failed to connect within 30 s
  in two of four parallel launches; the checklist names the fix.

Also recorded in `structured-memory/references/operations.md`: the MinIO bloom-cycle corruption
and Lance recovery sidecar that three concurrent writers produced, with the measured recovery.

### Added — `homelab` MCP server; `homelab-access` is now config-driven and site-neutral (2026-09-06)

`homelab-access` was a 153-line site log: 45 of its 121 non-blank lines named a
specific host, address, user or product. It could not be shared, and on a public
repository it was a disclosure. It was also only a *document* — it described the
right account to use and hoped the agent read it.

Both halves are fixed, and they are separable:

**The skill is now site-neutral** (102 lines, **zero** hostnames/users/addresses/
products). It teaches the reasoning — accounts are a capability matrix, not a
hierarchy; these errors mean something other than they say; check what is
deliberately unreachable before acting wide — and defers every fact to config.

**The reasoning is now executable.** `infra/mcp-servers/servers/homelab-mcp/` is
an MCP server fitted to a homelab *by config only*:

- `homelab_plan` picks the **least-capable role** that satisfies an intent, and
  warns on host status, fleet exclusions, quirks, and recorded roles.
- `homelab_diagnose` decodes an error (scoped-sudo refusal that is not a TTY
  problem; a successful login whose shell could not parse the command).
- `homelab_run` refuses configured destructive patterns before anything is sent,
  wraps for non-POSIX login shells, requires a reason for recorded roles.
- `homelab_verify`, `homelab_hosts`, `homelab_roles`, `homelab_boundaries`.

**The generalisation is enforced, not asserted.** `test_no_site_facts_in_code`
fails if a hostname, username, product or private IP ever appears in the source.
23 tests pass. The site config lives at `~/.config/homelab/homelab.yaml`,
**outside the repo**, with only `homelab.example.yaml` tracked and a `.gitignore`
rule as defence in depth.

Verified live: 15/15 host+role combinations authenticate through the server, and
`claude mcp list` reports `homelab ✔ Connected`.

Two defects were found by testing rather than reading. A host with no declared
roles bypassed the capability check entirely, so an intent was silently ignored
on the one host with no fallback account — hosts can now declare
`login_capabilities`. And the alias template assumed every host follows it; a
live probe failed on two hosts until an explicit `alias:` override and a
`login_user@address` fallback were added.

`mcp<2` is pinned, for the reason recorded in `servers/graphify-mcp/Dockerfile`.

### Changed - `unattended-orchestration` adopted into a second repository, and what that taught it (2026-09-05)

Adopting the runner into `research-repo` for a six-session, four-lane build found four defects and
nine missing pieces in one morning. Everything below is tested (HandoffCore 69, Runner.Smoke 11,
Portability 10 assertions) and the PROPOSALS file from the `sibling-analysis-repo` orchestrator sits
beside the skill with a status table.

| Finding (measured) | Change |
|---|---|
| Three lanes crashed in their first second: `([string](git rev-parse ...)).Trim()` is a null method call when git prints nothing | every native output goes through `Get-HandoffText` |
| Three lanes blocked in three seconds on "New MCP server found in this project": the `~/.claude.json` entry does not suppress it | `trust_worktree.py` ships with the skill and writes the worktree's `.claude/settings.local.json` with `enableAllProjectMcpServers`; the dialog is now a recognised prompt |
| A branch that merely fell behind the base is an ancestor too | merged-elsewhere uses the base's first-parent line |
| `briefTemplateFile` alone was ignored; sessions launched with bare bodies | either template form wraps the brief |
| A worktree holds tracked files only: the main venv's editable install imports the **main** checkout's package from inside a worktree | the adoption checklist in SKILL.md; `copyDirs`; per-worktree venv and graph in `postWorktree` |
| One runner invocation could not express "B, T11, T6 in parallel, then C, then D" | `dependsOn` per session (waits for merges, fails on a failed dependency) |
| Ordering is not exclusion; a reader starved a writer | `resources` (`name[:read\|write]`) with conflict detection |
| A finished session reported `working` for hours and was stopped-and-resumed | DONE-note finish rule (`quietMinutes`), `stopAfterMerge`, `merged_into` |
| No sanctioned way to add or stop a lane while running | `<stateDir>/queue/` (`lane-*.json`, `stop-<key>`) |
| The final suite ran in the moving main checkout and reported artefact reds | `guardsOnly` sessions; rule 7 |
| Nobody counted the whole machine | advisory `machineBudget` + `<stateDir>/load.json`; `{{traps}}` in every brief; `controllerBriefFile`; refusal counts; `-Cleanup` |

### Changed — markdown compressed where it was prose-heavy, not where it was dense (2026-09-04)

Audited all 67 tracked `.md` files by length, prose/code ratio and per-section size, then
compressed only where prose was doing the padding:

| File | Before | After | Note |
|---|---|---|---|
| `skills/mcp-servers-setup/SKILL.md` | 641 | 575 | its Serena section alone was 253 lines — 40% of the file, 4x the next |
| `CHANGELOG.md` (2026-09-04 entries) | 101 | 71 | three entries merged into two |
| `skills/unattended-orchestration/SKILL.md` | 267 | 249 | the adoption path and §6 both listed the same flags |

Every measurement survives — 237 MB shadow home, 5 s -> 16 s startup, 34.4 s, the
`serena_config.py` line numbers, the junction canary. Narrative framing went; facts did not.

**Deliberately not compressed:** `docs/superpowers/plans/2026-07-30-…` is the longest file at 1977
lines but is **71% executable code** for work ADR 0004 records as *specified but not yet built*.
It is a live task-by-task plan whose code blocks are the deliverable; shortening it would remove
the thing an agent executes. Length is not the metric — prose-per-fact is.

Also corrected a claim the audit exposed: the Serena section stated its memory tools are disabled,
but `.serena/project.yml` carries `excluded_tools: []`, so they are not. The text now records the
gap and why closing it is not a one-line edit — an invalid entry there can stop Serena starting,
which takes down **all** symbolic tools.

### Added — `unattended-orchestration`, and then made portable (2026-09-04)

Adopted `sibling-analysis-repo/scripts/management/run_handoff_sessions.ps1` as
[`skills/unattended-orchestration/`](skills/unattended-orchestration/SKILL.md): headless agent
sessions, a git worktree each, parallel lanes, recovery across usage limits and API outages,
guard-tested and auto-merged. The orchestration idea was cheap; the several nights of measured
failures encoded in it were not — a variadic `--allowedTools` swallowing the prompt so the session
starts with *no task*; an unapproved fresh worktree a background session can never trust its way
out of; an expired OAuth session that must stop the run rather than be retried 40 times; a
`$script:` assignment inside a mutex scriptblock that reported every successful merge as a
conflict. All of it was hardcoded to one repo's session table, venv, pytest ids and branch.

Split so that knowledge is testable rather than trapped: `HandoffCore.psm1` holds pure functions
(failure classification, config validation, template expansion, state I/O, brief assembly);
`run_handoff_sessions.ps1` keeps only what touches git, the agent or the clock. The recovery rules
were previously reachable only by hitting a usage limit at 03:00; they are assertions now.

Then made genuinely adoptable — it had been general in shape but not in fact, demanding an
absolute `repo` path, hardcoding `claude` at a dozen call sites, and using two Windows-only
primitives:

- **Repo detected** (`git rev-parse --show-toplevel`), not configured, so a committed config
  carries no machine-specific path. Found from a subdirectory too. `repo` survives as an override.
- **`-Init`** scaffolds a runnable config, detecting the *actual* base branch, not assuming `main`.
- **`launcher` adapter** replaces the hardcoded binary. Defaults describe Claude Code so an
  unedited config runs; another agent overrides only the keys it needs, merged key-by-key so
  replacing `command` cannot silently lose `list`/`logs`/`stop`. The variadic-flag gotcha is
  encoded as `{{tools}}`/`{{prompt}}` ordering in the default template, and asserted.
- **Cross-platform**: junction/symlink, named-mutex/lock-file. A named `System.Threading.Mutex`
  throws `PlatformNotSupportedException` off Windows and would have killed the first state write.
- **`-EmitBriefs`** renders briefs as copy-pasteable markdown, launching nothing; same
  `Build-HandoffBrief` as a real launch, so pasted text cannot drift from what would have run.
- **`subagents`** → `{{subagentPolicy}}` in every brief: cap, model tier per work shape, leaf rule.

Four bugs surfaced by *running* it, all now covered. Three were PowerShell traps: the output
stream unrolls one array level, so `[["E","A"]]` collapsed to `["E","A"]` and every **sequential**
lane silently ran in **parallel** — the precise contention lanes exist to prevent (twice: via
`ForEach-Object` over nested arrays, and via an `if`/`else` *expression* assigned to a variable);
and variable names are case-insensitive, so a local `$followUp` *is* the `[string]$FollowUp`
parameter, making a bool assignment become `"False"` and fail to bind.

72 assertions across three suites. `tests/Portability.Tests.ps1` is the adoption claim's proof: it
copies the skill into a throwaway repo on a branch named `trunk`, drives `-Init` → `-Validate` →
`-EmitBriefs` → `-DryRun` unedited, and asserts nothing reaches back into this repository.
Rationale: [ADR 0006](docs/decisions/0006-unattended-session-orchestration.md).

### Fixed — MCP connect timeouts were spawn concurrency, not CPU; `npx` was the cost (2026-09-04)

`context7` began failing `CONNECT_TIMEOUT after 30000ms` on a machine at 99% CPU. The CPU was a
red herring and measurement said so: saturating 24 of 32 cores moved the spawn 5.0 s → 7.1 s
(**+2.1 s**); going from 1 to 20 concurrent spawns moved it 5.0 s → **34.4 s** (+29.4 s) —
~14× more damaging. RAM (68 GB free), disk (99.75% idle, queue 0) and network (registry
320–512 ms) were ruled out by measurement.

Cause: **every open session holds its own resident copy of every stdio MCP server**, each a 3–4
process chain; sessions open ~23 h had accumulated 784 processes and 333k handles. `context7`
failed first as the only server paying a full `npx -y` registry resolve per spawn.

| Server | before (`npx`) | after (`node` global) |
|---|---|---|
| `context7` | 3704 ms solo · 11.9 s @20 concurrent | **1316 ms** · **3.7 s** |
| `playwright` | 3521 ms | **392 ms** (9×) |

Playwright also moved off the `claude-plugins-official` plugin (its `.mcp.json` runs
`npx @playwright/mcp@latest` and is overwritten on every plugin update) to one user-scope entry.
**Its tool prefix therefore changed** from `mcp__plugin_playwright_playwright__*` to
`mcp__playwright__*`; anything pre-allowing tools by name must follow. Docs corrected in
[`mcp-servers-setup`](skills/mcp-servers-setup/SKILL.md) and
[`repository-index`](skills/repository-index/SKILL.md), which had described a Docker wiring no
host was running. Also set `main`'s missing upstream (`git branch -u origin/main main`).

### Fixed — worktree agents keep Serena and Graphify; the rule that took them away was wrong (2026-09-01)

An earlier entry today concluded that subagents in worktrees must fall back to `grep`. That
traded symbolic navigation — the whole point of the stack — for a problem that is solvable. The
constraint behind it is real (one Serena process per *session*, one active project, switching
tears down the outgoing project's language servers) but the conclusion did not follow from it.

**The unit of worktree parallelism is a SESSION, not a subagent.** One `claude` process per
worktree gives each its own Serena process, its own `_active_project` and — graphify being
cwd-relative — its own graph. Nothing shared, nothing torn down, full capability everywhere.
In-session subagents stay in their own session's worktree and share its activated Serena.
*Worktrees get sessions; subagents share the session's worktree.* The `grep` fallback survives
only for the case where a worktree genuinely cannot have its own session.

Three artifacts have to reach the worktree for that to hold, and all three failed the same way —
untracked, therefore never checked out:

- **`.gitignore` bug, and the recurrence generator for this repo.** Line 50 was a bare
  `.serena/`, so `.serena/project.yml` was never tracked and **every worktree made Serena
  autogenerate one** — and autogeneration picks languages by file count, reintroducing
  `json`/`markdown`/`yaml` every single time. Now `.serena/*` plus `!.serena/project.yml`; a
  negation cannot re-include a file inside an already-excluded directory, which is why the bare
  form silently defeated the internal `.serena/.gitignore` that only ever excluded `cache` and
  `project.local.yml`.
- **This repo's own list was polluted too** — `json, markdown, python, typescript, yaml, bash,
  powershell, html`. Trimmed to `python, typescript, bash, powershell` (`.py` 42, `.ts` 24,
  `.sh` 10, `.ps1` 8), `project_name` removed, and the file is now tracked so worktrees inherit
  it. The re-save mechanism that keeps undoing such trims is documented inline.
- **`graphify-out/` is gitignored and both hooks deliberately skip linked worktrees**, so a
  worktree never has a graph. Link it to the main checkout rather than building a second copy
  nothing maintains — same pattern as `.env` and `launch.json`: untracked operator artifacts
  resolve to the main checkout. **Verified it cannot eat the real graph**: with a canary in the
  target, `Remove-Item -Recurse -Force <worktree>` left it intact — the junction is not
  followed. Not tested against `git worktree remove` itself, so drop the link first if you want
  belt and braces.

Also carried the `Edit` correction into the fallback wording: a subagent barred from Serena *by
rule* that also reads `Edit` as off-limits has no way to change a file at all, and in a repo
that fences shell edits to source that is a hard stop rather than a slowdown.

### Added — graphify's approval model and its worktree behaviour, both previously undocumented (2026-09-01)

Prompted by two questions the guide could not answer: why `enabledMcpjsonServers` never lists
graphify, and whether graphify is safe across worktrees.

- **`enabledMcpjsonServers` never lists graphify by design, not omission.** That array approves
  *project-scoped* servers from a repo's `.mcp.json`; user-scope servers need no approval
  because you configured them yourself. Graphify is user-scope only, so it is in no `.mcp.json`
  and there is nothing to approve — which is why `check-graphify-scope.*` treats a graphify
  entry in a repo's `settings.local.json` as a **defect** and removes it under `--fix`, checked
  independently of the server entry because an approval outlives what it approved.
- **Worktrees: graphify is a main-checkout tool and fails quietly outside one.**
  `graphify-out/` is gitignored, so — the same shape as `.env` in the Serena worktree rules — a
  linked worktree never has one. Launched *in* a worktree, the cwd-relative stdio server looks
  for a `graphify-out/graph.json` that has never existed there. Launched in the main checkout
  while an agent works in a worktree, it inherits its launch directory once and keeps answering
  from the main checkout's graph — a different branch's code, answered confidently. Neither
  case errors.
- **But there is no rebuild spillover**, which was the other half of the question. Both
  installed hooks refuse to run in a linked worktree: `post-commit` and `post-checkout` each
  compare `git rev-parse --git-dir` with `--git-common-dir` and `exit 0` when they differ, so N
  worktrees never produce N rebuilds or N competing graphs; `post-checkout` also exits when
  `graphify-out/` is absent, which in a worktree it always is. Read from the installed hooks.
- Rule recorded to match Serena's: **in a worktree, treat graphify as unavailable and say so.**
  Explicitly do *not* hand-build a graph there — the hooks will not maintain it, so it goes
  stale silently.

### Fixed — the last generally-phrased restatement of the omnigraph precedence trap (2026-09-01)

`skills/structured-memory/references/operations.md` stated it of "a same-named server". Scoped
to `omnigraph` with a pointer to the serena counter-measurement. Audited the other four
restatements while there: `README.md`, `.mcp.json` and `AGENTS.md` already name omnigraph or
sit inside omnigraph-only passages, and `SYNC-MANUAL.md` was already explicit — so this was the
only one left that read as a general law.

### Changed — Serena guide re-measured against 1.7.0; three claims were wrong (2026-09-01)

Measured on serena-agent 1.7.0, Windows 11, in `second-analysis-repo`. Three of these **correct**
what the guide previously said rather than adding to it.

- **NEW — an MCP `env` value does not tilde-expand.** Claude Code expands `${VAR}` in MCP env
  blocks but **not** a leading `~`, so `"SERENA_HOME": "~/.serena"` becomes a *relative
  directory name* and Serena builds a whole second home at `./~/.serena/` in the working tree:
  its own `serena_config.yml` (global `excluded_tools` never applies, so
  `execute_shell_command` / `read_file` / `list_dir` / `search_for_pattern` come back), its own
  `logs/` where the real diagnostics went, and 237 MB of re-downloaded language servers beside
  the 1,023 MB real home. No error at any point; it hid from `git status` only because that
  repo's `.gitignore` carried `~*`. **Rule: every path in an MCP `env` block is absolute, or
  omitted.**
- **NEW — Serena is graphify-shaped, not omnigraph-shaped: user scope only.** It is selected by
  path at call time and its per-repo config is the *tracked* `.serena/project.yml`, so a
  project-scope entry pins nothing and only adds a second definition. Stated in the same terms
  as the existing graphify rule.
- **CORRECTION — "user scope silently wins" is not general.** `CLAUDE.md` stated it of any
  same-named server; it was only ever measured for omnigraph. For serena a duplicate produced
  **no winner**: both ran as five processes (one `--context claude-code`, four without), and
  the session's calls landed on a *project-scope* one reporting `Active context: desktop-app`
  and a one-project roster against eleven in the real home. The claim is now scoped to
  omnigraph with the serena counter-measurement recorded, because stating it generally is
  exactly what made a duplicate serena entry look harmless.
- **REVISION — "proven working" was the wrong bar for the language list.** All of `bash, json,
  python, rust, toml, yaml` start; that was never the question. Adding one set at a time,
  `python`+`rust` gives a working `find_referencing_symbols`, and each of `json` / `toml` /
  `yaml` breaks it by flooding the symbol namespace with config keys (`$comment`,
  `build-system`, `name`×13) that no data-format server backs with
  `textDocument/references`. Startup went 5 s → 16 s with all six. **New rule: only programming
  languages the repo actually contains** — file count says a language is worth *testing*, never
  that it belongs. Serena's own `ls_priorities` comment agrees.
- **NEW — `serena project health-check` is the verification, and nothing else is.**
  `activate_project` returns success and lists every declared language as active *even when
  seven are dead*; the first symbol call is where you learn, via a message naming neither
  language nor cause. Require both `Health check completed successfully` and
  `FindReferencingSymbolsTool found N references`. The Windows `UnicodeEncodeError` on the
  final check-mark is cosmetic — exit code is still 0.
- **ADDED to *Worktrees* — omitting `project_name` prevents the collision structurally.**
  `serena_config.py:516-517` fills a missing key from the folder name at load, and the only
  write-back path (`:907-912`) touches a legacy out-of-project file, never
  `.serena/project.yml` — so each worktree self-names by its directory. Carried with its
  caveat: read from source, **not** yet tested live, so it is an addition to the absolute-path
  rule and not a replacement. Also records the real price of parallel work: **N sessions = N
  Serena processes**, each holding its own language servers, doubled by a duplicate definition.
- **`starters/mcp-servers/.serena/project.yml`** — `languages` cut from
  `["python","bash","yaml","json"]` to `["python"]`, with the health-check recipe inline; and
  the absence of `project_name` is now documented as deliberate so nobody adds one.

- **Corrected same day, from a run against `sibling-analysis-repo`:** the `UnicodeEncodeError` note
  said "exit code is still 0" — measured **1** (serena 1.7.0, cp1252). The traceback does
  propagate, so the log body is the verdict and `$?` is not. Added the criterion that run
  exposed: **check which symbol the check selected.** With 8 languages incl. `markdown` it
  logged `No class or function found, using first available symbol`, picked the heading
  `AGENTS.md — sibling-analysis-repo` (kind Namespace), and then satisfied *both* documented
  criteria — `found 1 references`, `completed successfully` — without touching Python once.
  Both lines green is necessary, not sufficient; a pass on a heading proves nothing about the
  call graph. Startup there was 34.4 s.

Unchanged deliberately: the warning that a failed `initialize` kills the language server
*manager* rather than one language (re-confirmed), and the Serena-memory-tools-disabled
guidance.

### Documented — how to extract repository / project names, and what gates it (2026-08-31)

The names now live only behind the token — `cluster.yaml` is gitignored and purged — so the
instruction files have to say how to ask for them, and be honest about the boundary.

- **The recipe, in the wiring skill** (`skills/mcp-servers-setup/SKILL.md` → *Discovering
  repository / project names*): graph id == repository folder name == `Project.slug` by
  construction, so `graphs_list` (or `GET /graphs`) is the roster, and `whoami` returns this
  repo's own `slug` / `name` / `repository`. Both MCP and `curl` forms, because Antigravity's
  MCP config is global and cannot be relied on per-repo.
- **The gate, measured rather than asserted** (2026-08-31, live server): no `Authorization`
  header → **401**; wrong token → **401**; valid `OMNIGRAPH_TOKEN` → 200; `/healthz` → 200 but
  liveness only, no names and no data. `/graphs` is closed by default and answers only because
  `cluster.policy.yaml` grants `graph_list` to the `operators` group at cluster scope — without
  it an authenticated call gets **403 ForbiddenError**, so "not allowed" can never be misread
  as "nothing here".
- **The scope, stated plainly.** One token resolves to the single actor `default`; the
  per-graph bundles grant that actor its graphs, so a holder sees exactly the graphs that are
  theirs — today all of them, because there is one user. It is **all-or-nothing**: there is no
  token that reveals part of the roster until the per-user bundle
  (`cluster/users.policy.yaml.example`, `users-access`, currently commented out) is enabled
  with one bearer token per user. Recorded so nobody shares the token assuming it is scoped.
- Pointers added from `AGENTS.md` and `GEMINI.md`; the protocol half stays in
  `skills/structured-memory/SKILL.md` and the wiring half in the setup skill, one home each.

### Added — the graph knows which repository it belongs to (2026-08-31)

`cluster.yaml` was the roster of graph names, and it is now gitignored *and* purged from
history — so agents needed a discovery path that does not depend on a tracked file. The
cluster already had half of it: `graphs_list` returns every `graphId`, which is the list of
repository names. The missing half was the inverse — nothing connected a graph back to a
repository, so an agent could not tell a correctly-pinned bridge from a misrouted one.

- **`Project.repository: String?`** added to `infra/mcp-servers/cluster/memory.pg` and
  converged onto the live cluster (`cluster plan`: 16 changes, **0 approval gates**, every
  one a purely additive `add_property`; `cluster apply`: 8 schemas Applied, `memory` intact
  at 27 nodes). Optional by necessity — a non-nullable property on an existing node is
  rejected as unsupported.
- **Backfilled for this repo** and verified live: `agent-skills` →
  `https://github.com/Ch3fUlrich/agent-skills.git`. The tracked seed carries it too, so a
  rebuild from `cluster/seed/agent-skills.jsonl` restores the link. The other seven graphs
  are still null; each must be set from its own repo's session, because a bridge pinned by
  `OMNIGRAPH_GRAPH_ID` cannot reach another project's graph.
- **This gives the repo's oldest silent failure a cheap detector.** A same-named `omnigraph`
  in `~/.claude.json` outranks a project `.mcp.json` with no error, and on 2026-07-17 that
  led an agent to read `memory`'s two Preferences, conclude a 135-node graph had been wiped,
  and start rebuilding it. `query whoami() { match { $p: Project } return { $p.slug,
  $p.repository } }` now separates "wrong graph" from "empty graph" in one call — the
  existing `~/.claude.json` check only ever proved the *absence* of a shadow.
- Protocol documented in `skills/structured-memory/SKILL.md` (*Which graph fits this
  repository?* — a table of the three questions and what answers each, plus the constraint
  that `graphs_list` is cluster-wide while `query` is not), with pointers from `AGENTS.md`
  and the `CLAUDE.md` precedence trap.

### Fixed — Serena and git worktrees: the two rules that were missing, and one that was wrong (2026-08-31)

Parallel agents across git worktrees broke Serena in two distinct ways, both traced to source
rather than guessed at, and one of this repo's own instructions was teaching the failure.

- **`skills/mcp-servers-setup/SKILL.md` taught activation by *name*** —
  `mcp_serena_activate_project(project="agent-skills")` — while `AGENTS.md` already said absolute
  path. `serena_config.get_registered_project` matches the argument against every registered
  `project_name` **before** it looks at paths and raises `Multiple projects found` on a tie, and
  `.serena/project.yml` is tracked, so every linked worktree registers another project with the
  *same* name. Measured in `sibling-analysis-repo` on 2026-08-31: five worktrees, five registrations all
  called `sibling-analysis-repo`, after which by-name activation failed for every agent at once —
  including the one in the main checkout. An absolute path cannot equal a name, so it skips that
  branch entirely. All three by-name examples in the skill corrected.
- **New *Worktrees and parallel agents* section** in the same skill, with the two mechanisms and
  the general worktree hazard:
  1. **Activate by absolute path, never a bare name** (above).
  2. **One Serena = one session = one worktree.** Serena is a single stdio process per Claude Code
     session holding exactly one `_active_project`, and `_activate_project` **shuts the previous
     project's language servers down** before switching. In-session subagents share that process,
     so N agents in N worktrees means every activation kills the previous agent's LSP mid-flight
     and the last one silently owns everybody's symbol results. Process architecture, not policy —
     no configuration fixes it. Subagents in other worktrees must not re-activate; parallel
     symbolic work needs separate sessions.
  3. **A worktree holds tracked files only.** `.env`, `.venv/` and caches are absent *by
     construction*, so code resolving them from `Path(__file__).parents[n]` finds nothing and, if
     written to degrade gracefully, degrades **silently**. Resolve untracked config through the
     main checkout via `.git` → `gitdir:` → `commondir`; worked example now lives in
     `sibling_analysis/io/_env.py::_linked_worktree_main_root`. Never copy credentials per worktree.
- **`.serena/memories/mcp-servers/current-status.md` corrected.** It claimed `activate_project`
  "switches between any registered repo within one session" — true but dangerously incomplete: it
  reads as license for concurrent multi-project use, which is exactly what breaks. It now records
  that switching is serial and destructive to the outgoing project's language servers.
- **`AGENTS.md`** — the `serena` row now carries "never a bare name" and "one active project per
  session" inline, since that table is what most sessions actually read.

Downstream: `sibling-analysis-repo` got the enforcing half — a PreToolUse fence that refuses a bare-name
activation, and a worktree-aware `.env` resolver — recorded in its own changelog.

### Removed — `antigravity-remote-ui`; Antigravity handles remote connections natively (2026-08-25)

Antigravity now supports remote connections **in the app**, so this repository no longer needs to
solve that problem. `infra/remote-access/antigravity-remote-ui/` — a Docker image plus tunnel that
captured the IDE's chat DOM over CDP and re-served it as a mobile web UI — is deleted rather than
left to rot. It was fragile by construction: any change to the IDE's DOM broke it silently, and it
carried its own image and tunnel to maintain for a single GUI use case.

- **Removed** the directory and its six files (guide, Dockerfile, compose, both start scripts,
  manual TODO), plus the now-dead `antigravity-remote-ui/node_modules/` rules in `.gitignore` and
  `.graphifyignore`.
- **`infra/remote-access/README.md` rewritten** around Herdr alone, with a short note recording why
  the scrape-and-tunnel approach existed and why it went — so the next reader does not rebuild it.
- **Cross-references updated**: `README.md`, `AGENTS.md`, `docs/architecture.md`,
  `skills/repository-index/SKILL.md`, `infra/remote-access/herdr/README.md`.
- **Deliberately untouched.** Antigravity is still a supported agent here: its MCP wiring
  (`infra/mcp-servers/config/mcp_antigravity.json`, `GEMINI.md`), the scratch-isolation rule naming
  its session directory, and the `antigravity` orchestration provider adapter all stay. Only the
  *remote-access workaround* is obsolete, not the app.
- Historical records (ADR 0002, the 2026-07-09 restructure spec and plan, and earlier changelog
  entries) keep their original wording — they document decisions that were correct when made.


### Changed — Orchestration docs collapsed to one home per fact; README made accurate (2026-07-30)

Prompted by two articles — Cursor's *Agent Swarms and Model Economics* (identical completion varied
~8× in cost purely by planner/worker model split; workers are 69–90% of tokens but a minority of
cost) and Rahul Garg's *The Orchestrator's Tax* (the cost of subagents is context pollution, not
tokens: "tokens are spent once; context shapes every decision that follows").

The audit's finding was **not** that the policy was wrong. Contract-first handoff, a different-family
reviewer, frontier-planner plus mid-tier-worker routing, and worktree isolation were already right.
What was wrong was that facts had multiple homes and had already drifted, and that nothing enforced
the policy on the surface actually driven.

- **Deleted `AGENT_ORCHESTRATION_FRAMEWORK.md`.** It restated `SKILL.md` and the YAML almost in
  full — risk signals, checkpoint fields, and cache rules each had three homes. Its unique content
  (decomposition criteria, the contract's minimum sections) folded into `SKILL.md`.
- **`swarm-orchestration/SKILL.md` rewritten to hold no numbers.** Every threshold, weight, tier,
  and matrix now points at `agent_orchestration.config.yaml`. A §0 ownership table states where each
  fact lives; a number in the skill is now a bug. This is the root-cause fix for a three-way
  disagreement on risk thresholds between the skill, the YAML, and `RiskEngine.score()`.
- **`CUSTOM_ORCHESTRATION_VS_OPENHANDS.md` → [ADR 0005](docs/decisions/0005-openhands-as-provider-adapter.md).**
  A build-vs-buy decision belongs to the repository, not to a skill.
- **`swarm-orchestration/README.md` is operational only** — how to run the scaffold, how to add a
  provider adapter. Its "Architectural Principles" duplicated policy and is gone.
- **[ADR 0004](docs/decisions/0004-executable-orchestration-policy.md) + a design spec** record the
  decision that policy must be *executable*: a generated `.claude/` adapter (role subagents with
  model tiers, workflows carrying return schemas, a hook blocking repo-wide git in subagent
  prompts), two-tier wave caps, and the `different_family_from_engineer` split by surface. Approved,
  **not yet implemented**.
- **Router rewritten** (`skills/repository-index/SKILL.md`) with a repository map — every directory,
  instruction file, and doc location with the trigger that sends you there — so an unfamiliar agent
  can find what it needs without browsing.
- **README corrected.** It claimed the repository "incorporates Harbor" (it contains no Harbor
  files), listed 5 of 13 skills, omitted `webpage/`, implied Sentry/Datadog run by default when they
  sit behind an `observability` profile, and cited only ADR 0001 for a memory model ADR 0003 had
  since changed.
- **Housekeeping:** stray `prompt/` merged into `prompts/`; the mock engineer checkpoint untracked.

Recorded during the audit and **not yet fixed** — see the spec's defect table: the Python scaffold's
swarm review fails **open**, returning `APPROVE` unconditionally and silently, because a model tier
is passed where a provider id is expected and `adapter_for()` sits outside its retry block.

### Added — Scratch script isolation rules across agent instructions (2026-07-28)

- Updated `AGENTS.md`, `GEMINI.md`, and `skills/coding-principles/SKILL.md` (Principle 7) requiring all agents (Gemini, Claude, Antigravity, etc.) to place scratch scripts exclusively inside connected scratch folders (`<appDataDir>/brain/<conversation-id>/scratch/`, `.claude/scratch/`, or `scratch/`).

### Added — `herdr-orchestration` skill: what Herdr is *for*, and what it is not (2026-07-19)

[ADR 0002](docs/decisions/0002-herdr-multiplexer.md) adopted Herdr and
`infra/remote-access/herdr/` documents installing it, but nothing under `skills/` covered
**driving** it — so the router could not route to it and its socket API was, in practice,
invisible to agents.

The risk was not that agents would miss it; it was that they would find it and misuse it.
"Agent orchestration" plus 14.6k stars invites replacing the `Agent`/`Workflow` tools with
Herdr, which trades typed return objects for **scraped terminal text** — losing schemas,
automatic result plumbing, resume, and budget accounting. The skill therefore leads with the
decision flow: native tools by default, Herdr only for the four things they genuinely cannot
do — outliving the session, non-Claude agents, human-in-the-loop, and long-lived process sync.

Every command was verified against 0.7.4 rather than taken from docs, which surfaced traps
that all fail **silently**:

| Trap | Why it bites |
|---|---|
| `agent read --source recent` returns `""` | `--source visible` returns the text. A working spawn reads as producing nothing; poll loops spin to timeout. |
| status is **heuristic** without the integration hook | Herdr shows `working`/`idle` either way, so it looks correct — but `agent wait --status idle` can fire while a model is thinking. `herdr integration status` reports `claude: not installed` on this host. |
| `agent send` writes literal text, no Enter | The prompt sits in the input box; needs `pane send-keys <pane> Enter`. |
| panes share the working tree | Parallel agents corrupt each other — `herdr worktree create` per agent. |

Routed from `skills/repository-index` §2c and the `AGENTS.md` trigger table.

### Changed — agent instructions halved; `omnigraph-globals` erased, not explained (2026-07-17)

Instruction files are paid for on **every** session, so their length is a running cost. The
seven across the four repos totalled **5754 words (~7.7k tokens)** and are now **2885
(~3.8k) — 49% off**, with nothing load-bearing dropped. Prose became tables; the session
flow became mermaid.

Most of the fat was **duplication the repos' own Principle 1 forbids** — a skill's `SKILL.md`
is the single source of truth, and these were re-deriving it:

| File | Was | Now | What went |
|---|---|---|---|
| `sibling-analysis-repo/AGENTS.md` | 1911 w | 629 | re-documented Serena's tool tables, the Omnigraph protocol, the Superpowers list — all of which live in `skills/` |
| `sibling-analysis-repo/CLAUDE.md` | 1061 w | 159 | ~90% duplicated its own AGENTS.md; now a real Claude-only delta |
| `agent-skills/GEMINI.md` | 268 w | 110 | duplicated AGENTS.md's skill list, which it says to read first |
| `agent-skills/CLAUDE.md` | 445 w | 314 | same; gained a mermaid of the MCP precedence trap |
| `agent-skills/AGENTS.md` | 678 w | 460 | prose → trigger table |
| `finance-repo/AGENTS.md` | 684 w | 515 | prose → tables |
| `.mcp.json` × 4 comments | 264–389 w | 129–161 | |

**`omnigraph-globals` is gone, not memorialised.** Nine files still explained *why there is
no such bridge* — the name alone kept the dead concept alive and invited agents to go looking.
They now state the positive fact: one bridge per repo; `memory` is unreachable by design
because its two globals are already Principles 2 and 6 of `coding-principles`.

Purging it surfaced instructions that were actively wrong:

- **`finance-repo/AGENTS.md` told agents to fall back to the `mem0-fallback` compose profile** if
  Omnigraph was unreachable. Mem0 was removed entirely (ADR 0003); that profile does not
  exist. It also told them to query global `Preference`s its bridge **cannot reach**, and to
  re-embed via `populate-embeddings.py` — which does not work on a populated graph. Replaced
  with the verified limitation: `merge` erases embeddings, an unembedded `Decision` is
  *dropped* from `nearest()`, and on v0.8.1 there is no working re-embed — don't chase it.
- **`sibling-analysis-repo/CLAUDE.md` still claimed "40-60% token savings"** — the unmeasured number
  removed from this repo days ago. Now: the mechanism is real, the figure is folklore.

Verified: all four `.mcp.json` still valid and correctly pinned; every bridge resolves to its
own graph (agent-skills / sibling-analysis-repo / homelab-server / finance-repo); 0 dangling links.

### Fixed — the router did not route to half the skills (2026-07-17)

`skills/repository-index/SKILL.md` is the file every instruction file points agents at
first — and it listed **6 of 11 skills**. The five it omitted included `coding-principles`
and `structured-memory`, the two that apply to *every* task. An agent told "start at the
router" would never have found them there.

- **All 11 skills are now routed**, split by how they are actually used: §2a always-on
  (`coding-principles`, `structured-memory`, `mcp-servers-setup`), §2b task-shaped, §2c
  orchestration/review. The old table implied everything was an escalation for big tasks;
  the baseline is not an escalation.
- **Superpowers was described as "workflow extensions and internet access … when you need to
  search the web".** It has no web access — it is `systematic-debugging`,
  `test-driven-development`, `brainstorming`, `verification-before-completion`. Corrected,
  and **`context7` added** — the server that *does* answer library/framework questions was
  missing from the MCP table entirely.
- The `omnigraph` row still claimed it "automatically syncs at start/end of sessions"; it is
  a 5-minute timer. Now records the per-graph pin, the silent-failure env vars, and the
  operational-rules prerequisite.
- **The decision tree began at "is it a simple tweak?"**, so its only path for small changes
  routed past memory, Serena and the baseline entirely. It now opens with session start
  (recall → activate → baseline) and closes with session end (verify → **persist**) — the
  loop it previously opened and never closed. Recall without persist is a memory that only
  ever shrinks.

`README.md` gains a proper explanation of what the router *is* and why it exists, rather
than a one-line pointer: a skill an agent does not know about is a skill it will not load,
and nothing announces the omission.

### Added — every sibling repo now points at the router (2026-07-17)

`homelab-server/CLAUDE.md`, `sibling-analysis-repo/{AGENTS,CLAUDE}.md` and `finance-repo/AGENTS.md` each pointed
at individual skills but never at the map. All four now open with a **START HERE** section.
Path per repo's own convention (`../agent-skills/…`; Server also notes `~/code/agent-skills`
is the coding.example.internal form and does not exist on a workstation).

Fixing them surfaced stale instructions that would have re-caused this week's incident:

- **`sibling-analysis-repo` still documented the `omnigraph-globals` server** in four places —
  removed on 2026-07-17, and dropped from its `.mcp.json` yesterday. Its docs were still
  telling agents to read globals from a server that only answers "invalid bearer token".
- **`finance-repo/AGENTS.md` said its memory lived in the `memory` graph.** It lives in `finance-repo`
  (71 nodes). Following that text would have written project data into the globals-only
  graph — the exact thing per-project isolation exists to prevent.
- **`finance-repo` had no `.mcp.json` at all**, relying on the user-scope override removed
  yesterday; it now had *no* bridge. Added one pinned to `finance-repo` — verified through the
  real bridge at 152 rows.

Each file now also carries the fingerprint that settles a false alarm: `0 rows except
2 Preferences` **is** the `memory` graph, not a wipe — check config with
`setup-agent-memory --check` before rebuilding anything.

### Added — `setup-agent-memory.{ps1,sh}`: the bridge setup, automated (2026-07-17)

The sibling of `setup-sync`, and the two are constantly confused, so the READMEs now lead
with the distinction: **`setup-sync` is the timer; `setup-agent-memory` is the MCP bridge the
agent reads memory through.** The bridge half had been documented but never automated — it
was the hand-work of the override incident above, so it is now one command.

It builds `omnigraph-mcp:latest` (published to no registry, so it must be built per host),
sets `OMNIGRAPH_TOKEN`/`OMNIGRAPH_NET` by reading `.env.shared` and `docker inspect` so
neither is typed or guessed, removes any user-scope `omnigraph` override (backing up
`~/.claude.json`), audits every sibling repo's `.mcp.json` (flagging `omnigraph-globals`, a
missing/nonexistent graph pin, a hardcoded token, a hardcoded `--network`), and finally
**drives the real bridge** to print rows per graph. `--check`/`-Check` diagnoses without
changing anything and exits non-zero, so it works as a health check.

Two bugs found by testing it, both worth keeping fixed:

- It reported **"OK: no user-scope omnigraph" when it had failed to parse `~/.claude.json`**
  — the exact false-success class this stack keeps producing. The file legitimately holds
  keys differing only in case (project paths as typed), which `ConvertFrom-Json` rejects;
  it now parses `-AsHashtable`, and an unreadable file is a **problem**, never an OK.
  Not knowing whether an override exists is not the same as knowing one does not.
- A run that *fixed* everything still exited 1, because the override was counted as a
  problem before being repaired and never uncounted.

Editing `~/.claude.json` is delegated to python rather than done via a PowerShell hashtable
round-trip, which would collapse those case-distinct keys and reformat all 68KB of unrelated
Claude Code state. Verified end-to-end by injecting a fake override: `-Check` detects it and
exits 1; the fix run removes it, keeps all 32 project entries and the other three user-scope
servers intact, and exits 0.

### Fixed — a user-scope MCP override made every repo read the wrong graph (2026-07-17)

`~/.claude.json` defined a **user-scope** server named `omnigraph`, pinned to
`graph_id: memory` with a hardcoded token. Same-named user-scope entries **silently win**
over a project's `.mcp.json`, so every repo's per-project pin was overridden. Nothing
errored — the bridge connected and answered, just about the wrong graph.

The damage was nearly severe. An agent in `sibling-analysis-repo` saw *every table rowCount 0
except 2 `Preference`s*, concluded the project graph had been **wiped**, and began
rebuilding it. That reading was `memory`'s entire contents. `sibling-analysis-repo` was intact
throughout — 135 nodes / 235 edges on both local and central — and the rebuild would have
written a project into the globals-only graph. Verified through the bridge itself:
`sibling-analysis-repo` → 370 rows, `memory` → 2 rows, empty token → `missing bearer token`.

- Removed the user-scope `omnigraph` entry (backed up first); each repo's `.mcp.json` now
  provides its own, correctly pinned.
- `sibling-analysis-repo/.mcp.json`: dropped the `omnigraph-globals` bridge deleted on 2026-07-17,
  which resolved an unset `${OMNIGRAPH_TOKEN}` to an empty bearer and failed every call with
  "invalid bearer token" — the visible half of this bug.
- `Server/.mcp.json`: `--network` was hardcoded to `mcp-servers_default`; now
  `${OMNIGRAPH_NET:-mcp-servers_default}`. The hardcoded value is right on coding.example.internal and
  silently wrong elsewhere: on the workstation that network **exists but is empty**, so
  `docker run` succeeds and the bridge just cannot resolve the server.
- **Built `omnigraph-mcp:latest`, which had never been built on this machine.** `CLAUDE.md`
  claimed "docker works on every host that runs the stack"; the image is published to no
  registry, so `docker run` was failing with `pull access denied` and the docker bridges
  could not have started here at all. The claim is corrected and the build step documented.

`operations.md` gains rule 0a — *an empty-looking graph is a config bug until proven
otherwise, never "rebuild" it* — with the fingerprint (`0 rows except 2 Preferences` **is**
the `memory` graph) and the out-of-band check that settles it.

### Added — one-command sync setup (2026-07-17)

`omnigraph-setup/setup-sync.ps1` (Windows) and `omnigraph-setup/setup-sync.sh`
(Linux/macOS/WSL) take a host from "the stack is running" to "memory syncs every 5 minutes":
they read `OMNIGRAPH_TOKEN` from `.env.shared`, detect `DOCKER_NET` from the **running**
`omnigraph-server` (never a config file — it differs per host and a wrong value fails
silently), write `omnigraph-setup/.env`, and register a Scheduled Task / systemd `--user`
timer. `--interval`, `--show`, `--no-schedule`, `-Unregister`.

Two properties worth keeping when editing them:

- **A dry run gates the schedule.** Nothing is scheduled unless a full DRY_RUN sync passes,
  so a broken setup fails at setup time rather than silently every 5 minutes forever.
- **A pre-flight gates the write.** Both servers must answer `200` *before* `.env` is
  touched, so a typo'd `--central-token` cannot replace a working config with a broken one.
  (Found by testing exactly that: the first version wrote first and validated after.)
  Same rule `pull_graph.py` learned: never destroy old state until its replacement works.

Verified on Windows end-to-end: the registered task ran on its own trigger with
`LastTaskResult=0`, and all five graphs stayed identical local↔central with no duplicates.

### Fixed — the sync re-pushed 52 unchanged nodes on every run (2026-07-17)

Every run reported nodes as "changed" that were byte-identical to central — 4 for
agent-skills, 52 of sibling-analysis-repo's 135, 6, 13 — and re-pushed them forever.

The count was exactly the number of records containing **non-ASCII bytes**. `pushset` read
the local export from **stdin**, which Python decodes with the *locale* encoding (`cp1252`
here), but read the central export via `open(…, encoding="utf-8")`. The same text became two
unequal strings, so every node with an em dash, arrow or umlaut compared as changed. Nothing
was ever corrupted — a cp1252 decode/encode round-trip is byte-lossless — but each run wrote
to central for no reason. `omnigraph_jsonl.py` now forces UTF-8 stdio itself
(`_force_utf8_stdio`) rather than relying on a `PYTHONIOENCODING` that only one of its three
call sites would have carried. All five graphs now report a **0-node, 0-edge** delta.

The same bug class was latent and **worse** in `sync-windows.ps1`, where it damages payloads
rather than just comparisons: native-command output is decoded with `[Console]::OutputEncoding`
(the OEM code page, cp850) and text piped *into* a native command is encoded with
`$OutputEncoding` (ASCII on Windows PowerShell 5.1, which turns `→` into `?`). It now pins
UTF-8 for all three hand-offs before any data moves. Its `-DryRun` also swallowed the central
verify verdict (`| Out-Null`) and never computed the delta — both fixed, so a dry run can now
be believed and reports what it would push.

Regression tests (`test_omnigraph_jsonl.py`, 9/9) drive the CLI as a subprocess with **raw
bytes** and a legacy child encoding; the previous `text=True` helper could not catch this,
because it encoded and decoded with the same parent locale and the mismatch cancelled out.

### Fixed — the local↔central sync, which was corrupting central (2026-07-17)

Running the sync **duplicated every edge on all four central project graphs** (agent-skills
27→54, sibling-analysis-repo 120→221, finance-repo 81→125, homelab-server 18→32) while reporting `rc=0`
and "pulled central main -> local main". The pull had in fact failed on every graph. Central
was repaired (dedup reproduced the git seeds byte-for-byte on all four — two independent
sources agreeing) and the causes fixed:

- **The push sent the whole local export** onto a device branch forked from `main`. Edges
  have no `@key`, so every edge central already had was appended. It now sends a **delta**
  (`omnigraph_jsonl.py pushset`): changed/new nodes only, and only edges central lacks.
- **Pushing an *unchanged* node** still bumps that table's version, so the merge died with
  `Concurrent modification: table version N already exists`. Identical nodes are now
  skipped; an empty delta pushes nothing at all — the common case on a timer.
- **The device branch is gone.** On v0.8.1 `branch create` can hit a Lance internal error
  ("Clone operation should not enter build_manifest") and `branch merge` the above. It
  existed for "review before merge", which is meaningless on an unattended timer; the delta
  push goes straight to central `main`. Safety is the delta + backups + verify gates.
- **The pull no longer uses `load --mode overwrite`** on a populated graph (Lance bug — and
  it can *land while exiting 1*, so even its failure is untrustworthy). New
  `omnigraph-setup/pull_graph.py` purges then loads into the empty graph, pre-flights that
  the load path is reachable **before** purging, and restores from backup on failure.
- **Failures are visible.** A non-zero exit from a native command is not a PowerShell
  terminating error, so the old `try/catch` never fired. Both scripts now check exit codes
  and honour `verify`'s verdict instead of piping it to `Out-Null` / `|| true`.
- `omnigraph-sync.sh` was rewritten to the same logic — it previously could not run on
  Windows at all (bind-mounted a `mktemp -d` path Git Bash mangles) and carried the same
  duplicate-push bug. Both platforms now drive the same two Python helpers.

Verified: repeated full runs, `rc=0`, all five graphs identical central↔local and
duplicate-free, **zero edge growth** across consecutive runs. Operator manual:
`infra/mcp-servers/omnigraph-setup/SYNC-MANUAL.md` (5-minute cadence, config,
troubleshooting, known limits).

### Changed — `infra/mcp-servers/setup/` → `infra/mcp-servers/omnigraph-setup/` (2026-07-17)

`setup/` said nothing about what it holds: the Omnigraph sync/reconcile tooling, not
general project setup. Every reference updated across 26 files, plus the live
`as-sync-automation` node's `location` on both local and central — a rename that leaves
memory pointing at a dead path is the same drift this repo keeps getting bitten by.

### Added — `.gitattributes` (2026-07-17)

The repo had no eol policy, so with `core.autocrlf=true` a Windows checkout rewrote shell
scripts to CRLF; the shebang became `#!/usr/bin/env bash\r` and the script could not run.
`*.sh`/`*.py`/units/configs are now pinned `eol=lf` (`*.ps1` stays `crlf`), and CR was
stripped from 33 tracked files.

### Fixed — graphify clustering on mixed str/int node ids (2026-07-17)

`sibling-analysis-repo` (14,275 str + 17 int ids) crashed with `TypeError: '<' not supported
between instances of 'int' and 'str'`, leaving nodes/edges current but community labels
stale (2% coverage). `scripts/patch-graphify-cluster-sort.py` adds `key=str` to the bare
node-id sorts upstream missed — in `build.py` (where the CLI actually dies first) as well as
`cluster.py`. Result: `cluster-only` completes, 934 communities, coverage 2% → 100%.

### Removed — Mem0, entirely (2026-07-16)

Omnigraph is the memory layer, with **no fallback** — see the new
[ADR 0003](docs/decisions/0003-remove-mem0-fallback.md) for why the escape hatch cost
more than it insured (ADR 0001 keeps the original Omnigraph rationale; only its fallback
clause is void). The stack now needs no Postgres/pgvector and no LLM API key.

- Deleted `servers/_fallback/` (mem0-custom, mem0-mcp, mem0-dashboard), the
  `mem0-fallback` Compose profile and its 4 services, and the Mem0/Postgres/DeepSeek env
  vars from `.env.server.example`.
- **Deleted the Mem0-era orchestration scripts** — `scripts/{windows,linux}/{setup,start,
  stop,test,migrate}.*` and `scripts/test_mcp_tools.py`. They were not merely
  mem0-flavoured: they stood up the Mem0 stack, health-checked `localhost:8888`, and
  their bare `docker compose` calls had been failing with "no configuration file
  provided" ever since the server/client compose split. The documented setup path had
  been dead for months. Docs now point at the compose commands that actually work.
- Deleted the legacy `docs/ARCHITECTURE.md` + `docs/TOKEN_SAVINGS.md` — self-declared
  duplicates of `docs/architecture.md`, structurally Mem0 documents.
- Swept every remaining live reference (README, AGENTS, architecture, infra README,
  TODO, runbook, troubleshooting, install guide, server READMEs, ignore files) and
  removed the obsolete `ba-conv-mem0-project-isolation` Convention (`user_id=` on "every
  Mem0 call") from the live `sibling-analysis-repo` graph. History — CHANGELOG, ADRs,
  `docs/superpowers/**` — is left intact.

### Changed — Omnigraph rules realigned to the upstream best-practices docs

Checked `skills/structured-memory/` against `omnigraph://best-practices/*` and verified
each claim against the running server rather than adopting it on faith:

- **Corrected a rule that was wrong for our schema.** `operations.md` warned that "blind
  retries duplicate append-only nodes like `Decision`" — that taxonomy is the upstream
  *cookbook's* schema, not ours. Every type in `memory.pg` is `@key(slug)`; verified two
  identical `insert Preference` calls produce **one** node. Nodes are safe to retry;
  **edges** (no `@key`) are the real risk.
- **Documented behaviour worse than upstream describes.** The docs say `load --mode
  merge` leaves an `@embed` vector "stale". Verified: it **erases** it — merging a
  `Decision` whose record omits `embedding` nulls the field, and an unembedded
  `Decision` is *dropped* from `nearest()`, not ranked low. Measured 3/6 `agent-skills`
  and 2/17 `sibling-analysis-repo` Decisions silently unsearchable this way.
- **And that the documented fix does not work.** On v0.8.1 *both* re-embed paths fail
  with the same Lance bug on a populated graph (`overwrite` →
  `stage_create_btree_index`; `merge` carrying vectors → `LanceError(Arrow)`), so
  `populate-embeddings.py` only works against a fresh graph. Recorded as a known
  limitation instead of an unfollowable instruction. A failed overwrite can leave Lance
  HEAD ahead of the manifest so all loads to that graph fail while reads work —
  `docker restart omnigraph-server` recovers it, no data lost.
- Added, from upstream: `load --from` forks a review branch in one shot; branches are
  short-lived (create → load → verify → merge → **delete**) and a stray one **blocks
  `schema apply`** (so a leftover `device/<host>` branch breaks `apply-cluster.sh`);
  "scope first, rank second" before `nearest`/`bm25`/`rrf`; `409 manifest_conflict` and
  `429` are always safe to retry; `sync_branch()` is a leaked internal directive, not a
  tool; pick `mutate` over `load` for a handful of records.
- Rewrote `references/schema.md`'s stale framing; `prompts/omnigraph-central-bring-up-to-date.md`
  replaces the two narrower central prompts.

### Fixed — per-project graph isolation actually deployed (2026-07-16)

The isolation model was declared in git but **never applied to a running server**.
Root cause: `scripts/apply-cluster.sh` sourced a `./.env` that never existed (the
convention is `.env.shared` + `.env.server`), so it died on line 15 under `set -e`
and had never run. Everything below followed from that one failure.

- **`apply-cluster.sh` made runnable** — sources the right env files, uses the real
  docker network (`mcp-server_mcp-net`, not `mcp-servers_default`), and converts
  container paths for Git Bash (MSYS rewrote `--config /cluster` to
  `C:/Program Files/Git/cluster`). Cluster converged: 5 graphs + updated schema.
- **Five relational edge types were missing from the live schema** — `Tracks`,
  `Affects`, `Addresses`, `Implements`, `DependsOn`. Every agent following the
  "link richly" rule hit `T4: unknown edge type`, and every seed line using them
  failed **silently**. Now live; 7 orphaned hub edges repaired (4 `Tracks`,
  3 `ConstrainsProject`) — the nodes that had been rendering as "global".
- **Seed loader corrected** (`docker-compose.server.yml`): loads each seed into the
  graph matching its **file name** (it hardcoded `--graph memory`, i.e. actively
  maintained the shared model it was meant to replace), declares the compose
  network (it had none, so it could never resolve `omnigraph-server`), and now
  **exits nonzero** on a failed seed instead of `|| echo WARN`.
- **All 4 projects migrated to their own graphs, `memory` pruned to globals-only.**
  Verified: 160 project nodes + 2 global `Preference`s = 162 = the old `memory`;
  163/163 edges. `memory` is now 2 nodes / 0 edges.
- **Seeds refreshed from live** — they had drifted badly (9 vs 16 decisions; tasks
  marked `planned` that were complete), and the loader merges by `@key(slug)` on
  every boot, so a stale seed **silently overwrites newer live values**.
- **`add-project-graph.sh`** told you to set `OMNIGRAPH_GRAPH` — the *viewer's*
  variable. The bridge reads `OMNIGRAPH_GRAPH_ID`; you would silently stay on
  `memory`.
- **`skills/structured-memory/references/schema.md` rewritten** — it documented kebab-case edge names
  (`decided-in`, `constrains`), a `relates-to (any → any)` edge that does not
  exist, and an `ingest` tool that is actually `load`. Its JSONL example would have
  failed on load.

### Added

- **`scripts/split-project-graph.py`** — carve a project's subgraph out of the
  shared graph (`--source memory --apply`, additive), refresh a seed from live
  (`--write-seed`), or prune the source (`--prune-source`, gated on verifying every
  node is mirrored in its project graph first). No such migration tooling existed.
- **Reading globals needs a second bridge.** `OMNIGRAPH_GRAPH_ID` pins a bridge to
  one graph and no tool takes a graph argument, so a project-scoped agent could not
  reach the global `Preference`s its own skill told it to recall. Repos now declare
  `omnigraph` (own graph) + `omnigraph-globals` (`memory`, read-only) — see this
  repo's `.mcp.json`.
- **Viewer: graph chips + focus-on-click.** The graph dropdown and per-project tabs
  are replaced by chips in the tab bar (graph ≈ project now): click to switch,
  ctrl/⌘/shift-click to show **several graphs at once**, each as its own coloured
  cluster. Clicking a node gathers its 1-hop neighbours at the centre and pushes
  everything unrelated out to a faded ring (`Esc`/click-away/re-click to clear).
  Node ids are namespaced `<graph>::<slug>` so slugs cannot collide across graphs.
- **`prompts/omnigraph-fix-central-server.md`** — handoff prompt to bring the
  central `coding.example.internal` instance through the same convergence, with gates and the
  sync/dedup warning below.
- **`webpage/orchestration-upgrade-plan.html`** — repo audit, bug register, router
  spec, T0–T3 hardware tiers, the L0–L5 loop architecture, and the
  crowds-vs-experts policy for Best-of-N.

### Known issues

- **Sync and dedup are single-graph and currently unsafe.** `omnigraph-sync.sh`
  (`GRAPH="${GRAPH:-memory}"`) pulls central → local with `load --mode overwrite`,
  so pointing a pruned local at a stale central **restores the pruned data and
  undoes the migration**; the 4 project graphs are never synced at all.
  `dedup-graph.py` hardcodes `--graph memory` in 3 places. **Keep
  `omnigraph-sync.timer` / `dedup-graph.timer` disabled until both iterate graphs.**
- The central `coding.example.internal` server has not been converged — same un-run
  `apply-cluster.sh`. The project graphs currently exist on one client only.
- **Exposed the stack to the internet** via the OPNsense/Caddy reverse proxy:
  `omnigraph.example.org` (API, bearer only), `omnigraph-ui.example.org`
  (viewer) and `omnigraph-minio.example.org` (MinIO console), the latter two
  behind Authelia (admin-only).
- Interactive **memory viewer**: project tabs (All/Global/per-project), a
  force-directed graph (clickable while animating, zoom/pan, edge details), a
  filterable/sortable table, and search highlighting with match explanations.
- Deployed the Omnigraph memory stack on coding.example.internal via the single-source
  `Server/server/coding/mcp-servers/docker-compose.yml` (server + MinIO + viewer),
  seeded, with real Ollama `nomic-embed-text` embeddings and working semantic
  search. `search_decisions` stored query.
- Credential-**access** memory (methods + file locations, never secret values)
  stored in the `homelab-server` graph, plus a hard rule against storing secrets
  in the internet-exposed graph.
- `infra/mcp-servers/omnigraph-setup/`: client/server setup guide, `client-setup.sh`,
  and `omnigraph-sync.sh` (+ systemd timer) for automatic device-branch sync.
- `coding-principles` skill + starter: DRY, TDD, single responsibility,
  document-the-why, changelog/ADR backtracking, MCP-first navigation.
- `structured-memory` skill: typed cross-project memory protocol on Omnigraph
  (schema + recall/persist workflow).
- `infra/remote-access/herdr/`: Herdr agent-multiplexer setup for Linux, macOS,
  and Windows, with helper scripts and a remote-access comparison guide.
- Omnigraph + MinIO as the default memory stack in `infra/mcp-servers/`, plus
  `servers/omnigraph/README.md`.
- ADRs: `0001-omnigraph-over-mem0`, `0002-herdr-multiplexer`.
- Restructure spec + implementation plan under `docs/superpowers/`.

### Changed
- **Repository restructured** into three pillars: `skills/` (reusable skills),
  `starters/` (per-repo adapters), `infra/` (self-hosted runtime:
  `mcp-servers/` + `remote-access/`). Loose dirs `Gen/`, `prompt/`,
  `repositories/` consolidated into `prompts/`; `mcp-servers/` and
  `antigravity-remote-ui/` moved under `infra/`.
- Default MCP memory layer switched from Mem0 to Omnigraph. Mem0 retained as an
  off-by-default `mem0-fallback` Compose profile. See
  `docs/decisions/0001-omnigraph-over-mem0.md`.
- Root instruction files (`AGENTS.md`, `CLAUDE.md`, new `GEMINI.md`) reduced to
  thin pointers at the skills.
- Infra scripts/configs de-personalized: hardcoded paths replaced with
  `${AGENT_SKILLS_ROOT}` / `${CODE_ROOT}` / `${SERENA_HOME}` (and PowerShell
  `$env:` equivalents).

### Removed
- Committed generated `graphify-out/` output is no longer tracked.
