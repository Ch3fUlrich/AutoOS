<#
main.ps1 - orchestrator for Oh My Posh setup

This script presents an interactive menu to install components.
#>

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Args
)

Write-Host "Oh My Posh Setup - Main Orchestrator" -ForegroundColor Green

$components = @(
    @{Id=1; Name='OhMyPosh'; Script='modules/install_ohmyposh.ps1'; Desc='Install or update Oh My Posh binary (winget)'; Help='Installs oh-my-posh using winget and verifies availability.'},
    @{Id=2; Name='Fonts'; Script='modules/install_fonts.ps1'; Desc='Install MesloLGS Nerd Font'; Help='Downloads MesloLGS NF (regular or all) and copies fonts to user fonts.'},
    @{Id=3; Name='Tools'; Script='modules/install_tools.ps1'; Desc='Install CLI tools (fzf, ripgrep)'; Help='Installs fzf and ripgrep using winget and ensures PATH has WinGet links.'},
    @{Id=4; Name='Modules'; Script='modules/install_modules.ps1'; Desc='PowerShell modules (PSReadLine, posh-git...)'; Help='Installs modules from PSGallery: PSReadLine, PSFzf, Terminal-Icons, z, posh-git.'},
    @{Id=5; Name='Theme'; Script='modules/install_theme.ps1'; Desc='Select and configure Oh My Posh theme'; Help='Export default theme, enable Python virtual env segment, and save a theme file.'},
    @{Id=6; Name='Profile'; Script='modules/configure_profile.ps1'; Desc='Configure PowerShell profile'; Help='Writes a version-aware profile that initializes oh-my-posh and modules.'}
)

Write-Host "`nAvailable components:" -ForegroundColor Cyan
foreach ($c in $components) { Write-Host "  $($c.Id). $($c.Name) - $($c.Desc)" }
Write-Host "`nCommands: enter numbers separated by comma (e.g. 1,4), '0' or 'all' to run everything. Enter 'info N' to learn more about component N." -ForegroundColor Yellow

# Allow non-interactive invocation via arguments, e.g. --all or 1,2
if ($Args -and $Args.Count -gt 0) {
    $input = ($Args -join ' ')
} else {
    $input = Read-Host "Your choice"
}

if ($input -match '^info\s+(\d+)$') {
    $n = [int]$matches[1]
    $comp = $components | Where-Object { $_.Id -eq $n }
    if ($comp) { Write-Host "Info: $($comp.Name) - $($comp.Help)" -ForegroundColor Magenta } else { Write-Host "Unknown component number." -ForegroundColor Red }
    exit 0
}

if ($input -match '^(all|0|--all|-all)$') { $toRun = $components } else {
    $idxs = $input -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ }
    $toRun = $components | Where-Object { $idxs -contains $_.Id }
}

if (-not $toRun -or $toRun.Count -eq 0) { Write-Host "Nothing selected, exiting." -ForegroundColor Yellow; exit 0 }

# If the profile component will be run, execute the fixer first to repair malformed profiles
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if ($toRun | Where-Object { $_.Name -eq 'Profile' }) {
    $fixer = Join-Path $scriptDir 'fix_profile.ps1'
    if (Test-Path $fixer) {
        Write-Host "Profile task detected - running profile fixer before configuring profile..." -ForegroundColor Cyan
        try { & $fixer } catch { Write-Host "Profile fixer failed: $_" -ForegroundColor Yellow }
    } else { Write-Host "Profile fixer not found: $fixer" -ForegroundColor Yellow }
}

foreach ($comp in $toRun) {
    Write-Host "`n=== Running: $($comp.Name) ===" -ForegroundColor Green
    $scriptPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) $comp.Script
    if (Test-Path $scriptPath) {
        try {
            & $scriptPath
            Write-Host "Completed: $($comp.Name)" -ForegroundColor Cyan
        } catch {
            Write-Host "Error running $($comp.Name): $_" -ForegroundColor Red
        }
    } else {
        Write-Host "Script not found: $scriptPath" -ForegroundColor Red
    }
}

Write-Host "`nAll selected tasks finished. Review output for any errors." -ForegroundColor Green
Write-Host "If you installed fonts, open Windows Terminal settings and set profile font to 'MesloLGS NF', then restart." -ForegroundColor Cyan
