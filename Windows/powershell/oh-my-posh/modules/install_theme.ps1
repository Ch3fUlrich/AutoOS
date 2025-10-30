param()

Write-Host "[theme] Choose and export an Oh My Posh theme (will also enable Python venv display)..." -ForegroundColor Green

$themes = @(
    @{Id=1; Name='jandedobbeleer'; Desc='Clean & minimal'},
    @{Id=2; Name='atomic'; Desc='Modern & fast'},
    @{Id=3; Name='powerlevel10k_rainbow'; Desc='Colorful & detailed'},
    @{Id=4; Name='powerlevel10k_lean'; Desc='Compact & clean'},
    @{Id=5; Name='robbyrussell'; Desc='Classic'},
    @{Id=6; Name='material'; Desc='Material Design'}
)

Write-Host "Available themes:" -ForegroundColor Cyan
foreach ($t in $themes) { Write-Host "  $($t.Id). $($t.Name) - $($t.Desc)" }

$choice = Read-Host "Enter theme number (1-$($themes.Count)) or press Enter for default"
if ($choice -match '^\d+$' -and $choice -ge 1 -and $choice -le $themes.Count) { $selected = $themes[[int]$choice-1].Name } else { $selected = 'jandedobbeleer' }
Write-Host "Selected: $selected" -ForegroundColor Green

$themeDir = "$HOME\.oh-my-posh"
$themeFile = "$themeDir\theme.omp.json"
New-Item -ItemType Directory -Force -Path $themeDir | Out-Null

oh-my-posh config export --config $selected --output $themeFile

$content = Get-Content $themeFile -Raw
$content = $content -replace '"fetch_virtual_env":\s*false', '"fetch_virtual_env": true'
$content = $content -replace '"display_mode":\s*"files"', '"display_mode": "environment"'
$content | Set-Content $themeFile -Encoding UTF8

Write-Host "Theme exported and modified: $themeFile" -ForegroundColor Cyan
