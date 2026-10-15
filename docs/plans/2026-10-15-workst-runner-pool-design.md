# AO-WORKST-RUNNER-POOL - design note (fixes-L2-runnerpool, D-1317)

Status: DRAFT for cross-family spec review (R-orch-13). No code before it passes.

## Problem
One workstation runner session queues beyond ~3 parallel L2 jobs, and workst-L1 reviews every job.
Separately, spawner host admission (`catalog/ai-registry.json` `host_admission`, 6 workers / 4608 MB)
is one pair for every run, so D-1317's coding.vm room for tool-less runs (4 / 3072 MB) cannot be
expressed without loosening tool-using runs too.

## A. Runner pool (new `tools/runner_pool.py`, stdlib + ctypes, Windows-first)
Workstation = WINDOWS (judge finding; projects.yaml paths, runner = `run_handoff_sessions.ps1`). Nothing
below uses systemd, /proc, killpg or ProtectHome. The spawner already carries the Windows primitives
(`os.name == "nt"` branches: msvcrt lock, `_proc_starttime` = GetProcessTimes creation FILETIME,
`CREATE_NEW_PROCESS_GROUP`); the pool reuses them rather than adding new ones.
- **Dispatch.** Task-card field `host: "workstation"` on `autoos-agent run` / MCP `spawn`. The spawner
  does not launch locally: it calls `runner_pool.py submit` (card + brief path + submitter lane), prints
  the job id, exits 0. `status`/`result`/`ps` read pool status. Admission refusal 13 ("queue or run on
  workstation") is the cue to re-issue with `host: workstation`.
- **N.** Registry top-level `runner_pool {workers: 8, mem_floor_mb: 6144, sample_every: 4}`; env
  `AUTOOS_POOL_N` overrides. N slots = N Windows Scheduled Tasks `AutoOS-Pool-<n>` (trigger: at logon +
  restart-on-failure; registered by a live-step packet, not by me), each running `python runner_pool.py
  slot <n>`. A slot claims only while available physical memory >= `mem_floor_mb`; the number comes from
  ctypes `GlobalMemoryStatusEx` (the spawner's `mem_available_mb` returns None off Linux = fails open, so
  the pool brings its own reader and fails CLOSED when it cannot read).
- **Queue + status.** Spool `%AUTOOS_POOL_DIR%` (default `%LOCALAPPDATA%\autoos\pool`):
  `queue\<id>.json` -> `os.rename` into `slots\<n>\` is the claim (atomic on NTFS, one winner; a loser
  gets WinError 32/183 and moves on). Each slot rewrites its `status.json` entry (write temp +
  `os.replace`) per tick: `{slot, state: idle|running|breaker-open, job, since, heartbeat}`; top-level
  `queue_depth`, `oldest_wait_s`. Finished: `done\<id>.json` (rc, failure class, run id, sha, guard
  verdicts the spawner already writes). `runner_pool.py status [--json]`.
- **Sampling (workst-L1).** Mechanical check of EVERY done record (rc, failure class, leak check,
  ready/review-status fields present) - no model. Deep review 1 in `sample_every` (counter in `done\`),
  PLUS every rc != 0, every job autoos_risk rates R2+, and each submitter's first 3 jobs.
- **Failure / restart.** Scheduled Task restarts a crashed slot; `unit_breaker.py` gets an `nt` branch
  (today it calls `systemctl --user disable`): after 5 identical failures in the same state dir it runs
  `schtasks /Change /TN AutoOS-Pool-<n> /DISABLE`, appends ONE `BREAKER` line to `AUTOOS_ALERT_INBOX`, and
  `status` shows breaker-open (state dir: `%LOCALAPPDATA%\autoos\breaker`). A job in `slots\<n>\` whose
  slot is dead (private pid gone / creation-time mismatch) goes back to `queue\` once, then `failed\`.
- **Kill (R-orch-17).** Each slot puts its job's process tree in a Windows Job Object
  (`KILL_ON_JOB_CLOSE` + per-job memory limit, ctypes; slot death takes the tree with it - no orphans) and
  writes `slots\<n>\run.private` (pid, creation FILETIME, run id, mode; owner-only ACL). `runner_pool.py
  kill <id>` reads ONLY that record, never the submitted job json, re-checks the creation FILETIME
  (pid reuse), then `taskkill /T /F /PID`; mismatch = refuse.
- **Scope.** Job = `autoos-agent run --isolate` in a private clone (existing leak check). Tasks run as a
  dedicated non-admin local account (live-step packet) whose ACLs allow only the pool dir + clone root.
  `submit` validates card fields against an allowlist; secrets never enter the queue.
- **If the workstation also runs WSL** (D-112 mentions a WSL writer host): not assumed; slots run native
  Windows. A WSL slot variant is out of scope unless fixes-L1 asks.

## B. Admission per kind (`tools/autoos-agent.py`, registry)
- Registry `host_admission.kinds.toolless = {max_live_workers: 4, mem_available_floor_mb: 3072}`;
  top-level 6 / 4608 stays the tool-using default, unchanged.
- Kind is DERIVED, never caller-set: toolless iff the resolved client's registry `capabilities` has
  `shell` false AND `write` false. A card/flag cannot declare itself toolless.
- Toolless launch: toolless-live < 4, total live < 6, MemAvailable >= 3072. Tool-using launch: all
  live (incl. toolless) < 6, MemAvailable >= 4608 - as today. Unknown/missing kind or malformed field
  -> tool-using defaults (fail closed). `AUTOOS_ADMISSION_OFF` unchanged (test only).
- Gate change = R-orch-16: 2 non-Claude seats, different families. Verify: test_registry_render.py +
  renderer `--checks`.

## Tests (failing-first)
pool (Windows cases skipped off nt, portable ones run everywhere): claim race (2 slots, 1 job -> 1 winner), requeue-once, status schema, kill refuses mismatched
creation-time and ignores a poisoned job json, unmocked Popen slot run + one live scoped spawn.
admission: toolless 5th refused, tool-using floor unchanged at 3072<=free<4608, kind not spoofable by
card, malformed `kinds` -> defaults.

## Questions / status (fixes-L1 reply folded in)
1. Toolless client: (a) accepted provisionally - registry client `api` wrapping review-call.py.
2. Transport: SSH forced command = HIGH access surface; 2 distinct-family seats (incl. non-Claude) on the
   forced-command script + the `authorized_keys` line BEFORE any key is placed. Windows OpenSSH: the
   line is `command="<python> runner_pool.py submit-only",no-pty,no-port-forwarding,...` in
   `administrators_authorized_keys` or the user's file; live step via workst-L1-autoos. Evidence that
   Windows OpenSSH honours `command=` goes with the packet.
3. N=8 as config; >3-parallel measured later (live packet).
