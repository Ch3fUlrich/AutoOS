# AutoOS Bash Scripts

This directory contains the main installation scripts for AutoOS Linux and Raspberry Pi OS setup.

## 🚀 Quick Start

Simply run:
```bash
./main.sh
```

The script automatically detects your OS (Linux/Raspberry Pi) and provides appropriate installation options.

## 📁 Files Overview

### Main Scripts

- **`main.sh`** - Interactive menu-driven installer
  - User-friendly interface with clear options
  - Modular installation (choose what to install)
  - Progress tracking and logging
  - Full installation mode available
  - **Automatic OS detection** (Linux/Raspberry Pi)

- **`config.sh`** - Central configuration file
  - Package definitions with descriptions
  - Theme and plugin settings
  - Installation behavior options
  - **Raspberry Pi hardware detection and overrides**
  - Easy to customize for your needs

### Module Scripts (in `modules/`)

- **`utils.sh`** - Core utility functions
  - Error handling
  - User interaction (prompts, confirmations)
  - Package management wrappers
  - Logging functionality
  - System information helpers

- **`core_packages.sh`** - Essential system tools
  - Git, curl, wget, tmux
  - System monitoring tools
  - File management utilities
  - Automation tools

- **`programming.sh`** - Development tools
  - Python 3 with pip and venv
  - C/C++ compiler (clang)
  - Build tools
  - Development utilities

- **`shell_setup.sh`** - Zsh configuration
  - Zsh installation
  - Oh My Zsh framework
  - Powerlevel10k theme
  - Useful plugins and fonts
  - Custom aliases

- **`gnome_setup.sh`** - Desktop enhancements
  - GNOME extensions
  - Tweaks and customization tools
  - Extension Manager
  - UI improvements

- **`gnome-extensions_installer.sh`** - **Advanced GNOME Extension Installer** ⭐ NEW
  - **Interactive grouped installation** with curated extension sets
  - **Apt-first with fallback** - tries apt packages, falls back to extensions.gnome.org
  - **Rich metadata** from `extensions.json` (descriptions, compatibility checks)
  - **GNOME version compatibility** checking (min/max version support)
  - **Raspberry Pi awareness** - respects Pi-specific compatibility flags
  - **CLI modes**: Interactive, non-interactive (`--non-interactive`), dry-run (`--dry-run`)
  - **Install methods**: `--install-method=auto|apt|extensions`
  - **Installation tracking** with rollback helper
  - **Colorized output** — titles, package/extension tags, numeric IDs, and messages are styled for readability
  - **28 curated extensions** in 5 organized groups (Productivity, UI/UX, System, Essentials, Customization)
  - See usage examples below

- **`docker_setup.sh`** - Container platform
  - Docker Engine
  - Portainer Agent
  - User configuration
  - Service management

### Raspberry Pi Modules (in `pi_modules/`)

- **`pi_overrides.sh`** - Raspberry Pi detection and package overrides
  - Hardware detection (Model, RAM, OS version)
  - CPU architecture identification
  - Package compatibility adjustments
  - Pi-specific package substitutions

- **`pi_autologin.sh`** - Pi-specific extras
  - Auto-login configuration
  - Additional Pi optimizations

- **`install_gnome_pi.sh`** - **GNOME Desktop Installation for Raspberry Pi** ⭐ NEW
  - Interactive installation of GNOME Shell on Raspberry Pi OS
  - **Detailed resource warnings** (RAM: 1-1.5GB idle, CPU: 5-15% idle, GPU: continuous usage)
  - **System compatibility checks** (RAM detection, Pi model verification)
  - **Performance optimizations** for Pi hardware
  - Automatic GDM3 configuration
  - Extension Manager integration
  - Post-installation guidance and reboot handling
  - **Not recommended for Pi 4 with 4GB or less**
  - **Optimal for Pi 5 with 8GB RAM**

### Extension Scripts (DEPRECATED - Use `modules/gnome-extensions_installer.sh` instead)

The old `extensions/` folder has been removed. All GNOME extension functionality is now in:
- `modules/gnome-extensions_installer.sh` - Main installer
- `modules/extensions.json` - Extension metadata

## 🎯 Usage Examples

### Basic Usage
```bash
# Interactive menu
./main.sh
```

### GNOME Extensions Installer

The new grouped GNOME extensions installer offers multiple usage modes:

#### Interactive Mode (Default)
```bash
# Run from modules directory
cd modules
bash gnome-extensions_installer.sh

# Or call from main menu (option for GNOME setup)
```

#### Non-Interactive Mode
```bash
# Auto-confirm all prompts (useful for automation)
bash gnome-extensions_installer.sh --non-interactive

# Or set environment variable
AUTO_CONFIRM=true bash gnome-extensions_installer.sh
```

#### Installation Methods
```bash
# Default: Try apt first, fallback to extensions.gnome.org
bash gnome-extensions_installer.sh --install-method=auto

# Apt packages only (no fallback)
bash gnome-extensions_installer.sh --install-method=apt

# Extensions.gnome.org only (direct installs)
bash gnome-extensions_installer.sh --install-method=extensions
```

#### Testing & Debugging
```bash
# Dry-run mode (preview without installing)
bash gnome-extensions_installer.sh --dry-run

# Combine flags
bash gnome-extensions_installer.sh --dry-run --non-interactive --install-method=apt

# Or use environment variable
DRY_RUN=true bash gnome-extensions_installer.sh
```

#### Available Extension Groups
1. **Productivity Extensions** - Clipboard management, notes, todo lists
2. **UI/UX Enhancements** - Panel customization, window management, blur effects
3. **System Extensions** - Monitoring, power management, resource tracking
4. **Important Essentials** - Core packages (GNOME Tweaks, Extension Manager, Dash panels)
5. **Customization Tools** - Themes, icons, appearance tweaks

#### Help
```bash
bash gnome-extensions_installer.sh --help
```

### Raspberry Pi Specific

### Raspberry Pi Specific

When running on Raspberry Pi OS, the scripts automatically:
- Detect Pi hardware (model, RAM, architecture)
- Apply Pi-specific package overrides
- Skip incompatible packages
- **Offer optional GNOME installation** with detailed resource warnings
- Automatically skip the GNOME extensions installer if GNOME Shell is not present
- Use optimized settings for ARM architecture

**GNOME on Raspberry Pi:**
The installer can optionally install GNOME Desktop on Raspberry Pi with:
- Interactive prompts explaining resource requirements
- RAM detection (warns if less than 6GB)
- Detailed CPU, GPU, and RAM usage information
- Performance optimizations for Pi hardware
- Ability to switch back to PIXEL desktop

```bash
# Run extensions installer on Pi without GNOME
./main.sh
# Select GNOME setup - you'll be asked if you want to install GNOME first

# Or directly:
bash Linux/bash/pi_modules/install_gnome_pi.sh
```

**Resource Requirements:**
- RAM: 1.0-1.5 GB idle (PIXEL uses ~400MB)
- CPU: 5-15% idle, 20-40% during use
- GPU: Continuous compositing
- Storage: ~1.5 GB
- Recommended: Pi 5 with 8GB RAM
- Not recommended: Pi 4 with 4GB or less

### Customization
Edit `config.sh` to customize:
```bash
# Add packages
CORE_PACKAGES+=(your-package)

# Change theme
ZSH_THEME="agnoster"

# Add plugins
ZSH_PLUGINS+=(your-plugin)

# Skip confirmations (use with caution!)
AUTO_CONFIRM=true
```

### Direct Module Execution
You can also source and run individual modules:
```bash
# Load configuration and utilities
source config.sh
source modules/utils.sh

# Run specific installation
source modules/core_packages.sh
install_core_packages
```

## 🔧 Advanced Configuration

### Environment Variables

- `AUTO_CONFIRM=true` - Skip all confirmation prompts
- `VERBOSE=true` - Enable verbose output
- `DRY_RUN=true` - Preview actions without executing
- `INSTALL_METHOD=auto|apt|extensions` - Control extension install method

Example:
```bash
AUTO_CONFIRM=true ./main.sh
DRY_RUN=true bash modules/gnome-extensions_installer.sh
```

### Package Customization

Edit `config.sh` to modify package lists:

```bash
CORE_PACKAGES=(
    git
    curl
    your-custom-package
)
```

### Adding New Modules

1. Create a new file in `modules/`
2. Define your installation function
3. Source it in `main.sh`
4. Add menu option in `main_menu()`

Example module template:
```bash
#!/bin/bash
# My custom module

install_my_stuff() {
    section_header "My Custom Installation"
    
    if ! confirm "Install my stuff?"; then
        return 0
    fi
    
    install_packages package1 package2
    
    # Your custom logic here
    
    success_message "Installation complete!"
}
```

## 📊 Logging

Logs are automatically created at:
```
/tmp/autoos-install-YYYYMMDD_HHMMSS.log
```

GNOME extension installations are tracked in:
```
/tmp/autoos-installed-gnome-extensions.log
```

View logs:
- From menu: Select option 7
- Command line: `tail -f /tmp/autoos-install-*.log`

Rollback extensions:
```bash
# View installed extensions
cat /tmp/autoos-installed-gnome-extensions.log

# Use the rollback helper (if available in installer)
bash modules/gnome-extensions_installer.sh --rollback
```

## 🐛 Debugging

Enable verbose mode:
```bash
VERBOSE=true ./main.sh
```

Test without installing:
```bash
DRY_RUN=true ./main.sh
```

Check syntax:
```bash
bash -n main.sh
bash -n modules/*.sh
```

## 🔒 Security

- Requires sudo for package installation
- No external script execution without user confirmation
- All sources are from official repositories
- Actions are logged for audit
- Configuration files are backed up before modification

## 📝 Notes

- Scripts are designed for Ubuntu/Debian-based systems and Raspberry Pi OS
  - Tested on Ubuntu 24.04 LTS
  - Tested on Raspberry Pi OS (Bookworm)
- **Raspberry Pi support**: Automatic hardware detection and optimizations
- **GNOME extensions**: 28 curated extensions with compatibility checking
- Package names may differ on other distributions
- Internet connection required for downloads
- Some changes require logout/login to take effect
- Extension installations tracked for rollback capability

## 🤝 Contributing

Feel free to:
- Add more packages to config.sh
- Create new modules or Pi-specific modules
- Add extensions to extensions.json
- Improve error handling
- Enhance user experience
- Report issues
- Test on different hardware (especially Raspberry Pi models)

---

For more information, see the [main Linux README](../README.md).
