<#
.SYNOPSIS
    Verify graphify is wired as ONE cwd-relative user-scope entry, not per repo
    (Windows mirror of linux/check-graphify-scope.sh — keep the two in sync).

.DESCRIPTION
    Graphify is wired as a SINGLE `graphify` entry in USER scope (~/.claude.json),
    never per repo. graphify.serve is stdio and inherits its launch directory, so
    one entry with the RELATIVE path graphify-out/graph.json serves whichever repo
    you started Claude Code in. On a Windows workstation that entry is `uv`
    (no bind mount, no CODE_ROOT). The Docker + wrapper path is for Linux servers.

    What this checks:
      USER scope  ~/.claude.json
        - exactly one `graphify` entry, cwd-relative (uv, or the graphify-mcp wrapper)
            missing         -> repos with a graph won't be served
            hardcoded mount -> serves ONE repo to EVERY repo (the retired bug)
        - NO `omnigraph` or `graphify-docker` in user scope
            omnigraph is per-repo (project scope); graphify-docker is retired
      PER repo  (any repo with a graphify-out\ graph)
        - NO graphify entry in <repo>\.mcp.json (graphify is user-scope, not per repo)
        - NO stale graphify approval in <repo>\.claude\settings.local.json. Checked
          independently of the entry: an approval outlives the server it approved,
          so once the entry is gone nothing would ever look at it again.
        - a built graph at <repo>\graphify-out\graph.json

    NOT CHECKED HERE — file ownership. On Docker Desktop bind mounts, container-
    created files surface as the host user, so the root-owned graphify-out problem
    the Linux checker repairs does not arise. If your repos live on a WSL filesystem,
    run the .sh version from inside WSL, where it does apply.

.PARAMETER CodeRoot
    Directory whose immediate subdirectories are the repos to check.
    Defaults to $env:CODE_ROOT, else $HOME\code.

.PARAMETER Fix
    Apply the safe, UNTRACKED-only repair: drop a stray graphify approval from a
    repo's .claude\settings.local.json. Never edits tracked files, never registers
    user-scope entries (that is `claude mcp add`, printed for you), never builds a graph.

.EXAMPLE
    .\check-graphify-scope.ps1
.EXAMPLE
    .\check-graphify-scope.ps1 -CodeRoot C:\Users\me\code -Fix
#>
[CmdletBinding()]
param(
    [string]$CodeRoot = $(if ($env:CODE_ROOT) { $env:CODE_ROOT } else { Join-Path $HOME 'code' }),
    [switch]$Fix
)

$ErrorActionPreference = 'Stop'
$problems = 0

function Write-Gate {
    param([string]$State, [string]$Text)
    switch ($State) {
        'ok'   { Write-Host "  v $Text" -ForegroundColor Green }
        'bad'  { Write-Host "  X $Text" -ForegroundColor Red }
        'warn' { Write-Host "  ~ $Text" -ForegroundColor Yellow }
        'hint' { Write-Host "    $Text" -ForegroundColor DarkGray }
    }
}

Write-Host '======================================================================' -ForegroundColor Cyan
Write-Host "  Graphify - scope check   (code root: $CodeRoot)" -ForegroundColor Cyan
Write-Host '======================================================================' -ForegroundColor Cyan

# ---------------------------------------------------------------- user scope
# Graphify BELONGS here as one cwd-relative entry; omnigraph/graphify-docker do not.
Write-Host ''
Write-Host 'USER SCOPE  ~/.claude.json'
$userCfgPath = Join-Path $HOME '.claude.json'
$gStatus = 'MISSING'
$bad = @()
if (Test-Path $userCfgPath) {
    try {
        # -AsHashTable: ~/.claude.json can contain project keys differing only by case
        # (Windows path casing), which the default (pscustomobject) parser rejects.
        $userCfg = Get-Content $userCfgPath -Raw | ConvertFrom-Json -AsHashTable
        $ms = $userCfg['mcpServers']
        if ($ms) {
            $names = @($ms.Keys)
            $bad = @($names | Where-Object { $_ -in @('omnigraph', 'graphify-docker') })
            if ($names -contains 'graphify') {
                $g = $ms['graphify']
                $gArgs = @($g['args'])
                $cmd = [string]$g['command']
                $hard = @($gArgs | Where-Object { $_ -like '*:/repo' -and -not ($_.TrimStart().StartsWith('$')) }).Count -gt 0
                $rel = ($gArgs -contains 'graphify-out/graph.json') -or ($cmd -eq 'graphify-mcp')
                if ($hard -and -not $rel) { $gStatus = 'HARDCODED' }
                elseif ($rel -or @('uv', 'uvx', 'python', 'graphify-mcp') -contains $cmd) { $gStatus = 'OK' }
                else { $gStatus = 'UNKNOWN' }
            }
        }
    } catch {
        Write-Gate warn "could not parse $userCfgPath : $($_.Exception.Message)"
    }
}
switch ($gStatus) {
    'OK'       { Write-Gate ok 'graphify present and cwd-relative' }
    'MISSING'  {
        Write-Gate bad "graphify MISSING - repos with a graph won't be served"
        Write-Gate hint 'Workstation: claude mcp add -s user graphify -- `'
        Write-Gate hint "  uv run --with 'graphifyy[mcp]' python -m graphify.serve graphify-out/graph.json"
        $problems++
    }
    'HARDCODED' {
        Write-Gate bad 'graphify hardcodes a repo mount - serves ONE repo to EVERY repo'
        Write-Gate hint 'Replace with the cwd-relative uv entry (or the graphify-mcp wrapper on a server).'
        $problems++
    }
    default    { Write-Gate warn 'graphify present but command/args unrecognised - verify by hand' }
}
if ($bad.Count -gt 0) {
    Write-Gate bad "must NOT be in user scope: $($bad -join ', ')"
    Write-Gate hint 'omnigraph is per-repo (project scope); graphify-docker is retired. Remove:'
    foreach ($s in $bad) { Write-Gate hint "claude mcp remove -s user $s" }
    $problems++
}

# ------------------------------------------------------------------ per repo
if (-not (Test-Path $CodeRoot)) {
    Write-Host ''
    Write-Host "X CodeRoot not found: $CodeRoot" -ForegroundColor Red
    exit 1
}

foreach ($repoDir in Get-ChildItem -Path $CodeRoot -Directory) {
    $repo = $repoDir.FullName
    $name = $repoDir.Name
    if (-not (Test-Path (Join-Path $repo '.git'))) { continue }

    $mcpPath      = Join-Path $repo '.mcp.json'
    $graphPath    = Join-Path $repo 'graphify-out\graph.json'
    $graphOutDir  = Join-Path $repo 'graphify-out'
    $settingsPath = Join-Path $repo '.claude\settings.local.json'

    # "Participating" = has a built graph. graphify is no longer declared per repo,
    # so the graphify-out\ dir — not a project .mcp.json entry — opts a repo in.
    if (-not (Test-Path $graphOutDir)) { continue }

    Write-Host ''
    Write-Host $name -ForegroundColor Cyan -NoNewline
    Write-Host "  $repo" -ForegroundColor DarkGray

    # -- gate 1: NO graphify entry in the project .mcp.json -----------------
    $stray = @()
    if (Test-Path $mcpPath) {
        try {
            $mcp = Get-Content $mcpPath -Raw | ConvertFrom-Json
            if ($mcp.mcpServers) {
                $stray = @($mcp.mcpServers.PSObject.Properties.Name | Where-Object { $_ -like '*graphify*' })
            }
        } catch {
            Write-Gate bad "gate 1  .mcp.json is not valid JSON: $($_.Exception.Message)"
            $problems++
        }
    }
    if ($stray.Count -eq 0) {
        Write-Gate ok "gate 1  no project-scope graphify entry (correct - it's user scope)"
    } else {
        Write-Gate bad "gate 1  stray project graphify entry: $($stray -join ', ')"
        Write-Gate hint "graphify is a single user-scope entry; remove it from"
        Write-Gate hint "$name\.mcp.json (tracked - edit by hand) and its approval below."
        $problems++
    }

    # -- stray approval: checked INDEPENDENTLY of gate 1 ---------------------
    # An approval outlives the server it approved. Once graphify-docker is gone from
    # .mcp.json, gate 1 passes and a leftover 'graphify-docker' in enabledMcpjsonServers
    # would never be looked at again - silent drift that reads as "still per-repo".
    $approved = @()
    if (Test-Path $settingsPath) {
        try {
            $st = Get-Content $settingsPath -Raw | ConvertFrom-Json
            if ($st.PSObject.Properties.Name -contains 'enabledMcpjsonServers') {
                $approved = @($st.enabledMcpjsonServers | Where-Object { $_ -like '*graphify*' })
            }
        } catch { }
    }
    if ($approved.Count -gt 0) {
        if ($Fix) {
            try {
                $st = Get-Content $settingsPath -Raw | ConvertFrom-Json
                $st.enabledMcpjsonServers = @($st.enabledMcpjsonServers | Where-Object { $_ -notlike '*graphify*' })
                ($st | ConvertTo-Json -Depth 20) + "`n" | Set-Content -Path $settingsPath -Encoding UTF8 -NoNewline
                Write-Gate warn "approval  FIXED - removed stale '$($approved -join ', ')' from settings.local.json"
            } catch {
                Write-Gate bad "approval  stale '$($approved -join ', ')' but the repair failed: $($_.Exception.Message)"
                $problems++
            }
        } else {
            Write-Gate bad "approval  stale graphify approval '$($approved -join ', ')' in settings.local.json"
            Write-Gate hint 'It approves a server that no longer exists. Re-run with -Fix.'
            $problems++
        }
    }

    # -- gate 2: a graph exists ---------------------------------------------
    if (Test-Path $graphPath) {
        $age = [int]((Get-Date) - (Get-Item $graphPath).LastWriteTime).TotalDays
        if ($age -gt 14) {
            Write-Gate warn "gate 2  graph exists but is ${age}d old - consider rebuilding"
        } else {
            Write-Gate ok "gate 2  graph present (${age}d old)"
        }
    } else {
        Write-Gate bad 'gate 2  no graphify-out\graph.json - server would serve nothing'
        Write-Gate hint "cd $repo; uv run --with 'graphifyy[mcp]' graphify update ."
        $problems++
    }
}

Write-Host ''
Write-Host '----------------------------------------------------------------------' -ForegroundColor Cyan
if ($problems -eq 0) {
    Write-Host '  Graphify wiring correct - one user-scope entry serves every repo''s own graph.' -ForegroundColor Green
} else {
    Write-Host "  $problems problem(s) found - see the fixes above (or re-run with -Fix)." -ForegroundColor Red
}
Write-Host '======================================================================' -ForegroundColor Cyan

if ($problems -eq 0) { exit 0 } else { exit 1 }
