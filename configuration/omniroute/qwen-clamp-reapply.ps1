#Requires -Version 5.1
<#
.SYNOPSIS
  Idempotent reapply of the AutoOS qwen3-235b output-cap clamp in the compiled gateway.

.DESCRIPTION
  Appends one entry to the gateway's compiled static model-output-cap array so that
  Scaleway's qwen3-235b-a22b-instruct-2507 gets an explicit 16384-token output
  ceiling and the gateway clamps to it instead of forwarding an over-limit
  max_tokens that Scaleway rejects with a 400 (the whole request then fails and a
  combo falls through):

      400: max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507

  Six compiled chunk copies carry that static array. In each, the array's last
  existing entry is the azure-ai gpt-4o-mini one, so the edit is a single insertion:

    OLD  {provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384}];
    NEW  {provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384},
         {provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,
          maxOutputCap:16384,clampToModelMaxOutput:!0}];

  This reproduces, as a tracked artifact, the live edit delivered on 2026-09-30 by
  an untracked diagnostic script (AutoOS-ws-qwenclamp/logs/patch_dist.py, 14:49Z).
  That edit was previously represented in no tracked file - see
  docs/handoff/2026-09-30-lanePatchIntegrity.md.

  LINE ENDINGS. The historical producer wrote with Python `Path.write_text`, whose
  default newline=None translates every LF to CRLF on Windows. Two of the six
  chunks it touched were therefore left wholly CRLF (+649 B) on top of the 106 B
  entry (+755 B total); the other four were later rewritten as LF by the
  deepseek/vertex patchers. CRLF vs LF is semantically irrelevant in JavaScript.
  THIS SCRIPT PRESERVES EACH FILE'S EXISTING LINE ENDINGS and does not reproduce
  the accidental CRLF rewrite; on a pristine (LF) install it yields LF. That is
  deliberate: the CRLF was an artifact of one tool's default, not part of the fix.
  See qwen-clamp-reapply-README.md.

  Idempotent: if the scaleway entry is already present the file is reported SKIP,
  never re-appended. Each changed file is backed up to
  <file>.autoos-backup-<timestamp> before it is written. The running gateway keeps
  the old bytes in memory, so RESTART it to load the change. `npm update omniroute`
  reverts all of this; re-run the script afterwards.

.PARAMETER PackageDir
  Root of the omniroute npm package. Defaults to the global install.
.PARAMETER DryRun
  Report what would change, write nothing.

.EXAMPLE
  pwsh -File qwen-clamp-reapply.ps1
.EXAMPLE
  pwsh -File qwen-clamp-reapply.ps1 -PackageDir C:\omniroute-dev\node_modules\omniroute

.NOTES
  AutoOS patch 2026-09-30  |  omniroute v3.8.50  |  branch L1-backlog/ws-fixes-20260930
#>
[CmdletBinding()]
param(
  [string]$PackageDir = (
    Join-Path (npm root -g 2>$null) 'omniroute'
  ),
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

if (-not (Test-Path (Join-Path $PackageDir 'package.json'))) {
  Write-Host "ERROR: omniroute package not found at: $PackageDir"
  Write-Host "Pass -PackageDir <path> to override."
  exit 1
}

# The six chunks that carry the compiled static model-output-cap array.
$chunksDir = Join-Path $PackageDir 'dist/.build/next/server/chunks'
$chunkFiles = @(
  '_08_y1bx._.js',
  '_0o8_5h8._.js',
  '_0t1t5fj._.js',
  '_18ct13i._.js',
  '_1j_edf1._.js',
  '_1luyz1c._.js'
)

$old = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384}];'
$new = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384},{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}];'
# The fragment that must be present after the edit. (The untracked producer's own
# VERIFY_NEW dropped the closing '/' of the regex, so its rerun check could never
# match and it was not idempotent; this fragment matches the bytes actually written.)
$verify = '{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}'

function Get-OccurrenceCount([string]$haystack, [string]$needle) {
  if ([string]::IsNullOrEmpty($needle)) { return 0 }
  $n = 0; $i = 0
  while (($i = $haystack.IndexOf($needle, $i)) -ge 0) { $n++; $i += $needle.Length }
  return $n
}

$results = [System.Collections.Generic.List[string]]::new()

Write-Host "`n=== Compiled static output-cap array (qwen3-235b clamp) ==="
foreach ($f in $chunkFiles) {
  $p = Join-Path $chunksDir $f
  if (-not (Test-Path $p)) {
    $results.Add("ERROR $f (file not found)")
    continue
  }
  $content = [System.IO.File]::ReadAllText($p)
  if ($content.IndexOf($verify) -ge 0) {
    $results.Add("SKIP  $f (already patched)")
    continue
  }
  $found = Get-OccurrenceCount $content $old
  if ($found -ne 1) {
    $results.Add("ERROR $f (anchor found $found times, expected 1 - version changed?)")
    continue
  }
  if ($DryRun) {
    $results.Add("DRYRUN $f (would insert scaleway cap entry)")
    continue
  }
  $bak = "$p.autoos-backup-$ts"
  Copy-Item $p $bak -Force
  $patched = $content.Replace($old, $new)
  [System.IO.File]::WriteAllText($p, $patched, $utf8NoBom)
  $after = [System.IO.File]::ReadAllText($p)
  if ($after.IndexOf($verify) -ge 0 -and (Get-OccurrenceCount $after $old) -eq 0) {
    $results.Add("PATCH $f (backup: $(Split-Path $bak -Leaf))")
  } else {
    $results.Add("ERROR $f (post-write verification failed)")
  }
}

Write-Host "`n=== Results ==="
$patched = 0; $skipped = 0; $errors = 0
foreach ($r in $results) {
  Write-Host "  $r"
  if ($r.StartsWith('PATCH')) { $patched++ }
  elseif ($r.StartsWith('SKIP')) { $skipped++ }
  elseif ($r.StartsWith('ERROR')) { $errors++ }
}
Write-Host "`n  $patched patched, $skipped skipped, $errors errors"
if ($errors -gt 0) { exit 1 } else { exit 0 }
