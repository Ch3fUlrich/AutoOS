#!/usr/bin/env bash
# Wrapper shim for Raspberry Pi GNOME installer.
# Delegate to the canonical implementation in ../modules/gnome-extensions_installer.sh
# to avoid duplicating code.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="${SCRIPT_DIR}/../modules"

# Prefer the module implementation; fall back to a local copy only for legacy setups.
if [ -f "${MODULE_DIR}/gnome-extensions_installer.sh" ]; then
    # shellcheck source=/dev/null
    source "${MODULE_DIR}/gnome-extensions_installer.sh"
elif [ -f "${SCRIPT_DIR}/install_gnome_pi.sh" ]; then
    # legacy: source local copy if present
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/install_gnome_pi.sh"
else
    echo "Error: cannot find gnome-extensions_installer.sh to delegate GNOME install" >&2
    exit 1
fi

set -Eeuo pipefail

# Call the canonical function if available. This preserves the previous
# behaviour of this script while ensuring a single implementation exists.
if declare -f install_gnome_on_pi >/dev/null 2>&1; then
    if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
        install_gnome_on_pi "$@"
        exit 0
    fi
    # Export for callers that source this script and expect the function
    export -f install_gnome_on_pi
else
    echo "Error: install_gnome_on_pi not available after sourcing module" >&2
    if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
        exit 2
    fi
fi
