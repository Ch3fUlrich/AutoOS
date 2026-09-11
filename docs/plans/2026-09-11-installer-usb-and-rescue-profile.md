# Installer USB + `rescue` profile — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let AutoOS build a bootable Linux USB stick — installer, persistent-live, or a full
portable OS — from the terminal menu, from the browser UI, and non-interactively, on both Windows
and Linux; and add a `rescue` profile that installs the recovery toolkit onto the machine itself.

**Architecture:** The OS image list becomes catalog *data* (`catalog/images.json`), exactly like
software already is. Writing a stick becomes a new verb (`--create-usb`) running the same
`detect → select → plan → confirm → execute → report` pipeline, so `--dry-run` means something.
Two write engines are offered on Windows — **Ventoy** (scriptable, multi-boot) and **WSL2 +
usbipd-win** (the full Linux toolchain, no Rufus at all) — and one on Linux (native). Rufus stays
as a catalog entry for people who want the GUI, not as machinery AutoOS depends on.

**Tech Stack:** PowerShell 5.1+ (`lib/windows/*.psm1`), bash 4+ (`lib/linux/*.sh`), python3 for
JSON and the browser server, vanilla JS in `web/index.html`, Ventoy, usbipd-win, `mmdebstrap`,
GnuPG / `Get-FileHash`.

**Spec:** this document. Part A is the review that motivates it; Part B is the design rationale.

## Scope warning — read before starting

This is **four independent subsystems**, not one feature:

1. the `rescue` profile (catalog data only),
2. verified image download + USB writing,
3. WSL as a first-class install *target* ("install this app into WSL, not Windows"),
4. the UI surfaces for all of it.

Subsystem 3 is the odd one out — it changes what a *component install* means repo-wide and has
nothing to do with USB sticks. **Phase 1–3 below ship working software on their own; Phase 4
should become its own plan** once Phase 1–3 have landed. Doing all four at once produces a change
too large for `pr-approval-agent`'s size ceiling and too large to review honestly.

## Global constraints

Copied verbatim from [AGENTS.md](../../AGENTS.md); every task inherits them.

- This repository is **public**. No password, hash, hostname, key or username in a tracked file.
  Templates end in `.example` and carry obviously fake values.
- **No vendor binary is ever committed** — not an `.exe`, not an `.iso`. Downloads go to a cache
  directory outside the working tree and are gitignored.
- **Every destructive action is opt-in and announced.** Writing a USB stick destroys it. So does
  an autoinstall answer file. Both must be named in the confirmation summary before anything runs.
- Never overwrite a PATH, profile or config wholesale; read, append idempotently, write back.
- Safe to run **twice**: the second run reports `skipped`, never `installed`.
- PowerShell: `Set-StrictMode -Version Latest`, `$ErrorActionPreference = 'Stop'`, approved
  `Verb-Noun` only, all output through `AutoOS.Ui.psm1`. Bash: `set -euo pipefail`, `local`, every
  expansion quoted, `shellcheck` clean, no `sudo` inside a function — use `$AUTOOS_SUDO`.
- Both suites must pass: `pwsh tests/run-tests.ps1` and `bash tests/run-tests.sh`.
- Tests assert on the **planned command**, never on system state. **No test writes to a disk.**

---

## Part A — Review of the existing work in `short_changes/`

Eight files, ~810 lines, plus a 6.1 GB ISO. The idea is right and the package lists are genuinely
good — that research is worth keeping verbatim. The delivery mechanism is not, and five findings
would be blocking defects under this repository's own rules.

### A1–A5 — Blocking: hard-rule violations

| # | Finding | Where | Rule |
|---|---|---|---|
| **A1** | A password hash is committed, and a plaintext password beside it: `d-i passwd/user-password password rescue`. AGENTS.md is explicit that "just a placeholder that looks real" still counts. | `user-data:13`, `preseed.cfg:21-22` | Hard rule 1 |
| **A2** | That hash is **malformed**. SHA-512 crypt requires an 86-character body; this one is **82**. Subiquity will reject the schema or set an unusable password — you cannot log in to the machine you just installed. | `user-data:13` | correctness |
| **A3** | **Unannounced whole-disk wipe.** `storage: layout: name: direct`, and `partman-auto/method regular` + `choose_recipe atomic` + `confirm_write_new_label true`, erase the target's first disk with no prompt. Boot this stick on the wrong machine and it eats it. | `user-data:17-19`, `preseed.cfg:27-28` | Hard rule 3 |
| **A4** | `ubuntu-26.04.1-desktop-amd64.iso` (6.1 GB) sits beside the scripts, and `.gitignore` covers `*.exe` but has **no `*.iso` rule**. One `git add -A` permanently bloats a public repo. | working tree, `.gitignore` | Hard rule 2 |
| **A5** | `rufus.exe`, `rufus.ini` and `ISOs/` are all written to `$PSScriptRoot`. If the script lands in `lib/windows/`, `$PSScriptRoot` *is* the repository. | `Create-RescueStick.ps1:30,229-230,237` | Hard rule 2 |

### A6–A14 — High: it will fail in the field

- **A6 — Nothing is verified.** Not one SHA256, not one signature, on any ISO or on `rufus.exe`.
  A tool whose only output is a bootable disk is exactly the tool that must verify its inputs.
- **A7 — The SystemRescue URL does not return an ISO.** SourceForge's `/download` suffix is an
  HTML interstitial. BITS saves that HTML as `systemrescue-11.01-amd64.iso` and reports success.
- **A8 — `Start-BitsTransfer` is the wrong downloader here.** It does not reliably follow the
  `download.fedoraproject.org` mirror redirect, and it fails outright where the BITS service is
  disabled by policy. The HttpClient fallback only runs when the *cmdlet* is absent — which is
  never, on Windows. So the fallback is dead code.
- **A9 — Every pinned version is already stale.** Ubuntu 24.04.1 (current LTS is **26.04.1**
  "Resolute Raccoon"), Fedora 40, Debian 12.7, SystemRescue 11.01 (current **13.02**, Aug 2026),
  Proxmox 8.2, Rufus 4.6 (current **4.15**, Jun 2026). The staleness is the symptom; *pinning
  versions inside code* is the defect.
- **A10 — Post-burn drive detection is wrong three ways.** `$initialDrives` is computed at
  line 397 and **never read again**, so the before/after comparison the design rests on does not
  happen. The `-eq 1` branch then copies onto whatever single removable volume exists, target or
  not. And `DriveType -eq 'Removable'` misses USB SSDs entirely — they enumerate as `Fixed`.
- **A11 — DD mode makes the copy step impossible.** A Proxmox stick written in DD mode is
  read-only ISO9660. The `Copy-Item` cannot succeed, and nothing guards it.
- **A12 — One `set -e` package install, so one missing package kills the run.** A single
  `dnf install -y` or `apt-get install -y` of ~50 packages aborts the *entire* bootstrap on the
  first unavailable name, discarding the 49 that would have succeeded. `arch-install-scripts` is
  Debian-only and correctly absent from the Fedora list, which shows the two lists were hand-diffed
  and will drift apart on the next edit.
  **Correction, 2026-09-11:** an earlier draft of this finding claimed `dislocker` and `chntpw` are
  absent from Fedora's default repositories. That is wrong — both ship in Fedora's own repos
  (`dislocker` 0.7.3 across F43–45, Rawhide and EPEL; verified at
  [packages.fedoraproject.org](https://packages.fedoraproject.org/pkgs/dislocker/dislocker/)).
  The structural defect stands on its own; the package examples do not support it.
- **A13 — Not idempotent.** A second run re-adds the NodeSource repository, re-runs
  `npm install -g`, and rewrites `/etc/profile.d/gemini-env.sh`. AGENTS.md §4 requires `skipped`.
- **A14 — `curl … | bash` three times, unverified** — and on a rescue stick booted with no network
  `set -e` kills the bootstrap at step 3 of 5, *after* the useful part already succeeded.

### A15–A23 — Medium: house rules and maintainability

- **A15** `Download-FileWithProgress` and `Ensure-Rufus` use unapproved verbs. `tests/run-tests.ps1`
  runs `Invoke-ScriptAnalyzer`; both fail it.
- **A16** Every line of output is `Write-Host`. AGENTS.md §3: output goes through `AutoOS.Ui.psm1`
  or it never reaches the log a user needs when the write fails.
- **A17** No `Set-StrictMode -Version Latest`.
- **A18** `$OsCatalog` is a hashtable keyed `"1".."8"` **inside the script**. The OS list is data;
  AGENTS.md §2 says adding one must not mean editing code.
- **A19** `Read-Host` fires *between* download and write, so "execution never asks questions" is
  broken, and there is no `--dry-run`.
- **A20** **Rufus cannot be driven headlessly at all.** Confirmed against upstream: 4.15 still has
  no scripting interface, and `rufus.ini` only sets GUI defaults. `Create-RescueStick.ps1` is
  therefore a manual procedure wearing a script — download, human clicks, copy files.
- **A21** Linux is unsupported. Requirement 4.1 asks for both platforms.
- **A22** Gemini CLI is hardcoded in five places, in a repository whose catalog is the place to
  choose an AI CLI and whose own profiles ship Claude Code.
- **A23** `README.md` restates every version already in the script. Two sources, guaranteed drift.

### What to keep

The package lists. `smartmontools`, `nvme-cli`, `ddrescue`, `testdisk`, `dislocker`, `chntpw`,
`efibootmgr`, `arch-install-scripts`, and the zram / `noatime` / `commit=60` flash-wear tuning are
real, hard-won content. They become the `rescue` profile in Task 1 almost verbatim.

---

## Part B — Design decisions

### B1. Two engines on Windows, and Rufus is neither of them

Rufus has no CLI (A20). Anything built on it cannot be dry-run, cannot be tested without a stick
in the machine, and cannot work on Linux. So AutoOS offers the user a real choice of **engine**:

| Engine | Platform | How it writes | Why you would pick it |
|---|---|---|---|
| `ventoy` *(default)* | Windows + Linux | `Ventoy2Disk.exe VTOYCLI /I /Drive:E: /GPT` / `Ventoy2Disk.sh -i -g /dev/sdX` | Scriptable, multi-boot: install once, then **add an OS by copying an ISO**. A second distribution costs a file copy, not a reflash. |
| `wsl` | Windows only | `usbipd attach` → real `/dev/sdX` inside WSL2 → `dd` / `mmdebstrap` / `parted` | The entire Linux toolchain on Windows. Required for `full-os` sticks. No Rufus. |
| `native` | Linux only | `dd` / `parted` / `mmdebstrap` directly | No extra dependency at all. |
| `uefi-copy` | Windows + Linux, **no admin** | Copy the ISO's contents onto an existing FAT32 partition | **The documented fallback**, kept deliberately (human partner, 2026-09-11: "keep the not-elevated solution as a backup"). Needs no elevation at all. See B16; it is how the first real stick was built. |
| `rufus` | Windows only | Launch the GUI, hand the user the settings, wait | Escape hatch for an image the others cannot handle. **Explicitly marked interactive**; unavailable under `--dry-run` and in the browser UI. |

Requirement 4.1.1 — "rufus should also be installed if not present on windows" — is met as a
*catalog entry* (`rufus`, provider `winget`, package `Rufus.Rufus`). It is offered and installed;
AutoOS just does not build its automation on it.

### B2. The WSL engine needs `usbipd-win`, not `wsl --mount`

This is the single most important technical finding for the WSL requirement, and getting it wrong
would waste a whole task.

**`wsl --mount` does not support USB flash drives or SD-card readers.** It is a documented
limitation ([microsoft/WSL#6011](https://github.com/microsoft/WSL/issues/6011),
[#9290](https://github.com/microsoft/WSL/issues/9290)); attempts typically fail with
`0x8007000f`, "the system cannot find the drive specified". It *does* work for USB SSDs and HDDs
in enclosures, which enumerate as fixed disks.

So the WSL engine is two-tier, and tries the cheap path first:

```
1. wsl --mount \\.\PHYSICALDRIVE<N> --bare      # works for USB SSD/HDD enclosures
   └─ on failure ──▶
2. usbipd list                                   # find the mass-storage BUSID
   usbipd bind   --busid <BUSID>                 # admin, one-time per device
   usbipd attach --wsl --busid <BUSID>           # device now /dev/sdX inside WSL2
   … write …
   usbipd detach --busid <BUSID>                 # ALWAYS, even on failure
```

`usbipd-win` is MIT-licensed and installable through the existing catalog
(`winget install dorssel.usbipd-win`), so it becomes a `requires` of the WSL engine rather than a
manual prerequisite. The detach step goes in a `try/finally` — leaving a device bound is how you
lose a USB stick from Windows until reboot.

### B3. Three artifact kinds, because "installer stick" and "OS stick" are different things

The user asked for both. They are not variations of one flow:

| Kind | What boots | How it is built | Engines |
|---|---|---|---|
| `installer` | the distribution's installer, which installs to the **target machine's** disk | ISO written/copied verbatim | ventoy, wsl, native, rufus |
| `live-persistent` | a live session whose changes **survive reboot** | ISO + a persistence partition (Ventoy persistence plugin / Rufus slider) | ventoy, rufus |
| `full-os` | a **real installation living on the stick** — portable OS, `grub-install --removable` | `mmdebstrap` → stick, or an autoinstall targeting the USB | wsl, native |

`full-os` is where the WSL engine earns its place: `mmdebstrap` builds a complete bootable
Debian/Ubuntu onto the stick from Windows with **no reboot and no ISO download at all**, and the
`rescue` package list drops straight into it.

### B4. Nothing is version-pinned in code

Every entry in `catalog/images.json` is a **release-index URL plus a filename pattern**, resolved
at run time, then verified against the vendor's own `SHA256SUMS` and its detached GPG signature.
A9 stops being possible, because there is no version to go stale.

```jsonc
{
  "id": "ubuntu-desktop-lts",
  "name": "Ubuntu Desktop (latest LTS)",
  "index":   "https://releases.ubuntu.com/",
  "resolve": "ubuntu-lts",
  "file":    "ubuntu-{version}-desktop-amd64.iso",
  "sums":    "SHA256SUMS",
  "sumsSig": "SHA256SUMS.gpg",
  "gpgKey":  "843938DF228D22F7B3742BC0D94AA3F0EFE21092",
  "kinds":   ["installer", "live-persistent"],
  "writeMode": "hybrid",
  "sizeHintGb": 6,
  "homepage": "https://ubuntu.com/download/desktop"
}
```

`writeMode` is `hybrid` (ISO9660+MBR, safe to copy onto Ventoy) or `raw` (must be written
block-for-block — Proxmox VE). A `raw` image is silently excluded from Ventoy's copy path and
routed to the raw writer, which is A11 fixed structurally.

### B5. Creating a USB is a separate verb

`--create-usb` is not a component install. It gets its own pipeline pass so `--dry-run` prints the
exact `Ventoy2Disk` / `dd` invocation and the exact device it would touch **without opening a
handle to a disk** — which is also the only way AGENTS.md §5 ("never test by actually installing")
can be satisfied for a feature whose job is destroying a disk.

### B6. `rescue` is an ordinary profile

No new machinery. One key in `profiles`, `"rescue"` added to the `profiles` array of the relevant
components, and the existing profile test covers it automatically.

### B7. Answer files become `.example` templates, with no credentials and no automatic wipe

`user-data.example`, `preseed.cfg.example`. The password is **generated at USB-creation time** and
shown once; the storage section defaults to **interactive partitioning**, with a full-disk wipe
available only behind an explicit `--wipe-target-disk` flag that prints the disk model and size in
the confirmation. A1, A2 and A3 all close here.

### B8. Both AI CLIs ship — in the catalog and on the stick

**Decided 2026-09-11: Claude Code *and* Gemini CLI, everywhere.** Both become ordinary catalog
entries (`claude-code` already exists; `gemini-cli` is new on all three platforms), both carry
`"profiles": ["rescue", ...]`, and the rescue bootstrap installs both onto the stick.

This closes A22 properly. The original scripts hardcoded Gemini in five places; the fix is not to
hardcode Claude Code in five places instead, it is to make the choice data. Two consequences the
implementer must honour:

- **Neither may be a hard failure.** Each is a separate catalog entry, so the existing
  per-component failure handling already isolates them — an npm registry outage that kills
  `@google/gemini-cli` must still leave `@anthropic-ai/claude-code` installed, and vice versa.
- **Neither carries a key.** `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are read from the
  environment at use time and never written to the stick. `/etc/profile.d/autoos-ai.sh` defines
  the `ai` alias and a commented `# export …_API_KEY=` line — a comment, not a value. Hard rule 1
  applies to the artifact this repository *produces*, not only to the repository itself.

### B10. Elevation is checked before the plan is shown, not at the moment of writing

Every raw-disk path needs admin/root: `Ventoy2Disk.*`, `Get-Disk`/`Clear-Disk`, `usbipd bind`, and
`dd`. The failure mode this avoids is the one the whole plan-then-execute design exists to avoid —
a `--dry-run` that looks perfect, followed by `Access is denied` the one time it writes for real.

- **Linux/macOS:** `$AUTOOS_SUDO` is already set once at startup (empty when root). If it is empty
  and `id -u` is not 0, refuse *before planning* and print the exact command to re-run.
- **Windows:** `([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)`.
  Do not attempt to self-elevate — a re-launch loses the session's log file.
- **Browser UI:** `/api/usb/devices` returns `"elevated": false` and the button renders disabled
  with the reason, rather than offering an action that cannot succeed.

Owner: Task 5 (the check), Task 6 (refuse at plan time), Task 11 (the UI surface).

### B11. macOS refuses cleanly, because `setup.sh` is a shared entry point

`setup.sh` serves Linux **and** macOS. `--create-usb` on Darwin must exit non-zero with
"USB creation is not supported on macOS — the rescue profile's recovery tools are", not fall into
whatever `usb_list` does against `diskutil`-shaped output it was never written for. Owner: Task 6,
with a test that fakes `SYS_OS=macos`.

### B12. Generating the autoinstall password hash is genuinely hard on Windows

B7 promises a password generated at creation time. The obvious tool does not exist: Python's
`crypt` module is POSIX-only and was **removed in Python 3.13**, and Windows has no glibc
`crypt(3)`. Resolution, in order of preference:

1. `openssl passwd -6 "$plain"` — present on every target Linux, and on Windows via the OpenSSL
   that ships with Git for Windows.
2. `mkpasswd -m sha512crypt` where `whois` is installed.
3. Under the WSL engine, shell out to the distro: `wsl -d <distro> -- openssl passwd -6 …`.
4. **If none is available, refuse to write an answer file at all** and tell the user to set the
   password at first boot.

**Never bundle a hand-rolled SHA-512-crypt implementation.** Getting it subtly wrong produces
exactly finding A2 — an 82-character hash that looks fine and locks you out. Owner: Task 9.

### B13. Concurrency, unplug, arm64, and replay

Four things the first draft did not mention at all, each of which is cheap now and expensive later.

- **Concurrency.** `serve.py` already guards concurrent component installs with a `RUN`/`LOCK` pair
  (`lib/linux/serve.py:421-427`). USB creation joins **that same lock**, not a second one: a write
  requested while an install is running returns `409`, and so does a second write. Two package
  managers against one machine is bad; two writers against one block device is worse. The terminal
  path takes a lock file in the cache directory for the same reason.
- **Unplug mid-write.** `dd` and Ventoy both fail with EIO, which is indistinguishable from a
  genuine I/O error until you look. `usb_execute` re-runs `usb_list` on failure: if the device is
  gone, the message is "the stick was removed during the write"; otherwise it is the underlying
  error. **Never retry automatically** — a re-plugged stick can enumerate under a different name,
  and a blind retry then writes to whatever now holds that name.
- **arm64 / Raspberry Pi.** Ventoy ships x86_64 binaries only. On arm64 the `ventoy` engine is
  **hidden, not shown-and-failing** (AGENTS.md §3), leaving `native` (raw write) as the only
  engine. `catalog/engines.json` therefore carries an `arch` array like every catalog component.
- **Replay and undo.** `--save-state`, `--from-state` and `--undo` are existing flags. A USB write
  **does not participate**: it is not a component, it is not reversible, and a stick is not part of
  this machine's state. `--undo` must say so explicitly — silently ignoring a flag the user
  reasonably expects to cover their last action is worse than refusing it.

### B14. `dd status=progress` emits no percentage — the runner needs one computed

`lib/linux/process.py:93` extracts progress with `re.search(r"(?<!\d)(100|\d{1,2})(?:\.\d+)?\s*%", line)`,
and `dd status=progress` prints bytes copied and a transfer rate with **no `%` anywhere**. Left
alone, the current bar silently degrades to `percentage unavailable` (`process.py:26`) — not the
behaviour the UI task promises.

So `usb_write_raw` does not hand `dd` straight to the runner. It knows the image size, so it reads
`dd`'s byte counter and emits its own `NN%` lines, which the existing regex then parses unchanged.
Owner: Task 7; Task 11 only consumes it.

### B15. There is no rollback, and pretending otherwise is the dangerous option

A half-written stick cannot be restored to what it held before. What the plan requires instead:

- The failure message says plainly that the stick is now unbootable and must be rewritten.
- A rewrite always starts from `wipefs -a` / a full re-partition, so **a retry is always safe** and
  `usb_guard` needs no special case for a partially-written device.
- "Ready to boot" is printed only after the writer exits 0 **and** the resulting filesystem reads
  back — an exit code alone has already been shown to be insufficient (A7).

Owner: Task 7.

### B16. `uefi-copy` — a bootable stick with no administrator rights at all

Every other engine needs elevation: `Ventoy2Disk` writes a partition table, `usbipd bind` needs
admin, `dd` needs the raw device. On a machine where the user cannot elevate — a work laptop, a
locked-down desktop, or simply an unattended agent session that cannot answer a UAC prompt — all
three are unavailable, and the feature is unavailable with them.

It does not have to be. A modern Linux ISO is already a UEFI boot structure; the firmware needs
only a FAT32 partition containing `EFI/boot/bootx64.efi`. **Copying the ISO's contents onto an
already-FAT32 USB stick produces a bootable UEFI installer** — no partitioning, no bootloader
install, no elevation, no Rufus and no Ventoy.

Verified against Ubuntu 26.04.1 "Resolute Raccoon" (build 20260826) on 2026-09-11:

| Check | Result |
|---|---|
| `EFI/boot/bootx64.efi` present | yes, plus `grubx64.efi` and `mmx64.efi` |
| Largest file inside the ISO | `casper/minimal.squashfs`, **3,432,136,704 B (3.20 GiB)** — under FAT32's 4 GiB ceiling |
| Total payload | 6,474,467,630 B (6.03 GiB) |
| How `boot/grub/grub.cfg` finds its root | **it does not need to** — the menu entries are bare `linux /casper/vmlinuz` / `initrd /casper/initrd`, with no `search --label` and no UUID. GRUB sets `$root` to the partition it booted from, and casper's initramfs then scans every block device for `/casper/`. |

That last row is what makes the engine safe rather than lucky: there is no label to match, so the
stick's existing FAT32 label is irrelevant. **Re-verify it per image** — an ISO whose `grub.cfg`
*does* `search --label` will not boot from a copy, and that check belongs in the planner.

**State the limits plainly, in the UI, every time this engine is chosen:**

- **UEFI only.** No MBR boot code is written, so a legacy-BIOS/CSM-only machine will not boot it.
  For a *rescue* stick aimed at old or broken hardware this is a real restriction, not a footnote.
- **The stick must already be FAT32**, and formatting one is itself an elevated operation.
- **No file inside the image may exceed 4 GiB.** Check before copying, and refuse with the
  offending filename rather than failing 20 minutes into a copy.
- **No persistence by default** — see B17.

### B17. Persistence without repartitioning

A rescue stick whose tools vanish on reboot is a live CD with extra steps. `uefi-copy` cannot add a
partition, but casper can persist into a **file**: a `casper-rw` ext4 image on the FAT32 partition,
with `persistent` appended to the kernel command line in `boot/grub/grub.cfg`.

FAT32 caps that file just under 4 GiB, so allocate 4,000 MiB and no more. Creating it needs a
`mkfs.ext4`, which on Windows means WSL — root inside WSL, not Windows administrator, so it stays
within the no-elevation budget.

**Treat this as probe-then-commit, not as a promise.** Casper's file-based persistence has changed
across releases; the planner must verify it took effect on first boot (write a marker, reboot, look
for it) and the UI must not claim persistence that was never confirmed. Owner: Task 7.

### B18. Elevation is available — so `ventoy` stays the default and gains legacy BIOS

Settled 2026-09-11: *"elevated shell is fine since a lot of applications need elevated shell, but
keep the not-elevated solution as a backup."*

That resolves the tension B16 opened. The engine order is:

1. **`ventoy`** — the default. Needs elevation, and in exchange writes a partition table and an MBR
   boot sector, so a stick boots on **legacy BIOS/CSM as well as UEFI**. For a rescue stick aimed at
   old or broken hardware this is the difference between working and not.
2. **`uefi-copy`** — the fallback, selected automatically when the process is not elevated and
   elevation cannot be obtained. Prints exactly one line saying the stick will be UEFI-only and
   why, so the limitation is never a surprise at the boot menu.

The planner picks the fallback on its own rather than failing, and says which it picked and why.

### B19. An offline package cache, because a rescue stick meets machines with no network

Settled 2026-09-11: *"yes do it."*

`rescue-bootstrap.sh` needs `apt` to reach the internet, and the machines this stick exists for are
exactly the ones with no working network. So the stick carries its own `.deb` cache.

**The version trap that makes this non-trivial:** packages must match the *live ISO's* release, not
the build machine's. This machine's WSL is Ubuntu **24.04** while the ISO is **26.04**, so a naive
`apt-get download` produces packages that fail dependency resolution on the target — worse than no
cache, because it fails *after* you have booted a broken machine.

The build therefore creates a throwaway chroot of the **image's own release** and downloads into it:

```bash
mmdebstrap --variant=apt --include=ca-certificates resolute /tmp/rescue-chroot \
           http://archive.ubuntu.com/ubuntu
chroot /tmp/rescue-chroot apt-get -y --download-only install <the rescue package set>
cp /tmp/rescue-chroot/var/cache/apt/archives/*.deb <stick>/rescue/debs/
```

The release codename comes from the image catalog entry, never a hardcoded string, so this cannot
silently drift from the ISO it ships beside. `rescue-bootstrap.sh` then prefers the local cache
(`apt-get install --no-download -o Dir::Cache::archives=<stick>/rescue/debs`) and falls back to the
network when a package is missing. Budget ~2 GB on the stick.

### B20. `ai` is a dispatcher over a registry, not an alias to one tool

Settled 2026-09-11: *"claude and [the other] should both be ai, but claude should be the default.
Make it possible to ship multiple ais, since there will be other ai clients added in the future."*

So `ai` is a small command over a data file, not an alias — the same catalog-is-data rule AGENTS.md
§2 applies to software:

- `/etc/autoos/ai-clients.conf` — one `id:binary:description` record per line.
- `ai "question"` runs the default (`claude`); `ai gemini "question"` dispatches by id;
  `ai --list` shows every registered client with an installed/not-installed column.
- `AUTOOS_AI_DEFAULT` overrides the default without editing a file.
- If the default is absent but another client is present, it says so and names the working one,
  rather than failing with `claude: command not found`.

Adding a third AI CLI is then one line in a config file and one catalog entry. Owner: Task 9.

### B21. A separate `local-ai` profile, because the models are enormous

Settled 2026-09-11: *"the user should be capable of requesting ollama and models to run local ai
stuff… add those local ai capabilities as an additional profile and warn because of the big size."*

Local inference is exactly right for a rescue stick — it is the only way the AI tooling works on a
machine with no network, and it needs no API key, which is the other standing constraint.

It is **its own profile**, never folded into `rescue`, because the size difference is two orders of
magnitude: the entire rescue toolkit is ~400 MB of packages; one useful local model is 4–20 GB.
A user who asked for a rescue stick has not asked for that.

- Profile id `local-ai`: `ollama`, plus model pulls as separate catalog components.
- **Every model component states its download size in its `description`**, and the plan summary
  shows the total before the confirmation — an unannounced 20 GB download on a metered connection
  is the kind of surprise AGENTS.md hard rule 3 exists to prevent.
- Models are `custom`-provider components whose `postInstall` runs `ollama pull`, so the existing
  idempotency applies: a model already pulled reports `skipped`.
- Default suggestion is one small general model (~4 GB), not the largest available.

### B9. WSL becomes an install *target*, not just a component (Phase 4)

`wsl` is added to `catalog/windows.json` as an ordinary component (`wsl --install
--no-distribution`, then `wsl --install -d Ubuntu`). Beyond that, a Windows component may declare:

```jsonc
"targets": ["host", "wsl"],     // default ["host"] when the key is absent
"wslId": "build-essentials"     // id in catalog/linux.json used when target = wsl
```

When the user picks `wsl` for a component, AutoOS resolves `wslId` **against the existing Linux
catalog** and runs that plan inside `wsl -d <distro> -u root -- bash -lc '…'`. No second catalog,
no duplicated package names — the Linux catalog is already the answer to "how does this install on
Linux". That is why this is worth doing and why it is worth doing *separately*: it changes the
meaning of a plan entry repo-wide.

---

## Part C — File structure

```
catalog/images.json                  NEW  OS images: index URL, filename pattern, checksum, GPG
                                          key, artifact kinds, writeMode, size hint
catalog/engines.json                 NEW  write engines: id, platforms, requires, interactive flag
catalog/linux.json                   MOD  + "rescue" profile, + rescue components, + gemini-cli
catalog/windows.json                 MOD  + "rescue" profile, + rescue components, + rufus,
                                          + ventoy, + usbipd-win, + wsl, + "targets"/"wslId" keys
catalog/macos.json                   MOD  + "rescue" profile (tools only; creation unsupported)

lib/linux/download.sh                NEW  fetch_verified(): resolve → download → sha256 → gpg
lib/linux/usb.sh                     NEW  usb_list, usb_guard, usb_plan, usb_write_ventoy,
                                          usb_write_raw, usb_copy_image, usb_build_full_os
lib/windows/AutoOS.Download.psm1     NEW  Get-AutoOSVerifiedFile
lib/windows/AutoOS.Usb.psm1          NEW  Get-AutoOSUsbDevice, Assert-AutoOSUsbSafe,
                                          New-AutoOSUsbPlan, Invoke-AutoOSUsbPlan
lib/windows/AutoOS.Wsl.psm1          NEW  Get-AutoOSWslState, Mount-AutoOSUsbInWsl,
                                          Dismount-AutoOSUsbFromWsl, Invoke-AutoOSWslCommand

lib/linux/serve.py                   MOD  GET /api/images, GET /api/usb/devices,
                                          POST /api/usb/plan, POST /api/usb/create
lib/windows/AutoOS.Serve.psm1        MOD  the same four routes
lib/windows/AutoOS.Ui.psm1           MOD  terminal chooser for image / kind / engine / device
lib/linux/ui.sh                      MOD  the same chooser
web/index.html                       MOD  action-bar button, <dialog> wizard, progress wiring

setup.sh                             MOD  --create-usb --image --kind --engine --usb-device
                                          --wipe-target-disk --list-usb
setup.ps1                            MOD  the same flags

templates/user-data.example          NEW  sanitised Subiquity autoinstall
templates/preseed.cfg.example        NEW  sanitised Debian preseed
templates/rescue-bootstrap.sh        NEW  idempotent, offline-tolerant, group-wise installs

tests/helpers/fake_usb.py            NEW  synthetic block-device fixtures
tests/helpers/images_validate.py     NEW  schema check for catalog/images.json
tests/run-tests.sh                   MOD  + "image catalog", "usb planning", "rescue profile"
tests/run-tests.ps1                  MOD  the same, + "wsl engine planning"

docs/usb-creator.md                  NEW
docs/profiles.md                     MOD  + rescue
docs/decisions/0004-ventoy-and-wsl-not-rufus.md   NEW
docs/decisions/0005-wsl-as-an-install-target.md   NEW  (Phase 4)
.gitignore                           MOD  + *.iso, + cache/
CHANGELOG.md                         MOD
```

---

## Part D — Tasks

> **The `--filter` argument matches a TEST NAME, not a describe-group name.**
> `tests/run-tests.ps1:48` is `if ($Filter -and $Name -notlike "*$Filter*") { return }`, and the
> bash harness does the same. Individual test names are written in the singular ("light **profile**
> is the Pi set"), so `--filter profiles` matches **zero tests** and reports a clean run — a silent
> pass that looks exactly like a real one. Always eyeball the test count a filtered run prints.

### Phase 1 — the `rescue` profile (ships alone; no USB code at all)

#### Task 1: `rescue` profile and rescue components

**Files:**
- Modify: `catalog/linux.json` (profiles block, and `profiles` arrays on components)
- Modify: `catalog/windows.json`, `catalog/macos.json`
- Test: `tests/run-tests.sh` (`describe "profiles"`), `tests/run-tests.ps1`

**Interfaces:**
- Produces: profile id `rescue`, resolvable by `catalog_profile_defaults rescue` (bash) and
  `Get-AutoOSProfileDefaults -Profile rescue` (PowerShell).

- [ ] **Step 1: Write the failing test** — append to `describe "profiles"` in `tests/run-tests.sh`:

```bash
if it "rescue profile carries the disk-recovery core"; then
    got="$(catalog_profile_defaults rescue)"
    ok=1
    for id in smartmontools nvme-cli ddrescue testdisk gdisk; do
        [[ " $got " == *" $id "* ]] || { ok=0; echo "missing: $id" >&2; }
    done
    (( ok )) && pass || fail "rescue profile is incomplete"
fi

if it "rescue profile stays off the desktop"; then
    assert_not_contains "$(catalog_profile_defaults rescue)" "antigravity"
fi
```

- [ ] **Step 2: Run it and watch it fail**

```bash
bash tests/run-tests.sh --filter profile
```

Expected: `rescue profile carries the disk-recovery core` FAILS listing all five ids, because
`catalog_profile_defaults` returns empty for an unknown profile.

- [ ] **Step 3: Add the profile key** to all three catalogs, beside `"server"`:

```jsonc
"rescue": "Disaster-recovery toolkit — disk, filesystem, hardware and Windows repair."
```

- [ ] **Step 4: Add the components.** New category `rescue` in `catalog/linux.json`, one entry per
  package, each carrying `"profiles": ["rescue"]`. Take the list verbatim from
  `short_changes/setup-rescue.sh` — it is good. Group them so one missing package cannot take the
  rest down (A12): each is its own catalog entry, so the existing per-component failure handling
  already gives that for free. Example entry:

```jsonc
{
  "id": "ddrescue",
  "name": "GNU ddrescue",
  "description": "Copy data off a failing disk, retrying bad sectors",
  "provider": "apt",
  "package": "gddrescue",
  "verify": "ddrescue --version",
  "homepage": "https://www.gnu.org/software/ddrescue/",
  "profiles": ["rescue"]
}
```

  Full set: `smartmontools nvme-cli hdparm pciutils usbutils lshw dmidecode inxi lm-sensors
  memtester stress-ng f3 gdisk parted testdisk gddrescue e2fsprogs btrfs-progs xfsprogs ntfs-3g
  dosfstools exfatprogs dislocker chntpw efibootmgr arch-install-scripts clonezilla gparted
  nmap tcpdump iperf3 ethtool wireguard borgbackup restic`.

  **`dislocker` and `chntpw` need `arch: ["x64"]` and are Debian-family only** — give them
  `"provider": "apt"` and leave them out of any `dnf` path until Fedora support is a real target
  (A12). `f3` and `stress-ng` are additions: fake-flash detection and load testing are exactly
  what a rescue stick is for and were missing from the original list.

- [ ] **Step 5: Add the Windows rescue set** to `catalog/windows.json` — `crystaldiskinfo`,
  `hwinfo`, `testdisk`, `7zip`, `windirstat`, `sysinternals`, `ventoy`, `rufus`, `usbipd-win`,
  each `"profiles": ["rescue"]`.

- [ ] **Step 5b: Add `gemini-cli` beside the existing `claude-code`** in all three catalogs, and
  put **both** in the `rescue` profile (decision B8). `claude-code` already exists — only its
  `profiles` array changes:

```jsonc
{
  "id": "gemini-cli",
  "name": "Gemini CLI",
  "description": "Google's terminal AI assistant",
  "provider": "npm",
  "package": "@google/gemini-cli",
  "verify": "gemini --version",
  "homepage": "https://github.com/google-gemini/gemini-cli",
  "requires": ["nodejs"],
  "profiles": ["workstation", "ai-coding", "rescue"]
}
```

  Add the matching test to `describe "profiles"`, because "both, independently" is the requirement
  and only a test keeps it true:

```bash
if it "rescue ships both AI CLIs"; then
    got="$(catalog_profile_defaults rescue)"
    assert_contains "$got" "claude-code"
    assert_contains "$got" "gemini-cli"
fi

if it "neither AI CLI depends on the other"; then
    # B8: an outage that kills one must leave the other installed.
    out="$(python3 -c "
import json
cat=json.load(open('catalog/linux.json'))
comps={c['id']: c for g in cat['categories'] for c in g['components']}
bad=[i for i in ('claude-code','gemini-cli')
     if i in comps and ({'claude-code','gemini-cli'} & set(comps[i].get('requires',[])))]
print(' '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "AI CLIs are coupled: $out"
fi
```

- [ ] **Step 6: Run the tests**

```bash
bash tests/run-tests.sh --filter profile && pwsh -File tests/run-tests.ps1 -Filter profiles
```

Expected: PASS, and the catalog schema test still passes because no new field was introduced.

- [ ] **Step 7: Dry run and read the plan**

```bash
bash setup.sh --profile rescue --dry-run
```

Expected: a plan listing the rescue components and nothing from `desktop`.

- [ ] **Step 8: Update `docs/profiles.md`** — add a `rescue` row to the profile table with one
  sentence on who it is for. Then commit:

```bash
git add catalog/ docs/profiles.md tests/
git commit -m "feat(catalog): add rescue profile and disaster-recovery components"
```

#### Task 2: gitignore the artifacts this feature will produce

**Files:** Modify `.gitignore`

- [ ] **Step 1: Add the missing rules** under the existing vendor-binary block. `*.exe` is already
  covered; `*.iso` is **not**, which is finding A4:

```gitignore
*.iso
*.iso.*
cache/
```

- [ ] **Step 2: Prove it** — with the 6.1 GB ISO still in `short_changes/`, confirm a copy of it
  inside the repo is ignored:

```bash
cp /dev/null ./probe.iso && git status --short --ignored -- probe.iso && rm probe.iso
```

Expected: `!! probe.iso`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore && git commit -m "chore: never track an ISO or the download cache"
```

---

#### Task 1B: the `local-ai` profile

**Files:** Modify `catalog/linux.json`, `catalog/windows.json`, `catalog/macos.json`;
`tests/run-tests.sh`, `tests/run-tests.ps1`, `docs/profiles.md`

**Interfaces:**
- Produces: profile id `local-ai`; component ids `ollama`, `ollama-model-<name>`.

- [ ] **Step 1: Write the failing tests.** The size warning is the point of this task, so it is
  what the tests guard:

```bash
if it "local-ai profile ships ollama"; then
    assert_contains "$(catalog_profile_defaults local-ai)" "ollama"
fi

if it "local-ai is NOT pulled in by the rescue profile"; then
    # Two orders of magnitude apart: ~400 MB of tools vs 4-20 GB of weights.
    assert_not_contains "$(catalog_profile_defaults rescue)" "ollama"
fi

if it "every model component states its download size"; then
    out="$(python3 -c "
import json,re
cat=json.load(open('catalog/linux.json'))
bad=[c['id'] for g in cat['categories'] for c in g['components']
     if c['id'].startswith('ollama-model-')
     and not re.search(r'[0-9]+(\.[0-9]+)?\s?GB', c.get('description',''))]
print(' '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "model entries with no size in the description: $out"
fi
```

- [ ] **Step 2: Run them and watch them fail** — `bash tests/run-tests.sh --filter local-ai`.
  Confirm the printed test count is non-zero before believing a pass.

- [ ] **Step 3: Add the profile key** to all three catalogs:

```jsonc
"local-ai": "Local AI inference with Ollama — no API key and no network, but models are 4-20 GB."
```

- [ ] **Step 4: Add the components.** `ollama` (provider `script`, the vendor's own installer, with
  its checksum verified per Task 3's helper), then one entry per model, each naming its size:

```jsonc
{
  "id": "ollama-model-llama3.2",
  "name": "Llama 3.2 3B",
  "description": "General-purpose local model, 2.0 GB download",
  "provider": "custom",
  "postInstall": "pull_ollama_model",
  "requires": ["ollama"],
  "verify": "ollama list | grep -q llama3.2",
  "homepage": "https://ollama.com/library/llama3.2",
  "profiles": ["local-ai"]
}
```

- [ ] **Step 5: Make the plan summary total the download size** before the confirmation, so a user
  sees "this will download 14 GB" while they can still say no.

- [ ] **Step 6: Run the filtered tests, update `docs/profiles.md`, commit.**

### Phase 2 — verified download and USB writing

#### Task 3: verified download helper

**Files:**
- Create: `lib/linux/download.sh`, `lib/windows/AutoOS.Download.psm1`
- Test: `tests/run-tests.sh` (`describe "verified download"`), `tests/run-tests.ps1`

**Interfaces:**
- Produces (bash): `fetch_verified <url> <dest> <sha256|-> <sig_url|-> <gpg_fpr|->` → 0 on a
  verified file, 2 on checksum mismatch, 3 on signature failure, 4 on transport failure.
- Produces (PowerShell): `Get-AutoOSVerifiedFile -Uri -Destination [-Sha256] [-SignatureUri]
  [-GpgFingerprint]` → the resolved `[string]` path; throws on any verification failure.
- Both must **delete the partial file** on any failure. A half-downloaded ISO left in the cache
  that a later run treats as complete is the worst possible outcome here.

- [ ] **Step 1: Write the failing test** in `tests/run-tests.sh`:

```bash
describe "verified download"

if it "a checksum mismatch fails and leaves nothing behind"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    fetch_verified "file://$tmp/src" "$tmp/out" \
        "0000000000000000000000000000000000000000000000000000000000000000" - - ; rc=$?
    if [[ $rc -eq 2 && ! -e "$tmp/out" ]]; then pass
    else fail "rc=$rc, out exists: $([[ -e $tmp/out ]] && echo yes || echo no)"; fi
    rm -rf "$tmp"
fi

if it "a matching checksum succeeds"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    sum="$(sha256sum "$tmp/src" | awk '{print $1}')"
    fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - && [[ -s "$tmp/out" ]] \
        && pass || fail "verified download did not produce the file"
    rm -rf "$tmp"
fi

if it "a cached, already-verified file is skipped, not refetched"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    sum="$(sha256sum "$tmp/src" | awk '{print $1}')"
    fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - >/dev/null
    out="$(fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - 2>&1)"
    assert_contains "$out" "skipped"
    rm -rf "$tmp"
fi
```

- [ ] **Step 2: Run it and watch it fail**

```bash
bash tests/run-tests.sh --filter "verified download"
```

Expected: FAIL, `fetch_verified: command not found`.

- [ ] **Step 3: Implement `lib/linux/download.sh`.** Shape, not prose — the third test is the
  idempotency requirement from AGENTS.md §4 and is why the cache check comes first:

```bash
#!/usr/bin/env bash
# Download a file and refuse to hand it back unless it verifies.
set -euo pipefail

fetch_verified() {
    local url="$1" dest="$2" want="${3:--}" sig_url="${4:--}" fpr="${5:--}"
    local tmp="${dest}.part"

    # Idempotency: a cached file that still matches is a skip, not a refetch.
    if [[ -s "$dest" && "$want" != "-" ]] && _sha256_matches "$dest" "$want"; then
        ui_line "skipped" "$(basename "$dest") already downloaded and verified"
        return 0
    fi

    rm -f "$tmp"
    # curl -L follows the mirror redirect BITS could not (A8); --fail turns an
    # HTML interstitial into a non-zero exit instead of a 6 GB HTML file (A7).
    if ! run curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
        rm -f "$tmp"; return 4
    fi
    if [[ "$want" != "-" ]] && ! _sha256_matches "$tmp" "$want"; then
        ui_err "checksum mismatch for $url"; rm -f "$tmp"; return 2
    fi
    if [[ "$sig_url" != "-" ]] && ! _gpg_verify "$tmp" "$sig_url" "$fpr"; then
        ui_err "signature verification failed for $url"; rm -f "$tmp"; return 3
    fi
    mv -f "$tmp" "$dest"
}
```

  Write `_sha256_matches` and `_gpg_verify` as separate functions in the same file;
  `_gpg_verify` imports the fingerprint into a throwaway `GNUPGHOME` under the cache dir so it
  never touches the user's keyring (hard rule 4, read-modify-write, applies to keyrings too).

- [ ] **Step 4: Run the tests**

```bash
bash tests/run-tests.sh --filter "verified download" && shellcheck lib/linux/download.sh
```

Expected: 3 PASS, shellcheck clean.

- [ ] **Step 5: Mirror it in `lib/windows/AutoOS.Download.psm1`** with the same three tests in
  `tests/run-tests.ps1`. Use `Invoke-WebRequest -MaximumRedirection 10` or
  `System.Net.Http.HttpClient` — **not** `Start-BitsTransfer` (A8) — and `Get-FileHash
  -Algorithm SHA256`. Function name `Get-AutoOSVerifiedFile`: `Get` is an approved verb, which
  `Download-` and `Ensure-` were not (A15).

- [ ] **Step 6: Commit**

```bash
git add lib/linux/download.sh lib/windows/AutoOS.Download.psm1 tests/
git commit -m "feat(download): verified, resumable, idempotent file fetch on both platforms"
```

#### Task 4: `catalog/images.json` and its schema test

**Files:**
- Create: `catalog/images.json`
- Modify: `tests/run-tests.sh`, `tests/run-tests.ps1`, `tests/helpers/catalog_has.py`

**Interfaces:**
- Produces: `images_load <path>` populating `IMG_ID[] IMG_NAME[] IMG_INDEX[] IMG_FILE[]
  IMG_SUMS[] IMG_SIG[] IMG_KEY[] IMG_KINDS[] IMG_WRITEMODE[] IMG_SIZE_GB[]`, mirroring the
  `CAT_*` convention `catalog_load` already uses.

- [ ] **Step 1: Write the failing test:**

```bash
describe "image catalog"

if it "the image catalog validates"; then
    out="$(python3 tests/helpers/images_validate.py catalog/images.json 2>&1)"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "$out"
fi

if it "no image pins a version number in its URL"; then
    # A9: a pinned version is stale the day it is written.
    if grep -qE '"index"[^,]*[0-9]+\.[0-9]+' catalog/images.json; then
        fail "an index URL contains a hardcoded version"
    else pass; fi
fi

if it "every image carries a checksum source"; then
    out="$(python3 -c "
import json,sys
bad=[i['id'] for i in json.load(open('catalog/images.json'))['images'] if not i.get('sums')]
print(' '.join(bad)); sys.exit(1 if bad else 0)")"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "no checksum source: $out"
fi

if it "a raw-write image is never offered to ventoy's copy path"; then
    out="$(python3 -c "
import json
bad=[i['id'] for i in json.load(open('catalog/images.json'))['images']
     if i.get('writeMode')=='raw' and 'live-persistent' in i.get('kinds',[])]
print(' '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "raw images claiming persistence: $out"
fi
```

- [ ] **Step 2: Run it and watch it fail** — `bash tests/run-tests.sh --filter "image catalog"`.
  Expected: FAIL, no such file.

- [ ] **Step 3: Write `tests/helpers/images_validate.py`** — reject a non-kebab `id`, an `index`
  that is not `https://`, an unknown `writeMode` (allowed: `hybrid`, `raw`), an unknown entry in
  `kinds` (allowed: `installer`, `live-persistent`, `full-os`), and a missing `homepage`. Make it
  fail loudly per AGENTS.md §5 rather than special-casing.

  **The two pseudo-entries are the one carve-out, and it must be explicit in the validator or the
  schema test fails on the entries Step 4 adds.** `custom-url` and `custom-local` are UI affordances
  that become a text field, not downloadable images — they have no `index`, no `homepage` and no
  `sums` by construction. Encode that as a named set, not as a blanket "skip if missing":

```python
PSEUDO = {"custom-url", "custom-local"}   # UI text fields, not downloadable images

def validate(entry):
    if entry["id"] in PSEUDO:
        # Must still be kebab-case, must still declare kinds, must NOT claim a
        # checksum it cannot have — a pseudo-entry with `sums` is a real bug.
        require_kebab(entry["id"]); require_kinds(entry)
        if entry.get("sums") or entry.get("index"):
            fail(f"{entry['id']} is a pseudo-entry but declares a download source")
        return
    require_https_index(entry); require_homepage(entry); require_sums(entry)
    ...
```

- [ ] **Step 4: Write `catalog/images.json`** with these entries, all version-free (B4). Use
  Ubuntu's `meta-release-lts` / the `releases.ubuntu.com` index for `resolve: ubuntu-lts`:

  `ubuntu-desktop-lts`, `ubuntu-server-lts`, `debian-netinst-stable`, `fedora-workstation`,
  `systemrescue`, `proxmox-ve` (`writeMode: raw`, `kinds: ["installer"]`), `gparted-live`,
  `clonezilla-live`, `memtest86plus`, plus `custom-url` and `custom-local` as pseudo-entries the
  UI turns into a text field.

  SystemRescue's `index` is `https://fastly-cdn.system-rescue.org/releases/` — **not** the
  SourceForge `/download` interstitial that finding A7 identified.

- [ ] **Step 5: Run the tests** — `bash tests/run-tests.sh --filter "image catalog"`. Expected:
  4 PASS.

- [ ] **Step 6: Commit**

```bash
git add catalog/images.json tests/
git commit -m "feat(catalog): OS images as data, resolved at run time, never version-pinned"
```

#### Task 5: USB enumeration and the safety guard

This is the task where a bug destroys someone's data. It gets the most tests and no shortcuts.

**Files:**
- Create: `lib/linux/usb.sh`, `lib/windows/AutoOS.Usb.psm1`, `tests/helpers/fake_usb.py`
- Test: `tests/run-tests.sh` (`describe "usb safety"`), `tests/run-tests.ps1`

**Interfaces:**
- Produces (bash): `usb_list` → TSV `device<TAB>model<TAB>size_bytes<TAB>removable<TAB>bus`;
  `usb_guard <device>` → 0 if safe, 1 with a named reason on stderr otherwise.
- Produces (PowerShell): `Get-AutoOSUsbDevice` → objects with `DeviceId, Model, SizeBytes, Bus,
  IsRemovable, IsSystem`; `Assert-AutoOSUsbSafe -DeviceId` → throws with a named reason.
- Both read `AUTOOS_FAKE_LSBLK` / `$env:AUTOOS_FAKE_DISKS` when set, so the suite runs against
  synthetic devices and never the live machine (AGENTS.md §5).

- [ ] **Step 1: Write the failing tests.** These are the four ways this feature destroys a laptop:

```bash
describe "usb safety"

if it "refuses the disk holding the root filesystem"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" usb_guard /dev/sda 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"root filesystem"* ]] && pass || fail "rc=$rc: $out"
fi

if it "refuses a non-removable internal disk"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb internal_nvme)" usb_guard /dev/nvme0n1 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not removable"* ]] && pass || fail "rc=$rc: $out"
fi

if it "refuses a disk with a mounted partition"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_mounted)" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"mounted"* ]] && pass || fail "rc=$rc: $out"
fi

if it "refuses a stick smaller than the image"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb tiny_stick)" \
           AUTOOS_IMAGE_BYTES=8000000000 usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"too small"* ]] && pass || fail "rc=$rc: $out"
fi

if it "accepts a real removable stick that is big enough"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_IMAGE_BYTES=4000000000 \
        usb_guard /dev/sdb && pass || fail "rejected a valid target"
fi

if it "a USB SSD reporting as fixed is still offered"; then
    # A10: DriveType/removable alone misses USB SSDs. Bus type is the signal.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_ssd_fixed)" usb_list)"
    assert_contains "$out" "/dev/sdb"
fi
```

- [ ] **Step 2: Run them and watch them fail** — `bash tests/run-tests.sh --filter "usb safety"`.
  Expected: 6 FAIL, `usb_guard: command not found`.

- [ ] **Step 3: Write `tests/helpers/fake_usb.py`** emitting `lsblk -J -b -o
  NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE` JSON for each named fixture above. Synthetic system
  objects, per AGENTS.md §5 — never the live machine.

- [ ] **Step 4: Implement `usb_list` and `usb_guard`.** Order matters: check root-disk first, so
  the most dangerous case has the shortest path to a refusal.

```bash
usb_guard() {
    local dev="$1" root_disk
    root_disk="$(_disk_of_mountpoint /)"
    [[ "$dev" == "$root_disk" ]] && { ui_err "$dev holds the root filesystem"; return 1; }
    _is_removable_or_usb "$dev" || { ui_err "$dev is not removable and not on the USB bus"; return 1; }
    _has_mounted_partition "$dev" && { ui_err "$dev has a mounted partition — unmount it first"; return 1; }
    local size; size="$(_size_bytes "$dev")"
    (( size >= ${AUTOOS_IMAGE_BYTES:-0} )) || { ui_err "$dev is too small for this image"; return 1; }
    return 0
}
```

  `_is_removable_or_usb` must accept `RM=0` when `TRAN=usb` — that is finding A10 fixed.

- [ ] **Step 5: Run the tests** — expected 6 PASS, `shellcheck lib/linux/usb.sh` clean.

- [ ] **Step 6: Mirror in `AutoOS.Usb.psm1`** with the same six tests. Enumerate with `Get-Disk`
  (`BusType -in 'USB','SCSI'`) joined to `Get-Partition`, and treat `IsBoot`/`IsSystem` as an
  automatic refusal. Read `$env:AUTOOS_FAKE_DISKS` (JSON) in place of `Get-Disk` when set.

- [ ] **Step 7: Add the elevation guard (B10)** — `usb_require_elevation` / `Assert-AutoOSElevated`,
  returning a structured result so the browser UI can render it rather than only throwing:

```bash
if it "refuses to plan a write without root"; then
    out="$( AUTOOS_SUDO="" AUTOOS_FAKE_UID=1000 usb_require_elevation 2>&1 )"; rc=$?
    [[ $rc -ne 0 && "$out" == *"sudo"* ]] && pass || fail "rc=$rc: $out"
fi

if it "is satisfied when already root"; then
    AUTOOS_SUDO="" AUTOOS_FAKE_UID=0 usb_require_elevation && pass || fail "refused root"
fi
```

  On Windows use
  `([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)`.
  **Do not self-elevate** — a re-launch loses the session's log file, which is the thing the user
  needs when a write fails.

- [ ] **Step 8: Commit**

```bash
git add lib/linux/usb.sh lib/windows/AutoOS.Usb.psm1 tests/
git commit -m "feat(usb): device enumeration, a guard that refuses the system disk, elevation check"
```

#### Task 6: the write plan, and `--dry-run` that touches nothing

**Files:** Modify `lib/linux/usb.sh`, `lib/windows/AutoOS.Usb.psm1`; modify `setup.sh`, `setup.ps1`

**Interfaces:**
- Produces: `usb_plan <image_id> <kind> <engine> <device>` → the exact command lines it would run,
  one per line, on stdout; exit 0. **Runs nothing.**
- Produces: `New-AutoOSUsbPlan -ImageId -Kind -Engine -DeviceId` → `[string[]]` of the same.

- [ ] **Step 1: Write the failing test** — the one that makes the whole feature testable:

```bash
describe "usb planning"

if it "a dry run names the device and writes nothing"; then
    before="$(find /tmp -newer /tmp -maxdepth 0 2>/dev/null; echo)"
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)"
    assert_contains "$out" "/dev/sdb"
    assert_contains "$out" "Ventoy2Disk.sh"
    assert_not_contains "$out" "installed"
fi

if it "a raw image is never planned onto ventoy's copy path"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image proxmox-ve \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)" || true
    assert_contains "$out" "raw"
fi

if it "a full-os kind is refused on an engine that cannot build one"; then
    out="$(AUTOOS_DRY_RUN=1 bash setup.sh --create-usb --image ubuntu-desktop-lts \
           --kind full-os --engine ventoy --usb-device /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"cannot build"* ]] && pass || fail "rc=$rc: $out"
fi
```

- [ ] **Step 2: Run and watch fail** — `bash tests/run-tests.sh --filter "usb planning"`.
  Expected: FAIL, `--create-usb: unknown option`.

- [ ] **Step 3: Add the flags to `setup.sh`** beside the existing ones at line 75:

```bash
--create-usb)       DO_CREATE_USB=1; shift ;;
--image)            USB_IMAGE="${2:-}"; shift 2 ;;
--kind)             USB_KIND="${2:-installer}"; shift 2 ;;
--engine)           USB_ENGINE="${2:-}"; shift 2 ;;
--usb-device)       USB_DEVICE="${2:-}"; shift 2 ;;
--wipe-target-disk) USB_WIPE=1; shift ;;
--list-usb)         LIST_USB=1; shift ;;
```

- [ ] **Step 4: Implement `usb_plan`.** It emits commands; it never runs them. The engine/kind
  compatibility matrix from B3 lives in `catalog/engines.json`, so an unsupported combination is a
  data lookup and a clear refusal, not an exception halfway through a write.

- [ ] **Step 5: Run the tests** — expected 3 PASS.

- [ ] **Step 6: Add the "dry run leaves the filesystem untouched" assertion** to the existing
  `describe "end-to-end (dry run only)"` block, covering `--create-usb` alongside the installer.

- [ ] **Step 7: Refuse the platforms and architectures that cannot do this (B11, B13).**
  `setup.sh` is the shared Linux **and macOS** entry point, and Ventoy ships x86_64 binaries only:

```bash
if it "macOS is refused cleanly, not left to fail inside usb_list"; then
    out="$( SYS_OS=macos bash setup.sh --create-usb --image ubuntu-desktop-lts \
            --kind installer --engine native --usb-device /dev/disk2 2>&1 )"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not supported on macOS"* ]] && pass || fail "rc=$rc: $out"
fi

if it "the ventoy engine is hidden on arm64, not offered and broken"; then
    out="$( SYS_ARCH=arm64 bash setup.sh --create-usb --list-engines 2>&1 )"
    assert_not_contains "$out" "ventoy"
    assert_contains     "$out" "native"
fi
```

  `catalog/engines.json` therefore carries an `arch` array, exactly like a catalog component, and
  an engine that cannot run on the detected machine is **hidden**, not shown-and-failing
  (AGENTS.md §3).

- [ ] **Step 8: Take the existing run lock, do not invent a second one (B13).** `serve.py` already
  serialises component installs through `RUN`/`LOCK` (`lib/linux/serve.py:421-427`). USB creation
  joins that same lock: a write requested while an install is running returns `409`, and so does a
  second write. The terminal path takes a lock file in the cache directory for the same reason.

```bash
if it "a second write is refused while one is running"; then
    out="$( AUTOOS_FAKE_RUN_ACTIVE=1 usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb 2>&1 )"
    rc=$?
    [[ $rc -ne 0 && "$out" == *"already in progress"* ]] && pass || fail "rc=$rc: $out"
fi
```

- [ ] **Step 9: Make `--undo` say what it cannot do (B13).** A USB write does not participate in
  `--save-state` / `--from-state` / `--undo`. Silently ignoring a flag the user reasonably expects
  to cover their last action is worse than refusing it:

```bash
if it "undo states plainly that a USB write cannot be undone"; then
    out="$( bash setup.sh --undo --dry-run 2>&1 )"
    assert_contains "$out" "USB"
fi
```

- [ ] **Step 10: Commit**

```bash
git add lib/linux/usb.sh lib/windows/AutoOS.Usb.psm1 setup.sh setup.ps1 catalog/engines.json tests/
git commit -m "feat(usb): plan-then-execute, platform and arch guards, one shared run lock"
```

#### Task 7: execute the plan — Ventoy and raw, Linux first

**Files:** Modify `lib/linux/usb.sh`; modify `lib/windows/AutoOS.Usb.psm1`

**Interfaces:**
- Consumes: `usb_plan` (Task 6) on **stdin, one command per line**; `fetch_verified` (Task 3);
  `usb_guard` (Task 5); `templates/rescue-bootstrap.sh` (Task 9, which is why Task 9 runs first).
- Produces (bash):
  - `usb_execute <device>` — reads the plan on stdin, runs it, returns 0 only when every step
    exited 0 **and** the resulting filesystem reads back (B15).
  - `usb_write_ventoy <device>` — installs Ventoy; returns 0 or a named error.
  - `usb_write_raw <device> <image_path> <image_bytes>` — raw write; emits its own `NN%` progress
    lines because `dd` emits none (B14).
  - `usb_copy_image <device> <image_path> [extra_file...]` — copies the ISO **and the rescue
    templates** onto a Ventoy stick; refuses on a raw-written stick, which is read-only (A11).
- Produces (PowerShell): `Invoke-AutoOSUsbPlan -DeviceId -Plan [string[]]` with the same contract.

- [ ] **Step 1: Write the failing test** — execution is asserted through the command runner, never
  against a disk:

```bash
if it "executing a plan runs exactly the planned commands, in order"; then
    plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
            usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    ran="$(AUTOOS_DRY_RUN=1 AUTOOS_TRACE=1 usb_execute /dev/sdb <<<"$plan" 2>&1)"
    assert_eq "$(echo "$ran" | grep -c '^TRACE ')" "$(echo "$plan" | wc -l)"
fi

if it "a failed write never leaves the stick claimed as successful"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"echo x" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" != *"Ready to boot"* ]] && pass || fail "reported success on failure"
fi
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement `usb_write_ventoy`** — download Ventoy from its GitHub release index,
  verify its published SHA256, extract to the cache, then `Ventoy2Disk.sh -i -g "$dev"`; then
  `usb_copy_image` mounts the resulting exFAT partition and copies the ISO onto it.
  `usb_write_raw` is `dd bs=4M status=progress conv=fsync` for `writeMode: raw` images.

- [ ] **Step 4: Implement the persistence path** for `kind=live-persistent` — Ventoy's persistence
  plugin: create a `.dat` file of the requested size with `dd`, register it in
  `ventoy/ventoy.json`. Default 16 GB, capped at half the stick.

- [ ] **Step 5: Hand the real-hardware check to the human partner. Do not perform it yourself.**

  **An implementing agent must never write to a physical disk.** Hard rule 3 requires a destructive
  action to be chosen by *the user*, and an agent selecting a block device off a checklist is not
  that — nothing in this repository lets an agent confirm by eye that `/dev/sdb` is the stick the
  human meant rather than their backup drive. AGENTS.md §7 still demands the feature be run for
  real, so the split is:

  - The agent produces the exact command, runs the full suite, and stops.
  - The agent reports `DONE_WITH_CONCERNS`, naming the unverified path in its report.
  - The human partner runs it against a stick they can see, boots it, and reports back.
  - The result is recorded in the commit body or a follow-up commit, naming the machine.

  A task is reviewable and mergeable without the hardware check; it is not *finished* without it.

- [ ] **Step 6: Run it a second time** and confirm it reports `skipped` for the already-verified
  ISO in the cache and re-writes only what changed.

- [ ] **Step 7: Commit.**

#### Task 8: the WSL engine on Windows

**Files:** Create `lib/windows/AutoOS.Wsl.psm1`; modify `lib/windows/AutoOS.Usb.psm1`

**Interfaces:**
- Produces: `Get-AutoOSWslState` → `@{ Installed; Version; Distros; Default }`;
  `Mount-AutoOSUsbInWsl -DeviceId` → the `/dev/sdX` path inside WSL, or throws;
  `Dismount-AutoOSUsbFromWsl -Handle` → always safe to call, always called from `finally`.

- [ ] **Step 1: Write the failing test** in `tests/run-tests.ps1`, against synthetic state.

  **This suite is deliberately not Pester** — AGENTS.md §5 says so, and `tests/run-tests.ps1`
  defines its own DSL at lines 44-82: `Describe-Group`, `Test-Case`, `Pass`, `Skip`,
  `Assert-Equal`, `Assert-True`, `Assert-Contains`, `Assert-NotContains`. There is no `It` and no
  `Should`; using them fails with "the term 'It' is not recognized". Note also that
  `Assert-Contains` tests **collection membership** (`-contains`), not substring — for a substring
  check use `Assert-True ($text -match 'pattern')`.

```powershell
Describe-Group 'wsl engine planning'

Test-Case 'falls back to usbipd when wsl --mount rejects a flash drive' {
    $env:AUTOOS_FAKE_WSL_MOUNT_RESULT = '0x8007000f'
    try {
        $plan = New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' `
                                  -Engine 'wsl' -DeviceId 'PHYSICALDRIVE2'
        Assert-True (($plan -join [Environment]::NewLine) -match 'usbipd attach --wsl') `
                    'plan never falls back to usbipd'
    } finally { $env:AUTOOS_FAKE_WSL_MOUNT_RESULT = $null }
}

Test-Case 'the last planned step is always a detach' {
    $plan = New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' `
                              -Engine 'wsl' -DeviceId 'PHYSICALDRIVE2'
    Assert-True ($plan[-1] -match 'usbipd detach') `
                "last planned step was [$($plan[-1])], not a detach"
}

Test-Case 'refuses the wsl engine when WSL2 is absent' {
    $env:AUTOOS_FAKE_WSL_STATE = '{"Installed":false}'
    try {
        $threw = $false
        try {
            New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' `
                              -Engine 'wsl' -DeviceId 'PHYSICALDRIVE2' | Out-Null
        } catch { $threw = $_.Exception.Message -match 'WSL2 is not installed' }
        Assert-True $threw 'planning succeeded on a machine with no WSL2'
    } finally { $env:AUTOOS_FAKE_WSL_STATE = $null }
}
```

  `New-AutoOSUsbPlan` is a **planner**: it returns `[string[]]` and runs nothing, so these tests
  need no `-WhatIf` switch and Task 6 must not add one — its signature stays exactly
  `New-AutoOSUsbPlan -ImageId -Kind -Engine -DeviceId`. The `finally` blocks matter: a fake-state
  environment variable left set leaks into every later test in the same process.

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement the two-tier attach from B2.** Try `wsl --mount \\.\PHYSICALDRIVE<N>
  --bare` first; on any non-zero exit fall back to `usbipd list` → `usbipd bind --busid` →
  `usbipd attach --wsl --busid`. Resolve the resulting device inside WSL by **size and model**,
  never by assuming `/dev/sdb` — WSL renumbers.

- [ ] **Step 4: Put the detach in `finally`.** A bound-and-attached device that is never detached
  disappears from Windows until reboot; that is a worse outcome than a failed write.

- [ ] **Step 5: Make `usbipd-win` a `requires`** of the `wsl` engine in `catalog/engines.json` so
  the existing dependency resolver installs it rather than the user hitting a cryptic failure.

- [ ] **Step 6: Implement `full-os`** — inside WSL, `mmdebstrap` the chosen suite onto the stick,
  then `grub-install --removable --target=x86_64-efi`. This is the path that needs no ISO at all
  and is why the WSL engine exists.

- [ ] **Step 7: Run the tests. Hand the real-hardware check to the human partner**, exactly as
  Task 7 Step 5 sets out — the agent never attaches or writes a physical device, it reports
  `DONE_WITH_CONCERNS` naming the unverified path.

- [ ] **Step 8: Commit.**

#### Task 9: sanitised answer-file templates

**Files:** Create `templates/user-data.example`, `templates/preseed.cfg.example`,
`templates/rescue-bootstrap.sh`

- [ ] **Step 1: Write the failing test** — this one guards hard rule 1 forever:

```bash
describe "answer file templates"

if it "no template contains a credential"; then
    if grep -rnE '(\$6\$|password[[:space:]]+[^ ]|passwd/user-password)' templates/ \
       | grep -v 'CHANGE-ME'; then
        fail "a template carries something that looks like a real credential"
    else pass; fi
fi

if it "no template wipes a disk without being asked"; then
    if grep -rqE 'layout:|partman-auto/method' templates/ && \
       ! grep -rq 'AUTOOS_WIPE_TARGET_DISK' templates/; then
        fail "automatic partitioning is not gated behind the wipe flag"
    else pass; fi
fi
```

- [ ] **Step 2: Run and watch fail** against the files copied from `short_changes/`.

- [ ] **Step 3: Write the templates.** Password becomes the literal `CHANGE-ME-AT-CREATION-TIME`,
  substituted at USB-creation time with a generated passphrase shown once (B7). Storage section is
  interactive by default; the `direct` layout is emitted only when `--wipe-target-disk` was passed,
  and the confirmation names the disk model and size.

- [ ] **Step 4: Write `templates/rescue-bootstrap.sh`** fixing A12, A13, A14: install in small
  groups with `|| true` per group and a summary of what was missing; `command -v` guard before
  every network step so an offline stick still gets the packages that are on the ISO; a marker
  file so a second run reports `skipped`.

  It installs **both** AI CLIs (B8), each isolated so one failure is not both:

```bash
install_ai_clis() {
    command -v npm >/dev/null || { ui_warn "no npm — skipping both AI CLIs"; return 0; }
    local cli failed=""
    for cli in "@anthropic-ai/claude-code:claude" "@google/gemini-cli:gemini"; do
        local pkg="${cli%%:*}" bin="${cli##*:}"
        if command -v "$bin" >/dev/null; then
            ui_line "skipped" "$bin already installed"          # AGENTS.md §4
        elif npm install -g "$pkg"; then
            ui_line "installed" "$bin"
        else
            failed+="$bin "                                      # never fails the whole run
        fi
    done
    [[ -z "$failed" ]] || ui_warn "AI CLIs unavailable: $failed"
}
```

  And `/etc/profile.d/autoos-ai.sh` carries **commented** key lines only — never a value:

```sh
alias ai="claude"
# export ANTHROPIC_API_KEY="..."
# export GEMINI_API_KEY="..."
```

- [ ] **Step 5: Validate the autoinstall against the real schema:**

```bash
cloud-init schema --config-file templates/user-data.example
```

Expected: `Valid schema`. (This is what would have caught A2 immediately.)

- [ ] **Step 6: Run the tests, then commit.**

---

### Phase 3 — the interfaces

#### Task 10: terminal UI

**Files:** Modify `lib/windows/AutoOS.Ui.psm1`, `lib/linux/ui.sh`, `setup.sh`, `setup.ps1`

- [ ] **Step 1: Write the failing test** in the existing `describe "terminal interactive UI"`
  block — assert the chooser renders every engine available on this platform and refuses an
  unavailable one, driving it with a scripted stdin exactly as the existing tests do.
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Add a `Create installer USB` entry to the top-level menu**, then a four-step
  chooser: image → kind → engine → device. Reuse the existing interactive selection helpers added
  in `f8ae521`; do not write a second selection widget.
- [ ] **Step 4: Make the confirmation summary name the device, its model, its size, and the words
  "all data on it will be destroyed".** Hard rule 3.
- [ ] **Step 5: Run the tests, dry-run it by hand, read the output, commit.**

#### Task 11: browser UI

**Files:** Modify `lib/linux/serve.py`, `lib/windows/AutoOS.Serve.psm1`, `web/index.html`

- [ ] **Step 1: Write the failing test** in `describe "browser UI"` — assert the three new routes
  exist and that `/api/usb/create` refuses a device that fails `usb_guard`, and that the page
  contains the button:

```bash
if it "the action bar offers USB creation"; then
    grep -q 'id="createUsb"' web/index.html && pass || fail "no create-usb button"
fi

if it "the usb dialog is hidden by the hidden attribute, not only by a class"; then
    # AGENTS.md §6: an author `display:` rule beats [hidden]{display:none}. This
    # shipped twice in one afternoon already.
    grep -q '\.usb-dialog\[hidden\]{display:none}' web/index.html && pass \
        || fail "usb dialog will render open on every load"
fi

# `usb_create_response(body: dict) -> tuple[int, dict]` is a MODULE-LEVEL function.
# `Handler` is a BaseHTTPRequestHandler subclass whose helpers all take `self`
# (serve.py:268-289), so `Handler._usb_create_body(dict)` would pass the dict AS
# self and raise TypeError. The repo already solves this the right way: `classify()`
# at serve.py:175 is module-level precisely so a test can import and call it.
if it "the create endpoint refuses an unguarded device"; then
    out="$(python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response          # module-level, NOT a Handler method
print(usb_create_response({"device": "/dev/sda", "image": "ubuntu-desktop-lts"}))
PY
)"
    assert_contains "$out" "root filesystem"
fi
```

- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Add the button to the action bar** at `web/index.html:1002-1013`, beside
  `Install selected`:

```html
<button class="secondary" id="createUsb" type="button">Create installer USB</button>
```

- [ ] **Step 4: Add the `<dialog>` wizard** — image, kind, engine, device, with the device list
  fetched from `/api/usb/devices` and refreshed on open. **Give it its own
  `[hidden]{display:none!important}` rule** — the `.usb-dialog` class sets `display:flex`, and
  AGENTS.md §6 records this exact bug shipping twice.
- [ ] **Step 5: Wire progress** through the existing `/api/log` poller. USB writing streams
  percentage from `dd status=progress` and from Ventoy; feed it into the existing current-item bar
  rather than inventing a second one.
- [ ] **Step 6: Make the confirm step a real confirmation** — the device model, size and
  "destroys all data" in the dialog, not a `window.confirm`.

- [ ] **Step 6b: Surface elevation and the run lock in the UI (B10, B13).** `/api/usb/devices`
  returns `"elevated": bool`; when false the button renders **disabled with the reason**, because
  offering an action that cannot succeed is the failure mode B10 exists to prevent.
  `/api/usb/create` returns `409` while any run — install or write — holds the lock, and the page
  shows that as "a run is already in progress", reusing the string the install path already uses.

```bash
if it "the create button is disabled when the server is not elevated"; then
    grep -q 'elevated' web/index.html && pass || fail "page never reads the elevated flag"
fi
```
- [ ] **Step 7: Drive it with Playwright** before claiming it works — open the page, open the
  dialog, assert the device list renders and the button is disabled until a device is chosen.
- [ ] **Step 8: Run the tests, commit.**

#### Task 12: documentation and the paper trail

**Files:** Create `docs/usb-creator.md`, `docs/decisions/0004-ventoy-and-wsl-not-rufus.md`;
modify `README.md`, `CHANGELOG.md`, `docs/README.md`

- [ ] **Step 1: Write ADR 0004** — Context: Rufus has no CLI, so it cannot be dry-run or tested.
  Decision: Ventoy is the default engine, WSL + usbipd-win is the Windows power path, Rufus stays
  a catalog entry. Consequences: a Ventoy dependency, a `usbipd` dependency on the WSL path, and
  images that need a raw write are handled by a separate writer.
- [ ] **Step 2: Write `docs/usb-creator.md`** — one page: the three artifact kinds, the engine
  table, the flags, and what to do when a stick will not boot.
- [ ] **Step 3: Update `README.md`** for the new flags (AGENTS.md §7 requires it) — **link** to
  `catalog/images.json` rather than restating the image list, which is finding A23.
- [ ] **Step 4: Run `python3 tests/check-links.py .`** — expected: every relative link resolves.
- [ ] **Step 5: Update `CHANGELOG.md`, commit.**

---

### Phase 4 — WSL as an install target *(split into its own plan before starting)*

Sketched here only so Phase 1–3 do not paint it into a corner.

- **Task 13:** add `wsl` to `catalog/windows.json` — `wsl --install --no-distribution`, then
  `wsl --install -d Ubuntu`. Needs reboot-pending detection, which `AutoOS.Detect.psm1` does not
  have yet; a component that requires a reboot must report `pending`, not `installed`.
- **Task 14:** add `targets` / `wslId` to the Windows catalog schema, defaulting to `["host"]` so
  every existing entry keeps its current meaning and the schema test stays green.
- **Task 15:** `Invoke-AutoOSWslCommand` — run a resolved *Linux* catalog plan inside
  `wsl -d <distro> -u root -- bash -lc`, reusing `lib/linux/install.sh` verbatim. Verification runs
  inside WSL too, so `verify` means what it says.
- **Task 16:** a per-component Host/WSL toggle in both UIs, and a `--target wsl` flag. The plan
  output must show the target per line — a plan that does not say *where* something is being
  installed is worse than no plan.

---

## Part E — Tools and pipelines worth adopting

| Tool | What it buys | Where it goes |
|---|---|---|
| **Ventoy** (GPLv3) | Multi-boot from copied ISOs; a real CLI on both platforms; persistence plugin. Turns "another distro" from a 20-minute reflash into a file copy. | Task 7, the default engine |
| **usbipd-win** (MIT) | The only way to get a USB *flash drive* into WSL2 as a block device. | Task 8 |
| **mmdebstrap** | Builds a complete bootable Debian/Ubuntu onto a stick with no ISO and no reboot. Faster and smaller than any installer. | Task 8, `full-os` |
| **`cloud-init schema --config-file`** | Validates the autoinstall file offline. Catches A2 in one second. | Task 9 CI gate |
| **`zsync`** | Ubuntu publishes `.zsync` beside every ISO; a point-release refresh downloads the delta, not 6 GB. | Task 3, optional fast path |
| **`aria2c`** | Multi-connection, resumable, torrent-aware. Typically several times faster than a single HTTPS stream on mirror infrastructure. | Task 3, used when present |
| **Vendor `SHA256SUMS` + `SHA256SUMS.gpg`** | Ubuntu, Debian and Fedora all publish both. Verification is a solved problem; A6 exists because nobody used it. | Task 3 |
| **Fido.ps1** (pbatard, GPLv3) | The official Windows-ISO retrieval script Rufus itself uses. Only needed if Windows images are ever added. | future |
| **`f3`** (`f3probe`, `f3write`) | Detects counterfeit flash before you trust a rescue stick to it. | Task 1, rescue profile |
| **GitHub Actions matrix** | Run both suites on `windows-latest` and `ubuntu-latest` per PR. `.github/workflows/ci.yml` already exists — extend it rather than adding a workflow. | Task 12 |

Two things deliberately **not** adopted: **balenaEtcher** (Electron, no meaningful CLI, bundles
analytics) and **WoeUSB** (Windows-to-Linux only — the wrong direction for this feature).

---

## Part F — Decisions

### Settled 2026-09-11

1. **Ventoy is the default engine.** Accepted. It means a GPLv3 third-party binary fetched at run
   time; Task 7 verifies its published SHA256 like any other download.
2. **Both Claude Code and Gemini CLI ship** — in the catalog on all three platforms and on the
   rescue stick. See B8 for the two constraints that follow (independent failure, no keys written).
3. **Phase 4 (WSL as an install target) becomes its own plan.** Phase 1–3 land first. Tasks 13–16
   below are a sketch to stop Phase 1–3 painting it into a corner, not an executable phase.
4. **Execution is subagent-driven** — one fresh subagent per task, reviewed between tasks, per
   `superpowers:subagent-driven-development`.

### Still open — answer before Phase 2 Task 7

5. **Is `full-os` (a portable installed OS on the stick) in scope for the first release?** It is
   the most valuable artifact kind and the most work, and it exists only on the WSL and native
   engines. *Recommended: yes, as the last task of Phase 2* — cut it if Phase 2 runs long. It is
   sequenced last precisely so cutting it costs nothing already built.
6. **What happens to `short_changes/`?** The package lists move into the catalog under Task 1; the
   6.1 GB ISO must not come near the repository. *Recommended: keep the directory outside the repo
   as a scratch area, and delete the ISO once Task 3's verified cache is working.* No task depends
   on this, so it can wait — but `.gitignore` gains its `*.iso` rule in Task 2 either way.
