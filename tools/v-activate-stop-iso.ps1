# v-activate-stop-iso.ps1 - stop ONLY this lane's isolated OmniRoute instance, by PID.
#
# The isolated instance is: launcher (bin/omniroute.mjs --no-open --port 20145) and its
# child server-ws.mjs listener. Both are killed by PID; nothing else in the 201xx range
# (the shared :20128 gateway, the pre-existing foreign :20138 listener) is touched.
param(
  [int[]]$Pids = @(121344, 125716)
)
$ErrorActionPreference = 'Continue'

$before = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 20100 -and $_.LocalPort -le 20200 } |
    Sort-Object LocalPort |
    ForEach-Object { "port=$($_.LocalPort) pid=$($_.OwningProcess)" }
Write-Output '--- before ---'
$before | ForEach-Object { Write-Output $_ }

foreach ($p in $Pids) {
    if (Get-Process -Id $p -ErrorAction SilentlyContinue) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Output "killed pid=$p"
    } else {
        Write-Output "pid=$p already gone"
    }
}

Start-Sleep -Seconds 3
$after = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 20100 -and $_.LocalPort -le 20200 } |
    Sort-Object LocalPort |
    ForEach-Object { "port=$($_.LocalPort) pid=$($_.OwningProcess)" }
Write-Output '--- after ---'
$after | ForEach-Object { Write-Output $_ }

$still20145 = $after | Where-Object { $_ -match 'port=20145 ' }
if ($still20145) {
    Write-Output 'RESULT: 20145 STILL LISTENING'
    exit 1
}
Write-Output 'RESULT: 20145 removed'
exit 0
