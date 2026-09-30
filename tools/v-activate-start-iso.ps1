# v-activate-start-iso.ps1 - start ONE isolated OmniRoute gateway for the V-activate probe run.
#
# Isolation: a separate DATA_DIR (config + a sqlite-backup snapshot of the DB) and a
# separate port, so the shared gateway on :20128 is never touched and never shares WAL.
# The launcher honours $env:DATA_DIR (bin/omniroute.mjs:112-118, bin/cli/data-dir.mjs:51).
#
# House pattern: tools/start-isolated-gateway.ps1 @ L1-backlog/ws-f1-vertex-20260930,
# with DATA_DIR isolation added per the V-activate brief.
param(
  [string]$Port = '20145'
)
$ErrorActionPreference = 'Stop'

$nodeExe = 'C:\Program Files\nodejs\node.exe'
$launcher = 'C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute\bin\omniroute.mjs'
$dataDir = Join-Path $env:TEMP 'opencode\v-activate-iso'
$logDir = Join-Path $env:TEMP 'opencode\v-activate-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$env:DATA_DIR = $dataDir
$proc = Start-Process -FilePath $nodeExe `
    -ArgumentList $launcher, '--no-open', '--port', $Port `
    -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $logDir 'stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'stderr.log')

Write-Output "launcher_pid=$($proc.Id)"
Write-Output "port=$Port"
Write-Output "DATA_DIR=$dataDir"
Write-Output "logs=$logDir"

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Output "listening=YES pid=$($conn[0].OwningProcess) after=$($i*2+2)s"
        exit 0
    }
}
Write-Output "listening=NO after 60s"
Write-Output '--- stderr ---'
Get-Content (Join-Path $logDir 'stderr.log') -Tail 40 -ErrorAction SilentlyContinue
Write-Output '--- stdout ---'
Get-Content (Join-Path $logDir 'stdout.log') -Tail 40 -ErrorAction SilentlyContinue
exit 1
