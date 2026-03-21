<#
.SYNOPSIS
    Execute Ansible playbooks for Windows setup

.DESCRIPTION
    Prepares and executes Ansible playbooks from the Windows/ansible folder.
    This includes network drive setup, Python/Miniconda, and other configurations.

.NOTES
    Author: AutoOS
    Version: 1.0.0
    Requires: WSL with Ubuntu and Ansible installed
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

function Test-WSLInstalled {
    try {
        $wsl = Get-Command wsl -ErrorAction SilentlyContinue
        return $null -ne $wsl
    }
    catch {
        return $false
    }
}

function Test-AnsibleInWSL {
    try {
        $result = wsl -e bash -c "command -v ansible" 2>$null
        return $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($result)
    }
    catch {
        return $false
    }
}

# ============================================================================
# Main Logic
# ============================================================================

Write-Section "Ansible Setup Preparation"

# Locate ansible directory
$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$ansibleDir = Join-Path $repoRoot "ansible"

if (-not (Test-Path $ansibleDir)) {
    Write-Status "Ansible directory not found: $ansibleDir" -Level Error
    exit 1
}

Write-Status "Found Ansible directory: $ansibleDir" -Level Info

# Check WSL installation
Write-Status "Checking for WSL (Windows Subsystem for Linux)..." -Level Info
if (-not (Test-WSLInstalled)) {
    Write-Status "WSL is not installed" -Level Warning
    Write-Host ""
    Write-Host "  Ansible requires WSL to run on Windows." -ForegroundColor Yellow
    Write-Host "  To install WSL, run the following command in PowerShell as Administrator:" -ForegroundColor Cyan
    Write-Host "    wsl --install" -ForegroundColor White
    Write-Host ""
    Write-Host "  After installation, restart your computer and run this setup again." -ForegroundColor Cyan
    Write-Host ""
    Write-Status "Skipping Ansible setup" -Level Warning
    exit 0
}

Write-Status "WSL is installed" -Level Success

# Check Ansible installation in WSL
Write-Status "Checking for Ansible in WSL..." -Level Info
if (-not (Test-AnsibleInWSL)) {
    Write-Status "Ansible is not installed in WSL" -Level Warning
    Write-Host ""
    Write-Host "  To install Ansible in WSL, run:" -ForegroundColor Cyan
    Write-Host "    wsl -e bash -c 'sudo apt update && sudo apt install -y ansible git sshpass'" -ForegroundColor White
    Write-Host ""
    Write-Host "  Or start WSL and run:" -ForegroundColor Cyan
    Write-Host "    sudo apt update && sudo apt install -y ansible git sshpass" -ForegroundColor White
    Write-Host ""
    Write-Status "Skipping Ansible setup" -Level Warning
    exit 0
}

Write-Status "Ansible is installed in WSL" -Level Success

# ============================================================================
# Ansible Setup Information
# ============================================================================

Write-Section "Ansible Playbook Execution"
Write-Host ""
Write-Host "  The Ansible playbooks in Windows/ansible include:" -ForegroundColor Cyan
Write-Host "    • Network drive configuration" -ForegroundColor White
Write-Host "    • Python and Miniconda setup" -ForegroundColor White
Write-Host "    • Software download and installation" -ForegroundColor White
Write-Host "    • Terminal setup (already handled by Oh My Posh)" -ForegroundColor White
Write-Host ""

Write-Status "NOTE: Ansible playbooks require additional configuration" -Level Info
Write-Host ""
Write-Host "  Before running Ansible playbooks, you need to:" -ForegroundColor Yellow
Write-Host "    1. Set up SSH server on Windows (OpenSSH)" -ForegroundColor White
Write-Host "    2. Edit Windows/ansible/inventory.yml with your:" -ForegroundColor White
Write-Host "       - Windows IP address" -ForegroundColor White
Write-Host "       - Windows username" -ForegroundColor White
Write-Host "       - Windows password" -ForegroundColor White
Write-Host "    3. Configure network drives in network_drives.yml (if needed)" -ForegroundColor White
Write-Host ""
Write-Host "  For detailed instructions, see:" -ForegroundColor Cyan
Write-Host "    README.md in the repository root" -ForegroundColor White
Write-Host ""

# Ask if user wants to proceed
$proceed = Read-Host "Have you completed the SSH and inventory configuration? (y/N)"
if ($proceed -notmatch '^[Yy]') {
    Write-Status "Skipping Ansible playbook execution" -Level Info
    Write-Host ""
    Write-Host "  When ready, you can manually run the playbooks:" -ForegroundColor Cyan
    Write-Host "    cd Windows/ansible" -ForegroundColor White
    Write-Host "    wsl -e bash -c 'ansible-playbook -i inventory.yml main_playbook.yml'" -ForegroundColor White
    Write-Host ""
    exit 0
}

# ============================================================================
# Execute Ansible Playbooks
# ============================================================================

Write-Status "Executing Ansible playbooks..." -Level Info
Write-Host ""

try {
    # Convert Windows path to WSL path
    # WSL uses /mnt prefix for Windows drives (C: -> /mnt/c, D: -> /mnt/d, etc.)
    $wslAnsiblePath = $ansibleDir -replace '\\', '/'
    
    # Handle drive letter conversion
    if ($wslAnsiblePath -match '^([A-Z]):') {
        $driveLetter = $matches[1].ToLower()
        $wslMountPrefix = '/mnt'
        $wslAnsiblePath = $wslAnsiblePath -replace '^[A-Z]:', "$wslMountPrefix/$driveLetter"
    }
    
    # Run Ansible playbook
    $ansibleCmd = "cd '$wslAnsiblePath' && ansible-playbook -i inventory.yml main_playbook.yml"
    
    Write-Status "Running: wsl -e bash -c '$ansibleCmd'" -Level Info
    Write-Host ""
    
    $process = Start-Process -FilePath 'wsl' -ArgumentList '-e', 'bash', '-c', $ansibleCmd -Wait -PassThru -NoNewWindow
    
    if ($process.ExitCode -eq 0) {
        Write-Host ""
        Write-Status "Ansible playbooks completed successfully!" -Level Success
    }
    else {
        Write-Status "Ansible playbooks completed with errors (Exit code: $($process.ExitCode))" -Level Warning
        Write-Host ""
        Write-Host "  Check the output above for details." -ForegroundColor Yellow
        Write-Host "  Some tasks may have failed due to network issues or missing dependencies." -ForegroundColor Yellow
    }
}
catch {
    Write-Status "Failed to execute Ansible playbooks: $_" -Level Error
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Status "Ansible setup complete!" -Level Success
