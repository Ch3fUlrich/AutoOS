Write-Host "[ARCHIVE] Original install_ohmyposh.ps1 moved to deprecated folder." -ForegroundColor Yellow

param()

Write-Host "[oh-my-posh] Installing or verifying Oh My Posh..." -ForegroundColor Green
function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }

Write-Host "[DEPRECATED] Root-level duplicate. Running canonical script under modules/ if available..." -ForegroundColor Yellow
$module = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'modules\install_ohmyposh.ps1'
if (Test-Path $module) { & $module } else { Write-Host "Module script not found: $module" -ForegroundColor Red }
