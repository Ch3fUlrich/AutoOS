#Requires -Version 5.1
<#
.SYNOPSIS
    Omnigraph MCP bridge benchmark (SPEC-OMNI A5) — the D9 measurement.
.DESCRIPTION
    Thin wrapper: all logic lives in tools\check_omnigraph_bridge.py, which this
    forwards every argument to. Add flags there, never here.

      tools\check-omnigraph-bridge.ps1 --cold
      tools\check-omnigraph-bridge.ps1 --warm
      tools\check-omnigraph-bridge.ps1 --fake-server   # offline: no npm, no network

    Exit codes (the python file's): 0 all bridges healthy and D9 met,
    1 a failure, 2 unusable input. Never prints the token.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BridgeArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tool = Join-Path $PSScriptRoot 'check_omnigraph_bridge.py'
if (-not (Test-Path -LiteralPath $tool)) {
    [Console]::Error.WriteLine("ERROR: missing $tool")
    exit 2
}
$py = Get-Command python, python3 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $py) {
    [Console]::Error.WriteLine('ERROR: python3 is required to run the bridge benchmark but was not found.')
    exit 2
}
if (-not $BridgeArgs) { $BridgeArgs = @() }

# PowerShell 5.1 makes a native command's stderr a terminating error under Stop, and
# the benchmark writes its ERROR lines to stderr — so run it with Continue (AGENTS.md 6).
$prev = $ErrorActionPreference
$rc = 1
try {
    $ErrorActionPreference = 'Continue'
    & $py.Source $tool @BridgeArgs
    $rc = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $prev
}
exit $rc
