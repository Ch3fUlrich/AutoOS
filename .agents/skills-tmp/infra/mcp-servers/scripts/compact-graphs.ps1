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

.EXAMPLE
  ./compact-graphs.ps1 -DryRun
  ./compact-graphs.ps1 -Keep 5
  ./compact-graphs.ps1 -Graphs agent-skills

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
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Image = 'modernrelay/omnigraph-server:v0.8.1'
$Stopped = @()

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
