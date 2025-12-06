<#
.SYNOPSIS
    Automated Windows Post-Installation Setup

.DESCRIPTION
    Comprehensive setup script for Windows that automates:
    - UniGetUI installation (replacement for Chocolatey)
    - Package installation via UniGetUI/WinGet
    - Oh My Posh terminal customization
    - Ansible playbook execution (optional)
    
    This script provides a modern, modular approach to Windows automation
    with excellent terminal output, error handling, and user feedback.

.PARAMETER SkipPackages
    Skip package installation

.PARAMETER SkipOhMyPosh
    Skip Oh My Posh setup

.PARAMETER SkipAnsible
    Skip Ansible playbook execution

.PARAMETER NonInteractive
    Run in non-interactive mode (uses defaults)

.EXAMPLE
    .\Setup-Windows.ps1
    Run full automated setup with interactive prompts

.EXAMPLE
    .\Setup-Windows.ps1 -SkipAnsible
    Run setup but skip Ansible playbooks

.EXAMPLE
    .\Setup-Windows.ps1 -NonInteractive
    Run setup without any user interaction

.NOTES
    Author: AutoOS
    Version: 1.0.0
    Requires: Windows 10/11 with PowerShell 5.1 or later
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [switch]$SkipPackages,
    
    [Parameter(Mandatory = $false)]
    [switch]$SkipOhMyPosh,
    
    [Parameter(Mandatory = $false)]
    [switch]$SkipAnsible,
    
    [Parameter(Mandatory = $false)]
    [switch]$NonInteractive
)

# ============================================================================
# Configuration
# ============================================================================
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$script:ScriptVersion = '1.0.0'

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Banner {
    param([string]$Version)
    
    $banner = @"

╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║                    AutoOS - Windows Automated Setup                         ║
║                                                                              ║
║                           Version $Version                                      ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

"@
    
    Write-Host $banner -ForegroundColor Cyan
}

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
    
    $timestamp = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$timestamp] $prefix $Message" -ForegroundColor $color
}

function Confirm-Action {
    param(
        [string]$Message,
        [bool]$NonInteractive = $false
    )
    
    if ($NonInteractive) {
        return $true
    }
    
    $response = Read-Host "$Message (Y/n)"
    return ($response -eq '' -or $response -match '^[Yy]')
}

function Invoke-Module {
    param(
        [string]$ModuleName,
        [string]$ModulePath,
        [scriptblock]$ScriptBlock
    )
    
    Write-Section "Module: $ModuleName"
    Write-Status "Starting $ModuleName..." -Level Info
    
    $startTime = Get-Date
    
    try {
        if ($ScriptBlock) {
            & $ScriptBlock
        }
        elseif ($ModulePath -and (Test-Path $ModulePath)) {
            & $ModulePath
        }
        else {
            Write-Status "Module not found: $ModulePath" -Level Error
            return $false
        }
        
        $duration = (Get-Date) - $startTime
        Write-Status "$ModuleName completed in $($duration.TotalSeconds.ToString('F1')) seconds" -Level Success
        return $true
    }
    catch {
        $duration = (Get-Date) - $startTime
        Write-Status "$ModuleName failed after $($duration.TotalSeconds.ToString('F1')) seconds" -Level Error
        Write-Host "Error: $_" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        return $false
    }
}

function Test-AdminPrivileges {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ============================================================================
# Pre-flight Checks
# ============================================================================

function Invoke-PreflightChecks {
    Write-Section "Pre-flight Checks"
    
    # Check PowerShell version
    $psVersion = $PSVersionTable.PSVersion
    Write-Status "PowerShell version: $psVersion" -Level Info
    
    if ($psVersion.Major -lt 5) {
        Write-Status "PowerShell 5.1 or later is required" -Level Error
        return $false
    }
    
    # Check if running as administrator
    if (Test-AdminPrivileges) {
        Write-Status "Running with administrator privileges" -Level Warning
        Write-Host ""
        Write-Host "  It's recommended to run this script as a regular user." -ForegroundColor Yellow
        Write-Host "  Some installations (like Oh My Posh) work better without admin rights." -ForegroundColor Yellow
        Write-Host ""
        
        if (-not $NonInteractive) {
            $continue = Confirm-Action "Continue anyway?" -NonInteractive $false
            if (-not $continue) {
                return $false
            }
        }
    }
    else {
        Write-Status "Running as regular user (recommended)" -Level Success
    }
    
    # Check Windows version
    $osVersion = [System.Environment]::OSVersion.Version
    Write-Status "Windows version: $osVersion" -Level Info
    
    if ($osVersion.Major -lt 10) {
        Write-Status "Windows 10 or later is required" -Level Error
        return $false
    }
    
    # Check internet connectivity
    Write-Status "Checking internet connectivity..." -Level Info
    try {
        $null = Test-Connection -ComputerName "8.8.8.8" -Count 1 -Quiet
        Write-Status "Internet connection available" -Level Success
    }
    catch {
        Write-Status "No internet connection detected" -Level Warning
        Write-Host "  Some features may not work without internet access." -ForegroundColor Yellow
    }
    
    Write-Status "Pre-flight checks completed" -Level Success
    return $true
}

# ============================================================================
# Main Setup Flow
# ============================================================================

function Invoke-Setup {
    # Display banner
    Write-Banner -Version $script:ScriptVersion
    
    Write-Host "  This script will automate your Windows post-installation setup." -ForegroundColor Cyan
    Write-Host "  It will install and configure software using UniGetUI and Oh My Posh." -ForegroundColor Cyan
    Write-Host ""
    
    # Pre-flight checks
    if (-not (Invoke-PreflightChecks)) {
        Write-Status "Pre-flight checks failed. Exiting." -Level Error
        exit 1
    }
    
    # Determine module paths
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $modulesDir = Join-Path $scriptDir "modules"
    
    # Setup tracking
    $script:Results = @{
        UniGetUI = $null
        Packages = $null
        OhMyPosh = $null
        Ansible = $null
    }
    
    $overallStartTime = Get-Date
    
    # ========================================================================
    # Module 1: Install UniGetUI
    # ========================================================================
    
    if (-not $SkipPackages) {
        $modulePath = Join-Path $modulesDir "Install-UniGetUI.ps1"
        $script:Results.UniGetUI = Invoke-Module -ModuleName "UniGetUI Installation" -ModulePath $modulePath
    }
    else {
        Write-Status "Skipping UniGetUI installation (--SkipPackages)" -Level Info
    }
    
    # ========================================================================
    # Module 2: Import and Install Packages
    # ========================================================================
    
    if (-not $SkipPackages -and $script:Results.UniGetUI) {
        $modulePath = Join-Path $modulesDir "Import-UniGetUIPackages.ps1"
        $script:Results.Packages = Invoke-Module -ModuleName "Package Installation" -ModulePath $modulePath
    }
    else {
        Write-Status "Skipping package installation" -Level Info
    }
    
    # ========================================================================
    # Module 3: Setup Oh My Posh
    # ========================================================================
    
    if (-not $SkipOhMyPosh) {
        if ($NonInteractive -or (Confirm-Action "Setup Oh My Posh terminal customization?" -NonInteractive $NonInteractive)) {
            $modulePath = Join-Path $modulesDir "Setup-OhMyPosh.ps1"
            $script:Results.OhMyPosh = Invoke-Module -ModuleName "Oh My Posh Setup" -ModulePath $modulePath
        }
        else {
            Write-Status "Skipping Oh My Posh setup" -Level Info
        }
    }
    else {
        Write-Status "Skipping Oh My Posh setup (--SkipOhMyPosh)" -Level Info
    }
    
    # ========================================================================
    # Module 4: Ansible Playbooks (Optional)
    # ========================================================================
    
    if (-not $SkipAnsible) {
        if ($NonInteractive -or (Confirm-Action "Execute Ansible playbooks? (Requires WSL and configuration)" -NonInteractive $NonInteractive)) {
            $modulePath = Join-Path $modulesDir "Invoke-AnsibleSetup.ps1"
            $script:Results.Ansible = Invoke-Module -ModuleName "Ansible Playbooks" -ModulePath $modulePath
        }
        else {
            Write-Status "Skipping Ansible playbooks" -Level Info
        }
    }
    else {
        Write-Status "Skipping Ansible playbooks (--SkipAnsible)" -Level Info
    }
    
    # ========================================================================
    # Summary
    # ========================================================================
    
    $overallDuration = (Get-Date) - $overallStartTime
    
    Write-Section "Setup Summary"
    Write-Host ""
    Write-Host "  Total duration: $($overallDuration.Minutes) minutes $($overallDuration.Seconds) seconds" -ForegroundColor White
    Write-Host ""
    Write-Host "  Results:" -ForegroundColor Cyan
    
    foreach ($key in $script:Results.Keys) {
        $result = $script:Results[$key]
        $status = if ($null -eq $result) { "Skipped" }
                  elseif ($result) { "Success" }
                  else { "Failed" }
        
        $color = switch ($status) {
            "Success" { "Green" }
            "Failed" { "Red" }
            "Skipped" { "Yellow" }
        }
        
        Write-Host "    $key`: $status" -ForegroundColor $color
    }
    
    Write-Host ""
    
    # Post-setup instructions
    Write-Section "Next Steps"
    Write-Host ""
    
    if ($script:Results.OhMyPosh) {
        Write-Host "  Terminal Configuration:" -ForegroundColor Cyan
        Write-Host "    • Open Windows Terminal → Settings → Profiles → Defaults" -ForegroundColor White
        Write-Host "    • Set 'Font face' to 'MesloLGS NF'" -ForegroundColor White
        Write-Host "    • Restart PowerShell or run: . `$PROFILE" -ForegroundColor White
        Write-Host ""
    }
    
    if ($script:Results.Packages) {
        Write-Host "  Package Management:" -ForegroundColor Cyan
        Write-Host "    • Launch UniGetUI from Start Menu to manage packages" -ForegroundColor White
        Write-Host "    • Automatic updates are enabled" -ForegroundColor White
        Write-Host ""
    }
    
    Write-Host "  Documentation:" -ForegroundColor Cyan
    Write-Host "    • See README.md files for more details" -ForegroundColor White
    Write-Host "    • Report issues at: https://github.com/Ch3fUlrich/AutoOS/issues" -ForegroundColor White
    Write-Host ""
    
    Write-Status "Windows setup complete!" -Level Success
    Write-Host ""
}

# ============================================================================
# Script Entry Point
# ============================================================================

try {
    Invoke-Setup
}
catch {
    Write-Status "Setup failed with an unexpected error" -Level Error
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
