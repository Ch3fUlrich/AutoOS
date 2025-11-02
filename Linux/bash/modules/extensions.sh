#!/usr/bin/env bash
# Deprecated shim. The modular installer has moved to modules/gnome-extensions_installer.sh
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "This shim (modules/extensions.sh) is deprecated. Please run modules/gnome-extensions_installer.sh directly." >&2
if [ -f "$MODULE_DIR/gnome-extensions_installer.sh" ]; then
    bash "$MODULE_DIR/gnome-extensions_installer.sh" "$@"
else
    echo "Installer not found at $MODULE_DIR/gnome-extensions_installer.sh" >&2
    exit 1
fi