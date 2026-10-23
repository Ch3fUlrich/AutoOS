# seat haiku-part2 on cd52c779 (round 1, before fixes)

sandbox base: 4359016e391cac2d4abd580aac6cfc73d82bd175 HEAD cd52c779d48dfd845e9dee2575682e6422f9cfdf

**Findings**

1. **HIGH, V3/V4 (`tools/runner_pool.py:457-487`).** The production `ssh-gate` route bypasses the gate. `main()` (line ~463) sends `ssh-gate` to `_cmd_ssh_gate`, which never calls `run_gate`, `parse_submit`, `validate_card`, `_check_path` or `read_capped`. It calls `stdin.read()` (line 471) with no cap and no timeout, then writes the client's JSON object into `queue/` (line 487) with no `id`. Input: `SSH_ORIGINAL_COMMAND=submit` with stdin `{"card":{"client":["x"],"paths":["../../Windows"]}}` is queued unvalidated. An oversized or never-closed stdin pins the process and exhausts memory. `test_ssh_gate_submit` in `tests/test_runner_pool_cli.py` (patch ~1861) asserts exactly this acceptance.

2. **HIGH crash, V2/V3 (`runner_pool.py:317-318`, `327`).** `run_one` calls `rpc.claim()` and ignores its return value, then computes `claimed_path = slot_dir / f"{job['id']}.json"` with no guard. Any queued job without `id` raises an uncaught `KeyError: [autoos:redacted] which is what finding 1 produces. `_cmd_slot` has no `try`, so the slot loop dies. A clean refusal was due here. Repro to run (in a temp spool, from the sandbox root):
   `python -I -c "import io,json,sys,tempfile; sys.path.insert(0,'tools'); import runner_pool as p; td=tempfile.mkdtemp(); env={'LOCALAPPDATA':td}; p.main(['ssh-gate','--submitter','lane1'],env,io.BytesIO(json.dumps({'brief':'x','card':{'client':'api'}}).encode()),io.StringIO()); r=p.spool_root(env); p.run_one(r,1,p.Deps(spawn=lambda a,l,e:0))"`
   Expected by trace: `KeyError: [autoos:redacted] at `runner_pool.py` line 318. Stub deps are used so the real admission check does not return early.

3. **HIGH, V2 (`runner_pool.py:317-323`, `389-393`).** After `claim()` moves a different job B into this slot, `claimed_path` falls back to `slot/B.json` (lines 320-323). `run_one` then runs job A's argv and unlinks `claimed_path` (B) on completion. Job B is deleted without running, and A may also run in another slot. The same fallback applies to the post-claim memory-recheck path (lines 326-334), which writes A back to the queue and unlinks B.

4. **MEDIUM, V6 (`runner_pool.py:172`, `181`, `409`).** `job_argv` does no type checks: `client`, `model` and `title` pass through as-is. A list-valued `client` (accepted by `validate_card` at `runner_pool_gate.py:88`) raises `TypeError` in `Popen`, which is outside any `try` in `run_one`. The length check at line 181 counts Python characters, not UTF-16 units or `list2cmdline` escapes. A brief of 20000 `"` characters passes the check but expands past the 32767-unit Windows limit, so `Popen` raises `OSError` and the slot crashes.

5. **MEDIUM, V8 (`runner_pool.py:491-505`, `516`).** `_find_slot` compares `data.get('job') == jobid`, but `slot.json` stores the full job dict (`write_slot_state(..., job, ...)` at line 211). Kill therefore always reports `no such job`. It fails closed, but kill does not work.

6. **MEDIUM, status (`runner_pool.py:443`).** `build_status(root)` omits the required `now`, `pid_alive` and `filetime_of` arguments. The `TypeError` is swallowed, so `status` always reports zero slots and an empty queue.

7. **MEDIUM, V5 (`runner_pool_gate.py:75-86`, `runner_pool.py:149-181`).** `_check_path` does not reject `:`, so NTFS alternate streams such as `src/a.py:stream` pass. Lexical checks are the only confinement. `job_argv` never reads `card.paths` or `spec`, so the validated restrictions are silently dropped.

8. **MEDIUM, V11 (`runner_pool.py:172-178`, `218`).** The production path does not use `api_client.build_command_api`. `card.model` is not checked against `API_MODELS`, and `default_risk_of` returns R0 for any `client == 'api'`, so an arbitrary model is classed as tool-less with no deep sampling.

9. **MEDIUM, V10 (`runner_pool.py:405-409`, `534-536`).** `_build_real_deps` spawns through plain `subprocess.Popen`. `create_job_nt` and `spawn_in_job` in `runner_pool_proc.py` have no production caller, so there is no Job Object containment. The known-limits list covers kill-by-pid, inheritance, ActiveProcessLimit, NTFS case and the env allow-list, but not the absence of containment. The design text (patch ~line 229) describes job containment as the design. I could not read the full CHANGELOG entry (line 7 truncated), so I did not check whether it claims containment.

10. **LOW.** `api_client.py:104-111`: `confine` runs before `os.open`, and `O_NOFOLLOW` only covers the final component, so a parent-directory swap between the two is not caught. `runner_pool_proc.py:77` requires `kill_job` to get exactly the `run.private` identity, which it does. `kill_job` raises `ValueError` on corrupt `run.private`, but `_cmd_kill` does not catch it. `runner_pool_gate.py:192` calls unguarded `json.loads` on `status.json`, though nothing writes that file.

**Passes checked:** `run_one` fails closed on `None` memory (lines 281, 326). A failed admission read is refused, not allowed (`_build_real_deps`, lines 534-552). `strip_env` is case-insensitive and is passed explicitly to `Popen` (`runner_pool_core.py:37`, `runner_pool.py:360`). `kill_job` never falls back to `job.json` and does not kill when identity is missing or mismatched. `_write_run_private` records the child PID's creation time.

**Missing evidence:** the test suite cannot run here (`mcp` is not installed). Nothing was executed, so every finding above is from reading the code. Findings 1-2 are the basis for REJECT. If running the repro in finding 2 does not produce the `KeyError`, the verdict should move to ACCEPT with findings 1, 3, 4 and 7-10 still open.

VERDICT: REJECT cd52c779d48dfd845e9dee2575682e6422f9cfdf
