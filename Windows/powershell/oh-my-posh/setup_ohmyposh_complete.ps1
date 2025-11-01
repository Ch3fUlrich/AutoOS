# ========================================================================
# Oh My Posh + Nerd Font + Productivity Setup (v2.1)
# ------------------------------------------------------------------------
# Fully automatic: Oh My Posh, MesloLGS NF, fzf, ripgrep, PSReadLine, PSFzf, z, posh-git
# Version-aware PSReadLine config (2.x & 3.x compatible)
# Safe profile update, UTF-8 + ANSI support
# ========================================================================

# UTF-8 + ANSI for icons/emojis
chcp 65001 | Out-Null
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSStyle.OutputRendering = 'Ansi'
}

# Prevent admin
if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Please run as regular user, not Administrator." -ForegroundColor Yellow
    exit 1
}

function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }

# ------------------------------------------------------------------------
# 1. Install Oh My Posh
# ------------------------------------------------------------------------
if (-not (Test-Command "oh-my-posh")) {
    Write-Host "Installing Oh My Posh..." -ForegroundColor Green
    winget install JanDeDobbeleer.OhMyPosh --source winget --scope user --force --accept-package-agreements --accept-source-agreements --silent
    if (-not (Test-Command "oh-my-posh")) { Write-Host "Failed to install Oh My Posh." -ForegroundColor Red; exit 1 }
} else {
    Write-Host "Oh My Posh already installed." -ForegroundColor Green
}

# ------------------------------------------------------------------------
# 2. Install MesloLGS NF (Latest v3.4.0 - All Variants)
# ------------------------------------------------------------------------
$fontUrl = "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/Meslo.zip"
$zipPath = "$env:TEMP\Meslo.zip"
$extractPath = "$env:TEMP\MesloFonts"
$fontTarget = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"

if (-not (Test-Path "$fontTarget\MesloLGSNerdFont-Regular.ttf")) {
    Write-Host "Downloading MesloLGS NF (v3.4.0)..." -ForegroundColor Green
    Invoke-WebRequest -Uri $fontUrl -OutFile $zipPath -ErrorAction Stop
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

    $installed = 0
    Get-ChildItem "$extractPath\*.ttf" | ForEach-Object {
        $dest = "$fontTarget\$($_.Name)"
        if (-not (Test-Path $dest)) {
            Copy-Item $_.FullName -Destination $dest -Force
            $installed++
        }
    }
    Write-Host "$installed font variants installed to user folder." -ForegroundColor Green

    Remove-Item $zipPath, $extractPath -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "MesloLGS NF already installed." -ForegroundColor Green
}

# ------------------------------------------------------------------------
# 3. Install fzf + ripgrep
# ------------------------------------------------------------------------
$tools = @(
    @{Name="junegunn.fzf"; Cmd="fzf"},
    @{Name="BurntSushi.ripgrep.MSVC"; Cmd="rg"}
)
foreach ($t in $tools) {
    if (-not (Test-Command $t.Cmd)) {
        Write-Host "Installing $($t.Name)..." -ForegroundColor Green
        winget install $t.Name --source winget --scope user --force --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to install $($t.Name)" -ForegroundColor Red
        } elseif (-not (Test-Command $t.Cmd)) {
            Write-Host "$($t.Cmd) not found in PATH after install. Adding WinGet Links to PATH." -ForegroundColor Yellow
            $env:PATH += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"
        }
    } else {
        Write-Host "$($t.Cmd) already installed." -ForegroundColor Green
    }
}

# ------------------------------------------------------------------------
# 4. Execution Policy
# ------------------------------------------------------------------------
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

# ------------------------------------------------------------------------
# 5. Ensure profile exists
# ------------------------------------------------------------------------
$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) {
    New-Item -Path $profilePath -ItemType File -Force | Out-Null
    Write-Host "Created profile: $profilePath" -ForegroundColor Green
}

# ------------------------------------------------------------------------
# 6. Choose Theme
# ------------------------------------------------------------------------
$themes = @(
    @{Name="jandedobbeleer"; Desc="Clean & minimal"},
    @{Name="atomic"; Desc="Modern & fast"},
    @{Name="powerlevel10k_rainbow"; Desc="Colorful & detailed"},
    @{Name="powerlevel10k_lean"; Desc="Compact & clean"},
    @{Name="robbyrussell"; Desc="Classic"},
    @{Name="material"; Desc="Material Design"}
)
Write-Host "`nChoose a theme:" -ForegroundColor Green
for ($i=0; $i -lt $themes.Count; $i++) {
    Write-Host "  $($i+1). $($themes[$i].Name) - $($themes[$i].Desc)" -ForegroundColor Cyan
}
$choice = Read-Host "Enter number (1-$($themes.Count)) or press Enter for default"
$selected = if ($choice -match '^\d+$' -and [int]$choice -ge 1 -and [int]$choice -le $themes.Count) {
    $themes[[int]$choice-1].Name
} else { "jandedobbeleer" }
Write-Host "Selected theme: $selected" -ForegroundColor Green

# ------------------------------------------------------------------------
# 7. Export & modify theme (show Python venv)
# ------------------------------------------------------------------------
$themeDir = "$HOME\.oh-my-posh"
$themeFile = "$themeDir\theme.omp.json"
New-Item -ItemType Directory -Force -Path $themeDir | Out-Null
oh-my-posh config export --config $selected --output $themeFile

$content = Get-Content $themeFile -Raw
$content = $content -replace '"fetch_virtual_env":\s*false', '"fetch_virtual_env": true'
$content = $content -replace '"display_mode":\s*"files"', '"display_mode": "environment"'
$content | Set-Content $themeFile -Encoding UTF8
Write-Host "Theme saved: $themeFile" -ForegroundColor Green

# ------------------------------------------------------------------------
# 8. Install Modules
# ------------------------------------------------------------------------
$modules = @("Terminal-Icons", "PSReadLine", "z", "PSFzf", "posh-git")
foreach ($m in $modules) {
    if (-not (Get-Module -ListAvailable -Name $m)) {
        Write-Host "Installing $m..." -ForegroundColor Green
        Install-Module -Name $m -Scope CurrentUser -Force -AllowClobber -Repository PSGallery
    } else {
        Write-Host "$m already installed." -ForegroundColor Green
    }
}

# ------------------------------------------------------------------------
# 9. Build final profile content (with version-aware PSReadLine)
# ------------------------------------------------------------------------
$psReadLineConfig = @'
# === PSReadLine Settings (Version-aware) ===
$psrl = Get-Module -ListAvailable PSReadLine | Sort-Object Version -Descending | Select-Object -First 1
if ($psrl) {
    $ver = $psrl.Version
    Write-Host "PSReadLine v$ver detected" -ForegroundColor Cyan

    # Prediction
    if ($ver.Major -ge 3 -or ($ver.Major -eq 2 -and $ver.Minor -ge 2)) {
        Set-PSReadLineOption -PredictionSource HistoryAndPlugin -ErrorAction SilentlyContinue
        Set-PSReadLineOption -PredictionViewStyle ListView -ErrorAction SilentlyContinue
    } elseif ($ver.Major -eq 2 -and $ver.Minor -eq 1) {
        Set-PSReadLineOption -HistoryBasedPrediction $true -ErrorAction SilentlyContinue
    }

    Set-PSReadLineOption -EditMode Windows -ErrorAction SilentlyContinue

    # Colors (ANSI only in 3.x, fallback to ConsoleColor)
    if ($ver.Major -ge 3) {
        Set-PSReadLineOption -Colors @{
            Command   = "`e[38;5;81m"
            Parameter = "`e[38;5;117m"
            Operator  = "`e[38;5;221m"
            String    = "`e[38;5;156m"
            Variable  = "`e[38;5;141m"
        } -ErrorAction SilentlyContinue
    } elseif ($ver.Major -eq 2 -and $ver.Minor -ge 2) {
        Set-PSReadLineOption -Colors @{
            Command   = 'Cyan'; Parameter = 'Blue'; Operator = 'Yellow'; String = 'Green'; Variable = 'Magenta'
        } -ErrorAction SilentlyContinue
    }
}
'@

$fzfConfig = @'
# === PSFzf (FZF Integration) ===
if (Get-Command fzf -ErrorAction SilentlyContinue) {
    Set-PsFzfOption -PSReadlineChordProvider 'Ctrl+f'
    Set-PsFzfOption -PSReadlineChordReverseHistory 'Ctrl+r'
    Set-PsFzfOption -GitKey 'Ctrl+g'
}
'@

$profileContent = @"
# === Oh My Posh ===
oh-my-posh init pwsh --config '$themeFile' | Invoke-Expression

# === PATH for WinGet tools ===
`$env:PATH += ";`$env:LOCALAPPDATA\Microsoft\WinGet\Links"

# === Modules ===
Import-Module Terminal-Icons
Import-Module z
Import-Module PSReadLine
Import-Module PSFzf
Import-Module posh-git

$psReadLineConfig

$fzfConfig
"@

# ------------------------------------------------------------------------
# 10. Update profile (replace entirely for safety)
# ------------------------------------------------------------------------
$profileContent.Split("`n") | Where-Object { $_.Trim() -ne "" } | Set-Content $profilePath -Encoding UTF8
Write-Host "Profile updated: $profilePath" -ForegroundColor Green

# ------------------------------------------------------------------------
# 11. Final check
# ------------------------------------------------------------------------
try {
    oh-my-posh print primary --config $themeFile | Out-Null
    Write-Host "Theme verified!" -ForegroundColor Green
} catch { Write-Host "Theme check failed (non-critical)." -ForegroundColor Yellow }

# ------------------------------------------------------------------------
# Done!
# ------------------------------------------------------------------------
Write-Host "`nSetup complete!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "   1. In Windows Terminal → Settings → Profiles → Defaults → Font face → 'MesloLGS NF'"
Write-Host "   2. Restart PowerShell or run: . `$PROFILE"
Write-Host "   3. Use Ctrl+F (files), Ctrl+R (history), Ctrl+G (git)"
Write-Host "`nEnjoy your new shell!"