#!/bin/bash
# Configuration settings

# Package lists
CORE_PACKAGES=(
    git curl wget tmux wireguard
    htop nmon neofetch mc ansible cron
    nfs-common rsync
)

PROGRAMMING_PACKAGES=(
    clang python3 python3-pip python3-venv
    python3-setuptools python3-wheel
)

GNOME_PACKAGES=(
    gnome-shell-extensions gnome-tweaks
    gnome-shell-extension-manager
    gnome-shell-extension-dash-to-panel
    gnome-shell-extension-arc-menu
    gnome-shell-extension-clipboard-indicator
    gnome-shell-extension-gpaste
)

# Shell configuration
ZSH_THEME="powerlevel10k/powerlevel10k"
ZSH_PLUGINS=(
    zsh-autosuggestions
    zsh-syntax-highlighting
    git
)

# GNOME Extensions list
declare -A GNOME_EXTENSIONS=(
    # ID:Name|min_version|max_version
    [800]="Lock Keys|3.36|44"
    [1506]="Notification Banner Reloaded|40|44"
    # ... (rest of extensions from original list)
)