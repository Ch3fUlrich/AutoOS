#!/usr/bin/env python3
"""Validate catalog/images.json — the OS image catalog (plan Task 4).

Usage: images_validate.py <images.json>

Exit 0 when the catalog is well-formed, 1 otherwise. Prints one problem per
line on stdout, matching lib/linux/catalog.sh's catalog_validate() convention
(tests/run-tests.sh's "catalog schema" block) so both catalogs fail the same
way in test output.

catalog/images.json is deliberately never version-pinned: it describes *how
to find* an image at run time — a release-index URL (`index`) plus a
filename pattern (`file`) — never a specific build. Ubuntu 24.04.1,
Fedora 40, Debian 12.7, SystemRescue 11.01 and Rufus 4.6 were all already
stale in the source scripts this plan replaced (plan finding A9). The
download fields (`index`/`file`, `sums`, `sig`, `key`) exist to feed
lib/linux/download.sh's fetch_verified(): once a task resolves `index`+`file`
to a concrete URL, `sums`/`sig`/`key` become fetch_verified's
<sha256|-> <sig_url|-> <gpg_fpr|-> arguments. This validator does not invent
a parallel scheme.

Planner note — not enforced here, since a downloaded image's inner file
sizes aren't known until fetch time: FAT32 caps a single file at
4,294,967,295 bytes. A `writeMode: hybrid` entry copied file-by-file onto a
FAT32-formatted stick needs every inner file under that cap — Ubuntu
26.04.1's largest, casper/minimal.squashfs at 3,432,136,704 B, is the only
reason a real file-copy stick built from it worked. Whichever task builds
the copy engine should check this against the actual downloaded image.
"""
import json
import re
import sys

WRITE_MODES = {"hybrid", "raw"}
KINDS = {"installer", "live-persistent", "full-os"}

# Finding A6 reopened: Task 4 left every entry's `key` as "-", which silently
# disables signature verification — `sig` names a detached signature but
# there is nothing to check it against, so the checksum is only as
# trustworthy as the mirror serving it. "-" means "not yet verified", not
# "no key exists"; a WRONG fingerprint here is worse than none at all (it
# fails 100% of the time, which is indistinguishable from an attack and
# reliably gets verification switched off), so this only ever checks SHAPE —
# a real gpg fingerprint is a 40-character uppercase hex string — never
# which key was entered. Whether the specific fingerprint is correct is a
# human-verification question, not a schema one.
_GPG_FPR = re.compile(r"[0-9A-F]{40}")

# UI affordances that become a free-text field, never a downloadable image —
# they have no `index`, no `homepage` and no `sums` by construction (plan
# Task 4 Step 3/4). This has to be a named set with an explicit carve-out,
# not a blanket "skip if missing": a real entry that is silently missing its
# download fields must still fail loudly, and a pseudo-entry that wrongly
# grows a download source (`sums` or `index`) must fail too.
PSEUDO = {"custom-url", "custom-local"}

_KEBAB = re.compile(r"[a-z0-9][a-z0-9-]*")


def _is_kebab(value):
    return bool(value) and bool(_KEBAB.fullmatch(value))


def validate(entry, problems, seen_ids):
    eid = entry.get("id")
    where = f"image '{eid}'" if eid else "image with no id"

    if not eid:
        problems.append(f"{where}: missing 'id'")
        return
    if eid in seen_ids:
        problems.append(f"{where}: duplicate id")
    seen_ids.add(eid)
    if not _is_kebab(eid):
        problems.append(f"{where}: id must be lower-case kebab-case")

    kinds = entry.get("kinds") or []
    if not kinds:
        problems.append(f"{where}: missing 'kinds'")
    for k in kinds:
        if k not in KINDS:
            problems.append(f"{where}: unknown kind '{k}'")

    write_mode = entry.get("writeMode")
    if write_mode is not None and write_mode not in WRITE_MODES:
        problems.append(f"{where}: unknown writeMode '{write_mode}'")

    # Finding A11: a raw image written block-for-block cannot also be handed
    # to a copy-based (Ventoy) path that promises a persistence overlay.
    if write_mode == "raw" and "live-persistent" in kinds:
        problems.append(
            f"{where}: writeMode 'raw' cannot declare kind 'live-persistent' — "
            "a raw image is written block-for-block, never copied"
        )

    if eid in PSEUDO:
        # Must still be kebab-case (checked above) and still declare kinds
        # (checked above), but must NOT claim a download source it cannot
        # have — a pseudo-entry with 'sums' or 'index' is a real bug.
        if entry.get("sums") or entry.get("index"):
            problems.append(f"{where}: is a pseudo-entry but declares a download source")
        return

    index = entry.get("index")
    if not index:
        problems.append(f"{where}: missing 'index'")
    elif not str(index).startswith("https://"):
        problems.append(f"{where}: 'index' must be an https:// URL")

    if not entry.get("homepage"):
        problems.append(f"{where}: missing 'homepage'")

    if not entry.get("sums"):
        problems.append(f"{where}: missing 'sums' — every real image needs a checksum source")

    key = entry.get("key")
    if key is not None and key != "-" and not _GPG_FPR.fullmatch(key):
        problems.append(
            f"{where}: 'key' must be '-' or a 40-character uppercase hex "
            f"gpg fingerprint, got '{key}'"
        )

    # `mirrors` (optional): alternative directory roots with the SAME layout
    # as `index`, used only to serve the big file faster once the canonical
    # index has chosen it and the signed manifest has fixed its digest
    # (usb_fetch_image / Invoke-AutoOSUsbFetchImage). Shape only: https,
    # trailing slash so the resolved relative path appends cleanly.
    mirrors = entry.get("mirrors")
    if mirrors is not None:
        if not isinstance(mirrors, list):
            problems.append(f"{where}: 'mirrors' must be a list of URLs")
        else:
            for m in mirrors:
                if not str(m).startswith("https://") or not str(m).endswith("/"):
                    problems.append(
                        f"{where}: mirror '{m}' must be an https:// URL ending in '/' "
                        "(a directory with the same layout as 'index')"
                    )


def main(argv):
    if len(argv) != 2:
        print("usage: images_validate.py <images.json>")
        return 1
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as fh:
            catalog = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the test run
        print(f"images: not valid JSON ({exc})")
        return 1

    images = catalog.get("images")
    if not images:
        print("images: missing 'images'")
        return 1

    problems = []
    seen_ids = set()
    for entry in images:
        validate(entry, problems, seen_ids)

    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
