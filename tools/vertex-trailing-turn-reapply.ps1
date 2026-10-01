#Requires -Version 5.1
<#
.SYNOPSIS
  Idempotent reapply of the Vertex/Gemini trailing-model-turn strip fix.

.DESCRIPTION
  OmniRoute v3.8.50 rejects a Gemini/Vertex request whose contents end on a
  role:"model" turn ("Requests ending with a model turn are not supported." 400).
  The fix strips a trailing model turn inside the openai-to-gemini base
  translator, at the mergeConsecutiveSameRoleContents call site.

  Two layers are patched:
    1. open-sse/translator/request/openai-to-gemini.ts
       (source-tree consistency; the compiled worker does not load it).
    2. the compiled Turbopack chunks that carry the mergeConsecutiveSameRoleContents
       call site -- these are what the running gateway executes:
         _08_y1bx._.js, _18ct13i._.js, _1j_edf1._.js, _1luyz1c._.js  (vars f / o)
         _15ose6x._.js, _1xkpq2s._.js                                (vars m / s)

  `npm update omniroute` replaces the package and reverts every patch; re-run this
  script afterwards.

  Each file is backed up to <file>.autoos-backup-<timestamp> before its first
  modification. The script is idempotent: a second run reports SKIP for every file
  and exits 0.

  All 13 anchors below were verified to match exactly once in the pristine
  omniroute@3.8.50 npm tarball. The equivalently-anchored chunk-only companion is
  tools/apply-vertex-patch.py.

.PARAMETER Path
  Root of the omniroute package. Defaults to the npm-global install.
.PARAMETER NoBackup
  Do not write .autoos-backup-<timestamp> copies (the file content is still changed).
.EXAMPLE
  pwsh -File vertex-trailing-turn-reapply.ps1
.EXAMPLE
  pwsh -File vertex-trailing-turn-reapply.ps1 -Path C:\omniroute-dev\node_modules\omniroute
.NOTES
  AutoOS lane F1-vertex | 2026-09-30 | omniroute v3.8.50
#>
[CmdletBinding()]
param(
  [string]$Path = (Join-Path $env:APPDATA 'npm\node_modules\omniroute'),
  [switch]$NoBackup
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$backedUpFiles = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

$pkgJsonPath = Join-Path $Path 'package.json'
if (-not (Test-Path -LiteralPath $pkgJsonPath)) {
  Write-Host "ERROR: omniroute package not found at: $Path"
  Write-Host "Pass -Path <path> to override."
  exit 1
}
try {
  $pkgJson = Get-Content -LiteralPath $pkgJsonPath -Raw | ConvertFrom-Json
  if ($pkgJson.name -ne 'omniroute') {
    Write-Host "ERROR: package at $Path has name '$($pkgJson.name)', expected 'omniroute'"
    exit 1
  }
} catch {
  Write-Host "ERROR: failed to parse $pkgJsonPath : $_"
  exit 1
}

$results = [System.Collections.Generic.List[string]]::new()

function Backup-File([string]$p) {
  if ($NoBackup) { return '<no-backup>' }
  $bak = "$p.autoos-backup-$stamp"
  if ($backedUpFiles.Contains($p)) {
    # File already backed up in pristine state earlier during this run; do not overwrite!
    return (Split-Path -Leaf $bak)
  }
  if (Test-Path -LiteralPath $bak) {
    throw "Backup destination already exists: $bak"
  }
  Copy-Item -LiteralPath $p -Destination $bak
  [void]$backedUpFiles.Add($p)
  return (Split-Path -Leaf $bak)
}

function Try-Replace([string]$p, [string]$find, [string]$replace, [string]$label) {
  $content = [System.IO.File]::ReadAllText($p)
  if ($content.IndexOf($replace, [System.StringComparison]::Ordinal) -ge 0) {
    $results.Add("SKIP  $label (already patched)")
    return
  }
  $count = ([regex]::Matches($content, [regex]::Escape($find))).Count
  if ($count -eq 0) {
    $results.Add("ERROR $label (anchor not found - package version changed?)")
    return
  }
  if ($count -gt 1) {
    $results.Add("ERROR $label ($count anchor matches, expected 1)")
    return
  }
  $bak = Backup-File $p
  [System.IO.File]::WriteAllText($p, $content.Replace($find, $replace), $utf8NoBom)
  $results.Add("PATCH $label (backup: $bak)")
}

# --- 1. Compiled chunks (runtime fix) --------------------------------
# old -> new, byte-identical to tools/apply-vertex-patch.py.
$chunkPatches = @(
  @('_08_y1bx._.js', 'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
  @('_08_y1bx._.js', 'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),
  @('_18ct13i._.js', 'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
  @('_18ct13i._.js', 'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),
  @('_1j_edf1._.js', 'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
  @('_1j_edf1._.js', 'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),
  @('_1luyz1c._.js', 'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
  @('_1luyz1c._.js', 'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),
  @('_15ose6x._.js', 'mergeConsecutiveSameRoleContents)(m.contents),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)'),
  @('_15ose6x._.js', 'mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools'),
  @('_1xkpq2s._.js', 'mergeConsecutiveSameRoleContents)(m.contents),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)'),
  @('_1xkpq2s._.js', 'mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools')
)

$chunksDir = Join-Path $Path 'dist/.build/next/server/chunks'
Write-Host ''
Write-Host '=== Compiled chunk patches (runtime fix) ==='
foreach ($patch in $chunkPatches) {
  $file = $patch[0]; $find = $patch[1]; $replace = $patch[2]
  $p = Join-Path $chunksDir $file
  if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
    $results.Add("ERROR chunk $file (file not found)")
    continue
  }
  Try-Replace $p $find $replace "chunk $file"
}

# --- 2. .ts source (source-tree consistency) -------------------------
$tsPath = Join-Path $Path 'open-sse/translator/request/openai-to-gemini.ts'
$tsFind = 'result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);'

# The em dash is injected as a codepoint so this script stays pure ASCII and is
# read identically by Windows PowerShell 5.1 and PowerShell 7 on any code page.
$tsBlock = @'
  // Strip trailing model turn <EMDASH> Vertex AI rejects requests ending with a model
  // turn ("Requests ending with a model turn are not supported." 400). The
  // existing stripTrailingAssistantForProvider in contextManager.ts only handles
  // plain-text trailing assistant messages (no tool_calls), and even that runs on
  // tb.messages before Gemini translation. The Antigravity executor handles this
  // correctly via stripTrailingAntigravityAssistantTurn; this mirrors that for the
  // standard Vertex/Gemini executor path. Guard: never strip contents down to empty.
  if (result.contents.length > 1) {
    const lastContent = result.contents[result.contents.length - 1];
    if (lastContent.role === "model") {
      result.contents.pop();
    }
  }
'@
$tsBlock = ($tsBlock -replace "`r`n", "`n").Replace('<EMDASH>', [string][char]0x2014)

Write-Host ''
Write-Host '=== Source patch (.ts consistency) ==='
if (Test-Path -LiteralPath $tsPath -PathType Leaf) {
  Try-Replace $tsPath $tsFind ($tsFind + "`n`n" + $tsBlock) 'openai-to-gemini.ts source'
}
else {
  $results.Add('ERROR openai-to-gemini.ts (file not found)')
}

# --- 3. Summary ------------------------------------------------------
Write-Host ''
Write-Host '=== Result ==='
foreach ($r in $results) { Write-Host "  $r" }

$patched = @($results | Where-Object { $_ -like 'PATCH*' }).Count
$skipped = @($results | Where-Object { $_ -like 'SKIP*' }).Count
$errors = @($results | Where-Object { $_ -like 'ERROR*' }).Count
Write-Host ''
Write-Host "Done: $patched patched, $skipped skipped, $errors errors"
if ($errors -gt 0) { exit 1 }
exit 0
