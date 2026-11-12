# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "Raspberry Pi and desktop installer modules"

if it "rpi: install.sh loads real extracted functions from a foreign cwd"; then
    moonlight_tmp="$(mktemp -d)"
    out="$(bash -c '
        set -euo pipefail
        cd "$2"
        . "$1/lib/linux/install.sh"
        for fn in install_jetbrains_mono_font install_moonlight install_zsh_plugins \
            setup_kitty_config setup_lsd_aliases setup_bat_alias setup_agy_pi_config \
            setup_rpi_cec_tv setup_rpi_moonlight_console; do
            declare -F "$fn" >/dev/null
        done
        declare -f setup_rpi_cec_tv | grep -F "tv-cec-on.service" >/dev/null
        declare -f install_jetbrains_mono_font | grep -F "JetBrainsMono.tar.xz" >/dev/null
        ui_muted() { printf "%s\n" "$*"; }
        AUTOOS_DRY_RUN=1
        install_moonlight
    ' _ "$ROOT" "$moonlight_tmp" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"would install Moonlight Game Streaming client"* ]]; then pass
    else fail "$out (exit $rc)"; fi
    rm -rf "$moonlight_tmp"
fi

if it "rpi: standalone detection retains the extracted desktop checks"; then
    moonlight_tmp="$(mktemp -d)"
    out="$(bash -c '
        set -euo pipefail
        cd "$2"
        . "$1/lib/linux/detect.sh"
        SYS_HOME="$2"
        has_bin() { [[ "$1" == moonlight-qt ]]; }
        script_is_installed moonlight
        mkdir -p "$SYS_HOME/.local/share/fonts/JetBrainsMonoNerdFont"
        touch "$SYS_HOME/.local/share/fonts/JetBrainsMonoNerdFont/JetBrainsMonoNerdFont-Regular.ttf"
        script_is_installed jetbrains-mono-nerd-font
        has_bin() { return 1; }
        ! script_is_installed moonlight
        ! script_is_installed unknown-desktop-component
    ' _ "$ROOT" "$moonlight_tmp" 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out (exit $rc)"; fi
    rm -rf "$moonlight_tmp"
fi

# All downloads, package managers and privileged commands are stand-ins. Even a
# broken path guard cannot write outside the fixture: the sudo stub refuses it.
moonlight_test_fixture() {
    moonlight_tmp="$(mktemp -d)"
    printf 'ID=%s\nVERSION_CODENAME=%s\n' "${1:-raspbian}" "${2-bookworm}" >"$moonlight_tmp/os-release"
}

moonlight_test_stubs() {
    AUTOOS_DRY_RUN=0
    AUTOOS_SUDO=moonlight_test_sudo
    AUTOOS_MOONLIGHT_KEYRING="$moonlight_tmp/keyring.gpg"
    AUTOOS_MOONLIGHT_SOURCES_LIST="$moonlight_tmp/moonlight.list"
    AUTOOS_MOONLIGHT_OS_RELEASE="$moonlight_tmp/os-release"
    export TMPDIR="$moonlight_tmp"
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
        [[ "$1" == -fsSL && "$2" == -o && "$3" == "$moonlight_tmp/"* && "$4" == */gpgkey ]] || return 2
        printf 'fixture armored key\n' >"$3"
    }
    gpg() {
        [[ "$1 $2 $3 $4" == '--batch --yes --dearmor -o' \
            && "$5" == "$moonlight_tmp/"* && "$6" == "$moonlight_tmp/"* ]] || return 2
        grep -q 'fixture armored key' "$6" || return 2
        printf 'partial binary key\n' >"$5"
        [[ "${moonlight_failure:-}" != dearmor ]] || return 2
        printf 'fixture binary key\n' >"$5"
    }
    wget() { printf 'wget\n' >>"$moonlight_tmp/calls"; return 1; }
    apt_update_once() { printf 'apt-update\n' >>"$moonlight_tmp/calls"; }
    apt-get() {
        [[ "$*" == 'install -y moonlight-qt' && "${moonlight_failure:-}" != apt ]] || return 1
        touch "$moonlight_tmp/native-installed"
    }
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
        local cmd="$1" arg
        shift
        case "$cmd" in
            gpg|apt-get) "$cmd" "$@" ;;
            tee|mktemp|chmod|mv|rm)
                for arg in "$@"; do
                    case "$arg" in
                        -*|644) ;;
                        "$moonlight_tmp/"*) ;;
                        *) return 2 ;;
                    esac
                done
                command "$cmd" "$@"
                ;;
            *) return 2 ;;
        esac
    }
}

if it "moonlight: path overrides require the explicit test flag"; then
    moonlight_test_fixture
    out="$( (
        moonlight_test_stubs
        unset AUTOOS_TEST_MODE
        moonlight_repo_paths
        AUTOOS_TEST_MODE=0 moonlight_repo_paths
        AUTOOS_TEST_MODE=1 moonlight_repo_paths
    ) 2>&1)"; rc=$?
    system_paths=$'/usr/share/keyrings/moonlight-game-streaming-moonlight-qt-archive-keyring.gpg\n/etc/apt/sources.list.d/moonlight-game-streaming-moonlight-qt.list\n/etc/os-release'
    expected="$system_paths"$'\n'"$system_paths"$'\n'"$moonlight_tmp/keyring.gpg"$'\n'"$moonlight_tmp/moonlight.list"$'\n'"$moonlight_tmp/os-release"
    if [[ $rc -eq 0 && "$out" == "$expected" && ! -f "$moonlight_tmp/calls" ]]; then pass
    else fail "exit $rc; resolved paths: $out"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: dry-run prints would install and performs no download or write"; then
    moonlight_test_fixture
    out="$( (
        moonlight_test_stubs
        AUTOOS_DRY_RUN=1
        install_script moonlight
    ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"would install Moonlight Game Streaming client"* \
        && "$(find "$moonlight_tmp" -mindepth 1 -printf '%f\n')" == os-release ]]; then pass
    else fail "exit $rc; dry-run output: $out; unexpected files or commands in fixture"; fi
    rm -rf "$moonlight_tmp"
fi

for moonlight_distro_suite in ubuntu/noble debian/bookworm raspbian/trixie; do
    if it "moonlight: os-release $moonlight_distro_suite selects the matching repository suite"; then
        moonlight_test_fixture "${moonlight_distro_suite%/*}" "${moonlight_distro_suite#*/}"
        out="$( ( moonlight_test_stubs; install_moonlight ) 2>&1)"; rc=$?
        expected="deb [signed-by=$moonlight_tmp/keyring.gpg] https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/deb/${moonlight_distro_suite%/*} ${moonlight_distro_suite#*/} main"
        list="$(cat "$moonlight_tmp/moonlight.list" 2>/dev/null)"
        if [[ $rc -eq 0 && "$list" == "$expected" && -s "$moonlight_tmp/keyring.gpg" ]]; then pass
        else fail "exit $rc; output: $out; list: $list"; fi
        rm -rf "$moonlight_tmp"
    fi
done

for moonlight_distro_suite in unknown/bookworm ubuntu/; do
    if it "moonlight: unsupported suite $moonlight_distro_suite uses flatpak without adding a repository"; then
        moonlight_test_fixture "${moonlight_distro_suite%/*}" "${moonlight_distro_suite#*/}"
        out="$( (
            moonlight_test_stubs
            moonlight_flatpak=1
            install_moonlight
        ) 2>&1)"; rc=$?
        calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
        if [[ $rc -eq 0 && "$out" == *"falling back to flatpak"* && "$calls" == *"flatpak install"* \
            && "$calls" != *curl* && "$calls" != *sudo* && "$calls" != *apt-update* \
            && ! -e "$moonlight_tmp/moonlight.list" && ! -e "$moonlight_tmp/keyring.gpg" ]]; then pass
        else fail "exit $rc; output: $out; calls: $calls"; fi
        rm -rf "$moonlight_tmp"
    fi
done

if it "moonlight: unsupported distro without flatpak warns and fails without writes"; then
    moonlight_test_fixture unknown bookworm
    out="$( ( moonlight_test_stubs; install_moonlight ) 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"flatpak is unavailable"* && ! -f "$moonlight_tmp/calls" \
        && ! -e "$moonlight_tmp/moonlight.list" && ! -e "$moonlight_tmp/keyring.gpg" ]]; then pass
    else fail "exit $rc; output: $out; unexpected writes or package commands"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: key download failure warns and refuses the sources list and install"; then
    moonlight_test_fixture
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

if it "moonlight: failed dearmor removes partial output and a second run downloads again"; then
    moonlight_test_fixture
    out="$( (
        moonlight_test_stubs
        moonlight_failure=dearmor
        install_moonlight && exit 3
        [[ ! -e "$AUTOOS_MOONLIGHT_KEYRING" ]] || exit 4
        [[ -z "$(find "$moonlight_tmp" -name 'keyring.gpg*' -print -quit)" ]] || exit 5
        unset moonlight_failure
        install_moonlight
    ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"failed to install Moonlight signing key"* \
        && "$(grep -c '^curl ' "$moonlight_tmp/calls")" == 2 \
        && "$(cat "$moonlight_tmp/keyring.gpg" 2>/dev/null)" == 'fixture binary key' ]]; then pass
    else fail "exit $rc; output: $out; calls: $(cat "$moonlight_tmp/calls" 2>/dev/null)"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: missing key never overwrites an existing user sources list"; then
    moonlight_test_fixture ubuntu noble
    printf '# User sources\r\ndeb https://packages.example.invalid/custom noble main\r\n# Keep this' >"$moonlight_tmp/moonlight.list"
    cp "$moonlight_tmp/moonlight.list" "$moonlight_tmp/original.list"
    out="$( ( moonlight_test_stubs; install_moonlight ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"without our signed-by entry"* && -s "$moonlight_tmp/keyring.gpg" ]] \
        && cmp -s "$moonlight_tmp/moonlight.list" "$moonlight_tmp/original.list"; then pass
    else fail "exit $rc; output: $out; existing sources were changed or key was not installed"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: existing signed-by sources remain unchanged when their key is repaired"; then
    moonlight_test_fixture
    printf 'deb [signed-by=%s/keyring.gpg] https://packages.example.invalid/custom bookworm main\n# custom\n' \
        "$moonlight_tmp" >"$moonlight_tmp/moonlight.list"
    cp "$moonlight_tmp/moonlight.list" "$moonlight_tmp/original.list"
    out="$( ( moonlight_test_stubs; install_moonlight ) 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" != *"without our signed-by entry"* && -s "$moonlight_tmp/keyring.gpg" ]] \
        && cmp -s "$moonlight_tmp/moonlight.list" "$moonlight_tmp/original.list"; then pass
    else fail "exit $rc; output: $out; existing signed-by sources changed"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: direct apt install uses signed-by and the second full run is skipped and unchanged"; then
    moonlight_test_fixture
    out="$( (
        moonlight_test_stubs
        has_bin() { has_cmd "$1"; }
        install_component script moonlight
        [[ "$INSTALL_STATE" == installed ]] || exit 3
        before="$(find "$moonlight_tmp" -type f -exec sha256sum '{}' + | sort)"
        install_component script moonlight
        [[ "$INSTALL_STATE" == skipped ]] || exit 4
        [[ "$(find "$moonlight_tmp" -type f -exec sha256sum '{}' + | sort)" == "$before" ]] || exit 5
    ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    list="$(cat "$moonlight_tmp/moonlight.list" 2>/dev/null)"
    if [[ $rc -eq 0 && "$out" == *"already installed"* && "$calls" == *"curl -fsSL -o "* \
        && "$calls" == *"gpg --batch --yes --dearmor -o "* && "$calls" == *"sudo mv "* \
        && "$calls" != *setup.deb.sh* && "$list" == "deb [signed-by=$moonlight_tmp/keyring.gpg] "* ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls; list: $list"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: an existing key and sources list are reused without downloading"; then
    moonlight_test_fixture
    printf 'existing key\n' >"$moonlight_tmp/keyring.gpg"
    printf 'existing sources\n' >"$moonlight_tmp/moonlight.list"
    out="$( ( moonlight_test_stubs; install_moonlight ) 2>&1)"; rc=$?
    calls="$(cat "$moonlight_tmp/calls" 2>/dev/null)"
    if [[ $rc -eq 0 && "$calls" != *curl* && "$calls" != *gpg* && "$calls" != *tee* \
        && "$calls" == *"apt-get install -y moonlight-qt"* \
        && "$(cat "$moonlight_tmp/moonlight.list")" == 'existing sources' ]]; then pass
    else fail "exit $rc; output: $out; calls: $calls"; fi
    rm -rf "$moonlight_tmp"
fi

if it "moonlight: apt failure falls back to flatpak and the second run skips"; then
    moonlight_test_fixture
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
    moonlight_test_fixture
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
