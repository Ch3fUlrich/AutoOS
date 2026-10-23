# AO-WORKST-RUNNER-POOL + CI-WORKST-RUNNER + CI-SPEED-WORKST lane record (2026-10-23)

Lane `lane/ao-workst-runner-pool`, PR #104. This record answers the judge CHANGE of gen6 and the c0a6a6 findings.

## Seats (tool-less, fresh, families different from the cx/openai writer; the pre-existing pool code was deepseek-authored, so deepseek is not an independent seat for it)
| round | sha | seat | part | verdict |
|---|---|---|---|---|
| 1 | cd52c779 | deepseek-direct-flash | admission / pool | ACCEPT / REJECT (kept as evidence only; family not independent for old pool code, then withdrawn D-2449) |
| 1 | cd52c779 | Claude Haiku 5.5 | admission / pool | ACCEPT / REJECT |
| 2 | 7e960334 | qodercli qwen3.8-flash (served "efficient") | admission / pool | ACCEPT / ACCEPT |
| 2 | 7e960334 | Claude Haiku 5.5 | admission / pool | HOLD / REJECT |
| 3 delta | 96f24607 | Claude Haiku 5.5 | delta | ACCEPT (5 MED findings, all fixed in the final round, D-2472 no re-seat) |
Evidence: docs/plans/evidence/2026-10-23-runnerpool-seat-round{1,2,3}-*.md; attacker briefs `2026-10-23-runnerpool-attacker-brief-part{1,2}-*.md`.
Real defects the seats found and the lane fixed failing-first: ssh-gate route bypassed run_gate; run_one ignored claim(); kill/status/_find_slot broken; kill_tree was a no-op reporting success; zero-available-memory treated as unreadable; UTF-16/list2cmdline argv length; NTFS stream and invalid path chars; MCP and pool admission kind; kind handover with `--registry`; commit ceiling bounds; schema declared `kinds`/`by_platform`; slot lock and done/ crash window; write_json_atomic result ignored; client-less cards looping.

## Known limits (valid ONLY while `host: workstation` dispatch stays off)
Windows Job Object path is not wired into popen_spawn (create_job_nt/spawn_in_job unexecuted); kill by pid vs TerminateJobObject, bInheritHandles inheritance, ActiveProcessLimit, NTFS case collision of submitters/<name>.count, env allow-list. The api client job writes its prompt/output under the pool spool with the job account (design question for the live step). Live scoped spawn (R-orch-19), Scheduled Tasks, authorized_keys line evidence and the >3-parallel measurement wait for the workstation live step.
`AUTOOS_ADMISSION_OFF` / `AUTOOS_MEMINFO_PATH` are pre-existing test escapes in the spawner's own environment.

## Flaky shard b (run 38815561335, job 116699272432)
The failing test name was hidden by CI's `tail -n 20`; suspect `InProcessEquivalenceTests.test_in_process_matches_subprocess` (agy probe yes vs broken). Its signin-probe stub fix (92342160) was already in that run, so the cause is **suspected, unproven**. The spawner CI section now prints the `FAIL:`/`ERROR:` lines before the tail so the next failure names itself. Also seen under host load: `tests/test_runner_pool_core_c.py` failed once in ~10 local runs and passed in the next six (timing-sensitive, file untouched this round; unproven).

## Writer table (per code commit; `git log origin/main..HEAD --no-merges`)
| commit | subject | writer model |
|---|---|---|
| 7f95a9af | fix(admission): recheck final client kind and count corrupt workers conservatively | omniroute/cx-gpt-6.1-sol 20261023-191628-final-round-b-admission-e5bbca |
| 7ffd1e4d | Fix pool admission quarantine and stale slot lock recovery | omniroute/cx-gpt-6.1-sol 20261023-191625-final-round-a-pool-clien-95e0fa |
| 3d2e8ac7 | AO-WORKST-RUNNER-POOL: seat evidence rounds 2 and 3 | unrecorded (docs/design commit) |
| 96f24607 | AO-WORKST-RUNNER-POOL: three pool tests follow the round-3 behaviour (slot-unreadable, api | unrecorded (docs/design commit) |
| fd39fe19 | Fix independent admission memory readings and final-client kind | omniroute/cx-gpt-6.1-sol round3-w4-admission-zero-7eb692 |
| da9dfd6d | fix(pool): protect slot ownership and preserve claims on write failure | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-190507-round3-w3-runner-pool-ki-bae521 |
| 10095f4a | fix(pool): reject lost submissions and harden gate input/status | omniroute/cx-gpt-6.1-sol 20261023-190452-round3-w2-gate-write-res-43a2b9 |
| df290ad5 | Fix runner cancellation outcomes and lazy typed Windows process APIs | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-190447-round3-w1-proc-kill-tree-66f0b3 |
| e2cca3f7 | fix(admission): fail closed on malformed kinds and sanitize memory | omniroute/cx-gpt-6.1-sol 20261023-184354-admission-consolidated-r-edc149 |
| 13bc08cf | Fix runner pool admission, model validation and API confinement | omniroute/cx-gpt-6.1-sol 20261023-184346-pool-consolidated-round-f0ff82 |
| fb38c726 | fix: classify MCP spawn client before host admission | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-152745-mcp-spawn-preflight-pass-23239f |
| a5664d32 | Fix pool UTF-16 argv validation, claimed-job failures and CLI state | omniroute/cx-gpt-6.1-sol pool-argv-utf16-spawn-er-d85702 |
| a322f440 | fix(runner-pool): reject NTFS streams and invalid path characters | omniroute/cx-gpt-6.1-sol gate-reject-ntfs-stream-f02e1e |
| 8cab47c2 | fix: preserve admission kind at worker handover and bound commit ceiling | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-152013-admission-kind-handover-edd79f |
| 05372fbe | fix(pool): validate SSH submissions and execute only claimed jobs | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-151954-pool-ssh-gate-delegates-416440 |
| fa4af63f | AO-WORKST-RUNNER-POOL: seat round 1 evidence, haiku part 1 full text | unrecorded (docs/design commit) |
| 8ba71c4f | AO-WORKST-RUNNER-POOL: seat round 1 evidence (part 1: 2 ACCEPT; part 2: 2 REJECT) | unrecorded (docs/design commit) |
| 1ed56ed5 | AO-WORKST-RUNNER-POOL: scrub username paths from the pool handoff; seat round 1 evidence ( | unrecorded (docs/design commit) |
| cd52c779 | AO-WORKST-RUNNER-POOL: changelog | unrecorded (docs/design commit) |
| 14c9936a | Add repeatable median-based CI concurrency timing tool | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-135330-ci-parallel-vs-single-me-eced83 |
| 30abd0e4 | docs: add admission and runner pool attacker briefs | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-135328-attacker-briefs-for-runn-f125a9 |
| c1f20012 | fix(ci-runner): defer slot restart to supervisor configuration | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-114434-restart-slot-defers-to-s-b5a76b |
| c18fdd9f | fix(ci-runner): forward repository cache scope through slots | omniroute/cx-gpt-6.1-sol 20261023-114245-c42aba |
| 931e95d5 | AO-WORKST-RUNNER-POOL: spawner CI section names failing tests (FAIL/ERROR lines before the | unrecorded (docs/design commit) |
| 551a73d0 | fix(ci-runner): isolate cache volumes by repository scope | omniroute/cx-gpt-6.1-sol ao-workst-runner-20261023-113501-per-repo-ci-cache-volume-b1c98c |
| 96480058 | AO-WORKST-RUNNER-POOL: admission cap/floor >= 1 per field, win32 unreadable stands down (m | unrecorded (docs/design commit) |
| 296ad2c1 | AO-WORKST-RUNNER-POOL: handoff gen6 (judge CHANGE: fresh seats + attacker briefs, writer t | unrecorded (docs/design commit) |
| 973aee7f | AO-WORKST-RUNNER-POOL: prepush fixes (slot test __main__ guard for pytest, handoff ready-c | unrecorded (docs/design commit) |
| ccfdf1d6 | AO-WORKST-RUNNER-POOL seat C fixes: card.paths confinement, case-insensitive strip_env, re | Claude Sonnet 5.5 (orchestrator; gate/core lines of ~53 code lines, D-1272 note in handoff gen6) |
| daebe00b | AO-WORKST-RUNNER-POOL: packet gen4 + seat evidence (qwen, llama) | unrecorded (docs/design commit) |
| e23f81e1 | AO-WORKST-RUNNER-POOL: handoff gen4, follow-up item for workst-L1-autoos | unrecorded (docs/design commit) |
| 57937b11 | AO-WORKST-RUNNER-POOL: Deps.risk_of defaults fail-safe (R2; R0 only for tool-less api jobs | Claude Sonnet 5.5 (orchestrator) |
| edbcfe44 | AO-WORKST-RUNNER-POOL D-1349: host admission floor 1 GB MemAvailable (top-level + toolless | Claude Sonnet 5.5 (orchestrator) |
| 7f0347db | AO-WORKST-RUNNER-POOL W4: spawner matrix tests know the api client (reds 1-3) | Claude Sonnet 5.5 (orchestrator) |
| b2de3169 | AO-WORKST-RUNNER-POOL: handoff fix | unrecorded (docs/design commit) |
| 38cca458 | AO-WORKST-RUNNER-POOL W4: api client (tool-less review-call wrapper) + unmocked e2e test;  | tool-less writers + Claude Sonnet 5.5 orchestrator edits |
| fce170d3 | AO-WORKST-RUNNER-POOL: handoff gen3 (uncommitted W4 state, 4 spawner reds listed) | unrecorded (docs/design commit) |
| a80a6fb6 | AO-WORKST-RUNNER-POOL W1: host admission by kind (toolless 4/3072), api client row, kind d | writer run 20261015-152819 (cancelled run, working-tree diff; model per its logs, unrecorded here) + Claude So |
| d6848f91 | AO-WORKST-RUNNER-POOL W2: runner pool core/gate/proc/slot loop/CLI (tool-less writers, fai | deepseek-v4.1-flash (some files ovh/gpt-oss-120b), orchestrator edits |
| 4ce166d9 | AO-WORKST-RUNNER-POOL: handoff push status (prepush red on main files) | unrecorded (docs/design commit) |
| 9f0c03c7 | AO-WORKST-RUNNER-POOL: L2 handoff (context cap) | unrecorded (docs/design commit) |
| 7c7e8c12 | AO-WORKST-RUNNER-POOL W3: unit_breaker Windows branch (schtasks /End + /DISABLE), failure- | tool-less writer (W3 unit_breaker), unrecorded model |
| 1c4df437 | AO-WORKST-RUNNER-POOL: design note v5 question 2 text matches the single authorized_keys l | unrecorded (docs/design commit) |
| ccc06430 | AO-WORKST-RUNNER-POOL: design note v5 (final-seat items 1-10) | unrecorded (docs/design commit) |
| 42fa157e | AO-WORKST-RUNNER-POOL: design note v4 (muse seat 1 deltas a-f) | unrecorded (docs/design commit) |
| 947a93fd | AO-WORKST-RUNNER-POOL: design note v3 (seat 2 findings 1-12 folded) | unrecorded (docs/design commit) |
| fc3848b8 | AO-WORKST-RUNNER-POOL: design note v2, Windows-native pool (Task Scheduler, Job Objects, c | unrecorded (docs/design commit) |
| 19132e4d | AO-WORKST-RUNNER-POOL: design note (draft for spec review) | unrecorded (docs/design commit) |

## CI speed (CI-SPEED-WORKST), measured 2026-10-23
- Docker Desktop VM: 32 CPUs / 64 GB. Slot table after operator order D-2441 (sum of container memory limits 48 GiB): autoos 3 @ 6g, gen-analysis 2 @ 6g, routing/omniroute/basic-analysis/ventu/tools/chaptarr 1 @ 3g each. Per-slot CPU cap 8 (D-1425). Pool of 6-8 slots per repo is not possible under the 48 GiB cap (operator order).
- Measured peaks (docker stats, 5 s sampling): autoos 3552 MiB, gen-analysis 3143 MiB; no repo job exceeded its limit.
- Cache volumes are per repository (`ci-cargo-registry-<prefix>`, `ci-cargo-git-<prefix>`, `ci-sccache-<prefix>`), no volume is shared between repositories. Temporal CLI 1.9.1 is baked into the image; the postgres:16 service image is pre-pulled into the host daemon (digest pinned in the install notes); a derived baked image adds nothing because service containers run on the host daemon through the mounted socket.
- 1.2x table (AutoOS ci.yml via workflow_dispatch on a throwaway branch, 3 rounds, median parallel/median single; parallel = 3 runs at once because autoos has 3 slots): max ratio 1.085, median ratio 0.90, jobs over 1.20: 0. Run ids single 39978924017 39984465544 39989727742; parallel 39980691231 39980695548 39980699609 39985904559 39985909242 39985914739 39991962347 39991966888 39991970546. Raw rows and table: tools/ci-runner/measure_speed.py output (job, single s, parallel s, ratio): Ansible lint 52/51/0.98, Linux suite lint 59/64/1.08, shard a 54/49/0.91, shard b 601/480/0.80, shard c 119/114/0.96, shard d 68/61/0.90, shard e 221/211/0.95, shard f 153/135/0.88, shard g 44/39/0.89, Public scrub 11/10/0.91, Secret scan 10/9/0.90.
- plangraph runners are NOT registered and plangraph CI stays on GitHub-hosted runners (D-1718, D-2418).
- A private-repo run on the new supervisor: gen-analysis run 39990374581 (success, jobs on workst-gen-analysis-1/2 at 6g, push 2026-10-23 16:52Z).
