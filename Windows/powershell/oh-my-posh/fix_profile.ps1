param(
    [switch]$WhatIf
)

Write-Host "Fixing PowerShell profile: backup -> clean -> regenerate using modules/configure_profile.ps1" -ForegroundColor Green

$profilePath = $PROFILE
if (-not $profilePath) { Write-Host "Could not determine $PROFILE. Run inside PowerShell." -ForegroundColor Red; exit 1 }

if (-not (Test-Path $profilePath)) {
    Write-Host "Profile not found, creating: $profilePath" -ForegroundColor Yellow
    if (-not $WhatIf) { New-Item -Path $profilePath -ItemType File -Force | Out-Null }
}

$backup = "$profilePath.bak"
Write-Host "Backing up existing profile to: $backup" -ForegroundColor Cyan
if (-not $WhatIf) { Copy-Item -Path $profilePath -Destination $backup -Force -ErrorAction SilentlyContinue }

Write-Host "Removing malformed lines (lines starting with '=') from profile..." -ForegroundColor Cyan
$lines = @()
if (Test-Path $profilePath) { $lines = Get-Content -Path $profilePath -ErrorAction SilentlyContinue }
$clean = $lines | Where-Object { -not ($_ -match '^[\s]*=') }

if (-not $WhatIf) { $clean | Set-Content -Path $profilePath -Encoding UTF8 }

Write-Host "Regenerating profile via modules/configure_profile.ps1 (this will overwrite profile with canonical content)." -ForegroundColor Green
$module = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'modules\configure_profile.ps1'
if (Test-Path $module) {
    if ($WhatIf) { Write-Host "Would run: $module" -ForegroundColor Yellow } else { & $module }
} else {
    Write-Host "Module writer not found: $module. You can manually edit $profilePath to initialize oh-my-posh." -ForegroundColor Red
}

Write-Host "Done. Restart PowerShell to apply changes." -ForegroundColor Cyan
