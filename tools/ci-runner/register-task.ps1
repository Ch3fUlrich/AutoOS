#requires -Version 7
# Registers "\AutoOS CI Runners": start the CI runner supervisor at logon (operator yes 2026-10-16, D-1380).
# Remove: Unregister-ScheduledTask -TaskName "AutoOS CI Runners" -Confirm:$false
$pwsh = (Get-Command pwsh).Source
$dir = $PSScriptRoot
$supervisor = Join-Path $dir 'supervisor.ps1'
$action = New-ScheduledTaskAction -Execute $pwsh -Argument ('-NoProfile -WindowStyle Hidden -File "' + $supervisor + '"') -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = 'PT1M'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'AutoOS CI Runners' -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Self-hosted GitHub Actions runners (ephemeral, Docker Desktop) for the fleet: labels workst,workstation,fleet-ci. $supervisor" -Force |
    Select-Object TaskName, State
