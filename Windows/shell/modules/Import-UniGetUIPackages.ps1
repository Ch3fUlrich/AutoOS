<#
.SYNOPSIS
    Import and install packages into UniGetUI

.DESCRIPTION
    Loads a JSON configuration file containing package definitions and installs them via WinGet.
    Configures UniGetUI for automatic updates.

.PARAMETER PackageJsonPath
    Path to the packages.json configuration file

.NOTES
    Author: AutoOS
    Version: 1.0.0
#>

param(
    [Parameter(Mandatory = $false)]
    [string]$PackageJsonPath
)

# ============================================================================
# Configuration
# ============================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Exit codes
$WINGET_EXIT_CODE_ALREADY_INSTALLED = -1978335189

# Determine package JSON path
if (-not $PackageJsonPath) {
    $scriptDir = Split-Path -Parent $PSCommandPath
    $PackageJsonPath = Join-Path (Split-Path -Parent $scriptDir) "packages.json"
}

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

function Test-PackageInstalled {
    param([string]$PackageId)
    
    try {
        $result = winget list --id $PackageId 2>$null
        return ($LASTEXITCODE -eq 0 -and $result -match [regex]::Escape($PackageId))
    }
    catch {
        return $false
    }
}

# ============================================================================
# Main Logic
# ============================================================================

Write-Section "Package Installation via UniGetUI/WinGet"

# Verify package JSON exists
if (-not (Test-Path $PackageJsonPath)) {
    Write-Status "Package configuration file not found: $PackageJsonPath" -Level Error
    exit 1
}

Write-Status "Loading package configuration from: $PackageJsonPath" -Level Info

# Load and parse JSON
try {
    $config = Get-Content $PackageJsonPath -Raw | ConvertFrom-Json
}
catch {
    Write-Status "Failed to parse package configuration: $_" -Level Error
    exit 1
}

# Extract packages from WinGet section
$packages = @()
if ($config.PSObject.Properties.Name -contains 'WinGet') {
    $packages = $config.WinGet.Packages
}
else {
    Write-Status "No WinGet packages found in configuration" -Level Warning
    exit 0
}

Write-Status "Found $($packages.Count) packages to process" -Level Info

# Track installation results
$installed = 0
$skipped = 0
$failed = 0

# Install each package
foreach ($package in $packages) {
    $packageId = $package.Id
    $packageName = $package.Name
    
    Write-Host ""
    Write-Host "Processing: $packageName ($packageId)" -ForegroundColor Cyan
    
    # Check if already installed
    if (Test-PackageInstalled -PackageId $packageId) {
        Write-Status "$packageName is already installed" -Level Success
        $skipped++
        continue
    }
    
    # Install package
    Write-Status "Installing $packageName..." -Level Info
    try {
        $installArgs = @(
            'install',
            '--id', $packageId,
            '--source', 'winget',
            '--silent',
            '--accept-source-agreements',
            '--accept-package-agreements'
        )
        
        $process = Start-Process -FilePath 'winget' -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
        
        if ($process.ExitCode -eq 0) {
            Write-Status "$packageName installed successfully" -Level Success
            $installed++
        }
        elseif ($process.ExitCode -eq $WINGET_EXIT_CODE_ALREADY_INSTALLED) {
            # Package already installed (different exit code)
            Write-Status "$packageName was already installed" -Level Success
            $skipped++
        }
        else {
            Write-Status "$packageName installation failed (Exit code: $($process.ExitCode))" -Level Warning
            $failed++
        }
    }
    catch {
        Write-Status "Error installing $packageName : $_" -Level Error
        $failed++
    }
}

# ============================================================================
# Summary
# ============================================================================

Write-Section "Installation Summary"
Write-Host ""
Write-Host "  Total packages:        $($packages.Count)" -ForegroundColor White
Write-Host "  Newly installed:       $installed" -ForegroundColor Green
Write-Host "  Already installed:     $skipped" -ForegroundColor Cyan
Write-Host "  Failed:                $failed" -ForegroundColor $(if ($failed -gt 0) { 'Red' } else { 'White' })
Write-Host ""

if ($failed -gt 0) {
    Write-Status "Some packages failed to install. Please check the output above for details." -Level Warning
}
else {
    Write-Status "All packages processed successfully!" -Level Success
}

# ============================================================================
# Configure UniGetUI Settings
# ============================================================================

Write-Section "Configuring UniGetUI Settings"

# Check if UniGetUI settings directory exists
$uniGetUISettingsPath = "$env:LOCALAPPDATA\UniGetUI"
if (Test-Path $uniGetUISettingsPath) {
    Write-Status "UniGetUI settings directory found" -Level Info
    
    # Create/update settings to enable automatic updates
    $settingsFile = Join-Path $uniGetUISettingsPath "Settings.json"
    
    $defaultSettings = @{
        "AutomaticallyCheckForUpdates" = $true
        "EnableAutoUpdatePackages" = $true
        "DisableAutoUpdateCheck" = $false
    } | ConvertTo-Json -Depth 5
    
    try {
        if (Test-Path $settingsFile) {
            # Update existing settings
            $existingSettings = Get-Content $settingsFile -Raw | ConvertFrom-Json
            $existingSettings.AutomaticallyCheckForUpdates = $true
            $existingSettings.EnableAutoUpdatePackages = $true
            $existingSettings.DisableAutoUpdateCheck = $false
            $existingSettings | ConvertTo-Json -Depth 5 | Set-Content $settingsFile -Encoding UTF8
            Write-Status "Updated UniGetUI settings for automatic updates" -Level Success
        }
        else {
            # Create new settings file
            $defaultSettings | Set-Content $settingsFile -Encoding UTF8
            Write-Status "Created UniGetUI settings for automatic updates" -Level Success
        }
    }
    catch {
        Write-Status "Could not configure automatic updates: $_" -Level Warning
        Write-Status "You can enable this manually in UniGetUI settings" -Level Info
    }
}
else {
    Write-Status "UniGetUI settings directory not found - settings will be created on first launch" -Level Info
}

Write-Host ""
Write-Status "Package import and configuration complete!" -Level Success
