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
        $blindTick = $false
        $procs = @()
        try {
            $procs = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" -ErrorAction Stop | Where-Object { $_.CommandLine -match 'runner-loop\.ps1' })
        } catch { $blindTick = $true; Log "blind tick: CIM query failed: $($_.Exception.Message)" }
        $containers = @(docker ps --format '{{.Names}}' 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'docker container discovery failed' }
        $slotNames = @(Get-CiSlotNames -Config $cfg)
        if ($procs.Count -eq 0 -and @($containers | Where-Object {
            $containerName = $_
            @($slotNames | Where-Object { $containerName -like "$_-*" }).Count -gt 0
        }).Count -gt 0) { $blindTick = $true; Log 'blind tick: no runner-loop processes found while slot containers run' }
        $ageConfig = Get-CiContainerMaxAge -Config $cfg
        $maxAge = $ageConfig.Minutes
        if (-not $ageConfig.Valid) { Log 'invalid or absent container_max_age_minutes; using 120' }
        $runnersByRepo = @{}
        foreach ($r in $cfg.repos) {
            $runners = @(gh api --paginate "repos/$($r.repo)/actions/runners" --jq '.runners[] | {id, name, status, busy}' 2>$null | ForEach-Object { $_ | ConvertFrom-Json })
            if ($LASTEXITCODE -ne 0) { throw "runner discovery failed for $($r.repo); skipping tick" }
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
            # Exact instance names (runner-loop: <slot>-yyyyMMddHHmmss): slot workst-a-1 must not claim workst-a-1-1-*.
            $slotRe = Get-CiSlotInstancePattern -Name $name
            $slotContainers = @(
                foreach ($containerName in @($containers | Where-Object { $_ -match $slotRe })) {
                    $registration = @($runners | Where-Object { $_.name -eq $containerName })
                    # Ambiguous/unknown busy state is protected; a missing registration ages out.
                    $isBusy = $registration.Count -gt 1 -or ($registration.Count -eq 1 -and $registration[0].busy -ne $false)
                    $isOnline = $registration.Count -eq 1 -and $registration[0].status -eq 'online'
                    $created = docker inspect --format '{{.Created}}' $containerName 2>$null
                    if ($LASTEXITCODE -ne 0) { continue }
                    try { $createdAt = [datetimeoffset]::Parse($created, [Globalization.CultureInfo]::InvariantCulture) }
                    catch { Log "skip $containerName invalid creation timestamp: $created"; continue }
                    [pscustomobject]@{ Name = $containerName; Created = $createdAt; Busy = $isBusy; Online = $isOnline }
                }
            )
            foreach ($remove in @(Get-CiContainersToRemove -Containers $slotContainers -MaxAgeMinutes $maxAge)) {
                # Recheck job state immediately before removal; API errors fail closed.
                $fresh = @(gh api --paginate "repos/$($r.repo)/actions/runners" --jq '.runners[] | {name, busy, status}' 2>$null | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.name -eq $remove })
                if ($LASTEXITCODE -ne 0 -or $fresh.Count -gt 1 -or ($fresh.Count -eq 1 -and $fresh[0].busy -ne $false)) { continue }
                # Registered at the tick but missing now: unknown state, fail closed.
                if ($fresh.Count -eq 0 -and @($runners | Where-Object { $_.name -eq $remove }).Count -gt 0) { continue }
                # Recompute with fresh registration state: age alone must never kill an online runner.
                $candidate = $slotContainers | Where-Object { $_.Name -eq $remove }
                $candidate.Online = $fresh.Count -eq 1 -and $fresh[0].status -eq 'online'
                if ($remove -notin @(Get-CiContainersToRemove -Containers $slotContainers -MaxAgeMinutes $maxAge)) { continue }
                docker rm -f $remove *> $null
                Log "reap $remove rc=$LASTEXITCODE"
            }
            $loop = $procs | Where-Object { $_.CommandLine -match "-Name\s+'?$([regex]::Escape($name))'?(\s|$)" }
            $loopAlive = (@($loop).Count -gt 0)
            $online = (@($runners | Where-Object { $_.name -match $slotRe -and $_.status -eq 'online' }).Count -gt 0)
            $busy = (@($runners | Where-Object { $_.name -match $slotRe -and $_.busy }).Count -gt 0)
            $running = (@($containers | Where-Object { $_ -match $slotRe }).Count -gt 0)
            if ($loop -and -not $online -and -not $busy -and -not $running) {
                if (-not $unhealthySince.ContainsKey($name)) { $unhealthySince[$name] = Get-Date }
                $unhealthyMinutes = ((Get-Date) - $unhealthySince[$name]).TotalMinutes
            } else {
                $unhealthySince.Remove($name)
                $unhealthyMinutes = 0
            }
            $action = Get-CiSlotAction -LoopAlive $loopAlive -Online $online -Busy $busy -ContainerRunning $running -UnhealthyMinutes $unhealthyMinutes
            if ($blindTick -and $action -eq 'restart') { continue }
            if ($action -eq 'restart') {
                foreach ($l in $loop) { Stop-Process -Id $l.ProcessId -Force -ErrorAction SilentlyContinue }
                Log "restart $name (loop alive, runner not online > 5 min, no container)"
            }
            if ($action -eq 'start' -or $action -eq 'restart') {
                $a = @('-NoProfile', '-File', (Join-Path $dir 'runner-loop.ps1'), '-Repo', $r.repo, '-Name', $name, '-Memory', $r.memory, '-Labels', $cfg.labels)
                if ($r.image) { $a += @('-Image', $r.image) }
                if ($r.netns) { $a += '-NetNs' }
                if ($r.cache) { $a += '-CacheVolumes', '-CacheScope', $r.prefix }
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
