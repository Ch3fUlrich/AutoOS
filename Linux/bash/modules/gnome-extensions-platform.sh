#!/usr/bin/env bash
# Platform-specific GNOME helper functions (Raspberry Pi, installers)
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer module-local helpers
# shellcheck source=/dev/null
if [ -f "$MODULE_DIR/utils.sh" ]; then
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    source "$MODULE_DIR/../modules/utils.sh"
else
    echo "Unable to locate utils.sh - platform helpers need helpers." >&2
    return 1
fi

set -Eeuo pipefail

# Install GNOME Desktop on Raspberry Pi
install_gnome_on_pi() {
    section_header "GNOME Desktop Installation for Raspberry Pi"
    # If caller passed CLI-style flags to this function (e.g. from a shim),
    # apply them by reusing the module's CLI parser if present so flags like
    # --dry-run and --non-interactive are respected when the function is
    # invoked directly.
    if declare -f parse_cli_args >/dev/null 2>&1; then
        parse_cli_args "$@"
    fi
    
    # Check if GNOME is already installed
    if command_exists gnome-shell; then
        info "GNOME Shell is already installed!"
        local version
        version=$(gnome-shell --version 2>/dev/null | awk '{print $3}')
        ok "Current version: ${version}"
        echo ""
        if ! confirm "Do you want to reinstall/update GNOME packages?"; then
            info "Skipping GNOME installation"
            return 0
        fi
    fi
    
    # Display resource usage warning
    info_box "⚠️  Resource Usage Warning" "GNOME is a modern, feature-rich desktop environment that requires significant system resources:

RAM Usage:
  • Idle: 1.0 - 1.5 GB (vs ~400MB for PIXEL desktop)
  • With apps: 2.5 - 4.0 GB
  • Minimum recommended: 4GB RAM
  • Optimal: 8GB RAM

CPU Usage:
  • Background: 5-15% on idle
  • Animations & effects: 20-40% during use
  • Can cause thermal throttling under load

GPU Usage:
  • Hardware compositing required
  • Continuous GPU usage for effects
  • May impact 4K video playback

Storage:
  • GNOME packages: ~1.5 GB
  • Additional extensions: ~200 MB

Performance Impact:
  • Slower boot time (30-45 seconds vs 15-20)
  • Less responsive than PIXEL desktop

Recommended For:
  ✓ Raspberry Pi 5 with 8GB RAM
  ✓ Users who need GNOME-specific apps
  ✓ Testing/development purposes

NOT Recommended For:
  ✗ Raspberry Pi 4 with 4GB or less
  ✗ Performance-critical applications"
    
    echo ""
    cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  Are you sure you want to install GNOME on Raspberry Pi?"
    echo ""
    
    if ! confirm "Continue with GNOME installation?" "N"; then
        warning_message "GNOME installation cancelled"
        info "Tip: Raspberry Pi OS's default PIXEL desktop is optimized for Pi hardware"
        return 1
    fi
    
    # Check RAM and warn if low
    local ram_mb
    ram_mb=$(get_ram_mb)
    
    if [ "$ram_mb" -lt 6000 ]; then
        echo ""
        warning_message "Your Raspberry Pi has less than 6GB RAM (detected: ${ram_mb}MB)"
        echo ""
        cecho "${COLOR_RED}${COLOR_BOLD}" "GNOME will use a significant portion of your available memory!"
        echo ""
        if ! confirm "Are you ABSOLUTELY sure you want to continue?" "N"; then
            warning_message "Installation cancelled - wise choice!"
            return 1
        fi
    fi
    
    log_info "Starting GNOME installation on Raspberry Pi"
    
    # Update package list
    section_header "Updating Package List"
    update_package_list
    
    # Install GNOME Shell and core components
    section_header "Installing GNOME Shell & Core Components"
    
    local gnome_packages=(
        gnome-shell
        gnome-session
        gnome-terminal
        gnome-control-center
        gnome-system-monitor
        nautilus
        gdm3
    )
    
    echo "Installing core GNOME packages..."
    print_pkg_list "${gnome_packages[@]}"
    echo ""
    
    install_packages "${gnome_packages[@]}" || {
        error_message "Failed to install GNOME core packages"
        return 1
    }
    
    # Install recommended GNOME components
    section_header "Installing Recommended GNOME Components"
    
    local recommended_packages=(
        gnome-tweaks
        gnome-shell-extension-prefs
        chrome-gnome-shell
        file-roller
        eog
        evince
        gedit
    )
    
    echo "Installing recommended GNOME utilities..."
    print_pkg_list "${recommended_packages[@]}"
    echo ""
    
    if confirm "Install recommended GNOME utilities?" "Y"; then
        install_packages "${recommended_packages[@]}" || {
            warning_message "Some recommended packages failed to install"
        }
    fi
    
    # Configure GDM3 as default display manager
    section_header "Configuring Display Manager"
    
    if command_exists gdm3; then
        info "Setting GDM3 as default display manager..."
        if [ "${DRY_RUN:-false}" != true ]; then
            safe_run sudo systemctl enable gdm3 2>/dev/null || true
            safe_run sudo systemctl set-default graphical.target 2>/dev/null || true
        fi
        ok "GDM3 configured"
    fi
    
    # Optimize GNOME for Raspberry Pi
    apply_gnome_pi_optimizations
    
    # Final success message
    section_header "Installation Complete"
    
    success_message "GNOME Desktop has been installed on your Raspberry Pi!"
    
    echo ""
    info_box "Next Steps" "1. Reboot your Raspberry Pi to start GNOME:
   ${COLOR_CYAN}${COLOR_BOLD}sudo reboot${COLOR_RESET}

2. At the login screen, select GNOME from the session menu (gear icon)

3. After logging in, run optimizations:
   ${COLOR_CYAN}${COLOR_BOLD}/usr/local/bin/gnome-pi-optimize${COLOR_RESET}

4. Monitor system resources:
   ${COLOR_CYAN}${COLOR_BOLD}gnome-system-monitor${COLOR_RESET}

5. If performance is poor, you can switch back to PIXEL:
   • Log out
   • Select 'LXDE-pi-wayfire' or 'LXDE' at login screen"
    
    echo ""
    cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  Remember: GNOME uses significantly more resources than PIXEL!"
    echo ""
    
    log_info "GNOME installation completed on Raspberry Pi"
    
    return 0
}

# Apply GNOME performance optimizations for Raspberry Pi
apply_gnome_pi_optimizations() {
    section_header "Optimizing GNOME for Raspberry Pi"
    
    info "Applying performance optimizations..."
    
    # Create optimization script
    local opt_script="/tmp/gnome-pi-optimize.sh"
    cat > "$opt_script" << 'OPTIMIZE_EOF'
#!/bin/bash
# GNOME optimizations for Raspberry Pi

# Disable animations for better performance
gsettings set org.gnome.desktop.interface enable-animations false

# Reduce thumbnail size
gsettings set org.gnome.nautilus.preferences thumbnail-limit 1

# Disable file indexing
gsettings set org.freedesktop.Tracker3.Miner.Files crawling-interval -2

# Set conservative power profile
gsettings set org.gnome.settings-daemon.plugins.power power-saver-profile-on-low-battery true

echo "GNOME optimizations applied for Raspberry Pi"
OPTIMIZE_EOF
    
    chmod +x "$opt_script"
    
    if [ "${DRY_RUN:-false}" != true ]; then
        # Run optimizations for current user
        bash "$opt_script" 2>/dev/null || true

        # Create systemwide optimization that runs on first login
        safe_run sudo cp "$opt_script" /usr/local/bin/gnome-pi-optimize 2>/dev/null || true
    fi
    
    ok "Performance optimizations configured"
    
    # Cleanup
    rm -f "$opt_script"
}

# Ensure installer helper is present (gnome-shell-extension-installer)
ensure_extension_installer() {
    if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
        return 0
    fi

    info "gnome-shell-extension-installer not found. Attempting to install it to /usr/local/bin"
    if [ "${DRY_RUN:-false}" = true ]; then
        info "[DRY RUN] Would download gnome-shell-extension-installer"
        return 0
    fi

    safe_run sudo curl -fsSL -o /usr/local/bin/gnome-shell-extension-installer \
        https://raw.githubusercontent.com/brunelli/gnome-shell-extension-installer/master/gnome-shell-extension-installer || {
        warning_message "Could not download gnome-shell-extension-installer automatically"
        return 1
    }
    safe_run sudo chmod +x /usr/local/bin/gnome-shell-extension-installer || true
    info "Installed gnome-shell-extension-installer"
}
