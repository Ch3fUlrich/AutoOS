<#
Plain-PowerShell tests for Ledger and Crash Recovery in HandoffCore.psm1

    pwsh -NoProfile -File skills/unattended-orchestration/tests/Ledger.Tests.ps1

Exit code 0 = green. Non-zero = the number of failed assertions.
#>
$ErrorActionPreference = "Stop"
$script:Failures = 0
$script:Ran = 0

function It([string]$name, [scriptblock]$body) {
    $script:Ran++
    try { & $body; Write-Host "  ok   $name" -ForegroundColor Green }
    catch { $script:Failures++; Write-Host "  FAIL $name`n       $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-Equal($expected, $actual, [string]$because = "") {
    if ($expected -ne $actual) { throw "expected '$expected', got '$actual' $because" }
}
function Assert-True($cond, [string]$because = "") {
    if (-not $cond) { throw "expected true $because" }
}
function Assert-Throws([scriptblock]$body, [string]$match) {
    try { & $body } catch {
        if ($_.Exception.Message -match $match) { return }
        throw "threw, but message '$($_.Exception.Message)' does not match '$match'"
    }
    throw "expected a throw matching '$match', but nothing was thrown"
}

$module = Join-Path (Split-Path $PSScriptRoot -Parent) "HandoffCore.psm1"
Import-Module $module -Force

$testDir = Join-Path ([System.IO.Path]::GetTempPath()) "handoff-ledger-test-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $testDir | Out-Null

try {
    Write-Host "`nRedact-HandoffSecrets"

    It "redacts sensitive keys in hashtables and nested structures" {
        $payload = @{
            sessionId   = "S1"
            apiKey      = "secret-key-12345"
            token       = "auth-token-67890"
            password    = "mypassword"
            credential  = "mycred"
            nested      = @{
                secretKey   = "sub-secret-999"
                safeParam   = "clean-data"
            }
            safeList    = @("normal", @{ authToken = "nested-tok" })
        }
        $clean = Redact-HandoffSecrets $payload
        Assert-Equal "S1" $clean["sessionId"]
        Assert-Equal "[REDACTED]" $clean["apiKey"]
        Assert-Equal "[REDACTED]" $clean["token"]
        Assert-Equal "[REDACTED]" $clean["password"]
        Assert-Equal "[REDACTED]" $clean["credential"]
        Assert-Equal "[REDACTED]" $clean["nested"]["secretKey"]
        Assert-Equal "clean-data" $clean["nested"]["safeParam"]
        Assert-Equal "normal" $clean["safeList"][0]
        Assert-Equal "[REDACTED]" $clean["safeList"][1]["authToken"]
    }

    It "preserves normal dictionary and session keys" {
        $data = @{
            key         = "A1"
            sessionKey  = "B2"
            status      = "running"
            correlationId = "corr-100"
        }
        $clean = Redact-HandoffSecrets $data
        Assert-Equal "A1" $clean["key"]
        Assert-Equal "B2" $clean["sessionKey"]
        Assert-Equal "running" $clean["status"]
        Assert-Equal "corr-100" $clean["correlationId"]
    }

    It "redacts inline bearer tokens and well-known token formats in strings" {
        $str = "Failed request: Bearer abc12345xyz6789 with token sk-abcdefghijklmnopqrstuvwxyz123456"
        $clean = Redact-HandoffSecrets $str
        Assert-True ($clean -match "Bearer \[REDACTED\]") "Bearer token should be redacted"
        Assert-True ($clean -notmatch "sk-abcdef") "sk- token should be redacted"
        Assert-True ($clean -match "\[REDACTED\]") "Replacement token should appear"
    }

    It "redacts values matching sensitive environment variables" {
        $env:TEST_SECRET_API_TOKEN = "super_classified_val_98765"
        try {
            $str = "Log entry with super_classified_val_98765 inside"
            $clean = Redact-HandoffSecrets $str
            Assert-Equal "Log entry with [REDACTED] inside" $clean
        }
        finally {
            Remove-Item "env:TEST_SECRET_API_TOKEN" -ErrorAction SilentlyContinue
        }
    }

    Write-Host "`nWrite-HandoffLedgerEvent & Read-HandoffLedger"

    It "appends single-line NDJSON events and reads them back" {
        $ledgerFile = Join-Path $testDir "progress.jsonl"
        $evt1 = Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = "A1"
            turnId        = 1
            kind          = "session_started"
            correlationId = "corr-1"
            data          = @{ model = "gemini-3.8-flash-high"; worktree = "wt-A1" }
        }

        $evt2 = Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = "A1"
            turnId        = 1
            kind          = "turn_ended"
            correlationId = "corr-1"
            data          = @{ ended = "done"; apiKey = "should-be-redacted" }
        }

        $evt3 = Write-HandoffLedgerEvent -Path $ledgerFile -Event @{
            sessionId     = "B2"
            turnId        = 1
            kind          = "session_started"
            correlationId = "corr-2"
            data          = @{ model = "sonnet" }
        }

        Assert-True (Test-Path $ledgerFile) "ledger file should exist"
        $lines = Get-Content $ledgerFile
        Assert-Equal 3 $lines.Count "should contain exactly 3 lines"

        $all = Read-HandoffLedger -Path $ledgerFile
        Assert-Equal 3 $all.Count
        Assert-Equal "A1" $all[0]["sessionId"]
        Assert-Equal "session_started" $all[0]["kind"]
        Assert-Equal 1 $all[0]["schemaVersion"]
        Assert-Equal "turn_ended" $all[1]["kind"]
        Assert-Equal "[REDACTED]" $all[1]["data"]["apiKey"]

        $aEvents = Read-HandoffLedger -Path $ledgerFile -SessionId "A1"
        Assert-Equal 2 $aEvents.Count

        $startedEvents = Read-HandoffLedger -Path $ledgerFile -Kind "session_started"
        Assert-Equal 2 $startedEvents.Count
        Assert-Equal "A1" $startedEvents[0]["sessionId"]
        Assert-Equal "B2" $startedEvents[1]["sessionId"]
    }

    It "tolerates corrupt or incomplete trailing lines when reading" {
        $ledgerFile = Join-Path $testDir "corrupt.jsonl"
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{ sessionId = "C1"; kind = "session_started" } | Out-Null
        [System.IO.File]::AppendAllText($ledgerFile, "`n{`"schemaVersion`": 1, `"sessionId`": `"C1`", `"kin`n", [System.Text.Encoding]::UTF8)
        Write-HandoffLedgerEvent -Path $ledgerFile -Event @{ sessionId = "C1"; kind = "crashed" } | Out-Null

        $recovered = Read-HandoffLedger -Path $ledgerFile
        Assert-Equal 2 $recovered.Count "corrupt line should be skipped gracefully"
        Assert-Equal "session_started" $recovered[0]["kind"]
        Assert-Equal "crashed" $recovered[1]["kind"]
    }

    Write-Host "`nRotate-HandoffLedger"

    It "rotates ledger file when exceeding size threshold" {
        $rotFile = Join-Path $testDir "rotate_test.jsonl"
        for ($i = 0; $i -lt 50; $i++) {
            Write-HandoffLedgerEvent -Path $rotFile -Event @{
                sessionId = "R1"
                kind      = "event_$i"
                data      = @{ padding = ("x" * 200) }
            } | Out-Null
        }

        $sizeBytes = (Get-Item $rotFile).Length
        Assert-True ($sizeBytes -gt 10000) "rotFile should have written some bytes"

        $didRotate = Rotate-HandoffLedger -Path $rotFile -MaxSizeMB 0.005 -MaxRotations 3
        Assert-True $didRotate "should report rotation occurred"
        Assert-True (Test-Path "$rotFile.1") "first rotated file should exist"
        Assert-True (-not (Test-Path $rotFile)) "original file should have moved"

        Write-HandoffLedgerEvent -Path $rotFile -Event @{ sessionId = "R1"; kind = "fresh_event" } | Out-Null
        Assert-True (Test-Path $rotFile) "primary file should be recreated on write"
        $fresh = Read-HandoffLedger -Path $rotFile
        Assert-Equal 1 $fresh.Count
        Assert-Equal "fresh_event" $fresh[0]["kind"]
    }

    Write-Host "`nReconcile-HandoffLedger (Crash-Recovery Semantics)"

    It "marks orphaned sessions as crashed and appends ledger events" {
        $stateFile = Join-Path $testDir "state_crash.json"
        $ledgerFile = Join-Path $testDir "ledger_crash.jsonl"

        $initialState = @{
            S1 = @{
                status     = "running"
                pid        = 99999999
                session_id = "sid-s1"
                model      = "opus"
            }
            S2 = @{
                status     = "running"
                pid        = $PID
                session_id = "sid-s2"
                model      = "sonnet"
            }
            S3 = @{
                status     = "merged"
                session_id = "sid-s3"
                model      = "flash"
            }
        }
        Write-HandoffState $stateFile $initialState

        $reconciled = Reconcile-HandoffLedger -LedgerPath $ledgerFile -StatePath $stateFile
        Assert-Equal 1 $reconciled.Count "only S1 should be reconciled"
        Assert-Equal "S1" $reconciled[0]

        $newState = Read-HandoffState $stateFile
        Assert-Equal "crashed" $newState["S1"]["status"]
        Assert-True ($newState["S1"]["last_error"] -match "crashed") "last_error should explain crash"
        Assert-Equal "running" $newState["S2"]["status"] "S2 should remain running as $PID is alive"
        Assert-Equal "merged" $newState["S3"]["status"] "S3 should remain merged"

        $events = Read-HandoffLedger -Path $ledgerFile
        Assert-Equal 1 $events.Count
        Assert-Equal "S1" $events[0]["sessionId"]
        Assert-Equal "crashed" $events[0]["kind"]
        Assert-Equal 99999999 $events[0]["data"]["pid"]
    }
}
finally {
    Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
}

Write-Host "`n$script:Ran assertions, $script:Failures failed."
if ($script:Failures -gt 0) { exit $script:Failures }
exit 0
