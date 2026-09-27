#Requires -Version 5.1
<#
.SYNOPSIS
    AutoOS browser UI for headless or remote Windows machines.

.DESCRIPTION
    Serves web/index.html and drives the real installer by shelling out to
    setup.ps1, so the browser path and the terminal path cannot drift apart.

    Security: binds 127.0.0.1 by default and always requires a per-run token.
    A wider bind is opt-in and warned about, because this endpoint installs
    software. Non-loopback binding also needs an elevated shell (HTTP.sys URL
    reservation), which is called out rather than failing obscurely.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1')      -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Detect.psm1')  -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Catalog.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Install.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Usb.psm1')     -DisableNameChecking

$script:Log     = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
$script:RunInfo = [hashtable]::Synchronized(@{ Running = $false; Done = 0; Total = 0; Summary = ''; Current = $null })

# The installer process and one pending async read per stream. The server loop
# drains them; see Update-AutoOSInstallLog for why it is not a reader thread.
$script:Proc    = $null
$script:OutTask = $null
$script:ErrTask = $null

function Get-AutoOSProviderStatus {
    <#
      .SYNOPSIS
        Which allowlisted keys have a value in configuration/api-keys.yml.
      .DESCRIPTION
        Values never leave this function: the payload carries only the key id,
        a display label and whether a value is present.
    #>
    param([string]$RepoRoot = $script:RepoRoot)
    $have = Get-AutoOSConfiguredIds -RepoRoot $RepoRoot
    $out = foreach ($id in $script:SecretKeys.Keys) {
        [ordered]@{ id = $id; name = $script:SecretKeys[$id].name; configured = $have.Contains($id) }
    }
    return @($out)
}

# -- AI services: live status + the setup actions that already exist ------
# Same payload as serve.py's service_status(): the browser page is shared.
# `bind` and `auth` are what each service is configured for
# (docs/web-services.md); `up` is probed live on loopback. No key is read.
$script:Services = @(
    [ordered]@{ id = 'omniroute'; name = 'OmniRoute gateway'; port = 20128; path = '/api/health'
        bind = '0.0.0.0'; auth = 'client key on /v1 (REQUIRE_API_KEY); dashboard password'
        unit = 'autoos-omniroute'; actions = @('apply-dry-run', 'apply', 'resume-stack') },
    [ordered]@{ id = 'litellm'; name = 'LiteLLM fallback'; port = 4000; path = '/'
        bind = '127.0.0.1'; auth = 'LITELLM_MASTER_KEY'
        unit = 'autoos-litellm'; actions = @('resume-stack') },
    [ordered]@{ id = 'opencode'; name = 'opencode serve (web UI)'; port = 4096; path = '/'
        bind = '0.0.0.0'; auth = 'HTTP Basic on /api/* (user opencode)'
        unit = 'autoos-opencode'; actions = @('start-opencode-serve') },
    [ordered]@{ id = 'openhands'; name = 'OpenHands'; port = 3000; path = '/'
        bind = '0.0.0.0'; auth = 'none - reach it only through the proxy'
        unit = $null; actions = @('start-openhands') }
)

# The only commands the page can run. live = $false is a dry run (no
# confirmation needed); everything else is confirmed in the browser and
# refused when the server itself runs with -DryRun.
$script:ServiceActions = [ordered]@{
    'apply-dry-run'        = @{ label = 'Apply router (dry run)'; live = $false; script = 'configuration\omniroute\apply.ps1'; args = @('-DryRun') }
    'apply'                = @{ label = 'Apply router'; live = $true; script = 'configuration\omniroute\apply.ps1'; args = @() }
    'start-openhands'      = @{ label = 'Start OpenHands'; live = $true; script = 'configuration\start-stack.ps1'; args = @('-App', 'openhands') }
    'start-opencode-serve' = @{ label = 'Start opencode serve'; live = $true; script = 'configuration\start-stack.ps1'; args = @('-App', 'opencode-serve') }
    'resume-stack'         = @{ label = 'Resume the stack'; live = $true; script = 'configuration\autostart\Start-AutoOSStack.ps1'; args = @() }
}

function Get-AutoOSServiceStatus {
    <#
      .SYNOPSIS
        Live status of every AI service (gateway, litellm, opencode serve,
        OpenHands). -Probe { param($port, $path) <http code> } is injectable
        so tests never touch this machine's ports; 0 means nothing answered.
    #>
    param([scriptblock]$Probe)
    if (-not $Probe) {
        $Probe = {
            param($port, $path)
            try {
                (Invoke-WebRequest -Uri "http://127.0.0.1:$port$path" -UseBasicParsing -TimeoutSec 2).StatusCode
            } catch {
                $resp = $_.Exception.Response
                if ($resp) { [int]$resp.StatusCode } else { 0 }
            }
        }
    }
    $out = foreach ($svc in $script:Services) {
        $code = [int](& $Probe $svc.port $svc.path)
        $up = switch ($svc.id) {
            'omniroute' { $code -eq 200 }
            'litellm'   { $code -eq 200 }
            'opencode'  { $code -eq 200 -or $code -eq 401 }
            default     { $code -ne 0 }
        }
        $labels = [ordered]@{}
        foreach ($a in $svc.actions) { $labels[$a] = $script:ServiceActions[$a].label }
        [ordered]@{
            id = $svc.id; name = $svc.name; port = $svc.port; bind = $svc.bind; auth = $svc.auth
            unit = $svc.unit; code = $code; up = [bool]$up
            health = "http://127.0.0.1:$($svc.port)$($svc.path)"
            actions = @($svc.actions); actionLabels = $labels
            liveActions = @($svc.actions | Where-Object { $script:ServiceActions[$_].live })
        }
    }
    $result = @($out)
    return $result
}

function Get-AutoOSServiceActionResult {
    <#
      .SYNOPSIS
        Validate a POST /api/services/action body. Returns @{ Code; Payload;
        Action } - Action is set only when the caller may start the job.
    #>
    param($Body, [bool]$ForceDryRun)
    $key = $null
    if ($Body -and $Body.PSObject.Properties.Name -contains 'action') { $key = [string]$Body.action }
    if (-not $key -or -not $script:ServiceActions.Contains($key)) {
        return @{ Code = 400; Payload = @{ error = 'unknown action'; allowed = @($script:ServiceActions.Keys) }; Action = $null }
    }
    if ($script:ServiceActions[$key].live -and $ForceDryRun) {
        return @{ Code = 409; Payload = @{ error = 'this server runs with -DryRun: only dry-run actions are allowed' }; Action = $null }
    }
    return @{ Code = 202; Payload = @{ started = $true; action = $key }; Action = $key }
}

function Start-AutoOSServiceActionJob {
    <#
      .SYNOPSIS
        Run one allowlisted service action as a child PowerShell, streaming
        into the same log/progress the install job uses.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Action
    )
    $act = $script:ServiceActions[$Action]
    $script:Log.Clear()
    $script:RunInfo.Running = $true
    $script:RunInfo.Done    = 0
    $script:RunInfo.Total   = 1
    $script:RunInfo.Summary = ''
    $script:RunInfo.Current = $null

    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $RepoRoot $act.script)) + @($act.args)
    [void]$script:Log.Add(@{ level = 'step'; text = '$ powershell ' + ($psArgs -join ' ') })

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = (Get-Command powershell).Source
    $psi.Arguments = ($psArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory      = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.EnvironmentVariables['AUTOOS_NO_COLOR'] = '1'
    try { $script:Proc = [System.Diagnostics.Process]::Start($psi) }
    catch {
        $script:RunInfo.Running = $false
        $script:RunInfo.Summary = "could not start: $($act.label)"
        throw
    }
    $script:OutTask = $script:Proc.StandardOutput.ReadLineAsync()
    $script:ErrTask = $script:Proc.StandardError.ReadLineAsync()
}

function Get-AutoOSLineLevel {
    param([string]$Line)
    $t = $Line.TrimStart()
    if ($t.StartsWith('+ ')) { return 'ok' }
    if ($t.StartsWith('! ')) { return 'warn' }
    if ($t.StartsWith('x ')) { return 'err' }
    if ($t.StartsWith('> ')) { return 'step' }
    if ($t -match '^(run:|would run:|would )') { return 'muted' }
    ''
}

function Get-AutoOSServeState {
    param([psobject]$SystemInfo, [psobject]$Catalog, [hashtable]$InstalledStatus = @{})

    $available = @(Get-AutoOSAvailableComponents -Catalog $Catalog -SystemInfo $SystemInfo)

    # Read every catalog, not just this machine's, so the page can say "Linux
    # only" rather than leaving the reader to guess whether something is absent
    # here because it cannot exist or because nobody has added it yet.
    $platforms = @{}
    foreach ($plat in @('windows', 'linux', 'macos')) {
        $path = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "catalog\$plat.json"
        if (-not (Test-Path $path)) { continue }
        $cat = Get-Content -Path $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($grp in $cat.categories) {
            foreach ($c in $grp.components) {
                if (-not $platforms.ContainsKey($c.id)) { $platforms[$c.id] = @() }
                $platforms[$c.id] += $plat
            }
        }
    }
    $installedList = @($available | Where-Object { $InstalledStatus[$_.Id] -eq 'installed' })
    $installedNames = @($installedList | ForEach-Object { $_.Name })
    $components = foreach ($c in $available) {
        [ordered]@{
            id = $c.Id; name = $c.Name; description = $c.Description
            provider = $c.Provider; package = $c.Package
            profiles = @($c.Profiles); prompt = $c.Prompt; category = $c.Category
            requires = @($c.Requires); homepage = $c.Homepage
            verify = $c.Verify; notes = $c.Notes
            installed = $InstalledStatus[$c.Id] -eq 'installed'
            installedStatus = $(if ($InstalledStatus.ContainsKey($c.Id)) { $InstalledStatus[$c.Id] } else { 'unknown' })
            platforms = @($platforms[$c.Id])
        }
    }
    [ordered]@{
        platform  = 'Windows'
        system    = [ordered]@{
            host           = $env:COMPUTERNAME
            'operating system' = $SystemInfo.OsName
            architecture   = $SystemInfo.Arch
            model          = "$($SystemInfo.Manufacturer) $($SystemInfo.Model)"
            cpu            = $SystemInfo.CpuName
            cores          = "$($SystemInfo.CpuCores)"
            memory         = "$($SystemInfo.RamGB) GB"
            'free disk'    = "$($SystemInfo.FreeDiskGB) GB"
            microphone     = $SystemInfo.Microphone
            user           = $SystemInfo.UserName
            elevated       = $(if ($SystemInfo.IsAdmin) { 'yes' } else { 'no' })
            environment    = $(if ($SystemInfo.IsVirtual) { 'virtual machine' } else { 'bare metal' })
            'installed applications' = if ($installedNames.Count -gt 0) {
                "✓ " + ($installedNames -join ', ') + " ($($installedNames.Count) detected)"
            } else {
                'none detected'
            }
        }
        wsl = [ordered]@{
            # Windows is the WSL *host*, never the guest - say so plainly rather
            # than leaving the field ambiguous.
            isWsl     = $false
            available = [bool]$SystemInfo.HasWsl
            version   = ''
            distro    = ''
        }
        suggested = Get-AutoOSSuggestedProfile -SystemInfo $SystemInfo
        profiles  = $Catalog.profiles
        prompts   = $Catalog.prompts
        components = @($components)
        providers = @(Get-AutoOSProviderStatus -RepoRoot (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
    }
}

function Start-AutoOSInstallJob {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string[]]$Ids,
        [hashtable]$Answers = @{},
        [bool]$DryRun = $true
    )
    $script:Log.Clear()
    $script:RunInfo.Running = $true
    $script:RunInfo.Done    = 0
    $script:RunInfo.Total   = $Ids.Count
    $script:RunInfo.Summary = ''
    $script:RunInfo.Current = $null

    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $RepoRoot 'setup.ps1'),
                '-Only', ($Ids -join ','), '-Yes', '-NoColor')
    if ($DryRun) { $psArgs += '-DryRun' }

    [void]$script:Log.Add(@{ level = 'step'; text = '$ powershell ' + ($psArgs -join ' ') })

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = (Get-Command powershell).Source
    $psi.Arguments = ($psArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory      = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    # Answers belong to this child only: retaining them on the server would
    # silently reuse an earlier run's inputs when the next form leaves them blank.
    $psi.EnvironmentVariables['AUTOOS_NO_COLOR'] = '1'
    $psi.EnvironmentVariables['AUTOOS_PROGRESS_EVENTS'] = '1'
    foreach ($k in $Answers.Keys) {
        if ($Answers[$k]) {
            $psi.EnvironmentVariables['AUTOOS_ANSWER_' + ($k.ToUpper() -replace '-', '_')] = [string]$Answers[$k]
        }
    }

    try { $script:Proc = [System.Diagnostics.Process]::Start($psi) }
    catch {
        $script:RunInfo.Running = $false
        $script:RunInfo.Summary = 'could not start installer'
        throw
    }

    # One pending read per stream. Both streams must be read as they fill: an
    # installer that writes enough to stderr while nobody drains it blocks on a
    # full pipe buffer and the run hangs with no output and no error.
    $script:OutTask = $script:Proc.StandardOutput.ReadLineAsync()
    $script:ErrTask = $script:Proc.StandardError.ReadLineAsync()
}

function Get-AutoOSServeUsbCatalog {
    <#
      .SYNOPSIS
        Task 11b: mirror of lib/linux/serve.py's usb_images_response.
      .DESCRIPTION
        Images are served unfiltered - the dialog itself narrows by kind
        and writeMode. Engines are filtered to this machine's platform/arch
        AND to non-interactive ones, so rufus (interactive: true, GUI-only)
        never reaches the browser on either platform - it needs a human at
        a GUI, which the browser UI cannot drive (task-11 brief).

        Module-level and exported so a test can call it directly, the same
        reason serve.py's version is a plain function rather than inlined
        in do_GET.
      #>
    param([Parameter(Mandatory)][string]$RepoRoot)
    $imagesPath = Join-Path $RepoRoot 'catalog\images.json'
    $images = @((Get-Content -LiteralPath $imagesPath -Raw -Encoding UTF8 | ConvertFrom-Json).images)
    $engines = @(Get-AutoOSUsbEngineList -Platform (Get-AutoOSUsbCurrentOs) -Arch (Get-AutoOSUsbCurrentArch) |
                 Where-Object { -not $_.interactive })
    [ordered]@{ images = $images; engines = $engines }
}

function Get-AutoOSServeUsbDevices {
    <#
      .SYNOPSIS
        Task 11b: mirror of lib/linux/serve.py's usb_devices_response.
      .DESCRIPTION
        Elevation is surfaced here, not discovered at write time, so the
        browser can disable the create button with a reason instead of
        offering an action that would fail on "Access is denied".
      #>
    [CmdletBinding()]
    param()
    $devices = @(Get-AutoOSUsbDevice | ForEach-Object {
        [ordered]@{
            device = $_.DeviceId; model = $(if ($_.Model) { $_.Model } else { '(unknown model)' })
            size = $_.SizeBytes; removable = [bool]$_.IsRemovable; bus = $_.Bus
        }
    })
    $elev = Test-AutoOSElevated
    [ordered]@{ devices = $devices; elevated = $elev.IsElevated; reason = $elev.Reason }
}

function Get-AutoOSServeUsbCreateResult {
    <#
      .SYNOPSIS
        Validates a /api/usb/create request; never writes to a device.
        Returns [pscustomobject]@{ Code; Payload } - Code 202 means the
        caller should start Start-AutoOSUsbCreateJob.
      .DESCRIPTION
        Task 11b: mirror of lib/linux/serve.py's usb_create_response.
        Module-level and exported (not inlined in the route) for the exact
        reason serve.py gives for its own version being module-level: a
        test can call this directly with $env:AUTOOS_FAKE_DISKS and
        $script:RunInfo.Running set, without starting a real HttpListener.

        The device-safety check (Assert-AutoOSUsbSafe) runs before
        anything else, mirroring the guard's own "most dangerous mistake
        first" ordering: a bad device is refused before this function ever
        looks at whether the rest of the request is even well-formed. The
        run-lock check happens last, against $script:RunInfo.Running - the
        exact same in-memory state /api/install uses, so a write while an
        install runs, or a second write, both return 409.
      #>
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Body)

    $device = if ($Body.PSObject.Properties.Name -contains 'device') { [string]$Body.device } else { '' }
    if (-not $device) {
        return [pscustomobject]@{ Code = 400; Payload = @{ error = 'a target device is required' } }
    }

    $image = if ($Body.PSObject.Properties.Name -contains 'image') { [string]$Body.image } else { '' }
    $engine = if ($Body.PSObject.Properties.Name -contains 'engine') { [string]$Body.engine } else { '' }
    $guardMode = if ($engine -eq 'uefi-copy') { 'MountedFat32Writable' } else { 'Unmounted' }

    # Finding F12 (mirror of lib/linux/serve.py's usb_create_response): this
    # used to call Assert-AutoOSUsbSafe with no $env:AUTOOS_IMAGE_BYTES at
    # all, so its size check compared the device against 0 and a device
    # smaller than the image still got a 202. Looked up the same way
    # New-AutoOSUsbPlan already does for the CLI path - 0 (unknown image,
    # unreadable catalog, or no sizeGb on this entry) intentionally
    # reproduces the old "never a refusal on size" behaviour rather than
    # inventing a limit this catalog entry never declared.
    $imageBytes = 0
    try {
        $catalogImage = Get-AutoOSUsbImage -Id $image
        if ($catalogImage -and $catalogImage.sizeGb) {
            $imageBytes = [int64][math]::Round([double]$catalogImage.sizeGb * 1000000000)
        }
    } catch {
        $imageBytes = 0
    }

    $prevImageBytes = $env:AUTOOS_IMAGE_BYTES
    $env:AUTOOS_IMAGE_BYTES = [string]$imageBytes
    try {
        Assert-AutoOSUsbSafe -DeviceId $device -Mode $guardMode | Out-Null
    } catch {
        return [pscustomobject]@{ Code = 400; Payload = @{ error = $_.Exception.Message } }
    } finally {
        if ($null -eq $prevImageBytes) { Remove-Item Env:\AUTOOS_IMAGE_BYTES -ErrorAction SilentlyContinue }
        else { $env:AUTOOS_IMAGE_BYTES = $prevImageBytes }
    }

    $kind = if (($Body.PSObject.Properties.Name -contains 'kind') -and $Body.kind) { [string]$Body.kind } else { 'installer' }
    if (-not $image -or -not $engine) {
        return [pscustomobject]@{ Code = 400; Payload = @{ error = 'image and engine are both required' } }
    }

    if ($script:RunInfo.Running) {
        return [pscustomobject]@{ Code = 409; Payload = @{ error = 'a run is already in progress' } }
    }

    [pscustomobject]@{
        Code = 202
        Payload = @{ ok = $true; image = $image; kind = $kind; engine = $engine; device = $device }
    }
}

function Start-AutoOSUsbCreateJob {
    <#
      .SYNOPSIS
        Runs the same plan-then-execute CLI path setup.ps1 -CreateUsb uses,
        as a background job the server loop streams like an install.
      .DESCRIPTION
        Task 11b: the exact Windows mirror of lib/linux/serve.py's
        run_usb_create. -DryRun is always forced here, never taken from the
        request - the browser path must never trigger a real write, and
        this is belt-and-suspenders on top of New-AutoOSUsbPlan's own
        checks rather than the only thing standing between this endpoint
        and a real write.

        Shares $script:Log / $script:RunInfo / $script:Proc with
        Start-AutoOSInstallJob on purpose: an install and a USB create must
        never run side by side, and joining the same in-memory run state is
        what makes the caller's "a run is already in progress" (409) check
        cover both.
      #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Image,
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Engine,
        [Parameter(Mandatory)][string]$Device
    )
    $script:Log.Clear()
    $script:RunInfo.Running = $true
    $script:RunInfo.Done    = 0
    $script:RunInfo.Total   = 1
    $script:RunInfo.Summary = ''
    $script:RunInfo.Current = $null

    # Finding F11' (mirror of lib/linux/serve.py's run_usb_create):
    # -WipeTargetDisk used to be passed here too even though this is only
    # ever a browser PREVIEW - -DryRun already neutralises it, but
    # acknowledging "contents are lost" from a preview is gratuitous, and it
    # left exactly one missing flag between a preview and a real wipe.
    # Dropped, and AUTOOS_DRY_RUN=1 is now also set directly in the child
    # process's environment (belt-and-suspenders alongside -DryRun on the
    # command line already) so nothing about this call depends on a single
    # flag surviving unchanged.
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $RepoRoot 'setup.ps1'),
                '-CreateUsb', '-Image', $Image, '-Kind', $Kind, '-Engine', $Engine,
                '-UsbDevice', $Device, '-DryRun', '-Yes', '-NoColor')

    [void]$script:Log.Add(@{ level = 'step'; text = '$ powershell ' + ($psArgs -join ' ') })

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = (Get-Command powershell).Source
    $psi.Arguments = ($psArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory       = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding  = New-Object Text.UTF8Encoding($false)
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.EnvironmentVariables['AUTOOS_NO_COLOR'] = '1'
    $psi.EnvironmentVariables['AUTOOS_DRY_RUN'] = '1'

    try { $script:Proc = [System.Diagnostics.Process]::Start($psi) }
    catch {
        $script:RunInfo.Running = $false
        $script:RunInfo.Summary = 'could not start usb create'
        throw
    }

    # Same async-read pattern as Start-AutoOSInstallJob (see
    # Update-AutoOSInstallLog for why a reader thread cannot do this).
    $script:OutTask = $script:Proc.StandardOutput.ReadLineAsync()
    $script:ErrTask = $script:Proc.StandardError.ReadLineAsync()
}

function Get-AutoOSServeInstalledStatus {
    param([psobject]$SystemInfo, [psobject]$Catalog)
    $components = @(Get-AutoOSAvailableComponents -Catalog $Catalog -SystemInfo $SystemInfo)
    Set-AutoOSInstalledStatus -Components $components -Refresh
    $status = @{}
    foreach ($component in $components) { $status[$component.Id] = $component.InstalledStatus }
    return $status
}

function Set-AutoOSServeProgress {
    param([string]$Line)
    $prefix = '@@AUTOOS_PROGRESS '
    if (-not $Line.StartsWith($prefix)) { return $false }
    try {
        $progressRecord = $Line.Substring($prefix.Length) | ConvertFrom-Json
        if ($progressRecord.total -lt 0 -or $progressRecord.done -lt 0 -or $progressRecord.done -gt $progressRecord.total) { return $true }
        $script:RunInfo.Done = [int]$progressRecord.done
        $script:RunInfo.Total = [int]$progressRecord.total
        $script:RunInfo.Current = $progressRecord
    } catch {
        # A malformed progress record must never stop draining either pipe.
        return $true
    }
    return $true
}

function Update-AutoOSInstallLog {
    <#
    .SYNOPSIS
        Move whatever the installer has printed so far into the served log.

    .DESCRIPTION
        Called on every tick of the server loop, idle ones included, and never
        blocks - so output reaches the browser while it happens and the listener
        stays answerable throughout a run.

        This is deliberately not a reader thread. A PowerShell scriptblock has no
        runspace on a raw .NET thread: it does not fail the read, it terminates
        the whole process with "There is no Runspace available to run scripts in
        this thread". Draining async reads from the one thread that does have a
        runspace is the version that works.
    #>
    if ($null -eq $script:Proc) { return }

    foreach ($stream in @('Out', 'Err')) {
        while ($true) {
            $task = if ($stream -eq 'Out') { $script:OutTask } else { $script:ErrTask }
            if (($null -eq $task) -or (-not $task.IsCompleted)) { break }

            # A faulted read means the stream died under us; treat it as its end
            # rather than letting one broken pipe take the server down.
            $line = $null
            try { $line = $task.Result } catch { $line = $null }

            if ($null -eq $line) {
                if ($stream -eq 'Out') { $script:OutTask = $null } else { $script:ErrTask = $null }
                break
            }

            if (-not (Set-AutoOSServeProgress -Line $line)) {
                $level = if ($stream -eq 'Err') { 'err' } else { Get-AutoOSLineLevel $line }
                [void]$script:Log.Add(@{ level = $level; text = $line })
            }

            if ($stream -eq 'Out') { $script:OutTask = $script:Proc.StandardOutput.ReadLineAsync() }
            else                   { $script:ErrTask = $script:Proc.StandardError.ReadLineAsync() }
        }
    }

    # Only call it finished once both streams have ended AND the process has
    # gone - an exit code read too early is not the one the user ran for.
    if (($null -ne $script:OutTask) -or ($null -ne $script:ErrTask)) { return }
    if (-not $script:Proc.HasExited) { return }

    $exit = $script:Proc.ExitCode
    $script:RunInfo.Running = $false
    $script:RunInfo.Summary = "finished (exit $exit)"
    [void]$script:Log.Add(@{
        level = $(if ($exit -eq 0) { 'ok' } else { 'err' })
        text  = "--- exit code $exit ---"
    })
    $script:Proc.Dispose()
    $script:Proc = $null
}

function Get-AutoOSExampleBlock {
    <#
      .SYNOPSIS
        One top-level block of autoos.config.example.json, or an empty object.
      .DESCRIPTION
        The example is the single home for the shipped defaults, so the server
        reads them from there rather than restating them.
    #>
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$RepoRoot)

    $path = Join-Path $RepoRoot 'autoos.config.example.json'
    if (-not (Test-Path -LiteralPath $path)) { return @{} }
    try {
        $block = (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json).$Name
        if ($block) { return $block }
    } catch { $null = $_ }
    @{}
}

function Get-AutoOSDetectedAnswers {
    <#
      .SYNOPSIS
        Answers that can be read off the machine instead of being asked for.
      .DESCRIPTION
        A prefilled field is only an improvement when the value is real; a
        plausible-looking placeholder is worse than an empty box, because it
        reads as answered and gets saved as though it had been.
    #>
    $answers = @{}
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $answers }
    foreach ($pair in @(@{ Key = 'git_user_name'; Setting = 'user.name' },
                        @{ Key = 'git_user_email'; Setting = 'user.email' })) {
        try {
            $value = (& git config --global $pair.Setting 2>$null | Select-Object -First 1)
            if ($value) { $answers[$pair.Key] = [string]$value }
        } catch { $null = $_ }
    }
    $answers
}

function Save-AutoOSWebConfig {
    <#
      .SYNOPSIS
        Merge $Updates into the JSON config at -Path; back up and rewrite only
        when the merged result actually differs from the file.
      .DESCRIPTION
        The body of POST /api/config, extracted from the listener scriptblock
        so a test can drive it without starting the real server. Returns
        @{ ok = $true; saved = <path> }, plus unchanged = $true when nothing
        changed. The equality check is canonical JSON (-ceq), not plain
        ConvertTo-Json text: Add-Member -Force moves an existing property to
        the end, so a merge that changed nothing still reorders keys, and an
        order-sensitive compare would treat the no-op save as a change and
        churn a backup + rewrite of identical bytes.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][pscustomobject]$Updates
    )
    $exists = Test-Path -LiteralPath $Path
    $merged = if ($exists) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    foreach ($property in $Updates.PSObject.Properties) {
        if ($property.Name -eq 'answers' -and $merged.PSObject.Properties.Name -contains 'answers') {
            foreach ($answer in $property.Value.PSObject.Properties) { $merged.answers | Add-Member -NotePropertyName $answer.Name -NotePropertyValue $answer.Value -Force }
        } else { $merged | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value -Force }
    }
    if ($exists -and ((ConvertTo-AutoOSCanonicalJson $merged) -ceq (ConvertTo-AutoOSCanonicalJson (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)))) {
        return @{ ok = $true; saved = $Path; unchanged = $true }
    }
    if ($exists) { $null = Copy-AutoOSBackup -Path $Path }
    $tmpPath = "$Path.tmp"
    [System.IO.File]::WriteAllText($tmpPath, ($merged | ConvertTo-Json -Depth 100), [System.Text.Encoding]::UTF8)
    Move-Item -Path $tmpPath -Destination $Path -Force
    @{ ok = $true; saved = $Path }
}

# -- Logins and keys: one allowlist for the status card AND write-only setting
#    from the page. `id` is a top-level key in configuration/api-keys.yml (a
#    git-ignored file); `group` is how the page files the row; `apply` is the
#    command that re-reads the file after a value changes (empty = nothing to
#    re-apply from this file). A value never leaves the server: the payloads
#    carry only presence, and POST /api/secrets cannot read one back out.
$script:SecretKeys = [ordered]@{
    'groq'                  = @{ name = 'Groq';                                    group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'google_ai_studio'      = @{ name = 'Google AI Studio (Gemini)';               group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'mistral'               = @{ name = 'Mistral';                                 group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'cloudflare_workers_ai' = @{ name = 'Cloudflare Workers AI';                  group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'cohere'                = @{ name = 'Cohere';                                  group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'hugging_face'          = @{ name = 'Hugging Face';                            group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'cerebras'              = @{ name = 'Cerebras';                                group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'sambanova'             = @{ name = 'SambaNova';                               group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'deepseek'              = @{ name = 'DeepSeek';                                group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'meta'                  = @{ name = 'Meta Model API';                          group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'openrouter'            = @{ name = 'OpenRouter';                              group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'zen'                   = @{ name = 'OpenCode Zen';                            group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'cheapinference'        = @{ name = 'Cheaper Inference (paid partner)';        group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'free_ai'               = @{ name = 'Free.ai';                                 group = 'AI providers';    apply = '.\configuration\omniroute\apply.ps1' }
    'omniroute'             = @{ name = 'OmniRoute client key';                    group = 'Router';          apply = '.\configuration\omniroute\apply.ps1' }
    'omniroute_management'  = @{ name = 'OmniRoute management token';              group = 'Router';          apply = '.\configuration\omniroute\apply.ps1' }
    'opencode_password'     = @{ name = 'opencode serve password (user opencode)'; group = 'Local services';  apply = '.\configuration\start-stack.ps1 -App opencode-serve' }
    'qoder_pat'             = @{ name = 'Qoder personal access token';             group = 'Coding agents';   apply = '' }
}

function Get-AutoOSKeysFilePath {
    <#
      .SYNOPSIS
        The api-keys.yml the page reads and writes. Overridable with
        AUTOOS_KEYS_FILE so tests point at a throwaway file.
    #>
    param([string]$RepoRoot)
    if ($env:AUTOOS_KEYS_FILE) { return $env:AUTOOS_KEYS_FILE }
    Join-Path $RepoRoot 'configuration\api-keys.yml'
}

function Get-AutoOSConfiguredIds {
    <#
      .SYNOPSIS
        The allowlisted key ids that hold a real value in the keys file.
        REPLACE_WITH_* counts as not configured.
    #>
    param([string]$RepoRoot)
    $file = Get-AutoOSKeysFilePath -RepoRoot $RepoRoot
    if (-not (Test-Path $file)) { return [System.Collections.Generic.HashSet[string]]::new() }
    $have = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($line in (Get-Content $file -Encoding utf8)) {
        if ($line.Length -eq 0 -or $line[0] -eq ' ' -or $line[0] -eq "`t") { continue }
        if ($line.StartsWith('#') -or -not $line.Contains(':')) { continue }
        $key = ($line -split ':', 2)[0].Trim()
        $val = ($line -split ':', 2)[1].Trim().Trim(@('"', "'")).Trim()
        if ($val -and -not $val.ToUpperInvariant().StartsWith('REPLACE_WITH_')) {
            [void]$have.Add($key)
        }
    }
    $have
}

function Split-AutoOSTrailingComment {
    <#
      .SYNOPSIS
        Split " 'value' # comment" into @{ Value; Comment }.
        A value may itself contain " #" inside its quotes, so a naive split
        would mistake that for the start of a comment.
    #>
    param([string]$After)
    $inQuote = $false
    $i = 0
    $n = $After.Length
    while ($i -lt $n) {
        $ch = $After[$i]
        if ($ch -eq "'") {
            if ($inQuote -and ($i + 1) -lt $n -and $After[$i + 1] -eq "'") {
                $i += 2
                continue
            }
            $inQuote = -not $inQuote
        } elseif ($ch -eq "#" -and -not $inQuote -and $i -gt 0 -and ($After[$i - 1] -eq " " -or $After[$i - 1] -eq "`t")) {
            return @{ Value = $After.Substring(0, $i); Comment = $After.Substring($i) }
        }
        $i++
    }
    @{ Value = $After; Comment = '' }
}

function Get-AutoOSYamlScalar {
    <#
      .SYNOPSIS
        The unquoted value of a YAML scalar as this server writes it.
    #>
    param([string]$Raw)
    $Raw.Trim().Trim(@('"', "'")).Trim()
}

function Get-AutoOSSecretValueError {
    <#
      .SYNOPSIS
        Why a value may not be written, or $null when it is acceptable.
        The returned messages never include the value itself.
    #>
    param($Value)
    if ($Value -isnot [string]) { return 'value must be a string' }
    if ($Value.Length -lt 1 -or $Value.Length -gt 4096) { return 'value must be 1..4096 characters' }
    if ($Value.IndexOfAny([char[]]@("`r", "`n", [char]0)) -ge 0) { return 'value must not contain a newline or NUL' }
    if ($Value -ne $Value.Trim()) { return 'value must not start or end with whitespace' }
    if ($Value.ToUpperInvariant().StartsWith('REPLACE_WITH_')) { return 'value still looks like the placeholder it replaces' }
    if ($Value.IndexOfAny([char[]]@("'", '"')) -ge 0) { return 'value must not contain a quote character' }
    $null
}

function Get-AutoOSGitIgnoreRefusal {
    <#
      .SYNOPSIS
        The 409 reason when the file is in a git work tree and not git-ignored.
        0 = ignored (safe), 1 = in a work tree but NOT ignored (refuse), 128 =
        not a work tree at all (safe). Run from the file's own directory so git
        discovers the work tree the file belongs to.
    #>
    param([string]$Path)
    $dir = Split-Path -Parent $Path
    if (-not $dir -or -not (Test-Path $dir)) { return $null }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
    $exitCode = 128
    $prevDir = $null
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $prevDir = Get-Location
        Set-Location $dir
        $null = & git check-ignore -q -- $Path 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
    } finally {
        if ($prevDir) { Set-Location $prevDir }
        $ErrorActionPreference = $prevEAP
    }
    if ($exitCode -eq 1) {
        return "$Path is not git-ignored; refusing to write a secret there"
    }
    $null
}

function Split-AutoOSLinesWithEndings {
    <#
      .SYNOPSIS
        Split text into lines, preserving each line's ending (CRLF, LF, or CR).
    #>
    param([string]$Text)
    if (-not $Text) { return @() }
    $parts = [regex]::Split($Text, '(\r\n|\r|\n)')
    $lines = New-Object System.Collections.ArrayList
    for ($i = 0; $i -lt $parts.Length; $i += 2) {
        $ending = if ($i + 1 -lt $parts.Length) { $parts[$i + 1] } else { '' }
        [void]$lines.Add(@{ Text = $parts[$i]; Ending = $ending })
    }
    ,$lines
}

function Set-AutoOSKeyValue {
    <#
      .SYNOPSIS
        Replace or append one key: 'value' line, returning the response payload.
        Write-only by construction: the value goes into the file and is never
        returned, logged or echoed. An identical value is a no-op with no backup.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$KeyId,
        [Parameter(Mandatory)][string]$Value
    )
    $file = Get-AutoOSKeysFilePath -RepoRoot $RepoRoot
    $bytes = $null
    $text = $null
    if (Test-Path $file) {
        $bytes = [System.IO.File]::ReadAllBytes($file)
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    }

    $lines = if ($text) { Split-AutoOSLinesWithEndings -Text $text } else { New-Object System.Collections.ArrayList }

    $found = $false
    $unchanged = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $core = $lines[$i].Text
        if ($core.Length -eq 0 -or $core[0] -eq ' ' -or $core[0] -eq "`t") { continue }
        if ($core.StartsWith('#') -or -not $core.Contains(':')) { continue }
        $colonIdx = $core.IndexOf(':')
        $head = $core.Substring(0, $colonIdx)
        $after = $core.Substring($colonIdx + 1)
        if ($head.Trim() -cne $KeyId) { continue }

        $found = $true
        $split = Split-AutoOSTrailingComment -After $after
        $currentValue = Get-AutoOSYamlScalar -Raw $split.Value
        if ($currentValue -ceq $Value) {
            $unchanged = $true
            break
        }

        $quotedValue = "'" + $Value + "'"
        $newCore = $head.TrimEnd() + ': ' + $quotedValue
        if ($split.Comment) { $newCore += ' ' + $split.Comment }
        $lines[$i] = @{ Text = $newCore; Ending = $lines[$i].Ending }
        break
    }

    if ($unchanged) {
        return @{ ok = $true; id = $KeyId; unchanged = $true }
    }

    $newText = ''
    if ($found) {
        foreach ($line in $lines) { $newText += $line.Text + $line.Ending }
    } else {
        $baseText = if ($text) { $text } else { '' }
        if ($baseText -and -not $baseText.EndsWith("`n")) { $baseText += "`n" }
        $newText = $baseText + $KeyId + ": '" + $Value + "'`n"
    }

    $dir = Split-Path -Parent $file
    $baseName = [System.IO.Path]::GetFileName($file)
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newText)
    $tmp = $null
    try {
        if ($bytes) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupBase = Join-Path $dir "$baseName.autoos-backup-$stamp"
            $backup = $backupBase
            $n = 0
            while (Test-Path $backup) {
                $n++
                $backup = "$backupBase-$n"
            }
            [System.IO.File]::WriteAllBytes($backup, $bytes)
            try {
                $srcAcl = Get-Acl -LiteralPath $file
                Set-Acl -LiteralPath $backup -AclObject $srcAcl -ErrorAction Stop
            } catch { }
        }

        $rand = [Guid]::NewGuid().ToString('N').Substring(0,8)
        $tmp = Join-Path $dir "$baseName.autoos-tmp-$PID-$rand"
        $fs = [System.IO.File]::Open($tmp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $fs.Write($newBytes, 0, $newBytes.Length)
        } finally {
            $fs.Dispose()
        }
        if ($bytes) {
            try {
                $srcAcl = Get-Acl -LiteralPath $file
                Set-Acl -LiteralPath $tmp -AclObject $srcAcl -ErrorAction Stop
            } catch { }
        }
        Move-Item -LiteralPath $tmp -Destination $file -Force
        $tmp = $null
    } finally {
        if ($tmp -and (Test-Path $tmp)) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }

    @{ ok = $true; id = $KeyId; configured = $true; apply = $script:SecretKeys[$KeyId].apply }
}

function Get-AutoOSSecretsPayload {
    <#
      .SYNOPSIS
        GET /api/secrets body: set/missing per key, an apply hint, no values.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [bool]$ClientIsLoopback
    )
    $remoteOk = $ClientIsLoopback -or ($env:AUTOOS_SERVE_REMOTE_SECRETS -eq '1')
    $file = Get-AutoOSKeysFilePath -RepoRoot $RepoRoot
    $refusal = Get-AutoOSGitIgnoreRefusal -Path $file
    $writable = (-not $refusal) -and $remoteOk
    $have = Get-AutoOSConfiguredIds -RepoRoot $RepoRoot
    $secrets = foreach ($id in $script:SecretKeys.Keys) {
        [ordered]@{
            id = $id
            name = $script:SecretKeys[$id].name
            group = $script:SecretKeys[$id].group
            configured = $have.Contains($id)
            apply = $script:SecretKeys[$id].apply
        }
    }
    [ordered]@{
        secrets  = @($secrets)
        file     = 'configuration/api-keys.yml'
        writable = $writable
    }
}

function Get-AutoOSSecretPostResult {
    <#
      .SYNOPSIS
        Validate and apply a POST /api/secrets body. Returns @{ Code; Payload }.
        The value is never written to host output, the log, or a response.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)]$Body,
        [bool]$ClientIsLoopback
    )
    if (-not $Body -or $Body -isnot [pscustomobject]) {
        return @{ Code = 400; Payload = @{ error = 'payload must be a JSON object' } }
    }

    $keyId = $null
    if ($Body.PSObject.Properties.Name -contains 'id') { $keyId = [string]$Body.id }
    if (-not $keyId -or -not $script:SecretKeys.Contains($keyId)) {
        return @{ Code = 400; Payload = @{ error = 'unknown key id'; allowed = @($script:SecretKeys.Keys) } }
    }

    if (-not ($Body.PSObject.Properties.Name -contains 'value')) {
        return @{ Code = 400; Payload = @{ error = 'value must be a string' } }
    }
    $value = $Body.value
    if ($value -isnot [string]) {
        return @{ Code = 400; Payload = @{ error = 'value must be a string' } }
    }

    $problem = Get-AutoOSSecretValueError -Value $value
    if ($problem) {
        return @{ Code = 400; Payload = @{ error = $problem } }
    }

    $remoteOk = $ClientIsLoopback -or ($env:AUTOOS_SERVE_REMOTE_SECRETS -eq '1')
    if (-not $remoteOk) {
        return @{ Code = 403; Payload = @{ error = 'secrets can be set only from this machine (loopback); use an SSH tunnel' } }
    }

    $file = Get-AutoOSKeysFilePath -RepoRoot $RepoRoot
    $refusal = Get-AutoOSGitIgnoreRefusal -Path $file
    if ($refusal) {
        return @{ Code = 409; Payload = @{ error = $refusal } }
    }

    try {
        $result = Set-AutoOSKeyValue -RepoRoot $RepoRoot -KeyId $keyId -Value $value
        @{ Code = 200; Payload = $result }
    } catch {
        $detail = $_.Exception.Message
        if ($value) { $detail = $detail.Replace($value, '***') }
        @{ Code = 500; Payload = @{ error = "failed to save secret: $detail" } }
    }
}

function Start-AutoOSServer {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [int]$Port = 8777,
        [string]$Bind = '127.0.0.1',
        [Parameter(Mandatory)][psobject]$SystemInfo,
        [Parameter(Mandatory)][psobject]$Catalog,
        # Starting the server with -DryRun LOCKS the whole session to dry-run:
        # the browser can then only preview, never install. A safe way to hand
        # someone the URL without handing them the ability to change the box.
        [switch]$DryRun
    )
    $forceDryRun = [bool]$DryRun

    $indexPath = Join-Path $RepoRoot 'web\index.html'
    if (-not (Test-Path $indexPath)) {
        Write-AutoOSLine 'web\index.html is missing - cannot start the browser UI.' -Level error
        return
    }

    $token = [Convert]::ToBase64String([Guid]::NewGuid().ToByteArray()).TrimEnd('=').Replace('/', '_').Replace('+', '-')
    $prefixHost = if ($Bind -eq '127.0.0.1') { 'localhost' } else { '+' }

    # A busy port is not a reason to make someone re-run the whole detect pass
    # with -Port. Walk forward until one is free and say which one won - the URL
    # printed below is the only one that matters, so a moved port is a note, not
    # an error. Access-denied is different: no port on the machine will help, so
    # stop on the first one rather than rattling twenty doors that are all shut.
    $requestedPort = $Port
    $listener      = $null
    $lastError     = ''
    foreach ($candidatePort in $requestedPort..($requestedPort + 19)) {
        $candidate = New-Object System.Net.HttpListener
        $candidate.Prefixes.Add("http://${prefixHost}:$candidatePort/")
        try {
            $candidate.Start()
            $listener = $candidate
            $Port     = $candidatePort
            break
        } catch {
            $lastError = $_.Exception.Message
            $candidate.Close()
            # 5 is ERROR_ACCESS_DENIED - a missing URL reservation, not a clash.
            if (($_.Exception -is [System.Net.HttpListenerException]) -and
                ($_.Exception.ErrorCode -eq 5)) { break }
        }
    }

    if ($null -eq $listener) {
        Write-AutoOSLine "Could not listen on port $requestedPort or the 19 ports after it: $lastError" -Level error
        if ($prefixHost -eq '+') {
            Write-AutoOSLine 'Binding beyond localhost needs an elevated shell, or a URL reservation:' -Level muted
            Write-AutoOSLine "    netsh http add urlacl url=http://+:$requestedPort/ user=$env:USERNAME" -Level muted
        }
        return
    }

    if ($Port -ne $requestedPort) {
        Write-AutoOSLine "Port $requestedPort was already in use - serving on $Port instead." -Level warn
    }

    $url = "http://$(if ($Bind -eq '127.0.0.1') { 'localhost' } else { $Bind }):$Port/?token=$token"
    Write-AutoOSLine ''
    Write-AutoOSLine '  AutoOS browser UI' -Level head
    Write-AutoOSLine ('  ' + ('-' * 58)) -Level muted
    Write-AutoOSLine "  $url"
    Write-AutoOSLine ('  ' + ('-' * 58)) -Level muted
    if ($Bind -ne '127.0.0.1') {
        Write-AutoOSLine 'WARNING: bound beyond loopback. Anyone who can reach this port AND' -Level warn
        Write-AutoOSLine '         has the token above can install software on this machine.' -Level warn
    }
    if ($forceDryRun) {
        Write-AutoOSLine '  Session is LOCKED to dry run - the browser cannot install anything.' -Level warn
    }
    Write-AutoOSLine '  The token changes every run. Ctrl-C to stop.' -Level muted
    Write-AutoOSLine ''

    try {
        Start-Process $url | Out-Null
    } catch {
        # Headless boxes have no default browser; the URL is printed above.
        Write-AutoOSLine 'Could not open a browser here - use the URL above.' -Level muted
    }

    # HttpListener.GetContext() blocks inside native code, and PowerShell can only
    # act on Ctrl-C between statements - so the old blocking loop could not be
    # interrupted at all. GetContextAsync + a short Wait() hands control back
    # every 200 ms, which is what gives Ctrl-C somewhere to land.
    $pending = $null
    $installedStatus = Get-AutoOSServeInstalledStatus -SystemInfo $SystemInfo -Catalog $Catalog
    try {
    while ($listener.IsListening) {
        # Before the wait, so a run keeps streaming even with nobody asking.
        $wasRunning = $script:RunInfo.Running
        Update-AutoOSInstallLog
        if ($wasRunning -and -not $script:RunInfo.Running) {
            $installedStatus = Get-AutoOSServeInstalledStatus -SystemInfo $SystemInfo -Catalog $Catalog
        }
        if ($null -eq $pending) { $pending = $listener.GetContextAsync() }
        if (-not $pending.Wait(200)) { continue }   # timeout: loop, stay interruptible
        $ctx = $pending.Result
        $pending = $null
        $req = $ctx.Request
        $res = $ctx.Response
        $res.Headers.Add('Cache-Control', 'no-store')
        $res.Headers.Add('X-Content-Type-Options', 'nosniff')

        $send = {
            param($code, [byte[]]$bytes, $ctype)
            $res.StatusCode = $code
            $res.ContentType = $ctype
            $res.ContentLength64 = $bytes.Length
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
            $res.OutputStream.Close()
        }
        $json = {
            param($code, $obj)
            & $send $code ([Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Depth 8 -Compress))) 'application/json'
        }

        try {
            $path = $req.Url.AbsolutePath
            $given = $req.QueryString['token']
            $authed = ($given -eq $token)

            if ($path -eq '/' -or $path -eq '/index.html') {
                & $send 200 ([IO.File]::ReadAllBytes($indexPath)) 'text/html; charset=utf-8'
            }
            elseif (-not $authed) {
                & $json 403 @{ error = 'bad or missing token' }
            }
            elseif ($path -eq '/api/state') {
                & $json 200 (Get-AutoOSServeState -SystemInfo $SystemInfo -Catalog $Catalog -InstalledStatus $installedStatus)
            }
            elseif ($path -eq '/api/ping') {
                # Deliberately the cheapest thing this server does: the page
                # polls it every couple of seconds to notice the moment this
                # process goes away.
                & $json 200 @{ ok = $true; running = $script:RunInfo.Running }
            }
            elseif ($path -eq '/api/log') {
                $offset = 0
                [void][int]::TryParse($req.QueryString['offset'], [ref]$offset)
                $all = @($script:Log)
                $offset = [Math]::Max(0, [Math]::Min($offset, $all.Count))
                $lines = if ($offset -lt $all.Count) { $all[$offset..($all.Count - 1)] } else { @() }
                & $json 200 @{
                    lines = @($lines); offset = $offset + @($lines).Count
                    running = $script:RunInfo.Running; done = $script:RunInfo.Done
                    total = $script:RunInfo.Total; summary = $script:RunInfo.Summary
                    current = $script:RunInfo.Current
                }
            }
            elseif ($path -eq '/api/config' -and $req.HttpMethod -eq 'GET') {
                $cfgPath = Join-Path $RepoRoot 'autoos.config.json'
                if (Test-Path $cfgPath) {
                    $raw = Get-Content $cfgPath -Raw -Encoding UTF8
                    $data = $raw | ConvertFrom-Json
                    & $json 200 $data
                } else {
                    # No config yet. Seed it from the machine, never from the
                    # example file: its answers are illustrative ("Your Name",
                    # "you@example.com") and serving them puts fake identity in
                    # the form, where it looks answered and gets saved as real.
                    & $json 200 @{
                        version          = 1
                        profile          = 'workstation'
                        answers          = (Get-AutoOSDetectedAnswers)
                        claude_autostart = (Get-AutoOSExampleBlock -Name 'claude_autostart' -RepoRoot $RepoRoot)
                    }
                }
            }
            elseif ($path -eq '/api/config' -and $req.HttpMethod -eq 'POST') {
                $body = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                $cfgPath = Join-Path $RepoRoot 'autoos.config.json'
                $updates = $body | ConvertFrom-Json
                if ($updates -isnot [pscustomobject]) { throw 'Configuration must be a JSON object.' }
                & $json 200 (Save-AutoOSWebConfig -Path $cfgPath -Updates $updates)
            }
            elseif ($path -eq '/api/claude/sessions' -and $req.HttpMethod -eq 'GET') {
                # Reads the recorded state; a GET must not have side effects.
                Import-Module (Join-Path $PSScriptRoot 'AutoOS.ClaudeAutostart.psm1') -DisableNameChecking
                $state = Get-AutoOSClaudeState
                & $json 200 @{
                    sessions        = @($state.sessions)
                    captured_at_iso = $state.captured_at_iso
                    installed       = (Test-AutoOSClaudeAutostartInstalled)
                }
            }
            elseif ($path -eq '/api/claude/snapshot' -and $req.HttpMethod -eq 'POST') {
                Import-Module (Join-Path $PSScriptRoot 'AutoOS.ClaudeAutostart.psm1') -DisableNameChecking
                [void](Save-AutoOSClaudeSnapshot)
                $state = Get-AutoOSClaudeState
                & $json 200 @{
                    ok              = $true
                    sessions        = @($state.sessions)
                    captured_at_iso = $state.captured_at_iso
                    installed       = (Test-AutoOSClaudeAutostartInstalled)
                }
            }
            elseif ($path -eq '/api/images') {
                # Task 11b: mirror of lib/linux/serve.py's usb_images_response.
                # Get-AutoOSServeUsbCatalog is module-level and exported so a
                # test can call it directly (AGENTS.md §5 / the bash side's
                # own convention), the same reason serve.py's version is a
                # plain function rather than inlined in do_GET.
                try {
                    & $json 200 (Get-AutoOSServeUsbCatalog -RepoRoot $RepoRoot)
                } catch {
                    & $json 500 @{ error = $_.Exception.Message }
                }
            }
            elseif ($path -eq '/api/usb/devices') {
                # Task 11b: mirror of usb_devices_response - elevation is
                # surfaced here, not discovered at write time, so the
                # browser can disable the create button with a reason
                # instead of offering an action that would fail later.
                try {
                    & $json 200 (Get-AutoOSServeUsbDevices)
                } catch {
                    & $json 500 @{ error = $_.Exception.Message }
                }
            }
            elseif ($path -eq '/api/usb/create' -and $req.HttpMethod -eq 'POST') {
                # Task 11b: mirror of usb_create_response/do_POST - the
                # validation (device guard first, then image/engine, then
                # the run-lock) lives in Get-AutoOSServeUsbCreateResult so a
                # test can call it directly with $script:RunInfo.Running
                # pre-set, exactly like the bash side calls
                # usb_create_response() directly per its own docstring.
                $rawBody = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                $parseFailed = $false
                $body = $null
                try { $body = $rawBody | ConvertFrom-Json } catch { $parseFailed = $true }
                if ($parseFailed) {
                    & $json 400 @{ error = 'payload must be JSON' }
                } elseif ($body -isnot [pscustomobject]) {
                    & $json 400 @{ error = 'payload must be a JSON object' }
                } else {
                    $result = Get-AutoOSServeUsbCreateResult -Body $body
                    if ($result.Code -ne 202) {
                        & $json $result.Code $result.Payload
                    } else {
                        # Reserved by Start-AutoOSUsbCreateJob itself (sets
                        # $script:RunInfo.Running = $true before returning),
                        # the same instant the SAME in-memory state
                        # /api/install checks - so a second write, or a
                        # write during an install, sees Running already
                        # true and gets 409 from Get-AutoOSServeUsbCreateResult.
                        Start-AutoOSUsbCreateJob -RepoRoot $RepoRoot -Image $result.Payload.image `
                            -Kind $result.Payload.kind -Engine $result.Payload.engine -Device $result.Payload.device
                        & $json 202 @{ started = $true }
                    }
                }
            }
            elseif ($path -eq '/api/services' -and $req.HttpMethod -eq 'GET') {
                & $json 200 @{ services = @(Get-AutoOSServiceStatus) }
            }
            elseif ($path -eq '/api/services/action' -and $req.HttpMethod -eq 'POST') {
                $body = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                $payload = $null
                try { $payload = $body | ConvertFrom-Json } catch { $payload = $null }
                $result = Get-AutoOSServiceActionResult -Body $payload -ForceDryRun $forceDryRun
                if ($result.Code -eq 202 -and $script:RunInfo.Running) {
                    & $json 409 @{ error = 'a run is already in progress' }
                } elseif ($result.Code -eq 202) {
                    Start-AutoOSServiceActionJob -RepoRoot $RepoRoot -Action $result.Action
                    & $json 202 $result.Payload
                } else {
                    & $json $result.Code $result.Payload
                }
            }
            elseif ($path -eq '/api/secrets' -and $req.HttpMethod -eq 'GET') {
                try {
                    $isLoopback = [System.Net.IPAddress]::IsLoopback($req.RemoteEndPoint.Address)
                    & $json 200 (Get-AutoOSSecretsPayload -RepoRoot $RepoRoot -ClientIsLoopback $isLoopback)
                } catch {
                    & $json 500 @{ error = $_.Exception.Message }
                }
            }
            elseif ($path -eq '/api/secrets' -and $req.HttpMethod -eq 'POST') {
                $rawBody = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                $parseFailed = $false
                $body = $null
                try { $body = $rawBody | ConvertFrom-Json } catch { $parseFailed = $true }
                if ($parseFailed) {
                    & $json 400 @{ error = 'payload must be JSON' }
                } else {
                    $isLoopback = [System.Net.IPAddress]::IsLoopback($req.RemoteEndPoint.Address)
                    $result = Get-AutoOSSecretPostResult -RepoRoot $RepoRoot -Body $body -ClientIsLoopback $isLoopback
                    & $json $result.Code $result.Payload
                }
            }
            elseif ($path -eq '/api/install' -and $req.HttpMethod -eq 'POST') {
                if ($script:RunInfo.Running) {
                    & $json 409 @{ error = 'a run is already in progress' }
                } else {
                    $body = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                    $payload = $body | ConvertFrom-Json
                    $ids = @($payload.ids)
                    if ($ids.Count -eq 0) {
                        & $json 400 @{ error = 'no components selected' }
                    } else {
                        $answers = @{}
                        if ($payload.PSObject.Properties.Name -contains 'answers' -and $payload.answers) {
                            foreach ($p in $payload.answers.PSObject.Properties) { $answers[$p.Name] = $p.Value }
                        }
                        $dry = $true
                        if ($payload.PSObject.Properties.Name -contains 'dryRun') { $dry = [bool]$payload.dryRun }
                        if ($forceDryRun) { $dry = $true }
                        Start-AutoOSInstallJob -RepoRoot $RepoRoot -Ids $ids -Answers $answers -DryRun $dry
                        & $json 202 @{ started = $true }
                    }
                }
            }
            else {
                & $json 404 @{ error = 'not found' }
            }
        } catch {
            $reason = $_.Exception.Message
            Write-AutoOSLine "request failed: $reason" -Level warn
            try {
                & $json 500 @{ error = $reason }
            } catch {
                # The client hung up mid-response; nothing left to report to.
                Write-AutoOSLine 'client disconnected before the error could be sent' -Level muted
            }
        }
    }
    } finally {
        # Runs on Ctrl-C too, so the port is released instead of being held by a
        # half-dead listener until the shell exits.
        Write-AutoOSLine ''
        Write-AutoOSLine 'stopping the AutoOS browser UI...' -Level muted
        if ($listener.IsListening) { $listener.Stop() }
        $listener.Close()
        Write-AutoOSLine 'server stopped.' -Level ok
    }
}

Export-ModuleMember -Function Start-AutoOSServer, Get-AutoOSServeState, Get-AutoOSLineLevel, Start-AutoOSInstallJob, Update-AutoOSInstallLog, `
    Start-AutoOSUsbCreateJob, Get-AutoOSServeUsbCatalog, Get-AutoOSServeUsbDevices, Get-AutoOSServeUsbCreateResult, Get-AutoOSProviderStatus, `
    Get-AutoOSServiceStatus, Get-AutoOSServiceActionResult, Start-AutoOSServiceActionJob, Save-AutoOSWebConfig, `
    Get-AutoOSSecretsPayload, Get-AutoOSSecretPostResult
