<#
.SYNOPSIS
  End of the agent working window: stop the runner and every background session it
  owns, and tell each session (through its inbox) what happened, so the next
  window_start.ps1 resumes them with context.

.DESCRIPTION
  The owner shares this host; agent runs are allowed evenings, nights and weekends
  only. Register this script with Task Scheduler for the window's end (e.g. Mon-Fri
  11:55) and window_start.ps1 for its start (e.g. daily 20:00). Both read
  .claude/handoff.config.json for stateDir and the worktree prefix; nothing here is
  repo-specific.

  Order matters: the runner's lane children resume a stopped session within two
  minutes, so the runner processes are killed BEFORE the sessions are stopped.
  Killed sessions keep their recorded session_id; the runner's next launch
  reconciles them as crashed and resumes each with a nudge (its subagents are gone
  - the inbox note says so, because the nudge does not).
#>
param(
    [string]$Config = ".claude/handoff.config.json",
    [string]$Reason = "end of the agent working window (owner rule: no agent work 12:00-20:00 on weekdays)"
)
$ErrorActionPreference = "Continue"
$repo = (git rev-parse --show-toplevel 2>$null); if (-not $repo) { $repo = (Get-Location).Path }
$cfgPath = Join-Path $repo $Config
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$stateDir = Join-Path $repo ($cfg.stateDir ?? "output/sessions")
$prefix = $cfg.worktreePrefix ?? (Split-Path $repo -Leaf)
$parent = $cfg.worktreeParent ?? (Split-Path $repo -Parent)
$log = Join-Path $stateDir "window.log"
function W($m) { $line = "{0} [window_stop] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m; Add-Content -Path $log -Value $line; Write-Host $line }
New-Item -ItemType Directory -Force (Join-Path $stateDir "inbox") | Out-Null

# 1. the runner (parent + lane children): any pwsh running run_handoff_sessions.ps1 against this repo
$runners = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pwsh' -and $_.CommandLine -match 'run_handoff_sessions\.ps1' -and $_.CommandLine -match [regex]::Escape((Split-Path $repo -Leaf)) }
foreach ($p in $runners) { W "stopping runner process $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }

# 2. the sessions: every background claude session whose cwd is one of this run's worktrees
$agents = @()
try { $agents = (claude agents --json 2>$null | ConvertFrom-Json) } catch { W "claude agents --json failed: $($_.Exception.Message)" }
$stopped = @()
foreach ($a in @($agents)) {
    if ($a.kind -ne "background") { continue }
    if (-not ($a.cwd -like (Join-Path $parent "$prefix-*"))) { continue }
    W "stopping session $($a.id) in $($a.cwd)"
    claude stop $a.id 2>&1 | Out-Null
    $stopped += $a
}

# 3. one inbox line per running session key, so the resumed session knows why it stopped
$stateFile = Join-Path $stateDir "state.json"
if (Test-Path $stateFile) {
    $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    foreach ($prop in $state.PSObject.Properties) {
        $e = $prop.Value
        if ($e.status -in @("running", "working", "waiting")) {
            $note = @"

## Window note $(Get-Date -Format "yyyy-MM-dd HH:mm") — you were stopped by the schedule
$Reason. You are resumed at the next window start (weekdays 20:00; weekends run through).
Your subagents from before the stop are GONE: check `git status` and the ledger for any
in-flight fix round, re-dispatch what is missing, and never assume a review that was
running has finished.
"@
            Add-Content -Path (Join-Path $stateDir "inbox/$($prop.Name).md") -Value $note
        }
    }
}
W "done: $($runners.Count) runner process(es), $($stopped.Count) session(s) stopped"
