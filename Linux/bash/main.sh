#!/bin/bash
# AutoOS - Automated Linux Setup Script
# Main installation orchestrator

# ============================================
# INITIALIZATION
# ============================================
# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration and utilities
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/modules/utils.sh"

# ============================================
# PRE-FLIGHT CHECKS
# ============================================
preflight_checks() {
    clear
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                                                                ║"
    echo "║                    AutoOS Installation Script                  ║"
    echo "║                  Fast Linux Setup & Configuration              ║"
    echo "║                                                                ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    
    # Check sudo access
    if ! check_sudo; then
        error_message "Sudo access is required to run this script"
        exit 1
    fi
    
    # Display system information
    echo "System Information:"
    echo "  Distribution: $(get_distro)"
    echo "  Kernel: $(uname -r)"
    echo "  User: $USER"
    echo "  Log file: $LOG_FILE"
    echo ""
    
    log_info "AutoOS installation started by user: $USER"
    log_info "System: $(get_distro), Kernel: $(uname -r)"
}

# ============================================
# MAIN MENU
# ============================================
main_menu() {
    clear
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                     AutoOS Setup Menu                          ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    print_menu_item 1 "Install Core System Packages" "└─ Essential tools: git, curl, htop, ansible, etc."
    print_menu_item 2 "Install Programming Languages & Tools" "└─ Python, C/C++, build tools"
    print_menu_item 3 "Configure Shell Environment" "└─ Zsh, Oh My Zsh, Powerlevel10k theme"
    print_menu_item 4 "Setup GNOME Desktop" "└─ Extensions, tweaks, and customizations"
    print_menu_item 5 "Install Docker & Portainer" "└─ Container platform and management"
    print_menu_item 6 "Install Everything (Full Setup)" "└─ All of the above in sequence"
    print_menu_item 7 "View Installation Log" "└─ Show detailed log file"
    print_menu_item 8 "Exit" ""
    echo "════════════════════════════════════════════════════════════════"
    echo -n "Enter your choice (1-8): "
}

# ============================================
# VIEW LOG
# ============================================
view_log() {
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo "Installation Log: $LOG_FILE"
        echo "════════════════════════════════════════════════════════════════"
        echo ""
        tail -n 50 "$LOG_FILE"
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo ""
        read -rp "Press Enter to continue..."
    else
        warning_message "No log file found yet"
        sleep 2
    fi
}

# ============================================
# FULL INSTALLATION
# ============================================
install_everything() {
    clear
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                    Full Installation Mode                      ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "This will install all components in the following order:"
    echo "  1. Core System Packages"
    echo "  2. Programming Languages & Tools"
    echo "  3. Shell Environment (Zsh)"
    echo "  4. GNOME Desktop Enhancements"
    echo "  5. Docker & Portainer"
    echo ""
    echo "⚠️  This may take 15-30 minutes depending on your internet speed."
    echo ""
    
    if ! confirm "Do you want to proceed with full installation?" "Y"; then
        warning_message "Full installation cancelled"
        return 0
    fi
    
    log_info "Starting full installation"
    
    # Run all installation modules
    install_core_packages
    if [ "${AUTO_CONFIRM:-false}" != true ]; then
        read -rp "Press Enter to continue to next step..."
    fi

    install_programming_tools
    if [ "${AUTO_CONFIRM:-false}" != true ]; then
        read -rp "Press Enter to continue to next step..."
    fi

    configure_shell_environment
    if [ "${AUTO_CONFIRM:-false}" != true ]; then
        read -rp "Press Enter to continue to next step..."
    fi

    setup_gnome_desktop
    if [ "${AUTO_CONFIRM:-false}" != true ]; then
        read -rp "Press Enter to continue to next step..."
    fi

    install_docker_stack
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║              🎉 Full Installation Complete! 🎉                 ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Next steps:"
    echo "  1. Log out and log back in for all changes to take effect"
    echo "  2. Run 'chsh -s \$(which zsh)' to set Zsh as default shell"
    echo "  3. Configure your terminal font to 'MesloLGS NF'"
    echo "  4. Run 'p10k configure' to setup Powerlevel10k theme"
    echo ""
    echo "Log file: $LOG_FILE"
    echo ""
    
    log_info "Full installation completed successfully"
    
    read -rp "Press Enter to continue..."
}

# ============================================
# LOAD MODULES
# ============================================
source "$SCRIPT_DIR/modules/core_packages.sh"
source "$SCRIPT_DIR/modules/programming.sh"
source "$SCRIPT_DIR/modules/shell_setup.sh"
source "$SCRIPT_DIR/modules/gnome_setup.sh"
source "$SCRIPT_DIR/modules/docker_setup.sh"

# ============================================
# MAIN LOOP
# ============================================
main() {
    # Run preflight checks
    preflight_checks
    
    # Main menu loop
    while true; do
    main_menu
    # Read raw input and sanitize: remove CR and trim whitespace
    read -r __choice_raw
    # strip carriage returns (CR) and leading/trailing whitespace
    choice="$(printf '%s' "${__choice_raw}" | tr -d '\r' | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')"
        
        case $choice in
            1)
                install_core_packages
                ;;
            2)
                install_programming_tools
                ;;
            3)
                configure_shell_environment
                ;;
            4)
                setup_gnome_desktop
                ;;
            5)
                install_docker_stack
                ;;
            6)
                install_everything
                ;;
            7)
                view_log
                ;;
            8)
                echo ""
                echo "Thank you for using AutoOS!"
                echo "Log file: $LOG_FILE"
                echo ""
                log_info "AutoOS installation exited by user"
                exit 0
                ;;
            *)
                warning_message "Invalid option. Please choose 1-8."
                sleep 2
                ;;
        esac
    done
}

# Run main function
main "$@"
