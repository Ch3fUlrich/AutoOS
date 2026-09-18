#!/usr/bin/env bash
# AutoOS live catalog check — uses the REAL internet.
#
# This script is NOT part of tests/run-tests.sh and must NEVER be sourced or
# invoked from it. That suite is required to run with no route to the
# internet (see .github/workflows/ci.yml, which runs it inside a network
# namespace with only loopback up) — this script exists precisely because
# that suite therefore cannot see when an upstream host moves a file, breaks
# a listing, or 404s a mirror. Run this by hand, or let
# .github/workflows/catalog-live-check.yml run it on a weekly schedule.
#
#   bash tests/live-check-images.sh
#
# For every real entry in catalog/images.json (i.e. every id except the
# custom-url/custom-local UI affordances, which have no index to resolve)
# this script:
#   1. resolves the entry with the real resolver, image_resolve()
#      (lib/linux/download.sh), against a fresh scratch cache directory;
#   2. HEAD-checks the resolved url and sums URL (skipping sums when the
#      catalog entry sets it to "-", meaning no separate manifest);
#   3. HEAD-checks every mirror the entry lists, built the same way
#      usb_fetch_image() (lib/linux/usb.sh) builds a mirror candidate: the
#      mirror URL plus the resolved url with the index prefix stripped.
#
# A handful of entries are known, understood failures — fixing them needs
# resolver work (CDN- or host-specific HTML parsing) that is out of scope
# here. They are named explicitly below so a failure there is reported as
# KNOWN, not FAIL, and does not fail the run. If a known-broken entry
# unexpectedly resolves, that IS a failure: it means the list has gone
# stale and must be pruned.
#
# shellcheck disable=SC2034
#   AUTOOS_NO_COLOR configures the sourced ui.sh (read inside ui_init); the
#   linter sees no reader for it because that read lives in another file.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Confirmed (2026-09-18) unable to resolve today. One line each, why.
declare -A KNOWN_BROKEN=(
    [systemrescue]="vendor CDN (fastly-cdn.system-rescue.org) returns 403 on the directory listing"
    [gparted-live]="SourceForge project 'files' page is not a directory listing image_resolve can parse"
    [clonezilla-live]="SourceForge project 'files' page is not a directory listing image_resolve can parse"
    [memtest86plus]="GitHub releases page renders assets via JS - they are not present in the fetched HTML"
)

# ─── Load the libraries under test, exactly as tests/run-tests.sh does ─────
cd "$ROOT" || { echo "cannot enter $ROOT" >&2; exit 1; }
# shellcheck source=../lib/linux/ui.sh
. lib/linux/ui.sh
# shellcheck source=../lib/linux/detect.sh
. lib/linux/detect.sh
# shellcheck source=../lib/linux/catalog.sh
. lib/linux/catalog.sh
# shellcheck source=../lib/linux/imagecache.sh
. lib/linux/imagecache.sh
# shellcheck source=../lib/linux/install.sh
. lib/linux/install.sh
# shellcheck source=../lib/linux/download.sh
. lib/linux/download.sh
# shellcheck source=../lib/linux/usb.sh
. lib/linux/usb.sh

AUTOOS_NO_COLOR=1
ui_init

OK_COUNT=0
FAIL_COUNT=0
KNOWN_COUNT=0
UNEXPECTED_COUNT=0

# _lc_line <status> <id> <what> <detail>
# One aligned report line. <detail> may be empty.
_lc_line() {
    printf '%-5s %-22s %-8s %s\n' "$1" "$2" "$3" "$4"
}

# _lc_head_ok <url>
# True (rc 0) when a HEAD request (following redirects) returns HTTP 200.
_lc_head_ok() {
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' -I -L --max-time 30 "$1" 2>/dev/null)"
    [[ "$code" == "200" ]]
}

# All real image ids, in catalog order, excluding the two pseudo-entries.
mapfile -t IMAGE_IDS < <(python3 -c "
import json
data = json.load(open('catalog/images.json', encoding='utf-8'))
for e in data['images']:
    if e['id'] not in ('custom-url', 'custom-local'):
        print(e['id'])
" | tr -d '\r')

for id in "${IMAGE_IDS[@]}"; do
    [[ -z "$id" ]] && continue

    is_known=0
    [[ -n "${KNOWN_BROKEN[$id]+x}" ]] && is_known=1

    scratch="$(mktemp -d)"
    resolved="$(AUTOOS_CACHE_DIR="$scratch" image_resolve "$id" 2>&1)"
    rc=$?
    rm -rf "$scratch"

    if (( rc != 0 )); then
        if (( is_known )); then
            _lc_line KNOWN "$id" resolve "${KNOWN_BROKEN[$id]}"
            KNOWN_COUNT=$((KNOWN_COUNT + 1))
        else
            _lc_line FAIL "$id" resolve "$(printf '%s' "$resolved" | tail -n1)"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
        continue
    fi

    if (( is_known )); then
        _lc_line KNOWN "$id" resolve "UNEXPECTEDLY RESOLVED - prune from KNOWN_BROKEN (was: ${KNOWN_BROKEN[$id]})"
        UNEXPECTED_COUNT=$((UNEXPECTED_COUNT + 1))
        continue
    fi

    url="$(_image_resolve_kv "$resolved" url)"
    file="$(_image_resolve_kv "$resolved" file)"
    sums="$(_image_resolve_kv "$resolved" sums)"

    _lc_line OK "$id" resolve "$file"
    OK_COUNT=$((OK_COUNT + 1))

    if _lc_head_ok "$url"; then
        _lc_line OK "$id" url "$url"
        OK_COUNT=$((OK_COUNT + 1))
    else
        _lc_line FAIL "$id" url "$url"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    if [[ -n "$sums" && "$sums" != "-" ]]; then
        if _lc_head_ok "$sums"; then
            _lc_line OK "$id" sums "$sums"
            OK_COUNT=$((OK_COUNT + 1))
        else
            _lc_line FAIL "$id" sums "$sums"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # Mirrors: the SAME relative-path construction usb_fetch_image() uses
    # (lib/linux/usb.sh) - the resolved url with the entry's index prefix
    # stripped, appended to each mirror in turn.
    index="$(_image_field "$id" index)"
    [[ -n "$index" && "$index" != */ ]] && index="$index/"
    rel=""
    [[ -n "$index" && "$url" == "$index"* ]] && rel="${url#"$index"}"

    if [[ -n "$rel" ]]; then
        mirrors_csv="$(_image_field "$id" mirrors)"
        IFS=',' read -ra mirrors <<<"$mirrors_csv"
        for mirror in "${mirrors[@]}"; do
            [[ -z "$mirror" ]] && continue
            [[ "$mirror" != */ ]] && mirror="$mirror/"
            candidate="${mirror}${rel}"
            if _lc_head_ok "$candidate"; then
                _lc_line OK "$id" mirror "$candidate"
                OK_COUNT=$((OK_COUNT + 1))
            else
                _lc_line FAIL "$id" mirror "$candidate  (not HTTP 200 - lagging, moved, or unreachable)"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        done
    fi
done

printf '\n%d ok, %d fail, %d known-broken, %d unexpectedly-resolved (out of %d images checked)\n' \
    "$OK_COUNT" "$FAIL_COUNT" "$KNOWN_COUNT" "$UNEXPECTED_COUNT" "${#IMAGE_IDS[@]}"

if (( FAIL_COUNT > 0 || UNEXPECTED_COUNT > 0 )); then
    exit 1
fi
exit 0
