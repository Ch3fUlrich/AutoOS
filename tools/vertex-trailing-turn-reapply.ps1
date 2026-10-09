#Requires -Version 5.1
<#
.SYNOPSIS
  Idempotent reapply of the Vertex/Gemini turn-shape guard (v3, D-908).

.DESCRIPTION
  OmniRoute v3.8.50 rejects a Gemini/Vertex request whose contents end on a
  role:"model" turn ("Requests ending with a model turn are not supported." 400),
  and (D-908) it also 400s a body whose LAST turn is a user turn that MIXES a
  functionResponse part with a text part on the signed tool path -- even though
  that body ends on "user", so a trailing-turn strip alone never ran. The v3 fix
  rewrites every mixed user turn into user[functionResponse parts], a model
  ["Noted."] turn, and user[the other parts], then strips the trailing model run.

  v1 of this fix tested `contents.length > 1`, so it never popped a body whose
  contents is ONE lone model turn -- the shape that reproduces the 400 (lane
  VERTEX-GUARD replay, steps 5/9/10) -- and `>= 1` alone empties the array, which
  Vertex rejects too. v2 pops every trailing model turn and, when that empties
  contents, appends one synthetic user turn. v3 (this script, lane VERTEX-LIVE,
  D-908) keeps the v2 pop/refill and first splits every mixed tool/text user turn.
  Rationale and the trade-off are in
  configuration/omniroute/vertex-trailing-turn-README.md.

  Two layers are patched:
    1. open-sse/translator/request/openai-to-gemini.ts
       (source-tree consistency; the compiled worker does not load it).
    2. the compiled Turbopack chunks that carry the mergeConsecutiveSameRoleContents
       call site -- these are what the running gateway executes:
         _08_y1bx._.js, _18ct13i._.js, _1j_edf1._.js, _1luyz1c._.js  (vars f / o)
         _15ose6x._.js, _1xkpq2s._.js                                (vars m / s)

  `npm update omniroute` replaces the package and reverts every patch; re-run this
  script afterwards. A package already carrying the v1 or v2 guard is upgraded in
  place: every site is decided as already-v3 (SKIP), pristine/v1/v2 (PATCH), or
  unknown (ERROR - the anchor table no longer describes the file, so nothing is
  guessed).

  Each file is backed up to <file>.autoos-backup-<timestamp> before its first
  modification. The script is idempotent: a second run reports SKIP for every file
  and exits 0.

  All 13 sites below were verified to match exactly once in the pristine
  omniroute@3.8.50 npm tarball, the v1 forms to match exactly once in
  autoos/omniroute:3.8.50-autoos3, and the v2 forms to match exactly once in
  autoos/omniroute:3.8.50-autoos4. The equivalently-anchored chunk-only companion
  is tools/apply-vertex-patch.py; a test asserts the two tables are byte-identical.

.PARAMETER Path
  Root of the omniroute package. Defaults to the npm-global install.
.PARAMETER NoBackup
  Do not write .autoos-backup-<timestamp> copies (the file content is still changed).
.EXAMPLE
  pwsh -File vertex-trailing-turn-reapply.ps1
.EXAMPLE
  pwsh -File vertex-trailing-turn-reapply.ps1 -Path C:\omniroute-dev\node_modules\omniroute
.NOTES
  AutoOS lane F1-vertex    | 2026-09-30 | omniroute v3.8.50 (v1 guard)
  AutoOS lane VERTEX-GUARD | 2026-10-09 | omniroute v3.8.50 (v2 guard + upgrade)
  AutoOS lane VERTEX-LIVE  | 2026-10-09 | omniroute v3.8.50 (v3 split + upgrade, D-908)
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

function Set-Site {
  param(
    [string]$FilePath,
    [string]$Label,
    [string]$Done,
    [hashtable[]]$Forms
  )
  $content = [System.IO.File]::ReadAllText($FilePath)
  if ($content.IndexOf($Done, [System.StringComparison]::Ordinal) -ge 0) {
    $results.Add("SKIP  $Label (v3 guard present)")
    return
  }
  # The first form that matches exactly once wins, so a v2 or v1 site upgrades
  # instead of being re-anchored on the shorter pristine text it also contains.
  $chosen = $null
  foreach ($form in $Forms) {
    $count = ([regex]::Matches($content, [regex]::Escape($form.Find))).Count
    if ($count -gt 1) {
      $results.Add("ERROR $Label ($count matches of the $($form.State) anchor, expected 1)")
      return
    }
    if ($count -eq 1 -and $null -eq $chosen) { $chosen = $form }
  }
  if ($null -eq $chosen) {
    $results.Add("ERROR $Label (no anchor form matched - package text changed, refusing to guess)")
    return
  }
  $bak = Backup-File $FilePath
  [System.IO.File]::WriteAllText($FilePath, $content.Replace($chosen.Find, $chosen.Replace), $utf8NoBom)
  $results.Add("PATCH $Label (v3 guard over $($chosen.State) text, backup: $bak)")
}

# --- 1. Compiled chunks (runtime fix) --------------------------------
# chunk, variable, v2 text, v1 text, pristine text, v3 text -- byte-identical to
# the table in tools/apply-vertex-patch.py.
$chunkSites = @(
  @('_08_y1bx._.js', 'f',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{f.contents=f.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)'),
  @('_08_y1bx._.js', 'o',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);o.contents=o.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools'),
  @('_18ct13i._.js', 'f',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{f.contents=f.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)'),
  @('_18ct13i._.js', 'o',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);o.contents=o.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools'),
  @('_1j_edf1._.js', 'f',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{f.contents=f.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)'),
  @('_1j_edf1._.js', 'o',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);o.contents=o.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools'),
  @('_1luyz1c._.js', 'f',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),f},null)',
    'mergeConsecutiveSameRoleContents)(f.contents),(()=>{f.contents=f.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;f.contents.length&&"model"===f.contents[f.contents.length-1].role;)f.contents.pop();f.contents.length||f.contents.push({role:"user",parts:[{text:"Continue."}]})})(),f},null)'),
  @('_1luyz1c._.js', 'o',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools',
    'mergeConsecutiveSameRoleContents)(o.contents??[]);o.contents=o.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;o.contents.length&&"model"===o.contents[o.contents.length-1].role;)o.contents.pop();o.contents.length||o.contents.push({role:"user",parts:[{text:"Continue."}]});let _=t.tools'),
  @('_15ose6x._.js', 'm',
    'mergeConsecutiveSameRoleContents)(m.contents),(()=>{for(;m.contents.length&&"model"===m.contents[m.contents.length-1].role;)m.contents.pop();m.contents.length||m.contents.push({role:"user",parts:[{text:"Continue."}]})})(),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),(()=>{m.contents=m.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;m.contents.length&&"model"===m.contents[m.contents.length-1].role;)m.contents.pop();m.contents.length||m.contents.push({role:"user",parts:[{text:"Continue."}]})})(),m},null)'),
  @('_15ose6x._.js', 's',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);for(;s.contents.length&&"model"===s.contents[s.contents.length-1].role;)s.contents.pop();s.contents.length||s.contents.push({role:"user",parts:[{text:"Continue."}]});let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);s.contents=s.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;s.contents.length&&"model"===s.contents[s.contents.length-1].role;)s.contents.pop();s.contents.length||s.contents.push({role:"user",parts:[{text:"Continue."}]});let A=t.tools'),
  @('_1xkpq2s._.js', 'm',
    'mergeConsecutiveSameRoleContents)(m.contents),(()=>{for(;m.contents.length&&"model"===m.contents[m.contents.length-1].role;)m.contents.pop();m.contents.length||m.contents.push({role:"user",parts:[{text:"Continue."}]})})(),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),m},null)',
    'mergeConsecutiveSameRoleContents)(m.contents),(()=>{m.contents=m.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;m.contents.length&&"model"===m.contents[m.contents.length-1].role;)m.contents.pop();m.contents.length||m.contents.push({role:"user",parts:[{text:"Continue."}]})})(),m},null)'),
  @('_1xkpq2s._.js', 's',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);for(;s.contents.length&&"model"===s.contents[s.contents.length-1].role;)s.contents.pop();s.contents.length||s.contents.push({role:"user",parts:[{text:"Continue."}]});let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools',
    'mergeConsecutiveSameRoleContents)(s.contents??[]);s.contents=s.contents.reduce((a,c)=>{const q=c.parts||[];if("user"!==c.role){a.push(c);return a}const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);if(r.length&&x.length){a.push({role:"user",parts:r});a.push({role:"model",parts:[{text:"Noted."}]});a.push({role:"user",parts:x})}else{a.push(c)}return a},[]);for(;s.contents.length&&"model"===s.contents[s.contents.length-1].role;)s.contents.pop();s.contents.length||s.contents.push({role:"user",parts:[{text:"Continue."}]});let A=t.tools')
)

$chunksDir = Join-Path $Path 'dist/.build/next/server/chunks'
Write-Host ''
Write-Host '=== Compiled chunk patches (runtime fix) ==='
foreach ($site in $chunkSites) {
  $file = $site[0]; $var = $site[1]; $v2 = $site[2]; $v1 = $site[3]; $pristine = $site[4]; $v3 = $site[5]
  $p = Join-Path $chunksDir $file
  if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
    $results.Add("ERROR chunk $file $var (file not found)")
    continue
  }
  Set-Site -FilePath $p -Label "chunk $file $var" -Done $v3 -Forms @(
    @{ Find = $v2; Replace = $v3; State = 'v2' },
    @{ Find = $v1; Replace = $v3; State = 'v1' },
    @{ Find = $pristine; Replace = $v3; State = 'pristine' }
  )
}

# --- 2. .ts source (source-tree consistency) -------------------------
$tsPath = Join-Path $Path 'open-sse/translator/request/openai-to-gemini.ts'
$tsFind = 'result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);'

# The em dash is injected as a codepoint so this script stays pure ASCII and is
# read identically by Windows PowerShell 5.1 and PowerShell 7 on any code page.
$tsV1Template = @'
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
$tsV2Template = @'
  // Pop every trailing model turn <EMDASH> Vertex AI rejects a request whose contents
  // end with a model turn ("Requests ending with a model turn are not supported."
  // 400). The v1 guard skipped a contents of ONE model turn, which is the body that
  // reproduces the 400, and popping can leave the array empty, which Vertex rejects
  // too: an emptied contents gets one synthetic user turn instead of a refused call,
  // because refusing is the failure this patch exists to remove. The popped model
  // text is dropped, not re-appended <EMDASH> the model continues from systemInstruction
  // and the history that is left. A body already ending on "user" is never touched.
  while (result.contents.length > 0) {
    const lastContent = result.contents[result.contents.length - 1];
    if (lastContent.role !== "model") {
      break;
    }
    result.contents.pop();
  }
  if (result.contents.length === 0) {
    result.contents.push({ role: "user", parts: [{ text: "Continue." }] });
  }
'@
$tsV3Template = @'
  // Split mixed user turns <EMDASH> Vertex AI 400s a user turn carrying BOTH a
  // functionResponse part and a text part on the signed tool path (D-908), even
  // though the body ends on "user". Each such turn becomes a user turn of the
  // functionResponse parts, a model "Noted." turn, and a user turn of the rest;
  // text-only and functionResponse-only turns are untouched.
  result.contents = result.contents.reduce((acc, c) => {
    if (c.role !== "user") {
      acc.push(c);
      return acc;
    }
    const responses = c.parts.filter((p) => p.functionResponse);
    const others = c.parts.filter((p) => !p.functionResponse);
    if (responses.length && others.length) {
      acc.push({ role: "user", parts: responses });
      acc.push({ role: "model", parts: [{ text: "Noted." }] });
      acc.push({ role: "user", parts: others });
    } else {
      acc.push(c);
    }
    return acc;
  }, []);
  // Pop every trailing model turn <EMDASH> Vertex AI rejects a request whose contents
  // end with a model turn ("Requests ending with a model turn are not supported."
  // 400). The v1 guard skipped a contents of ONE model turn, which is the body that
  // reproduces the 400, and popping can leave the array empty, which Vertex rejects
  // too: an emptied contents gets one synthetic user turn instead of a refused call,
  // because refusing is the failure this patch exists to remove. The popped model
  // text is dropped, not re-appended <EMDASH> the model continues from systemInstruction
  // and the history that is left. A body already ending on "user" is never touched.
  while (result.contents.length > 0) {
    const lastContent = result.contents[result.contents.length - 1];
    if (lastContent.role !== "model") {
      break;
    }
    result.contents.pop();
  }
  if (result.contents.length === 0) {
    result.contents.push({ role: "user", parts: [{ text: "Continue." }] });
  }
'@
$tsV1 = ($tsV1Template -replace "`r`n", "`n").Replace('<EMDASH>', [string][char]0x2014)
$tsV2 = ($tsV2Template -replace "`r`n", "`n").Replace('<EMDASH>', [string][char]0x2014)
$tsNew = ($tsV3Template -replace "`r`n", "`n").Replace('<EMDASH>', [string][char]0x2014)
$tsPatched = $tsFind + "`n`n" + $tsNew
# The previous scripts wrote the block with LF newlines; accept a CRLF copy too so
# a source tree checked out with autocrlf still upgrades instead of doubling up.
$tsV1Crlf = ($tsFind + "`n`n" + $tsV1) -replace "`n", "`r`n"
$tsV2Crlf = ($tsFind + "`n`n" + $tsV2) -replace "`n", "`r`n"

Write-Host ''
Write-Host '=== Source patch (.ts consistency) ==='
if (Test-Path -LiteralPath $tsPath -PathType Leaf) {
  Set-Site -FilePath $tsPath -Label 'openai-to-gemini.ts source' -Done $tsPatched -Forms @(
    @{ Find = ($tsFind + "`n`n" + $tsV2); Replace = $tsPatched; State = 'v2' },
    @{ Find = $tsV2Crlf; Replace = $tsPatched; State = 'v2-crlf' },
    @{ Find = ($tsFind + "`n`n" + $tsV1); Replace = $tsPatched; State = 'v1' },
    @{ Find = $tsV1Crlf; Replace = $tsPatched; State = 'v1-crlf' },
    @{ Find = $tsFind; Replace = $tsPatched; State = 'pristine' }
  )
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
