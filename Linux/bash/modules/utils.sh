#!/usr/bin/env bash
# Utility functions for AutoOS
# Located at Linux/bash/modules/utils.sh
# This file provides color helpers and common UI functions.

# Prevent double-sourcing
if [ -n "${AUTOOS_UTILS_LOADED:-}" ]; then
    return 0
fi
AUTOOS_UTILS_LOADED=1

# ------------------ Color helpers ------------------
_autoos_supports_color() {
    # stdout is a terminal and TERM is not dumb
    if [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ]; then
        return 0
    fi
    return 1
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

# Print colored message: cecho <color> <text>
cecho() {
    local color="$1"; shift
    # Use printf to avoid issues with -e and expand escape sequences
    printf "%b%s%b\n" "${color}" "$*" "${COLOR_RESET}"
}

info()    { cecho "${COLOR_CYAN}${COLOR_BOLD}" "💡 [INFO]  $*"; }
ok()      { cecho "${COLOR_GREEN}${COLOR_BOLD}" "✅ $*"; }
warn()    { cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  $*"; }
err()     { cecho "${COLOR_RED}${COLOR_BOLD}" "❌ $*"; }
title()   { cecho "${COLOR_MAGENTA}${COLOR_BOLD}" "$*"; }
header()  { cecho "${COLOR_BLUE}${COLOR_BOLD}" "$*"; }

# ------------------ Presentation helpers ------------------
# Wrap text in a color (reset applied automatically)
color_wrap() {
    local color="$1"; shift
    printf "%b%s%b" "${color}" "$*" "${COLOR_RESET}"
}

# Specific semantic colors
col_num()   { color_wrap "${COLOR_MAGENTA}${COLOR_BOLD}" "$*"; }
col_title() { color_wrap "${COLOR_BLUE}${COLOR_BOLD}" "$*"; }
col_app()   { color_wrap "${COLOR_GREEN}" "$*"; }
col_info()  { color_wrap "${COLOR_CYAN}" "$*"; }

# Print a numbered menu item: print_menu_item <num> <title> <desc>
print_menu_item() {
    local num="$1"; shift
    local title="$1"; shift
    local desc="$*"
    printf "  %s) %s\n     %s\n\n" "$(col_num "$num")" "$(col_title "$title")" "$desc"
}

# Print package list with colored names
print_pkg_list() {
    for pkg in "$@"; do
        printf "  • %s\n" "$(col_app "$pkg")"
    done
}

# Execute a command safely: if DRY_RUN=true then just echo the command,
# otherwise execute it. Accepts the command as arguments.
safe_run() {
    if [ "${DRY_RUN:-false}" = true ]; then
        # Print the command as it would run
        printf "[DRY RUN] %s\n" "$*"
        return 0
    fi

    # Execute the command
    "$@"
}

# ------------------ Error handling ------------------
trap 'error_handler $? $LINENO' ERR

error_handler() {
    local exit_code=$1
    local line_no=$2
    echo ""
    err "Error occurred at line $line_no with exit code $exit_code"
    echo "   Please check the error message above for details."
    echo ""
    exit $exit_code
}

# ------------------ Package helpers ------------------
update_package_list() {
    header "\n=============================="
    header "  Updating package list  "
    header "==============================\n"
    info "Updating package list..."
    safe_run sudo apt-get update -qq || {
        err "Failed to update package list"
        return 1
    }
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

    safe_run sudo apt-get install -y --no-install-recommends "$@" || {
        err "Failed to install packages: $*"
        return 1
    }

    ok "Packages installed successfully"
}

package_installed() {
    # usage: package_installed <pkgname>
    local pkg="$1"
    if [ -z "$pkg" ]; then
        warn "package_installed called without package name"
        return 1
    fi

    dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"
}

# ------------------ User interaction ------------------
confirm() {
    if [ "${AUTO_CONFIRM:-false}" = true ]; then
        return 0
    fi

    local prompt="$1"
    local default="${2:-N}"
    local response

    if [ "$default" = "Y" ]; then
        read -rp "$prompt [Y/n] " response
        response="${response:-Y}"
    else
        read -rp "$prompt [y/N] " response
        response="${response:-N}"
    fi

    [[ "$response" =~ ^[Yy]$ ]]
}

info_box() {
    local title="$1"
    local message="$2"

    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    printf "║ %-62s ║\n" "$title"
    echo "╠════════════════════════════════════════════════════════════════╣"

    echo "$message" | fold -s -w 62 | while IFS= read -r line; do
        printf "║ %-62s ║\n" "$line"
    done

    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
}

section_header() {
    local title="$1"
    header "\n=============================="
    header "  $title  "
    header "==============================\n"
}

success_message() {
    ok "$1"
}

warning_message() {
    warn "$1"
}

error_message() {
    err "$1"
}

# ------------------ Spinner ------------------
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

# ------------------ Select menu ------------------
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

# ------------------ System helpers ------------------
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

command_exists() {
    command -v "$1" >/dev/null 2>&1
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

# ------------------ File utilities ------------------
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

# ------------------ Logging ------------------
LOG_FILE="/tmp/autoos-install-$(date +%Y%m%d_%H%M%S).log"

log() {
    local level="$1"
    shift
    local message="$*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message" >> "$LOG_FILE"

    if [ "${VERBOSE:-false}" = true ]; then
        case "$level" in
            INFO)  cecho "${COLOR_CYAN}${COLOR_BOLD}" "💡 [INFO]  $message" ;;
            ERROR) cecho "${COLOR_RED}${COLOR_BOLD}" "❌ [ERROR] $message" ;;
            WARNING) cecho "${COLOR_YELLOW}${COLOR_BOLD}" "⚠️  [WARN]  $message" ;;
            *) echo "[$level] $message" ;;
        esac
    fi
}

log_info()    { log "INFO" "$*"; }
log_error()   { log "ERROR" "$*"; }
log_warning() { log "WARNING" "$*"; }