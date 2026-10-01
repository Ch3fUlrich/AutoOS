# DONE — L1-backlog/ws-freewire-20260930 (free-leg wiring + gemini retention)

**Lane:** `L1-backlog/ws-freewire-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-freewire`
**Base:** combos lineage tip `a975d48`
**Date:** 2026-09-30 (gateway measurements 22:42–22:46Z)
**Writer:** freewire (pinned, critical)
**Inputs:** `L1-backlog/ws-free-probe-20260930` @ `6e978dd`
(`docs/handoff/2026-09-30-laneFreeProbe.md`)
**Evidence:** `docs/handoff/2026-09-30-laneFreeWire.md`
**Commits:** `7eff602` (wiring; gemini retained under the 429 backoff policy), `4cb49b4` (test updates)

---

## 1. What was wired

**10 free legs** free-probe proved, into `catalog/ai-registry.json` and every
rendered surface:

- huggingface: `zai-org/GLM-5.2`, `Qwen/Qwen3.8-27B`
- groq: `qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`
- openrouter `:free`: `qwen/qwen3.8-27b:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`, `cohere/north-mini-code:free`,
  `poolside/laguna-s-2.1:free`
- plus `vertex/gemini-3.8-flash`, a leg of the `gemini-3.8-flash` combo.

- **5 new model rows** (`Qwen/Qwen3.8-27B`, the 4 openrouter `:free` ids); the
  three groq model rows and `qwen/qwen3.8-27b` promoted `tool_calls: proven`.
- **7 new single-provider combos** (`groq-qwen3.8-27b`, `hf-glm-5.2`,
  `hf-qwen3.8-27b`, `or-laguna-s-2.1-free`, `or-nemotron-3-super-free`,
  `or-north-mini-code-free`, `or-qwen3.8-27b-free`); added to `IDE_MODEL_ORDER`.
- **Free bands** interleaved before the credits band in `t1-orchestrator-free-only`,
  `t2-worker`, `t2-worker-free-only`, `t3-driver`, `t3-driver-free-only`; **no
  free leg entered a `*-clean` route**.
- **3 policy allow rules** above `deny-groq` / `deny-openrouter`.
- **OpenRouter guard moved**: `providers.openrouter.available` false→true so the
  `:free` ids serve; the two paid legs DSMAX gated
  (`openrouter/meta/muse-spark-1.3-contributor`,
  `openrouter/deepseek/deepseek-v4.1-flash`) now carry route-level gates
  (`+ t2-worker-clean`). Free-probe's proposal did **not** account for the
  provider flag; this lane found and handled it.

## 2. What failed / was excluded (verbatim reasons)

- `huggingface/deepseek-ai/DeepSeek-V4-Flash-0731` — **V4 weights, not V4.1**;
  the operator rule *"DeepSeek only V4.1 Flash"* keeps it referenced by no route.
- `cheaperinference/*` — **paid**, not free (402 cleared but `tier: paid`); stays
  in the credit band.
- `agentrouter/deepseek-v4-flash` — ack-only; tool 400
  `Failed to deserialize ... tools[0]: unknown variant 'custom'`.
- `navy` — Cloudflare **Error 1010** `browser_signature_banned` (do not retry).
- `opencode` / `opencode-zen` — 402 `This model requires an opencode API key`.
- `antigravity` / `claude` — OAuth expired. `devin`, `qoder` — OAuth pending.
- `cerebras` (401 no creds), `cloudflare-ai` (502 no Account ID), `sambanova`
  (401 expired key), `together`/`novita` (CF 1010), `bazaarlink` (402 credits).
- **groq multi-turn caveat stands:** `deny-groq` exists for 400
  `messages.N role:assistant` ×335 + 413 ×78. The wiring rests on a **single**
  get_weather round only; re-probe a real multi-turn agent shape before lifting
  the blanket deny. A reviewer routed via `groq-qwen3.8-27b` reproduced a **413**
  (ITPM 7000, requested 14023) — recorded in the evidence §10.

## 3. Gemini retention (429 backoff policy)

- **gemini retained; usage governed by the repeated-429 backoff policy
  (3×429/120 s → 300 s cooldown per leg; 30 min park).** The 5 routes that
  carried a `gemini/*` leg keep it under that policy; `vertex/gemini-3.8-flash`
  is a leg of the `gemini-3.8-flash` combo. The leg restoration lands on the
  combined lineage (`gemini-restore`).
- `providers.google_ai_studio`: `available: false`,
  `unavailable_until: "2026-10-07T00:00:00Z"`; key unwired.
- Measured reason: **8088** gemini cooling-down lines / **7187**
  `lastErrorCode=429` on 2026-09-30 + live 429 `model_cooldown`.
- Honest caveat: the gateway *connection* is not deleted (no restart) and a
  direct probe of the unwired leg returned 200 at 22:46:17Z — consistent with
  retention: the leg is available and its repeated 429s are absorbed per leg by
  the backoff policy above.

## 4. 429 policy outcome

- The **provider-breaker family is already applied** through
  `apply.ps1`/`apply.sh`'s `patch-api-resilience` (`failureThreshold=2`,
  `degradationThreshold=1`, `resetTimeoutMs=30000`, `maxWaitMs=180000`) — verified
  live (`= resilience settings already current`). No change needed.
- The **rotation family** (`OMNIROUTE_ROTATION_*` / `OMNIROUTE_ROTATE_*`) has **no
  tracked surface** (nothing sets those env vars; the running gateway would need a
  restart). Recorded as a **proposal**, not claimed (values in the evidence §4).
- **Cross-branch overlap:** the resilience block is lane-B's; `start-stack.ps1` is
  the only plausible launcher-env surface — both left untouched for L0.

## 5. Verification (all green unless noted)

- `registry.py check` + `validate` → `ok: registry 2026-09-30, 32 routes, 80
  models, 34 providers`.
- 5 renders `--check` → all `ok`; `sync-ide-models.py --check` → `OK`.
- `tests/test_registry_render.py` OK; `tests/test_sync_ide_models.py` OK;
  `tests/test_autoos_resolver.py` OK; `tests/test_registry.py` 1 failure —
  **pre-existing at `a975d48`** (`test_a_credit_leg_is_last_and_gated_until_priced`,
  the combos-lineage unpriced-credit placement; flagged for L0).
- `tools/audit-router.py --offline` → `no drift`.
- **Full `tests/test_*.py` sweep vs the `a975d48` baseline worktree: NO new
  failures; 7 baseline failures fixed** (resolver ×2, sync-ide ×5).
- `apply.ps1 -DryRun` → correct plan (7 new combos). **Live apply**
  → the 7 combos created; store scan confirms the bands.
- **Ack probes** on all 10 newly wired legs: **10/10 HTTP 200** (22:45:43–22:46:00Z);
  no 429/503/504, no `chat_admission_busy`.
- **≥2-usable-provider invariant** per agentic route: t1-orchestrator **2**,
  t1-orchestrator-free-only 5, t2-worker 6, t2-worker-free-only 5, t3-driver 6,
  t3-driver-free-only 5 — all green.

## 6. Pre-existing / flagged for L0

1. `test_registry.py::test_a_credit_leg_is_last_and_gated_until_priced` —
   unpriced `credit` legs not at the tail (`vertex/gemini-3.8-flash`, the three
   `ovhcloud/*` legs). Pre-existing at `a975d48`.
2. `apply.ps1` cannot parse `ai-registry.json` on Windows PowerShell — two model
   keys differ only by case (OVH `Mistral-Small-...` vs scaleway
   `mistral-small-...`; this lane adds `Qwen/Qwen3.8-27B` vs `qwen/qwen3.8-27b`).
   `ConvertFrom-Json` aborts with `DuplicateKeysInJsonString`, so provider
   registration is skipped (connections already exist; combos still apply).
   Baseline-identical. Fix: parse case-sensitively.
3. `context_advertised 131072→1048576` (mission item 4) was **already applied**
   (GEM1M); no edit.

## 7. Reviews

3 families, nonce-gated (nonce `FREEWIRE-NONCE-7Qm4Zt9K` written in the evidence,
quoted back by each): DeepSeek (`ses_f0b7ef95fffe368XttRHtR7uOZ`), NVIDIA
free (`ses_f0b7ef95dffetuZMSQd30zGpN2`), Zhipu GLM free
(`ses_f0b6748bdffeFlroHXtH4Hpacm`) — **all APPROVED**. Details in evidence §10.

## 8. Explicitly NOT done here

The **nebius removal** (follows in the same wave), and **no gateway restart**.

## 9. Fix-up (2026-10-01, operator reversal)

- Redacted the worktree username in `docs/handoff/2026-09-30-laneFreeWire.md`
  to `C:\Users\<user>\…`.
- Corrected the gemini wording across this lane's docs: the operator **reversed
  the earlier decision** — **gemini retained; usage governed by the repeated-429
  backoff policy (3×429/120 s → 300 s cooldown per leg; 30 min park)**. The
  measured evidence (8088 cooling-down lines / 7187 × 429 on 2026-09-30) is kept
  as the policy's justification. No combos/registry content changed — the leg
  restoration is `gemini-restore`'s job on the combined lineage.

### CHANGELOG bullet

- FreeWire fix-up (2026-10-01): redacted the real worktree path in
  `docs/handoff/2026-09-30-laneFreeWire.md` to `C:\Users\<user>`, and corrected
  the lane docs to the operator's reversed gemini policy — **gemini retained;
  usage governed by the repeated-429 backoff policy (3×429/120 s → 300 s cooldown
  per leg; 30 min park)**; the 8088 cooling-down / 7187 × 429 measurements are
  kept as the policy's justification, and no combos/registry content changed (the
  leg restoration is `gemini-restore`'s job on the combined lineage).
