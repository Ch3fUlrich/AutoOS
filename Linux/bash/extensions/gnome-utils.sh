#!/usr/bin/env bash
# gnome-utils.sh - Reusable GNOME shell utility functions

# Prevent direct execution of this library
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "This is a library file and should be sourced, not executed directly." >&2
    exit 1
fi

# Error handling function
error_handler() {
    local exit_code=$1
    local line_no=$2
    local bash_lineno=$3
    local last_command=$4
    local func_stack=$5
    echo "Error on or near line ${line_no}; exiting with status ${exit_code}"
    echo "Last command: ${last_command}"
    echo "Function stack: ${func_stack}"
    return "${exit_code}"
}

# Distribution detection
get_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${ID}"
    elif [ -f /etc/lsb-release ]; then
        . /etc/lsb-release
        echo "${DISTRIB_ID}" | tr '[:upper:]' '[:lower:]'
    else
        echo "unknown"
    fi
}

# GNOME version check
check_gnome_version() {
    local gnome_version
    if command -v gnome-shell >/dev/null; then
        gnome_version=$(gnome-shell --version | awk '{print $3}')
        echo "Detected GNOME Shell version: ${gnome_version}"
        # Convert version string to number for comparison
        local version_num=$(echo "${gnome_version}" | awk -F. '{ printf("%d%02d%02d\n", $1,$2,$3); }')
        if [ "${version_num}" -lt "400000" ]; then
            echo "Warning: Some extensions may not be compatible with GNOME ${gnome_version}" >&2
            return 1
        fi
        return 0
    else
        echo "Error: GNOME Shell not found. Please ensure GNOME is installed." >&2
        return 1
    fi
}

# Package manager setup
setup_package_manager() {
    local distro=$(get_distro)
    case "${distro}" in
        ubuntu|debian|pop|elementary|zorin|linuxmint)
            export DEBIAN_FRONTEND=noninteractive
            echo "apt-get"
            ;;
        fedora|rhel|centos)
            echo "dnf"
            ;;
        arch|manjaro)
            echo "pacman"
            ;;
        *)
            echo "unknown"
            return 1
            ;;
    esac
    return 0
}

# Get package manager options
get_pkg_manager_opts() {
    local pkg_manager=$1
    case "${pkg_manager}" in
        "apt-get")
            echo "-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o Dpkg::Use-Pty=0 -qq"
            ;;
        "dnf")
            echo "-y"
            ;;
        "pacman")
            echo "-S --noconfirm"
            ;;
        *)
            echo ""
            ;;
    esac
}

# Check dependencies
check_dependencies() {
    local pkg_manager=$1
    shift
    local dependencies=("$@")
    local missing=()
    
    for dep in "${dependencies[@]}"; do
        if ! command -v "${dep}" &>/dev/null; then
            missing+=("${dep}")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo "${missing[*]}"
        return 1
    fi
    return 0
}

# Privilege check
check_privileges() {
    if [ "$EUID" -ne 0 ]; then
        return 1
    fi
    return 0
}

# Extension installation
install_gnome_extension() {
    local ext_id=$1
    local ext_name=$2
    local max_retries=${3:-3}
    local retry_delay=${4:-5}
    local success=false

    # Check if extension is already installed
    if gnome-extensions list | grep -q "@${ext_id}"; then
        echo "Extension ${ext_name} is already installed."
        return 0
    fi

    for ((try=1; try<=max_retries; try++)); do
        if gnome-shell-extension-installer "${ext_id}" --yes; then
            success=true
            break
        else
            echo "Attempt ${try}/${max_retries} failed. Waiting ${retry_delay}s before retry..."
            sleep "${retry_delay}"
        fi
    done

    if [ "${success}" = true ]; then
        echo "Successfully installed ${ext_name}"
        return 0
    else
        echo "Failed to install ${ext_name} after ${max_retries} attempts" >&2
        return 1
    fi
}

# Version comparison
version_check() {
    local current=$1
    local min=$2
    local max=$3
    
    local current_num=$(version_to_num "${current}")
    local min_num=$(version_to_num "${min}")
    local max_num=$(version_to_num "${max}")
    
    [ "${current_num}" -ge "${min_num}" ] && [ "${current_num}" -le "${max_num}" ]
}

version_to_num() {
    echo "$1" | awk -F. '{ printf("%d%02d%02d\n", $1,$2,$3); }'
}

# Print completion message
print_completion_message() {
    cat <<EOF

Installation complete! Please follow these steps:

1. Restart GNOME Shell:
   - Press Alt+F2, type 'r' and press Enter
   - Or log out and back in

2. Enable extensions using:
   - GNOME Extensions application
   - GNOME Tweaks
   - Extension Manager

3. Configure extensions:
   - Use the Extensions application for individual settings
   - Visit extensions.gnome.org for documentation

Note: Some extensions might require additional configuration.
      Check the Extensions application for available settings.
EOF
}