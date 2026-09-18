# Building a bootable USB stick

AutoOS can build a bootable Linux stick — an installer, a live system that keeps its changes,
or (on Windows via WSL) a full portable OS — from the terminal menu, from the browser UI, or
non-interactively. The images it knows are the entries in
[`catalog/images.json`](../catalog/images.json); the write engines are the entries in
[`catalog/engines.json`](../catalog/engines.json). Neither list is repeated here on purpose:
the catalog is the single source of truth.

## The three kinds of stick

| `--kind` | What you get | Who offers it |
|---|---|---|
| `installer` (default) | Boots the distribution's installer. | Every image |
| `live-persistent` | Boots the live system; changes survive on the stick (Ventoy's persistence overlay). | Images that declare it |
| `full-os` | A complete installed OS on the stick. | The `wsl` engine only |

## Engines

| Engine | Platforms | Builds | How it writes | Needs |
|---|---|---|---|---|
| `ventoy` (default) | Linux, Windows | installer, live-persistent | Installs the Ventoy boot manager once, then copies the image on as a file. | Ventoy (fetched and verified at run time), elevation |
| `uefi-copy` | Linux, Windows | installer, live-persistent | Copies the image's files onto a FAT32 partition you already formatted and mounted. | Nothing — no elevation |
| `native` | Linux, macOS, Windows | installer, live-persistent | Block-for-block raw write (`dd` or the platform equivalent). The only non-interactive engine for a raw image. | Elevation |
| `wsl` | Windows | installer, live-persistent, full-os | Attaches the stick into WSL2 so a real Linux `dd` is available. | `usbipd-win`, elevation |
| `rufus` | Windows | installer, live-persistent | Launches Rufus with the image; you drive its window. | Rufus; never offered under `--dry-run` or in the browser |

Why Ventoy and WSL rather than Rufus as the default:
[ADR 0004](decisions/0004-ventoy-and-wsl-not-rufus.md).

## Running it

```bash
./setup.sh --list-usb                     # candidate devices (internal disks are never listed)
./setup.sh --list-engines                 # engines this machine offers
./setup.sh --create-usb --image ubuntu-desktop-lts --engine ventoy --usb-device /dev/sdb --dry-run
./setup.sh --create-usb --image ubuntu-desktop-lts --engine ventoy --usb-device /dev/sdb --wipe-target-disk
```

```powershell
.\setup.ps1 -ListUsb
.\setup.ps1 -CreateUsb -Image ubuntu-desktop-lts -Engine uefi-copy -UsbDevice \\.\PHYSICALDRIVE5 -DryRun
.\setup.ps1 -CreateUsb -Image ubuntu-desktop-lts -Engine uefi-copy -UsbDevice \\.\PHYSICALDRIVE5 -WipeTargetDisk
```

- **`--dry-run` shows the plan and touches nothing** — not the network, not the cache, not the
  device. Read it; it is exactly what a real run executes.
- **A real write needs `--wipe-target-disk` / `-WipeTargetDisk`.** Without it the run shows the
  plan and refuses. `--yes` answers the install menu's questions, not "may I destroy this disk".
- **Interactive sessions get a chooser.** Run `--create-usb` without the flags, or pick
  *Create installer USB* from the profile menu, and you are walked through image → kind →
  engine → device, ending in a confirmation that names the device, its model, its size, and
  that all data on it will be destroyed.
- **The browser UI** ([web-ui.md](web-ui.md)) has the same flow but only ever previews the plan;
  it never writes.

## Your own image

For an image the catalog does not describe, use `custom-local` (a file you already have) or
`custom-url` (one to download):

```bash
./setup.sh --create-usb --image custom-local --image-path ~/Downloads/my.iso --write-mode hybrid \
           --image-sha256 <hex> --engine ventoy --usb-device /dev/sdb --dry-run
./setup.sh --create-usb --image custom-url --image-url https://example.org/my.iso --write-mode hybrid \
           --image-sha256 <hex> --engine ventoy --usb-device /dev/sdb --dry-run
```

```powershell
.\setup.ps1 -CreateUsb -Image custom-local -ImagePath 'D:\isos\my image.iso' -WriteMode hybrid `
            -Engine uefi-copy -UsbDevice \\.\PHYSICALDRIVE5 -DryRun
```

- **`--write-mode hybrid|raw` is required.** The catalog normally says how an image must be
  written; for your own image you do. A `raw` image is refused on the copy engines (`ventoy`,
  `uefi-copy`) because copied as a file it would not boot.
- **`--image-sha256` is required for a URL.** An unverified download never reaches a disk.
  For a local file it is optional; without it the file is used as-is, with a warning.
- **A local file is written from where it is.** Nothing is copied into the cache, and the
  file is only ever read. A block device is refused as a source.
- **On Linux the path must not contain spaces** (plan steps are split on whitespace, never
  evaluated). Windows paths may contain spaces.
- A downloaded custom image is cached under its digest, so a second run is `skipped`.

## What a run does

1. **Resolves** the image from its catalog entry: the release index decides the current file,
   so the catalog never pins a version. `-lts` ids pick the current LTS by Ubuntu's own rule.
2. **Fetches and verifies**: the vendor's checksum manifest (GPG-verified when the catalog names
   a signature and key), then the image itself against that digest — from a catalog mirror when
   one is listed, the canonical site otherwise, verified the same either way. A second run finds
   the verified image in the cache (`~/.cache/autoos/images`, `%LOCALAPPDATA%\AutoOS\images`)
   and reports `skipped`.
3. **Re-checks the device** immediately before the first destructive step: the safety guard
   runs again and the device's identity is compared with the one pinned at plan time, so a
   stick swapped in the meantime is refused.
4. **Writes** with the chosen engine, then **flushes** the volume.
5. **Reads every file back** and compares it with the image. A stick that returns different
   bytes than were written is refused with "do not boot it, replace the stick" — this has
   happened on real hardware.

Only after all five does it say `Ready to boot`.

## Limitations, stated flatly

- `uefi-copy` sticks are **UEFI-only**; there is no legacy-BIOS boot path. They also depend on
  the image's own GRUB configuration not searching for a specific label or UUID (Ubuntu's
  does not; re-verify per image).
- **FAT32 caps a single file at 4,294,967,295 bytes.** A file-copy engine refuses an image
  with a larger inner file before copying anything. Large model weights for the `local-ai`
  profile belong on an exFAT data partition, not the boot partition.
- The vendor's `md5sum.txt` does not cover the boot chain (`EFI/`, `.disk/`, `boot/grub/`), so
  a manifest match alone never proves bootability; the read-back step compares the whole tree.
- On Windows, `wsl --mount --bare` cannot attach a USB flash drive; the `wsl` engine needs
  `usbipd-win`, which may ask for a reboot after installing.
- **Booting is the one check the tool cannot do.** Sticks have been built and verified by this
  tool; each one still has to be booted by a person.

## When a stick will not boot

1. Check the firmware: `uefi-copy` sticks need UEFI boot enabled and Secure Boot may need to
   be off for some images. Ventoy sticks boot in both modes.
2. Run the build again with `--dry-run` and read the plan: was it the engine and kind you
   meant?
3. Re-run without `--dry-run`. The image is a cache hit; the write and the read-back repeat.
   A read-back failure names the files whose bytes differ — that is the stick, not the image.
4. Try another stick before anything else. Cheap flash silently corrupts written clusters at
   the right size; the read-back exists because one did.
5. Try another engine: `ventoy` or `native` where `uefi-copy` failed (they need elevation).

Where the run logs go and how to report a problem: [troubleshooting.md](troubleshooting.md).
