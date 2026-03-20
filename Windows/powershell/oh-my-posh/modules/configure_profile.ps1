param(
    [string]$ThemeFile = "$HOME\.oh-my-posh\theme.omp.json"
)

Write-Host "[profile] Creating PowerShell profile and wiring Oh My Posh + modules..." -ForegroundColor Green

$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) { 
    New-Item -Path $profilePath -ItemType File -Force | Out-Null 
}

# PSReadLine config (version-aware; requires >= 2.2 for ListView)
$psReadLineConfig = @'
# === PSReadLine: ListView completion and history prediction ===
$psrl = Get-Module PSReadLine
if ($psrl) {
    # EditMode must come first - it resets all keybindings
    Set-PSReadLineOption -EditMode Windows -ErrorAction SilentlyContinue

    if ($psrl.Version -ge [Version]'2.2.0') {
        # HistoryAndPlugin requires PowerShell 7.2+ (not available in Windows PowerShell 5.1)
        if ($PSVersionTable.PSVersion -ge [Version]'7.2') {
            Set-PSReadLineOption -PredictionSource HistoryAndPlugin -ErrorAction SilentlyContinue
        } else {
            Set-PSReadLineOption -PredictionSource History -ErrorAction SilentlyContinue
        }
        Set-PSReadLineOption -PredictionViewStyle ListView -ErrorAction SilentlyContinue
    } elseif ($psrl.Version -ge [Version]'2.1.0') {
        Set-PSReadLineOption -PredictionSource History -ErrorAction SilentlyContinue
        Set-PSReadLineOption -PredictionViewStyle ListView -ErrorAction SilentlyContinue
    }

    # Tab / Ctrl+Space: popup completion menu; Shift+Tab: cycle backwards
    Set-PSReadLineKeyHandler -Key Tab           -Function MenuComplete         -ErrorAction SilentlyContinue
    Set-PSReadLineKeyHandler -Key 'Ctrl+Spacebar' -Function MenuComplete         -ErrorAction SilentlyContinue
    Set-PSReadLineKeyHandler -Key 'Shift+Tab'   -Function TabCompletePrevious  -ErrorAction SilentlyContinue

    # Arrow keys: prefix-aware history search
    Set-PSReadLineKeyHandler -Key UpArrow   -Function HistorySearchBackward -ErrorAction SilentlyContinue
    Set-PSReadLineKeyHandler -Key DownArrow -Function HistorySearchForward  -ErrorAction SilentlyContinue

    # Completion menu and syntax colors
    Set-PSReadLineOption -Colors @{
        InlinePrediction       = '#6A6A6A'
        ListPrediction         = '#6A6A6A'
        ListPredictionSelected = '#005FFF'
        Command                = '#98C379'
        Parameter              = '#ABB2BF'
        String                 = '#E5C07B'
        Operator               = '#56B6C2'
        Variable               = '#E06C75'
        Comment                = '#5C6370'
        Keyword                = '#C678DD'
        Error                  = '#FF5555'
    } -ErrorAction SilentlyContinue
}
'@

# PSFzf config
$fzfConfig = @'
# PSFzf key bindings
if (Get-Command fzf -ErrorAction SilentlyContinue) {
    Set-PsFzfOption -PSReadlineChordProvider 'Ctrl+f' -ErrorAction SilentlyContinue
    Set-PsFzfOption -PSReadlineChordReverseHistory 'Ctrl+r' -ErrorAction SilentlyContinue
    Set-PsFzfOption -GitKey 'Ctrl+g' -ErrorAction SilentlyContinue
}
'@

# Build profile lines
$profileLines = @()
$profileLines += '# === Oh My Posh ==='
$profileLines += '# === CRITICAL: Initialize Conda PROPERLY for CONDA_DEFAULT_ENV ==='
$profileLines += 'try {'
$profileLines += '    # Use one hook path only to avoid duplicate startup messages.'
$profileLines += '    if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {'
$profileLines += '        $condaHook = (& $env:CONDA_EXE shell.powershell hook 2>$null) | Out-String'
$profileLines += '        if ($condaHook -and $condaHook.Trim().Length -gt 0) { Invoke-Expression $condaHook }'
$profileLines += '    } elseif (Get-Command conda -ErrorAction SilentlyContinue) {'
$profileLines += '        $condaHook = (conda shell.powershell hook 2>$null) | Out-String'
$profileLines += '        if ($condaHook -and $condaHook.Trim().Length -gt 0) { Invoke-Expression $condaHook }'
$profileLines += '    }'
$profileLines += '} catch { Write-Verbose "Conda initialization attempt error: $_" }'

# Load Oh My Posh with Conda-aware theme
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
$profileLines += ($psReadLineConfig -split '\r?\n')
$profileLines += ''
$profileLines += ($fzfConfig -split '\r?\n')

# Write profile
Write-Host "Writing profile to $profilePath" -ForegroundColor Green
$profileLines | Where-Object { $_.Trim() -ne '' } | Set-Content $profilePath -Encoding UTF8

# If running under Windows PowerShell 5.1 and PS7 is available, also write to the PS7 profile
# so that switching to pwsh works out of the box without re-running this script
$ps7Profile = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'PowerShell\Microsoft.PowerShell_profile.ps1'
if ($PSVersionTable.PSVersion.Major -lt 7 -and (Get-Command pwsh -ErrorAction SilentlyContinue)) {
    $ps7Dir = Split-Path $ps7Profile
    if (-not (Test-Path $ps7Dir)) { New-Item -Path $ps7Dir -ItemType Directory -Force | Out-Null }
    $profileLines | Where-Object { $_.Trim() -ne '' } | Set-Content $ps7Profile -Encoding UTF8
    Write-Host "Also wrote PS7 profile to $ps7Profile" -ForegroundColor Green
}

# =============================================================================
# FINAL INSTRUCTIONS
# =============================================================================
Write-Host "`nInstallation complete!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "   1. Run: conda init powershell   (do this ONCE if not already done)"
Write-Host "   2. Restart PowerShell (or run: . `$PROFILE)"
Write-Host "   3. Activate a conda env: conda activate myenv"
Write-Host "   4. You should now see (myenv) in your prompt!" -ForegroundColor Magenta
Write-Host "`nTip: Run 'Get-Command conda' to verify it's in PATH." -ForegroundColor Yellow