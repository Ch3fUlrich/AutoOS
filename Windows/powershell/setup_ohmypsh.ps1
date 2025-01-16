# Ensure script runs with administrative privileges
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "This script must be run as Administrator!" -ForegroundColor Red
    exit 1
}

# Define relative paths
$scriptPath = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
$themeSource = Join-Path -Path $scriptPath -ChildPath "..\Terminal\oh-my-posh\theme\powerlevel10k_rainbow_env.omp.json"
$themeDestination = "C:\Program Files (x86)\oh-my-posh\themes\powerlevel10k_rainbow.omp.json"
$profilePath = "$HOME\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1"

# 1. Ensure PowerShell profile exists
if (-not (Test-Path $profilePath)) {
    New-Item -Path $profilePath -ItemType File -Force
}

# 2. Add Oh My Posh initialization to $PROFILE
$ohMyPoshLine = 'oh-my-posh init pwsh --config "$env:POSH_THEMES_PATH/powerlevel10k_rainbow.omp.json" | Invoke-Expression'
if (-not (Get-Content $profilePath | Select-String -Pattern $ohMyPoshLine)) {
    Add-Content -Path $profilePath -Value $ohMyPoshLine
}

# 3. Copy custom theme to Oh My Posh themes folder
Copy-Item -Path $themeSource -Destination $themeDestination -Force

# 4. Install PSReadLine module
Install-Module -Name PSReadLine -Scope CurrentUser -Force

# 5. Set execution policy to RemoteSigned
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

Write-Host "Oh My Posh setup complete!" -ForegroundColor Green
