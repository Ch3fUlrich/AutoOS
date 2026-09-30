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
.\configuration\omniroute\vertex-trailing-turn-reapply.ps1
```

## Impact

This fix is required for all Vertex/Gemini routes in OmniRoute. It does not affect
other providers or models.

## Compatibility

- OmniRoute v3.8.50
- Node.js 20.x or later
- Windows 10/11 or Linux/macOS with PowerShell 7.4 or later

## Notes

The patch is applied to the global OmniRoute package at:

```powershell
C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute
```

The script creates backups of modified files with a `.autoos-backup-<timestamp>` suffix.
