#!/bin/bash
# Programming tools installation

# --- Source color helpers (module-local) ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    source "$MODULE_DIR/../modules/utils.sh"
fi

install_programming_tools() {
    # Use the generic install flow for consistent UX
    standard_install_flow "Programming Languages & Tools" "$PROGRAMMING_DESCRIPTION" PROGRAMMING_PACKAGES
    log_info "Programming packages installation requested/completed by standard flow"
    
    # Python verification and setup
    echo ""
    info "Verifying Python installation..."
    if python3 --version &> /dev/null; then
        success_message "Python $(python3 --version 2>&1 | cut -d' ' -f2) installed"

        if confirm "Do you want to upgrade pip to the latest version?"; then
            info "Pip upgrade: choosing installation method based on environment"

            # Check if we're in a virtual environment
            if [ -n "$VIRTUAL_ENV" ]; then
                info "Virtual environment detected: $VIRTUAL_ENV — upgrading pip inside venv"
                safe_run python3 -m pip install --upgrade pip
            else
                info "No virtual environment detected — upgrading pip for user"
                safe_run python3 -m pip install --upgrade pip --user
            fi
            ok "Pip upgrade attempted"
        fi

        if confirm "Do you want to install common Python development tools (virtualenv, black, pylint)?"; then
            info "Installing Python development tools (virtualenv, black, pylint, flake8)"

            # Install based on environment
            if [ -n "$VIRTUAL_ENV" ]; then
                safe_run python3 -m pip install virtualenv black pylint flake8
            else
                safe_run python3 -m pip install --user virtualenv black pylint flake8
            fi
            ok "Python development tools installation attempted"
        fi
    else
        error_message "Python installation verification failed!"
        log_error "Python installation failed"
        return 1
    fi

    success_message "Programming tools installed successfully!"
    log_info "Programming tools installation completed"
}