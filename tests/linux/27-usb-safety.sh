# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── USB device enumeration and the safety guard ────────────────────────────
# Task 5 of plan 2026-09-11-installer-usb-and-rescue-profile: usb_guard() is
# the only thing standing between the installer-USB writer and a live
# workstation disk, so this block gets the most tests and no shortcuts. Every
# fixture is synthetic (tests/helpers/fake_usb.py via AUTOOS_FAKE_LSBLK) —
# this suite must never enumerate the real machine's disks (AGENTS.md §5).
# Every test name below carries "usb" so `--filter usb` actually reaches it —
# a filter that matches zero tests reports a clean run indistinguishable
# from a real pass, which bit two earlier tasks in this same plan.
describe "usb safety"

if it "usb_guard refuses the disk holding the root filesystem"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" usb_guard /dev/sda 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"root filesystem"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a non-removable internal disk"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb internal_nvme)" usb_guard /dev/nvme0n1 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not removable"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a disk with a mounted partition"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_mounted)" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"mounted"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a stick smaller than the image"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb tiny_stick)" \
           AUTOOS_IMAGE_BYTES=8000000000 usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"too small"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard accepts a real removable usb stick that is big enough"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_IMAGE_BYTES=4000000000 \
        usb_guard /dev/sdb && pass || fail "rejected a valid target"
fi

if it "usb_list still offers a USB SSD reporting as fixed (A10)"; then
    # A10: DriveType/RM alone misses USB SSDs (commonly behind a UAS/UASP
    # bridge, reporting RM=0/"fixed"). Bus type is the signal.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_ssd_fixed)" usb_list)"
    assert_contains "$out" "/dev/sdb"
fi

if it "usb_guard refuses when device discovery itself fails, rather than treating it as an empty list (F8)"; then
    # Finding F8: lsblk output that fails to parse used to become an empty
    # device list, which made root_disk resolve to nothing and skipped the
    # root-disk check entirely - it only failed safe by accident (the bus
    # check also saw an empty list). Malformed AUTOOS_FAKE_LSBLK stands in
    # for "lsblk itself failed/produced garbage" without needing a real
    # broken lsblk.
    out="$(AUTOOS_FAKE_LSBLK="not valid json" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"discovery failed"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a USB-hosted /boot/efi regardless of bus (F2-prime)"; then
    # Finding F2': reachable whenever the machine booted from USB - the
    # bus/removable check alone accepts this (it IS a real USB device), so
    # the mountpoint itself must be refused, not just the bus type.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_boot_efi_mounted)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"/boot/efi"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_require_elevation refuses to plan a write without root (B10)"; then
    out="$(AUTOOS_SUDO="" AUTOOS_FAKE_UID=1000 usb_require_elevation 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"sudo"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_require_elevation is satisfied when already root (B10)"; then
    AUTOOS_SUDO="" AUTOOS_FAKE_UID=0 usb_require_elevation && pass || fail "refused root"
fi

# ─── Engine-aware guard modes (Task 6, B16) ─────────────────────────────────
# The real-hardware bug that started Task 6: Assert-AutoOSUsbSafe/usb_guard
# correctly refused every internal disk, then refused the legitimate USB
# stick too, because the only mode it knew was "must be unmounted" — wrong
# for uefi-copy, which writes onto a partition that is ALREADY mounted.
# usb_guard's <mode> parameter is what fixes this; every branch gets its
# own fixture (tests/helpers/fake_usb.py) and its own test here.
if it "usb_guard (mounted-fat32-writable mode) accepts an already-mounted writable FAT32 target"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" \
        usb_guard /dev/sdb mounted-fat32-writable && pass || fail "rejected a valid uefi-copy target"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a target with nothing mounted"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"no mounted partition"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a mounted partition that is not FAT32"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_wrong_fs_mounted)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not FAT32"* && "$out" == *"ntfs"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a read-only FAT32 partition"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_readonly)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"read-only"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard still defaults to unmounted mode when none is given (B16 backward compat)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_mounted)" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"mounted"* ]] && pass || fail "rc=$rc: $out"
fi

