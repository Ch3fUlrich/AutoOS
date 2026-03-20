# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoOS is a cross-platform post-OS-installation automation toolkit supporting Linux (Ubuntu/Debian), Raspberry Pi, and Windows. It automates tool installation, shell configuration, and system customization.

## Running the Scripts

### Linux/Bash

```bash
# Run the interactive menu installer
cd Linux/bash
bash main.sh

# Environment variable flags (can be combined)
DRY_RUN=true bash main.sh           # Preview without executing
AUTO_CONFIRM=true bash main.sh      # Skip all confirmation prompts
VERBOSE=true bash main.sh           # Show detailed output

# Run individual modules directly (they source utils.sh internally)
bash modules/core_packages.sh
bash modules/shell_setup.sh

# GNOME extensions installer
bash modules/gnome-extensions_installer.sh
bash modules/gnome-extensions_installer.sh --dry-run --non-interactive
INSTALL_METHOD=apt bash modules/gnome-extensions_installer.sh

# Validate bash syntax
bash -n main.sh
bash -n modules/*.sh

# View logs
tail -f /tmp/autoos-install-*.log
```

### Windows/Ansible (run from WSL or Linux)

```bash
# Run full playbook
ansible-playbook -i Windows/ansible/inventory.yml Windows/ansible/main_playbook.yml

# Run specific playbook
ansible-playbook -i Windows/ansible/inventory.yml Windows/ansible/playbooks/chocolatey_software.yml

# Check mode (dry run)
ansible-playbook -i Windows/ansible/inventory.yml Windows/ansible/main_playbook.yml --check
```

### Windows/PowerShell

```powershell
# Run the Oh My Posh interactive setup
.\Windows\powershell\oh-my-posh\main.ps1

# Repair corrupted PowerShell profile
.\Windows\powershell\oh-my-posh\fix_profile.ps1
```

## Architecture

### Linux/Bash — Modular Source Pattern

`main.sh` is the interactive menu entry point. It sources modules rather than subshelling them, so all modules share the same environment:

- **`config.sh`** — All configuration lives here: package lists (`CORE_PACKAGES`, `PROGRAMMING_PACKAGES`, `GNOME_PACKAGES`), ZSH theme/plugins, behavior flags. Edit this to add/remove packages.
- **`modules/utils.sh`** — Shared utilities used by every module: colored output (`cecho`, `info`, `ok`, `warn`, `err`), `install_packages`, `confirm`, `is_raspberry_pi`, `get_distro`, logging. Guard-loaded via `AUTOOS_UTILS_LOADED`.
- **`modules/`** — One file per feature: `core_packages.sh`, `programming.sh`, `shell_setup.sh`, `gnome_setup.sh`, `docker_setup.sh`.
- **`pi_modules/`** — Raspberry Pi overrides loaded conditionally when Pi hardware is detected. `pi_overrides.sh` substitutes incompatible packages.

**GNOME extension installer** is more complex — split across three files:
- `gnome-extensions-core.sh` — Parses `extensions.json`, handles compatibility checks
- `gnome-extensions-platform.sh` — Pi-specific behavior
- `gnome-extensions_installer.sh` — Main installer with APT-first, gnome-extensions-cli fallback strategy

`extensions.json` is the canonical metadata source for all GNOME extensions: IDs, names, GNOME version compatibility ranges (`min_version`/`max_version`), Raspberry Pi support (`pi_support`: `"yes"/"maybe"/"no"`), and install grouping.

### Windows/Ansible — Import-Based Playbooks

`main_playbook.yml` imports sub-playbooks. Each playbook is standalone and can be run independently. Connection uses SSH (not WinRM) — credentials in `inventory.yml`.

### Windows/PowerShell — Oh My Posh Setup

`main.ps1` orchestrates numbered module scripts. `configure_profile.ps1` uses version detection to write PSReadLine options compatible with both PS 5.x and 7.x, and auto-detects fzf/rg paths.

## Bash Conventions

- Use `[[ ]]` for conditionals (not `[ ]`)
- Always double-quote variable expansions
- Scripts that should only be sourced once use a guard: `[[ -n "${AUTOOS_UTILS_LOADED:-}" ]] && return 0`
- Prefer `set -Eeuo pipefail` with trap-based error handlers (see `gnome-extensions_installer.sh` as reference)
- Log to `/tmp/autoos-install-YYYYMMDD_HHMMSS.log` using the `log()`/`log_info()`/`log_error()` functions from `utils.sh`
- Pi detection: always use `is_raspberry_pi()` from `utils.sh` — do not hardcode hostname checks
