# Linux Automation Tools

This directory contains comprehensive automation tools for Linux systems, primarily focused on Ubuntu/Debian distributions. The tools provide post-installation setup, network configuration, and automated OS installation capabilities.

## 📁 Directory Structure

```
Linux/
├── bash/                 # Modular bash scripts for post-installation setup
├── ubuntu_autoinstall/   # Ubuntu automated installation configuration
├── Network/              # Network configuration tools (eduroam setup)
└── README.md             # This file
```

## 🚀 Quick Start

### Interactive Setup (Recommended)
```bash
cd Linux/bash
./install.sh
```

### Automated Full Setup
```bash
cd Linux/bash
./main.sh
```

## 📦 Available Tools

### 1. Bash Scripts (`bash/`)
**Purpose**: Modular post-installation setup for Ubuntu/Debian systems

**Features**:
- Core system packages installation
- Programming languages and development tools
- Shell environment configuration (Zsh, Oh My Zsh)
- GNOME desktop customization
- Docker and Portainer setup
- Gnome extensions management

**Usage**:
- **Interactive mode**: `./install.sh` - Choose specific modules
- **Full installation**: `./main.sh` - Install everything
- **Configuration**: Edit `config.sh` to customize installations

**Supported Systems**: Ubuntu 24.04 LTS, Debian-based distributions

### 2. Ubuntu Autoinstall (`ubuntu_autoinstall/`)
**Purpose**: Automated Ubuntu installation configuration

**Features**:
- Unattended Ubuntu installation
- Pre-configured user accounts and networking
- LVM storage configuration
- Custom partition layouts

**Usage**: Use during Ubuntu installation process with `autoinstall.yml`

### 3. Network Configuration (`Network/`)
**Purpose**: Automated network setup tools

**Features**:
- Eduroam WiFi configuration for university networks
- Automated certificate and credential setup

**Usage**: Run the Python script for your institution's eduroam setup

## 🔧 Installation Methods

### Method 1: Clone and Run
```bash
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Linux/bash
chmod +x install.sh
./install.sh
```

### Method 2: Direct Download and Execute
```bash
curl -fsSL https://raw.githubusercontent.com/Ch3fUlrich/AutoOS/main/Linux/bash/install.sh | bash
```

## 📋 Module Overview

| Module | Purpose | Files |
|--------|---------|--------|
| **Core Packages** | Essential system tools and utilities | `modules/core_packages.sh` |
| **Programming** | Development tools (Python, Node.js, VS Code) | `modules/programming.sh` |
| **Shell Setup** | Zsh, Oh My Zsh, custom themes | `modules/shell_setup.sh` |
| **GNOME Setup** | Desktop customization and themes | `modules/gnome_setup.sh` |
| **Docker** | Container platform and Portainer | `modules/docker_setup.sh` |
| **Extensions** | GNOME shell extensions | `extensions/` |

## ⚙️ Configuration

### Customizing Installation
Edit `bash/config.sh` to modify:
- Default packages lists
- User preferences
- Installation paths
- Feature toggles

### Example Configuration
```bash
# Enable/disable modules
INSTALL_DOCKER=true
INSTALL_PROGRAMMING=true
INSTALL_GNOME_EXTENSIONS=false

# Custom package lists
EXTRA_PACKAGES="htop tree curl git"
```

## 🔍 Troubleshooting

### Common Issues

**Permission Errors**:
```bash
chmod +x *.sh
sudo chown $USER:$USER ~/.config/
```

**Package Installation Fails**:
```bash
sudo apt update && sudo apt upgrade
sudo apt --fix-broken install
```

**Network Configuration Issues**:
- Ensure university credentials are correct
- Check if institution supports eduroam
- Verify certificate installation

### Debug Mode
Run scripts with debug output:
```bash
bash -x ./install.sh
```

## 🧪 Testing

### Supported Environments
- ✅ Ubuntu 24.04 LTS
- ✅ Ubuntu 22.04 LTS  
- ✅ Debian 12
- 🔄 Pop!_OS (partial support)

### Pre-Installation Testing
Test in a virtual machine first:
```bash
# Create VM snapshot before running
./install.sh --dry-run  # Preview what will be installed
```

## 🔗 Related Documentation

- [Bash Scripts Detailed Guide](bash/README.md)
- [Ubuntu Autoinstall Guide](ubuntu_autoinstall/README.md)
- [Network Setup Guide](Network/README.md)
- [Main Project Documentation](../README.md)

## 📝 Examples

### Installing Development Environment Only
```bash
cd Linux/bash
./main.sh
# Select option 2: "Install Programming Languages & Tools"
```

### Custom GNOME Setup
```bash
cd Linux/bash
./modules/gnome_setup.sh
./extensions.sh
```

### Automated Eduroam Setup
```bash
cd Linux/Network
python3 eduroam-linux-Universitat_Basel-UniBasel.py
```

---

**Note**: Always backup your system before running automation scripts. Test in a virtual environment when possible.