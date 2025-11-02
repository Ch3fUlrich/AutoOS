#!/usr/bin/env bash
# GNOME Extensions — Core installer logic (metadata, grouping, installs)
MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer module-local helpers
# shellcheck source=/dev/null
if [ -f "$MODULE_DIR/utils.sh" ]; then
    source "$MODULE_DIR/utils.sh"
elif [ -f "$MODULE_DIR/../modules/utils.sh" ]; then
    source "$MODULE_DIR/../modules/utils.sh"
else
    echo "Unable to locate utils.sh - extensions core needs helpers." >&2
    return 1
fi

set -Eeuo pipefail

# Extension metadata storage
# Ensure any previously-used names are unset so we can safely declare associative arrays
unset EXT_META EXT_EXTRA_TYPE EXT_EXTRA_PKG EXT_DESC EXT_FALLBACK_EXTID GROUPS GROUP_DESC 2>/dev/null || true
declare -A EXT_META
declare -A EXT_EXTRA_TYPE
declare -A EXT_EXTRA_PKG
declare -A EXT_DESC
declare -A EXT_FALLBACK_EXTID
declare -A GROUPS
declare -A GROUP_DESC

# Simple JSON parser for extension metadata
# Expects extensions.json with structure: {"extensions": [...], "groups": {...}}
load_extensions_json() {
    local json_file="$MODULE_DIR/extensions.json"
    if [ ! -f "$json_file" ]; then
        error_message "Extension metadata file not found: $json_file"
        return 1
    fi

    # Parse extensions array
    # Use a simple grep/sed approach (no jq dependency required)
    local in_ext=0
    local curr_id="" curr_name="" curr_desc="" curr_min="" curr_max="" curr_rpi="" curr_type="" curr_pkg="" curr_extid=""
    
    while IFS= read -r line; do
        # Detect start of extension object
        if [[ "$line" =~ \"id\":[[:space:]]*\"?([^,\"]+)\"? ]]; then
            # Store previous if exists
            if [ -n "$curr_id" ]; then
                EXT_META[$curr_id]="${curr_name}|${curr_min}|${curr_max}|${curr_rpi}"
                [ -n "$curr_desc" ] && EXT_DESC[$curr_id]="$curr_desc"
                [ -n "$curr_type" ] && EXT_EXTRA_TYPE[$curr_id]="$curr_type"
                [ -n "$curr_pkg" ] && EXT_EXTRA_PKG[$curr_id]="$curr_pkg"
                [ -n "$curr_extid" ] && EXT_FALLBACK_EXTID[$curr_id]="$curr_extid"
            fi
            # Start new extension
            curr_id="${BASH_REMATCH[1]}"
            curr_name="" curr_desc="" curr_min="0.0" curr_max="999" curr_rpi="unknown" curr_type="" curr_pkg="" curr_extid=""
        fi
        
        [[ "$line" =~ \"name\":[[:space:]]*\"([^\"]+)\" ]] && curr_name="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"desc\":[[:space:]]*\"([^\"]+)\" ]] && curr_desc="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"min\":[[:space:]]*\"([^\"]+)\" ]] && curr_min="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"max\":[[:space:]]*\"([^\"]+)\" ]] && curr_max="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"rpi\":[[:space:]]*\"([^\"]+)\" ]] && curr_rpi="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"type\":[[:space:]]*\"([^\"]+)\" ]] && curr_type="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"pkg\":[[:space:]]*\"([^\"]+)\" ]] && curr_pkg="${BASH_REMATCH[1]}"
        [[ "$line" =~ \"ext_id\":[[:space:]]*([0-9]+) ]] && curr_extid="${BASH_REMATCH[1]}"
    done < "$json_file"
    
    # Store last extension
    if [ -n "$curr_id" ]; then
        EXT_META[$curr_id]="${curr_name}|${curr_min}|${curr_max}|${curr_rpi}"
        [ -n "$curr_desc" ] && EXT_DESC[$curr_id]="$curr_desc"
        [ -n "$curr_type" ] && EXT_EXTRA_TYPE[$curr_id]="$curr_type"
        [ -n "$curr_pkg" ] && EXT_EXTRA_PKG[$curr_id]="$curr_pkg"
        [ -n "$curr_extid" ] && EXT_FALLBACK_EXTID[$curr_id]="$curr_extid"
    fi

    # Parse groups
    local in_groups=0
    local group_name="" group_desc="" group_items=""
    while IFS= read -r line; do
        if [[ "$line" =~ "groups" ]]; then
            in_groups=1
            continue
        fi
        if [ $in_groups -eq 1 ]; then
            if [[ "$line" =~ \"([^\"]+)\":[[:space:]]*\{ ]]; then
                # Store previous group if exists
                if [ -n "$group_name" ]; then
                    GROUPS["$group_name"]="$group_items"
                    [ -n "$group_desc" ] && GROUP_DESC["$group_name"]="$group_desc"
                fi
                group_name="${BASH_REMATCH[1]}"
                group_desc=""
                group_items=""
            fi
            [[ "$line" =~ \"desc\":[[:space:]]*\"([^\"]+)\" ]] && group_desc="${BASH_REMATCH[1]}"
            if [[ "$line" =~ \"items\":[[:space:]]*\[([^\]]+)\] ]]; then
                group_items="${BASH_REMATCH[1]}"
                # Clean up: remove quotes and commas, convert to space-separated
                group_items=$(echo "$group_items" | sed -E 's/"//g;s/,/ /g')
            fi
        fi
    done < "$json_file"
    
    # Store last group
    if [ -n "$group_name" ]; then
        GROUPS["$group_name"]="$group_items"
        [ -n "$group_desc" ] && GROUP_DESC["$group_name"]="$group_desc"
    fi
}

print_group_summary() {
    cecho "${COLOR_BLUE}${COLOR_BOLD}" "Available groups:"
    local i=1
    for g in "${!GROUPS[@]}"; do
        local ids=${GROUPS[$g]}
        local desc="${GROUP_DESC[$g]:-}"
        local count
        count=$(echo "$ids" | wc -w)

        # Build a comma-separated list of display names for the group's items
        local app_list=""
        for id in $ids; do
            local display_name=""
            if [ -n "${EXT_META[$id]:-}" ]; then
                IFS='|' read -r display_name _min _max _rpi <<< "${EXT_META[$id]}"
            else
                if [[ "$id" =~ ^apt- ]]; then
                    display_name="${id#apt-}"
                else
                    display_name="$id"
                fi
            fi
            if [ -z "$app_list" ]; then
                app_list="$display_name"
            else
                app_list="$app_list, $display_name"
            fi
        done

        # Use shared color helpers instead of raw printf with color vars
        cecho "${COLOR_YELLOW}${COLOR_BOLD}" "  ${i}) ${g} (${count} extensions)"
        if [ -n "$desc" ]; then
            cecho "${COLOR_GRAY}" "     $desc"
        fi
        cecho "${COLOR_GRAY}" "     Includes: ${app_list}"
        ((i++))
    done
}

select_groups() {
    cecho "${COLOR_BOLD}" "Select groups to install (comma-separated numbers), or 'a' for all, 'n' to skip:"
    # Non-interactive / auto-confirm mode should skip prompts and choose sensible defaults
    if [ "${AUTO_CONFIRM:-false}" = true ] || [ "${DRY_RUN:-false}" = true ]; then
        info "[AUTO] Non-interactive mode: selecting all groups"
        SELECTION="all"
        return 0
    fi

    print_group_summary
    echo ""
    read -rp "Enter selection: " selection
    if [ "$selection" = "n" ] || [ -z "$selection" ]; then
        return 0
    fi
    if [ "$selection" = "a" ]; then
        SELECTION="all"
        return 0
    fi
    # Convert numbers to group names
    local chosen_groups=()
    IFS=',' read -ra parts <<< "$selection"
    for p in "${parts[@]}"; do
        p=$(echo "$p" | sed -E 's/[^0-9]//g')
        if [ -z "$p" ]; then continue; fi
        local idx=1
        for g in "${!GROUPS[@]}"; do
            if [ "$idx" -eq "$p" ]; then
                chosen_groups+=("$g")
                break
            fi
            ((idx++))
        done
    done
    SELECTED_GROUPS=("${chosen_groups[@]}")
}

gather_extensions_to_install() {
    TO_INSTALL=()
    if [ "${SELECTION:-}" = "all" ]; then
        for g in "${!GROUPS[@]}"; do
            TO_INSTALL+=( ${GROUPS[$g]} )
        done
    else
        for g in "${SELECTED_GROUPS[@]:-}"; do
            TO_INSTALL+=( ${GROUPS[$g]} )
        done
    fi
    # Remove duplicates
    TO_INSTALL=( $(printf "%s\n" "${TO_INSTALL[@]}" | awk '!seen[$0]++') )
}

check_extension_compat() {
    local ext_id=$1
    local meta=${EXT_META[$ext_id]:-}
    if [ -z "$meta" ]; then
        # Unknown extension: be conservative
        return 0
    fi
    IFS='|' read -r name min_ver max_ver rpi_flag <<< "$meta"
    local curr_ver="0.0.0"
    if command_exists gnome-shell; then
        curr_ver=$(gnome-shell --version | awk '{print $3}')
    fi
    # version_to_num from gnome-utils.sh is available
    local curr_num=$(version_to_num "$curr_ver")
    local min_num=$(version_to_num "$min_ver")
    local max_num=$(version_to_num "$max_ver")

    if [ "$curr_num" -lt "$min_num" ] || [ "$curr_num" -gt "$max_num" ]; then
        warn "${name} (id=${ext_id}) incompatible with GNOME ${curr_ver} (supported ${min_ver}-${max_ver})"
        return 2
    fi

    if is_raspberry_pi; then
        case "$rpi_flag" in
            yes) return 0 ;;
            no) warn "${name} (id=${ext_id}) is known to not work well on Raspberry Pi"; return 3 ;;
            maybe) warn "${name} (id=${ext_id}) may have issues on Raspberry Pi"; return 0 ;;
            *) return 0 ;;
        esac
    fi

    return 0
}

install_selected_extensions() {
    # Ensure helper available for extension installs, but apt installs do not depend on it.
    ensure_extension_installer || warn "gnome-shell-extension-installer not available; extension installs may need manual steps"

    for ext_id in "${TO_INSTALL[@]}"; do
        local meta=${EXT_META[$ext_id]:-}
        local name="Unknown"
        if [ -n "$meta" ]; then
            IFS='|' read -r name min_ver max_ver rpi_flag <<< "$meta"
        else
            # handle apt-* tokens
            if [[ "$ext_id" =~ ^apt-(.*) ]]; then
                name="${BASH_REMATCH[1]}"
            fi
        fi

        info "Preparing to install: ${name} (id=${ext_id})"

        # Compatibility check (skip check for explicit apt-package tokens)
        if [[ ! "${ext_id}" =~ ^apt- ]]; then
            # check_extension_compat can return non-zero codes to signal
            # incompatibility; avoid the ERR trap or 'set -e' causing an exit
            # by temporarily disabling the ERR trap and errexit, then
            # restoring them after the call.
            local _old_err_trap
            _old_err_trap=$(trap -p ERR || true)
            trap '' ERR
            set +e
            check_extension_compat "$ext_id"
            local status=$?
            set -e
            # restore previous ERR trap if it existed
            if [ -n "$_old_err_trap" ]; then
                eval "$_old_err_trap"
            else
                trap 'error_handler $? $LINENO' ERR
            fi
            if [ $status -eq 2 ]; then
                warning_message "Skipping ${name} due to GNOME version incompatibility"
                continue
            elif [ $status -eq 3 ]; then
                warning_message "Skipping ${name} because it is marked incompatible with Raspberry Pi"
                continue
            fi
        fi

        # Install packages (apt) when entry is an apt-* token or marked type=apt
        if [[ "${ext_id}" =~ ^apt- ]] || [[ "${EXT_EXTRA_TYPE[$ext_id]:-}" == "apt" ]]; then
            local pkgname=""
            if [[ "${ext_id}" =~ ^apt- ]]; then
                pkgname="${EXT_EXTRA_PKG[$ext_id]:-${ext_id#apt-}}"
            else
                pkgname="${EXT_EXTRA_PKG[$ext_id]:-$ext_id}"
            fi
            info "Installing apt package: ${pkgname}"
            if [ "${DRY_RUN:-false}" = true ]; then
                info "[DRY RUN] Would install package: ${pkgname}"
                record_installed_extension "${ext_id}" "${pkgname}"
                continue
            fi
            # If the user requested 'extensions' only, skip apt
            if [ "${INSTALL_METHOD}" = "extensions" ]; then
                info "Skipping apt package ${pkgname} because INSTALL_METHOD=extensions"
                local fallback="${EXT_FALLBACK_EXTID[$ext_id]:-}"
                if [ -n "$fallback" ]; then
                    info "Attempting install via extensions.gnome.org (id=${fallback})"
                    if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
                        if gnome-shell-extension-installer "$fallback" --yes; then
                            ok "Installed extension id=${fallback} for ${pkgname}"
                            record_installed_extension "${fallback}" "${pkgname} (extensions-mode)"
                        else
                            warn "Extension-site install failed for id=${fallback}"
                        fi
                    else
                        warn "No extension installer available to handle fallback id=${fallback}"
                    fi
                else
                    warn "No fallback ext_id configured for ${pkgname}; cannot install in extensions-only mode"
                fi
                continue
            fi

            if install_packages "${pkgname}"; then
                ok "Installed package ${pkgname}"
                record_installed_extension "${ext_id}" "${pkgname}"
            else
                warning_message "Failed to install package ${pkgname} via apt"
                # fallback: if we have an ext_id mapping for this entry, try installing from extensions.gnome.org
                local fallback="${EXT_FALLBACK_EXTID[$ext_id]:-}"
                if [ -n "$fallback" ]; then
                    info "Attempting fallback installation from extensions.gnome.org (id=${fallback})"
                    if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
                        if gnome-shell-extension-installer "$fallback" --yes; then
                            ok "Fallback installed extension id=${fallback} for ${pkgname}"
                            record_installed_extension "${fallback}" "${pkgname} (fallback)"
                        else
                            warn "Fallback install via extension installer failed for id=${fallback}"
                        fi
                    else
                        warn "No gnome-shell-extension-installer available to perform fallback for id=${fallback}"
                    fi
                fi
            fi
            continue
        fi

        # Default: use gnome-shell-extension-installer for numeric extension IDs
        if [ "${DRY_RUN:-false}" = true ]; then
            info "[DRY RUN] Would run: gnome-shell-extension-installer ${ext_id} --yes"
            continue
        fi

        if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
            if gnome-shell-extension-installer "${ext_id}" --yes; then
                ok "Installed ${name}"
                record_installed_extension "${ext_id}" "${name}"
            else
                warning_message "Failed to install ${name} via gnome-shell-extension-installer"
            fi
        else
            warning_message "gnome-shell-extension-installer not available — please install ${name} manually via extensions.gnome.org or Extension Manager"
        fi
    done
}

record_installed_extension() {
    local id=$1; local name=$2
    local logf="/tmp/autoos-installed-gnome-extensions.log"
    echo "${id}|${name}|$(date '+%Y-%m-%d %H:%M:%S')" >> "$logf"
}

rollback_installed_extensions() {
    local logf="/tmp/autoos-installed-gnome-extensions.log"
    if [ ! -f "$logf" ]; then
        info "No installation log found at $logf"
        return 0
    fi
    info "Rolling back extensions listed in $logf"
    # read in reverse order
    tac "$logf" | while IFS='|' read -r id name ts; do
        if command -v gnome-shell-extension-installer >/dev/null 2>&1; then
            if [ "${DRY_RUN:-false}" = true ]; then
                info "[DRY RUN] Would remove extension ${name} (${id})"
            else
                if gnome-shell-extension-installer --remove "${id}"; then
                    ok "Removed ${name} (${id})"
                else
                    warn "Failed to remove ${name} (${id}) via installer — you may need to remove it manually"
                fi
            fi
        else
            warn "No remove helper available for ${name} (${id}). Manual removal required."
        fi
    done
}

install_gnome_extensions_grouped() {
    info_box "GNOME Extensions" "This installer offers curated groups of stable and useful GNOME Shell extensions. Groups are bundled to make decisions easier and reduce bloat."

    # Raspberry Pi: If GNOME Shell isn't available, offer to install it
    if is_raspberry_pi && ! command_exists gnome-shell; then
        warning_message "Raspberry Pi OS detected without GNOME Shell"
        echo ""
        show_gnome_resource_requirements
        
        if confirm "Would you like to install GNOME Desktop on this Raspberry Pi?" "N"; then
            install_gnome_on_pi || {
                error_message "GNOME installation failed or was cancelled"
                return 1
            }
            
            # Check if gnome-shell is now available
            if command_exists gnome-shell; then
                success_message "GNOME installed successfully!"
                echo ""
                if confirm "Continue with GNOME extensions installation?" "Y"; then
                    info "Proceeding with extensions installation..."
                    echo ""
                else
                    info "You can run this installer again later to install extensions"
                    return 0
                fi
            else
                warning_message "GNOME Shell not available yet - reboot may be required"
                info "After rebooting into GNOME, run this installer again for extensions"
                
                if confirm "Reboot now to start using GNOME?" "N"; then
                    info "Rebooting in 5 seconds... (Ctrl+C to cancel)"
                    sleep 5
                    safe_run sudo reboot
                fi
                return 0
            fi
        else
            info "Skipping GNOME extensions - GNOME Shell not installed"
            info "Tip: Raspberry Pi OS's PIXEL desktop is optimized for Pi hardware"
            return 0
        fi
    fi

    # Quick deps check
    local deps=(curl)
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

    # load metadata (JSON if present)
    load_extensions_json || {
        error_message "Failed to load extension metadata"
        return 1
    }
    select_groups
    if [ -z "${SELECTION:-}" ] && [ "${#SELECTED_GROUPS[@]}" -eq 0 ]; then
        info "No groups selected — exiting"
        return 0
    fi

    gather_extensions_to_install
    if [ ${#TO_INSTALL[@]} -eq 0 ]; then
        info "No extensions to install"
        return 0
    fi

    cecho "${COLOR_BLUE}${COLOR_BOLD}" "Extensions to be installed:"
    for id in "${TO_INSTALL[@]}"; do
        local meta=${EXT_META[$id]:-}
        local name="Unknown"
        local desc=""
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

    if ! confirm "Proceed with installation of the above extensions?" "N"; then
        warning_message "Extension installation cancelled by user"
        return 0
    fi

    install_selected_extensions

    print_install_completion "GNOME extensions" "/tmp/autoos-installed-gnome-extensions.log"
}

# When executed directly, run the interactive installer. When sourced, callers
# can invoke `install_gnome_extensions_grouped` to run the flow.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    parse_cli_args "$@" 2>/dev/null || true
    install_gnome_extensions_grouped "$@"
fi

export -f install_gnome_extensions_grouped
