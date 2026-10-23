# seat haiku-part1 on 7e960334 (round 2)

I read the `tools/autoos-agent.py` hunks (REVIEW-DIFF.patch lines 4846–5325) and the `autoos_agent_mcp.py` hunk. Nothing was run: the project tests are unrunnable here (missing `mcp`), and I only read the patch. I did not write the plan file, since this is a review.

**Findings, most severe first**

1. **High, V6, weakened readable-memory guard (fail-open on an exhausted host).** `tools/autoos-agent.py` `windows_memory_status`, REVIEW-DIFF.patch:5084, returns `None` when `ullAvailPhys == 0`. `_sane_memstatus` (patch:5039–5047, called from :5094) also returns `None` whenever available MB floors to 0, so the commit-charge reading is dropped with it. The baseline returned `(0, commit_pct)`, which `host_admission_refusal` refused against the 12288 MB floor.
   - Failing input: `GlobalMemoryStatusEx` reports `ullAvailPhys=0`, `ullTotalPhys=8 GiB`, `ullTotalPageFile=20 GiB`, `ullAvailPageFile=0`. The new code gives `free_mb=None` and `commit_pct=None`, so with `live < cap` the run is admitted. The baseline refused it.
   - This is a readable reading, not an unreadable one, so the D-2253 fail-open does not cover it.
   - Fix: keep zero available memory as a real reading (`0 <= avail`, reject only a total of 0 or avail > total) and keep the commit reading when only the physical value is bad.

2. **Medium, mandatory escape removal not done (brief "known limits" says it is not deferred).** `host_admission_refusal` still returns `None` on `os.environ.get(ADMISSION_OFF_ENV) == "1"` (patch:5239, unchanged context). The spawner process reads its own environment, so `AUTOOS_ADMISSION_OFF=1 autoos-agent ...` with `live == cap` admits. The diff strips the variable from child environments (`runner_pool_gate`, slot and SSH paths), but not from the admission check itself.
   - The same applies to the `AUTOOS_MEMINFO_PATH` seam (patch:5252–5253 and `mem_available_mb`). On a real win32 launch, setting it skips `GlobalMemoryStatusEx` and the commit check. Failing input: a file containing `MemAvailable: 99999999 kB` admits a run on a host with no free memory. This predates the diff, but it is still a bypass of readable-memory checks.
   - The brief's rule is that this removal is mandatory, so the diff is not complete without it.

3. **Medium, unverified, V12.** `_worker_record_start` uses `kind` from the admission claim (`cmd_run`, patch:5310–5311, :5320–5321). If `plan["client"]` can change inside the fallthrough or cooldown loop, the worker record can say `toolless` while a tool-using client runs. The loop code is not in the patch, so I cannot settle this.

4. **Medium, unverified, V12/V9.** `redact_record` (patch:4899) is not in the patch. If it drops unknown keys, `kind` disappears. The record then reads as tool-using in `list_workers`, and the toolless cap undercounts. Confirm against the `redact_record` source.

5. **Low, V9.** `kind` is read from worker records, which the code comments say workers can write. A tool-using worker can set its own record to `kind="toolless"`. That only inflates the toolless lane, so it is a denial of service on that lane, not a bypass of the total cap.

6. **Low, V8.** `live_worker_count` (patch:5131) counts non-dict rows as running, while `live_worker_count_by_kind` (patch:5158) skips them. Both are conservative, and `visible_workers` returns dicts, so this is inert.

7. **Low, pre-existing, V10.** A failed lock take or a failed placeholder write still admits the run unreserved (`_reserve_free_slot` returns `None`, patch:4862–4881). The diff keeps the documented best-effort behaviour. It does not add a new path.

8. **Minor, V2.** `max_live_workers` and `mem_available_floor_mb` have a minimum of 1 but no maximum. A large operator floor refuses every tool-using run. That is misconfiguration, not an attacker path.

**What checks out**
- `host_admission_config` has a single definition (the old one is removed, patch:4919–4931), and per-field platform→host→default fallback is correct.
- `_host_admission_value` rejects bool, string and out-of-range values per field.
- `host_admission_kind_config` falls back to the whole host pair on an invalid kind field, and clamps the kind cap to the total cap.
- `host_run_kind` compares `caps.get(...) is False` for both capabilities; unknown or malformed clients are tool-using.
- The total cap is checked before the toolless cap and floor, and the whole claim runs under one lock with unlock in `finally`.
- The commit ceiling is inclusive (`>=`) at 80.
- `MEMORYSTATUSEX` field widths and `dwLength` match the Win32 struct.

**Not verified:** the `redact_record` body, the `cmd_run` fallthrough loop, the `client` variable in `spawn()` (`autoos_agent_mcp.py`), and whether `HostAdmissionWindowsTests` covers finding 1. Neither the sandbox base nor the HEAD sha appears in the visible patch text, so I could not check the stamp against the patch itself.

I am not rejecting. Findings 1 and 2 fall outside the REJECT list in the criteria, but they are not minor by the brief's own terms, so I am holding rather than accepting.

VERDICT: HOLD 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e
