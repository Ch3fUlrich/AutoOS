# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── usb_fetch_image: the resolver-to-downloader bridge ─────────────────────
# image_resolve (tested above, against loopback index fixtures) and
# fetch_verified (tested above, against loopback files) were each done;
# nothing joined them, so --create-usb could never download the image it
# planned to write. Every test here serves a scratch release directory over
# _start_test_http_server - a tiny text file standing in for the ISO, plus
# a real SHA256SUMS computed from it - and points image_resolve at it
# through a scratch AUTOOS_ROOT whose catalog/images.json is the only file
# in it. No test here reaches the real internet or the real catalog. Every
# test name carries "usb_fetch_image" and "usb" so `--filter fetch` and
# `--filter usb` both reach them (lesson L2: filters match test names).
describe "usb fetch"

# _usb_fetch_fixture <good|bad|missing>
# Builds the scratch release dir and prints its path. <good> lists the
# image's real digest, <bad> lists an all-zero one, <missing> lists a
# different filename entirely.
_usb_fetch_fixture() {
    local mode="$1" rel sum
    rel="$(mktemp -d)"
    mkdir -p "$rel/1.0"
    printf 'AUTOOS TEST IMAGE - not a real ISO\n' >"$rel/1.0/testos-1.0-amd64.iso"
    sum="$(sha256sum "$rel/1.0/testos-1.0-amd64.iso" | awk '{print $1}')"
    case "$mode" in
        good)    printf '%s *testos-1.0-amd64.iso\n' "$sum" >"$rel/1.0/SHA256SUMS" ;;
        bad)     printf '%064d *testos-1.0-amd64.iso\n' 0 >"$rel/1.0/SHA256SUMS" ;;
        missing) printf '%s *something-else.iso\n' "$sum" >"$rel/1.0/SHA256SUMS" ;;
    esac
    cat >"$rel/1.0/index.html" <<'HTML'
<html><body><pre><a href="../">../</a>
<a href="testos-1.0-amd64.iso">testos-1.0-amd64.iso</a>
<a href="SHA256SUMS">SHA256SUMS</a>
</pre></body></html>
HTML
    printf '%s\n' "$rel"
}

# _usb_fetch_root <port> [sums-field]
# Writes a scratch AUTOOS_ROOT holding only catalog/images.json, with one
# entry whose index is the loopback server's 1.0/ directory. Prints the
# root. The image entry carries every field the real catalog schema has so
# usb_plan and usb_fetch_image read it exactly like a shipped entry.
_usb_fetch_root() {
    local port="$1" sums="${2:-SHA256SUMS}" mirrors="${3:-}" root
    root="$(mktemp -d)"
    mkdir -p "$root/catalog"
    python3 - "$root/catalog/images.json" "http://127.0.0.1:${port}/1.0/" "$sums" "$mirrors" <<'PY'
import json, sys
path, index, sums, mirrors = sys.argv[1:5]
entry = {"id": "testos", "name": "Test OS", "homepage": "http://127.0.0.1/",
         "index": index, "file": r"testos-[0-9.]+-amd64\.iso$", "sums": sums, "sig": "-", "key": "-",
         "kinds": ["installer"], "writeMode": "hybrid", "sizeGb": 0.001}
if mirrors:
    entry["mirrors"] = [m for m in mirrors.split(",") if m]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"images": [entry]}, fh)
PY
    printf '%s\n' "$root"
}

# _usb_fetch_mirror_of <fixture-dir> <good|corrupt>
# A second release dir with the same 1.0/ layout, served as a "mirror":
# an exact copy, or one whose image bytes differ from what the canonical
# manifest publishes. Prints its path.
_usb_fetch_mirror_of() {
    local src="$1" mode="$2" m
    m="$(mktemp -d)"
    cp -r "$src/1.0" "$m/1.0"
    if [[ "$mode" == corrupt ]]; then
        printf 'CORRUPT MIRROR COPY\n' >"$m/1.0/testos-1.0-amd64.iso"
    fi
    printf '%s\n' "$m"
}

if it "_usb_sums_digest_for reads coreutils, starred and BSD-style manifests and ignores non-SHA-256 lines (usb_fetch_image)"; then
    m="$(mktemp)"
    good="$(printf '%064d' 7)"
    cat >"$m" <<EOF
d41d8cd98f00b204e9800998ecf8427e *plain.iso
$(printf '%064d' 1)  plain.iso
$(printf '%064d' 2) *starred.iso
$(printf '%064d' 3)  ./dotslash.iso
SHA256 (bsd.iso) = $(printf '%064d' 4)
EOF
    r1="$(_usb_sums_digest_for "$m" plain.iso)"
    r2="$(_usb_sums_digest_for "$m" starred.iso)"
    r3="$(_usb_sums_digest_for "$m" dotslash.iso)"
    r4="$(_usb_sums_digest_for "$m" bsd.iso)"
    r5="$(_usb_sums_digest_for "$m" absent.iso)"
    if [[ "$r1" == "$(printf '%064d' 1)" && "$r2" == "$(printf '%064d' 2)" && "$r3" == "$(printf '%064d' 3)" \
          && "$r4" == "$(printf '%064d' 4)" && -z "$r5" ]]; then pass
    else fail "plain=$r1 starred=$r2 dotslash=$r3 bsd=$r4 absent=[$r5] (good=$good)"; fi
    rm -f "$m"
fi

if it "usb_fetch_image downloads the image, verifies it against the served SHA256SUMS and keeps the manifest beside it (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"verified image"* && -s "$cache/testos.iso" && -s "$cache/testos.iso.sums" ]] \
        && cmp -s "$cache/testos.iso" "$rel/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a second run is a cache hit reported as skipped, never a refetch (usb_fetch_image idempotent)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" >/dev/null 2>&1
    out2="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out2" == *"skipped"* && "$out2" == *"testos.iso already downloaded"* ]]; then pass
    else fail "rc=$rc out=$out2"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a checksum mismatch is refused and leaves nothing at the destination (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture bad)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"checksum mismatch"* && ! -e "$cache/testos.iso" && ! -e "$cache/testos.iso.part" ]]; then pass
    else fail "rc=$rc iso=$( [[ -e "$cache/testos.iso" ]] && echo EXISTS || echo absent ) out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a manifest that does not list the image is refused before any image download (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture missing)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"does not list"* && ! -e "$cache/testos.iso" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a catalog entry with no checksum manifest is refused with nothing fetched (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port" -)"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no checksum manifest"* && ! -e "$cache/testos.iso" && ! -e "$cache/testos.iso.sums" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a healthy mirror serves the image before the canonical source, verified against the canonical manifest (usb_fetch_image mirror)"; then
    # The canonical copy is deliberately corrupted AFTER its manifest was
    # written: if the canonical source were tried first, the digest check
    # would fail. Success therefore proves the mirror served the bytes.
    rel="$(_usb_fetch_fixture good)"
    mir="$(_usb_fetch_mirror_of "$rel" good)"
    printf 'CANONICAL COPY NOW CORRUPT\n' >"$rel/1.0/testos-1.0-amd64.iso"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    read -r mir_pid mir_port < <(_start_test_http_server "$mir")
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:${mir_port}/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"from http://127.0.0.1:${mir_port}/1.0/testos-1.0-amd64.iso"* \
          && "$out" == *"verified image"* ]] && cmp -s "$cache/testos.iso" "$mir/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" "$mir_pid" 2>/dev/null; wait "$srv_pid" "$mir_pid" 2>/dev/null
    rm -rf "$rel" "$mir" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: an unreachable mirror is skipped with a warning and the canonical source used (usb_fetch_image mirror)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    # Port 1 on loopback: nothing listens there, so curl fails fast.
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:1/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"http://127.0.0.1:1/1.0/testos-1.0-amd64.iso failed"* \
          && "$out" == *"trying the next source"* && "$out" == *"verified image"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a mirror serving bytes that do not match the manifest is skipped, the canonical copy wins (usb_fetch_image mirror)"; then
    rel="$(_usb_fetch_fixture good)"
    mir="$(_usb_fetch_mirror_of "$rel" corrupt)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    read -r mir_pid mir_port < <(_start_test_http_server "$mir")
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:${mir_port}/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"rc=2"* && "$out" == *"trying the next source"* ]] \
        && cmp -s "$cache/testos.iso" "$rel/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" "$mir_pid" 2>/dev/null; wait "$srv_pid" "$mir_pid" 2>/dev/null
    rm -rf "$rel" "$mir" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: creates a cache directory that does not exist yet, as on a machine's first run (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache/images" usb_fetch_image testos "$cache/brand/new/dir/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && -s "$cache/brand/new/dir/testos.iso" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: custom-local without its source and digest is a specific refusal, not a fetch attempt (usb_fetch_image)"; then
    # custom-local / custom-url are real images now (see "usb custom image"),
    # but only on the 4-argument line usb_plan emits for them. Called the
    # catalog way - no source, no digest - it must refuse by name and touch
    # nothing, never fall through to image_resolve and the network.
    cache="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$cache" usb_fetch_image custom-local "$cache/x.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"needs its source and digest"* && -z "$(find "$cache" -mindepth 1)" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$cache"
fi

if it "usb_fetch_image: a dry run says what it would download and touches nothing (usb_fetch_image)"; then
    cache="$(mktemp -d)"
    out="$(AUTOOS_DRY_RUN=1 AUTOOS_CACHE_DIR="$cache" usb_fetch_image ubuntu-desktop-lts "$cache/x.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"would resolve ubuntu-desktop-lts"* && -z "$(find "$cache" -mindepth 1)" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$cache"
fi

if it "usb_execute: dispatches a usb_fetch_image plan line into the real download path (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_fetch_image testos $cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && -s "$cache/testos.iso" && "$out" == *"Ready to boot: /dev/sdb"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_execute: a failure in the fetch step says the device was not touched, never 'rewrite from wipefs' (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"usb_fetch_image ubuntu-desktop-lts /tmp/x.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"not touched"* && "$out" != *"wipefs"* && "$out" != *"Ready to boot"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb_execute: a failure in a write step still reports no-rollback, rewrite from wipefs (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 AUTOOS_TRACE=1 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"wipefs"* && "$out" != *"not touched"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb_execute: a usb_fetch_image line with the wrong word count is refused, never run (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_fetch_image ubuntu-desktop-lts" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"malformed usb_fetch_image step"* ]] && pass || fail "rc=$rc out=$out"
fi

