#!/usr/bin/env bash
# Raspberry Pi OS detection and package overrides
# This file is intended to be sourced from `config.sh` or `main.sh`.

# Prevent double-sourcing
if [ -n "${AUTOOS_PI_OVERRIDES_LOADED:-}" ]; then
    return 0
fi
AUTOOS_PI_OVERRIDES_LOADED=1

## Use canonical detection from modules/utils.sh when available to avoid
## duplicate logic. Source utils.sh if not already loaded.
if [ -z "${AUTOOS_UTILS_LOADED:-}" ]; then
    if [ -f "$(dirname "${BASH_SOURCE[0]}")/../modules/utils.sh" ]; then
        # shellcheck source=/dev/null
        source "$(dirname "${BASH_SOURCE[0]}")/../modules/utils.sh"
    fi
fi

if is_raspberry_pi; then
    IS_RPI=true
    log_info "Raspberry Pi OS detected — enabling Pi-specific overrides"

    # Append or adjust package lists to be more Pi-friendly. These are minimal
    # and intentionally conservative — avoid removing base items from the
    # default lists so the common modules still work.
    CORE_PACKAGES+=(raspi-config pulseaudio libraspberrypi-bin)

    # Some Pi-specific useful packages for multimedia / performance tuning
    PROGRAMMING_PACKAGES+=(build-essential cmake)

    # Provide a hook name that other scripts or main.sh can call. The actual
    # implementation of `install_pi_extras` lives in pi_autologin.sh so we
    # keep concerns separated.
    PI_MODULES_LOADED=true
else
    IS_RPI=false
fi

export IS_RPI
