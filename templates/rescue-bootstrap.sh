#!/usr/bin/env bash
# AutoOS rescue-USB bootstrap.
#
# Runs inside an Ubuntu 26.04.1 LTS live session booted from the rescue stick
# (it also works on an already-installed machine). It installs the
# "Disaster Recovery" toolkit from catalog/linux.json's `rescue` category,
# plus both AI CLIs and a small dispatcher for them.
#
# This replaces an older pair of scripts that had five known defects, and
# every design choice below exists to fix one of them:
#
#   A12  One giant `apt-get install` under `set -e`: a single unavailable
#        package name aborted the whole bootstrap, discarding ~49 successful
#        installs. Fixed by installing in small, named groups, and by
#        retrying a failed group one package at a time so a single bad name
#        only ever costs that one package.
#   A13  Not idempotent: a second run re-added the NodeSource apt repository
#        and re-ran `npm install -g` unconditionally. Fixed by using Ubuntu's
#        own `nodejs`/`npm` packages (apt is already idempotent — no repo file
#        to re-add) and by having install_ai_clis() check `command -v` before
#        ever calling npm.
#   A14  `curl | bash` run unverified, and on a stick booted with no network
#        `set -e` killed the run partway through. Fixed by never piping a
#        remote script into a shell here, and by gating every network step
#        behind network_reachable() so an offline stick still gets whatever
#        is on the ISO's local apt cache and reports a clear summary for the
#        rest, instead of dying.
#   A1/2 A committed plaintext credential and a malformed SHA-512 hash. Not this
#        script's concern directly (see templates/user-data.example and
#        templates/preseed.cfg.example), but it never writes a credential
#        either — /etc/profile.d/autoos-ai.sh carries only *commented*
#        `# export ...API_KEY=` lines, never a value.
#   A3   Automatic whole-disk wipe with no prompt. Not this script's concern
#        (it does not touch storage at all) — see the answer-file templates.
#
# Safe to run twice (AGENTS.md §4): every already-installed package or file
# is reported "skipped", never re-installed or re-created from scratch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="/var/lib/autoos/rescue-bootstrap.done"

# ─── tiny, self-contained UI (no dependency on lib/linux/ui.sh: this script
# must work even when only templates/ was copied onto the stick) ───────────
COUNT_INSTALLED=0
COUNT_SKIPPED=0
MISSING=()

ui_line() {
    local status="$1" msg="$2"
    case "$status" in
        installed) COUNT_INSTALLED=$((COUNT_INSTALLED + 1)) ;;
        skipped)   COUNT_SKIPPED=$((COUNT_SKIPPED + 1)) ;;
    esac
    printf '  %-9s %s\n' "$status" "$msg"
}
ui_warn()  { printf '  warning   %s\n' "$1" >&2; }
ui_info()  { printf '  info      %s\n' "$1"; }

# ─── root / sudo (AGENTS.md: no bare `sudo` inside a function — resolve it
# once at startup, empty when already root) ─────────────────────────────────
AUTOOS_SUDO=""
require_root() {
    [[ "$(id -u)" == "0" ]] && return 0
    if command -v sudo >/dev/null 2>&1; then
        AUTOOS_SUDO="sudo"
        return 0
    fi
    printf 'rescue-bootstrap.sh must run as root (or with sudo available)\n' >&2
    exit 1
}

# ─── offline guard: every network-touching step calls this first ──────────
NETWORK_OK=""
network_reachable() {
    if [[ -z "$NETWORK_OK" ]]; then
        if command -v curl >/dev/null 2>&1 \
            && curl -fsS --max-time 3 --head https://archive.ubuntu.com >/dev/null 2>&1; then
            NETWORK_OK="yes"
        else
            NETWORK_OK="no"
        fi
    fi
    [[ "$NETWORK_OK" == "yes" ]]
}

# ─── apt install in small, named groups (A12) ──────────────────────────────
pkg_installed() { dpkg -s "$1" >/dev/null 2>&1; }

APT_UPDATED=0
apt_update_once() {
    (( APT_UPDATED )) && return 0
    APT_UPDATED=1
    ui_info "refreshing apt package lists"
    # shellcheck disable=SC2086  # AUTOOS_SUDO is intentionally unquoted: empty, or the single word "sudo"
    if ! $AUTOOS_SUDO apt-get update -y >/tmp/autoos-rescue-apt-update.log 2>&1; then
        ui_warn "apt-get update failed — installs below may fail too (see /tmp/autoos-rescue-apt-update.log)"
    fi
}

install_apt_group() {
    local group_name="$1"; shift
    local pkgs=("$@")
    local pkg to_install=()

    for pkg in "${pkgs[@]}"; do
        if pkg_installed "$pkg"; then
            ui_line "skipped" "$pkg (already installed)"
        else
            to_install+=("$pkg")
        fi
    done
    (( ${#to_install[@]} == 0 )) && return 0

    if ! network_reachable; then
        for pkg in "${to_install[@]}"; do
            ui_warn "$pkg — offline, cannot install ($group_name)"
            MISSING+=("$pkg")
        done
        return 0
    fi

    apt_update_once

    # shellcheck disable=SC2086
    if $AUTOOS_SUDO apt-get install -y --no-install-recommends "${to_install[@]}" \
        >/tmp/autoos-rescue-apt.log 2>&1; then
        for pkg in "${to_install[@]}"; do ui_line "installed" "$pkg"; done
        return 0
    fi

    # The group failed together (this is exactly A12): retry its packages one
    # at a time so a single bad/unavailable name never drags its neighbours
    # down with it, and so the final summary names precisely what failed.
    ui_warn "group '$group_name' failed together — retrying its packages one at a time"
    for pkg in "${to_install[@]}"; do
        # shellcheck disable=SC2086
        if $AUTOOS_SUDO apt-get install -y --no-install-recommends "$pkg" \
            >/tmp/autoos-rescue-apt.log 2>&1; then
            ui_line "installed" "$pkg"
        else
            ui_warn "$pkg — install failed"
            MISSING+=("$pkg")
        fi
    done
}

# The exact "Disaster Recovery" tool list from catalog/linux.json's `rescue`
# category (plus wireguard, which lives in that catalog's `network` category
# but also carries the "rescue" profile). This is the single source of truth
# for what "rescue tooling" means (see AGENTS.md and the task brief on A12) —
# tests/run-tests.sh cross-checks this list against the catalog directly so
# the two can never drift apart silently again.
install_rescue_tools() {
    install_apt_group "disk-health"    smartmontools nvme-cli hdparm
    install_apt_group "hardware-info"  pciutils usbutils lshw dmidecode inxi lm-sensors
    install_apt_group "stress-test"    memtester stress-ng f3
    install_apt_group "partitioning"   gdisk parted testdisk gddrescue gparted
    install_apt_group "filesystems"    e2fsprogs btrfs-progs xfsprogs ntfs-3g dosfstools exfatprogs
    install_apt_group "volume-management" lvm2 mdadm cryptsetup
    install_apt_group "windows-repair" dislocker chntpw efibootmgr arch-install-scripts
    install_apt_group "cloning"        clonezilla
    install_apt_group "networking"     nmap tcpdump iperf3 ethtool wireguard
    install_apt_group "backup"         borgbackup restic
}

# ─── Node.js (required by both AI CLIs) ────────────────────────────────────
# Deliberately Ubuntu's own `nodejs`/`npm` apt packages, not the NodeSource
# `curl | bash` setup script the original scripts used: that is what made
# them non-idempotent (A13, a repo file re-added on every run) and unverified
# (A14, a remote script piped straight into a shell). apt is already
# idempotent on its own, which is the fix AGENTS.md §4 prefers.
install_nodejs() {
    if command -v npm >/dev/null 2>&1; then
        ui_line "skipped" "nodejs/npm (already installed)"
        return 0
    fi
    if ! network_reachable; then
        ui_warn "nodejs/npm — offline, cannot install (both AI CLIs will be skipped)"
        return 0
    fi
    apt_update_once
    # shellcheck disable=SC2086
    if $AUTOOS_SUDO apt-get install -y --no-install-recommends nodejs npm \
        >/tmp/autoos-rescue-apt-node.log 2>&1; then
        ui_line "installed" "nodejs/npm"
    else
        ui_warn "nodejs/npm — install failed (both AI CLIs will be skipped)"
    fi
}

# ─── both AI CLIs, each isolated so one failure is never both (B8) ─────────
install_ai_clis() {
    command -v npm >/dev/null || { ui_warn "no npm — skipping both AI CLIs"; return 0; }
    local cli failed=""
    for cli in "@anthropic-ai/claude-code:claude" "@google/gemini-cli:gemini"; do
        local pkg="${cli%%:*}" bin="${cli##*:}"
        if command -v "$bin" >/dev/null; then
            ui_line "skipped" "$bin already installed"          # AGENTS.md §4
        elif ! network_reachable; then
            ui_warn "$bin — offline, cannot install"
            failed+="$bin "
        elif npm install -g "$pkg" >/tmp/autoos-rescue-npm.log 2>&1; then
            ui_line "installed" "$bin"
        else
            failed+="$bin "                                      # never fails the whole run
        fi
    done
    [[ -z "$failed" ]] || ui_warn "AI CLIs unavailable: $failed"
}

# ─── the `ai` dispatcher (claude default, gemini and any future backend
# selectable by name) — shipped as data next to this script, installed
# verbatim, never generated inline here ─────────────────────────────────────
install_ai_dispatcher() {
    local src_conf="$SCRIPT_DIR/ai-clients.conf"
    local src_bin="$SCRIPT_DIR/ai-dispatcher.sh"
    if [[ ! -f "$src_conf" || ! -f "$src_bin" ]]; then
        ui_warn "ai-clients.conf or ai-dispatcher.sh missing next to rescue-bootstrap.sh — skipping the 'ai' dispatcher"
        return 0
    fi
    # shellcheck disable=SC2086
    $AUTOOS_SUDO mkdir -p /etc/autoos
    # shellcheck disable=SC2086
    $AUTOOS_SUDO install -m 644 "$src_conf" /etc/autoos/ai-clients.conf
    # shellcheck disable=SC2086
    $AUTOOS_SUDO install -m 755 "$src_bin" /usr/local/bin/ai
    ui_line "installed" "ai dispatcher (/usr/local/bin/ai, registry /etc/autoos/ai-clients.conf)"
}

# No API key is EVER written here — only commented key lines (see AGENTS.md
# hard rule 1). `alias ai=` is gone: /usr/local/bin/ai now covers that job
# for every registered backend, not just one hard-coded tool.
write_ai_profile() {
    local dest="/etc/profile.d/autoos-ai.sh"
    local tmp
    tmp="$(mktemp)"
    cat >"$tmp" <<'EOF'
# AutoOS rescue stick — AI CLIs.
#
# `ai` is a small dispatcher (/usr/local/bin/ai) that picks a backend from
# /etc/autoos/ai-clients.conf. Claude is the default; run `ai --list` to see
# what is actually installed on this stick, or call a backend by name
# directly: `ai gemini "..."`, or the CLIs themselves: `claude ...`, `gemini ...`.
#
# No API key is ever written here. Uncomment and paste your own below, or use
# `claude login` / `gemini` interactive auth instead.
# export ANTHROPIC_API_KEY=
# export GEMINI_API_KEY=
EOF
    # shellcheck disable=SC2086
    $AUTOOS_SUDO install -m 644 "$tmp" "$dest"
    rm -f "$tmp"
    ui_line "installed" "$dest"
}

print_summary() {
    printf '\n'
    ui_info "$COUNT_INSTALLED installed, $COUNT_SKIPPED already present"
    if (( ${#MISSING[@]} )); then
        ui_warn "could not install: ${MISSING[*]}"
    else
        ui_info "every rescue tool is present"
    fi
}

main() {
    require_root
    # shellcheck disable=SC2086
    $AUTOOS_SUDO mkdir -p "$(dirname "$MARKER")"
    if [[ -f "$MARKER" ]]; then
        ui_line "skipped" "rescue-bootstrap already ran on $(cat "$MARKER" 2>/dev/null || printf unknown) — re-verifying"
    fi

    install_rescue_tools
    install_nodejs
    install_ai_clis
    install_ai_dispatcher
    write_ai_profile
    print_summary

    # shellcheck disable=SC2086
    date -u +%Y-%m-%dT%H:%M:%SZ | $AUTOOS_SUDO tee "$MARKER" >/dev/null
}

main "$@"
