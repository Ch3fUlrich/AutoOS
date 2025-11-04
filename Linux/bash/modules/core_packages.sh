#!/bin/bash
# Core system packages installation

# --- Source color helpers (module-local) ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    source "$MODULE_DIR/../modules/utils.sh"
fi

install_core_packages() {
    # Use the generic install flow to present and install core packages
    standard_install_flow "Core System Packages" "$CORE_DESCRIPTION" CORE_PACKAGES

    # Post-install: ensure docker group membership if docker was installed
    if command_exists docker; then
        add_user_to_group "$USER" docker
    fi

    # Configure Git user name and email
    if command_exists git; then
        if ! git config --global user.name > /dev/null 2>&1; then
            info "Configuring Git user name..."
            read -p "Enter your Git user name: " git_name
            if [ -n "$git_name" ]; then
                git config --global user.name "$git_name"
                success_message "Git user name set to $git_name"
            fi
        else
            info "Git user name already configured"
        fi

        if ! git config --global user.email > /dev/null 2>&1; then
            info "Configuring Git user email..."
            read -p "Enter your Git user email: " git_email
            if [ -n "$git_email" ]; then
                git config --global user.email "$git_email"
                success_message "Git user email set to $git_email"
            fi
        else
            info "Git user email already configured"
        fi
    fi
}