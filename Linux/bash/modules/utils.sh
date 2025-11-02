#!/usr/bin/env bash

# utils.sh — concise, portable utilities for AutoOS

# Prevent double-sourcing
if [ -n "${AUTOOS_UTILS_LOADED:-}" ]; then
    return 0
fi
AUTOOS_UTILS_LOADED=1

# Colors
_autoos_supports_color() {
    [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ]
}

if _autoos_supports_color; then
    COLOR_RESET='\033[0m'
    COLOR_BOLD='\033[1m'
    COLOR_RED='\033[0;31m'
    COLOR_GREEN='\033[0;32m'
    COLOR_YELLOW='\033[0;33m'
    COLOR_BLUE='\033[0;34m'
    COLOR_MAGENTA='\033[0;35m'
    COLOR_CYAN='\033[0;36m'
    COLOR_GRAY='\033[0;90m'
else
    COLOR_RESET=''
    COLOR_BOLD=''
    COLOR_RED=''
    COLOR_GREEN=''
    COLOR_YELLOW=''
    COLOR_BLUE=''
    COLOR_MAGENTA=''
    COLOR_CYAN=''
    COLOR_GRAY=''
fi

cecho() { local color="$1"; shift; # interpret backslash escapes in both color and message so literal \033 sequences become real ANSI codes
    printf "%b%b%b\n" "${color}" "$*" "${COLOR_RESET}"; }
info() { cecho "${COLOR_CYAN}${COLOR_BOLD}" "💡 [INFO]  $*"; }
ok()   { cecho "${COLOR_GREEN}${COLOR_BOLD}" "✅ $*"; }
warn() { cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  $*"; }
err()  { cecho "${COLOR_RED}${COLOR_BOLD}" "❌ $*"; }

# Formatted message helpers
section_header() {
    local title="$*"
    echo ""
    cecho "${COLOR_BLUE}${COLOR_BOLD}" "=============================="
    cecho "${COLOR_BLUE}${COLOR_BOLD}" "  $title"
    cecho "${COLOR_BLUE}${COLOR_BOLD}" "=============================="
    echo ""
}

success_message() {
    echo ""
    cecho "${COLOR_GREEN}${COLOR_BOLD}" "✅ SUCCESS: $*"
    echo ""
}

warning_message() {
    echo ""
    cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  WARNING: $*"
    echo ""
}

error_message() {
    echo ""
    cecho "${COLOR_RED}${COLOR_BOLD}" "❌ ERROR: $*"
    echo ""
}

# Simple UI helpers
print_menu_item() {
    local num="$1"; shift
    local title="$1"; shift
    local desc="$*"
    printf "  %s) %s\n     %s\n\n" "${num}" "${title}" "$desc"
}

print_pkg_list() { for pkg in "$@"; do printf "  • %s\n" "$pkg"; done }

# Command availability check
command_exists() { command -v "$1" >/dev/null 2>&1; }

# Dry-run wrapper
safe_run() {
    if [ "${DRY_RUN:-false}" = true ]; then
        printf "[DRY RUN] %s\n" "$*"
        return 0
    fi
    "$@"
}

# Error handling
trap 'error_handler $? $LINENO' ERR
error_handler() {
    local exit_code=$1
    local line_no=$2
    echo ""
    err "Error occurred at line $line_no with exit code $exit_code"
    exit $exit_code
}

# Package helpers
update_package_list() {
    info "Updating package list..."
    safe_run sudo apt-get update -qq || { err "Failed to update package list"; return 1; }
}

install_packages() {
    if [ $# -eq 0 ]; then
        warn "No packages specified for installation"
        return 0
    fi
    info "Installing packages: $*"
    if [ "${DRY_RUN:-false}" = true ]; then
        echo "   [DRY RUN] Would install: $*"
        return 0
    fi
    update_package_list || return 1
    safe_run sudo apt-get install -y --no-install-recommends "$@" || { err "Failed to install packages: $*"; return 1; }
    ok "Packages installed successfully"
}

package_installed() { dpkg -l "$1" 2>/dev/null | grep -q "^ii"; }

# Standard module installation flow
# Usage: standard_install_flow "Title" "Description" PACKAGE_ARRAY[@] [callback_function]
standard_install_flow() {
    local title="$1"
    local description="$2"
    shift 2
    local -n packages=$1
    local callback_fn="${2:-}"
    
    section_header "$title"
    info_box "$title" "$description"
    
    echo "The following packages will be installed:"
    print_pkg_list "${packages[@]}"
    echo ""
    
    if ! confirm "Do you want to proceed with installation?" "Y"; then
        warning_message "Skipping $title installation"
        return 0
    fi
    
    log_info "Starting $title installation"
    install_packages "${packages[@]}" || {
        error_message "$title installation failed"
        return 1
    }
    
    # Run optional callback function
    if [ -n "$callback_fn" ] && type "$callback_fn" >/dev/null 2>&1; then
        "$callback_fn"
    fi
    
    success_message "$title installed successfully!"
    log_info "$title installation completed"
    return 0
}

# Installation completion message with log reference
# Usage: print_install_completion "Component Name" [log_file_path]
print_install_completion() {
    local component_name="$1"
    local log_file="${2:-}"
    
    ok "$component_name installation finished."
    
    if [ -n "$log_file" ] && [ -f "$log_file" ]; then
        info "Installation details logged to: $log_file"
    fi
}

# Verify command installation and show version
# Usage: verify_command_installation "command_name" "display_name"
verify_command_installation() {
    local cmd="$1"
    local display_name="${2:-$1}"
    
    if command_exists "$cmd"; then
        local version
        version=$("$cmd" --version 2>&1 | head -1 || echo "version unknown")
        ok "$display_name installed: $version"
        return 0
    else
        err "$display_name installation failed or not found"
        return 1
    fi
}

# Add user to a system group
# Usage: add_user_to_group "username" "groupname"
add_user_to_group() {
    local username="$1"
    local groupname="$2"
    
    if groups "$username" | grep -q "\b$groupname\b"; then
        info "User $username is already in $groupname group"
        return 0
    fi
    
    safe_run sudo groupadd "$groupname" 2>/dev/null || true
    safe_run sudo usermod -aG "$groupname" "$username"
    ok "Added $username to $groupname group (requires re-login)"
    return 0
}

# Interaction
confirm() {
    # If non-interactive or dry-run is enabled, auto-confirm
    if [ "${AUTO_CONFIRM:-false}" = true ] || [ "${DRY_RUN:-false}" = true ]; then
        info "[AUTO] $1 -> proceeding"
        return 0
    fi
    local prompt="$1"
    local default="${2:-N}"
    local response
    read -rp "$prompt " response
    response="${response:-$default}"
    [[ "$response" =~ ^[Yy]$ ]]
}

info_box() {
    local title="$1"
    local message="$2"
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    printf "║ %-62s ║\n" "$title"
    echo "╠════════════════════════════════════════════════════════════════╣"
    # Use %b to interpret any embedded ANSI escape sequences in message lines
    echo "$message" | fold -s -w 62 | while IFS= read -r line; do
        printf "║ %-62b ║\n" "$line"
    done
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
}

# Spinner for long-running operations
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

# Select menu helper
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
    # If non-interactive or dry-run, default to the first option
    if [ "${AUTO_CONFIRM:-false}" = true ] || [ "${DRY_RUN:-false}" = true ]; then
        info "[AUTO] selecting default option 1"
        return 0
    fi
    while true; do
        read -rp "Enter your choice (1-${#options[@]}): " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#options[@]}" ]; then
            return $((choice - 1))
        else
            echo "Invalid choice. Please try again."
        fi
    done
}

# System helpers
get_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$NAME $VERSION_ID"
    else
        echo "Unknown"
    fi
}

is_root() {
    [ "$EUID" -eq 0 ]
}

get_ram_mb() {
    free -m | awk '/^Mem:/ {print $2}'
}

is_raspberry_pi() {
    if [ -f /proc/device-tree/model ] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
        return 0
    fi
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "${ID:-}${ID_LIKE:-}${NAME:-}${PRETTY_NAME:-}" in
            *raspbian*|*raspberry*) return 0 ;;
        esac
    fi
    return 1
}

check_sudo() {
    if ! command_exists sudo; then
        error_message "sudo is not installed. Please install sudo or run as root."
        return 1
    fi
    if ! sudo -n true 2>/dev/null; then
        echo "🔑 Sudo access required. Please enter your password:"
        sudo -v || {
            error_message "Failed to obtain sudo access"
            return 1
        }
    fi
    return 0
}

# File utilities
ensure_directory() {
    local dir="$1"
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo "📁 Created directory: $dir"
    fi
}

backup_file() {
    local file="$1"
    if [ -f "$file" ]; then
        local backup="${file}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$file" "$backup"
        echo "💾 Backed up $file to $backup"
    fi
}

# Logging
LOG_FILE="/tmp/autoos-install-$(date +%Y%m%d_%H%M%S).log"
log() {
    local level="$1"
    shift
    local message="$*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message" >> "$LOG_FILE"
    if [ "${VERBOSE:-false}" = true ]; then
        case "$level" in
            INFO)
                cecho "${COLOR_CYAN}${COLOR_BOLD}" "💡 [INFO]  $message"
                ;;
            ERROR)
                cecho "${COLOR_RED}${COLOR_BOLD}" "❌ [ERROR] $message"
                ;;
            WARNING)
                cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  [WARN]  $message"
                ;;
            *)
                echo "[$level] $message"
                ;;
        esac
    fi
}
log_info() { log "INFO" "$*"; }
log_error() { log "ERROR" "$*"; }
log_warning() { log "WARNING" "$*"; }

# GNOME helpers
version_to_num() { echo "$1" | awk -F. '{ printf("%d%02d%02d\n", $1,$2,$3); }'; }

check_gnome_version() {
    if ! command -v gnome-shell >/dev/null 2>&1; then
        echo "Error: GNOME Shell not found." >&2
        return 1
    fi
    local v
    v=$(gnome-shell --version | awk '{print $3}')
    echo "Detected GNOME Shell version: ${v}"
}

install_gnome_extension() {
    local ext_id="$1"; local ext_name="$2"
    if [ "${DRY_RUN:-false}" = true ]; then
        info "[DRY RUN] Would install ${ext_name} (${ext_id})"
        return 0
    fi
    if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
        gnome-shell-extension-installer "$ext_id" --yes
    else
        err "gnome-shell-extension-installer not found"
        return 1
    fi
}

# Display GNOME resource requirements for Raspberry Pi
show_gnome_resource_requirements() {
    cecho "${COLOR_CYAN}${COLOR_BOLD}" "📊 GNOME Resource Requirements for Raspberry Pi:"
    echo ""
    cecho "${COLOR_YELLOW}" "  RAM:  1.0-1.5 GB idle (vs ~400MB for PIXEL)"
    cecho "${COLOR_YELLOW}" "        2.5-4.0 GB with applications"
    echo ""
    cecho "${COLOR_YELLOW}" "  CPU:  5-15% idle, 20-40% during use"
    cecho "${COLOR_YELLOW}" "        May cause thermal throttling"
    echo ""
    cecho "${COLOR_YELLOW}" "  GPU:  Continuous usage for compositing"
    cecho "${COLOR_YELLOW}" "        May impact video playback"
    echo ""
    cecho "${COLOR_YELLOW}" "  Storage: ~1.5 GB for GNOME packages"
    echo ""
    cecho "${COLOR_GREEN}" "  ✓ Recommended: Pi 5 with 8GB RAM"
    cecho "${COLOR_RED}" "  ✗ Not Recommended: Pi 4 with 4GB or less"
    echo ""
}

# Service management helpers
enable_and_start_service() {
    local service_name="$1"
    
    if [ "${DRY_RUN:-false}" = true ]; then
        info "[DRY RUN] Would enable and start service: $service_name"
        return 0
    fi
    
    safe_run sudo systemctl enable "$service_name" 2>/dev/null || {
        warn "Failed to enable $service_name"
        return 1
    }
    safe_run sudo systemctl start "$service_name" 2>/dev/null || {
        warn "Failed to start $service_name"
        return 1
    }
    ok "Service $service_name enabled and started"
    return 0
}

# Create directory with user feedback
create_directory_with_feedback() {
    local dir="$1"
    local description="${2:-directory}"
    
    if [ -d "$dir" ]; then
        info "$description already exists at: $dir"
        return 0
    fi
    
    safe_run mkdir -p "$dir"
    ok "Created $description at: $dir"
    return 0
}


