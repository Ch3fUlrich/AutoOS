#Requires -Version 5.1
<#
  AutoOS.CostGate - register the daily cost gate on Windows.

  The gate itself (tools/run_budget.py day, the spawner-side check) is shared
  with Linux; this module only owns the Windows plumbing:

    %APPDATA%\autoos\daily-gate.conf        warn= / block= thresholds
    %LOCALAPPDATA%\autoos\daily-gate.json   the gate file (written by the refresh)
    %LOCALAPPDATA%\autoos\daily-gate.status one status line (written by the refresh)

  The "AutoOS cost gate" Scheduled Task runs lib\windows\cost-gate-export.ps1
  every 10 minutes and at logon; the wrapper exports the recent call-log rows
  through the omniroute CLI helper and hands them to cost-gate-refresh.py.
  Idempotent in the same sense as AutoOS.ClaudeAutostart: a second run that
  finds the task already current reports 'skipped' and changes nothing.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:CostGateTaskName = 'AutoOS cost gate'

# ─── configuration ───────────────────────────────────────────────────────────

function Get-AutoOSCostGateConfigPath {
    <#
      .SYNOPSIS
        The per-user config file: %APPDATA%\autoos\daily-gate.conf.
    #>
    Join-Path $env:APPDATA 'autoos\daily-gate.conf'
}

function Test-AutoOSCostGateThreshold {
    <#
      .SYNOPSIS
        A gate threshold must be a plain non-negative whole number.
    #>
    param([string]$Value)
    if ($null -eq $Value -or $Value -notmatch '^[0-9]+$') { return $false }
    [decimal]$Value -lt 1000000
}

function Write-AutoOSCostGateConfig {
    <#
      .SYNOPSIS
        Create the config file when absent; never overwrite an existing one.
      .OUTPUTS
        'created' or 'kept'; throws when the file cannot be written.
      .DESCRIPTION
        Two lines, `warn=` and `block=`, nothing else. An existing file is
        user data: editing thresholds by hand is the supported way to change
        them, so a second install must leave it byte-for-byte alone.
    #>
    param([int]$Warn, [int]$Block)

    $configPath = Get-AutoOSCostGateConfigPath
    $dir = Split-Path -Parent $configPath
    if (-not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Path $dir -Force)
    }
    if (Test-Path -LiteralPath $configPath) {
        Write-AutoOSCostGateLine 'daily gate config already exists - kept as is' -Level muted
        return 'kept'
    }
    $tmp = "$configPath.tmp"
    [System.IO.File]::WriteAllText($tmp, "warn=$warn`nblock=$block`n",
        (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $configPath -Force
    Write-AutoOSCostGateLine "daily gate config created (warn $warn, block $block)" -Level ok
    'created'
}

# ─── scheduled task ──────────────────────────────────────────────────────────

function Get-AutoOSCostGateTaskArguments {
    <#
      .SYNOPSIS
        The command line the scheduled task runs, for a given repo checkout.
    #>
    param([Parameter(Mandatory)][string]$RepoRoot)

    $wrapper = Join-Path $PSScriptRoot 'cost-gate-export.ps1'
    "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" -RepoRoot `"$RepoRoot`""
}

function Get-AutoOSCostGateTaskExe {
    <#
      .SYNOPSIS
        pwsh when present, the system powershell otherwise.
    #>
    if (Get-Command -Name 'pwsh' -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }
}

function Test-AutoOSCostGateTaskCurrent {
    <#
      .SYNOPSIS
        True when the registered task already does exactly what we would register.
      .DESCRIPTION
        This is what lets a second run report `skipped`: re-registering
        identical tasks is not idempotent, it just churns the scheduler. The
        task carries two triggers (repetition every N minutes, plus logon);
        the check looks for the repetition on any of them, since the order
        the scheduler reports them back in is not ours to assume.
    #>
    param([Parameter(Mandatory)][string]$Arguments, [Parameter(Mandatory)][string]$Execute, [int]$IntervalMinutes)

    $task = Get-ScheduledTask -TaskName $script:CostGateTaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    $action = @($task.Actions)[0]
    if (-not $action -or $action.Arguments -ne $Arguments) { return $false }
    if ($action.Execute -ne $Execute) { return $false }
    if ($IntervalMinutes -gt 0) {
        $wanted = (New-TimeSpan -Minutes $IntervalMinutes)
        $current = $false
        foreach ($trigger in @($task.Triggers)) {
            if ($trigger -and $trigger.Repetition -and
                [System.Xml.XmlConvert]::ToTimeSpan($trigger.Repetition.Interval) -eq $wanted) {
                $current = $true
                break
            }
        }
        if (-not $current) { return $false }
    }
    $true
}

function Write-AutoOSCostGateLine {
    <#
      .SYNOPSIS
        A status line this module can always print.
      .DESCRIPTION
        The UI layer's Write-AutoOSLine writes straight to the console, which
        is invisible to callers who capture the module's output stream (and
        to tests). Writing through Write-Output keeps both paths working;
        the console is not touched here.
    #>
    param([string]$Message, [string]$Level = 'plain')
    $prefix = switch ($Level) {
        'ok'    { '+ ' }
        'warn'  { '! ' }
        'error' { 'x ' }
        default { '' }
    }
    Write-Output ("$prefix$Message")
}

# ─── install ─────────────────────────────────────────────────────────────────

function Install-CostGateTask {
    <#
      .SYNOPSIS
        Register the per-user "AutoOS cost gate" Scheduled Task.
      .PARAMETER Warn
        USD spend at which the gate turns `warn`. Non-numeric values are
        refused with an error and nothing is written.
      .PARAMETER Block
        USD spend at which the gate turns `block`. Must be greater than Warn.
      .PARAMETER RepoRoot
        Absolute path of the AutoOS checkout this machine uses.
      .PARAMETER Start
        Start the task right now (a first refresh while the operator watches).
      .PARAMETER DryRun
        Say what would happen without touching the scheduler.
      .OUTPUTS
        'installed' when something changed, 'skipped' when the task was
        already current, 'failed' on error.
      .DESCRIPTION
        Creates %APPDATA%\autoos\daily-gate.conf only when it is absent
        (warn=/block=, nothing else), then registers the task to run the
        wrapper every 10 minutes and at user logon. The task is not started
        unless -Start is passed, so an install can never race the gateway.
    #>
    param(
        [int]$Warn = 20,
        [int]$Block = 25,
        [Parameter(Mandatory)][string]$RepoRoot,
        [switch]$Start,
        [switch]$DryRun
    )

    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw "RepoRoot '$RepoRoot' is not a directory"
    }
    if (-not (Test-AutoOSCostGateThreshold -Value ([string]$Warn))) {
        throw "Warn must be a non-negative whole number, got '$Warn'"
    }
    if (-not (Test-AutoOSCostGateThreshold -Value ([string]$Block))) {
        throw "Block must be a non-negative whole number, got '$Block'"
    }
    if ($Block -le $Warn) {
        throw "Block ($Block) must be greater than Warn ($Warn)"
    }

    $configPath = Get-AutoOSCostGateConfigPath
    $taskArguments = Get-AutoOSCostGateTaskArguments -RepoRoot $RepoRoot
    $exe = Get-AutoOSCostGateTaskExe

    if ($DryRun) {
        Write-AutoOSCostGateLine "would ensure $configPath (warn $warn, block $block) and register $([string]$script:CostGateTaskName) (every 10 minutes)" -Level muted
        return 'installed'
    }

    try {
        Write-AutoOSCostGateConfig -Warn $Warn -Block $Block | Where-Object { $_ -notin @('created', 'kept') }

        if (Test-AutoOSCostGateTaskCurrent -Arguments $taskArguments -Execute $exe -IntervalMinutes 10) {
            Write-AutoOSCostGateLine 'scheduled task is already current' -Level ok
            return 'skipped'
        }

        $userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew
        # A repetition on a one-shot trigger (the same shape the Claude
        # snapshot task uses) plus a logon trigger: the repetition keeps the
        # gate fresh while the machine is on, the logon run catches up after
        # a boot, and StartWhenAvailable covers short gaps.
        $repetition = New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes 10) `
            -RepetitionDuration (New-TimeSpan -Days 3650)
        $logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
        $action = New-ScheduledTaskAction -Execute $exe -Argument $taskArguments

        # Re-registering with -Force is how the Claude autostart module updates
        # a stale task; for us it only ever runs when the current check failed,
        # so a healthy task is never touched.
        Register-ScheduledTask -TaskName $script:CostGateTaskName -Force `
            -Action $action `
            -Trigger @($repetition, $logon) `
            -Principal $principal -Settings $settings | Out-Null
        Write-AutoOSCostGateLine "registered '$($script:CostGateTaskName)' (every 10 minutes and at logon)" -Level ok

        if ($Start) {
            Start-ScheduledTask -TaskName $script:CostGateTaskName
            Write-AutoOSCostGateLine "started '$($script:CostGateTaskName)' now" -Level ok
        }
        'installed'
    } catch {
        Write-AutoOSCostGateLine "could not register the cost gate: $($_.Exception.Message)" -Level warn
        'failed'
    }
}

Export-ModuleMember -Function `
    Install-CostGateTask, Get-AutoOSCostGateConfigPath, Write-AutoOSCostGateConfig,
    Test-AutoOSCostGateThreshold, Get-AutoOSCostGateTaskArguments, Test-AutoOSCostGateTaskCurrent
