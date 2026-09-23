<#
Smoke tests for run_handoff_sessions.ps1 — the DRIVER, not the pure core.

These exist because both bugs found on the first real run lived here and were
invisible to HandoffCore.Tests.ps1: a lane array unrolled by the output stream,
and a local $followUp colliding with the [string]$FollowUp parameter. Both only
appear when the script is actually invoked, so these tests invoke it.

    pwsh -NoProfile -File skills/unattended-orchestration/tests/Runner.Smoke.Tests.ps1

Nothing here starts a `claude` session: -DryRun stops before any launch, and
-Validate stops before preflight.
#>
$ErrorActionPreference = "Stop"
$script:Failures = 0
$script:Ran = 0

function It([string]$name, [scriptblock]$body) {
    $script:Ran++
    try { & $body; Write-Host "  ok   $name" -ForegroundColor Green }
    catch { $script:Failures++; Write-Host "  FAIL $name`n       $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-Match([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -notmatch $pattern) { throw "expected output matching '$pattern' $because`n--- got ---`n$text" }
}
function Assert-NotMatch([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -match $pattern) { throw "did NOT expect '$pattern' $because`n--- got ---`n$text" }
}
function Assert-Equal($expected, $actual, [string]$because = "") {
    if ($expected -ne $actual) { throw "expected '$expected', got '$actual' $because" }
}
function Assert-True($cond, [string]$because = "") {
    if (-not $cond) { throw "expected true $because" }
}

$runner = Join-Path (Split-Path $PSScriptRoot -Parent) "run_handoff_sessions.ps1"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("handoff-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function New-Config([array]$lanes, [string]$name) {
    $cfg = @{
        repo         = $repo
        baseBranch   = (git -C $repo rev-parse --abbrev-ref HEAD).Trim()
        branchPrefix = "smoke"
        stateDir     = (Join-Path $tmp "state-$name")
        sessions     = @{
            X = @{ name = "alpha"; model = "opus";   brief = "alpha work" }
            Y = @{ name = "beta";  model = "sonnet"; brief = "beta work" }
        }
        lanes        = $lanes
    }
    $p = Join-Path $tmp "$name.json"
    $cfg | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
    return $p
}

try {
    Write-Host "`n-Validate"
    It "reports a two-session lane as ONE lane, not two" {
        $p = New-Config @(, @("X", "Y")) "onelane"
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "lanes:\s+X,Y" "a sequential lane must stay sequential"
        Assert-NotMatch $out "X\s+\|\s+Y" "must not split into parallel lanes"
    }
    It "splits one -Lanes token on semicolons into separate lanes (the Start-Process-safe form)" {
        $p = New-Config @(, @("X", "Y")) "semilanes"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun -Lanes "X;Y" 2>&1 | Out-String)
        $log = Get-Content (Join-Path (Split-Path $p -Parent) "state-semilanes\runner.log") -Raw
        Assert-Match $log "lane \[X\] starting as lane1" "X must be its own lane"
        Assert-Match $log "lane \[Y\] starting as lane2" "Y must be its own lane"
    }
    It "reports two lanes as two" {
        $p = New-Config @(@("X"), @("Y")) "twolanes"
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "lanes:\s+X\s+\|\s+Y"
    }
    It "fails a config whose lane names an unknown session" {
        $p = New-Config @(, @("X", "NOPE")) "badlane"
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "NOPE"
    }

    It "prints the stall watchdog and its rate rule, so a night is never committed to an unknown one" {
        $p = New-Config @(@("X"), @("Y")) "stallknobs"
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "stall:\s+60 min" "-Validate must surface stallMinutes"
        Assert-Match $out "5 stalls within 3 h" "-Validate must surface the stallWindow rate rule"
    }
    It "reports the stall watchdog as OFF when stallMinutes is 0, instead of printing 0 min" {
        $cfg = ConvertFrom-Json (Get-Content (New-Config @(@("X")) "stalloff") -Raw) -AsHashtable
        $cfg.stallMinutes = 0
        $p = Join-Path $tmp "stalloff2.json"
        $cfg | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "stall:\s+off" "a disabled watchdog must say so"
    }

    It "reports a session that drives a different agent" {
        $cfg = @{
            repo         = $repo
            baseBranch   = (git -C $repo rev-parse --abbrev-ref HEAD).Trim()
            branchPrefix = "smoke"
            stateDir     = (Join-Path $tmp "state-launcher")
            launcher     = "claude"
            sessions     = @{
                X = @{ name = "alpha"; model = "opus"; brief = "a" }
                Z = @{ name = "zeta";  model = "claude-sonnet-4-6"; brief = "z"; launcher = "agy" }
            }
            lanes        = @(@("X"), @("Z"))
        }
        $p = Join-Path $tmp "launcher.json"
        $cfg | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "launcher:\s+Z drives agy" "-Validate must surface a per-session launcher"
        Assert-NotMatch $out "launcher:\s+X drives" "a session on the default needs no line"
    }

    Write-Host "`n-DryRun"
    It "runs a single lane INLINE, without spawning lane child processes" {
        # The unrolling bug showed up exactly here: one lane became two children.
        $p = New-Config @(, @("X", "Y")) "inline"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun 2>&1 | Out-String)
        Assert-Match $out "dry run: would create" "the lane body must actually run"
        Assert-NotMatch $out "starting as lane\d" "a single lane must not fan out to children"
        Assert-Match $out "lane \[X,Y\] finished"
    }
    It "visits every session of a sequential lane, in order" {
        $p = New-Config @(, @("X", "Y")) "order"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun 2>&1 | Out-String)
        $ix = $out.IndexOf("session X"); $iy = $out.IndexOf("session Y")
        if ($ix -lt 0 -or $iy -lt 0) { throw "expected both sessions to be visited`n$out" }
        if ($ix -gt $iy) { throw "X must be visited before Y" }
    }
    It "dispatches multiple lanes as children that do NOT crash on argument binding" {
        # The $followUp / [string]$FollowUp collision surfaced only in a child.
        $p = New-Config @(@("X"), @("Y")) "children"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun 2>&1 | Out-String)
        Assert-Match $out "starting as lane1"
        $laneLogs = Get-ChildItem (Join-Path $tmp "state-children") -Filter "lane*.out.log" -ErrorAction SilentlyContinue
        if (-not $laneLogs) { throw "no lane child logs were written" }
        $childText = ($laneLogs | ForEach-Object { Get-Content $_.FullName -Raw }) -join "`n"
        Assert-NotMatch $childText "crashed at session" "child lanes must not crash"
        Assert-Match $childText "dry run: would create" "child lanes must reach the lane body"
    }
    Write-Host "`n-EmitBriefs"
    It "renders every session as markdown without launching anything" {
        $p = New-Config @(, @("X", "Y")) "emit"
        $out = (& pwsh -NoProfile -File $runner -Config $p -EmitBriefs 2>&1 | Out-String)
        Assert-Match $out "### Session X"
        Assert-Match $out "### Session Y"
        Assert-Match $out "claude attach handoff-X" "each section must say how to join the session"
        Assert-NotMatch $out "dry run: would create" "-EmitBriefs must not enter the run loop"
        Assert-NotMatch $out "preflight" "-EmitBriefs must not require a live claude login"
    }
    It "writes to -OutFile when asked, and the file round-trips" {
        $p = New-Config @(, @("X")) "emitfile"
        $f = Join-Path $tmp "briefs.md"
        (& pwsh -NoProfile -File $runner -Config $p -EmitBriefs -OutFile $f 2>&1) | Out-Null
        if (-not (Test-Path $f)) { throw "no file written to $f" }
        Assert-Match (Get-Content $f -Raw) "### Session X"
    }

    It "a session with dependsOn reports the wait in -DryRun and -Validate instead of blocking" {
        # The wait itself needs a live state file; a dry run must SAY it would
        # wait and carry on, or every rehearsal of a dependent lane hangs.
        $cfg = @{
            repo         = $repo
            baseBranch   = (git -C $repo rev-parse --abbrev-ref HEAD).Trim()
            branchPrefix = "smoke"
            stateDir     = (Join-Path $tmp "state-deps")
            sessions     = @{
                X = @{ name = "alpha"; model = "opus";   brief = "alpha work" }
                Y = @{ name = "beta";  model = "sonnet"; brief = "beta work"; dependsOn = @("X") }
            }
            lanes        = @(@("X"), @("Y"))
        }
        $p = Join-Path $tmp "deps.json"
        $cfg | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "dependsOn:\s+Y waits for X" "-Validate must surface the dependency"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun -Sessions "Y" 2>&1 | Out-String)
        Assert-Match $out "would wait for X" "a dry run must report the wait"
        Assert-Match $out "dry run: would create" "and still reach the lane body"
    }

    It "a guards-only session is reported by -Validate and rehearsed by -DryRun without a launch" {
        $cfg = @{
            repo         = $repo
            baseBranch   = (git -C $repo rev-parse --abbrev-ref HEAD).Trim()
            branchPrefix = "smoke"
            stateDir     = (Join-Path $tmp "state-guardsonly")
            sessions     = @{
                X = @{ name = "alpha"; model = "opus"; brief = "alpha work"; resources = @("store:write") }
                F = @{ name = "final"; model = "sonnet"; guardsOnly = $true; dependsOn = @("X") }
            }
            lanes        = @(@("X"), @("F"))
        }
        $p = Join-Path $tmp "guardsonly.json"
        $cfg | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $out = (& pwsh -NoProfile -File $runner -Config $p -Validate 2>&1 | Out-String)
        Assert-Match $out "guardsOnly:\s+F" "-Validate must surface the guards-only session"
        Assert-Match $out "resources:\s+X holds store:write" "-Validate must surface held resources"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun -Sessions "F" 2>&1 | Out-String)
        Assert-Match $out "guards-only" "a dry run must say the session runs guards only"
        Assert-NotMatch $out "run claude there.*\n.*claude " "no launch may be rehearsed for it"
    }

    It "-Cleanup walks a merged session whose worktree and branch are already gone, and prunes its Serena row" {
        # The first real -Cleanup died inside Remove-SerenaProjectRow on a
        # variable named $home ($HOME is read-only). This drives that exact path
        # against a throwaway SERENA_HOME so the real config is never touched.
        $p = New-Config @(, @("X")) "cleanup"
        $stateDir = Join-Path $tmp "state-cleanup"
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
        $wt = Join-Path $tmp "gone-worktree"
        @{ X = @{ status = "merged"; worktree = $wt; branch = "smoke/nope"; bg_id = "" } } |
            ConvertTo-Json -Depth 6 | Set-Content (Join-Path $stateDir "state.json") -Encoding utf8
        $serenaHome = Join-Path $tmp "serena-home"
        New-Item -ItemType Directory -Force -Path $serenaHome | Out-Null
        "projects:`n- $wt`n- C:\elsewhere\kept`n" | Set-Content (Join-Path $serenaHome "serena_config.yml") -Encoding utf8
        $env:SERENA_HOME = $serenaHome
        try { $out = (& pwsh -NoProfile -File $runner -Config $p -Cleanup 2>&1 | Out-String) }
        finally { Remove-Item Env:SERENA_HOME -ErrorAction SilentlyContinue }
        Assert-Match $out "cleanup finished" "-Cleanup must run to the end`n$out"
        Assert-NotMatch $out "Cannot overwrite variable" "the read-only HOME trap must stay fixed"
        $rows = Get-Content (Join-Path $serenaHome "serena_config.yml") -Raw
        Assert-NotMatch $rows ([regex]::Escape($wt)) "the merged worktree's Serena row must be pruned"
        Assert-Match $rows "kept" "other rows must survive"
    }

    Write-Host "`nThe stall watchdog's impure half"
    # These lift ONE function out of the runner's own source with the PowerShell
    # parser and call it, instead of re-implementing it here: a copy in the test
    # would pass while the runner's copy was broken. The two below are the only
    # new code the unit tests cannot reach, and both fail SAFE (returning "I
    # cannot tell", which switches the watchdog off), so a silent break in either
    # looks exactly like a quiet night.
    # Returns the SOURCE TEXT; the caller dot-sources it. A dot-source in here
    # would define the function in this function's own scope and it would be gone
    # by the time the test called it (measured while writing these).
    function Get-RunnerFunctionText([string]$name) {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($runner, [ref]$null, [ref]$null)
        $fn = @($ast.FindAll({
            param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
        }, $true))
        if (-not $fn.Count) { throw "the runner defines no function named $name" }
        return $fn[0].Extent.Text
    }

    It "sums the CPU of a real process tree, and reports -1 for a handle that is not a pid" {
        Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) "HandoffCore.psm1") -Force -DisableNameChecking
        . ([scriptblock]::Create((Get-RunnerFunctionText "Get-TreeCpuSeconds")))
        Assert-Equal -1 (Get-TreeCpuSeconds "a1b2c3d4") "a claude --bg hex id is not a pid: unknown, not idle"
        Assert-Equal -1 (Get-TreeCpuSeconds "") "no id at all: unknown"
        if ((Get-HandoffLinkType) -eq "Junction") {
            # This very pwsh has burned CPU getting here, so anything but a
            # positive number means the CIM query or the tree walk is broken.
            $mine = Get-TreeCpuSeconds ([string]$PID)
            Assert-True ($mine -gt 0) "a live process tree must report positive CPU seconds, got $mine"
            Assert-Equal -1 (Get-TreeCpuSeconds "999999999") "a pid that does not exist is unknown, never idle"
        }
    }

    It "reads a log file's mtime as an epoch, and 0 for one that does not exist" {
        # Same silent-failure shape as Get-WorktreeChange: the mtime read sits in
        # a try/catch, so a broken read returns 0, 0 means "unknown", and unknown
        # switches the watchdog off without a word. This is the test that caught
        # the unparenthesised [DateTimeOffset] cast.
        . ([scriptblock]::Create((Get-RunnerFunctionText "Get-NewestWriteEpoch")))
        $f = Join-Path $tmp "mtime-probe.log"
        "x" | Set-Content $f
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $e = Get-NewestWriteEpoch @($f, "$f.err")
        Assert-True ($e -gt 0) "a file that exists must yield a real epoch, got $e"
        Assert-True ([math]::Abs($now - $e) -lt 600) "the epoch must be the file's mtime, not something else ($e vs $now)"
        Assert-Equal 0 (Get-NewestWriteEpoch @((Join-Path $tmp "no-such.log"))) "a missing file is unknown (0), never 'quiet since 1970'"
    }

    It "counts an untracked file's mtime as a worktree change, not just a commit" {
        # The signal that keeps the watchdog off a session that is editing but has
        # not committed yet. A commit-only reading would call that session hung.
        Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) "HandoffCore.psm1") -Force -DisableNameChecking
        . ([scriptblock]::Create((Get-RunnerFunctionText "Get-WorktreeChange")))
        $wt = Join-Path $tmp "wtchange"
        New-Item -ItemType Directory -Force -Path $wt | Out-Null
        git -C $wt init -q 2>&1 | Out-Null
        git -C $wt config user.email "t@t"; git -C $wt config user.name "t"
        "one" | Set-Content (Join-Path $wt "a.txt")
        git -C $wt add -A 2>&1 | Out-Null
        # Committed an hour ago, so "now" can only come from the working tree.
        $env:GIT_COMMITTER_DATE = "2001-01-01T00:00:00"
        try { git -C $wt commit -q -m one --date="2001-01-01T00:00:00" 2>&1 | Out-Null }
        finally { Remove-Item Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue }
        $committed = Get-WorktreeChange $wt
        Assert-True (-not $committed.dirty) "a clean worktree must not read as dirty"
        New-Item -ItemType Directory -Force -Path (Join-Path $wt "sub") | Out-Null
        "fresh" | Set-Content (Join-Path $wt "sub\new.txt")
        $touched = Get-WorktreeChange $wt
        Assert-True $touched.dirty "an untracked file must make the worktree dirty"
        Assert-True ($touched.epoch -gt $committed.epoch) "a file written INTO an untracked directory must move the change epoch (-uall), got $($touched.epoch) vs $($committed.epoch)"
        Assert-Equal 0 (Get-WorktreeChange "").epoch "no worktree -> epoch 0, which reads as unknown"
    }

    It "-Sessions overrides the config into a single inline lane" {
        $p = New-Config @(@("X"), @("Y")) "override"
        $out = (& pwsh -NoProfile -File $runner -Config $p -DryRun -Sessions "X,Y" 2>&1 | Out-String)
        Assert-NotMatch $out "starting as lane\d" "-Sessions must collapse to one lane"
        Assert-Match $out "lane \[X,Y\] finished"
    }
}
finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }

Write-Host "`n$($script:Ran) assertions, $($script:Failures) failed."
exit $script:Failures
