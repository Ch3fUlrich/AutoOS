# 0004 — Ventoy is the default USB engine; WSL is the Windows power path; Rufus stays a catalog entry

Status: accepted · 2026-09-17

## Context

AutoOS builds bootable installer and rescue sticks (see [usb-creator.md](../usb-creator.md)).
Every engine has to satisfy the same rules as the rest of the repository: `--dry-run` must
show exactly what a real run does, a second run must report `skipped`, and the test suite must
be able to prove the behaviour without a real device.

Rufus is the tool most Windows users reach for, and it is good at what it does. But it has no
command-line interface for the write itself: AutoOS can launch it and hand it an image, and
from then on a human drives its window. That means:

- it cannot be dry-run — there is no plan to print, only a window to open;
- it cannot be tested — nothing in the suite can assert on what Rufus would do;
- it cannot be driven from the browser UI or a non-interactive run at all;
- it cannot participate in the read-back and flush steps the other engines end with.

Writing a raw image block-for-block, on the other hand, needs a real `dd` on Windows. WSL2 can
provide one, but `wsl --mount --bare` cannot attach a USB flash drive (verified: it fails with
`0x8007000f`); `usbipd-win` is the route, and it may want a reboot after installing.

## Decision

- **`ventoy` is the default engine** on Linux and Windows. It installs a boot manager once and
  then every image is a file copy, never a raw write; another distribution is a file copy
  away, and its persistence plugin gives `live-persistent` sticks a writable overlay without
  repartitioning. The Ventoy tool itself is fetched from its release page and verified against
  its published checksum at run time — it is never committed (hard rule 2).
- **`wsl` (via `usbipd-win`) is the Windows power path**: the only engine that can build a
  `full-os` stick, and the Windows way to write a raw image with a real `dd`.
- **`uefi-copy`** stays as the zero-elevation engine for a partition the user already formatted.
- **`rufus` stays in [`catalog/engines.json`](../../catalog/engines.json) as an interactive
  engine**: the terminal chooser may offer it (a human is present), `--dry-run` and the browser
  never do, and it is never the default. Images that need a raw write are handled by `native`
  or `wsl`, not by asking Rufus.

## Consequences

- A Ventoy dependency, downloaded and verified per run; a `usbipd-win` dependency on the WSL
  path, with a possible reboot the tool never performs itself.
- Raw-mode images (e.g. Proxmox VE) are refused by the copy engines and routed to `native`,
  `wsl` or `rufus` — the planner enforces this from the catalog's `writeMode`, so a raw image
  can never be planned onto Ventoy's copy path.
- Rufus users lose nothing: the engine is still there for a person at a keyboard. What they
  gain is that every other path has a plan, a dry run, a test, a flush and a read-back.
