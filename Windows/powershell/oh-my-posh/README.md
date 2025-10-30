# Oh My Posh Setup Script

This script automates the installation and configuration of Oh My Posh on Windows, including support for displaying Python conda and venv environments.

## What It Does

1. **Checks and installs Oh My Posh** using `winget` if not already installed.
2. **Downloads MesloLGS Nerd Font (Regular)**:
   - Downloads the MesloLGSNerdFont-Regular.ttf file from the Nerd Fonts GitHub release.
   - Provides instructions for manual installation.
3. **Sets execution policy** to `RemoteSigned` for the current user.
4. **Creates PowerShell profile file** if it doesn't exist.
5. **Prompts user to choose a theme** from a list of popular Oh My Posh themes with descriptions and previews.
6. **Creates a modified version of the chosen theme** that includes Python environment display.
7. **Adds Oh My Posh initialization** to the profile file with the modified theme.
8. **Installs and configures PSReadLine module** for enhanced PowerShell features like autocomplete and history.
8. **Reloads the profile** to apply changes immediately.
9. **Tests the setup** and automatically fixes issues if possible.

## Usage

Run the `setup_ohmyposh_complete.ps1` script in PowerShell:

```powershell
.\setup_ohmyposh_complete.ps1
```

## Manual Step

After running the script, install the downloaded font by double-clicking the .ttf file in %TEMP% and clicking Install. Then set "MesloLGS NF" as your terminal font in Windows Terminal settings.

## Features

- Interactive theme selection with descriptions
- Displays Python conda environments when activated
- Displays Python virtual environments (venv) when activated
- Uses a modified version of the selected theme
- Includes git status, path, and other useful segments
- Automated testing and fixing of setup issues
- Enhanced PowerShell features: autocomplete, history search, prediction

## Requirements

- Windows 10 or later
- PowerShell 5.1 or later
- Internet connection for downloading fonts and installing via winget
