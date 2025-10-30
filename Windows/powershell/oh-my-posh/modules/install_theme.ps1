param()

# =============================================================================
# STEP 1: Choose & Export Oh My Posh Theme + Enable Conda Display
# =============================================================================
Write-Host "[theme] Choose and export an Oh My Posh theme (will also enable Python venv + Conda display)..." -ForegroundColor Green

$themes = @(
    @{Id=1; Name='jandedobbeleer'; Desc='Clean & minimal'},
    @{Id=2; Name='atomic'; Desc='Modern & fast'},
    @{Id=3; Name='powerlevel10k_rainbow'; Desc='Colorful & detailed'},
    @{Id=4; Name='powerlevel10k_lean'; Desc='Compact & clean'},
    @{Id=5; Name='robbyrussell'; Desc='Classic'},
    @{Id=6; Name='material'; Desc='Material Design'},
    @{Id=7; Name='paradox'; Desc='Elegant, information-rich'},
    @{Id=8; Name='sorin'; Desc='Simple & classic'},
    @{Id=9; Name='avit'; Desc='Minimal with accent'},
    @{Id=10; Name='honukai'; Desc='Dark, colorful'},
    @{Id=11; Name='powerline'; Desc='Powerline-style segments'},
    @{Id=12; Name='fishy'; Desc='Fish-inspired compact'},
    @{Id=13; Name='sequoia'; Desc='Balanced and readable'},
    @{Id=14; Name='blueish'; Desc='Cool blue palette'}
)

Write-Host "Available themes:" -ForegroundColor Cyan
foreach ($t in $themes) { Write-Host "  $($t.Id). $($t.Name) - $($t.Desc)" }

$choice = Read-Host "Enter theme number (1-$($themes.Count)) or press Enter for default"
if ($choice -match '^\d+$' -and $choice -ge 1 -and $choice -le $themes.Count) { 
    $selected = $themes[[int]$choice-1].Name 
} else { 
    $selected = 'jandedobbeleer' 
}
Write-Host "Selected: $selected" -ForegroundColor Green

$themeDir = "$HOME\.oh-my-posh"
$themeFile = "$themeDir\theme.omp.json"
New-Item -ItemType Directory -Force -Path $themeDir | Out-Null

oh-my-posh config export --config $selected --output $themeFile

# Modify JSON to ensure Python segment shows Conda env
try {
    $json = Get-Content $themeFile -Raw | ConvertFrom-Json
    if ($json -and $json.segments) {
        $modified = $false
        foreach ($seg in $json.segments) {
            if ($seg.type -ieq 'python') {
                if (-not $seg.properties) { $seg.properties = @{} }
                $seg.properties.fetch_virtual_env = $true
                $seg.properties.display_mode = 'environment'
                $modified = $true
            }
        }
        if ($modified) {
            $json | ConvertTo-Json -Depth 10 | Set-Content $themeFile -Encoding UTF8
            Write-Host "Python segment updated to show Conda envs: $themeFile" -ForegroundColor Cyan
        } else {
            Write-Host "No Python segment found in theme." -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "JSON modify failed, using regex fallback..." -ForegroundColor Yellow
    $content = Get-Content $themeFile -Raw
    $content = $content -replace '"fetch_virtual_env":\s*false', '"fetch_virtual_env": true'
    $content = $content -replace '"display_mode":\s*"[^"]*"', '"display_mode": "environment"'
    $content | Set-Content $themeFile -Encoding UTF8
    Write-Host "Theme patched via regex." -ForegroundColor Cyan
}