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
        [switch]$NetNs                         # caps for `sudo unshare --net`
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

Export-ModuleMember -Function Get-CiRunnerDockerArgs, Get-CiSlotAction, Test-CiOfflineExpired, Get-CiSlotNames
