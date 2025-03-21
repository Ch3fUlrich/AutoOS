#!/bin/bash
# Utility functions

# Error handling
trap 'error_handler $? $LINENO' ERR

error_handler() {
    local exit_code=$1
    local line_no=$2
    echo "Error occurred at line $line_no with exit code $exit_code"
    exit $exit_code
}

# Package manager functions
install_packages() {
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "$@"
}

# Check if package is installed
is_package_installed() {
    dpkg -l "$1" &> /dev/null
}

# Check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# User confirmation prompt
confirm() {
    read -rp "$1 [y/N] " response
    [[ "$response" =~ ^[Yy]$ ]]
}