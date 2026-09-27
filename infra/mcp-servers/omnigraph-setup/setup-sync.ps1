<#
  setup-sync.ps1 — configure + schedule the Omnigraph local<->central sync (Windows).

  The Windows twin of setup-sync.sh. One command from "the stack is running" to "memory
  syncs every 5 minutes":
    1. derive what it can (LOCAL_TOKEN from ..\.env.shared, DOCKER_NET + the two local URLs
       from the RUNNING containers, DEVICE from the hostname)
    2. write omnigraph-setup\.env, MERGING rather than clobbering
    3. register a Scheduled Task
    4. prove it with a DRY RUN before anything is scheduled

  WHY IT MERGES INSTEAD OF WRITING FRESH: the one value that cannot be derived is
  CENTRAL_TOKEN — central's bearer is NOT the local one in .env.shared (they are different
  secrets). An earlier setup script in this directory overwrote a good .env with a template
  and cost the operator their credentials. An existing value always wins over a derived one,
  and nothing here writes an empty over a non-empty. Re-running is safe by construction.

  Usage:
    .\setup-sync.ps1                                  # derive, merge, schedule, dry-run
    .\setup-sync.ps1 -CentralUrl https://… -CentralToken abc…
    .\setup-sync.ps1 -NoSchedule                      # config + dry-run only
    .\setup-sync.ps1 -IntervalMinutes 15              # default 5
    .\setup-sync.ps1 -Show                            # print resolved config, change nothing
    .\setup-sync.ps1 -Unregister                      # remove the Scheduled Task

  Run in PowerShell 7 (pwsh).
#>
[CmdletBinding()]
param(
  [string]$CentralUrl,
  [string]$CentralToken,
  [int]$IntervalMinutes = 5,
  [switch]$NoSchedule,
  [switch]$Show,
  [switch]$Unregister
)
$ErrorActionPreference = 'Stop'

# UTF-8 for every hand-off — same reasoning as sync-windows.ps1: Windows does not default
# to it, and the .env we write can hold non-ASCII. A BOM here would be read back as part of
# the first key name.
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8NoBom
try { [Console]::OutputEncoding = $utf8NoBom } catch { }
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcpRoot  = Split-Path -Parent $here
$shared   = Join-Path $mcpRoot '.env.shared'
$envFile  = Join-Path $here '.env'
$sync     = Join-Path $here 'sync-windows.ps1'
$TaskName = 'Omnigraph Sync'

function Log($m)  { Write-Host "[setup-sync] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[setup-sync] OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[setup-sync] !! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "[setup-sync] ERROR: $m" -ForegroundColor Red; exit 1 }

if ($Unregister) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Ok "removed the '$TaskName' Scheduled Task. Config in .env was left alone."
  } else { Warn "no '$TaskName' Scheduled Task registered — nothing to remove." }
  exit 0
}

# --- 1. read an existing .env (values here WIN over anything derived) ------------------
function Get-EnvValue([string]$key) {
  if (-not (Test-Path $envFile)) { return $null }
  $hit = Select-String -LiteralPath $envFile -Pattern "^\s*$key\s*=\s*(.*)$" |
           Select-Object -Last 1
  if (-not $hit) { return $null }
  $hit.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
}

# --- 2. derive from .env.shared -------------------------------------------------------
if (-not (Test-Path $shared)) {
  Die @"
missing $shared
  This is the file the whole stack is keyed on. Create it first:
    cd $mcpRoot
    Copy-Item .env.shared.example .env.shared
    # then set OMNIGRAPH_TOKEN — generate one with:
    #   python -c "import secrets;print(secrets.token_hex(32))"
"@
}
$sharedToken = (Select-String -LiteralPath $shared -Pattern '^\s*OMNIGRAPH_TOKEN\s*=\s*(.*)$' |
                Select-Object -Last 1).Matches[0].Groups[1].Value.Trim().Trim('"')
if (-not $sharedToken) { Die "OMNIGRAPH_TOKEN is empty in $shared" }
if ($sharedToken -match '^(generate-with-|change-me)') {
  Die "OMNIGRAPH_TOKEN in $shared is still the placeholder. Generate a real one:`n  python -c `"import secrets;print(secrets.token_hex(32))`""
}
Log "read OMNIGRAPH_TOKEN from .env.shared ($($sharedToken.Length) chars) -> LOCAL_TOKEN"

# --- 3. derive the docker facts from what is RUNNING, never from a config file ---------
# The network name differs per host (compose project + network) and a wrong value fails
# QUIETLY: the container starts and DNS for `omnigraph-server` simply does not resolve.
$detNet = $null
if (Get-Command docker -ErrorAction SilentlyContinue) {
  $fmt = '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}'
  $detNet = (& docker inspect omnigraph-server --format $fmt 2>$null |
             Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($detNet) { $detNet = $detNet.Trim() }
}
if ($detNet) {
  Log "detected docker network from the running container: $detNet"
} else {
  $detNet = (Get-EnvValue 'DOCKER_NET'); if (-not $detNet) { $detNet = 'mcp-server_mcp-net' }
  Warn @"
omnigraph-server is not running (or docker is unavailable) — falling back to '$detNet'.
       Start the stack and re-run to have this detected instead of guessed:
         cd $mcpRoot
         docker compose --env-file .env.shared --env-file .env.server --env-file $HOME/.config/autoos/site.env -f docker-compose.server.yml up -d
"@
}

# --- 4. merge: existing > parameter > derived -----------------------------------------
function Pick([string]$key, [string]$argVal, [string]$derived) {
  $cur = Get-EnvValue $key
  if ($argVal) { return $argVal }      # explicit parameter beats everything
  if ($cur)    { return $cur }         # never clobber what is already there
  return $derived
}
$CENTRAL_URL         = Pick 'CENTRAL_URL'         $CentralUrl   $env:CENTRAL_URL
$CENTRAL_TOKEN       = Pick 'CENTRAL_TOKEN'       $CentralToken $env:CENTRAL_TOKEN
$LOCAL_TOKEN         = Pick 'LOCAL_TOKEN'         ''            $sharedToken
$LOCAL_URL           = Pick 'LOCAL_URL'           ''            'http://127.0.0.1:8080'
$LOCAL_URL_CONTAINER = Pick 'LOCAL_URL_CONTAINER' ''            'http://omnigraph-server:8080'
$DOCKER_NET          = Pick 'DOCKER_NET'          ''            $detNet
$DEVICE              = Pick 'DEVICE'              ''            $env:COMPUTERNAME.ToLower()
# Optional. Left empty unless already set or exported: guessing a viewer URL would make
# every sync retry a bogus host. Empty simply means "no attribution".
$VIEWER_URL          = Pick 'VIEWER_URL'          ''            $env:VIEWER_URL

if ($LOCAL_TOKEN -ne $sharedToken) {
  Warn @"
LOCAL_TOKEN in .env differs from OMNIGRAPH_TOKEN in .env.shared.
       The local server authenticates with .env.shared's value, so the existing one is
       probably stale. Keeping yours; delete the line from .env to re-derive it.
"@
}
if (-not $CENTRAL_URL) {
  Die @"
CENTRAL_URL is not set and cannot be derived. Pass it once and it is remembered:
  .\setup-sync.ps1 -CentralUrl https://omnigraph.example.com -CentralToken <bearer>
"@
}
if (-not $CENTRAL_TOKEN) {
  Die @"
CENTRAL_TOKEN is not set and cannot be derived. Central's bearer is NOT the local token in
.env.shared — it is a separate secret held by the server operator. Pass it once:
  .\setup-sync.ps1 -CentralToken <bearer>
"@
}

if ($Show) {
  Log 'resolved configuration (nothing written):'
  "  CENTRAL_URL=$CENTRAL_URL"
  "  CENTRAL_TOKEN=$($CENTRAL_TOKEN.Substring(0,[Math]::Min(6,$CENTRAL_TOKEN.Length)))…($($CENTRAL_TOKEN.Length) chars)"
  "  LOCAL_TOKEN=$($LOCAL_TOKEN.Substring(0,[Math]::Min(6,$LOCAL_TOKEN.Length)))…($($LOCAL_TOKEN.Length) chars)"
  "  LOCAL_URL=$LOCAL_URL"
  "  LOCAL_URL_CONTAINER=$LOCAL_URL_CONTAINER"
  "  DOCKER_NET=$DOCKER_NET"
  "  DEVICE=$DEVICE"
  exit 0
}

# --- 5. PRE-FLIGHT: prove the credentials work BEFORE touching a working .env ----------
# Same rule pull_graph.py learned the hard way: never destroy the old state until the thing
# that replaces it is known to work. Writing first and validating after means one typo'd
# -CentralToken replaces a good config with a broken one; the backup makes that recoverable,
# but only if you notice. Check first instead.
function Preflight([string]$label, [string]$url, [string]$token) {
  $u = ($url.TrimEnd('/')) + '/graphs'
  try {
    $r = Invoke-WebRequest -Uri $u -Headers @{ Authorization = "Bearer $token" } `
           -TimeoutSec 30 -SkipHttpErrorCheck -ErrorAction Stop
  } catch {
    Die @"
pre-flight FAILED: $label is unreachable at $url
  Nothing was written; your existing .env is untouched.
  Is the stack up / are you online?  curl.exe -fsS $($url.TrimEnd('/'))/healthz
  ($($_.Exception.Message))
"@
  }
  switch ($r.StatusCode) {
    200 { Log "pre-flight OK: $label answered 200 at $url" }
    { $_ -in 401, 403 } {
      Die @"
pre-flight FAILED: $label rejected the token (HTTP $($r.StatusCode)) at $url
  Nothing was written; your existing .env is untouched. Check the bearer and re-run.
"@
    }
    default {
      Die @"
pre-flight FAILED: $label answered HTTP $($r.StatusCode) at $url (expected 200)
  Nothing was written; your existing .env is untouched.
"@
    }
  }
}
Preflight 'central' $CENTRAL_URL $CENTRAL_TOKEN
Preflight 'local'   $LOCAL_URL   $LOCAL_TOKEN

# --- 6. write .env --------------------------------------------------------------------
if (Test-Path $envFile) {
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
  Copy-Item $envFile "$envFile.bak-$stamp"
  Log 'backed up the existing .env'
}
$now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$body = @"
# omnigraph sync config — generated by setup-sync.ps1 on $now.
# GITIGNORED: holds two bearer tokens. Never commit it.
# Re-run .\setup-sync.ps1 any time; it preserves whatever is already here.

# Central (authoritative) server.
CENTRAL_URL=$CENTRAL_URL
CENTRAL_TOKEN=$CENTRAL_TOKEN

# Local server — TWO views of the SAME thing, not interchangeable:
#   LOCAL_URL           reachable from THIS HOST (the published port)
#   LOCAL_URL_CONTAINER reachable from INSIDE the CLI container (compose service name)
# Mixing them up is how a pull once emptied a graph: the export worked from the host and
# the load silently had nowhere to go.
LOCAL_URL=$LOCAL_URL
LOCAL_URL_CONTAINER=$LOCAL_URL_CONTAINER
LOCAL_TOKEN=$LOCAL_TOKEN

# Docker network hosting omnigraph-server. Detected from the running container, because it
# differs per host and a wrong value fails silently (DNS just does not resolve).
DOCKER_NET=$DOCKER_NET

DEVICE=$DEVICE

# Central viewer, for the Sync log's "source" column. The viewer attributes a push to a
# device by the SOURCE IP of this ping — a commit records no client address, and actor_id
# comes from the shared bearer token (so it reads ``default`` for every device). Optional:
# empty = no attribution, sync unaffected. e.g. http://coding.example.internal:8090
VIEWER_URL=$VIEWER_URL
# GRAPHS unset => sync every graph central exposes (per-project isolation means all of them).
"@
[System.IO.File]::WriteAllText($envFile, $body, $utf8NoBom)
Ok "wrote $envFile"
# Windows has no chmod; this is the equivalent of the 0600 the bash twin sets. A file
# holding two bearer tokens should not be readable by every account on the box.
#   /inheritance:r  drop inherited ACEs (else "Users" keeps read access)
#   /grant:r        replace this identity's ACEs rather than adding to them
# icacls, not Get-Acl/Set-Acl: Set-Acl writes the WHOLE security descriptor including the
# SACL, which needs SeSecurityPrivilege — a privilege a normal shell does not hold, so it
# fails with a misleading error even though only the DACL was being changed.
$icacls = & icacls.exe $envFile /inheritance:r /grant:r "${env:USERNAME}:(F)" 2>&1
if ($LASTEXITCODE -eq 0) {
  Ok 'restricted .env to your account only (icacls)'
} else {
  Warn "could not tighten .env permissions (it still holds live tokens): $icacls"
}

# --- 7. prove it works BEFORE scheduling ----------------------------------------------
Log 'dry run (no writes) — this is the gate: nothing gets scheduled unless it passes'
$pwshExe = (Get-Process -Id $PID).Path
if (-not $pwshExe -or $pwshExe -notmatch 'pwsh|powershell') { $pwshExe = 'pwsh.exe' }
$env:DRY_RUN = '1'
& $pwshExe -NoProfile -File $sync
$dryRc = $LASTEXITCODE
Remove-Item Env:\DRY_RUN -ErrorAction SilentlyContinue
if ($dryRc -ne 0) {
  Die @"
dry run FAILED (exit $dryRc). Nothing was scheduled. Fix the above, then re-run.
Config is written, so re-running is cheap. Common causes:
  - local stack down     -> docker compose --env-file .env.shared --env-file .env.server --env-file $HOME/.config/autoos/site.env -f docker-compose.server.yml up -d
  - wrong CENTRAL_TOKEN  -> .\setup-sync.ps1 -CentralToken <bearer>
  - central unreachable  -> curl.exe -fsS $CENTRAL_URL/healthz
"@
}
Ok 'dry run passed'

if ($NoSchedule) { Ok 'done (-NoSchedule: nothing was scheduled)'; exit 0 }

# --- 8. register the Scheduled Task ---------------------------------------------------
# -Once + RepetitionInterval, not -Daily: this must repeat all day, every day.
# -RepetitionDuration is deliberately OMITTED — omitting it means "repeat indefinitely".
# The obvious spelling for forever, [TimeSpan]::MaxValue, is a trap: it serialises to
# P99999999DT23H59M59S and Task Scheduler rejects the XML outright
# ("value which is incorrectly formatted or out of range").
# Launch via wscript.exe + a VBScript stub so NO console window appears. A task that
# runs "only when logged on" flashes a conhost window every run even with
# -WindowStyle Hidden (the host is created before pwsh can hide it); wscript is
# windowless and starts pwsh with window style 0, so the 5-minute run is invisible.
$hiddenVbs = Join-Path $here 'omnigraph-sync-hidden.vbs'
$action  = New-ScheduledTaskAction -Execute 'wscript.exe' `
             -Argument "`"$hiddenVbs`" `"$pwshExe`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
             -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
# IgnoreNew: if a run overruns the interval, skip the next rather than pile up concurrent
# syncs writing to the same graphs. StartWhenAvailable catches up after sleep/downtime.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
              -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Description 'Reconcile local Omnigraph memory with the central server' `
  -Force | Out-Null
Ok "Scheduled Task '$TaskName' registered — every $IntervalMinutes min"
Get-ScheduledTask -TaskName $TaskName |
  Get-ScheduledTaskInfo |
  Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult |
  Format-List | Out-String | ForEach-Object { $_.TrimEnd() } | Write-Host

Write-Host @"

  This task runs as $env:USERNAME and ONLY while that account is logged on. That is the
  safe default: running it logged-off requires storing your password, and a laptop that
  syncs while nobody is at it buys little here.

  Run now:   Start-ScheduledTask -TaskName '$TaskName'
  History:   Get-ScheduledTaskInfo -TaskName '$TaskName'
  Remove:    .\setup-sync.ps1 -Unregister
"@

# --- 9. the OTHER env vars — agent memory, not sync -----------------------------------
# Different mechanism entirely, and the most common silent failure on this stack, so it is
# worth saying out loud at the end of a successful setup.
$netHasServer = $false
if ($env:OMNIGRAPH_NET) {
  $c = (& docker network inspect $env:OMNIGRAPH_NET --format '{{range .Containers}}{{.Name}} {{end}}' 2>$null)
  $netHasServer = ($c -match 'omnigraph-server')
}
if (-not $env:OMNIGRAPH_TOKEN -or -not $env:OMNIGRAPH_NET -or -not $netHasServer) {
  Write-Host @"

  ---------------------------------------------------------------------------------------
  SEPARATE FROM SYNC: your AGENT's memory bridge reads two env vars that are NOT set here.
  The sync you just scheduled does not need them; the MCP bridge in .mcp.json does, and
  when they are wrong it fails SILENTLY — the bridge starts and memory just never works.

    OMNIGRAPH_TOKEN = $(if ($env:OMNIGRAPH_TOKEN) { 'set' } else { 'NOT SET — the bridge will send an empty bearer' })
    OMNIGRAPH_NET   = $(if ($env:OMNIGRAPH_NET) { "$env:OMNIGRAPH_NET$(if (-not $netHasServer) { '  <-- exists but omnigraph-server is NOT on it' })" } else { "NOT SET — .mcp.json falls back to 'mcp-servers_default', which on THIS machine exists but is EMPTY" })

  Fix both (and the rest of the bridge setup) with the sibling script — it also builds the
  omnigraph-mcp image, removes any user-scope override that would silently point every repo
  at the wrong graph, and verifies by driving the real bridge:

    .\setup-agent-memory.ps1 -Check     # diagnose, change nothing
    .\setup-agent-memory.ps1            # fix

  OMNIGRAPH_NET is the DOCKER NETWORK NAME omnigraph-server is attached to — the detected
  value for this host is '$DOCKER_NET'. See SYNC-MANUAL.md ("What OMNIGRAPH_NET is").
  ---------------------------------------------------------------------------------------
"@
}
