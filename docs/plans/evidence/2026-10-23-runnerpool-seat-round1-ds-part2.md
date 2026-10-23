# seat ds-part2 on cd52c779 (round 1, before fixes)

sandbox base: 4359016e391cac2d4abd580aac6cfc73d82bd175 HEAD cd52c779d48dfd845e9dee2575682e6422f9cfdf

Stamp/evidence checks first: `git apply -R --check REVIEW-DIFF.patch` succeeded against the snapshot, so the sandbox tree is the lane-head tree named in the patch; the change is visible to me; no secret, private key or credential appears anywhere in the diff (grepped for keys/tokens/emails/IPs). No stamp mismatch. Unit suites run: `python -m pytest tests/test_runner_pool*.py tests/test_api_client.py -q` â†’ `190 passed, 77 subtests passed`. The spawner suite cannot run here (missing `mcp`), as stated.

## REJECT â€” a crash where a clean refusal was due (V3 + V4 + V1, root cause: the installed forced command bypasses the gate)

`tools/runner_pool_gate.py:157` `authorized_keys_line` installs `C:\Python\python.exe C:\autoos\tools\runner_pool.py ssh-gate --submitter <lane>`. That dispatch lands on `tools/runner_pool.py:457 _cmd_ssh_gate`, which **never calls `gate.run_gate`** (the `gate` argument it is handed is unused). Consequently `read_capped` (65536-byte cap + 30 s timeout), `parse_original_command`, `validate_card` (field allowlist, 8192-byte card cap), `_check_path` and brief validation are all unreachable from the only route an SSH key can invoke; `status --json`, which the design allows, is refused while arbitrary payloads are accepted.

Command I ran (`scratch/rp/REPRO-gate-crash.py`, both real CLI entry points, no stubs in the gate or the slot dispatch):

```
python scratch/rp/REPRO-gate-crash.py
gate rc=0 job='job-41312-1792768712227033700'
slot CRASH: KeyError KeyError('id') at runner_pool.py:318
queue still holds: ['job-41312-1792768712227033700.json']
```

The submission was `{"brief":"x"}` â€” no card, no id. The documented gate refuses exactly that payload:

```
run_gate b'{"brief":"x"}'                              -> 2 'rejected: extra or missing keys'
run_gate b'{"card":{"client":"api"},"brief":"x","id':  -> 2 'rejected: extra or missing keys'
```

So a clean exit-2 refusal existed and was bypassed. The slot entry (`runner_pool.py slot --n K`, the Scheduled-Task action the design names) raises `KeyError('id')` at `runner_pool.py:318`, `_cmd_slot`'s loop has no handler, and the file stays at the head of the queue, so **every restart re-crashes on the same file** â€” one authenticated submission stops the pool instead of the gate refusing it. `tests/test_runner_pool_cli.py:135-147` bakes this in: it asserts `ssh-gate` accepts an id-less payload.

Two more crashes in the same chain (each should be the documented `failed`/refusal path):

* `tools/runner_pool.py:181` measures the command line in characters; `Popen` gets UTF-16 units. `20000` emoji â†’ `job_argv` accepted (`len()=20210`) â†’ `Popen OSError: 2 206 The filename or extension is too long`, uncaught in `run_one` (`rc = deps.spawn(...)` is outside any try). 16 300 emoji is 65 200 bytes, i.e. under the documented 65 536-byte brief cap, and still over the Windows limit.
* `kill` on malformed `run.private`: `_cmd_kill` (`runner_pool.py:530`) does not catch `kill_job`; `b'[]'` â†’ `AttributeError 'list' object has no attribute 'keys'` at `runner_pool_proc.py:65` (a list is not a dict, so `_validate_params` never runs); `{"run_id":"job-7"}` â†’ uncaught `ValueError: unexpected keys in run.private`. I expected a clean "refused" line.

## High â€” V2: claim's result is discarded, so claim failure and claim races both execute the wrong thing

`runner_pool.py:317` ignores the `Path` that `runner_pool_core.claim` (`core.py:133-162`) returns and re-derives `slot_dir/f"{job['id']}.json"` (line 318). `ensure_dirs` creates `slots/` but not `slots/<n>`, so on the first tick of a fresh slot the Windows rename has no destination directory, `claim` returns `None`, and `run_one` **spawns the queue file anyway**:

```
python scratch/rp/repro9.py
0 done | queue before: ['job-40123-1792768493.json'] after: ['job-40123-1792768493.json'] | spawns: 1
1 done | queue before: ['job-40123-1792768493.json'] after: [] | spawns: 2
```

One job, two executions. Losing a claim race has the same shape: the payload parsed is job A, the file actually held/deleted may be job B. With the real gate the filename is server-generated (`job-<pid>-<ns>.json`) while `id` comes from the sender, so id and filename never agree. Attack input (`id` accepted verbatim by `_cmd_ssh_gate`):

```
python scratch/rp/repro10.py
before: ['job-9.json', 'victim.json'] victim size 89
run_one -> done
after : ['job-9.json'] | spawns: ['victim work']
```

`{"id":"../queue/victim", ...}` overwrites and deletes **another submitter's** queue file â€” the victim's job never ran. `_ID_RE` is checked only later, in `job_argv`, so the damage is done before validation; `_cmd_ssh_gate` should not have accepted the payload at all.

## High â€” V8/V9: `kill <id>` can never find a running job

`runner_pool.py:505` compares `slot.json["job"]` â€” the whole job dict that `write_slot_state` writes â€” with the id string. It is never equal:

```
python scratch/rp/repro6.py
slot.json job field: {'id': 'job-7', 'card': {'client': 'api'}}
kill rc/out -> 1 'no such job\n'
```

Cancellation of a live run is dead. `tests/test_runner_pool_cli.py:172,185` pass `{'job': 'other'}` / `{'job': jobid}`, a slot shape production never writes â€” helper-test-only evidence for the one operator action that must work. `read_run_private` itself is the sole kill identity source and refuses a mismatched/gone FILETIME correctly; the wiring is what fails.

## Minor (say it, don't block)

* **V1 config not implemented**: `floor_mb=6144` is a `Deps` default (`runner_pool.py:226`), N=6 is duplicated (`_cmd_slot` and `core.POOL_MAX_N`), `sample_every=4` is a default; there is no `runner_pool` registry section, so the design's "validated configuration" is a constant. The pool's `admission_refusal()` also calls `host_admission_refusal()` with the default `kind="tool-using"` (cap 10), so api/toolless pool jobs are admitted against the looser cap (4 for toolless).
* **V5**: `card.paths` is validated nowhere on the live route and `job_argv` silently drops it â€” the promised per-job path restriction does not exist downstream.
* **V12**: `confine` re-checks `realpath`, and brief text does go to a private prompt file with only paths in argv (good), but `api_client.py:109-111` falls back to no `O_NOFOLLOW` on Windows and nothing re-checks the path at open, so a parent-junction swap between `confine` and `os.open` is unguarded.
* **V7**: `strip_env` is case-insensitive and applied at the spawn edge (verified: `{'AutoOS_Admission_Off','autoos_admission_off'}` removed), but `_build_real_deps.admission_refusal` imports autoos-agent and reads the **slot's own** `os.environ`, so a slot launched with `AUTOOS_ADMISSION_OFF=1` still bypasses admission; the scrub only protects the child.
* **V10**: `popen_spawn` is plain `Popen` (`runner_pool.py:409`) with no JobObject; `create_job_nt`/`spawn_in_job` are unwired. Nothing in `tools/` dispatches pool jobs and the HANDOFF states this plainly, so this stays a known-limit deferral â€” but the containment described in the component brief does not exist in code.
* **Repo rule**: the new tracked `docs/plans/2026-10-15-workst-runner-pool-HANDOFF.md` / `-PACKET.md` contain absolute host paths with a username (`<home>/code/routing-worktrees/...`, `<home>/.claude/jobs/...`) and name `configuration/api-keys.yml` â€” AGENTS.md hard rule 1 / DoD "no absolute path containing a username in a tracked file". No secret value is exposed.

## Contracts that hold (checked, not assumed)

`run_one` fails closed on `None` memory before and after the claim (`'memory'`, queue preserved; `may_claim(None) == False`); `_build_real_deps` turns an unreadable admission into `'admission-unreadable'` (refusal, not permissive defaults); `rc=13` requeues without a failure count and `requeue_dead` moves to `failed/` on the second pass; `api_client._valid_model` requires exact `API_MODELS` membership and `build_argv` rejects `0/-1/True/"8000"/8001`; a dash-leading brief is protected by `--`; `run.private` records the child's own creation FILETIME after `Popen` and `kill_job` never reaches `kill_tree` on `gone`/`filetime-mismatch`; the `authorized_keys_line` `restrict,command=` form binds the submitter and rejects quotes/spaces in lane and key.

VERDICT: REJECT cd52c779d48dfd845e9dee2575682e6422f9cfdf
