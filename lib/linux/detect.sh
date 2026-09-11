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

# ─── Where commands hide when they are not on PATH ──────────────────────────
# `command -v` only ever answers for the PATH of the shell asking. That PATH is
# wrong more often than not here: ~/.local/bin is added by a login profile this
# script has not sourced, /snap/bin is missing under sudo, and flatpak exports
# its binaries somewhere no non-desktop session ever looks. Every directory
# below is a real place a package manager puts a command.
_extra_bin_dirs() {
    local home_="${SYS_HOME:-$HOME}"
    printf '%s\n' \
        "$home_/.local/bin" \
        "$home_/bin" \
        "$home_/.cargo/bin" \
        "$home_/.local/share/flatpak/exports/bin" \
        /usr/local/bin \
        /usr/bin \
        /usr/sbin \
        /snap/bin \
        /var/lib/flatpak/exports/bin
}

# Installed, or not here at all. Never "probably": the file has to exist and be
# executable, which is why a leftover configuration directory cannot fake it.
has_bin() {
    local name="$1" dir
    # if-blocks, not `has_cmd x && return 0`: under `set -e` a failing AND-list
    # at the tail of a function is what aborts the caller's whole script.
    if has_cmd "$name"; then return 0; fi
    while IFS= read -r dir; do
        if [[ -n "$dir" && -f "$dir/$name" && -x "$dir/$name" ]]; then return 0; fi
    done < <(_extra_bin_dirs)
    return 1
}

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
    SYS_HOME="$(dscl . -read "/Users/$SYS_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
    if [[ -z "$SYS_HOME" || ! -d "$SYS_HOME" ]]; then SYS_HOME="$HOME"; fi
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
    SYS_HOME="$(getent passwd "$SYS_USER" 2>/dev/null | cut -d: -f6 || true)"
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

declare -A _LAUNCH_DESKTOP_CACHE=()
_LAUNCH_DESKTOP_INITIALIZED=0

_init_desktop_cache() {
    (( _LAUNCH_DESKTOP_INITIALIZED )) && return 0
    _LAUNCH_DESKTOP_INITIALIZED=1
    has_cmd python3 || return 0
    local norm name path
    while IFS=$'\x1f' read -r norm name path; do
        [[ -n "$norm" ]] && _LAUNCH_DESKTOP_CACHE["$norm"]="${name}"$'\x1f'"${path}"
    done < <(python3 - "$HOME" <<'PY'
import os, sys, shutil

home = sys.argv[1]
dirs = [
    os.path.join(home, ".local/share/applications"),
    "/usr/local/share/applications",
    "/usr/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.join(home, ".local/share/flatpak/exports/share/applications")
]

seen = set()
for d in dirs:
    if not os.path.isdir(d):
        continue
    for root, _, files in os.walk(d):
        for f in files:
            if not f.endswith(".desktop"):
                continue
            path = os.path.join(root, f)
            name, exec_cmd = "", ""
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if not name and line.startswith("Name="):
                            name = line[5:].strip()
                        elif not exec_cmd and line.startswith("Exec="):
                            exec_cmd = line[5:].strip()
                        if name and exec_cmd:
                            break
            except Exception:
                continue
            if not name:
                name = f[:-8]
            norm = "".join(c.lower() for c in name if c.isalnum())
            if norm and norm not in seen:
                seen.add(norm)
                prog = exec_cmd.split()[0] if exec_cmd else ""
                resolved = shutil.which(prog) if (prog and not os.path.isabs(prog)) else prog
                print(f"{norm}\x1f{name}\x1f{resolved or path}")
PY
    )
}

_launch_from_desktop() {
    local wanted="$1" entry
    wanted="$(_launch_normalise "$wanted")"
    [[ -z "$wanted" ]] && return 1
    _init_desktop_cache
    if [[ -v _LAUNCH_DESKTOP_CACHE["$wanted"] ]]; then
        entry="${_LAUNCH_DESKTOP_CACHE["$wanted"]}"
        LAUNCH_HOW="Applications:  ${entry%%$'\x1f'*}"
        LAUNCH_PATH="${entry#*$'\x1f'}"
        return 0
    fi
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

    if [[ "$id" == "bitwarden-chrome" ]]; then
        LAUNCH_PATH="chrome://extensions"; LAUNCH_HOW="Chrome:  Bitwarden extension"
        return 0
    fi

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

script_is_installed() {
    # has_bin, not has_cmd: every one of these installs somewhere PATH does not
    # necessarily reach - ~/.local/bin, /snap/bin or a flatpak export - and a
    # missed detection here reinstalls software the user already has.
    case "$1" in
        oh-my-zsh)       [[ -d "$SYS_HOME/.oh-my-zsh" ]] ;;
        zsh-plugins)     [[ -d "$SYS_HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]] ;;
        powerlevel10k)   [[ -d "$SYS_HOME/.oh-my-zsh/custom/themes/powerlevel10k" ]] ;;
        meslo-nerd-font) [[ -f "$SYS_HOME/.local/share/fonts/MesloLGS NF Regular.ttf" ]] ;;
        nodesource-lts)  has_bin node ;;
        docker)          has_bin docker ;;
        tailscale)       has_bin tailscale ;;
        antigravity)     has_bin antigravity ;;
        xpipe)           has_bin xpipe ;;
        herdr)           has_bin herdr ;;
        handy)           has_bin handy ;;
        vscode)          has_bin code ;;
        agy)             has_bin agy ;;
        gh)              has_bin gh ;;
        uv)              has_bin uv ;;
        ollama)          has_bin ollama ;;
        claude-autostart) [[ -f "$SYS_HOME/.config/systemd/user/claude-sessions-restore.service" ]] ;;
        google-chrome)   has_bin google-chrome || has_bin google-chrome-stable ;;
        bitwarden-chrome) [[ -f /opt/google/chrome/extensions/nngceckbapebfimnlniiiahkandclblb.json ]] || \
                         [[ -f /usr/share/google-chrome/extensions/nngceckbapebfimnlniiiahkandclblb.json ]] || \
                         [[ -f /etc/opt/chrome/policies/managed/bitwarden.json ]] ;;
        *)               return 1 ;;
    esac
}

# A bounded package-manager query shared by the menu and execution.
detect_installed_status() {
    local provider="$1" package="$2" cask="${3:-0}"
    local SYS_HOME="${SYS_HOME:-$HOME}"
    INSTALLED_STATUS="unknown"
    if [[ "$provider" == custom ]] && declare -F custom_is_installed >/dev/null; then
        if custom_is_installed "$package"; then INSTALLED_STATUS=installed; else INSTALLED_STATUS=not-detected; fi
        return 0
    fi
    if [[ "$provider" == script ]]; then
        if script_is_installed "$package"; then INSTALLED_STATUS=installed; else INSTALLED_STATUS=not-detected; fi
        return 0
    fi
    INSTALLED_STATUS="$(python3 - "$provider" "$package" "$cask" <<'PY'
import json,shutil,subprocess,sys
provider,package,cask=sys.argv[1:]
def probe(args):
    if not shutil.which(args[0]): return None
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=8)
try:
    installed=False; known=True
    if provider=='apt':
        r=probe(['dpkg-query','-W','-f=${db:Status-Status}\n',*package.split()])
        installed=bool(r and r.returncode==0 and r.stdout.splitlines() and all(x=='installed' for x in r.stdout.splitlines()))
    elif provider=='snap':
        r=probe(['snap','list',package]); installed=bool(r and r.returncode==0)
    elif provider=='brew':
        r=probe(['brew','list','--versions',*(['--cask'] if cask=='1' else ['--formula']),*package.split()]); installed=bool(r and r.returncode==0 and len(r.stdout.strip().splitlines())==len(package.split()))
    elif provider=='npm':
        r=probe(['npm','ls','-g','--depth=0','--json']); installed=bool(r and package in json.loads(r.stdout).get('dependencies',{}))
    else: known=False
    print('installed' if installed else 'not-detected' if known else 'unknown')
except (subprocess.TimeoutExpired,OSError,ValueError): print('unknown')
PY
)"
}
