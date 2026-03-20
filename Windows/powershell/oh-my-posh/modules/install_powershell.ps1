param()

Write-Host "[powershell] Installing or updating PowerShell 7 (pwsh)..." -ForegroundColor Green

# Check if pwsh is already installed and get its version
$currentVersion = $null
try { $currentVersion = & pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>$null } catch {}

if ($currentVersion) {
    Write-Host "PowerShell $currentVersion detected - checking for updates..." -ForegroundColor Green
    winget upgrade Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PowerShell updated successfully." -ForegroundColor Green
    } else {
        Write-Host "Already at latest version or upgrade skipped." -ForegroundColor Cyan
    }
} else {
    Write-Host "PowerShell 7 not found - installing..." -ForegroundColor Green
    winget install Microsoft.PowerShell --source winget --scope user --accept-package-agreements --accept-source-agreements
}

# Verify
$newVersion = $null
try { $newVersion = & pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>$null } catch {}
if ($newVersion) {
    Write-Host "PowerShell $newVersion is ready at: $((Get-Command pwsh).Source)" -ForegroundColor Green
} else {
    Write-Host "Could not verify pwsh. You may need to restart your terminal." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "IMPORTANT: To get the full experience (ListView predictions, HistoryAndPlugin):" -ForegroundColor Cyan
Write-Host "  Open Windows Terminal Settings -> Startup -> Default Profile -> select 'PowerShell'" -ForegroundColor Yellow
Write-Host "  (This switches from Windows PowerShell 5.1 to PowerShell 7+)" -ForegroundColor Yellow
