<#
.SYNOPSIS
    Setup Oh My Posh and terminal customization

.DESCRIPTION
    Executes the Oh My Posh setup scripts located in Windows/powershell/oh-my-posh.
    This includes installing Oh My Posh, fonts, tools, modules, themes, and profile configuration.

.NOTES
    Author: AutoOS
    Version: 1.0.0
#>

param()

# ============================================================================
# Configuration
# ============================================================================
$ErrorActionPreference = 'Stop'

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

# ============================================================================
# Main Logic
# ============================================================================

Write-Section "Oh My Posh Setup"

# Locate the Oh My Posh setup script
$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$ohMyPoshScript = Join-Path $repoRoot "powershell\oh-my-posh\main.ps1"

# Verify script exists
if (-not (Test-Path $ohMyPoshScript)) {
    Write-Status "Oh My Posh setup script not found: $ohMyPoshScript" -Level Error
    exit 1
}

Write-Status "Found Oh My Posh setup script: $ohMyPoshScript" -Level Info

# Inform user about the setup
Write-Host ""
Write-Host "  The Oh My Posh setup will install:" -ForegroundColor Cyan
Write-Host "    • Oh My Posh binary (via winget)" -ForegroundColor White
Write-Host "    • MesloLGS Nerd Font" -ForegroundColor White
Write-Host "    • CLI tools (fzf, ripgrep)" -ForegroundColor White
Write-Host "    • PowerShell modules (PSReadLine, PSFzf, Terminal-Icons, z, posh-git)" -ForegroundColor White
Write-Host "    • Custom theme configuration" -ForegroundColor White
Write-Host "    • PowerShell profile configuration" -ForegroundColor White
Write-Host ""

# Run the setup with all components
Write-Status "Launching Oh My Posh setup (all components)..." -Level Info
Write-Host ""

try {
    # Run with --all parameter to install everything
    & $ohMyPoshScript --all
    
    if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
        Write-Host ""
        Write-Status "Oh My Posh setup completed successfully!" -Level Success
    }
    else {
        Write-Status "Oh My Posh setup completed with warnings (Exit code: $LASTEXITCODE)" -Level Warning
    }
}
catch {
    Write-Status "Oh My Posh setup encountered an error: $_" -Level Error
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# ============================================================================
# Post-Setup Information
# ============================================================================

Write-Section "Terminal Configuration"
Write-Host ""
Write-Host "  To complete the setup:" -ForegroundColor Cyan
Write-Host "    1. Open Windows Terminal → Settings → Profiles → Defaults" -ForegroundColor White
Write-Host "    2. Set 'Font face' to 'MesloLGS NF'" -ForegroundColor White
Write-Host "    3. Restart PowerShell or run: . `$PROFILE" -ForegroundColor White
Write-Host ""
Write-Host "  Keyboard shortcuts available after restart:" -ForegroundColor Cyan
Write-Host "    • Ctrl+F : Search files with fzf" -ForegroundColor White
Write-Host "    • Ctrl+R : Search command history with fzf" -ForegroundColor White
Write-Host "    • Ctrl+G : Git integration shortcuts" -ForegroundColor White
Write-Host ""
Write-Status "Oh My Posh setup complete!" -Level Success
