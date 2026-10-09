<#
.SYNOPSIS
  Call the one fleet rule D-825 GO-reference implementation (_go_gate.py) from PowerShell.

.DESCRIPTION
  apply.ps1, apply-capability-overrides.ps1 and compact-graphs.ps1 all gate a live
  gateway or live store change behind -Go <ref> / -GoSha <sha>. What a reference is
  ALLOWED to name is decided in one place - infra/mcp-servers/scripts/_go_gate.py, the
  module the Python live-store tools import and the bash gates exec - and this script is
  its PowerShell front: it resolves an interpreter, runs `verify-ref`, and hands the
  caller back the exit code plus the lines to print. No ref grammar, no artefact lookup,
  no verdict lives here.

  The reference must not only look like a judge GO but name something that EXISTS: a
  line with that exact id in the routing decisions log (AUTOOS_DECISIONS_LOG, else
  AUTOOS_ROUTING_DIR/docs/decisions-log.md and .../DECISIONS.md), the same in
  QUESTIONS.md / ANSWERS.md for an OS-<n> item, or a worker record
  (AUTOOS_WORKERS_DIR/<id>.json, else logs/workers and logs/agents under -Root) for a
  judge run id. An absent id AND an unreadable source both refuse; -GoOffline is the
  operator's declared exception, which waives the lookup only (-GoSha is still verified)
  and logs 'GO-OFFLINE: <ref> unverified'.

  Returns [pscustomobject]{ Code, Out, Err }: Code 0 clears the bar, anything else is a
  refusal. Out holds the line to print BEFORE the caller's own 'GO: <ref> sha=<sha>'
  (empty unless offline waived the lookup), Err the refusal text for stderr. It never
  exits the caller and never writes anything itself, so each gate keeps its own voice.

.EXAMPLE
  $v = & (Join-Path $PSScriptRoot 'invoke-go-gate.ps1') -Tool apply.ps1 -Ref $Go -Root $Root -Offline:$GoOffline
  foreach ($l in $v.Err) { [Console]::Error.WriteLine($l) }
  if ($v.Code -ne 0) { exit 2 }
  foreach ($l in $v.Out) { Write-Host $l }
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Tool,
    [Parameter(Mandatory)][string]$Ref,
    [string]$Root = '',
    [switch]$Offline
)

Set-StrictMode -Version Latest
# 'Continue': 5.1 turns a native command's stderr into a terminating error under Stop,
# and the refusal this call is made to produce is written to stderr.
$ErrorActionPreference = 'Continue'

$GateScript = Join-Path $PSScriptRoot '_go_gate.py'
if (-not $Root) {
    # Two trees up from configuration/omniroute, three from infra/mcp-servers/scripts:
    # the caller knows its own layout, so it passes -Root. Falling back to the gate's
    # checkout keeps a caller from silently verifying against no tree at all.
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
}

function Get-AutoOSPython {
    # The Store 'python3' stub on Windows answers a shrug and exit 0-ish, so each
    # candidate is proven by running it, not by finding it.
    $candidates = @('python3', 'python')
    if ([Environment]::OSVersion.Platform -eq 'Win32NT') { $candidates = @('python', 'python3') }
    foreach ($c in $candidates) {
        if (-not (Get-Command $c -CommandType Application -ErrorAction SilentlyContinue)) { continue }
        & $c -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return $null
}

$outLines = @()
$errLines = @()
$code = 0
$python = Get-AutoOSPython
if (-not $python -or -not (Test-Path -LiteralPath $GateScript)) {
    # No way to read the artefact tree at all: an unverifiable GO is not a GO, unless
    # the operator said so with -GoOffline, which is then logged as unverified.
    if (-not $Offline) {
        $code = 2
        $errLines += "$Tool`: cannot verify -Go '$Ref' - no python interpreter or no $GateScript, so the GO's artefact tree cannot be read here (fleet rule D-825)."
        $errLines += '  Install python, or pass -GoOffline to run with the ref logged as unverified.'
    }
    else {
        $outLines += "GO-OFFLINE: $Ref unverified"
    }
}
else {
    $gateArgs = @($GateScript, 'verify-ref', '--tool', $Tool, '--ref', $Ref,
                  '--root', $Root, '--flag-style', 'ps')
    if ($Offline) { $gateArgs += '--offline' }
    $captured = & $python @gateArgs 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $captured) {
        # 5.1 wraps native stderr in ErrorRecord; the record, not its .ToString(),
        # carries the stream it came from.
        if ($line -is [System.Management.Automation.ErrorRecord]) {
            $errLines += "$($line.Exception.Message)"
        }
        else { $outLines += "$line" }
    }
}

[pscustomobject]@{ Code = $code; Out = $outLines; Err = $errLines }
