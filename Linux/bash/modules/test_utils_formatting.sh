#!/usr/bin/env bash
# Test script to demonstrate beautiful formatting functions from utils.sh

set -euo pipefail

# Source utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║        AutoOS Utils.sh - Formatting Functions Demo            ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Test section_header
section_header "Section Header Example"

# Test basic colored output
info "This is an info message with emoji"
ok "This is a success message"
warn "This is a warning message"
err "This is an error message (non-fatal for demo)"

echo ""

# Test formatted messages
success_message "Installation completed successfully!"
warning_message "This is a warning that something needs attention"
error_message "This is an error message with proper formatting"

# Test info_box
info_box "Welcome to AutoOS" "This is a beautiful info box with box-drawing characters. It automatically wraps long text to fit within the box boundaries and looks professional."

# Test system helpers
section_header "System Information"
echo "Distribution: $(get_distro)"
echo "Running as root: $(is_root && echo 'Yes' || echo 'No')"
echo "Command 'bash' exists: $(command_exists bash && echo 'Yes' || echo 'No')"

# Test menu
section_header "Interactive Menu Demo"
echo "Example of select_from_list (skipping for non-interactive demo):"
echo "  1) Option One"
echo "  2) Option Two"
echo "  3) Option Three"
echo ""

# Test spinner (background process demo)
section_header "Spinner Demo"
echo "Starting a background process with spinner..."
sleep 3 &
SLEEP_PID=$!
spinner $SLEEP_PID "Processing your request"

# Test file operations info
section_header "File Operations Demo"
echo "Directory check:"
if [ -d "/tmp" ]; then
    echo "  ✓ /tmp directory exists"
fi

echo ""
echo "Backup file example:"
echo "  backup_file /path/to/file.txt"
echo "  → Would create: /path/to/file.txt.backup.YYYYMMDD_HHMMSS"

# Test logging with verbose
section_header "Logging Demo"
echo "Logs are written to: $LOG_FILE"
log_info "This is an info log entry"
log_warning "This is a warning log entry"
log_error "This is an error log entry"
echo ""
echo "To see verbose output, run with: VERBOSE=true"

# Test package list formatting
section_header "Package List Formatting"
echo "Example package list:"
print_pkg_list "git" "curl" "wget" "tmux" "vim"

# Test menu items
section_header "Menu Item Formatting"
print_menu_item "1" "Install Core Packages" "Installs essential system utilities and tools"
print_menu_item "2" "Setup Shell Environment" "Configures Zsh with Oh My Zsh and themes"
print_menu_item "3" "Exit" "Quit the installer"

# Final message
echo ""
info_box "Demo Complete" "All formatting functions have been demonstrated. These functions are available throughout the AutoOS installation scripts for beautiful and consistent output."

echo ""
ok "Demo completed successfully!"
echo ""
