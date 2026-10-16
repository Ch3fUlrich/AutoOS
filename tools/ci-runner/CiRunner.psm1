# CiRunner.psm1 - pure decision logic for the workstation CI runner tooling (D-1380).
# runner-loop.ps1, supervisor.ps1 and register-task.ps1 do the side effects (gh, docker,
# process control, scheduled task); everything they *decide* lives here, free of I/O, free
# of host-specific paths and free of the registration token, so tests/run-tests.ps1 can
# exercise it without Docker, GitHub or a workstation.

function Get-CiRunnerDockerArgs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,   # container/runner instance name
        [Parameter(Mandatory)][string]$Repo,   # owner/name
        [Parameter(Mandatory)][string]$Labels, # csv added to self-hosted,Linux,X64
        [Parameter(Mandatory)][string]$Memory, # docker --memory value
        [Parameter(Mandatory)][string]$Image,  # runner image
        [switch]$DockerSocket,                 # mount the host Docker socket into the job
        [switch]$NetNs,                        # caps for `sudo unshare --net`
        [switch]$CacheVolumes,                 # mount the shared cargo/sccache cache volumes
        [int]$Cpus = 0                         # per-slot CPU cap (D-1425); 0 = no cap
    )
    # NOTE: there is deliberately no token parameter. The registration token travels in the
    # RUNNER_TOKEN environment name only - never on argv, never in a log line.
    $d = @('run', '--rm', '--name', $Name, '--memory', $Memory,
           '-e', 'RUNNER_TOKEN',
           '-e', "REPO_URL=https://github.com/$Repo",
           '-e', "RUNNER_NAME=$Name",
           '-e', "RUNNER_LABELS=$Labels")
    if ($DockerSocket) { $d += @('--group-add', '0', '-v', '/var/run/docker.sock:/var/run/docker.sock') }
    if ($NetNs) { $d += @('--cap-add', 'SYS_ADMIN', '--cap-add', 'NET_ADMIN', '--security-opt', 'seccomp=unconfined') }
    # CI-SPEED-WORKST (D-1393): shared named volumes for the cargo registry/git and the
    # sccache build cache. `docker run` requires every option BEFORE the image, so this
    # block must stay above the --entrypoint/image line below.
    if ($CacheVolumes) {
        $vols = Get-CiCacheVolumes
        $dirs = @('/home/runner/.cargo/registry', '/home/runner/.cargo/git', '/home/runner/.cache/sccache')
        for ($k = 0; $k -lt $vols.Count; $k++) { $d += @('-v', "$($vols[$k]):$($dirs[$k])") }
        $d += @('-e', 'RUSTC_WRAPPER=/usr/local/bin/sccache',
                '-e', 'SCCACHE_DIR=/home/runner/.cache/sccache',
                '-e', 'SCCACHE_CACHE_SIZE=30G')
    }
    # CI-SPEED-WORKST (D-1425, operator decision 2026-10-16): a per-slot CPU cap, because
    # 16 containers for 9 repos on one box let one repo's pytest starve the others
    # (measured: gen-analysis pytest 32 min under load vs ~9 min hosted). Equal
    # --cpu-shares keeps the scheduling weight proportional to the cap. Like every other
    # docker run option this must stay ABOVE the --entrypoint/image line below. 0 = no cap.
    if ($Cpus -gt 0) { $d += @('--cpus', "$Cpus", '--cpu-shares', '1024') }
    $d += @('--entrypoint', 'bash', $Image, '-c',
        './config.sh --url "$REPO_URL" --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" --labels "$RUNNER_LABELS" --ephemeral --unattended --disableupdate --replace >/dev/null && unset RUNNER_TOKEN && ./run.sh')
    return ,$d
}

function Get-CiSlotAction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][bool]$LoopAlive,
        [Parameter(Mandatory)][bool]$Online,
        [Parameter(Mandatory)][bool]$Busy,
        [Parameter(Mandatory)][bool]$ContainerRunning,
        [Parameter(Mandatory)][double]$UnhealthyMinutes
    )
    # start: the loop process is gone - nothing else matters, start a new one.
    if (-not $LoopAlive) { return 'start' }
    # ok: a runner is online, or a job is running, or the container is still up.
    if ($Online -or $Busy -or $ContainerRunning) { return 'ok' }
    # A busy runner is never restarted (checked above): a running job must not be killed.
    if ($UnhealthyMinutes -gt 5) { return 'restart' }
    return 'wait'
}

function Test-CiOfflineExpired {
    [CmdletBinding()]
    param([Parameter(Mandatory)][double]$OfflineMinutes)
    # A fresh ephemeral runner shows offline until its session connects: only after it has
    # stayed offline for 3 min is its registration safe to delete.
    return ($OfflineMinutes -ge 3)
}

function Get-CiSlotNames {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Config)
    $names = @()
    foreach ($r in @($Config.repos)) {
        for ($s = 1; $s -le [int]$r.slots; $s++) { $names += "$($r.prefix)-$s" }
    }
    return $names
}

function Get-CiCacheVolumes {
    [CmdletBinding()]
    param()
    # Single source of truth for the shared CI cache volumes (CI-SPEED-WORKST, D-1393):
    # Get-CiRunnerDockerArgs -CacheVolumes mounts exactly these, and anything that has to
    # create or prune them asks here.
    return @('ci-cargo-registry', 'ci-cargo-git', 'ci-sccache')
}

function Get-CiPrepullImages {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Config)
    # Config 'prepull' array: images the supervisor pulls before the first slot starts.
    # Absent key -> empty array (no pre-pull work).
    return @(@($Config.prepull) | Where-Object { $_ })
}

function Get-CiSlotCpus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Repo,    # one slots.json repo entry
        [Parameter(Mandatory)]$Config   # the whole slots.json config
    )
    # CI-SPEED-WORKST (D-1425, operator decision 2026-10-16): the per-slot CPU cap, so one
    # repository's pytest cannot starve the other repositories' jobs on the same box.
    # Precedence: the repo entry's "cpus", else the config's top-level "cpus_default",
    # else 8. A resolved value of 0 means "no cap" and is returned unchanged (the
    # supervisor then omits -Cpus and the container may use every core).
    $v = $null
    if ($Repo -is [System.Collections.IDictionary]) {
        if ($Repo.Contains('cpus')) { $v = $Repo['cpus'] }
    } elseif ($Repo.PSObject.Properties['cpus']) {
        $v = $Repo.PSObject.Properties['cpus'].Value
    }
    if ($null -ne $v) { return [int]$v }
    $v = $null
    if ($Config -is [System.Collections.IDictionary]) {
        if ($Config.Contains('cpus_default')) { $v = $Config['cpus_default'] }
    } elseif ($Config.PSObject.Properties['cpus_default']) {
        $v = $Config.PSObject.Properties['cpus_default'].Value
    }
    if ($null -ne $v) { return [int]$v }
    return 8
}

function Test-CiDiskFloor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][double]$FreeGB,  # free space on the Docker disk
        [Parameter(Mandatory)][double]$FloorGB  # config "disk_floor_gb"; <= 0 disables
    )
    # CI-SPEED-WORKST (D-1425): $true when the supervisor must prune - free space below the
    # floor. A floor of 0 (or less) turns the check off entirely: never prune.
    if ($FloorGB -le 0) { return $false }
    return ($FreeGB -lt $FloorGB)
}

function Get-CiPruneCommands {
    [CmdletBinding()]
    param()
    # CI-SPEED-WORKST (D-1425): what the supervisor runs when the Docker disk drops below
    # the floor, in order. Deliberately narrow - dangling images and the build cache only.
    # It NEVER touches volumes (the ci-cargo-registry / ci-cargo-git / ci-sccache caches
    # are named volumes) and NEVER removes tagged images (workst-runner:* and the
    # pre-pulled job images), so a prune can neither destroy a cache nor force a re-pull.
    $prunes = [System.Collections.Generic.List[object]]::new()
    [void]$prunes.Add([object[]]@('image', 'prune', '-f'))
    [void]$prunes.Add([object[]]@('builder', 'prune', '-f', '--keep-storage', '20gb'))
    return $prunes
}

Export-ModuleMember -Function Get-CiRunnerDockerArgs, Get-CiSlotAction, Test-CiOfflineExpired, Get-CiSlotNames, Get-CiCacheVolumes, Get-CiPrepullImages, Get-CiSlotCpus, Test-CiDiskFloor, Get-CiPruneCommands
