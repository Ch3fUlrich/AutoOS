# AO-SPAWNER-REMOVE design (autoos:AO-SPAWNER-REMOVE)

Status: DESIGN for 1 seat (D-2739 rule 2). Base: AutoOS main da6a4f8e, plangraph main 77c9d57.
Author: AutoOS L1 (Claude Opus 5.5), 2026-10-25. Decisions: D-2576 (AutoOS keeps no spawner), D-2749 (plangraph spawner
only), D-2758(4) (full removal incl. skills/docs), D-2810 (bulk cancel applied; this design rules the group C items).

## 1. Goal and done line

AutoOS main has no code that launches a model agent: no `run`/`route`/`ps`/`list` verbs in `tools/autoos-agent.py`, no
autoos-agent MCP server, no second launcher (`runner_pool.py`, `oc_l2.py`), and no skill/doc that tells an agent to spawn through
AutoOS. Item done line (keeper): "No spawner code or MCP tool in AutoOS main; grep over fleet MCP configs shows no autoos spawn
tool; 1 non-Claude seat; merged; CI green."

Merge fence: NO slice merges before `plangraph:MODEL-ROUTE-SPAWN` is done (keeper dep) and while any AutoOS run still executes
the fleet `ao-spawn` checkout's `tools/autoos-agent.py` (check `autoos-agent ps` / process list before each merge).

## 2. What stays (contract with plangraph and the kept verbs)

| Kept surface | Why | Verified at |
|---|---|---|
| `tools/autoos_gateway_key.py` `resolve_client_key(env, path)` | plangraph loads it BY FILE PATH | plangraph `routing_support/host_keys.py:33-64` (`$AUTOOS_ROOT`, default `~/code/AutoOS`, `<root>/tools/<name>.py`) |
| `tools/keys_file.py` `read_keys(path)` | same | `host_keys.py:79-83`; `autoos_gateway_key.py` imports only `keys_file` (:46) |
| `configuration/api-keys.yml` (path, format) | plangraph reads the path | `host_keys.py:31-34` |
| `opencode.jsonc` `providers` block, valid JSONC | plangraph overlay reads ONLY `providers` | `opencode_overlay.py:63-71` (not in the original brief: this is an added keep) |
| verbs `ready`, `review-status`, `risk`, `preflight`, `inbox`, `card`, `context`, `heartbeat`, `usage`, `token-rate` | merge gate / L1 tooling, not launching | `VERB_PARSERS`/`VERB_HANDLERS` `autoos-agent.py:16408-16445` |
| `tools/autoos_resolver.py`, `tools/registry.py` (whole files) | used outside the spawner: `autoos_usage.py:104-105`, `autoos_writer_ledger.py:189`, review gate, `run_budget.py`, `registry_loader.py`, installers | inventory §3 |
| family resolution `autoos-agent.py:5820-6239` (`family_for_model` 5834, `reviewer_family` 5866, `is_final_reviewer` 6220 ...) | `review_status` 6773 uses it | grep |
| `_iso_zulu` 11359, `parse_now` 10087, `load_registry` 10108 | `cmd_ready` 7785 path | grep |
| historical spawned-run record READERS `workers_dir` 13741, `worker_writer` 13907, `read_kill_record` 12528 (+ `kill_store_dir` 12440, `kill_store_path` 12470) | the seat ledger (`autoos_writer_ledger.py:760-829`) and `autoos_recovery.record_lane` (:524-555) read old run records | grep: ledger loads `autoos-agent.py` via `spec_from_file_location` (:763-764) |
| `key_files`/`client_key`/`zen_key` 5157-5229 | ~15 probe/diag scripts via `probe_common.load_agent_module` (:254-260) | grep |

Plangraph does NOT import `autoos-agent.py` or the MCP (only a comment `opencode.jsonc:5` and `docs/PROPOSAL.md:151`).

## 3. What goes

| Surface | Lines (approx) | Callers to cut first |
|---|---|---|
| `autoos-agent.py` verbs `run` (`cmd_run` 14551-15908), `route` (10746, `route_plan_for` 10666), `ps` (14535), `list` (9908) + parsers, `CFG_VERBS` | ~13,000 of 16,473: regions 352-5156 (minus nothing kept), 5231-5819, 8197-9942, 10114-10791 (minus `parse_now`/`load_registry`), 10792-14550 (minus record readers, `_iso_zulu`) | MCP, runner_pool, oc_l2, skills |
| `tools/autoos_agent_mcp.py` (whole, 2,268) incl. `spawn`/`status`/`result`/`respond`/`cancel`/`route`/`list_*`/`ps`/`oc_*`/`l2_*`; `context`/`heartbeat` wrappers go too (CLI verbs stay) | 2,268 | `.mcp.json`, `opencode.jsonc:925-939` (+ deny rules 250-252, 405-407), `catalog/agent-harness.json:123-125,147,168`, `lib/agent_harness.py:34,183-184,406`, `openhands/agent-profiles/{orchestrator,suborchestrator}.json:12`, `configuration/oc-l1.example.json:11,34,64`, installers `lib/linux/install.sh:4773-4776,6335-6341`, `AutoOS.Install.psm1:1532-1535,3383-3387`, `oc_l1_render.py:169-200` (abs path pin), `oc_l1.py:325` (lane MCP default) |
| `tools/oc_l2.py` (1,632) | spawns through the MCP (`oc_l2.py:17,150,172,185`) | `l2_*` MCP tools (go with the MCP) |
| `tools/runner_pool.py` (682) + `runner_pool_core/gate/proc` | builds `autoos-agent.py run --isolate` (:180-189) | none outside its tests |
| spawner tests (§6) | ~25,500 of `test_autoos_spawner.py` + listed files | suite wiring |
| skill/doc teaching (§7) | rewrites, not deletions of history | — |

Stays out of scope (separate items): `oc_l1.py` lane runner itself (RT-P4-DELETIONS lists oc_l*; this design only cuts its
autoos-agent MCP default), trimming `autoos_resolver.plan()` (dead after `route` goes; follow-up note in the item's fix log, no new
item per D-2739 rule 1), historical `docs/plans`, `docs/handoff`, `CHANGELOG.md`, `logs/` mentions (~55 files, left as history).

## 4. Rulings asked by routing D-2810 (group C) and the inventory's UNSURE items

| Item / surface | Ruling | Reason |
|---|---|---|
| autoos:AO-MCP-CWD-INDEPENDENT | CANCEL | the autoos-agent MCP server is deleted (slice S3) |
| autoos:SEAT-BLIND-QUOTED | KEEP | `tools/autoos_verdict.py _blind_statement` is ready-gate code, which stays |
| autoos:AO-LEDGER-DRYRUN | KEEP | ready/ledger path stays |
| autoos:AO-READY-MULTI-BRIEF | KEEP, amend V3 | V1/V2/V4-V6 are ready-gate only. V3 ("each brief maps to a real writer run in the record") must read the PLANGRAPH run record for new runs (AutoOS writes no records after S5); old AutoOS records via `autoos_run_records` (S1) |
| autoos:AO-SPAWN-PATCH-ID | CANCEL in AutoOS, fold into AO-READY-MULTI-BRIEF V3 | its "spawner writes patch-ids into logs/kill/<run>.json" half needs the AutoOS spawner. If routing wants the patch-id record, it belongs to the plangraph spawner run record (router's call; no new AutoOS item) |
| autoos:LAUNCHER-CPU-GATE | CANCEL in AutoOS | the launcher is the AutoOS spawner. Port only if plangraph host admission lacks a load/RAM gate (router checks against plangraph admission) |
| autoos:AO-AUTO-FANOUT | KEEP | gateway auto-combo candidate cap + autoos-omniroute restart loop: gateway, not spawner |
| `prune-sandboxes` verb (3120-3185) | KEEP (small, self-contained) | still cleans legacy AutoOS sandboxes (the legacy sandbox dir still holds many). Removal later once the dir is drained |
| `oc_*` MCP tools / `oc_l1.py` | MCP tools go with S3; `oc_l1.py` stays (RT-P4-DELETIONS); its lane MCP default `["autoos-agent"]` -> `[]` in S2a | |
| `l2_*` MCP tools / `oc_l2.py` | REMOVE (S4) | spawner in effect |
| `runner_pool*` | REMOVE (S4) | second launcher |

## 5. Invariants (each slice must keep all of them; the seat checks each)

| # | Invariant | Check |
|---|---|---|
| I1 | `tools/autoos_gateway_key.py` + `tools/keys_file.py` exist at those paths, signatures `resolve_client_key(env, path)` / `read_keys(path)` unchanged, `autoos_gateway_key` imports only `keys_file` + stdlib | NEW contract test `tests/test_plangraph_host_keys_contract.py` (S1): loads both by `spec_from_file_location` exactly as `host_keys.py:37-55` does, in a temp root with only those two files copied; asserts import works and signatures match |
| I2 | `opencode.jsonc` parses as JSONC and its `providers` block is byte-identical to base | same contract test: strip-JSONC + compare `providers` to a recorded sha256 of da6a4f8e's block |
| I3 | kept verbs behave identically | their existing suites unchanged and green every slice (`test_ready_*`, `test_autoos_writer_ledger`, `test_autoos_heartbeat`, `test_autoos_recovery`, `test_autoos_risk`, `test_autoos_card`, `test_autoos_inbox`, `test_prepush`, the review/ready/ledger/preflight classes of `test_autoos_spawner.py`) |
| I4 | historical run records stay readable (seat ledger for old runs) | `autoos_run_records` tests on a fixture `logs/kill/<run>.json` + workers dir in the current format |
| I5 | no test file left unwired, no wired file missing | `tests/test_suite_wiring.py` green; each deletion unwires parts 33/34/44/17/18 + `run-tests.ps1` in the SAME slice |
| I6 | nothing in the tree calls a removed verb/tool after its slice | per-slice grep gate (§8) in the slice's tests, not by hand |
| I7 | no secret value read or printed; `configuration/api-keys.yml` untouched | diff check: file not in any slice diff |
| I8 | merge fence of §1 | L1 checks before each merge |

## 6. State/input table: callers before -> after

| Caller (input) | Before | After |
|---|---|---|
| agent wants a writer/seat | `autoos-agent.py run` / MCP `spawn` | plangraph `python -m plangraph.spawner.cli spawn` (D-2749) |
| `autoos-agent.py run/route/ps/list ...` | runs | argparse "invalid choice" exit 2 (no shim: a shim would keep teaching the old path) |
| `ready`, `review-status`, other kept verbs | run | unchanged |
| seat ledger `writer_for_run(run_id)` for an OLD AutoOS run | loads all of `autoos-agent.py` | imports `autoos_run_records` (S1), same result |
| seat ledger for a NEW (plangraph) run | not found -> current "unknown writer" path | unchanged behavior; V3 port belongs to AO-READY-MULTI-BRIEF |
| probe/diag scripts needing a client key | `probe_common.load_agent_module()` -> `client_key` | `probe_common` imports `autoos_client_keys` (S1), same result |
| plangraph `host_keys` | loads the two files | unchanged (I1) |
| harness/role config listing the autoos-agent MCP | MCP starts | entry gone; role lists without it; `lib/agent_harness.py` `SPAWNER` constant removed |
| `oc_l1` lane start | lane MCP default `["autoos-agent"]` | `[]` |

## 7. Skill/doc rewrites (S2b)

Rewrite to "spawn via the plangraph spawner" (point at plangraph's own doc, do not duplicate its how-to):
`unattended-orchestration/unattended-orchestration.md:62-73,95-97,222-243,273`; `SKILL.md:87,96-101,159 (R-coord-09),220 (R-worker-06)`
(keep 110 `ready`); `references/main-orchestrator.md:92,105,121`, `l3-routing.md:7`, `opencode-fleet-lessons.md:8`, `rule-map.md:9-33,104,
161,183-184` (R-spawn-* rows point at plangraph code or are dropped); `docs/ai/opencode-fleet.md:104,153-167,217,250,272-292`,
`docs/ai/cost-guard.md:10,133-156`, `docs/ai/memory-guard.md:138-156,223`, `docs/ai/writer-contract.md:39`, `docs/api-keys.md:172`,
`docs/routing.md:43,208-252` (keep 78/105/123), `docs/models.md:309,334,352-361`; ADR 0006 gets a "Superseded by D-2576/D-2758"
status line (ADRs are history, not rewritten). `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`: no mentions (verified by the
inventory). CHANGELOG entry per slice.

## 8. Slices (ordered; each green on its own)

Line-cap reading (GO-REQ to routing, see §9): D-2739 rule 3 counts ADDED + MODIFIED lines (<= ~400). Pure deletion hunks of
whole functions/files do not count toward the cap but each slice deletes ONE cohesive region, and every deletion is proven by the
I6 grep gate + I3/I5 suites. Without this reading the ~16k deleted lines would need ~40 slices of mechanical deletion.

| Slice | Content | Adds/mods (est) | Deletes (est) | Tests |
|---|---|---|---|---|
| S1 decouple | new `tools/autoos_run_records.py` (workers_dir, worker_writer, read_kill_record, kill_store_*) and `tools/autoos_client_keys.py` (key_files, client_key, zen_key, ENV_KEY); `autoos-agent.py` re-imports them (no behavior change); repoint `autoos_writer_ledger.py:760-829`, `autoos_recovery.py:524-555`, `probe_common.py:254-260` (+ the ~15 probe scripts if they touch more than `client_key`/`key_files`/`ENV_KEY`); contract test I1+I2; record-reader test I4 | ~300 | ~200 (moved) | ledger, recovery, probes, new tests, suite wiring |
| S2a unwire config | `.mcp.json`, `opencode.jsonc` MCP entry + deny rules (providers untouched, I2), `catalog/agent-harness.json`, `lib/agent_harness.py` SPAWNER, openhands profiles, `oc-l1.example.json`, installers (bash + psm1), `oc_l1.py:325` default, `oc_l1_render.py:169-200` | ~250 | ~80 | harness/catalog/installer parts 17/18, `test_oc_l1_render` |
| S2b skills/docs | §7 | ~350 | ~150 | docs checks (`check-changelog-docs` if present), skill size cap |
| S3 MCP server | delete `tools/autoos_agent_mcp.py` + `test_agent_mcp_tool_profile`, `test_mcp_stdin_devnull`, `test_mcp_writer_policy`; unwire parts 33:961, 34:4911, `run-tests.ps1` | ~40 | ~2,600 | wiring, part 33/34 |
| S4 other launchers | delete `oc_l2.py`, `runner_pool*.py` + `test_oc_l2`, 11 `test_runner_pool*`; unwire part 33:662-674; drop `autoos_recovery` import in oc_l2 side only | ~40 | ~4,000 | wiring, recovery |
| S5a route/list/ps | delete verbs `route`, `list`, `ps` + parsers + `cmd_list`/`cmd_ps`/`cmd_route`/`route_plan_for`/`list_workers`/`worker_tree` and region 10114-10791 minus kept; their tests | ~80 | ~2,500 | I3 suites, part 33 inline dry-runs |
| S5b run | delete `run` + `CFG_VERBS` + regions 352-5156, 5231-5819, 8197-9907, 10792-14550 minus kept, unused imports 308-328; spawn-path test files (§3 list); part 33:555-655,657,697,906,918,962,1017-1023,1240-1260; part 44:939 | ~120 | ~10,500 | I3 suites, wiring |
| S5c test split | move the ReviewStatus/FinalReviewFamily/Ledger*/Ready*/Preflight classes (~10410-14990) of `test_autoos_spawner.py` to `tests/test_autoos_ready_gate.py` (git rename-detect: content unchanged), delete the rest | ~60 | ~25,500 | wiring, I3 |
| S6 gate | permanent CI check: no `autoos-agent(.py)? (run|route|spawn)` / `autoos_agent_mcp` outside history dirs; L1 greps the fleet MCP configs on each fleet host (read only) for the done line | ~60 | 0 | new check test |

Order rationale: S1 first so every later deletion leaves no importer behind; S2 before S3 so nothing starts the MCP when it is
deleted; S5c last because S5a/S5b delete tests class by class. S2b can run in parallel with S1 (no shared files).
Writer per slice: plangraph spawner (D-2749); fixer mimo/mimo-v2.6-pro (D-2739 rule 5); 2 text seats per slice of families !=
writer. Main merged into the lane ONCE before each slice's final review (rule 7).

## 9. Open questions for routing

1. GO-REQ: the deletion line-cap reading in §8 (added+modified <= 400; pure deletions uncapped but one cohesive region per slice).
2. Fleet MCP configs outside the repo (user `~/.claude.json` / project `.mcp.json` files, host opencode configs) that still
   register autoos-agent: the done line needs them gone. Editing a user/host MCP config is harness config, not auth, but it touches
   other sessions' tools. Proposal: S6 lists them (read only); the owning L1 of each host removes its own entry after S3 merges.
3. AO-SPAWN-PATCH-ID patch-id record: plangraph spawner run record or dropped? (§4)
</content>
</invoke>
