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
            echo ""
            echo "ℹ️  Pip Installation Method:"
            echo "   • Installing with --user flag (user-specific installation)"
            echo "   • Recommended for most users to avoid system-wide conflicts"
            echo "   • If you're using a virtual environment, pip will install there instead"
            echo ""
            
            # Check if we're in a virtual environment
            if [ -n "$VIRTUAL_ENV" ]; then
                echo "✅ Virtual environment detected: $VIRTUAL_ENV"
                echo "   Installing pip without --user flag (virtual environment-specific)"
                python3 -m pip install --upgrade pip
            else
                echo "📦 No virtual environment detected"
                echo "   Installing pip with --user flag (user-specific, no sudo required)"
                python3 -m pip install --upgrade pip --user
            fi
            echo "✅ Pip upgraded successfully"
        fi
        
        if confirm "Do you want to install common Python development tools (virtualenv, black, pylint)?"; then
            echo ""
            echo "Installing Python development tools..."
            echo "ℹ️  Note: Tools will be installed in the same way as pip"
            echo "   (user-specific with --user flag if not in a virtual environment)"
            echo ""
            
            # Install based on environment
            if [ -n "$VIRTUAL_ENV" ]; then
                python3 -m pip install virtualenv black pylint flake8
            else
                python3 -m pip install --user virtualenv black pylint flake8
            fi
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