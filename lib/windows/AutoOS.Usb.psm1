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
# WindowsIdentity check in the whole codebase.
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Detect.psm1') -DisableNameChecking

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
        return @($env:AUTOOS_FAKE_DISKS | ConvertFrom-Json)
    }

    $disks = Get-Disk -ErrorAction Stop
    $result = @()
    foreach ($d in $disks) {
        $partitions = @()
        try {
            $partitions = @(Get-Partition -DiskNumber $d.Number -ErrorAction Stop |
                ForEach-Object {
                    [pscustomobject]@{
                        DriveLetter = if ($_.DriveLetter) { [string]$_.DriveLetter } else { $null }
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
    foreach ($p in @($Raw.Partitions)) {
        if ($p.DriveLetter) { $mountedLetter = [string]$p.DriveLetter; break }
    }

    [pscustomobject]@{
        DeviceId      = "\\.\PHYSICALDRIVE$($Raw.Number)"
        Number        = $Raw.Number
        Model         = $Raw.Model
        SizeBytes     = [int64]$Raw.Size
        Bus           = $Raw.BusType
        IsRemovable   = [bool]$Raw.IsRemovable
        IsSystem      = [bool]$Raw.IsSystem
        IsBoot        = [bool]$Raw.IsBoot
        MountedLetter = $mountedLetter
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
          3. mounted (drive-lettered) partition — refuse a target with an
             assigned drive letter; writing under a mounted volume corrupts
             it.
          4. size — refuse a stick too small for the image, only when the
             caller has told us how big the image is ($env:AUTOOS_IMAGE_BYTES;
             unset means "unknown", never a refusal).
      .PARAMETER DeviceId
        A device id as returned by Get-AutoOSUsbDevice, e.g.
        \\.\PHYSICALDRIVE5.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$DeviceId)

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

    if ($target.MountedLetter) {
        throw "$DeviceId has a mounted volume ($($target.MountedLetter):) — unmount it first"
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

Export-ModuleMember -Function `
    Get-AutoOSUsbDevice, Assert-AutoOSUsbSafe, Test-AutoOSElevated, Assert-AutoOSElevated
