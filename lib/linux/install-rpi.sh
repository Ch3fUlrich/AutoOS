#!/usr/bin/env bash
# Raspberry Pi streaming console installers; sourced by install.sh for every loader.
# Uses its caller's shell options, ui_*, has_cmd, SYS_HOME and AUTOOS_* globals.
# shellcheck shell=bash

rpi_custom_is_installed() {
    case "$1" in
        rpi-cec-tv)
            [[ -f /etc/systemd/system/tv-cec-on.service && -f /usr/local/bin/tv-cec-on.sh ]]
            ;;
        rpi-moonlight-console)
            [[ -f "$SYS_HOME/.local/bin/moonlight-smart-launcher" && -f "$SYS_HOME/.config/autostart/moonlight.desktop" ]]
            ;;
        *) return 1 ;;
    esac
}

# Prints keyring, sources-list and os-release paths; overrides are test-only.
moonlight_repo_paths() {
    local key=/usr/share/keyrings/moonlight-game-streaming-moonlight-qt-archive-keyring.gpg
    local list=/etc/apt/sources.list.d/moonlight-game-streaming-moonlight-qt.list
    local os_release=/etc/os-release
    if [[ "${AUTOOS_TEST_MODE:-0}" == 1 ]]; then
        key="${AUTOOS_MOONLIGHT_KEYRING:-$key}"
        list="${AUTOOS_MOONLIGHT_SOURCES_LIST:-$list}"
        os_release="${AUTOOS_MOONLIGHT_OS_RELEASE:-$os_release}"
    fi
    printf '%s\n' "$key" "$list" "$os_release"
}

moonlight_flatpak_install() {
    if has_cmd flatpak; then
        flatpak install -y flathub com.moonlight_stream.Moonlight
    else
        ui_warn "Moonlight could not be installed: flatpak is unavailable"
        return 1
    fi
}

install_moonlight() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install Moonlight Game Streaming client"
        return 0
    fi
    if has_cmd moonlight-qt || has_cmd moonlight ||
        { has_cmd flatpak && flatpak info com.moonlight_stream.Moonlight >/dev/null 2>&1; }; then
        # shellcheck disable=SC2034 # install_component reads this shared result.
        INSTALL_SCRIPT_STATE="skipped"
        ui_muted "Moonlight is already installed - skipped"
        return 0
    fi
    if has_cmd apt-get; then
        local paths=() key list os_release ID="" VERSION_CODENAME=""
        mapfile -t paths < <(moonlight_repo_paths)
        key="${paths[0]}"; list="${paths[1]}"; os_release="${paths[2]}"
        # Match detect_system's os-release fields without probing the whole host.
        if [[ -r "$os_release" ]]; then
            # shellcheck disable=SC1090
            . "$os_release"
        fi
        case "$ID" in
            raspbian|debian|ubuntu) ;;
            *) ID="" ;;
        esac
        if [[ -z "$ID" || -z "$VERSION_CODENAME" ]]; then
            ui_muted "no supported Moonlight apt suite in os-release; falling back to flatpak"
            moonlight_flatpak_install
            return $?
        fi
        # A pre-existing list belongs to the user, even when only the key is missing.
        if [[ -e "$list" || -L "$list" ]] && ! grep -qF "signed-by=$key" "$list"; then
            ui_warn "keeping existing Moonlight sources list without our signed-by entry; only the signing key will be managed"
        fi
        if [[ ! -s "$key" || ! -f "$list" ]]; then
            ui_muted "setting up Moonlight Cloudsmith apt repository"
            if [[ ! -s "$key" ]]; then
                local tmp dearmored
                tmp="$(mktemp)" || { ui_warn "failed to create Moonlight signing key temp file"; return 1; }
                if ! curl -fsSL -o "$tmp" https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/gpgkey; then
                    rm -f "$tmp"
                    ui_warn "failed to download Moonlight signing key"
                    return 1
                fi
                # Stage beside the destination so only a complete key is renamed
                # into place, atomically on the same filesystem, with apt-readable mode.
                if ! dearmored="$($AUTOOS_SUDO mktemp "$key.tmp.XXXXXX")"; then
                    rm -f "$tmp"
                    ui_warn "failed to create Moonlight signing key staging file"
                    return 1
                fi
                if ! $AUTOOS_SUDO gpg --batch --yes --dearmor -o "$dearmored" "$tmp" \
                    || [[ ! -s "$dearmored" ]] \
                    || ! $AUTOOS_SUDO chmod 644 "$dearmored" \
                    || ! $AUTOOS_SUDO mv "$dearmored" "$key"; then
                    $AUTOOS_SUDO rm -f "$dearmored"
                    rm -f "$tmp"
                    ui_warn "failed to install Moonlight signing key"
                    return 1
                fi
                rm -f "$tmp"
            fi
            if [[ ! -e "$list" && ! -L "$list" ]]; then
                if ! printf '%s\n' "deb [signed-by=$key] https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/deb/$ID $VERSION_CODENAME main" | $AUTOOS_SUDO tee "$list" >/dev/null; then
                    ui_warn "failed to write Moonlight apt sources list"
                    return 1
                fi
                ui_ok "added Moonlight Cloudsmith repository"
            fi
        fi
        apt_update_once
        $AUTOOS_SUDO apt-get install -y moonlight-qt || {
            ui_muted "apt install failed; falling back to flatpak"
            moonlight_flatpak_install
        }
    else
        moonlight_flatpak_install
    fi
}

setup_rpi_cec_tv() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install TV HDMI-CEC power on/standby scripts and systemd services"
        return 0
    fi
    $AUTOOS_SUDO tee /usr/local/bin/tv-cec-on.sh >/dev/null <<'EOF'
#!/bin/bash
# HDMI-CEC wake up and active source script for TV & Raspberry Pi 5

for i in {1..20}; do
    if [ -e /dev/cec0 ]; then break; fi
    sleep 0.5
done

if [ ! -e /dev/cec0 ]; then
    echo "CEC device /dev/cec0 not found" >&2
    exit 1
fi

cec-ctl -d /dev/cec0 --playback --osd-name "Moonlight" >/dev/null 2>&1 || true
cec-ctl -d /dev/cec0 -t 0 --image-view-on >/dev/null 2>&1 || true

PHYS_ADDR=$(cec-ctl -d /dev/cec0 2>/dev/null | awk -F': ' '/Physical Address/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')
if [ -z "$PHYS_ADDR" ] || [ "$PHYS_ADDR" = "f.f.f.f" ]; then
    PHYS_ADDR="1.0.0.0"
fi

sleep 2
cec-ctl -d /dev/cec0 --active-source phys-addr="$PHYS_ADDR" >/dev/null 2>&1 || true
sleep 2
cec-ctl -d /dev/cec0 --active-source phys-addr="$PHYS_ADDR" >/dev/null 2>&1 || true

exit 0
EOF
    $AUTOOS_SUDO chmod +x /usr/local/bin/tv-cec-on.sh

    $AUTOOS_SUDO tee /usr/local/bin/tv-cec-off.sh >/dev/null <<'EOF'
#!/bin/bash
# Put connected TV into standby on Pi shutdown/poweroff
if [ -e /dev/cec0 ]; then
    cec-ctl -d /dev/cec0 -t 0 --standby >/dev/null 2>&1 || true
fi
exit 0
EOF
    $AUTOOS_SUDO chmod +x /usr/local/bin/tv-cec-off.sh

    $AUTOOS_SUDO tee /etc/systemd/system/tv-cec-on.service >/dev/null <<'EOF'
[Unit]
Description=TV HDMI-CEC Wakeup and Active Source
After=systemd-udevd.service
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/tv-cec-on.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    $AUTOOS_SUDO tee /etc/systemd/system/tv-cec-standby.service >/dev/null <<'EOF'
[Unit]
Description=TV HDMI-CEC Standby on Shutdown
DefaultDependencies=no
Before=poweroff.target halt.target
Conflicts=reboot.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/tv-cec-off.sh
TimeoutSec=10

[Install]
WantedBy=poweroff.target halt.target
EOF

    if has_cmd systemctl; then
        $AUTOOS_SUDO systemctl daemon-reload >/dev/null 2>&1 || true
        $AUTOOS_SUDO systemctl enable tv-cec-on.service tv-cec-standby.service >/dev/null 2>&1 || true
    fi
    ui_ok "installed and enabled TV HDMI-CEC power on/standby services"
}

setup_rpi_moonlight_console() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install moonlight-smart-launcher, autostart, and AntiMicroX controller manager"
        return 0
    fi

    mkdir -p "$SYS_HOME/.local/bin" "$SYS_HOME/.config/autostart" "$SYS_HOME/Documents/AntiMicroX"

    cat > "$SYS_HOME/.local/bin/moonlight-smart-launcher" <<'EOF'
#!/bin/bash
# moonlight-smart-launcher
# Console-style launcher for Moonlight Game Streaming on Raspberry Pi 5.
# Stops mouse emulation when Moonlight runs, and restores it when Moonlight exits.

if pgrep -x "moonlight" >/dev/null; then
    exit 0
fi

for i in {1..30}; do
    if [ -n "$WAYLAND_DISPLAY" ] && [ -e "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/$WAYLAND_DISPLAY" ]; then
        break
    elif [ -n "$DISPLAY" ]; then
        break
    fi
    sleep 0.5
done

# 1. Stop AntiMicroX if running to avoid conflicts with game controls
killall -9 antimicrox 2>/dev/null || true

# 2. Start Moonlight in console / full-screen mode
if command -v /usr/local/bin/moonlight >/dev/null 2>&1; then
    /usr/local/bin/moonlight "$@"
elif command -v moonlight-qt >/dev/null 2>&1; then
    moonlight-qt "$@"
elif command -v flatpak >/dev/null 2>&1 && flatpak info com.moonlight_stream.Moonlight >/dev/null 2>&1; then
    flatpak run com.moonlight_stream.Moonlight "$@"
fi

# 3. Once Moonlight exits, restart AntiMicroX for desktop mouse control
PROFILE="$HOME/Documents/AntiMicroX/Default Settings.gamecontroller.amgp"
if [ -f "$PROFILE" ] && command -v antimicrox >/dev/null 2>&1; then
    DISPLAY="${DISPLAY:-:0}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}" \
        antimicrox --profile "$PROFILE" --eventgen uinput --hidden >/dev/null 2>&1 &
fi
EOF
    chmod +x "$SYS_HOME/.local/bin/moonlight-smart-launcher"

    cat > "$SYS_HOME/.local/bin/toggle-controller-mouse" <<'EOF'
#!/bin/bash
# Toggles AntiMicroX controller mouse emulation on/off
PROFILE="$HOME/Documents/AntiMicroX/Default Settings.gamecontroller.amgp"

if pgrep -x antimicrox >/dev/null; then
    killall antimicrox
    echo "Controller mouse emulation: OFF"
else
    DISPLAY="${DISPLAY:-:0}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}" \
        antimicrox --profile "$PROFILE" --eventgen uinput --hidden >/dev/null 2>&1 &
    echo "Controller mouse emulation: ON"
fi
EOF
    chmod +x "$SYS_HOME/.local/bin/toggle-controller-mouse"

    local am_profile="$SYS_HOME/Documents/AntiMicroX/Default Settings.gamecontroller.amgp"
    if [[ ! -f "$am_profile" ]]; then
        cat > "$am_profile" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<gamecontroller configversion="19" appversion="3.2.3">
    <sdlname>Game Controller</sdlname>
    <stickAxisAssociation index="2" xAxis="3" yAxis="4"/>
    <stickAxisAssociation index="1" xAxis="1" yAxis="2"/>
    <sets>
        <set index="1">
            <stick index="1">
                <stickbutton index="1"><slots><slot><code>1</code><mode>mousemovement</mode></slot></slots></stickbutton>
                <stickbutton index="7"><slots><slot><code>3</code><mode>mousemovement</mode></slot></slots></stickbutton>
                <stickbutton index="5"><slots><slot><code>2</code><mode>mousemovement</mode></slot></slots></stickbutton>
                <stickbutton index="3"><slots><slot><code>4</code><mode>mousemovement</mode></slot></slots></stickbutton>
            </stick>
            <button index="1"><slots><slot><code>1</code><mode>mousebutton</mode></slot></slots></button>
            <button index="2"><slots><slot><code>3</code><mode>mousebutton</mode></slot></slots></button>
            <button index="3"><slots><slot><code>0x1000004</code><mode>keyboard</mode></slot></slots></button>
            <button index="4"><slots><slot><code>0x1000000</code><mode>keyboard</mode></slot></slots></button>
        </set>
    </sets>
</gamecontroller>
EOF
    fi

    cat > "$SYS_HOME/.config/autostart/moonlight.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Moonlight
Comment=Moonlight Game Streaming Console
Exec=/bin/bash -c "sleep 3; exec $HOME/.local/bin/moonlight-smart-launcher"
Icon=moonlight
Terminal=false
Categories=Game;
EOF

    chown -R "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$SYS_HOME/.local/bin" "$SYS_HOME/.config/autostart" "$SYS_HOME/Documents/AntiMicroX" 2>/dev/null || true
    ui_ok "installed Moonlight smart launcher, autostart, and controller profile"
}
