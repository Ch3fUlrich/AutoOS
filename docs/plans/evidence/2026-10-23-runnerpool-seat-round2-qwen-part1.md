# seat qwen-part1 on 7e960334 (round 2)

sandbox base: 90270a46c3c9d552a3274bdaa773369536523fd8 HEAD 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e
project tests unrunnable: missing python modules: mcp

## Part 1 Review: Host Admission (tools/autoos-agent.py)

**V1: host_admission_config** â€” Clean. Only one definition exists at line 13618. The earlier seats' finding about duplicate definitions was against an older SHA.

**V2: _host_admission_value** â€” Clean. Validates `isinstance(value, int) and not isinstance(value, bool)` with `minimum=1` for cap/floor and `minimum=1, maximum=100` for commit_charge_max_pct (line 13594-13615).

**V3: host_admission_kind_config** â€” Clean. Falls back to the platform-aware host pair for any malformed kind field, including zero/negative/bool/string values (line 13633-13660). Uses `min(cap, top_pair[0])` to clamp kind cap to platform total.

**V4: host_run_kind** â€” Clean. Uses identity comparison `caps.get("shell") is False and caps.get("write") is False` (line 13681). Returns "tool-using" for any non-dict registry, client, capabilities, or non-bool values.

**V5: host_admission_refusal** â€” Clean for production. Production callers (cmd_run at line 14573, MCP spawn at line 1224, pool _build_real_deps at line 530) do not pass platform/meminfo/memstatus overrides. Test seams remain injectable.

**V6: windows_memory_status** â€” Clean. Uses `wintypes.DWORD` for dwLength/dwMemoryLoad, `c_ulonglong` for 64-bit fields (line 13703-13714). Handles `ullTotalPageFile == 0` by returning None for commit_pct. Validates `0 < ullAvailPhys <= ullTotalPhys`. Uses pagefile-derived commit percentage, not physical memory load.

**V7: mem_available_mb** â€” Clean. Converts kB to MB correctly (`int(fields[1]) // 1024`). The live cap still binds when memory is unknown (line 13846).

**V8: live_worker_count** â€” Clean. Counts both running records and live reservations (line 13752-13757). Total cap checked before kind cap (line 13846 before 13856).

**V9: live_worker_count_by_kind** â€” Clean. Missing/unknown kind counts as tool-using (line 13780: requires `isinstance(row.get("kind"), str) and row["kind"] == "toolless"`). Both counts happen within the admission lock.

**V10: host_admission_claim** â€” Clean. Refusal and reservation creation are inside one lock (line 13799-13810), released in finally.

**V11: _reserve_free_slot** â€” Clean. Placeholder carries `kind` (line 3976), `pid_start` for liveness checks, and is reaped if stale. Refusal returns `(refusal, None)` leaving no phantom claim.

**V12: _worker_record_start** â€” Clean. Kind is derived in cmd_run (line 14572), passed to host_admission_claim (line 14573), and passed to _worker_record_start (line 14695). The kind survives handover.

## Verified Clean

- Schema properly declares `kinds` and `by_platform` with correct validation (catalog/ai-registry.schema.json lines 42-63)
- MCP spawn passes kind to host_admission_refusal (autoos_agent_mcp.py line 1224)
- Pool _build_real_deps derives kind from queued job's client, defaults to 'tool-using' on failure (runner_pool.py lines 522-530)
- _cmd_slot strips AUTOOS_ADMISSION_OFF case-insensitively from slot environment (runner_pool.py lines 545-547)

## Earlier Seats' Findings Not Applicable

The earlier seats (ds-part2, haiku-part2) reviewed SHA cd52c779, not the current HEAD 7e960334. Their findings about `_cmd_ssh_gate` bypassing the gate and `run_one` ignoring the claim return value are not valid for this HEAD:
- `_cmd_ssh_gate` now calls `gate.run_gate` (runner_pool.py line 6140)
- `run_one` uses the returned `claimed_path` from `rpc.claim()` (runner_pool.py lines 6003-6007)
- `_run_claimed` validates `job['id'] == claimed_path.stem` (runner_pool.py line 6028)

VERDICT: ACCEPT 7e9603340bde4b2ef7b0f8e0cd61214b25c0c70e
