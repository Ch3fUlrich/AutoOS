# Linux Setup (AutoOS)

**AutoOS** is an automated Linux setup system designed to quickly configure a freshly installed Linux system (primarily Ubuntu/Debian) with essential tools, development environments, and desktop customizations.

## 🎯 Purpose

The main goal of AutoOS is to provide a fast, interactive, and reproducible way to set up a new Linux installation, allowing you to start working as quickly as possible. Whether you're setting up a development environment, a workstation, or a server, AutoOS streamlines the process with modular, well-organized scripts.

## 📁 Structure

```
Linux/bash/
├── main.sh                    # Main interactive menu script
├── config.sh                  # Configuration and package definitions
├── modules/                   # Modular installation scripts
│   ├── utils.sh              # Utility functions and helpers
│   ├── core_packages.sh      # Core system packages
│   ├── programming.sh        # Programming languages & tools
│   ├── shell_setup.sh        # Zsh and Oh My Zsh configuration
│   ├── gnome_setup.sh        # GNOME desktop enhancements
│   └── docker_setup.sh       # Docker and Portainer installation
└── extensions/               # GNOME extensions installer
    ├── gnome-extensions_installer.sh
    └── gnome-utils.sh
```

## 🚀 Quick Start

### Prerequisites

1. A fresh Ubuntu/Debian-based Linux installation (Ubuntu 20.04+ recommended)
2. Internet connection
3. Sudo access

### Installation Steps

1. **Update your system:**
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. **Clone the repository:**
   ```bash
   git clone https://github.com/Ch3fUlrich/AutoOS.git
   cd AutoOS/Linux/bash
   ```

3. **Make the main script executable:**
   ```bash
   chmod +x main.sh
   ```

4. **Run the installation script:**
   ```bash
   ./main.sh
   ```

5. **Follow the interactive prompts** to select which components to install

## 📦 What Gets Installed

### Option 1: Core System Packages
Essential command-line tools and utilities:
- **Version Control:** git
- **Network Tools:** curl, wget, wireguard
- **System Monitoring:** htop, nmon, neofetch
- **File Management:** mc (Midnight Commander), rsync, nfs-common
- **Automation:** ansible, cron
- **Terminal:** tmux (terminal multiplexer)

### Option 2: Programming Languages & Tools
Development environment setup:
- **Languages:** Python 3 (with pip, venv), C/C++ (clang)
- **Build Tools:** build-essential
- **Package Managers:** pip, setuptools, wheel
- **Optional:** Node.js and npm (if selected)

### Option 3: Shell Environment Configuration
Enhanced terminal experience:
- **Shell:** Zsh with Oh My Zsh framework
- **Theme:** Powerlevel10k (beautiful, fast, and informative)
- **Plugins:**
  - zsh-autosuggestions (command suggestions)
  - zsh-syntax-highlighting (syntax coloring)
  - git integration
  - z (quick directory navigation)
  - colored man pages
- **Fonts:** MesloLGS Nerd Font (for proper icon rendering)
- **Custom Aliases:** Helpful shortcuts and commands

### Option 4: GNOME Desktop Setup
Desktop environment enhancements (requires GNOME):
- **Extensions Manager:** GUI tool for managing extensions
- **Tweaks:** GNOME Tweaks for customization
- **Extensions:**
  - Dash to Panel (Windows-like taskbar)
  - Arc Menu (application menu)
  - Clipboard History
  - System Monitor
  - And many more via the extensions installer

### Option 5: Docker & Portainer
Container platform for running applications:
- **Docker Engine:** Latest stable version
- **Docker Compose:** Multi-container orchestration
- **Portainer Agent:** Web-based Docker management (port 9001)
- **User Configuration:** Adds user to docker group
- **Auto-start:** Enabled on system boot

### Option 6: Full Installation
Installs all of the above components in sequence with interactive prompts between each step.

## 🎮 Interactive Features

### User-Friendly Interface
- **Clear Menu System:** Easy-to-understand options
- **Detailed Descriptions:** Know what you're installing before proceeding
- **Confirmation Prompts:** Choose exactly what you want
- **Progress Indicators:** Visual feedback during installation
- **Error Handling:** Graceful failure with helpful messages

### Smart Installation
- **Dependency Checking:** Verifies prerequisites before installation
- **Skip Existing:** Detects already-installed software
- **Backup System:** Creates backups of configuration files
- **Logging:** Detailed log file for troubleshooting
- **Modular Design:** Install only what you need

## 📝 Configuration

All configuration is centralized in `config.sh`:

```bash
# Customize package lists
CORE_PACKAGES=(...)
PROGRAMMING_PACKAGES=(...)
GNOME_PACKAGES=(...)

# Configure shell theme and plugins
ZSH_THEME="powerlevel10k/powerlevel10k"
ZSH_PLUGINS=(git zsh-autosuggestions ...)

# Installation behavior
AUTO_CONFIRM=false  # Skip all confirmations
VERBOSE=false       # Show detailed output
DRY_RUN=false      # Preview without installing
```

## 🔧 Post-Installation Steps

### After Installing Shell Environment:
1. **Set Zsh as default shell:**
   ```bash
   chsh -s $(which zsh)
   ```

2. **Log out and log back in** to start using Zsh

3. **Configure your terminal:**
   - Set font to "MesloLGS NF" (any variant)
   - This is required for Powerlevel10k icons

4. **Configure Powerlevel10k theme:**
   ```bash
   p10k configure
   ```
   This will launch an interactive wizard to customize your prompt.

### After Installing Docker:
- **Log out and log back in** for docker group changes to take effect
- Test Docker: `docker run hello-world`
- Access Portainer: Connect from main Portainer instance to `http://your-ip:9001`

### After Installing GNOME Extensions:
- **Restart GNOME Shell:**
  - Press `Alt+F2`
  - Type `r`
  - Press `Enter`
  - Or log out and back in
- **Enable extensions:**
  - Use "Extensions" application
  - Or "GNOME Tweaks"
  - Or "Extension Manager"

## 📊 Viewing Logs

The installation creates a detailed log file at `/tmp/autoos-install-<timestamp>.log`

To view the log from within the script:
- Select **Option 7** from the main menu

To view manually:
```bash
tail -f /tmp/autoos-install-*.log
```

## 🛠️ Troubleshooting

### Package Installation Fails
- Ensure you have internet connectivity
- Try running `sudo apt update` manually
- Check the log file for specific errors

### Docker Group Issues
- You must log out and back in after Docker installation
- Verify with: `groups` (should show 'docker')

### Zsh Not Activating
- Run `chsh -s $(which zsh)`
- Log out completely and log back in
- Check with: `echo $SHELL` (should show /usr/bin/zsh)

### Powerlevel10k Icons Not Showing
- Install MesloLGS NF font (done automatically)
- Configure your terminal to use this font
- Restart your terminal

### GNOME Extensions Not Loading
- Ensure GNOME Shell version is compatible
- Restart GNOME Shell (Alt+F2, type 'r')
- Check extensions with: `gnome-extensions list`

## 🔐 Security Notes

- The script requires sudo access for package installation
- Your password may be requested during installation
- All actions are logged for audit purposes
- No external scripts are executed without confirmation
- Official package repositories and sources are used

## 🤝 Contributing

The modular design makes it easy to:
- Add new packages to `config.sh`
- Create new installation modules in `modules/`
- Extend functionality without breaking existing features
- Customize for your specific needs

## 📚 Additional Resources

- **Main Repository:** [Ch3fUlrich/AutoOS](https://github.com/Ch3fUlrich/AutoOS)
- **Oh My Zsh:** [ohmyz.sh](https://ohmyz.sh/)
- **Powerlevel10k:** [github.com/romkatv/powerlevel10k](https://github.com/romkatv/powerlevel10k)
- **GNOME Extensions:** [extensions.gnome.org](https://extensions.gnome.org/)
- **Docker Docs:** [docs.docker.com](https://docs.docker.com/)

## 📄 License

See the main repository for license information.

---

**Note:** This script is designed for Ubuntu/Debian-based systems. For other distributions, package names and installation methods may differ.

For issues, suggestions, or contributions, please visit the [GitHub repository](https://github.com/Ch3fUlrich/AutoOS).