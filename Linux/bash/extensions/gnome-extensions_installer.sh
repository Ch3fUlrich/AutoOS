#!/usr/bin/env bash
# This copy of the installer has been moved to 'modules'.
# Please use: Linux/bash/modules/gnome-extensions_installer.sh
echo "This installer has moved. Run: Linux/bash/modules/gnome-extensions_installer.sh"
exit 0

main() {
    info_box "GNOME Extensions" "This installer offers curated groups of stable and useful GNOME Shell extensions. Groups are bundled to make decisions easier and reduce bloat."

    # Quick deps check
    local deps=(curl jq)
    local missing=()
    for d in "${deps[@]}"; do
        if ! command_exists "$d"; then
            missing+=("$d")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        info "Installing missing helper dependencies: ${missing[*]}"
        if [ "${DRY_RUN:-false}" = true ]; then
            info "[DRY RUN] Would install: ${missing[*]}"
        else
            safe_run sudo apt-get update -qq
            safe_run sudo apt-get install -y "${missing[@]}"
        fi
    fi

    print_group_summary
    select_groups
    if [ -z "${SELECTION:-}" ] && [ ${#SELECTED_GROUPS[@]:-0} -eq 0 ]; then
        info "No groups selected — exiting"
        return 0
    fi

    gather_extensions_to_install
    if [ ${#TO_INSTALL[@]} -eq 0 ]; then
        info "No extensions to install"
        return 0
    fi

    echo "Extensions to be installed:"
    for id in "${TO_INSTALL[@]}"; do
        echo "  • ${id} — ${EXT_MAP[$id]:-Unknown}"
    done

    if ! confirm "Proceed with installation of the above extensions?" "N"; then
        warning_message "Extension installation cancelled by user"
        return 0
    fi

    install_selected_extensions

    print_completion_message
}

main "$@"
