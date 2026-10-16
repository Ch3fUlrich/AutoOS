#requires -Version 7
# CI runner supervisor (autoos:CI-WORKST-RUNNER, D-1380): keeps every slot in slots.json alive.
# Every 60 s: (1) a slot whose loop process is gone gets a new loop; (2) a slot whose loop is alive but
# whose runner has not been online on GitHub for > 5 min, and whose container is not running, is restarted
# (re-registers); never a slot whose runner is busy; (3) offline workst-* registrations are deleted.
# Single instance (named mutex). Started at logon by the scheduled task "\AutoOS CI Runners".
# Stop everything: create $PSScriptRoot\stop-supervisor (loops keep running; touch stop-<slot> for those).
$ErrorActionPreference = "Continue"
$dir = $PSScriptRoot
$log = Join-Path $dir "supervisor.log"
Import-Module (Join-Path $PSScriptRoot 'CiRunner.psm1') -Force -DisableNameChecking
function Log([string]$m) { Add-Content $log ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $m) }
$mutex = [System.Threading.Mutex]::new($false, "Global\AutoOS-CI-Runner-Supervisor")
if (-not $mutex.WaitOne(0)) { Log "another supervisor is running; exiting"; exit 0 }
Log "supervisor start pid=$PID"

# Docker Desktop must be up before any slot can start.
for ($i = 0; $i -lt 60; $i++) { docker info --format '{{.ServerVersion}}' *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 10 }

$unhealthySince = @{}
$offlineSince = @{}
while (-not (Test-Path (Join-Path $dir "stop-supervisor"))) {
    try {
        $cfg = Get-Content (Join-Path $dir "slots.json") -Raw | ConvertFrom-Json
        $procs = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" | Where-Object { $_.CommandLine -match 'runner-loop\.ps1' })
        $containers = @(docker ps --format '{{.Names}}' 2>$null)
        $runnersByRepo = @{}
        foreach ($r in $cfg.repos) {
            $runners = @(gh api "repos/$($r.repo)/actions/runners" --jq '.runners[] | {id, name, status, busy}' 2>$null | ForEach-Object { $_ | ConvertFrom-Json })
            $runnersByRepo[[string]$r.repo] = $runners
            # A fresh ephemeral runner shows offline until its session connects: delete a registration
            # only after it has stayed offline for 3 min (measured 2026-10-16: deleting at first sight churned).
            foreach ($rr in $runners | Where-Object { $_.status -eq 'offline' -and $_.name -like 'workst-*' -and -not $_.busy }) {
                $k = "$($r.repo)#$($rr.id)"
                if (-not $offlineSince.ContainsKey($k)) { $offlineSince[$k] = Get-Date; continue }
                $offlineMinutes = ((Get-Date) - $offlineSince[$k]).TotalMinutes
                if (-not (Test-CiOfflineExpired -OfflineMinutes $offlineMinutes)) { continue }
                gh api -X DELETE "repos/$($r.repo)/actions/runners/$($rr.id)" *> $null
                $offlineSince.Remove($k)
                Log "deleted offline registration $($rr.name) (offline > 3 min)"
            }
            foreach ($k in @($offlineSince.Keys | Where-Object { $_ -like "$($r.repo)#*" })) {
                $id = [long]($k -split '#')[1]
                if (-not ($runners | Where-Object { $_.id -eq $id -and $_.status -eq 'offline' })) { $offlineSince.Remove($k) }
            }
        }
        foreach ($name in @(Get-CiSlotNames -Config $cfg)) {
            $r = $cfg.repos | Where-Object { $name -match "^$([regex]::Escape($_.prefix))-[0-9]+$" } | Select-Object -First 1
            if (-not $r) { continue }
            $runners = @($runnersByRepo[[string]$r.repo])
            $loop = $procs | Where-Object { $_.CommandLine -match "-Name\s+'?$([regex]::Escape($name))'?(\s|$)" }
            $loopAlive = (@($loop).Count -gt 0)
            $online = (@($runners | Where-Object { $_.name -like "$name-*" -and $_.status -eq 'online' }).Count -gt 0)
            $busy = (@($runners | Where-Object { $_.name -like "$name-*" -and $_.busy }).Count -gt 0)
            $running = (@($containers | Where-Object { $_ -like "$name-*" }).Count -gt 0)
            if ($loop -and -not $online -and -not $busy -and -not $running) {
                if (-not $unhealthySince.ContainsKey($name)) { $unhealthySince[$name] = Get-Date }
                $unhealthyMinutes = ((Get-Date) - $unhealthySince[$name]).TotalMinutes
            } else {
                $unhealthySince.Remove($name)
                $unhealthyMinutes = 0
            }
            $action = Get-CiSlotAction -LoopAlive $loopAlive -Online $online -Busy $busy -ContainerRunning $running -UnhealthyMinutes $unhealthyMinutes
            if ($action -eq 'restart') {
                foreach ($l in $loop) { Stop-Process -Id $l.ProcessId -Force -ErrorAction SilentlyContinue }
                Log "restart $name (loop alive, runner not online > 5 min, no container)"
            }
            if ($action -eq 'start' -or $action -eq 'restart') {
                $a = @('-NoProfile', '-File', (Join-Path $dir 'runner-loop.ps1'), '-Repo', $r.repo, '-Name', $name, '-Memory', $r.memory, '-Labels', $cfg.labels)
                if ($r.image) { $a += @('-Image', $r.image) }
                if ($r.netns) { $a += '-NetNs' }
                Remove-Item (Join-Path $dir "stop-$name") -ErrorAction SilentlyContinue
                $p = Start-Process pwsh -ArgumentList $a -WindowStyle Hidden -PassThru
                $unhealthySince.Remove($name)
                Log "started $name pid=$($p.Id)"
            }
        }
    } catch { Log "error: $($_.Exception.Message)" }
    Start-Sleep -Seconds 60
}
Log "stop-supervisor found; exiting"
