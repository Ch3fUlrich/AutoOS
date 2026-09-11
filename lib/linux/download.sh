#!/usr/bin/env bash
# Verified, resumable, idempotent file download for AutoOS.
# Task 3 of plan 2026-09-11-installer-usb-and-rescue-profile: every later
# phase-2 step (fetching a multi-gigabyte installer ISO over a connection
# that may drop mid-transfer, onto a machine that might already have a good
# copy cached from a previous run) is built on fetch_verified() below. It is
# the single choke point that never hands back a file it has not itself
# verified, and never leaves a half-downloaded file sitting where a later
# run could mistake it for a complete one.
#
# Exit codes are a contract later tasks branch on - keep them exactly:
#   0  verified (freshly downloaded, or a cache hit that still matches)
#   2  checksum mismatch
#   3  signature verification failure
#   4  transport failure (the download itself failed, or produced nothing)
#
# shellcheck shell=bash
#
# Sourced alongside lib/linux/ui.sh (ui_line/ui_err), lib/linux/detect.sh
# (has_cmd) and lib/linux/install.sh (run, AUTOOS_SUDO) - like every other
# lib/linux/*.sh module, this file is never executed standalone and
# deliberately carries no top-level `set -e`/`set -u`: sourcing a script that
# sets shell options changes them for whoever sourced it too (setup.sh,
# tests/run-tests.sh). Every function below checks its own exit codes
# explicitly, quotes every expansion and uses `local` throughout.

# download_cache_dir
# Prints the resolved AutoOS download cache directory (plan ruling P6):
#   ${AUTOOS_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/autoos/images}
# Always outside the repository. Pure - it does not create the directory;
# callers mkdir -p it at the point they actually need it (the same
# convention imagecache_build() uses for its own `$out`, rather than baking
# directory creation into a helper that only computes a path).
download_cache_dir() {
    if [[ -n "${AUTOOS_CACHE_DIR:-}" ]]; then
        printf '%s\n' "$AUTOOS_CACHE_DIR"
    else
        printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}/autoos/images"
    fi
}

# _sha256_matches <file> <want-hex>
# Case-insensitive compare against sha256sum (Linux) or shasum -a 256 (macOS
# fallback) - the same pair install_oh_my_zsh() already uses in install.sh.
_sha256_matches() {
    local file="$1" want="$2" have=""
    [[ -f "$file" ]] || return 1
    if has_cmd sha256sum; then
        have="$(sha256sum "$file" 2>/dev/null | awk '{print $1}')"
    elif has_cmd shasum; then
        have="$(shasum -a 256 "$file" 2>/dev/null | awk '{print $1}')"
    else
        ui_err "fetch_verified: no sha256sum or shasum found to verify $file"
        return 1
    fi
    [[ -n "$have" && "${have,,}" == "${want,,}" ]]
}

# _gpg_verify <file> <sig_url> <fpr>
# Downloads the detached signature from sig_url and verifies <file> against
# it using ONLY the given fingerprint - imported into a throwaway GNUPGHOME
# under the download cache dir so this never reads or writes the invoking
# user's real ~/.gnupg. AGENTS.md hard rule 4 is framed around PATH/profile/
# config files, but a keyring is exactly that kind of thing the user owns; a
# throwaway homedir is the only way to check a signature without touching it.
_gpg_verify() {
    local file="$1" sig_url="$2" fpr="$3"
    local cache_dir gnupghome sig_tmp rc=0

    if ! has_cmd gpg; then
        ui_err "fetch_verified: gpg not found - cannot verify signature"
        return 1
    fi
    if [[ -z "$fpr" || "$fpr" == "-" ]]; then
        ui_err "fetch_verified: a signature URL was given without a GPG fingerprint"
        return 1
    fi

    cache_dir="$(download_cache_dir)"
    mkdir -p "$cache_dir" || { ui_err "fetch_verified: cannot create cache dir $cache_dir"; return 1; }
    gnupghome="$(mktemp -d "$cache_dir/.gnupg-XXXXXX")" || {
        ui_err "fetch_verified: cannot create a throwaway GNUPGHOME under $cache_dir"
        return 1
    }
    chmod 700 "$gnupghome"
    sig_tmp="$(mktemp)"

    if ! curl -fL --retry 3 --retry-delay 2 -o "$sig_tmp" "$sig_url"; then
        ui_err "fetch_verified: failed to download signature from $sig_url"
        rm -rf "$gnupghome"
        rm -f "$sig_tmp"
        return 1
    fi

    if ! GNUPGHOME="$gnupghome" gpg --batch --quiet --keyserver hkps://keyserver.ubuntu.com \
            --recv-keys "$fpr" >/dev/null 2>&1; then
        ui_err "fetch_verified: could not fetch GPG key $fpr"
        rm -rf "$gnupghome"
        rm -f "$sig_tmp"
        return 1
    fi

    GNUPGHOME="$gnupghome" gpg --batch --quiet --verify "$sig_tmp" "$file" >/dev/null 2>&1 || rc=1

    rm -rf "$gnupghome"
    rm -f "$sig_tmp"
    return "$rc"
}

# fetch_verified <url> <dest> <sha256|-> <sig_url|-> <gpg_fpr|->
# Downloads <url> to <dest> and refuses to hand it back unless it verifies.
# "-" skips a check (no checksum wanted, no signature wanted). Returns:
#   0 verified   2 checksum mismatch   3 signature failure   4 transport failure
# Deletes the partial file on every failure path: a half-downloaded file left
# where <dest> is expected is worse than failing loudly, because a later run
# has no way to tell it apart from a real one.
fetch_verified() {
    local url="$1" dest="$2" want="${3:--}" sig_url="${4:--}" fpr="${5:--}"
    local tmp="${dest}.part"

    # Idempotency (AGENTS.md SS4): a cached file that still matches is a
    # skip, not a refetch. This is why the cache check runs before anything
    # else touches the network.
    if [[ -s "$dest" && "$want" != "-" ]] && _sha256_matches "$dest" "$want"; then
        ui_line "skipped" "$(basename "$dest") already downloaded and verified"
        return 0
    fi

    rm -f "$tmp"
    # curl -L follows the mirror redirect BITS could not (plan finding A8);
    # --fail turns an HTTP error page into a non-zero exit instead of a
    # multi-GB HTML file saved under the ISO's name (A7). Routed through
    # run() (install.sh) rather than called bare: a large ISO benefits from
    # the same timeout guard and output draining every other long-running
    # installer command already gets, and run() honours AUTOOS_DRY_RUN for
    # callers that invoke fetch_verified without checking it themselves.
    if ! run curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
        rm -f "$tmp"
        return 4
    fi
    if [[ ! -s "$tmp" ]]; then
        ui_err "fetch_verified: download of $url produced no file"
        rm -f "$tmp"
        return 4
    fi

    if [[ "$want" != "-" ]] && ! _sha256_matches "$tmp" "$want"; then
        ui_err "fetch_verified: checksum mismatch for $url"
        rm -f "$tmp"
        return 2
    fi

    if [[ "$sig_url" != "-" ]] && ! _gpg_verify "$tmp" "$sig_url" "$fpr"; then
        ui_err "fetch_verified: signature verification failed for $url"
        rm -f "$tmp"
        return 3
    fi

    mkdir -p "$(dirname "$dest")" 2>/dev/null
    if ! mv -f "$tmp" "$dest"; then
        ui_err "fetch_verified: could not move verified file into place: $dest"
        rm -f "$tmp"
        return 4
    fi
    return 0
}
