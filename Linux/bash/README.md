# Linux Bash Automation Scripts

This directory contains modular bash scripts for comprehensive post-installation setup of Linux systems, specifically designed for Ubuntu/Debian distributions.

## 🎯 Purpose

The bash scripts automate the tedious process of setting up a fresh Linux installation by providing:
- **Modular Installation**: Choose specific components to install
- **Interactive Setup**: User-friendly menu-driven interface
- **Comprehensive Coverage**: Development tools, desktop customization, and system utilities
- **Tested Compatibility**: Verified on Ubuntu 24.04 LTS and Debian-based distributions

## 📁 Directory Structure

```
bash/
├── main.sh              # Interactive main menu script
├── install.sh           # Quick installation launcher
├── config.sh            # Configuration and settings
├── extensions.sh        # GNOME extensions installer
├── modules/             # Individual installation modules
│   ├── core_packages.sh    # Essential system packages
│   ├── programming.sh      # Development tools and languages
│   ├── shell_setup.sh      # Zsh and terminal configuration
│   ├── gnome_setup.sh      # Desktop environment setup
│   └── utils.sh            # Utility functions
└── extensions/          # GNOME extension management
    ├── gnome-extensions_installer.sh
    └── gnome-utils.sh
```

## 🚀 Quick Start

### Interactive Installation (Recommended)
```bash
cd Linux/bash
chmod +x install.sh
./install.sh
```

### Full Automatic Installation
```bash
./main.sh
# Select option 6: "Install Everything"
```

### Individual Module Installation
```bash
# Install only development tools
./modules/programming.sh

# Setup shell environment only
./modules/shell_setup.sh

# Configure GNOME desktop
./modules/gnome_setup.sh
```

## 📦 Available Modules

| Module | Description | Key Software |
|--------|-------------|--------------|
| **Core Packages** | Essential system tools | curl, git, htop, tree, vim |
| **Programming** | Development environment | Python, Node.js, VS Code, Docker |
| **Shell Setup** | Terminal enhancement | Zsh, Oh My Zsh, custom themes |
| **GNOME Setup** | Desktop customization | Themes, icons, extensions |
| **Extensions** | GNOME shell extensions | User themes, dash-to-dock, tweaks |

## ⚙️ Configuration

### Customizing Installation
Edit `config.sh` to modify default settings:
```bash
# Example configurations
INSTALL_DOCKER=true
INSTALL_VSCODE=true
DEFAULT_SHELL="zsh"
GNOME_THEME="Adwaita-dark"
```

### Environment Variables
```bash
# Set before running scripts
export SKIP_UPDATES=false
export VERBOSE_OUTPUT=true
export AUTO_CONFIRM=false
```

## 🔧 Usage Examples

### Development Environment Setup
```bash
# Complete development setup
./install.sh
# Select: Core Packages + Programming + Shell Setup
```

### Minimal System Setup
```bash
# Essential tools only
./modules/core_packages.sh
./modules/shell_setup.sh
```

### Desktop Customization
```bash
# GNOME desktop enhancement
./modules/gnome_setup.sh
./extensions.sh
```

## 🧪 Supported Systems

- ✅ **Ubuntu 24.04 LTS** (Primary target)
- ✅ **Ubuntu 22.04 LTS** 
- ✅ **Debian 12** (Bookworm)
- 🔄 **Pop!_OS** (Partial support)
- 🔄 **Linux Mint** (Partial support)

## 🔍 Troubleshooting

### Common Issues

**Permission Denied**:
```bash
chmod +x *.sh
chmod +x modules/*.sh
chmod +x extensions/*.sh
```

**Package Installation Fails**:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt --fix-broken install
```

**Shell Configuration Issues**:
```bash
# Reset shell configuration
chsh -s /bin/bash
# Re-run shell setup
./modules/shell_setup.sh
```

### Debug Mode
Run scripts with verbose output:
```bash
bash -x ./install.sh
# or
export VERBOSE_OUTPUT=true
./install.sh
```

## 💡 Features

### Interactive Menu System
- Choose specific components to install
- Skip already installed software
- Progress indicators and status messages
- Error handling and recovery options

### Idempotent Operations
- Safe to run multiple times
- Detects existing installations
- Updates configurations as needed
- Preserves user customizations

### Modular Design
- Independent modules for specific tasks
- Clear separation of concerns
- Easy to extend and customize
- Minimal dependencies between modules

## 🔗 Related Documentation

- [Linux Automation Overview](../README.md)
- [Ubuntu Autoinstall Guide](../ubuntu_autoinstall/README.md)
- [Main AutoOS Documentation](../../README.md)

---

**Note**: Always review scripts before execution. Test in a virtual machine environment when possible. Backup important data before running system-wide changes.