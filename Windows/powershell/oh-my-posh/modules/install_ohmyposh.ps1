param()

Write-Host "[oh-my-posh] Installing or verifying Oh My Posh..." -ForegroundColor Green
function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }

if (-not (Test-Command "oh-my-posh")) {
    Write-Host "oh-my-posh not found - installing via winget..." -ForegroundColor Green
    try {
        winget install JanDeDobbeleer.OhMyPosh --source winget --scope user --force --accept-package-agreements --accept-source-agreements
    } catch {
        Write-Host "winget install failed: $_" -ForegroundColor Yellow
    }
    if (-not (Test-Command "oh-my-posh")) {
        Write-Host "oh-my-posh could not be installed automatically. Please install it manually." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "oh-my-posh already installed." -ForegroundColor Green
}

$ver = $null
try { $ver = (oh-my-posh --version) 2>$null } catch { }
Write-Host "oh-my-posh ready: $ver" -ForegroundColor Cyan
