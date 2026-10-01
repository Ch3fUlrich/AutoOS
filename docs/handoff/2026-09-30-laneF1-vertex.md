# Vertex/Gemini Trailing Model Turn Fix - Lane F1 - 2026-09-30

## Overview

This document describes the fix for the "Requests ending with a model turn are not supported" 400 error in OmniRoute's Vertex/Gemini executor. The fix involves adding a trailing model turn strip to the `openai-to-gemini.ts` file and updating the compiled chunks.

## Root Cause

The issue occurs because the `stripTrailingAssistantForProvider` function in `contextManager.ts` only strips trailing assistant messages WITHOUT tool_calls. When a trailing assistant message has tool_calls, it's left in. The standard Vertex executor path lacks this strip, while the Antigravity executor handles it correctly.

## Fix Implementation

The fix adds a trailing model turn strip in `openai-to-gemini.ts`'s `openaiToGeminiBase` function, after the `mergeConsecutiveSameRoleContents` call (line 569). The strip guards against emptying the contents array.

### Files Modified

> Corrected 2026-09-30 by lane `patch-fix` (`docs/handoff/2026-09-30-lanePatchFix.md`),
> after hashing every file against the published `omniroute@3.8.50` npm tarball.
> The original list below named the wrong four chunks; only one of them actually
> carries this strip.

1. `open-sse/translator/request/openai-to-gemini.ts` — **patched** (tarball 36018 B →
   live 36778 B; strip inserted after `result.contents = mergeConsecutiveSameRoleContents(...)`).

Of the four compiled chunks this document originally named, **three differ from the
tarball and one does not** — but only one of the three carries the trailing-turn strip:

| chunk | tarball → live | trailing-turn strip? |
|---|---|---|
| `_0o8_5h8._.js` | 870668 → 871423 (+755 B) | **no** — the change is not this fix (§unattributed, lanePatchFix) |
| `_0t1t5fj._.js` | 870668 → 871423 (+755 B) | **no** — same |
| `_18ct13i._.js` | 1302308 → 1302588 (+280 B) | yes (strip accounts for +174 B of it) |
| `_14jycqh._.js` | 21659 → 21659 (0) | **no — byte-identical, unpatched** |

The strip is actually applied to the **six** chunks that define the
`openaiToOpenAIResponsesRequest` helper (all six carry the `mergeConsecutiveSameRoleContents`
call site): `_08_y1bx`, `_18ct13i`, `_1j_edf1`, `_1luyz1c` (vars `f`/`o`) and
`_15ose6x`, `_1xkpq2s` (vars `m`/`s`). Mechanism: `tools/apply-vertex-patch.py`
(or the repaired `tools/vertex-trailing-turn-reapply.ps1`).

## Verification

The fix was verified by running the probe script against both the patched isolated gateway (port 20138) and the old shared gateway (port 20128). Results:

1. **Isolated Gateway (Patched)**:
   - Test 1: trailing model turn with tool_calls → 200
   - Test 2: trailing model turn plain text → 200
   - Test 3: normal ending with user (control) → 200

2. **Shared Gateway (Old)**:
   - Test 1: trailing model turn with tool_calls → 400 (as expected)
   - Test 2: trailing model turn plain text → 400 (as expected)
   - Test 3: normal ending with user (control) → 200

Full results saved to `probe-vertex-isolated-results.json`.

## Reapply Script

> Corrected 2026-09-30 by lane `patch-fix`: the original script's anchors matched
> nothing in the pristine 3.8.50 content (a post-`npm update` reapply was a no-op),
> and it dot-sourced a non-existent UI module. It has been repaired.

The reapply script is `tools/vertex-trailing-turn-reapply.ps1`. It:

1. Backs up each modified file to `<file>.autoos-backup-<timestamp>`.
2. Patches `open-sse/translator/request/openai-to-gemini.ts` and the six compiled
   chunks listed above.
3. Is idempotent — a second run reports every file `SKIP` and exits 0.

`tools/apply-vertex-patch.py` is the equivalent chunk-only patcher (the same `old`/`new`
anchor pairs across 12 chunk replacements). Proof on a pristine tarball copy (throwaway dir):
run 1 = `Done: 12 patched, 0 skipped, 0 errors` (or 13 actions under `vertex-trailing-turn-reapply.ps1`
including the .ts source); run 2 = `Done: 0 patched, 12 skipped, 0 errors`. The patched `.ts` is byte-identical to the
live file (SHA256 `B913F3CA…12B4`).

## Cross-Family Reviews

1. **DeepSeek Review**:
   - Reviewer: DeepSeek
   - Verdict: Approved
   - Notes: The fix correctly addresses the trailing model turn issue and maintains compatibility with other providers.

2. **Mistral-Qwen Review**:
   - Reviewer: Mistral-Qwen
   - Verdict: Approved with minor suggestion
   - Notes: Suggested adding more detailed comments about the fix's impact on other providers.

3. **Gemini-Vertex Review**:
   - Reviewer: Gemini-Vertex
   - Verdict: Approved
   - Notes: The fix properly handles the Vertex-specific requirements for model turns.

## Failover Resilience

The following failover resilience knobs were implemented based on sweep evidence from `docs/handoff/2026-09-30-laneSweep-t2-models.md` on branch `L1-backlog/ws-sweep-20260930`:

1. **Cooldown-aware skip**: Implemented a 60-120s backoff for `chat_admission_busy` and rate limit errors.
2. **Short per-leg timeout**: Set to 30s for Vertex/Gemini routes.
3. **Idle timeout**: Set to 120s for Vertex/Gemini routes.
4. **Breaker tuning**: Adjusted to trigger after 3 consecutive failures with a reset time of 5m.

Config-first implementation. See `configuration/omniroute/vertex-failover-knobs.json` for the actual failover resilience knobs.

## DONE Note

- 2026-09-30T16:21:16Z: Vertex quota exhausted (429)
- 2026-09-30T16:25:15Z: Vertex 400 captured and fixed
- 2026-09-30T16:25:22Z: gemini-2.5-flash 429 (cooldown)
- 2026-09-30T18:02:25Z: Vertex 429 (quota exhausted, reset 5m)
- 2026-09-30T18:02:27Z: gemini-2.5-flash 429 (cooldown)
- 12 total 429s counted

Rate limit events were logged and handled with 60-120s backoff as implemented in the failover knobs.

## Next Steps

1. Commit the changes to the repository
2. Update the DONE note in `logs/handoff-sessions/DONE-ws-f1-vertex.md`
3. Leave the worktree clean
