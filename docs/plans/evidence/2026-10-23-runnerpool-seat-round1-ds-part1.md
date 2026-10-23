# seat ds-part1 on cd52c779 (round 1, before fixes)

sandbox base: 4359016e391cac2d4abd580aac6cfc73d82bd175 HEAD cd52c779d48dfd845e9dee2575682e6422f9cfdf
> project tests unrunnable: missing python modules: mcp

## 1. V12/V11 â€” the claim's kind and the record's kind are derived from two different registries; the handoff can lose the toolless kind entirely (minor, real)
`tools/autoos-agent.py:14562` derives the admission kind from `registry` â€” which is `load_registry(getattr(args,"registry",None) or REGISTRY_PATH)` (14130), i.e. the documented `run --registry` flag (`tools/autoos-agent.py:15516`). `tools/autoos-agent.py:13361` (`_worker_record_start`, line added by this diff) instead reloads `REGISTRY_PATH` unconditionally and writes `"kind": kind` at 13367.

Concrete failing input: a run with `--registry /tmp/alt.json` where `alt.json` declares `clients.opencode.capabilities={shell:false,write:false}` (opencode is tool-using in the shipped file). Claim kind = `toolless`; record kind = `tool-using`. `free_reservation_release(host_claim)` at 14698 then drops the toolless placeholder, so the run stops counting toward the toolless cap the moment it starts. Measured on the sandbox head:

```
claim kind with --registry ALT  : toolless
record kind (shipped REGISTRY)  : tool-using
worker record kind written       : tool-using
claim #2 (admitted as toolless)  : None placeholder kind: toolless
toolless live before release     : 1
toolless live after handover     : 0
```

Effect: `live_worker_count_by_kind(dir,"toolless")` never accumulates, so repeated "toolless" launches are admitted until the **total** cap (10 shipped / 20 win32) instead of the 4 kind cap. The mirror direction (alt marks a client tool-using) over-charges the kind tier and produces premature `rc 13`. Normal runs (no `--registry`, same file both loads) are unaffected; the shipped registry is self-consistent. Fix: pass the registry (or the already-computed `kind`) into `_worker_record_start` instead of reloading.

## 2. V1 â€” duplicate `host_admission_config`; the shadowed definition is the weaker one and its docstring states an invariant the live code does not implement (minor, latent)
Two top-level definitions: `tools/autoos-agent.py:13587` (dead) and `:13630` (live). The dead body calls `_host_admission_value(registry, platform, field, default)` with **no** `minimum`; the live body passes `minimum=1` per field. The dead docstring (13596-13597) claims *"If ANY top-level field is invalid â€¦ BOTH fall back to code defaults (6, 6144) - fail closed for the entire section"* â€” false for the live code and for the test that asserts the opposite (`test_host_admission_config_top_level_invalid_falls_back_to_code_defaults`). Measured: `host_admission_config({"host_admission":{"max_live_workers":0,"mem_available_floor_mb":4608}}, "linux")` â†’ `(6, 4608)`. Today the safe definition wins only because it is defined last: any de-duplication that keeps `13587`, or a reader who trusts the strong comment as the contract, silently turns `max_live_workers: 0` into "refuse every spawn" and `mem_available_floor_mb: -1` into "memory guard off". Delete 13587-13602 and state the per-field rule in the surviving docstring.

## 3. V2 residual â€” `commit_charge_max_pct` gets no bound; `0`/`-1` refuse every Windows launch, `>100` disables the ceiling (minor)
`host_admission_commit_max` (`:13605`) calls `_host_admission_value(..., None)` â€” the `minimum` the diff added for cap/floor is not applied to the percentage. Measured with a healthy status `(32000 MB, 10.0%)` and `by_platform.win32.commit_charge_max_pct` of `0` / `-1`:

```
commit_charge_max_pct=0    -> host admission: 0 live workers, cap 6; MemAvailable 32000 MB, â€¦ the commit-charge ceiling reached
commit_charge_max_pct=-1   -> â€¦ the commit-charge ceiling reached
commit_charge_max_pct=800  -> ADMIT          (ceiling silently gone)
```

The inclusive boundary itself is correct (79.9 % admits, 80.0 % refuses â€” V6 clean). Needs registry write access, hence minor; bound it (1..100, or treat `>=100` as "no ceiling" explicitly).

## 4. V12 â€” the MCP spawn preflight does not pass the kind, re-creating the split answer the code blames for a past bug (minor)
`tools/autoos_agent_mcp.py:1217` calls `agent.host_admission_refusal()` with no `kind`, so an `api` (toolless) spawn is judged on the tool-using pair â€” 20 / 12288 MB + commit ceiling on win32 â€” instead of 4 / 1024 MB. With 4 toolless workers already live it admits and the runner then exits 13; with ~4 GB free physical it rejects a spawn the CLI would admit. The comment at 1208-1216 names exactly this split ("admitted here, refused there, and the caller read 'spawned'") as the bug being fixed. Same shape at `tools/runner_pool.py:545` (pool preflight can never use the toolless floor). Fix: `host_admission_refusal(kind=host_run_kind(client, registry))` at both call sites. Not a cap *bypass* â€” the real claim still enforces.

## 5. V12 (test) â€” `test_worker_record_carries_kind` cannot detect finding 1 (minor)
`tests/test_autoos_spawner.py:27143-27155` builds a `registry` dict (27148-27152) that production never receives â€” `_worker_record_start` takes no registry argument â€” so the fixture is dead and the test passes only because the *shipped* file declares `api` toolless. It would stay green under the exact `--registry` divergence above. Make `_worker_record_start` take the registry and assert against it (or patch `REGISTRY_PATH`).

## 6. Registry contract â€” `host_admission.kinds` is not declared in the schema (minor)
`catalog/ai-registry.schema.json:36-50` declares `host_admission` with `additionalProperties: false` and only `$comment` / `max_live_workers` / `mem_available_floor_mb`; the new `kinds` (and the pre-existing `by_platform`) are undeclared. Nothing goes red because `tools/registry.py:_walk_required` only walks `required` keys, so the only validation of the new field is the code-side fallback. The `clients` description at schema line 34 also still lists eight-era names minus `api`.

## 7. V6/V7 â€” the new refusal docstring claims a Windows fail-closed memory guard the code does not have (minor)
`tools/autoos-agent.py:13816-13817`: *"Unreadable meminfo stands down. An unreadable native Windows status instead refuses when the applicable memory floor is positive."* The code (13831-13851) leaves `free_mb=None` and skips the floor, and the new test `test_win32_unreadable_memory_status_stands_down_but_the_cap_still_binds` (`tests/test_autoos_spawner.py:27180`) asserts fail-open. The behaviour matches main's D-2253 baseline and is not a defect; the sentence is â€” it is the kind of docstring the brief warns not to read as proof.

## Verified clean (no finding)
- **V2**: int-but-not-bool for both cap and floor; invalid override keeps the valid sibling; code defaults bounded (6 / 6144). A bool / string / float in `by_platform.win32` falls back (existing test 26518 passes).
- **V3**: valid kind values survive `platform="win32"` (`(4, 1024)`); a missing/malformed/zero/negative kind field falls back to the whole platform-aware pair (`(10, 1024)` linux, `(20, 12288)` win32). Measured both.
- **V4**: `caps.get("shell") is False and caps.get("write") is False` (identity), resolved client only, card cannot set `kind`; the shipped registry's only toolless client is `api` (all other seven declare true/true).
- **V5**: production callers pass no `platform`/`meminfo`/`memstatus` (`cmd_run`â†’`host_admission_claim(registry, kind)`; MCP and pool pass neither); test seams stay injectable.
- **V6**: pagefile-derived commit percentage, `ullTotalPageFile == 0` â†’ `None` (no ZeroDivision), `DWLength` set from `sizeof`, refusal at exactly 80 % inclusive.
- **V8**: total cap checked before the kind cap; missing/unknown kind counts as tool-using on both the record and reservation side.
- **V10/V11**: refusal and reservation creation are inside one lock, released in `finally`; placeholders keep `pid_start` and are reaped; refusal returns `(refusal, None)` leaving no phantom claim. The `_reserve_free_slot` â†’ `None` â†’ "proceeds unreserved" path (3979-3984) is pre-existing and unchanged, so a burst on an unwritable workers dir can still exceed the kind cap until records exist â€” residual, not introduced here.
- Ran `python -m unittest -k HostAdmission tests.test_autoos_spawner` on the sandbox head: **47 tests OK**. I did not run the rest of the suite (per the seat statement, `mcp` is missing). Cosmetic: `_host_admission_value` line 13625 carries a 17-space continuation; style-only.

## Hygiene (not a blocker, but fix before merge)
`docs/plans/2026-10-15-workst-runner-pool-HANDOFF.md` (new file) carries personal absolute paths in a tracked file: `<home>/code/routing-worktrees/â€¦`, `<home>/.claude/jobs/406179e9/tmp`, `<home>/code/AutoOS/configuration/api-keys.yml`, `<home>/code/AutoOS-worktrees/reviewcall-mimo` (added lines 3, 22, 37, 42-43). `AGENTS.md` Â§1 rule 1 and the Â§7 checklist forbid "an absolute path containing a username in a tracked file" in this public repo. It names a keys *path*, not key material, and the change itself stays inside the repo (all touched blobs are repo-relative), so this is a scrub request, not a REJECT.

VERDICT: ACCEPT cd52c779d48dfd845e9dee2575682e6422f9cfdf
