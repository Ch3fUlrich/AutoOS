#!/usr/bin/env bash
# Raspberry Pi specific setup helpers (autologin, pi-apps, moonlight helpers)
# This file should be sourced after `pi_overrides.sh`.

# Prevent double-sourcing
if [ -n "${AUTOOS_PI_AUTOGEN_LOADED:-}" ]; then
    return 0
fi
AUTOOS_PI_AUTOGEN_LOADED=1

section_header "Raspberry Pi Specific Helpers"

install_pi_extras() {
    if [ "${IS_RPI:-false}" != true ]; then
        info "Skipping Pi extras: not running on Raspberry Pi OS"
        return 0
    fi

    info "Running Raspberry Pi extra setup"

    # 1) Autologin (optional)
    if confirm "Enable autologin on tty1 for the current user ($USER)?" "N"; then
        sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
        sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USER --noclear %I \$TERM
EOF
        ok "Autologin configured for tty1"
    else
        info "Autologin skipped"
    fi

    # 2) Install audio and optional Moonlight helper packages
    if confirm "Install PulseAudio and related helpers (recommended for streaming)?" "N"; then
        sudo apt-get update -qq
        sudo apt-get install -y pulseaudio curl || {
            warn "Failed to install pulseaudio or curl — continuing"
        }
        sudo systemctl --global enable pulseaudio || true
        ok "PulseAudio (or equivalent) installed/enabled"
    fi

    # 3) pi-apps (convenience app store) — installed via upstream script
    if confirm "Install pi-apps (third-party apps manager, optional)?" "N"; then
        curl -fsSL https://raw.githubusercontent.com/Botspot/pi-apps/master/install | bash || {
            warn "pi-apps install script failed"
        }
        ok "pi-apps installation attempted"
    fi

    # 4) Moonlight helper instructions (deferred build steps)
    info "If you want Moonlight (game streaming), see Raspberry Pi 5 README for build options"

    log_info "Raspberry Pi extra setup finished"
}

# Export function name so it can be tested by main.sh
export -f install_pi_extras
