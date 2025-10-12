#!/bin/bash
# Core system packages installation

install_core_packages() {
    section_header "Core System Packages Installation"
    
    info_box "Core System Packages" "$CORE_DESCRIPTION"
    
    echo "The following packages will be installed:"
    for pkg in "${CORE_PACKAGES[@]}"; do
        echo "  • $pkg"
    done
    echo ""
    
    if ! confirm "Do you want to proceed with core package installation?" "Y"; then
        warning_message "Skipping core packages installation"
        return 0
    fi
    
    # Install packages
    log_info "Starting core packages installation"
    install_packages "${CORE_PACKAGES[@]}"
    
    # Additional system setup
    if ! command_exists docker; then
        if groups "$USER" | grep -q docker; then
            log_info "User already in docker group"
        else
            sudo groupadd docker 2>/dev/null || true
            sudo usermod -aG docker "$USER"
            echo "✅ Added $USER to docker group (requires re-login)"
        fi
    fi
    
    success_message "Core packages installed successfully!"
    log_info "Core packages installation completed"
}