MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/modules/utils.sh" ]; then
  source "$MODULE_DIR/modules/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
  source "$MODULE_DIR/../modules/utils.sh"
fi
#!/bin/bash
# Configuration settings for AutoOS Linux installation

# Ensure utility helpers are available (colors, logging)
if [ -z "${AUTOOS_UTILS_LOADED:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$SCRIPT_DIR/modules/utils.sh" ]; then
    source "$SCRIPT_DIR/modules/utils.sh"
  elif [ -f "$SCRIPT_DIR/../modules/utils.sh" ]; then
    source "$SCRIPT_DIR/../modules/utils.sh"
  fi
fi

# ============================================
# CORE SYSTEM PACKAGES
# ============================================
CORE_PACKAGES=(
    git              # Version control system
    curl             # Command line tool for transferring data
    wget             # Network downloader
    tmux             # Terminal multiplexer
    wireguard        # VPN solution
    htop             # Interactive process viewer
    nmon             # Performance monitoring tool
    neofetch         # System information tool
    mc               # Midnight Commander file manager
    ansible          # IT automation tool
    cron             # Job scheduler
    nfs-common       # NFS client support
    rsync            # File synchronization tool
)

CORE_DESCRIPTION="Essential system utilities for daily operations:
  - Version control (git)
  - Network tools (curl, wget, wireguard)
  - System monitoring (htop, nmon, neofetch)
  - File management (mc, rsync, nfs-common)
  - Automation (ansible, cron)
  - Terminal multiplexing (tmux)"

# ============================================
# PROGRAMMING TOOLS & LANGUAGES
# ============================================
PROGRAMMING_PACKAGES=(
    clang                 # C/C++ compiler
    python3               # Python 3 interpreter
    python3-pip           # Python package installer
    python3-venv          # Python virtual environment
    python3-setuptools    # Python package utilities
    python3-wheel         # Python wheel support
    build-essential       # Essential build tools
    nodejs                # Node.js runtime
    npm                   # Node package manager
)

PROGRAMMING_DESCRIPTION="Development tools and programming languages:
  - C/C++ development (clang, build-essential)
  - Python 3 with package management (pip, venv)
  - Build tools and compilers
  - Includes: Node.js and npm"

# ============================================
# GNOME DESKTOP ENHANCEMENTS
# ============================================
GNOME_PACKAGES=(
    gnome-shell-extensions                   # Base extensions support
    gnome-tweaks                             # GNOME customization tool
    gnome-shell-extension-manager            # Extension manager GUI
    gnome-shell-extension-dash-to-panel      # Windows-like taskbar
    gnome-shell-extension-arc-menu           # Application menu
    gnome-shell-extension-clipboard-indicator # Clipboard manager
    gnome-shell-extension-gpaste             # Advanced clipboard
    gnome-shell-extension-system-monitor     # System monitor in panel
)

GNOME_DESCRIPTION="GNOME Desktop enhancements and extensions:
  - Extension management tools
  - Taskbar and menu improvements
  - Clipboard management
  - System monitoring in top panel
  - UI customization options"

# ============================================
# SHELL CONFIGURATION
# ============================================
ZSH_THEME="powerlevel10k/powerlevel10k"
ZSH_PLUGINS=(
    git                      # Git aliases and functions
    zsh-autosuggestions      # Command suggestions based on history
    zsh-syntax-highlighting  # Syntax highlighting for commands
    z                        # Quick directory jumping
    colored-man-pages        # Colorful man pages
)

SHELL_DESCRIPTION="Zsh shell with Oh My Zsh framework:
  - Powerlevel10k theme (beautiful and fast)
  - Auto-suggestions and syntax highlighting
  - Git integration
  - Enhanced history and navigation
  - Custom fonts for proper rendering"

# ============================================
# DOCKER CONFIGURATION
# ============================================
DOCKER_DESCRIPTION="Docker container platform:
  - Docker Engine (container runtime)
  - Docker Compose (multi-container apps)
  - Portainer Agent (web-based management)
  - User group configuration
  - Auto-start on boot"

# ============================================
# FONTS
# ============================================
REQUIRED_FONTS=(
    "MesloLGS NF Regular"
    "MesloLGS NF Bold"
    "MesloLGS NF Italic"
    "MesloLGS NF Bold Italic"
)

FONT_BASE_URL="https://github.com/romkatv/powerlevel10k-media/raw/master"

# ============================================
# GNOME EXTENSIONS (for manual installation)
# ============================================
declare -A GNOME_EXTENSIONS=(
    # Extension configuration: ID:Name|min_version|max_version
    [800]="Lock Keys|3.36|44"
    [1506]="Notification Banner Reloaded|40|44"
    [517]="Caffeine|3.36|44"
    [16]="Auto Move Windows|3.36|44"
    [277]="Impatience|3.36|44"
    [3088]="GNOME 4x UI Improvements|40|44"
    [779]="Clipboard History|3.38|44"
    [3780]="DDterm|40|44"
    [1460]="Vitals|3.34|44"
    [1262]="EasyScreenCast|3.38|44"
)

# ============================================
# INSTALLATION BEHAVIOR
# ============================================
# Set to 'true' to skip confirmations (dangerous!)
## Allow environment overrides for these flags (useful for CI/automation)
# Set to 'true' to skip confirmations (dangerous!)
AUTO_CONFIRM=${AUTO_CONFIRM:-false}

# Set to 'true' to enable verbose output
VERBOSE=${VERBOSE:-false}

# Set to 'true' to perform a dry-run (show what would be done)
DRY_RUN=${DRY_RUN:-false}