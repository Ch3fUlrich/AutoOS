#Requires -Version 5.1
<#
.SYNOPSIS
  Idempotent reapply of the DeepSeek reasoning-content 400 fix.
.DESCRIPTION
  Patches the OmniRoute gateway (npm package `omniroute`) so that when
  DeepSeek / Xiaomi-MiMo thinking-mode models are used through the
  Responses API and the reasoning replay cache misses, a placeholder
  reasoning item is injected before function_call items instead of
  omitting the reasoning entirely.  Without this, DeepSeek returns:
    400: The `reasoning_text` in the thinking mode must be passed back to the API.

  Two layers are patched:
    1. Compiled .js chunk files in dist/.build/next/server/chunks/
       (these are what the running gateway actually executes).
    2. .ts source files in open-sse/ (for source-tree consistency and
       future maintainers; not loaded at runtime by the compiled worker).

  Six compiled chunk copies define the affected module: four carry the
  pattern-A anchor (push target `g`) and two the pattern-B anchor (push
  target `d`).  `_18ct13i._.js` was added to pattern A on 2026-09-30 after
  docs/handoff/2026-09-30-lanePatchFix.md §4 showed it still carries the
  unmigrated `$findA` anchor live and in the published tarball.

  Each file is backed up to <file>.autoos-backup-<timestamp> before the
  first modification.  The script is idempotent: a second run reports
  "already patched" and exits 0.

  `npm update omniroute` reverts all patches (the package directory is
  replaced wholesale).  Re-run this script afterwards.
.PARAMETER PackageDir
  Root of the omniroute npm package.  Defaults to the global install.
  Override when patching a local/dev install.
.EXAMPLE
  pwsh -File reason-fix-reapply.ps1
.EXAMPLE
  pwsh -File reason-fix-reapply.ps1 -PackageDir C:\omniroute-dev\node_modules\omniroute
.NOTES
  AutoOS patch 2026-09-30  |  omniroute v3.8.50  |  branch L1-backlog/ws-fixes-20260930
#>
[CmdletBinding()]
param(
  [string]$PackageDir = (
    Join-Path (npm root -g 2>$null) "omniroute"
  )
)

$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyyMMddHHmmss'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

if (-not (Test-Path (Join-Path $PackageDir 'package.json'))) {
  Write-Host "ERROR: omniroute package not found at: $PackageDir"
  Write-Host "Pass -PackageDir <path> to override."
  exit 1
}

$results = [System.Collections.Generic.List[string]]::new()

# ── helpers ────────────────────────────────────────────────────────

function Backup-File($path) {
  $bak = "$path.autoos-backup-$ts"
  Copy-Item $path $bak -Force
  return $bak
}

function Try-Replace($path, $find, $replace, $label) {
  $content = [System.IO.File]::ReadAllText($path)
  if ($content.IndexOf($find) -lt 0) {
    if ($content.IndexOf($replace) -ge 0) {
      $results.Add("SKIP  $label (already patched)")
      return $true
    }
    $results.Add("ERROR $label (find string not found — version changed?)")
    return $false
  }
  $bak = Backup-File $path
  $patched = $content.Replace($find, $replace)
  [System.IO.File]::WriteAllText($path, $patched, $utf8NoBom)
  $results.Add("PATCH $label (backup: $(Split-Path $bak -Leaf))")
  return $true
}

# Normalized variant for .ts files that may have CRLF line endings.
# Normalizes content, find, and replace to LF before matching so that
# multi-line find strings work regardless of the file's line ending.
# The optional $marker parameter is for insertion-type patches where the
# find string is a substring of the replacement (e.g. inserting text
# before a line that remains in the output). For those patches, the marker
# (a unique string from the replacement) is checked FIRST — if present,
# the patch is already applied. Without this, the find string always
# matches and the patch is re-applied, creating duplicates.
function Try-Replace-Normalized($path, $find, $replace, $label, $marker = "") {
  $content = [System.IO.File]::ReadAllText($path) -replace "`r`n", "`n"
  $findN = $find -replace "`r`n", "`n"
  $replaceN = $replace -replace "`r`n", "`n"
  if ($marker) {
    $markerN = $marker -replace "`r`n", "`n"
    if ($content.IndexOf($markerN) -ge 0) {
      $results.Add("SKIP  $label (already patched)")
      return $true
    }
  }
  if ($content.IndexOf($findN) -lt 0) {
    if ($content.IndexOf($replaceN) -ge 0) {
      $results.Add("SKIP  $label (already patched)")
      return $true
    }
    $results.Add("ERROR $label (find string not found — version changed?)")
    return $false
  }
  $bak = Backup-File $path
  $patched = $content.Replace($findN, $replaceN)
  [System.IO.File]::WriteAllText($path, $patched, $utf8NoBom)
  $results.Add("PATCH $label (backup: $(Split-Path $bak -Leaf))")
  return $true
}

# ── 1. Compiled .js chunk patches (runtime fix) ──────────────────────

$chunksDir = Join-Path $PackageDir 'dist/.build/next/server/chunks'

# Pattern A: push target g, credentialRecord A, model p.model
$findA = 'e&&!(0,n.isInternalReasoningPlaceholder)(e)&&g.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]})'
$replaceA = 'var _ip=(0,n.isInternalReasoningPlaceholder)(e);if(e&&!_ip)g.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]});else if((_ip||!e)&&function(){var _p=String(A&&A._provider||"").trim().toLowerCase(),_m=String(p.model||"").trim().toLowerCase();return"deepseek"===_p||/(^|\/)deepseek/i.test(_m)||"xiaomi-mimo"===_p||/(^|\/)mimo/i.test(_m)}()&&Array.isArray(t.tool_calls)&&t.tool_calls.length>0)g.push({type:"reasoning",content:[{type:"reasoning_text",text:"(prior reasoning summary unavailable)"}],summary:[]})'

# Pattern B: push target d, credentialRecord p, model g.model
$findB = 'e&&!(0,n.isInternalReasoningPlaceholder)(e)&&d.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]})'
$replaceB = 'var _ip=(0,n.isInternalReasoningPlaceholder)(e);if(e&&!_ip)d.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]});else if((_ip||!e)&&function(){var _p=String(p&&p._provider||"").trim().toLowerCase(),_m=String(g.model||"").trim().toLowerCase();return"deepseek"===_p||/(^|\/)deepseek/i.test(_m)||"xiaomi-mimo"===_p||/(^|\/)mimo/i.test(_m)}()&&Array.isArray(t.tool_calls)&&t.tool_calls.length>0)d.push({type:"reasoning",content:[{type:"reasoning_text",text:"(prior reasoning summary unavailable)"}],summary:[]})'

$patternAFiles = @('_08_y1bx._.js', '_18ct13i._.js', '_1j_edf1._.js', '_1luyz1c._.js')
$patternBFiles = @('_15ose6x._.js', '_1xkpq2s._.js')

Write-Host "`n=== Compiled .js chunk patches (runtime fix) ==="
foreach ($f in $patternAFiles) {
  $p = Join-Path $chunksDir $f
  if (Test-Path $p) { Try-Replace $p $findA $replaceA "chunk-A $f" | Out-Null }
  else { $results.Add("ERROR chunk-A $f (file not found)") }
}
foreach ($f in $patternBFiles) {
  $p = Join-Path $chunksDir $f
  if (Test-Path $p) { Try-Replace $p $findB $replaceB "chunk-B $f" | Out-Null }
  else { $results.Add("ERROR chunk-B $f (file not found)") }
}

# ── 2. .ts source patches (source-tree consistency) ──────────────────

$tsIndex = Join-Path $PackageDir 'open-sse/translator/index.ts'
$tsToResp = Join-Path $PackageDir 'open-sse/translator/request/openai-responses/toResponses.ts'

Write-Host "`n=== .ts source patches (source consistency) ==="

# 2a. translator/index.ts — add deepseek to requiresReasoningContentPresence
#     Each patch is idempotent: Try-Replace-Normalized checks for the find
#     string, and if absent, checks for the replace string (already patched).
if (Test-Path $tsIndex) {
  $findIdx = 'return normalizedProvider === "xiaomi-mimo" || /(^|\/)mimo/i.test(normalizedModel);'
  $replaceIdx = @'
return (
    normalizedProvider === "xiaomi-mimo" ||
    /(^|\/)mimo/i.test(normalizedModel) ||
    normalizedProvider === "deepseek" ||
    /(^|\/)deepseek/i.test(normalizedModel)
  );
'@
  Try-Replace-Normalized $tsIndex $findIdx $replaceIdx "index.ts requiresReasoningContentPresence" | Out-Null

  # Add patch marker comment (separate idempotent step)
  $c = [System.IO.File]::ReadAllText($tsIndex) -replace "`r`n", "`n"
  $findComment = ' * isInternalReasoningPlaceholder(), so it never re-poisons cache or history.'
  $replaceComment = @'
 * isInternalReasoningPlaceholder(), so it never re-poisons cache or history.
 * AutoOS patch 2026-09-30: added deepseek (see configuration/omniroute/reason-fix-README.md).
'@
  if ($c.IndexOf($replaceComment) -ge 0) {
    $results.Add("SKIP  index.ts patch marker (already present)")
  } elseif ($c.IndexOf($findComment) -ge 0) {
    $bak = Backup-File $tsIndex
    $c = $c.Replace($findComment, $replaceComment)
    [System.IO.File]::WriteAllText($tsIndex, $c, $utf8NoBom)
    $results.Add("PATCH index.ts patch marker (backup: $(Split-Path $bak -Leaf))")
  } else {
    $results.Add("SKIP  index.ts patch marker (anchor not found — non-fatal)")
  }
}
else { $results.Add("ERROR index.ts (file not found)") }

# 2b. toResponses.ts — import, helper, reasoningIsPlaceholder var, else-if branch
#     Four patches, each idempotent via Try-Replace-Normalized. No top-level
#     marker check — each patch checks its own find/replace independently.
if (Test-Path $tsToResp) {
  # Patch 1: import — add NON_ANTHROPIC_THINKING_PLACEHOLDER to import
  $find1 = 'import { isInternalReasoningPlaceholder } from "../../../utils/reasoningPlaceholder.ts";'
  $replace1 = 'import { isInternalReasoningPlaceholder, NON_ANTHROPIC_THINKING_PLACEHOLDER } from "../../../utils/reasoningPlaceholder.ts";'
  Try-Replace-Normalized $tsToResp $find1 $replace1 "toResponses.ts import" | Out-Null

  # Patch 2: helper function — insert before openaiToOpenAIResponsesRequest
  $find2 = 'export function openaiToOpenAIResponsesRequest('
  $helperFn = @'

// DeepSeek and other strict Responses-API upstreams reject function_call
// input items that are not preceded by a reasoning item in thinking mode
// (400: "The reasoning_text in the thinking mode must be passed back to the
// API"). Mirrors translator/index.ts:requiresReasoningContentPresence but kept
// local to avoid a cross-module import. (AutoOS patch 2026-09-30)
function responsesProviderRequiresReasoningPresence(provider: unknown, model: unknown): boolean {
  const normalizedProvider = String(provider ?? "").trim().toLowerCase();
  const normalizedModel = String(model ?? "").trim().toLowerCase();
  return (
    normalizedProvider === "xiaomi-mimo" ||
    /(^|\/)mimo/i.test(normalizedModel) ||
    normalizedProvider === "deepseek" ||
    /(^|\/)deepseek/i.test(normalizedModel)
  );
}

'@
  $replace2 = $helperFn + $find2
  Try-Replace-Normalized $tsToResp $find2 $replace2 "toResponses.ts helper function" "function responsesProviderRequiresReasoningPresence" | Out-Null

  # Patch 3: reasoningIsPlaceholder variable
  $find3 = 'const reasoning = getReadableReasoningValue(msg).trim();' + "`n" + '      if (reasoning && !isInternalReasoningPlaceholder(reasoning)) {'
  $replace3 = @'
const reasoning = getReadableReasoningValue(msg).trim();
      const reasoningIsPlaceholder = isInternalReasoningPlaceholder(reasoning);
      if (reasoning && !reasoningIsPlaceholder) {
'@
  Try-Replace-Normalized $tsToResp $find3 $replace3 "toResponses.ts reasoningIsPlaceholder var" | Out-Null

  # Patch 4: else-if branch injection
  $find4 = @'
        });
      }

      // Thinking blocks remain display-only here. They do not prove that the
'@
  $replace4 = @'
        });
      } else if (
        // DeepSeek (and Xiaomi MiMo) reject function_call items with no
        // preceding reasoning item in thinking mode. On a reasoning-cache
        // miss the replay layer injects a placeholder into reasoning_content,
        // but the check above skips it as internal. Re-inject it here so the
        // Responses API input carries a reasoning item before function_call.
        // (AutoOS patch 2026-09-30: see configuration/omniroute/reason-fix-README.md)
        (reasoningIsPlaceholder || !reasoning) &&
        responsesProviderRequiresReasoningPresence(credentialRecord._provider, model) &&
        Array.isArray(msg.tool_calls) &&
        msg.tool_calls.length > 0
      ) {
        input.push({
          type: "reasoning",
          content: [{ type: "reasoning_text", text: NON_ANTHROPIC_THINKING_PLACEHOLDER }],
          summary: [],
        });
      }

      // Thinking blocks remain display-only here. They do not prove that the
'@
  Try-Replace-Normalized $tsToResp $find4 $replace4 "toResponses.ts else-if branch" "responsesProviderRequiresReasoningPresence(credentialRecord._provider, model)" | Out-Null
}
else { $results.Add("ERROR toResponses.ts (file not found)") }

# ── 3. Report ───────────────────────────────────────────────────────

Write-Host "`n=== Results ==="
$patched = 0; $skipped = 0; $errors = 0
foreach ($r in $results) {
  Write-Host "  $r"
  if ($r.StartsWith('PATCH')) { $patched++ }
  elseif ($r.StartsWith('SKIP')) { $skipped++ }
  elseif ($r.StartsWith('ERROR')) { $errors++ }
}
Write-Host "`n  $patched patched, $skipped skipped, $errors errors"
if ($errors -gt 0) { exit 1 } else { exit 0 }
