#!/bin/bash
# Docker and Portainer installation

install_docker_stack() {
    echo "======================================"
    echo "Docker & Portainer Installation"
    echo "======================================"
    echo ""
    echo "This will install:"
    echo "  - Docker Engine"
    echo "  - Docker Compose"
    echo "  - Portainer Agent (for remote management)"
    echo ""
    
    if ! confirm "Do you want to proceed with Docker installation?"; then
        echo "Skipping Docker installation"
        return 0
    fi
    
    # Check if Docker is already installed
    if command_exists docker; then
        echo "Docker is already installed ($(docker --version))"
        if ! confirm "Do you want to reinstall Docker?"; then
            echo "Skipping Docker installation"
            docker_post_install
            return 0
        fi
    fi
    
    # Install Docker using official script
    echo "Installing Docker..."
    if [ ! -f /tmp/get-docker.sh ]; then
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    fi
    
    # Verify the integrity of the downloaded script
    SCRIPT_HASH=$(sha256sum /tmp/get-docker.sh | awk '{print $1}')
    echo "SHA256 checksum of downloaded script: $SCRIPT_HASH"
    echo "Please verify this checksum against the official Docker documentation (if available)."
    if ! confirm "Do you want to proceed and execute the downloaded Docker installation script?"; then
        echo "Aborting Docker installation."
        return 1
    fi
    
    sudo sh /tmp/get-docker.sh
    
    # Post-installation setup
    docker_post_install
    
    # Install Portainer Agent
    if confirm "Do you want to install Portainer Agent?"; then
        install_portainer_agent
    fi
    
    # Create apps directory
    if [ ! -d "$HOME/apps" ]; then
        mkdir -p "$HOME/apps"
        echo "Created apps directory at $HOME/apps"
    fi
    
    echo ""
    echo "Docker installation completed!"
    echo "Note: You may need to log out and back in for group changes to take effect."
}

docker_post_install() {
    echo "Configuring Docker..."
    
    # Add user to docker group
    if ! groups "$USER" | grep -q docker; then
        sudo groupadd docker 2>/dev/null || true
        sudo usermod -aG docker "$USER"
        echo "Added $USER to docker group"
    fi
    
    # Start and enable Docker service
    sudo systemctl start docker 2>/dev/null || true
    sudo systemctl enable docker.service 2>/dev/null || true
    sudo systemctl enable containerd.service 2>/dev/null || true
    
    echo "Docker service enabled and started"
}

install_portainer_agent() {
    echo "Installing Portainer Agent..."
    
    # Check if container already exists
    if docker ps -a | grep -q portainer_agent; then
        echo "Portainer Agent container already exists"
        if confirm "Do you want to remove and reinstall it?"; then
            docker stop portainer_agent 2>/dev/null || true
            docker rm portainer_agent 2>/dev/null || true
        else
            echo "Skipping Portainer Agent installation"
            return 0
        fi
    fi
    
    # Run Portainer Agent
    docker run -d \
        -p 9001:9001 \
        --name portainer_agent \
        --restart=always \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /var/lib/docker/volumes:/var/lib/docker/volumes \
        portainer/agent:latest
    
    echo "Portainer Agent installed and running on port 9001"
}
