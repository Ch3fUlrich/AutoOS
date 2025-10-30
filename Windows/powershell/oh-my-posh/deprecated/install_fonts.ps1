Write-Host "[ARCHIVE] Original install_fonts.ps1 moved to deprecated folder." -ForegroundColor Yellow

# Original content archived below

Write-Host "[DEPRECATED] Root-level duplicate. Running canonical script under modules/ if available..." -ForegroundColor Yellow
$module = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'modules\install_fonts.ps1'
if (Test-Path $module) { & $module } else { Write-Host "Module script not found: $module" -ForegroundColor Red }

param(
    [ValidateSet('regular','all')]
    [string]$Variant = 'regular'
)

param(
    [ValidateSet('regular','all')]
    [string]$Variant = 'regular'
)

Write-Host "[fonts] Installing MesloLGS Nerd Font (variant: $Variant)" -ForegroundColor Green

$zipUrl = 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/Meslo.zip'
$zipPath = "$env:TEMP\meslo_fonts.zip"
$extract = "$env:TEMP\MesloFonts"
$fontTarget = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"

if (-not (Test-Path $fontTarget)) { New-Item -ItemType Directory -Path $fontTarget -Force | Out-Null }

if ($Variant -eq 'regular') {
    # Single-file download if possible (fallback to zip)
    $singleUrl = 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/Meslo/MesloLGSNerdFont-Regular.ttf'
    $singleFile = "$env:TEMP\MesloLGSNerdFont-Regular.ttf"
    if (-not (Test-Path $singleFile)) {
        try { Invoke-WebRequest -Uri $singleUrl -OutFile $singleFile -ErrorAction Stop; Write-Host "Downloaded $singleFile" -ForegroundColor Green } catch { Write-Host "Single-file download failed, will try zip: $zipUrl" -ForegroundColor Yellow }
    } else { Write-Host "Font file already present: $singleFile" -ForegroundColor Yellow }

    if (Test-Path $singleFile) {
        Copy-Item -Path $singleFile -Destination "$fontTarget\$(Split-Path $singleFile -Leaf)" -Force
        Write-Host "Copied font to user fonts folder." -ForegroundColor Green
    } else {
        # fallback: download zip and extract only matching ttf
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -ErrorAction Stop
        Expand-Archive -Path $zipPath -DestinationPath $extract -Force
        Get-ChildItem $extract -Filter '*MesloLGS*Regular*.ttf' -Recurse | ForEach-Object { Copy-Item $_.FullName -Destination "$fontTarget\$($_.Name)" -Force }
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Installed regular MesloLGS fonts from zip." -ForegroundColor Green
    }
} else {
    # install all ttf variants from the zip
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -ErrorAction Stop
    Expand-Archive -Path $zipPath -DestinationPath $extract -Force
    $installed = 0
    Get-ChildItem $extract -Filter *.ttf -Recurse | ForEach-Object {
        $dest = "$fontTarget\$($_.Name)"
        if (-not (Test-Path $dest)) { Copy-Item $_.FullName -Destination $dest -Force; $installed++ }
    }
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "$installed fonts copied to $fontTarget" -ForegroundColor Green
}

Write-Host "Font install step finished. You may need to restart Windows Terminal and select 'MesloLGS NF' in your profile settings." -ForegroundColor Cyan
