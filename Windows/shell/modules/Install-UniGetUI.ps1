<#
.SYNOPSIS
    Install UniGetUI package manager

.DESCRIPTION
    Downloads and installs UniGetUI (formerly WingetUI), a modern GUI for WinGet, Scoop, Chocolatey, and more.
    UniGetUI provides automatic updates and better package management compared to Chocolatey.

.NOTES
    Author: AutoOS
    Version: 1.0.0
#>

param()

# ============================================================================
# Configuration
# ============================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Section {
    param([string]$Message)
    Write-Host "`n$('=' * 80)" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "$('=' * 80)" -ForegroundColor Cyan
}

function Write-Status {
    param(
        [string]$Message,
        [ValidateSet('Info', 'Success', 'Warning', 'Error')]
        [string]$Level = 'Info'
    )
    
    $color = switch ($Level) {
        'Info'    { 'White' }
        'Success' { 'Green' }
        'Warning' { 'Yellow' }
        'Error'   { 'Red' }
    }
    
    $prefix = switch ($Level) {
        'Info'    { '[INFO]' }
        'Success' { '[✓]' }
        'Warning' { '[!]' }
        'Error'   { '[✗]' }
    }
    
    Write-Host "$prefix $Message" -ForegroundColor $color
}

function Test-UniGetUIInstalled {
    <#
    .SYNOPSIS
        Check if UniGetUI is installed
    #>
    $paths = @(
        "$env:LOCALAPPDATA\Programs\UniGetUI\UniGetUI.exe",
        "$env:ProgramFiles\UniGetUI\UniGetUI.exe",
        "$env:ProgramFiles(x86)\UniGetUI\UniGetUI.exe"
    )
    
    foreach ($path in $paths) {
        if (Test-Path $path) {
            return $true
        }
    }
    
    # Also check via winget
    try {
        $result = winget list --id MartiCliment.UniGetUI 2>$null
        if ($LASTEXITCODE -eq 0 -and $result -match 'UniGetUI') {
            return $true
        }
    }
    catch {
        # Ignore errors
    }
    
    return $false
}

# ============================================================================
# Main Installation Logic
# ============================================================================

Write-Section "UniGetUI Installation"

# Check if already installed
if (Test-UniGetUIInstalled) {
    Write-Status "UniGetUI is already installed" -Level Success
    
    # Check for updates
    Write-Status "Checking for updates..." -Level Info
    try {
        winget upgrade --id MartiCliment.UniGetUI --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Status "UniGetUI is up to date" -Level Success
        }
    }
    catch {
        Write-Status "Could not check for updates: $_" -Level Warning
    }
    
    exit 0
}

# Install UniGetUI via WinGet
Write-Status "Installing UniGetUI via WinGet..." -Level Info

try {
    # Ensure WinGet is available
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Status "WinGet is not available. Please install it first." -Level Error
        exit 1
    }
    
    Write-Status "Downloading and installing UniGetUI..." -Level Info
    
    # Install UniGetUI
    $installArgs = @(
        'install',
        '--id', 'MartiCliment.UniGetUI',
        '--source', 'winget',
        '--silent',
        '--accept-source-agreements',
        '--accept-package-agreements'
    )
    
    $process = Start-Process -FilePath 'winget' -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
    
    if ($process.ExitCode -ne 0) {
        Write-Status "WinGet installation failed with exit code: $($process.ExitCode)" -Level Error
        exit 1
    }
    
    Write-Status "UniGetUI installed successfully!" -Level Success
    
    # Verify installation
    if (Test-UniGetUIInstalled) {
        Write-Status "Installation verified successfully" -Level Success
    }
    else {
        Write-Status "Installation completed but verification failed" -Level Warning
        Write-Status "UniGetUI may require a system restart or PATH refresh" -Level Warning
    }
}
catch {
    Write-Status "Installation failed: $_" -Level Error
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# ============================================================================
# Post-Installation Information
# ============================================================================

Write-Section "Next Steps"
Write-Host ""
Write-Host "  1. UniGetUI has been installed successfully" -ForegroundColor Green
Write-Host "  2. You can launch it from the Start Menu or by running 'unigetui'" -ForegroundColor Cyan
Write-Host "  3. The setup script will now configure automatic updates and import packages" -ForegroundColor Cyan
Write-Host ""
Write-Status "UniGetUI installation complete!" -Level Success
