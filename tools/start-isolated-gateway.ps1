#!/usr/bin/env pwsh
# start-isolated-gateway.ps1 — Start an isolated OmniRoute gateway for testing.
#
# Uses the SAME default DATA_DIR (~/.omniroute/) so the gateway has provider
# credentials and the .env config. Only the PORT differs (20138 vs 20128).
# The shared gateway on :20128 keeps running its old in-memory code — we do
# NOT restart it. The isolated gateway on :20138 loads the patched code from disk.
#
# SQLite contention is minimal: WAL mode allows concurrent reads; we only send
# a handful of test requests.
$ErrorActionPreference = 'Stop'

$port = '20138'
$nodeExe = 'C:\Program Files\nodejs\node.exe'
$launcher = Join-Path $env:APPDATA 'npm\node_modules\omniroute\bin\omniroute.mjs'
$logDir = Join-Path ([System.IO.Path]::GetTempPath()) 'opencode\omniroute-iso-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# Start the gateway via the launcher — same DATA_DIR, different PORT.
# The launcher reads .env from the default DATA_DIR (~/.omniroute/).
$proc = Start-Process -FilePath $nodeExe `
    -ArgumentList $launcher, '--no-open', "--port", $port `
    -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $logDir 'stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'stderr.log')

Write-Output "Launcher PID: $($proc.Id)"
Write-Output "Port: $port"
Write-Output "DATA_DIR: default (~/.omniroute/)"
Write-Output "Logs: $logDir"

# Wait for it to start (the launcher spawns server-ws.mjs which takes a few seconds)
Start-Sleep -Seconds 10

# Check if it's listening
$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Output "Gateway is LISTENING on port $port"
    $conn | Select-Object LocalPort,State,OwningProcess | Format-Table -AutoSize
} else {
    Write-Output "Gateway NOT listening yet — checking logs:"
    Get-Content (Join-Path $logDir 'stderr.log') -Tail 30 -ErrorAction SilentlyContinue
    Get-Content (Join-Path $logDir 'stdout.log') -Tail 30 -ErrorAction SilentlyContinue
}
