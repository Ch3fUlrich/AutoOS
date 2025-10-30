param(
    [string]$ThemeFile = "$HOME\.oh-my-posh\theme.omp.json"
)

Write-Host "[profile] Creating PowerShell profile and wiring Oh My Posh + modules..." -ForegroundColor Green

$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) { New-Item -Path $profilePath -ItemType File -Force | Out-Null }

# PSReadLine version-aware settings and safe imports
$psReadLineConfig = @'
# PSReadLine settings (auto-detect version)
$psrl = Get-Module -ListAvailable PSReadLine | Sort-Object Version -Descending | Select-Object -First 1
if ($psrl) {
    $ver = $psrl.Version
    if ($ver.Major -ge 3 -or ($ver.Major -eq 2 -and $ver.Minor -ge 2)) {
        Set-PSReadLineOption -PredictionSource HistoryAndPlugin -ErrorAction SilentlyContinue
        Set-PSReadLineOption -PredictionViewStyle ListView -ErrorAction SilentlyContinue
    } elseif ($ver.Major -eq 2 -and $ver.Minor -eq 1) {
        Set-PSReadLineOption -HistoryBasedPrediction $true -ErrorAction SilentlyContinue
    }
    Set-PSReadLineOption -EditMode Windows -ErrorAction SilentlyContinue
}
'@

$fzfConfig = @'
# PSFzf key bindings
if (Get-Command fzf -ErrorAction SilentlyContinue) {
    Set-PsFzfOption -PSReadlineChordProvider 'Ctrl+f' -ErrorAction SilentlyContinue
    Set-PsFzfOption -PSReadlineChordReverseHistory 'Ctrl+r' -ErrorAction SilentlyContinue
    Set-PsFzfOption -GitKey 'Ctrl+g' -ErrorAction SilentlyContinue
}
'@

# Build profile as an array of lines to avoid unintended variable expansion
$profileLines = @()
$profileLines += '# === Oh My Posh ==='
$profileLines += "oh-my-posh init pwsh --config '$ThemeFile' | Invoke-Expression"
$profileLines += ''
$profileLines += '# === PATH for WinGet tools ==='
$profileLines += '$env:PATH += ";" + $env:LOCALAPPDATA + "\Microsoft\WinGet\Links"'
$profileLines += ''
$profileLines += '# === Modules ==='
$profileLines += 'Import-Module Terminal-Icons -ErrorAction SilentlyContinue'
$profileLines += 'Import-Module z -ErrorAction SilentlyContinue'
$profileLines += 'Import-Module PSReadLine -ErrorAction SilentlyContinue'
$profileLines += 'Import-Module PSFzf -ErrorAction SilentlyContinue'
$profileLines += 'Import-Module posh-git -ErrorAction SilentlyContinue'
$profileLines += ''
# Append PSReadLine and fzf config blocks as lines
$profileLines += ($psReadLineConfig -split "`n")
$profileLines += ''
$profileLines += ($fzfConfig -split "`n")

Write-Host "Writing profile to $profilePath" -ForegroundColor Green
$profileLines | Where-Object { $_.Trim() -ne '' } | Set-Content $profilePath -Encoding UTF8

Write-Host "Profile configured. To apply: restart PowerShell or run: . `$PROFILE" -ForegroundColor Cyan
