#Requires -Version 5.1
<#
.SYNOPSIS
    Machine and capability detection for AutoOS on Windows.

.DESCRIPTION
    Pure inspection: nothing here installs, downloads or writes. The result is a
    single flat object that the rest of the pipeline reads, which is what makes
    the selection stage testable against captured fixtures instead of a live box.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-AutoOSCommand {
    param([Parameter(Mandatory)][string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-AutoOSAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { $false }
}

function Get-AutoOSSystemInfo {
    <#
      .SYNOPSIS Everything the pipeline needs to know about this machine.
    #>
    [CmdletBinding()]
    param()

    $info = [ordered]@{}

    # ─── Operating system ───────────────────────────────────────────────────
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $info.OsName    = $os.Caption
        $info.OsVersion = $os.Version
        $info.OsBuild   = [int]($os.BuildNumber)
    } catch {
        $info.OsName    = 'Windows (unknown edition)'
        $info.OsVersion = [Environment]::OSVersion.Version.ToString()
        $info.OsBuild   = [Environment]::OSVersion.Version.Build
    }
    # 22000 is the first Windows 11 build; everything below is Windows 10 or older.
    $info.WindowsMajor = if ($info.OsBuild -ge 22000) { 11 } elseif ($info.OsBuild -ge 10240) { 10 } else { 0 }

    # ─── Architecture ───────────────────────────────────────────────────────
    $arch = $env:PROCESSOR_ARCHITECTURE
    $info.Arch = switch -Regex ($arch) {
        'ARM64' { 'arm64' }
        'AMD64' { 'x64' }
        'x86'   { if ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64') { 'x64' } else { 'x86' } }
        default { 'x64' }
    }

    # ─── Hardware ───────────────────────────────────────────────────────────
    try {
        $cs  = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        $cpu = @(Get-CimInstance Win32_Processor -ErrorAction Stop)[0]
        $info.Manufacturer = $cs.Manufacturer
        $info.Model        = $cs.Model
        $info.CpuName      = ($cpu.Name -replace '\s+', ' ').Trim()
        $info.CpuCores     = [int]$cs.NumberOfLogicalProcessors
        $info.RamGB        = [Math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
        $info.IsVirtual    = $cs.Model -match 'Virtual|VMware|KVM|Xen|Hyper-V'
    } catch {
        $info.Manufacturer = 'unknown'; $info.Model = 'unknown'
        $info.CpuName = 'unknown'; $info.CpuCores = 1; $info.RamGB = 0; $info.IsVirtual = $false
    }

    try {
        $gpus = @(Get-CimInstance Win32_VideoController -ErrorAction Stop |
                  Where-Object { $_.Name -and $_.Name -notmatch 'Remote|Basic Display' })
        $info.Gpu       = if ($gpus.Count) { ($gpus[0].Name -replace '\s+', ' ').Trim() } else { 'none' }
        $info.HasGpu    = $gpus.Count -gt 0
        $info.HasDiscreteGpu = [bool](@($gpus | Where-Object { $_.Name -match 'NVIDIA|Radeon RX|Arc' }).Count)
    } catch { $info.Gpu = 'unknown'; $info.HasGpu = $false; $info.HasDiscreteGpu = $false }

    try {
        $bat = @(Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)
        $info.IsLaptop = $bat.Count -gt 0
    } catch { $info.IsLaptop = $false }

    # Reported so a dictation tool like Handy can say up front whether this
    # machine can actually use it. Never used to hide the component: Handy
    # installs fine without a microphone, it is just not much use.
    try {
        $mics = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
                  Where-Object { $_.PNPClass -eq 'AudioEndpoint' -and
                                 $_.Name -match 'Microphone|Mikrofon|Input' })
        $info.HasMicrophone = $mics.Count -gt 0
        $info.Microphone = if ($mics.Count) { ($mics[0].Name -replace '\s+', ' ').Trim() } else { 'none' }
    } catch { $info.HasMicrophone = $false; $info.Microphone = 'unknown' }

    try {
        $sys = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'" -ErrorAction Stop
        $info.FreeDiskGB = [Math]::Round($sys.FreeSpace / 1GB, 1)
    } catch { $info.FreeDiskGB = 0 }

    # ─── Session ────────────────────────────────────────────────────────────
    $info.IsAdmin      = Test-AutoOSAdmin
    $info.UserName     = $env:USERNAME
    $info.PsVersion    = $PSVersionTable.PSVersion.ToString()
    $info.PsEdition    = $PSVersionTable.PSEdition
    # A machine reached only over SSH has no interactive desktop session.
    $info.IsHeadless   = [bool]($env:SSH_CLIENT -or $env:SSH_TTY) -or -not [Environment]::UserInteractive
    $info.IsInteractive = -not ([Console]::IsInputRedirected)

    # ─── Available package managers and runtimes ────────────────────────────
    $info.HasWinget = Test-AutoOSCommand 'winget'
    $info.HasChoco  = Test-AutoOSCommand 'choco'
    $info.HasScoop  = Test-AutoOSCommand 'scoop'
    $info.HasGit    = Test-AutoOSCommand 'git'
    $info.HasNode   = Test-AutoOSCommand 'node'
    $info.HasNpm    = Test-AutoOSCommand 'npm'
    $info.HasDocker = Test-AutoOSCommand 'docker'
    $info.HasWsl    = Test-AutoOSCommand 'wsl'

    $info.NodeVersion = if ($info.HasNode) { (& node --version 2>$null) } else { $null }

    # Virtualisation matters because Docker Desktop and WSL2 both need it.
    try {
        $cpu2 = @(Get-CimInstance Win32_Processor -ErrorAction Stop)[0]
        $info.VirtualizationEnabled = [bool]$cpu2.VirtualizationFirmwareEnabled
    } catch { $info.VirtualizationEnabled = $null }

    [pscustomobject]$info
}

function Get-AutoOSSuggestedProfile {
    <#
      .SYNOPSIS
        Pick a sensible default profile from the detected hardware.
      .DESCRIPTION
        Only ever a *default* — the user confirms it. Deliberately conservative:
        a low-spec or headless machine gets the small profile rather than being
        handed a desktop install it cannot use.
    #>
    param([Parameter(Mandatory)][psobject]$SystemInfo)

    if ($SystemInfo.IsHeadless)       { return 'ai-coding' }
    if ($SystemInfo.Arch -eq 'arm64') { return 'light' }
    # An 8 GB machine reports ~7.4 GB usable, so the small-board threshold sits
    # below that: 'light' is for Pi-class hardware, not for a modest laptop.
    if ($SystemInfo.RamGB -gt 0 -and $SystemInfo.RamGB -lt 6) { return 'light' }
    if ($SystemInfo.RamGB -ge 16 -and $SystemInfo.CpuCores -ge 8) { return 'workstation' }
    'ai-coding'
}

function Get-AutoOSBlockers {
    <#
      .SYNOPSIS
        Conditions that will make the run fail or install nothing useful.
      .OUTPUTS
        Array of @{ Severity; Message; Fix }
    #>
    param([Parameter(Mandatory)][psobject]$SystemInfo)

    $blockers = @()

    if (-not $SystemInfo.HasWinget -and -not $SystemInfo.HasChoco) {
        $blockers += @{
            Severity = 'error'
            Message  = 'Neither winget nor Chocolatey is available.'
            Fix      = 'Install App Installer from the Microsoft Store, or run with -BootstrapChoco.'
        }
    }
    if (-not $SystemInfo.IsAdmin) {
        $blockers += @{
            Severity = 'warn'
            Message  = 'Not running as Administrator.'
            Fix      = 'Machine-wide packages will be skipped. Re-run from an elevated terminal for a full install.'
        }
    }
    if ($SystemInfo.FreeDiskGB -gt 0 -and $SystemInfo.FreeDiskGB -lt 10) {
        $blockers += @{
            Severity = 'warn'
            Message  = "Only $($SystemInfo.FreeDiskGB) GB free on $($env:SystemDrive)."
            Fix      = 'Free up space before selecting large components such as Docker Desktop.'
        }
    }
    if ($SystemInfo.VirtualizationEnabled -eq $false) {
        $blockers += @{
            Severity = 'warn'
            Message  = 'Hardware virtualisation is disabled in firmware.'
            Fix      = 'Enable VT-x / AMD-V in the BIOS, otherwise Docker Desktop and WSL2 will not start.'
        }
    }
    if ($SystemInfo.WindowsMajor -eq 0) {
        $blockers += @{
            Severity = 'warn'
            Message  = "Unrecognised Windows build ($($SystemInfo.OsBuild))."
            Fix      = 'AutoOS targets Windows 10 build 10240 and newer; older builds are untested.'
        }
    }
    $blockers
}

Export-ModuleMember -Function `
    Test-AutoOSCommand, Test-AutoOSAdmin, Get-AutoOSSystemInfo,
    Get-AutoOSSuggestedProfile, Get-AutoOSBlockers
