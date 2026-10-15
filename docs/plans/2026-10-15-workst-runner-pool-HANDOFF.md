# HANDOFF fixes-L2-runnerpool (item autoos:AO-WORKST-RUNNER-POOL), written 2026-10-15 ~16:35Z

Brief: /home/s/code/routing-worktrees/fixes-L1/docs/plan/briefs/fixes-L2-runnerpool-brief.md. Report only to fixes-L1 (SendMessage). Worktree + branch: lane-workst-runner-pool / lane/ao-workst-runner-pool. TTL 24 h from 2026-10-15 ~14:50Z. Never merge, no PR, push only this branch.

## Done
- Design note v5 (GO'd by fixes-L1 after 3 spec seats): docs/plans/2026-10-15-workst-runner-pool-design.md @1c4df437e. Windows-native workstation; N=6; per-kind admission; api client; ssh-gate.
- W3 unit_breaker nt branch: taken as 7c7e8c122 (tests/test_unit_breaker.py 32 green). Caveat for code seats: failure-class signature only on the nt path (AUTOOS_OS_NAME env seam), Linux keeps old signature.
- Heartbeat cron 7-59/10 is session-only: recreate after relaunch (R-coord-07).

## In flight / pending
- W1 admission-by-kind (GATE change R-orch-16, tool-using opencode run, allowed to finish): run 20261015-152819-runnerpool-w1-admission-574ddb, sandbox under logs/sandboxes/. When done: inspect diff, `git fetch <sandbox> agent/<runid>` and cherry-pick ONLY the WIP commit (not the range; the range includes the sandbox base commit), run filtered tests (`-k Admission`), test_registry_render.py, `tools/registry.py validate`, renderer --check. Do not move the lane branch while a writer runs (LEAK flag, R-coord-01).
- W2 first cut (20261015-152724) REJECTED: pass-stub tests, simulated job. Do not take it.
- W2a pool core (tools/runner_pool_core.py + tests/test_runner_pool_core.py): spec in /home/s/.claude/jobs/0d77494d/tmp/w2a.md (tool-less variant w2a-prompt.md). Nemotron-super tool-using run ignored the brief; pinned model id `l3-driver-1m` is invalid for opencode.
- Then W2b (gate: submit + ssh-gate + validators), W2c (kill + run.private + Job Object nt), W2d (CLI/slot/status), W4 (`api` registry client wrapping review-call.py, shell/write false, model allowlist, pool-dir paths; do after W1: same registry file).

## New rule from fixes-L1 (D-437/D-1317)
On coding.vm only TOOL-LESS L3 from now: POST brief + pasted files via `python3 tools/autoos_gateway_key.py exec --no-notice /home/s/code/AutoOS/configuration/api-keys.yml -- python3 tools/review-call.py --model <m> --prompt-file P --out-dir D --title T --max-tokens N`, keep run_id/evidence.json, apply returned files myself (git apply / write), run tests failing-first (tests applied and run BEFORE impl), self-written code stays <=10 lines (D-1272). Free first: `bzl/deepseek/deepseek-v4-flash-0731free:free`.
- First tool-less attempt (W2a, 32000 max tokens) hit review-call REQUEST_TIMEOUT_SECONDS=300 -> rc 4, no output. Fix: smaller prompts (one function group per call, <= ~8000 max-tokens) or a faster model; tools/review-call.py timeout is a constant at line 100 (do not edit; a change is a tool change).
- Key file path argument is the main checkout's configuration/api-keys.yml (worktree has none); never read it (R-orch-27).

## Machine notes
- Spawner admission refuses at MemAvailable < 4608 MB (rc 13): a launch right after another is often refused; wait with a Monitor until >= 4900.
- `autoos-agent ps` output is huge: use `status <run_id>` / `result`.
- `route` card dialects: v1 (role/complexity/privacy/spend) and v2 (kind/paths/spec/task_type) must not be mixed.

## Remaining to brief done
integrate W1,W2a-d,W4 -> unmocked Popen test + one live scoped spawn (R-orch-19) -> `tools/prepush.py` green -> push lane -> CI green (cite run id) -> 2 non-Claude code seats, different families, on the admission change (+ attacker brief: weakened admission/memory guard, kill path reading job.json, runner escape; seat-model-evidence) -> packet to fixes-L1: lane sha+tree, CI run id, prepush record, per-item diff summary, seat run ids + answer/evidence sha256, live-step packet (Scheduled Tasks AutoOS-Pool-<n>, pool-slot/pool-job accounts, authorized_keys verbatim line + evidence Windows OpenSSH honours command=, >3-parallel measurement), questions.

## Push status (16:40Z)
Lane NOT pushed: prepush refused, 2 reds on files this lane does not touch (pre-existing on origin/main, the lane-main-green fix): test_suite_wiring (test_benchmark_toolcalls.py, test_sync_openhands_hdi.py unwired) and test_windows_portability BOM (lib/windows/AutoOS.Install.psm1, tests/run-tests.ps1). Re-merge origin/main once main-green lands, then prepush + push. No override used.
