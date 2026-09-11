#!/usr/bin/env bash
# AutoOS USB device enumeration and write-safety guard for Linux.
#
# Task 5 of plan 2026-09-11-installer-usb-and-rescue-profile: this is the
# module where a bug destroys someone's data — usb_guard() below is the only
# thing standing between the installer-USB writer and a live workstation
# disk. It is written and tested accordingly: the root-filesystem check runs
# first (AGENTS.md-style defence in depth — the most dangerous mistake gets
# the shortest path to refusal), every refusal names its reason in words a
# human can act on, and every code path is exercised against synthetic
# `lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE` fixtures
# (AUTOOS_FAKE_LSBLK) rather than the live machine (AGENTS.md §5) — this
# module must never enumerate or touch a real disk during the test suite.
#
# Finding A10: `RM` (the kernel's "removable media" flag) is not a reliable
# USB signal — a USB SSD, especially anything behind a UAS/UASP bridge,
# commonly reports RM=0 ("fixed") even though it is plainly a USB stick that
# should be offered. The bus (`TRAN=usb`) is the signal that actually holds;
# _is_removable_or_usb() below accepts either RM=1 or TRAN=usb, never RM
# alone. usb_list()'s candidate filter uses the same rule so the picker never
# hides a device the guard would otherwise accept.
#
# shellcheck shell=bash
#
# Sourced alongside lib/linux/ui.sh (ui_err) and lib/linux/detect.sh
# (has_cmd) — like every other lib/linux/*.sh module, this file is never
# executed standalone and deliberately carries no top-level
# `set -e`/`set -u`: sourcing a script that sets shell options changes them
# for whoever sourced it too (setup.sh, tests/run-tests.sh). Every function
# below checks its own exit codes explicitly, quotes every expansion and
# uses `local` throughout.

# AUTOOS_SUDO is normally exported by lib/linux/detect.sh (empty when already
# root, "sudo" otherwise); default it here too so this file behaves when
# sourced on its own, e.g. by a test. Nothing in this file calls sudo
# directly — enumeration is read-only, and usb_require_elevation() only ever
# inspects AUTOOS_SUDO/uid, never invokes it.
AUTOOS_SUDO="${AUTOOS_SUDO:-}"

# _lsblk_json
# Prints `lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE` output, or
# the fixture captured in AUTOOS_FAKE_LSBLK when the caller has set it. Tests
# always set AUTOOS_FAKE_LSBLK (AGENTS.md §5) — this is the only place in the
# module that would otherwise touch the live machine's block devices.
_lsblk_json() {
    if [[ -n "${AUTOOS_FAKE_LSBLK:-}" ]]; then
        printf '%s\n' "$AUTOOS_FAKE_LSBLK"
        return 0
    fi
    if ! has_cmd lsblk; then
        ui_err "usb: lsblk not found"
        return 1
    fi
    lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE 2>/dev/null
}

# _usb_py <subcommand> [args...]
# Reads lsblk -J JSON on stdin and answers one structural question. All
# parsing lives in one place, in python3, rather than hand-rolled JSON
# scraping in awk/sed — the same choice detect.sh and imagecache.sh already
# make for structured data. Tolerant of both JSON encodings real lsblk has
# shipped: older util-linux quotes every column ("rm":"0"), newer versions
# emit native types ("rm":false, "size":256060514304) — a fixture or a live
# lsblk on either version parses the same way.
_usb_py() {
    if ! has_cmd python3; then
        ui_err "usb: python3 is required to parse lsblk output but was not found"
        return 1
    fi
    # The script is read into a variable first, and handed to python3 via
    # `-c`, deliberately NOT `python3 - <<'PY'`: that form reads the script
    # itself off stdin, which — piped as `_lsblk_json | _usb_py ...` always
    # is — consumes the lsblk JSON this function exists to parse before
    # json.load(sys.stdin) ever runs. `cat <<'PY'` below owns its own stdin
    # (the heredoc); the actual pipeline's stdin reaches python3 untouched.
    local _usb_py_src
    _usb_py_src="$(cat <<'PY'
import json, sys


def norm_rm(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if v is None:
        return "0"
    return "1" if str(v).strip().lower() in ("1", "true", "yes") else "0"


def norm_str(v):
    return "" if v is None else str(v)


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return 0


def load_devices():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return []
    return data.get("blockdevices") or []


def flatten(devs, parent=None):
    # Yields one row per device/partition: (name, model, size, rm, tran,
    # mountpoint, type, top_level_disk_name). Recurses into "children" so a
    # partition's row always carries the disk it belongs to, however deep
    # lsblk nests it (e.g. LVM/dm layers), without the caller re-deriving it.
    for d in devs:
        name = norm_str(d.get("name"))
        top = parent or name
        yield (
            name, norm_str(d.get("model")), to_int(d.get("size")),
            norm_rm(d.get("rm")), norm_str(d.get("tran")).lower(),
            norm_str(d.get("mountpoint")), norm_str(d.get("type")), top,
        )
        for child in (d.get("children") or []):
            for row in flatten([child], top):
                yield row


cmd = sys.argv[1] if len(sys.argv) > 1 else ""
rows = list(flatten(load_devices()))

if cmd == "list":
    # Candidate devices for the picker: same RM-or-TRAN=usb rule usb_guard
    # uses (finding A10) so the list never hides what the guard would accept.
    for name, model, size, rm, tran, mp, typ, top in rows:
        if typ != "disk":
            continue
        if rm == "1" or tran == "usb":
            print(f"/dev/{name}\t{model}\t{size}\t{rm}\t{tran}")

elif cmd == "disk_of_mountpoint":
    target = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, top in rows:
        if mp == target:
            print(f"/dev/{top}")
            break

elif cmd == "disk_info":
    # rm, tran and the WHOLE DISK's own size for the named top-level disk —
    # usb_guard operates on /dev/sdX, never on a single partition.
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, top in rows:
        if typ == "disk" and name == want:
            print(f"{rm}\t{tran}\t{size}")
            break

elif cmd == "has_mounted_partition":
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    found = False
    for name, model, size, rm, tran, mp, typ, top in rows:
        if top == want and typ != "disk" and mp:
            found = True
            break
    print("1" if found else "0")
PY
)"
    # MSYS_NO_PATHCONV=1: same Windows Git Bash trap as the shellcheck-via-
    # docker fallback in tests/run-tests.sh — MSYS auto-rewrites a bare "/"
    # argument (the root mountpoint usb_guard's caller passes) into a
    # Windows host path before a native, non-MSYS python3.exe ever sees it,
    # silently breaking the root-disk lookup. A no-op everywhere else
    # (WSL2/Linux, where this suite normally runs per AGENTS.md §5).
    MSYS_NO_PATHCONV=1 python3 -c "$_usb_py_src" "$@"
}

# _disk_of_mountpoint <mountpoint>
# Prints /dev/<disk> for the disk that owns <mountpoint>, or nothing if no
# device is mounted there. Used to find the root disk (checked FIRST by
# usb_guard, below) — the mountpoint match happens on the partition, the
# printed device is always the top-level disk that partition belongs to.
_disk_of_mountpoint() {
    local mp="$1"
    _lsblk_json | _usb_py disk_of_mountpoint "$mp"
}

# _is_removable_or_usb <device>
# Finding A10: accepts RM=1 OR TRAN=usb — RM alone misses USB SSDs (see file
# header). <device> is a whole-disk path like /dev/sdb; basename strips the
# /dev/ prefix to match lsblk's bare NAME field.
_is_removable_or_usb() {
    local dev="$1" name info rm tran
    name="$(basename -- "$dev")"
    info="$(_lsblk_json | _usb_py disk_info "$name")"
    [[ -z "$info" ]] && return 1
    IFS=$'\t' read -r rm tran _ <<<"$info"
    [[ "$rm" == "1" || "$tran" == "usb" ]]
}

# _has_mounted_partition <device>
# True if any partition under <device> currently has a non-empty mountpoint.
# Deliberately does not care WHERE it is mounted (unlike the root-disk check,
# which cares specifically about "/") — any mounted partition on the target
# disk must be unmounted before a write, or the write corrupts a live mount.
_has_mounted_partition() {
    local dev="$1" name result
    name="$(basename -- "$dev")"
    result="$(_lsblk_json | _usb_py has_mounted_partition "$name")"
    [[ "$result" == "1" ]]
}

# _size_bytes <device>
# Prints the whole disk's size in bytes, or 0 if the device is not present
# in the current (real or faked) lsblk output.
_size_bytes() {
    local dev="$1" name info size
    name="$(basename -- "$dev")"
    info="$(_lsblk_json | _usb_py disk_info "$name")"
    IFS=$'\t' read -r _ _ size <<<"$info"
    printf '%s\n' "${size:-0}"
}

# usb_list
# TSV of USB write-candidate devices: device<TAB>model<TAB>size_bytes<TAB>
# removable<TAB>bus, one line per top-level disk that is either RM=1 or on
# the USB bus (finding A10 — see _usb_py's "list" branch). Never touches the
# live machine when AUTOOS_FAKE_LSBLK is set (AGENTS.md §5).
usb_list() {
    _lsblk_json | _usb_py list
}

# usb_guard <device>
# 0 if <device> is safe to write an installer/rescue image to, 1 with a
# human-actionable reason on stderr otherwise. Checks run in this exact
# order, most dangerous first, so the worst mistake — writing over the disk
# holding "/" — has the shortest possible path to refusal:
#
#   1. root disk        — never overwrite the disk this process itself is
#                          running from, whatever else is true about it.
#   2. removable/USB bus — refuse anything that is not plausibly a USB
#                          stick (finding A10: bus type, not RM alone).
#   3. mounted partition — refuse a target with something mounted; writing
#                          under a live mount corrupts it.
#   4. size              — refuse a stick too small for the image, when the
#                          caller has told us how big the image is
#                          (AUTOOS_IMAGE_BYTES; unset means "unknown", never
#                          a refusal).
usb_guard() {
    local dev="$1" root_disk size

    if [[ -z "$dev" ]]; then
        ui_err "usb_guard: no device given"
        return 1
    fi

    root_disk="$(_disk_of_mountpoint /)"
    if [[ -n "$root_disk" && "$dev" == "$root_disk" ]]; then
        ui_err "$dev holds the root filesystem"
        return 1
    fi

    if ! _is_removable_or_usb "$dev"; then
        ui_err "$dev is not removable and not on the USB bus"
        return 1
    fi

    if _has_mounted_partition "$dev"; then
        ui_err "$dev has a mounted partition — unmount it first"
        return 1
    fi

    size="$(_size_bytes "$dev")"
    if (( size < ${AUTOOS_IMAGE_BYTES:-0} )); then
        ui_err "$dev is too small for this image"
        return 1
    fi

    return 0
}

# usb_require_elevation (finding B10)
# 0 when this process can perform a privileged write (already root, or
# AUTOOS_SUDO names a usable sudo — the same decision lib/linux/detect.sh's
# detect_system() already makes once, mirrored here rather than re-derived).
# 1 with a named reason on stderr otherwise. AUTOOS_FAKE_UID lets tests
# substitute the effective uid without touching the real session
# (AGENTS.md §5) — this file never calls `id -u` unconditionally elsewhere.
#
# Deliberately never self-elevates (e.g. re-exec under sudo): a re-launch
# starts a fresh process with a fresh AUTOOS_LOG, discarding the very log a
# user needs to diagnose a failed write. It only ever reports whether the
# CURRENT process is already privileged enough.
usb_require_elevation() {
    local uid
    uid="${AUTOOS_FAKE_UID:-$(id -u)}"

    if [[ "$uid" == "0" ]]; then
        return 0
    fi
    if [[ -n "${AUTOOS_SUDO:-}" ]]; then
        return 0
    fi

    ui_err "usb_require_elevation: not root and sudo is unavailable — re-run this script with sudo"
    return 1
}
