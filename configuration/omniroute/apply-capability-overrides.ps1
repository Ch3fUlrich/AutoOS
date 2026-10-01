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
#>
[CmdletBinding()]
param(
    [string]$Gateway = 'http://127.0.0.1:20128',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$OverridesFile = Join-Path $Here 'capability-overrides.json'

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
