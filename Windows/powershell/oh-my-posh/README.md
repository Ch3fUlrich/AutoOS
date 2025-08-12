# Oh My Posh Setup Script

This script automates the installation and configuration of Oh My Posh on Windows.

## What It Does

1. **Installs Oh My Posh** using `winget`.
2. **Downloads and installs Meslo Nerd Font**:
   - Downloads the Meslo font zip from the Nerd Fonts GitHub release.
   - Extracts the fonts and opens each `.ttf` file for installation.
3. **Creates PowerShell profile file** if it doesn't exist.
4. **Adds Oh My Posh initialization** to the profile file.
5. **Sets execution policy** to `RemoteSigned` for the current user.

## Manual Step

After running the script, open your terminal settings and set the font to `MesloLGS NF` to ensure proper rendering of Oh My Posh themes.
