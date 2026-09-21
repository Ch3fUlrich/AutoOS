<#
.SYNOPSIS
    Resume the AutoOS AI stack after logon: gateway + OpenHands + opencode serve.

.DESCRIPTION
    Opt-in resume helper run by the "AutoOS Stack" scheduled task created with
    Register-AutoOSAutostart.ps1 (or by hand after a reboot). Safe to run twice:
    every probe below no-ops when already up, and nothing here installs
    software, removes anything, or overwrites user files.

    What resumes:
      1. OmniRoute gateway on http://127.0.0.1:20128 (started hidden when down).
      2. OpenHands container `openhands-app` (docker start when exited;
         first boot still needs .\configuration\start-stack.ps1 -App openhands).
      3. opencode serve on :4096 (started hidden when down; 401 = alive).

    What does NOT resume: AutoOS --serve. Its token is random per run, so an
    autostarted browser UI would print its URL where nobody reads it. Start it
    by hand when you need it: .\setup.ps1 -Serve.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Start-AutoOSStack.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Gateway = 'http://127.0.0.1:20128'

function Test-AutoOSGateway {
    try { (Invoke-WebRequest -Uri "$Gateway/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}

# 1. Gateway first: everything else routes through it.
if (Test-AutoOSGateway) {
    Write-Host 'Gateway already up on 20128 - nothing to do.'
} elseif (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    Write-Host 'omniroute is not installed - run setup.ps1 -Only omniroute -Yes once, then re-run this.'
} else {
    Write-Host 'Starting OmniRoute in the background...'
    Start-Process -FilePath 'omniroute' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
    $tries = 0
    while ((-not (Test-AutoOSGateway)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
    if (Test-AutoOSGateway) { Write-Host 'Gateway OK on 20128.' }
    else { Write-Host 'Gateway did not answer - run `omniroute doctor`.' }
}

# 2. OpenHands container: restart only when it exists and is not running.
#    `docker start` on a missing name exits non-zero; that is reported, not fatal.
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'docker is not on PATH - skipping the OpenHands container.'
} else {
    $running = (& docker ps --format '{{.Names}}' 2>$null) -join "`n"
    if ($running -match '(?m)^openhands-app$') {
        Write-Host 'OpenHands container already running - nothing to do.'
    } else {
        $existing = (& docker ps -a --format '{{.Names}}' 2>$null) -join "`n"
        if ($existing -match '(?m)^openhands-app$') {
            Write-Host 'Restarting the stopped openhands-app container...'
            & docker start openhands-app | Out-Null
            Write-Host 'OpenHands UI should answer on http://localhost:3000 shortly.'
        } else {
            Write-Host 'No openhands-app container yet - first boot: .\configuration\start-stack.ps1 -App openhands'
        }
    }
}

# 3. opencode serve on :4096: start hidden only when down (401 = alive,
#    needs pairing — the server is up, the browser just needs credentials).
function Test-OpencodeServe {
    try { (Invoke-WebRequest -Uri "http://127.0.0.1:4096/" -UseBasicParsing -TimeoutSec 5).StatusCode -in 200, 401 }
    catch {
        $resp = $_.Exception.Response
        if ($null -ne $resp) { return ([int]$resp.StatusCode) -in 200, 401 }
        return $false
    }
}
if (Test-OpencodeServe) {
    Write-Host 'opencode serve already up on :4096 - nothing to do.'
} elseif (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Host 'opencode is not installed - run setup.ps1 -Only opencode-cli -Yes once, then re-run this.'
} else {
    Write-Host 'Starting opencode serve in the background...'
    Start-Process -FilePath 'opencode' -ArgumentList 'serve', '--hostname', '0.0.0.0', '--port', '4096' -WindowStyle Hidden
    $tries = 0
    while ((-not (Test-OpencodeServe)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
    if (Test-OpencodeServe) { Write-Host 'opencode serve OK on :4096.' }
    else { Write-Host 'opencode serve did not answer - re-run configuration\healthcheck.ps1 for detail.' }
}
