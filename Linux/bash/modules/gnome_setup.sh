#!/bin/bash
# GNOME desktop setup (refactored to use shared utils)

# --- Source helpers ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/../modules/utils.sh"
fi

setup_gnome_desktop() {
    section_header "GNOME Desktop Setup"

    # Check if GNOME is installed
    if ! command_exists gnome-shell; then
        warning_message "GNOME Shell is not installed. Skipping GNOME configuration."
        return 0
    fi

    # Check GNOME version for compatibility warnings
    local gnome_version
    gnome_version=$(gnome-shell --version | grep -oP '\d+\.\d+')
    if [[ "$(printf '%s\n' "$gnome_version" "45.0" | sort -V | head -n1)" == "$gnome_version" && "$gnome_version" != "45.0" ]]; then
        warning_message "GNOME $gnome_version detected. Many extensions are not yet compatible with GNOME 46+."
        info "Most extensions in the installer are designed for GNOME 44 and earlier."
        info "Consider using GNOME Extension Manager to find GNOME $gnome_version compatible extensions."
    fi

    info_box "GNOME Desktop" "$GNOME_DESCRIPTION"

    # Add GNOME Shell Extensions PPA for additional packages
    info "Adding GNOME Shell Extensions PPA for additional extension packages..."
    if ! sudo add-apt-repository ppa:gnome-shell-extensions/ppa -y 2>/dev/null; then
        warning_message "Failed to add GNOME Shell Extensions PPA. Some extension packages may not be available."
    else
        sudo apt update -qq
        success_message "GNOME Shell Extensions PPA added successfully."
    fi

    # Try to install GNOME packages, but don't fail if some are unavailable
    info "Attempting to install GNOME packages..."
    if install_packages "${GNOME_PACKAGES[@]}"; then
        success_message "GNOME packages installed successfully!"
    else
        warning_message "Some GNOME packages could not be installed from repositories."
        info "The unavailable packages will be installed via GNOME extensions website."
    fi

    success_message "GNOME desktop setup completed!"
    info "You may need to restart GNOME Shell (Alt+F2, type 'r', press Enter) or log out and back in for changes to take effect."

    log_info "GNOME desktop setup completed"
}

install_gnome_extensions_advanced() {
    local ext_installer="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/gnome-extensions_installer.sh"

    if type install_gnome_extensions_grouped >/dev/null 2>&1; then
        info "Running GNOME extensions installer (module function)..."
        if confirm "Proceed with extension installation?"; then
            install_gnome_extensions_grouped
        fi
    elif [ -f "$ext_installer" ]; then
        info "Running GNOME extensions installer..."
        if confirm "Proceed with extension installation?"; then
            bash "$ext_installer"
        fi
    else
        warning_message "GNOME extensions installer not found at $ext_installer"
        info "You can manually install extensions using GNOME Extension Manager"
    fi
}
