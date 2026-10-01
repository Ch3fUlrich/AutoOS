# Handoff: 19 Missing + 1 Reconciled Operator-Listed Providers (Lane A Rescue)

**Date:** 2026-09-30  
**Branch:** `L1-backlog/ws-providers-rescue-20260930`  
**Worktree:** `AutoOS-ws-providers-rescue`  
**Writer:** t2 smart-reasoning-128k (lane A rescue, ws-providers-rescue)

## Objective

Add the 20 operator-listed providers that were missing from `catalog/ai-registry.json`,
probe each through the OmniRoute gateway, and register every one as `available: false`
with a measured `$comment` — none could serve a chat completion.  19 remain on this
branch; the 20th (ovhcloud) was reconciled to the sister ws-ovh lane (f6f5e69, credited
tier / 200 USD) and its stub dropped here (L0 decision D1).

## Context

Lane A (`AutoOS-ws-providers`) had attempted this work but shipped 4 wrong
`available: false → true` flips (navyai, arcee, bluesminds, together_ai) with
self-contradictory `$comment` text, and mutilated the test file (916 lines vs HEAD 3437).
This rescue branch starts from `d08f7f2` (clean HEAD) and redoes the work correctly.

## Provider key renames

Two of the 19 remaining provider names collided with existing `clients` section IDs
(`_check_unique_ids` checks across providers + models + clients + routes):

| Intended key | Renamed to | omniroute_id | Reason |
|---|---|---|---|
| `opencode` | `opencode_gateway` | `opencode` | `clients.opencode` exists (the app) |
| `qoder` | `qoder_ai` | `qoder` | `clients.qoder` exists (the app) |

All other 17 keys are unchanged from the operator listing.

## meta/meta_api base URL investigation

**Verdict: KEEP** `https://api.meta.ai/v1`.

The handoff doc §5 (`2026-09-30-omniroute-gateway-findings.md`) already has the
solution, applied 2026-09-30: `meta-api` is wired as an OpenAI-compatible node
(`POST /api/provider-nodes` with `type: openai-compatible, apiType: chat`), not a
built-in OmniRoute provider type. The live gateway confirms the meta-api
connection is active (id `627d8593`) and serving models. No change needed.

## together_ai probe

**Verdict: KEEP** `available: false`.

276 models listed in `/v1/models` under prefix `together`, but ALL chat
completion probes returned HTTP 400 "not available in the active live catalog
for provider 'together'". Tool-call probe also 400. The FREEKEYS-1 `$comment`
already records 403 x3; this re-probe confirms it still cannot serve.

## Lane A wrong-flip confirmation

Lane A flipped 4 providers to `available: true` with self-contradictory comments
("No leg answered, so available is false" then "Updated 2026-09-30: the key now
works (200 on /v1/models), so available is true"). GET /v1/models returning 200
does NOT mean chat completions work — it only lists the catalog. My base at
`d08f7f2` already has all 4 correctly at `available: false`. No revert needed.

## Per-provider coverage table

All 20 probed through the OmniRoute gateway at `localhost:20128` on 2026-09-30 (19 registered here + ovhcloud reconciled to ws-ovh).
Probe method: `GET /v1/models` (read-only catalog) + `POST /v1/chat/completions`
(chat trial, max_tokens 16). None answered a chat completion.

| # | Provider key | omniroute_id | model_prefix | Gateway connection | Models in /v1/models | Chat probe result | available |
|---|---|---|---|---|---|---|---|
| 1 | api_airforce | api-airforce | af | not in 32 active | 0 | nothing to probe | false |
| 2 | llm7 | llm7 | null | not in 32 active | 0 | nothing to probe | false |
| 3 | nscale | nscale | null | not in 32 active | 0 | nothing to probe | false |
| 4 | siliconflow | siliconflow | null | not in 32 active | 0 | nothing to probe | false |
| 5 | sealion | sealion | null | not in 32 active | 0 | nothing to probe | false |
| 6 | routeway | routeway | null | not in 32 active | 0 | nothing to probe | false |
| 7 | requesty | requesty | null | not in 32 active | 0 | nothing to probe | false |
| 8 | aion_labs | aion | null | not in 32 active | 0 | nothing to probe | false |
| 9 | agnes | agnes | null | not in 32 active | 0 | nothing to probe | false |
| 10 | pollinations | pollinations | pol | not in 32 active | 0 | nothing to probe | false |
| 11 | g4f | g4f-pollinations | g4f | not in 32 active | 0 | nothing to probe | false |
| 12 | kilo_gateway | kilo-gateway | kg | not in 32 active | 0 | nothing to probe | false |
| 13 | ainative | ainative | null | not in 32 active | 0 | nothing to probe | false |
| 14 | ovhcloud | ovhcloud | null | not in 32 active | 0 | nothing to probe | reconciled to ws-ovh f6f5e69 (credited tier, 200 USD) — stub removed |
| 15 | felo | felo-web | felo | noauth (not in 32 list) | 5 | 400 "thread creation failed" x3 | false |
| 16 | uncloseai | uncloseai | unc | noauth (not in 32 list) | 3 | 502 x3 | false |
| 17 | opencode_gateway | opencode | null | connected (active) | 121 | 400 "not available in active live catalog" x3 | false |
| 18 | ai_horde | aihorde | horde | noauth (not in 32 list) | 165 | 400 "image-generation model" x3 | false |
| 19 | z_ai | zai | null | not in 32 active | 0 | nothing to probe | false |
| 20 | qoder_ai | qoder | null | not in 32 active | 0 | nothing to probe | false |

**Notes:**
- "not in 32 active" = the provider type exists in `omniroute providers available`
  (352 types) but no connection was registered (`omniroute providers list` shows 32
  active connections).  The operator's `api-keys.yml` has a key name for each, but
  `apply` has not registered the connection on this gateway.
- "noauth" = the provider serves without an API key (no connection needed), but
  still appears in `/v1/models` with models.  Not in the 32-connection list because
  noauth providers don't need a registered connection.
- opencode_gateway: the free noauth tier serves only the opencode client
  directly, not gateway chat completions.  The paid `zen` provider
  (`omniroute_id: opencode-zen`) is a separate entry with its own key.
- z_ai: distinct from `zcode` (ZCode GLM Coding Plan, alias `zc`) which IS
  connected with 13 GLM models.

## Test coverage

Five new tests in `ThirdPartyClaudeLegTests` (`tests/test_registry.py`):

1. `test_each_of_the_19_missing_providers_is_registered` — all 19 keys exist in `providers`
2. `test_each_of_the_19_missing_providers_is_available_false` — `available` is exactly `False`
3. `test_each_of_the_19_missing_providers_has_a_measured_comment` — `$comment` has date + "available is false"
4. `test_the_19_missing_providers_register_no_claude_model` — no Claude legs served (D-102)
5. `test_the_19_missing_providers_appear_in_no_route_legs` — no route carries a leg from any of the 19

A 6th test (`test_the_19_missing_providers_carry_no_model_rows`) was removed after
cross-family review found it a no-op (read a `provider` field no model object has);
its intent is covered by test 5.

Full suite: **305 passed, 33 subtests passed** (300 original + 5 new).
Registry CLI check: `ok: registry 2026-09-28, 25 routes, 71 models, 52 providers` (was 53 before ovhcloud stub removal).

## Files changed

- `catalog/ai-registry.json`: +19 provider entries (52 total, was 33); ovhcloud reconciled to ws-ovh lane f6f5e69 (credit entry lands at L1 merge)
- `tests/test_registry.py`: +5 tests in `ThirdPartyClaudeLegTests` (305 total, was 300)

## NOT touched (per brief constraints)

- `configuration/omniroute/combos.json` — not modified
- `tools/autoos-*.py`, `tools/registry.py`, `tools/sync-*` — not modified
- Main checkout — only read `api-keys.yml` (git-ignored) and `docs/api-keys.md` for key NAMES
- Lane A worktree (`AutoOS-ws-providers`) — read-only reference only

---

# P0-VALIDATE — independent verification (successor session, 2026-09-30)

**Validator:** t2 smart-reasoning-128k (P0-VALIDATE, ws-omniroute-20260930)
**Method:** re-probed the gateway myself at `http://127.0.0.1:20128` (key loaded from
git-ignored `api-keys.yml`, value never printed); `GET /v1/models` once (5295 models, 68
prefixes) + one chat ack per provider, >=3.5 s apart. Suite re-run: `305 passed, 33 subtests`.

## Spot-check table (7 of 19 + together_ai, spanning all 4 categories)

| Provider (reg key) | Category | Gateway models (my count) | Chat probe (verbatim) | reg `available:false` matches? |
|---|---|---|---|---|
| opencode_gateway | known-connected | 119 under `opencode` | 402 "requires an opencode API key — add one in Settings → Providers" (resolved to opencode-zen) | YES |
| ai_horde | noauth | 165 under `aihorde` | 400 "is an image-generation model and cannot be used on /v1/chat/completions" | YES |
| felo | noauth + probe-failed | 5 under `felo` | 400 "Felo thread creation failed with HTTP 400" | YES |
| uncloseai | probe-failed (502) | 3 under `unc` | 502 "Unknown error" / bad_gateway | YES |
| api_airforce | zero-model | 0 under `af` | 401 "No active credentials for provider: api-airforce." | YES |
| llm7 | zero-model | 0 under `llm7` | 401 "No active credentials for provider: llm7." | YES |
| z_ai | zero-model | 0 under `zai` | 401 "No active credentials for provider: zai." | YES |
| together_ai (existing) | — | 276 under `together` | 400 "Model 'meta-llama/Llama-3.3-70B-Instruct-Turbo-Free' is not available in the active live catalog for provider 'together'." | YES |

**Verdict:** all 7 spot-checked + together_ai: none served a chat completion; each
registry `available:false` still matches reality. The exact together_ai 400 body is
recorded above (mission item 1).

## Completeness (all 47 operator-listed → registry keys)

All 47 operator-listed providers map to a registry key — **no gaps**. Renames/mismatches:

| Operator name | Registry key | Reason |
|---|---|---|
| `agnes_ai` | `agnes` | registry used short key |
| `kilo` | `kilo_gateway` | renamed (collision with client id) |
| `opencode` | `opencode_gateway` | renamed (collision with clients.opencode) |
| `qoder_pat` | `qoder_ai` | renamed (collision with clients.qoder) |

6 registry providers are NOT in the operator 47-list (pre-existing base): `antigravity`,
`cc`, `cxa`, `meta_api`, `omniroute`, `samba`. Registry total: 52 providers (53 after ws-ovh merges its credit entry).

## OVH reconciliation (L0-flagged)

This branch's `ovhcloud` (a8fa659): tier `free`, credit_usd 0, available false,
model_prefix `null`, no model rows; `$comment` claims "1 canonical model, 400 x1" —
but the evidence doc row 14 says "0 models, nothing to probe" (inconsistent stub text).

Sister lane ws-ovh (f6f5e69 + 2abd09f): tier `credit`, credit_usd 200, available **true**,
model_prefix `ovh`, 3 model rows (Meta-Llama-3_3-70B-Instruct, Mistral-Small-3.2-24B-Instruct-2506,
Qwen3-Coder-30B-A3B-Instruct; gpt-oss-120b reuses shared row), `test_shipped_grants`
updated to `ovhcloud: 200`, version bumped to 2026-09-30. Connection already active
(id 2e7f59a9); 24 models, 4 proven 3/3 tool-call trials.

**Which entry wins:** ws-ovh (f6f5e69) — it has live models, proven tool calls, real
pricing, correct prefix `ovh` (this branch's stub used prefix `null`/`ovhcloud` and
found nothing). This branch's stub is a stale, inconsistent placeholder.

**What the rescue branch changed (D1, IMPLEMENTED 2026-09-30):** dropped its `ovhcloud`
stub so the ws-ovh lane's entry lands without conflict (both branches added a
`providers.ovhcloud` key → merge conflict otherwise; L1 takes ws-ovh's version at merge).

**Minimal patch (IMPLEMENTED):**
1. `catalog/ai-registry.json`: removed the `providers.ovhcloud` block (14 lines, the
   free/0/false stub added by a8fa659). Result: 52 providers on this branch; final
   main = 53 after ws-ovh merges its credit entry.
2. `tests/test_registry.py`: removed `"ovhcloud"` from the 20-element list (→ 19-list,
   renamed `MISSING_20`→`MISSING_19`, test names `..._20_missing...`→`..._19_missing...`,
   comment/docstring/error-message 20→19); net tests 305 (assertions are list-membership,
   so removing one element keeps them green — the red was confirmed first: "ovhcloud
   missing from providers", failures=1 errors=2, then the fix).
3. `docs/handoff/2026-09-30-laneA-providers.md`: coverage table row 14 → "reconciled to
   ws-ovh f6f5e69 (credited tier, 200 USD) — stub removed"; "20 missing" wording →
   19 + 1 reconciled throughout.

**Why now implemented:** L0 decision D1 approved the reconcile; cross-family review
(R-orch-13) recorded with a reviewer from a 3rd model family (distinct from the two
earlier t3-reviewer approvals). Merge order remains the L1's call (R-coord-01). The
ws-ovh lane carries the correct entry + its own test update; this branch does NOT
duplicate ws-ovh's ~70-line registry diff — it drops the stub so the merge is
conflict-free.

## Minor findings (not blocking)

- **ai_horde prefix mismatch:** registry `model_prefix` is `horde`, but the gateway lists
  its 165 models under `aihorde` (the omniroute_id). Moot — ai_horde is image-generation
  only and `available:false`, so no chat leg is ever built; resolve_leg() never uses the
  prefix. Latent if someone later flips it true, but it can never serve chat regardless.
- **opencode_gateway model drift:** evidence doc said 121 models; my probe saw 119 (2
  dropped since the prior session — normal gateway refresh). Chat still fails (402 now,
  was 400). `available:false` unaffected.
