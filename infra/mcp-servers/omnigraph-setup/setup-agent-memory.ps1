<#
  setup-agent-memory.ps1 — make the AGENT's memory bridge work on this machine (Windows).

  The sibling of setup-sync.ps1. They fix two different things and are often confused:

      setup-sync.ps1          the TIMER that reconciles local <-> central. Reads .env.
      setup-agent-memory.ps1  the MCP BRIDGE your agent reads memory through. Reads the
                              environment + each repo's .mcp.json. THIS one.

  Every failure it repairs is SILENT — the bridge starts, answers, and is simply wrong:

    * a same-named `omnigraph` in ~/.claude.json (USER SCOPE) overrides every repo's
      .mcp.json. On 2026-07-17 one pinned to graph_id `memory` hid every project's graph.
      An agent read memory's 2 Preferences, concluded sibling-analysis-repo (135 nodes, intact)
      had been WIPED, and started rebuilding it into the globals-only graph.
    * OMNIGRAPH_TOKEN unset  -> empty bearer -> "missing bearer token"
    * OMNIGRAPH_NET wrong    -> "fetch failed". The network can EXIST but be EMPTY, so
                                docker run succeeds and only DNS quietly fails.
    * omnigraph-mcp:latest not built -> "pull access denied" (it is on no registry)

  Usage:
    .\setup-agent-memory.ps1            # diagnose AND fix
    .\setup-agent-memory.ps1 -Check     # diagnose only, change nothing
    .\setup-agent-memory.ps1 -KeepUserScope   # leave ~/.claude.json alone
    .\setup-agent-memory.ps1 -NoEnv           # do not touch the User environment
    .\setup-agent-memory.ps1 -CodeRoot D:\src # where sibling repos live (default: ..\..\..)

  Run in PowerShell 7 (pwsh). Restart your agent afterwards — MCP servers and env vars are
  only read at session start.
#>
[CmdletBinding()]
param(
  [switch]$Check,
  [switch]$KeepUserScope,
  [switch]$NoEnv,
  [string]$CodeRoot
)
$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8NoBom
try { [Console]::OutputEncoding = $utf8NoBom } catch { }

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcpRoot = Split-Path -Parent $here                       # infra/mcp-servers
$repo    = Split-Path -Parent (Split-Path -Parent $mcpRoot)  # the agent-skills checkout
$shared  = Join-Path $mcpRoot '.env.shared'
if (-not $CodeRoot) { $CodeRoot = Split-Path -Parent $repo }
$claudeJson = Join-Path $HOME '.claude.json'
$IMAGE = 'omnigraph-mcp:latest'

function Log($m)  { Write-Host "[agent-memory] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[agent-memory] OK   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[agent-memory] WARN $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "[agent-memory] FAIL $m" -ForegroundColor Red }
function Die($m)  { Bad $m; exit 1 }
$script:problems = 0
$script:fixed    = 0

if ($Check) { Log 'CHECK mode — nothing will be changed.' }

# --- 1. the token, from the file the server was started with ---------------------------
if (-not (Test-Path $shared)) {
  Die "missing $shared — create it first (cp .env.shared.example .env.shared) and set OMNIGRAPH_TOKEN."
}
$token = (Select-String -LiteralPath $shared -Pattern '^\s*OMNIGRAPH_TOKEN\s*=\s*(.*)$' |
          Select-Object -Last 1).Matches[0].Groups[1].Value.Trim().Trim('"')
if (-not $token -or $token -match '^(generate-with-|change-me)') {
  Die "OMNIGRAPH_TOKEN in .env.shared is empty or still the placeholder."
}
Ok "token found in .env.shared ($($token.Length) chars) — never printed"

# --- 2. the network, from the RUNNING container ----------------------------------------
$net = $null
if (Get-Command docker -ErrorAction SilentlyContinue) {
  $net = (& docker inspect omnigraph-server `
           --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' 2>$null |
          Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($net) { $net = $net.Trim() }
}
if ($net) {
  Ok "docker network detected: $net"
} else {
  $script:problems++
  Warn @"
omnigraph-server is not running, so the network cannot be detected and the bridge has
     nothing to talk to. Start the stack, then re-run:
       cd $mcpRoot
       docker compose --env-file .env.shared --env-file .env.server --env-file $HOME/.config/autoos/site.env -f docker-compose.server.yml up -d
"@
}

# --- 3. the bridge image (docker form only; on no registry, so it must be built) --------
$haveImage = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
  $null = & docker image inspect $IMAGE 2>$null
  $haveImage = ($LASTEXITCODE -eq 0)
}
if ($haveImage) {
  Ok "$IMAGE present"
} elseif ($Check) {
  $script:problems++
  Bad "$IMAGE MISSING — every docker-form bridge fails with 'pull access denied'. Fix: re-run without -Check."
} else {
  Log "$IMAGE missing — building it (it is published to no registry)"
  & docker build -q -t $IMAGE (Join-Path $mcpRoot 'servers\omnigraph-mcp') | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok "built $IMAGE"; $haveImage = $true; $script:fixed++ }
  else { $script:problems++; Bad "could not build $IMAGE" }
}

# --- 4. the user-scope override — the one that fakes a data loss ------------------------
# -AsHashtable, not plain ConvertFrom-Json: this file legitimately contains keys differing
# only in case (Claude Code records project paths as typed, so `C:/…/foo` and `c:/…/foo`
# both appear). ConvertFrom-Json maps to a case-INSENSITIVE PSObject and throws on the
# collision — which made an earlier version of this script fail to parse, find no override,
# and print "OK: no user-scope omnigraph". Unable-to-check is NOT a clean bill of health;
# claiming one here would recreate the exact silent-wrong-graph bug this script exists to
# catch. So a parse failure is a PROBLEM, never an OK.
$override = $null
$checkedOverride = $false
if (-not (Test-Path $claudeJson)) {
  $checkedOverride = $true
  Ok '~/.claude.json does not exist — no user-scope servers at all'
} else {
  try {
    $cj = Get-Content -Raw -Encoding utf8 $claudeJson | ConvertFrom-Json -AsHashtable
    $checkedOverride = $true
    if ($cj.mcpServers -and $cj.mcpServers.ContainsKey('omnigraph')) {
      $override = $cj.mcpServers['omnigraph']
    }
  } catch {
    $script:problems++
    Bad @"
COULD NOT PARSE $claudeJson — the user-scope override check DID NOT RUN.
     This is reported as a problem rather than passed over: a same-named user-scope
     `omnigraph` silently outranks every repo's .mcp.json, and not knowing whether one
     exists is not the same as knowing one does not. Check by hand:
       python -c "import json,pathlib;print(sorted((json.loads((pathlib.Path.home()/'.claude.json').read_text()).get('mcpServers') or {})))"
     ($($_.Exception.Message))
"@
  }
}
if ($checkedOverride -and -not $override) {
  Ok 'no user-scope `omnigraph` in ~/.claude.json — each repo keeps its own pin'
} elseif ($override) {
  $gid = $override.env.OMNIGRAPH_GRAPH_ID
  $script:problems++
  Bad @"
USER-SCOPE OVERRIDE PRESENT: ~/.claude.json defines `omnigraph` (graph_id=$gid).
     A same-named user-scope server SILENTLY WINS over every repo's .mcp.json, so every
     project reads '$gid' no matter what its own config says. Nothing errors. This is how
     an intact 135-node graph was mistaken for a wiped one.
"@
  if ($KeepUserScope) { Warn 'left in place (-KeepUserScope)' }
  elseif ($Check)     { Warn 'run without -Check to remove it (a backup is made first)' }
  else {
    # Edited with python, not PowerShell: ~/.claude.json holds ~68KB of unrelated Claude Code
    # state, and a PowerShell hashtable round-trip through ConvertTo-Json would silently
    # collapse the case-distinct project keys described above and reformat the whole file.
    # python's json preserves them and rewrites only what we removed.
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    Copy-Item $claudeJson "$claudeJson.bak-$stamp"
    $py = @'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
d = json.loads(p.read_text(encoding="utf-8"))
cfg = (d.get("mcpServers") or {}).pop("omnigraph", None)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
print((cfg or {}).get("env", {}).get("OMNIGRAPH_GRAPH_ID", "?"))
'@
    $was = ($py | & python - $claudeJson) 2>&1
    if ($LASTEXITCODE -eq 0) {
      Ok "removed the user-scope override (was pinned to '$was'; backup: $(Split-Path -Leaf "$claudeJson.bak-$stamp"))"
      $script:fixed++
      $script:problems--   # repaired, so it must not count against the final verdict
    } else {
      $script:problems++
      Bad "could not rewrite $claudeJson : $was  (restore from the .bak- file if needed)"
    }
  }
}

# --- 5. the environment the tracked .mcp.json files interpolate ------------------------
$curTok = [Environment]::GetEnvironmentVariable('OMNIGRAPH_TOKEN','User')
$curNet = [Environment]::GetEnvironmentVariable('OMNIGRAPH_NET','User')
$tokOk = ($curTok -eq $token)
$netOk = ($net -and $curNet -eq $net)
if ($tokOk -and $netOk) {
  Ok 'OMNIGRAPH_TOKEN and OMNIGRAPH_NET already correct in the User environment'
} elseif ($NoEnv -or $Check) {
  $script:problems++
  Bad @"
User environment is not set correctly:
       OMNIGRAPH_TOKEN : $(if (-not $curTok) { 'NOT SET -> empty bearer -> "missing bearer token"' } elseif (-not $tokOk) { 'set but does NOT match .env.shared' } else { 'ok' })
       OMNIGRAPH_NET   : $(if (-not $curNet) { "NOT SET -> .mcp.json falls back to 'mcp-servers_default'" } elseif (-not $netOk) { "'$curNet' but the server is on '$net'" } else { 'ok' })
     Fix: re-run without -Check/-NoEnv, or set them yourself:
       [Environment]::SetEnvironmentVariable('OMNIGRAPH_TOKEN', '<token from .env.shared>', 'User')
       [Environment]::SetEnvironmentVariable('OMNIGRAPH_NET',   '$net', 'User')
"@
} else {
  # Read from .env.shared / docker rather than echoing values, so the token never lands in
  # a transcript, a screenshot, or shell history.
  [Environment]::SetEnvironmentVariable('OMNIGRAPH_TOKEN', $token, 'User')
  if ($net) { [Environment]::SetEnvironmentVariable('OMNIGRAPH_NET', $net, 'User') }
  $script:fixed++
  Ok "set OMNIGRAPH_TOKEN + OMNIGRAPH_NET in the User environment (persists across reboots)"
  Warn 'a RESTART of your agent is required — env vars are read once at session start'
}

# --- 6. audit every sibling repo's .mcp.json -------------------------------------------
Log "auditing .mcp.json under $CodeRoot"
$graphs = @()
if ($net -and $token) {
  try {
    $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/graphs' -Headers @{Authorization="Bearer $token"} `
              -TimeoutSec 20 -SkipHttpErrorCheck
    if ($resp.StatusCode -eq 200) {
      $graphs = @([regex]::Matches($resp.Content, '"graph_id":"([^"]*)"') | ForEach-Object { $_.Groups[1].Value })
    }
  } catch { }
}
if ($graphs) { Log "graphs the server actually has: $($graphs -join ', ')" }

Get-ChildItem -Path $CodeRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $f = Join-Path $_.FullName '.mcp.json'
  if (-not (Test-Path $f)) { return }
  $name = $_.Name
  try { $d = Get-Content -Raw -Encoding utf8 $f | ConvertFrom-Json }
  catch { $script:problems++; Bad "$name/.mcp.json is not valid JSON"; return }
  $servers = @($d.mcpServers.PSObject.Properties | Where-Object { $_.Name -like '*omnigraph*' })
  if (-not $servers) { Log "$name : no omnigraph bridge (nothing to check)"; return }
  foreach ($s in $servers) {
    $blob = ($s.Value.args -join ' ')
    $envs = $s.Value.env
    if ($s.Name -eq 'omnigraph-globals') {
      $script:problems++
      Bad "$name : declares 'omnigraph-globals' — that bridge was removed 2026-07-17 and now only produces 'invalid bearer token'. Delete the block."
      continue
    }
    $gid = if ($blob -match 'OMNIGRAPH_GRAPH_ID=(\S+)') { $Matches[1] } else { $envs.OMNIGRAPH_GRAPH_ID }
    $tok = if ($blob -match 'OMNIGRAPH_TOKEN=(\S*)')    { $Matches[1] } else { $envs.OMNIGRAPH_TOKEN }
    $issues = @()
    if (-not $gid)                       { $issues += 'no OMNIGRAPH_GRAPH_ID (bridge has no graph)' }
    elseif ($graphs -and $gid -notin $graphs) { $issues += "graph '$gid' does not exist on the server" }
    if ($tok -and $tok -notmatch '^\$\{') { $issues += 'OMNIGRAPH_TOKEN is HARDCODED in a tracked file — use ${OMNIGRAPH_TOKEN}' }
    if ($blob -match '--network\s+(mcp-\S+)' -and $Matches[1] -notmatch '^\$\{') {
      $issues += "docker --network is hardcoded to '$($Matches[1])' — use `${OMNIGRAPH_NET:-...}`, it differs per host"
    }
    if ($issues) { $script:problems += $issues.Count; foreach ($i in $issues) { Bad "$name : $i" } }
    else { Ok "$name : '$($s.Name)' -> graph '$gid', token from env" }
  }
}

# --- 7. prove it: drive the real bridge and count rows ---------------------------------
if ($haveImage -and $net -and -not $Check) {
  Log 'probing the bridge for real (initialize + snapshot)'
  foreach ($g in $graphs) {
    $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}'
    $call = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"snapshot","arguments":{}}}'
    $out = ($init + "`n" + $call) | & docker run -i --rm --network $net `
             -e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080 `
             -e "OMNIGRAPH_GRAPH_ID=$g" -e "OMNIGRAPH_TOKEN=$token" $IMAGE 2>$null
    $rows = $null
    foreach ($ln in @($out)) {
      if ($ln -notmatch '^\{') { continue }
      try { $m = $ln | ConvertFrom-Json } catch { continue }
      if ($m.id -ne 2) { continue }
      try { $rows = ($m.result.content[0].text | ConvertFrom-Json).tables | Measure-Object -Property rowCount -Sum | ForEach-Object { $_.Sum } } catch { }
    }
    if ($null -ne $rows) { Ok "bridge -> graph '$g' : $rows rows" }
    else { $script:problems++; Bad "bridge -> graph '$g' : no usable response" }
  }
}

# --- verdict ---------------------------------------------------------------------------
Write-Host ''
if ($script:problems -eq 0) {
  Ok "everything checks out$(if ($script:fixed) { " ($script:fixed fix(es) applied — RESTART your agent)" })"
  exit 0
}
if ($Check) { Bad "$($script:problems) problem(s) found. Re-run without -Check to fix what is fixable."; exit 1 }
Warn "$($script:problems) problem(s) remain — see above. $(if ($script:fixed) { "$script:fixed fixed." })"
Write-Host @"

  Not everything here is auto-fixable. A .mcp.json belongs to its own repo, so edit those by
  hand (agent-skills/.mcp.json is the reference form) and commit them there.
"@
exit 1
