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
.EXAMPLE
  .\configuration\omniroute\apply.ps1 -Probe   # one tiny request per combo
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Probe
)

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

# No api-keys.yml, no keys to apply. Say how to create it (bash prints the
# same), but stay green under -DryRun so a preview never demands secrets.
$Keys = @{}
$KeysMissing = $false
if (-not (Test-Path $KeysFile)) {
    $KeysMissing = $true
    Write-Host 'No configuration\api-keys.yml yet - copy configuration\api-keys.example.yml and fill it in.'
    Write-Host 'Continuing with combos only.'
} else {
    foreach ($line in (Get-Content $KeysFile -Encoding utf8)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains(':')) { continue }
        $key = ($t -split ':', 2)[0].Trim().ToLowerInvariant()
        $val = ($t -split ':', 2)[1].Trim().Trim('"').Trim("'")
        if ($key -and $val) { $Keys[$key] = $val }
    }
}

if (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    if ($DryRun) {
        Write-Host 'OmniRoute CLI not installed - dry run continues with the static plan (nothing started, registered or created).'
    } else {
        Write-Host 'OmniRoute CLI not installed. Run: .\setup.ps1 -Only omniroute -Yes'
        exit 1
    }
}

# --- Gateway up? --- ---
function Test-Gateway {
    try { (Invoke-WebRequest -Uri "$Gateway/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}
if (-not (Test-Gateway)) {
    if ($DryRun) {
        Write-Host 'Gateway is down; dry run continues with the static plan (would start it with: omniroute --no-open --port 20128).'
    } else {
        Write-Host 'Starting OmniRoute (background)...'
        Start-Process -FilePath 'omniroute' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
        $tries = 0
        while ((-not (Test-Gateway)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
        if (-not (Test-Gateway)) { Write-Host 'Gateway did not start - run: omniroute doctor'; exit 1 }
    }
}
if (Test-Gateway) { Write-Host "Gateway OK on $Gateway" }

# Provider registry: catalog\providers.json is the single source of truth for
# which api-keys.yml name maps to which OmniRoute provider id and for the
# provider-specific connection data. apply.sh and the two Python tools read the
# same file, so the copies these maps used to carry cannot drift. Only providers
# with an omniroute_id are registered: meta (unregistered 2026-09-23,
# openrouter-first, no combo leg) and the omniroute client key carry none and
# are skipped, exactly as before.
function Get-AutoOSProviderMap {
    param([string]$CatalogPath)
    $providers = (Get-Content $CatalogPath -Raw -Encoding utf8 | ConvertFrom-Json).providers
    $map = [ordered]@{}
    $data = @{}
    foreach ($prop in $providers.PSObject.Properties) {
        $entry = $prop.Value
        if ($null -eq $entry.omniroute_id) { continue }
        # api-keys.yml keys are lower-cased when read above, so match that.
        $keyName = $prop.Name.ToLowerInvariant()
        $map[$keyName] = $entry.omniroute_id
        if ($null -ne $entry.provider_data) {
            # Keep the plain JSON string: the 5.1-vs-7.x escaping branch below
            # needs a string, and ConvertTo-Json -Compress is stable across both.
            $data[$entry.omniroute_id] = ($entry.provider_data | ConvertTo-Json -Compress)
        }
    }
    [pscustomobject]@{ Map = $map; Data = $data }
}
$registry = Get-AutoOSProviderMap (Join-Path $Root 'catalog\providers.json')
$ProviderMap = $registry.Map
$ProviderData = $registry.Data

# Build the --provider-specific-data JSON for the running shell. PowerShell
# 5.1 strips inner double quotes when marshalling to a native exe (the same
# class of bug as the embedded-python quoting fix in run-tests.ps1), so 5.1
# needs backslash-escaped quotes while pwsh 7 passes clean JSON through.
# The $ShellMajor override exists for the suite: it pins the branch under
# test instead of asserting on the live shell.
function Get-AutoOSProviderDataJson {
    param([string]$ProviderId, [int]$ShellMajor = $PSVersionTable.PSVersion.Major)
    if (-not $ProviderData.ContainsKey($ProviderId)) { return $null }
    $plain = $ProviderData[$ProviderId]
    if ($ShellMajor -lt 6) { return $plain -replace '"', '\"' }
    return $plain
}

Write-Host 'Providers:'
# Connections that already exist are left alone: re-adding would either fail
# or duplicate them, and neither proves the pipeline works.
$existingIds = @()
try {
    $listText = & omniroute providers list 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in ($listText -split "`r?`n")) {
            if ($line -match '^\s*[0-9a-f]{6,}\s+(\S+)') { $existingIds += $Matches[1] }
        }
    }
} catch { $existingIds = @() }
foreach ($keyName in $ProviderMap.Keys) {
    $providerId = $ProviderMap[$keyName]
    if ($existingIds -contains $providerId) {
        Write-Host "  = $providerId already registered"
        continue
    }
    if (-not $Keys.ContainsKey($keyName)) {
        Write-Host "  - $providerId : no key in api-keys.yml, skipped"
        continue
    }
    if ($DryRun) {
        Write-Host "  - $providerId : would register (key from $keyName)"
        continue
    }
    $varName = 'AUTOOS_KEY_' + $keyName.ToUpperInvariant()
    # Save a pre-existing variable of the same name: apply must not clobber
    # (or delete, below) something the user's shell already had.
    $hadVar = Test-Path "Env:$varName"
    $oldVar = if ($hadVar) { (Get-Item "Env:$varName").Value } else { $null }
    Set-Item -Path "Env:$varName" -Value $Keys[$keyName]
    $addArgs = @('providers', 'add', $providerId, '--credential-env', $varName)
    $dataJson = Get-AutoOSProviderDataJson $providerId
    if ($null -ne $dataJson) {
        $addArgs += @('--provider-specific-data', $dataJson)
    }
    $addArgs += '--yes'
    & omniroute @addArgs *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $providerId registered" }
    else { Write-Host "  ! $providerId registration failed - register it in the dashboard" }
    if ($hadVar) { Set-Item -Path "Env:$varName" -Value $oldVar }
    else { Remove-Item -Path "Env:$varName" -ErrorAction SilentlyContinue }
}

# --- Live catalog for validation ---
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
    Write-Host '    omniroute simulate --combo t1-orchestrator'
}

# --- Resilience: fit a reasoning model AND fast-skip a dead leg ------------
# 1. requestQueue.maxWaitMs ships at 15000 ms, which is below Muse Spark's own
#    thinking time: every spark request died with "Request exceeded OmniRoute's
#    local rate-limit execution expiration", the chain fell through to a dead
#    free leg, and the client saw the last leg's error (measured 2026-09-22).
#    180000 ms sits under comboCooldownWait.budgetMs (300s) so a genuinely dead
#    leg still hops instead of stalling the run.
# 2. providerBreaker.apikey.failureThreshold 12 -> 2 (operator 2026-09-23):
#    the zen free promo stays FIRST (free when it works), but a 403 is a
#    permanent-class error, so at the shipped threshold the gateway retried the
#    dead promo on EVERY request. At 2 it is skipped for resetTimeoutMs (30s)
#    after two failures, then retried - at most two cheap round-trips, and
#    every other failing free leg hops fast too.
$MaxWaitMs = 180000
$Breaker = @{ failureThreshold = 2; degradationThreshold = 1; resetTimeoutMs = 30000 }
Write-Host 'Resilience:'
if ($DryRun) {
    Write-Host "  - would set requestQueue.maxWaitMs = $MaxWaitMs"
    Write-Host "  - would set providerBreaker.apikey.failureThreshold = $($Breaker.failureThreshold)"
} elseif (-not $Keys.ContainsKey('omniroute')) {
    Write-Host '  - no client key - cannot set the resilience settings (PATCH /api/resilience)'
} else {
    $auth = @{ Authorization = "Bearer $($Keys['omniroute'])" }
    $current = $null
    $currentBreaker = $null
    try {
        $cfg = Invoke-RestMethod -Uri "$Gateway/api/resilience?include=config" -TimeoutSec 15 -Headers $auth
        $current = $cfg.requestQueue.maxWaitMs
        $currentBreaker = $cfg.providerBreaker.apikey.failureThreshold
    } catch { $current = $null; $currentBreaker = $null }
    if ($current -eq $MaxWaitMs -and $currentBreaker -eq $Breaker.failureThreshold) {
        Write-Host "  = resilience settings already current (maxWaitMs=$MaxWaitMs, breaker=$($Breaker.failureThreshold))"
    } else {
        $body = @{
            requestQueue   = @{ maxWaitMs = $MaxWaitMs }
            providerBreaker = @{ apikey = $Breaker }
        } | ConvertTo-Json -Depth 5
        try {
            $null = Invoke-RestMethod -Uri "$Gateway/api/resilience" -Method Patch -TimeoutSec 15 `
                -ContentType 'application/json' -Headers $auth -Body $body
            Write-Host "  + maxWaitMs $current -> $MaxWaitMs; breaker $currentBreaker -> $($Breaker.failureThreshold)"
        } catch {
            Write-Host '  ! could not set resilience settings - use the dashboard (Resilience)'
        }
    }
}

# --- (Re)create combos --- ---
Write-Host 'Combos:'
$combos = (Get-Content $CombosFile -Raw -Encoding utf8 | ConvertFrom-Json).combos
$created = @()
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
    # Create first, delete only what it replaces: if the create fails the old
    # tier survives instead of leaving a hole.
    & omniroute combo create $combo.name --strategy $combo.strategy --models ($keep -join ',') *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  + $($combo.name) created ($($combo.strategy))"
        $created += $combo.name
    } else {
        & omniroute combo delete $combo.name --yes *> $null
        & omniroute combo create $combo.name --strategy $combo.strategy --models ($keep -join ',') *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  + $($combo.name) replaced ($($combo.strategy))"
            $created += $combo.name
        }
        else { Write-Host "  ! $($combo.name) creation failed (previous version, if any, is untouched)" }
    }
}

# --- Probe: prove the combos answer, end to end ---
if ($Probe) {
    $probeKey = if ($Keys.ContainsKey('omniroute')) { $Keys['omniroute'] } else { '' }
    if (-not $probeKey -or $DryRun) {
        Write-Host 'Probe skipped (dry run, or no omniroute client key in api-keys.yml).'
    } elseif ($created.Count -eq 0) {
        Write-Host 'Probe skipped (no combos were created).'
    } else {
        Write-Host 'Probe (one tiny request per combo):'
        foreach ($name in $created) {
            $body = @{
                model = $name
                messages = @(@{ role = 'user'; content = 'Reply with: ok' })
                # 2048, not 256: reasoning models (spark) spend tokens on hidden
                # thinking first and answer "empty" when the budget is tiny.
                max_tokens = 2048
            } | ConvertTo-Json -Depth 5
            try {
                $r = Invoke-RestMethod -Uri "$Gateway/v1/chat/completions" -Method Post `
                    -Body $body -ContentType 'application/json' -TimeoutSec 300 `
                    -Headers @{ Authorization = "Bearer $probeKey" }
                Write-Host ("  + {0,-12} served by {1}" -f $name, $r.model)
            } catch {
                $detail = $_.Exception.Message
                if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                    $detail = $_.ErrorDetails.Message.Substring(0, [Math]::Min(200, $_.ErrorDetails.Message.Length))
                }
                Write-Host ("  ! {0,-12} {1}" -f $name, $detail)
            }
        }
    }
}

Write-Host 'Done. Apps pick this up on next start (configuration\start-stack.ps1).'
