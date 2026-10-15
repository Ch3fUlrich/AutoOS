# AO-WORKST-RUNNER-POOL - design note (fixes-L2-runnerpool, D-1317)

Status: DRAFT for cross-family spec review (R-orch-13). No code before it passes.

## Problem
One workstation runner session queues beyond ~3 parallel L2 jobs, and workst-L1 reviews every job.
Separately, spawner host admission (`catalog/ai-registry.json` `host_admission`, 6 workers / 4608 MB)
is one pair for every run, so D-1317's coding.vm room for tool-less runs (4 / 3072 MB) cannot be
expressed without loosening tool-using runs too.

## A. Runner pool (new `tools/runner_pool.py`, stdlib only)
- **Dispatch.** Task-card field `host: "workstation"` on `autoos-agent run` / MCP `spawn`. The
  spawner does not launch locally: it calls `runner_pool.py submit` (card + brief path + submitter lane),
  prints the job id, exits 0. `status`/`result`/`ps` read pool status for that id. Admission refusal 13
  ("queue or run on workstation") is the cue to re-issue with `host: workstation`.
- **N.** Registry top-level `runner_pool {workers: 8, mem_floor_mb: 6144, sample_every: 4}`;
  `AUTOOS_POOL_N` overrides on the workstation. N slots = N systemd user units `autoos-pool@<n>.service`
  (live-step packet, not run by me). A slot claims only while MemAvailable >= `mem_floor_mb`.
- **Queue + status.** Spool `$AUTOOS_POOL_DIR` (default `~/.autoos/pool`): `queue/<id>.json` ->
  atomic `rename` into `slots/<n>/` = the claim (no lock, one winner). Each slot rewrites
  `status.json` entry atomically each tick: `{slot, state: idle|running|breaker-open, job, since,
  heartbeat}`; top-level `queue_depth`, `oldest_wait_s`. Finished: `done/<id>.json` (rc, failure class,
  run id, sha, guard verdicts the spawner already writes). `runner_pool.py status [--json]`.
- **Sampling (workst-L1).** Mechanical check of EVERY done record (rc, failure class, leak check,
  ready/review-status fields present) - cheap, no model. Deep review 1 in `sample_every` jobs (counter
  in `done/`), PLUS every rc != 0, every job whose diff autoos_risk rates R2+, and each submitter's
  first 3 jobs. Deep = read diff vs brief + tests ran; same rules as today, fewer jobs.
- **Failure / restart.** Slot unit ExecStart wrapped in `tools/unit_breaker.py --unit
  autoos-pool@N.service -- <slot loop>` + `breaker.conf.in` (5 failures / 600 s -> disabled, BREAKER line
  to `AUTOOS_ALERT_INBOX`, `status` shows breaker-open). A job in `slots/<n>/` whose slot is dead
  (private pid gone / start-time mismatch) is renamed back to `queue/` once, then to `failed/`
  (never loops).
- **Kill path (R-orch-17).** `runner_pool.py kill <id>` reads ONLY the slot-private record
  `slots/<n>/run.private` (0600, written by the slot: pid, pgid, /proc start-time, mode, run id). It never
  opens the submitted job json. Identity re-verified (start-time) before `killpg`; mismatch = refuse.
- **Scope.** Job = `autoos-agent run --isolate` in a private clone (existing leak check). Slot unit:
  no sudo, `ProtectHome=read-only` except pool dir + clone root, `MemoryMax` per slot. `submit`
  validates card fields against an allowlist; secrets never enter the queue (env from the unit).

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
pool: claim race (2 slots, 1 job -> 1 winner), requeue-once, status schema, kill refuses mismatched
start-time and ignores a poisoned job json, unmocked Popen slot run + one live scoped spawn.
admission: toolless 5th refused, tool-using floor unchanged at 3072<=free<4608, kind not spoofable by
card, malformed `kinds` -> defaults.

## Questions for fixes-L1 (default = proceed with it)
1. **No toolless client exists** - the spawner has only tool-using CLIs; `tools/review-call.py` (single
   chat completion) is outside the spawner and unadmitted. Options: (a, DEFAULT) add registry client
   `api` wrapping review-call.py with capabilities shell/write false, kind derived from it; (b) admission
   table + derivation only, `api` client in a later lane; (c) caller flag `--toolless` (rejected: spoofable).
2. **Transport coding.vm -> workstation.** (a, DEFAULT) SSH forced command running only
   `runner_pool.py submit|status`, target from env `AUTOOS_POOL_SSH` (no hostname committed); (b) workst-L1
   inbox relay as today (keeps the bottleneck).
3. N=8 initial (62 GB / ~2 GB per job headroom); measure >3 parallel on the workstation (live packet).
