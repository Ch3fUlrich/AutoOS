#!/usr/bin/env bash
# AutoOS machine detection for Linux.
#
# Pure inspection: nothing here installs, downloads or writes. Results land in
# SYS_* globals so the selection stage can be tested against captured fixtures
# rather than the live machine.
#
# shellcheck shell=bash

has_cmd() { command -v "$1" >/dev/null 2>&1; }

detect_system() {
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
    if grep -qi microsoft /proc/version 2>/dev/null; then SYS_IS_WSL=1; fi
    if [[ -f /.dockerenv ]] || grep -qE '(docker|lxc|containerd)' /proc/1/cgroup 2>/dev/null; then
        SYS_IS_CONTAINER=1
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
            "Running under WSL2." \
            "systemd services and desktop packages may not behave as they would on bare metal."
    fi
}
