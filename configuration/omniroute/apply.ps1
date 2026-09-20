<#
.SYNOPSIS
  Apply the AutoOS router configuration to OmniRoute (Windows).

.DESCRIPTION
  1. Registers every provider key found in configuration/api-keys.yml.
  2. (Re)creates the tier combos from configuration/omniroute/combos.json.

  Safe to re-run: providers are add-or-update, combos are replaced in place.
  Model refs the live catalog does not know are skipped with a warning, so a
  renamed upstream model degrades one tier leg instead of breaking the run.

.EXAMPLE
  .\configuration\omniroute\apply.ps1
.EXAMPLE
  .\configuration\omniroute\apply.ps1 -DryRun
#>
[CmdletBinding()]
param([switch]$DryRun)

Set-StrictMode -Version Latest
# 'Continue', not 'Stop': Windows PowerShell 5.1 promotes native-command
# stderr to a terminating error under Stop, and the omniroute CLI writes a
# harmless .env warning to stderr on every invocation. Outcomes are checked
# explicitly via $LASTEXITCODE / try-catch instead.
$ErrorActionPreference = 'Continue'

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent (Split-Path -Parent $Here)
$Gateway   = 'http://127.0.0.1:20128'
$KeysFile  = Join-Path $Root 'configuration\api-keys.yml'
$CombosFile = Join-Path $Here 'combos.json'

# â”€â”€â”€ Parse the flat key: value map (no YAML dependency) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$Keys = @{}
if (Test-Path $KeysFile) {
    foreach ($line in (Get-Content $KeysFile -Encoding utf8)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains(':')) { continue }
        $key = ($t -split ':', 2)[0].Trim().ToLowerInvariant()
        $val = ($t -split ':', 2)[1].Trim().Trim('"').Trim("'")
        if ($key -and $val) { $Keys[$key] = $val }
    }
}

if (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    Write-Host 'OmniRoute CLI not installed. Run: .\setup.ps1 -Only omniroute -Yes'
    exit 1
}

# â”€â”€â”€ Gateway up? â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function Test-Gateway {
    try { (Invoke-WebRequest -Uri "$Gateway/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}
if (-not (Test-Gateway)) {
    Write-Host 'Starting OmniRoute (background)...'
    Start-Process -FilePath 'omniroute' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
    $tries = 0
    while ((-not (Test-Gateway)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
    if (-not (Test-Gateway)) { Write-Host 'Gateway did not start - run: omniroute doctor'; exit 1 }
}
Write-Host "Gateway OK on $Gateway"

# key name in api-keys.yml (lower-case) -> OmniRoute provider id
$ProviderMap = [ordered]@{
    'groq'                 = 'groq'
    'google_ai_studio'     = 'gemini'
    'mistral'              = 'mistral'
    'cloudflare_workers_ai' = 'cloudflare-ai'
    'cohere'               = 'cohere'
    'hugging_face'         = 'huggingface'
    'cerebras'             = 'cerebras'
    'sambanova'            = 'sambanova'
    'deepseek'             = 'deepseek'
    'meta'                 = 'muse-code'
    'openrouter'           = 'openrouter'
    'zen'                  = 'opencode-zen'
}

# Provider-specific connection data. groq and cerebras sit behind Cloudflare,
# which answers error 1010 to Node's default User-Agent; a plain client UA is
# accepted. Keyed by OmniRoute provider id. The backslash-escaped quotes are
# the Windows PowerShell 5.1 argument-marshalling workaround - without them
# the inner double quotes are stripped before node sees the JSON.
$ProviderData = @{
    'groq'     = '{\"customUserAgent\":\"curl/8.7.1\"}'
    'cerebras' = '{\"customUserAgent\":\"curl/8.7.1\"}'
}

Write-Host 'Providers:'
foreach ($keyName in $ProviderMap.Keys) {
    $providerId = $ProviderMap[$keyName]
    if (-not $Keys.ContainsKey($keyName)) {
        Write-Host "  - $providerId : no key in api-keys.yml, skipped"
        continue
    }
    if ($DryRun) {
        Write-Host "  - $providerId : would register (key from $keyName)"
        continue
    }
    $varName = 'AUTOOS_KEY_' + $keyName.ToUpperInvariant()
    Set-Item -Path "Env:$varName" -Value $Keys[$keyName]
    $addArgs = @('providers', 'add', $providerId, '--credential-env', $varName)
    if ($ProviderData.ContainsKey($providerId)) {
        $addArgs += @('--provider-specific-data', $ProviderData[$providerId])
    }
    $addArgs += '--yes'
    & omniroute @addArgs *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $providerId registered" }
    else { Write-Host "  ! $providerId registration failed - register it in the dashboard" }
    Remove-Item -Path "Env:$varName" -ErrorAction SilentlyContinue
}

# â”€â”€â”€ Live catalog for validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$LiveIds = @()
if ($Keys.ContainsKey('omniroute')) {
    try {
        $resp = Invoke-RestMethod -Uri "$Gateway/v1/models" -TimeoutSec 15 `
            -Headers @{ Authorization = "Bearer $($Keys['omniroute'])" }
        $LiveIds = @($resp.data | ForEach-Object { $_.id })
    } catch { $LiveIds = @() }
}
if ($LiveIds.Count -eq 0) {
    Write-Host '  ! could not read /v1/models (check the omniroute client key in api-keys.yml)'
    Write-Host '    combos will be created without catalog validation - verify with:'
    Write-Host '    omniroute simulate --combo tier1'
}

# â”€â”€â”€ (Re)create combos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Write-Host 'Combos:'
$combos = (Get-Content $CombosFile -Raw -Encoding utf8 | ConvertFrom-Json).combos
foreach ($combo in $combos) {
    $keep = @()
    $dropped = @()
    foreach ($ref in $combo.models) {
        if ($LiveIds.Count -eq 0 -or $LiveIds -contains $ref) { $keep += $ref }
        else { $dropped += $ref }
    }
    if ($dropped.Count -gt 0) {
        Write-Host "  - $($combo.name): catalog does not know $($dropped -join ', ') (skipped)"
    }
    if ($keep.Count -eq 0) {
        Write-Host "  ! $($combo.name): no usable models - not created"
        continue
    }
    if ($DryRun) {
        Write-Host "  - $($combo.name): would create [$($combo.strategy)] with $($keep -join ',')"
        continue
    }
    & omniroute combo delete $combo.name --yes *> $null
    & omniroute combo create $combo.name --strategy $combo.strategy --models ($keep -join ',') *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $($combo.name) created ($($combo.strategy))" }
    else { Write-Host "  ! $($combo.name) creation failed" }
}

Write-Host 'Done. Apps pick this up on next start (configuration\start-stack.ps1).'
