<#
.SYNOPSIS
    Probe the AutoOS phone-reachable stack; log-only unless -Fix is passed.

.DESCRIPTION
    Checks four listeners and reports up/down:
      :20128 OmniRoute gateway (/api/health must be 200)
      :3000  OpenHands web UI (any HTTP answer counts)
      :4096  opencode serve (200 with creds, 401 without = still alive)
      :8777  AutoOS browser UI (only when you started it with setup.ps1 -Serve)

    Default mode only prints and appends to logs/healthcheck-<date>.log.
    With -Fix, a down gateway or container is resumed via
    configuration/autostart/Start-AutoOSStack.ps1. Nothing is installed,
    uninstalled, or overwritten in either mode.

    Human fallback (when a restart does not help) is printed at the end.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File configuration\healthcheck.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File configuration\healthcheck.ps1 -Fix
#>
[CmdletBinding()]
param([switch]$Fix)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("healthcheck-" + (Get-Date -Format 'yyyyMMdd') + '.log')

function Write-AutoOSHealth {
    param([string]$Text)
    $line = "$(Get-Date -Format 'HH:mm:ss') $Text"
    $line | Out-File $LogFile -Append -Encoding utf8
    Write-Host $Text
}

function Test-AutoOSPort {
    param([int]$Port, [string]$Path = '/')
    try {
        $code = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 5).StatusCode
        return $code
    } catch {
        # An auth challenge still proves the server is alive: opencode serve
        # answers 401 without credentials, which counts as up, not down.
        $resp = $_.Exception.Response
        if ($null -ne $resp) { return [int]$resp.StatusCode }
        return 0
    }
}

$gw = Test-AutoOSPort 20128 '/api/health'
$oh = Test-AutoOSPort 3000 '/'
$oc = Test-AutoOSPort 4096 '/'
$ui = Test-AutoOSPort 8777 '/'

$gwUp = ($gw -eq 200)
$ohUp = ($oh -ne 0)
$ocUp = ($oc -eq 200 -or $oc -eq 401)
$uiUp = ($ui -ne 0)

Write-AutoOSHealth "gateway :20128 -> $gw $(if ($gwUp) { 'up' } else { 'DOWN' })"
Write-AutoOSHealth "openhands :3000 -> $oh $(if ($ohUp) { 'up' } else { 'DOWN' })"
Write-AutoOSHealth "opencode :4096 -> $oc $(if ($ocUp) { 'up (401 = alive, needs credentials)' } else { 'DOWN' })"
if ($uiUp) { Write-AutoOSHealth "autoos-ui :8777 -> $ui up" }
else { Write-AutoOSHealth 'autoos-ui :8777 -> down (expected unless setup.ps1 -Serve is running)' }

if ($Fix -and ((-not $gwUp) -or (-not $ohUp))) {
    Write-AutoOSHealth '--fix: resuming via Start-AutoOSStack.ps1 ...'
    & (Join-Path $RepoRoot 'configuration\autostart\Start-AutoOSStack.ps1')
} elseif ((-not $gwUp) -or (-not $ohUp) -or (-not $ocUp)) {
    Write-AutoOSHealth 'log-only mode: nothing restarted (re-run with -Fix to resume).'
}

Write-Host ''
Write-Host 'Human fallback:'
Write-Host '  omniroute --no-open --port 20128   # gateway'
Write-Host '  .\configuration\start-stack.ps1 -App openhands   # container'
Write-Host '  opencode serve --hostname 0.0.0.0 --port 4096    # phone fallback'
Write-Host '  .\setup.ps1 -Serve -Bind 0.0.0.0   # browser UI (needs elevation, shows a token)'
