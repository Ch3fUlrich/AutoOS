#Requires -Version 5.1
<#
  .SYNOPSIS
    Snapshot, restore and report Claude Code sessions on Windows.
  .DESCRIPTION
    The CLI face of AutoOS.ClaudeAutostart. Both Scheduled Tasks invoke this, and
    so can a person:

        pwsh lib\windows\claude-sessions.ps1 -Action status
        pwsh lib\windows\claude-sessions.ps1 -Action snapshot
        pwsh lib\windows\claude-sessions.ps1 -Action restore -DryRun
        pwsh lib\windows\claude-sessions.ps1 -Action configure

    All output goes through AutoOS.Ui so -NoColor, a non-TTY and the run log keep
    working; nothing here writes with Write-Host directly.
  .PARAMETER Action
    status (default), snapshot, restore or configure.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'snapshot', 'restore', 'configure')]
    [string]$Action = 'status',

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.ClaudeAutostart.psm1') -DisableNameChecking -Force

$config = Get-AutoOSClaudeConfig

function Show-AutoOSClaudeStatus {
    $state = Get-AutoOSClaudeState
    $sessions = @($state.sessions)
    $terminal = Get-AutoOSClaudeTerminalHost -Config $config
    $installed = Test-AutoOSClaudeAutostartInstalled

    Write-AutoOSSection 'Claude sessions'
    # Health first, settings second: "will my sessions come back?" is answered by
    # the tasks, the terminal and the age of the snapshot, not by the resume mode.
    Write-AutoOSKeyValue 'Scheduled tasks' $(if ($installed) { 'registered' } else { 'not registered' })
    Write-AutoOSKeyValue 'Terminal host'   $(if ($terminal) { $terminal } else { 'none available' })
    Write-AutoOSKeyValue 'Last snapshot'   (Format-AutoOSTimestamp $state.captured_at_iso)
    Write-AutoOSKeyValue 'Tracked sessions' $sessions.Count
    Write-AutoOSKeyValue 'Live processes'  (Get-AutoOSClaudeLiveCount)
    Write-AutoOSKeyValue 'State file'      (Get-AutoOSClaudeSessionsPath)
    Write-AutoOSKeyValue 'Autostart'       $(if ([bool]$config['enabled']) { 'on' } else { 'paused' })
    Write-AutoOSKeyValue 'Resume mode'     $config['resume_mode']
    Write-AutoOSKeyValue 'Fallback'        $config['fallback']
    Write-AutoOSKeyValue 'Remote control'  $config['remote_control']

    # Every unhealthy state gets the command that fixes it, not just a label.
    if (-not $installed) {
        Write-AutoOSLine 'the scheduled tasks are not registered - install the claude-autostart component' -Level muted
    }
    if (-not $terminal) {
        Write-AutoOSLine 'no terminal host: restore will refuse until Windows Terminal is installed' -Level warn
    }
    if ($sessions.Count -eq 0) {
        Write-AutoOSLine 'nothing recorded yet - run: setup.ps1 -ClaudeSessions snapshot' -Level muted
        return
    }

    Write-AutoOSSection 'Tracked sessions'
    foreach ($s in $sessions) {
        $when = if ($s.PSObject.Properties.Name -contains 'last_active' -and $s.last_active) {
            [DateTimeOffset]::FromUnixTimeSeconds([int64]$s.last_active).LocalDateTime.ToString('yyyy-MM-dd HH:mm')
        } else { '-' }
        $short = if ($s.session_uuid) { $s.session_uuid.Substring(0, [Math]::Min(8, $s.session_uuid.Length)) } else { '-' }
        $branch = if ($s.PSObject.Properties.Name -contains 'git_branch' -and $s.git_branch) { "  ($($s.git_branch))" } else { '' }
        Write-AutoOSKeyValue $s.name "$when  $short…  $($s.cwd)$branch"
    }
}

function Invoke-AutoOSClaudeConfigure {
    <#
      .SYNOPSIS
        Edit the settings with radio menus and save them.
      .DESCRIPTION
        Every one of these is a closed set, so they are chosen rather than typed:
        a typo in a free-text answer used to be accepted silently and then ignored
        by the code that read it.
    #>
    if (-not (Test-AutoOSInteractive)) {
        Write-AutoOSLine 'configure needs an interactive terminal' -Level error
        return
    }

    $resume = Show-AutoOSRadioMenu -Title 'How should a restored session resume?' `
        -DefaultId ([string]$config['resume_mode']) -Items @(
            @{ Id = 'full'; Name = 'Resume in full'; Description = 'The whole conversation, with nothing compacted away'; Badge = 'recommended' }
            @{ Id = 'summary'; Name = 'Resume from a summary'; Description = 'Faster to load, and it discards the working context'; Badge = '' }
        )
    $fallback = Show-AutoOSRadioMenu -Title 'When there is no snapshot to restore' `
        -DefaultId ([string]$config['fallback']) -Items @(
            @{ Id = 'continue'; Name = 'Start one session'; Description = 'Runs claude --continue in the fallback directory'; Badge = 'default' }
            @{ Id = 'none'; Name = 'Do nothing'; Description = 'Leaves the machine alone until you start a session yourself'; Badge = '' }
        )
    $rc = Show-AutoOSRadioMenu -Title 'Remote control for restored sessions' `
        -DefaultId ([string]$config['remote_control']) -Items @(
            @{ Id = 'snapshot'; Name = 'Keep what was recorded'; Description = 'Restores each session the way it was running'; Badge = 'default' }
            @{ Id = 'always'; Name = 'Always enable'; Description = 'Every restored session gets --rc'; Badge = '' }
            @{ Id = 'never'; Name = 'Never enable'; Description = 'No restored session gets --rc'; Badge = '' }
        )
    $interval = Read-AutoOSValue -Question 'Snapshot interval in minutes' -Default ([string]$config['snapshot_interval_mins'])
    $parsed = 0
    if (-not [int]::TryParse($interval, [ref]$parsed) -or $parsed -lt 1 -or $parsed -gt 59) {
        Write-AutoOSLine "interval must be 1-59 minutes; keeping $($config['snapshot_interval_mins'])" -Level warn
        $parsed = [int]$config['snapshot_interval_mins']
    }

    if ($DryRun) {
        Write-AutoOSLine "would save: resume_mode=$resume fallback=$fallback remote_control=$rc snapshot_interval_mins=$parsed" -Level muted
        return
    }

    $saved = Set-AutoOSClaudeConfig -Settings @{
        resume_mode = $resume; fallback = $fallback
        remote_control = $rc; snapshot_interval_mins = $parsed
    }
    Write-AutoOSLine "saved to $saved" -Level ok
    Write-AutoOSLine 're-run the installer to apply the new snapshot interval to the scheduled task' -Level muted
}

switch ($Action) {
    'snapshot'  { [void](Save-AutoOSClaudeSnapshot -DryRun:$DryRun) }
    'restore'   { Restore-AutoOSClaudeSessions -Config $config -DryRun:$DryRun }
    'status'    { Show-AutoOSClaudeStatus }
    'configure' { Invoke-AutoOSClaudeConfigure }
}
