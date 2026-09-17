#Requires -Version 5.1
<#
.SYNOPSIS
    AutoOS USB device enumeration and write-safety guard for Windows.

.DESCRIPTION
    Task 5 of plan 2026-09-11-installer-usb-and-rescue-profile: this is the
    module where a bug destroys someone's data — Assert-AutoOSUsbSafe below
    is the only thing standing between the installer-USB writer and a live
    workstation disk. It is the exact mirror of lib/linux/usb.sh's
    usb_guard(): the system/boot disk is checked FIRST (the most dangerous
    mistake gets the shortest path to refusal), every refusal names its
    reason in words a human can act on, and every code path is exercised
    against synthetic $env:AUTOOS_FAKE_DISKS fixtures rather than the live
    machine's disks (AGENTS.md §5) — this module must never enumerate or
    touch a real disk during the test suite.

    Finding A10: a USB SSD, especially anything behind a USB-Attached-SCSI
    (UASP) bridge, commonly reports BusType 'SCSI' rather than 'USB' in
    Get-Disk, and has no reliable native "removable" flag either. Both
    Get-AutoOSUsbDevice's candidate filter and Assert-AutoOSUsbSafe's bus
    check therefore treat BusType -in ('USB','SCSI') as USB-plausible —
    never IsRemovable alone, and never BusType -eq 'USB' alone.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# No -Force here: forcing a re-import from inside a module REMOVES the
# caller's global copy of AutoOS.Detect.psm1, which would silently strip its
# functions from the session (same rule AutoOS.Install.psm1 documents at its
# own top). Test-AutoOSAdmin below is reused from there rather than
# re-derived, so this module has exactly one WindowsPrincipal/
# WindowsIdentity check in the whole codebase. AutoOS.Download.psm1 is
# imported the same way, since Task 6's New-AutoOSUsbPlan below reuses
# Get-AutoOSDownloadCacheDir rather than re-deriving the P6 cache path.
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Detect.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Download.psm1') -DisableNameChecking
# Task 7: the write executor below reports status the way every other
# AutoOS module does (Write-AutoOSLine, never Write-Host - AGENTS.md SS3),
# which this module did not previously need since Assert-AutoOSUsbSafe/
# New-AutoOSUsbPlan only ever throw or return data.
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

# Get-AutoOSUsbRawDisk
# Prints the raw disk records this module reasons over: parsed from
# $env:AUTOOS_FAKE_DISKS (a JSON array) when the caller has set it, or from
# Get-Disk + Get-Partition + Win32_DiskDrive on a real machine otherwise.
# Tests always set AUTOOS_FAKE_DISKS (AGENTS.md §5) — this is the only place
# in the module that would otherwise touch the live machine's disks.
#
# Every record carries the same shape whichever source it came from: Number,
# Model, Size, BusType, IsBoot, IsSystem, IsRemovable, Partitions (an array
# of @{ DriveLetter = <letter-or-$null> }). A fixture must supply every one
# of these fields explicitly — Set-StrictMode turns a missing property into
# a hard error rather than a silently-$null one, which is the point: an
# incomplete fixture must fail loudly, not quietly under-test the guard.
function Get-AutoOSUsbRawDisk {
    if ($env:AUTOOS_FAKE_DISKS) {
        # `@($env:AUTOOS_FAKE_DISKS | ConvertFrom-Json)` — piping straight
        # into the array subexpression operator — silently collapses a
        # multi-element JSON array into ONE merged object under Windows
        # PowerShell 5.1 (confirmed: 2 disks in, 1 record out, every
        # property an array of both disks' values). `-InputObject` instead
        # of the pipeline avoids it entirely; the `-is [array]` check below
        # then covers the other shape ConvertFrom-Json can hand back — a
        # single JSON object (one disk, no brackets) parses to one
        # pscustomobject, not an array, and still needs wrapping for every
        # caller here that iterates the result.
        $parsed = ConvertFrom-Json -InputObject $env:AUTOOS_FAKE_DISKS
        if ($parsed -is [array]) { return $parsed }
        return @($parsed)
    }

    $disks = Get-Disk -ErrorAction Stop
    $result = @()
    foreach ($d in $disks) {
        $partitions = @()
        try {
            $partitions = @(Get-Partition -DiskNumber $d.Number -ErrorAction Stop |
                ForEach-Object {
                    $driveLetter = if ($_.DriveLetter) { [string]$_.DriveLetter } else { $null }
                    # FileSystem/IsReadOnly (Task 6): what
                    # Assert-AutoOSUsbSafe's "MountedFat32Writable" mode
                    # checks for the uefi-copy engine. Best-effort — a
                    # volume lookup failure here still leaves IsReadOnly (a
                    # real Partition property) available.
                    $fileSystem = ''
                    if ($driveLetter) {
                        try {
                            $vol = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
                            $fileSystem = [string]$vol.FileSystem
                        } catch {
                            Write-Verbose "Get-AutoOSUsbRawDisk: Get-Volume failed for drive ${driveLetter}: $($_.Exception.Message)"
                        }
                    }
                    [pscustomobject]@{
                        DriveLetter = $driveLetter
                        FileSystem  = $fileSystem
                        IsReadOnly  = [bool]$_.IsReadOnly
                    }
                })
        } catch {
            $partitions = @()
        }

        # Get-Disk carries no reliable native "removable" flag; Win32_DiskDrive's
        # MediaType ("Removable Media" vs "Fixed hard disk media") is the closest
        # real signal, best-effort and never load-bearing on its own — the bus
        # check below (BusType -in USB/SCSI) is what actually gates the guard,
        # exactly because IsRemovable alone is finding A10's known blind spot.
        $isRemovable = $false
        try {
            $cim = Get-CimInstance -ClassName Win32_DiskDrive -Filter "Index=$($d.Number)" -ErrorAction Stop
            if ($cim -and $cim.MediaType -match 'Removable') { $isRemovable = $true }
        } catch {
            # Best-effort only; BusType below still gates the guard correctly.
            Write-Verbose "Get-AutoOSUsbRawDisk: Win32_DiskDrive lookup failed for disk $($d.Number): $($_.Exception.Message)"
        }

        $result += [pscustomobject]@{
            Number      = $d.Number
            Model       = $d.FriendlyName
            Size        = $d.Size
            BusType     = [string]$d.BusType
            IsBoot      = [bool]$d.IsBoot
            IsSystem    = [bool]$d.IsSystem
            IsRemovable = $isRemovable
            Partitions  = $partitions
        }
    }
    return $result
}

# ConvertTo-AutoOSUsbRecord <raw disk record>
# Normalises one Get-AutoOSUsbRawDisk record into the public shape:
# DeviceId, Number, Model, SizeBytes, Bus, IsRemovable, IsSystem, IsBoot,
# MountedLetter (the first assigned drive letter found on the disk, or
# $null). DeviceId follows the standard Windows physical-disk path so it is
# directly usable by a later write step, the same way /dev/sdX is on Linux.
function ConvertTo-AutoOSUsbRecord {
    param([Parameter(Mandatory)]$Raw)

    $mountedLetter = $null
    $mountedFileSystem = $null
    $mountedReadOnly = $false
    foreach ($p in @($Raw.Partitions)) {
        if ($p.DriveLetter) {
            $mountedLetter = [string]$p.DriveLetter
            $mountedFileSystem = [string]$p.FileSystem
            $mountedReadOnly = [bool]$p.IsReadOnly
            break
        }
    }

    [pscustomobject]@{
        DeviceId           = "\\.\PHYSICALDRIVE$($Raw.Number)"
        Number             = $Raw.Number
        Model              = $Raw.Model
        SizeBytes          = [int64]$Raw.Size
        Bus                = $Raw.BusType
        IsRemovable        = [bool]$Raw.IsRemovable
        IsSystem           = [bool]$Raw.IsSystem
        IsBoot             = [bool]$Raw.IsBoot
        MountedLetter      = $mountedLetter
        # Task 6 (B16): the uefi-copy engine's guard mode needs the
        # mounted partition's filesystem and whether it is read-only — the
        # exact mirror of lib/linux/usb.sh's _mounted_partition_info.
        MountedFileSystem  = $mountedFileSystem
        MountedReadOnly    = $mountedReadOnly
    }
}

function Get-AutoOSUsbDevice {
    <#
      .SYNOPSIS
        Lists candidate USB write targets: DeviceId, Model, SizeBytes, Bus,
        IsRemovable, IsSystem.
      .DESCRIPTION
        Filtered to BusType -in ('USB','SCSI') — finding A10: a USB SSD
        behind a UAS/UASP bridge commonly enumerates as BusType 'SCSI', not
        'USB', and IsRemovable is not a reliable signal either. This is the
        same rule Assert-AutoOSUsbSafe's bus check uses, so the list never
        hides a device the guard would otherwise accept. Reads
        $env:AUTOOS_FAKE_DISKS in place of Get-Disk when set (AGENTS.md §5).
    #>
    [CmdletBinding()]
    param()

    $records = @(Get-AutoOSUsbRawDisk | ForEach-Object { ConvertTo-AutoOSUsbRecord $_ })
    return @($records | Where-Object { $_.Bus -in @('USB', 'SCSI') })
}

function Assert-AutoOSUsbSafe {
    <#
      .SYNOPSIS
        Throws with a named, human-actionable reason unless -DeviceId is
        safe to write an installer/rescue image to; otherwise returns the
        device record.
      .DESCRIPTION
        Checks run in this exact order, most dangerous first — mirrors
        lib/linux/usb.sh's usb_guard() check-for-check:

          1. system/boot disk — never overwrite the disk Windows itself is
             installed on or booted from, whatever else is true about it.
          2. removable/USB-or-SCSI bus — refuse anything that is not
             plausibly a USB stick (finding A10: bus type, not IsRemovable
             alone).
          3. mount state — engine-aware (Task 6, B16): -Mode decides what
             "safe" means here (see below). This is the exact real-hardware
             bug that started Task 6: the old unmounted-only guard refused
             every internal disk correctly, then refused the human
             partner's own legitimate USB stick too — J:, PHYSICALDRIVE5,
             mounted and FAT32, the one target uefi-copy actually needs.
          4. size — refuse a stick too small for the image, only when the
             caller has told us how big the image is ($env:AUTOOS_IMAGE_BYTES;
             unset means "unknown", never a refusal).
      .PARAMETER DeviceId
        A device id as returned by Get-AutoOSUsbDevice, e.g.
        \\.\PHYSICALDRIVE5.
      .PARAMETER Mode
        'Unmounted' (default) — refuse a target with an assigned drive
        letter; writing under a mounted volume corrupts it. What every
        raw/block-writing engine needs (ventoy, native, wsl).

        'MountedFat32Writable' — the opposite requirement, for the
        uefi-copy engine (plan B16): it copies files onto a volume the
        caller already formatted and mounted, so it refuses when nothing
        is mounted, when the mounted filesystem is not FAT32, or when it
        is read-only. This lives in the guard, not as separate logic in
        the planner, so "is this device safe for this engine" has exactly
        one owner.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [ValidateSet('Unmounted', 'MountedFat32Writable')]
        [string]$Mode = 'Unmounted'
    )

    $records = @(Get-AutoOSUsbRawDisk | ForEach-Object { ConvertTo-AutoOSUsbRecord $_ })
    $target = $records | Where-Object { $_.DeviceId -eq $DeviceId } | Select-Object -First 1

    if (-not $target) {
        throw "$DeviceId was not found among enumerated disks"
    }

    if ($target.IsBoot -or $target.IsSystem) {
        throw "$DeviceId is the system disk (Windows is installed on it)"
    }

    if (-not ($target.IsRemovable -or $target.Bus -in @('USB', 'SCSI'))) {
        throw "$DeviceId is not removable and not on the USB bus"
    }

    switch ($Mode) {
        'Unmounted' {
            if ($target.MountedLetter) {
                throw "$DeviceId has a mounted volume ($($target.MountedLetter):) — unmount it first"
            }
        }
        'MountedFat32Writable' {
            if (-not $target.MountedLetter) {
                throw "$DeviceId has no mounted volume — mount a FAT32 volume on it first (uefi-copy writes onto an existing mounted filesystem, not the raw device)"
            }
            # Finding F2' (mirror of lib/linux/usb.sh's usb_guard): the bus/
            # removable check above only rules out disks Windows itself
            # considers system/boot - it says nothing about a system-critical
            # volume that happens to live on a USB disk, which is exactly the
            # case for a machine actually booted from this rescue stick.
            # Refused on the drive letter alone, before the filesystem/
            # read-only checks below, whatever the bus type says.
            $sysDrive = $env:SystemDrive
            if ($sysDrive) { $sysDrive = $sysDrive.TrimEnd(':') }
            if ($sysDrive -and $target.MountedLetter -eq $sysDrive) {
                throw "$DeviceId's mounted volume ($($target.MountedLetter):) is the system drive — refusing to treat a live system volume as a USB write target, whatever the bus type (finding F2')"
            }
            if ($target.MountedFileSystem -ne 'FAT32') {
                $fs = if ($target.MountedFileSystem) { $target.MountedFileSystem } else { 'unknown' }
                throw "$DeviceId's mounted volume ($($target.MountedLetter):) is '$fs', not FAT32 — uefi-copy requires an existing FAT32 volume"
            }
            if ($target.MountedReadOnly) {
                throw "$DeviceId's mounted volume ($($target.MountedLetter):) is read-only — uefi-copy needs to write to it"
            }
        }
    }

    $imageBytes = 0
    if ($env:AUTOOS_IMAGE_BYTES) { $imageBytes = [int64]$env:AUTOOS_IMAGE_BYTES }
    if ($target.SizeBytes -lt $imageBytes) {
        throw "$DeviceId is too small for this image"
    }

    return $target
}

function Test-AutoOSElevated {
    <#
      .SYNOPSIS
        Non-throwing elevation check (B10): { IsElevated, Reason }.
      .DESCRIPTION
        Reads $env:AUTOOS_FAKE_ELEVATED ('0'/'1') in place of the real
        WindowsPrincipal/IsInRole check when set, so the "no admin" path is
        testable without actually running the suite unelevated
        (AGENTS.md §5). Returns a structured result — not just a boolean —
        so a caller like AutoOS.Serve.psm1's browser UI can render Reason
        directly instead of only catching an exception.
    #>
    [CmdletBinding()]
    param()

    if ($null -ne $env:AUTOOS_FAKE_ELEVATED) {
        $elevated = $env:AUTOOS_FAKE_ELEVATED -eq '1'
    } else {
        $elevated = Test-AutoOSAdmin
    }

    if ($elevated) {
        return [pscustomobject]@{ IsElevated = $true; Reason = '' }
    }
    return [pscustomobject]@{
        IsElevated = $false
        Reason     = 'AutoOS needs an elevated (Run as Administrator) PowerShell session ' +
                     'to write to a disk. Re-launch PowerShell as Administrator and run AutoOS again.'
    }
}

function Assert-AutoOSElevated {
    <#
      .SYNOPSIS
        Throws unless the current process can perform a privileged disk
        write; otherwise returns the same structured result
        Test-AutoOSElevated does.
      .DESCRIPTION
        B10. Deliberately never self-elevates (no Start-Process -Verb
        RunAs): re-launching starts a fresh process with a fresh transcript
        log, discarding the exact log a user needs to diagnose a failed
        write. This only ever reports whether the CURRENT process already
        has enough privilege.
    #>
    [CmdletBinding()]
    param()

    $result = Test-AutoOSElevated
    if (-not $result.IsElevated) {
        throw $result.Reason
    }
    return $result
}

# ─── The write planner (Task 6) ─────────────────────────────────────────────
# New-AutoOSUsbPlan (below) turns (image, kind, engine, device) into the
# exact command lines a real write would run — nothing more. It is the
# exact mirror of lib/linux/usb.sh's usb_plan(): every check a real write
# would need (does this engine even build this kind, does it accept this
# image's writeMode, does it run on this machine, is a write already
# happening, is the device itself safe for this engine) all happen here,
# and NONE of them touch the device, the network or the filesystem beyond
# reading catalog\*.json and (via Assert-AutoOSUsbSafe) the live disk
# table. No -WhatIf switch: this is a planner, not a ShouldProcess cmdlet —
# it always returns [string[]] and never runs anything itself.

# Get-AutoOSUsbCatalogRoot
# The repo root to resolve catalog\*.json against. $PSScriptRoot always
# points at lib\windows regardless of the caller's own working directory
# (unlike lib/linux/usb.sh's bash equivalent, which has no such per-file
# anchor and falls back to AUTOOS_ROOT/pwd instead) — two parents up is the
# repo root for every caller, production or test.
function Get-AutoOSUsbCatalogRoot {
    (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
}

function Get-AutoOSUsbEngineCatalog {
    $path = Join-Path (Get-AutoOSUsbCatalogRoot) 'catalog\engines.json'
    @((Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json).engines)
}

function Get-AutoOSUsbImageCatalog {
    $path = Join-Path (Get-AutoOSUsbCatalogRoot) 'catalog\images.json'
    @((Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json).images)
}

function Get-AutoOSUsbEngine {
    param([Parameter(Mandatory)][string]$Id)
    Get-AutoOSUsbEngineCatalog | Where-Object { $_.id -eq $Id } | Select-Object -First 1
}

function Get-AutoOSUsbImage {
    param([Parameter(Mandatory)][string]$Id)
    Get-AutoOSUsbImageCatalog | Where-Object { $_.id -eq $Id } | Select-Object -First 1
}

# Get-AutoOSUsbEngineList -Platform -Arch
# Engines catalog\engines.json offers on -Platform/-Arch — an engine whose
# platforms/arch exclude the caller's machine never appears (AGENTS.md §3:
# hidden, not shown-and-failing). An empty platforms/arch list on an entry
# means "any", the same convention catalog components use.
function Get-AutoOSUsbEngineList {
    param([string]$Platform, [string]$Arch)
    Get-AutoOSUsbEngineCatalog | Where-Object {
        $plats = @($_.platforms)
        $archs = @($_.arch)
        (-not $plats -or $plats -contains $Platform) -and (-not $archs -or $archs -contains $Arch)
    }
}

# Get-AutoOSUsbCurrentOs / Get-AutoOSUsbCurrentArch
# setup.ps1 is Windows-only (unlike setup.sh, which is also macOS's entry
# point), so OS is always 'windows' here — arch still varies (Windows on
# ARM is real hardware). $env:AUTOOS_FAKE_ARCH lets a test simulate arm64
# without a second machine (AGENTS.md §5), the same role $env:AUTOOS_FAKE_
# ELEVATED / $env:AUTOOS_FAKE_DISKS already play elsewhere in this module.
function Get-AutoOSUsbCurrentOs { 'windows' }

function Get-AutoOSUsbCurrentArch {
    if ($env:AUTOOS_FAKE_ARCH) { return $env:AUTOOS_FAKE_ARCH }
    switch -Regex ($env:PROCESSOR_ARCHITECTURE) {
        'ARM64' { return 'arm64' }
        'AMD64' { return 'x64' }
        'x86'   { return $(if ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64') { 'x64' } else { 'x86' }) }
        default { return 'x64' }
    }
}

# Get-AutoOSUsbRunLockPath / Test-AutoOSUsbRunActive
# The shared cross-process run lock (B13: "USB creation joins that same
# lock, not a second one") — a file next to the download cache, the same
# location lib/linux/usb.sh's usb_run_lock_path uses relative to
# download_cache_dir, so a --serve-driven install and a terminal
# -CreateUsb agree on one location without either knowing about the
# other's code. $env:AUTOOS_FAKE_RUN_ACTIVE lets a test simulate either
# state without a real second process (AGENTS.md §5): '1' for "in
# progress", anything else (including unset) for "idle". Taking/releasing
# the lock is Task 7/the server's job; this only ever reads it.
function Get-AutoOSUsbRunLockPath {
    Join-Path (Split-Path -Parent (Get-AutoOSDownloadCacheDir)) 'run.lock'
}

function Test-AutoOSUsbRunActive {
    if ($null -ne $env:AUTOOS_FAKE_RUN_ACTIVE) {
        return $env:AUTOOS_FAKE_RUN_ACTIVE -eq '1'
    }
    Test-Path -LiteralPath (Get-AutoOSUsbRunLockPath)
}

function New-AutoOSUsbPlan {
    <#
      .SYNOPSIS
        Prints the exact command lines a real USB write would run, as
        [string[]]; throws with a human-actionable reason instead. Never
        runs a command itself.
      .DESCRIPTION
        Checks run data-only first, most general first, exactly so an
        incompatible (image, kind, engine) triple is refused before this
        function ever asks about the live machine:
          1. image/engine exist in their catalogs
          2. engine builds this -Kind at all
          3. image actually offers this -Kind
          4. engine can write this image's writeMode (never raw onto
             ventoy — A11)
          5. engine runs on this platform/arch
          6. an interactive engine (rufus) is unavailable under -DryRun
          7. no other run is already in progress (B13)
          8. Assert-AutoOSUsbSafe, in the mode this engine requires (B16)

        Elevation (B10) is deliberately NOT checked here — it is a
        property of who can run the emitted commands, not of whether the
        plan itself is coherent. setup.ps1's -CreateUsb handling checks it
        separately, after the plan is built.
      .PARAMETER DryRun
        Only gates the interactive-engine-under-dry-run refusal (rufus);
        this function never writes anything regardless.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)][string]$ImageId,
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Engine,
        [Parameter(Mandatory)][string]$DeviceId,
        [switch]$DryRun
    )

    $image = Get-AutoOSUsbImage -Id $ImageId
    if (-not $image) { throw "unknown image '$ImageId'" }

    $eng = Get-AutoOSUsbEngine -Id $Engine
    if (-not $eng) { throw "unknown engine '$Engine'" }

    $engKinds = @($eng.kinds)
    if ($engKinds -notcontains $Kind) {
        throw "engine '$Engine' ($($eng.name)) cannot build a '$Kind' image — it builds: $($engKinds -join ', ')"
    }
    $imgKinds = @($image.kinds)
    if ($imgKinds -notcontains $Kind) {
        throw "image '$ImageId' ($($image.name)) does not offer kind '$Kind' — it offers: $($imgKinds -join ', ')"
    }
    $engWriteModes = @($eng.writeModes)
    if ($engWriteModes -notcontains $image.writeMode) {
        throw "engine '$Engine' cannot write a '$($image.writeMode)' image ('$ImageId') — it writes: $($engWriteModes -join ', ')"
    }

    $curOs = Get-AutoOSUsbCurrentOs
    $curArch = Get-AutoOSUsbCurrentArch
    $engPlatforms = @($eng.platforms)
    if ($engPlatforms -and ($engPlatforms -notcontains $curOs)) {
        throw "engine '$Engine' is not available on $curOs"
    }
    $engArch = @($eng.arch)
    if ($engArch -and ($engArch -notcontains $curArch)) {
        throw "engine '$Engine' is not available on $curArch"
    }

    if ($eng.interactive -and $DryRun) {
        throw "engine '$Engine' is interactive and unavailable under -DryRun"
    }

    if (Test-AutoOSUsbRunActive) {
        throw 'a run is already in progress — try again once it finishes'
    }

    $guardMode = if ($Engine -eq 'uefi-copy') { 'MountedFat32Writable' } else { 'Unmounted' }
    $imageBytes = [int64][math]::Round([double]$image.sizeGb * 1000000000)
    $prevImageBytes = $env:AUTOOS_IMAGE_BYTES
    $env:AUTOOS_IMAGE_BYTES = [string]$imageBytes
    try {
        Assert-AutoOSUsbSafe -DeviceId $DeviceId -Mode $guardMode | Out-Null
    } finally {
        # Restore rather than blindly clear: a caller that already had its
        # own AUTOOS_IMAGE_BYTES set (unlikely, but $env: vars persist for
        # the whole session unlike bash's `VAR=x cmd` subshell scoping)
        # must not lose it because this function ran.
        if ($null -eq $prevImageBytes) { Remove-Item Env:\AUTOOS_IMAGE_BYTES -ErrorAction SilentlyContinue }
        else { $env:AUTOOS_IMAGE_BYTES = $prevImageBytes }
    }

    $localPath = Join-Path (Get-AutoOSDownloadCacheDir) "$ImageId.iso"

    $lines = [System.Collections.Generic.List[string]]::new()
    # The image itself: resolved (Resolve-AutoOSImageUrl), checksum-verified
    # and cached by Invoke-AutoOSUsbFetchImage, the FIRST line of every plan
    # and the only slow one - it runs before anything touches the device, so
    # a failed or interrupted download never leaves a half-written stick.
    # Under -DryRun this line is traced and skipped like every other, so
    # New-AutoOSUsbPlan itself still runs nothing and touches no network.
    $lines.Add("Invoke-AutoOSUsbFetchImage $ImageId $localPath")
    switch ($Engine) {
        'ventoy' {
            # Ventoy is a two-step engine: install the boot manager onto
            # the raw device once, then copy the verified image on as a
            # plain file (never a raw write — A11). Invoke-AutoOSUsbCopyImage
            # is Task 7's function; this line names it, it does not call
            # it — New-AutoOSUsbPlan runs nothing.
            $lines.Add("Ventoy2Disk.exe -I -G $DeviceId")
            $lines.Add("Invoke-AutoOSUsbCopyImage $DeviceId $localPath")
        }
        'uefi-copy' {
            $lines.Add("Invoke-AutoOSUsbCopyImage $DeviceId $localPath")
        }
        'native' {
            # Task 7/B14: Write-AutoOSUsbRaw computes its own progress from
            # bytes written rather than parsing a subprocess (there is no dd
            # on Windows to parse in the first place) - it needs the image
            # size up front, already computed above for the guard.
            $lines.Add("Write-AutoOSUsbRaw $DeviceId $localPath $imageBytes")
        }
        'wsl' {
            if ($Kind -eq 'full-os') {
                $lines.Add("wsl.exe --import AutoOSRescue $DeviceId $localPath")
            } else {
                $lines.Add("wsl.exe -e dd if=$localPath of=$DeviceId bs=4M status=progress conv=fsync")
            }
        }
        'rufus' {
            $lines.Add("rufus.exe -i $localPath")
        }
        default {
            throw "no write plan defined for engine '$Engine'"
        }
    }
    # No leading unary comma here: every caller (setup.ps1, the test suite)
    # wraps this call in @(...), the codebase's existing convention for
    # guaranteeing array-ness (e.g. @(Resolve-AutoOSPlan ...)) — adding one
    # here too double-wraps a single-line plan into an array containing one
    # array, which then stringifies as "System.String[]" instead of joining.
    return $lines.ToArray()
}

# ─── The write executor (Task 7) ────────────────────────────────────────────
# Invoke-AutoOSUsbPlan is the exact mirror of lib/linux/usb.sh's
# usb_execute(): the only thing in this module that ever runs a line
# New-AutoOSUsbPlan printed. Everything above this point only reads
# catalog\*.json and the live disk table; everything below actually writes.

# Invoke-AutoOSUsbPlanStep -DeviceId -Line
# One executed plan step - the mirror of usb.sh's _usb_run_step/
# _usb_dispatch_step combined. $env:AUTOOS_TRACE/AUTOOS_FORCE_FAIL/
# AUTOOS_DRY_RUN are read here, in one place, exactly like their bash
# counterparts, so Invoke-AutoOSUsbPlan's own loop stays "run each line,
# stop at the first failure". "Ventoy2Disk.exe ..." is dispatched to
# Install-AutoOSUsbVentoy for the same reason usb_execute special-cases the
# Linux binary's name: New-AutoOSUsbPlan deliberately keeps that line
# literal in its own output (a human previewing a plan should see the real
# tool that runs), even though the binary it names is not on PATH until
# Install-AutoOSUsbVentoy has fetched it.
function Invoke-AutoOSUsbPlanStep {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string]$Line
    )

    if ($env:AUTOOS_TRACE -eq '1') {
        Write-Output "TRACE $Line"
    }
    if ($env:AUTOOS_FORCE_FAIL -eq '1') {
        throw "forced failure (AUTOOS_FORCE_FAIL) before: $Line"
    }
    if ($env:AUTOOS_DRY_RUN -eq '1') {
        return
    }

    # [regex]::Match rather than -match: -match overwrites $Matches on every
    # evaluation, so a later -match in this function would silently discard
    # these captures (handoff lesson L6). Matched explicitly, not left to
    # Invoke-Expression below, so a cache path containing a space (a
    # Windows user name with one is common) still arrives as ONE argument.
    $fetch = [regex]::Match($Line, '^Invoke-AutoOSUsbFetchImage\s+(\S+)\s+(.+?)\s*$')
    if ($fetch.Success) {
        Invoke-AutoOSUsbFetchImage -ImageId $fetch.Groups[1].Value -Destination $fetch.Groups[2].Value
        return
    }
    if ($Line -match '^Ventoy2Disk\.exe\s') {
        Install-AutoOSUsbVentoy -DeviceId $DeviceId
        return
    }
    # Every other line is either a real function this module (or its
    # siblings) already defines - Invoke-AutoOSUsbCopyImage,
    # Write-AutoOSUsbRaw - or an external tool (wsl.exe, rufus.exe).
    # Invoke-Expression is PowerShell's eval, the same role bash's
    # `eval "$line"` plays in the Linux mirror.
    Invoke-Expression $Line
}

function Invoke-AutoOSUsbPlan {
    <#
      .SYNOPSIS
        Runs a plan (New-AutoOSUsbPlan's output) against -DeviceId. Returns
        once every step has succeeded AND the device still enumerates
        (B15) - throws with a human-actionable reason otherwise. There is
        no rollback: a failure means the stick must be rewritten from
        wipefs onward, and this function never retries automatically (a
        re-plugged stick can enumerate under a different device id).
      .PARAMETER Plan
        The exact output of New-AutoOSUsbPlan, one command per array
        element (the PowerShell equivalent of usb_execute's one-command-
        per-line stdin contract).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string[]]$Plan
    )

    $steps = @($Plan | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($steps.Count -eq 0) {
        throw "Invoke-AutoOSUsbPlan: empty plan - nothing to run"
    }

    # A plan's fetch line touches only the cache. A failure THERE must not
    # be reported as "there is no rollback, rewrite from wipefs" - the
    # first real run on 2026-09-17 failed inside the resolver and said
    # exactly that about a stick nothing had written to.
    $destructiveStarted = $false
    foreach ($line in $steps) {
        if ($line -notmatch '^Invoke-AutoOSUsbFetchImage\s') { $destructiveStarted = $true }
        try {
            Invoke-AutoOSUsbPlanStep -DeviceId $DeviceId -Line $line
        } catch {
            if (-not $destructiveStarted) {
                Write-AutoOSLine "usb_execute: stopped before any write step ran ($($_.Exception.Message)) - $DeviceId was not touched and does not need rewriting." -Level error
                throw
            }
            $stillThere = @(Get-AutoOSUsbDevice | Where-Object { $_.DeviceId -eq $DeviceId })
            if ($stillThere.Count -gt 0) {
                Write-AutoOSLine "usb_execute: write to $DeviceId failed ($($_.Exception.Message)). There is no rollback (B15) - the stick must be rewritten from wipefs onward; do not retry automatically." -Level error
            } else {
                Write-AutoOSLine "usb_execute: $DeviceId is no longer present - the stick was removed during the write. Re-seat it and start over; do not retry blindly, a re-plugged stick can enumerate under a different device id." -Level error
            }
            throw
        }
    }

    $stillThere = @(Get-AutoOSUsbDevice | Where-Object { $_.DeviceId -eq $DeviceId })
    if ($stillThere.Count -eq 0) {
        Write-AutoOSLine "usb_execute: $DeviceId did not read back after the write - treat this stick as unbootable and rewrite it; a retry is safe, it always starts from wipefs (B15)." -Level error
        throw "$DeviceId did not read back after the write"
    }

    Write-AutoOSLine "Ready to boot: $DeviceId" -Level ok
}

# ─── Invoke-AutoOSUsbFetchImage: the resolver-to-downloader bridge ──────────
# Until this existed, Resolve-AutoOSImageUrl and Get-AutoOSVerifiedFile were
# each written and tested, and New-AutoOSUsbPlan named a cache path nobody
# ever filled: -CreateUsb could plan a write but never download the image
# it planned to write. This is the join - the mirror of lib/linux/usb.sh's
# usb_fetch_image, same contract, same refusals.

function Get-AutoOSUsbSumsDigest {
    <#
      .SYNOPSIS
        The lower-case SHA-256 hex digest -ManifestPath lists for -FileName,
        or $null. Mirror of usb.sh's _usb_sums_digest_for.
      .DESCRIPTION
        Reads the two shapes the catalog's checksum files actually come in:
          <hex>  <file>  /  <hex> *<file>    GNU coreutils (Ubuntu, Debian,
                                             SystemRescue's .sha256)
          SHA256 (<file>) = <hex>            BSD style (Fedora's CHECKSUM)
        A leading "*" or "./" on the filename is stripped; anything else
        must match exactly. Only a 64-hex-character digest is accepted, so
        an MD5 or SHA-1 line for the same file can never be mistaken for
        the SHA-256 one. [regex]::Match, not -match: -match overwrites
        $Matches on every evaluation (handoff lesson L6).
    #>
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$FileName
    )
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { return $null }
    # ReadAllLines, not ReadLines: a `return` out of a foreach over the lazy
    # ReadLines enumerator leaves its file handle open in PowerShell, and the
    # next run's Move-Item onto the same .sums file then fails "could not
    # move verified file into place" - the idempotency test caught it.
    foreach ($raw in [IO.File]::ReadAllLines($ManifestPath)) {
        $line = $raw.Trim()
        if (-not $line) { continue }
        $bsd = [regex]::Match($line, '^SHA256\s+\((.+)\)\s*=\s*([0-9A-Fa-f]{64})$')
        if ($bsd.Success -and $bsd.Groups[1].Value -eq $FileName) {
            return $bsd.Groups[2].Value.ToLowerInvariant()
        }
        $gnu = [regex]::Match($line, '^([0-9A-Fa-f]{64})\s+\*?(?:\./)?(.+)$')
        if ($gnu.Success -and $gnu.Groups[2].Value -eq $FileName) {
            return $gnu.Groups[1].Value.ToLowerInvariant()
        }
    }
    return $null
}

function Invoke-AutoOSUsbFetchImage {
    <#
      .SYNOPSIS
        The first step of every plan New-AutoOSUsbPlan emits, and the only
        one that talks to the network: resolves -ImageId to a concrete URL,
        fetches the vendor's checksum manifest (GPG-verified against the
        catalog's `key` whenever the entry names a `sig`), reads this
        image's SHA-256 out of it, then fetches the image itself through
        Get-AutoOSVerifiedFile against that digest - so the file a later
        step writes to the stick has always been checked against a
        published digest, never trusted bare. The manifest is kept beside
        the image as <Destination>.sums so a run can be audited afterwards.
      .DESCRIPTION
        Idempotent (AGENTS.md §4): a cached -Destination that still matches
        the digest is reported as "skipped" by Get-AutoOSVerifiedFile, not
        refetched. The manifest IS refetched every run - it is small, and a
        stale one is exactly how a superseded point release would be
        silently written.

        Refuses, never guesses: a pseudo-entry (custom-url/custom-local),
        an entry with no `sums`, a manifest that does not list the resolved
        file, and every download failure each throw a clear message, with
        nothing left at -Destination (Get-AutoOSVerifiedFile deletes its
        own partial).
      .PARAMETER CatalogPath
        Defaults to <repo root>\catalog\images.json; tests pass a scratch
        catalog pointing at a local fixture instead. A plan line never
        carries it.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ImageId,
        [Parameter(Mandatory)][string]$Destination,
        [string]$CatalogPath
    )

    if ($ImageId -in @('custom-url', 'custom-local')) {
        throw "usb_fetch_image: '$ImageId' is a UI affordance (a user-supplied URL or local file), not a downloadable catalog entry - there is nothing to fetch"
    }
    if ($env:AUTOOS_DRY_RUN -eq '1') {
        Write-AutoOSLine "would resolve $ImageId, download it to $Destination and verify it against the vendor's published SHA-256" -Level muted
        return
    }

    if (-not $CatalogPath) {
        $CatalogPath = Join-Path (Get-AutoOSUsbCatalogRoot) 'catalog\images.json'
    }
    $entry = @((Get-Content -LiteralPath $CatalogPath -Raw -Encoding UTF8 | ConvertFrom-Json).images) |
        Where-Object { $_.id -eq $ImageId } | Select-Object -First 1
    if (-not $entry) {
        throw "usb_fetch_image: no catalog entry for image id '$ImageId' in $CatalogPath"
    }

    $resolved = Resolve-AutoOSImageUrl -ImageId $ImageId -CatalogPath $CatalogPath
    $sums = [string]$resolved.Sums
    $sig = [string]$resolved.Sig
    $key = [string]$entry.key
    if (-not $sums -or $sums -eq '-') {
        throw "usb_fetch_image: catalog entry '$ImageId' names no checksum manifest - refusing to write an image nothing can verify"
    }
    if (-not $sig -or $sig -eq '-') {
        $sig = ''; $key = ''
    } elseif (-not $key -or $key -eq '-') {
        throw "usb_fetch_image: catalog entry '$ImageId' names a signature but no GPG key fingerprint to check it against"
    }

    # Get-AutoOSVerifiedFile writes its .part next to the destination before
    # it ever creates the destination's directory, and on a machine's very
    # first run the cache dir (Get-AutoOSDownloadCacheDir) does not exist
    # yet - the fetch tests caught exactly that. Created here, once, for
    # both the manifest and the image.
    $destDir = Split-Path -Parent $Destination
    if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    $sumsFile = "$Destination.sums"
    Write-AutoOSLine "fetching checksum manifest for $ImageId" -Level info
    try {
        # No digest to check the manifest against (it is the source of
        # digests); its own integrity comes from the detached signature
        # when there is one.
        Get-AutoOSVerifiedFile -Uri $sums -Destination $sumsFile -SignatureUri $sig -GpgFingerprint $key | Out-Null
    } catch {
        throw "usb_fetch_image: could not fetch or verify checksum manifest ${sums}: $($_.Exception.Message)"
    }

    $digest = Get-AutoOSUsbSumsDigest -ManifestPath $sumsFile -FileName ([string]$resolved.File)
    if (-not $digest) {
        throw "usb_fetch_image: $(Split-Path -Leaf $sumsFile) does not list a SHA-256 for '$($resolved.File)' - refusing to download an image with no published digest"
    }

    # Mirrors (the catalog entry's optional `mirrors` list): the canonical
    # index decides WHICH file and the signed manifest decides its digest;
    # any mirror with the same directory layout may then serve the bytes,
    # because Get-AutoOSVerifiedFile checks them against that digest
    # whatever their origin. Measured on 2026-09-17: releases.ubuntu.com
    # served this host at ~0.7 MB/s (a 6 GB ISO in ~3 h) while
    # mirror.init7.net did ~490 MB/s (13 s). A mirror that fails is skipped
    # with a warning; the canonical URL is always the last resort, so a
    # wrong mirror list can only ever cost time.
    $url = [string]$resolved.Url
    $index = [string]$entry.index
    if ($index -and -not $index.EndsWith('/')) { $index += '/' }
    $candidates = @()
    if ($index -and $url.StartsWith($index)) {
        $rel = $url.Substring($index.Length)
        # Property-safe under Set-StrictMode: most entries have no `mirrors`.
        $mirrorProp = $entry.PSObject.Properties['mirrors']
        $mirrorList = if ($mirrorProp) { @($mirrorProp.Value) } else { @() }
        foreach ($m in $mirrorList) {
            if (-not $m) { continue }
            $mirror = [string]$m
            if (-not $mirror.EndsWith('/')) { $mirror += '/' }
            $candidates += ($mirror + $rel)
        }
    }
    $candidates += $url

    $lastError = ''
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-AutoOSLine "fetching $($resolved.File) from $($candidates[$i])" -Level info
        try {
            Get-AutoOSVerifiedFile -Uri $candidates[$i] -Destination $Destination -Sha256 $digest | Out-Null
            Write-AutoOSLine "verified image: $Destination" -Level ok
            return
        } catch {
            $lastError = $_.Exception.Message
            if ($i -lt $candidates.Count - 1) {
                Write-AutoOSLine "usb_fetch_image: $($candidates[$i]) failed ($lastError) - trying the next source" -Level warn
            }
        }
    }
    throw "usb_fetch_image: ${lastError}; nothing was kept"
}

# Write-AutoOSUsbRaw -DeviceId -ImagePath -ImageBytes
# B14's Windows side: there is no dd here to parse a progress line out of in
# the first place, so this writes the image itself in 4 MB chunks and
# reports bytes-written/-ImageBytes as its own "NN%" lines - the mirror of
# lib/linux/usb.sh's usb_write_raw(), which reaches the same percentage by
# reading dd's stderr instead.
function Write-AutoOSUsbRaw {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string]$ImagePath,
        [int64]$ImageBytes = 0
    )

    if ($env:AUTOOS_DRY_RUN -eq '1') {
        Write-AutoOSLine "would write $ImagePath onto $DeviceId (raw)" -Level muted
        return
    }
    if (-not (Test-Path -LiteralPath $ImagePath)) {
        throw "Write-AutoOSUsbRaw: image not found: $ImagePath"
    }
    if ($ImageBytes -le 0) {
        $ImageBytes = (Get-Item -LiteralPath $ImagePath).Length
    }

    $bufferSize = 4MB
    $buffer = New-Object byte[] $bufferSize
    $written = [int64]0
    $lastPct = -1

    $src = [System.IO.File]::Open($ImagePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
    try {
        $dst = [System.IO.File]::Open($DeviceId, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write)
        try {
            while ($true) {
                $read = $src.Read($buffer, 0, $bufferSize)
                if ($read -le 0) { break }
                $dst.Write($buffer, 0, $read)
                $written += $read
                if ($ImageBytes -gt 0) {
                    # [math]::Floor, not a bare [int] cast: PowerShell divides
                    # two integers as a [double] and [int] ROUNDS that
                    # (confirmed live in Add-AutoOSUsbVentoyPersistence's own
                    # half-stick cap, which this mirrors) - a rounded-up
                    # percentage could print "100%" before the write is
                    # actually done, exactly the overclaim B15 exists to rule
                    # out.
                    $pct = [int][math]::Floor(($written * 100) / $ImageBytes)
                    if ($pct -gt 100) { $pct = 100 }
                    if ($pct -ne $lastPct) {
                        Write-Output "$pct%"
                        $lastPct = $pct
                    }
                }
            }
            $dst.Flush($true)
        } finally {
            $dst.Close()
        }
    } finally {
        $src.Close()
    }
}

# Invoke-AutoOSUsbCopyImage -DeviceId -ImagePath [-ExtraFiles]
# Mirrors lib/linux/usb.sh's usb_copy_image(): copies a hybrid ISO's
# contents, plus any extra files (Task 9's rescue templates), onto the
# FAT32/exFAT volume already mounted on -DeviceId. Never the raw device -
# that is Install-AutoOSUsbVentoy's job, or Write-AutoOSUsbRaw's for a
# writeMode:raw image.
#
# Finding A11: refuses outright when the mounted volume is CDFS/UDF (a
# raw-written hybrid image mounts as one of those, and both are read-only
# in Windows) - checked before anything else, so a raw-written stick fails
# fast with a named reason instead of a confusing copy error partway
# through. Finding B16: refuses before copying anything if any file inside
# the image exceeds FAT32's 4 GiB per-file ceiling, naming the offending
# file - failing 20 minutes into a multi-gigabyte copy is exactly what this
# guards against.
function Test-AutoOSRobocopyFailed {
    <#
      .SYNOPSIS
        $true when a robocopy exit code means the copy is not complete.
      .DESCRIPTION
        robocopy's exit code is a bitmask: 1 copied, 2 extras, 4 mismatches
        (all fine), 8 some files FAILED, 16 fatal - so only 0..7 is success.
        Checked as "not in 0..7" rather than "-ge 8" because a robocopy that
        is killed, crashes or loses its destination device mid-copy can
        exit with a NEGATIVE or otherwise out-of-range code, and "-ge 8"
        reads every one of those as success. Kept as its own pure function
        so the rule is unit-tested without running robocopy.
    #>
    param([Parameter(Mandatory)][int]$ExitCode)
    return ($ExitCode -lt 0 -or $ExitCode -gt 7)
}

function Test-AutoOSUsbCopyReadback {
    <#
      .SYNOPSIS
        Compares every file under -SourceRoot with its copy under -DestRoot
        by size and SHA-256 and returns one record per mismatch (Path,
        Reason: missing | size | content). Empty result = identical.
      .DESCRIPTION
        The read-back step of Invoke-AutoOSUsbCopyImage, kept as a pure
        directory-vs-directory function so it is unit-tested with temp
        dirs and never needs a mounted ISO or a real stick. Streams every
        file through Get-AutoOSFileSha256, so memory stays flat for a
        multi-gigabyte squashfs.
    #>
    param(
        [Parameter(Mandatory)][string]$SourceRoot,
        [Parameter(Mandatory)][string]$DestRoot
    )
    $srcRoot = $SourceRoot.TrimEnd('\') + '\'
    $dstRoot = $DestRoot.TrimEnd('\') + '\'
    $bad = @()
    foreach ($f in Get-ChildItem -LiteralPath $srcRoot -Recurse -File -Force -ErrorAction SilentlyContinue) {
        $rel = $f.FullName.Substring($srcRoot.Length)
        $dst = Join-Path $dstRoot $rel
        if (-not (Test-Path -LiteralPath $dst -PathType Leaf)) {
            $bad += [pscustomobject]@{ Path = $rel; Reason = 'missing' }; continue
        }
        if ((Get-Item -LiteralPath $dst).Length -ne $f.Length) {
            $bad += [pscustomobject]@{ Path = $rel; Reason = 'size' }; continue
        }
        if ((Get-AutoOSFileSha256 -Path $f.FullName) -ne (Get-AutoOSFileSha256 -Path $dst)) {
            $bad += [pscustomobject]@{ Path = $rel; Reason = 'content' }
        }
    }
    return $bad
}

function Invoke-AutoOSUsbCopyImage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string]$ImagePath,
        [string[]]$ExtraFiles = @()
    )

    if ($env:AUTOOS_DRY_RUN -eq '1') {
        Write-AutoOSLine "would copy $ImagePath (+$($ExtraFiles.Count) extra file(s)) onto $DeviceId" -Level muted
        return
    }

    $target = Get-AutoOSUsbDevice | Where-Object { $_.DeviceId -eq $DeviceId } | Select-Object -First 1
    if (-not $target) { throw "Invoke-AutoOSUsbCopyImage: $DeviceId was not found among enumerated disks" }
    if ($target.MountedFileSystem -in @('CDFS', 'UDF')) {
        throw "Invoke-AutoOSUsbCopyImage: $DeviceId's volume is $($target.MountedFileSystem) - this stick was written with a raw block copy (finding A11) and is read-only. It must be rewritten before this engine can use it."
    }
    if (-not $target.MountedLetter) {
        throw "Invoke-AutoOSUsbCopyImage: $DeviceId has no mounted volume to copy onto"
    }
    $destRoot = "$($target.MountedLetter):\"

    $mounted = Mount-DiskImage -ImagePath $ImagePath -PassThru -ErrorAction Stop
    try {
        $srcLetter = ($mounted | Get-Volume).DriveLetter
        $srcRoot = "${srcLetter}:\"

        $biggest = Get-ChildItem -LiteralPath $srcRoot -Recurse -File -ErrorAction SilentlyContinue |
            Sort-Object Length -Descending | Select-Object -First 1
        if ($biggest -and $biggest.Length -gt 4294967295) {
            throw "Invoke-AutoOSUsbCopyImage: $($biggest.FullName.Substring($srcRoot.Length)) is $($biggest.Length) bytes - over FAT32's 4 GiB single-file ceiling (finding B16). Refusing before copying anything."
        }

        robocopy $srcRoot $destRoot /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        if (Test-AutoOSRobocopyFailed -ExitCode $LASTEXITCODE) {
            throw "Invoke-AutoOSUsbCopyImage: robocopy reported a failure copying $ImagePath onto $destRoot (exit $LASTEXITCODE)"
        }

        # Flush before anyone is told the stick is ready. robocopy returns
        # when Windows has ACCEPTED the writes, not when the stick has them:
        # on the first real build (2026-09-17) the stick was pulled after
        # "Ready to boot" was printed and came back with an empty boot
        # partition. The bash mirror (usb_copy_image) does sync + umount for
        # the same reason; Write-VolumeCache is the per-volume sync.
        try {
            Write-VolumeCache -DriveLetter $target.MountedLetter -ErrorAction Stop
        } catch {
            throw "Invoke-AutoOSUsbCopyImage: could not flush $($target.MountedLetter): after the copy ($($_.Exception.Message)) - the stick may hold unflushed writes; do not unplug it, and treat this build as incomplete"
        }

        # Read back EVERY file and compare it with the source (B15: "reads
        # back" means the bytes, not that the device still enumerates). The
        # second real build on 2026-09-17 finished, flushed, printed "Ready
        # to boot" - and the stick had silently replaced one 16 KB cluster
        # of md5sum.txt and part of the 3.4 GB squashfs with garbage, at
        # the right sizes, stable on re-read. robocopy cannot see that; only
        # reading the stick can. Costs one full read of the stick; a stick
        # that cannot afford that cannot be trusted to boot either.
        Write-AutoOSLine "reading back $destRoot to verify every file against the image" -Level info
        $bad = @(Test-AutoOSUsbCopyReadback -SourceRoot $srcRoot -DestRoot $destRoot)
        if ($bad.Count -gt 0) {
            $shown = ($bad | Select-Object -First 5 | ForEach-Object { "$($_.Path) ($($_.Reason))" }) -join '; '
            throw "Invoke-AutoOSUsbCopyImage: $($bad.Count) file(s) on $destRoot did not read back identical to the image: $shown - the stick is returning different bytes than were written (hardware fault, finding B15); do not boot it, and replace the stick"
        }
    } finally {
        Dismount-DiskImage -ImagePath $ImagePath -ErrorAction SilentlyContinue | Out-Null
    }

    $rescueDir = Join-Path $destRoot 'rescue'
    New-Item -ItemType Directory -Path $rescueDir -Force | Out-Null
    foreach ($f in $ExtraFiles) {
        if (-not (Test-Path -LiteralPath $f)) {
            Write-AutoOSLine "Invoke-AutoOSUsbCopyImage: extra file not found, skipping: $f" -Level warn
            continue
        }
        Copy-Item -LiteralPath $f -Destination $rescueDir -Force
    }
}

# Get-AutoOSUsbVentoyCacheDir
# Where the Ventoy TOOL itself (not an image) is cached - a sibling of the
# image cache, mirroring lib/linux/usb.sh's _usb_ventoy_cache_dir.
function Get-AutoOSUsbVentoyCacheDir {
    Join-Path (Split-Path -Parent (Get-AutoOSDownloadCacheDir)) 'tools\ventoy'
}

# Install-AutoOSUsbVentoy -DeviceId
# Ensures the Ventoy tool is present and verified, then runs
# Ventoy2Disk.exe -I -G -DeviceId. Mirrors lib/linux/usb.sh's
# usb_write_ventoy(): downloads from Ventoy's own GitHub release index,
# verifies the published sha256.txt entry for the exact asset downloaded
# (never a pinned version or checksum in this repository - AGENTS.md hard
# rule 2), and caches the extracted tool so a second call is a no-op
# download. $env:AUTOOS_FAKE_VENTOY_RELEASE substitutes the "ask GitHub"
# step with test-supplied JSON, the same knob the Linux mirror reads.
function Install-AutoOSUsbVentoy {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$DeviceId)

    if ($env:AUTOOS_DRY_RUN -eq '1') {
        Write-AutoOSLine "would run: Ventoy2Disk.exe -I -G $DeviceId" -Level muted
        return
    }

    $cacheDir = Get-AutoOSUsbVentoyCacheDir
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

    $bin = Get-ChildItem -Path $cacheDir -Filter 'Ventoy2Disk.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $bin) {
        if ($env:AUTOOS_FAKE_VENTOY_RELEASE) {
            $release = $env:AUTOOS_FAKE_VENTOY_RELEASE | ConvertFrom-Json
        } else {
            $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ventoy/Ventoy/releases/latest'
        }
        $asset = $release.assets | Where-Object { $_.name -like '*-windows.zip' } | Select-Object -First 1
        $shaAsset = $release.assets | Where-Object { $_.name -eq 'sha256.txt' } | Select-Object -First 1
        if (-not $asset) { throw "Install-AutoOSUsbVentoy: no windows.zip asset in the latest Ventoy release" }

        $zipPath = Join-Path $cacheDir $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

        if (-not $shaAsset) {
            Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
            throw "Install-AutoOSUsbVentoy: no published sha256 found for $($asset.name) - refusing to install an unverified Ventoy"
        }
        $shaText = (Invoke-WebRequest -Uri $shaAsset.browser_download_url -UseBasicParsing).Content
        $wantLine = ($shaText -split "`r?`n") | Where-Object { $_ -match [regex]::Escape($asset.name) } | Select-Object -First 1
        $want = if ($wantLine) { ($wantLine.Trim() -split '\s+')[0] } else { $null }
        $have = Get-AutoOSFileSha256 -Path $zipPath
        if (-not $want -or $have -ne $want.ToLowerInvariant()) {
            Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
            throw "Install-AutoOSUsbVentoy: checksum mismatch for $($asset.name) - refusing to install an unverified Ventoy"
        }

        Expand-Archive -LiteralPath $zipPath -DestinationPath $cacheDir -Force
        $bin = Get-ChildItem -Path $cacheDir -Filter 'Ventoy2Disk.exe' -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $bin) { throw "Install-AutoOSUsbVentoy: Ventoy2Disk.exe not found even after fetching Ventoy" }

    Write-AutoOSLine "run: $bin -I -G $DeviceId" -Level muted
    & $bin -I -G $DeviceId
    if ($LASTEXITCODE -ne 0) {
        throw "Install-AutoOSUsbVentoy: Ventoy2Disk.exe exited $LASTEXITCODE"
    }
}

# Add-AutoOSUsbVentoyPersistence -DeviceId [-SizeGb]
# Mirrors lib/linux/usb.sh's usb_ventoy_add_persistence() (Task 7 Step 4,
# B17/B18): a .dat file on the Ventoy data volume, registered in
# ventoy\ventoy.json, gives a live-persistent stick a writable overlay
# without repartitioning. Defaults to 16 GB, capped at half the stick's
# total size. Not wired into New-AutoOSUsbPlan's own output, for the same
# reason its Linux mirror is not - see usb_ventoy_add_persistence's own
# comment in lib/linux/usb.sh.
function Add-AutoOSUsbVentoyPersistence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [int]$SizeGb = 16
    )

    $target = Get-AutoOSUsbDevice | Where-Object { $_.DeviceId -eq $DeviceId } | Select-Object -First 1
    if (-not $target) { throw "Add-AutoOSUsbVentoyPersistence: $DeviceId was not found among enumerated disks" }

    if ($target.SizeBytes -gt 0) {
        # [int] on a [double] ROUNDS (MidpointRounding.ToEven) rather than
        # truncating - 31437766656 bytes/2/1e9 = 15.7188... would cast to
        # 16, not 15, silently defeating the whole cap on a stick sized just
        # so (confirmed live: good_stick's own 31437766656 B does exactly
        # this). [math]::Floor matches lib/linux/usb.sh's bash integer
        # division (which truncates toward zero) exactly.
        $halfGb = [int][math]::Floor($target.SizeBytes / 2 / 1000000000)
        if ($halfGb -gt 0 -and $SizeGb -gt $halfGb) {
            Write-AutoOSLine "Add-AutoOSUsbVentoyPersistence: ${SizeGb}GB would be more than half of $DeviceId - capping at ${halfGb}GB" -Level warn
            $SizeGb = $halfGb
        }
    }
    if ($SizeGb -lt 1) {
        throw "Add-AutoOSUsbVentoyPersistence: $DeviceId is too small to fit a persistence file"
    }

    if ($env:AUTOOS_DRY_RUN -eq '1') {
        Write-AutoOSLine "would create a ${SizeGb}GB Ventoy persistence file on $DeviceId" -Level muted
        return
    }
    if (-not $target.MountedLetter) {
        throw "Add-AutoOSUsbVentoyPersistence: $DeviceId has no mounted volume to write the persistence file onto"
    }

    $ventoyDir = "$($target.MountedLetter):\ventoy"
    New-Item -ItemType Directory -Path $ventoyDir -Force | Out-Null
    $datPath = Join-Path $ventoyDir 'persistence.dat'

    $sizeBytes = [int64]$SizeGb * 1000 * 1MB
    $stream = [System.IO.File]::Open($datPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try { $stream.SetLength($sizeBytes) } finally { $stream.Close() }

    $jsonPath = Join-Path $ventoyDir 'ventoy.json'
    $data = if (Test-Path -LiteralPath $jsonPath) {
        try { Get-Content -LiteralPath $jsonPath -Raw | ConvertFrom-Json } catch { [pscustomobject]@{} }
    } else { [pscustomobject]@{} }
    $persistence = @()
    if ($data.PSObject.Properties.Name -contains 'persistence') { $persistence = @($data.persistence) }
    $entry = [pscustomobject]@{ backend = '/ventoy/persistence.dat'; mount = @('/') }
    if (-not ($persistence | Where-Object { $_.backend -eq $entry.backend })) {
        $persistence += $entry
    }
    if ($data.PSObject.Properties.Name -contains 'persistence') {
        $data.persistence = $persistence
    } else {
        $data | Add-Member -NotePropertyName persistence -NotePropertyValue $persistence -Force
    }
    $data | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $jsonPath -Encoding utf8
}

Export-ModuleMember -Function `
    Get-AutoOSUsbDevice, Assert-AutoOSUsbSafe, Test-AutoOSElevated, Assert-AutoOSElevated, `
    New-AutoOSUsbPlan, Get-AutoOSUsbEngine, Get-AutoOSUsbImage, Get-AutoOSUsbEngineList, `
    Get-AutoOSUsbCurrentOs, Get-AutoOSUsbCurrentArch, Test-AutoOSUsbRunActive, `
    Invoke-AutoOSUsbPlan, Invoke-AutoOSUsbFetchImage, Get-AutoOSUsbSumsDigest, `
    Write-AutoOSUsbRaw, Invoke-AutoOSUsbCopyImage, Test-AutoOSRobocopyFailed, Test-AutoOSUsbCopyReadback, `
    Install-AutoOSUsbVentoy, Add-AutoOSUsbVentoyPersistence, Get-AutoOSUsbVentoyCacheDir
