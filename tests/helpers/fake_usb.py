#!/usr/bin/env python3
"""Synthetic `lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE` fixtures
for the "usb safety" tests in tests/run-tests.sh (plan Task 5).

AGENTS.md §5: usb_guard()/usb_list() (lib/linux/usb.sh) must never be
exercised against the live machine's disks — this is the module where a bug
destroys someone's data. Every fixture below is synthetic; the two shaped
after real hardware ("good_stick", the internal boot disk shared by several
fixtures) use the exact numbers the plan brief measured on the human
partner's machine, never the live disk itself:

    J: -> Disk 5, "Intenso Office Line", BusType USB, IsSystem False,
    IsBoot False, 31,437,766,656 bytes, MBR, FAT32.

Usage: fake_usb.py <fixture-name>   (prints lsblk -J -b -o ... JSON to stdout)
"""
import json
import sys


def _disk(name, model, size, rm, tran, mountpoint=None, children=None):
    d = {
        "name": name,
        "model": model,
        "size": size,
        "rm": rm,
        "tran": tran,
        "mountpoint": mountpoint,
        "type": "disk",
    }
    if children is not None:
        d["children"] = children
    return d


def _part(name, size, rm, tran, mountpoint=None, fstype=None, ro=False):
    return {
        "name": name,
        "model": None,
        "size": size,
        "rm": rm,
        "tran": tran,
        "mountpoint": mountpoint,
        "type": "part",
        "fstype": fstype,
        "ro": ro,
    }


# The internal boot disk present in every fixture below: not removable, not
# USB, with its first partition mounted at "/" — every fixture except
# root_is_sda names a DIFFERENT device than this one to usb_guard, so this
# disk's presence is what proves the guard looks past "some root exists" to
# "is THIS the root disk".
def _boot_disk():
    return _disk(
        "sda", "Boot SSD", 256060514304, False, "nvme",
        children=[_part("sda1", 255000000000, False, None, "/")],
    )


FIXTURES = {
    # The most dangerous case: /dev/sda itself holds the running root
    # filesystem. _disk_of_mountpoint("/") must resolve sda1's mountpoint
    # back to the whole disk, /dev/sda.
    "root_is_sda": lambda: {"blockdevices": [_boot_disk()]},

    # A real internal NVMe, distinct from the boot disk: not removable, not
    # USB. Must be refused even though it does not hold "/".
    "internal_nvme": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("nvme0n1", "WD Black SN850", 1000204886016, False, "nvme"),
    ]},

    # Removable, USB, but a partition is mounted somewhere other than "/" —
    # must refuse until the caller unmounts it.
    "usb_mounted": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "SanDisk Ultra", 32017047552, True, "usb", children=[
            _part("sdb1", 32000000000, True, "usb", "/media/user/SANDISK"),
        ]),
    ]},

    # Removable, USB, unmounted — but smaller than the image the caller asks
    # for (AUTOOS_IMAGE_BYTES in the test).
    "tiny_stick": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Kingston DataTraveler", 4000000000, True, "usb"),
    ]},

    # The real target, measured on the human partner's machine (see module
    # docstring): USB bus, unmounted, big enough for a 4 GB image.
    "good_stick": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Intenso Office Line", 31437766656, True, "usb", children=[
            _part("sdb1", 31400000000, True, "usb", None),
        ]),
    ]},

    # Finding A10: a USB SSD (commonly behind a UAS/UASP bridge) enumerates
    # with RM=0 ("fixed") even though it is on the USB bus. usb_list must
    # still surface it — RM alone is not the signal, TRAN is.
    "usb_ssd_fixed": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Samsung T7 (USB enclosure)", 2000398934016, False, "usb"),
    ]},

    # Task 6 (B16): the uefi-copy engine writes onto an EXISTING mounted
    # FAT32 partition rather than the raw device, so usb_guard's
    # "mounted-fat32-writable" mode wants the opposite of every fixture
    # above — mounted, not unmounted. This is the human partner's real
    # stick shape (see module docstring): FAT32, writable, already mounted.
    "usb_fat32_mounted": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Intenso Office Line", 31437766656, True, "usb", children=[
            _part("sdb1", 31400000000, True, "usb", "/media/user/AUTOOS",
                  fstype="vfat", ro=False),
        ]),
    ]},

    # Same target, but its one mounted partition is NTFS, not FAT32 — the
    # guard must name the actual filesystem so the refusal is actionable.
    "usb_wrong_fs_mounted": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Intenso Office Line", 31437766656, True, "usb", children=[
            _part("sdb1", 31400000000, True, "usb", "/media/user/AUTOOS",
                  fstype="ntfs", ro=False),
        ]),
    ]},

    # FAT32, mounted, but read-only (RO=1) — uefi-copy needs to write to it.
    "usb_fat32_readonly": lambda: {"blockdevices": [
        _boot_disk(),
        _disk("sdb", "Intenso Office Line", 31437766656, True, "usb", children=[
            _part("sdb1", 31400000000, True, "usb", "/media/user/AUTOOS",
                  fstype="vfat", ro=True),
        ]),
    ]},
}


def main(argv):
    if len(argv) != 2 or argv[1] not in FIXTURES:
        names = "|".join(sorted(FIXTURES))
        print(f"usage: fake_usb.py <{names}>", file=sys.stderr)
        return 1
    print(json.dumps(FIXTURES[argv[1]]()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
