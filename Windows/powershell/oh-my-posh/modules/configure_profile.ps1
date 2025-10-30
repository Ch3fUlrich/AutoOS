param(
    [string]$ThemeFile = "$HOME\.oh-my-posh\theme.omp.json"
)

Write-Host "[profile] Creating PowerShell profile and wiring Oh My Posh + modules..." -ForegroundColor Green

$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) { 
    New-Item -Path $profilePath -ItemType File -Force | Out-Null 
}

# PSReadLine config (version-aware)
$psReadLineConfig = @'
# PSReadLine settings (auto-detect version)
$psrl = Get-Module -ListAvailable PSReadLine | Sort-Object Version -Descending | Select-Object -First 1
if ($psrl) {
    $ver = $psrl.Version
    if ($ver.Major -ge 3 -or ($ver.Major -eq 2 -and $ver.Minor -ge 2)) {
        Set-PSReadLineOption -PredictionSource HistoryAndPlugin -ErrorAction SilentlyContinue
        Set-PSReadLineOption -PredictionViewStyle ListView -ErrorAction SilentlyContinue
    } elseif ($ver.Major -eq 2 -and $ver.Minor -ge 1) {
        Set-PSReadLineOption -HistoryBasedPrediction $true -ErrorAction SilentlyContinue
    }
    Set-PSReadLineOption -EditMode Windows -ErrorAction SilentlyContinue
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
$profileLines += '    # Prefer a real conda executable if available'
$profileLines += '    $condaApp = Get-Command conda -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -eq "Application" }'
$profileLines += '    if ($condaApp) {'
$profileLines += '        try { (& conda shell.powershell hook) | Out-String | Invoke-Expression } catch { Write-Verbose "conda (app) hook failed: $_" }'
$profileLines += '    } elseif ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {'
$profileLines += '        try { (& $env:CONDA_EXE shell.powershell hook) | Out-String | Invoke-Expression } catch { Write-Verbose "CONDA_EXE hook failed: $_" }'
$profileLines += '    } elseif (Get-Command Invoke-Conda -ErrorAction SilentlyContinue) {'
$profileLines += '        # Some Conda installs provide a PowerShell function/alias "Invoke-Conda" that proxies to the real conda'
$profileLines += '        try {'
$profileLines += '            # Try calling the helper with the same args a real conda would get'
$profileLines += '            Invoke-Conda shell.powershell hook | Out-String | Invoke-Expression'
$profileLines += '        } catch {'
$profileLines += '            Write-Verbose "Invoke-Conda hook call failed: $_"'
$profileLines += '        }'
$profileLines += '    }'
$profileLines += ''
$profileLines += '    # If basic hook didn''t populate variables, try a dry-run init via available executable as a fallback'
$profileLines += '    if (-not $env:CONDA_DEFAULT_ENV -and -not $env:CONDA_PREFIX) {'
$profileLines += '        try {'
$profileLines += '            if ($condaApp) {'
$profileLines += '                (& conda init --dry-run powershell) | Out-String | Invoke-Expression'
$profileLines += '            } elseif ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {'
$profileLines += '                (& $env:CONDA_EXE init --dry-run powershell) | Out-String | Invoke-Expression'
$profileLines += '            }'
$profileLines += '        } catch { Write-Verbose "conda init fallback failed: $_" }'
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
$profileLines += ($psReadLineConfig -split "`n")
$profileLines += ''
$profileLines += ($fzfConfig -split "`n")

# Write profile
Write-Host "Writing profile to $profilePath" -ForegroundColor Green
$profileLines | Where-Object { $_.Trim() -ne '' } | Set-Content $profilePath -Encoding UTF8

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