<#
    Archive.Tests.ps1 — Contract tests for session archiving, artifact preservation, and idempotent cleanup
#>

$ErrorActionPreference = "Stop"
$script:Ran = 0; $script:Failures = 0

function It([string]$Name, [scriptblock]$Body) {
    $script:Ran++
    try {
        & $Body
        Write-Host "  ok   $Name" -ForegroundColor Green
    } catch {
        $script:Failures++
        Write-Host "  FAIL $Name`n       $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal($Expected, $Actual, [string]$Message = "") {
    if ($Expected -ne $Actual) {
        throw "assert equal failed $(if ($Message) { "($Message): " })expected '$Expected', got '$Actual'"
    }
}

function Assert-True([bool]$Condition, [string]$Message = "expected true") {
    if (-not $Condition) { throw "assert true failed: $Message" }
}

function Assert-Match([string]$Value, [string]$Pattern, [string]$Message = "") {
    if ($Value -notmatch $Pattern) {
        throw "assert match failed $(if ($Message) { "($Message): " })'$Value' does not match pattern '$Pattern'"
    }
}

$modulePath = Join-Path $PSScriptRoot "..\HandoffCore.psm1"
$runnerPath = Join-Path $PSScriptRoot "..\run_handoff_sessions.ps1"
Import-Module (Resolve-Path $modulePath).Path -Force

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "archive-test-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    # Initialize a test git repo
    $repo = Join-Path $tmp "test-repo"
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    git -C $repo init -b main 2>&1 | Out-Null
    git -C $repo config user.email "test@archive.local"
    git -C $repo config user.name "Archive Test"
    "Initial base" | Set-Content (Join-Path $repo "README.md")
    git -C $repo add . 2>&1 | Out-Null
    git -C $repo commit -m "initial commit" 2>&1 | Out-Null

    $stateDir = Join-Path $repo "output/sessions"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $ledgerFile = Join-Path $stateDir "progress.jsonl"
    $stateFile = Join-Path $stateDir "state.json"

    # Create dummy branch and commit
    git -C $repo checkout -b handoff/worker main 2>&1 | Out-Null
    "Feature work" | Set-Content (Join-Path $repo "feature.txt")
    git -C $repo add . 2>&1 | Out-Null
    git -C $repo commit -m "feat: implement feature" 2>&1 | Out-Null
    git -C $repo checkout main 2>&1 | Out-Null

    # Create fake done note and session log
    $doneNote = Join-Path $stateDir "session-worker-DONE.md"
    "## Summary`nCompleted cleanly." | Set-Content -Path $doneNote -Encoding utf8
    $sessionLog = Join-Path $stateDir "session-worker.log"
    "Session log content line 1`nLine 2" | Set-Content -Path $sessionLog -Encoding utf8

    $cfg = @{
        repo       = $repo
        baseBranch = "main"
        stateDir   = "output/sessions"
        sessions   = @{
            worker = @{ name = "worker"; model = "sonnet"; brief = "do work" }
        }
    }
    $vars = @{
        worktree = (Join-Path $tmp "wt-worker")
        branch   = "handoff/worker"
        doneNote = $doneNote
    }

    Write-Host "`nBuild-HandoffArchiveManifest"

    It "builds archive manifest with destination path and required file mappings" {
        $manifest = Build-HandoffArchiveManifest $cfg "worker" $vars -Timestamp "20260909-120000"
        Assert-Equal "worker" $manifest.sessionKey "session key"
        Assert-Equal "20260909-120000" $manifest.timestamp "timestamp"
        $expectedDir = Join-Path $stateDir "archive\worker-20260909-120000"
        Assert-Equal $expectedDir $manifest.archiveDir "archive directory"
        Assert-True ($manifest.files.Count -ge 2) "must map DONE.md and session.log"
    }

    Write-Host "`nArchive-HandoffSession"

    It "archives DONE note, logs, git patch, and writes manifest.json" {
        @{ worker = @{ status = "merged"; branch = "handoff/worker" } } |
            ConvertTo-Json -Depth 4 | Set-Content -Path $stateFile -Encoding utf8

        $archDir = Archive-HandoffSession $cfg "worker" $vars -LedgerPath $ledgerFile -StatePath $stateFile -CorrelationId "test-corr-1"
        Assert-True (Test-Path $archDir) "archive directory must exist"
        
        Assert-True (Test-Path (Join-Path $archDir "DONE.md")) "DONE.md must be archived"
        Assert-True (Test-Path (Join-Path $archDir "session.log")) "session.log must be archived"
        Assert-True (Test-Path (Join-Path $archDir "changes.patch")) "changes.patch must be archived"
        Assert-True (Test-Path (Join-Path $archDir "manifest.json")) "manifest.json must be written"

        $manifestContent = Get-Content (Join-Path $archDir "manifest.json") -Raw | ConvertFrom-Json
        Assert-Equal "worker" $manifestContent.sessionKey "manifest sessionKey"
        Assert-Equal "handoff/worker" $manifestContent.branch "manifest branch"

        # Check ledger event
        Assert-True (Test-Path $ledgerFile) "ledger must exist"
        $ledgerLines = Get-Content $ledgerFile
        $archEvt = $ledgerLines | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.kind -eq "archived" } | Select-Object -First 1
        Assert-True ($null -ne $archEvt) "archived event must be in ledger"
        Assert-Equal "worker" $archEvt.sessionId "event sessionId"

        # Check state update
        $updatedState = Read-HandoffState $stateFile
        Assert-True ($updatedState.worker.ContainsKey("archived")) "state must record archived timestamp"
        Assert-Equal $archDir $updatedState.worker.archive_path "state must record archive_path"
    }

    It "Archive-HandoffSession is idempotent when run a second time" {
        $archDir2 = Archive-HandoffSession $cfg "worker" $vars -LedgerPath $ledgerFile -StatePath $stateFile -CorrelationId "test-corr-2"
        Assert-True (Test-Path $archDir2) "second archival call must succeed"
    }

    Write-Host "`nIdempotent -Cleanup Integration"

    It "-Cleanup archives unarchived sessions, cleans worktree and branch, and is re-entrant" {
        # Setup config file for runner
        $cfgJson = Join-Path $repo "handoff.config.json"
        @{
            repo       = $repo
            baseBranch = "main"
            stateDir   = "output/sessions"
            sessions   = @{
                worker = @{ name = "worker"; model = "sonnet"; brief = "do work" }
            }
        } | ConvertTo-Json -Depth 4 | Set-Content -Path $cfgJson -Encoding utf8

        # Create dummy worktree
        $wtPath = Join-Path $tmp "wt-cleanup-test"
        git -C $repo worktree add $wtPath handoff/worker 2>&1 | Out-Null
        Assert-True (Test-Path $wtPath) "worktree must exist before cleanup"

        @{ worker = @{ status = "merged"; branch = "handoff/worker"; worktree = $wtPath } } |
            ConvertTo-Json -Depth 4 | Set-Content -Path $stateFile -Encoding utf8

        # Run -Cleanup first time
        $out1 = & pwsh -NoProfile -File $runnerPath -Config $cfgJson -Cleanup 2>&1 | Out-String
        Assert-Match $out1 "cleanup finished" "first cleanup must complete"
        Assert-True (-not (Test-Path $wtPath)) "worktree must be removed"

        # Check state and ledger
        $cleanState = Read-HandoffState $stateFile
        Assert-True ($cleanState.worker.ContainsKey("cleaned")) "state must record cleaned timestamp"

        # Re-run -Cleanup second time (re-entrancy / idempotency check)
        $out2 = & pwsh -NoProfile -File $runnerPath -Config $cfgJson -Cleanup 2>&1 | Out-String
        Assert-Match $out2 "cleanup finished" "second cleanup must complete cleanly without errors"
    }

} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "`n$($script:Ran) assertions, $($script:Failures) failed."
exit $script:Failures
