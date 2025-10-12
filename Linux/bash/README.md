# AutoOS Bash Scripts

This directory contains the main installation scripts for AutoOS Linux setup.

## 🚀 Quick Start

Simply run:
```bash
./main.sh
```

## 📁 Files Overview

### Main Scripts

- **`main.sh`** - Interactive menu-driven installer
  - User-friendly interface with clear options
  - Modular installation (choose what to install)
  - Progress tracking and logging
  - Full installation mode available

- **`config.sh`** - Central configuration file
  - Package definitions with descriptions
  - Theme and plugin settings
  - Installation behavior options
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

- **`docker_setup.sh`** - Container platform
  - Docker Engine
  - Portainer Agent
  - User configuration
  - Service management

### Extension Scripts (in `extensions/`)

- **`gnome-extensions_installer.sh`** - Advanced GNOME extension installer
- **`gnome-utils.sh`** - Helper functions for GNOME management

## 🎯 Usage Examples

### Basic Usage
```bash
# Interactive menu
./main.sh
```

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

Example:
```bash
AUTO_CONFIRM=true ./main.sh
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

View logs:
- From menu: Select option 7
- Command line: `tail -f /tmp/autoos-install-*.log`

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

- Scripts are designed for Ubuntu/Debian-based systems (tested on Ubuntu 24.04 LTS)
- Package names may differ on other distributions
- Internet connection required for downloads
- Some changes require logout/login to take effect

## 🤝 Contributing

Feel free to:
- Add more packages to config.sh
- Create new modules
- Improve error handling
- Enhance user experience
- Report issues

---

For more information, see the [main Linux README](../README.md).
