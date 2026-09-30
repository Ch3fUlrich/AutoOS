# Handoff: 20 Missing Operator-Listed Providers (Lane A Rescue)

**Date:** 2026-09-30  
**Branch:** `L1-backlog/ws-providers-rescue-20260930`  
**Worktree:** `AutoOS-ws-providers-rescue`  
**Writer:** t2 smart-reasoning-128k (lane A rescue, ws-providers-rescue)

## Objective

Add the 20 operator-listed providers that were missing from `catalog/ai-registry.json`,
probe each through the OmniRoute gateway, and register every one as `available: false`
with a measured `$comment` — none could serve a chat completion.

## Context

Lane A (`AutoOS-ws-providers`) had attempted this work but shipped 4 wrong
`available: false → true` flips (navyai, arcee, bluesminds, together_ai) with
self-contradictory `$comment` text, and mutilated the test file (916 lines vs HEAD 3437).
This rescue branch starts from `d08f7f2` (clean HEAD) and redoes the work correctly.

## Provider key renames

Two of the 20 provider names collided with existing `clients` section IDs
(`_check_unique_ids` checks across providers + models + clients + routes):

| Intended key | Renamed to | omniroute_id | Reason |
|---|---|---|---|
| `opencode` | `opencode_gateway` | `opencode` | `clients.opencode` exists (the app) |
| `qoder` | `qoder_ai` | `qoder` | `clients.qoder` exists (the app) |

All other 18 keys are unchanged from the operator listing.

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

All 20 probed through the OmniRoute gateway at `localhost:20128` on 2026-09-28.
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
| 14 | ovhcloud | ovhcloud | null | not in 32 active | 0 | nothing to probe | false |
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

Six new tests in `ThirdPartyClaudeLegTests` (`tests/test_registry.py`):

1. `test_each_of_the_20_missing_providers_is_registered` — all 20 keys exist in `providers`
2. `test_each_of_the_20_missing_providers_is_available_false` — `available` is exactly `False`
3. `test_each_of_the_20_missing_providers_has_a_measured_comment` — `$comment` has date + "available is false"
4. `test_the_20_missing_providers_register_no_claude_model` — no Claude legs served (D-102)
5. `test_the_20_missing_providers_carry_no_model_rows` — no `models.<id>` has one as its `provider`
6. `test_the_20_missing_providers_appear_in_no_route_legs` — no route carries a leg from any of the 20

Full suite: **306 passed, 33 subtests passed** (300 original + 6 new).
Registry CLI check: `ok: registry 2026-09-28, 25 routes, 71 models, 53 providers`.

## Files changed

- `catalog/ai-registry.json`: +20 provider entries (53 total, was 33)
- `tests/test_registry.py`: +6 tests in `ThirdPartyClaudeLegTests` (306 total, was 300)

## NOT touched (per brief constraints)

- `configuration/omniroute/combos.json` — not modified
- `tools/autoos-*.py`, `tools/registry.py`, `tools/sync-*` — not modified
- Main checkout — only read `api-keys.yml` (git-ignored) and `docs/api-keys.md` for key NAMES
- Lane A worktree (`AutoOS-ws-providers`) — read-only reference only
