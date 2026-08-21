#!/usr/bin/env bash
# AutoOS machine detection for Linux.
#
# Pure inspection: nothing here installs, downloads or writes. Results land in
# SYS_* globals so the selection stage can be tested against captured fixtures
# rather than the live machine.
#
# shellcheck shell=bash
# shellcheck disable=SC2034
#   The SYS_*/CAT_*/MENU_*/*_STATE globals below are this module's public
#   interface - they are read by setup.sh, serve.py's probe and the tests,
#   none of which shellcheck can see from here.


has_cmd() { command -v "$1" >/dev/null 2>&1; }

# macOS keeps none of the /proc and /etc/os-release furniture the Linux path
# reads, so it gets its own probe rather than a pile of conditionals.
detect_macos() {
    SYS_OS="macos"
    SYS_DISTRO_ID="macos"
    SYS_DISTRO_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
    SYS_DISTRO_NAME="macOS ${SYS_DISTRO_VERSION}"
    SYS_DISTRO_CODENAME=""; SYS_DISTRO_LIKE="darwin"
    SYS_IS_DEBIAN_LIKE=0

    case "$(uname -m)" in
        x86_64) SYS_ARCH="x64" ;;
        arm64)  SYS_ARCH="arm64" ;;
        *)      SYS_ARCH="$(uname -m)" ;;
    esac

    SYS_MODEL="$(sysctl -n hw.model 2>/dev/null || echo unknown)"
    SYS_IS_PI=0; SYS_IS_WSL=0; SYS_IS_CONTAINER=0
    SYS_WSL_VERSION=""; SYS_WSL_DISTRO=""; SYS_ENVIRONMENT="macOS"
    SYS_CPU_CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo 1)"
    SYS_CPU_NAME="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
    local mem_bytes
    mem_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    SYS_RAM_GB="$(awk -v b="$mem_bytes" 'BEGIN{printf "%.1f", b/1073741824}')"
    SYS_FREE_DISK_GB="$(df -g / 2>/dev/null | awk 'NR==2{print $4}' || echo 0)"
    if [[ -z "$SYS_FREE_DISK_GB" ]]; then SYS_FREE_DISK_GB=0; fi

    SYS_USER="${SUDO_USER:-${USER:-$(id -un)}}"
    SYS_HOME="$(eval echo "~$SYS_USER" 2>/dev/null || echo "$HOME")"
    if [[ ! -d "$SYS_HOME" ]]; then SYS_HOME="$HOME"; fi
    SYS_IS_ROOT=0; if [[ "$(id -u)" -eq 0 ]]; then SYS_IS_ROOT=1; fi
    if (( SYS_IS_ROOT )); then AUTOOS_SUDO=""; SYS_CAN_SUDO=1
    elif has_cmd sudo; then AUTOOS_SUDO="sudo"
        if sudo -n true 2>/dev/null; then SYS_CAN_SUDO=1; else SYS_CAN_SUDO=2; fi
    else AUTOOS_SUDO=""; SYS_CAN_SUDO=0; fi
    export AUTOOS_SUDO

    # A Mac reached over SSH has no Aqua session; launchctl is the reliable probe.
    SYS_IS_HEADLESS=1
    if launchctl print gui/"$(id -u)" >/dev/null 2>&1; then SYS_IS_HEADLESS=0; fi
    if [[ -n "${SSH_CLIENT:-}${SSH_TTY:-}" ]]; then SYS_IS_HEADLESS=1; fi

    SYS_HAS_APT=0; SYS_HAS_SNAP=0
    SYS_HAS_BREW=0;   if has_cmd brew;   then SYS_HAS_BREW=1;   fi
    SYS_HAS_GIT=0;    if has_cmd git;    then SYS_HAS_GIT=1;    fi
    SYS_HAS_NODE=0;   if has_cmd node;   then SYS_HAS_NODE=1;   fi
    SYS_HAS_NPM=0;    if has_cmd npm;    then SYS_HAS_NPM=1;    fi
    SYS_HAS_DOCKER=0; if has_cmd docker; then SYS_HAS_DOCKER=1; fi
    SYS_HAS_ZSH=0;    if has_cmd zsh;    then SYS_HAS_ZSH=1;    fi
    SYS_NODE_VERSION=""
    if (( SYS_HAS_NODE )); then SYS_NODE_VERSION="$(node --version 2>/dev/null || true)"; fi
}

detect_system() {
    SYS_OS="linux"
    SYS_HAS_BREW=0
    if [[ "$(uname -s)" == "Darwin" ]]; then
        detect_macos
        return 0
    fi

    # ─── Distribution ───────────────────────────────────────────────────────
    SYS_DISTRO_ID="unknown"; SYS_DISTRO_NAME="unknown"
    SYS_DISTRO_VERSION=""; SYS_DISTRO_CODENAME=""; SYS_DISTRO_LIKE=""
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        SYS_DISTRO_ID="${ID:-unknown}"
        SYS_DISTRO_NAME="${PRETTY_NAME:-${NAME:-unknown}}"
        SYS_DISTRO_VERSION="${VERSION_ID:-}"
        SYS_DISTRO_CODENAME="${VERSION_CODENAME:-}"
        SYS_DISTRO_LIKE="${ID_LIKE:-}"
    fi
    # Everything in the catalog assumes apt; say so plainly rather than failing later.
    if [[ "$SYS_DISTRO_ID" == "debian" || "$SYS_DISTRO_ID" == "ubuntu" \
       || "$SYS_DISTRO_LIKE" == *debian* ]]; then
        SYS_IS_DEBIAN_LIKE=1
    else
        SYS_IS_DEBIAN_LIKE=0
    fi

    # ─── Architecture ───────────────────────────────────────────────────────
    case "$(uname -m)" in
        x86_64|amd64)   SYS_ARCH="x64" ;;
        aarch64|arm64)  SYS_ARCH="arm64" ;;
        armv7l|armhf)   SYS_ARCH="armhf" ;;
        *)              SYS_ARCH="$(uname -m)" ;;
    esac

    # ─── Board / virtualisation ─────────────────────────────────────────────
    SYS_MODEL="unknown"; SYS_IS_PI=0; SYS_IS_WSL=0; SYS_IS_CONTAINER=0
    if [[ -r /proc/device-tree/model ]]; then
        SYS_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
    elif [[ -r /sys/devices/virtual/dmi/id/product_name ]]; then
        SYS_MODEL="$(cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null || echo unknown)"
    fi
    if [[ "$SYS_MODEL" == *"Raspberry Pi"* ]]; then SYS_IS_PI=1; fi
    # WSL: three independent signals, because none is reliable alone. The env
    # var is absent in a service or a bare `wsl -u root` shell, /proc/version is
    # rewritten by some kernels, and osrelease differs between WSL1 and WSL2.
    SYS_WSL_VERSION=""; SYS_WSL_DISTRO=""
    if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        SYS_IS_WSL=1; SYS_WSL_DISTRO="$WSL_DISTRO_NAME"
    elif grep -qi microsoft /proc/version 2>/dev/null; then
        SYS_IS_WSL=1
    elif grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
        SYS_IS_WSL=1
    fi
    if (( SYS_IS_WSL )); then
        # WSL2 ships a real Linux kernel tagged microsoft-standard-WSL2 and has
        # /run/WSL; WSL1 is a syscall translation layer on an NT-era version string.
        if grep -qiE 'wsl2|microsoft-standard' /proc/sys/kernel/osrelease 2>/dev/null \
           || grep -qi 'WSL2' /proc/version 2>/dev/null \
           || [[ -d /run/WSL ]]; then
            SYS_WSL_VERSION=2
        else
            SYS_WSL_VERSION=1
        fi
        if [[ -z "$SYS_WSL_DISTRO" && -r /etc/wsl.conf ]]; then
            SYS_WSL_DISTRO="$(awk -F= '/^[[:space:]]*hostname/{gsub(/ /,"",$2); print $2}' /etc/wsl.conf 2>/dev/null)"
        fi
    fi
    if [[ -f /.dockerenv ]] || grep -qE '(docker|lxc|containerd)' /proc/1/cgroup 2>/dev/null; then
        SYS_IS_CONTAINER=1
    fi

    # One human-readable summary the UI and the terminal can both print.
    if (( SYS_IS_WSL )); then
        SYS_ENVIRONMENT="WSL${SYS_WSL_VERSION} on Windows"
        if [[ -n "$SYS_WSL_DISTRO" ]]; then
            SYS_ENVIRONMENT="$SYS_ENVIRONMENT (${SYS_WSL_DISTRO})"
        fi
    elif (( SYS_IS_CONTAINER )); then SYS_ENVIRONMENT="container"
    elif (( SYS_IS_PI )); then        SYS_ENVIRONMENT="Raspberry Pi"
    else                              SYS_ENVIRONMENT="bare metal or VM"
    fi

    # ─── Hardware ───────────────────────────────────────────────────────────
    SYS_CPU_CORES="$(nproc 2>/dev/null || echo 1)"
    SYS_CPU_NAME="$(awk -F': ' '/^model name|^Model/{print $2; exit}' /proc/cpuinfo 2>/dev/null || echo unknown)"
    if [[ -z "$SYS_CPU_NAME" ]]; then SYS_CPU_NAME="unknown"; fi
    local mem_kb
    mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    SYS_RAM_GB="$(awk -v k="$mem_kb" 'BEGIN{printf "%.1f", k/1048576}')"
    SYS_FREE_DISK_GB="$(df -BG / 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}' || echo 0)"
    if [[ -z "$SYS_FREE_DISK_GB" ]]; then SYS_FREE_DISK_GB=0; fi

    # ─── Session ────────────────────────────────────────────────────────────
    SYS_USER="${SUDO_USER:-${USER:-$(id -un)}}"
    SYS_HOME="$(getent passwd "$SYS_USER" 2>/dev/null | cut -d: -f6)"
    if [[ -z "$SYS_HOME" ]]; then SYS_HOME="$HOME"; fi
    SYS_IS_ROOT=0; if [[ "$(id -u)" -eq 0 ]]; then SYS_IS_ROOT=1; fi

    # One sudo decision, made once. Functions must never call sudo directly:
    # this keeps the scripts identical as root, under sudo, and in a container.
    if (( SYS_IS_ROOT )); then
        AUTOOS_SUDO=""
        SYS_CAN_SUDO=1
    elif has_cmd sudo; then
        AUTOOS_SUDO="sudo"
        if sudo -n true 2>/dev/null; then SYS_CAN_SUDO=1; else SYS_CAN_SUDO=2; fi   # 2 = will prompt
    else
        AUTOOS_SUDO=""
        SYS_CAN_SUDO=0
    fi
    export AUTOOS_SUDO

    SYS_IS_HEADLESS=1
    if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then SYS_IS_HEADLESS=0; fi

    # ─── Package managers and runtimes ──────────────────────────────────────
    # Written as if-blocks, not `has_cmd x && VAR=1`: under `set -e` that idiom
    # aborts the whole script the moment a command is absent.
    SYS_HAS_APT=0;    if has_cmd apt-get; then SYS_HAS_APT=1;    fi
    SYS_HAS_SNAP=0;   if has_cmd snap;    then SYS_HAS_SNAP=1;   fi
    SYS_HAS_GIT=0;    if has_cmd git;     then SYS_HAS_GIT=1;    fi
    SYS_HAS_NODE=0;   if has_cmd node;    then SYS_HAS_NODE=1;   fi
    SYS_HAS_NPM=0;    if has_cmd npm;     then SYS_HAS_NPM=1;    fi
    SYS_HAS_DOCKER=0; if has_cmd docker;  then SYS_HAS_DOCKER=1; fi
    SYS_HAS_ZSH=0;    if has_cmd zsh;     then SYS_HAS_ZSH=1;    fi
    SYS_NODE_VERSION=""
    if (( SYS_HAS_NODE )); then SYS_NODE_VERSION="$(node --version 2>/dev/null || true)"; fi
}

suggested_profile() {
    # Only ever a default; the user confirms it. Deliberately conservative so a
    # small board is never handed a desktop install it cannot use.
    #
    # macOS has no `server` profile, so a headless Mac gets ai-coding instead of
    # a profile name its catalog does not define.
    if [[ "${SYS_OS:-linux}" == "macos" ]]; then
        if awk -v r="$SYS_RAM_GB" -v c="$SYS_CPU_CORES" 'BEGIN{exit !(r >= 15 && c >= 8)}'            && (( ! SYS_IS_HEADLESS )); then echo "workstation"; else echo "ai-coding"; fi
        return
    fi
    if (( SYS_IS_PI )); then echo "light"; return; fi
    if (( SYS_IS_CONTAINER )); then echo "server"; return; fi
    # 4 GB or less is small-board territory; 8 GB reports ~7.7 so the cut sits below it.
    if awk -v r="$SYS_RAM_GB" 'BEGIN{exit !(r > 0 && r <= 5)}'; then echo "light"; return; fi
    if (( SYS_IS_HEADLESS )); then echo "server"; return; fi
    if awk -v r="$SYS_RAM_GB" -v c="$SYS_CPU_CORES" 'BEGIN{exit !(r >= 15 && c >= 8)}'; then
        echo "workstation"; return
    fi
    echo "ai-coding"
}

# Populates BLOCKER_SEVERITY / BLOCKER_MESSAGE / BLOCKER_FIX arrays.
declare -a BLOCKER_SEVERITY BLOCKER_MESSAGE BLOCKER_FIX
detect_blockers() {
    BLOCKER_SEVERITY=(); BLOCKER_MESSAGE=(); BLOCKER_FIX=()
    _blocker() { BLOCKER_SEVERITY+=("$1"); BLOCKER_MESSAGE+=("$2"); BLOCKER_FIX+=("$3"); }

    if (( ! SYS_HAS_APT )); then
        _blocker error \
            "No apt-get on this system (detected: ${SYS_DISTRO_NAME})." \
            "AutoOS's Linux catalog targets Debian and Ubuntu. Port the catalog or use --only with script providers."
    fi
    if (( SYS_CAN_SUDO == 0 )); then
        _blocker error \
            "Not root and sudo is unavailable." \
            "Re-run as root, or install sudo and add ${SYS_USER} to the sudo group."
    fi
    if [[ "$SYS_FREE_DISK_GB" =~ ^[0-9]+$ ]] && (( SYS_FREE_DISK_GB < 5 )); then
        _blocker warn \
            "Only ${SYS_FREE_DISK_GB} GB free on /." \
            "Free space before selecting Docker or large toolchains."
    fi
    if (( SYS_IS_WSL )); then
        _blocker warn \
            "Running under ${SYS_ENVIRONMENT}." \
            "systemd services and desktop packages may not behave as they would on bare metal."
    fi
}

# ─── Where a component landed ───────────────────────────────────────────────
# "Installed 1" answers nothing on its own: most desktop packages put no command
# on PATH, and the reasonable next question is where it went and how to open it.
# Resolved from the live machine - an empty answer is honest, a guessed path is
# not. Sets LAUNCH_PATH and LAUNCH_HOW, both empty when nothing is found.
LAUNCH_PATH=""
LAUNCH_HOW=""

_launch_normalise() { printf '%s' "$1" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]'; }

_launch_from_desktop() {
    local wanted="$1" dir file name exec_line
    wanted="$(_launch_normalise "$wanted")"
    [[ -z "$wanted" ]] && return 1
    for dir in "$HOME/.local/share/applications" /usr/local/share/applications                /usr/share/applications /var/lib/snapd/desktop/applications                /var/lib/flatpak/exports/share/applications                "$HOME/.local/share/flatpak/exports/share/applications"; do
        [[ -d "$dir" ]] || continue
        while IFS= read -r file; do
            [[ -n "$file" ]] || continue
            name="$(grep -m1 '^Name=' "$file" 2>/dev/null | cut -d= -f2-)"
            [[ -n "$name" ]] || name="$(basename "$file" .desktop)"
            if [[ "$(_launch_normalise "$name")" == "$wanted" ]]; then
                # Exec carries %U/%F placeholders the launcher fills in; the
                # first field is the actual program.
                exec_line="$(grep -m1 '^Exec=' "$file" 2>/dev/null | cut -d= -f2- | awk '{print $1}')"
                # Exec is often a bare command name; report where it actually is.
                if [[ -n "$exec_line" && "$exec_line" != /* ]]; then
                    exec_line="$(command -v "$exec_line" 2>/dev/null || printf '%s' "$exec_line")"
                fi
                LAUNCH_PATH="${exec_line:-$file}"
                LAUNCH_HOW="Applications:  ${name}"
                return 0
            fi
        done < <(find "$dir" -maxdepth 2 -name '*.desktop' 2>/dev/null)
    done
    return 1
}

_launch_from_app_bundle() {
    local wanted="$1" dir app
    for dir in /Applications "$HOME/Applications"; do
        [[ -d "$dir" ]] || continue
        while IFS= read -r app; do
            [[ -n "$app" ]] || continue
            if [[ "$(_launch_normalise "$(basename "$app" .app)")" == "$(_launch_normalise "$wanted")" ]]; then
                LAUNCH_PATH="$app"
                LAUNCH_HOW="run  open -a '$(basename "$app" .app)'"
                return 0
            fi
        done < <(find "$dir" -maxdepth 2 -name '*.app' 2>/dev/null)
    done
    return 1
}

launch_hint() {
    local name="$1" id="$2" verify="${3:-}" exe resolved
    LAUNCH_PATH=""; LAUNCH_HOW=""

    # A verify command names the executable, which is the most precise handle
    # there is: command -v resolves it to the exact file that will run.
    if [[ -n "$verify" ]]; then
        exe="${verify%% *}"
        if resolved="$(command -v "$exe" 2>/dev/null)"; then
            LAUNCH_PATH="$resolved"; LAUNCH_HOW="run  $exe"
            return 0
        fi
    fi

    if [[ "$(uname -s)" == "Darwin" ]]; then
        _launch_from_app_bundle "$name" && return 0
    else
        _launch_from_desktop "$name" && return 0
    fi

    # Last resort: something whose id happens to be its command.
    if resolved="$(command -v "$id" 2>/dev/null)"; then
        LAUNCH_PATH="$resolved"; LAUNCH_HOW="run  $id"
        return 0
    fi
    return 1
}
