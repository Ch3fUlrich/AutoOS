# Complete Oh My Posh Setup Script for Windows
# This script installs Oh My Posh, sets up fonts, configures PowerShell profile,
# and ensures Python conda/venv environments are displayed in the prompt.

# Set console to UTF-8 for proper symbol display
chcp 65001 | Out-Null

# Enable ANSI rendering for PowerShell 7+
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSStyle.OutputRendering = 'Ansi'
}

# Check if running as administrator (not required for user installation)
if ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent().IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "Please run this script as a regular user, not as Administrator." -ForegroundColor Yellow
    exit 1
}

# Function to check if a command exists
function Test-Command($cmdname) {
    return [bool](Get-Command -Name $cmdname -ErrorAction SilentlyContinue)
}

# 1. Install Oh My Posh if not already installed
if (-not (Test-Command "oh-my-posh")) {
    Write-Host "Installing Oh My Posh..." -ForegroundColor Green
    try {
        winget install JanDeDobbeleer.OhMyPosh --source winget --scope user --force
        Write-Host "Oh My Posh installed successfully." -ForegroundColor Green
    } catch {
        Write-Host "Failed to install Oh My Posh. Please install it manually." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Oh My Posh is already installed." -ForegroundColor Green
}

# 2. Download Meslo Nerd Font (Regular only)
Write-Host "Downloading MesloLGS Nerd Font (Regular)..." -ForegroundColor Green
$fontUrl = "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.0.2/Meslo/MesloLGSNerdFont-Regular.ttf"
$fontFile = "$env:TEMP\MesloLGSNerdFont-Regular.ttf"

if (-not (Test-Path $fontFile)) {
    try {
        Invoke-WebRequest -Uri $fontUrl -OutFile $fontFile -ErrorAction Stop
        Write-Host "Font downloaded to $fontFile" -ForegroundColor Green
    } catch {
        Write-Host "Failed to download font. You can download it manually from $fontUrl" -ForegroundColor Yellow
    }
} else {
    Write-Host "Font file already exists at $fontFile" -ForegroundColor Yellow
}

if (Test-Path $fontFile) {
    Write-Host "Please install the font manually by double-clicking $fontFile and clicking Install." -ForegroundColor Cyan
} else {
    Write-Host "Font not available. Please install a Nerd Font manually." -ForegroundColor Yellow
}

# 3. Set execution policy
Write-Host "Setting execution policy..." -ForegroundColor Green
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

# 4. Ensure PowerShell profile exists
$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) {
    Write-Host "Creating PowerShell profile..." -ForegroundColor Green
    New-Item -Path $profilePath -ItemType File -Force
}

# 5. Let user choose a theme
Write-Host "Available Oh My Posh themes:" -ForegroundColor Green
$themes = @(
    @{Name="jandedobbeleer"; Description="Clean and minimal theme with essential segments (default)"},
    @{Name="powerlevel10k_rainbow"; Description="Colorful theme with rainbow colors and many segments"},
    @{Name="powerlevel10k_lean"; Description="Lean version of powerlevel10k with fewer segments"},
    @{Name="atomic"; Description="Modern atomic theme with clean design"},
    @{Name="robbyrussell"; Description="Simple theme inspired by Oh My Zsh"},
    @{Name="agnoster"; Description="Classic agnoster theme with angled separators"},
    @{Name="paradox"; Description="Paradox theme with unique styling"},
    @{Name="material"; Description="Material design inspired theme"}
)

for ($i = 0; $i -lt $themes.Count; $i++) {
    Write-Host "$($i+1). $($themes[$i].Name) - $($themes[$i].Description)" -ForegroundColor Cyan
}

$choice = Read-Host "Choose a theme (1-$($themes.Count), or press Enter for default)"
if ($choice -match '^\d+$' -and $choice -ge 1 -and $choice -le $themes.Count) {
    $selectedTheme = $themes[$choice-1].Name
    Write-Host "Selected theme: $selectedTheme" -ForegroundColor Green
} else {
    $selectedTheme = "jandedobbeleer"
    Write-Host "Using default theme: $selectedTheme" -ForegroundColor Green
}

# Create modified theme
$themeDest = "$HOME\.oh-my-posh\theme.omp.json"
$themeDir = Split-Path $themeDest -Parent

if (-not (Test-Path $themeDir)) {
    New-Item -ItemType Directory -Path $themeDir -Force
}

# Export the selected theme
oh-my-posh config export --config $selectedTheme --output $themeDest

# Modify the theme to enable Python virtual environment display using string replacement
$content = Get-Content $themeDest -Raw
$content = $content -replace '"fetch_virtual_env": false', '"fetch_virtual_env": true'
$content = $content -replace '"display_mode": "files"', '"display_mode": "environment"'
$content = $content -replace '\{\{ if \.Error \}\}\{\{ \.Error \}\}\{\{ else \}\}\{\{ \.Full \}\}\{\{ end \}\}', '{{ if .Venv }}{{ .Venv }}{{ else }}{{ .Full }}{{ end }}'
$content | Set-Content $themeDest
Write-Host "Modified theme created at $themeDest" -ForegroundColor Green

# 6. Add Oh My Posh initialization to profile
$initCmd = "oh-my-posh init pwsh --config '$themeDest' | Invoke-Expression"

# Read current profile content, filter out any oh-my-posh related lines, and add the new init
$content = Get-Content $profilePath -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch "oh-my-posh" -and $_ -notmatch "Invoke-Expression" }
$content += $initCmd
$content | Set-Content $profilePath
Write-Host "Oh My Posh initialization added to profile." -ForegroundColor Green

# 7. Install PSReadLine module if not present and configure it
if (-not (Get-Module -ListAvailable -Name PSReadLine)) {
    Write-Host "Installing PSReadLine module..." -ForegroundColor Green
    Install-Module -Name PSReadLine -Scope CurrentUser -Force
} else {
    Write-Host "PSReadLine is already installed." -ForegroundColor Green
}

# Configure PSReadLine for better autocomplete and features
Write-Host "Configuring PSReadLine..." -ForegroundColor Green
$psReadLineConfig = @"
Set-PSReadLineOption -EditMode Emacs
Set-PSReadLineOption -PredictionSource History
Set-PSReadLineKeyHandler -Key UpArrow -Function HistorySearchBackward
Set-PSReadLineKeyHandler -Key DownArrow -Function HistorySearchForward
Set-PSReadLineKeyHandler -Key Tab -Function Complete
"@

# Remove any existing PSReadLine configuration and add the new one
$content = Get-Content $profilePath -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch "Set-PSReadLineOption" }
$content += $psReadLineConfig
$content | Set-Content $profilePath
Write-Host "PSReadLine configured." -ForegroundColor Green

# 8. Reload profile
Write-Host "Reloading PowerShell profile..." -ForegroundColor Green
try {
    . $profilePath
    Write-Host "Profile reloaded successfully." -ForegroundColor Green
} catch {
    Write-Host "Failed to reload profile. Please restart your PowerShell session." -ForegroundColor Yellow
}

# 9. Test the setup
Write-Host "Testing Oh My Posh setup..." -ForegroundColor Green

try {
    $version = oh-my-posh --version 2>$null
    Write-Host "Oh My Posh version: $version" -ForegroundColor Green
} catch {
    Write-Host "Failed to get Oh My Posh version. Installation may have failed." -ForegroundColor Red
    exit 1
}

try {
    oh-my-posh print primary --config $themeDest 2>$null | Out-Null
    Write-Host "Theme loaded successfully." -ForegroundColor Green
} catch {
    Write-Host "Failed to load theme. Attempting to recreate..." -ForegroundColor Yellow
    try {
        # Recreate the theme
        oh-my-posh config export --config $selectedTheme --output $themeDest 2>$null
        $content = Get-Content $themeDest -Raw
        $content = $content -replace '"fetch_virtual_env": false', '"fetch_virtual_env": true'
        $content = $content -replace '"display_mode": "files"', '"display_mode": "environment"'
        $content = $content -replace '\{\{ if \.Error \}\}\{\{ \.Error \}\}\{\{ else \}\}\{\{ \.Full \}\}\{\{ end \}\}', '{{ if .Venv }}{{ .Venv }}{{ else }}{{ .Full }}{{ end }}'
        $content | Set-Content $themeDest
        Write-Host "Theme recreated successfully." -ForegroundColor Green
    } catch {
        Write-Host "Failed to recreate theme. Using default theme." -ForegroundColor Yellow
        # Fallback to default theme
        $initCmd = "oh-my-posh init pwsh | Invoke-Expression"
        $content = Get-Content $profilePath | Where-Object { $_ -notmatch "oh-my-posh" -and $_ -notmatch "Invoke-Expression" }
        $content += $initCmd
        $content | Set-Content $profilePath
        Write-Host "Switched to default theme." -ForegroundColor Green
    }
}

Write-Host "`nSetup complete!" -ForegroundColor Green
Write-Host "Please set 'MesloLGS NF' as your terminal font in Windows Terminal settings." -ForegroundColor Cyan
Write-Host "Restart your PowerShell session or run '. `$PROFILE' to apply changes." -ForegroundColor Cyan
Write-Host "The prompt should now display Python conda/venv environments when activated." -ForegroundColor Cyan