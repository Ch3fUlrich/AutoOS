# AutoOS audit 2026-10-08 (autoos-L1-main, routing D-643 steps 2-3)

Base: origin/main b5eee50e. Readers: Sonnet 5.5 via spawner (run 20261008-114426 = §A, 20261008-114432 = §B), judged
by L1. Readers ran no live gateway/host probes. L1 measured: every l* combo spawn fails on the central gateway
("add as a combo entry"); host ~/.config/opencode/opencode.json patched with l* ids (D-645); free-leg probe OK
(run 20261008-114514). L1 correction: c1b18c02 IS on main (merge-base checked); oc-runtime sources stay
`docs/ai/oc-runtime-sources/*.txt` until lane c2. UNVERIFIED items stay open in the plan.

## L1 verdict (step 2)
- Sound: availability ledger 93170f23, FAIK fail-closed 6fe30211, guard c0 b5eee50e, bash-guard e08be5b1, oc_* MCP
  c1b18c02, ORQWEN404, glm-5.3 gate.
- Fix: rename incomplete (14f025c0): default profile `omniroute-t1-orchestrator` in install.sh/.psm1 (real bug),
  ai-stack.sh verify combo, probe-sweep t-prefix filter, tests (lane REDMAIN); central gateway not re-applied (lane R1).
- Conflicts: R-orch-29 vs code + context rot; D-637 vs item 15 (L1 effort, L0 model); deepseek/muse in default routes;
  l2-orchestrator head repo vs server; OVH reachable by the long-lived l2-worker agent; trial legs after the free band.
- Revert: none.

## §A Workstation pushes (reader report)


**Measured here**
- `tools/sync-ide-models.py --check` exits 0.
- `tools/registry.py validate` passes.
- `tools/combo-contract.py` passes 24 combos (SKIP: no key or gateway).
- `pytest tests/test_sync_ide_models.py` has 1 failure, `test_write_fixes_every_surface_and_a_rerun_is_byte_identical`. It asserts `by_id["omniroute-t2-worker-clean"]` at `tests/test_sync_ide_models.py:198`, an id that no longer exists.

**1. Per commit**

| commit | verdict | evidence | action |
|---|---|---|---|
| c8787f98 layer rename t1-t4 → l-ids | sound core, incomplete | `combos.json` combos are l1/l2/l3 (retired list at `combos.json:166-179`, `registry.py:1190`). `opencode.jsonc` has 38 models, which matches your "38". The ide-models catalog has 0 stale ids. | Fix the stale-id list in section 3. |
| 14f025c0 rename follow-up | incomplete | The test at `tests/test_sync_ide_models.py:198-199,569` still uses t-ids and is red. `opencode.jsonc:19,125-130` still says t1 and t3-reviewer. | Finish it (lane spec). |
| 93170f23 availability ledger | sound, UNVERIFIED in depth | `autoos_resolver.py:495 _leg_availability` and the stale-ledger refusal at `:1348` exist. | None. |
| 6fe30211 FAIK credit pool | sound | `ai-registry.json:4051-4073`: the route is `omitted` and priced 0.0, so `credit_leg_priced()` refuses it (fail-closed). | None. |
| b5eee50e guard-orchestrator c0 mode | sound | `bash-guard/index.mjs:101-106,560-574` implements an orchestrator allow-list: no `git checkout`, `merge` only with `--ff-only`. | None. The "grant write permission" loop is by design (outside `.oc-pilot/`). |
| 5df27a19 aliases + R-orch-29 / R-orch-30 | R-orch-30 sound; R-orch-29 conflicts | See section 2a. | Reword R-orch-29. |
| e08be5b1 bash-guard | sound | `tools/hooks/bash_guard.py`, `tests/test_bash_guard.py` and `test_opencode_bash_guard.py` exist. | None. |
| c1b18c02 oc-runtime | not in this tree | `tools/oc_runtime/` is absent. Only `docs/ai/oc-runtime-sources/*.txt` is present. | Confirm the commit is not on main, or that I'm looking at the wrong base. UNVERIFIED. |
| 5e84f19d / 6d91df21 ORQWEN404 | sound | `or-qwen3.8-27b-free` is in the retired list (`registry.py:1184-1187`) and in no route. | None. |
| c901d15c scaleway removal | partial | `providers.scaleway available:false`, but `scw/*` model rows (`ai-registry.json:1634,1651`) and `capability-overrides.json:12-22` remain. | Optional cleanup. |
| agy claude swap | sound with a divergence | `opus-5-5` combo and `DEFAULT_ORCHESTRATOR_MODEL`/`AGY_DEFAULT_MODEL` are `claude-opus-5-5-medium` (`autoos-agent.py:365`, `autoos_clients.py:88`). Here `l2-orchestrator` is `[agy/claude-sonnet-5-5-medium, vertex, deepseek]`. The server dropped the agy sonnet head for privacy and renders `[vertex, deepseek]`. | Pick one (see 2d). |
| glm-5.3 gated 402 | sound | No GLM-5.3 leg in any combo. | Re-open once the OpenCode key is added. |
| oc_status / oc_start / oc_restart | sound | `autoos_agent_mcp.py:166/188/203` run children with `stdin=DEVNULL` and a filtered env. | None. |

**2. Conflicts**

a. **R-orch-29 ("1M-or-nothing") against context rot and your "tier by reasoning, not window size".**
- The operator text at `operator-input:744,751` is "further suggestions", not a directive, so this is a design conflict rather than a rule breach.
- The rule is also inconsistent with the code. `TIERS[2] = "l2-worker"` and `TIER_SPAWN_CHILD` (`autoos-agent.py:280,421`) give L2 a 128k combo.
- Only the explicit `--model omniroute/l2-orchestrator` (`unattended-orchestration.md:70`) gives L2 1M.
- Fix: say "L2 uses the l2-orchestrator band when it needs more than about 128k of live context. Otherwise split into bounded tasks. Checkpoint DONE criteria to a file at 60-70% fill." Make the tier mapping consistent with that.

b. **D-637 against the older "Sonnet L0/L1" input.**
- D-637 (`routing/DECISIONS.md:229`): every L1 is Opus 5.5 at medium effort.
- Operator item 15 (`operator-input:239`, router D-643, newer): L0 is Sonnet low or medium, L1 is high effort. The code follows D-637.
- `docs/handoff.md:9` says `l1-orchestrator#high`.
- The two conflict on L1 effort (medium vs high) and on L0's model. Neither is recorded as superseding the other.
- Needs one operator line in `QUESTIONS.md`: "L1 = Opus 5.5 medium or high? L0 = Sonnet?"

c. **OVH (no prompt caching) on orchestrator legs.**
- The orchestrator combos are clean: none of `l1-orchestrator`, `l1-orchestrator-paid`, `l2-orchestrator` or `l2-researcher` has an OVH leg.
- OVH is the head of `l2-worker-clean` and `l3-driver-clean`, and sits at positions 10-11 in `l2-worker` and `l3-driver`.
- The opencode agent `l2-worker` is "owns one track" and is bound to `omniroute/l2-worker` (`opencode.jsonc:119-122`). A long-lived track owner can therefore end up on a non-caching leg.
- The `ovh-direct-*` models (`opencode.jsonc` provider block) are selectable by hand.
- Fix: keep OVH off any agent that lasts longer than one task. `fleet-lessons.md:26` states the rule and nothing enforces it.

d. **deepseek and muse-spark are reachable in default routes, against operator item 15.1.** Where they appear:
- `l1-orchestrator`: legs 5 and 6, so these are the tail of the default route. `l1-orchestrator-paid`: both legs.
- `l2-orchestrator`: leg 3 (deepseek).
- `l2-worker`: legs 13 and 14. `l3-driver`: legs 14 and 15.
- `l2-worker-clean` and `l3-driver-clean`: deepseek as the last leg.
- The `spark-1.3-contributor` and `deepseek-v4.1-flash` standalone combos.
- `opencode.jsonc` declares `deepseek-direct-flash` and `meta-direct-muse-spark-1.3-contributor` (a direct meta/muse provider, which returned 401 on the workstation).
- `lib/linux/install.sh:5775-5800` writes a direct `meta` provider whenever `META_API_KEY` is set.
- `DECISIONS.md:89` still says muse and deepseek are allowed as paid last legs. The 10-08 input supersedes it.
- The other pieces: ainative first is correct, and `agy/claude-sonnet-5-5-medium` heads `l2-orchestrator`, which the server excluded for privacy (see section 1, agy row).
- Item 15 says trial first (ovh and vertex have $450 together), then free, then credits, then paid. `l2-worker` puts ovh and vertex after the whole free band.

**3. Stale t-ids outside changelogs**
- `lib/linux/install.sh:6565,6580-6581` and `lib/windows/AutoOS.Install.psm1:3435,3450-3451`: the default active profile is `omniroute-t1-orchestrator`. `tier-profiles.json:109` now generates `omniroute-l1-orchestrator`, so the default is never set. Real bug.
- `tests/run-tests.ps1:4814-4849`, `tests/linux/34-ai-services.sh:2841,2884-3075`, `tests/linux/07-mcp-pins.sh:131`: profile and file names still use t-ids. UNVERIFIED whether they fail, because I didn't run those suites. `tests/test_sync_ide_models.py:198,199,569` is red (measured).
- `configuration/docker/ai-stack/ai-stack.sh:1904`: the default `AUTOOS_VERIFY_COMBOS` includes `t2-worker-free-only`, which no longer exists, so the keyed-combo verify tests a missing combo.
- `tools/probe-sweep.py:354-355,439-440`: filters on `startswith t1-/t2-/t3-`, so it selects no l-combos.
- `tools/combo-contract.py:345-346`, `configuration/README.md:11`, `docs/models.md`, `docs/handoff.md:9`, `opencode.jsonc:19`: wording only.
- `t3-reviewer` is the agent-role name (`opencode.jsonc:125-130`, `autoos-agent.py:280,421`). It's consistent but not renamed. Decide to keep it or rename it with a grep of all consumers (R-orch-11).
- `t4-rag` is kept on purpose.
- Spawner route JSON: I ran `autoos-agent.py route` for 5 cards in this tree and none printed `t2-worker`. The server's `t2-worker` is most likely a pre-rename checkout of the server. UNVERIFIED on the server.

**4. Repair lane spec (L1 spawns writer + cross-family seats; I edit nothing)**

*Lane R1: server host config (R-router-10, host is the source).*
1. On the server, run `./setup.sh --only opencode-cli --yes`. `_opencode_merge_config` backs up first and replaces `providers['omniroute']` from `catalog/ide-models.json` (`install.sh:5810-5830`), so stale t1/t2/t3 ids are removed. It is idempotent.
2. Run `ai-stack.sh init` so the container copy is re-rendered from the host file via `render-opencode-container-config.py` (`ai-stack.sh:686-690`). Do not hand-edit the container copy.
3. Run `omniroute/apply.sh` against the gateway. It prunes the retired t-combos (`combos.json` retired list). Never touch `~/.omniroute` (R-coord-15).
4. Verify: `opencode run -m omniroute/l2-worker` no longer says "Unable to determine provider". Then run `oc_status` on both lanes.
- Do not run step 3 on the server before step 1: the order matters because the host file lists models the gateway must still serve.

*Lane R2: workstation central-first, no muse/deepseek.*
1. In the user opencode config, put the central gateway (`<central-gateway>`) first and `127.0.0.1:20128` as a second provider or fallback. Key via `{env:AUTOOS_OMNIROUTE_KEY}` only.
2. Remove the direct `meta` and `deepseek` models/providers and the muse/deepseek model entries from the user config.
3. Drop the muse/deepseek legs from `combos.json` through the registry's gate mechanism so the legs stay re-open-ready, as was done for glm-5.3. In the registry: `providers.deepseek` and `providers.meta_api` unavailable, `deepseek-direct-flash` / `meta-direct-muse` models gated or removed from `opencode.jsonc`. Then run `registry.py render ... --check`, `sync-ide-models.py`, and `omniroute/apply.* --prune`.
4. Remove the direct `meta` block in `install.sh:5775-5800` or gate it behind an explicit flag.
5. Finish the rename: fix the section-3 items, including `install.sh` / `.psm1` `omniroute-l1-orchestrator` and `test_sync_ide_models.py:198,569`, then run `tests/run-tests.sh` and the pytest shards.
6. Reconcile `l2-orchestrator` (the agy-sonnet head privacy question) with the server's `[vertex, deepseek]`. Both lack deepseek once step 3 is done.
7. Reorder `l2-worker` and `l3-driver` to trial (ovh, vertex), then free, then credits, then paid, per item 15. Decide D-637 against item 15 first (see 2b).

*Lane R3: skill.* Reword R-orch-29 as described in 2a, keep the checkpoint-to-file rule, and record the OVH-no-cache-for-long-lived-agents rule with a mechanical check in `select_combo`.

The guard, spawner MCP and ORQWEN items need no revert.

## §B Operator input vs repo: THE TABLE (reader report)

**Evidence limits**
- The sandbox clone has one commit (`e555c93`, source b5eee50e), so `git log -S` and `--grep` are unavailable. I cite file:line and test names instead.
- The sandbox already contains the `oc_*` MCP tools, so it includes the a1c81ea7 lineage. Main at 005a7200 (later) is UNVERIFIED.
- Branch `workst/plangraph-handoff-2026-10-08` does not exist in `/home/s/code/plangraph` (only `main` and `worktree-proposal`). The workstation digest is unread.
- I did not query any live gateway, OmniRoute dashboard, workstation, or server host. Every "live" claim from agent text is UNVERIFIED.
- Skill `plangraph-planning` (D-642) is not in AutoOS `.agents/skills/`. It is also not in `/home/s/code/plangraph` or `/home/s/code/routing` under `skills/`. UNVERIFIED.
- I ran one test: `python3 tests/test_suite_wiring.py`. It fails.

## 1. OpenCode fleet (lines 4-30, 112-185, 193, 779-865, PASTE tasks 1-12)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| L0 workstation router, L1 backup (193) | partly | Brief `docs/plan/briefs/workstation.md`. D-644 says no OpenCode session runs on the workstation. | Relaunch workst-L1 and workst-L0 as briefed. |
| Let in-flight workstation sessions finish (193) | conflicts | D-644: the omniroute_setup session failed 10-08 09:44Z. L1-alpha/beta are idle. | Salvage the pushed commits 6fe30211/14f025c0. Don't wait for sessions. |
| `oc_start`/`oc_status`/`oc_restart` MCP (113-182) | done | `tools/autoos_agent_mcp.py:166,188,203,1558-1576`. Tool-set pin test UNVERIFIED (not run). | None. |
| c2: move runtime into `tools/oc_runtime/` (PASTE 2) | missing | `tools/oc_runtime` does not exist. Sources are still `docs/ai/oc-runtime-sources/*.txt`. | Lead lane c2. |
| Watcher never kills spawned runs; proxy babysit (PASTE 1) | UNVERIFIED | Only in the agent text (178). The watcher is host-local, not in the repo. | Land as part of c2, with tests. |
| Rotation by real per-turn input; prune compaction (PASTE 3, 296) | partly | `tools/oc_l1_render.py:77` sets `COMPACTION={auto,prune,tail_turns:4}`. `ROTATE_INPUT` is not in the repo. | Move the watcher into the repo. |
| Canary retry (3 tries), heartbeat refresh (PASTE 3) | UNVERIFIED | `tools/oc_l1_canary.py` exists. Retry not checked. | Check in c2. |
| Fleet-bus relay :47100 (PASTE 4) | needs-operator-input | Needs a Tailscale ACL (OS-131). | See Q1. |
| OpenCode L0 acceptance for HIGH risk (PASTE 5) | missing | The checklist is in routing `OPENCODE-ROUTER.md`. No `review-call` family-pair enforcement found. | Lead lane. |
| L0->L3 architecture test (PASTE 6, item 11) | missing | No test or record found. Cap: $6. | See group 12. |
| U13 central, OVH-GUARD, PUBLISH-GUARD (PASTE 7-9) | missing | No guard code found. | Lead lanes; prox applies the image. |
| Drop `or-qwen3.8-27b-free`, Gemini `max_tokens` floor (PASTE 10) | UNVERIFIED | Not found in the sandbox registry via grep. Agent text claims it is pruned. | Run `registry.py check`. |
| ZCode client for glm-5.3-flash (PASTE 11) | partly | Registry row `ai-registry.json:1856`. The client is not implemented. Entitlement must be probed on the workstation. | Workstation probe per card. |
| Lessons into `references/opencode-fleet-lessons.md` (PASTE 12) | partly | The file exists. Whether the newest lessons are in it is not checked. | Diff against the incident notes. |
| Lane message-flow fixes (13-22) | UNVERIFIED | Agent claims only (unblock-001.json, msg_024.json). | Read the lane inboxes/outboxes. |
| Cross-session messaging hints (32-109) | missing | No change to AGENTS.md or the skill picks up subagent frontmatter, `isolation: worktree`, or the `CLAUDE_CODE_SUBAGENT_MODEL` ladder. | Skill rewrite (group 6). |
| CI break: `test_suite_wiring` (309-330) | partly | `test_prepush.py` is now wired. The test still fails: `test_probe_ledger.py` is unwired. | Wire it into a suite. |
| CI shard f POSIX/BOM (330) | UNVERIFIED | The test is at `tests/linux/36-static-analysis.sh:71`. Not run. | Run shard f. |

## 2. OmniRoute combos, providers, caching, context (194-203, 296-345, 673-760, 773-777)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Combos too small a context (198) | partly | `configuration/omniroute/context-overrides.json` and `capability-overrides.json`. glm-5.3-flash `context_usable` defaults to 100k. | Run probe-recall. |
| Context carry-over across combo switches (199) | missing | No doc found. | Research task, doc in `docs/routing.md`. |
| Better combos; no repeated model when the rest are rate-limited (200) | missing | All `combos.json` strategies are `priority` (`:194-219`). | Measure, then pilot a rotating strategy. |
| Free-only OpenRouter (200.1) | partly | OpenRouter is off at the provider level (litellm header). | Document it in `docs/routing.md`. |
| Tier order trial -> free -> credits -> paid (238) | partly | The handoff applies the order. Registry front band: ainative, glm. | Check the rendered combos. |
| Stop deepseek / muse-spark (237) | partly | The registry has both. `ai-registry.json:321` notes deepseek 402. | A deny rule or `available:false`. |
| Caching-only providers for L0-L2; OVH workers only (776-777) | partly | OVH is workers only (PASTE rule 4). t2-orchestrator is claimed done (UNVERIFIED). | A registry field `prompt_cache` plus a test. |
| Rate-limit tiers; researchers fan out (240, 340) | partly | Researcher tier exists (`2026-09-30-researcher-tier.md`). | Make it spawner policy. |
| Weekly model-sync fetch (337) | missing | Per handoff: only the images-only `catalog-live-check.yml`. | Add a read-only inventory job. |
| Protect button, add-account (338-339) | done | Answered in the handoff, lines 13 and 32 (protect = per-connection rate-limit protection). Add-account answer not checked. | Copy into `docs/`. |
| deepseek 400s (341, 680) | partly | `reasoning_text` handling in `configuration/omniroute/reason-fix-README.md` and `tools/probe-reasoning-edge.py`. "Duplicate tool output" appears nowhere in the repo. | Repro and test it. |
| Max effort for deepseek (341) | partly | The ladder is in the registry. Alias `#max` stays hidden while the route is off. | Check once the route is re-enabled. |
| Image/STT/TTS/3D combos (343) | missing | Plan items C8/T1-MM only. | Lane T1-MM. |
| OpenCode-Zen direct, no proxy (1, 242) | partly | Plan item T1-ZEN. `2026-10-01-opencode-direct-fallback-ladder.md` exists. | Verify that opencode uses its own key. |
| meta.ai endpoint doubt (720) | missing | The handoff says meta-api cannot be registered. | Check dev.meta.ai docs. |
| Local OmniRoute backed by <central-gateway> (230, 680) | partly | Plan item T1-PEER. No config found. | Needs a bearer: see Q4. |
| Sync combos server vs workstation (273) | missing | Not verified. | Diff rendered combos. |
| Router dumb, `select_combo` (743) | done | `tools/autoos_routing.py:317`. The MCP imports the same function (`:4`). | None. |
| Tier contract: reasoning over window (744) | missing | The skill has the 1M-or-nothing rule. | Decide: Q3. |

## 3. LiteLLM fallback (199)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Update the outdated setup | partly | `configuration/litellm/config.yaml` is labelled MANUAL FALLBACK. `router_settings.fallbacks` is empty. Chains were last touched around 2026-09-28. | Re-render from the registry. Add an automatic OmniRoute -> LiteLLM switch. |

## 4. Cost and tiers (203, 237-244, 773-777)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Use free tiers before paid (203) | partly | `tools/cost-gate-status.py` and the paid-run gate exist. | None. |
| Spend the ainative $9.79 by 11-02 (773) | partly | Registry `:1873` front band; 5 combo hits. It was 429 at probe time (UNVERIFIED). | Recheck after the cooldown. |
| Vertex $250-300, OVH $200 (347-349) | partly | Credits unspent per operator (274). OVH-GUARD missing. | Probe both. |
| Cheapest provider per model (244) | missing | No cross-provider price comparison. | Research. |
| `jev-1.13-free` research (196, 241) | missing | Not found in the repo. | Research note. |
| Effort policy: L0 Sonnet low/med, L1 high (238) | missing | Not in AGENTS.md or the skill. | Add to the skill. |

## 5. Providers and api-keys docs (359-365, 556-678)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Vertex ADC / service-account steps in `docs/api-keys.md` (365-556) | missing | grep finds no gcloud/service-account/ADC steps. Only a table row at `:267`. | Write the section with the aiplatform API-enable URL; no secrets. |
| OVH key steps (559-672) | missing | grep finds no "AI Endpoints" or "Discovery". | Write them. |
| Vertex works automatically (365) | partly | `tools/probe-vertex.py` (159 lines) and the trailing-turn patch exist. Operator says it is fixed (UNVERIFIED). | Probe. |
| `probe-vertex.py` deletion (277) | missing | The file is present. | Delete it if OmniRoute probes cover it. |
| togetherai $5 (675) | partly | Registry `:3580`. Key check UNVERIFIED. | Key-presence probe, by name. |
| navyai, bluesminds, arcee unusable (676-678) | partly | Registry rows exist (`:3463,3477,3491`). | Set `available:false` with reasons. |
| nscale, siliconflow, sealion, routeway, requesty, aion, agnes, pollinations (346-358) | partly | Registry rows, FREEKEYS-1 2026-09-28. Credits are not recorded. | Add credit and expiry fields. |
| No-key providers (359-364) | partly | felo, uncloseai, ai_horde, g4f are in the registry. They appear in no combo and not in `api-keys.md`. duckduckgo-web has no registry row. | Wire them, or document why not. |
| g4fpol kimi-k3 / gemini-3.1-pro / glm-5.3-flash (333-336) | partly | `kimi-k3` is in the registry. The g4fpol ids are not found. | Probe them. |
| qoder free (275) | partly | The registry has a `qoder` client. A Qwen3.8-flash leg is not found. | Add it. |
| Multiple OpenCode Free accounts (339) | needs-operator-input | Terms-of-service question. | See Q5. |

## 6. Skills (13, 331, 742-776)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Evaluate R-coord-13/14 (13) | partly | `SKILL.md:140-141`. `references/rule-map.md` maps older rules into `R-coord-*`. | Classify as below. |
| Spawn decisions by MCP, not agents (13.1) | partly | `select_combo` is shared by CLI and MCP. The depth cap lives in `child_depth` (`autoos_agent_mcp.py:589`). Production-untested, per operator. | Mechanize below. |
| Max 2 levels deep (770) | UNVERIFIED | No literature check. | Research. |
| Rules that could become mechanical | missing | Candidates: R-coord-14 as a role-to-tier table enforced at spawn; a spawn-count budget per run (only depth exists, 756); exclude client x role pairs that can't honour `--lean` in `select_combo` (757); a spawn-time clean-clone assert; per-run token/context-peak metrics in `exit.json`; ack-probe budget as a registry field (761). R-coord-13 stays prompt-level, since the MCP can't see whether a session is interactive. | Implement in `tools/autoos-agent.py` / `autoos_routing.py`. |
| Dead field `spend=credit` (753) | UNVERIFIED | I did not grep for it. | Remove it or implement it. |
| Watchdog thinking-trace signal (754) | missing | Not found. | Heartbeat field. |
| Key-shaped canary (763) | missing | Not found. | Add it. |
| Router prose table generated from `select_combo --explain-all` (752) | UNVERIFIED | Not checked. | Check `docs/routing.md`. |
| Model name in every Done note (196) | partly | `tools/seat-model-evidence.py` gives per-seat evidence. No rule covers the Done notes themselves. | A required record field in `autoos_report.py`. |
| coding-principles skill | partly | The skill exists. Limits conflict: see group 10. | Add the "max 400 lines" rule. |
| New workstation skills reviewed (12.5) | UNVERIFIED | Not seen. | Review on the workstation. |
| `plangraph-planning` skill | UNVERIFIED | Not found in the three repos. | Locate it. |

## 7. Server self-healing MCP, item 19 (280-292)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| prox's MCP proxy for weak models | UNVERIFIED | Server repo is out of scope. Plan item T6-HEAL has a pilot gate. | server-L1-main continues. |
| Research first | partly | T6-HEAL names HolmesGPT, Keep and the OpenHands resolver. | The pilot scorecard decides. |
| Mechanical detection, cheap triage, L2 fix, reviews | missing | T6-FIX is a plan row, no code in AutoOS. | server-L1-main. |
| Decision DB and mail report | missing | Not seen. | Define the schema. |
| Kanban link | missing | `tools/vibe_kanban_bridge.py` exists. | Wire in later. |

## 8. Open WebUI, Home Assistant STT/TTS, item 16 (245-249)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Open WebUI update and OmniRoute models | missing | AutoOS `infra/local-ai` pins an old stack. The cloud-VM container is in the Server repo (T6-OWUI). | server-L1-main. |
| HA Assist via `wyoming_openai` | missing | T6-HA only. | Latency target under 3 s. |
| Phone apps / keyboard STT | missing | Not seen. | Dictate-style client. |
| Transformer speech correction | missing | Not seen. | Research. |

## 9. Grok bot, item 17 (250-271, 774)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Wrapper on the workstation | needs-operator-input | T5-GROK says the code is on no host; the operator must paste it. | See Q2. |
| Temporary combo | missing | 0 grokbot hits in the registry and combos. | Own combo: no fallback, retries 0, timeout over 700 s. |

## 10. Cleanup and coding practices (18.3, 276-279)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| 400-line file cap | conflicts | `tools/autoos-agent.py` has 11,483 lines. `ai-registry.json` has 5,150. `tests/test_autoos_spawner.py` has 20,717. | Split in phases, starting with the spawner. |
| Redundant scripts | partly | Many one-off `probe-*`, `diag-*`, `v-activate-*` scripts in `tools/`. | Inventory and delete. |

## 11. CI (309-330)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Suite wiring | partly | See group 1. | Wire `test_probe_ledger.py`. |

## 12. Testing in production, item 11 (205-225)
| Item | Status | Evidence | Next action |
|---|---|---|---|
| Replica of L0-L3 inside OpenCode | missing | No test or record. `TESTING.md` S8 only plans a drill. | Run PASTE 6. |
| PID growth, dead subagents, inter-agent comms, long runs | partly | Container PID/memory limits were set (decisions-log, 2026-10-01). | Soak test. |
| Combo latency | missing | No measurement. | Add to the test. |
| Orchestrator gh/git fences | UNVERIFIED | `tools/hooks/bash_guard.py` exists. | Test the L1 deny and worker allow cases. |
| Credential leak | partly | `tools/autoos_redact.py` exists. | Add the canary. |
| Session ids (216-224) | UNVERIFIED | Sessions are on other hosts. | Read them via the lead. |

## Needs operator input
1. **Relay ACL.** Open Tailscale port 47100 for the fleet bus (OS-131)? Options: (a) yes, now; (b) later; (c) use a git-based message dir. Recommended default: (c) until you click it.
2. **Grok wrapper code.** How should the FastAPI wrapper and README reach the repo? Options: (a) paste into the routing repo; (b) a gist; (c) skip Grok. Recommended default: (a).
3. **Tier contract.** Should the "t1 equals at least 1M window" rule be reframed as "t1 equals strongest reasoning plus file-based memory" (742-745)? Options: (a) reframe; (b) keep. Recommended default: (a).
4. **Backup route.** Which bearer key should the workstation use for <central-gateway>? Options: (a) a new named workstation key; (b) reuse the coding.vm key; (c) tailnet only. Recommended default: (a).
5. **OpenCode Free.** Is a second unauthenticated "free" account acceptable (339)? Options: (a) one account only; (b) two; (c) first read the terms of service. Recommended default: (c).
6. **Agent rule changes.** May agents add the three rules (Done notes name the model; effort policy; 400-line cap) to AGENTS.md? Options: (a) yes; (b) skill only. Recommended default: (b).
7. **Retired providers.** Mark navyai, bluesminds and arcee `available:false` (reason: no free models)? Options: (a) yes; (b) delete their rows. Recommended default: (a).
