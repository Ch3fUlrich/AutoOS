# AO-WORKST-RUNNER-POOL - design note v5 (fixes-L2-runnerpool, D-1317)

Status: v5 = v4 + final-seat items 1-10 (judge GO for writers once folded). Code review seats later (R-orch-13). No code before it passes.

## Problem
One workstation runner session queues beyond ~3 parallel L2 jobs, and workst-L1 reviews every job.
Separately, spawner host admission (`catalog/ai-registry.json` `host_admission`, 6 workers / 4608 MB)
is one pair for every run, so D-1317's coding.vm room for tool-less runs (4 / 3072 MB) cannot be
expressed without loosening tool-using runs too.

## A. Runner pool (new `tools/runner_pool.py`, stdlib + ctypes, Windows-first)
Workstation = WINDOWS (judge finding). No systemd, /proc, killpg, ProtectHome. Reuses the spawner's `nt`
primitives (msvcrt lock, `_proc_starttime` = GetProcessTimes creation FILETIME).
- **Dispatch.** Card field `host: "workstation"` on `autoos-agent run` / MCP `spawn` -> spawner calls
  `runner_pool.py submit` (brief on STDIN, never a path; submitter taken from the transport, never an
  argument), prints the job id, exits 0. `status`/`result`/`ps` read pool status. Refusal 13 ("queue or run
  on workstation") is the cue to re-issue with `host: workstation`.
- **N and the cap (F5).** Registry `runner_pool {workers: 6, mem_floor_mb: 6144, sample_every: 4}`;
  `AUTOOS_POOL_N` may only LOWER it: slot startup computes `N = min(requested, 6)`, logs
  `clamp requested=<r> used=<n>` when they differ, and a non-integer or < 1 value is a startup error (never
  silently raised, never defaulted up). Default N = 6 = the shipped `host_admission.max_live_workers`, so a
  slot never meets rc 13 in steady state; no admission loosening for tool-using runs. A slot also runs
  `host_admission_refusal` BEFORE claiming a job and sleeps if refused; a job that still returns rc 13 is
  requeued without a failure count (the slot loop handles it, it does not exit). Raising N above the cap =
  a separate gate change (R-orch-16), not this lane.
- **Slots.** N Scheduled Tasks `AutoOS-Pool-<n>` (live packet): at logon, restart-on-failure,
  `ExecutionTimeLimit PT0S`, `MultipleInstancesPolicy IgnoreNew`, plus a per-slot msvcrt lock inside the
  process (double-start proof). `runner_pool.py status` on the submit side flags a stale `heartbeat`
  (> T = 120 s) as `slot-dead`. Memory floor: ctypes `GlobalMemoryStatusEx` on nt, `/proc/meminfo` elsewhere;
  an unreadable value REFUSES to claim (F4) - for the pool and for any admission kind with a floor on nt.
- **Accounts (F6).** TWO local accounts: `pool-slot` (non-admin) owns the spool, status, counter, `done\`,
  `run.private`; `pool-job` (non-admin) runs the job and can write only its clone + the workers dir. The
  slot spawns the job as `pool-job` (CreateProcessWithLogon/`runas`-style, live packet); the job cannot
  touch spool files. Secrets never enter the queue.
- **Who writes what (items 2-4).** ONLY `pool-slot` writes `done\\<id>.json`, `status.json`, the sample
  counter, the per-submitter counters, `run.private` and the run log (`log\\<id>.log`, pool-slot-only dir:
  the job's stdout/stderr are piped to it by the slot, the job holds no handle to the directory). `pool-job`
  has NO write on spool / done / status / counters / run.private / log. Per-submitter first-3 counters are
  durable files in the pool-slot dir (`submitters\\<name>.count`), read-modify-written under the same claim
  lock as the other counters, so they survive restart.
- **Queue + status.** Spool `%LOCALAPPDATA%\autoos\pool` (pool-slot only). Claim = `os.rename`
  `queue\<id>.json` -> `slots\<n>\`: FileNotFoundError = lost the race (next job); PermissionError /
  WinError 32 = sharing violation -> at most 8 attempts, backoff 25 ms doubling to 400 ms (~1.5 s total),
  then skip the job (claim) or log `status-write-failed` and continue (status); same bound on `status.json`
  `os.replace` (F8). Never unbounded.
  Done: `done\<id>.json`. `status [--json]`: per slot `{state idle|running|breaker-open|slot-dead, job,
  since, heartbeat}`, `queue_depth`, `oldest_wait_s`.
- **SSH surface (F7), HIGH.** ONE verbatim authorized_keys line (the only form; the earlier `submit-only` variant is dropped):
  `restrict,command="C:\\Python\\python.exe C:\\autoos\\tools\\runner_pool.py ssh-gate --submitter <lane>" ssh-ed25519 <PUBKEY> <lane>@coding`
  (`restrict` = no pty / forwarding / agent / X11); paths set by the live packet. The gate the gate parses `SSH_ORIGINAL_COMMAND`
  with a strict regex allowing ONLY `submit` (brief on stdin capped at 65536 BYTES, read with a hard `read(65537)` and refused above the cap BEFORE
  any JSON parse; card allowlist) and `status --json`;
  anything else exits 2. The submitter is bound per key line, so a caller cannot rename itself to dodge
  first-3 sampling. Seats on the gate script + the line before a key is placed.
- **Sampling (F12).** Counter RMW under an msvcrt lock. Mechanical check reads the spawner's WORKER
  record + leak warning from the run log (written by the job's parent process, not a self-written done
  record); the pool recomputes `autoos_risk` from `git diff base...HEAD` in the clone. Deep review: 1 in
  `sample_every`, every rc != 0, every R2+, each submitter's first 3.
- **Failure / restart.** `unit_breaker.py` nt branch (F11): `trip()` on nt runs `schtasks /End` then
  `schtasks /Change /DISABLE` for the task (never `systemctl`), writes the `.tripped` marker, one
  `BREAKER` line; "identical" = same FAILURE CLASS, not same rc string; classes: `startup` (import/config error, rc 2-3),
  `io` (OSError/WinError on spool), `memory` (Job/MemoryError or floor unreadable), `crash` (unhandled
  exception / signal-like exit), `other`; 5 consecutive same-class failures trip, no time window. Dead-slot job: back to
  `queue\` once, then `failed\`.
- **Kill (R-orch-17) + Job Object (F9).** Job process created `CREATE_SUSPENDED` ->
  `AssignProcessToJobObject` -> `ResumeThread`; job limits `KILL_ON_JOB_CLOSE` + `JOB_OBJECT_LIMIT_JOB_MEMORY` = 8192 MB per job, applied at Assign time
  (6 jobs x 8 GB = 48 GB < 62 GB host), never `BREAKAWAY_OK`. `run.private` (pid, creation FILETIME, run id, mode). `kill <id>` reads ONLY it, re-checks
  the FILETIME, `taskkill /T /F`; test asserts the tree is empty.

- **Seat-1 deltas (v4).**
  (a) `AUTOOS_ADMISSION_OFF` is stripped from the env of the slot, the job and the SSH gate and never
  forwarded; the pool ignores it. (b) Toolless and tool-using counters are read under the SAME claim lock,
  and memory is re-read inside that lock immediately before exec. (c) Requeue-once is a durable
  `requeued` marker in the job file's slot record; a slot is dead only if (pid gone OR creation FILETIME mismatch) AND heartbeat
  older than T = 120 s (tick = 15 s, so 8 missed ticks); a `breaker-open` slot never claims. (d) Mechanical every-done check also
  covers leak check + rc / failure-class / ready consistency; an empty diff or tests-skipped forces deep
  review; an unsampled job never auto-promotes an R2+ result. (f) Gate rejects any arg starting with `-`;
  job id must match `^[A-Za-z0-9_-]{1,64}$`; card size cap (8 KB); brief size cap.

## B. Admission per kind (`tools/autoos-agent.py`, registry)
- Registry `host_admission.kinds.toolless = {max_live_workers: 4, mem_available_floor_mb: 3072}`;
  top-level pair unchanged (shipped 6 / 4608; code defaults 6 / 6144).
- **Kind derived, fail closed (F1).** toolless iff the resolved client's registry row exists and has
  explicit boolean `shell is False` AND `write is False`. Unregistered client, missing `capabilities`, or any
  non-bool = tool-using (do NOT use `client_capabilities()`'s default-False). Tests: unregistered name,
  missing field, card-supplied `toolless`.
- **Config validation (F2).** Missing/invalid `kinds.toolless` falls back to the TOP-LEVEL pair, never to
  3072; cap < 1 or floor < 1 is invalid (also for the top-level pair -> defaults).
- **Count (F3).** `host_admission_claim` writes `kind` into the reservation placeholder AND the worker
  record; a record without `kind` counts as tool-using. Toolless launch: toolless-live < 4, total live < 6,
  memory >= 3072. Tool-using launch: all live < 6, memory >= 4608 (unchanged).
- **`api` client (e).** build_command passes ONLY `--model` (allowlist from the registry, no passthrough
  args), `--prompt-file` and `--out-dir` confined to pool dirs (realpath check, no symlinks/`..`). `write
  false` means no clone / workspace write: its only output is `--out-dir` text + evidence.json.
- Gate change = R-orch-16: 2 non-Claude seats, different families. Verify: test_registry_render.py +
  renderer `--checks` (R-orch-18).

## Tests (failing-first)
pool: claim race (1 job, 2 slots), PermissionError retry, requeue-once, rc13 requeue without count,
status schema + stale heartbeat, kill refuses FILETIME mismatch and ignores a poisoned job json, job
account cannot write spool (nt only), gate rejects every non-`submit`/`status --json` string and a path in
the brief, ADMISSION_OFF stripped on slot/gate, lock covers both counters, requeued marker survives restart, `-x`/bad-id/oversize card rejected, api-client path/model confinement, pinned items: dead-slot truth table (pid gone only / FILETIME only / fresh heartbeat / stale), job account cannot write done/log/counters, 65537-byte brief refused before parse, N clamp + log, retry stops at 8 attempts, breaker same-class vs different-class, authorized_keys line equals the verbatim string, Job limit = 8192 MB, unmocked Popen slot run + one live scoped spawn (Windows cases skipped off nt).
admission: 5th toolless refused, tool-using floor unchanged between 3072 and 4608 free, kind not
spoofable, unregistered client = tool-using, malformed/zero `kinds` -> top-level pair, nt unreadable memory
refuses, placeholder carries kind.

## Questions / status (fixes-L1 reply folded in)
1. Toolless client: (a) accepted provisionally - registry client `api` wrapping review-call.py.
2. Transport: SSH forced command = HIGH access surface; 2 distinct-family seats (incl. non-Claude) on the
   forced-command script + the `authorized_keys` line BEFORE any key is placed. Windows OpenSSH: the
   line is `command="<python> runner_pool.py submit-only",no-pty,no-port-forwarding,...` in
   `administrators_authorized_keys` or the user's file; live step via workst-L1-autoos. Evidence that
   Windows OpenSSH honours `command=` goes with the packet.
3. N=6 (= admission cap, F5); >3-parallel measured later (live packet). Above 6 = separate R-orch-16 gate change.
