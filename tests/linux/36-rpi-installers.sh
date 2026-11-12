# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "Raspberry Pi and desktop installer modules"

if it "moonlight: setup.sh sources every extracted installer module"; then
    out="$(bash -x "$ROOT/setup.sh" --help 2>&1)"; rc=$?
    missing=""
    for module in install-desktop-extras.sh install-rpi.sh; do
        [[ "$out" == *". $ROOT/lib/linux/$module"* ]] || missing+=" $module"
    done
    if [[ $rc -eq 0 && -z "$missing" ]]; then pass
    else fail "setup.sh exited $rc; modules not sourced:$missing"; fi
fi

if it "moonlight: standalone install.sh loaders retain all extracted functions"; then
    out="$(bash -c '
        set -euo pipefail
        . "$1/lib/linux/install.sh"
        for fn in install_jetbrains_mono_font install_moonlight install_zsh_plugins \
            setup_kitty_config setup_lsd_aliases setup_bat_alias setup_agy_pi_config \
            setup_rpi_cec_tv setup_rpi_moonlight_console; do
            declare -F "$fn" >/dev/null
        done
    ' _ "$ROOT" 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out (exit $rc)"; fi
fi

# All package managers, downloads and privileged writes are stand-ins. The two
# repository paths point only at this fixture, never the host's apt directory.
moonlight_test_stubs() {
    AUTOOS_DRY_RUN=0
    AUTOOS_SUDO=moonlight_test_sudo
    AUTOOS_MOONLIGHT_KEYRING="$moonlight_tmp/keyring.gpg"
    AUTOOS_MOONLIGHT_SOURCES_LIST="$moonlight_tmp/moonlight.list"
    has_cmd() {
        case "$1" in
            moonlight-qt|moonlight) [[ -f "$moonlight_tmp/native-installed" ]] ;;
            apt-get) return 0 ;;
            flatpak) [[ "${moonlight_flatpak:-0}" == 1 ]] ;;
            *) return 1 ;;
        esac
    }
    curl() {
        printf 'curl %s\n' "$*" >>"$moonlight_tmp/calls"
        [[ "${moonlight_failure:-}" != download ]] || return 22
        [[ "$1" == -fsSL && "$2" == -o && "$4" == */gpgkey ]] || return 2
        printf 'fixture armored key\n' >"$3"
    }
    wget() { printf 'wget\n' >>"$moonlight_tmp/calls"; return 1; }
    apt_update_once() { printf 'apt-update\n' >>"$moonlight_tmp/calls"; }
    flatpak() {
        printf 'flatpak %s\n' "$*" >>"$moonlight_tmp/calls"
        case "$1" in
            info) [[ -f "$moonlight_tmp/flatpak-installed" ]] ;;
            install) touch "$moonlight_tmp/flatpak-installed" ;;
            *) return 1 ;;
        esac
    }
    moonlight_test_sudo() {
        printf 'sudo %s\n' "$*" >>"$moonlight_tmp/calls"
        case "$1" in
            gpg)
                [[ "${moonlight_failure:-}" != dearmor ]] || return 2
                [[ "${*: -1}" != "$AUTOOS_MOONLIGHT_KEYRING" ]] || return 2
                grep -q 'fixture armored key' "${@: -1}" || return 2
                printf 'fixture binary key\n' >"$AUTOOS_MOONLIGHT_KEYRING"
                ;;
            tee) cat >"$AUTOOS_MOONLIGHT_SOURCES_LIST" ;;
            apt-get)
                [[ "${moonlight_failure:-}" != apt ]] || return 1
                touch "$moonlight_tmp/native-installed"
                ;;
            *) return 1 ;;
        esac
    }
}

if it "moonlight: dry-run prints would install and performs no download or write"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        AUTOOS_DRY_RUN=1
        install_script moonlight
    ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"would install Moonlight Game Streaming client"* \
        && -z "$(find "$moonlight_tmp" -mindepth 1 -print -quit)" ]]; then pass
    else fail "exit $rc; dry-run output: $out; unexpected files or commands in fixture"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: key download failure warns and refuses the sources list and install"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        moonlight_failure=download
        install_moonlight
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    if [[ $rc -ne 0 && "$out" == *"failed to download Moonlight signing key"* \
        && "$calls" == "curl -fsSL -o "* && "$calls" != *sudo* \
        && "$calls" != *apt-update* && ! -f "$moonlight_tmp/moonlight.list" ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: dearmor failure warns and refuses the sources list and install"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        moonlight_failure=dearmor
        install_moonlight
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    if [[ $rc -ne 0 && "$out" == *"failed to install Moonlight signing key"* \
        && "$calls" != *"sudo tee"* && "$calls" != *apt-update* \
        && ! -f "$moonlight_tmp/moonlight.list" ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: direct apt install uses a key file and signed-by, then skips"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        install_moonlight || exit $?
        cp "$moonlight_tmp/calls" "$moonlight_tmp/first-calls"
        install_moonlight
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    list="$(cat "$moonlight_tmp/moonlight.list" 2>/dev/null)"
    if [[ $rc -eq 0 && "$out" == *"already installed"* \
        && "$calls" == *"curl -fsSL -o "* && "$calls" == *"gpg --batch --yes --dearmor -o "* \
        && "$calls" != *setup.deb.sh* && "$list" == "deb [signed-by=$moonlight_tmp/keyring.gpg] "* ]] \
        && cmp -s "$moonlight_tmp/calls" "$moonlight_tmp/first-calls"; then pass
    else fail "exit $rc; output: $out; calls: $calls; list: $list"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: an existing key and sources list are reused without downloading"; then
    moonlight_tmp="$(mktemp -d)"
    printf 'existing key\n' >"$moonlight_tmp/keyring.gpg"
    printf 'existing sources\n' >"$moonlight_tmp/moonlight.list"
    out="$( (
        moonlight_test_stubs
        install_moonlight
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    if [[ $rc -eq 0 && "$calls" != *curl* && "$calls" != *gpg* && "$calls" != *tee* \
        && "$calls" == *"apt-get install -y moonlight-qt"* \
        && "$(cat "$moonlight_tmp/moonlight.list")" == 'existing sources' ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: apt failure falls back to flatpak and the second run skips"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        moonlight_failure=apt moonlight_flatpak=1
        install_moonlight || exit $?
        install_moonlight
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    if [[ $rc -eq 0 && "$out" == *"falling back to flatpak"* && "$out" == *"already installed"* \
        && "$(grep -c '^flatpak install -y flathub com.moonlight_stream.Moonlight$' \
            "$moonlight_tmp/calls")" == 1 \
        && "$(grep -c '^sudo apt-get install' "$moonlight_tmp/calls")" == 1 ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: package dispatch records a flatpak repeat as skipped, not installed"; then
    moonlight_tmp="$(mktemp -d)"
    out="$( (
        moonlight_test_stubs
        moonlight_failure=apt moonlight_flatpak=1
        has_bin() { has_cmd "$1"; }
        install_component script moonlight
        printf 'first=%s\n' "$INSTALL_STATE"
        install_component script moonlight
        printf 'second=%s\n' "$INSTALL_STATE"
    ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *first=installed* && "$out" == *second=skipped* ]]; then pass
    else fail "exit $rc; output: $out"; fi
    rm -rf "$moonlight_tmp"
fi
