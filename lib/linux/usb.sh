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
            printf 'dd if=%s of=%s bs=4M status=progress conv=fsync\n' "$local_path" "$dev"
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
