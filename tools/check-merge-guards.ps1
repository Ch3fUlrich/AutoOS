#Requires -Version 5.1
<#
.SYNOPSIS
    Merge gate for the tier-orchestration integration lanes.
.DESCRIPTION
    Runs the AGENTS.md section 7 definition of done that can run headless:
    both suites, the vendored-profile mirror guard, the harness unit tests,
    and the linters when present. Exit 0 = mergeable, anything else = stop
    the lane. Used as the `guards` command in .claude/handoff.config.json.
    Run from the repo root (the runner runs it in the guards worktree).
    Extra argv (e.g. the runner's path list) is accepted and ignored.
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GuardPaths)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$fail = 0
function Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "`n== $Name"
    try { & $Body }
    catch {
        Write-Host "GUARD FAILED: $Name : $($_.Exception.Message)"
        $script:fail++
    }
}

Step 'harness unit tests' { python tests/test_agent_harness.py 2>&1 | Select-Object -Last 3 }
Step 'vendored mirror guard' { python tests/check-vendored.py 2>&1 | Select-Object -Last 3 }

$prevAction = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    Step 'Windows suite' {
        powershell -File tests\run-tests.ps1 2>&1 | Select-String -Pattern 'passed.*failed' | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) { throw 'run-tests.ps1 exited non-zero' }
    }
} finally { $ErrorActionPreference = $prevAction }
Step 'Linux suite' {
    bash tests/run-tests.sh 2>&1 | Select-Object -Last 3
}

Step 'shellcheck' {
    if (Get-Command shellcheck -ErrorAction SilentlyContinue) { shellcheck -S warning lib/linux/*.sh 2>&1 | Select-Object -First 5 }
    else { Write-Host 'shellcheck not present, skipped (not a pass)' }
}
Step 'PSScriptAnalyzer' {
    if (Get-Module -ListAvailable -Name PSScriptAnalyzer) {
        $r = Invoke-ScriptAnalyzer -Path lib/windows -Recurse | Where-Object { $_.Severity -in 'Error', 'Warning' }
        if ($r) { $r | Select-Object -First 5 | Format-Table -AutoSize | Out-String | Write-Host; throw 'analyzer findings' }
    } else { Write-Host 'PSScriptAnalyzer not present, skipped (not a pass)' }
}

if ($fail) { Write-Host "`nGUARDS RED ($fail)"; exit 1 }
Write-Host '`nGUARDS GREEN'
exit 0
