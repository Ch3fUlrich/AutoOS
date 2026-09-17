# Handoff — installer USB, `rescue` and `local-ai` profiles

> **You are an AI agent picking this work up cold.** Read this whole file before touching anything.
> It is the only durable record of the previous session: its working ledger lived in a gitignored
> worktree that has since been deleted. What is written here is what survived.
>
> Written 2026-09-17. **Assume every fact below may be stale.** Section 2 tells you how to check.

---

## 0. Safety — read this before any command

These are not preferences. Each one is here because something nearly went wrong.

1. **Never reboot, restart or shut down this machine** — not as a step, not "quickly", not in a
   script. The human partner reboots it manually, themselves. If a task appears to need a reboot,
   stop, say so, and find a path that does not.
2. **Never write to a physical disk from a test or an agent.** Writing a USB stick destroys it.
   A real write needs the human partner to name the device explicitly. **On Windows Git Bash (MSYS),
   `/dev/sdX` maps to REAL physical disks** — a path that looks like a harmless fake is not. Gate
   test isolation on `AUTOOS_FAKE_LSBLK` being set, never on `[[ -e "$dev" ]]`.
3. **This repository is public.** No token, key, password, hash, hostname, email or absolute path
   containing a username in any tracked file. It has leaked credentials once already.
4. **`main` may have someone else's uncommitted work in it.** Check `git status` in the main
   checkout before doing anything there. Do your work in a worktree.
5. **Never pass `--dangerously-skip-permissions` to `agy`**, and never hand an agent unrestricted
   tool access on this machine for a task whose output is only text.

Read [`AGENTS.md`](../../AGENTS.md) next. It is the canonical rule set and overrides anything here.

---

## 1. The goal

Let AutoOS **build a bootable Linux USB rescue stick** — installer, persistent-live, or full portable
OS — from the terminal menu, from the browser UI, and non-interactively, on Windows and Linux; and
ship two profiles that install onto a machine directly:

- **`rescue`** — a disaster-recovery toolkit: disk and data recovery, hardware diagnosis, Windows
  repair, network tools, plus AI assistants to help diagnose a broken device.
- **`local-ai`** — Ollama and local models, so the AI tooling still works on a machine with **no
  network and no API key**, which is exactly the machine a rescue stick meets.

The human partner's framing: *a Ubuntu stick with rescue apps and AI apps, with the goal to detect
and repair broken devices.*

The full design and rationale is [`docs/plans/2026-09-11-installer-usb-and-rescue-profile.md`](../plans/2026-09-11-installer-usb-and-rescue-profile.md).
Part A is the review that motivated it; Part B is the design; Part D is the task list.

---

## 2. First: find out what changed since this was written

Do not trust sections 3–5 until you have run this. The previous handoff attempt nearly shipped
stale facts, because five days had passed and `main` had moved in ways that mattered.

```bash
BRANCH=feat/installer-usb-rescue-profile
git fetch origin

# 1. Has the branch already been merged? If yes, most of section 4 no longer applies.
git branch -r --merged origin/main | grep -c "$BRANCH"        # 1 = merged, 0 = not

# 2. What has landed on main since the branch point, and who wrote it?
git log --oneline --format='%h %ad %an  %s' --date=short "$(git merge-base origin/main origin/$BRANCH)"..origin/main

# 3. Has anyone pushed to the branch since this file was committed?
git log --oneline "$BRANCH"..origin/"$BRANCH"

# 4. Does main have uncommitted work in progress? (run in the MAIN checkout)
git status --short

# 5. The real conflict set, computed without touching the working tree.
#    Do NOT pipe this into `head` - see lesson L1 in section 6.
git merge-tree --write-tree --name-only origin/main origin/"$BRANCH"
echo "merge-tree exit: $?   (0 = clean, 1 = conflicts)"
```

**How to read the results:**

- If step 1 prints `1`, the branch is merged: skip section 4 and go straight to section 5.
- For every commit step 2 shows, **read its message and skim its diff**. Look specifically for
  anything touching `catalog/*.json`, `setup.sh`, `lib/*/AutoOS.Install.psm1`, `lib/linux/install.sh`,
  `tests/run-tests.*`, or anything named `rescue`, `local-ai`, `ollama`, `agy`, `usb`, or `image`.
  Another agent working on `main` independently added a `rescue` profile once already; it can
  happen again.
- A file that **auto-merges is not thereby correct.** Git detects textual conflicts, not semantic
  ones. Section 4 has a live example.

---

## 2b. What the second 2026-09-17 session changed (read after section 2)

A later session on the same day owned section 5's item 1 — and found the gap was bigger than
written: not only did `usb_plan` never call `image_resolve`, **neither entry point ever executed
a plan.** `--create-usb` without `--dry-run` printed the plan and exited 0; `usb_execute` /
`Invoke-AutoOSUsbPlan` had no caller outside the tests. Both halves are now wired, on both
platforms, test-first:

- **`usb_fetch_image <image_id> <dest>`** (`lib/linux/usb.sh`) / **`Invoke-AutoOSUsbFetchImage`**
  (`AutoOS.Usb.psm1`): `image_resolve` → fetch the vendor's checksum manifest (GPG-verified when
  the catalog names `sig` + `key`) → read the SHA-256 for the resolved file (`_usb_sums_digest_for`
  / `Get-AutoOSUsbSumsDigest`: coreutils, starred and BSD `SHA256 (f) = h` shapes; 64-hex only,
  so an MD5 line can never match) → `fetch_verified` against it. Manifest kept as `<dest>.sums`.
  Refuses pseudo-entries, entries with no `sums`, a manifest that omits the file, and every
  download failure, leaving nothing behind. Second run is `skipped`.
- **The plan now leads with it.** `usb_plan` / `New-AutoOSUsbPlan` emit
  `usb_fetch_image <id> <cache>/<id>.iso` (Windows: `Invoke-AutoOSUsbFetchImage …`) as line 1,
  *before* `usb_reverify` — deliberately: the fetch is the hour-long step and must not sit
  between the re-verify and the write. The F3/F6 test now asserts "re-verify immediately before
  the first destructive line", not "first line". Dry runs trace and skip it; the
  "dry run leaves the filesystem untouched" test still proves the cache dir stays empty.
- **`setup.sh` / `setup.ps1` execute.** After the plan and the elevation check, a non-dry-run
  now pipes the printed plan into `usb_execute` / `Invoke-AutoOSUsbPlan` — **only with
  `--wipe-target-disk` / `-WipeTargetDisk`**; without it the run shows the plan and refuses
  (a plan is not consent, and neither is `--yes`). The flag changed meaning from
  "acknowledged, ignored" to "required opt-in"; usage text, README and
  `docs/getting-started.md#flags` say so.
- Two bugs the new tests caught on the way: the Windows fetch wrote its `.part` before the
  cache directory existed (a first-run failure; fixed on both sides by creating `dirname dest`),
  and `[IO.File]::ReadLines` + an early `return` left the `.sums` file locked so the second run
  could not replace it (now `ReadAllLines`).

Counts recorded for this session on the Windows Git Bash host: bash `--filter usb` **70/0**,
`--filter fetch` **15/1** (the 1 is the host-only `file://` curl failure), full bash **252 passed /
8 failed / 2 skipped** — all 8 host-only (three `verified download` `file://` cases,
`catalog_probe_installed` needing `dpkg-query`, three state/config round-trips whose expected and
got strings print identically = CR difference, `shellcheck is clean` = SC1017 on the CRLF checkout;
none touch files this session changed); full pwsh **212/1** (the 1 is `PSScriptAnalyzer is clean`,
pre-existing findings in `AutoOS.ClaudeAutostart/Detect/Download/Serve.psm1`, none in touched
files); shellcheck via docker on CR-stripped copies of the touched `.sh` files: clean apart from
three pre-existing SC2034 in `setup.sh`.

**The test stick, re-inspected read-only on 2026-09-17 (second session):** enumerates as
`\\.\PHYSICALDRIVE5`, serial `960806056010`, MBR, one FAT32 partition, drive letter `J:` back.
`chkdsk J:` (no repair flags) reports problems it could not correct: `boot\grub` holds directory
entries with garbage names and ~4 GB sizes, ~14 GB sit in lost cluster chains, and the offline
`.deb` cache is gone (`rescue\debs` and `pool\` hold zero `.deb`). `casper/minimal.squashfs`
*lists* at its expected size (3,432,136,704 B) but **reading it fails with "The file or directory
is corrupted and unreadable"**, and a lookup in `md5sum.txt` returned nothing — the payload is
not intact either. The stick must be rebuilt from scratch; nothing on it is worth preserving
except the human partner's own files. It also carries the human partner's
own files (`ROG-STRIX-…-ASUS-4505.CAP`, `BIOSRenamer.exe`) — copy those off before any rebuild.
`setup.ps1 -ListUsb` reported "No USB devices found" for the first ~minute after plug-in
(`Get-Partition` failed with "No MSFT_Partition objects") and then listed it correctly — treat an
empty listing right after plug-in as a race, not a bug. A real Windows run also needs `gpg` on
PATH (Ubuntu's manifest is signature-verified; `Test-AutoOSGpgSignature` throws without it) and
none is installed on this host; WSL has gpg/curl but cannot see the stick without usbipd.

**The first real run (Windows, 2026-09-17, later the same day)** — stick rebuilt as agreed
(elevated one-off script, serial-gated; GnuPG 2.5.22 installed via winget so the Windows fetch
path can verify Ubuntu's manifest), then `setup.ps1 -CreateUsb -Image ubuntu-desktop-lts -Engine
uefi-copy -UsbDevice \\.\PHYSICALDRIVE5 -WipeTargetDisk`. Each attempt found one real defect,
each fixed test-first on both platforms:

1. **Resolver ambiguity on the live index.** `releases.ubuntu.com/26.04.1/` lists
   `ubuntu-26.04-desktop-amd64.iso` beside `ubuntu-26.04.1-desktop-amd64.iso`. The parser now
   applies the one ordering rule that exists at a single directory level: matches identical
   except for one dotted version number are the same artefact, highest version wins (26.04 <
   26.04.1). Anything else (same version, different arch) is still an error — the
   `multiple_match` fixture was changed to that shape; new fixture `ubuntu_point_release`.
2. **gpg stderr under `$ErrorActionPreference='Stop'`** (lesson L6, live): every fresh
   GNUPGHOME makes gpg print "gpg-agent: directory … created" to stderr, which became a
   terminating error before the signature was checked. `Test-AutoOSGpgSignature` now runs the
   two native calls with the preference set to Continue and judges by exit code only;
   `Get-AutoOSVerifiedFile` deletes its `.part` when the signature check *throws*, not only
   when it returns false. Tested with a fake `gpg.cmd` shim.
3. **`Invoke-WebRequest -OutFile` cannot download a 6 GB file** (Windows PowerShell 5.1
   buffers the whole response in memory, and crawled at ~1 MB/s on the part it managed).
   `Get-AutoOSRawDownload` now uses the `curl.exe` that ships with Windows (`-fsSL --retry 3`,
   same flags as `download.sh`), streaming to disk; Invoke-WebRequest remains the fallback.
   Tested against a loopback python server (new `Start-AutoOSTestHttpServer` helper).
4. **Executor message.** A failure in the fetch step used to say "there is no rollback — rewrite
   from wipefs" about a stick nothing had written to. Both executors now track when the
   destructive part begins and say "was not touched" before it.

After those, the manifest verified against Ubuntu's real key and the ISO download ran — at
**~0.7 MB/s, which is what `releases.ubuntu.com` itself served this host** (measured directly
with curl, twice; a mirror list on the catalog entry is the obvious follow-up). The download
takes ~2 h at that rate; see the next dated handoff or the git log for whether the copy onto
J: and a boot were reached.

Still true after this session: **nobody has booted a stick built by this tool**, the
`custom-url` / `custom-local` pseudo-entries are still not plumbed through the CLI (usb_plan
already refuses them — no `writeMode`), the Windows plan still has no re-verify step, and
plan lines are word-split so a cache path containing a space breaks the bash executor (the
Windows dispatcher now handles it for the fetch line only).

---

## 3. Where things stand (as verified 2026-09-17)

Branch **`feat/installer-usb-rescue-profile`**, pushed to `origin`, tip `10ca8ed`, **32 commits,
not merged.** Branch point `1eab6fc`. `main` was 6 commits ahead. *(The handoff commit `64b9b8f`
and the second session's commit came after; section 2 tells you how to re-check.)*

### Done, tested and committed

| Area | What exists |
|---|---|
| `rescue` profile | 40 components on Linux, 11 on Windows — incl. `lvm2`, `mdadm`, `cryptsetup` (without these a rescue stick cannot even *see* LVM, RAID or LUKS volumes) |
| `local-ai` profile | `ollama`, `qwen3:4b` (2.5 GB, pre-ticked), `qwen3:1.7b` (1.4 GB), `qwen2.5-coder:7b` (4.7 GB), `oterm`; every model entry must state its size, enforced by a test |
| AI clients | `claude-code` and `agy` (Antigravity CLI) as independent entries; an `ai` dispatcher over a registry, with a `local` backend so it degrades to offline inference |
| Verified download | `lib/linux/download.sh` `fetch_verified` / `AutoOS.Download.psm1`; SHA256 + GPG, throwaway keyring, cache outside the tree |
| Image catalog | `catalog/images.json`, version-free (index URL + filename pattern). Real GPG fingerprints verified from the signature packets: Ubuntu `843938DF228D22F7B3742BC0D94AA3F0EFE21092`, Debian `DF9B9C49EAA9298432589D76DA87E80D6294BE9B` |
| Image resolver | `image_resolve` / `Resolve-AutoOSImageUrl`. LTS rule: even-numbered year, `.04` release; never an interim; no LTS found is a loud error |
| USB safety guard | `usb_guard` / `Assert-AutoOSUsbSafe`, engine-aware (`unmounted` vs `mounted-fat32-writable`), **exercised against six real disks** on the development machine: all internal disks refused, system/boot disks named |
| Planner / executor | `--create-usb`, `--list-usb`, `--list-engines`; plan-then-execute so `--dry-run` opens no disk handle; five engines (`ventoy` default, `uefi-copy`, `wsl`, `native`, `rufus`) in `catalog/engines.json` |
| Browser UI | "Create installer USB" button + dialog; `/api/images`, `/api/usb/devices`, `/api/usb/create` on **both** `serve.py` and `AutoOS.Serve.psm1` |
| Offline stick support | `.deb` cache builder, **proven to install all 38 rescue packages with the network forced unreachable**; pip wheelhouse for `oterm` |
| Templates | sanitised `user-data.example` / `preseed.cfg.example` (validated with `cloud-init schema`), idempotent `rescue-bootstrap.sh` |

Last recorded counts: bash `--filter usb` **56/0**, pwsh `-Filter usb` **34/0**, `resolve` 13/0,
`image` 34/0, `template` 17/0, `catalog` 15/1 (the 1 is `dpkg-query` being absent in native
Git Bash — environment-only), `shellcheck is clean`.

---

## 4. The merge — it is no longer a fast-forward

On 2026-09-17 a read-only `git merge-tree` reported **four real conflicts**:

| File | Kind |
|---|---|
| `catalog/macos.json` | content |
| `lib/windows/AutoOS.Install.psm1` | content |
| `tests/run-tests.sh` | content |
| `docs/plans/2026-09-11-installer-usb-and-rescue-profile.md` | **add/add** — both branches created this file |

### The `rescue` profile collision

`main` commit `3e03709` added its own `rescue` profile, described as *"Disaster-recovery toolkit and
emergency AI recovery environment"* — but with **only one member: `opencode`**. The branch has the
real toolkit. **There is no member overlap**, so the correct resolution is a **union**: keep `main`'s
`opencode` in `rescue` **and** every member from the branch.

### ⚠️ The dangerous one git will NOT flag: `agy`

`catalog/windows.json` **auto-merges**, but the same id is defined two different ways:

| | provider | package |
|---|---|---|
| `main` | `script` | `agy` — i.e. `irm … \| iex`, piping a download straight into a shell |
| branch | `winget` | `Google.AntigravityCLI` — signed, hash-checked |

Removing pipe-to-shell installers is one of this branch's explicit purposes (plan finding **A14**).
A merge that keeps `main`'s definition **silently reinstates the unsafe installer** with no conflict
marker to warn you. **After merging, check `agy` in `catalog/windows.json` by eye.** The winget
definition must win.

Also check `1436ad2` on `main` — its message mentions *"catalog globs scoped to OS files"*. That may
be a second, independent fix for the problem this branch solved in `72be96c` (`--check-catalog`
failing on `images.json` / `engines.json`). Two fixes for one problem will not necessarily conflict
textually and will not necessarily agree.

**After resolving, run both `--check-catalog` and `bash tests/run-tests.sh --filter catalog`, and
read the printed counts.**

---

## 5. Remaining work, in priority order

1. ~~**Wire `image_resolve` into the planner.**~~ **Done (section 2b)** — and the executor is now
   called from both entry points. What remains of it: the first *real* run against the test
   stick (serial `960806056010`, section 7) — every path below the fetch has only ever run under
   `AUTOOS_DRY_RUN` / `AUTOOS_FORCE_FAIL` or against loopback fixtures.
2. **Resolve the merge** (section 4). Land it on `main`. The second session's changes touch
   `setup.sh`, `lib/linux/usb.sh`, `tests/run-tests.sh` (already in the conflict set) and
   `setup.ps1`, `lib/windows/AutoOS.Usb.psm1`, `tests/run-tests.ps1`, `README.md`,
   `docs/getting-started.md` — re-run `git merge-tree` before assuming section 4's list.
3. **Terminal chooser (plan Task 10)** — image → kind → engine → device, reusing the existing
   interactive selection helpers from commit `f8ae521`. The browser has this flow; the terminal
   still needs the flags typed by hand. Unlike the browser, the terminal **may** offer `rufus`,
   since a human is present to drive its GUI. The confirmation must name the device model, its
   size, and that all data on it will be destroyed.
4. **Docs (plan Task 12)** — `docs/usb-creator.md`, ADR `docs/decisions/0004-ventoy-and-wsl-not-rufus.md`,
   README flags. **Link to `catalog/images.json`; do not restate the image list** (finding A23).
   State the limitations flatly: `uefi-copy` is UEFI-only; FAT32 caps one file at 4,294,967,295 B;
   nobody has booted a stick built by this tool.
5. Wire `usb_ventoy_add_persistence` into `usb_plan` for `kind=live-persistent`.
6. Minor, parked: `backup_file "$dest" || true` in `rescue-bootstrap.sh` discards the backup's exit
   status and then prints "backup taken" regardless. No data is lost, but the message can lie.
7. `usb_copy_image`'s real mount/copy path has only its guards tested — there was no loop-device
   support in the test environment. The Windows engine command strings have never run on real
   hardware.

---

## 6. Lessons the previous session paid for

The ledger that held these is gone. They are here so you do not pay for them again.

- **L1 — A check that has never been observed to fail is not evidence.** Four false greens:
  `md5sum -c … | head` (the exit status came from `head`, masking a real mismatch);
  `--filter profiles` matched **zero** tests and printed a clean run; the template guards **passed
  against deliberately reintroduced versions of the very defects they existed to catch**; and
  `--filter browser` reached 1 of 6 tests. **Always read the printed test count.** Before trusting a
  new test, break the thing it guards and watch it fail.
- **L2 — The test harness filters on TEST NAME, not describe-group name.** `--filter profiles`
  matched nothing because test names are singular. Name tests so a sensible filter reaches them.
- **L3 — When a command reports a side effect *and* an error, check the resulting state at once.**
  `mountvol J: /P` said it had force-dismounted the volume, then failed with Access denied. The
  dismount had already happened; the stick lost its drive letter.
- **L4 — Gaps live in the seams between tasks.** Honest reports are not enough. Someone has to own
  the joins.
- **L5 — Cross-family review works.** `agy` on `gemini-3.8-flash-high` found five real defects in the
  disk-destroying path that several same-family reviews had missed, including a guard that failed
  open. **Verify the reviewer too**: two of its scariest findings did not hold as stated.
  Mechanics: headless `agy` cannot read files, so pipe the code in; `-p` takes its prompt inline, so
  `agy -p --model X` silently eats `--model` — for large input use `--input-format stream-json` with
  NDJSON on stdin.
- **L6 — PowerShell 5.1 (`powershell.exe`, which elevated launches use):**
  a BOM-less `.ps1` is read as **cp1252**, so one em-dash inside a double-quoted string breaks the
  parse — write elevated scripts ASCII-only and check with `Parser::ParseFile` under 5.1;
  `-match` overwrites `$Matches` on **every** evaluation, so two `-match` in one `-and` discards the
  first capture; and `$ErrorActionPreference='Stop'` plus `2>&1` turns a native tool's stderr
  **warning** into a terminating error.
- **L7 — Windows Python defaults to cp1252.** Always `open(…, encoding="utf-8")` for the catalogs.
- **L8 — Subagents stall and die.** One spent 525k tokens then waited on a monitor it had spawned and
  committed nothing; one died to a rate limit mid-task. Tell every subagent: run each test once, no
  monitors or polling, **commit before reporting**.
- **L9 — "Plan-then-execute" had no execute.** Six tasks built and tested a planner and an
  executor and nobody checked that any entry point called the second. A grep for callers of
  `usb_execute` / `Invoke-AutoOSUsbPlan` outside `tests/` returned nothing. When a feature is
  described as a pipeline, grep each stage's *callers*, not just its tests.
- **L10 — On this Windows host, the checkout is CRLF (`git ls-files --eol` → `w/crlf`) even though
  `.gitattributes` says `eol=lf`.** Git Bash tolerates it, the suite's shellcheck-in-docker does
  not (`SC1017 literal carriage return` on every `.sh`), and a `bash` reached from PowerShell
  fails on line 10. Lint copies stripped with `tr -d '\r'`; the index is LF, so commits are fine.
  The `verified download … skipped, not refetched` and `catalog` `dpkg-query` failures are the
  same class: host-only.
- **L11 — PowerShell `foreach` over `[IO.File]::ReadLines()` with an early `return` leaks the
  file handle.** The next `Move-Item` onto that file fails "could not move verified file into
  place". Use `ReadAllLines` for small files. The idempotency test is what caught it.

---

## 7. Hardware facts

Constraints that determine how a stick can be built:

- **FAT32 caps a single file at 4,294,967,295 bytes.** `qwen2.5-coder:7b`'s weights blob is
  4,683,074,048 B, so it cannot sit on a FAT32 partition. Ubuntu 26.04.1's largest inner file,
  `casper/minimal.squashfs`, is 3,432,136,704 B — under the cap, which is why a file-copy stick works.
- **`uefi-copy` works only because Ubuntu's `boot/grub/grub.cfg` uses bare `linux /casper/vmlinuz`
  with no `search --label` and no UUID.** GRUB takes the partition it booted from. Re-verify per
  image. A stick built this way is **UEFI-only**.
- **Ubuntu's `md5sum.txt` does not cover the boot chain** (`EFI/`, `.disk/`, `boot/grub/`,
  `boot.catalog`). A manifest check says nothing about bootability; verify those files separately.
- **`wsl --mount --bare` cannot attach a USB flash drive** — verified locally, fails `0x8007000f`.
  `usbipd-win` is the route, and after installing it Windows may want a reboot (see safety rule 1).
- **`Set-Disk -IsOffline` refuses removable media.**

The human partner's test stick is identified by **USB serial `960806056010`** (an Intenso Office
Line, ~29.3 GB). Every destructive script must refuse unless that serial matches. As of 2026-09-12 it
held a verified Ubuntu 26.04.1 build plus the offline `.deb` cache, and **had lost its Windows drive
letter** — data intact, and still bootable, since UEFI does not use drive letters. A rebuild to
**p1 FAT32 9 GB + p2 exFAT ~20 GB** (exFAT has no 4 GiB cap and needs no reboot to format) was
agreed but **not run**. The scripts for it were one-off prototypes outside the repository and should
be treated as lost; rebuild them from this section if needed.

**Nobody has booted a stick built by this tool.** Do not claim it works.

---

## 8. How to work here

- Isolate in a worktree. Never edit in the main checkout while it has someone else's changes.
- Follow [`AGENTS.md`](../../AGENTS.md) §7 definition of done.
- Run **filtered** tests for what you touch; the full unfiltered suite is slow in this environment
  (roughly 90 s+ per dry-run subprocess). Record the printed counts, and treat a zero count as a
  failure to report, not a pass.
- For reviews, consider `agy` on a non-Claude model (lesson L5) — it is cheap and genuinely catches
  different defects.
- Commit with a message body explaining *why*.
- Ask the human partner through **interactive questions**, not by writing questions into a file.
- When you finish, **update this file** — or write the next dated handoff beside it — so the next
  agent does not start from a stale record the way this one nearly did.
