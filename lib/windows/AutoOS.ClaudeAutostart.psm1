#Requires -Version 5.1
<#
  AutoOS.ClaudeAutostart — restore the Claude Code sessions that were live before
  the last shutdown, on Windows.

  Discovery reads %USERPROFILE%\.claude\projects\<slug>\<session-id>.jsonl. A
  transcript record carries `cwd` and `sessionId` verbatim, which is the only way
  to learn a session's directory here: Win32_Process exposes no working directory,
  so the previous command-line scan could never resolve one and reported zero
  sessions on a machine running six (ADR 0001).

  Restore opens a Windows Terminal tab per session. A Claude Code session is an
  interactive TUI; started from a hidden Scheduled Task it becomes a process the
  user can neither see nor answer, so with no terminal host this module refuses
  and says why rather than orphaning one (ADR 0002).
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

$script:StateVersion = 2
$script:RestoreTaskName = 'AutoOS-Claude-Restore'
$script:SnapshotTaskName = 'AutoOS-Claude-Snapshot'

# ─── configuration ───────────────────────────────────────────────────────────

function Get-AutoOSClaudeDefaults {
    <#
      .SYNOPSIS
        Default for every claude_autostart setting, read from the shipped example.
      .DESCRIPTION
        autoos.config.example.json is the single home for these values so that this
        module, lib/linux/claude_sessions.py and the web UI cannot drift apart. The
        literal below is a backstop for a broken checkout only.
    #>
    $example = Join-Path $PSScriptRoot '..\..\autoos.config.example.json'
    if (Test-Path -LiteralPath $example) {
        try {
            $block = (Get-Content -LiteralPath $example -Raw -Encoding UTF8 | ConvertFrom-Json).claude_autostart
            if ($block) {
                $table = @{}
                foreach ($p in $block.PSObject.Properties) { $table[$p.Name] = $p.Value }
                if ($table.Count -gt 0) { return $table }
            }
        } catch { $null }
    }
    @{
        enabled = $true; snapshot_interval_mins = 5; liveness_window_mins = 240
        max_sessions = 8; resume_mode = 'full'; fallback = 'continue'
        fallback_cwd = ''; fallback_name = 'main'; remote_control = 'snapshot'
        terminal_host = 'auto'
    }
}

function Get-AutoOSClaudeConfig {
    <#
      .SYNOPSIS
        Effective settings: defaults, overlaid by autoos.config.json.
    #>
    param([string]$ConfigPath)

    $config = Get-AutoOSClaudeDefaults
    if (-not $ConfigPath) {
        $ConfigPath = if ($env:AUTOOS_CONFIG_FILE) { $env:AUTOOS_CONFIG_FILE }
                      else { Join-Path $PSScriptRoot '..\..\autoos.config.json' }
    }
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $block = (Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).claude_autostart
            if ($block) {
                foreach ($p in $block.PSObject.Properties) { $config[$p.Name] = $p.Value }
            }
        } catch {
            Write-AutoOSLine "ignoring unreadable $ConfigPath : $($_.Exception.Message)" -Level warn
        }
    }
    $config
}

function Set-AutoOSClaudeConfig {
    <#
      .SYNOPSIS
        Merge settings into autoos.config.json's claude_autostart block.
      .DESCRIPTION
        Read-modify-write, never replace: the file also holds the profile and every
        other answer, and the web UI's first version wiped three keys by assigning
        a fresh object over the top of it.
    #>
    param(
        [Parameter(Mandatory)][hashtable]$Settings,
        [string]$ConfigPath
    )

    if (-not $ConfigPath) {
        $ConfigPath = if ($env:AUTOOS_CONFIG_FILE) { $env:AUTOOS_CONFIG_FILE }
                      else { Join-Path $PSScriptRoot '..\..\autoos.config.json' }
    }
    $known = Get-AutoOSClaudeDefaults

    $doc = if (Test-Path -LiteralPath $ConfigPath) {
        Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        [pscustomobject]@{ version = 1 }
    }
    if (-not ($doc.PSObject.Properties.Name -contains 'claude_autostart')) {
        $doc | Add-Member -NotePropertyName 'claude_autostart' -NotePropertyValue ([pscustomobject]@{}) -Force
    }

    foreach ($key in $Settings.Keys) {
        if (-not $known.ContainsKey($key)) { throw "unknown setting: $key" }
        $value = $Settings[$key]
        # Match the shipped default's type so "5" never lands where 5 belongs.
        if ($known[$key] -is [bool])   { $value = [bool]$value }
        elseif ($known[$key] -is [int]) { $value = [int]$value }
        $doc.claude_autostart | Add-Member -NotePropertyName $key -NotePropertyValue $value -Force
    }

    $dir = Split-Path -Parent $ConfigPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
    $tmp = "$ConfigPath.tmp.$([Guid]::NewGuid().ToString('N'))"
    [System.IO.File]::WriteAllText($tmp, ($doc | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $ConfigPath -Force
    $ConfigPath
}

function Get-AutoOSClaudeSessionsPath {
    if ($env:AUTOOS_CLAUDE_STATE_FILE) { return $env:AUTOOS_CLAUDE_STATE_FILE }
    Join-Path (Join-Path $env:LOCALAPPDATA 'AutoOS') 'claude-sessions.json'
}

function Get-AutoOSClaudeProjectsPath {
    $home_ = if ($env:AUTOOS_CLAUDE_HOME) { $env:AUTOOS_CLAUDE_HOME }
             else { Join-Path $env:USERPROFILE '.claude' }
    Join-Path $home_ 'projects'
}

# ─── liveness ────────────────────────────────────────────────────────────────

function Get-AutoOSClaudeLiveCount {
    <#
      .SYNOPSIS
        How many Claude Code processes are running.
      .DESCRIPTION
        Identity comes from transcripts; this only answers "is anything up at all",
        which is the gate that stops a snapshot firing mid-boot from describing the
        machine as empty. AUTOOS_CLAUDE_LIVE_COUNT overrides it for tests and
        diagnostics.
    #>
    if ($env:AUTOOS_CLAUDE_LIVE_COUNT) {
        $n = 0
        if ([int]::TryParse($env:AUTOOS_CLAUDE_LIVE_COUNT, [ref]$n)) { return $n }
    }
    try {
        # An npm install is hosted by node.exe, so the image name alone is not
        # enough; our own helper scripts mention claude and must not count.
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cmd = [string]$_.CommandLine
            ($_.Name -like 'claude*' -or ($_.Name -in @('node.exe', 'bun.exe') -and $cmd -like '*claude*')) -and
            $cmd -notlike '*claude-sessions*' -and $cmd -notlike '*claude_sessions*'
        })
        return $procs.Count
    } catch {
        return 0
    }
}

# ─── discovery ───────────────────────────────────────────────────────────────

function Get-AutoOSClaudeTranscriptHead {
    <#
      .SYNOPSIS
        The first record in a transcript that carries a cwd, or $null.
      .DESCRIPTION
        A transcript opens with queue-operation records that have no cwd, so this
        reads forward — but stops well short of parsing a whole conversation.
    #>
    param([Parameter(Mandatory)][string]$Path, [int]$MaxLines = 40)

    try {
        $reader = [System.IO.StreamReader]::new($Path)
    } catch {
        return $null
    }
    try {
        for ($i = 0; $i -lt $MaxLines; $i++) {
            $line = $reader.ReadLine()
            if ($null -eq $line) { return $null }
            if (-not $line.Trim()) { continue }
            try { $record = $line | ConvertFrom-Json } catch { continue }
            if ($record.PSObject.Properties.Name -contains 'cwd' -and $record.cwd) { return $record }
        }
    } finally {
        $reader.Dispose()
    }
    $null
}

function Find-AutoOSClaudeSessions {
    <#
      .SYNOPSIS
        Every session whose transcript was touched inside the liveness window.
      .OUTPUTS
        Newest-first, capped at max_sessions, so the cap keeps the sessions the
        user was actually in rather than an arbitrary slice of a directory listing.
    #>
    param([hashtable]$Config)

    if (-not $Config) { $Config = Get-AutoOSClaudeConfig }
    if ((Get-AutoOSClaudeLiveCount) -lt 1) { return @() }

    $projects = Get-AutoOSClaudeProjectsPath
    if (-not (Test-Path -LiteralPath $projects)) { return @() }

    $cutoff = (Get-Date).AddMinutes(-1 * [int]$Config['liveness_window_mins'])
    $found = New-Object System.Collections.ArrayList

    foreach ($file in @(Get-ChildItem -LiteralPath $projects -Filter '*.jsonl' -File -Recurse -ErrorAction SilentlyContinue)) {
        if ($file.LastWriteTime -lt $cutoff) { continue }
        $record = Get-AutoOSClaudeTranscriptHead -Path $file.FullName
        if (-not $record) { continue }

        $uuid = if ($record.PSObject.Properties.Name -contains 'sessionId' -and $record.sessionId) {
            [string]$record.sessionId
        } else {
            [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
        }
        $cwd = [string]$record.cwd
        if (-not $uuid -or -not $cwd) { continue }

        $branch = if ($record.PSObject.Properties.Name -contains 'gitBranch') { [string]$record.gitBranch } else { '' }
        [void]$found.Add([pscustomobject]@{
            session_uuid    = $uuid
            name            = (Split-Path $cwd.TrimEnd('\', '/') -Leaf)
            cwd             = $cwd
            git_branch      = $branch
            transcript_path = $file.FullName
            last_active     = [int][double]::Parse((Get-Date $file.LastWriteTimeUtc -UFormat %s))
            # No transcript records whether --remote-control was on, so the recorded
            # value is always false and the remote_control setting decides at restore.
            remote_control  = $false
        })
    }

    # One session can leave transcripts under two project slugs — a scratchpad
    # directory beside the repo does exactly this — and restoring the same id
    # twice opens two terminals fighting over one conversation. Newest wins.
    $seen = @{}
    $unique = New-Object System.Collections.ArrayList
    foreach ($session in @($found | Sort-Object -Property last_active -Descending)) {
        if ($seen.ContainsKey($session.session_uuid)) { continue }
        $seen[$session.session_uuid] = $true
        [void]$unique.Add($session)
    }

    @($unique | Select-Object -First ([int]$Config['max_sessions']))
}

# ─── state ───────────────────────────────────────────────────────────────────

function Get-AutoOSClaudeState {
    param([string]$Path)
    $file = if ($Path) { $Path } else { Get-AutoOSClaudeSessionsPath }
    if (Test-Path -LiteralPath $file) {
        try {
            $doc = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($doc.PSObject.Properties.Name -contains 'sessions') {
                return [pscustomobject]@{
                    version         = $doc.version
                    captured_at     = $doc.captured_at
                    captured_at_iso = if ($doc.PSObject.Properties.Name -contains 'captured_at_iso') { $doc.captured_at_iso } else { $null }
                    sessions        = @($doc.sessions)
                }
            }
        } catch { $null }
    }
    [pscustomobject]@{ version = $script:StateVersion; captured_at = 0; captured_at_iso = $null; sessions = @() }
}

function Save-AutoOSClaudeSnapshot {
    <#
      .SYNOPSIS
        Record the live sessions, refusing to replace a good snapshot with an empty one.
      .PARAMETER Sessions
        The sessions to record. Omit to discover them. The previous version of this
        function ignored its input entirely, which is what made the hook path a
        silent no-op — passing sessions now actually stores them.
    #>
    param(
        [object[]]$Sessions,
        [string]$Path,
        [switch]$DryRun
    )

    $file = if ($Path) { $Path } else { Get-AutoOSClaudeSessionsPath }
    if ($null -eq $Sessions) { $Sessions = @(Find-AutoOSClaudeSessions) }
    $Sessions = @($Sessions | Where-Object { $_ -and $_.session_uuid })

    $previous = Get-AutoOSClaudeState -Path $file
    if ($Sessions.Count -eq 0 -and @($previous.sessions).Count -gt 0) {
        # An empty result almost always means "the sessions are not up yet", not
        # "the user closed everything"; overwriting erases the record the next
        # restore depends on.
        Write-AutoOSLine 'no live sessions found - keeping the previous snapshot untouched' -Level muted
        return $previous
    }

    $payload = [pscustomobject]@{
        version         = $script:StateVersion
        captured_at     = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        captured_at_iso = (Get-Date).ToString('o')
        sessions        = $Sessions
    }

    if ($DryRun) {
        Write-AutoOSLine "would record $($Sessions.Count) session(s) to $file" -Level muted
        return $payload
    }

    $dir = Split-Path -Parent $file
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
    # Write-then-rename: a snapshot interrupted by the shutdown it is racing must
    # not leave a half-written file where the next boot's restore will read it.
    $tmp = "$file.tmp.$([Guid]::NewGuid().ToString('N'))"
    [System.IO.File]::WriteAllText($tmp, ($payload | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $file -Force
    Write-AutoOSLine "recorded $($Sessions.Count) session(s) -> $file" -Level ok
    $payload
}

# ─── terminal host ───────────────────────────────────────────────────────────

function Get-AutoOSClaudeTerminalHost {
    <#
      .SYNOPSIS
        A terminal a restored session can be seen and typed in, or $null.
    #>
    param([hashtable]$Config)
    if (-not $Config) { $Config = Get-AutoOSClaudeConfig }

    $want = [string]$Config['terminal_host']
    if ($want -and $want -ne 'auto' -and $want -ne 'wt') { return $null }

    $wt = Get-Command -Name 'wt.exe' -ErrorAction SilentlyContinue
    if ($wt) { return 'wt' }
    $null
}

function Start-AutoOSClaudeSession {
    <#
      .SYNOPSIS
        Open one Windows Terminal tab running the given claude arguments.
      .DESCRIPTION
        `cmd /k` keeps the tab open once claude exits, so a session that fails to
        resume leaves its error on screen instead of closing over it.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Cwd,
        [Parameter(Mandatory)][string[]]$ClaudeArgs
    )

    $wtArgs = @('-w', '0', 'new-tab', '--title', $Name, '-d', $Cwd, 'cmd.exe', '/k', 'claude') + $ClaudeArgs
    Start-Process -FilePath 'wt.exe' -ArgumentList $wtArgs
}

# ─── restore ─────────────────────────────────────────────────────────────────

function Get-AutoOSClaudeRestorePlan {
    <#
      .SYNOPSIS
        One entry per recorded session: what to start, or why it is being skipped.
    #>
    param([hashtable]$Config, [string]$Path)
    if (-not $Config) { $Config = Get-AutoOSClaudeConfig }

    $override = [string]$Config['remote_control']
    foreach ($session in @((Get-AutoOSClaudeState -Path $Path).sessions)) {
        $uuid = if ($session.PSObject.Properties.Name -contains 'session_uuid') { [string]$session.session_uuid } else { '' }
        $name = if ($session.PSObject.Properties.Name -contains 'name' -and $session.name) { [string]$session.name } else { 'main' }
        $cwd = if ($session.PSObject.Properties.Name -contains 'cwd' -and $session.cwd) { [string]$session.cwd } else { $env:USERPROFILE }

        if (-not $uuid) {
            [pscustomobject]@{ Action = 'SKIP'; Uuid = ''; Name = $name; Cwd = $cwd; RemoteControl = $false; Reason = 'no session id was recorded' }
            continue
        }
        $rc = switch ($override) {
            'always' { $true }
            'never'  { $false }
            default  { [bool]$session.remote_control }
        }
        [pscustomobject]@{ Action = 'START'; Uuid = $uuid; Name = $name; Cwd = $cwd; RemoteControl = $rc; Reason = '' }
    }
}

function Restore-AutoOSClaudeSessions {
    param(
        [hashtable]$Config,
        [switch]$DryRun
    )
    if (-not $Config) { $Config = Get-AutoOSClaudeConfig }

    # A pause switch that leaves the tasks and the snapshots in place, so turning
    # it back on restores the sessions you had rather than starting over.
    if (-not [bool]$Config['enabled']) {
        Write-AutoOSLine 'autostart is disabled in the configuration - restoring nothing' -Level muted
        return
    }
    if (-not (Get-Command -Name 'claude' -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'claude is not on PATH - nothing to restore' -Level warn
        return
    }

    $host_ = Get-AutoOSClaudeTerminalHost -Config $Config
    if (-not $host_) {
        Write-AutoOSLine 'no terminal host available (Windows Terminal not found) - refusing to start an interactive session nothing can attach to' -Level warn
        Write-AutoOSLine 'install Windows Terminal (winget install Microsoft.WindowsTerminal), then run: claude-sessions.ps1 -Action restore' -Level muted
        return
    }

    $plan = @(Get-AutoOSClaudeRestorePlan -Config $Config)
    $starts = @($plan | Where-Object { $_.Action -eq 'START' })

    if ($starts.Count -eq 0) {
        Restore-AutoOSClaudeFallback -Config $Config -DryRun:$DryRun
        return
    }

    foreach ($entry in $plan) {
        if ($entry.Action -eq 'SKIP') {
            Write-AutoOSLine "skipped $($entry.Name) ($($entry.Cwd)): $($entry.Reason)" -Level warn
            continue
        }
        $claudeArgs = @('--resume', $entry.Uuid)
        if ($entry.RemoteControl) { $claudeArgs += '--rc' }
        $claudeArgs += @('-n', $entry.Name)

        if ($DryRun) {
            Write-AutoOSLine "would open a terminal in $($entry.Cwd): claude $($claudeArgs -join ' ')" -Level muted
            continue
        }
        Start-AutoOSClaudeSession -Name $entry.Name -Cwd $entry.Cwd -ClaudeArgs $claudeArgs
        Write-AutoOSLine "restored $($entry.Name) in $($entry.Cwd)" -Level ok
    }
}

function Restore-AutoOSClaudeFallback {
    param([hashtable]$Config, [switch]$DryRun)

    if ([string]$Config['fallback'] -ne 'continue') {
        Write-AutoOSLine 'nothing recorded to restore, and fallback is off' -Level muted
        return
    }
    if ((Get-AutoOSClaudeLiveCount) -gt 0) {
        Write-AutoOSLine 'a Claude session is already running - fallback skipped' -Level muted
        return
    }

    $name = if ($Config['fallback_name']) { [string]$Config['fallback_name'] } else { 'main' }
    $cwd = if ($Config['fallback_cwd']) { [string]$Config['fallback_cwd'] } else { $env:USERPROFILE }
    $claudeArgs = @('--continue')
    if ([string]$Config['remote_control'] -eq 'always') { $claudeArgs += '--rc' }
    $claudeArgs += @('-n', $name)

    if ($DryRun) {
        Write-AutoOSLine "would open a terminal in $($cwd): claude $($claudeArgs -join ' ')" -Level muted
        return
    }
    Write-AutoOSLine "no snapshot to restore: starting $name in $cwd" -Level ok
    Start-AutoOSClaudeSession -Name $name -Cwd $cwd -ClaudeArgs $claudeArgs
}

# ─── supervisor ──────────────────────────────────────────────────────────────

function Get-AutoOSClaudeTaskArguments {
    param([Parameter(Mandatory)][string]$Action)
    $cli = Join-Path $PSScriptRoot 'claude-sessions.ps1'
    # The launcher is hidden because it is a script with no UI of its own; the
    # Windows Terminal tab it opens is visible, which is the whole point (ADR 0002).
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$cli`" -Action $Action"
}

function Test-AutoOSClaudeAutostartInstalled {
    $null -ne (Get-ScheduledTask -TaskName $script:RestoreTaskName -ErrorAction SilentlyContinue)
}

function Test-AutoOSClaudeTaskCurrent {
    <#
      .SYNOPSIS
        True when the registered task already does exactly what we would register.
      .DESCRIPTION
        This is what lets a second run report `skipped`: re-registering identical
        tasks is not idempotent, it just churns the scheduler.
    #>
    param([Parameter(Mandatory)][string]$TaskName, [Parameter(Mandatory)][string]$Arguments, [int]$IntervalMinutes)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    $action = @($task.Actions)[0]
    if (-not $action -or $action.Arguments -ne $Arguments) { return $false }
    if ($IntervalMinutes -gt 0) {
        $trigger = @($task.Triggers)[0]
        $wanted = (New-TimeSpan -Minutes $IntervalMinutes)
        if (-not $trigger -or -not $trigger.Repetition -or
            [System.Xml.XmlConvert]::ToTimeSpan($trigger.Repetition.Interval) -ne $wanted) { return $false }
    }
    $true
}

function Register-AutoOSClaudeAutostartTask {
    <#
      .OUTPUTS
        'installed' when something changed, 'skipped' when both tasks were already
        current, 'failed' on error.
    #>
    param([hashtable]$Config, [switch]$DryRun)
    if (-not $Config) { $Config = Get-AutoOSClaudeConfig }
    $interval = [int]$Config['snapshot_interval_mins']
    if ($interval -lt 1 -or $interval -gt 59) { $interval = 5 }

    $restoreArgs = Get-AutoOSClaudeTaskArguments -Action 'restore'
    $snapshotArgs = Get-AutoOSClaudeTaskArguments -Action 'snapshot'

    if ($DryRun) {
        Write-AutoOSLine "would register $($script:RestoreTaskName) (at logon) and $($script:SnapshotTaskName) (every $interval min)" -Level muted
        return 'installed'
    }

    $restoreCurrent = Test-AutoOSClaudeTaskCurrent -TaskName $script:RestoreTaskName -Arguments $restoreArgs
    $snapshotCurrent = Test-AutoOSClaudeTaskCurrent -TaskName $script:SnapshotTaskName -Arguments $snapshotArgs -IntervalMinutes $interval
    if ($restoreCurrent -and $snapshotCurrent) {
        Write-AutoOSLine 'scheduled tasks are already current' -Level ok
        return 'skipped'
    }

    try {
        $userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive
        # StartWhenAvailable is the catch-up the systemd side gets from Persistent=true.
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew

        if (-not $restoreCurrent) {
            Register-ScheduledTask -TaskName $script:RestoreTaskName -Force `
                -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $restoreArgs) `
                -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $userId) `
                -Principal $principal -Settings $settings | Out-Null
            Write-AutoOSLine "registered $($script:RestoreTaskName) (at logon)" -Level ok
        }

        if (-not $snapshotCurrent) {
            # A repetition on a one-shot trigger, not a daily trigger grafted onto
            # someone else's Repetition object: the latter is undefined behaviour
            # and silently produced a task that ran once a day.
            $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
                -RepetitionInterval (New-TimeSpan -Minutes $interval) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            Register-ScheduledTask -TaskName $script:SnapshotTaskName -Force `
                -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $snapshotArgs) `
                -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
            Write-AutoOSLine "registered $($script:SnapshotTaskName) (every $interval min)" -Level ok
        }
        return 'installed'
    } catch {
        Write-AutoOSLine "could not register the scheduled tasks: $($_.Exception.Message)" -Level warn
        return 'failed'
    }
}

function Unregister-AutoOSClaudeAutostartTask {
    param([switch]$DryRun)
    foreach ($name in @($script:RestoreTaskName, $script:SnapshotTaskName)) {
        if ($DryRun) {
            Write-AutoOSLine "would unregister $name" -Level muted
            continue
        }
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
            Write-AutoOSLine "unregistered $name" -Level ok
        } else {
            Write-AutoOSLine "$name was not registered" -Level muted
        }
    }
}

Export-ModuleMember -Function `
    Get-AutoOSClaudeDefaults, Get-AutoOSClaudeConfig, Set-AutoOSClaudeConfig, Get-AutoOSClaudeSessionsPath,
    Get-AutoOSClaudeProjectsPath, Get-AutoOSClaudeLiveCount, Get-AutoOSClaudeTranscriptHead,
    Find-AutoOSClaudeSessions, Get-AutoOSClaudeState, Save-AutoOSClaudeSnapshot,
    Get-AutoOSClaudeTerminalHost, Start-AutoOSClaudeSession, Get-AutoOSClaudeRestorePlan,
    Restore-AutoOSClaudeSessions, Restore-AutoOSClaudeFallback, Get-AutoOSClaudeTaskArguments,
    Test-AutoOSClaudeAutostartInstalled, Test-AutoOSClaudeTaskCurrent,
    Register-AutoOSClaudeAutostartTask, Unregister-AutoOSClaudeAutostartTask
