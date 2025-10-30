# Oh My Posh — modular Windows installer

This folder contains modular PowerShell scripts to install and configure Oh My Posh and useful shell tooling on Windows. Use `main.ps1` as the orchestrator — either interactively or non-interactively.

Quick overview
- `main.ps1` — interactive orchestrator, pick components or run everything.
- `modules/install_ohmyposh.ps1` — install or verify the Oh My Posh binary (winget).
- `modules/install_fonts.ps1` — downloads and installs the MesloLGS Nerd Font (regular or full set).
- `modules/install_tools.ps1` — installs CLI tools (fzf, ripgrep) via winget.
- `modules/install_modules.ps1` — installs PowerShell modules: PSReadLine, PSFzf, Terminal-Icons, z, posh-git.
- `modules/install_theme.ps1` — export/choose/modify an Oh My Posh theme and enable Python venv display.
- `modules/configure_profile.ps1` — writes a safe, version-aware PowerShell profile that initializes Oh My Posh and loads modules.
- `fix_profile.ps1` — helper to backup/clean and regenerate your Windows PowerShell profile if it contains malformed lines.

Why modular?
- Easier to test and maintain each responsibility separately.
- You can run just the font step, just the module step, or everything with a single command.

How to run

Interactive (recommended):

```powershell
# from this directory
.\main.ps1
```

Non-interactive (run all):

```powershell
.\main.ps1 --all
# or: .\main.ps1 0
```

You can also pass a comma-separated list of component numbers (for example `1,3` to run Oh My Posh + Tools).

Examples

- Run only fonts and theme interactively: run `.\main.ps1` then enter `2,5` at the prompt.
- Run everything unattended: `.\main.ps1 --all` (this will also run the profile fixer before configuring the profile).

Notes and troubleshooting

- Fonts: After installing fonts, open Windows Terminal → Settings → Profiles → Defaults → Font face and select `MesloLGS NF` (or the Meslo MesloLGS variant provided) and restart the terminal.
- Profile corruption: If your profile previously had malformed lines (for example a stray line starting with `=`), `fix_profile.ps1` will back it up as `Microsoft.PowerShell_profile.ps1.bak`, remove malformed lines, and regenerate a canonical profile using `modules/configure_profile.ps1`.
- PSReadLine: The installer writes version-aware PSReadLine options. Older PSReadLine versions will skip unsupported options.
- fzf/WinGet: If `fzf` installs but is not immediately found, the scripts attempt to add the WinGet Links folder to PATH for the current session.

Editing or customizing the theme
- `modules/install_theme.ps1` exports the default theme to `~\.oh-my-posh\theme.omp.json`, modifies the JSON to enable Python virtual env segment, and saves the file. You can further customize that JSON or place your preferred theme anywhere and point the profile to it.

Development notes
- Canonical scripts live under `modules/`. Root-level copies are archived under `deprecated/` to preserve originals.
- The orchestrator (`main.ps1`) will call `fix_profile.ps1` automatically when the Profile component is selected.

If something breaks
- Run the fixer in dry-run first:

```powershell
.\fix_profile.ps1 -WhatIf
```

- Then run it to repair the profile:

```powershell
.\fix_profile.ps1
```

If you still have errors, copy/paste the output (or the first ~40 lines of `$PROFILE`) and I'll help patch it.

License & attribution
- This repository assembles existing tooling (Oh My Posh, Nerd Fonts, winget packages, and PSGallery modules). Respect upstream licenses for those projects.

Enjoy — if you'd like I can convert this to a one-shot installer (single-file) or add a `--non-interactive` preset with custom theme selection.
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
