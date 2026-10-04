#Requires -Version 5.1
<#
  AutoOS cost gate - scheduled-task wrapper (runs as the logged-in user, every
  10 minutes via the "AutoOS cost gate" Scheduled Task).

  WHAT IT DOES
    (a) Pages the recent call-log rows through the omniroute CLI's own
        authenticated helper and writes them as a JSON array to
        %LOCALAPPDATA%\autoos\daily-gate-rows.json (tmp file, then
        Move-Item -Force over the destination).
    (b) Runs `python <RepoRoot>\tools\cost-gate-refresh.py --rows <that file>`,
        which re-writes the daily gate file and the status line.

  THE EXPORT, VERBATIM - the exact command this wrapper runs in node, one
  page at a time:
      node --input-type=module -e "
        import { pathToFileURL } from 'url';
        const { apiFetch } = await import(pathToFileURL(process.env.AUTOS_API).href);
        const path = '/api/usage/call-logs?limit=500&offset=0&excludeTests=1';
        const r = await apiFetch(path, { method: 'GET', raw: true });
        const t = await r.text();
        process.stdout.write(String(r.status) + '\n' + t);
      "
  Pages /api/usage/call-logs?limit=<n>&offset=<k>&excludeTests=1 through the
  omniroute CLI's own authenticated helper (node, `npm root -g` +
  omniroute/bin/cli/api.mjs, function apiFetch(path, {method:'GET', raw:true}))
  and repeats with offset += 500 until the rows are older than two days or a
  page cap (20 pages) is reached.

  NO CREDENTIALS TOUCHED: the helper authenticates from inside its own
  omniroute install; this script reads, prints, writes and passes no
  credential of any kind, opens no credential store file, and uses no
  elevated scope. The gateway body is written to the rows file and nowhere
  else.

  EXIT CODES
    0  rows written and the refresh ran (the refresh decides the verdict)
    3  export failed (node missing, non-200, non-JSON, or empty): the
       status file says "UNAVAILABLE: rows export failed", the old gate
       file is kept, and nothing else changed.
#>
param([Parameter(Mandatory)][string]$RepoRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$autoosDir  = Join-Path $env:LOCALAPPDATA 'autoos'
$rowsPath   = Join-Path $autoosDir 'daily-gate-rows.json'
$statusPath = Join-Path $autoosDir 'daily-gate.status'
$pageLimit  = 500
$maxPages   = 20
$cutoffDays = 2

# Resolve the helper module the same way the operator's CLI does.
$apiModule = $null
if (Get-Command -Name 'npm' -ErrorAction SilentlyContinue) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $npmRoot = (npm root -g 2>$null | Out-String).Trim() }
    finally { $ErrorActionPreference = $prevEap }
    if ($npmRoot) { $apiModule = Join-Path $npmRoot 'omniroute\bin\cli\api.mjs' }
}
if ($apiModule -and -not (Test-Path -LiteralPath $apiModule)) { $apiModule = $null }

function Write-StatusUnavailable {
    param([string]$Reason)
    # One line, UTC time first, in the shared daily-gate status format.
    $iso = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    try {
        if (-not (Test-Path -LiteralPath $autoosDir)) {
            [void](New-Item -ItemType Directory -Path $autoosDir -Force)
        }
        $tmp = Join-Path $autoosDir 'daily-gate.status.tmp'
        [System.IO.File]::WriteAllText($tmp, "$iso UNAVAILABLE: $Reason`n",
            (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $tmp -Destination $statusPath -Force
    } catch {
        Write-Error "cost gate: export failed ($Reason) and the status write also failed"
    }
}

function Invoke-NativeNoError {
    <#
      .SYNOPSIS
        Run a native command, keeping its stdout, and ignore its stderr.
      .DESCRIPTION
        PowerShell 5.1 turns a native command's stderr into a terminating
        error under $ErrorActionPreference = 'Stop'; a local downgrade to
        Continue (and stderr redirected away) keeps a noisy but successful
        process from being read as a failure. $LASTEXITCODE survives.
    #>
    param([string]$Command, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command @Arguments 2>$null | Out-String }
    finally { $ErrorActionPreference = $prev }
}

function Export-CallLogRows {
    <#
      .SYNOPSIS
        Page the gateway's call log through the omniroute CLI helper.
      .OUTPUTS
        The accumulated rows, or $null when the export failed.
      .DESCRIPTION
        Each page is one node process: the helper authenticates inside the
        omniroute install, so this script sees no credential at all. The
        response body is a JSON array of rows; paging stops when a page's
        oldest timestamp is older than the cutoff, the page is short, or the
        page cap is reached. Any missing tool, non-200 status, non-JSON body
        or empty log reads as a failed export.
    #>
    if (-not (Get-Command -Name 'node' -ErrorAction SilentlyContinue)) { return $null }
    if (-not $apiModule) { return $null }
    # pathToFileURL turns a Windows path into the file:// form node's ESM
    # loader requires (a bare C:\... path is not a valid module URL there).
    $nodeScript = @"
import { pathToFileURL } from 'url';
const { apiFetch } = await import(pathToFileURL(process.env.AUTOS_API).href);
const limit = Number(process.env.AUTOS_LIMIT);
const offset = Number(process.env.AUTOS_OFFSET);
const path = '/api/usage/call-logs?limit=' + limit + '&offset=' + offset + '&excludeTests=1';
try {
    const r = await apiFetch(path, { method: 'GET', raw: true });
    const t = await r.text();
    process.stdout.write(String(r.status) + '\n' + t);
} catch (e) {
    process.stdout.write('0\n' + String(e && e.message || 'network error'));
}
"@
    $rows = @()
    for ($page = 0; $page -lt $maxPages; $page++) {
        $env:AUTOS_API = $apiModule
        $env:AUTOS_LIMIT = "$pageLimit"
        $env:AUTOS_OFFSET = "$($page * $pageLimit)"
        $out = Invoke-NativeNoError -Command 'node' -Arguments @('--input-type=module', '-e', $nodeScript)
        Remove-Item Env:\AUTOS_API, Env:\AUTOS_LIMIT, Env:\AUTOS_OFFSET -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) { return $null }
        $nl = $out.IndexOf("`n")
        if ($nl -lt 1) { return $null }
        $status = 0
        if (-not [int]::TryParse($out.Substring(0, $nl).Trim(), [ref]$status) -or $status -ne 200) { return $null }
        try {
            $pageRows = $out.Substring($nl + 1) | ConvertFrom-Json
        } catch {
            return $null
        }
        $count = @($pageRows).Count
        if ($count -eq 0) {
            # An empty log on the very first page is not a valid export.
            if ($page -eq 0) { return $null }
            break
        }
        $rows += $pageRows
        $cutoff = (Get-Date).ToUniversalTime().AddDays(-$cutoffDays)
        $oldest = $null
        foreach ($r in $pageRows) {
            if (-not $r.PSObject.Properties['timestamp']) { continue }
            $stamp = [DateTime]::MinValue
            if (-not [DateTime]::TryParse($r.timestamp, [ref]$stamp)) { continue }
            if ($stamp.Kind -ne [System.DateTimeKind]::Utc) { $stamp = $stamp.ToUniversalTime() }
            if ($null -eq $oldest -or $stamp -lt $oldest) { $oldest = $stamp }
        }
        if ($count -lt $pageLimit -or $null -ne $oldest -and $oldest -lt $cutoff) { break }
    }
    if ($rows.Count -eq 0) { return $null }
    $rows
}

function ConvertTo-AutoOSRowsJson {
    param([object[]]$Rows)
    # Force an array: a single row would otherwise serialize as a bare JSON
    # object (ConvertTo-Json unrolls single-element arrays), and the refresh
    # script rejects anything that is not a JSON array of rows.
    ConvertTo-Json -InputObject @($Rows) -Depth 12 -Compress
}

function Write-RowsFile {
    param([object[]]$Rows)
    $tmp = Join-Path $autoosDir 'daily-gate-rows.json.tmp'
    [System.IO.File]::WriteAllText($tmp, (ConvertTo-AutoOSRowsJson -Rows $Rows),
        (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $rowsPath -Force
}

try {
    if (-not (Test-Path -LiteralPath $autoosDir)) {
        [void](New-Item -ItemType Directory -Path $autoosDir -Force)
    }
    $rows = Export-CallLogRows
    if ($null -eq $rows) {
        Write-StatusUnavailable 'rows export failed'
        exit 3
    }
    Write-RowsFile -Rows $rows
} catch {
    Write-StatusUnavailable 'rows export failed'
    exit 3
}

$refresh = Join-Path $repo 'tools\cost-gate-refresh.py'
$python = (Get-Command -Name 'python' -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-StatusUnavailable 'rows export failed'
    exit 3
}
$ErrorActionPreference = 'Continue'
try {
    & $python $refresh '--rows' $rowsPath 2>&1 | Out-String | Out-Host
    exit $LASTEXITCODE
} finally {
    $ErrorActionPreference = 'Stop'
}
