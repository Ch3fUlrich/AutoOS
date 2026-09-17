#!/usr/bin/env bash
# AutoOS rescue-USB bootstrap.
#
# Runs inside an Ubuntu 26.04.1 LTS live session booted from the rescue stick
# (it also works on an already-installed machine). It installs the
# "Disaster Recovery" toolkit from catalog/linux.json's `rescue` category,
# both networked AI CLIs (claude, agy) and oterm (the local-ai TUI client),
# and a small dispatcher (`ai`) that picks whichever of those — including a
# plain `ollama` fallback with no API key or network — is actually usable.
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
#   B19  network_reachable() above made this script offline-*tolerant*, not
#        offline-*capable*: a stick with no network still got nothing, because
#        nothing pointed apt at a local cache. Fixed by register_offline_cache()
#        below, which — when lib/linux/imagecache.sh has pre-built one next to
#        this script — registers it as a local apt repo before the first
#        install, so the rescue toolkit installs with the network fully down.
#   A1/2 A committed plaintext credential and a malformed SHA-512 hash. Not this
#        script's concern directly (see templates/user-data.example and
#        templates/preseed.cfg.example), but it never writes a credential
#        either — /etc/profile.d/autoos-ai.sh carries only *commented*
#        `# export ...API_KEY=` lines, never a value.
#   A3   Automatic whole-disk wipe with no prompt. Not this script's concern
#        (it does not touch storage at all) — see the answer-file templates.
#
# DELIBERATE DIVERGENCE FROM THE CATALOG — Node.js only.
#
#   catalog/linux.json installs `nodejs` with provider "script" (NodeSource),
#   and both AI CLIs `require` it, so `setup.sh --profile rescue` gets Node
#   from NodeSource. install_nodejs() below instead takes Ubuntu's own
#   `nodejs`/`npm` apt packages. That is intentional, not drift: it is the
#   fix for A14 (NodeSource means `curl | bash` of an unverified remote
#   script) and A13 (its apt repo file was re-added on every run). apt is
#   idempotent on its own and needs no network trust decision, which is what
#   a rescue stick handed to a stranger needs.
#
#   Everything ELSE here is cross-checked against the catalog by
#   tests/run-tests.sh, in both directions, precisely so a second hand-written
#   package list can never drift again (A12). Node is the one exception, and
#   it is documented here and in docs/profiles.md rather than left implicit.
#
# Safe to run twice (AGENTS.md §4): every already-installed package or file
# is reported "skipped", never re-installed or re-created from scratch. The
# two files an operator can edit — /etc/autoos/ai-clients.conf and
# /etc/profile.d/autoos-ai.sh — are merged or left alone, never overwritten
# (hard rule 4), and backed up before any change (hard rule 5).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# AUTOOS_ROOT_PREFIX: test-only override, empty in every real install. The test
# suite points it at a temp directory so main() and every installer below can be
# driven end to end without touching a real /etc, /usr/local or /var — the same
# test-only-override pattern as AUTOOS_OFFLINE_APT_LIST in
# register_offline_cache() below. Nothing a real operator runs ever sets it.
PREFIX="${AUTOOS_ROOT_PREFIX:-}"
MARKER="$PREFIX/var/lib/autoos/rescue-bootstrap.done"
AI_REGISTRY_DEST="$PREFIX/etc/autoos/ai-clients.conf"
AI_BIN_DEST="$PREFIX/usr/local/bin/ai"
AI_PROFILE_DEST="$PREFIX/etc/profile.d/autoos-ai.sh"
OLLAMA_CHAT_DEST="$PREFIX/usr/local/bin/ollama-chat"

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

# ─── backups (AGENTS.md hard rule 5: never modify a file the user owns without
# copying it to <file>.autoos-backup-<timestamp> first) ─────────────────────
backup_file() {
    local path="$1" backup
    backup="$path.autoos-backup-$(date -u +%Y%m%d%H%M%S)"
    # shellcheck disable=SC2086
    if $AUTOOS_SUDO cp -p "$path" "$backup"; then
        ui_info "backed up $path -> $backup"
        return 0
    fi
    ui_warn "could not back up $path"
    return 1
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

# ─── local offline package cache (B19) ─────────────────────────────────────
# A rescue stick meets exactly the machines that have no working network, so
# apt needs a source that isn't the internet. lib/linux/imagecache.sh builds
# `<stick_root>/rescue/debs` ahead of time (a real apt repo: .deb files plus
# Packages.gz) for the ISO's own release; this only ever *consumes* it.
OFFLINE_CACHE_DIR=""
OFFLINE_CACHE_REGISTERED=0

detect_offline_cache() {
    local candidate
    # The bootstrap script sits at the stick root, so the cache built beside
    # it is "rescue/debs" relative to SCRIPT_DIR. /cdrom is where Ubuntu's
    # live session mounts the boot medium itself, which is the same stick
    # under a different, well-known path — accept either.
    for candidate in "$SCRIPT_DIR/rescue/debs" /cdrom/rescue/debs; do
        if [[ -f "$candidate/Packages.gz" ]]; then
            OFFLINE_CACHE_DIR="$candidate"
            return 0
        fi
    done
    return 1
}

# apt_can_install(): true when apt has *something* to install from — real
# network, or a registered local cache. Without this, a fully offline stick
# would still see network_reachable() fail and skip every apt group before
# ever trying the local repo apt itself already knows how to use.
apt_can_install() {
    network_reachable && return 0
    [[ "$OFFLINE_CACHE_REGISTERED" -eq 1 ]]
}

register_offline_cache() {
    detect_offline_cache || return 0

    local abs_dir list_file entry tmp
    abs_dir="$(cd "$OFFLINE_CACHE_DIR" && pwd)"
    # AUTOOS_OFFLINE_APT_LIST: test-only override (the test suite uses this to
    # avoid ever writing to a real /etc/apt/sources.list.d; a real install
    # never needs to set it — same pattern as AUTOOS_AI_REGISTRY in
    # templates/ai-dispatcher.sh).
    list_file="${AUTOOS_OFFLINE_APT_LIST:-/etc/apt/sources.list.d/autoos-offline.list}"
    # [trusted=yes] is a deliberate, narrow trade-off, not a shortcut taken
    # everywhere: this one repo is generated locally on this same stick,
    # minutes before it is used, and ships no GPG key to check it against —
    # so apt is told not to require a signature *for this source only*.
    # NEVER add [trusted=yes] to a real network apt source.
    entry="deb [trusted=yes] file:${abs_dir} ./"

    # One visible line every run: an unsigned local repo is an intentional,
    # narrow exception and the person at the keyboard should know it's active.
    ui_warn "using local, UNSIGNED offline package cache: $abs_dir (no GPG check — see rescue-bootstrap.sh B19 comment)"

    if [[ -f "$list_file" ]] && grep -qF "$entry" "$list_file" 2>/dev/null; then
        ui_line "skipped" "offline cache already registered ($list_file)"
    else
        tmp="$(mktemp)"
        cat >"$tmp" <<EOF
# AutoOS rescue stick — local offline package cache (B19). Unsigned on
# purpose: this repo has no GPG key, it is built fresh on this same stick.
# Left in place after bootstrap (not removed at the end) so a later manual
# \`apt-get install\` on this machine can still reach the rescue tools with
# no network — it only ever resolves while this path exists on this stick.
$entry
EOF
        # shellcheck disable=SC2086
        $AUTOOS_SUDO install -m 644 "$tmp" "$list_file"
        rm -f "$tmp"
        ui_line "installed" "offline cache registered ($list_file)"
    fi

    OFFLINE_CACHE_REGISTERED=1
    apt_update_once
}

# ─── offline pip wheelhouse (Task 13) ──────────────────────────────────────
# oterm is Python, not apt, so it needs its own offline cache: a directory of
# wheels/sdists for oterm AND its full dependency tree, built ahead of time
# by lib/linux/imagecache.sh's imagecache_wheelhouse_build() and consumed
# here with `pip install --no-index --find-links` — the same
# built-ahead-of-time / consumed-offline split as register_offline_cache()
# above, just for pip instead of apt.
WHEELHOUSE_DIR=""

detect_wheelhouse() {
    local candidate
    for candidate in "$SCRIPT_DIR/rescue/wheels" /cdrom/rescue/wheels; do
        if compgen -G "$candidate"/oterm-*.whl >/dev/null 2>&1 \
            || compgen -G "$candidate"/oterm-*.tar.gz >/dev/null 2>&1; then
            WHEELHOUSE_DIR="$candidate"
            return 0
        fi
    done
    return 1
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

    if ! apt_can_install; then
        for pkg in "${to_install[@]}"; do
            ui_warn "$pkg — offline and no local cache, cannot install ($group_name)"
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

# ─── Node.js (required by Claude Code; Antigravity CLI is a standalone
# binary and needs no runtime) ───────────────────────────────────────────────
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
        ui_warn "nodejs/npm — offline, cannot install (claude will be skipped)"
        return 0
    fi
    apt_update_once
    # shellcheck disable=SC2086
    if $AUTOOS_SUDO apt-get install -y --no-install-recommends nodejs npm \
        >/tmp/autoos-rescue-apt-node.log 2>&1; then
        ui_line "installed" "nodejs/npm"
    else
        ui_warn "nodejs/npm — install failed (claude will be skipped)"
    fi
}

# ─── both AI CLIs, each isolated so one failure is never both (B8) ─────────
# claude-code (npm, needs Node — see install_nodejs() above) and agy (a
# standalone binary, needs only network) install very differently now, so
# each gets its own branch instead of the old one-size-fits-all npm loop —
# but the B8 property (one failing client must never take the other with it)
# still holds: each branch appends to $failed independently and neither can
# short-circuit the other.
install_ai_clis() {
    local failed=""

    if command -v claude >/dev/null; then
        ui_line "skipped" "claude already installed"                # AGENTS.md §4
    elif ! command -v npm >/dev/null; then
        ui_warn "claude — no npm, cannot install"
        failed+="claude "
    elif ! network_reachable; then
        ui_warn "claude — offline, cannot install"
        failed+="claude "
    else
        # `npm install -g` writes to /usr/lib/node_modules and /usr/local/bin,
        # so it needs $AUTOOS_SUDO exactly like every other privileged command
        # here. Without it the normal Ubuntu live session — user "ubuntu" with
        # passwordless sudo, i.e. NOT uid 0 — installed every apt package fine
        # and then failed claude with EACCES, surfacing only as a terse
        # "AI CLIs unavailable: claude".
        # shellcheck disable=SC2086  # AUTOOS_SUDO is intentionally unquoted: empty, or the single word "sudo"
        if $AUTOOS_SUDO npm install -g @anthropic-ai/claude-code >/tmp/autoos-rescue-npm.log 2>&1; then
            ui_line "installed" "claude"
        else
            failed+="claude "                                      # never fails the whole run
        fi
    fi

    if command -v agy >/dev/null; then
        ui_line "skipped" "agy already installed"                   # AGENTS.md §4
    elif ! network_reachable; then
        ui_warn "agy — offline, cannot install"
        failed+="agy "
    elif install_agy_cli; then
        ui_line "installed" "agy"
    else
        failed+="agy "                                              # never fails the whole run
    fi

    [[ -z "$failed" ]] || ui_warn "AI CLIs unavailable: $failed"
}

# ─── oterm (Task 13, the local-ai TUI client) ──────────────────────────────
# Prefers the offline wheelhouse (see detect_wheelhouse() above) so a stick
# with the wheels cache built ahead of time installs it with the network
# fully down, same as install_rescue_tools() does for apt via
# register_offline_cache(). Falls back to a normal networked `pip install`
# when no wheelhouse is present, and degrades to a warning — never a hard
# failure — when neither python3 nor network is available, matching every
# other installer in this script (B8: one missing tool never blocks the
# rest of the run).
install_oterm() {
    if command -v oterm >/dev/null 2>&1; then
        ui_line "skipped" "oterm already installed"                  # AGENTS.md §4
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        ui_warn "oterm — python3 not found, cannot install"
        return 0
    fi
    # `pip install --user` puts the console script in ~/.local/bin, which is
    # not on PATH in every session (a fresh live boot, sudo, CI): a second
    # run then found no `oterm` command and installed it again - the exact
    # "installed, not skipped" the Linux CI caught. The package, not the
    # command, is what proves it is there.
    if python3 -m pip show oterm >/dev/null 2>&1; then
        ui_line "skipped" "oterm already installed (pip user site; ~/.local/bin may not be on PATH)"
        return 0
    fi

    if detect_wheelhouse; then
        ui_info "installing oterm from offline wheelhouse ($WHEELHOUSE_DIR)"
        if python3 -m pip install --user --no-index --find-links "$WHEELHOUSE_DIR" oterm \
            >/tmp/autoos-rescue-oterm.log 2>&1; then
            ui_line "installed" "oterm (offline wheelhouse)"
        else
            ui_warn "oterm — offline install from wheelhouse failed (see /tmp/autoos-rescue-oterm.log)"
        fi
        return 0
    fi

    if ! network_reachable; then
        ui_warn "oterm — offline and no wheelhouse cache, cannot install"
        return 0
    fi

    ui_info "installing oterm via pip"
    # --break-system-packages only as a second attempt (PEP 668): the plain,
    # safer call is always tried first and this one only runs if it was
    # refused, same order as install_ai_clis()'s neighbours use for apt.
    if python3 -m pip install --user oterm >/tmp/autoos-rescue-oterm.log 2>&1 \
        || python3 -m pip install --user --break-system-packages oterm >/tmp/autoos-rescue-oterm.log 2>&1; then
        ui_line "installed" "oterm"
    else
        ui_warn "oterm — install failed (see /tmp/autoos-rescue-oterm.log)"
    fi
}

# Google's Antigravity CLI installer, replacing Gemini CLI at the human
# partner's direction — Gemini CLI is not deprecated; this is a deliberate
# product choice, not a response to a broken package.
#
# This used to be `curl -fsSL $url | bash`: an unverified remote script piped
# straight into a shell (A14, the exact finding this branch exists to
# remove — a pipe can't be inspected and can be swapped mid-stream). Google
# publishes no checksum for this installer, so a hash can't be pinned the way
# oh-my-zsh's is in lib/linux/install.sh's install_oh_my_zsh(). The minimum
# acceptable bar instead: download to a file, log the exact URL, verify it is
# non-empty and actually looks like a script, and only then execute the
# FILE — never the pipe.
install_agy_cli() {
    local url="https://antigravity.google/cli/install.sh"
    ui_info "downloading Antigravity CLI installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_warn "Antigravity CLI installer from $url does not look like a script — aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    bash "$tmp" >/tmp/autoos-rescue-agy.log 2>&1 || rc=$?
    rm -f "$tmp"
    return $rc
}

# ─── the `ai` dispatcher (claude default, agy and any future backend
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
    $AUTOOS_SUDO mkdir -p "$(dirname "$AI_REGISTRY_DEST")" "$(dirname "$AI_BIN_DEST")"

    install_ai_registry "$src_conf"

    # The dispatcher itself is *our* code, not the operator's, so replacing it
    # wholesale is correct — but only when it actually differs. A second run
    # over an identical file must report "skipped" (AGENTS.md §4), which is
    # what `cmp -s` buys and what an unconditional `install` never could.
    if [[ -f "$AI_BIN_DEST" ]] && cmp -s "$src_bin" "$AI_BIN_DEST"; then
        ui_line "skipped" "ai dispatcher ($AI_BIN_DEST, unchanged)"
    else
        # shellcheck disable=SC2086
        $AUTOOS_SUDO install -m 755 "$src_bin" "$AI_BIN_DEST"
        ui_line "installed" "ai dispatcher ($AI_BIN_DEST)"
    fi

    install_ollama_chat_wrapper
}

# The "local" backend's binary (see templates/ai-clients.conf and
# templates/ollama-chat.sh's own header for why this wrapper exists at all).
# Same idempotency rule as the dispatcher itself just above: it is our code,
# so a second run over an identical file reports "skipped", never
# re-installed.
install_ollama_chat_wrapper() {
    local src="$SCRIPT_DIR/ollama-chat.sh"
    if [[ ! -f "$src" ]]; then
        ui_warn "ollama-chat.sh missing next to rescue-bootstrap.sh — 'ai local' will not work"
        return 0
    fi
    # shellcheck disable=SC2086
    $AUTOOS_SUDO mkdir -p "$(dirname "$OLLAMA_CHAT_DEST")"
    if [[ -f "$OLLAMA_CHAT_DEST" ]] && cmp -s "$src" "$OLLAMA_CHAT_DEST"; then
        ui_line "skipped" "ollama-chat ($OLLAMA_CHAT_DEST, unchanged)"
    else
        # shellcheck disable=SC2086
        $AUTOOS_SUDO install -m 755 "$src" "$OLLAMA_CHAT_DEST"
        ui_line "installed" "ollama-chat ($OLLAMA_CHAT_DEST)"
    fi
}

# templates/ai-clients.conf documents itself as THE extension point: "adding a
# third AI CLI later is a one-line addition to THIS file". Installing it with a
# plain `install` then erased that line on the next run — a documented
# extension point that silently deletes the extension is worse than no
# extension point at all, and it is AGENTS.md hard rule 4 (never overwrite a
# config wholesale; read, merge idempotently, write back).
#
# So: merge by id. Everything already in the file is kept verbatim — comments,
# ordering, and any backend the operator registered — and only shipped records
# whose id is absent get appended. The file is backed up first (hard rule 5)
# and left completely untouched when the merge would change nothing.
install_ai_registry() {
    local src="$1" tmp
    if [[ ! -f "$AI_REGISTRY_DEST" ]]; then
        # shellcheck disable=SC2086
        $AUTOOS_SUDO install -m 644 "$src" "$AI_REGISTRY_DEST"
        ui_line "installed" "ai registry ($AI_REGISTRY_DEST)"
        return 0
    fi

    tmp="$(mktemp)"
    # Pass 1 (the installed file) is copied out line for line; pass 2 (the
    # shipped file) contributes only records whose id pass 1 did not define.
    awk -F: '
        NR == FNR {
            if ($0 !~ /^[[:space:]]*#/ && NF >= 2) have[$1] = 1
            print
            next
        }
        /^[[:space:]]*#/ { next }
        NF < 2           { next }
        !($1 in have)    { print }
    ' "$AI_REGISTRY_DEST" "$src" >"$tmp"

    if cmp -s "$tmp" "$AI_REGISTRY_DEST"; then
        rm -f "$tmp"
        ui_line "skipped" "ai registry ($AI_REGISTRY_DEST, every shipped backend already registered)"
        return 0
    fi

    # Hard rule 5: no backup, no overwrite. `|| true` here used to let a
    # failed backup fall straight through to the install below.
    if ! backup_file "$AI_REGISTRY_DEST"; then
        rm -f "$tmp"
        ui_err "ai registry: could not back up $AI_REGISTRY_DEST - leaving it untouched"
        return 1
    fi
    # shellcheck disable=SC2086
    $AUTOOS_SUDO install -m 644 "$tmp" "$AI_REGISTRY_DEST"
    rm -f "$tmp"
    ui_line "installed" "ai registry ($AI_REGISTRY_DEST, appended the shipped backends it was missing)"
}

# No API key is EVER written here — only commented key lines (see AGENTS.md
# hard rule 1). `alias ai=` is gone: /usr/local/bin/ai now covers that job
# for every registered backend, not just one hard-coded tool.
write_ai_profile() {
    local dest="$AI_PROFILE_DEST"
    local tmp
    # shellcheck disable=SC2086
    $AUTOOS_SUDO mkdir -p "$(dirname "$dest")"
    tmp="$(mktemp)"
    cat >"$tmp" <<'EOF'
# AutoOS rescue stick — AI CLIs.
#
# `ai` is a small dispatcher (/usr/local/bin/ai) that picks a backend from
# /etc/autoos/ai-clients.conf. Claude is the default; run `ai --list` to see
# what is actually installed on this stick, or call a backend by name
# directly: `ai agy "..."`, or the CLIs themselves: `claude ...`, `agy ...`.
#
# No API key is ever written here. Uncomment and paste your own below, or use
# `claude login` interactive auth instead.
# export ANTHROPIC_API_KEY=
#
# agy (Antigravity CLI) has no API-key env var: `agy -p "..."` (headless)
# only works with credentials cached from a prior *interactive* `agy`
# session. Run `agy` once, log in, then headless use works — but that is not
# possible on an offline machine that has never run `agy` interactively
# before. See docs/profiles.md.
EOF
    # This file invites the operator to paste their own key into it
    # ("Uncomment and paste your own below"), so the moment it exists it is
    # THEIRS. It carries no logic this script ever needs to update — only
    # comments — so once present it is never rewritten: overwriting it deleted
    # the operator's key on every second run, which is AGENTS.md hard rule 4.
    if [[ -f "$dest" ]]; then
        if cmp -s "$tmp" "$dest"; then
            ui_line "skipped" "$dest (unchanged)"
        else
            # Hard rule 5, before deciding - and say what actually happened:
            # `|| true` used to print "backup taken" whether or not it was.
            if backup_file "$dest"; then
                ui_line "skipped" "$dest (kept your edits — backup taken)"
            else
                ui_line "skipped" "$dest (kept your edits — backup FAILED, nothing was overwritten)"
            fi
        fi
        rm -f "$tmp"
        return 0
    fi
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
    if command -v agy >/dev/null 2>&1; then
        ui_warn "agy (Antigravity CLI) headless mode needs a prior interactive login — run 'agy' once to authenticate before 'agy -p ...' works. Not possible on an offline machine with no earlier session."
    fi
}

main() {
    require_root
    # shellcheck disable=SC2086
    $AUTOOS_SUDO mkdir -p "$(dirname "$MARKER")"
    if [[ -f "$MARKER" ]]; then
        ui_line "skipped" "rescue-bootstrap already ran on $(cat "$MARKER" 2>/dev/null || printf unknown) — re-verifying"
    fi

    register_offline_cache
    install_rescue_tools
    install_nodejs
    install_ai_clis
    install_oterm
    install_ai_dispatcher
    write_ai_profile
    print_summary

    # shellcheck disable=SC2086
    date -u +%Y-%m-%dT%H:%M:%SZ | $AUTOOS_SUDO tee "$MARKER" >/dev/null
}

main "$@"
