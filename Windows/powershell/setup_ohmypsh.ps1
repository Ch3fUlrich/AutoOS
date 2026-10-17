<#
.SYNOPSIS
    Compatibility shim. The real implementation is ..\..\setup.ps1.

.DESCRIPTION
    This script used to be a second copy of the oh-my-posh setup. It wrote the
    Windows PowerShell 5.1 profile while initialising `pwsh` (so neither shell
    was themed), overwrote oh-my-posh's shipped theme in Program Files, and
    guarded itself with a Select-String regex built from a string full of regex
    metacharacters. All of that is fixed in the main entry point, so this now
    forwards rather than diverging again.
#>
[CmdletBinding()]
param([switch]$DryRun)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Write-Host 'Windows\powershell\setup_ohmypsh.ps1 is deprecated; running setup.ps1 instead.'
Write-Host ''

$argsList = @('-Only', 'oh-my-posh', '-Yes')
if ($DryRun) { $argsList += '-DryRun' }
& (Join-Path $root 'setup.ps1') @argsList
exit $LASTEXITCODE
