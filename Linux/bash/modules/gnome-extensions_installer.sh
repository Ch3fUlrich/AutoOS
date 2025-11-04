#!/usr/bin/env bash
# GNOME Extensions — Grouped Installer (moved into modules)
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer module-local helpers
# shellcheck source=/dev/null
if [ -f "$MODULE_DIR/utils.sh" ]; then
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    source "$MODULE_DIR/../modules/utils.sh"
else
    echo "Unable to locate utils.sh - extensions installer needs helpers." >&2
    return 1
fi

set -Eeuo pipefail

if [ -z "${GNOME_INSTALLER_SOURCED:-}" ]; then
    section_header "GNOME Extensions — Grouped Installer"
    GNOME_INSTALLER_SOURCED=1
fi

# Source platform-specific helpers (Raspberry Pi install / optimizations)
if [ -f "$MODULE_DIR/gnome-extensions-platform.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/gnome-extensions-platform.sh"
fi

# Source core installer logic
if [ -f "$MODULE_DIR/gnome-extensions-core.sh" ]; then
    # shellcheck source=/dev/null
    source "$MODULE_DIR/gnome-extensions-core.sh"
fi
# Default install method:
#   auto       - try apt/package installs first, fall back to extensions.gnome.org when ext_id provided (default)
#   apt        - only install via apt packages; never attempt extension-site installs
#   extensions - only install via extensions.gnome.org (skip apt installs)
INSTALL_METHOD="auto"

# Parse a simple --install-method flag when the script is executed directly.
parse_cli_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --install-method=*) INSTALL_METHOD="${1#*=}"; shift ;;
            --install-method) INSTALL_METHOD="$2"; shift 2 ;;
            -m) INSTALL_METHOD="$2"; shift 2 ;;
            --non-interactive|--yes|-y) export AUTO_CONFIRM=true; shift ;;
            --dry-run) export DRY_RUN=true; shift ;;
            --help|-h)
                cecho "${COLOR_BOLD}${COLOR_BLUE}" "Usage: $0 [OPTIONS]"
                echo ""
                cecho "${COLOR_CYAN}" "Options:"
                cecho "${COLOR_YELLOW}" "  --install-method=MODE   Install method: auto (default), apt, or extensions"
                cecho "${COLOR_YELLOW}" "  -m MODE                 Short form of --install-method"
                cecho "${COLOR_YELLOW}" "  --non-interactive, -y   Skip all confirmation prompts (auto-confirm)"
                cecho "${COLOR_YELLOW}" "  --dry-run               Show what would be done without making changes"
                cecho "${COLOR_YELLOW}" "  --help, -h              Show this help message"
                exit 0
                ;;
            --) shift; break ;;
            *) shift ;;
        esac
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    parse_cli_args "$@"
fi

    # GNOME optimizations are handled by the platform helper
    # (apply_gnome_pi_optimizations) — avoid running gsettings here when the
    # module is sourced. Resource requirement text is provided by
    # utils.sh via show_gnome_resource_requirements().

# Platform-specific functionality (Raspberry Pi GNOME installer & optimizations)
# moved into `modules/gnome-extensions-platform.sh` and are sourced at the
# top of this file when available. This keeps platform helpers separated from
# the core extensions-installation logic.

# Ensure installer helper is present (gnome-shell-extension-installer)
ensure_extension_installer() {
    if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
        return 0
    fi

    info "gnome-shell-extension-installer not found. Attempting to install it to /usr/local/bin"
    if [ "${DRY_RUN:-false}" = true ]; then
        info "[DRY RUN] Would download gnome-shell-extension-installer"
        return 0
    fi

    safe_run sudo curl -fsSL -o /usr/local/bin/gnome-shell-extension-installer \
        https://raw.githubusercontent.com/brunelli/gnome-shell-extension-installer/master/gnome-shell-extension-installer || {
        warning_message "Could not download gnome-shell-extension-installer automatically"
        return 1
    }
    safe_run sudo chmod +x /usr/local/bin/gnome-shell-extension-installer || true
    info "Installed gnome-shell-extension-installer"
}

# When executed directly, the core module will handle parsing and running the
# installer. If callers source this file, they can invoke
# `install_gnome_extensions_grouped` (provided by the core module) directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # If core wasn't already sourced above (rare), attempt to source it now.
    if ! declare -f install_gnome_extensions_grouped >/dev/null 2>&1 && [ -f "$MODULE_DIR/gnome-extensions-core.sh" ]; then
        # shellcheck source=/dev/null
        source "$MODULE_DIR/gnome-extensions-core.sh"
    fi
    # Parse any CLI args if defined in the core module
    if declare -f parse_cli_args >/dev/null 2>&1; then
        parse_cli_args "$@"
    fi
    install_gnome_extensions_grouped "$@"
fi

export -f install_gnome_extensions_grouped 2>/dev/null || true
# Also export the GNOME-on-Pi installer function to allow other scripts
# (for example, legacy or shim scripts in pi_modules/) to source this
# module and call the implementation rather than duplicating it.
if declare -f install_gnome_on_pi >/dev/null 2>&1; then
    export -f install_gnome_on_pi
fi
