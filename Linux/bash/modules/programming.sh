#!/bin/bash
# Programming tools installation

install_programming_tools() {
    section_header "Programming Languages & Tools Installation"
    
    info_box "Programming Tools" "$PROGRAMMING_DESCRIPTION"
    
    echo "The following packages will be installed:"
    for pkg in "${PROGRAMMING_PACKAGES[@]}"; do
        echo "  • $pkg"
    done
    echo ""
    
    if ! confirm "Do you want to proceed with programming tools installation?" "Y"; then
        warning_message "Skipping programming tools installation"
        return 0
    fi
    
    log_info "Starting programming tools installation"
    install_packages "${PROGRAMMING_PACKAGES[@]}"
    
    # Python verification and setup
    echo ""
    echo "Verifying Python installation..."
    if python3 --version &> /dev/null; then
        echo "✅ Python $(python3 --version 2>&1 | cut -d' ' -f2) installed"
        
        if confirm "Do you want to upgrade pip to the latest version?"; then
            echo "Upgrading pip..."
            python3 -m pip install --upgrade pip --user
            echo "✅ Pip upgraded successfully"
        fi
        
        if confirm "Do you want to install common Python development tools (virtualenv, black, pylint)?"; then
            echo "Installing Python development tools..."
            python3 -m pip install --user virtualenv black pylint flake8
            echo "✅ Python development tools installed"
        fi
    else
        error_message "Python installation verification failed!"
        log_error "Python installation failed"
        return 1
    fi
    
    success_message "Programming tools installed successfully!"
    log_info "Programming tools installation completed"
}