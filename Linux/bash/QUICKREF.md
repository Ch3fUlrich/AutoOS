# AutoOS Quick Reference

## 🚀 Getting Started

```bash
cd AutoOS/Linux/bash
./main.sh
```

## 📋 Menu Options

1. **Core System Packages** - git, curl, htop, ansible, tmux, etc.
2. **Programming Tools** - Python, C/C++, build tools
3. **Shell Environment** - Zsh + Oh My Zsh + Powerlevel10k
4. **GNOME Desktop** - Extensions and customizations
5. **Docker & Portainer** - Container platform
6. **Full Setup** - Install everything
7. **View Log** - Show installation log
8. **Exit** - Quit installer

## 🎯 Quick Commands

```bash
# Run with verbose output
VERBOSE=true ./main.sh

# Preview without installing (dry run)
DRY_RUN=true ./main.sh

# Skip all confirmations (use carefully!)
AUTO_CONFIRM=true ./main.sh

# Check script syntax
bash -n main.sh

# View latest log
tail -f /tmp/autoos-install-*.log
```

## ⚙️ Customization

Edit `config.sh` to customize:

```bash
# Add packages
CORE_PACKAGES+=(your-package)

# Change theme
ZSH_THEME="agnoster"

# Add plugins
ZSH_PLUGINS+=(your-plugin)
```

## 🔧 Post-Installation

### After Shell Setup
```bash
chsh -s $(which zsh)  # Set Zsh as default
# Log out and back in
p10k configure        # Configure theme
```

### After Docker Install
```bash
# Log out and back in for group changes
docker run hello-world  # Test Docker
```

### After GNOME Setup
```bash
# Restart GNOME Shell: Alt+F2, type 'r', Enter
# Or log out and back in
```

## 📚 Documentation Files

- `README.md` - Complete guide
- `USAGE.md` - Usage scenarios and examples
- `CHANGES.md` - Summary of improvements
- `config.sh` - Configuration reference

## 🐛 Troubleshooting

### View Logs
```bash
# From menu: Select option 7
# Or manually:
tail -f /tmp/autoos-install-*.log
```

### Common Issues

**Docker group not working?**
```bash
# Log out and back in, then check:
groups | grep docker
```

**Zsh not default?**
```bash
chsh -s $(which zsh)
# Log out and back in
```

**Fonts not showing?**
- Set terminal font to "MesloLGS NF"
- In GNOME Terminal: Preferences → Profile → Font

## 🏗️ File Structure

```
Linux/bash/
├── main.sh           # Main script (run this)
├── config.sh         # Configuration
├── modules/          # Installation modules
│   ├── utils.sh
│   ├── core_packages.sh
│   ├── programming.sh
│   ├── shell_setup.sh
│   ├── gnome_setup.sh
│   └── docker_setup.sh
└── extensions/       # GNOME tools
```

## ✅ What Gets Installed

### Option 1: Core (13 packages)
git, curl, wget, tmux, wireguard, htop, nmon, neofetch, mc, ansible, cron, nfs-common, rsync

### Option 2: Programming (9 packages)
clang, python3, python3-pip, python3-venv, python3-setuptools, python3-wheel, build-essential, nodejs, npm

### Option 3: Shell
Zsh, Oh My Zsh, Powerlevel10k theme, plugins (autosuggestions, syntax-highlighting), MesloLGS fonts

### Option 4: GNOME (8 packages)
gnome-shell-extensions, gnome-tweaks, extension-manager, dash-to-panel, arc-menu, clipboard-indicator, gpaste, system-monitor

### Option 5: Docker
Docker Engine, Docker Compose, Portainer Agent

## 💡 Tips

- **Always test in a VM first**
- **Read descriptions before confirming**
- **Check logs for any issues**
- **Customize config.sh for your needs**
- **Use dry-run mode to preview**

## 🔗 Links

- Repository: https://github.com/Ch3fUlrich/AutoOS
- Issues: https://github.com/Ch3fUlrich/AutoOS/issues

---

**Quick Start:** `cd AutoOS/Linux/bash && ./main.sh`
