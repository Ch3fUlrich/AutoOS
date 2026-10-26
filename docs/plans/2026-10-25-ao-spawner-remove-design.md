# AO-SPAWNER-REMOVE design (autoos:AO-SPAWNER-REMOVE)

Status: DESIGN v3 for 1 seat (D-2739 rule 2). v1 (331eedd0) and v2 (4b054158) got REQUEST_CHANGES from the qwen design
seat (r1 run-7df2c280: 5 HIGH, 8 MED, 5 LOW; r2 run-4d08cef3: all r1 FIXED, 2 HIGH, 2 MED, 3 LOW new); every finding is
folded in below (map in §10). Base: AutoOS main da6a4f8e,
plangraph main d66bf9e5 (plangraph excerpts quoted in Appendix A so the seat can verify them without a plangraph checkout).
Author: AutoOS L1 (Claude Opus 5.5), 2026-10-26. Decisions: D-2576 (AutoOS keeps no spawner), D-2749 (plangraph spawner
only), D-2758(4) (full removal incl. skills/docs), D-2810 (bulk cancel applied), D-2813 (rulings, §9).

## 1. Goal and done line

AutoOS main has no code that launches a model agent: no `run`/`route`/`ps`/`list` verbs in `tools/autoos-agent.py`, no
autoos-agent MCP server, no second launcher (`runner_pool.py`, `oc_l2.py`), and no live skill/doc/comment that tells an agent
to spawn through AutoOS. Item done line (keeper): "No spawner code or MCP tool in AutoOS main; grep over fleet MCP configs shows
no autoos spawn tool; 1 non-Claude seat; merged; CI green."

Merge fence: NO slice merges before `plangraph:MODEL-ROUTE-SPAWN` is done (keeper dep) and while any AutoOS run still executes
the fleet `ao-spawn` checkout's `tools/autoos-agent.py` (process list before each merge). S2b (docs that point agents at the
plangraph spawner) is the slice this fence matters most for: it merges only after MODEL-ROUTE-SPAWN is done.

## 2. What stays (contract with plangraph and the kept verbs)

| Kept surface | Why | Verified at |
|---|---|---|
| `tools/autoos_gateway_key.py` `resolve_client_key(env, path)` | plangraph loads it BY FILE PATH | App. A.1 (`host_keys.py:37-64`) |
| `tools/keys_file.py` `read_keys(path)` | same | App. A.1 (`host_keys.py:73-83`); `autoos_gateway_key.py` imports only `keys_file` + stdlib (:37-46) |
| `configuration/api-keys.yml` (path, format) | plangraph reads the path | App. A.1 (`host_keys.py:31-34`) |
| `opencode.jsonc` `providers` block, valid JSONC | plangraph overlay reads ONLY `providers` | App. A.2 (`opencode_overlay.py:63-71`) |
| verbs `ready`, `review-status`, `risk`, `preflight`, `inbox`, `card`, `context`, `heartbeat`, `usage`, `token-rate`, `prune-sandboxes` | merge gate / L1 tooling / legacy sandbox cleanup, not launching | `VERB_PARSERS`/`VERB_HANDLERS` `autoos-agent.py:16408-16445` |
| `tools/autoos_resolver.py`, `tools/registry.py`, `tools/run_budget.py` (whole files) | used outside the spawner: usage, ledger, review gate, cost-gate tooling (`cost-gate-refresh.py`, `test_cost_gate_*`, `test_run_budget*`) | grep |
| `tools/autoos-ask.py` | kept file; S5b-run changes only its refusal text (§6) | — |
| every `autoos-agent.py` symbol reachable from KEEP_ROOTS (§5 I9) | the kept verbs and external by-path loaders need them | the I9 closure test, not hand-made line lists |

KEEP_ROOTS (the closure test's roots, S1): `main`, the kept verbs' parser + handler functions, `VERB_PARSERS`, `VERB_HANDLERS`,
`ROOT`, `load_jsonc`, and the attributes external code reads from the module object: `worker_writer`, `workers_dir`,
`read_kill_record` (seat ledger `autoos_writer_ledger.py:760-829`, `autoos_recovery.py:538-556`), `client_key`, `key_files`
(15 tools via `probe_common.load_agent_module` :254-260, e.g. `diag-key-source.py`, `diag-models.py`, `probe-free.py`,
`probe-reasoning-*.py`, `probe-effort/recall/sweep/toolcalls.py`, all `gw-*.py`; all read only `client_key`/`key_files`). `ENV_KEY` is NOT a root: it does not exist in `autoos-agent.py` (it lives in `oc_l1_render.py:75` /
`sync-router-tiers.py:79`) and `diag-key-source.py:21` already reads it with `hasattr`.
Plangraph does NOT import `autoos-agent.py` or the MCP (only a comment `opencode.jsonc:5` and `docs/PROPOSAL.md:151`).

## 3. What goes

Deletion contract = SYMBOLS, not line numbers. A slice deletes the named top-level symbols (functions, classes, constants,
parser entries) and any symbol that the I9 closure test reports as unreachable from KEEP_ROOTS after that slice. Line numbers
below are orientation at base da6a4f8e only; every earlier slice shifts them, so writers and seats work from symbol names.

| Surface | Symbols / files | Callers to cut first |
|---|---|---|
| `autoos-agent.py` verbs `run`, `route`, `ps`, `list` | `cmd_run`, `cmd_route`, `route_plan_for`, `cmd_ps`, `cmd_list`, `list_workers`, `worker_tree`, their parser builders and `VERB_*` entries, `CFG_VERBS` (and its use in `main()`, see S5b-run), plus everything the closure test then reports unreachable (~13k of 16,473 lines) | MCP, runner_pool, oc_l2, skills |
| `tools/autoos_agent_mcp.py` (whole, 2,268) | incl. `spawn`/`status`/`result`/`respond`/`cancel`/`route`/`list_*`/`ps`/`oc_*`/`l2_*` and the `context`/`heartbeat` MCP wrappers (CLI verbs stay) | §3a configs, §3b tests |
| `tools/oc_l2.py` (1,632) | whole file | `l2_*` MCP tools (go with the MCP) |
| `tools/runner_pool.py` (682) + `runner_pool_core/gate/proc` | whole files | none outside their tests |
| spawn-path tests | §3b, enumerated | suite wiring |
| skill/doc/comment teaching | §7 rewrites (history dirs stay as history) | — |

### 3a. Config/consumers of the MCP (all cut in S2a unless noted)
`.mcp.json`; `opencode.jsonc:925-939` MCP entry + deny rules `250-252, 405-407` (providers untouched, I2);
`catalog/agent-harness.json:123-125,147,168`; `lib/agent_harness.py:34 (SPAWNER), 183-184, 196-197, 406` (semantics in §6);
`openhands/agent-profiles/{orchestrator,suborchestrator}.json:12`; `configuration/oc-l1.example.json:8,11,34,64`;
`lib/linux/install.sh:4773-4776,6335-6341`; `AutoOS.Install.psm1:1532-1535,3383-3387`; `tools/oc_l1_render.py:25,84,169-200,
261,265`; `tools/oc_l1.py:325-333` (default AND validator, §6); `catalog/ai-registry.json:2392` risk rule keyed to
`tools/autoos_agent_mcp.py` (S3: repoint to the kept files it guarded or drop with the file; `test_autoos_risk.py:988,1052` and
`test_autoos_writer_ledger.py:1062` path strings updated in the same slice); `tests/linux/17-ai-routing.sh:2389` pinned
MCP-server-name list (drop `autoos-agent` and its `pin-ok` in S2a).

### 3b. Test files (enumerated; disposition per slice)
| File | Touches | Slice |
|---|---|---|
| `tests/test_autoos_spawner.py` (~30k) | kept classes (ReviewStatus, FinalReviewFamily, EvidenceGate, Ledger*, Ready*, Preflight, CIRunStatus; :10410-15008, ~7,100 lines) + spawn classes; module-level `import autoos_agent_mcp` (:40); every `mcp_server` use is in classes before ~:1850 (e.g. `McpToolTests` :1491) | NO move (a 7k-line move would count as adds): S3 deletes the classes that use `mcp_server` (by class name; the writer greps `mcp_server`) and the :40 import; the spawn classes die with their verbs in S5a/S5b-route/S5b-run; the kept classes stay in place. The file keeps its name (a rename is a later cosmetic item, not this one) |
| `tests/test_autoos_heartbeat.py` | `import autoos_agent_mcp` (:41); class `McpHeartbeatTests` (:1184 to end of file; incl. the `autoos-agent.py run` text at :1212) | S3: drop the import and the WHOLE class by its boundary; CLI heartbeat cases stay |
| `tests/test_t2_record_pin.py` | module-level `import autoos_agent_mcp` (:48); subprocess `autoos-agent.py run --dry-run` (:70,105,128); wired `33:843`, `run-tests.ps1:5468` | S3: drop the import and its MCP cases; S5b-run: delete the `run` cases (or the file if none remain) + unwire |
| `tests/test_spawn_pin_allowed_id.py` | subprocess `autoos-agent.py run` (:105,128), asserts the `autoos-agent.py list` hint (:132); wired `33:1020` | S5b-run: delete (the whole file is spawn-pin) + unwire 33:1020 |
| `tests/test_opencode_bash_guard.py:373` | asserts the guard allows `python3 tools/autoos-agent.py list` (guard role survives) | S5a: change the example to a kept verb (`autoos-agent.py ready`) |
| `tests/test_agent_mcp_tool_profile.py` | renders the MCP entry (:45, :284-301) | S3 (deleted; S2a must not break it first: S2a keeps the entry's render test green by deleting this file's config-dependent cases in S2a) |
| `tests/test_mcp_stdin_devnull.py`, `tests/test_mcp_writer_policy.py` | MCP server | S3 |
| `tests/test_worker_scope_spawn_paths.py` (AST-reads MCP :49,250; wired `44-memguard.sh:939`) | spawner | S3 (+ unwire part 44:939) |
| `tests/test_codex_newest.py:117,182,197`, `tests/test_review_call.py:730-798` | MCP/spawn cases | S3: delete those cases; rest of each file stays |
| `tests/test_run_budget_spawner.py` (calls `agent.cmd_run`) | spawner | S5b-run (deleted; `test_run_budget*.py` stay) |
| `tests/test_oc_l2.py` (imports MCP :1279,2032; wired `33-documentation.sh:1219-1220`) | oc_l2 | S3 drops its 2 MCP-import cases; file deleted in S4 (+ unwire 33:1219-1220) |
| 11 `tests/test_runner_pool*.py` | runner_pool | S4 (+ unwire 33:662-674) |
| kept-verb suites `test_autoos_context.py`, `test_autoos_tokenrate.py`, `test_autoos_usage.py`, `test_autoos_track.py`, `test_autoos_verdict.py`, `test_ready_*`, `test_autoos_writer_ledger.py`, `test_autoos_recovery.py`, `test_autoos_risk.py`, `test_autoos_card.py`, `test_autoos_inbox.py`, `test_prepush.py` | load `autoos-agent.py` by path | must stay green every slice (I3); only the path-string edits listed in §3a |

Stays out of scope (separate items): `oc_l1.py` lane runner itself (RT-P4-DELETIONS; this design only cuts its autoos-agent MCP
default and validator), trimming `autoos_resolver.plan()` (dead after `route` goes; note in the item's fix log, no new item per
D-2739 rule 1), historical `docs/plans`, `docs/handoff/` (the directory of dated handoffs), `CHANGELOG.md`, `logs/` mentions
(~55 files, left as history).

## 4. Rulings asked by routing D-2810 (group C) and the inventory's UNSURE items

| Item / surface | Ruling | Reason |
|---|---|---|
| autoos:AO-MCP-CWD-INDEPENDENT | CANCEL | the autoos-agent MCP server is deleted (S3) |
| autoos:SEAT-BLIND-QUOTED | KEEP | `tools/autoos_verdict.py _blind_statement` is ready-gate code, which stays |
| autoos:AO-LEDGER-DRYRUN | KEEP | ready/ledger path stays |
| autoos:AO-READY-MULTI-BRIEF | KEEP, amend V3 | V3 must read the PLANGRAPH run record for new runs; old AutoOS records via `autoos_run_records` (S1) |
| autoos:AO-SPAWN-PATCH-ID | CANCELLED (D-2813) | needed the AutoOS spawner |
| autoos:LAUNCHER-CPU-GATE | CANCEL in AutoOS | the launcher is the AutoOS spawner |
| autoos:AO-AUTO-FANOUT | KEEP | gateway, not spawner |
| `prune-sandboxes` verb | KEEP with its whole helper cluster (`_pin_dir_fd`, `prune_toolhome`, `_prune_sandbox_roots`, `_sandbox_eligible`, `_list_root`, `_child_mb`, `cap_toolhomes`, ...): it is a verb in KEEP_ROOTS, so I9 keeps every helper it reaches | legacy sandbox dir still holds many; removal later once drained |
| `oc_*` MCP tools / `oc_l1.py` | MCP tools go with S3; `oc_l1.py` stays; lane MCP default `["autoos-agent"]` -> `[]` AND the validator accepts an empty list (S2a) | |
| `l2_*` MCP tools / `oc_l2.py`, `runner_pool*` | REMOVE (S4) | launchers |

## 5. Invariants (each slice must keep all of them; the seat checks each)

| # | Invariant | Check (test, run in the slices named) |
|---|---|---|
| I1 | `autoos_gateway_key.py` + `keys_file.py` at those paths, signatures `resolve_client_key(env, path)` / `read_keys(path)`, `autoos_gateway_key` imports only `keys_file` + stdlib | NEW `tests/test_plangraph_host_keys_contract.py` (S1): loads both by `spec_from_file_location` exactly as App. A.1 does, in a temp root holding only those two files; asserts import + signatures. Runs in EVERY slice |
| I2 | `opencode.jsonc` parses as JSONC and its `providers` block is byte-identical to base | same contract test: strip-JSONC, sha256 of `providers` == da6a4f8e's. Runs in EVERY slice, named explicitly in S2a (the only slice that edits `opencode.jsonc`) |
| I3 | kept verbs behave identically | the kept-verb suites of §3b and the kept classes of `test_autoos_spawner.py` green every slice |
| I4 | historical run records stay readable | `autoos_run_records` tests (S1) on a fixture `logs/kill/<run>.json` + workers dir; note `kill_store_dir` needs `clients.state_dir()`, so `autoos_run_records` imports `autoos_clients` |
| I5 | no unwired test file AND no wired-but-missing file | `tests/test_suite_wiring.py` gains the reverse check in S1 (every file named in parts 17/18/33/34/44 and `run-tests.ps1` exists); each deletion unwires in the SAME slice |
| I6 | nothing live teaches or calls a removed verb/tool | `tests/test_no_autoos_spawn_refs.py` (S1, pattern grows per slice): no `autoos-agent(.py)? (run|route|ps|list)\b`, `autoos_agent_mcp`, `oc_l2`, `runner_pool` outside the history allowlist (`docs/plans/`, `docs/handoff/`, `docs/adr/`, `CHANGELOG.md`, `logs/`). Kept-file comments that matched are rewritten in S2b, not exempted |
| I7 | no secret value read or printed; `configuration/api-keys.yml` untouched | review check per slice (the file is git-ignored, `.gitignore:11`, so a diff check alone is vacuous): no slice adds code that opens it except the existing kept helpers |
| I8 | merge fence of §1 | manual L1 check before each merge (not test-checkable; the seat checks only that each slice's PR text records it) |
| I9 | every symbol reachable from KEEP_ROOTS still exists | NEW `tests/test_autoos_agent_keep_closure.py` (S1): AST name-reference closure over `autoos-agent.py` from KEEP_ROOTS; fails on any missing reachable name (catches `CFG_VERBS` in `main()`, the prune-sandboxes cluster, `parse_now`/`load_registry`). Runs in EVERY slice |

## 6. State/input table: callers before -> after

| Caller (input) | Before | After |
|---|---|---|
| agent wants a writer/seat | `autoos-agent.py run` / MCP `spawn` | plangraph `python3 -m plangraph.spawner.cli spawn` (App. A.3; D-2749) |
| `autoos-agent.py run/route/ps/list ...` | runs | argparse "invalid choice" exit 2 (no shim); `main()`'s guard text (:16465-16466) and the module docstring (:89-116) rewritten in S5b-run to list only the kept verbs |
| `main()` config load | `cfg = load_jsonc(...) if args.cmd in CFG_VERBS` | S5b-run deletes `CFG_VERBS` and that line together (no kept verb reads `cfg`; I9 proves it) |
| kept verbs incl. `prune-sandboxes` | run | unchanged (I3, I9) |
| seat ledger `writer_for_run(run_id)`, OLD AutoOS run | loads all of `autoos-agent.py` | imports `autoos_run_records` (S1), same result |
| seat ledger, NEW (plangraph) run | not found -> "unknown writer" path | unchanged; V3 port belongs to AO-READY-MULTI-BRIEF |
| probe/diag scripts needing a client key | `probe_common.load_agent_module()` -> `client_key`/`key_files` | `probe_common` imports `autoos_client_keys` (S1), same result |
| plangraph `host_keys` / overlay | loads the two files / reads `providers` | unchanged (I1, I2) |
| harness/role config | `SPAWNER = "autoos-agent"`; fence: non-spawn role may not list it; at least one `spawn: true` role required | S2a: `spawn: true` now means "may call the plangraph spawner". `SPAWNER` is repointed to the plangraph spawner MCP server name if `catalog/agent-harness.json` lists one (fence kept); if none is listed, `SPAWNER`, the fence (:183-184) and the any-spawn rule (:196-197) are removed together with a test that a role list without `spawn` validates |
| `oc_l1` lane start without `mcp` | default `["autoos-agent"]` | default `[]`; validator (:325-333) accepts an empty list, still refuses non-list / duplicate / empty-string names |
| `AUTOOS_WORKERS_DIR` for kept verbs (heartbeat, usage) | pinned by `oc_l1_render.py:268-272` on the MCP entry only | the pin goes with the entry (S2a); kept readers use `workers_dir` (:13742): `AUTOOS_WORKERS_DIR` if set, else the repo's `logs/workers` (comment :13616); unchanged for any caller that sets it |
| `tools/autoos-ask.py` without `AUTOOS_TASK_DIR` | "...the autoos-agent spawner exports to its workers" exit 2 | S5b-run: "AUTOOS_TASK_DIR is not set: ask-back only works inside a legacy AutoOS run; the AutoOS spawner is removed (D-2576), new runs use the plangraph spawner" exit 2 |
| `tools/run_budget.py` | spawner + cost-gate tooling | unchanged; spawner-side doc lines in `cost-guard.md` rewritten (S2b) |

## 7. Skill/doc/comment rewrites (S2b)

Rewrite to "spawn via the plangraph spawner" (point at plangraph's own doc, do not duplicate its how-to):
`unattended-orchestration/unattended-orchestration.md:62-73,95-97,222-243,273`; `SKILL.md:87,96-101,159 (R-coord-09),220
(R-worker-06)` (keep 110 `ready`); `references/main-orchestrator.md:92,105,121`, `l3-routing.md:7`,
`opencode-fleet-lessons.md:8`, `rule-map.md:9-33,104,161,183-184`; `docs/handoff.md:99-100` (the live "Start here, new agent"
file, not the history dir); `docs/tasks.md:23,41`; `docs/ai/opencode-fleet.md:104,106,153-167,192-203,217,250,272-292,323-330`
(incl. the whole "oc_l2.py phase lanes" section); `docs/ai/cost-guard.md:10,16,34,49-52,122,133-156`;
`docs/ai/memory-guard.md:138-156,223`; `docs/ai/writer-contract.md:39`; `docs/api-keys.md:172`; `docs/routing.md:43,208-252`
(keep 78/105/123); `docs/models.md:309,334,352-361`. Kept-file comments/help text: `tools/autoos_gateway_key.py:32`,
`tools/autoos_overlay.py:3`, `tools/autoos_routing.py:4`, `tools/autoos_recovery.py:12`, `tools/registry.py:708`,
`tools/seat-model-evidence.py:12,105,737`, `tools/oc_l1_render.py:25,84`, `configuration/opencode/plugins/bash-guard/index.mjs:915`,
`lib/linux/memguard.sh:16`, `configuration/oc-l1.example.json:8`, and kept test comments that match the I6 pattern
(`tests/test_autoos_resolver.py:4825`; the writer runs the I6 test to find any other). ADR 0006 gets a "Superseded by D-2576/D-2758" status line.
`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`: no mentions. CHANGELOG entry per slice.

## 8. Slices (ordered; each green on its own)

Line-cap reading (APPROVED D-2813, §9): count ADDED + MODIFIED lines (<= ~400); pure deletions uncapped, but each slice deletes
ONE cohesive region (one file, or one connected symbol group of `autoos-agent.py`), proven by I6 + I9 + I3/I5. Seats read the
full diff. Every slice runs I1, I2, I5, I6, I9 plus its own tests.

| Slice | Content | Adds/mods (est) | Deletes (est) | Tests |
|---|---|---|---|---|
| S1 decouple + guards | `tools/autoos_run_records.py` (workers_dir, worker_writer, read_kill_record, kill_store_*), `tools/autoos_client_keys.py` (key_files, client_key, zen_key); `autoos-agent.py` re-imports them; repoint ledger, recovery, `probe_common`; NEW tests I1+I2, I4, I6 (initial pattern = MCP-independent names only), I9; I5 reverse check | ~350 | ~200 (moved) | ledger, recovery, probes, new tests, wiring |
| S2a unwire config | §3a configs (not the catalog risk rule), `SPAWNER` semantics (§6), `oc_l1` default + validator, `oc_l1_render` pin, the config-dependent cases of `test_agent_mcp_tool_profile.py` | ~250 | ~80 | I2 contract test, harness/catalog/installer parts 17/18, `test_oc_l1`, `test_oc_l1_render`, `test_agent_harness` |
| S2b skills/docs/comments | §7 | ~380 | ~150 | I6 (pattern now includes docs), docs checks, skill size cap. Merges only after MODEL-ROUTE-SPAWN |
| S3 MCP server | delete `tools/autoos_agent_mcp.py`, `test_mcp_stdin_devnull.py`, `test_mcp_writer_policy.py`, `test_worker_scope_spawn_paths.py`, rest of `test_agent_mcp_tool_profile.py`; drop MCP cases/imports in `test_autoos_spawner.py` (§3b row 1), `test_autoos_heartbeat.py` (whole `McpHeartbeatTests`), `test_t2_record_pin.py`, `test_codex_newest.py`, `test_review_call.py`, `test_oc_l2.py`; catalog risk rule + its path-string tests; unwire 33:961, 34:4911, 44:939, `run-tests.ps1` | ~90 | ~3,000 | I3, I5, I6 (+ `autoos_agent_mcp`) |
| S4 other launchers | delete `oc_l2.py`, `runner_pool*.py`, `test_oc_l2.py`, 11 `test_runner_pool*`; unwire 33:662-674, 33:1219-1220 | ~40 | ~4,000 | I5, I6 (+ `oc_l2`, `runner_pool`), recovery |
| S5a list/ps | delete `cmd_list`, `cmd_ps`, `list_workers`, `worker_tree` + parsers/`VERB_*` entries + what I9 then reports unreachable in that group + their test classes; `test_opencode_bash_guard.py:373` example -> kept verb | ~60 | ~1,500 | I3, I9, I6 (+ `list`, `ps`) |
| S5b-route | delete `cmd_route`, `route_plan_for` + parser/entry + unreachable group | ~60 | ~2,000 | I3, I9 |
| S5b-run | delete `cmd_run` + parser/entry + `CFG_VERBS` and its `main()` line + unreachable group; rewrite `main()` guard text + module docstring; `autoos-ask.py` refusal text; delete `test_run_budget_spawner.py`, `test_spawn_pin_allowed_id.py`, the `run` cases of `test_t2_record_pin.py` and the remaining spawn classes of `test_autoos_spawner.py`; unwire 33:555-655,657,697,906,918,962,1017-1023,1240-1260, 33:1020 (+ 33:843 / `run-tests.ps1:5468` if `test_t2_record_pin.py` empties) | ~120 | ~9,000 + ~25,000 tests | I3, I5, I6 (+ verbs), I9 |
| S6 fleet gate | I6 test becomes the permanent CI check (already live since S1, final pattern); L1 greps the fleet MCP configs on each fleet host (read only) and routing routes removals to each host's L1 (D-2813 ruling 2) | ~30 | 0 | I6 |

If a S5 slice's adds/mods or its one-group rule does not hold when the writer runs I9, it splits further (S5b-run-1/-2 by
symbol group) before review; the cap never stretches.
Order rationale: S1 builds the guards every later slice relies on; S2a before S3 so nothing starts the MCP when it is deleted; S3 removes the MCP imports from tests before S4/S5; S5a-S5b
go from the smallest verb to `run`. S2b can be written in parallel with S1 (no shared files) but merges last before S6.
Writer per slice: plangraph spawner (D-2749); fixer mimo/mimo-v2.6-pro (D-2739 rule 5); 2 text seats per slice of families !=
writer. Main merged into the lane ONCE before each slice's final review (rule 7).

## 9. Routing rulings (D-2813)

1. APPROVED: deletion-heavy slices count only added + modified lines (<= ~400). Pure deletions are uncapped, but each slice
   deletes ONE cohesive region, proven by the grep gate + the kept suites green. Seats still read the FULL diff.
   v2 applies it strictly: v1's S5b (four disjoint regions) is split into S5a / S5b-route / S5b-run.
2. APPROVED: S6 lists the out-of-repo fleet MCP configs read only; after S3 merges, each host's owning L1 removes its own entry.
3. Patch-id record DROPPED; AO-MCP-CWD-INDEPENDENT, AO-SPAWN-PATCH-ID and LAUNCHER-CPU-GATE are cancelled.
4. I2 is part of S1's invariants (contract test) and runs in every slice.

## 10. Seat r1 findings -> v2 (run-7df2c280, qwen)

| Finding | v2 |
|---|---|
| HIGH `CFG_VERBS` used in `main()` | §6 row + S5b-run; I9 catches it |
| HIGH prune-sandboxes helpers inside a deleted region | §4 keep with the cluster; KEEP_ROOTS; I9 |
| HIGH `oc_l1` validator refuses `[]` | §6: validator accepts empty list (S2a) |
| HIGH `test_autoos_spawner.py:40` imports the MCP before S5c | S1b moves kept classes first; rest deleted in S5b-run |
| HIGH kept suites import/name the MCP | §3b: heartbeat import + cases (S3); ledger/risk path strings (S3) |
| HIGH spawner test list missing | §3b enumerated |
| MED S5c splits `CIRunStatusTests`, needs helpers | S1b moves whole classes by name with `load_agent()`, `_reviewer_registry()`, imports |
| MED absolute line ranges shift | §3: symbol contract + I9; lines are orientation only |
| MED S5b four disjoint regions | split S5a / S5b-route / S5b-run |
| MED S6 pattern matches kept text | kept comments rewritten in S2b (§7); history allowlist only |
| MED §7 missing `docs/handoff.md`, `docs/tasks.md`, oc_l2 section | added to §7 |
| MED I2 test not in S2a | I2 runs every slice, named in S2a |
| MED I5 one-directional | I5 reverse check added in S1 |
| MED `ENV_KEY` not in `autoos-agent.py` | removed from S1; §2 note |
| LOW `catalog/ai-registry.json:2392` | §3a, S3 |
| LOW harness fence lost | §6 harness row |
| LOW `autoos-ask.py` refusal | §6 row, S5b-run |
| LOW stale comments in kept files | §7 kept-file list |
| LOW §3/§8 region arithmetic | gone with the symbol contract |
| LOW plangraph claims unverifiable | Appendix A (verbatim excerpts at d66bf9e5) |
| MISSING `run_budget.py` ruling | §2: kept (cost-gate tooling uses it) |
| GAP I7 vacuous / I8 not testable | I7 = review check; I8 = manual, PR text records it |
| r2 HIGH 3 wired spawn tests missing (`test_t2_record_pin`, `test_spawn_pin_allowed_id`, `test_opencode_bash_guard:373`) | §3b rows; S3 / S5a / S5b-run |
| r2 HIGH kept `test_autoos_resolver.py:4825` matches I6 | §7 kept test comments (S2b) |
| r2 MED S1b is ~7,100 lines, not ~150 | S1b dropped: no move; S3 deletes the `mcp_server` classes + import, kept classes stay in place |
| r2 MED heartbeat range starts mid-file | delete the whole `McpHeartbeatTests` class by its boundary (:1184 to end of file) |
| r2 LOW probe list 4 of 15 | §2 KEEP_ROOTS note |
| r2 LOW `17-ai-routing.sh:2389` pin | §3a, S2a |
| r2 LOW `AUTOOS_WORKERS_DIR` after-state | §6 row |

## Appendix A. plangraph excerpts (main d66bf9e5, verbatim; for the seat)

A.1 `src/plangraph/routing_support/host_keys.py`
```
31 def _keys_path(env):
32     """<root>/configuration/api-keys.yml under the AutoOS root of `env`."""
33     root = env.get("AUTOOS_ROOT") or os.environ.get("AUTOOS_ROOT") or str(Path.home() / "code/AutoOS")
34     return Path(root) / "configuration" / "api-keys.yml"
37 def _load_helper(root, name):
47         spec = importlib.util.spec_from_file_location(name, path)
57 def _gateway_key(env):
64         resolve = getattr(helper, "resolve_client_key", None)
73 def _zen_key(env):
80         read_keys = getattr(helper, "read_keys", None)
81         if not callable(read_keys):
83         keys = read_keys(keys_path)
```
A.2 `src/plangraph/routing_support/opencode_overlay.py`
```
63 def load_root_providers(root=None):
70         providers = json.loads(text).get('providers')
71         return copy.deepcopy(providers) if isinstance(providers, dict) else None
```
A.3 the plangraph spawner entry point is `python3 -m plangraph.spawner.cli spawn ...` (`src/plangraph/spawner/cli.py`),
used by every fleet writer/seat run since SPAWN-WIN-PARITY (2026-10-26).
