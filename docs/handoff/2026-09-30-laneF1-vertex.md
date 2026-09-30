# Vertex/Gemini Trailing Model Turn Fix - Lane F1 - 2026-09-30

## Overview

This document describes the fix for the "Requests ending with a model turn are not supported" 400 error in OmniRoute's Vertex/Gemini executor. The fix involves adding a trailing model turn strip to the `openai-to-gemini.ts` file and updating the compiled chunks.

## Root Cause

The issue occurs because the `stripTrailingAssistantForProvider` function in `contextManager.ts` only strips trailing assistant messages WITHOUT tool_calls. When a trailing assistant message has tool_calls, it's left in. The standard Vertex executor path lacks this strip, while the Antigravity executor handles it correctly.

## Fix Implementation

The fix adds a trailing model turn strip in `openai-to-gemini.ts`'s `openaiToGeminiBase` function, after the `mergeConsecutiveSameRoleContents` call (line 569). The strip guards against emptying the contents array.

### Files Modified

1. `open-sse/translator/request/openai-to-gemini.ts`
   - Added trailing model turn strip after `mergeConsecutiveSameRoleContents` call
2. `dist/.build/next/server/chunks/_0o8_5h8._.js`
   - Updated with the same trailing model turn strip
3. `dist/.build/next/server/chunks/_0t1t5fj._.js`
   - Updated with the same trailing model turn strip
4. `dist/.build/next/server/chunks/_14jycqh._.js`
   - Updated with the same trailing model turn strip
5. `dist/.build/next/server/chunks/_18ct13i._.js`
   - Updated with the same trailing model turn strip

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

A reapply script is provided at `tools/vertex-trailing-turn-reapply.ps1` to reapply the patch after running `npm update`. The script:

1. Creates backups of modified files
2. Updates the source file and compiled chunks
3. Is idempotent and can be run multiple times safely

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
