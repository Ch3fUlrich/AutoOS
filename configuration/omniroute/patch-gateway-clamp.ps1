#Requires -Version 5.1
<#
.SYNOPSIS
  Apply (or re-apply) the AutoOS max_tokens output-clamp patch to a local OmniRoute
  gateway bundle.

.DESCRIPTION
  Vendor file is outside the repo, so this is the durable reapply artifact for
  configuration/omniroute/patches/0001-clamp-max-tokens.patch. It edits
  dist/open-sse/mcp-server/server.js by exact string replacement (byte-stable: the file
  is LF, no BOM):

    1. Un-gate resolveReasoningBufferedMaxTokens so the per-model output cap
       (capability override max_output_tokens / registry maxOutputTokens) clamps ANY
       model, not only reasoning models.
    2. Call that clamp at the head of executeModelUnit so the single-model unit path
       clamps its per-target body before dispatch.

  A model with no explicit cap is untouched (the helper returns null), so this is a
  per-model change, not a global default. Idempotent: the marker line makes a second
  run a no-op. The vendor file is backed up first to server.js.autoos-backup-<ts>.

  The gateway must be restarted after this for the patched bundle to load
  (omniroute serve / the workstation start-stack). See the evidence doc.

.PARAMETER ServerJs
  Path to the bundle. Defaults to the npm-global install.

.PARAMETER DryRun
  Report what would change, write nothing.
#>
[CmdletBinding()]
param(
    [string]$ServerJs = (Join-Path $env:APPDATA 'npm\node_modules\omniroute\dist\open-sse\mcp-server\server.js'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Marker = 'AutoOS clamp: never forward max_tokens above'

if (-not (Test-Path $ServerJs)) {
    Write-Host "  ! bundle not found: $ServerJs"
    exit 1
}

$text = [IO.File]::ReadAllText($ServerJs)

if ($text.Contains($Marker)) {
    Write-Host "  = already patched (marker present): $ServerJs"
    exit 0
}

$oldGate = @'
  const capabilities = getResolvedModelCapabilities(modelStr);
  if (capabilities.supportsThinking !== true) return null;
  const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));
'@ -replace "`r`n", "`n"

$newGate = @'
  const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));
'@ -replace "`r`n", "`n"

$oldUnit = @'
  if (args.isModelAvailable) {
    const available = await args.isModelAvailable(args.unit.modelStr, args.unit);
    if (!available) return errorResponse(503, `Model ${args.unit.modelStr} is unavailable`);
  }
  return args.handleSingleModel(args.body, args.unit.modelStr, {
'@ -replace "`r`n", "`n"

$newUnit = @'
  if (args.isModelAvailable) {
    const available = await args.isModelAvailable(args.unit.modelStr, args.unit);
    if (!available) return errorResponse(503, `Model ${args.unit.modelStr} is unavailable`);
  }
  {
    // AutoOS clamp: never forward max_tokens above the model's explicit output cap
    // (capability override max_output_tokens / registry maxOutputTokens). Per-model:
    // a model with no cap still clamps to nothing.
    const clampedMaxTokens = resolveReasoningBufferedMaxTokens(args.unit.modelStr, args.body && args.body.max_tokens);
    if (clampedMaxTokens !== null && args.body && typeof args.body === "object" && clampedMaxTokens !== args.body.max_tokens) {
      args.body = { ...args.body, max_tokens: clampedMaxTokens };
    }
  }
  return args.handleSingleModel(args.body, args.unit.modelStr, {
'@ -replace "`r`n", "`n"

if (-not $text.Contains($oldGate)) {
    Write-Host "  ! un-gate anchor not found - bundle differs; refusing to patch"
    exit 1
}
if (-not $text.Contains($oldUnit)) {
    Write-Host "  ! executeModelUnit anchor not found - bundle differs; refusing to patch"
    exit 1
}

if ($DryRun) {
    Write-Host "  - would un-gate resolveReasoningBufferedMaxTokens (1 site)"
    Write-Host "  - would add the clamp call to executeModelUnit (1 site)"
    Write-Host "  - would back up $ServerJs"
    exit 0
}

$backup = "$ServerJs.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $ServerJs -Destination $backup -Force
Write-Host "  + backup: $backup"

$patched = $text.Replace($oldGate, $newGate).Replace($oldUnit, $newUnit)
[IO.File]::WriteAllText($ServerJs, $patched, (New-Object System.Text.UTF8Encoding($false)))

# Verify.
$check = [IO.File]::ReadAllText($ServerJs)
if ($check.Contains($Marker) -and -not $check.Contains('capabilities.supportsThinking !== true) return null;')) {
    Write-Host "  + patched OK: $ServerJs"
    Write-Host '  ! restart the gateway so the patched bundle loads'
} else {
    Write-Host "  ! verification failed - restoring backup"
    Copy-Item -LiteralPath $backup -Destination $ServerJs -Force
    exit 1
}
