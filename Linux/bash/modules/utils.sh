#!/bin/bash
# Utility functions

# ============================================
# ERROR HANDLING
# ============================================
# Error handling
trap 'error_handler $? $LINENO' ERR

error_handler() {
    local exit_code=$1
    local line_no=$2
    echo ""
    echo "❌ Error occurred at line $line_no with exit code $exit_code"
    echo "   Please check the error message above for details."
    echo ""
    exit $exit_code
}

# ============================================
# PACKAGE MANAGER FUNCTIONS
# ============================================
# Update package list
update_package_list() {
    echo "📦 Updating package list..."
    sudo apt-get update -qq || {
        echo "❌ Failed to update package list"
        return 1
    }
}

# Install packages with progress indication
install_packages() {
    if [ $# -eq 0 ]; then
        echo "⚠️  No packages specified for installation"
        return 0
    fi
    
    echo "📦 Installing packages: $*"
    
    if [ "$DRY_RUN" = true ]; then
        echo "   [DRY RUN] Would install: $*"
        return 0
    fi
    
    update_package_list
    
    sudo apt-get install -y --no-install-recommends "$@" || {
        echo "❌ Failed to install some packages"
        return 1
    }
    
    echo "✅ Packages installed successfully"
}

# Check if package is installed
is_package_installed() {
    dpkg -l "$1" 2>/dev/null | grep -q "^ii"
}

# Check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# ============================================
# USER INTERACTION FUNCTIONS
# ============================================
# User confirmation prompt with default value
confirm() {
    if [ "$AUTO_CONFIRM" = true ]; then
        return 0
    fi
    
    local prompt="$1"
    local default="${2:-N}"
    local response
    
    if [ "$default" = "Y" ]; then
        read -rp "$prompt [Y/n] " response
        response=${response:-Y}
    else
        read -rp "$prompt [y/N] " response
        response=${response:-N}
    fi
    
    [[ "$response" =~ ^[Yy]$ ]]
}

# Display information box
info_box() {
    local title="$1"
    local message="$2"
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    printf "║ %-62s ║\n" "$title"
    echo "╠════════════════════════════════════════════════════════════════╣"
    
    # Word wrap the message
    echo "$message" | fold -s -w 62 | while IFS= read -r line; do
        printf "║ %-62s ║\n" "$line"
    done
    
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
}

# Display a section header
section_header() {
    local title="$1"
    echo ""
    echo "======================================"
    echo "$title"
    echo "======================================"
    echo ""
}

# Display success message
success_message() {
    echo ""
    echo "✅ $1"
    echo ""
}

# Display warning message
warning_message() {
    echo ""
    echo "⚠️  $1"
    echo ""
}

# Display error message
error_message() {
    echo ""
    echo "❌ $1"
    echo ""
}

# Display progress spinner
spinner() {
    local pid=$1
    local message="${2:-Processing...}"
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    
    while kill -0 "$pid" 2>/dev/null; do
        i=$(( (i+1) % 10 ))
        printf "\r%s %s" "${spin:$i:1}" "$message"
        sleep 0.1
    done
    printf "\r%s\n" "✅ $message done"
}

# Multi-line select menu
select_from_list() {
    local prompt="$1"
    shift
    local options=("$@")
    
    echo "$prompt"
    echo ""
    
    local i=1
    for option in "${options[@]}"; do
        echo "  $i) $option"
        ((i++))
    done
    echo ""
    
    local choice
    while true; do
        read -rp "Enter your choice (1-${#options[@]}): " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#options[@]}" ]; then
            return $((choice - 1))
        else
            echo "Invalid choice. Please try again."
        fi
    done
}

# ============================================
# SYSTEM INFORMATION
# ============================================
# Get distribution name
get_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$NAME $VERSION_ID"
    else
        echo "Unknown"
    fi
}

# Check if running as root
is_root() {
    [ "$EUID" -eq 0 ]
}

# Check if sudo is available
check_sudo() {
    if ! command_exists sudo; then
        error_message "sudo is not installed. Please install sudo or run as root."
        return 1
    fi
    
    # Test sudo access
    if ! sudo -n true 2>/dev/null; then
        echo "🔑 Sudo access required. Please enter your password:"
        sudo -v || {
            error_message "Failed to obtain sudo access"
            return 1
        }
    fi
    
    return 0
}

# ============================================
# FILE AND DIRECTORY OPERATIONS
# ============================================
# Create directory if it doesn't exist
ensure_directory() {
    local dir="$1"
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo "📁 Created directory: $dir"
    fi
}

# Backup file if it exists
backup_file() {
    local file="$1"
    if [ -f "$file" ]; then
        local backup="${file}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$file" "$backup"
        echo "💾 Backed up $file to $backup"
    fi
}

# ============================================
# LOGGING
# ============================================
# Initialize log file
LOG_FILE="/tmp/autoos-install-$(date +%Y%m%d_%H%M%S).log"

log() {
    local level="$1"
    shift
    local message="$*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message" >> "$LOG_FILE"
    
    if [ "$VERBOSE" = true ]; then
        echo "[$level] $message"
    fi
}

log_info() {
    log "INFO" "$*"
}

log_error() {
    log "ERROR" "$*"
}

log_warning() {
    log "WARNING" "$*"
}