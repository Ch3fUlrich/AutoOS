<#
.SYNOPSIS
    Opt-in autostart for the AutoOS AI stack (Windows scheduled task).

.DESCRIPTION
    Creates (or replaces, idempotently) a per-user scheduled task named
    "AutoOS Stack" that runs configuration/autostart/Start-AutoOSStack.ps1 at
    logon with a 1-minute delay. Re-running registers the same single task,
    never a duplicate. Nothing runs until you call this script: autostart is
    strictly opt-in, and -Unregister removes the task again.

    The task runs as the current user (no elevation, no stored password) and
    only starts things: gateway + container resume. It never installs,
    uninstalls, or modifies user files.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Register-AutoOSAutostart.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Register-AutoOSAutostart.ps1 -Unregister
#>
[CmdletBinding()]
param([switch]$Unregister)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'AutoOS Stack'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Launcher = Join-Path $PSScriptRoot 'Start-AutoOSStack.ps1'

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) { Write-Host "No '$TaskName' task - nothing to remove."; return }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the '$TaskName' task."
    return
}

if (-not (Test-Path $Launcher)) { throw "Launcher missing: $Launcher" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`"")
$trigger = New-ScheduledTaskTrigger -AtLogOn -RandomDelay (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

# Register-ScheduledTask with -Force replaces the same-named task in place:
# the second run reports the same single task, never a duplicate.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Resume the AutoOS AI stack (OmniRoute gateway + OpenHands container) after logon.' `
    -Force | Out-Null
Write-Host "Registered '$TaskName': logon trigger, runs Start-AutoOSStack.ps1."
Write-Host 'Remove any time with: Register-AutoOSAutostart.ps1 -Unregister'
