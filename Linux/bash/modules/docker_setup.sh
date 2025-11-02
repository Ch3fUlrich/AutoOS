#!/bin/bash
# Docker & Portainer installation (module)

# --- Source color/helpers (module-local) ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
    # module placed alongside utils (rare)
    # shellcheck source=/dev/null
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    # normal layout: modules/docker_setup.sh -> modules/utils.sh
    # shellcheck source=/dev/null
    source "$MODULE_DIR/../modules/utils.sh"
else
    echo "Unable to locate utils.sh for color/logging helpers. Please ensure modules/utils.sh is present." >&2
    return 1
fi

install_docker_stack() {
    section_header "Docker & Portainer Installation"

    info_box "This will install:\n  - Docker Engine\n  - Docker Compose\n  - Portainer Agent (for remote management)"

    if ! confirm "Do you want to proceed with Docker installation?"; then
        warning_message "Skipping Docker installation"
        return 0
    fi

    # Check if Docker is already installed
    if command_exists docker; then
        info "Docker is already installed: $(docker --version 2>/dev/null || echo 'unknown')"
        if ! confirm "Do you want to reinstall Docker?"; then
            info "Skipping Docker installation"
            docker_post_install
            return 0
        fi
    fi

    # Download the official convenience script (safe_run will print in DRY_RUN)
    info "Downloading Docker install script to /tmp/get-docker.sh"
    safe_run curl -fsSL https://get.docker.com -o /tmp/get-docker.sh

    # Try to show checksum if file exists (may not exist during DRY_RUN)
    if [ -f /tmp/get-docker.sh ]; then
        if command_exists sha256sum; then
            SCRIPT_HASH=$(sha256sum /tmp/get-docker.sh | awk '{print $1}' 2>/dev/null || true)
            if [ -n "$SCRIPT_HASH" ]; then
                info_box "SHA256 checksum of downloaded script: $SCRIPT_HASH"
                info "Please verify this checksum against the official Docker documentation (if available)."
            fi
        fi
    else
        info "Checksum unavailable: /tmp/get-docker.sh not present (DRY_RUN or download skipped)"
    fi

    if ! confirm "Do you want to proceed and execute the downloaded Docker installation script?"; then
        warning_message "Aborting Docker installation."
        return 1
    fi

    safe_run sudo sh /tmp/get-docker.sh

    # Post-installation setup
    docker_post_install

    # Install Portainer Agent
    if confirm "Do you want to install Portainer Agent?"; then
        install_portainer_agent
    fi

    # Offer to install Portainer CLI (managed by other computer)
    if confirm "Do you want to install the Portainer CLI (portainer-cli) to manage Portainer remotely?"; then
        install_portainer_cli
    fi

    # Create apps directory (use utils helper for consistent feedback)
    create_directory_with_feedback "$HOME/apps"

    success_message "Docker installation completed!\nNote: You may need to log out and back in for group changes to take effect."
}

docker_post_install() {
    info "Configuring Docker..."
    # Add user to docker group (idempotent helper)
    add_user_to_group "$USER" docker

    # Start and enable Docker + containerd services using the helper
    enable_and_start_service docker
    enable_and_start_service containerd

    info "Docker service enabled (if installed)"
}

install_portainer_agent() {
    section_header "Portainer Agent"

    info "Installing Portainer Agent..."

    # Check if container already exists
    if command_exists docker && docker ps -a | grep -q portainer_agent; then
        info "Portainer Agent container already exists"
        if confirm "Do you want to remove and reinstall it?"; then
            safe_run docker stop portainer_agent || true
            safe_run docker rm portainer_agent || true
        else
            info "Skipping Portainer Agent installation"
            return 0
        fi
    fi

    # Run Portainer Agent
    safe_run docker run -d \
        -p 9001:9001 \
        --name portainer_agent \
        --restart=always \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /var/lib/docker/volumes:/var/lib/docker/volumes \
        portainer/agent:latest

    success_message "Portainer Agent installation attempted (check docker ps to verify)."
}

# Install Portainer CLI (portainer-cli)
install_portainer_cli() {
    section_header "Portainer CLI (Managed by Other Computer)"

    info "This section installs the Portainer CLI (portainer-cli) for managing Portainer from the command line."

    if ! confirm "Do you want to install the Portainer CLI now?" "Y"; then
        warning_message "Skipping Portainer CLI installation"
        return 0
    fi

    # prefer npm global installation if available
    if command_exists npm; then
        info "Installing portainer-cli via npm (global)..."
        safe_run sudo npm install -g @portainer/portainer-cli || true
    else
        # Try pipx/pip install from PyPI if present (portainer-cli may not be available there)
        if command_exists pipx; then
            info "Installing via pipx..."
            safe_run pipx install portainer-cli || true
        else
            warning_message "npm not found and pipx not available. Please install Node.js/npm or pipx to install portainer-cli."
            return 0
        fi
    fi

    success_message "Portainer CLI installation attempted. Run 'portainer-cli --help' to verify."
}
