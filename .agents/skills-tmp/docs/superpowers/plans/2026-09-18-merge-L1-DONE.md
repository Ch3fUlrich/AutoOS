# L1 DONE note: merge agent-skills into AutoOS (session 2026-09-18)

**Status: living note, updated as lanes land.** Last update 17:30 CEST. Phase 0 is nearly done
(INST at its guards, HARN queued behind it). Phase 1 is running. Claude weekly budget: 89% used
(measured with `get_usage`), resets 2026-09-24.

## Commits landed

| Repo | Commit | What | Evidence |
|---|---|---|---|
| AutoOS | `bc5ae3a` merge/mcp | Task 0.9b repo part: one pinned omnigraph entry in `.mcp.json` (`@0.8.0`, `${OMNIGRAPH_BASE_URL:-http://localhost:8080}`) | guard GREEN: Windows 263/0, Linux 299/0; Sonnet review PASS |
| AutoOS | `23066d2` merge/memories (`6417bc6`, `f1a84b0`) | Task 0.7 docs lines from the retired Serena memories | guard GREEN; DeepSeek review (2 flags, both fixed); Sonnet review PASS (every claim checked against the code) |
| agent-skills | `160452a` (`37d2f30`) | Operator work rules (DeepSeek first / Claude closes / reviewer ≠ writer; piecewise + data ladder) and the proposed `references/l3-routing.md` | pwsh 7/7 green; pytest unattended 507/13 skipped; swarm 61; infra 44 |
| agent-skills | `4c441fe` merge/licences (`352d89e`) | Task 1.6 `THIRD_PARTY.md` | guard GREEN |
| AutoOS (branch, pending) | `b5679be` `2b108a8` `4b91d34` `060f039` merge/installers | Tasks 0.5, 0.6, 0.8, 0.9c (installers) | guard running |

## Host changes (no commits; all backed up)

- **0.9a:** the `~/.claude.json` omnigraph token literal is now passed by name
  (`-e OMNIGRAPH_TOKEN`). Probe: auth OK with the variable; `missing bearer token` without it.
  Backup `.bak-20260918-0.9a`. The backup still holds the old literal: the operator may delete it.
- **0.9b–e:** HOST lane; see `AutoOS-worktrees/AutoOS-host/logs/handoff/DONE-HOST.md`. Pins,
  removals, updates done. **Failed:** gh upgrade (the MSI rolled itself back; needs admin).
  **Deferred:** Git, pwsh, the npm globals (playwright/context7), the graphify uv tool, and the
  WSL Claude channel.
- omnigraph-server memory raised live, 1 GiB (+1 GiB swap) → 4 GiB (operator OK). Reason: the
  cgroup had hit its ceiling 316,585 times.
- `\Omnigraph Sync` scheduled task **disabled** (operator OK). Its in-flight run was left to
  finish.
- **D10 profile guard applied by hand** (operator OK) to `Documents\PowerShell\Microsoft.PowerShell_profile.ps1`,
  `Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1` and
  `Documents\WindowsPowerShell\profile.ps1`, each with a `.autoos-backup-*`.
  - Probe with `AI_AGENT=openhands`: 17.7 s → 0.57–0.67 s.
  - Original bytes verified unchanged after the prefix; both shells start normally.
- **OpenCode (WSL) harness fence:**
  - a `permission` block with 13 bash denies, and `skill: allow`;
  - `instructions` → coding-principles;
  - `~/.config/opencode/skills` → agent-skills/skills;
  - backup `.bak-20260918-harness`.
  - Verified: a leaf wrote its file, its commit was refused, and it listed the 15 skills.
- **User settings:** allow rules `Bash(wsl -e bash -lc *opencode run *)` and
  `Bash(wsl bash -lc *opencode run *)` (operator OK, after the refusal below). Backup
  `~/.claude/settings.json.bak-20260918-opencode-rule`. Verified in an auto-mode Haiku session.

## Refusals (verbatim)

- INST L2, command `wsl bash .../run-0.8.sh` (which runs `opencode run --model deepseek/deepseek-v4-pro ...`):
  "Permission for this action was denied by the Claude Code auto mode classifier. Reason:
  [Create Unsafe Agents]." Routed to the operator. Resolved by the scoped allow rule above.

## Incident: `autoos` graph lost nodes (see `memories_to_omnigraph.md`)

- Main lost the Project, 2 Decisions and 1 Component, with their edges. Commits
  `01M2T37YX78735JDD2EC88F82F` (11:08 UTC) and `01M2TAKAY5AYM29Y6JW6NEMB57` (13:16 UTC).
  The pre-session snapshot `01M2T2K3EARTYSTYMC7QV718FX` is intact.
- **Likely cause (inferred):** `\Omnigraph Sync`, which every 5 minutes pushes the local delta and
  then OVERWRITES the local graphs from central, concurrently with agent writes under memory
  pressure. An L1 `branches_merge` that returned `fetch failed` coincides with the second commit.
- **The operator froze all Omnigraph writes.** Every memory is held in
  `memories_to_omnigraph.md`. Open task: restore from the snapshot, then replay the file.

## Decisions taken, and why

| Decision | By | Why |
|---|---|---|
| D10 = profile guard; D3 = placeholders + `~/.config/autoos/site.env`; D4 = `~/.config/autoos/api_keys.conf` | operator | recommended options |
| D2 amended: nothing dropped, scrub then migrate; the Omnigraph viewer goes to its own **public** repo (local first; only the operator pushes) | operator | "no information should be lost" |
| 3 unlicensed skills (qa-swarm, review-triage, babysit-prs): clean-room rewrite; excluded until then | operator | all rights reserved upstream (S16, via `gh api`) |
| Free models allowed for private content | operator | explicitly accepted the data terms |
| Phase 1 based on agent-skills `main`, not a `prep/public` branch | L1 | the runner needs the main checkout on the base branch, and that checkout hosts the live skill; every task still merges only when its guard is green |
| Phase 1 after Phase 0 (not in parallel) | L1 | the operator's order; it also keeps nine Opus L2s off the quota at once |
| L2s: no Claude subagents; Muse/DeepSeek leaves; L1 runs the Sonnet reviews | operator | Claude usage |
| DONE notes in git-ignored paths (`logs/handoff/`, `tmp/handoff/`), copied here | L1 | they name host paths, and AutoOS is public |
| Guard requires the suite summary line, not only the exit code | L1 | INST measured `run-tests.sh` exiting 0 after a syntax error |

## Pools and windows

- L2s: anthropic/Opus, launched 13:02 (quiet window) and 16:25 (busy window: blocking work, not
  deferred).
- Reviews: DeepSeek v4 flash, off-peak all weekend (`provider_windows.py`).
- Research: 3 Sonnet/Haiku agents, 273k tokens. Sonnet reviews: 84k tokens.
- **L3 routes measured today** (tiny TDD task each, re-verified by L1):

  | Route | Result |
  |---|---|
  | `meta/muse-spark-1.3-contributor` (opencode) | 48 s, pass |
  | `opencode/muse-spark-1.3-contributor-free` | 57 s, pass |
  | `deepseek/deepseek-v4-flash` review | 41 s |
  | `openrouter/moonshotai/kimi-k3` | 46 s, pass, **paid**: not to be used |
  | `openrouter/nvidia/nemotron-3-ultra` / `-super` `:free` | 73 s / 59 s, pass. Later **closed** by the account privacy settings ("Free model training violation") |
  | `openrouter/meta/muse-spark-1.3-contributor` | first "18+ age confirmation" (the operator confirmed), then "Paid model training violation" |
  | `z-ai/glm-5.2:free` | fails: no endpoint supports tool use |
  | `laguna-s-2.1:free` | provider error |
  | `gemma-4-31b:free` | rate-limited |
  | `north-mini-code:free` | hit the fence and gave up |

## What remains

1. **INST:** guard → merge. **HARN** (the harness from one source, pin the floating MCP
   registrations, the `profile.ps1` guard, the `run-tests.sh` exit-0 hole) → merge → L1 Sonnet
   review. Then **FINAL** on AutoOS `main`.
2. **Phase 1:** S11 → S12/S13/S14 → S17 → S15 → FINAL. L1 runs a Sonnet review per merged lane.
3. **Operator:**
   - OpenRouter privacy toggles, if the free Nemotron / OpenRouter Muse routes should work;
   - gh reinstall (admin);
   - the deferred updates (Git, pwsh, npm globals, graphify tool) once no lane runs;
   - rotate the context7 key (printed once in the HOST transcript);
   - delete `~/.claude.json.bak-20260918-0.9a` (it holds the old token);
   - the WSL Claude channel.
4. **Omnigraph:** restore `autoos` from the snapshot, fix or retire the sync's overwrite
   direction, lift the freeze, replay `memories_to_omnigraph.md` (plus the lanes' "memory to
   write" sections), then 0.7 step 4 (delete the Serena memory files: operator OK).
5. **Approval of `references/l3-routing.md`**, then its implementation (scorer, catalogue fields,
   an OpenCode launcher adapter).
6. **OpenHands Muse L2 experiment:** the probe is fixed; `enable_sub_agents` is still off
   (HARN generates it).
7. The runner's hard-coded MCP paragraph (`Build-HandoffMcpPolicy`) names graph
   `'<RepoFolder>'` (case) and a `skills/repository-index` path that AutoOS lacks. Fix it in
   Phase 6.
8. Phases 2–9 per the plan; D1 (layout) gates Phase 2.
