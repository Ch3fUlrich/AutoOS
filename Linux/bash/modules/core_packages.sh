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
}