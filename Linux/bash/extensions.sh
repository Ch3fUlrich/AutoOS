#!/bin/bash
# AutoOS - Extension management

# --- Source color/helpers (module-local) ---
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$MODULE_DIR/utils.sh" ]; then
	# shellcheck source=/dev/null
	source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
	# shellcheck source=/dev/null
	source "$MODULE_DIR/../modules/utils.sh"
else
	echo "Unable to locate modules/utils.sh - extension installer requires utilities." >&2
	return 1
fi

# Update and upgrade (use safe_run so DRY_RUN will only print these)
safe_run sudo apt update
safe_run sudo apt upgrade -y

# Install common GNOME extension packages
safe_run sudo apt install -y \
	gnome-shell-extension-manager \
	gnome-shell-extension-alphabetical-grid \
	gnome-shell-extension-appindicator \
	gnome-shell-extension-gpaste \
	gnome-shell-extensions \
	variety

# Additional extensions website
# ensure browser extension is installed
# https://extensions.gnome.org/extension/3780/ddterm/
# https://extensions.gnome.org/extension/1460/vitals/