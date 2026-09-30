# DeepSeek Reasoning 400 Fix — Patch Documentation

**Date:** 2026-09-30
**Patch:** `reason-fix-reapply.ps1` (this directory)
**Branch:** `L1-backlog/ws-fixes-20260930`
**Gateway:** omniroute v3.8.50

## Problem

DeepSeek thinking-mode models return a 400 error when an assistant message
with `tool_calls` is sent in the conversation history without a preceding
`reasoning_content` field:

```
400: The `reasoning_text` in the thinking mode must be passed back to the API.
```

This fires when the gateway's **reasoning replay cache misses** — e.g. when
`tool_call_id` is overwritten by the client, the cache expires (TTL 2h), or
the DB is cleared. See `reasoning-defense.md` for the full defense pipeline
analysis.

## Root Cause

The gateway converts chat-completion history to the Responses API format in
`toResponses` (compiled to `dist/.build/next/server/chunks/*.js`). When
processing an assistant message with tool calls, the code builds an `input[]`
array of items. A `reasoning` item is pushed only when the reasoning text is
non-empty and not an internal placeholder:

```js
e&&!(0,n.isInternalReasoningPlaceholder)(e)&&g.push({type:"reasoning",...})
```

When the reasoning replay cache misses, the reasoning text is either empty or
a placeholder. In both cases, **no reasoning item is pushed**. The subsequent
`function_call` items have no preceding `reasoning` item, and DeepSeek rejects
the request with the 400.

## Fix

Inject a placeholder reasoning item (`NON_ANTHROPIC_THINKING_PLACEHOLDER` =
`"(prior reasoning summary unavailable)"`) when **all** of the following are
true:

1. The reasoning is a placeholder or absent (`_ip || !e`)
2. The provider is DeepSeek or Xiaomi-MiMo (checked via `_provider` and model
   name)
3. The message has tool calls (`Array.isArray(t.tool_calls) && t.tool_calls.length > 0`)

The placeholder is recognized by `isInternalReasoningPlaceholder()` on
subsequent round trips, so the injection is idempotent — it never duplicates
or poisons the cache.

### Compiled .js chunk patches (runtime fix)

The running gateway executes compiled JavaScript from
`dist/.build/next/server/chunks/`, not the `.ts` source. Six chunk files
contain the `toResponses` reasoning push logic, in two patterns:

| Pattern | Files | Push target | Credential record | Model |
|---------|-------|-------------|-------------------|-------|
| A | `_08_y1bx._.js`, `_18ct13i._.js`, `_1j_edf1._.js`, `_1luyz1c._.js` | `g` | `A._provider` | `p.model` |
| B | `_15ose6x._.js`, `_1xkpq2s._.js` | `d` | `p._provider` | `g.model` |

`_18ct13i._.js` was added to pattern A on 2026-09-30: it defines the same
module with the same scope names (only an inner loop variable differs), so the
identical `$findA`/`$replaceA` pair applies verbatim. Before that it was the one
copy of the four that `reason-fix-reapply.ps1` left unfixed — see
`docs/handoff/2026-09-30-lanePatchIntegrity.md`.

Each file receives the same logical fix, adapted to the minified variable
names for its pattern. The replacement transforms:

```js
e&&!isInternalReasoningPlaceholder(e)&&g.push({type:"reasoning",...})
```

into:

```js
var _ip=isInternalReasoningPlaceholder(e);
if(e&&!_ip) g.push({type:"reasoning",...});          // normal: push real reasoning
else if((_ip||!e) && isDeepSeekOrMimo() && hasToolCalls)
  g.push({type:"reasoning",text:"(prior reasoning summary unavailable)"});  // fix: inject placeholder
```

### .ts source patches (source-tree consistency)

The `.ts` source files are patched for maintainability, though they are not
loaded at runtime:

- **`open-sse/translator/index.ts`**: Added `deepseek` to
  `requiresReasoningContentPresence()` (was already present in the compiled
  code — the `.ts` source was older).
- **`open-sse/translator/request/openai-responses/toResponses.ts`**: Added
  `NON_ANTHROPIC_THINKING_PLACEHOLDER` import, a local
  `responsesProviderRequiresReasoningPresence()` helper, a
  `reasoningIsPlaceholder` variable, and the `else if` placeholder injection
  branch.

## Applying the Patch

```powershell
# From the AutoOS repository root:
pwsh -File configuration/omniroute/reason-fix-reapply.ps1
```

The script:
1. Auto-detects the global omniroute package (`npm root -g`)
2. Backs up each file to `<file>.autoos-backup-<timestamp>`
3. Patches all 6 compiled `.js` chunks + 2 `.ts` source files
4. Is idempotent — a second run reports "already patched" and exits 0

Override the package directory for a local/dev install:

```powershell
pwsh -File configuration/omniroute/reason-fix-reapply.ps1 -PackageDir C:\dev\omniroute
```

## After `npm update omniroute`

`npm update` replaces the entire package directory, reverting all patches.
Re-run the reapply script. If the find strings no longer match (new version
restructured the code), the script reports errors — inspect the new code and
update the find/replace strings.

## Verification

The fix was verified with `tools/probe-reasoning-repro.py` against an
isolated gateway instance (port 20130, separate `DATA_DIR`), without
restarting the shared gateway (port 20128):

| Model | Scenario | Before | After |
|-------|----------|--------|-------|
| `deepseek-v4.1-flash` | cache miss (overwrite) | 400 | **200** |
| `deepseek-v4.1-flash` | cache hit (preserve) | 200 | 200 |
| `deepseek/deepseek-flash` | cache miss (overwrite) | 400 | **200** |
| `deepseek/deepseek-flash` | cache hit (preserve) | 200 | 200 |

## Backup Files

Created during the initial patch (2026-09-30 17:44 UTC):

| File | Backup |
|------|--------|
| `_08_y1bx._.js` | `.autoos-backup-20260930174401` |
| `_1j_edf1._.js` | `.autoos-backup-20260930174401` |
| `_1luyz1c._.js` | `.autoos-backup-20260930174401` |
| `_15ose6x._.js` | `.autoos-backup-20260930174401` |
| `_1xkpq2s._.js` | `.autoos-backup-20260930174401` |
| `translator/index.ts` | `.autoos-backup-20260930170532` |
| `toResponses.ts` | `.autoos-backup-20260930172918` |

## Files

| File | Role |
|------|------|
| `reason-fix-reapply.ps1` | Idempotent reapply script (this patch) |
| `reason-fix-README.md` | This file |
| `reasoning-defense.md` | Gateway defense pipeline analysis |
| `combos.json` | Combo definitions (don't edit — P1-combos lane) |
| `context-overrides.json` | Model context window overrides |
| `apply.ps1` / `apply.sh` | Applies combos + overrides to the gateway |
