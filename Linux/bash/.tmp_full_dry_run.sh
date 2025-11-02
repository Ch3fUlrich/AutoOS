#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Load utils and modules (mirrors main.sh but avoids running main())
source "$SCRIPT_DIR/modules/utils.sh"
source "$SCRIPT_DIR/modules/core_packages.sh"
source "$SCRIPT_DIR/modules/programming.sh"
source "$SCRIPT_DIR/modules/shell_setup.sh"
source "$SCRIPT_DIR/modules/gnome_setup.sh"
source "$SCRIPT_DIR/modules/gnome-extensions_installer.sh"
source "$SCRIPT_DIR/modules/docker_setup.sh"
# Source pi modules if present
if [ -d "$SCRIPT_DIR/pi_modules" ]; then
  for f in "$SCRIPT_DIR/pi_modules"/*.sh; do
    [ -f "$f" ] && source "$f"
  done
fi
# Test mode: avoid prompting for sudo by stubbing check_sudo
check_sudo() { info "[TEST MODE] Skipping sudo check"; return 0; }
export DRY_RUN=true AUTO_CONFIRM=true
# Run preflight and full install
preflight_checks || true
install_everything || true
