# Vertex/Gemini Trailing Model Turn Strip Fix

## Description

This fix addresses the "Requests ending with a model turn are not supported" 400 error
from Vertex AI when using the OmniRoute gateway with Gemini models. The issue occurs when
a conversation ends with an assistant turn that contains tool_calls.

## Root Cause

The `stripTrailingAssistantForProvider` function in `contextManager.ts` only strips
trailing assistant messages WITHOUT tool_calls. When a trailing assistant message has
tool_calls, it's left in. The standard Vertex executor path lacks this strip, while
the Antigravity executor handles it correctly.

## Fix

The fix adds a trailing model turn strip in `openai-to-gemini.ts`'s `openaiToGeminiBase`
function, after the `mergeConsecutiveSameRoleContents` call (line 569). The strip guards
against emptying the contents array.

## Reapply Script

After running `npm update`, the patch must be reapplied using the script at:

```powershell
.\tools\vertex-trailing-turn-reapply.ps1
```

(Path corrected 2026-09-30 by lane `patch-fix`; the script previously lived at the
mistyped `configuration/omniroute/` path and its anchors matched nothing, so a
reapply was a silent no-op.)

The script patches the `.ts` source plus the six compiled chunks that define the
`openaiToOpenAIResponsesRequest` helper — `_08_y1bx`, `_18ct13i`, `_1j_edf1`,
`_1luyz1c`, `_15ose6x`, `_1xkpq2s` — and is idempotent (a second run reports
`SKIP` for every file). `tools/apply-vertex-patch.py` is the equivalent chunk-only
patcher.

## Impact

This fix is required for all Vertex/Gemini routes in OmniRoute. It does not affect
other providers or models.

## Compatibility

- OmniRoute v3.8.50
- Node.js 20.x or later
- Windows 10/11 with Windows PowerShell 5.1 or PowerShell 7.x

## Notes

The patch is applied to the global OmniRoute package at:

```powershell
%APPDATA%\npm\node_modules\omniroute
```

The script creates backups of modified files with a `.autoos-backup-<timestamp>` suffix (preserving pristine originals without intra-run overwrites).
