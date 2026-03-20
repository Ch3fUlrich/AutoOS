param()

Write-Host "[modules] Installing PowerShell modules (Terminal-Icons, PSReadLine, PSFzf, z, posh-git)..." -ForegroundColor Green

# PSReadLine needs special version handling: >= 2.2 required for ListView completion and HistoryAndPlugin prediction
$psrlInstalled = Get-Module -ListAvailable PSReadLine | Sort-Object Version -Descending | Select-Object -First 1
if (-not $psrlInstalled -or $psrlInstalled.Version -lt [Version]'2.2.0') {
    Write-Host "Installing/upgrading PSReadLine (requires >= 2.2 for ListView completion)..." -ForegroundColor Green
    try { Install-Module -Name PSReadLine -Scope CurrentUser -Force -AllowClobber -Repository PSGallery -ErrorAction Stop }
    catch { Write-Host "Failed to install PSReadLine: $_" -ForegroundColor Yellow }
} else {
    Write-Host "PSReadLine $($psrlInstalled.Version) already up to date." -ForegroundColor Green
}

$modules = @(
    @{Name='Terminal-Icons'; Description='Pretty file icons for the shell'},
    @{Name='PSFzf'; Description='FZF integration for PowerShell'},
    @{Name='z'; Description='Directory jumper (posh-z) for fast navigation'},
    @{Name='posh-git'; Description='Git prompt and tab completion'}
)

foreach ($m in $modules) {
    if (-not (Get-Module -ListAvailable -Name $m.Name)) {
        Write-Host "Installing module: $($m.Name) - $($m.Description)" -ForegroundColor Green
        try { Install-Module -Name $m.Name -Scope CurrentUser -Force -AllowClobber -Repository PSGallery -ErrorAction Stop }
        catch { Write-Host "Failed to install $($m.Name): $_" -ForegroundColor Yellow }
    } else { Write-Host "$($m.Name) already available." -ForegroundColor Green }
}

Write-Host "Modules installation complete." -ForegroundColor Cyan
