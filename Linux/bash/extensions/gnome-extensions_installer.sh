#!/usr/bin/env bash

# Safer bash settings
set -Eeuo pipefail

# Source the utility functions
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
source "${SCRIPT_DIR}/gnome-utils.sh"

# Set up error handling
trap 'error_handler $? $LINENO $BASH_LINENO "$BASH_COMMAND" $(printf "::%s" ${FUNCNAME[@]:-})' ERR

# In the package installation section of gnome-extension-installer.sh
install_core_gnome_packages() {
    local packages=(
        gnome-shell-extensions
        gnome-tweaks
        gnome-shell-extension-manager
        gnome-shell-extension-dash-to-panel
        gnome-shell-extension-dash-to-dock
        gnome-shell-extension-arc-menu
        gnome-shell-extension-clipboard-indicator
        gnome-shell-extension-gpaste
        gnome-shell-extension-system-monitor
    )
    
    echo "Installing core GNOME packages..."
    "${PKG_MANAGER}" "${PKG_OPTS[@]}" install --no-install-recommends "${packages[@]}"
}

# Main execution
main() {
    # Check privileges
    if ! check_privileges; then
        if command -v sudo >/dev/null; then
            exec sudo -E bash "$0" "$@"
        else
            echo "This script must be run as root and 'sudo' is not available." >&2
            exit 1
        fi
    fi

    # Setup package manager
    PKG_MANAGER=$(setup_package_manager)
    if [ "${PKG_MANAGER}" = "unknown" ]; then
        echo "Unsupported distribution" >&2
        exit 1
    fi

    # Get package manager options
    PKG_OPTS=$(get_pkg_manager_opts "${PKG_MANAGER}")

    # Check GNOME version
    if ! check_gnome_version; then
        echo "GNOME version check failed" >&2
        exit 1
    fi

    # Define required dependencies
    DEPENDENCIES=(
        "wget"
        "curl"
        "jq"
        "unzip"
        "gnome-shell"
    )

    # Check and install dependencies
    MISSING_DEPS=$(check_dependencies "${PKG_MANAGER}" "${DEPENDENCIES[@]}")
    if [ -n "${MISSING_DEPS}" ]; then
        echo "Installing missing dependencies: ${MISSING_DEPS}"
        ${PKG_MANAGER} ${PKG_OPTS} install ${MISSING_DEPS}
    fi

    install_core_gnome_packages

    # Define extensions with compatibility information
    # In the extensions configuration section
    declare -A extensions=(
        # Core functionality
        [800]="Lock Keys|3.36|44"
        [1506]="Notification Banner Reloaded|40|44"
        [517]="Caffeine|3.36|44"
        [16]="Auto Move Windows|3.36|44"
        [277]="Impatience|3.36|44"
        [3088]="GNOME 4x UI Improvements|40|44"
        [779]="Clipboard History|3.38|44"
        [3780]="DDterm|40|44"
        [4144]="Status Area Horizontal Spacing|40|44"
        [3041]="Transparent Notifications|40|44"
        [1460]="Vitals|3.34|44"
        [1262]="EasyScreenCast|3.38|44"
        [4203]="Space Bar|41|44"
        [4167]="Forge|42|44"
        [5171]="WinTile Beyond|40|44"
        [3693]="Improved Workspace Indicator|40|44"
        [19]="User Themes|3.36|44"
        [750]="OpenWeather|3.38|44"
        [4269]="Desktop Icons NG|40|44"
        [4922]="Rounded Window Corners|42|44"
        [600]="Launch New Instance|3.38|44"
        [8]="Places Status Indicator|3.36|44"
        [602]="Window List|3.36|44"
        [1401]="Bluetooth Quick Connect|3.34|44"
        [615]="AppIndicator Support|3.36|44"
        [974]="Removable Drive Menu|3.36|44"
        [4158]="V-Shell|40|44"
        [4042]="Hot Edge|40|44"
        [3843]="QSTweak|40|44"
        [4268]="Alphabetical App Grid|40|44"
        [3193]="Blur My Shell|40|44"
        [4267]="Gesture Improvements|40|44"
        [3841]="Just Perfection|40|44"
    )

    # Install extensions
    local current_gnome_version=$(gnome-shell --version | awk '{print $3}')
    for ext_id in "${!EXTENSIONS[@]}"; do
        IFS="|" read -r name min_ver max_ver <<< "${EXTENSIONS[$ext_id]}"
        
        if version_check "${current_gnome_version}" "${min_ver}" "${max_ver}"; then
            install_gnome_extension "${ext_id}" "${name}"
        else
            echo "Skipping ${name}: not compatible with GNOME ${current_gnome_version}" >&2
        fi
    done

    print_completion_message
}

# Execute main function
main "$@"