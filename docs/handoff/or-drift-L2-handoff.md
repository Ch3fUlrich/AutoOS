# fixes-L2-ordrift handoff (ctx cap, 2026-10-15)

Lane: /home/s/code/AutoOS/.claude/worktrees/lane-or-drift, branch lane/ao-rt-p0-or-drift. Brief: /home/s/code/routing-worktrees/fixes-L1/docs/plan/briefs/fixes-L2-ordrift-brief.md. Report to fixes-L1 only.
Design note (reviewed, seat nemotron-3-ultra free, corr 6e4ec58d-5290-4445-a92e-bf88828fbeaf): docs/plans/2026-10-15-or-drift-design.md.

## Done (committed, local only, NOT pushed)
- tools/combo_drift.py (check/export/guard, clean_violations) + tests/test_combo_drift.py (+ xfail strict test on real combos.json clean legs), tests/test_apply_guard.py, tests/test_combo_drift_units.py: 46 passed, 1 xfailed.
- apply.sh: --live-lines, --overwrite-live, exit-4 guard before any mutating step (placed before `if ! gateway_up` after the --drift block).
- configuration/omniroute/{drift-known-gaps.json,combos.live.json (live export 29 combos)}, configuration/autostart/autoos-combo-drift.{service,timer}.
- Live reads: `combo_drift.py check` rc 1 (drift), `guard` rc 4 (forbidden vertex legs in 6 *-clean combos in the file) - expected until fold merges.

## In progress (UNCOMMITTED in tree): registry fold, catalog/ai-registry.json
- Non-clean legs from job-tmp legs-after.json (mimo first, nemotron/deepseek/groq folded per fixes-L1 rulings A/B/C1); *-clean = default variant, deepseek OUT: l1-orchestrator-clean [mimo-flash, muse contributor (gated unavailable)], l2-orchestrator-clean/l2-worker-clean/l3-researcher-clean [mimo-flash], l3-driver-clean/l3-implementer-clean [mimo-pro].
- xiaomi_mimo provider: trains_on_prompts false + privacy block (quote privacy.mi.com/XiaomiMiMoPlatform/en_GB/, accessed 2026-10-15, caveats) - operator ruling D-1348 via fixes-L1. DeepSeek-in-clean ruling still PENDING (default out).
- TODO: `python3 tools/registry.py validate` must be ok; regenerate configuration/omniroute/combos.json ONLY via `registry.py render omniroute --check` diff then minimal --out (R-worker-01: --out once erased 461 lines, inspect diff); update docs/models.md clean rows (vertex -> mimo); run test_registry_render.py, test_combo_contract.py, test_audit_router_registry.py, renderer --checks (R-orch-18); grep route-id consumers (R-orch-11); remove the xfail marker on test_real_combos_json_clean_combos_have_no_forbidden_leg once combos.json is clean (strict xfail turns red when it passes).
- l1-orchestrator-paid: do NOT retire. Live consumers: registry route (empty legs, LiteLLM-only), configuration/litellm/config.yaml, opencode.jsonc litellm provider, tools/combo-contract.py T1_ROUTES. It is in drift-known-gaps unmanaged. Tell fixes-L1 (not yet sent).
- apply.sh --drift still exists unchanged (31 file combos; 19 differ/7 missing/7 extra measured pre-fold).

## Left
1. finish fold + docs + tests above; re-merge origin/main (main CI red until maingreen); free -m (MemAvailable >=3000 MB) before suites, one heavy suite at a time.
2. tools/prepush.py green; push lane branch only after green (D-1338); CI run id.
3. Seats: 2 non-Claude different families on the apply.sh guard + combo_drift.py diff (R-orch-16); plain VERDICT first line; groq has 8000 TPM cap (413 on 8.3k tokens); free nemotron-ultra calls take ~100-280 s, keep prompts small, max-tokens <=6000, no reasoning text.
4. Packet to fixes-L1: lane sha, CI run id, prepush record, diff summary, seat run ids + sha256, live-step packet (apply with --go after merge; export snapshot after apply; register-autostart ALL_UNITS does not list the new units: unit install = operator step), questions.
5. Design note update: --overwrite-live is a boolean (GO already names the artefact), not `<ref>`; update the note text.
Writer procedure: python3 tools/autoos_gateway_key.py exec /home/s/code/AutoOS/configuration/api-keys.yml -- python3 tools/review-call.py --model openrouter/nvidia/nemotron-3-ultra-550b-a55b:free --prompt-file P --out-dir D --max-tokens N (main-checkout key file path as 1st arg of exec; the worktree has no api-keys.yml). Writers return unfenced text sometimes; strip trailing TEST-GAMING lines.
