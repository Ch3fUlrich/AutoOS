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

# Get-StartApps takes a couple of seconds; a run asks about dozens of
# components, so it is read once and reused.
$script:StartAppsCache = $null
$script:AppxCache      = $null
$script:ShortcutCache  = $null
$script:InstalledCache = @{}
$script:InstalledInventory = $null

function Clear-AutoOSInstalledStatus {
    $script:InstalledCache = @{}
    $script:InstalledInventory = $null
}

function Get-AutoOSInstalledInventory {
    # Inspect local registration only. A winget list for each row can contact
    # sources, prompt, or hang before an installer even starts.
    if ($null -ne $script:InstalledInventory) { return $script:InstalledInventory }
    $entries = @(foreach ($root in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($entry in @(Get-ItemProperty -Path "$root\*" -ErrorAction SilentlyContinue)) {
            $display = if ($entry.PSObject.Properties.Name -contains 'DisplayName') { [string]$entry.DisplayName } else { '' }
            $identifier = if ($entry.PSObject.Properties.Name -contains 'WinGetPackageIdentifier') { [string]$entry.WinGetPackageIdentifier } else { '' }
            if ($display -or $identifier) {
                [pscustomobject]@{ Name = $display; Package = $identifier; Key = $entry.PSChildName }
            }
        }
    })
    # Use persistent PATH, not the hosting process's injected dependency PATH.
    # In particular, Codex's private pwsh runtime is not an installed PS7 app.
    $paths = @((([Environment]::GetEnvironmentVariable('Path', 'Machine')) + ';' +
                 ([Environment]::GetEnvironmentVariable('Path', 'User'))) -split ';' |
        Where-Object { $_ } | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_.Trim('"')) } |
        Where-Object { $_ -notmatch '(?i)[\\/](?:\.codex|codex-runtimes|codex-primary-runtime)[\\/]' } |
        Select-Object -Unique)
    $appx = @()
    try {
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $psi.Arguments = '-NoProfile -Command "Get-AppxPackage | Select-Object -ExpandProperty Name"'
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $probe = [Diagnostics.Process]::Start($psi)
        try {
            $out = $probe.StandardOutput.ReadToEndAsync(); $err = $probe.StandardError.ReadToEndAsync()
            if ($probe.WaitForExit(8000) -and $out.Wait(1000)) { $appx = @($out.Result -split '[\r\n]+' | Where-Object { $_ }) }
            else { $probe.Kill() }
            $null = $err
        } finally { $probe.Dispose() }
    } catch { $appx = @() }
    $script:InstalledInventory = [pscustomobject]@{ Entries = $entries; Paths = $paths; Appx = $appx }
    $script:InstalledInventory
}

function Test-AutoOSRegisteredName {
    param([string]$Actual, [string]$Expected)
    if (-not $Expected) { return $false }
    # Permit version/architecture suffixes, but never arbitrary substring
    # matches: "Steam Tools" is not Steam and "Firefox Helper" is not Firefox.
    $pattern = '^' + [regex]::Escape($Expected) + '(?:\s+(?:v?\d[^\r\n]*|\((?:x64|x86|arm64|64-bit|32-bit)(?: [a-z]{2}(?:-[A-Z]{2})?)?\)|\((?:User|Machine)\)))?$'
    $Actual -match $pattern
}

function Get-AutoOSInstalledStatus {
    <# .SYNOPSIS Read-only detection shared by selection and installer skipping.
       .DESCRIPTION Unknown is retained when a component has no reliable probe.
       Verify commands are never executed: some of them launch a desktop app. #>
    param([Parameter(Mandatory)][psobject]$Component, [switch]$Refresh)
    if ($Refresh) { $script:InstalledCache = @{}; $script:InstalledInventory = $null }
    $verify = if ($Component.PSObject.Properties.Name -contains 'Verify') { [string]$Component.Verify } else { '' }
    $name = if ($Component.PSObject.Properties.Name -contains 'Name') { [string]$Component.Name } else { '' }
    $key = "$($Component.Provider)|$($Component.Package)|$name|$verify"
    if ($script:InstalledCache.ContainsKey($key)) { return $script:InstalledCache[$key] }
    $status = 'unknown'
    if ($Component.Provider -eq 'custom') {
        if ($Component.Package -eq 'agent-skills') {
            $docs = [Environment]::GetFolderPath('MyDocuments')
            return $(if (Test-Path -LiteralPath (Join-Path $docs 'Code\agent-skills') -PathType Container) { 'installed' } else { 'not-detected' })
        }
        if ($Component.Package -like 'mcp-*') {
            $server = $Component.Package.Substring(4)
            foreach ($config in @((Join-Path $env:USERPROFILE '.claude.json'), (Join-Path $env:USERPROFILE '.gemini\antigravity\mcp_config.json'))) {
                if (-not (Test-Path -LiteralPath $config)) { continue }
                try {
                    $data = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
                    if ($data.PSObject.Properties.Name -contains 'mcpServers' -and $data.mcpServers.PSObject.Properties.Name -contains $server) { return 'installed' }
                } catch { continue }
            }
        }
        return $status
    }
    if ($Component.Provider -eq 'psmodule') {
        $minimum = if ($Component.PSObject.Properties.Name -contains 'MinimumVersion' -and $Component.MinimumVersion) { [version]$Component.MinimumVersion } else { [version]'0.0' }
        $found = @(Get-Module -ListAvailable -Name $Component.Package | Where-Object { $_.Version -ge $minimum -and $_.ModuleBase -notmatch '[\\/](?:codex-runtimes|codex-primary-runtime)[\\/]' })
        return $(if ($found.Count) { 'installed' } else { 'not-detected' })
    }
    $inventory = Get-AutoOSInstalledInventory
    if ($Component.PSObject.Properties.Name -contains 'InstalledAppx' -and $inventory.PSObject.Properties.Name -contains 'Appx') {
        foreach ($app in $Component.InstalledAppx) { if ($app -in $inventory.Appx) { $script:InstalledCache[$key] = 'installed'; return 'installed' } }
    }
    $names = @($name)
    if ($Component.PSObject.Properties.Name -contains 'InstalledNames') { $names += @($Component.InstalledNames) }
    foreach ($entry in $inventory.Entries) {
        if (($entry.Package -and $entry.Package -eq $Component.Package) -or
            ($entry.Key -eq "$($Component.Package)_winget")) { $status = 'installed'; break }
        foreach ($candidate in $names) {
            if (Test-AutoOSRegisteredName $entry.Name $candidate) { $status = 'installed'; break }
        }
        if ($status -eq 'installed') { break }
    }
    if ($status -ne 'installed' -and $verify -match '^([a-zA-Z0-9][a-zA-Z0-9_.-]*)(?:\s|$)') {
        $exe = $Matches[1]
        foreach ($directory in $inventory.Paths) {
            # WindowsApps contains execution aliases (including uninstalled
            # Python Store stubs), so existence there cannot prove installation.
            if ($directory -match '(?i)[\\/]Microsoft[\\/]WindowsApps(?:[\\/]|$)') { continue }
            foreach ($extension in @('', '.exe', '.cmd', '.bat')) {
                if (Test-Path -LiteralPath (Join-Path $directory ($exe + $extension)) -PathType Leaf) {
                    $status = 'installed'; break
                }
            }
            if ($status -eq 'installed') { break }
        }
        if ($status -ne 'installed') { $status = 'not-detected' }
    }
    if ($status -eq 'unknown' -and $Component.Provider -in @('winget', 'choco', 'manual')) { $status = 'not-detected' }
    if ($Component.Provider -eq 'script' -and $Component.Package -eq 'meslo-nerd-font') {
        $fontPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Fonts\MesloLGS NF Regular.ttf'
        $status = if (Test-Path -LiteralPath $fontPath) { 'installed' } else { 'not-detected' }
    }
    $script:InstalledCache[$key] = $status
    $status
}

function Set-AutoOSInstalledStatus {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Components, [switch]$Refresh)
    if ($Refresh) { $script:InstalledCache = @{}; $script:InstalledInventory = $null }
    foreach ($component in $Components) {
        $status = Get-AutoOSInstalledStatus -Component $component
        Add-Member -InputObject $component -NotePropertyName InstalledStatus -NotePropertyValue $status -Force
        Add-Member -InputObject $component -NotePropertyName Installed -NotePropertyValue ($status -eq 'installed') -Force
    }
}

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
            Fix      = 'Some installers may request administrator approval. Use an elevated terminal if a machine-wide install fails.'
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

function Get-AutoOSStartMenuShortcut {
    <#
      .SYNOPSIS Find the Start menu entry for a display name, if there is one.

      .DESCRIPTION
        Most of this catalog is desktop software with no command on PATH, so the
        honest answer to "how do I open it" is the shortcut the installer made.
        Both the machine-wide and per-user Start menus are searched, because an
        elevated run and a user-scope run put things in different places.
    #>
    param([Parameter(Mandatory)][string]$Name)

    # Enumerated once: a run asks about dozens of components and recursing both
    # Start menu trees each time is the slowest thing in the report.
    if ($null -eq $script:ShortcutCache) {
        $roots = @(
            (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'),
            (Join-Path $env:APPDATA     'Microsoft\Windows\Start Menu\Programs')
        ) | Where-Object { $_ -and (Test-Path $_) }
        $script:ShortcutCache = @(foreach ($root in $roots) {
            Get-ChildItem -Path $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue
        })
    }
    if (-not $script:ShortcutCache) { return $null }

    # Compare on letters and digits only: "Notepad++" and "7-Zip" never match
    # their shortcut names otherwise.
    $wanted = ($Name -replace '[^\p{L}\p{N}]', '').ToLowerInvariant()
    if (-not $wanted) { return $null }

    # An exact name wins outright; a prefix is kept only if nothing better turns up.
    $fallback = $null
    foreach ($lnk in $script:ShortcutCache) {
        if ($lnk.BaseName -match '(?i)(manual|uninstall|readme|help|website|reset preferences|cache files)') { continue }
        $bare = ($lnk.BaseName -replace '[^\p{L}\p{N}]', '').ToLowerInvariant()
        if ($bare -eq $wanted) { return $lnk }
        if ((-not $fallback) -and $bare.StartsWith($wanted)) { $fallback = $lnk }
    }
    $fallback
}

function Get-AutoOSStartApp {
    <#
      .SYNOPSIS Find a Start menu entry that has no shortcut file behind it.

      .DESCRIPTION
        Matches on the AppID as well as the display name, because the two often
        disagree: Windows Terminal is listed as "Terminal", and only its AppID
        still says WindowsTerminal.
    #>
    param([Parameter(Mandatory)][string]$Name)

    if ($null -eq $script:StartAppsCache) {
        $script:StartAppsCache = @(try { Get-StartApps -ErrorAction SilentlyContinue } catch { @() })
    }
    $wanted = ($Name -replace '[^\p{L}\p{N}]', '').ToLowerInvariant()
    if (-not $wanted) { return $null }

    foreach ($a in $script:StartAppsCache) {
        $bare = ($a.Name -replace '[^\p{L}\p{N}]', '').ToLowerInvariant()
        if ($bare -eq $wanted) { return $a }
    }
    foreach ($a in $script:StartAppsCache) {
        $id = ($a.AppID -replace '[^\p{L}\p{N}]', '').ToLowerInvariant()
        if ($id -like "*$wanted*") { return $a }
    }
    $null
}

function Get-AutoOSLaunchHint {
    <#
      .SYNOPSIS Where a component actually landed, and how to start it.

      .DESCRIPTION
        Resolved from the live machine rather than declared in the catalog, so it
        reports what is true here rather than what was true on the author's box.
        Returns empty strings when nothing can be found - a blank is honest, a
        guessed path is not.
    #>
    param([Parameter(Mandatory)][psobject]$Component)

    $result = [pscustomobject]@{ Path = ''; How = '' }

    # A verify command names the executable, which is the most precise handle
    # there is: Get-Command resolves it to the exact file that will run.
    $verify = if ($Component.PSObject.Properties.Name -contains 'Verify') { $Component.Verify } else { $null }
    if ($verify) {
        $exe = ($verify -split '\s+')[0]
        $cmd = Get-Command -Name $exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and $cmd.Source) {
            $result.Path = $cmd.Source
            $result.How  = "run  $exe"
            return $result
        }
    }

    $lnk = Get-AutoOSStartMenuShortcut -Name $Component.Name
    if ($lnk) {
        $target = ''
        try {
            $shell = New-Object -ComObject WScript.Shell
            $target = $shell.CreateShortcut($lnk.FullName).TargetPath
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        } catch { $target = '' }
        $result.Path = if ($target) { $target } else { $lnk.FullName }
        $result.How  = "Start menu:  $($lnk.BaseName)"
        return $result
    }

    # Store and MSIX apps (Windows Terminal, WhatsApp) have no shortcut file -
    # their Start entry is an AppID, and their payload lives under WindowsApps.
    $app = Get-AutoOSStartApp -Name $Component.Name
    if ($app) {
        $result.How = "Start menu:  $($app.Name)"
        $family = ($app.AppID -split '!')[0]
        if ($null -eq $script:AppxCache) {
            $script:AppxCache = @(try { Get-AppxPackage -ErrorAction SilentlyContinue } catch { @() })
        }
        $pkg = $script:AppxCache | Where-Object { $_.PackageFamilyName -eq $family } | Select-Object -First 1
        if ($pkg) { $result.Path = $pkg.InstallLocation }
        return $result
    }

    # Last resort: something whose id happens to be its command, like 7z.
    $cmd = Get-Command -Name $Component.Id -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source) {
        $result.Path = $cmd.Source
        $result.How  = "run  $($Component.Id)"
    }
    $result
}

Export-ModuleMember -Function `
    Test-AutoOSCommand, Test-AutoOSAdmin, Get-AutoOSSystemInfo,
    Get-AutoOSSuggestedProfile, Get-AutoOSBlockers,
    Get-AutoOSLaunchHint, Get-AutoOSStartMenuShortcut, Get-AutoOSStartApp,
    Get-AutoOSInstalledStatus, Set-AutoOSInstalledStatus, Clear-AutoOSInstalledStatus
