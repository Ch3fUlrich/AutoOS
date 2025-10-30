Write-Host "[ARCHIVE] Original install_modules.ps1 moved to deprecated folder." -ForegroundColor Yellow

# Original content archived below

param()

Write-Host "[modules] Installing PowerShell modules (Terminal-Icons, PSReadLine, PSFzf, z, posh-git)..." -ForegroundColor Green

$modules = @(
    @{Name='Terminal-Icons'; Description='Pretty file icons for the shell'},
    @{Name='PSReadLine'; Description='Improved command-line editing and history'},
    @{Name='PSFzf'; Description='FZF integration for PowerShell'},
    @{Name='z'; Description='Directory jumper (posh-z) for fast navigation'},
    @{Name='posh-git'; Description='Git prompt and tab completion'}
)

Write-Host "[DEPRECATED] Root-level duplicate. Running canonical script under modules/ if available..." -ForegroundColor Yellow
$module = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'modules\install_modules.ps1'
if (Test-Path $module) { & $module } else { Write-Host "Module script not found: $module" -ForegroundColor Red }

foreach ($m in $modules) {
    if (-not (Get-Module -ListAvailable -Name $m.Name)) {
        Write-Host "Installing module: $($m.Name) - $($m.Description)" -ForegroundColor Green
        try { Install-Module -Name $m.Name -Scope CurrentUser -Force -AllowClobber -Repository PSGallery -ErrorAction Stop }
        catch { Write-Host "Failed to install $($m.Name): $_" -ForegroundColor Yellow }
    } else { Write-Host "$($m.Name) already available." -ForegroundColor Green }
}

Write-Host "Modules installation complete." -ForegroundColor Cyan
