#!/bin/bash
# GNOME Desktop configuration

setup_gnome_desktop() {
    section_header "GNOME Desktop Setup"
    
    # Check if GNOME is installed
    if ! command_exists gnome-shell; then
        warning_message "GNOME Shell is not installed. Skipping GNOME configuration."
        return 0
    fi
    
    info_box "GNOME Desktop" "$GNOME_DESCRIPTION"
    
    echo "The following GNOME packages will be installed:"
    for pkg in "${GNOME_PACKAGES[@]}"; do
        echo "  • $pkg"
    done
    echo ""
    
    if ! confirm "Do you want to proceed with GNOME desktop setup?"; then
        warning_message "Skipping GNOME desktop setup"
        return 0
    fi
    
    log_info "Starting GNOME desktop setup"
    
    # Install GNOME packages
    install_packages "${GNOME_PACKAGES[@]}"
    
    echo ""
    echo "GNOME packages installed successfully!"
    echo ""
    
    # Ask about extension installation
    if confirm "Do you want to install GNOME extensions using the advanced installer?"; then
        install_gnome_extensions_advanced
    fi
    
    success_message "GNOME desktop setup completed!"
    echo "You may need to restart GNOME Shell (Alt+F2, type 'r', press Enter)"
    echo "or log out and back in for changes to take effect."
    echo ""
    
    log_info "GNOME desktop setup completed"
}

install_gnome_extensions_advanced() {
    local ext_installer="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")/extensions/gnome-extensions_installer.sh"
    
    if [ -f "$ext_installer" ]; then
        echo ""
        echo "Running GNOME extensions installer..."
        echo "This will install additional extensions from extensions.gnome.org"
        echo ""
        
        if confirm "Proceed with extension installation?"; then
            bash "$ext_installer"
        fi
    else
        warning_message "GNOME extensions installer not found at $ext_installer"
        echo "You can manually install extensions using GNOME Extension Manager"
    fi
}
