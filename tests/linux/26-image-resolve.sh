# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── image_resolve (Task 4B) ────────────────────────────────────────────────
# Bridges catalog/images.json's index+file-regex entries to a concrete
# download URL (the gap that left --create-usb unable to actually fetch an
# image). Every test here serves its fixtures through a real loopback HTTP
# server (tests/helpers/image_index_fixtures/) rather than a file:// URL:
# image_resolve joins relative hrefs against the page's own URL itself, and
# that join has to see a real directory-shaped base URL the same way a live
# index server would hand one back - see _start_test_http_server's own
# comment above for why file:// does not stand in for that on this suite's
# Windows/Git-Bash hosts. No test here reaches the real internet.
describe "image_resolve"

read -r _img_srv_pid _img_srv_port < <(_start_test_http_server "$ROOT/tests/helpers/image_index_fixtures")
_img_cache="$(mktemp -d)"

# _image_resolve_test_catalog <id> <fixture-subdir> <file-regex> [sums] [sig] [leaf]
# Writes a one-entry scratch images.json pointing `index` at this test run's
# loopback fixture server, and echoes its path. AUTOOS_ROOT/catalog/images.json
# itself is never touched (constraint: catalog/*.json stays off limits) -
# image_resolve's optional second argument exists for exactly this. <leaf>
# mirrors the catalog's optional `leaf` field (fedora-workstation: the ISO
# sits three directories below the chosen version dir) - omitted, it is left
# out of the entry entirely so every existing caller above is unaffected.
_image_resolve_test_catalog() {
    local id="$1" subdir="$2" file_re="$3" sums="${4:-SHA256SUMS}" sig="${5:--}" leaf="${6:-}"
    local f; f="$(mktemp)"
    python3 - "$f" "$id" "http://127.0.0.1:${_img_srv_port}/${subdir}/" "$file_re" "$sums" "$sig" "$leaf" <<'PY'
import json, sys
f, id_, index, file_re, sums, sig, leaf = sys.argv[1:8]
entry = {"id": id_, "index": index, "file": file_re, "sums": sums, "sig": sig}
if leaf:
    entry["leaf"] = leaf
with open(f, "w", encoding="utf-8") as fh:
    json.dump({"images": [entry]}, fh)
PY
    echo "$f"
}

if it "image_resolve: an LTS entry picks the current LTS release, never an interim one"; then
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_releases \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/ubuntu_releases/26.04.1"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"file=ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"sums=${base}/SHA256SUMS"* && "$out" == *"sig=${base}/SHA256SUMS.gpg"* \
        && "$out" != *"25.10"* && "$out" != *"24.04.5"* && "$out" != *"22.04.5"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: Debian's current/ symlink shape resolves with no directory recursion"; then
    cat_json="$(_image_resolve_test_catalog debian-netinst-stable debian_iso_cd \
        'debian-[0-9]+\.[0-9]+\.[0-9]+-amd64-netinst\.iso$' SHA256SUMS SHA256SUMS.sign)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve debian-netinst-stable "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/debian_iso_cd"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/debian-13.1.0-amd64-netinst.iso"* \
        && "$out" == *"file=debian-13.1.0-amd64-netinst.iso"* \
        && "$out" == *"sums=${base}/SHA256SUMS"* && "$out" == *"sig=${base}/SHA256SUMS.sign"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a pattern matching nothing fails loudly and non-zero, never guesses"; then
    cat_json="$(_image_resolve_test_catalog nothing-here no_match 'nonexistent-[0-9]+\.iso$' - -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve nothing-here "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"matched nothing"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: a pattern matching several files that differ by more than a version is an error, never a silent first-match"; then
    # Same version, different arch - a regex broader than its author meant.
    # No ordering rule applies here, unlike the point-release case below.
    cat_json="$(_image_resolve_test_catalog ambiguous multiple_match \
        'debian-[0-9]+\.[0-9]+\.[0-9]+-(amd64|arm64)-netinst\.iso$' - -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ambiguous "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"matched multiple"* \
        && "$out" == *"debian-13.2.0-amd64-netinst.iso"* && "$out" == *"debian-13.2.0-arm64-netinst.iso"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a point release listed beside its .0 release in one directory resolves to the highest version (live 26.04.1 shape)"; then
    # Found on the first real run, 2026-09-17: releases.ubuntu.com/26.04.1/
    # lists ubuntu-26.04-desktop-amd64.iso AND ubuntu-26.04.1-desktop-amd64.iso.
    # Names identical except for one dotted version number are the same
    # artefact at different point releases - the highest wins. Missing
    # components compare as 0, so 26.04 < 26.04.1.
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_point_release \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/ubuntu_point_release"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"file=ubuntu-26.04.1-desktop-amd64.iso"* && "$out" == *"sums=${base}/SHA256SUMS"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: an -lts id with only interim directories present fails loudly instead of picking one"; then
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_only_interim \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no LTS release directory"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: a fedora-shaped entry resolves via 'leaf' to the ISO and CHECKSUM three directories below the chosen version dir, picking the highest of 43/44 (image_resolve)"; then
    # Real upstream shape (verified 2026-09-18): releases/ lists bare-integer
    # version dirs (44/, no dot - VERSION_DIR must accept that), and the ISO
    # is three levels below the chosen one (Workstation/x86_64/iso/) - `leaf`
    # is what lets a single recursion step land there directly.
    cat_json="$(_image_resolve_test_catalog fedora-workstation fedora_releases \
        'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' \
        'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' - 'Workstation/x86_64/iso/')"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve fedora-workstation "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/fedora_releases/44/Workstation/x86_64/iso"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/Fedora-Workstation-Live-44-1.7.x86_64.iso"* \
        && "$out" == *"file=Fedora-Workstation-Live-44-1.7.x86_64.iso"* \
        && "$out" == *"sums=${base}/Fedora-Workstation-44-1.7-x86_64-CHECKSUM"* \
        && "$out" != *"/43/"* && "$out" != *"/test/"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a bare-integer version directory (Fedora's '44/') is never LTS-eligible - it has no .04, so it must be ineligible (image_resolve)"; then
    cat_json="$(_image_resolve_test_catalog fedora-workstation-lts fedora_releases \
        'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' \
        'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve fedora-workstation-lts "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no LTS release directory"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: custom-url is a specific refusal, not a crash"; then
    out="$(image_resolve custom-url "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"UI affordance"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

if it "image_resolve: custom-local is a specific refusal, not a crash"; then
    out="$(image_resolve custom-local "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"UI affordance"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

if it "image_resolve: an unknown image id is a clear error, not a crash"; then
    out="$(image_resolve totally-not-a-real-image-id "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no catalog entry"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

kill "$_img_srv_pid" 2>/dev/null
rm -rf "$_img_cache"

