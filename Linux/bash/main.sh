#!/bin/bash
# Main setup script

# Load configuration and utilities
source config.sh
source modules/utils.sh

# Main menu
function main_menu() {
    clear
    echo "Ubuntu Setup Script"
    echo "-------------------"
    echo "1) Install Core System Packages"
    echo "2) Install Programming Languages & Tools"
    echo "3) Configure Shell Environment"
    echo "4) Setup GNOME Desktop"
    echo "5) Install Docker & Portainer"
    echo "6) Install Everything"
    echo "7) Exit"
    echo -n "Your choice: "
}

# Load modules
source modules/core_packages.sh
source modules/programming.sh
source modules/shell_setup.sh
source modules/gnome_setup.sh
source modules/docker_setup.sh

# Handle user input
while true; do
    main_menu
    read choice
    case $choice in
        1) install_core_packages ;;
        2) install_programming_tools ;;
        3) configure_shell_environment ;;
        4) setup_gnome_desktop ;;
        5) install_docker_stack ;;
        6) 
            install_core_packages
            install_programming_tools
            configure_shell_environment
            setup_gnome_desktop
            install_docker_stack
            ;;
        7) exit 0 ;;
        *) echo "Invalid option"; sleep 1 ;;
    esac
done