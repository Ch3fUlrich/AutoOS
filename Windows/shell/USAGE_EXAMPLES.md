# Usage Examples - Windows Shell Setup

This document provides practical examples for using the Windows Shell automation scripts.

## Basic Usage

### Complete Automated Setup

Run everything with default settings:
```powershell
.\Setup-Windows.ps1
```

This will:
1. Install UniGetUI
2. Install all packages from `packages.json`
3. Set up Oh My Posh terminal customization
4. Optionally run Ansible playbooks (with confirmation)

### Non-Interactive Mode

Perfect for automation or when you want to accept all defaults:
```powershell
.\Setup-Windows.ps1 -NonInteractive
```

## Selective Installation

### Only Install Packages

Skip terminal customization and Ansible:
```powershell
.\Setup-Windows.ps1 -SkipOhMyPosh -SkipAnsible
```

### Only Setup Terminal

Skip package installation and Ansible:
```powershell
.\Setup-Windows.ps1 -SkipPackages -SkipAnsible
```

### Skip Ansible

Most common use case - install packages and customize terminal:
```powershell
.\Setup-Windows.ps1 -SkipAnsible
```

## Running Individual Modules

You can also run modules independently:

### Install UniGetUI Only
```powershell
.\modules\Install-UniGetUI.ps1
```

### Import Packages Only
```powershell
.\modules\Import-UniGetUIPackages.ps1
```

### Setup Oh My Posh Only
```powershell
.\modules\Setup-OhMyPosh.ps1
```

### Run Ansible Playbooks Only
```powershell
.\modules\Invoke-AnsibleSetup.ps1
```

## Customization Examples

### Custom Package List

1. Edit `packages.json` to add/remove packages
2. Find package IDs: `winget search "package name"`
3. Run installation:
   ```powershell
   .\modules\Import-UniGetUIPackages.ps1
   ```

Example additions to `packages.json`:
```json
{
  "WinGet": {
    "Packages": [
      {
        "Id": "Docker.DockerDesktop",
        "Name": "Docker Desktop"
      },
      {
        "Id": "Python.Python.3.12",
        "Name": "Python 3.12"
      }
    ]
  }
}
```

### Custom Package JSON Location

```powershell
.\modules\Import-UniGetUIPackages.ps1 -PackageJsonPath "C:\path\to\my-packages.json"
```

## Advanced Scenarios

### Corporate Environment

For environments with proxy or network restrictions:
```powershell
# Set proxy if needed
$env:HTTP_PROXY = "http://proxy.company.com:8080"
$env:HTTPS_PROXY = "http://proxy.company.com:8080"

# Run setup
.\Setup-Windows.ps1 -SkipAnsible
```

### Multiple Machines

Create a custom package JSON for different machine types:

**Developer Machine** (`dev-packages.json`):
```json
{
  "WinGet": {
    "Packages": [
      {"Id": "Microsoft.VisualStudioCode", "Name": "VS Code"},
      {"Id": "Git.Git", "Name": "Git"},
      {"Id": "Docker.DockerDesktop", "Name": "Docker"},
      {"Id": "Microsoft.PowerShell", "Name": "PowerShell 7"}
    ]
  }
}
```

**Media Machine** (`media-packages.json`):
```json
{
  "WinGet": {
    "Packages": [
      {"Id": "VideoLAN.VLC", "Name": "VLC"},
      {"Id": "GIMP.GIMP", "Name": "GIMP"},
      {"Id": "Audacity.Audacity", "Name": "Audacity"},
      {"Id": "OBSProject.OBSStudio", "Name": "OBS Studio"}
    ]
  }
}
```

Then install:
```powershell
.\modules\Import-UniGetUIPackages.ps1 -PackageJsonPath "dev-packages.json"
```

### Unattended Installation

For deployment tools or remote execution:
```powershell
# Download repository
Invoke-WebRequest -Uri "https://github.com/Ch3fUlrich/AutoOS/archive/refs/heads/main.zip" -OutFile "$env:TEMP\AutoOS.zip"
Expand-Archive -Path "$env:TEMP\AutoOS.zip" -DestinationPath "$env:TEMP\AutoOS"

# Run setup
Set-Location "$env:TEMP\AutoOS\AutoOS-main\Windows\shell"
.\Setup-Windows.ps1 -NonInteractive -SkipAnsible
```

## Troubleshooting Examples

### Check What's Installed

```powershell
# List all installed WinGet packages
winget list

# Check specific package
winget list --id Microsoft.VisualStudioCode

# Check PowerShell modules
Get-Module -ListAvailable

# Check Oh My Posh
oh-my-posh --version
```

### Reinstall Failed Package

```powershell
# Uninstall
winget uninstall --id PackageId

# Reinstall
winget install --id PackageId --source winget
```

### Update All Packages

```powershell
# Via WinGet
winget upgrade --all

# Or launch UniGetUI and click "Update All"
```

### Reset PowerShell Profile

If terminal customization causes issues:
```powershell
# Backup current profile
Copy-Item $PROFILE "$PROFILE.backup"

# Remove profile
Remove-Item $PROFILE

# Re-run Oh My Posh setup
.\modules\Setup-OhMyPosh.ps1
```

## Automation Integration

### Task Scheduler

Create a scheduled task to run updates:
```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File C:\AutoOS\Windows\shell\modules\Import-UniGetUIPackages.ps1"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 2am
Register-ScheduledTask -TaskName "AutoOS-Update" -Action $action -Trigger $trigger
```

### Intune/SCCM Deployment

Deploy via Microsoft Intune:
1. Package the scripts into an .intunewin file
2. Create a Win32 app in Intune
3. Install command: `powershell.exe -ExecutionPolicy Bypass -File Setup-Windows.ps1 -NonInteractive -SkipAnsible`
4. Detection: Check for UniGetUI installation

## Testing Scenarios

### Dry Run (No Actual Installation)

Unfortunately, WinGet doesn't have a true dry-run mode, but you can:

1. Review what will be installed:
   ```powershell
   Get-Content packages.json | ConvertFrom-Json | Select-Object -ExpandProperty WinGet | Select-Object -ExpandProperty Packages | Format-Table
   ```

2. Check if packages are already installed:
   ```powershell
   $packages = (Get-Content packages.json | ConvertFrom-Json).WinGet.Packages
   foreach ($pkg in $packages) {
       $installed = winget list --id $pkg.Id 2>$null
       if ($installed -match $pkg.Id) {
           Write-Host "✓ $($pkg.Name) - Already installed" -ForegroundColor Green
       } else {
           Write-Host "✗ $($pkg.Name) - Not installed" -ForegroundColor Yellow
       }
   }
   ```

## Best Practices

1. **Run as Regular User**: Don't use Administrator unless necessary
2. **Backup First**: Keep backups of important configurations
3. **Test Incrementally**: Run modules individually first
4. **Review Packages**: Edit `packages.json` before installation
5. **Check Logs**: Review terminal output for any errors
6. **Update Regularly**: Keep UniGetUI and packages up to date

## Common Workflows

### New Windows Installation
```powershell
# 1. Clone repository
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Windows/shell

# 2. Review and customize packages
notepad packages.json

# 3. Run full setup
.\Setup-Windows.ps1

# 4. Restart PowerShell
# 5. Verify installation
winget list
oh-my-posh --version
```

### Adding Software Later
```powershell
# 1. Find package
winget search "software name"

# 2. Add to packages.json
# Edit the file manually

# 3. Import packages
.\modules\Import-UniGetUIPackages.ps1
```

### Update Existing Installation
```powershell
# Update all packages
winget upgrade --all

# Or use UniGetUI GUI
# Launch from Start Menu
```

## Getting Help

- **Documentation**: [README.md](README.md)
- **Module Help**: Each module has comment-based help
- **Issues**: https://github.com/Ch3fUlrich/AutoOS/issues
- **WinGet Docs**: https://learn.microsoft.com/windows/package-manager/
- **UniGetUI Docs**: https://github.com/marticliment/UniGetUI

---

**Need more examples?** Open an issue on GitHub!
