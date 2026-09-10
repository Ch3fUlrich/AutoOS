#Requires -Version 7.0
<# .SYNOPSIS Select the WinGet downloader while preserving existing settings.
   .DESCRIPTION Run with -DryRun to preview. JSON comments are accepted by PS7.
   A backup is created only when a value actually changes. #>
[CmdletBinding()]
param(
    [ValidateSet('wininet', 'do')][string]$Downloader = 'wininet',
    [switch]$DryRun,
    [string]$SettingsPath = (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe\LocalState\settings.json')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '..\..\lib\windows\AutoOS.Ui.psm1') -DisableNameChecking
$settings = if (Test-Path -LiteralPath $SettingsPath) {
    Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json -AsHashtable
} else { @{} }
if ($null -eq $settings) { throw 'WinGet settings must be a JSON object.' }
if (-not $settings.Contains('network')) { $settings['network'] = @{} }
if ($settings['network'] -isnot [System.Collections.IDictionary]) { throw 'WinGet network settings must be a JSON object.' }
if ($settings['network']['downloader'] -eq $Downloader) { Write-AutoOSLine "WinGet already uses $Downloader." -Level ok; return }
if ($DryRun) { Write-AutoOSLine "would back up $SettingsPath and set network.downloader to $Downloader" -Level muted; return }
$settings['network']['downloader'] = $Downloader
if (Test-Path -LiteralPath $SettingsPath) {
    Copy-Item -LiteralPath $SettingsPath -Destination "$SettingsPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')"
} else {
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $SettingsPath) -Force)
}
$settings | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $SettingsPath -Encoding utf8
Write-AutoOSLine "WinGet now uses $Downloader for future downloads." -Level ok
