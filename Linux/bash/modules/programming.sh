#!/bin/bash
# Programming tools installation

install_programming_tools() {
    echo "Installing programming languages & tools..."
    install_packages "${PROGRAMMING_PACKAGES[@]}"
    
    # Python verification
    if python3 --version &> /dev/null; then
        python3 -m pip install --upgrade pip
    else
        echo "Python installation failed!" >&2
        return 1
    fi
}