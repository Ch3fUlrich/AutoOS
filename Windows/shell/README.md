# Windows Shell - Automated Setup Scripts

Modern PowerShell automation scripts for Windows post-installation setup. This directory contains a comprehensive, modular setup system that replaces the Chocolatey-based Ansible approach with UniGetUI and provides excellent user experience.

## Overview

The Windows Shell setup provides:
- ✅ **UniGetUI Integration** - Modern package manager (replaces Chocolatey)
- ✅ **Automated Package Installation** - Pre-configured software via WinGet
- ✅ **Oh My Posh Setup** - Beautiful terminal customization
- ✅ **Ansible Playbook Execution** - Optional advanced configuration
- ✅ **Modular Architecture** - Clean, maintainable code structure
- ✅ **Excellent UX** - Clear output, progress tracking, error handling

## Quick Start

### Prerequisites
- Windows 10/11
- PowerShell 5.1 or later (comes with Windows)
- Internet connection

### Basic Usage

1. **Clone the repository** (if not already done):
   ```powershell
   git clone https://github.com/Ch3fUlrich/AutoOS.git
   cd AutoOS/Windows/shell
   ```

2. **Run the setup script**:
   ```powershell
   .\Setup-Windows.ps1
   ```

3. **Follow the prompts** - The script will guide you through each step

### Advanced Usage

```powershell
# Skip specific modules
.\Setup-Windows.ps1 -SkipOhMyPosh       # Skip terminal customization
.\Setup-Windows.ps1 -SkipAnsible        # Skip Ansible playbooks
.\Setup-Windows.ps1 -SkipPackages       # Skip package installation

# Non-interactive mode (uses defaults)
.\Setup-Windows.ps1 -NonInteractive

# Combine options
.\Setup-Windows.ps1 -SkipAnsible -NonInteractive
```

## What Gets Installed

### Software Packages (via UniGetUI/WinGet)

**Utilities:**
- 7-Zip
- PowerToys
- TreeSize Free

**Development Tools:**
- Visual Studio Code
- Git
- Cygwin (Linux commands on Windows)

**Terminal Customization:**
- Oh My Posh
- Nerd Fonts (MesloLGS)
- CLI tools (fzf, ripgrep)

**Browsers:**
- Mozilla Firefox
- Google Chrome

**Cloud & Sync:**
- (SwitchDrive, OwnCloud, Google Drive - if available)

**Remote Access:**
- TeamViewer
- Parsec
- Moonlight

**Communication:**
- Slack
- WhatsApp
- Zoom

**Productivity:**
- Zotero
- Obsidian
- Notepad++

**Media:**
- VLC Media Player
- Spotify

**Security:**
- WireGuard VPN

### Terminal Customization (Oh My Posh)

- Oh My Posh with custom themes
- PowerShell modules:
  - PSReadLine (enhanced command-line editing)
  - PSFzf (fuzzy finder integration)
  - Terminal-Icons (file icons in terminal)
  - z (smart directory jumping)
  - posh-git (Git integration)
- Keyboard shortcuts:
  - `Ctrl+F` - Search files with fzf
  - `Ctrl+R` - Search command history
  - `Ctrl+G` - Git shortcuts

## Directory Structure

```
Windows/shell/
├── Setup-Windows.ps1          # Main orchestrator script
├── packages.json              # Package definitions for UniGetUI
├── README.md                  # This file
└── modules/
    ├── Install-UniGetUI.ps1           # Install UniGetUI package manager
    ├── Import-UniGetUIPackages.ps1    # Install packages from JSON
    ├── Setup-OhMyPosh.ps1             # Terminal customization
    └── Invoke-AnsibleSetup.ps1        # Ansible playbook execution
```

## Module Details

### 1. Install-UniGetUI.ps1

Installs UniGetUI (formerly WingetUI), a modern package manager GUI that supports:
- WinGet
- Scoop
- Chocolatey
- And more...

**Why UniGetUI over Chocolatey?**
- Better package source integration (WinGet is built into Windows)
- Modern, user-friendly interface
- Automatic update checking
- Better error handling and logging
- Active development and community

### 2. Import-UniGetUIPackages.ps1

Reads `packages.json` and installs all defined packages using WinGet. Features:
- Parallel installation support
- Progress tracking
- Error handling and retry logic
- Automatic update configuration

### 3. Setup-OhMyPosh.ps1

Executes the Oh My Posh setup from `Windows/powershell/oh-my-posh/`. Installs:
- Oh My Posh binary
- MesloLGS Nerd Font
- CLI tools (fzf, ripgrep)
- PowerShell modules
- Custom theme configuration
- PowerShell profile

### 4. Invoke-AnsibleSetup.ps1

Optional module for advanced setup via Ansible. Requires:
- WSL (Windows Subsystem for Linux)
- Ubuntu in WSL
- Ansible installed in WSL
- SSH server on Windows
- Configured `inventory.yml`

## Customization

### Adding/Removing Packages

Edit `packages.json` to customize the software list:

```json
{
  "WinGet": {
    "Packages": [
      {
        "Id": "Microsoft.VisualStudioCode",
        "Name": "Visual Studio Code"
      },
      {
        "Id": "Git.Git",
        "Name": "Git"
      }
    ]
  }
}
```

**Finding Package IDs:**
```powershell
# Search for a package
winget search "package name"

# Get package details
winget show "PackageId"
```

### Customizing Oh My Posh

After setup, customize your terminal:
1. Browse themes: `Get-PoshThemes`
2. Edit your profile: `notepad $PROFILE`
3. Change theme: Update the `oh-my-posh init` line in your profile

### Skipping Modules

Use command-line switches to skip unwanted modules:
```powershell
.\Setup-Windows.ps1 -SkipOhMyPosh -SkipAnsible
```

## Troubleshooting

### Common Issues

**1. "Execution Policy" errors:**
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
```

**2. WinGet not found:**
- WinGet comes with Windows 11 by default
- On Windows 10, install from: [Microsoft Store - App Installer](https://www.microsoft.com/p/app-installer/9nblggh4nns1)

**3. UniGetUI installation fails:**
- Ensure WinGet is working: `winget --version`
- Try installing manually: `winget install MartiCliment.UniGetUI`

**4. Oh My Posh setup issues:**
- Ensure you're not running as Administrator
- Check Windows Terminal font settings
- Restart PowerShell after installation

**5. Ansible playbooks fail:**
- Verify WSL is installed: `wsl --version`
- Check SSH server is running: `Get-Service sshd`
- Verify `inventory.yml` has correct IP/credentials

### Logs and Debugging

The script provides detailed output for each step. Common debug commands:

```powershell
# Check WinGet packages
winget list

# Check PowerShell modules
Get-Module -ListAvailable

# Test Oh My Posh
oh-my-posh --version

# Check WSL
wsl --list --verbose

# Check Ansible in WSL
wsl -e bash -c "ansible --version"
```

### Getting Help

1. Check the main repository README: `../../README.md`
2. Browse module source code in `modules/` directory
3. Open an issue: https://github.com/Ch3fUlrich/AutoOS/issues

## Design Principles

This setup follows state-of-the-art PowerShell practices:

### Code Quality
- ✅ **Modular Design** - Each module has a single responsibility
- ✅ **Error Handling** - Try/catch blocks with meaningful messages
- ✅ **Progress Tracking** - Clear user feedback at each step
- ✅ **Idempotent** - Safe to run multiple times
- ✅ **Parameter Validation** - Strong typing and validation

### User Experience
- ✅ **Beautiful Output** - Color-coded messages and sections
- ✅ **Interactive Prompts** - User control over execution
- ✅ **Non-interactive Mode** - Automation-friendly
- ✅ **Progress Indicators** - Time tracking and status
- ✅ **Helpful Errors** - Actionable error messages

### PowerShell Best Practices
- ✅ **Approved Verbs** - Following PowerShell naming conventions
- ✅ **Comment-Based Help** - Full documentation in code
- ✅ **Parameter Sets** - Flexible command-line interface
- ✅ **Pipeline Support** - Where applicable
- ✅ **UTF-8 BOM** - Proper encoding for international characters

## Comparison with Previous Approach

| Feature | Old (Chocolatey/Ansible) | New (UniGetUI/PowerShell) |
|---------|-------------------------|--------------------------|
| Package Manager | Chocolatey | UniGetUI/WinGet |
| Requires WSL | Yes (for Ansible) | No (optional) |
| User Experience | Terminal-based | Modern GUI + CLI |
| Modularity | Ansible playbooks | PowerShell modules |
| Error Handling | Basic | Comprehensive |
| Progress Tracking | Limited | Detailed |
| Maintenance | Complex | Simple |
| Speed | Slower | Faster |
| Windows Integration | Indirect | Native |

## Contributing

To contribute improvements:

1. Follow PowerShell best practices
2. Test on Windows 10 and 11
3. Update documentation
4. Add error handling
5. Maintain consistent styling

## License

This project is part of AutoOS. See the main repository for license information.

## Credits

- **AutoOS Team** - Script development
- **UniGetUI** by Martí Climent - Package manager GUI
- **Oh My Posh** by Jan De Dobbeleer - Terminal customization
- **WinGet** by Microsoft - Windows Package Manager

---

**Last Updated:** 2025-12-06  
**Version:** 1.0.0
