# Oh My Posh Setup for Windows

This directory contains automated scripts for installing and configuring Oh My Posh on Windows systems, providing a modern, customizable terminal experience with beautiful themes and powerline functionality.

## 🎯 Purpose

The Oh My Posh setup automates:
- **Oh My Posh Installation**: Modern terminal prompt themes
- **Font Installation**: Nerd Fonts for icon and symbol support
- **PowerShell Configuration**: Profile setup and theme initialization
- **Terminal Enhancement**: Beautiful, informative command-line interface

## 📁 Directory Structure

```
oh-my-posh/
├── setup_ohmypsh.ps1              # Main setup script
├── install_ohmypsh.ps1            # Alternative installation method
├── profile/                       # PowerShell profile configurations
│   └── Microsoft.Powershell_profile.ps1
└── README.md                      # This file
```

## 🚀 Quick Start

### Automated Installation (Recommended)
```powershell
# Run PowerShell as Administrator
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
cd Windows/powershell/oh-my-posh
.\setup_ohmypsh.ps1
```

### Manual Installation Steps
```powershell
# 1. Install Oh My Posh via winget
winget install JanDeDobbeleer.OhMyPosh -s winget

# 2. Run the setup script
.\install_ohmypsh.ps1
```

## ✨ What the Setup Does

### 1. Oh My Posh Installation
- **Installs Oh My Posh** using Windows Package Manager (winget)
- **Updates PATH** environment variable
- **Verifies installation** and functionality

### 2. Nerd Font Installation
- **Downloads Meslo Nerd Font** from GitHub releases
- **Extracts font files** from zip archive
- **Installs fonts** to Windows Fonts directory
- **Registers fonts** in Windows registry

### 3. PowerShell Profile Configuration
- **Creates PowerShell profile** if it doesn't exist
- **Adds Oh My Posh initialization** to profile
- **Configures default theme** (customizable)
- **Sets up completion and functions**

### 4. Execution Policy Setup
- **Sets execution policy** to `RemoteSigned` for current user
- **Enables script execution** for PowerShell customization
- **Maintains security** while allowing signed scripts

## ⚙️ Configuration

### Default PowerShell Profile
The script creates/modifies `$PROFILE` with:
```powershell
# Oh My Posh initialization
oh-my-posh init pwsh --config "$env:POSH_THEMES_PATH/default.omp.json" | Invoke-Expression

# Optional: Custom functions and aliases
function ll { Get-ChildItem -Force }
function la { Get-ChildItem -Force -Hidden }
Set-Alias -Name grep -Value Select-String
```

### Available Themes
Oh My Posh includes numerous built-in themes:
- `agnoster` - Classic powerline style
- `paradox` - Modern, colorful theme
- `powerlevel10k_rainbow` - Vibrant, information-rich
- `atomic` - Clean, minimal design
- `craver` - Git-focused theme

### Switching Themes
```powershell
# List available themes
Get-PoshThemes

# Change theme in profile
oh-my-posh init pwsh --config "$env:POSH_THEMES_PATH/paradox.omp.json" | Invoke-Expression

# Preview themes
oh-my-posh config export --output ~/.mytheme.omp.json
```

## 🎨 Customization

### Custom Theme Configuration
Create a custom theme file:
```json
{
  "$schema": "https://raw.githubusercontent.com/JanDeDobbeleer/oh-my-posh/main/themes/schema.json",
  "version": 2,
  "blocks": [
    {
      "type": "prompt",
      "alignment": "left",
      "segments": [
        {
          "type": "path",
          "style": "powerline",
          "foreground": "#ffffff",
          "background": "#61AFEF"
        }
      ]
    }
  ]
}
```

### Font Configuration
After installation, configure your terminal:
1. **Open Terminal Settings** (Windows Terminal, VS Code, etc.)
2. **Navigate to Profiles** → **Appearance**
3. **Set Font Face** to: `MesloLGS NF`
4. **Adjust Font Size** as needed (12-14pt recommended)

### PowerShell Profile Customization
Add personal customizations to profile:
```powershell
# Custom aliases
Set-Alias -Name k -Value kubectl
Set-Alias -Name g -Value git

# Custom functions
function weather { curl wttr.in }
function myip { (Invoke-WebRequest -Uri "https://api.ipify.org").Content }

# Import modules
Import-Module posh-git
Import-Module Terminal-Icons
```

## 🔧 Advanced Features

### Git Integration
Enable enhanced Git information in prompt:
```powershell
# Install posh-git
Install-Module posh-git -Scope CurrentUser
Import-Module posh-git

# Configure Oh My Posh with Git segment
# Theme will automatically show branch, status, and changes
```

### Terminal Icons
Add file type icons to directory listings:
```powershell
# Install Terminal-Icons module
Install-Module -Name Terminal-Icons -Repository PSGallery -Scope CurrentUser
Import-Module Terminal-Icons
```

### Auto-completion Enhancement
```powershell
# Enable advanced tab completion
Set-PSReadLineOption -PredictionSource History
Set-PSReadLineOption -PredictionViewStyle ListView
Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete
```

## 🔍 Troubleshooting

### Common Issues

**Font Not Displaying Correctly**:
- Ensure terminal font is set to `MesloLGS NF`
- Restart terminal application after font installation
- Check if terminal supports Unicode/UTF-8

**Oh My Posh Not Loading**:
```powershell
# Check if Oh My Posh is in PATH
oh-my-posh --version

# Reload PowerShell profile
. $PROFILE

# Check profile content
Get-Content $PROFILE
```

**Execution Policy Issues**:
```powershell
# Check current policy
Get-ExecutionPolicy -List

# Set for current user only
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Temporary bypass for single session
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
```

**Theme Not Loading**:
```powershell
# Verify theme path
echo $env:POSH_THEMES_PATH

# Test theme manually
oh-my-posh init pwsh --config "$env:POSH_THEMES_PATH/agnoster.omp.json" | Invoke-Expression

# Check for theme errors
oh-my-posh debug
```

### Manual Font Installation
If automatic font installation fails:
1. **Download font** from [Nerd Fonts Releases](https://github.com/ryanoasis/nerd-fonts/releases)
2. **Extract files** from MesloLGS.zip
3. **Right-click .ttf files** and select "Install"
4. **Configure terminal** to use `MesloLGS NF`

## 🧪 Testing

### Verify Installation
```powershell
# Check Oh My Posh version
oh-my-posh --version

# Test theme rendering
oh-my-posh print primary

# Verify font installation
[System.Drawing.Text.InstalledFontCollection]::new().Families | Where-Object {$_.Name -like "*Meslo*"}
```

### Performance Testing
```powershell
# Measure prompt loading time
Measure-Command { oh-my-posh init pwsh --config $env:POSH_THEMES_PATH/atomic.omp.json }

# Profile loading time
Measure-Command { . $PROFILE }
```

## 🔗 Related Documentation

- [Oh My Posh Official Documentation](https://ohmyposh.dev/)
- [Nerd Fonts Documentation](https://nerdfonts.com/)
- [PowerShell Profile Guide](https://docs.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_profiles)
- [Windows Automation Overview](../../README.md)

## 💡 Tips

1. **Backup Profile**: Save your PowerShell profile before making changes
2. **Theme Preview**: Use `Get-PoshThemes` to preview all available themes
3. **Performance**: Some themes may impact prompt speed; choose based on needs
4. **Consistency**: Use the same theme across all terminal applications
5. **Updates**: Regularly update Oh My Posh for new features and themes

---

**Manual Configuration Required**: After running the script, manually set your terminal font to `MesloLGS NF` in your terminal application settings for proper theme rendering.
