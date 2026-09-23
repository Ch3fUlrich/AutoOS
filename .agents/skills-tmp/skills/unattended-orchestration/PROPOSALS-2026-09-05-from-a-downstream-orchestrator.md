# Proposals for `unattended-orchestration`, from one orchestrator's day (2026-09-04/05)

> Private project names are replaced by *downstream project A/B/C* (publication scrub, 2026-09-18).

> **Status (2026-09-05, adopted while orchestrating downstream project C).** Each proposal below is
> annotated with where it landed; the file stays as the backlog and the incident record.
>
> | § | proposal | status |
> |---|---|---|
> | 1 | finished on evidence (DONE note + quiet branch; never resume a finished session; merged-elsewhere) | **built** — `quietMinutes`, `Test-HandoffDoneQuiet`, `finished_by`, the ancestor check |
> | 2 | lanes addable / stoppable while running; successor sessions | **built** — `<stateDir>/queue/lane-*.json` and `stop-<key>`; the child re-reads the config so a re-cut session is added to the config and queued. Automatic `successor` start: open |
> | 3 | cleanup as part of the lane | **built** — `stopAfterMerge`, `removeWorktreeAfterMerge`, `-Cleanup` (process, worktree, branch, Serena row). `archiveDirs`: open |
> | 4 | verify on a pinned commit | **built** — `guardsOnly` sessions with `dependsOn`; rule 7 in SKILL.md |
> | 5 | resource exclusion beyond ordering | **built** — `resources` (`name[:read|write]`), `Test-HandoffResourceConflict`, `waiting-resource` |
> | 6 | one machine budget | **built, advisory** — `machineBudget` rendered as `{{machineBudget}}`, `<stateDir>/load.json` refreshed every poll. Blocking on CPU deliberately not built (deadlock behind another project's pass) |
> | 7 | traps travel with the brief | **built** — `traps` → `{{traps}}` |
> | 8 | the successor brief skeleton | **documented** in SKILL.md §4 |
> | 9 | refusal counts, `merged_into`, controller brief, logs only on done sessions, cross-session messages | **built** (`refusals`, `merged_into`, `controllerBriefFile`); logs are read only after a turn ends; messaging noted in SKILL.md §4 |
> | 10 | keep resume-never-restart and guard-gated merges | unchanged |


*Written by the downstream project A orchestrator session (`orchestrator2`, Fable 5.1) on 2026-09-05
06:30, while the final corpus pass ran. Source material: the day's log in
`<project-A>/docs/superpowers/plans/2026-09-03-session-handoffs/ORCHESTRATOR-log-2026-09-04.md`
and the DONE notes of lanes A, B, C, D, E, F, F2–F8, G. The skill's files were being edited by
someone else while this was written (uncommitted changes in the working tree), so this is a
separate file and nothing in `SKILL.md` was touched. Each proposal names the incident that
motivates it; where I would just be guessing, I say so.*

What worked and should stay exactly as it is: **guard-gated `--no-ff` merges under a mutex** (the
runner merged F3, G, F4, F5, F6 for me on green guards, four hours of merging I never did by hand);
**one worktree per session** with `-c core.hooksPath=/dev/null` commits; the **refusal rule** in
the brief (every DONE note carried a "refused by the classifier" section, three of them non-empty,
none worked around); the **DONE note as the unit of hand-off** (every re-cut brief today was
written from the previous DONE note in under ten minutes); `claude attach` / `claude logs` on a
stable name.

## 1. A finished session can look `working` for hours — merge on evidence, not on the turn end

**Incident.** Sessions C and F7 both wrote their DONE note, made their last commit, and then
reported `working` in `claude agents --json` for three hours (C's own log said "done 6:18 PM" while
the state said `working`). The runner waits for the turn end, so nothing merged; the orchestrator
merged both by hand. Worse, C's lane hit `MaxSessionHours`, **stopped the finished session and
resumed it** — twice — each time spawning a fresh Fable session that read the brief, found the work
done, and idled, at a cost of a full session start.

**Proposal.**
- Treat a lane as *finished and mergeable* when **any** of these hold: the turn ended; **or** the
  DONE note exists in the worktree **and the branch has had no new commit for `quietMinutes`**
  (default 20); **or** the branch is already an ancestor of the base branch
  (`git merge-base --is-ancestor <branch> <base>` — the orchestrator merged it).
- Before `MaxSessionHours` stops a session, **check for a DONE note**. A session with a DONE note
  is never resumed; the lane proceeds to guards and merge.
- Record which rule fired in `state.json` (`finishedBy: turn-end | quiet-branch | merged-elsewhere`).

## 2. Lanes must be addable, stoppable and re-cuttable while the runner runs

**Incident.** The F lane was re-cut eight times (F → F2 → … → F8). Each time I had to add a `$spec`
row, commit it, fast-forward the base branch, and **start a new runner process** with `-Lanes Fn`,
because `-Lanes` is read once. I ended the day with six runner processes over one `state.json`
(F4, G, F5, F6, F7, F8), and stopped two lane pollers with `Stop-Process` because there was no
sanctioned way to say "this lane is done, do not resume".

**Proposal.**
- A control directory `<stateDir>/queue/`: dropping `lane-<name>.json` (`{"sessions": ["F8"]}`)
  starts a lane in the running runner; `stop-<lane>` stops its poller after the current session;
  `merged-<session>` tells it the orchestrator merged the branch. Poll it in the main loop.
- Or, minimally: document that **one runner process per lane is supported** and make every
  `state.json` write go through the same mutex the merge uses (the writes are read-modify-write;
  I saw no corruption, but I also did not look hard).
- A `nextBrief` convention for re-cuts: a session whose DONE note has a `## What remains` section
  can declare `successor: "F9"` in its config entry with a **templated** brief; the runner starts
  the successor when the predecessor merges. The brief skeleton that worked eight times is in §8.

## 3. Cleanup is part of the lane, not the operator's morning

**Incident.** Eleven worktrees, twelve `wave3/*` branches, six `claude` background processes and
a page of `~/.serena/serena_config.yml` rows were left when the lanes finished. Every removal hit
`Permission denied` because the **done** session's process still held its worktree folder;
`claude stop <id>` on the done session released it every time. Serena appended a `projects:` row
per worktree and never removed one.

**Proposal.** A `postMerge` step, on by default: `claude stop <sessionId>` once merged (the session
is done — this is not "stopping a working session"), `git worktree remove` + `git branch -d`,
prune the Serena `projects:` row, and archive the worktree's `output/` artefacts the config names
(`archiveDirs`) into the main checkout before removal. A `-Cleanup` flag runs the same for lanes
that finished before the flag existed.

## 4. Verify on a pinned commit, never on the moving main checkout

**Incident.** A full suite started from the main checkout ran 3 h 40 min while five merges
fast-forwarded the checkout under it: 18 red, of which **9 were artefacts of the moving tree**
(modules imported before a merge, tests collected after it). Re-run on a worktree pinned to one
commit: 9 red, two real roots. Separately, every lane's DONE note recorded "a consolidated
`pytest tests` run on an idle box is still owed" — six sessions, the same sentence.

**Proposal.**
- The runner's guards already run inside the worktree (good). Add a **final-suite lane**: a
  configured session (or a plain runner step, no agent) with `dependsOn` every other session, that
  cuts a worktree at the merged base HEAD, runs the full suite there with the venv the
  `postWorktree` step built, and writes the result beside `state.json`. Nothing else ever runs the
  full suite.
- Document the failure mode in `SKILL.md` §7: "never run a long suite in the main checkout while
  lanes merge into it."

## 5. Shared resources need exclusion, not only ordering

**Incident.** `dependsOn` orders sessions; it does not stop a store-reading session from starving
a store-writing one (a single-holder HDF5 store: a shared reader blocks the exclusive writer).
I coordinated by hand: Session C messaged "store writes finished" and I ran the read-only evidence
job in that gap. The orchestrator's own long reads (a report build) had to be timed against a
running pass by reading progress files.

**Proposal.** A `resources` block: each session lists what it holds (`["store:write"]`,
`["store:read"]`, `["cpu:heavy"]`); the runner does not start a session whose resource conflicts
with a running one (write excludes everything; read excludes write). This is the lane primitive
"put contenders in one lane" made explicit, and it survives the orchestrator adding lanes at
midnight.

## 6. The box is one budget: lanes, subagents and the compute pass share it

**Incident.** The ten-worker pass (a measured ceiling for this machine) plus two Opus subagents
plus two lanes running suites pushed a headless-browser PDF test past its 180 s budget twice, and
F4 reported a `tests/app` run stalling for ten minutes that passes in 30 s alone. Every session
counted its *own* subagents against a cap of two; nobody counted the whole machine.

**Proposal.** A `machineBudget` in the config (`maxWorkers`, read by the runner and rendered into
every brief) and a rule in the brief template: *before a run longer than a minute, read the
runner's `load.json` (workers alive, lanes alive, CPU %) and defer if over budget.* Cheap to write
(`Get-Counter`, process count), and it turns "the box was busy" from a recurring excuse into a
number.

## 7. Traps travel with the brief

**Incident.** Four traps cost real time today and were each discovered by one session and
re-discovered by another: Serena's `replace_symbol_body` **drops decorators** silently (F4);
**PowerShell variable names are case-insensitive**, so `$doneFile` *is* the `-DoneFile` parameter
(the chain launcher, twice in one day, once killing a ten-worker launch); `pwsh -File` hands an
**array argument to a child as one string** (the same script); a monkeypatch on a **re-export
facade reaches external callers and nothing else** (F7 corrected F6's seam rule after four broken
tests). None is repo-specific.

**Proposal.** A `traps` list in the config, rendered into every brief under "measured traps — do
not re-measure", seeded with these four and with the skill's own array-unrolling note from §8.
When a DONE note reports a new one, the orchestrator appends it; the next brief carries it.

## 8. The re-cut brief skeleton (what a DONE note turns into)

Every successor brief today had the same six blocks, and the sessions that received them lost no
time orienting. Worth making the default `briefTemplate` shape for a *successor* session:

```
# Session <N> — <one line: what this pass is>
*Read <previous DONE note> in full first — above all <the one finding it must not re-learn>.*
## Start            worktree + junctions + import check + commit flags + refusal rule
## What is true now measured facts with the session that measured them; what other lanes own RIGHT NOW
## The work, in order   numbered; each item names the guard it ships with (pass AND fire)
## Not yours — write it down, do not do it   the operator's items, other lanes' files, the changelog
## Done means       DONE note contents: commits, remains, refusals verbatim, fallbacks
```

The "what other lanes own right now" line is the one that prevented conflicts: two Opus subagents
edited the report package in parallel with exclusive file lists and merged with one conflict, in
a file both were told to append to.

## 9. Smaller things, each from one incident

- **`Test-HandoffPermissionPrompt` is not the only silent stop.** A session that ends its turn
  without a DONE note and without a permission prompt (C, 17:45: "C ran past 6 h in one turn") is
  resumed with a nudge; when the DONE note already exists it should not be. (Same fix as §1.)
- **`claude logs <id>` on a live session hung once** (the orchestrator's 3-minute timeout fired
  while it was combined with a worktree removal). Prefer `claude agents --json` + the branch's git
  log for status; read logs only on a `done` session.
- **State the base-branch head at merge time in `state.json`** (`mergedInto: <sha>`). Twice I had
  to infer from `git log` which lane's merge had moved the base under my fast-forward.
- **`-EmitBriefs` for the controller too.** The controller session's own brief (this session's)
  lived outside the config; if the runner emitted it with the same variables (`stateDir`,
  `runnerLog`, the attach names), the controller could be restarted from the same source of truth.
- **Cross-session messages worked** (`SendMessage` to a lane by its `--remote-control` name,
  `notify_when_idle`). Say so in §4 of `SKILL.md`: the brief should give every session the
  controller's name and the two events to report — DONE written, shared resource released.
- **The auto-mode classifier refused nothing today in seven lanes** and refused `Stop-Process`,
  a repair dry run and one read in the controller. Record per-session refusal counts in
  `state.json` so the morning reader sees the pattern without opening eight DONE notes.

## 10. Two things I would not change

- **Resume, never restart.** Every resumed session today continued from its commits; nothing was lost.
- **Guards as the merge gate, changelog at merge time by the controller.** Eight lanes appended
  nothing to the shared changelog and the runner never had a changelog conflict; the controller's
  `changelog_append.py --expect-head` never refused a stale write because there was never a second
  writer.

---

## 11. Adopted for downstream project B, 2026-09-05 — the second real run, and what it cost

*Written by the downstream project B controller session (`project-B-d0`, Fable 5.1) while the run was
still going: eleven sessions in five lanes, one guards-only final suite, launched 07:20. Every item
below was measured in `runner.log`, `state.json` or a DONE note that day.*

| § | what happened | built / documented | where |
|---|---|---|---|
| 11.1 | `-Lanes 'A' 'B' 'C'` through `Start-Process` bound lanes 2–4 to `-Sessions`, `-OutFile`, `-WaitUntil`; one lane ran alone in-process | `-Lanes "A;B;C"` one-token form; smoke test | `c6f1e28` |
| 11.2 | a tracked placeholder (`data/.gitkeep`) made git create the directory before the junction; the old "link if absent" skipped the live-data link silently | placeholder-only directories are replaced, real content is refused and logged; per-session `linkDirs`/`copyDirs`/`copyFiles` so ONE lane sees the live data | `cfe7883` |
| 11.3 | a session's guard went red on its own new test file and its DONE note: `git grep` sees the committed tree, the session tested the working tree | a config `trap`; the adopting repo's guard was rewritten | config |
| 11.4 | a stop marker beside a DONE note stopped the lane before start instead of going to the guards, contradicting §3 | pre-start check honours the DONE note | `36d4b5d` |
| 11.5 | two runners starting in the same second shared `preflight-auth.json`; the queued lane's child died on the file lock before logging anything and was missing for 1 h 45 min | per-process probe file; the parent now logs every lane child's exit code | `fd18fd4`, this commit |
| 11.6 | the controller folded a session's changelog paragraph into `CHANGELOG.md` while the one session allowed to edit that file was still unmerged: conflict | rule in the adopting plan: fold nothing until the changelog-editing session has merged | plan README |
| 11.7 | `app/main.py` (router registration) conflicted on three of nine merges — every lane that adds a page includes a router there; "lanes never share a file" missed the registration point | documented below; proposal: name the registration files in the plan and pre-assign their insertion order, or make registration a per-module table | this file |
| 11.8 | three hand-resolved merges each failed the final-suite lane's dependency check the moment the terminal state appeared; FINAL was re-queued three times | `guards-red` and `merge-conflict` are waited on by dependents; terminal stays terminal | `60a5fc0` |
| 11.9 | Serena's `uvx`-launched MCP server missed the 30 s startup timeout in three of nine sessions under load; the sessions fell back to grep and finished without loss | adopting repo: `.claude/settings.local.json` with `MCP_TIMEOUT=120000`, copied into every worktree by `trust_worktree.py` | config |
| 11.10 | two DONE notes carried a family's full names, kit codes and lab-document names; one reached the public origin before the controller read it | brief-template rule (counts, hashes, slugs only), a redaction tool with a gitignored map, a shape-based guard test; **the controller must run the guard on a merged note before pushing** | adopting repo |
| 11.11 | full-suite wall time varied 6:54 → 53:21 for the same suite as one to four lanes ran suites concurrently; a 120 s job-wait test with a 22 % idle margin failed in three sessions' own runs and passed alone every time | measured; proposal: a `resources` entry for "the suite" so at most N guard suites run at once, and a `machineBudget` that briefs actually read | this file |

**11.7 in detail — registration files are shared files.** The adopting plan's lane rule said
lanes never share a file, and the briefs honoured it for modules, tests and migrations. Every lane
that adds a page still had to add `from app.routes_x import router` and `app.include_router(...)`
to `app/main.py`, at the same two lines, so L1 vs D2 and then A2 vs both conflicted. The fix was
trivial each time (keep both), but it stopped a lane and cascaded into 11.8. When writing briefs,
name every registration point (`main.py` includes, a capabilities registry, an i18n catalogue, a
migration index) and either give each session its own line by position or move registration into
a per-module table the app discovers.

**11.11 in detail — the suite is a resource too.** Four sessions ran the full suite before their
DONE notes, the runner ran it again as each guard, and the controller's own merged-tree run made
it nine full suites in five hours on one host. The suite's own duration went from 6:54 (idle) to
53:21 (four concurrent), and its one timing-sensitive test failed whenever more than two suites
overlapped. `resources: ["suite"]` on every session would serialise the guards; the sessions'
pre-DONE runs are the brief's to cap ("run it once, and read `load.json` first").

## Observations from the project C v3 run (2026-09-10 → 2026-09-12, controller Fable 5.1)

| # | observation | status |
|---|---|---|
| 11 | **Only the parent may reconcile the ledger at startup.** A lane child that reconciles races every other lane's resume (old id dead, new one not yet registered) and stops dependents on a false "ended without merging". Fixed: `if (-not $DryRun -and -not $LaneName)`. | **built** (2026-09-11) |
| 12 | **`Get-BgEntry` returns a PSCustomObject for `claude agents --json`** (no `.ContainsKey`); the session-start path crashed one second after every launch. Fixed with a duck-typed read. | **built** (2026-09-10) |
| 13 | **The reconcile must consult the launcher's live list** — the claude launcher records no pid, so pid-based liveness judged every session dead. `-AliveBgIds` from the list action. | **built** (2026-09-10) |
| 14 | **`trust_worktree.py` races on `~/.claude.json`** when three lanes start at once (`os.replace` → PermissionError); the loser's session starts untrusted and goes `blocked`. Retry loop with jitter. | **built** (2026-09-10) |
| 15 | **A session blocked on the trust dialog cannot be `--resume`d** — the runner started a copy that exited at once, six times. Start fresh when the previous turn ended `blocked` with no commits. | open |
| 16 | **Usage-limit stalls burn the resumes.** After a `limit` turn the runner waited 30 min once, then resumed six times at 2-min intervals while the limit still held, then "proceeded to the guards anyway" (four lanes, 2026-09-11 22:38). Back off on every turn that ends `blocked` right after a limit; treat a DONE-note-less session that hit its cap as *paused*, not finished. | open |
| 17 | **A lane re-queued with a DONE note already present should skip the session** and go straight to guards; today it resumes the session (token cost, and a fresh usage-limit exposure) unless the operator drops a `stop-<KEY>` marker. | open |
| 18 | **Shared append-only files conflict on every merge** (`QUESTIONS-FOR-OWNER.md`, `timings.csv`): give each lane its own file and let the controller union them; the template should say so from the start. | **built** in project C's template; the skill's example template should carry it |
| 19 | **Queue pickup latency is bounded by the parent's wait on lane children**, not `pollSeconds` (measured 1 h 50 min). | open |
| 20 | **`git checkout -- <path>` is unsafe as a Rule M revert while the lane's own edits are uncommitted** — it reverts to the last commit and takes the legitimate edit with the mutation (bit two lane-RA subagents). Recommend byte-exact backup + whole-file restore verified by hash, or commit before mutating. | open (brief wording) |
| 21 | **LF files in a CRLF tree** (`core.autocrlf = true`) make exact-match edit helpers report "0 occurrences" for text plainly present. Normalise before matching. | open |
| 22 | **Working-window scripts** (`window_stop.ps1` / `window_start.ps1` + Task Scheduler) let a shared host stay free during the owner's hours; the stopped sessions resume with context at the next window. | **built** (2026-09-11) |

## Observations continued — project C v3 run, 2026-09-12 → 13 (successor controller, Opus 5)

| # | observation | status |
|---|---|---|
| 23 | **A lane holds its declared resources through its guards and merge, and a guards-only re-queue re-acquires them** — C waited ~1 h on RA's `db:write`; M's guard re-run waited on K's `perm:write`. Right for scoring locks; a `guardsResources: []` override would stop guard runs queueing behind sessions. | open |
| 24 | **A lane child captures `dependsOn` at launch** and afterwards re-reads only `state.json`, so a config change never reaches a waiting lane (lane19 kept waiting for M after the owner removed M from K's deps). Re-read `dependsOn` on every poll. | open |
| 25 | **`stop-<KEY>` is keyed by session, not lane**, so it cannot retire one of two lanes holding the same key. It does retire a stale *waiting* lane cleanly, because its check runs after the dependency wait and before the worktree is cut (lane11, 2026-09-12 20:15: no worktree or branch left behind). Add `stop-lane-<name>`. | open |
| 26 | **An interrupted background session is invisible to the runner.** SX was interrupted ~100 s after launch and sat at "What should Claude do instead?" for the full 8-hour `maxSessionHours`, because liveness comes only from the agents list saying `working`. Detect the interrupt prompt in the session log, or a transcript whose last entry is `[Request interrupted by user]` with no later activity, and resume within minutes. | open |
| 27 | **A DONE note can claim a task it did not do.** Lane K's note said "K.3 in full", was committed before K.3's own commits, and two required artefacts were missing; the runner treats the note's existence as completion. Check the brief's done-means artefact list against the tree before the guards run. | open |
| 28 | **The template's cross-family review recipe told Gemini to read a diff at a path**, which headless `agy` refuses ("a tool required the command permission"); four lanes rediscovered the fix independently. Forbid tools in the prompt, inline the diff (≤ ~23 KB per call), build the prompt in a file and pass `agy -p "$(cat file)"`. | **built** (project C template 736c1ee) |
| 29 | **Sibling-repo definition drift reddens every lane's guards at once** — downstream project A redefined `burst_fraction` mid-run and two import-parity tests failed on upstream's own value. An era-aware guard (ours pinned in every era; agreement or divergence asserted by the detected era) beats a skip, which would let our own regression pass green. | **built** (project C 9555482) |
| 30 | **The auto-mode classifier refuses `taskkill` on an idle waiting lane child** ("Interfere With Workloads") until the owner authorizes it in conversation. A runner-native lane retirement (25) removes the need. | open |
| 31 | **Controller tooling: the harness Bash tool truncates a command above roughly 5 KB**, silently breaking a long quoted heredoc ("unexpected EOF while looking for matching"). Write long files in appends of ~40 lines. | open (harness) |
| 32 | **`Remove-Worktree` can leave an empty directory behind** (a locked handle at removal time): three `project-C-v3-*` folders with no `.git` and 0 files survived their merges for four days. After `git worktree remove`, verify the directory is gone and retry the delete with a short backoff. | open |
| 33 | **A DONE note that says "STATUS: IN PROGRESS" was treated as done.** Lane D's note carried an explicit in-progress banner and an unfilled §6; the runner saw the file exist, ran the guards and merged, and D.2d (the report build) never happened. A DONE note needs a completion marker the runner checks, not just a path that exists. | open |
| 34 | **A "Stop" typed into a lane session is indistinguishable from an early stop to the runner**, which resumes the lane; lane GF resumed, correctly refused to restart work the operator had stopped, and wrote a note saying so — which the runner then merged as if the task were done. The operator's stop should set a marker the runner honours, and a note that declares "task not performed" must not count as done (same root as 33). | open |
| 35 | **The brief's `load.json` gate did not enforce from Python** because a wrapper opened it through a Git-Bash `/c/...` path Windows Python cannot read. Render the Windows path into every brief and say "open it from Python with this path". | open |
| 36 | **Sequencing a controller-level cross-family review before commit pays for itself** — on this run it caught an invalid family-wise correction in a brief, a one-directional guard in a hotfix, two single-step/unenforced escape routes in a hand-derived test, and an FWER/power misstatement in a results paragraph. Make "review the artefact you just wrote with the other model family" a standing step for the controller, not only for lanes. | **adopted** (project C R-v3-32) |
| 37 | **`run_handoff_sessions.ps1 --resume <id>` of a *stopped* session starts a copy instead of resuming it, and the copy may exit immediately.** Reproduction: resume a stopped session by id. Observed 2026-09-17: three resumes of RB1 exited immediately; RB2 and W1 resumed correctly (once each). Cause not established. | open |
