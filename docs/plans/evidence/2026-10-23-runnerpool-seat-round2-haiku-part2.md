# seat haiku-part2 on 7e960334 (round 2)

Reject basis: a crash where a clean refusal was due, in the kill path (finding 1). I confirmed it by running a read-only probe against the checkout. It needs a corrupted local `slot.json`, so the likelihood is low. The most serious problem is finding 2, a cancellation that reports success without killing anything. It is outside the listed reject categories, so I am stating it as high severity rather than as the reject basis.

**Stamp.** The patch carries no base or HEAD sha. This checkout's HEAD is `818bb62` (message: "sandbox base (source 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e)"). `90270a46â€¦` is not an object in this repo, so I could not confirm the quoted base. The lane head matches only through the commit message. Treat the stamp as unverified, not as a confirmed mismatch.

sandbox base: 90270a46c3c9d552a3274bdaa773369536523fd8 HEAD 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e

Tests were not run. The prompt reports `project tests unrunnable: missing python modules: mcp`. My checks were read-only probes with `python -I -B` and in-memory fakes, so nothing was written. Nothing was edited or committed.

## Findings

1. **REJECT basis â€” crash where a clean refusal was due (V8).** `tools/runner_pool.py:474-477`, `_find_slot`. `json.loads` is guarded, but `data.get('job')` is not. A `slot.json` containing `[]` raises from `_cmd_kill`, which does not catch it.
   Command run:
   ```
   python -I -B -c "import sys, io; sys.path.insert(0,'tools'); import runner_pool as p; import runner_pool_proc as proc
   class F:
       def exists(self): return True
       def read_text(self): return '[]'
       def is_dir(self): return True
       def __truediv__(self, name): return self
       def iterdir(self): return [self]
   p.spool_root = lambda env: F()
   out = io.StringIO()
   try:
       rc = p._cmd_kill(['j1'], {}, out, None, proc); print('rc', rc)
   except Exception as e: print('raised', type(e).__name__, e)"
   ```
   Output: `raised AttributeError 'list' object has no attribute 'get'`. The expected result is a refusal.

2. **HIGH, outside the reject list â€” cancellation reports success and kills nothing (V8/V9).** `tools/runner_pool.py:497-498` falls back to `lambda argv: None` because `runner_pool_proc.py` never defines `kill_tree`. `runner_pool_proc.py:89` then calls that no-op. Probe: `read_run_private` and `filetime_of` were patched to match, and `_cmd_kill(['j1'], â€¦, None, proc)` printed `killed` with rc 0 and issued no taskkill. Fix: define a `kill_tree` that runs taskkill and checks its return code, and report failure otherwise.

3. **MEDIUM (V11) â€” the pool accepts tool-using clients.** `runner_pool_gate.py` `validate_card` only type-checks `client`, and `tools/runner_pool.py:152-185` (`job_argv`) checks only `api` models (line 169). Probe: `parse_submit` with `{'client':'claude',â€¦}` passed; `job_argv` returned `['python','tools\\autoos-agent.py','run','--client','claude','--model',â€¦]`; `default_risk_of` returned `R2`. The design calls this pool tool-less, so the gate or `job_argv` should allowlist `client == 'api'`.

4. **MEDIUM (V2) â€” no per-slot lock; duplicate execution.** The design promises an in-process msvcrt slot lock, but none exists. `run_one` (`runner_pool.py:304-316`) requeues any claimed file in the slot, so a second `slot --n 1` process requeues the first process's live job. Separately, `_run_claimed` writes `done/` before unlinking the claim (`runner_pool.py:398-403`). A crash between those steps re-runs a completed job on restart.

5. **MEDIUM â€” unbounded rc=13 requeue (V1/V2 liveness).** `runner_pool.py:376-385` requeues on any child rc 13 with no counter. A job that always exits 13 is requeued every 15 s indefinitely.

6. **MEDIUM â€” plain `Popen` with no containment (V10).** `runner_pool.py:419`. The JobObject helpers are not wired. The known-limit deferral holds only while dispatch is off, but `slot` and `ssh-gate` are runnable now. If `_write_run_private` fails after spawn (`runner_pool.py:427`), the child keeps running with no identity while the job is quarantined as failed.

7. **LOW (V9) â€” manufactured zero identity.** `runner_pool.py:427` writes `filetime_of(pid) or 0`, and `_validate_params` accepts 0. A live process's FILETIME is never 0, so this cannot match a wrong process. It does make cancellation of that run silently unreachable.

8. **LOW (V8) â€” no run_id check.** `kill_job` (`runner_pool_proc.py:77-89`) never compares `run.private`'s `run_id` with the requested job. `_find_slot` matches only `slot.json`. A stale `run.private` for a still-live process can therefore be killed under a different job's id.

9. **LOW â€” NotImplementedError is not caught.** `kill_job` raises on non-nt, and `_cmd_kill`'s except list (`runner_pool.py:505`) omits it, so `kill` on POSIX produces a traceback. This is a second instance of the finding-1 class.

10. **LOW (V6) â€” argv length ignores list2cmdline escaping.** `job_argv`'s total (`runner_pool.py:~180`) counts raw UTF-16 units. A brief of about 29,000 `"` characters passes the 30,000 check but serializes past 32,767. Spawn then fails with OSError, which the job record handles, so this is not a crash. This is by reading; I did not run it.

11. **LOW (V12) â€” shared prompt and output paths.** `api_client.build_command_api` names files by `sha256(task)[:12]` and reuses `api-out/<ident>` without clearing it. Identical concurrent briefs race on the prompt, and a stale `review.txt` can be read as output. The parent-directory race after `confine` is also unguarded.

**Checked and holding:** post-claim memory recheck, with `None` refusing at `runner_pool.py:295` and `:356`; `_valid_model` exact membership; `strip_env` case-insensitive at the spawn edge; no `os.environ` merge afterwards; `read_capped` 65536-byte cap with 30 s timeout before JSON parse; exact forced-command matching; `paths` and `spec` rejected in `job_argv`, not discarded; no hostile-input crash found in the gate. I could not reproduce two earlier seat claims in `docs/plans/evidence/`: that `parse_submit` accepts an `id` key (it rejects any key other than card/brief), and that `job_argv` does not check `card.model` (it does, at `runner_pool.py:169`).

Not verified here: JobObject execution, the real SSH path, and the test suite.

VERDICT: REJECT 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e
