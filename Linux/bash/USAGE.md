# AutoOS Usage Guide

This guide provides practical examples and workflows for using AutoOS installation scripts.

## 🚀 Basic Usage

### Quick Start (Interactive Mode)

The simplest way to use AutoOS:

```bash
cd AutoOS/Linux/bash
./main.sh
```

You'll see an interactive menu where you can choose what to install.

### Example Session

```
╔════════════════════════════════════════════════════════════════╗
║                    AutoOS Installation Script                  ║
║                  Fast Linux Setup & Configuration              ║
╚════════════════════════════════════════════════════════════════╝

System Information:
  Distribution: Ubuntu 24.04
  Kernel: 6.11.0-1018-azure
  User: youruser
  Log file: /tmp/autoos-install-20251012_083258.log

╔════════════════════════════════════════════════════════════════╗
║                     AutoOS Setup Menu                          ║
╚════════════════════════════════════════════════════════════════╝

  1) Install Core System Packages
     └─ Essential tools: git, curl, htop, ansible, etc.

  2) Install Programming Languages & Tools
     └─ Python, C/C++, build tools

  3) Configure Shell Environment
     └─ Zsh, Oh My Zsh, Powerlevel10k theme

  4) Setup GNOME Desktop
     └─ Extensions, tweaks, and customizations

  5) Install Docker & Portainer
     └─ Container platform and management

  6) Install Everything (Full Setup)
     └─ All of the above in sequence

  7) View Installation Log

  8) Exit

════════════════════════════════════════════════════════════════
Enter your choice (1-8):
```

## 📋 Usage Scenarios

### Scenario 1: New Workstation Setup

You just installed Ubuntu on your laptop and want a complete development environment:

1. **Run full installation:**
   ```bash
   ./main.sh
   # Select option 6 (Install Everything)
   # Confirm when prompted
   ```

2. **Post-installation steps:**
   ```bash
   # Log out and log back in
   
   # Set Zsh as default shell
   chsh -s $(which zsh)
   
   # Configure Powerlevel10k
   p10k configure
   ```

3. **Verify installations:**
   ```bash
   # Check Python
   python3 --version
   
   # Check Docker
   docker --version
   
   # Check Zsh
   echo $SHELL
   ```

### Scenario 2: Minimal Server Setup

You're setting up a server and only need core tools and Docker:

1. **Install core packages:**
   ```bash
   ./main.sh
   # Select option 1 (Install Core System Packages)
   # Confirm installation
   ```

2. **Install Docker:**
   ```bash
   # From the menu, select option 5
   # Confirm Docker installation
   # Skip Portainer if not needed
   ```

3. **Exit and test:**
   ```bash
   # Log out and log back in for docker group
   docker run hello-world
   ```

### Scenario 3: Development Environment Only

You want Python and C++ development tools without desktop enhancements:

```bash
./main.sh
# Select option 2 (Programming Languages & Tools)
# Select option 3 (Shell Environment) for better terminal
# Exit when done
```

### Scenario 4: GNOME Desktop Customization

You already have tools installed but want to enhance your GNOME desktop:

```bash
./main.sh
# Select option 4 (Setup GNOME Desktop)
# Confirm extension installation when prompted
# Restart GNOME Shell: Alt+F2, type 'r', press Enter
```

## 🔧 Advanced Usage

### Customizing Package Lists

Edit `config.sh` before running:

```bash
nano config.sh

# Add your packages
CORE_PACKAGES+=(
    neovim
    ranger
    fzf
)

# Save and run
./main.sh
```

### Non-Interactive Mode

For automation or scripting:

```bash
# Skip all confirmations (use with caution!)
AUTO_CONFIRM=true ./main.sh
```

### Dry Run Mode

Preview what would be installed without actually installing:

```bash
DRY_RUN=true ./main.sh
```

### Verbose Mode

See detailed output for debugging:

```bash
VERBOSE=true ./main.sh
```

### Combining Options

```bash
VERBOSE=true DRY_RUN=true ./main.sh
```

## 📦 Individual Module Usage

You can also use individual modules directly:

### Install Only Core Packages

```bash
# Load dependencies
source config.sh
source modules/utils.sh

# Run specific module
source modules/core_packages.sh
install_core_packages
```

### Install Only Docker

```bash
source config.sh
source modules/utils.sh
source modules/docker_setup.sh
install_docker_stack
```

### Configure Shell Only

```bash
source config.sh
source modules/utils.sh
source modules/shell_setup.sh
configure_shell_environment
```

## 🔍 Troubleshooting

### View Installation Logs

During installation:
```bash
# From the main menu, select option 7
```

After installation:
```bash
# View the latest log
tail -f /tmp/autoos-install-*.log

# Or search for errors
grep -i error /tmp/autoos-install-*.log
```

### Check What Was Installed

```bash
# Check installed packages
dpkg -l | grep -E "python3|docker|zsh"

# Check running services
systemctl status docker

# Check shell
echo $SHELL
zsh --version
```

### Common Issues

**Issue: Docker group not working**
```bash
# Solution: Log out and back in, then verify
groups | grep docker
```

**Issue: Zsh not default**
```bash
# Solution: Set it manually
chsh -s $(which zsh)
# Then log out and back in
```

**Issue: Fonts not displaying correctly**
```bash
# Solution: Configure terminal font
# In GNOME Terminal: Preferences → Profile → Font
# Select "MesloLGS NF Regular"
```

**Issue: GNOME extensions not loading**
```bash
# Solution: Restart GNOME Shell
# Press Alt+F2, type 'r', press Enter
# Or log out and back in
```

## 🎯 Best Practices

### Before Installation

1. **Update your system:**
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. **Have a stable internet connection**

3. **Know your sudo password**

4. **Backup important data** (if modifying existing system)

### During Installation

1. **Read descriptions** - Know what you're installing
2. **Answer prompts carefully** - Confirmations are there for a reason
3. **Don't interrupt** - Let processes complete
4. **Watch for errors** - Check output for issues

### After Installation

1. **Review the log file** - Check for any warnings
2. **Test installations** - Verify everything works
3. **Log out/in** - Apply group changes
4. **Configure tools** - Customize to your needs

## 📚 Next Steps

After installing AutoOS components:

### For Developers

```bash
# Create Python virtual environment
python3 -m venv myproject
source myproject/bin/activate

# Install development tools
pip install pytest black pylint
```

### For System Administrators

```bash
# Setup Ansible playbooks
cd ~/apps
git clone your-ansible-repo

# Configure Docker containers
docker-compose up -d
```

### For Desktop Users

```bash
# Configure GNOME extensions
gnome-extensions-app

# Customize appearance
gnome-tweaks
```

## 🔗 Related Documentation

- [Main README](README.md) - Overview and installation
- [Linux README](../../Linux/README.md) - Linux-specific information
- [config.sh](config.sh) - Configuration reference

## 💡 Tips and Tricks

### Faster Installations

```bash
# Use parallel installations (APT does this automatically)
# Or skip unnecessary packages in config.sh
```

### Custom Configurations

```bash
# Create your own config file
cp config.sh my-config.sh
# Edit my-config.sh with your preferences
# Source it before running modules
```

### Automation

```bash
# Create a script for your specific setup
cat > my-setup.sh << 'EOF'
#!/bin/bash
source config.sh
source modules/utils.sh
install_core_packages
install_docker_stack
EOF
chmod +x my-setup.sh
./my-setup.sh
```

---

**Need help?** Check the [GitHub Issues](https://github.com/Ch3fUlrich/AutoOS/issues) or create a new one.
