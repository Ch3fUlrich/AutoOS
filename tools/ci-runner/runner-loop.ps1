#requires -Version 7
# Ephemeral self-hosted GitHub Actions runner on Docker Desktop (autoos:CI-WORKST-RUNNER).
# One job per container: register (--ephemeral) -> run one job -> container exits -> loop re-registers.
# Operator decisions: Docker Desktop, 32 GB RAM cap, per-slot CPU cap (D-1425), workstation-only CI.
# The registration token is fetched per iteration with the operator's gh login, passed by ENV NAME
# only (never argv, never logged). Stop: create the file  <dir>\stop-<Name>.
param(
    [Parameter(Mandatory)][string]$Repo,          # owner/name
    [Parameter(Mandatory)][string]$Name,          # slot name, e.g. workst-plangraph-1
    [string]$Labels = "workst,workstation,fleet-ci", # added to the defaults self-hosted,Linux,X64
    [string]$Memory = "32g",
    [string]$Image = "workst-runner:2.338.0-4",
    [int]$Cpus = 0,                                # per-slot CPU cap (D-1425); 0 = no cap
    [switch]$NoDockerSocket,                       # jobs cannot reach the host Docker
    [switch]$NetNs,                                # caps for `sudo unshare --net` (AutoOS loopback-only suite)
    [switch]$CacheVolumes,                         # mount the shared cargo/sccache cache volumes
    [string]$CacheScope = ''                          # slots.json repository prefix; required with -CacheVolumes
)
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$log = Join-Path $dir "$Name.log"
$stop = Join-Path $dir "stop-$Name"
Import-Module (Join-Path $PSScriptRoot 'CiRunner.psm1') -Force -DisableNameChecking
function Log([string]$m) { Add-Content $log ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $m) }
Log "start repo=$Repo labels=$Labels memory=$Memory image=$Image dockerSocket=$(-not $NoDockerSocket) pid=$PID"
while (-not (Test-Path $stop)) {
    try {
        $env:RUNNER_TOKEN = (gh api -X POST "repos/$Repo/actions/runners/registration-token" --jq .token)
        if (-not $env:RUNNER_TOKEN) { throw "no registration token" }
        $inst = "$Name-$(Get-Date -Format yyyyMMddHHmmss)"
        $dargs = Get-CiRunnerDockerArgs -Name $inst -Repo $Repo -Labels $Labels -Memory $Memory -Image $Image -DockerSocket:(-not $NoDockerSocket) -NetNs:$NetNs -CacheVolumes:$CacheVolumes -CacheScope $CacheScope -Cpus $Cpus
        Log "launch $inst"
        $env:MSYS_NO_PATHCONV = "1"
        & docker @dargs 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch 'token' } | Add-Content $log
        Log "exit $inst rc=$LASTEXITCODE"
    } catch {
        Log "error: $($_.Exception.Message)"
        Start-Sleep -Seconds 30
    } finally {
        Remove-Item Env:RUNNER_TOKEN -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}
Log "stop file found; exiting"
