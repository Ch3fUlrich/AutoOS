# Lane FreeWire — free-leg wiring, gemini exclusion, 429 policy (2026-09-30)

**Lane:** `L1-backlog/ws-freewire-20260930`
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-freewire`
**Base:** combos lineage tip `a975d48` (carries the OVH legs, the vertex leg and the 1M contexts)
**Date:** 2026-09-30 (gateway measurements 22:42–22:46Z)
**Writer:** freewire (pinned, critical)
**Input evidence:** `L1-backlog/ws-free-probe-20260930` @ `6e978dd`
(`docs/handoff/2026-09-30-laneFreeProbe.md`)

> **REVIEW NONCE: `FREEWIRE-NONCE-7Qm4Zt9K`** — a reviewer must quote this string
> back, read from THIS file, to prove it read the evidence and not a summary.

Commits on the lane:
- `7eff602` — wire the probe-passed free legs; remove every `gemini/*` leg.
- `4cb49b4` — update the behaviour-pinning tests for the new heads/bands.

---

## 0. What this lane did and did not do

- **Wired** the 10 free legs free-probe proved, into `catalog/ai-registry.json`
  and every rendered surface (combos.json, litellm config.yaml, ide-models.json,
  tier-profiles.json, docs/models.md, opencode.jsonc).
- **Removed** every `gemini/*` leg from every route; repointed the
  `gemini-3.8-flash` combo to `vertex/gemini-3.8-flash`; gated
  `providers.google_ai_studio`.
- **Did NOT** perform the nebius removal (follows in the same wave).
- **Did NOT** restart the gateway.

---

## 1. Free legs wired (10) and the model rows

Proven by free-probe (one `get_weather` tool call answered through the gateway):

| # | Leg | New model row? | tool_calls |
|---|---|---|---|
| 1 | `huggingface/zai-org/GLM-5.2` | reused `zai-org/GLM-5.2` | already `proven` |
| 2 | `huggingface/Qwen/Qwen3.8-27B` | **new** `Qwen/Qwen3.8-27B` | `proven` |
| 3 | `groq/qwen/qwen3.8-27b` | reused `qwen/qwen3.8-27b` | promoted `unproven`→`proven` |
| 4 | `groq/openai/gpt-oss-120b` | reused `openai/gpt-oss-120b` | promoted `unproven`→`proven` |
| 5 | `groq/openai/gpt-oss-20b` | reused `openai/gpt-oss-20b` | promoted `unproven`→`proven` |
| 6 | `openrouter/qwen/qwen3.8-27b:free` | **new** | `proven` (model `tier: free`) |
| 7 | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | **new** | `proven` (`tier: free`) |
| 8 | `openrouter/cohere/north-mini-code:free` | **new** | `proven` (`tier: free`) |
| 9 | `openrouter/poolside/laguna-s-2.1:free` | **new** | `proven` (`tier: free`) |
| 10 | `vertex/gemini-3.8-flash` | reused `gemini-3.8-flash` | repointed combo target |

**Excluded on purpose:** `huggingface/deepseek-ai/DeepSeek-V4-Flash-0731` is V4
weights, not V4.1 — the operator rule *"DeepSeek only V4.1 Flash"* keeps it
referenced by no route (free-probe §3 caveat). `cheaperinference/*` is **paid**,
not free (its 402 cleared, but the registry `tier` is `paid`), so it stays in the
credit band, not the free band. `agentrouter/deepseek-v4-flash` is ack-only (its
endpoint rejects the OpenAI function-call shape). `navy` (Cloudflare 1010),
`opencode`/`opencode-zen` (402 vendor-gated), `antigravity`/`claude` (OAuth
expired), `devin`, `qoder`, `cerebras`, `cloudflare-ai`, `sambanova`, `together`,
`bazaarlink`, `novita`, `bluesminds`, `arcee` all failed at the credential /
config / transport / vendor-ban layer — untouched.

### 1.1 New single-provider combos (7)

`groq-qwen3.8-27b`, `hf-glm-5.2`, `hf-qwen3.8-27b`, `or-laguna-s-2.1-free`,
`or-nemotron-3-super-free`, `or-north-mini-code-free`, `or-qwen3.8-27b-free`
(opencode/zed only, no `openhands_profile`, per the `cheaperinference/kimi-k3`
precedent). Added to `IDE_MODEL_ORDER`.

### 1.2 Free-band insertion (interleaved, before the credits band)

Inserted into `t1-orchestrator-free-only`, `t2-worker`, `t2-worker-free-only`,
`t3-driver`, `t3-driver-free-only`. **Never into `*-clean`**;
`*-free-only` stays free-only. The only `*-clean` change is a **new route-level
gate** on the paid `openrouter/deepseek/deepseek-v4.1-flash` leg in
`t2-worker-clean` (needed because the OpenRouter provider flag moved; see §2).

### 1.3 Policy rules

```jsonc
{ "id": "allow-groq-qwen3.8-27b",  "match": "groq/qwen/qwen3.8-27b",   "allow": true },
{ "id": "allow-groq-gpt-oss",      "match": "groq/openai/gpt-oss-*",   "allow": true },
{ "id": "allow-openrouter-free",   "match": "openrouter/*:free",       "allow": true }
```
The two groq allows sit **above** `deny-groq`; `allow-openrouter-free` sits
**above** `deny-openrouter`. `deny-groq` and `deny-openrouter` still deny every
other leg. **Caveat carried verbatim from free-probe:** one single-tool round is
NOT `deny-groq`'s multi-turn agent workload (400 `messages.N role:assistant` ×335,
413 ×78). Re-probe a real multi-turn agent shape before lifting the blanket
`deny-groq` for the rest.

---

## 2. The OpenRouter guard had to move (free-probe missed this)

free-probe's proposal did not notice that `providers.openrouter.available` was
`false` (the DSMAX guard), and `gateway_legs()` drops every leg of a
provider with `available: false`. Wiring the four `:free` ids therefore required
flipping that flag to `true` and **moving the guard to the route level** for the
two paid legs DSMAX protected:

| route | gated paid openrouter leg |
|---|---|
| `t1-orchestrator`, `t1-orchestrator-clean`, `spark-1.3-contributor` | `openrouter/meta/muse-spark-1.3-contributor` |
| `t2-orchestrator`, `t2-worker`, `t2-worker-clean` | `openrouter/deepseek/deepseek-v4.1-flash` |

`openrouter/openai/gpt-oss-120b` and `openrouter/google/gemini-3.8-flash` keep
their existing route gates. Confirmed by the live combo store: no paid openrouter
leg is served (§5).

---

## 3. Gemini exclusion

### 3.1 Measured reason (2026-09-30, from free-probe §5.1)

- **8088** `gemini` cooling-down lines that day (8218 for all providers = **98.4 %**).
- **7187** of them `lastErrorCode=429`; **7136** `Model gemini-3.8-flash rate_limited`.
- live probe `gemini/gemini-3.8-flash` → HTTP **429** `model_cooldown`
  (`reset_seconds: 51`, `2026-09-30T21:13:58.892Z`).

### 3.2 What changed

- Every `gemini/gemini-3.8-flash` leg removed from the 5 routes that carried it
  (`gemini-3.8-flash`, `t1-orchestrator`, `t1-orchestrator-free-only`,
  `t2-worker`, `t2-worker-free-only`).
- The `gemini-3.8-flash` combo is now exactly `["vertex/gemini-3.8-flash"]`
  (the OpenRouter and DeepInfra mirrors stay gated). `vertex/gemini-3.8-flash`
  was probed by free-probe at 2026-09-30T21:13:23Z (ack 200, tool-call ok).
- `providers.google_ai_studio`: `available: false`,
  `unavailable_until: "2026-10-07T00:00:00Z"`, key left **unwired**.
- `render models-doc` / combos show **no** `gemini/*` leg (verified §5).

**Honest caveat:** the gateway *connection* `google_ai_studio` is not deleted
(no gateway restart), and a direct probe of the unwired leg at 22:46:17Z returned
HTTP 200 — the provider momentarily served again. What is guaranteed is that **no
combo references a `gemini/*` leg** (combo-store scan in §5), so no route can
land on it. The registry marks the provider unavailable for the resolver/config
surfaces.

---

## 4. 429 policy (mission item 3) — outcome

free-probe §6 confirmed v3.8.50 supports `OMNIROUTE_ROTATION_*` /
`OMNIROUTE_ROTATE_ON_429` / `OMNIROUTE_ROTATE_429_{THRESHOLD,WINDOW_SECONDS}` /
`OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS` (`open-sse/services/rotationConfig.ts:84-110`)
and the provider-breaker env family (`open-sse/config/constants.ts:251-277`).

**Surface finding.** The tracked launcher `configuration/start-stack.ps1` starts
`omniroute` but sets **no** `OMNIROUTE_*` rotation/breaker env. The only tracked
resilience surface is `configuration/omniroute/apply.ps1` / `apply.sh`'s
`patch-api-resilience` call, and it **already** applies sane breaker values:

- `providerBreaker.apikey.failureThreshold = 2` (env default 15)
- `providerBreaker.apikey.degradationThreshold = 1` (hops on the first 429 — the
  fast-skip)
- `providerBreaker.apikey.resetTimeoutMs = 30000` (env default cooldown 600000)
- `requestQueue.maxWaitMs = 180000`

(lane-B `docs/handoff/2026-09-30-laneB-combos.md` §5 documents the same block.)

**Therefore:**
- The **provider-breaker family is already applied** through `patch-api-resilience`
  (verified live: dry-run printed `= resilience settings already current
  (maxWaitMs=180000, breaker=2)`); no change was needed.
- The **rotation family has no surface** — it is read from the gateway process
  env only, and the running gateway must be restarted to pick it up (not done
  here). Recorded as a **proposal**, not claimed:

```
OMNIROUTE_ROTATION_ENABLED=true
OMNIROUTE_ROTATE_ON_429=true
OMNIROUTE_ROTATE_429_THRESHOLD=3          # hold the first two 429s; rotate on the third
OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120
OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300
OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000
```

**Cross-branch overlap for L0:** `apply.ps1`/`apply.sh`'s resilience block is the
file lane-B edits (its §3 comment); `start-stack.ps1` is the only plausible
launcher-env surface for the rotation family, and the admission lanes touch these
files on other branches, so this lane deliberately did **not** add env there.

---

## 5. Verification (every command, quoting the result)

| Command | Result |
|---|---|
| `python tools/registry.py check` | `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (exit 0) |
| `python tools/registry.py validate` | same ok line (exit 0) |
| `python tools/registry.py render omniroute --check` | `ok: render omniroute matches ...combos.json` |
| `python tools/registry.py render litellm --check` | `ok: render litellm matches ...config.yaml` |
| `python tools/registry.py render ide --check` | `ok: render ide matches ...ide-models.json` |
| `python tools/registry.py render openhands --check` | `ok: render openhands matches ...tier-profiles.json` |
| `python tools/registry.py render models-doc --check` | `ok: render models-doc matches ...models.md` |
| `python tools/audit-router.py --offline` | `no drift (non-200 legs above are provider/balance state, not config)` (exit 0) |
| `python tests/test_registry_render.py` | `Ran 160 tests ... OK` |
| `python tests/test_registry.py` | `Ran 300 tests ... FAILED (failures=1)` — the **pre-existing** `test_a_credit_leg_is_last_and_gated_until_priced` (see §7) |
| `python tests/test_sync_ide_models.py` | `Ran 36 tests ... OK` |
| `python tests/test_autoos_resolver.py` | `Ran 292 tests ... OK` |
| `python tools/sync-ide-models.py --check` | `OK: opencode.jsonc, tier-profiles.json, config.toml match ai-registry.json` |

**Full-suite sweep vs the `a975d48` baseline** (`tests/test_*.py`, 48 files):
**no new failures**; 7 baseline failures **fixed**
(`test_autoos_resolver.py` ×2, `test_sync_ide_models.py` ×5). Method: the base
commit checked out in a second worktree (`AutoOS-baseline-a975d48`), same sweep,
set-difference by file.

### 5.1 Live apply

```
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1 -DryRun
...
  - t2-worker: would create [priority] with antigravity/gemini-3.7-flash-high,scw/qwen3-235b-a22b-instruct-2507,...,groq/openai/gpt-oss-120b,openrouter/qwen/qwen3.8-27b:free,ovh/gpt-oss-120b,...
  - gemini-3.8-flash: would create [priority] with vertex/gemini-3.8-flash
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1
Gateway OK on http://127.0.0.1:20128
Combos:
  + groq-qwen3.8-27b created (priority)
  + hf-glm-5.2 created (priority)
  + hf-qwen3.8-27b created (priority)
  + or-laguna-s-2.1-free created (priority)
  + or-nemotron-3-super-free created (priority)
  + or-north-mini-code-free created (priority)
  + or-qwen3.8-27b-free created (priority)
  = resilience settings already current (maxWaitMs=180000, breaker=2)
```

**Pre-existing apply.ps1 limitation (baseline-identical, NOT this lane):** on
Windows PowerShell, `Get-AutoOSProviderMap` cannot parse `ai-registry.json`
because two models differ only by case (`mistral-small-3.2-24b-instruct-2506` vs
OVH's `Mistral-Small-3.2-24B-Instruct-2506`), so `ConvertFrom-Json` aborts with
`DuplicateKeysInJsonString`. The same error reproduces at `a975d48` (verified in
the baseline worktree). This lane **adds a second such pair** (`Qwen/Qwen3.8-27B`
vs `qwen/qwen3.8-27b`) — both are the vendors' own spellings. Provider
registration is skipped as a result (connections already exist); combos are still
applied. Flagged for L0: `apply.ps1` should parse case-sensitively.

### 5.2 Ack probes (newly wired legs, `POST /v1/chat/completions`, `max_tokens:16`)

| UTC | Leg | HTTP | ms |
|---|---|---|---|
| 22:45:43Z | `huggingface/zai-org/GLM-5.2` | 200 | 639 |
| 22:45:44Z | `huggingface/Qwen/Qwen3.8-27B` | 200 | 713 |
| 22:45:44Z | `groq/qwen/qwen3.8-27b` | 200 | 217 (`ACK_OK`) |
| 22:45:45Z | `groq/openai/gpt-oss-120b` | 200 | 325 |
| 22:45:45Z | `groq/openai/gpt-oss-20b` | 200 | 286 |
| 22:45:53Z | `openrouter/qwen/qwen3.8-27b:free` | 200 | 7880 |
| 22:45:54Z | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | 200 | 1428 |
| 22:45:55Z | `openrouter/cohere/north-mini-code:free` | 200 | 753 |
| 22:45:58Z | `openrouter/poolside/laguna-s-2.1:free` | 200 | 2799 (`ACK_OK`) |
| 22:46:00Z | `vertex/gemini-3.8-flash` | 200 | 2748 |

No 429/503/504 hit a newly wired leg; no `chat_admission_busy`; no backoff
needed. (Empty content on several reasoning models is expected — the ack budget
is spent on reasoning.)

### 5.3 No `gemini/*` leg remains live (combo store, `omniroute combo list --json`)

```
gemini-3.8-flash: vertex/gemini-3.8-flash
GEMINI LEGS LIVE: []
```

### 5.4 ≥2-usable-provider invariant per agentic route

`tests/test_registry.py::usable_legs` (`live_legs` ∩ `tool_calls: proven` ∩
priced-credit ∩ context-fits):

| route | usable legs | distinct providers | providers |
|---|---|---|---|
| `t1-orchestrator` | 3 | **2** | nebius, scaleway |
| `t1-orchestrator-free-only` | 6 | 5 | groq, hugging_face, nebius, openrouter, scaleway |
| `t2-worker` | 14 | 6 | groq, hugging_face, nebius, openrouter, ovhcloud, scaleway |
| `t2-worker-free-only` | 12 | 5 | groq, hugging_face, nebius, openrouter, scaleway |
| `t3-driver` | 14 | 6 | groq, hugging_face, nebius, openrouter, ovhcloud, scaleway |
| `t3-driver-free-only` | 10 | 5 | groq, hugging_face, nebius, openrouter, scaleway |

`test_every_agentic_route_has_two_distinct_usable_providers` and
`test_every_agentic_route_has_three_usable_legs` both pass; the resolver's own
cross-provider-fallthrough test passes too.

---

## 6. Mission item 4 — `context_advertised 131072→1048576`

Not needed: `catalog/ai-registry.json` line 403 (`models.gemini-3.8-flash.
context_advertised`) is already `1048576` (the GEM1M change, before this lane).
No edit.

---

## 7. Pre-existing failure left in place (NOT this lane)

`tests/test_registry.py::test_a_credit_leg_is_last_and_gated_until_priced`
fails at `a975d48` exactly as it does here (baseline sweeps equal). Root cause is
the combos-lineage work: an odd number of `credit` legs sit **not** at the end of
a route and are not gated with a "price" comment — `vertex/gemini-3.8-flash` in
`gemini-3.8-flash` and the three `ovhcloud/*` legs in `t2-worker`/`t3-driver`.
This lane removed the gemini head (the first route the test reaches), which does
not fix and does not worsen it. Flagged for L0.

---

## 8. Files changed

| File | Change |
|---|---|
| `catalog/ai-registry.json` | 5 new model rows; 3 rows `tool_calls proven`; 3 policy rules; openrouter flag flip + guard move; google_ai_studio gate; 7 new routes; legs/free-bands on 6 routes; route gates |
| `configuration/omniroute/combos.json` | rendered |
| `configuration/litellm/config.yaml` | rendered (7 new managed blocks) |
| `catalog/ide-models.json` | rendered (27 models) |
| `configuration/openhands/tier-profiles.json` | rendered |
| `docs/models.md` | rendered models-doc block |
| `opencode.jsonc` | re-synced |
| `tools/registry.py` | `IDE_MODEL_ORDER` += 7 new route ids |
| `tests/test_registry.py`, `tests/test_registry_render.py`, `tests/test_sync_ide_models.py`, `tests/test_autoos_resolver.py`, `tests/test_autoos_spawner.py` | behaviour-pinning updates |

## 9. Cross-branch overlaps for L0

- `configuration/omniroute/apply.ps1` / `apply.sh` resilience block — lane-B.
  This lane made **no** change there (429 policy = proposal).
- `configuration/start-stack.ps1` — the only plausible launcher-env surface for
  the rotation family; admission lanes edit launcher files on other branches, so
  untouched.
- `catalog/ai-registry.json`, `combos.json` — single-writer this wave.
- The nebius removal (same wave, after this lane) edits the same registry/combos.

## 10. Review

Three `t3-reviewer` subagents ran read-only, on three different families,
**including two free models** (operator mandate). Each was nonce-gated: the
reviewer had to quote `FREEWIRE-NONCE-7Qm4Zt9K` read from this file. All three
read it back correctly. No reviewer edited anything.

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | `omniroute/t3-driver-clean` (DeepSeek) | APPROVED | `ses_f0b7ef95fffe368XttRHtR7uOZ` |
| 2 | `omniroute/or-nemotron-3-super-free` (NVIDIA, **free**) | APPROVED | `ses_f0b7ef95dffetuZMSQd30zGpN2` |
| 3 | `omniroute/hf-glm-5.2` (Zhipu GLM, **free**) | APPROVED | `ses_f0b6748bdffeFlroHXtH4Hpacm` |

What each read and verified:

- **#1 (DeepSeek):** `git show 7eff602`, a scan of every route `legs` for
  `gemini/*` (none), the 7 new routes, free-before-credit ordering, that no
  `*-clean` route gained a leg (`t2-worker-clean`'s only change is a route gate);
  `registry.py check` (ok line quoted); `tests/test_registry.py` (1 failure,
  proved pre-existing by recomputing at `a975d48`); the ≥2-provider invariant on
  all six agentic routes (2/5/6/5/6/5). Verdict APPROVED.
- **#2 (NVIDIA, free):** read `policy.leg_rules` + `providers.openrouter` at
  `7eff602`; proved every paid `openrouter/*` leg is denied or route-gated and
  none is `gateway_servable`; read the live `omniroute combo list --json` and
  confirmed only `:free` openrouter legs are served; ran
  `tests/test_registry.py` and `tests/test_autoos_resolver.py`. Verdict APPROVED.
- **#3 (GLM, free):** `git show 4cb49b4`, judged the three test edits honest
  (`leg_tier` effective-tier, the documented single case-fold verdict change on a
  non-served spelling, the `not head_ladder` default-forward re-pin); compared
  failure sets against the `a975d48` baseline worktree — **no new failures, 7
  fixed**; ran `render omniroute --check` and `sync-ide-models.py --check` (both
  ok). Verdict APPROVED.

**Corroborating evidence for `deny-groq`:** a first attempt to route reviewer #3
through `omniroute/groq-qwen3.8-27b` failed at the model layer with the gateway's
own line —

```
[413]: Request too large for model `qwen/qwen3.8-27b` ... input tokens per minute
(ITPM): Limit 7000, Requested 14023
```

— i.e. the 413 class free-probe recorded for `deny-groq` (`413 too large x78`)
reproduces on a real review payload. The reviewer was re-routed to
`omniroute/hf-glm-5.2` (free) and completed. This does not overturn the wired
groq free band (which the single-tool probes passed) but reinforces free-probe's
multi-turn caveat.

