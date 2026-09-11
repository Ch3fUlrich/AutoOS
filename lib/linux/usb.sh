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
# Task 6 adds the write planner, usb_plan(): it turns (image, kind, engine,
# device) into the exact command lines a real write would run, checks every
# compatibility/platform/lock/safety question a write would need first, and
# runs nothing itself — see its own docstring below for the full contract.
#
# shellcheck shell=bash
#
# Sourced alongside lib/linux/ui.sh (ui_err), lib/linux/detect.sh (has_cmd)
# and, since Task 6, lib/linux/download.sh (download_cache_dir, which
# usb_run_lock_path and usb_plan both read) — like every other
# lib/linux/*.sh module, this file is never executed standalone and
# deliberately carries no top-level `set -e`/`set -u`: sourcing a script
# that sets shell options changes them for whoever sourced it too
# (setup.sh, tests/run-tests.sh). Every function below checks its own exit
# codes explicitly, quotes every expansion and uses `local` throughout.

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
    # FSTYPE and RO were added for Task 6's engine-aware guard (uefi-copy's
    # "mounted-fat32-writable" mode needs to know the mounted partition's
    # filesystem and whether it is write-protected); every existing fixture
    # and code path that only ever read the original columns is unaffected
    # since _usb_py below defaults both to empty/"0" when absent.
    lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE,FSTYPE,RO 2>/dev/null
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
    # mountpoint, type, fstype, ro, top_level_disk_name). Recurses into
    # "children" so a partition's row always carries the disk it belongs
    # to, however deep lsblk nests it (e.g. LVM/dm layers), without the
    # caller re-deriving it. fstype/ro default to ""/"0" when the source
    # JSON omits them (every Task 5 fixture does — they predate Task 6's
    # engine-aware guard) so old fixtures keep parsing unchanged.
    for d in devs:
        name = norm_str(d.get("name"))
        top = parent or name
        yield (
            name, norm_str(d.get("model")), to_int(d.get("size")),
            norm_rm(d.get("rm")), norm_str(d.get("tran")).lower(),
            norm_str(d.get("mountpoint")), norm_str(d.get("type")),
            norm_str(d.get("fstype")).lower(), norm_rm(d.get("ro")), top,
        )
        for child in (d.get("children") or []):
            for row in flatten([child], top):
                yield row


cmd = sys.argv[1] if len(sys.argv) > 1 else ""
rows = list(flatten(load_devices()))

if cmd == "list":
    # Candidate devices for the picker: same RM-or-TRAN=usb rule usb_guard
    # uses (finding A10) so the list never hides what the guard would accept.
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if typ != "disk":
            continue
        if rm == "1" or tran == "usb":
            print(f"/dev/{name}\t{model}\t{size}\t{rm}\t{tran}")

elif cmd == "disk_of_mountpoint":
    target = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if mp == target:
            print(f"/dev/{top}")
            break

elif cmd == "disk_info":
    # rm, tran and the WHOLE DISK's own size for the named top-level disk —
    # usb_guard operates on /dev/sdX, never on a single partition.
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if typ == "disk" and name == want:
            print(f"{rm}\t{tran}\t{size}")
            break

elif cmd == "has_mounted_partition":
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    found = False
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if top == want and typ != "disk" and mp:
            found = True
            break
    print("1" if found else "0")

elif cmd == "partitions_of":
    # Task 7: every partition under a top-level disk, largest first once the
    # bash caller sorts this - what usb_copy_image needs to find "the
    # partition Ventoy2Disk.sh just created" when nothing is mounted yet
    # (the ventoy path; the uefi-copy path already has one mounted and uses
    # mounted_info below instead).
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if top == want and typ != "disk":
            print(f"{name}\t{size}\t{fstype}\t{ro}\t{mp}")

elif cmd == "mounted_info":
    # The first mounted partition under the named top-level disk: its
    # mountpoint, filesystem type and read-only flag — what usb_guard's
    # "mounted-fat32-writable" mode (Task 6, B16/uefi-copy) checks against.
    # Empty output means "nothing mounted on this disk".
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    for name, model, size, rm, tran, mp, typ, fstype, ro, top in rows:
        if top == want and typ != "disk" and mp:
            print(f"{mp}\t{fstype}\t{ro}")
            break
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

# _mounted_partition_info <device>
# Prints "mountpoint<TAB>fstype<TAB>ro" for the first mounted partition on
# <device>, or nothing if none is mounted. Task 6: the uefi-copy engine's
# guard mode needs the filesystem type and the read-only flag, neither of
# which _has_mounted_partition above ever needed to answer.
_mounted_partition_info() {
    local dev="$1" name
    name="$(basename -- "$dev")"
    _lsblk_json | _usb_py mounted_info "$name"
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

# usb_guard <device> [mode]
# 0 if <device> is safe to write an installer/rescue image to, 1 with a
# human-actionable reason on stderr otherwise. Checks run in this exact
# order, most dangerous first, so the worst mistake — writing over the disk
# holding "/" — has the shortest possible path to refusal:
#
#   1. root disk        — never overwrite the disk this process itself is
#                          running from, whatever else is true about it.
#   2. removable/USB bus — refuse anything that is not plausibly a USB
#                          stick (finding A10: bus type, not RM alone).
#   3. mount state       — engine-aware (Task 6): <mode> decides what
#                          "safe" means here (see below).
#   4. size              — refuse a stick too small for the image, when the
#                          caller has told us how big the image is
#                          (AUTOOS_IMAGE_BYTES; unset means "unknown", never
#                          a refusal).
#
# <mode> (default "unmounted"):
#   unmounted               — refuse a target with anything mounted;
#                              writing under a live mount corrupts it. What
#                              every raw/block-writing engine needs
#                              (ventoy, native, wsl) — they own the whole
#                              device.
#   mounted-fat32-writable   — the opposite requirement, for the uefi-copy
#                              engine (plan B16): it copies files onto a
#                              partition the caller already formatted and
#                              mounted, so it REFUSES when nothing is
#                              mounted, when the mounted filesystem is not
#                              FAT32, or when it is read-only. This mode
#                              lives here, not as separate logic in the
#                              planner, so "is this device safe for this
#                              engine" has exactly one owner (the human
#                              partner's finding that started Task 6: the
#                              old unmounted-only guard refused the one
#                              engine that needs a mount).
#
# A device the picker filtered as a plain block target (e.g. straight from
# usb_list) never has an fstype/ro of its own to check for
# "mounted-fat32-writable" — that mode always inspects the device's
# partitions, never the whole-disk row.
usb_guard() {
    local dev="$1" mode="${2:-unmounted}" root_disk size

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

    case "$mode" in
        unmounted)
            if _has_mounted_partition "$dev"; then
                ui_err "$dev has a mounted partition — unmount it first"
                return 1
            fi
            ;;
        mounted-fat32-writable)
            local info mp fstype ro
            info="$(_mounted_partition_info "$dev")"
            if [[ -z "$info" ]]; then
                ui_err "$dev has no mounted partition — mount a FAT32 partition on it first (uefi-copy writes onto an existing mounted filesystem, not the raw device)"
                return 1
            fi
            IFS=$'\t' read -r mp fstype ro <<<"$info"
            if [[ "$fstype" != "vfat" ]]; then
                ui_err "$dev's mounted partition is '${fstype:-unknown}', not FAT32 — uefi-copy requires an existing FAT32 partition"
                return 1
            fi
            if [[ "$ro" == "1" ]]; then
                ui_err "$dev's mounted partition ($mp) is read-only — uefi-copy needs to write to it"
                return 1
            fi
            ;;
        *)
            ui_err "usb_guard: unknown mode '$mode'"
            return 1
            ;;
    esac

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

# ─── The write planner (Task 6) ─────────────────────────────────────────────
# usb_plan (below) turns (image, kind, engine, device) into the exact
# command lines a real write would run — nothing more. It is the piece that
# makes `--dry-run` on this feature provable: every check an actual write
# would need (does this engine even build this kind, does it accept this
# image's writeMode, does it run on this machine, is a write already
# happening, is the device itself safe for this engine) all happen here,
# and NONE of them touch the device, the network or the filesystem beyond
# reading catalog/*.json and (via usb_guard) the live block-device table.
# Task 7's usb_execute is the only thing that ever runs a line this
# function prints.
#
# Sourced alongside lib/linux/download.sh (download_cache_dir) in addition
# to ui.sh/detect.sh — setup.sh's sourcing order was extended for this.

# _usb_root_dir
# Repo root to resolve catalog/*.json against: $AUTOOS_ROOT when the caller
# (setup.sh) has set it, else the current directory — which is exactly
# where tests/run-tests.sh's own `cd "$ROOT"` before sourcing this file
# leaves it. Never hardcoded, so this file works both ways without knowing
# which one is calling it.
_usb_root_dir() {
    if [[ -n "${AUTOOS_ROOT:-}" ]]; then
        printf '%s\n' "$AUTOOS_ROOT"
    else
        pwd
    fi
}

# _usb_current_os / _usb_current_arch
# The platform/arch usb_plan gates engines against. Deliberately NOT a call
# to detect_system(): that function unconditionally overwrites SYS_OS/
# SYS_ARCH from the live `uname`, which is exactly what B11/B13's tests
# need to bypass to simulate macOS and arm64 without a second machine
# (AGENTS.md §5) — they export SYS_OS/SYS_ARCH before invoking setup.sh,
# and setup.sh's own --create-usb handling skips detect_system() when
# SYS_OS is already set for the same reason. Reading the two globals here
# rather than re-deriving them means production (where detect_system() DID
# run first and set them for real) and tests (where it did not) both work
# through the one code path.
_usb_current_os() {
    if [[ -n "${SYS_OS:-}" ]]; then
        printf '%s\n' "$SYS_OS"
        return 0
    fi
    if [[ "$(uname -s)" == "Darwin" ]]; then printf 'macos\n'; else printf 'linux\n'; fi
}

_usb_current_arch() {
    if [[ -n "${SYS_ARCH:-}" ]]; then
        printf '%s\n' "$SYS_ARCH"
        return 0
    fi
    case "$(uname -m)" in
        x86_64|amd64)  printf 'x64\n' ;;
        aarch64|arm64) printf 'arm64\n' ;;
        *)             uname -m ;;
    esac
}

# _usb_catalog_py <file> <array-key> <subcommand> [args...]
# One tiny reader for both catalog/images.json ("images") and
# catalog/engines.json ("engines") — same JSON-in-python choice _usb_py
# above documents (structured data belongs in a real parser, not awk/sed),
# and the same file for both rather than one script per catalog, since the
# only difference between them is which top-level array key to read and
# what fields the caller asks for.
_usb_catalog_py() {
    local file="$1" key="$2" cmd="$3"; shift 3
    if ! has_cmd python3; then
        ui_err "usb: python3 is required to read $file but was not found"
        return 1
    fi
    local _catalog_py_src
    _catalog_py_src="$(cat <<'PY'
import json, sys

path, key, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]

try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as exc:
    print(f"usb: cannot read {path}: {exc}", file=sys.stderr)
    sys.exit(2)

entries = data.get(key) or []


def joined(v):
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    if isinstance(v, bool):
        return "1" if v else "0"
    return "" if v is None else str(v)


if cmd == "get":
    # One field per output LINE, not tab-joined on one: a bash `read`
    # sourced from a process substitution that produces NO output (unknown
    # id) fails (nothing to read), and a bare failing `read` would trip
    # `set -e` in any caller that has it active (setup.sh does) — `mapfile`
    # does not share that failure mode (an empty read is just an empty
    # array), which is why every caller here uses it instead of `read`.
    want = args[0] if args else ""
    match = next((e for e in entries if e.get("id") == want), None)
    if match is None:
        sys.exit(1)
    for field in args[1:]:
        print(joined(match.get(field)))

elif cmd == "list":
    # ids whose platforms/arch admit <platform>/<arch> — an absent or empty
    # platforms/arch list means "any", the same convention catalog
    # components use (AGENTS.md: "omit to mean any").
    platform = args[0] if len(args) > 0 else ""
    arch = args[1] if len(args) > 1 else ""
    for e in entries:
        plats = e.get("platforms") or []
        archs = e.get("arch") or []
        if plats and platform not in plats:
            continue
        if archs and arch not in archs:
            continue
        print(e.get("id", ""))
PY
)"
    # No MSYS_NO_PATHCONV here (unlike _usb_py above): that variable exists
    # to stop Windows Git Bash rewriting a bare "/" argument into a host
    # path, which _usb_py's root-mountpoint lookup does not want. <file>
    # here is the opposite case — a real MSYS-style catalog path that DOES
    # need Git Bash's normal auto-conversion to reach a native python3.exe.
    #
    # `tr -d '\r'`: the same trap lib/linux/imagecache.sh's
    # imagecache_packages() already documents — a python3 invoked from a
    # native Windows install (this repo is tested from Git Bash as well as
    # WSL2/Linux, per AGENTS.md §5) writes CRLF line endings to a text-mode
    # stdout even inside a Unix-style shell. Invisible in any single printed
    # line, but it broke every `mapfile`-read field's equality/substring
    # comparison in usb_plan — "x64" != "x64\r" — silently, only on that
    # platform. A no-op everywhere else.
    python3 -c "$_catalog_py_src" "$file" "$key" "$cmd" "$@" | tr -d '\r'
}

# _image_field <image_id> <field...>
# One tab-free line per requested field of catalog/images.json's entry
# <image_id> (each field value itself never contains a tab or newline in
# this catalog), in the order asked — empty output (rc 1) for an unknown
# id. Read with `mapfile` by callers that want several fields at once.
_image_field() {
    local image_id="$1"; shift
    _usb_catalog_py "$(_usb_root_dir)/catalog/images.json" images get "$image_id" "$@"
}

# _engine_field <engine_id> <field...> — the catalog/engines.json mirror.
_engine_field() {
    local engine_id="$1"; shift
    _usb_catalog_py "$(_usb_root_dir)/catalog/engines.json" engines get "$engine_id" "$@"
}

# _engine_list_for_platform <platform> <arch>
# ids of every engine catalog/engines.json offers on <platform>/<arch>, one
# per line — an engine whose platforms/arch exclude the caller's machine
# never appears (AGENTS.md §3: hidden, not shown-and-failing). This is what
# `--list-engines` prints and what keeps Ventoy off the list on arm64.
_engine_list_for_platform() {
    local platform="$1" arch="$2"
    _usb_catalog_py "$(_usb_root_dir)/catalog/engines.json" engines list "$platform" "$arch"
}

# usb_run_lock_path
# Where the shared cross-process run lock lives — the download cache's
# parent directory (same P6 root download_cache_dir uses), so a
# --serve-driven package install and a terminal --create-usb agree on one
# location without either having to know about the other's code (B13:
# "USB creation joins that same lock, not a second one").
usb_run_lock_path() {
    printf '%s/run.lock\n' "$(dirname -- "$(download_cache_dir)")"
}

# usb_run_in_progress
# True (rc 0) when another AutoOS run currently holds the shared run lock,
# so the caller must refuse rather than start a second one (B13: a write
# requested while an install runs returns 409/refused, and so does a
# second write). AUTOOS_FAKE_RUN_ACTIVE lets tests simulate either state
# without a real second process or lock file (AGENTS.md §5) — set to "1"
# for "in progress", anything else (including unset) for "idle".
# Deliberately a plain existence check, not flock: taking/releasing the
# lock is Task 7/serve.py's job (whoever actually starts a run); this
# function only ever reads it.
usb_run_in_progress() {
    if [[ -n "${AUTOOS_FAKE_RUN_ACTIVE:-}" ]]; then
        [[ "$AUTOOS_FAKE_RUN_ACTIVE" == "1" ]]
        return
    fi
    [[ -e "$(usb_run_lock_path)" ]]
}

# usb_plan <image_id> <kind> <engine> <device>
# Prints the exact command lines a write would run, one per line, on
# stdout, and returns 0 — or leaves a human-actionable reason via ui_err
# (Task 5's convention) and returns non-zero. Never runs a command itself.
#
# Checks run data-only first, most general first, exactly so an
# incompatible (image, kind, engine) triple is refused before this
# function ever asks about the live machine — no AUTOOS_FAKE_LSBLK fixture
# is needed to prove "ventoy cannot build a full-os image", and none is
# given to this function's own tests for that case:
#   1. image/engine exist in their catalogs
#   2. engine builds this <kind> at all
#   3. image actually offers this <kind>
#   4. engine can write this image's writeMode (never raw onto ventoy — A11)
#   5. engine runs on this platform/arch (hidden engines refuse loudly if
#      asked for by name directly, same as an unknown catalog id would)
#   6. an interactive engine (rufus) is unavailable under --dry-run — B12
#   7. no other run is already in progress (B13) — shared with serve.py's
#      RUN/LOCK via usb_run_in_progress
#   8. usb_guard, in the mode this engine requires (B16)
#
# Elevation (B10) is deliberately NOT checked here: it is a property of who
# can run the emitted commands, not of whether the plan itself is
# coherent, and Task 7's usb_execute test calls usb_plan directly, outside
# any elevated session, expecting a full plan back. setup.sh's
# --create-usb handling checks it separately, after the plan is built.
usb_plan() {
    local image_id="$1" kind="$2" engine="$3" dev="$4"

    if [[ -z "$image_id" || -z "$kind" || -z "$engine" || -z "$dev" ]]; then
        ui_err "usb_plan: usage: usb_plan <image_id> <kind> <engine> <device>"
        return 1
    fi

    local img_name img_kinds img_write_mode img_size_gb
    local -a _img_fields=()
    mapfile -t _img_fields < <(_image_field "$image_id" name kinds writeMode sizeGb)
    if [[ ${#_img_fields[@]} -eq 0 ]]; then
        ui_err "usb_plan: unknown image '$image_id'"
        return 1
    fi
    img_name="${_img_fields[0]:-}"; img_kinds="${_img_fields[1]:-}"
    img_write_mode="${_img_fields[2]:-}"; img_size_gb="${_img_fields[3]:-0}"

    local eng_name eng_platforms eng_arch eng_kinds eng_write_modes eng_interactive
    local -a _eng_fields=()
    mapfile -t _eng_fields < <(_engine_field "$engine" name platforms arch kinds writeModes interactive)
    if [[ ${#_eng_fields[@]} -eq 0 ]]; then
        ui_err "usb_plan: unknown engine '$engine'"
        return 1
    fi
    eng_name="${_eng_fields[0]:-}"; eng_platforms="${_eng_fields[1]:-}"
    eng_arch="${_eng_fields[2]:-}"; eng_kinds="${_eng_fields[3]:-}"
    eng_write_modes="${_eng_fields[4]:-}"; eng_interactive="${_eng_fields[5]:-0}"

    if [[ ",$eng_kinds," != *",$kind,"* ]]; then
        ui_err "engine '$engine' ($eng_name) cannot build a '$kind' image — it builds: ${eng_kinds//,/, }"
        return 1
    fi
    if [[ ",$img_kinds," != *",$kind,"* ]]; then
        ui_err "usb_plan: image '$image_id' ($img_name) does not offer kind '$kind' — it offers: ${img_kinds//,/, }"
        return 1
    fi
    if [[ ",$eng_write_modes," != *",$img_write_mode,"* ]]; then
        ui_err "engine '$engine' cannot write a '$img_write_mode' image ('$image_id') — it writes: ${eng_write_modes//,/, }"
        return 1
    fi

    local cur_os cur_arch
    cur_os="$(_usb_current_os)"; cur_arch="$(_usb_current_arch)"
    if [[ -n "$eng_platforms" && ",$eng_platforms," != *",$cur_os,"* ]]; then
        ui_err "engine '$engine' is not available on $cur_os"
        return 1
    fi
    if [[ -n "$eng_arch" && ",$eng_arch," != *",$cur_arch,"* ]]; then
        ui_err "engine '$engine' is not available on $cur_arch"
        return 1
    fi

    if [[ "$eng_interactive" == "1" ]] && (( AUTOOS_DRY_RUN )); then
        ui_err "engine '$engine' is interactive and unavailable under --dry-run"
        return 1
    fi

    if usb_run_in_progress; then
        ui_err "usb_plan: a run is already in progress — try again once it finishes"
        return 1
    fi

    local guard_mode="unmounted"
    [[ "$engine" == "uefi-copy" ]] && guard_mode="mounted-fat32-writable"

    local image_bytes
    image_bytes="$(awk -v g="$img_size_gb" 'BEGIN{printf "%.0f", (g == "" ? 0 : g) * 1000000000}')"
    AUTOOS_IMAGE_BYTES="$image_bytes" usb_guard "$dev" "$guard_mode" || return 1

    local local_path
    local_path="$(download_cache_dir)/${image_id}.iso"

    case "$engine" in
        ventoy)
            # Ventoy is a two-step engine: install the boot manager onto the
            # raw device once, then copy the verified image on as a plain
            # file (never a raw write — A11). usb_copy_image is Task 7's
            # function (lib/linux/usb.sh); this line names it, it does not
            # call it — usb_plan runs nothing.
            printf 'Ventoy2Disk.sh -i -g %s\n' "$dev"
            printf 'usb_copy_image %s %s\n' "$dev" "$local_path"
            ;;
        uefi-copy)
            printf 'usb_copy_image %s %s\n' "$dev" "$local_path"
            ;;
        native)
            # Task 7/B14: dd's own status=progress emits bytes-and-rate, never
            # a percentage, so lib/linux/process.py's `NN%` regex would
            # silently degrade to "percentage unavailable" for every raw
            # write. usb_write_raw (this file, below) is dd wrapped with its
            # own computed percentage - the same function name the Windows
            # sibling (New-AutoOSUsbPlan) already emits here, so both
            # platforms' native plans name the real thing that runs. Passing
            # $image_bytes (already computed above for the guard) lets
            # usb_write_raw compute NN% without re-deriving the image size.
            printf 'usb_write_raw %s %s %s\n' "$dev" "$local_path" "$image_bytes"
            ;;
        wsl)
            if [[ "$kind" == "full-os" ]]; then
                printf 'wsl.exe --import AutoOSRescue %s %s\n' "$dev" "$local_path"
            else
                printf 'wsl.exe -e dd if=%s of=%s bs=4M status=progress conv=fsync\n' "$local_path" "$dev"
            fi
            ;;
        rufus)
            printf 'rufus.exe -i %s\n' "$local_path"
            ;;
        *)
            ui_err "usb_plan: no write plan defined for engine '$engine'"
            return 1
            ;;
    esac
}

# ─── The write executor (Task 7) ────────────────────────────────────────────
# usb_execute (below) is the only thing in this file that ever runs a line
# usb_plan prints. Everything above this point only ever reads catalog/*.json
# and the live block-device table; everything below actually writes.
#
# FAT32's per-file ceiling (finding B16): one byte short of 4 GiB. Ubuntu
# 26.04.1's largest inner file (casper/minimal.squashfs, 3,432,136,704 B) is
# safely under it - that headroom is what makes uefi-copy work at all, and
# it must be re-checked per image, never assumed.
_USB_FAT32_MAX_FILE_BYTES=4294967295

# _usb_file_size <path>
# Prints a file's size in bytes. GNU stat (-c) and BSD/macOS stat (-f) take
# different flags for the same thing; wc -c is the last-resort fallback.
_usb_file_size() {
    local f="$1"
    [[ -f "$f" ]] || { printf '0\n'; return 1; }
    stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || wc -c <"$f"
}

# _usb_device_present <device>
# True if <device> still enumerates as a usb_list() candidate. Shared by
# _usb_verify_readback (B15: "reads back" means "still there") and
# _usb_report_write_failure (the "unplug mid-write" finding: this is exactly
# how that check tells "the stick vanished" apart from "the write itself
# failed").
_usb_device_present() {
    local dev="$1"
    usb_list | awk -F'\t' -v d="$dev" '$1==d{found=1} END{exit !found}'
}

# _usb_verify_readback <device>
# B15: "Ready to boot" is earned by the filesystem reading back, not by an
# exit code alone (that already proved insufficient once in this plan -
# finding A7, an HTML mirror page saved under an ISO's name with exit 0).
# What this can honestly check from userspace, without mounting anything a
# human is about to unplug and boot from, is that the device still
# enumerates and lsblk can still read a partition table off it - the same
# enumeration usb_guard() and usb_list() already trust elsewhere in this
# file. A deeper check (mount every partition, diff file contents against
# the source) is exactly the class of thing Step 5 defers to the human
# hardware run, not something this function fakes confidence about.
_usb_verify_readback() {
    _usb_device_present "$1"
}

# _usb_report_write_failure <device>
# The "unplug mid-write" finding: dd and Ventoy both surface a vanished USB
# stick as a plain I/O error, indistinguishable from a genuine write fault
# until something re-checks enumeration. usb_execute calls this once, after
# a step has already failed, so the message a human sees names the actual
# cause instead of whatever low-level errno the failed step happened to
# print. Never retries automatically: a re-plugged stick can enumerate under
# a different device name, and blindly retrying THIS name would then write
# to whatever now holds it.
_usb_report_write_failure() {
    local dev="$1"
    if _usb_device_present "$dev"; then
        ui_err "usb_execute: write to $dev failed. There is no rollback (B15) - the stick must be rewritten from wipefs onward; do not retry automatically."
    else
        ui_err "usb_execute: $dev is no longer present - the stick was removed during the write. Re-seat it and start over from wipefs; do not retry blindly, a re-plugged stick can enumerate under a different device name."
    fi
}

# _usb_dispatch_step <device> <line>
# Turns one plan line into the real action. Most lines ARE the literal
# thing that runs (usb_copy_image, usb_write_raw are already real function
# names usb_plan prints); one is not: usb_plan deliberately keeps
# "Ventoy2Disk.sh -i -g <dev>" literal in its output (the existing "usb
# planning" tests assert that exact string appears under --dry-run, and a
# human previewing a plan should see the real tool that will run, not an
# internal function name) even though the binary it names is not actually
# on PATH until usb_write_ventoy has fetched, verified and extracted it.
# This is the one place that gap is bridged.
_usb_dispatch_step() {
    local dev="$1" line="$2"
    case "$line" in
        "Ventoy2Disk.sh "*) usb_write_ventoy "$dev" ;;
        *) eval "$line" ;;
    esac
}

# _usb_run_step <device> <line>
# One executed plan step. Every AUTOOS_* testing knob usb_execute's own
# tests depend on lives HERE, in one place, so usb_execute's loop stays a
# plain "run each line, stop at the first failure":
#   AUTOOS_TRACE=1      print "TRACE <line>" before anything else happens -
#                        the plan-order proof Task 7's own tests assert on.
#                        Fires even under a forced failure or a dry run: a
#                        traced dry run must still trace every line.
#   AUTOOS_FORCE_FAIL=1 fail this step without running it - lets a test
#                        exercise the "never claim success on failure" path
#                        (B15) without a real failing command.
#   AUTOOS_DRY_RUN=1    succeed without running it - the same "would run"
#                        contract every other AutoOS installer honours
#                        (install.sh's run()), reimplemented locally because
#                        a plan line here is sometimes a real function call
#                        (usb_copy_image), not always an external command.
_usb_run_step() {
    local dev="$1" line="$2"

    if [[ "${AUTOOS_TRACE:-0}" == "1" ]]; then
        printf 'TRACE %s\n' "$line"
    fi
    if [[ "${AUTOOS_FORCE_FAIL:-0}" == "1" ]]; then
        ui_err "usb_execute: forced failure (AUTOOS_FORCE_FAIL) before: $line"
        return 1
    fi
    if (( ${AUTOOS_DRY_RUN:-0} )); then
        return 0
    fi
    _usb_dispatch_step "$dev" "$line"
}

# usb_execute <device>
# Reads the plan (usb_plan's output, Task 6) on stdin, one command per line,
# and runs each in order. Returns 0 only when every step exited 0 AND the
# resulting filesystem reads back (B15) - never on exit code alone.
usb_execute() {
    local dev="$1"
    if [[ -z "$dev" ]]; then
        ui_err "usb_execute: no device given"
        return 1
    fi

    local -a steps=()
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        steps+=("$line")
    done

    if [[ ${#steps[@]} -eq 0 ]]; then
        ui_err "usb_execute: empty plan on stdin - nothing to run"
        return 1
    fi

    local step
    for step in "${steps[@]}"; do
        if ! _usb_run_step "$dev" "$step"; then
            _usb_report_write_failure "$dev"
            return 1
        fi
    done

    if ! _usb_verify_readback "$dev"; then
        ui_err "usb_execute: $dev did not read back after the write - treat this stick as unbootable and rewrite it; a retry is safe, it always starts from wipefs (B15)."
        return 1
    fi

    ui_ok "Ready to boot: $dev"
    return 0
}

# ─── usb_write_ventoy ────────────────────────────────────────────────────────

# _usb_ventoy_cache_dir
# Where the Ventoy tool itself (not an image) is cached - a sibling of the
# image cache (download_cache_dir), never the same directory, since this
# holds an extracted tool tree rather than a single verified file.
_usb_ventoy_cache_dir() {
    printf '%s/tools/ventoy\n' "$(dirname -- "$(download_cache_dir)")"
}

# _usb_ventoy_binary <cache_dir>
# Prints the path to an already-extracted Ventoy2Disk.sh under <cache_dir>,
# or nothing if none is there yet. Step 6's idempotency: a cache hit here
# skips _usb_ventoy_fetch (and its network calls) entirely on a second run.
_usb_ventoy_binary() {
    local cache_dir="$1"
    find "$cache_dir" -maxdepth 2 -name 'Ventoy2Disk.sh' -type f 2>/dev/null | head -1
}

# _usb_ventoy_release_fields <release_json>
# Prints "<tarball_name>\t<tarball_url>\t<sha256_txt_url>" from a GitHub
# releases/latest JSON document. python3, like every other structured-data
# read in this file (_usb_py, _usb_catalog_py above) - piped on stdin, not
# passed as an argv string, so a large JSON body never risks an argv-length
# or quoting problem.
#
# `tr -d '\r'`: the same trap _usb_catalog_py above already documents - a
# python3 invoked from a native Windows install (this repo is tested from
# Git Bash as well as WSL2/Linux, per AGENTS.md SS5) writes CRLF line endings
# to a text-mode stdout even inside a Unix-style shell. Invisible on the
# tarball name/url (never the LAST field), but it silently appended \r to
# sha_url - curl then requested "...sha256.txt\r" and got nothing back, so
# every checksum lookup failed with "no published sha256 found" even though
# the release JSON and the sha256.txt content were both correct. A no-op
# everywhere else.
_usb_ventoy_release_fields() {
    python3 -c '
import json, sys

data = json.load(sys.stdin)
assets = data.get("assets") or []
tarball = next((a for a in assets if a.get("name", "").endswith("-linux.tar.gz")), None)
sha = next((a for a in assets if a.get("name") == "sha256.txt"), None)

name = tarball.get("name", "") if tarball else ""
url = tarball.get("browser_download_url", "") if tarball else ""
sha_url = sha.get("browser_download_url", "") if sha else ""
print(f"{name}\t{url}\t{sha_url}")
' <<<"$1" | tr -d '\r'
}

# _usb_ventoy_fetch <cache_dir>
# Resolves Ventoy's latest GitHub release, downloads the Linux tarball
# through fetch_verified (Task 3) against the sha256 published in that same
# release's sha256.txt asset (never a hardcoded checksum, never a pinned
# version - AGENTS.md hard rule 2: no vendor binary is ever committed to
# this repository, it is always fetched and verified at run time), then
# extracts it. AUTOOS_FAKE_VENTOY_RELEASE substitutes the "ask GitHub" step
# with test-supplied JSON (AGENTS.md SS5: this file must never depend on
# live network access to be exercised); its asset URLs can point at local
# file:// paths, the same trick the "verified download" tests already use
# for fetch_verified itself.
_usb_ventoy_fetch() {
    local cache_dir="$1"
    mkdir -p "$cache_dir" || { ui_err "usb_write_ventoy: cannot create $cache_dir"; return 1; }

    local release_json
    if [[ -n "${AUTOOS_FAKE_VENTOY_RELEASE:-}" ]]; then
        release_json="$AUTOOS_FAKE_VENTOY_RELEASE"
    elif has_cmd curl; then
        release_json="$(curl -fsSL https://api.github.com/repos/ventoy/Ventoy/releases/latest)" || {
            ui_err "usb_write_ventoy: could not reach GitHub for Ventoy's latest release"
            return 1
        }
    else
        ui_err "usb_write_ventoy: curl not found - cannot fetch Ventoy"
        return 1
    fi

    local tarball_name tarball_url sha_url
    IFS=$'\t' read -r tarball_name tarball_url sha_url < <(_usb_ventoy_release_fields "$release_json")
    if [[ -z "$tarball_name" || -z "$tarball_url" ]]; then
        ui_err "usb_write_ventoy: no linux tarball asset in the latest Ventoy release"
        return 1
    fi

    local want=""
    if [[ -n "$sha_url" ]]; then
        want="$(curl -fsSL "$sha_url" 2>/dev/null | awk -v f="$tarball_name" '$2==f{print $1; exit}')"
    fi
    if [[ -z "$want" ]]; then
        ui_err "usb_write_ventoy: no published sha256 found for $tarball_name - refusing to install an unverified Ventoy"
        return 1
    fi

    local dest="$cache_dir/$tarball_name"
    fetch_verified "$tarball_url" "$dest" "$want" - - || {
        ui_err "usb_write_ventoy: download/verification of $tarball_name failed"
        return 1
    }

    tar -xzf "$dest" -C "$cache_dir" || {
        ui_err "usb_write_ventoy: could not extract $tarball_name"
        return 1
    }
}

# usb_write_ventoy <device>
# Ensures the Ventoy tool is present and verified, then runs
# Ventoy2Disk.sh -i -g <device> - the partitioning step usb_plan's ventoy
# branch names directly in its own output. usb_execute's dispatcher
# (_usb_dispatch_step above) is what routes that plan line here, because the
# binary it names is not on PATH until this function has fetched it.
usb_write_ventoy() {
    local dev="$1"
    if [[ -z "$dev" ]]; then
        ui_err "usb_write_ventoy: no device given"
        return 1
    fi

    if (( ${AUTOOS_DRY_RUN:-0} )); then
        ui_muted "would run: Ventoy2Disk.sh -i -g $dev"
        return 0
    fi

    local cache_dir bin
    cache_dir="$(_usb_ventoy_cache_dir)"
    bin="$(_usb_ventoy_binary "$cache_dir")"
    if [[ -z "$bin" ]]; then
        _usb_ventoy_fetch "$cache_dir" || return 1
        bin="$(_usb_ventoy_binary "$cache_dir")"
    fi
    if [[ -z "$bin" ]]; then
        ui_err "usb_write_ventoy: Ventoy2Disk.sh not found even after fetching Ventoy"
        return 1
    fi
    chmod +x "$bin" 2>/dev/null

    ui_muted "run: $bin -i -g $dev"
    $AUTOOS_SUDO "$bin" -i -g "$dev"
}

# ─── usb_write_raw ───────────────────────────────────────────────────────────

# _usb_raw_progress_reader <total_bytes>
# Reads dd's stderr (status=progress lines, one roughly per second, shaped
# like "1234567890 bytes (1.2 GB, 1.1 GiB) copied, 5 s, 246 MB/s") on stdin
# and prints "NN%" once per distinct percentage reached. Split out of
# usb_write_raw so it can be the read side of a progress fifo running
# concurrently with dd.
_usb_raw_progress_reader() {
    local total="$1" line bytes pct last=-1
    while IFS= read -r line; do
        bytes="${line%% *}"
        [[ "$bytes" =~ ^[0-9]+$ ]] || continue
        [[ "$total" -gt 0 ]] || continue
        pct=$(( bytes * 100 / total ))
        (( pct > 100 )) && pct=100
        if (( pct != last )); then
            printf '%d%%\n' "$pct"
            last=$pct
        fi
    done
}

# usb_write_raw <device> <image_path> <image_bytes>
# B14: `dd status=progress` prints bytes-and-rate but never a percentage, so
# lib/linux/process.py's existing `(100|\d{1,2})\s*%` regex silently
# degrades to "percentage unavailable" for every raw write. This function
# does not hand dd straight to that runner - it reads dd's own stderr and
# computes NN% from the byte counter against the known <image_bytes>,
# printing exactly the lines process.py already knows how to parse. dd's
# real output is not discarded, it drives this function's percentage
# instead of being handed to a regex that cannot find one in it.
usb_write_raw() {
    local dev="$1" image="$2" total="${3:-0}"

    if [[ -z "$dev" || -z "$image" ]]; then
        ui_err "usb_write_raw: usage: usb_write_raw <device> <image_path> <image_bytes>"
        return 1
    fi

    if (( ${AUTOOS_DRY_RUN:-0} )); then
        ui_muted "would run: dd if=$image of=$dev bs=4M status=progress conv=fsync"
        return 0
    fi
    if [[ ! -f "$image" ]]; then
        ui_err "usb_write_raw: image not found: $image"
        return 1
    fi
    if [[ -z "$total" || "$total" -le 0 ]]; then
        total="$(_usb_file_size "$image")"
    fi

    ui_muted "run: dd if=$image of=$dev bs=4M status=progress conv=fsync"

    local fifo
    fifo="$(mktemp -u)"
    if ! mkfifo "$fifo" 2>/dev/null; then
        ui_err "usb_write_raw: cannot create a progress pipe"
        return 1
    fi

    ( _usb_raw_progress_reader "$total" <"$fifo" ) &
    local reader_pid=$!

    $AUTOOS_SUDO dd if="$image" of="$dev" bs=4M status=progress conv=fsync 2>"$fifo"
    local rc=$?

    wait "$reader_pid" 2>/dev/null
    rm -f "$fifo"
    return "$rc"
}

# ─── usb_copy_image ──────────────────────────────────────────────────────────

# _usb_copy_target_mount <device>
# Prints the mountpoint of <device>'s already-mounted partition (the
# uefi-copy case - usb_guard already ran in mounted-fat32-writable mode, so
# this is always populated by the time usb_copy_image needs it) and returns
# 0, or prints nothing and returns 0 when nothing is mounted yet (the ventoy
# case - the caller mounts the data partition itself, below), or returns 1
# with a named reason when what IS mounted cannot be written to.
#
# Finding A11: an ISO9660 filesystem here means this stick was written with
# a raw block copy and is read-only in every OS - refuses immediately,
# defense-in-depth alongside usb_guard's own fstype check for the uefi-copy
# path, and the only check standing between a bare usb_copy_image call (the
# ventoy path never goes through usb_guard's mounted-fat32-writable mode)
# and a confusing mid-copy failure.
#
# ui_err's own printf writes to stdout (like every ui_* helper in this
# codebase - see lib/linux/ui.sh), which is exactly what THIS function's own
# stdout also carries as its return value. Every ui_err call below is
# explicitly redirected to stderr (`>&2`) so a caller doing
# `mnt="$(_usb_copy_target_mount "$dev")"` gets only the mountpoint on
# failure-free paths and never has a refusal MESSAGE silently swallowed
# into that variable instead of reaching whoever is watching stderr.
_usb_copy_target_mount() {
    local dev="$1" info mp fstype ro
    info="$(_mounted_partition_info "$dev")"
    if [[ -z "$info" ]]; then
        return 0
    fi
    IFS=$'\t' read -r mp fstype ro <<<"$info"
    if [[ "$fstype" == "iso9660" ]]; then
        ui_err "usb_copy_image: $dev's mounted partition is ISO9660 - this stick was written with a raw block copy (finding A11) and is read-only. It must be rewritten (wipefs) before this engine can use it." >&2
        return 1
    fi
    if [[ "$ro" == "1" ]]; then
        ui_err "usb_copy_image: $dev's mounted partition ($mp) is read-only" >&2
        return 1
    fi
    printf '%s\n' "$mp"
}

# _usb_copy_mount_data_partition <device>
# The ventoy path: Ventoy2Disk.sh has just partitioned <device> but mounted
# nothing. Mounts the larger of its partitions (Ventoy's data partition;
# the small VTOYEFI partition is always the smaller one) at a fresh
# temporary directory and prints that path. Needs root - the ventoy engine
# already requires elevation for the partitioning step above this one, so
# nothing about uefi-copy's "no elevation at all" promise is affected.
#
# Same stdout-carries-the-return-value constraint as _usb_copy_target_mount
# above: every ui_err call below is redirected to stderr (`>&2`) so it never
# gets captured into a caller's `mnt="$(...)"` instead of being seen.
_usb_copy_mount_data_partition() {
    local dev="$1" name line part fstype mnt
    name="$(basename -- "$dev")"
    line="$(_lsblk_json | _usb_py partitions_of "$name" | sort -t $'\t' -k2,2rn | head -1)"
    if [[ -z "$line" ]]; then
        ui_err "usb_copy_image: $dev has no partitions to write onto - did Ventoy2Disk.sh run first?" >&2
        return 1
    fi
    IFS=$'\t' read -r part _ fstype _ _ <<<"$line"
    if [[ "$fstype" == "iso9660" ]]; then
        ui_err "usb_copy_image: $dev's largest partition is ISO9660 - this stick was written with a raw block copy (finding A11) and is read-only. It must be rewritten (wipefs) before ventoy can use it." >&2
        return 1
    fi

    mnt="$(mktemp -d)" || { ui_err "usb_copy_image: cannot create a mount point" >&2; return 1; }
    if ! $AUTOOS_SUDO mount "/dev/$part" "$mnt" 2>/dev/null; then
        ui_err "usb_copy_image: could not mount /dev/$part at $mnt" >&2
        rmdir "$mnt" 2>/dev/null
        return 1
    fi
    printf '%s\n' "$mnt"
}

# _usb_copy_onto <image_path> <mountpoint> [extra_file...]
# Mounts <image_path> read-only (a loop mount - the source is a plain ISO
# file, never the physical device), refuses before copying anything if any
# file inside it exceeds FAT32's 4 GiB ceiling (B16 - "check before copying,
# refuse naming the offending file, rather than failing 20 minutes in"),
# then copies its contents onto <mountpoint> followed by every extra file
# (Task 9's rescue templates) into <mountpoint>/rescue/.
#
# AUTOOS_FAKE_ISO_MAX_FILE_BYTES/_NAME let a test exercise the B16 refusal
# without mounting a real multi-gigabyte ISO (AGENTS.md SS5) - production
# never sets them, so the real mount-and-measure path is what actually runs
# outside a test.
_usb_copy_onto() {
    local image="$1" mnt="$2"; shift 2
    local -a extra_files=("$@")

    if [[ -n "${AUTOOS_FAKE_ISO_MAX_FILE_BYTES:-}" ]]; then
        if (( AUTOOS_FAKE_ISO_MAX_FILE_BYTES > _USB_FAT32_MAX_FILE_BYTES )); then
            ui_err "usb_copy_image: ${AUTOOS_FAKE_ISO_MAX_FILE_NAME:-<unknown file>} is $AUTOOS_FAKE_ISO_MAX_FILE_BYTES bytes - over FAT32's 4 GiB single-file ceiling (finding B16). Refusing before copying anything."
            return 1
        fi
    else
        local src_mnt
        src_mnt="$(mktemp -d)" || { ui_err "usb_copy_image: cannot create a mount point for $image"; return 1; }
        if ! $AUTOOS_SUDO mount -o loop,ro "$image" "$src_mnt" 2>/dev/null; then
            ui_err "usb_copy_image: could not mount $image to inspect and copy it"
            rmdir "$src_mnt" 2>/dev/null
            return 1
        fi

        local biggest big_bytes big_path
        biggest="$(find "$src_mnt" -type f -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -1)"
        IFS=$'\t' read -r big_bytes big_path <<<"$biggest"
        if [[ -n "$big_bytes" ]] && (( big_bytes > _USB_FAT32_MAX_FILE_BYTES )); then
            ui_err "usb_copy_image: ${big_path#"$src_mnt"/} is $big_bytes bytes - over FAT32's 4 GiB single-file ceiling (finding B16). Refusing before copying anything."
            $AUTOOS_SUDO umount "$src_mnt" 2>/dev/null; rmdir "$src_mnt" 2>/dev/null
            return 1
        fi

        if ! cp -a "$src_mnt/." "$mnt/"; then
            ui_err "usb_copy_image: copying $image onto $mnt failed"
            $AUTOOS_SUDO umount "$src_mnt" 2>/dev/null; rmdir "$src_mnt" 2>/dev/null
            return 1
        fi
        $AUTOOS_SUDO umount "$src_mnt" 2>/dev/null
        rmdir "$src_mnt" 2>/dev/null
    fi

    local f
    mkdir -p "$mnt/rescue"
    for f in "${extra_files[@]}"; do
        if [[ ! -f "$f" ]]; then
            ui_warn "usb_copy_image: extra file not found, skipping: $f"
            continue
        fi
        cp -a "$f" "$mnt/rescue/" || { ui_err "usb_copy_image: could not copy $f onto $mnt/rescue/"; return 1; }
    done
}

# usb_copy_image <device> <image_path> [extra_file...]
# Copies a hybrid ISO's contents, plus any extra files (Task 9's rescue
# templates - rescue-bootstrap.sh, ai-clients.conf, ai-dispatcher.sh), onto
# the FAT32/exFAT partition already on <device>. Never the raw device
# itself - that is Ventoy2Disk.sh's job, or usb_write_raw's for a
# writeMode:raw image.
#
# Two callers, two starting states (usb_plan, Task 6):
#   uefi-copy - usb_guard already ran in mounted-fat32-writable mode, so the
#               target partition is already mounted, writable, FAT32. This
#               function never mounts or unmounts anything for that case,
#               matching the engine's documented "no elevation at all"
#               (B16): a mount() syscall for an already-mounted filesystem
#               is not needed.
#   ventoy    - Ventoy2Disk.sh has just partitioned <device> but mounted
#               nothing; this function mounts the data partition itself
#               (needs root - ventoy already requires elevation for the
#               partitioning step above it) and unmounts it again when done.
usb_copy_image() {
    local dev="$1" image="$2"
    local -a extra_files=()
    if [[ $# -gt 2 ]]; then
        shift 2
        extra_files=("$@")
    fi

    if [[ -z "$dev" || -z "$image" ]]; then
        ui_err "usb_copy_image: usage: usb_copy_image <device> <image_path> [extra_file...]"
        return 1
    fi

    if (( ${AUTOOS_DRY_RUN:-0} )); then
        ui_muted "would copy $image (+${#extra_files[@]} extra file(s)) onto $dev"
        return 0
    fi

    local mnt owned_mount=0
    mnt="$(_usb_copy_target_mount "$dev")" || return 1
    if [[ -z "$mnt" ]]; then
        mnt="$(_usb_copy_mount_data_partition "$dev")" || return 1
        owned_mount=1
    fi

    local rc=0
    _usb_copy_onto "$image" "$mnt" "${extra_files[@]}" || rc=1

    if (( owned_mount )); then
        sync 2>/dev/null
        $AUTOOS_SUDO umount "$mnt" 2>/dev/null
        rmdir "$mnt" 2>/dev/null
    fi

    return "$rc"
}

# ─── Ventoy persistence (Task 7 Step 4, B17/B18) ────────────────────────────

# usb_ventoy_add_persistence <device> [size_gb]
# Ventoy's persistence plugin: a .dat file on the data partition, registered
# in ventoy/ventoy.json, gives a live-persistent stick a writable overlay
# without repartitioning. Defaults to 16 GB, capped at half the stick's
# total size so persistence can never claim more room than the image itself
# plus headroom needs.
#
# Not wired into usb_plan's own output: Task 6's ventoy branch prints the
# same two lines regardless of <kind>, and adding a kind-conditional third
# line there is a decision about the already-verified planner's contract
# that this task's given tests never exercise. This function is a
# standalone, independently callable and independently tested capability;
# wiring a kind=live-persistent plan line to call it is left to whichever
# task owns that decision. B17 also flags this as "probe-then-commit, not a
# promise" - confirming persistence actually survives a reboot needs a
# second boot of the real stick, which is exactly the class of check Step 5
# defers to the human hardware run.
usb_ventoy_add_persistence() {
    local dev="$1" size_gb="${2:-16}"

    if [[ -z "$dev" ]]; then
        ui_err "usb_ventoy_add_persistence: no device given"
        return 1
    fi

    local disk_size half_gb
    disk_size="$(_size_bytes "$dev")"
    if [[ "$disk_size" -gt 0 ]]; then
        half_gb=$(( disk_size / 2 / 1000000000 ))
        if (( half_gb > 0 && size_gb > half_gb )); then
            ui_warn "usb_ventoy_add_persistence: ${size_gb}GB would be more than half of $dev - capping at ${half_gb}GB"
            size_gb=$half_gb
        fi
    fi
    if (( size_gb < 1 )); then
        ui_err "usb_ventoy_add_persistence: $dev is too small to fit a persistence file"
        return 1
    fi

    if (( ${AUTOOS_DRY_RUN:-0} )); then
        ui_muted "would create a ${size_gb}GB Ventoy persistence file on $dev"
        return 0
    fi

    local mnt owned_mount=0
    mnt="$(_usb_copy_target_mount "$dev")" || return 1
    if [[ -z "$mnt" ]]; then
        mnt="$(_usb_copy_mount_data_partition "$dev")" || return 1
        owned_mount=1
    fi

    local rc=0
    local dat="$mnt/ventoy/persistence.dat"
    mkdir -p "$mnt/ventoy"
    if ! dd if=/dev/zero of="$dat" bs=1M count="$(( size_gb * 1000 ))" status=none 2>/dev/null; then
        ui_err "usb_ventoy_add_persistence: could not create $dat"
        rc=1
    fi

    if (( rc == 0 )) && ! python3 -c '
import json, sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
data.setdefault("persistence", [])
entry = {"backend": "/ventoy/persistence.dat", "mount": ["/"]}
if entry not in data["persistence"]:
    data["persistence"].append(entry)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
' "$mnt/ventoy/ventoy.json"; then
        ui_err "usb_ventoy_add_persistence: could not register $dat in ventoy.json"
        rc=1
    fi

    if (( owned_mount )); then
        sync 2>/dev/null
        $AUTOOS_SUDO umount "$mnt" 2>/dev/null
        rmdir "$mnt" 2>/dev/null
    fi

    return "$rc"
}
