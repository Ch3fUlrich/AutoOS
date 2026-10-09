#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Compact Omnigraph graph stores: prune old Lance manifest versions, then compact
  small fragments. Reclaims manifest bloat; does NOT touch graph content.

.DESCRIPTION
  Omnigraph rewrites the WHOLE manifest on every commit, and each manifest grows
  ~173 bytes per commit. Total manifest storage is therefore O(commits^2):

      agent-skills, measured 2026-09-11
        version     1 ->   1.6 KB
        version  1000 ->   206 KB
        version  2678 ->   497 KB
        2678 versions ->   692 MB of _versions, for 53 nodes + 88 edges

  Resolving "latest version" LISTs that whole prefix, so read latency is O(N) too
  (43.5 s for one LIST on the Windows bind-mount store). This is why the server
  thrashes at boot and why a trivial query takes seconds.

  This is NOT what dedup-graph.py fixes. That script collapses nodes that share a
  casefolded slug, and it exits 0 without touching the store when there are none
  -- which is the normal case. The bloat is commit history, not duplicate rows.

  Compaction is `omnigraph cleanup` + `omnigraph optimize`, which ship in the
  server image. Nothing about the write pipeline changes.

.PARAMETER Keep
  Recent versions to retain per table. Default 5.

.PARAMETER Graphs
  Graph ids to compact. Default: every graph in the store.

.PARAMETER DryRun
  Report only. Omits --confirm, so `cleanup` prints its policy and exits.

.PARAMETER Go
  Fleet rule D-825: a judge GO naming this change — a judge run id (YYYYMMDD-HHMMSS-...),
  a decision id D-<n> or an OS-<n> item. Required for any run that is not -DryRun; the
  live compaction refuses without it.

.PARAMETER GoSha
  'git rev-parse HEAD' of this checkout. Must equal the checkout's HEAD so the GO names
  the exact code being applied. Required with -Go on a live run.

.PARAMETER GoOffline
  Proceed although this machine cannot read the artefact the GO names (a missing or
  unreadable routing dir, no worker record on disk). The run logs 'GO-OFFLINE: <ref>
  unverified' as its first line; -GoSha is still verified.

.EXAMPLE
  ./compact-graphs.ps1 -DryRun
  ./compact-graphs.ps1 -Keep 5 -Go D-825 -GoSha (git rev-parse HEAD)
  ./compact-graphs.ps1 -Graphs agent-skills -Go <ref> -GoSha <sha>

.NOTES
  Take a logical backup first -- this is the same safety bar dedup-graph.py uses:
    omnigraph export --server local --graph <g> > <g>.jsonl
  Recovery from one is `omnigraph load --mode overwrite`.

  The stack is stopped while compacting (the server holds the datasets open) and
  is ALWAYS restarted, including on failure or Ctrl-C.
#>
[CmdletBinding()]
param(
  [int]$Keep = 5,
  [string[]]$Graphs,
  [switch]$DryRun,
  [string]$Go = '',
  [string]$GoSha = '',
  [switch]$GoOffline
)

$ErrorActionPreference = 'Stop'
$Image = 'modernrelay/omnigraph-server:v0.8.1'
$Stopped = @()

# --- Fleet rule D-825: compaction converges the live store only on an explicit GO ---
# A non-dry run stops and restarts the live omnigraph-server and rewrites each graph's
# manifest storage (cleanup + optimize --yes) against the running store, so it mutates
# shared infrastructure. It refuses without -Go <ref> naming the approving artefact (a
# judge run id YYYYMMDD-HHMMSS-..., a decision id D-<n> or an OS-<n> item) and -GoSha
# equal to 'git rev-parse HEAD' of this checkout, so the GO covers the exact code applied.
# -DryRun needs none and is unchanged; a -Go there is echoed only. This runs before the
# docker calls below, so a refused run touches nothing. A GO of the right SHAPE is not
# yet a GO: the ref must name an artefact that EXISTS - a line with that exact id in the
# routing decisions log, a QUESTIONS.md / ANSWERS.md item, a worker record for a judge
# run id - and an unreadable source refuses too, unless -GoOffline declared the
# exception, which logs 'GO-OFFLINE: <ref> unverified' and still verifies -GoSha.
if (-not $DryRun) {
    $ErrorActionPreference = 'Continue'
    if (-not $Go) {
        [Console]::Error.WriteLine('compact-graphs.ps1: refusing to compact the live omnigraph store without -Go <ref> (fleet rule D-825).')
        [Console]::Error.WriteLine('  Stopping and restarting the running server and rewriting every graph manifest mutates shared infrastructure; one runs only on an explicit judge GO naming the sha and scope it covers.')
        [Console]::Error.WriteLine("  Pass -Go <ref> -GoSha <sha>, where <ref> is a judge run id (YYYYMMDD-HHMMSS-...), a decision id D-<n> or an OS-<n> item, and <sha> is 'git rev-parse HEAD' of this checkout. Use -DryRun to inspect.")
        exit 2
    }
    if (-not $GoSha) {
        [Console]::Error.WriteLine('compact-graphs.ps1: refusing to compact the live omnigraph store without -GoSha <sha> (fleet rule D-825).')
        [Console]::Error.WriteLine("  <sha> must equal 'git rev-parse HEAD' of this checkout, so the GO names the exact code applied.")
        exit 2
    }
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
    $CheckoutHead = ( (& git -C $Root rev-parse HEAD 2>$null) | Select-Object -First 1 )
    if ($CheckoutHead) { $CheckoutHead = "$CheckoutHead".Trim() }
    if (-not $CheckoutHead) {
        [Console]::Error.WriteLine("compact-graphs.ps1: cannot read this checkout's HEAD (git rev-parse failed in $Root) - refusing to mutate.")
        exit 2
    }
    if ($GoSha -ne $CheckoutHead) {
        [Console]::Error.WriteLine("compact-graphs.ps1: -GoSha '$GoSha' is not this checkout's HEAD '$CheckoutHead'.")
        [Console]::Error.WriteLine('  The GO must name the exact sha of the code being applied (fleet rule D-825).')
        exit 2
    }
    # The reference's shape and its EXISTENCE are one implementation, the same
    # _go_gate.py the bash gates exec and the Python live-store tools import, reached
    # here through invoke-go-gate.ps1 - never re-derived in this file.
    $goVerdict = & (Join-Path $PSScriptRoot 'invoke-go-gate.ps1') `
        -Tool 'compact-graphs.ps1' -Ref $Go -Root $Root -Offline:$GoOffline
    foreach ($goLine in $goVerdict.Err) { [Console]::Error.WriteLine($goLine) }
    if ($goVerdict.Code -ne 0) { exit 2 }
    foreach ($goLine in $goVerdict.Out) { Write-Host $goLine }
    Write-Host "GO: $Go sha=$CheckoutHead"
    $ErrorActionPreference = 'Stop'
} elseif ($Go) {
    $goShaShown = if ($GoSha) { $GoSha } else { 'none' }
    Write-Host "GO: $Go sha=$goShaShown (read-only run - no gate applies)"
}

function Get-DirMB($path) {
  if (-not (Test-Path $path)) { return 0.0 }
  $s = Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
       Measure-Object -Property Length -Sum
  return [math]::Round($s.Sum / 1MB, 1)
}

# --- resolve the live stack rather than assuming it -------------------------
$net = docker inspect omnigraph-server --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
if (-not $net) { throw "omnigraph-server not found; is the stack up?" }

$cluster = (docker inspect omnigraph-server --format '{{range .Config.Env}}{{println .}}{{end}}' |
            Select-String '^OMNIGRAPH_CLUSTER=').ToString().Split('=', 2)[1].Trim()

$dataRoot = docker inspect omnigraph-minio --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}'
$graphsDir = Join-Path $dataRoot 'omnigraph\cluster\graphs'

# S3 credentials/endpoint: copy exactly what the server uses.
$envFile = Join-Path ([System.IO.Path]::GetTempPath()) "og-compact-$PID.env"
docker inspect omnigraph-server --format '{{range .Config.Env}}{{println .}}{{end}}' |
  Select-String '^(AWS_|OMNIGRAPH_CLUSTER)' |
  ForEach-Object { $_.ToString() } |
  Set-Content -Path $envFile -Encoding ascii

if (-not $Graphs) {
  $Graphs = Get-ChildItem $graphsDir -Directory | ForEach-Object { $_.Name -replace '\.omni$', '' }
}

Write-Host "cluster : $cluster"
Write-Host "network : $net"
Write-Host "store   : $graphsDir"
Write-Host "graphs  : $($Graphs -join ', ')"
Write-Host "keep    : $Keep$(if ($DryRun) { '   [DRY RUN]' })`n"

$before = @{}
foreach ($g in $Graphs) { $before[$g] = Get-DirMB (Join-Path $graphsDir "$g.omni") }

try {
  if (-not $DryRun) {
    # MinIO stays up: cleanup talks S3 to it. Only the server pins dataset handles.
    Write-Host "stopping omnigraph-server, omnigraph-viewer ..."
    docker stop omnigraph-server omnigraph-viewer | Out-Null
    $Stopped = @('omnigraph-minio', 'omnigraph-server', 'omnigraph-viewer')
  }

  foreach ($g in $Graphs) {
    Write-Host "`n--- $g ---"
    $args = @('cleanup', '--cluster', $cluster, '--graph', $g, '--keep', "$Keep")
    if (-not $DryRun) { $args += @('--confirm', '--yes') }
    docker run --rm --network $net --env-file $envFile --entrypoint omnigraph $Image @args

    if (-not $DryRun) {
      docker run --rm --network $net --env-file $envFile --entrypoint omnigraph $Image `
        optimize --cluster $cluster --graph $g --yes
    }
  }
}
finally {
  Remove-Item $envFile -Force -ErrorAction SilentlyContinue
  if ($Stopped) {
    Write-Host "`nrestarting stack ..."
    docker start omnigraph-server omnigraph-viewer | Out-Null
  }
}

# --- report -----------------------------------------------------------------
Write-Host "`n=== result ==="
$rows = foreach ($g in $Graphs) {
  $after = Get-DirMB (Join-Path $graphsDir "$g.omni")
  [PSCustomObject]@{
    Graph    = $g
    BeforeMB = $before[$g]
    AfterMB  = $after
    FreedMB  = [math]::Round($before[$g] - $after, 1)
    Versions = (Get-ChildItem (Join-Path $graphsDir "$g.omni\__manifest\_versions") -Directory -ErrorAction SilentlyContinue).Count
  }
}
$rows | Sort-Object FreedMB -Descending | Format-Table -AutoSize
"total freed: {0} MB" -f [math]::Round(($rows | Measure-Object FreedMB -Sum).Sum, 1)

if (-not $DryRun) {
  try {
    $h = Invoke-WebRequest -Uri 'http://localhost:8080/healthz' -TimeoutSec 30 -UseBasicParsing
    Write-Host "healthz: HTTP $($h.StatusCode) $($h.Content)"
  } catch {
    Write-Warning "server not answering yet: $($_.Exception.Message)"
  }
}
