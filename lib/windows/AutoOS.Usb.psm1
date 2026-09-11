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
            $lines.Add("Write-AutoOSUsbRaw $DeviceId $localPath")
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

Export-ModuleMember -Function `
    Get-AutoOSUsbDevice, Assert-AutoOSUsbSafe, Test-AutoOSElevated, Assert-AutoOSElevated, `
    New-AutoOSUsbPlan, Get-AutoOSUsbEngine, Get-AutoOSUsbImage, Get-AutoOSUsbEngineList, `
    Get-AutoOSUsbCurrentOs, Get-AutoOSUsbCurrentArch, Test-AutoOSUsbRunActive
