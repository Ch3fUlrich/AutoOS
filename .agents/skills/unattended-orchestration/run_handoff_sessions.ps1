<#
.SYNOPSIS
Run a set of agent sessions unattended: background sessions in their own git
worktrees, in parallel lanes, surviving usage limits, API outages and early stops.

.DESCRIPTION
A GENERAL, PORTABLE runner. Copy this folder into any git repository, run -Init,
edit the sessions, and go — nothing here is specific to the repository it came
from, and the session tool itself is a swappable adapter (see `launcher` in the
config), so it can drive agents other than Claude Code.

  -Init        scaffold a config for the repo you are standing in
  -Validate    check the config; report repo, branch, lanes, guards, launcher
  -EmitBriefs  render every brief as copy-pasteable markdown, launch nothing
  -DryRun      rehearse: preflight and lane dispatch, but no session is started
  -Cleanup     stop, remove and prune every MERGED session's background process,
               worktree, branch and Serena project row; launches nothing
  -Status      print every session's state from state.json as a table

-Lanes takes several lanes as ONE string separated by `;` ("A,B;C;D"), so it
survives `pwsh -File` and Start-Process, which flatten an array argument into
positional tokens and bind them to the wrong parameters.
  (no flag)    run it

While it runs, <stateDir>/queue is the operator's control surface: drop
lane-<name>.json ({"sessions": ["X"]}) to start another lane (the child re-reads
the config, so a session added to it after the start is launchable), or an empty
stop-<session key> file to stop that session's lane at its next decision point.
<stateDir>/load.json carries cpu_percent and the running sessions, refreshed
every poll, for briefs that defer heavy runs under a machine budget.

The repository is DETECTED (`git rev-parse --show-toplevel`), so a committed
config carries no machine-specific path and works for everyone who clones it.

For each session, in its lane:
  1. a worktree <worktreeParent>/<worktreePrefix>-<name> on branch
     <branchPrefix>/<name> is created from the CURRENT base branch (pruned
     first, so a deleted directory is recreated), with any `linkDirs` linked to
     the main checkout's copies (junction on Windows, symlink elsewhere);
  2. the session is started through the launcher adapter's `start` action with a
     self-contained brief on the model the config assigns, under a stable
     remote-control name so it stays listed and joinable. The runner polls the
     adapter's `list` action until the session leaves its working state, then
     reads its log: on a usage limit ("usage limit reached|<epoch>") it sleeps
     until that reset (else 30 min) and RESUMES the same session; on an API
     outage it waits 5 min and resumes; when the session stops without writing
     its DONE note it is resumed with a nudge, up to -MaxContinues times; a
     single turn running past -MaxSessionHours is stopped and resumed;
  3. the config's guard command runs in the worktree; if it passes and -NoMerge
     is not set, the branch is merged into the base branch (--no-ff, under a
     cross-process lock so lanes never merge at once); a red guard or a merge
     conflict stops THAT lane only.

State lives in <stateDir>/state.json (one entry per session: status, session id,
attempts, last error); a re-run skips sessions already merged and resumes ones
left running. Everything is logged to <stateDir>/runner.log.

-WaitUntil "yyyy-MM-dd HH:mm" sleeps before starting. -FollowUp
"yyyy-MM-dd HH:mm" additionally spawns a detached child that waits until then
and runs -FollowUpSessions fresh, so one invocation covers a night and a morning.

Keep the machine awake (no sleep/hibernate); the runner cannot change power
settings.

Permissions: an unattended session cannot answer prompts, so the config's
`allowedTools` are pre-allowed. Repository hooks still run on every call — the
hook, not the prompt, is the guard.

See SKILL.md for the model and adoption guide, and handoff.config.example.json
for every field annotated.

.EXAMPLE
pwsh -File run_handoff_sessions.ps1 -Init
pwsh -File run_handoff_sessions.ps1 -Validate
pwsh -File run_handoff_sessions.ps1 -EmitBriefs -OutFile briefs.md
pwsh -File run_handoff_sessions.ps1 -DryRun
pwsh -File run_handoff_sessions.ps1 -Sessions build
#>
param(
    [string]$Config = "",
    [string[]]$Sessions = @(),
    [string[]]$Lanes = @(),
    [switch]$NoMerge,
    [switch]$DryRun,
    [switch]$Fresh,
    [switch]$Validate,
    [switch]$EmitBriefs,
    [switch]$Init,
    [switch]$Force,
    [string]$OutFile = "",
    [string]$WaitUntil = "",
    [string]$FollowUp = "",
    [string[]]$FollowUpSessions = @(),
    [int]$MaxContinues = 0,
    [int]$MaxRetries = 0,
    [int]$MaxSessionHours = 0,
    [switch]$Cleanup,
    [switch]$Status,
    [string]$LaneName = ""
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "HandoffCore.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot "AgentAdapters.psm1") -Force -DisableNameChecking -ErrorAction SilentlyContinue

# --------------------------------------------------------------------------
# Config discovery: explicit -Config, else the conventional locations. Being
# able to run with no arguments from a configured repo is what makes this
# reusable rather than a script every repo re-parameterises by hand.
# --------------------------------------------------------------------------
$detectedRoot = Resolve-HandoffRepoRoot "."
$configCandidates = @(".claude/handoff.config.json", "handoff.config.json", ".handoff.json")

if ($Init) {
    # Scaffold a runnable config for whatever repository this was copied into.
    $target = if ($Config) { $Config } else { ".claude/handoff.config.json" }
    $base = if ($detectedRoot) {
        $b = (& git -C $detectedRoot rev-parse --abbrev-ref HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $b) { ([string]$b).Trim() } else { "main" }
    } else { "main" }
    $written = New-HandoffConfigScaffold -Path $target -RepoRoot $detectedRoot -BaseBranch $base -Force:$Force
    Write-Host "wrote $written (base branch: $base)"
    Write-Host "next: edit the sessions, then -Validate, then -EmitBriefs or -DryRun"
    exit 0
}

if (-not $Config) {
    foreach ($c in $configCandidates) { if (Test-Path $c) { $Config = $c; break } }
    # Also look beside the repo root, so the runner works from a subdirectory.
    if (-not $Config -and $detectedRoot) {
        foreach ($c in $configCandidates) {
            $p = Join-Path $detectedRoot $c
            if (Test-Path $p) { $Config = $p; break }
        }
    }
}
if (-not $Config) {
    throw "no config found ($($configCandidates -join ', ')). Create one with:  pwsh -File $PSCommandPath -Init"
}
# `repo` may be absent from the config on purpose; the detected root fills it in,
# which is what lets a committed config be shared between machines.
$cfg = Read-HandoffConfig $Config -DefaultRepo $detectedRoot
# Where this skill lives: briefs, guards and postWorktree may reference the
# bundled helpers as {{skillDir}} instead of a path that belongs to one repo.
$cfg["skillDir"] = $PSScriptRoot
$launcherExecutable = if (Get-Command Resolve-AdapterExecutable -ErrorAction SilentlyContinue) {
    Resolve-AdapterExecutable $cfg.launcher.command
} else {
    $cfg.launcher.command
}
# The config's launcher is the DEFAULT; a session may drive another agent. Kept here so each
# session resolves from the default rather than from whatever the previous session swapped in.
$defaultLauncher = $cfg.launcher

# CLI overrides beat config; config beats the module defaults.
if ($MaxContinues -gt 0) { $cfg.maxContinues = $MaxContinues }
if ($MaxRetries -gt 0) { $cfg.maxRetries = $MaxRetries }
if ($MaxSessionHours -gt 0) { $cfg.maxSessionHours = $MaxSessionHours }

$repo = $cfg.repo
$stateDir = if ([System.IO.Path]::IsPathRooted($cfg.stateDir)) { $cfg.stateDir } else { Join-Path $repo $cfg.stateDir }
$stateFile = Join-Path $stateDir "state.json"
$ledgerFile = Join-Path $stateDir "progress.jsonl"
$runnerLog = Join-Path $stateDir "runner.log"
$queueDir = Join-Path $stateDir "queue"
$loadFile = Join-Path $stateDir "load.json"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
New-Item -ItemType Directory -Force -Path $queueDir | Out-Null

function Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$LaneName] $msg"
    Write-Host $line
    Add-Content -Path $runnerLog -Value $line -Encoding utf8
}

function Invoke-WithLock([scriptblock]$body) {
    <#  Cross-process mutual exclusion, portably, RETURNING the body's value.

        Named mutexes are a Windows facility: `System.Threading.Mutex` with a name
        throws PlatformNotSupportedException on Linux and macOS, so a copied skill
        would die on its first state write. Elsewhere an exclusive lock file gives
        the same guarantee — two lanes never merge at once.

        It returns the value rather than letting callers assign inside the
        scriptblock, because a `$script:` assignment in there writes a DIFFERENT
        variable from the function-scoped one read afterwards: that bug reported
        every successful merge as a conflict and stopped its lane.  #>
    $isWin = (Get-HandoffLinkType) -eq "Junction"
    if ($isWin) {
        $m = New-Object System.Threading.Mutex($false, $cfg.mutexName)
        [void]$m.WaitOne()
        try { return (& $body) } finally { $m.ReleaseMutex(); $m.Dispose() }
    }
    $lockFile = Join-Path $stateDir "runner.lock"
    $fs = $null
    for ($i = 0; $i -lt 600 -and -not $fs; $i++) {
        try { $fs = [System.IO.File]::Open($lockFile, 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $fs) { throw "could not acquire the runner lock at $lockFile after 5 minutes" }
    try { return (& $body) } finally { $fs.Dispose() }
}

function Update-State([string]$key, [hashtable]$fields) {
    Invoke-WithLock {
        $state = Read-HandoffState $stateFile
        if (-not $state.ContainsKey($key)) { $state[$key] = @{} }
        foreach ($k in $fields.Keys) { $state[$key][$k] = $fields[$k] }
        $state[$key]["updated"] = (Get-Date -Format "s")
        Write-HandoffState $stateFile $state
    }
}

function Parse-Result([string]$path) {
    # The JSON result is the last {...} object in the file; anything before it is
    # stderr noise.
    $out = @{ is_error = $true; result = ""; session_id = $null }
    if (-not (Test-Path $path)) { $out.result = "no transcript written"; return $out }
    $text = Get-Content $path -Raw
    $start = $text.LastIndexOf("`n{")
    if ($start -lt 0) { $start = $text.IndexOf("{") } else { $start += 1 }
    if ($start -lt 0) { $out.result = $text; return $out }
    try {
        $obj = $text.Substring($start) | ConvertFrom-Json
        $out.is_error = if ($obj.PSObject.Properties.Name -contains "is_error") { [bool]$obj.is_error } elseif ($obj.PSObject.Properties.Name -contains "status" -and $obj.status -ne "SUCCESS") { $true } else { $false }
        $out.result = if ($obj.PSObject.Properties.Name -contains "result") { [string]$obj.result } else { "" }
        $out.session_id = if ($obj.PSObject.Properties.Name -contains "session_id" -and $obj.session_id) { [string]$obj.session_id }
                          elseif ($obj.PSObject.Properties.Name -contains "conversation_id" -and $obj.conversation_id) { [string]$obj.conversation_id }
                          elseif ($obj.PSObject.Properties.Name -contains "id" -and $obj.id) { [string]$obj.id }
                          else { $null }
        if ($obj.PSObject.Properties.Name -contains "subtype" -and $obj.subtype -and $obj.subtype -ne "success") {
            $out.is_error = $true
            if (-not $out.result) { $out.result = [string]$obj.subtype }
        }
    }
    catch { $out.result = $text }
    return $out
}

# Lanes run as child processes: PowerShell 7 when present, without the user's
# profile, whose PSReadLine setup errors out on a redirected console.
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

function Ensure-Worktree([string]$wt, [string]$branch, [hashtable]$vars, [hashtable]$assets) {
    git -C $repo worktree prune 2>$null
    if (-not (Test-Path $wt)) {
        $exists = git -C $repo branch --list $branch
        if ($exists) { git -C $repo worktree add $wt $branch 2>&1 | Out-Null }
        else { git -C $repo worktree add $wt -b $branch $cfg.baseBranch 2>&1 | Out-Null }
        if ($LASTEXITCODE -ne 0) { throw "git worktree add failed for $wt" }
    }
    # Directories shared with the main checkout rather than copied — a generated
    # graph, a model cache. Junctions, so the worktree sees one artifact.
    foreach ($d in @($assets.linkDirs)) {
        if (-not $d) { continue }
        $link = Join-Path $wt $d
        $target = Join-Path $repo $d
        if (-not (Test-Path $target)) { continue }
        if (Test-Path $link) {
            $item = Get-Item $link -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }   # already linked
            # A tracked placeholder (data/.gitkeep) makes git create the directory
            # before the link can, and `-not (Test-Path $link)` then skipped the
            # link SILENTLY: the data session would have moved nothing and reported
            # success (measured 2026-09-05, downstream project B). A directory holding
            # nothing but placeholders is replaced by the link; real content is
            # left alone and logged, because replacing it would delete work.
            $real = @(Get-ChildItem $link -Force -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne ".gitkeep" })
            if ($real.Count) { Log "linkDirs: $link already holds $($real.Count) real file(s); NOT replacing it with a link to $target"; continue }
            Remove-Item -Recurse -Force $link
        }
        New-Item -ItemType (Get-HandoffLinkType) -Path $link -Target $target -ErrorAction SilentlyContinue | Out-Null
        if (Test-Path $link) { Log "linked $link -> $target" } else { Log "could not link $link -> $target" }
    }
    # Directories COPIED from the main checkout, so each worktree owns an
    # independent instance it may rebuild or append to without touching the
    # others' - a per-worktree code graph, a gitignored working ledger. Use
    # linkDirs for one shared artifact, copyDirs for one artifact per session.
    foreach ($d in @($assets.copyDirs)) {
        if (-not $d) { continue }
        $dst = Join-Path $wt $d
        $src = Join-Path $repo $d
        if ((Test-Path $src) -and -not (Test-Path $dst)) {
            $parent = Split-Path $dst -Parent
            if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -Recurse -Force $src $dst
        }
    }
    # Single FILES copied from the main checkout - a gitignored `.env`, a
    # local settings file - so a worktree session runs with the same secrets
    # and switches as the main checkout instead of silently generating its
    # own (a fresh SESSION_SECRET is harmless; a fresh DB key is not).
    foreach ($f in @($assets.copyFiles)) {
        if (-not $f) { continue }
        $dst = Join-Path $wt $f
        $src = Join-Path $repo $f
        if ((Test-Path $src -PathType Leaf) -and -not (Test-Path $dst)) {
            $parent = Split-Path $dst -Parent
            if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -Force $src $dst
        }
    }
    # A brand-new worktree is an unapproved folder: the first session in it asks
    # to trust the directory and its hooks, and a BACKGROUND session cannot
    # answer — it goes `blocked` and stays there. Pre-approving it is what an
    # interactive session would have done once. The repo supplies the command.
    if ($cfg.postWorktree) {
        $cmd = Expand-HandoffTemplate ([string]$cfg.postWorktree) $vars
        $out = & $shell -NoProfile -Command $cmd 2>&1 | Out-String
        Log "postWorktree: $(($out -replace '\s+', ' ').Trim())"
    }
}

function Invoke-Launcher([string]$action, [hashtable]$vars) {
    # Every call to the session tool goes through the adapter, so a repository can
    # drive a different agent by replacing config rather than editing this script.
    $argv = Build-HandoffLaunchArgs $cfg $action $vars
    return (& $launcherExecutable @argv 2>&1 | Out-String)
}

function Start-Bg([string]$wt, [string]$action, [hashtable]$vars, [string]$log) {
    if ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed") {
        # Runner-managed backgrounding for CLIs without daemon managers (agy, grok, codewhale).
        $argv = Build-HandoffLaunchArgs $cfg $action $vars
        $safeArgv = @($argv | ForEach-Object {
            $s = [string]$_
            if ($s -match '[\s"]') {
                $escaped = $s -replace '(\\+)(")', '$1$1$2' -replace '(")', '\"'
                "`"$escaped`""
            } else {
                $s
            }
        })
        $proc = Start-Process -FilePath $launcherExecutable -ArgumentList $safeArgv -WorkingDirectory $wt `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        if ($proc -and $proc.Id) { return [string]$proc.Id }
        Log "could not launch runner-managed background process for $($cfg.launcher.command)"
        return $null
    }
    # `claude --bg` returns at once and prints the short id that `claude agents`,
    # `attach`, `logs` and `stop` take. Background sessions are listed in the
    # agents view, so the operator can follow and even join them — which a `-p`
    # print session does not allow.
    Push-Location $wt
    try {
        $out = Invoke-Launcher $action $vars
        $out | Out-File -Encoding utf8 $log
    }
    finally { Pop-Location }
    if ($out -match $cfg.launcher.idPattern) { return $Matches[1] }
    Log "could not read a background id from: $(($out -replace '\s+', ' ').Trim())"
    return $null
}

function Get-BgEntry([string]$id) {
    if ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed") {
        try {
            $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
            if ($p -and -not $p.HasExited) {
                return @{ id = $id; state = "working"; pid = $p.Id }
            }
            return $null
        }
        catch { return $null }
    }
    try {
        $arr = (Invoke-Launcher "list" @{} | ConvertFrom-Json)
        return ($arr | Where-Object { $_.id -eq $id } | Select-Object -First 1)
    }
    catch { return $null }
}

function Stop-BgSession([string]$id) {
    if (-not $id) { return }
    if ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed") {
        try {
            $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
            if ($p -and -not $p.HasExited) { Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue }
        } catch { }
    } else {
        try { Invoke-Launcher "stop" @{ id = $id } | Out-Null } catch { }
    }
}

function Write-Load {
    # <stateDir>/load.json: the one number briefs can read before a heavy run.
    try {
        $cpu = $null
        if ((Get-HandoffLinkType) -eq "Junction") {
            $cpu = [double](Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
        }
        elseif (Test-Path "/proc/loadavg") {
            $la = [double](((Get-Content /proc/loadavg -Raw) -split " ")[0])
            $cpu = [math]::Round(100.0 * $la / [math]::Max(1, [Environment]::ProcessorCount), 1)
        }
        $st = Read-HandoffState $stateFile
        $running = @()
        foreach ($k in @($st.Keys)) { $e = $st[$k]; if ($e -is [hashtable] -and $e.ContainsKey("status") -and $e["status"] -eq "running") { $running += $k } }
        @{ updated = (Get-Date -Format "s"); cpu_percent = $cpu; running_sessions = $running; lane = $LaneName } |
            ConvertTo-Json -Depth 4 | Set-Content -Path $loadFile -Encoding utf8
    }
    catch { }
}

function Remove-Worktree([string]$wt, [string]$branch) {
    if (Test-Path $wt) {
        # Unlink every junction first. `git worktree remove --force` on Windows
        # leaves a reparse point (and therefore the folder) behind, and the
        # junctions a session had were its links INTO the live data: five empty
        # folders each holding a live `data` junction is what one -Cleanup left
        # (2026-09-17). Removing the reparse point never touches its target.
        Get-ChildItem -Path $wt -Force -Directory -Recurse -Depth 1 -Attributes ReparsePoint -ErrorAction SilentlyContinue | ForEach-Object {
            cmd /c rmdir "$($_.FullName)" 2>&1 | Out-Null
            Log "unlinked $($_.FullName) (junction, target untouched)"
        }
        git -C $repo worktree remove --force $wt 2>&1 | Out-Null
        if (Test-Path $wt) {
            # whatever is left is not a link any more; only an empty folder is removed by us
            if (-not (Get-ChildItem -Path $wt -Force -ErrorAction SilentlyContinue)) { Remove-Item -Path $wt -Force -ErrorAction SilentlyContinue }
            else { Log "warning: $wt still holds files after removal; left in place" }
        }
    }
    git -C $repo worktree prune 2>$null
    if (git -C $repo branch --list $branch) { git -C $repo branch -d $branch 2>&1 | Out-Null }
    Remove-SerenaProjectRow $wt
}

function Remove-SerenaProjectRow([string]$wt) {
    # Serena appends a `projects:` row per activated worktree and never removes
    # one; a page of dead rows is what every unattended day used to leave.
    # NOT $home: PowerShell variable names are case-insensitive and $HOME is a
    # read-only automatic variable - assigning it threw "Cannot overwrite
    # variable HOME" and killed -Cleanup on its first real run (2026-09-08).
    $serenaHome = if ($env:SERENA_HOME) { $env:SERENA_HOME } else { Join-Path $HOME ".serena" }
    $cfgPath = Join-Path $serenaHome "serena_config.yml"
    if (-not (Test-Path $cfgPath)) { return }
    try {
        $lines = Get-Content $cfgPath
        $a = $wt.Replace('/', '\'); $b = $wt.Replace('\', '/')
        $kept = @($lines | Where-Object { ($_.Trim() -ne "- $a") -and ($_.Trim() -ne "- $b") })
        if ($kept.Count -ne $lines.Count) {
            Copy-Item $cfgPath "$cfgPath.bak-$(Get-Date -Format 'yyyyMMdd')" -ErrorAction SilentlyContinue
            $kept | Set-Content $cfgPath -Encoding utf8
            Log "pruned the Serena project row for $wt"
        }
    }
    catch { Log "could not prune the Serena project row for ${wt}: $($_.Exception.Message)" }
}

function Get-WorktreeChange([string]$wt) {
    # THE answer to "when did this worktree last change", for both the finished
    # test (quietMinutes) and the hung test (stallMinutes): @{ epoch; dirty }.
    # One owner, because two would drift and the two decisions would disagree
    # about the same worktree.
    #
    # The epoch is the newest of the last commit and the mtime of every file git
    # reports as changed or untracked (-uall, so a file written INTO an untracked
    # directory counts - a session that is editing but has not committed yet is
    # working, not hung). Ignored files are not listed and deliberately do not
    # count: a build artifact is not evidence that the agent is alive.
    $out = @{ epoch = 0; dirty = $false }
    if (-not $wt -or -not (Test-Path $wt)) { return $out }
    $last = Get-HandoffText (git -C $wt log -1 --format=%ct 2>$null)
    if ($last) { $out.epoch = [long]$last }
    $status = @(git -C $wt status --porcelain -uall 2>$null)
    $out.dirty = [bool]($status.Count)
    foreach ($line in $status) {
        if (-not $line -or $line.Length -le 3) { continue }
        # "XY path", or "XY old -> new" for a rename: the new name is the one
        # that was just written. Git quotes paths holding unusual characters.
        $p = $line.Substring(3)
        $arrow = $p.IndexOf(" -> ")
        if ($arrow -ge 0) { $p = $p.Substring($arrow + 4) }
        $p = $p.Trim().Trim('"')
        $full = Join-Path $wt $p
        try {
            if (Test-Path -LiteralPath $full) {
                # The cast MUST be parenthesised: a PowerShell type cast binds to
                # the whole postfix expression, so `[DateTimeOffset]$x.Foo()`
                # casts the RESULT of $x.Foo() and here threw "[System.DateTime]
                # does not contain a method named ToUnixTimeSeconds" straight
                # into the catch below - leaving every mtime unread and the stall
                # watchdog permanently, silently off.
                $mt = ([DateTimeOffset]((Get-Item -LiteralPath $full -Force).LastWriteTimeUtc)).ToUnixTimeSeconds()
                if ($mt -gt $out.epoch) { $out.epoch = $mt }
            }
        }
        catch { }
    }
    return $out
}

function Test-DoneQuiet([hashtable]$change, [string]$doneNotePath) {
    # The DONE note exists, the branch is clean, and nothing was committed for
    # quietMinutes: the session is finished whatever the agents view says.
    if (-not $doneNotePath -or -not (Test-Path $doneNotePath)) { return $false }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    return (Test-HandoffDoneQuiet $true ([long]$change.epoch) $now ([int]$cfg.quietMinutes) ([bool]$change.dirty))
}

function Get-NewestWriteEpoch([string[]]$paths) {
    # Newest mtime across the given files; 0 when none of them exists yet, which
    # Test-HandoffStalled reads as "unknown", never as silence.
    $newest = 0
    foreach ($p in @($paths)) {
        if (-not $p -or -not (Test-Path -LiteralPath $p)) { continue }
        try {
            # Parenthesised for the same reason as in Get-WorktreeChange: an
            # unparenthesised cast applies to the method call's result and throws.
            $e = ([DateTimeOffset]((Get-Item -LiteralPath $p -Force).LastWriteTimeUtc)).ToUnixTimeSeconds()
            if ($e -gt $newest) { $newest = $e }
        }
        catch { }
    }
    return $newest
}

function Get-TreeCpuSeconds([string]$id) {
    # Total processor time of the child AND every descendant, in seconds, or -1
    # when it cannot be read.
    #
    # This is the signal that keeps the stall watchdog from killing working
    # sessions. Measured 2026-09-12: an agy child with an empty stdout and an
    # unchanged worktree for 35 min was busy the whole time - its own stderr said
    # "root agent idle; waiting for 1 background task(s)", which means the ROOT
    # AGENT was blocked on an asynchronous terminal command it had started (a
    # pytest run), not that it had spawned a subagent: `agy agents` was empty and
    # no child conversation existed. The work was burning CPU in a DESCENDANT
    # PROCESS, so the tree, not the child, is what must be summed.
    #
    # -1 (unknown) for everything but a runner-managed launcher on Windows: a
    # `claude --bg` id is a short hex handle, not a pid, and Win32_Process is a
    # Windows facility. Unknown disables the watchdog for that session rather
    # than guessing - see Test-HandoffStalled.
    if (-not $id -or $id -notmatch '^\d+$') { return -1 }
    if ((Get-HandoffLinkType) -ne "Junction") { return -1 }
    $rootPid = [int]$id
    try { $procs = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, UserModeTime, KernelModeTime -ErrorAction Stop) }
    catch { return -1 }
    $cpu = @{}; $kids = @{}
    foreach ($p in $procs) {
        $cpu[[int]$p.ProcessId] = ([double]$p.UserModeTime + [double]$p.KernelModeTime) / 1e7   # 100 ns units
        $pp = [int]$p.ParentProcessId
        if (-not $kids.ContainsKey($pp)) { $kids[$pp] = @() }
        $kids[$pp] += [int]$p.ProcessId
    }
    # A pid that is gone is not an idle pid: report unknown so the caller's
    # "process has exited" branch decides, not the watchdog.
    if (-not $cpu.ContainsKey($rootPid)) { return -1 }
    $total = 0.0
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($rootPid)
    $seen = @{}
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        if ($seen.ContainsKey($cur)) { continue }
        $seen[$cur] = $true
        if ($cpu.ContainsKey($cur)) { $total += $cpu[$cur] }
        if ($kids.ContainsKey($cur)) { foreach ($c in $kids[$cur]) { $queue.Enqueue($c) } }
    }
    return $total
}

function Test-Stalled([hashtable]$change, [string]$doneNotePath, [string]$logPath, [long]$cpuEpoch) {
    # Hung, not finished and not working: see Test-HandoffStalled for the two
    # measurements that set this shape.
    if ([int]$cfg.stallMinutes -le 0) { return $false }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $done = [bool]($doneNotePath -and (Test-Path $doneNotePath))
    # STDOUT IS NOT A LIFE SIGN for a runner-managed print-mode launcher. Measured
    # 2026-09-12: agy under --output-format json creates its stdout file at 0
    # bytes and writes the whole 92 KB transcript once, at turn end (08:43:48 ->
    # 09:40:13). Its mtime is therefore the LAUNCH time for the entire turn and
    # says nothing about the last hour, so only stderr, the worktree and the
    # process tree may count. Everything else has a stdout log that is appended
    # as it goes, and that is the only log it has.
    $isRunnerManaged = ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed")
    $logFiles = if ($isRunnerManaged) { @("$logPath.err") } else { @($logPath, "$logPath.err") }
    $logEpoch = Get-NewestWriteEpoch $logFiles
    return (Test-HandoffStalled $logEpoch ([long]$change.epoch) $cpuEpoch $done $now ([int]$cfg.stallMinutes))
}

function Wait-Bg([string]$id, [datetime]$deadline, [string]$wt = "", [string]$doneNotePath = "", [string]$logPath = "") {
    # "working" while the session is in a turn; anything else (idle, done, gone)
    # means the turn ended and the runner may look at what it produced. A
    # session that wrote its DONE note and went quiet is finished even if the
    # agents view still says "working" (measured: three hours of that, twice).
    # A session whose log, worktree AND process tree have all gone quiet without
    # a DONE note is HUNG, and returns "stalled" - the case quietMinutes cannot
    # see, because it only ever closes a session that already finished.
    $missing = 0
    $isRunnerManaged = ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed")
    # CPU is a RATE, so it is tracked as "when did this tree last do any work":
    # the epoch moves every time the tree's total processor time goes up, and a
    # tree the runner cannot read (no pid, not Windows) keeps the epoch at 0,
    # which Test-HandoffStalled treats as unknown and never as silence.
    $cpuSeen = Get-TreeCpuSeconds $id
    $cpuEpoch = if ($cpuSeen -ge 0) { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } else { 0 }
    while ((Get-Date) -lt $deadline) {
        Write-Load
        $change = Get-WorktreeChange $wt
        if ($wt -and (Test-DoneQuiet $change $doneNotePath)) { return "done-note" }
        $cpuNow = Get-TreeCpuSeconds $id
        if ($cpuNow -ge 0 -and $cpuNow -gt $cpuSeen) { $cpuSeen = $cpuNow; $cpuEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
        if ($wt -and (Test-Stalled $change $doneNotePath $logPath $cpuEpoch)) { return "stalled" }
        if ($isRunnerManaged) {
            $p = try { Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue } catch { $null }
            if (-not $p -or $p.HasExited) { return "done" }
            Start-Sleep -Seconds $cfg.pollSeconds
            continue
        }
        $e = Get-BgEntry $id
        if (-not $e) {
            $missing++
            # Two consecutive absences, so ONE failed `claude agents` call is not
            # read as a finished session.
            if ($missing -ge 2) { return "gone" }
        }
        else {
            $missing = 0
            if ($e.state -and $e.state -ne $cfg.launcher.workingState) { return [string]$e.state }
        }
        Start-Sleep -Seconds $cfg.pollSeconds
    }
    return "timeout"
}

function Run-Session([string]$s, [bool]$isFollowUpRun) {
    # NOT named $followUp: PowerShell variable names are case-insensitive, so a
    # local $followUp IS the [string]$FollowUp parameter, and assigning a bool to
    # it silently became the string "False" — which then failed to bind here.
    $info = $cfg.sessions[$s]
    # One session, one agent. Lanes run in their own process and sequentially inside themselves,
    # so swapping the resolved adapter into $cfg here leaves every call site below unchanged.
    $cfg["launcher"] = Resolve-HandoffSessionLauncher $cfg $s $defaultLauncher
    $script:launcherExecutable = if (Get-Command Resolve-AdapterExecutable -ErrorAction SilentlyContinue) {
        Resolve-AdapterExecutable $cfg.launcher.command
    } else { $cfg.launcher.command }
    # Vars and brief come from HandoffCore, the same path -EmitBriefs uses, so a
    # brief pasted by hand is byte-identical to the one the runner launches.
    $vars = Get-HandoffSessionVars $cfg $s $isFollowUpRun
    $wt = $vars["worktree"]; $branch = $vars["branch"]; $key = $vars["key"]

    $state = Read-HandoffState $stateFile
    if ($state.ContainsKey($key) -and $state[$key]["status"] -eq "merged" -and -not $Fresh) {
        Log "session $key already merged; skipping"; return $true
    }
    Log "=== session $key ($($info.name)) on $branch in $wt, model $($info.model)"
    # Cross-lane dependencies: wait until every listed session has merged, so
    # the worktree cut below forks from a base branch that already carries
    # their work. A dependency that ends without merging stops this lane.
    $deps = @($info["dependsOn"])
    if ($deps.Count) {
        if ($DryRun) { Log "dry run: $key would wait for $($deps -join ', ') to merge" }
        else {
            $depDeadline = (Get-Date).AddHours($cfg.maxDependencyHours)
            $announced = $false
            while ($true) {
                $ds = Get-HandoffDependencyState (Read-HandoffState $stateFile) $deps
                if ($ds.ready) { break }
                if (@($ds.failed).Count) {
                    Log "$key cannot start: dependency $(@($ds.failed) -join ', ') ended without merging"
                    Update-State $key @{ status = "dependency-failed"; last_error = "dependency failed: $(@($ds.failed) -join ', ')" }
                    return $false
                }
                if (-not $announced) {
                    Log "$key waiting for $(@($ds.waiting) -join ', ') to merge"
                    Update-State $key @{ status = "waiting"; waiting_for = (@($ds.waiting) -join ",") }
                    $announced = $true
                }
                if ((Get-Date) -gt $depDeadline) {
                    Log "$key gave up waiting for $(@($ds.waiting) -join ', ') after $($cfg.maxDependencyHours) h"
                    Update-State $key @{ status = "dependency-timeout" }; return $false
                }
                Start-Sleep -Seconds $cfg.pollSeconds
            }
            Log "$key dependencies merged: $($deps -join ', ')"
        }
    }
    # Operator control: an empty stop-<key> file in the queue directory ends
    # this lane before the session starts.
    # With a DONE note already in the worktree the marker means "never resume this
    # session again - go straight to the guards and the merge", exactly as it does
    # inside the session loop. The first version stopped the lane here regardless,
    # so an operator who had fixed a red branch by hand and relaunched the lane got
    # "stopped by the operator" instead of a guard run (measured 2026-09-05).
    $doneEarly = [bool]($vars["doneNote"] -and (Test-Path (Join-Path $wt $vars["doneNote"])))
    if ((Test-Path (Join-Path $queueDir "stop-$key")) -and -not $doneEarly) {
        Log "$key stopped by the operator's stop marker before starting"
        Update-State $key @{ status = "stopped-by-operator" }; return $false
    }
    # Merged elsewhere - by hand, or by another runner over the same state: an
    # existing branch whose tip is reachable from the base branch but OFF its
    # first-parent line (a --no-ff merge). A branch that merely fell behind the
    # base is an ancestor too, and sits ON that line - it is not merged.
    if (-not $Fresh -and -not $DryRun) {
        $tipB = Get-HandoffText (git -C $repo rev-parse --verify --quiet $branch 2>$null)
        $tipBase = Get-HandoffText (git -C $repo rev-parse --verify --quiet $cfg.baseBranch 2>$null)
        if ($tipB -and $tipBase -and $tipB -ne $tipBase) {
            git -C $repo merge-base --is-ancestor $branch $cfg.baseBranch 2>$null
            $isAncestor = ($LASTEXITCODE -eq 0)
            $firstParents = @((Get-HandoffText (git -C $repo rev-list --first-parent $cfg.baseBranch 2>$null)) -split "`n")
            if (Test-HandoffMergedElsewhere $tipB $tipBase $isAncestor $firstParents) {
                Log "$key ($branch) is already merged into $($cfg.baseBranch); skipping"
                Update-State $key @{ status = "merged"; finished_by = "merged-elsewhere"; merged_into = $tipBase }
                return $true
            }
        }
    }
    # Resource exclusion: never start while a RUNNING session holds a
    # conflicting resource (write excludes all; read excludes write).
    $wanted = @($info["resources"])
    if ($wanted.Count) {
        if ($DryRun) { Log "dry run: $key would hold $($wanted -join ', ') and wait for any running holder" }
        else {
            $announced = $false
            while ($true) {
                $st = Read-HandoffState $stateFile
                $held = @()
                foreach ($k in @($st.Keys)) {
                    if ($k -eq $key) { continue }
                    $e = $st[$k]
                    if ($e -is [hashtable] -and $e.ContainsKey("status") -and $e["status"] -eq "running" -and $e.ContainsKey("resources")) {
                        $held += , @{ key = $k; resources = @($e["resources"]) }
                    }
                }
                $c = @(Test-HandoffResourceConflict $held $wanted)
                if (-not $c.Count) { break }
                if (-not $announced) {
                    Log "$key waiting for resources held by $($c -join ', ') (wants $($wanted -join ', '))"
                    Update-State $key @{ status = "waiting-resource"; waiting_for = ($c -join ",") }
                    $announced = $true
                }
                Start-Sleep -Seconds $cfg.pollSeconds
            }
        }
    }
    if ($DryRun) {
        if ($info["guardsOnly"]) { Log "dry run: $key is guards-only; would cut $wt at $($cfg.baseBranch) and run the guards there" }
        $as = Get-HandoffWorktreeAssets $cfg $s
        Log "dry run: would create $wt (link: $($as.linkDirs -join ','); copy: $($as.copyDirs -join ','); files: $($as.copyFiles -join ',')) and run claude there"; return $true
    }

    Ensure-Worktree $wt $branch $vars (Get-HandoffWorktreeAssets $cfg $s)
    Update-State $key @{ status = "running"; worktree = $wt; branch = $branch; model = $info.model; resources = @($info["resources"]) }

    if ($info["guardsOnly"]) {
        # No agent: the pinned, idle final suite on a worktree at the merged HEAD.
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $guard = Build-HandoffGuardCommand $cfg $vars
        if (-not $guard) { Log "$key is guards-only but no guards are configured; nothing to do"; Update-State $key @{ status = "guards-green" }; return $true }
        $guardLog = Join-Path $stateDir "session-$key-$stamp-guards.txt"
        Log "$key is guards-only: running the guards on $wt (cut at $($cfg.baseBranch)'s HEAD), no agent launched"
        Push-Location $wt
        try { & $guard.command @($guard.args) 2>&1 | Out-File -Encoding utf8 $guardLog; $green = ($LASTEXITCODE -eq 0) }
        finally { Pop-Location }
        $baseSha = Get-HandoffText (git -C $repo rev-parse HEAD 2>$null)
        Log "guards $(if ($green) { 'GREEN' } else { 'RED' }) at $baseSha`: $guardLog"
        Update-State $key @{ status = $(if ($green) { "guards-green" } else { "guards-red" }); guard_log = $guardLog; base_sha = $baseSha }
        return $green
    }

    $doneNote = Join-Path $wt $vars["doneNote"]
    $sessionId = $null
    if (-not $Fresh -and $state.ContainsKey($key) -and $state[$key]["session_id"]) { $sessionId = $state[$key]["session_id"] }
    $continues = 0; $retries = 0
    $brief = Build-HandoffBrief $cfg $s $vars
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    # Why the next resume happens. A session id recorded by an EARLIER runner
    # process means that process died (host restart, runner killed) - the one
    # case where the session's subagents are gone without any failure in its log.
    $resumeReason = if ($sessionId) { "restart" } else { "" }

    while ($true) {
        if (Test-Path (Join-Path $queueDir "stop-$key")) {
            # The operator says this session is done: never resume it again.
            if (Test-Path $doneNote) { Log "$key has the operator's stop marker and a DONE note; proceeding to the guards"; break }
            Log "$key stopped by the operator's stop marker; lane stops"
            Update-State $key @{ status = "stopped-by-operator" }; return $false
        }
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $log = Join-Path $stateDir "session-$key-$stamp.log"
        # ARGUMENT ORDER MATTERS: `--allowedTools <tools...>` is variadic and
        # swallows everything up to the next flag, so a prompt placed after it
        # becomes another "tool name" and the session starts with NO task. The
        # tool list therefore comes first and the prompt last, after a flag that
        # takes exactly one value.
        if ($sessionId) {
            $action = "resume"
            $lvars = @{
                sessionId = $sessionId; model = $info.model; rcName = $vars["rcName"]
                prompt    = (Build-HandoffResumePrompt -Reason $resumeReason -DoneNote $vars["doneNote"])
            }
            Log "resuming $key (session $sessionId, reason: $(if ($resumeReason) { $resumeReason } else { 'early stop' }))"
        }
        else {
            $action = "start"
            # The remote-control name gives the session a stable handle an operator
            # can join from anywhere, so an unattended run stays interactive on
            # demand rather than being a black box until morning.
            $lvars = @{ rcName = $vars["rcName"]; model = $info.model; prompt = $brief }
            Log "starting $key as a background session named $($vars['rcName'])"
        }
        $bgId = Start-Bg $wt $action $lvars $log
        if (-not $bgId) {
            $retries++
            $f = Classify-HandoffFailure (Get-Content $log -Raw)
            Update-State $key @{ last_error = "could not start a background session"; retries = $retries; failure_kind = $f.kind }
            Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                sessionId     = $key
                turnId        = $continues + 1
                kind          = "error"
                correlationId = $sessionId
                data          = @{ error = "could not start background session"; failureKind = $f.kind; retries = $retries }
            } | Out-Null
            if ($f.kind -eq "auth") {
                Log "AUTHENTICATION FAILED for $key — check agent authentication, then re-run this script"
                Update-State $key @{ status = "auth-failed" }; return $false
            }
            if ($retries -gt $cfg.maxRetries) { Log "giving up on $key after $retries retries"; Update-State $key @{ status = "failed" }; return $false }
            Start-Sleep -Seconds ([math]::Max(60, $f.wait)); continue
        }
        $entry = Get-BgEntry $bgId
        # Get-BgEntry returns a hashtable for runner-managed launchers but a
        # PSCustomObject (ConvertFrom-Json | Select-Object) for `claude agents --json`,
        # which has no ContainsKey (measured 2026-09-10: every lane crashed here).
        $entrySid = $null
        if ($entry) {
            if ($entry -is [hashtable]) { if ($entry.ContainsKey("sessionId")) { $entrySid = $entry["sessionId"] } }
            elseif ($entry.PSObject.Properties["sessionId"]) { $entrySid = $entry.sessionId }
        }
        if ($entrySid) { $sessionId = [string]$entrySid }
        $isRunnerManaged = ($cfg.launcher.ContainsKey("backgroundMode") -and $cfg.launcher.backgroundMode -eq "runner-managed")
        $attachCmd = if ($isRunnerManaged) {
            if ($cfg.launcher.command -eq "agy") { "agy --conversation $(if ($sessionId) { $sessionId } else { '<sessionId>' })" }
            elseif ($cfg.launcher.command -eq "grok") { "grok --resume $(if ($sessionId) { $sessionId } else { '<sessionId>' })" }
            elseif ($cfg.launcher.command -eq "codewhale") { "codewhale exec --resume $(if ($sessionId) { $sessionId } else { '<sessionId>' })" }
            else { "$($cfg.launcher.command) --resume $(if ($sessionId) { $sessionId } else { '<sessionId>' })" }
        } else {
            "$($cfg.launcher.command) attach $bgId"
        }
        $pidVal = if ($isRunnerManaged) { [int]$bgId } else { $null }
        Update-State $key @{ bg_id = $bgId; pid = $pidVal; session_id = $sessionId; attach = $attachCmd }
        Log "$key is background session $bgId (join it: $attachCmd)"

        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $key
            turnId        = $continues + 1
            kind          = "session_started"
            correlationId = $sessionId
            data          = @{
                action    = $action
                model     = $info.model
                worktree  = $wt
                branch    = $branch
                bgId      = $bgId
                pid       = $pidVal
            }
        } | Out-Null

        $ended = Wait-Bg $bgId (Get-Date).AddHours($cfg.maxSessionHours) $wt $doneNote $log
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $key
            turnId        = $continues + 1
            kind          = "turn_ended"
            correlationId = $sessionId
            data          = @{
                ended     = $ended
                bgId      = $bgId
                sessionId = $sessionId
            }
        } | Out-Null

        if ($ended -eq "stalled") {
            # Hung, not finished: nothing written, nothing changed, no CPU in its
            # process tree, no DONE note. Treated exactly as "stopped without its
            # DONE note" - the child is ended and the loop falls through to the
            # same read-the-log-and-classify path, so a stall that is really a
            # usage limit or an expired login is still recognised as one.
            $stallNow = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $prior = @()
            $stState = Read-HandoffState $stateFile
            if ($stState.ContainsKey($key) -and $stState[$key] -is [hashtable] -and $stState[$key].ContainsKey("stalls")) {
                $prior = @($stState[$key]["stalls"])
            }
            $stalls = @(@($prior) + $stallNow)
            Update-State $key @{ stalls = $stalls }
            Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                sessionId     = $key
                turnId        = $continues + 1
                kind          = "stalled"
                correlationId = $sessionId
                data          = @{ stallMinutes = $cfg.stallMinutes; stalls = @($stalls).Count; bgId = $bgId }
            } | Out-Null
            $win = $cfg.stallWindow
            if (Test-HandoffStallBudgetExceeded $stalls $stallNow ([int]$win.maxStalls) ([double]$win.hours)) {
                # The operator's rate rule: stalls this frequent are a general
                # problem, and a sixth resume would only spend another hour
                # proving it. The lane stops and the morning reader gets the
                # stall times in the state row.
                Log "$key stalled $($win.maxStalls) times within $($win.hours) h — a general problem, not a one-off; stopping the lane for the operator"
                Update-State $key @{ status = "stalled-repeatedly"; last_error = "stalled $(@($stalls).Count) times" }
                Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                    sessionId     = $key
                    turnId        = $continues + 1
                    kind          = "error"
                    correlationId = $sessionId
                    data          = @{ error = "stalled-repeatedly"; stalls = @($stalls).Count; withinHours = $win.hours }
                } | Out-Null
                # The hung child is ended even though the lane stops: it holds the
                # worktree folder and a machine slot until morning otherwise.
                Stop-BgSession $bgId
                return $false
            }
            Log "$key stalled: no log line, no worktree change and no CPU in its process tree for $($cfg.stallMinutes) min; ending the child and resuming ($($continues + 1)/$($cfg.maxContinues))"
            Stop-BgSession $bgId
        }
        if ($ended -eq "done-note" -or ($ended -eq "timeout" -and (Test-Path $doneNote))) {
            # Finished on the evidence that matters: the DONE note is there and
            # the branch has been quiet. Never resume a finished session.
            Log "$key finished by DONE note ($ended); stopping session $bgId and proceeding to the guards"
            Update-State $key @{ finished_by = $ended }
            Stop-BgSession $bgId
            break
        }
        $text = try {
            if ($isRunnerManaged) {
                if (Test-Path $log) { Get-Content $log -Raw } else { "" }
            } else {
                Invoke-Launcher "logs" @{ id = $bgId }
            }
        } catch { "" }
        if (-not $isRunnerManaged) {
            $text | Out-File -Append -Encoding utf8 $log
        }
        if ($isRunnerManaged -and -not $sessionId -and (Test-Path $log)) {
            $pr = Parse-Result $log
            if ($pr.session_id) {
                $sessionId = [string]$pr.session_id
                Update-State $key @{ session_id = $sessionId }
            }
        }
        $textStr = if ($text) { [string]$text } else { "" }
        $tail = if ($textStr.Length -gt 0) { $textStr.Substring([math]::Max(0, $textStr.Length - 4000)) } else { "" }
        Log "$key turn ended ($ended)"

        if (Test-HandoffPermissionPrompt $tail) {
            Log "BLOCKED: $key is waiting for a permission answer a background session cannot give."
            Log "  Read it with '$attachCmd'; the last lines are in $log"
            Update-State $key @{ status = "blocked"; last_error = "permission prompt"; bg_id = $bgId }
            Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                sessionId     = $key
                turnId        = $continues + 1
                kind          = "error"
                correlationId = $sessionId
                data          = @{ error = "permission prompt"; bgId = $bgId }
            } | Out-Null
            return $false
        }
        $f = Classify-HandoffFailure $tail
        if ($f.kind -ne "other" -or $ended -eq "timeout") {
            $retries++
            Update-State $key @{ last_error = $f.kind; retries = $retries; failure_kind = $f.kind }
            if ($f.kind -eq "auth") {
                Log "AUTHENTICATION FAILED for $key — check agent authentication, then re-run; nothing is lost"
                Update-State $key @{ status = "auth-failed" }
                Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                    sessionId     = $key
                    turnId        = $continues + 1
                    kind          = "error"
                    correlationId = $sessionId
                    data          = @{ error = "auth-failed"; failureKind = $f.kind }
                } | Out-Null
                return $false
            }
            if ($retries -gt $cfg.maxRetries) {
                Log "giving up on $key after $retries retries"
                Update-State $key @{ status = "failed" }
                Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                    sessionId     = $key
                    turnId        = $continues + 1
                    kind          = "error"
                    correlationId = $sessionId
                    data          = @{ error = "max-retries-exceeded"; retries = $retries }
                } | Out-Null
                return $false
            }
            # A turn has ended, so exactly one live session per handoff is kept:
            # the finished one is stopped before any resume, otherwise every
            # retry would leave another background session holding the same
            # conversation.
            Stop-BgSession $bgId
            if ($ended -eq "timeout") { Log "$key ran past $($cfg.maxSessionHours) h in one turn; stopped it, resuming"; $resumeReason = "timeout" }
            else { Log "$($f.kind) failure; waiting $([int]($f.wait/60)) min before resuming"; Start-Sleep -Seconds $f.wait; $resumeReason = $f.kind }
            Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                sessionId     = $key
                turnId        = $continues + 1
                kind          = "resumed"
                correlationId = $sessionId
                data          = @{ reason = $resumeReason; retries = $retries }
            } | Out-Null
            continue
        }
        if ($vars["doneNote"] -and (Test-Path $doneNote)) { Log "DONE note present for $key"; Update-State $key @{ finished_by = "turn-end" }; break }
        if (-not $vars["doneNote"]) { Log "no doneNote configured; treating a clean stop as done"; break }

        $continues++
        Update-State $key @{ continues = $continues }
        if ($continues -gt $cfg.maxContinues) { Log "$key stopped $continues times without a DONE note; proceeding to the guards anyway"; break }
        Log "$key stopped without its DONE note; stopping session $bgId and resuming in 2 min ($continues/$($cfg.maxContinues))"
        Stop-BgSession $bgId
        $resumeReason = ""
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $key
            turnId        = $continues + 1
            kind          = "resumed"
            correlationId = $sessionId
            data          = @{ reason = "continuation"; continues = $continues }
        } | Out-Null
        Start-Sleep -Seconds 120
    }

    # Anything the session left uncommitted in its worktree - its DONE note,
    # typically - would vanish: the worktree is removed with --force after the
    # merge, and by -Cleanup on a no-merge or red lane (session AS2, 2026-09-17:
    # the note existed, was seen, and was lost). Committed before the guards so
    # the branch they judge is the branch that is merged or left for review.
    if ($wt -and (Test-Path $wt) -and (git -C $wt status --porcelain 2>$null)) {
        git -C $wt add -A 2>&1 | Out-File -Append -Encoding utf8 $runnerLog
        git -C $wt commit -q -m "docs(handoff): session $key files left uncommitted, committed by the runner before the merge" 2>&1 | Out-File -Append -Encoding utf8 $runnerLog
        Log "committed $key's uncommitted files before the guards"
    }

    $guard = Build-HandoffGuardCommand $cfg $vars
    if ($guard) {
        $guardLog = Join-Path $stateDir "session-$key-$stamp-guards.txt"
        Push-Location $wt
        try {
            # Redirected to a file, never piped through grep/tail: a piped
            # long-running command buffers, and a traceback is lost.
            & $guard.command @($guard.args) 2>&1 | Out-File -Encoding utf8 $guardLog
            $green = ($LASTEXITCODE -eq 0)
        }
        finally { Pop-Location }
        Log "guards $(if ($green) { 'GREEN' } else { 'RED' }): $guardLog"
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $key
            turnId        = $continues + 1
            kind          = "guards_evaluated"
            correlationId = $sessionId
            data          = @{ green = $green; guardLog = $guardLog }
        } | Out-Null
        if (-not $green) { Update-State $key @{ status = "guards-red" }; Log "lane stops: $branch is red; read the guard log and the DONE note"; return $false }
    }
    else { Log "no guards configured; skipping straight to the merge decision" }

    if ($NoMerge -or $cfg.ContainsKey("noMerge") -and $cfg.noMerge) {
        Update-State $key @{ status = "done-unmerged" }; Log "no-merge: $branch left for review"; return $true
    }
    # The result is taken from Invoke-WithLock's RETURN VALUE, never assigned to
    # an outer variable inside the scriptblock: such an assignment writes a
    # DIFFERENT variable from the one read afterwards, and that bug reported every
    # successful merge as a conflict and stopped its lane.
    $merged = Invoke-WithLock {
        git -C $repo merge --no-ff $branch -m "merge $branch (session $key, unattended run $stamp)" 2>&1 | Out-File -Append -Encoding utf8 $runnerLog
        $ok = ($LASTEXITCODE -eq 0)
        if (-not $ok) { git -C $repo merge --abort 2>$null }
        $ok
    }
    if (-not $merged) {
        Update-State $key @{ status = "merge-conflict" }
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $key
            turnId        = $continues + 1
            kind          = "error"
            correlationId = $sessionId
            data          = @{ error = "merge-conflict"; branch = $branch }
        } | Out-Null
        Log "lane stops: merging $branch into $($cfg.baseBranch) conflicted; resolve by hand"
        return $false
    }
    $mergedSha = Get-HandoffText (git -C $repo rev-parse HEAD 2>$null)
    $refusals = 0
    if (Test-Path $doneNote) { $refusals = Get-HandoffRefusalCount (Get-Content $doneNote -Raw) }
    Update-State $key @{ status = "merged"; merged_into = $mergedSha; refusals = $refusals }
    Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
        sessionId     = $key
        turnId        = $continues + 1
        kind          = "merged"
        correlationId = $sessionId
        data          = @{ mergedInto = $mergedSha; refusals = $refusals; branch = $branch }
    } | Out-Null
    Log "merged $branch into $($cfg.baseBranch) ($mergedSha)"
    # The session is done and its process still holds the worktree folder;
    if ($cfg.stopAfterMerge -and $bgId) { Stop-BgSession $bgId; Log "stopped finished session $bgId" }
    try {
        $archPath = Archive-HandoffSession $cfg $key $vars -LedgerPath $ledgerFile -StatePath $stateFile -CorrelationId $sessionId
        Log "archived $key artifacts to $archPath"
    } catch {
        Log "warning: could not archive $key artifacts: $($_.Exception.Message)"
    }
    if ($cfg.removeWorktreeAfterMerge) { Remove-Worktree $wt $branch; Log "removed worktree $wt and branch $branch" }
    return $true
}

function Preflight {
    $problems = @()
    if (-not (Get-Command $launcherExecutable -ErrorAction SilentlyContinue)) { $problems += "launcher '$($cfg.launcher.command)' not on PATH" }
    elseif (-not $DryRun) {
        # A one-word headless call: the cheapest proof that the CLI's own login
        # is alive. An expired OAuth session fails every session instantly,
        # which is not something to discover at 03:00.
        # Per-process file: two runners starting in the same second (a queued lane
        # and a relaunch) both wrote preflight-auth.json and one died with "cannot
        # access the file because it is being used by another process" - before
        # logging a line, so the lane simply never existed (measured 2026-09-05).
        $probe = Join-Path $stateDir "preflight-auth-$PID.json"
        Push-Location $repo
        try { Invoke-Launcher "probe" @{ prompt = "Reply with exactly the word OK."; probeModel = $cfg.launcher.probeModel } | Out-File -Encoding utf8 $probe }
        finally { Pop-Location }
        $r = Parse-Result $probe
        if ($r.is_error) { $problems += "launcher probe failed: $($r.result) - check the agent is authenticated (e.g. 'claude login')" }
        else { Log "preflight: launcher answered (session $($r.session_id))" }
    }
    if (-not (Test-Path $repo)) { $problems += "repo does not exist: $repo" }
    foreach ($p in @($cfg.requirePaths)) {
        if (-not $p) { continue }
        $full = if ([System.IO.Path]::IsPathRooted($p)) { $p } else { Join-Path $repo $p }
        if (-not (Test-Path $full)) { $problems += "required path missing: $full" }
    }
    $branch = git -C $repo rev-parse --abbrev-ref HEAD
    if ($branch -ne $cfg.baseBranch) { $problems += "main checkout is on '$branch', not $($cfg.baseBranch)" }
    if (git -C $repo status --porcelain) { Log "warning: the main checkout has uncommitted changes; sessions fork from $($cfg.baseBranch)'s HEAD, not from them" }
    # Secrets stay in the main checkout and are passed to children as env, never
    # copied into a worktree.
    $localSettings = Join-Path $repo $cfg.envFrom
    if (Test-Path $localSettings) {
        $local = Get-Content $localSettings -Raw | ConvertFrom-Json
        if ($local.PSObject.Properties.Name -contains "env" -and $local.env) {
            foreach ($p in $local.env.PSObject.Properties) { Set-Item -Path "env:$($p.Name)" -Value $p.Value }
        }
    }
    if ($problems) { throw ("preflight failed: " + ($problems -join "; ")) }
}

# ---------------------------------------------------------------------------
if ($Status) {
    # The morning (or the controller's) one-glance view of state.json, so
    # nobody has to read raw JSON to learn which lane stopped and why.
    $state = Read-HandoffState $stateFile
    if (-not @($state.Keys).Count) { Write-Host "no state yet at $stateFile"; exit 0 }
    $rows = @()
    foreach ($k in @($state.Keys | Sort-Object)) {
        $e = $state[$k]
        if ($e -isnot [hashtable]) { continue }
        # Refusals are recomputed from the DONE note on every -Status, never read
        # back from the state file: the counter's rule changed on 2026-09-17 and a
        # stale 34 sat beside a note that said nothing was refused. A session with
        # no note yet shows a blank, which is the truth at that moment.
        $refusals = $null
        try {
            $vars = if ($cfg.sessions.ContainsKey($k)) { Get-HandoffSessionVars $cfg $k $false } else { $null }
            $rel = if ($vars -and $vars.ContainsKey("doneNote")) { [string]$vars["doneNote"] } else { $null }
            # the note lives in the worktree while the session runs and on the base
            # branch in the main checkout once it has merged and been cleaned up
            $note = $null
            if ($rel) {
                foreach ($base in @($e["worktree"], $vars["worktree"], $repo)) {
                    if ($base -and (Test-Path (Join-Path $base $rel))) { $note = Join-Path $base $rel; break }
                }
            }
            if ($env:HANDOFF_DEBUG) { Write-Host "status: $k note=$note (rel=$rel)" }
            if ($note) { $refusals = Get-HandoffRefusalCount (Get-Content $note -Raw) }
            elseif ($e.ContainsKey("refusals")) { $refusals = "$($e["refusals"])?" }   # stale: no note found to recount
        } catch { $refusals = "$($e["refusals"])?"; Write-Host "status: $k refusals not recounted: $($_.Exception.Message)" }
        # last_error outlives the state it described; show it only while the session is not settled
        $err = if ($e["status"] -in @("merged", "guards-green", "done")) { $null } else { $e["last_error"] }
        $rows += [pscustomobject]@{
            session = $k; status = $e["status"]; finished_by = $e["finished_by"]; bg = $e["bg_id"]
            branch = $e["branch"]; refusals = $refusals; updated = $e["updated"]; error = $err
        }
    }
    Write-Host "state: $stateFile   (refusals recounted from each DONE note; a trailing ? means no note was found and the stored value is shown)"
    if (Test-Path $ledgerFile) { Write-Host "ledger: $ledgerFile" }
    $rows | Format-Table -AutoSize | Out-String -Width 240 | Write-Host
    exit 0
}

if ($Validate) {
    Write-Host "config OK: $($cfg.configPath)"
    Write-Host "  repo:      $repo"
    Write-Host "  base:      $($cfg.baseBranch)   branches: $($cfg.branchPrefix)/<name>"
    Write-Host "  worktrees: $($cfg.worktreeParent)\$($cfg.worktreePrefix)-<name>"
    Write-Host "  sessions:  $(($cfg.sessions.Keys | Sort-Object) -join ', ')"
    Write-Host "  lanes:     $(($cfg.lanes | ForEach-Object { $_ -join ',' }) -join '  |  ')"
    foreach ($k in ($cfg.sessions.Keys | Sort-Object)) {
        $dd = @($cfg.sessions[$k]["dependsOn"])
        if ($dd.Count) { Write-Host "  dependsOn: $k waits for $($dd -join ', ')" }
        $rr = @($cfg.sessions[$k]["resources"])
        if ($rr.Count) { Write-Host "  resources: $k holds $($rr -join ', ')" }
        if ($cfg.sessions[$k]["guardsOnly"]) { Write-Host "  guardsOnly: $k launches no agent" }
        $sl = Resolve-HandoffSessionLauncher $cfg $k $cfg.launcher
        if ($sl.command -ne $cfg.launcher.command) {
            $slPath = if (Get-Command Resolve-AdapterExecutable -ErrorAction SilentlyContinue) { Resolve-AdapterExecutable $sl.command } else { $sl.command }
            $slOn = if (Get-Command $slPath -ErrorAction SilentlyContinue) { "on PATH" } else { "NOT ON PATH" }
            Write-Host "  launcher:  $k drives $($sl.command) ($slOn)"
        }
        $own = @($cfg.sessions[$k]["linkDirs"]) + @($cfg.sessions[$k]["copyDirs"]) + @($cfg.sessions[$k]["copyFiles"])
        if (@($own | Where-Object { $_ }).Count) {
            $as = Get-HandoffWorktreeAssets $cfg $k
            Write-Host "  assets:    $k links [$($as.linkDirs -join ', ')] copies [$($as.copyDirs -join ', ')] files [$($as.copyFiles -join ', ')]"
        }
    }
    # Printed because a watchdog that ends children is not something to commit a
    # night to without seeing its two numbers first.
    Write-Host "  stall:     $(if ([int]$cfg.stallMinutes -le 0) { 'off (stallMinutes 0)' } else { "$($cfg.stallMinutes) min quiet (log, worktree and process tree) -> end the child and resume; $($cfg.stallWindow.maxStalls) stalls within $($cfg.stallWindow.hours) h stops the lane" })"
    Write-Host "  queue:     $queueDir   load: $loadFile"
    Write-Host "  guards:    $(if (Build-HandoffGuardCommand $cfg @{}) { 'configured' } else { 'none' })"
    Write-Host "  subagents: $(if (Build-HandoffSubagentPolicy $cfg) { 'policy configured' } else { 'none' })"
    # Surfaced because a repo driving a different agent has no other way to
    # confirm the swap took before committing a night to it.
    $onPath = if (Get-Command $launcherExecutable -ErrorAction SilentlyContinue) { "on PATH" } else { "NOT ON PATH" }
    Write-Host "  launcher:  $($cfg.launcher.command) ($onPath)"
    exit 0
}

if ($Cleanup) {
    # Everything a finished day leaves behind: the done sessions' processes
    # (still holding their worktree folders), archives artifacts, removes worktrees,
    # deletes branches, and prunes Serena rows. Only MERGED sessions are touched;
    # unmerged sessions left for review keep their worktrees.
    # Idempotent and re-entrant: can be run repeatedly without error.
    $state = Read-HandoffState $stateFile
    foreach ($k in @($state.Keys | Sort-Object)) {
        $e = $state[$k]
        if ($e -isnot [hashtable] -or -not $e.ContainsKey("status") -or $e["status"] -ne "merged") { continue }
        if ($e.ContainsKey("bg_id") -and $e["bg_id"]) { Stop-BgSession [string]$e["bg_id"] }
        $vars = if ($cfg.sessions.ContainsKey($k)) { Get-HandoffSessionVars $cfg $k $false } else { @{ worktree = $e["worktree"]; branch = $e["branch"] } }
        if (-not ($e.ContainsKey("archived") -and $e["archived"])) {
            try {
                $archPath = Archive-HandoffSession $cfg $k $vars -LedgerPath $ledgerFile -StatePath $stateFile
                Log "cleanup: archived $k artifacts to $archPath"
            } catch {
                Log "cleanup: warning: could not archive $k artifacts: $($_.Exception.Message)"
            }
        }
        $wt = if ($e.ContainsKey("worktree")) { [string]$e["worktree"] } else { [string]$vars["worktree"] }
        $br = if ($e.ContainsKey("branch")) { [string]$e["branch"] } else { [string]$vars["branch"] }
        if ($wt -and $br) { Remove-Worktree $wt $br; Log "cleanup: $k - removed $wt and $br" }
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = $k
            turnId        = 0
            kind          = "cleaned"
            correlationId = if ($e.ContainsKey("session_id")) { [string]$e["session_id"] } else { $null }
            data          = @{ worktree = $wt; branch = $br }
        } | Out-Null
        Update-State $k @{ cleaned = (Get-Date -Format "s") }
    }
    Log "cleanup finished"
    exit 0
}

if ($EmitBriefs) {
    # Render every brief WITHOUT launching anything, so one config serves both an
    # unattended run and a human pasting each brief into an interactive session.
    # Same code path as a real launch — a copy-pasted brief that differs from the
    # launched one is worse than no brief at all.
    $md = Export-HandoffBriefs $cfg
    if ($OutFile) {
        $dir = Split-Path $OutFile -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $md | Set-Content -Path $OutFile -Encoding utf8
        Write-Host "wrote $((($md -split "`n").Count)) lines to $OutFile"
    }
    else { Write-Output $md }
    exit 0
}

if ($WaitUntil) {
    # Quotes survive Start-Process (it joins the argument array with spaces and
    # does not quote), and an unparseable value must not kill the runner: a
    # follow-up run was once scheduled twice and never ran, because
    # `-WaitUntil 2026-09-04 09:45` arrived as two arguments and ParseExact threw
    # under ErrorActionPreference Stop.
    $waitAt = $null
    try { $waitAt = [datetime]::ParseExact($WaitUntil.Trim('"', " "), "yyyy-MM-dd HH:mm", $null) }
    catch { Log "could not parse -WaitUntil '$WaitUntil' (expected 'yyyy-MM-dd HH:mm'); starting now instead" }
    if ($waitAt) {
        $delay = ($waitAt - (Get-Date)).TotalSeconds
        if ($delay -gt 0) { Log "waiting until $($waitAt.ToString('yyyy-MM-dd HH:mm')) ($([int]($delay/60)) min)"; Start-Sleep -Seconds ([int]$delay) }
    }
}

# Only the PARENT reconciles. A lane child that reconciles at its own startup races
# every other lane's resume: the old session id is dead, the new one is not yet
# registered, and the dependent lane stops on a false 'ended without merging'
# (measured 2026-09-11 20:03: lane S stopped while lane C was mid-resume).
if (-not $DryRun -and -not $LaneName) {
    Invoke-WithLock {
        $aliveIds = $null
        try {
            $listed = (Invoke-Launcher "list" @{} | ConvertFrom-Json)
            $aliveIds = @($listed | ForEach-Object { [string]$_.id } | Where-Object { $_ })
        } catch { $aliveIds = $null }
        $reconciled = Reconcile-HandoffLedger -LedgerPath $ledgerFile -StatePath $stateFile -AliveBgIds $aliveIds
        if ($reconciled -and $reconciled.Count -gt 0) {
            Log "reconciled $($reconciled.Count) crashed session(s): $($reconciled -join ', ')"
        }
    }
}

Preflight

# -Sessions collapses everything to one lane; -Lanes overrides the config's.
# Built with an explicit loop and `+= ,`: assigning an if/else EXPRESSION to a
# variable sends its value through the output stream, which unrolls one array
# level — so a single lane of ["X","Y"] arrived here as two lanes of one, and
# every sequential lane silently ran in parallel.
$runLanes = @()
if ($Sessions.Count -gt 0) {
    $runLanes += , @($Sessions | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
elseif ($Lanes.Count -gt 0) {
    # A lane list may arrive as ONE string with `;` between lanes: `pwsh -File` (and
    # therefore Start-Process) hands an array argument to the script as separate
    # positional tokens, and PowerShell then binds the second lane to -Sessions, the
    # third to -OutFile and the fourth to -WaitUntil - measured 2026-09-05, when
    # `-Lanes 'A1,A2,A3' 'L1,L2,L3,L4' 'D1,D2' 'FINAL'` ran lane L alone in-process
    # and logged "could not parse -WaitUntil 'FINAL'". `-Lanes 'A1,A2,A3;L1,L2;FINAL'`
    # is one token everywhere.
    foreach ($group in $Lanes) {
        foreach ($l in ($group -split ";")) {
            $items = @($l -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            if ($items.Count) { $runLanes += , $items }
        }
    }
}
else {
    foreach ($l in $cfg.lanes) { $runLanes += , @($l) }
}

if ($FollowUp) {
    $fus = if ($FollowUpSessions.Count) { $FollowUpSessions } else { @($runLanes[0][0]) }
    $childArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Config", $cfg.configPath, "-Sessions", ($fus -join ","), "-Fresh",
        "-WaitUntil", $FollowUp, "-LaneName", "followup")
    if ($NoMerge) { $childArgs += "-NoMerge" }
    Start-Process -FilePath $shell -ArgumentList $childArgs -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $stateDir "lane-followup.out.log") `
        -RedirectStandardError (Join-Path $stateDir "lane-followup.err.log") | Out-Null
    Log "follow-up run of $($fus -join ',') scheduled for $FollowUp (lane-followup.out.log)"
}

if ($runLanes.Count -le 1) {
    if (-not $LaneName) { $LaneName = ($runLanes[0] -join ",") }
    $isFollowUpRun = [bool]$Fresh -and [bool]$WaitUntil
    foreach ($s in $runLanes[0]) {
        if (-not $cfg.sessions.ContainsKey($s)) { Log "unknown session '$s'; skipping"; continue }
        try {
            if (-not (Run-Session $s $isFollowUpRun)) { Log "lane [$LaneName] stopped at session $s"; break }
        }
        catch {
            Log "lane [$LaneName] crashed at session ${s}: $($_.Exception.Message)"
            Update-State $s @{ status = "crashed"; last_error = $_.Exception.Message }
            try {
                Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
                    sessionId = $s
                    kind      = "crashed"
                    data      = @{ error = $_.Exception.Message; lane = $LaneName }
                } | Out-Null
            } catch { }
            break
        }
    }
    Log "lane [$LaneName] finished"
}
else {
    # Each lane in its own process so the sessions run concurrently; this script
    # re-invokes itself with -Sessions for one lane at a time.
    $procs = @(); $n = 0
    foreach ($lane in $runLanes) {
        $n++
        # One comma-joined value: passed as separate tokens, PowerShell binds
        # only the first to -Sessions and the lane silently shrinks to one
        # session.
        $childArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
            "-Config", $cfg.configPath, "-Sessions", ($lane -join ","), "-LaneName", "lane$n")
        if ($NoMerge) { $childArgs += "-NoMerge" }
        if ($DryRun) { $childArgs += "-DryRun" }
        if ($Fresh) { $childArgs += "-Fresh" }
        Log "lane [$($lane -join ',')] starting as lane$n"
        $procs += Start-Process -FilePath $shell -ArgumentList $childArgs -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $stateDir "lane$n.out.log") `
            -RedirectStandardError (Join-Path $stateDir "lane$n.err.log")
        Start-Sleep -Seconds 30
    }
    $reported = @()
    # While lanes run, the queue directory is the operator's way in: a
    # lane-<name>.json ({"sessions": ["F9"]}) starts another lane child, which
    # re-reads the config - so a session added to the config after this runner
    # started is launchable without a second runner over the same state file.
    while ($true) {
        foreach ($qf in @(Get-ChildItem $queueDir -Filter "lane-*.json" -ErrorAction SilentlyContinue)) {
            try {
                $spec = ConvertTo-HandoffHash (Get-Content $qf.FullName -Raw | ConvertFrom-Json)
                $sessions = @(@($spec["sessions"]) | Where-Object { $_ })
                if (-not $sessions.Count) { throw "no sessions listed" }
                $n++
                $childArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
                    "-Config", $cfg.configPath, "-Sessions", ($sessions -join ","), "-LaneName", "lane$n")
                if ($NoMerge) { $childArgs += "-NoMerge" }
                Log "lane [$($sessions -join ',')] queued by the operator ($($qf.Name)); starting as lane$n"
                $procs += Start-Process -FilePath $shell -ArgumentList $childArgs -PassThru -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $stateDir "lane$n.out.log") `
                    -RedirectStandardError (Join-Path $stateDir "lane$n.err.log")
                Move-Item -Force $qf.FullName ($qf.FullName -replace '\.json$', '.started')
            }
            catch {
                Log "queue file $($qf.Name) ignored: $($_.Exception.Message)"
                Move-Item -Force $qf.FullName ($qf.FullName -replace '\.json$', '.rejected') -ErrorAction SilentlyContinue
            }
        }
        # A lane child that dies BEFORE its first log line - a preflight crash, a
        # locked file - used to vanish without a trace: no state entry, nothing in
        # runner.log, and the lane was discovered missing hours later (measured
        # 2026-09-05: a queued lane lost 1 h 45 min this way). Every exit is logged
        # once, with the code and where its stderr went.
        foreach ($pr in @($procs)) {
            if (-not $pr -or -not $pr.HasExited) { continue }
            if ($reported -contains $pr.Id) { continue }
            $reported += $pr.Id
            $code = $pr.ExitCode
            if ($code -eq 0) { Log "lane child pid $($pr.Id) exited (0)" }
            else { Log "lane child pid $($pr.Id) EXITED WITH CODE $code before finishing its lane - read its lane*.err.log in $stateDir" }
        }
        $alive = @($procs | Where-Object { $_ -and -not $_.HasExited })
        if (-not $alive.Count) { break }
        Start-Sleep -Seconds $cfg.pollSeconds
    }
    Log "all lanes finished; state: $stateFile"
}
