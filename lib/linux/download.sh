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

# ─── Task 4B: catalog entry -> concrete download URL ───────────────────────
# catalog/images.json deliberately never stores a version (plan finding A9):
# each real entry gives an `index` release-listing URL and a `file` filename
# regex instead of a URL. image_resolve() is the missing bridge between that
# and fetch_verified() above, which needs one concrete URL. It lives here
# (download.sh), not usb.sh, because it is itself a download-adjacent read
# (an HTTP GET of an index page) and setup.sh sources download.sh before
# usb.sh - putting the resolver in the later-sourced file would make it
# depend on something not guaranteed loaded yet.

# _image_resolve_root_dir — mirrors usb.sh's _usb_root_dir (duplicated
# rather than called: usb.sh is sourced AFTER this file, so its functions
# are not necessarily defined yet at the point image_resolve runs if some
# future caller ever sources download.sh on its own).
_image_resolve_root_dir() {
    if [[ -n "${AUTOOS_ROOT:-}" ]]; then
        printf '%s\n' "$AUTOOS_ROOT"
    else
        pwd
    fi
}

# _image_resolve_is_pseudo <image_id>
# custom-url and custom-local are UI affordances (a free-text URL field / a
# free-text local path field) - they have no `index` by construction and
# must never be handed to the network code below. Named set, same
# convention as tests/helpers/images_validate.py's own PSEUDO set.
_image_resolve_is_pseudo() {
    case "$1" in
        custom-url|custom-local) return 0 ;;
        *) return 1 ;;
    esac
}

# _image_resolve_kv <text> <key>
# Pulls the value out of one "key=value" line of <text> (this file's
# internal wire format between the python helpers below and image_resolve).
# Anchored at line-start so a value that itself contains "key=" later in the
# string is never truncated.
_image_resolve_kv() {
    printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1
}

# _image_resolve_entry <catalog> <image_id>
# Looks up one images.json entry by id and prints it as status=/index=/
# file=/sums=/sig= lines - status=error + msg=... (never a crash) for an
# unknown id or unreadable catalog, so image_resolve can report it as a
# clear failure with no fetch attempted.
_image_resolve_entry() {
    local catalog="$1" id="$2"
    python3 - "$catalog" "$id" <<'PY'
import json, sys

path, want = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as exc:
    print("status=error")
    print(f"msg=cannot read {path}: {exc}")
    sys.exit(0)

entry = next((e for e in data.get("images", []) if e.get("id") == want), None)
if entry is None:
    print("status=error")
    print(f"msg=no catalog entry for image id '{want}' in {path}")
    sys.exit(0)

print("status=ok")
print(f"index={entry.get('index') or ''}")
print(f"file={entry.get('file') or ''}")
print(f"sums={entry.get('sums') or ''}")
print(f"sig={entry.get('sig') or ''}")
PY
}

# _image_resolve_parse_page <html_file> <base_url> <file_regex> <sums_field>
#                           <sig_field> <stage:top|leaf> <lts_wanted:0|1>
# Parses one already-fetched directory-listing page and decides what to do
# next. Prints exactly one of:
#   status=ok       url=... file=... sums=... sig=...   (fully resolved)
#   status=recurse  subdir=...                           (top stage only)
#   status=error     msg=...
#
# Two directory shapes are handled by the SAME code, gated on what is
# actually on the page rather than on which catalog entry this is:
#   - files listed directly (Debian's current/-symlinked iso-cd/, or any
#     index whose `index` URL already points at the release itself) ->
#     direct match, first branch below;
#   - only version-numbered subdirectories listed (releases.ubuntu.com's
#     top-level page) -> recurse into exactly one of them, chosen below.
#
# <file_regex> is matched with re.search against each anchor's basename
# (never re.fullmatch): the catalog's own patterns end in "$" but are not
# anchored at "^", and this does not try to make them stricter than their
# author wrote them.
#
# Ambiguity is always an error, never a first-match: two files at the same
# directory level carry no ordering/recency signal (a duplicate build, a
# mirror artefact, a regex that is broader than intended - any of these is a
# catalog/regex bug, not something to silently paper over).
#
# lts_wanted=1 (image_resolve sets this whenever the id ends in "-lts")
# selects Ubuntu's actual release rule: an LTS is the April ("-XX.04") release
# of an even-numbered year; the October ("-XX.10") release and any odd-year
# release are interim. This is a rule about Ubuntu's release cadence, not a
# pinned version number, so it never goes stale the way a literal "24.04"
# would. Without -lts, the best-effort fallback is "highest version
# directory present" - a catalog entry that needs some OTHER channel than
# "latest" should point `index` at a stable symlink (Debian's current/
# shape) instead of a bare release list, the same way debian-netinst-stable
# already does.
_image_resolve_parse_page() {
    local html_file="$1" base_url="$2" file_re="$3" sums_field="$4" sig_field="$5" stage="$6" lts_wanted="$7"
    python3 - "$html_file" "$base_url" "$file_re" "$sums_field" "$sig_field" "$stage" "$lts_wanted" <<'PY'
import re
import sys

html_file, base_url, file_re_str, sums_field, sig_field, stage, lts_wanted = sys.argv[1:8]
lts_wanted = lts_wanted == "1"

with open(html_file, encoding="utf-8", errors="replace") as fh:
    html = fh.read()

HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
hrefs = []
for m in HREF_RE.finditer(html):
    h = m.group(1)
    if not h or h in ("../", "./", "/") or h.startswith(("?", "#", "mailto:")):
        continue
    hrefs.append(h)


def basename(href):
    return href.rstrip("/").split("/")[-1].split("?")[0]


def is_pattern(s):
    return bool(s) and s != "-" and any(c in s for c in "[]()+\\$^*")


def resolve_sidecar(kind, field, base, hrefs):
    # Returns (url_or_dash, error_message_or_None). "-" mirrors the
    # catalog's/fetch_verified's own convention for "not applicable" -
    # returned only when the FIELD itself says so, never as a fallback for
    # a pattern that failed to resolve (that is an error, below): a
    # silently-missing checksum source is exactly the "guess" this function
    # must not make.
    if not field or field == "-":
        return "-", None
    if is_pattern(field):
        pat = re.compile(field)
        matches = sorted({basename(h) for h in hrefs if not h.endswith("/") and pat.search(basename(h))})
        if not matches:
            return None, f"{kind} pattern '{field}' matched nothing under {base}"
        if len(matches) > 1:
            return None, f"{kind} pattern '{field}' matched multiple files under {base}: {', '.join(matches)}"
        return base + matches[0], None
    return base + field, None


file_re = re.compile(file_re_str)
direct = [h for h in hrefs if not h.endswith("/") and file_re.search(basename(h))]

if direct:
    names = sorted({basename(h) for h in direct})
    if len(names) > 1:
        print("status=error")
        print(f"msg=pattern '{file_re_str}' matched multiple files at {base_url}: {', '.join(names)} "
              "- refusing to guess, no ordering rule is defined for files at the same directory level")
        sys.exit(0)

    fname = names[0]
    sums_url, sums_err = resolve_sidecar("sums", sums_field, base_url, hrefs)
    if sums_err:
        print("status=error"); print(f"msg={sums_err}"); sys.exit(0)
    sig_url, sig_err = resolve_sidecar("sig", sig_field, base_url, hrefs)
    if sig_err:
        print("status=error"); print(f"msg={sig_err}"); sys.exit(0)

    print("status=ok")
    print(f"url={base_url}{fname}")
    print(f"file={fname}")
    print(f"sums={sums_url}")
    print(f"sig={sig_url}")
    sys.exit(0)

if stage == "leaf":
    print("status=error")
    print(f"msg=pattern '{file_re_str}' matched nothing under {base_url}")
    sys.exit(0)

# stage == "top" and nothing matched directly: look for version-numbered
# subdirectories (releases.ubuntu.com's shape) to recurse into.
VERSION_DIR = re.compile(r'^([0-9]+)\.([0-9]+)(?:\.([0-9]+))?$')
candidates = []
for h in hrefs:
    if not h.endswith("/"):
        continue
    m = VERSION_DIR.match(basename(h))
    if not m:
        continue
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    candidates.append(((major, minor, patch), basename(h)))

if not candidates:
    print("status=error")
    print(f"msg=pattern '{file_re_str}' matched nothing under {base_url} "
          "(no files and no version-numbered subdirectories found either)")
    sys.exit(0)

if lts_wanted:
    eligible = [c for c in candidates if c[0][1] == 4 and c[0][0] % 2 == 0]
    if not eligible:
        print("status=error")
        print(f"msg=no LTS release directory (an even-numbered year's .04) found under {base_url} "
              f"among: {', '.join(n for _, n in sorted(candidates))}")
        sys.exit(0)
    chosen = max(eligible)
else:
    chosen = max(candidates)

print("status=recurse")
print(f"subdir={base_url}{chosen[1]}/")
PY
}

# image_resolve <image_id> [catalog_path]
# Resolves a catalog/images.json entry to a concrete, ready-to-fetch record,
# printed on stdout as exactly:
#   url=<concrete ISO URL>
#   file=<filename>
#   sums=<concrete SHA256SUMS URL, or "-">
#   sig=<concrete signature URL, or "-">
# <catalog_path> defaults to <repo root>/catalog/images.json; tests pass a
# scratch catalog pointing at a loopback fixture server instead (this file
# must never be edited to add a network-touching test - see
# tests/run-tests.sh's "image resolve" block).
#
# Never guesses: a pattern matching nothing, a pattern matching several
# things with no ordering rule, an unknown id, or a pseudo-entry
# (custom-url/custom-local) are all a clear stderr message and a non-zero
# return - never a first-match, never a crash.
image_resolve() {
    local image_id="$1" catalog="${2:-}"
    [[ -z "$catalog" ]] && catalog="$(_image_resolve_root_dir)/catalog/images.json"

    if _image_resolve_is_pseudo "$image_id"; then
        ui_err "image_resolve: '$image_id' is a UI affordance (a user-supplied URL or local file path), not a catalog entry - there is no index to resolve it against"
        return 1
    fi

    local entry_out
    entry_out="$(_image_resolve_entry "$catalog" "$image_id")"
    if [[ "$(_image_resolve_kv "$entry_out" status)" != ok ]]; then
        ui_err "image_resolve: $(_image_resolve_kv "$entry_out" msg)"
        return 1
    fi

    local index file_re sums_field sig_field
    index="$(_image_resolve_kv "$entry_out" index)"
    file_re="$(_image_resolve_kv "$entry_out" file)"
    sums_field="$(_image_resolve_kv "$entry_out" sums)"
    sig_field="$(_image_resolve_kv "$entry_out" sig)"

    if [[ -z "$index" || -z "$file_re" ]]; then
        ui_err "image_resolve: catalog entry '$image_id' has no 'index'/'file' to resolve - it may be a pseudo-entry missing from the check above, or a malformed catalog row"
        return 1
    fi
    case "$index" in
        */) ;;
        *) index="$index/" ;;
    esac

    local lts_wanted=0
    [[ "$image_id" == *-lts ]] && lts_wanted=1

    local cache_dir work
    cache_dir="$(download_cache_dir)"
    mkdir -p "$cache_dir" || { ui_err "image_resolve: cannot create cache dir $cache_dir"; return 1; }
    work="$(mktemp -d "$cache_dir/.resolve-XXXXXX")" || { ui_err "image_resolve: cannot create a scratch dir under $cache_dir"; return 1; }

    if ! fetch_verified "$index" "$work/top.html" - - -; then
        ui_err "image_resolve: could not fetch index $index"
        rm -rf "$work"
        return 1
    fi

    local parsed status
    parsed="$(_image_resolve_parse_page "$work/top.html" "$index" "$file_re" "$sums_field" "$sig_field" top "$lts_wanted")"
    status="$(_image_resolve_kv "$parsed" status)"

    if [[ "$status" == recurse ]]; then
        local subdir; subdir="$(_image_resolve_kv "$parsed" subdir)"
        if ! fetch_verified "$subdir" "$work/leaf.html" - - -; then
            ui_err "image_resolve: could not fetch $subdir"
            rm -rf "$work"
            return 1
        fi
        parsed="$(_image_resolve_parse_page "$work/leaf.html" "$subdir" "$file_re" "$sums_field" "$sig_field" leaf "$lts_wanted")"
        status="$(_image_resolve_kv "$parsed" status)"
    fi

    rm -rf "$work"

    if [[ "$status" != ok ]]; then
        ui_err "image_resolve: $(_image_resolve_kv "$parsed" msg)"
        return 1
    fi

    printf 'url=%s\n' "$(_image_resolve_kv "$parsed" url)"
    printf 'file=%s\n' "$(_image_resolve_kv "$parsed" file)"
    printf 'sums=%s\n' "$(_image_resolve_kv "$parsed" sums)"
    printf 'sig=%s\n' "$(_image_resolve_kv "$parsed" sig)"
    return 0
}
