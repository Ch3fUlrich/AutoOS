<#
.SYNOPSIS
  Start of the agent working window: launch the runner unless one is already
  running against this repo. The runner resumes every session its state file
  records (crashed / stopped ones get a nudge with their old context) and starts
  the lanes that have not run yet.

.DESCRIPTION
  Companion of window_stop.ps1. Register with Task Scheduler (e.g. daily 20:00).
  Safe to fire on a day the runner is still alive (weekend): it exits without a
  second runner over the same state file. Logs to <stateDir>/window.log and the
  runner's own stdout/stderr to <stateDir>/runner.stdout.log / runner.stderr.log.
#>
param(
    [string]$Config = ".claude/handoff.config.json",
    [string[]]$RunnerArgs = @()
)
$ErrorActionPreference = "Continue"
$repo = (git rev-parse --show-toplevel 2>$null); if (-not $repo) { $repo = (Get-Location).Path }
$cfgPath = Join-Path $repo $Config
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$stateDir = Join-Path $repo ($cfg.stateDir ?? "output/sessions")
New-Item -ItemType Directory -Force $stateDir | Out-Null
$log = Join-Path $stateDir "window.log"
function W($m) { $line = "{0} [window_start] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m; Add-Content -Path $log -Value $line; Write-Host $line }

$alive = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pwsh' -and $_.CommandLine -match 'run_handoff_sessions\.ps1' -and $_.CommandLine -match [regex]::Escape((Split-Path $repo -Leaf)) }
if ($alive) { W "a runner is already running (pid $($alive[0].ProcessId)); not starting a second one"; exit 0 }
if (-not $env:OMNIGRAPH_TOKEN) { $env:OMNIGRAPH_TOKEN = [Environment]::GetEnvironmentVariable("OMNIGRAPH_TOKEN", "User") }
if (-not $env:OMNIGRAPH_TOKEN) { W "WARNING: OMNIGRAPH_TOKEN is not set in this environment; sessions will have no memory layer" }

$runner = Join-Path $PSScriptRoot "run_handoff_sessions.ps1"
$args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runner, "-Config", $cfgPath) + $RunnerArgs
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$p = Start-Process -FilePath "pwsh" -ArgumentList $args -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $stateDir "runner.stdout.$stamp.log") `
    -RedirectStandardError (Join-Path $stateDir "runner.stderr.$stamp.log")
W "runner started (pid $($p.Id)) with args: $($RunnerArgs -join ' ')"
