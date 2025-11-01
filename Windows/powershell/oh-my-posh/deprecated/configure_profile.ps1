Write-Host "[ARCHIVE] Original configure_profile.ps1 moved to deprecated folder." -ForegroundColor Yellow

# Original content archived below

Write-Host "[DEPRECATED] Root-level duplicate. Running canonical script under modules/ if available..." -ForegroundColor Yellow
$module = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'modules\configure_profile.ps1'
if (Test-Path $module) { & $module } else { Write-Host "Module script not found: $module" -ForegroundColor Red }
