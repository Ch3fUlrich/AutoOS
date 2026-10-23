#requires -Version 7
# Restart one runner slot safely: refuse while its runner is busy (a running job would be killed).
# usage: restart-slot.ps1 -Repo owner/name -Name workst-autoos-1 -Memory 4g [-NetNs]
param([Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][string]$Name, [string]$Memory = "4g", [switch]$NetNs)
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$runners = gh api "repos/$Repo/actions/runners" --jq ".runners[] | select(.name | startswith(`"$Name-`")) | {busy, status, name}" | ForEach-Object { $_ | ConvertFrom-Json }
if ($runners | Where-Object { $_.busy }) { "BUSY $Name - not restarted"; exit 3 }
# stop the loop process (by its own command line, never another slot) and its idle container
$loops = Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape("runner-loop.ps1") -and $_.CommandLine -match "-Name\s+'?$([regex]::Escape($Name))'?(\s|$)" }
foreach ($l in $loops) { Stop-Process -Id $l.ProcessId -Force; "stopped loop PID $($l.ProcessId)" }
docker ps --filter "name=$Name-" --format '{{.Names}}' | ForEach-Object { docker stop -t 5 $_ | Out-Null; "stopped container $_" }
$a = @('-NoProfile', '-File', (Join-Path $dir 'runner-loop.ps1'), '-Repo', $Repo, '-Name', $Name, '-Memory', $Memory)
if ($NetNs) { $a += '-NetNs' }
$p = Start-Process pwsh -ArgumentList $a -WindowStyle Hidden -PassThru
"$Name PID $($p.Id)" | Tee-Object -FilePath (Join-Path $dir 'pids.txt') -Append
