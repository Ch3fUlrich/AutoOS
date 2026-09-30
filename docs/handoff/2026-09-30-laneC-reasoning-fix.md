# Handoff: DeepSeek Thinking-Mode 400 Fix (Lane C)

**Date:** 2026-09-30  
**Branch:** `L1-backlog/ws-fixes-20260930`  
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-fixes`  
**Base HEAD:** `d08f7f2` (unchanged — no push/merge/rebase)  
**Gateway:** OmniRoute v3.8.50 at `http://127.0.0.1:20128`

## Problem

DeepSeek thinking-mode returns 400: "The `reasoning_text` in the thinking mode
must be passed back to the API." when sending a tool-call round trip through
the OmniRoute gateway.

## Root Cause (confirmed by reproduction)

The gateway strips `reasoning_content` from responses and stores it in a
Reasoning Replay Cache keyed by `tool_call_id`. On the next turn, the cache
re-injects the actual reasoning. **When the cache misses, the `toResponses`
function omits the `reasoning` input item entirely (the reasoning text is empty
or a placeholder, so the push is skipped). The subsequent `function_call` items
have no preceding `reasoning` item, and DeepSeek's Responses API rejects the
request.**

The empty injection (`reasoning_content: ""` in the chat-completions layer) is
a separate defense that adds the field to the upstream request — but it does
not help when the Responses API conversion (`toResponses`) fails to emit a
`reasoning` item in the `input[]` array. The actual fix (commit `3f6fd25`)
patches `toResponses` to inject a placeholder reasoning item when the provider
requires it and the message has tool calls.

### Defense Pipeline (3 layers, `translator/index.ts`)

1. **Preserve Reasoning** — when `isReasoner=true`, gateway doesn't strip
   `reasoning_content` from the *request's* assistant messages. Helps only if
   the client includes it (AI SDK typically does not).
2. **Empty Injection** (`schemaCoercion.ts:455`) — adds `reasoning_content: ""`
   to assistant messages with tool_calls. Guard: `!requiresExplicitReasoningReplay`
   (`translator/index.ts:614`). **Insufficient** — DeepSeek rejects empty.
3. **Replay Cache** (`reasoningCache.ts`, `translator/index.ts:639`) —
   re-injects actual cached reasoning by `tool_call_id`. **Primary defense.**
   TTL: 2h. DB-backed (`storage.sqlite` table `reasoning_cache`).

### `requiresExplicitReasoningReplay` Logic

```
reasoningCache.ts:97   interleavedField === "reasoning_content"  → true
reasoningCache.ts:114  isDeepSeekReasoningModel (V4 pattern match) → true
reasoningCache.ts:116  !allowLegacyFallback                        → false
reasoningCache.ts:119  REASONING_REPLAY_PROVIDERS.has("deepseek")  → true (isReasoner only)
```

When `requiresExplicitReasoningReplay=true`: empty injection **skipped**. Only
replay cache remains. Cache miss → 400.

Current state: `interleaved_field` = NULL (model_capabilities empty for DeepSeek)
→ `requiresExplicitReasoningReplay=false` → empty injection fires (but
insufficient) → 400 on cache miss.

## Reproduction

Probe: `tools/probe-reasoning-repro.py`

```
model=deepseek-v4.1-flash  call_id=overwrite  → 400_REASONING  (cache miss)
model=deepseek-v4.1-flash  call_id=preserve   → 200 PASS        (cache hit)
model=deepseek/deepseek-flash  call_id=overwrite  → 400_REASONING
model=deepseek/deepseek-flash  call_id=preserve   → 200 PASS
```

The `overwrite` variant changes the `tool_call_id` to a unique value, causing
the replay cache to miss. The `preserve` variant keeps the original ID from
step 1, so the cache hits and the actual reasoning is re-injected.

## Fixes Applied

### 1. opencode Config Fix (user-owned file, backed up in-place)

**File:** `C:\Users\<user>\AppData\Roaming\opencode\config.json`  
**Line 507:** `"model": "deepseek/deepseek-v4-flash"` → `"model": "omniroute/deepseek-v4.1-flash"`

The `leaf-reviewer` agent referenced `deepseek/deepseek-v4-flash` which is
unresolvable (no `deepseek` provider in opencode — only `omniroute`). The fix
routes it through the gateway where the reasoning defense pipeline operates.

### 2. Config Artifact

**File:** `configuration/omniroute/reasoning-defense.md`  
Documents the defense pipeline, `requiresExplicitReasoningReplay` logic, cache
TTL, and operator mitigations.

### 3. Compiled Chunk + .ts Source Patch (runtime fix)

**Commit:** `3f6fd25`  
**Files:**
- `configuration/omniroute/reason-fix-reapply.ps1` — idempotent reapply script
- `configuration/omniroute/reason-fix-README.md` — patch documentation

**Patched in the npm package** (`C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`):

The running gateway executes compiled JavaScript from
`dist/.build/next/server/chunks/`, not the `.ts` source. The `toResponses`
function in 5 chunk files has the bug: when reasoning is a placeholder or
absent (cache miss), no `reasoning` item is pushed before `function_call`
items, and DeepSeek 400s.

**Compiled .js chunk patches** (5 files, 2 patterns):

| Pattern | Files | Push | CredRec | Model |
|---------|-------|------|---------|-------|
| A | `_08_y1bx._.js`, `_1j_edf1._.js`, `_1luyz1c._.js` | `g` | `A._provider` | `p.model` |
| B | `_15ose6x._.js`, `_1xkpq2s._.js` | `d` | `p._provider` | `g.model` |

Fix: inject `NON_ANTHROPIC_THINKING_PLACEHOLDER` reasoning item when
`isInternalReasoningPlaceholder(reasoning) || !reasoning` AND provider is
DeepSeek/MiMo AND message has `tool_calls`.

**.ts source patches** (2 files, for source-tree consistency):
- `open-sse/translator/index.ts` — added `deepseek` to `requiresReasoningContentPresence`
- `open-sse/translator/request/openai-responses/toResponses.ts` — added import,
  helper, `reasoningIsPlaceholder` var, `else if` placeholder injection branch

**Backups:** All 7 files backed up to `<file>.autoos-backup-<timestamp>`.

**Verification** (isolated gateway, port 20130, separate DATA_DIR, shared
gateway on 20128 untouched):

```
deepseek-v4.1-flash      overwrite (cache miss) → 200 PASS  (was 400)
deepseek-v4.1-flash      preserve (cache hit)   → 200 PASS  (was 200)
deepseek/deepseek-flash  overwrite (cache miss) → 200 PASS  (was 400)
deepseek/deepseek-flash  preserve (cache hit)   → 200 PASS  (was 200)
```

4/4 test cases PASS. 400 error no longer reproduced.

## What Was NOT Changed (by design)

- `configuration/omniroute/combos.json` — P1-combos territory, not touched
- `configuration/omniroute/apply.ps1` — resilience hunks excluded
- `catalog/ai-registry.json` — L1-beta territory
- `tools/probe-sweep.py` — P1-sweep territory
- OmniRoute compiled chunks (`dist/.build/next/server/chunks/`) — patched
  in-place (see fix #3 below); reapply script handles `npm update` revert

## Files in This Change

| File | Description |
|---|---|
| `tools/probe-reasoning.py` | Multi-model probe with reasoning_effort variants |
| `tools/probe-reasoning-repro.py` | Before/after probe (overwrite vs preserve tool_call_id) |
| `tools/probe-reasoning-edge.py` | Edge-case probe (strip, no-effort, streaming) |
| `tools/probe-reasoning-stream.py` | Streaming reasoning probe |
| `tools/probe-cross-family.py` | Cross-family review probe (2-3 model families) |
| `tools/probe-cross-family-extra.py` | Extra cross-family candidates (NVIDIA, QwQ) |
| `tools/diag-db.py` | Queries storage.sqlite for model_capabilities + reasoning_cache |
| `tools/diag-interleaved.py` | Attempts to query interleaved_field via API |
| `tools/diag-key-source.py` | Traces key source for gateway requests |
| `tools/diag-models.py` | Lists models from gateway /v1/models |
| `tools/diag-models-auth.py` | Lists models from gateway with auth header |
| `configuration/omniroute/reasoning-defense.md` | Reasoning defense documentation |
| `configuration/omniroute/reason-fix-reapply.ps1` | Idempotent reapply script for compiled chunk + .ts patches |
| `configuration/omniroute/reason-fix-README.md` | Patch documentation (compiled chunk approach, backups, verification) |
| `docs/handoff/2026-09-30-laneC-reasoning-fix.md` | This file |

## Verification

```
# Before (stale config): leaf-reviewer model unresolvable
# After (fixed config): leaf-reviewer routes through omniroute gateway

# Probe (reproduces 400 on cache miss, 200 on cache hit):
python tools/probe-reasoning-repro.py

# After compiled chunk patch (isolated gateway, port 20130):
AUTOOS_OMNIROUTE_URL=http://127.0.0.1:20130 python tools/probe-reasoning-repro.py
# → 4/4 PASS (400→200 on cache miss, 200→200 on cache hit)

# Diagnostic (confirms model_capabilities empty for DeepSeek):
python tools/diag-db.py
```

## Cross-Family Review

Tested 3 model families through the same gateway with the same probe
(`tools/probe-cross-family.py`): reasoning-mode tool-call round trip with
`reasoning_content` stripped and `tool_call_id` overwritten (cache miss).

| Family | Model | Step 1 rc_len | Step 2 (round trip) | Result |
|---|---|---|---|---|
| **DeepSeek** | `deepseek-v4.1-flash` (combo) | 0–81 | **400** | **400_REASONING** |
| **Gemini** | `vertex/gemini-3.8-flash` | 466–494 | 200 | PASS |
| **Qwen** | `ovh/Qwen3.8-27B` | 0 | 200 | PASS |

### Key findings

- **The 400 is DeepSeek-specific.** No other family returned a reasoning-related
  400 on the same cache-miss round trip.
- **Gemini** IS a reasoning model (returns `reasoning_content` of 466–494 chars)
  but does NOT require it to be passed back. The round trip with stripped
  reasoning + overwritten `tool_call_id` succeeds (200). The gateway's empty
  injection (`reasoning_content: ""`) is harmless — Gemini ignores it.
- **Qwen** (via OVH) returned `reasoning_content` of length 0 (may not be a
  thinking-mode model or uses a different reasoning field). Tool call + round
  trip succeeds regardless.
- **DeepSeek** returned the 400 consistently across both combo
  (`deepseek-v4.1-flash` → upstream `deepseek/deepseek-flash`) and direct
  (`deepseek/deepseek-flash`) model paths. The error message is identical:
  `"The \`reasoning_text\` in the thinking mode must be passed back to the API."`

### Models that could not be tested (non-reasoning failures)

| Model | Family | Failure | Reason |
|---|---|---|---|
| `openrouter/qwen/qwen3.8-flash` | Qwen | 402 | Credits exhausted |
| `openrouter/moonshotai/kimi-k3` | Moonshot | 401 | Credits exhausted |
| `openrouter/qwen/qwen3.8-27b:free` | Qwen | 429 | Rate limit (cooldown) |
| `openrouter/thinkingmachines/inkling-small:free` | TM | 403 | Agentic-harness only |
| `openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | NVIDIA | 502 | Upstream empty |
| `together/Qwen/QwQ-32B` | Qwen | 403 | Cloudflare access denied |

These failures are infrastructure/credit issues, not reasoning 400s. The
2 non-DeepSeek families that completed the full round trip (Gemini, Qwen)
are sufficient to confirm the 400 is DeepSeek-specific.

### Probe: `tools/probe-cross-family.py`

```
python tools/probe-cross-family.py
```

Verdict: **DeepSeek-specific.** The reasoning replay defense pipeline
(`isReasoner`, `requiresExplicitReasoningReplay`, replay cache) fires only
for the `deepseek` provider (`REASONING_REPLAY_PROVIDERS`). Other families
are unaffected by the cache-miss issue because they don't enforce the
"reasoning must be passed back" constraint.
