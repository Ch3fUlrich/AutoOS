#!/usr/bin/env bash
set -euo pipefail
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/modules"
source "$MODULE_DIR/utils.sh"
source "$MODULE_DIR/gnome-extensions-core.sh"
export DRY_RUN=true AUTO_CONFIRM=true
load_extensions_json
print_group_summary
SELECTION=all
gather_extensions_to_install
cecho "${COLOR_BLUE}${COLOR_BOLD}" "Extensions to be installed:"
for id in "${TO_INSTALL[@]}"; do
    meta="${EXT_META[$id]:-}"
    name="Unknown"
    desc=""
    if [ -n "$meta" ]; then
        IFS='|' read -r name min_ver max_ver rpi_flag <<< "$meta"
        desc="${EXT_DESC[$id]:-}"
    else
        if [[ "$id" =~ ^apt- ]]; then
            name="${id#apt-}"
            desc="${EXT_DESC[$id]:-}"
        fi
    fi

    if [[ "$id" =~ ^apt- ]]; then
        cecho "${COLOR_MAGENTA}${COLOR_BOLD}" "  • [PKG] ${id} — ${COLOR_BOLD}${name}${COLOR_RESET}"
        [ -n "$desc" ] && cecho "${COLOR_GRAY}" "     ${desc}"
    else
        cecho "${COLOR_CYAN}${COLOR_BOLD}" "  • [EXT] ${id} — ${COLOR_BOLD}${name}${COLOR_RESET}"
        [ -n "$desc" ] && cecho "${COLOR_GRAY}" "     ${desc}"
    fi
done
