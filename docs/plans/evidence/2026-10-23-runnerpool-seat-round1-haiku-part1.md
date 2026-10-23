# seat haiku-part1 on cd52c779 (round 1, before fixes)

Findings are ranked. None falls in the REJECT classes (secret or outside-sandbox path, stamp mismatch, change not visible, crash where a clean refusal was due). Nothing was executed; this is a read of `REVIEW-DIFF.patch` only, and the project tests were not run (missing `mcp` module).

1. **high, V5, `tools/autoos-agent.py` ~4440 and ~4452–4454.** The env escape `AUTOOS_ADMISSION_OFF=1` is still honored in production `host_admission_refusal`. It is unchanged context from base, so it is pre-existing, not introduced here. The brief says mandatory removal of the admission escape is not deferred, and this change does not remove it. Also, `AUTOOS_MEMINFO_PATH` set on a real win32 host makes `mem_available_mb` read that path. On Windows that returns None and `commit_pct` stays None, so both the RAM floor and the commit ceiling stand down. Failing input: spawner env `AUTOOS_MEMINFO_PATH=C:\nonexistent`, with 20 live workers, returns no refusal from the memory and commit checks. The tests assert the job worker never inherits `AUTOOS_ADMISSION_OFF` (~1169), but nothing covers the spawner's own env. Under the stated criteria this is not a REJECT, but it should be fixed before dispatch is enabled.

2. **medium, V1, `tools/autoos-agent.py` ~4149–4157 vs ~4194.** Two module-level `def host_admission_config` exist. The first, with the docstring "If ANY top-level field is invalid … BOTH fall back to code defaults (6, 6144)", is dead code. The second wins and does per-field fallback, and the tests (~1248–1256) agree with the second. The stale docstring is a trap for the next editor. Failing input: `host_admission_config({"host_admission": {"max_live_workers": 0, "mem_available_floor_mb": 4608}}, "linux")` returns `(6, 4608)`, not the `(6, 6144)` the stale docstring promises. Delete the first definition.

3. **medium, V12, `tools/autoos-agent.py` ~4131–4132 and test ~1363–1375.** `_worker_record_start` re-reads `REGISTRY_PATH` with an unguarded `load_registry`, so an OSError or ValueError there escapes after admission. `cmd_run` classifies from its own already-loaded registry (~4510). If the file changes between the two, the reservation and the record can carry different kinds. The test `test_worker_record_carries_kind` builds a local `registry` dict that is never passed to `_worker_record_start`. It passes only if the on-disk registry marks `api` as shell=False and write=False, and the patch does not show a `REGISTRY_PATH` patch. So the test does not exercise the dict it claims to.

4. **medium (unverified), V9/V12, `tools/autoos-agent.py` ~4137.** `"kind": kind` is passed into `redact_record(...)`. The patch does not show `redact_record`. If it whitelists keys and drops `kind`, the record counts as tool-using. The toolless cap is then bypassed, because `live_worker_count_by_kind` excludes kindless records. Needs the `redact_record` source to settle.

5. **low, V10, `tools/autoos-agent.py` ~4408–4410.** If `_reserve_free_slot` returns None (placeholder write fails), `host_admission_claim` returns `(None, None)` and the run is admitted unreserved. The base code had the same shape. The worker record would also fail to write in that case, so the practical exposure is small.

6. **low, V2, `tools/autoos-agent.py` ~4164.** `host_admission_commit_max` has no `minimum`. With `commit_charge_max_pct: 0` on win32, `commit_pct >= 0` is always true, so every Windows launch is refused. Values above 100 disable the ceiling. This is operator config, so the impact is limited.

7. **low, V4, `tools/autoos-agent.py` ~4242–4245.** `host_run_kind` calls `(registry or {}).get(...)` with no dict guard. A non-empty JSON list registry raises AttributeError, and an unhashable client value raises TypeError. Both crash rather than refuse. The earlier registry use in `cmd_run` may already fail on these inputs; the patch does not show that.

8. **low, V9.** `kind` is read from the same file-based record store as `state`. Any same-user writer can change `kind` to `tool-using` and undercount toolless. This is the existing trust boundary, not new in this change.

9. **Not verified: stamps.** The patch contains neither the sandbox base sha nor the HEAD sha, and I found no mismatch. The sha line below is quoted from the prompt, not checked against the patch.

Quoted stamp line: sandbox base: 4359016e391cac2d4abd580aac6cfc73d82bd175 HEAD cd52c779d48dfd845e9dee2575682e6422f9cfdf

VERDICT: ACCEPT cd52c779d48dfd845e9dee2575682e6422f9cfdf
