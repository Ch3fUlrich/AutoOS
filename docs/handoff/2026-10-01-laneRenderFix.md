# RENDERFIX — the 15 `tests/test_registry_render.py` expectations vs the post-TORDER registry

**Lane:** `L1-backlog/ws-renderfix-20261001` from base `75af3236` (worktree
`C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-renderfix`).
**Commits:** `274d9c42` (the `t4-researcher` combos-gate pin) · `ec59a3b5` (this file's subject, 107+/59−).

## Attribution (measured, not assumed)

`python -m pytest tests/test_registry.py tests/test_registry_render.py tests/test_registry_loader.py -q`
at three revisions:

| revision | result |
|---|---|
| `e3436a4d` (pre-TORDER base) | 6 failed / 459 passed — the six old render-*drift* tests |
| `11757db4` (TORDER carrier tip) | **15 failed** / 457 passed |
| `ee7dc63e` → `75af3236` (main) | **15 failed** / 457 passed — identical names |

So the 15 are **not** merge-caused: TORDER fixed the six drift tests and broke 15 different
expectations in their place, and no wave ran `test_registry_render.py`. This lane is that gap.

## The 15 rows — every one **(A) stale expectation**, zero **(B) defects**

| # | test | class | why it was red / what changed |
|---|---|---|---|
| 1 | `RenderMatchesTodayTests::test_operator_flagged_dead_leg_is_not_mirrored_into_combos` | A | GEMRESTORE reversed the head removal: `gemini/gemini-3.8-flash` **is** served in `t2-worker` again → `assertNotIn`→`assertIn`. |
| 2 | `GatewayRefTests::test_registry_legs_keep_their_own_spelling` | A | antigravity's legs were removed (provider unavailable), so the rule is re-pinned on a live leg: `openrouter/nvidia/nemotron-3-super-120b-a12b:free` in `routes.t2-worker.legs`, `resolve_leg` still splitting at the first `/`. |
| 3 | `GatewayRefTests::test_render_omniroute_renders_antigravity_by_its_canonical_id` | A | same removal; AGYCANON is exercised on a copy that re-opens `providers.antigravity` and restores one declared leg, plus a loop proving no `agy/*` spelling survives anywhere in the real render. |
| 4 | `LitellmRenderMatchesTodayTests::test_gateway_only_leg_is_dropped_not_silently_kept_or_missing` | A | same; `GATEWAY_ONLY = {antigravity, agy, cc}` (verified in `tools/sync-router-tiers.py`), so the synthetic re-open still proves the leg is dropped while `scaleway/mistral-small-3.2-24b-instruct-2506` is kept. |
| 5 | `ModelsDocCellsComeFromTheRegistryTests::test_leg_whose_provider_is_globally_unavailable_is_marked` | A | `t2-orchestrator` lost its paid openrouter leg (openrouter has no credits), so the flip is re-pinned on `t2-worker`, which still declares openrouter `:free` legs. |
| 6 | `ChangedLegAvailabilityFailsModelsDocCheckTests::test_marking_a_leg_unavailable_exits_one_and_names_the_route` | A | `opus-4-6` lost the antigravity leg and is **omitted**, so the mutation-detection flip moved to a leg that exists and serves: `groq/qwen/qwen3.8-27b` on `t2-worker`. |
| 7 | `GatewayLegsFilterTests::test_real_litellm_drops_gated_legs` | A | the litellm `t2-worker` block mirrors the restored gemini head again → `assertNotIn`→`assertIn`. |
| 8 | `FreeAiRenderTests::test_free_ai_is_last_in_the_free_only_combos` | A | the operator's order keeps the scaleway credit legs **after** the free band, so the stopgap is mid-band → renamed `…_is_a_mid_band_stopgap_in_the_free_only_combos`, asserting it is present and never the head. |
| 9 | `FreeAiRenderTests::test_t2_worker_combo_ends_with_the_free_ai_leg` | A | same → renamed `…_carries_the_free_ai_stopgap_leg`; the last leg is pinned as `deepseek/deepseek-flash`. |
| 10 | `FreeAiRenderTests::test_t3_driver_free_only_leaves_omitted_and_gains_a_combo` | A | the restored gemini head leads; band order re-pinned (`gemini` pos0, `groq/qwen`/`or-nemotron` next, `free-ai/qwen7b` mid-band). |
| 11 | `RouteContextCapTests::test_docs_promise_carries_the_clamped_window` | A | D-TORDER-1(b): t1 keeps only ≥600000 legs, so its promise is the honest **1M**. |
| 12 | `RouteContextCapTests::test_the_real_t1_combo_does_not_promise_more_than_gemini_takes` | A | same: `t1-orchestrator` and `t1-orchestrator-free-only` render 1M (`spark-1.3-contributor` stays 1M). |
| 13 | `IdeContextAndEffortFollowServedLegsTests::test_the_real_t1_picker_window_is_clamped` | A | t1's picker window is **1000000** (≤ the model's own 1048576) — a clamp, no longer 131072. |
| 14 | `IdeContextAndEffortFollowServedLegsTests::test_the_real_free_head_keeps_its_own_default` | A | **the one (B)-candidate, resolved as (A):** the served head is the restored `gemini/gemini-3.8-flash`, whose ladder is `low/medium/high`; the surface default `xhigh` is not a rung it carries, so `render_ide()` correctly **drops** it instead of forwarding an effort the head rejects. No capability was lost — the ladder is forwarded. |
| 15 | `IdeContextAndEffortFollowServedLegsTests::test_openhands_max_input_tokens_is_clamped` | A | t1's openhands/litellm profile window is **1000000** (≤ 1048576), still a clamp. |

No test was deleted, no assertion was relaxed to a tautology, and every renamed test keeps its
original intent with the new truth pinned in it.

## Verification (quoted, on `ec59a3b5`)

- `python -m pytest tests/test_registry.py tests/test_registry_render.py tests/test_registry_loader.py -q`
  → **472 passed, 37 subtests passed — 0 failed** (was 15 failed).
- `python tools/registry.py render omniroute --check` → **exit 0**.
- `python tools/combo-contract.py` → **`contract PASS: 24 combos (LIVE 4161 models)`**, exit 0.
- `python tools/registry.py check` → `ok: registry 2026-09-30, 37 routes, 80 models, 53 providers`.
- `powershell -File tests/run-tests.ps1 -Filter "combos.json"` → **242 passed / 0 failed**.
- `render litellm|ide|openhands|models-doc --check` → all **exit 0**.

## The prior lane's failures (for the record)

Two attempts of this task were retired for writing to the operator's `main` checkout (once
switching its branch). Their work was preserved at `…\Temp\opencode\renderfix-wip.patch` and
`renderfix2-partial.patch` and reviewed; the only salvageable hunk (the gemini
`assertNotIn`→`assertIn` flip) is row 1 above. Nothing else was reused blindly.
