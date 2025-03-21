#!/bin/bash
# Core system packages

install_core_packages() {
    echo "Installing core system packages..."
    install_packages "${CORE_PACKAGES[@]}"
    
    # Additional system setup
    if ! command_exists docker; then
        sudo groupadd docker
        sudo usermod -aG docker "$USER"
    fi
}