# seat haiku-delta on 96f24607 (round 3 delta)

Quoted line, verbatim: sandbox base: 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e HEAD 96f24607b6644533356b03ec4e26604f64321ace

Evidence is REVIEW-DIFF.patch only. I read it and ran no commands. The project tests are unrunnable here (missing `mcp`), so every "FIXED" below rests on reading the code and the new tests, not on a passing run. Line numbers are patch-file lines and are approximate in places.

**Previous-round findings**

| Item | Status | Evidence |
|---|---|---|
| H1 kill | FIXED | `kill_tree` now returns `run(argv).returncode == 0` (patch ~L899-902). `kill_job` returns `'kill-failed'` when it is false and `'run-mismatch'` when `expect_run_id` differs, before any kill (~L907-918). `_cmd_kill` passes `expect_run_id=jobid` (~L716) and exits 1 unless the result is `killed` or `gone` (~L722). Tests: `test_kill_tree_adapter` (~L344-349), `test_failed_kill_and_run_mismatch` (~L351-358), `test_round3_kill_and_slot_shapes` (~L118-138). The no-op fallback is gone. |
| H2 memory | FIXED | `_sane_memstatus` accepts 0 and nulls only the bad field (~L468-472). `windows_memory_status` keeps `ullAvailPhys=0` as a real reading (~L480-490). Tests: ~L8-16, ~L25-38 (`(1024,0)` gives `0`), and `test_partial_memory_readings_bind_independently` (~L40-49), which asserts `"the memory floor"` for 0 MB. Commit ceiling is judged independently. |
| H3 `_find_slot` non-dict | FIXED | Non-dict `slot.json` and non-dict/str `job` are skipped (~L692-703). Test ~L123-126 covers `[]`, `'x'`, `None`, `{'job': []}`. |
| H3 pool accepts non-api | PARTLY | `validate_card` rejects explicit clients outside `('api',)` (~L820-821; tests ~L243-249). A missing `client` defaults to `'api'` and is accepted (see finding 1). |
| H3 recovery vs live slot | FIXED | `run_one` takes `slot.lock` under `.lock-guard`, returns `slot-busy` for a live owner, and fails closed on an unreadable owner (~L594-614). Test `test_round3_live_and_stale_lock_and_done_recovery` (~L443-456). The residual gap is in finding 4. |
| H3 done-then-unlink re-run | FIXED | `recovered-done` unlinks the claim when `done/<id>.json` exists (~L329-332). Same test as above. |
| H3 `write_json_atomic` False ignored | FIXED | Checked at the gate (`rejected: spool busy`, ~L226-229), memory requeue (~L383-386), rc13 requeue (~L655-658), claimed-admission requeue (~L646-648), and done record (`record-failed`, ~L668-670). Tests ~L414-441 and gate ~L276-291. |
| H3 single short read | FIXED | `read_capped` loops until cap+1 or EOF (~L798-812). Tests `test_read_capped_short_reads` and `test_read_capped_stalled_after_packet` (~L202-225). |
| H3 `list2cmdline` inflation | FIXED | Length is measured on `list2cmdline(argv)` in UTF-16 units, with a separate 30000 brief cap (~L567-569). Tests ~L140-144 cover quotes and surrogate pairs. |
| H3 status.json never written | FIXED | `_publish_status` runs from `status` and after each slot iteration (~L486-491, ~L768, ~L778). It keeps only `n` and `state` per slot. Test ~L146-155 asserts the key set and that `SECRET` is absent. The gate also filters to three keys (gate test ~L311-319). |
| H3 `filetime_of` prototypes | FIXED (Windows) | `OpenProcess` gets argtypes and a `c_void_p` restype in `_k32()` (~L1025-1027). `_nt_filetime` uses `_k32()` (~L1036). Test ~L384-393 checks the restype and `CloseHandle`. The POSIX branch regressed (finding 3). |
| H3 wintypes at module scope | FIXED | Types are built lazily in `_init_nt_types()` (~L924-1017). Test ~L367-382 blocks the eager import in a subprocess. |
| H3 kind re-derived | PARTLY | `_host_claim_kind` retags the claim before the final record (~L508-522, ~L544; test ~L58-67). The retag happens after the admission cap check (finding 2). |

**New findings**

1. **Medium-high. `tools/runner_pool_gate.py` `validate_card` (~L820) and `run_one` (~L383-395, ~L646-649).** `card.get('client', 'api')` accepts a card with no `client` key, and the gate does not normalise it. Downstream, `admission_refusal(job)` reads `job['card']['client']` inside a try, and any exception becomes `'admission-unreadable'`. That string is truthy, so `claimed_admission` requeues the job as `requeued-refused`. The oldest-first queue then returns the same job to every slot. Failing input: `{"card":{},"brief":"x"}` through `ssh-gate submit`. The gate accepts it (the test at ~L276-291 asserts `len(calls)==1` for `card: {}`), and the queue head never drains. I rate this above minor, but it is not a crash and not a secret or path issue, so it does not meet the REJECT bar. Fix: require `client` to be present, or default it in the gate.

2. **Medium. `tools/autoos-agent.py` `_host_claim_kind` (~L508-522) and `cmd_run` (~L544).** The claim is admitted under the initial kind, then retagged to the final client's kind without re-running the cap check. If a toolless claim falls through to a tool-using client, the tool-using cap is never applied. I inferred the ordering from the hunk, since the admission call sits outside the patch.

3. **Medium. `tools/runner_pool_proc.py` `filetime_of` (~L1050-1062).** The POSIX branch now always returns `None`. Pid-reuse detection is lost. A stale `slot.lock` whose pid is reused by a live process compares `None == None` and returns `slot-busy` forever. The patch does not add an exemption for POSIX.

4. **Medium. `tools/runner_pool.py` `run_one` lock check (~L600-607).** An owner record with `filetime: null` passes JSON validation. If `filetime_of(owner_pid)` later returns a real value on Windows, the lock is treated as stale and a second slot process takes it. The same fail-open applies when `deps.filetime` is `None` at lock creation. Also, a crash between `open('x')` and `json.dump` leaves an empty lock, which fails closed and wedges the slot until someone removes it by hand.

5. **Medium. `tools/autoos-agent.py` `live_worker_count` (~L495-501) and the test change at `tests/test_autoos_spawner.py` ~L83-84.** Malformed worker rows are no longer counted. The tool-using count drops from 3 to 2 in that test. The rows list is outside the patch, so I infer the dropped row is the non-string `kind: 3` case. Corrupt or unknown-kind running workers therefore stop counting against the caps. That is fail-open on the same class as H2.

6. **Minor.** `slot-dead` was renamed to `slot-unreadable` (`runner_pool.py` ~L119-122; test ~L96-97). This changes a visible status value. `kill_tree` on a missing `taskkill` raises `OSError`, which `_cmd_kill` reports as `refused: bad run.private`. `claimed_admission` passes a `Path` to `write_json_atomic`, while the other calls pass `str(...)`. The `rpc.locked` and `read_run_private` dict-type handling are called from code outside the patch and were not checked.

Verdict basis: none of the findings is a secret, a path outside the sandbox, a stamp mismatch, or a crash where a clean refusal was due. Finding 1 is the closest to that last category, since a clean gate refusal was due and the pool loops instead. It is still a non-terminating requeue rather than a crash, so I do not reject on it, but it should be fixed before the pool is used with untrusted submitters.

VERDICT: ACCEPT 96f24607b6644533356b03ec4e26604f64321ace
