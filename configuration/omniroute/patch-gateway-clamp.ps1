#Requires -Version 5.1
<#
.SYNOPSIS
  Apply (or re-apply) the AutoOS max_tokens output-clamp patch to a local OmniRoute install.

.DESCRIPTION
  The live HTTP gateway is the Next.js standalone build (dist/server-ws.mjs ->
  dist/.build/next/**), NOT dist/open-sse/mcp-server/server.js (that file is the MCP
  stdio bundle, used only by bin/mcp-server.mjs). Both carry a copy of the same
  open-sse source, so the patch is applied to BOTH:

    1. dist/open-sse/mcp-server/server.js (canonical, un-minified compiled form):
       - un-gate resolveReasoningBufferedMaxTokens (drop the supportsThinking check),
       - clamp the per-target body in executeModelUnit.
    2. every dist/.build/next/**/*.js that carries the gateway's minified clamp
       (exactly 4 chunk copies today): remove the same supportsThinking gate with a
       name-agnostic regex.

  Effect: a per-model output cap (capability override max_output_tokens / registry
  maxOutputTokens) now clamps max_tokens for any model, not only reasoning models. A
  model with no explicit cap is untouched (the helper still returns null), so this is a
  per-model change, not a global default.

  Idempotent (the gate regex no longer matches after the first run). Every changed file
  is backed up to <file>.autoos-backup-<timestamp>. The running gateway keeps the old
  bytes in memory: RESTART it to load the patch (see the evidence doc).

.PARAMETER OmniRouteRoot
  Install root. Defaults to the npm-global install.

.PARAMETER DryRun
  Report what would change, write nothing.
#>
[CmdletBinding()]
param(
    [string]$OmniRouteRoot = (Join-Path $env:APPDATA 'npm\node_modules\omniroute'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$McpBundle = Join-Path $OmniRouteRoot 'dist\open-sse\mcp-server\server.js'
$NextRoot  = Join-Path $OmniRouteRoot 'dist\.build\next'
$Marker    = 'AutoOS clamp: never forward max_tokens above'
$stamp     = Get-Date -Format 'yyyyMMdd-HHmmss'
$utf8      = New-Object System.Text.UTF8Encoding($false)

function Backup-And-Write {
    param([string]$Path, [string]$Text)
    if ($DryRun) { Write-Host "  - would write $Path"; return }
    Copy-Item -LiteralPath $Path -Destination "$Path.autoos-backup-$stamp" -Force
    [IO.File]::WriteAllText($Path, $Text, $utf8)
    Write-Host "  + patched $Path (backup .autoos-backup-$stamp)"
}

# --- 1. MCP bundle (canonical compiled form) --------------------------------
if (Test-Path $McpBundle) {
    $text = [IO.File]::ReadAllText($McpBundle)
    if ($text.Contains($Marker)) {
        Write-Host "  = mcp bundle already patched"
    } else {
        $oldGate = "  const capabilities = getResolvedModelCapabilities(modelStr);`n  if (capabilities.supportsThinking !== true) return null;`n  const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));"
        $newGate = "  const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));"
        $oldUnit = "  if (args.isModelAvailable) {`n    const available = await args.isModelAvailable(args.unit.modelStr, args.unit);`n    if (!available) return errorResponse(503, ``Model `${args.unit.modelStr} is unavailable``);`n  }`n  return args.handleSingleModel(args.body, args.unit.modelStr, {"
        $newUnit = "  if (args.isModelAvailable) {`n    const available = await args.isModelAvailable(args.unit.modelStr, args.unit);`n    if (!available) return errorResponse(503, ``Model `${args.unit.modelStr} is unavailable``);`n  }`n  {`n    // AutoOS clamp: never forward max_tokens above the model's explicit output cap`n    const clampedMaxTokens = resolveReasoningBufferedMaxTokens(args.unit.modelStr, args.body && args.body.max_tokens);`n    if (clampedMaxTokens !== null && args.body && typeof args.body === `"object`" && clampedMaxTokens !== args.body.max_tokens) {`n      args.body = { ...args.body, max_tokens: clampedMaxTokens };`n    }`n  }`n  return args.handleSingleModel(args.body, args.unit.modelStr, {"
        if (-not $text.Contains($oldGate) -or -not $text.Contains($oldUnit)) {
            Write-Host "  ! mcp bundle anchors not found - skipping"
        } else {
            Backup-And-Write -Path $McpBundle -Text ($text.Replace($oldGate, $newGate).Replace($oldUnit, $newUnit))
        }
    }
} else {
    Write-Host "  ! mcp bundle not found: $McpBundle"
}

# --- 2. Gateway Next build (what the live gateway loads) --------------------
# Remove the supportsThinking gate from the clamp, name-agnostic.
$gatePattern = 'let ([A-Za-z0-9_$]+)=\(0,[A-Za-z0-9_$]+\.getResolvedModelCapabilities\)\([A-Za-z0-9_$]+\);if\(!0!==\1\.supportsThinking\)return null;'
$gatewayFiles = Get-ChildItem $NextRoot -Recurse -Filter *.js -File -ErrorAction SilentlyContinue |
    Select-String -Pattern $gatePattern -List
$gatewayFiles = @($gatewayFiles)

if ($gatewayFiles.Count -eq 0) {
    Write-Host "  = gateway already patched (no clamp gate found under .build/next)"
} else {
    foreach ($hit in $gatewayFiles) {
        $path = $hit.Path
        $text = [IO.File]::ReadAllText($path)
        $new = [regex]::Replace($text, $gatePattern, '')
        if ($new -eq $text) { continue }
        Backup-And-Write -Path $path -Text $new
    }
}

# --- 3. Verify --------------------------------------------------------------
$ok = $true
if (Test-Path $McpBundle) {
    $m = [IO.File]::ReadAllText($McpBundle)
    if (-not $m.Contains($Marker)) { Write-Host "  ! verify: mcp marker missing"; $ok = $false }
}
$left = Get-ChildItem $NextRoot -Recurse -Filter *.js -File -ErrorAction SilentlyContinue |
    Select-String -Pattern $gatePattern -List
if ($left) { Write-Host "  ! verify: $($left.Count) gateway file(s) still carry the gate"; $ok = $false }

if ($ok) {
    Write-Host '  + patched OK'
    Write-Host '  ! RESTART the gateway to load the patched bundle (the running process keeps the old bytes in memory)'
} else {
    Write-Host '  ! verification failed - restore from the .autoos-backup-* files'
    exit 1
}
