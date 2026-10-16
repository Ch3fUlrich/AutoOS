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

# Pre-pull the configured images (autoos:CI-SPEED-WORKST, D-1393) before the first slot
# starts, so the first job does not pay for a cold pull. Never fatal here: a failed pull
# is logged and the slot's own `docker run` pulls the image again when it needs it.
try {
    $cfg = Get-Content (Join-Path $dir "slots.json") -Raw | ConvertFrom-Json
    foreach ($img in @(Get-CiPrepullImages -Config $cfg)) {
        Log "prepull $img"
        docker pull $img *> $null
        if ($LASTEXITCODE -ne 0) { Log "prepull $img failed rc=$LASTEXITCODE (continuing)" }
    }
} catch { Log "prepull error: $($_.Exception.Message)" }

$unhealthySince = @{}
$offlineSince = @{}
$nextDiskCheck = Get-Date
while (-not (Test-Path (Join-Path $dir "stop-supervisor"))) {
    try {
        $cfg = Get-Content (Join-Path $dir "slots.json") -Raw | ConvertFrom-Json
        # Disk floor (D-1425): the main loop only ticks every 60 s, so the disk check keeps
        # its own timestamp and runs at most every 10 minutes. Free space is read with a
        # throwaway `df` container on the default image (second output line = avail in 1G
        # blocks); on any error it is logged and skipped, and a failed prune never fails
        # the supervisor.
        if (((Get-Date) - $nextDiskCheck).TotalMinutes -ge 10) {
            $nextDiskCheck = Get-Date
            $floorGB = 0.0
            $floorProp = $cfg.PSObject.Properties['disk_floor_gb']
            if ($floorProp -and $null -ne $floorProp.Value) { $floorGB = [double]$floorProp.Value }
            $freeGB = $null
            try {
                $dfImage = 'workst-runner:2.338.0-4'
                $imageProp = $cfg.PSObject.Properties['image']
                if ($imageProp -and $imageProp.Value) { $dfImage = [string]$imageProp.Value }
                $dfLines = @(docker run --rm --entrypoint df $dfImage -B1G --output=avail / 2>$null)
                if ($LASTEXITCODE -ne 0 -or $dfLines.Count -lt 2) { throw "df rc=$LASTEXITCODE" }
                $freeGB = [double]::Parse((($dfLines[1].Trim()) -split '\s+')[-1], [Globalization.CultureInfo]::InvariantCulture)
            } catch {
                Log "disk floor check failed: $($_.Exception.Message)"
                $freeGB = $null
            }
            if ($null -ne $freeGB -and (Test-CiDiskFloor -FreeGB $freeGB -FloorGB $floorGB)) {
                Log "disk floor: $freeGB GB < $floorGB GB, pruning"
                foreach ($pruneArgs in @(Get-CiPruneCommands)) {
                    docker @pruneArgs *> $null
                    Log "prune: docker $($pruneArgs -join ' ') rc=$LASTEXITCODE"
                }
            }
        }
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
                if ($r.cache) { $a += '-CacheVolumes' }
                # D-1425: per-slot CPU cap (repo "cpus" > config "cpus_default" > 8); 0 = no cap.
                $slotCpus = Get-CiSlotCpus -Repo $r -Config $cfg
                if ($slotCpus -gt 0) { $a += @('-Cpus', $slotCpus) }
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
