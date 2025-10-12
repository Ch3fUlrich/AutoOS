# Automated Ubuntu Installation

This folder contains the autoinstall configuration file for automating Ubuntu 24.04 LTS installation.

## Overview

The `autoinstall.yml` file in this directory provides a complete, unattended installation configuration for Ubuntu Server. It includes:
- System identity configuration (hostname, username, password)
- Network setup (DHCP or static IP)
- LVM-based storage layout
- Essential packages for development and system administration
- Post-installation setup (Oh My Zsh, Powerlevel10k theme, plugins)

## Quick Start

For detailed step-by-step instructions on using this autoinstall configuration, please refer to:
**[ubuntu_autoinstall/README.md](../../ubuntu_autoinstall/README.md)**

The comprehensive guide covers:
- Prerequisites and preparation
- Creating bootable media with autoinstall
- Configuration customization
- Network and HTTP-based installation methods
- Troubleshooting common issues

## Configuration File

The `autoinstall.yml` file is structured as follows:

### 1. System Identity
```yaml
identity:
  hostname: ubuntu-server
  username: ubuntu
  password: "$6$..."  # Hashed password
```

### 2. Network Configuration
```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
```

### 3. Storage Layout
Uses LVM with:
- 1GB boot partition
- 20GB root partition (with 18GB for root filesystem and 2GB swap)
- Automatic disk selection

### 4. Pre-installed Packages
Includes development tools, system utilities, and automation tools:
- Python 3 with pip, venv, and wheel
- Git, Ansible, Zsh
- Neovim, tmux, htop
- WireGuard for VPN
- And more...

### 5. Post-Installation Automation
The configuration automatically:
- Installs xPipe for connection management
- Configures Oh My Zsh with Powerlevel10k theme
- Installs zsh plugins (autosuggestions, syntax-highlighting)
- Downloads and installs MesloLGS Nerd Font
- Sets up enhanced shell environment

## Customization

Before using this configuration, customize the following:

1. **Password**: Generate a new password hash:
   ```bash
   mkpasswd --method=SHA-512 --rounds=4096
   ```

2. **Hostname and Username**: Update to match your requirements

3. **Network**: Adjust network interface name and settings if needed

4. **Packages**: Add or remove packages based on your needs

5. **Timezone**: Change from UTC to your timezone:
   ```yaml
   user-data:
     timezone: America/New_York  # Change as needed
   ```

## Integration with AutoOS

After the automated installation completes, you can further customize your system using the AutoOS Linux scripts:

```bash
# Navigate to the Linux automation scripts
cd /home/ubuntu
git clone https://github.com/Ch3fUlrich/AutoOS.git
cd AutoOS/Linux/bash

# Run the main setup script
bash main.sh
```

This will apply additional configurations from the modular bash scripts in the `Linux/bash/modules/` directory.

## Future Improvements

Planned enhancements:
- [ ] Create modular bash scripts for specific software installations:
  - Terminal design and customization
  - Programming language environments
  - Dotfiles management
- [ ] Modify autoinstall.yml to download scripts from AutoOS repository
- [ ] Add support for multiple installation profiles (minimal, developer, server)
- [ ] Include automated testing for autoinstall configurations

## Resources

- [Ubuntu Autoinstall Documentation](https://ubuntu.com/server/docs/install/autoinstall)
- [AutoOS Main Documentation](../../README.md)
- [Linux Setup Guide](../README.md)

---
For complete usage instructions and troubleshooting, see [ubuntu_autoinstall/README.md](../../ubuntu_autoinstall/README.md)

