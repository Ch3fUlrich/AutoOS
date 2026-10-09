#Requires -Version 5.1
<#
.SYNOPSIS
  Apply configuration/omniroute/capability-overrides.json to a running local OmniRoute gateway.

.DESCRIPTION
  Companion to context-overrides.json / apply.ps1. Both write the same management route
  PATCH /api/model-capability-overrides; this script only carries the OUTPUT-token rows
  (key = "max_output_tokens") so the clamp fix can be (re)applied without running the
  whole apply. Idempotent: a row already at the wanted value is reported, not rewritten.

  It is intentionally standalone (a new file) rather than folded into apply.ps1/apply.sh,
  so it never conflicts with work another lane is doing in those shared scripts.

.PARAMETER Gateway
  Base URL of the local gateway. Default http://127.0.0.1:20128.

.PARAMETER DryRun
  Print what would be applied and exit without writing.

.PARAMETER Go
  Fleet rule D-825: a judge GO naming this change — a judge run id
  (YYYYMMDD-HHMMSS-...), a decision id (D-<n>) or an OS-<n> item. Required for
  any run that is not -DryRun; the live PATCH refuses without it.

.PARAMETER GoSha
  'git rev-parse HEAD' of this checkout. Must equal the checkout's HEAD so the
  GO names the exact code being applied. Required with -Go on a live run.
#>
[CmdletBinding()]
param(
    [string]$Gateway = 'http://127.0.0.1:20128',
    [switch]$DryRun,
    [string]$Go = '',
    [string]$GoSha = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent (Split-Path -Parent $Here)
$OverridesFile = Join-Path $Here 'capability-overrides.json'

# --- Fleet rule D-825: a live gateway change runs only on an explicit GO ---
# This script PATCHes /api/model-capability-overrides on the live gateway (the
# standalone twin of apply.sh's override step), so a run that is not -DryRun
# refuses without -Go <ref> and -GoSha <sha> equal to this checkout's git HEAD.
# -DryRun needs none and is unchanged; a -Go there is echoed only.
if (-not $DryRun) {
    if (-not $Go) {
        $ErrorActionPreference = 'Continue'
        [Console]::Error.WriteLine('apply-capability-overrides.ps1: refusing to change live gateway state without -Go <ref> (fleet rule D-825).')
        [Console]::Error.WriteLine("  Pass -Go <ref> -GoSha <sha> (a judge run id YYYYMMDD-HHMMSS-..., D-<n> or OS-<n>, and 'git rev-parse HEAD' of this checkout); use -DryRun to inspect.")
        exit 2
    }
    if ($Go -notmatch '^(?:\d{8}-\d{6}-\S+|D-\d+|OS-\d+)$') {
        $ErrorActionPreference = 'Continue'
        [Console]::Error.WriteLine("apply-capability-overrides.ps1: -Go '$Go' is not a judge run id (YYYYMMDD-HHMMSS-...), a decision id (D-<n>) or an OS-<n> item.")
        exit 2
    }
    if (-not $GoSha) {
        $ErrorActionPreference = 'Continue'
        [Console]::Error.WriteLine('apply-capability-overrides.ps1: refusing to change live gateway state without -GoSha <sha> (fleet rule D-825).')
        exit 2
    }
    $CheckoutHead = ( (& git -C $Root rev-parse HEAD 2>$null) | Select-Object -First 1 )
    if ($CheckoutHead) { $CheckoutHead = "$CheckoutHead".Trim() }
    $ErrorActionPreference = 'Continue'
    if (-not $CheckoutHead) {
        [Console]::Error.WriteLine("apply-capability-overrides.ps1: cannot read this checkout's HEAD (git rev-parse failed in $Root) - refusing to mutate.")
        exit 2
    }
    if ($GoSha -ne $CheckoutHead) {
        [Console]::Error.WriteLine("apply-capability-overrides.ps1: -GoSha '$GoSha' is not this checkout's HEAD '$CheckoutHead'.")
        exit 2
    }
    $ErrorActionPreference = 'Stop'
    Write-Host "GO: $Go sha=$CheckoutHead"
} elseif ($Go) {
    $goShaShown = if ($GoSha) { $GoSha } else { 'none' }
    Write-Host "GO: $Go sha=$goShaShown (read-only run - no gate applies)"
}

# The management API accepts the machine loopback token the local CLI sends
# (omniroute docs/security/CLI_TOKEN.md): HMAC-SHA256 of the machine id, keyed by that
# id, over the salt "omniroute-cli-auth-v1", sent as the x-omniroute-cli-token header.
# Same derivation as configuration/omniroute/apply.ps1 Get-AutoOSManageToken.
function Get-AutoOSManageToken {
    try {
        $guid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -ErrorAction Stop).MachineGuid
        if (-not $guid) { return '' }
        $hmac = New-Object System.Security.Cryptography.HMACSHA256
        $hmac.Key = [Text.Encoding]::UTF8.GetBytes([string]$guid)
        $digest = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes('omniroute-cli-auth-v1'))
        return (-join ($digest | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}

function Invoke-AutoOSGateway {
    param([string]$Method, [string]$Path, $Body = $null)
    $script:GatewayError = ''
    if ([string]::IsNullOrEmpty($script:ManageToken)) {
        $script:GatewayError = 'no machine token available'
        return $null
    }
    try {
        $call = @{ Uri = "$Gateway$Path"; Method = $Method; TimeoutSec = 15
                   Headers = @{ 'x-omniroute-cli-token' = $script:ManageToken } }
        if ($null -ne $Body) {
            $call.Body = ($Body | ConvertTo-Json -Depth 6 -Compress)
            $call.ContentType = 'application/json'
        }
        return Invoke-RestMethod @call
    } catch {
        $script:GatewayError = $_.Exception.Message
        return $null
    }
}

$script:ManageToken = Get-AutoOSManageToken

if (-not (Test-Path $OverridesFile)) {
    Write-Host "  = no capability-overrides.json - nothing to do"
    exit 0
}

$entries = @()
try {
    $doc = Get-Content $OverridesFile -Raw -Encoding utf8 | ConvertFrom-Json
    $entries = @($doc.overrides)
} catch {
    Write-Host "  ! $OverridesFile is unreadable - nothing applied"
    exit 1
}

if ($entries.Count -eq 0) {
    Write-Host '  = capability-overrides.json lists none'
    exit 0
}

foreach ($o in $entries) {
    if (-not $o.target -or -not $o.key -or -not $o.value) { continue }
    if ($DryRun) {
        Write-Host "  - would ensure $($o.target) $($o.key) = $($o.value)"
        continue
    }
    if (-not $script:ManageToken) {
        Write-Host "  ! $($o.target): no machine token for the management API - set it in the dashboard (Model Overrides)"
        continue
    }
    $current = $null
    $list = Invoke-AutoOSGateway -Method GET -Path '/api/model-capability-overrides'
    if ($null -eq $list -and $script:GatewayError) {
        Write-Host "  ! $($o.target): the gateway did not answer ($($script:GatewayError))"
        continue
    }
    foreach ($row in @($list.overrides)) {
        if ($row.target -eq $o.target -and $row.key -eq $o.key) { $current = $row.value }
    }
    if ($current -eq [int64]$o.value) {
        Write-Host "  = $($o.target) $($o.key) already $($o.value)"
        continue
    }
    $null = Invoke-AutoOSGateway -Method PATCH -Path '/api/model-capability-overrides' -Body @{
        target = $o.target; key = $o.key; value = [int64]$o.value
    }
    if ($script:GatewayError) {
        Write-Host "  ! $($o.target) override failed ($($script:GatewayError)) - set it in the dashboard"
    } else {
        Write-Host "  + $($o.target) $($o.key) -> $($o.value)"
    }
}

Write-Host 'Done. Overrides are read live by the gateway; no restart needed for this file.'
