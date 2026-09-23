<#
.SYNOPSIS
    Build Graphify knowledge graphs for one or more repositories (Windows).

.DESCRIPTION
    Runs Graphify against a repository, writes graphify-out/graph.json, and
    optionally installs the post-commit hooks that keep the graph fresh.

    Graphify is used as a project graph layer, not as a Serena replacement.
    Serena still handles symbol-level navigation; Graphify handles graph-level
    queries across code and docs.

    See mcp-servers/README.md ("Graphify + local Ollama - known gotchas") for
    the full story behind the defaults below - they were tuned the hard way.

.PARAMETER Path
    A single repository to initialize. Defaults to the current directory.

.PARAMETER CodeRoot
    If set, batch-initializes every git repository directly under this folder.

.PARAMETER Force
    Rebuild the graph even if graphify-out/graph.json already exists. Note:
    this also bypasses graphify's semantic cache, so a --force rebuild always
    redoes the full LLM extraction pass (can take ~1h per repo on a local
    8B-class model), not just the graph-merge step.

.PARAMETER InstallHooks
    Install Graphify's git hooks after a successful build.

.PARAMETER Backend
    Graphify backend for extraction. Defaults to ollama so the stack stays
    local and does not require an external API key.

.PARAMETER Model
    Model to use for the given backend. If omitted, defaults to
    'hermes3:8b' for the ollama backend (graphify's own ollama default,
    qwen2.5-coder:7b, is a coding model, not a JSON/structured-output
    model - hermes3:8b consistently produced cleaner tool-call-style JSON
    in local testing). Auto-pulled via the Ollama REST API if not already
    present locally.

.EXAMPLE
    .\init-graphify-projects.ps1
    Initialize the repository in the current directory.

.EXAMPLE
    .\init-graphify-projects.ps1 -CodeRoot $env:CODE_ROOT
    Initialize every git repo directly under the given folder.
#>
[CmdletBinding()]
param(
    [string]$Path = (Get-Location).Path,
    [string]$CodeRoot,
    [switch]$Force,
    [switch]$InstallHooks = $true,
    [string]$Backend = 'ollama',
    [string]$Model = ''
)

$ErrorActionPreference = 'Stop'
$GraphifyOut = 'graphify-out\graph.json'
$PatchScript = Join-Path $PSScriptRoot '..\patch-graphify-ollama-bugs.py'

# graphify's own ollama default (qwen2.5-coder:7b) is code-tuned, not
# structured-output-tuned, and needs a manual `ollama pull` first since it's
# not part of any base image. hermes3:8b is closer in size but noticeably
# more reliable at emitting valid JSON for graphify's extraction schema.
$DefaultOllamaModel = 'hermes3:8b-ctx8k'

function Ensure-OllamaModel {
    param([string]$ModelName)

    $tagsJson = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5
    $have = @($tagsJson.models | ForEach-Object { $_.name })
    if ($have -contains $ModelName) {
        return
    }

    Write-Host "  Pulling ollama model '$ModelName' (first run only, several GB)..." -ForegroundColor Yellow
    $body = @{ name = $ModelName; stream = $false } | ConvertTo-Json
    Invoke-RestMethod -Uri 'http://localhost:11434/api/pull' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 1800 | Out-Null
    Write-Host "  v $ModelName pulled" -ForegroundColor Green
}

function Invoke-Graphify {
    param(
        [string]$RepoPath,
        [string]$RepoName
    )

    $graphPath = Join-Path $RepoPath $GraphifyOut
    if ((Test-Path $graphPath) -and (-not $Force)) {
        Write-Host "  - $RepoName : graph already exists (use -Force to rebuild)" -ForegroundColor DarkGray
        return [pscustomobject]@{ Repo = $RepoName; Status = 'skipped'; Path = $graphPath }
    }

    Write-Host "  - $RepoName : building graph with backend '$Backend'" -ForegroundColor Green
    Push-Location $RepoPath
    try {
        # graphify's extraction pipeline has known bugs when a local model
        # returns malformed JSON for a chunk (str where a dict is expected,
        # int where a string ID is expected) - patch them defensively before
        # every run. Idempotent and cheap; safe even if graphify isn't
        # installed in the uv cache yet.
        if (Test-Path $PatchScript) {
            & python $PatchScript | Out-Null
        }
        # Ensure graphify respects .gitignore and ignores its own output
        $ignoreContent = @()
        if (Test-Path '.gitignore') {
            $ignoreContent += Get-Content '.gitignore'
        }
        $ignoreContent += 'graphify-out/'
        $ignoreContent += 'GRAPH_*.html'
        $ignoreContent | Out-File -FilePath '.graphifyignore' -Encoding utf8

        $withSpec = 'graphifyy'
        $extractArgs = @('run', '--with')
        if ($Backend -eq 'ollama') {
            $withSpec = 'graphifyy[ollama]'
            $extractArgs += $withSpec
            $extractArgs += @(
                'graphify', 'extract', '.', '--backend', $Backend,
                '--model', $Model,
                # Local single-GPU ollama serves one request at a time -
                # concurrency > 1 just queues and adds contention. A ~6000
                # token-budget keeps chunks small enough for reliable JSON
                # from an 8B model. The 5-minute client default timeout is
                # too short: `docker logs ollama` showed legitimate
                # generations getting killed mid-flight and retried from
                # scratch, wasting far more time than a longer timeout costs.
                '--token-budget', '6000',
                '--max-concurrency', '1',
                '--api-timeout', '1200',
                '--no-viz'
            )
            if ($Force) { $extractArgs += '--force' }
        } else {
            $extractArgs += $withSpec
            $extractArgs += @('graphify', 'extract', '.', '--backend', $Backend, '--no-viz')
            if ($Force) { $extractArgs += '--force' }
        }
        & uv @extractArgs
        if ($LASTEXITCODE -ne 0) {
            throw "graphify extract failed with exit code $LASTEXITCODE"
        }

        Write-Host "    Generating D3 collapsible tree HTML..." -ForegroundColor Gray
        $treeArgs = @('run', '--with', 'graphifyy', 'graphify', 'tree', '--graph', $graphPath, '--output', (Join-Path $RepoPath 'graphify-out\GRAPH_TREE.html'))
        & uv @treeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "graphify tree failed with exit code $LASTEXITCODE"
        }

        if ($InstallHooks) {
            $hookArgs = @('run', '--with', 'graphifyy', 'graphify', 'hook', 'install')
            & uv @hookArgs
            if ($LASTEXITCODE -ne 0) {
                throw "graphify hook install failed with exit code $LASTEXITCODE"
            }
        }

        if (-not (Test-Path $graphPath)) {
            throw "expected graph not found at $graphPath"
        }

        Write-Host "    v graphify-out\graph.json ready" -ForegroundColor Green
        return [pscustomobject]@{ Repo = $RepoName; Status = 'built'; Path = $graphPath }
    } finally {
        Pop-Location
    }
}

Write-Host '======================================================================' -ForegroundColor Cyan
Write-Host '  Graphify - Initialize Repository Graphs (Windows)' -ForegroundColor Cyan
Write-Host '======================================================================' -ForegroundColor Cyan

if ($Backend -eq 'ollama') {
    try {
        Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 3 | Out-Null
    } catch {
        Write-Host "X Ollama is not responding on :11434. Start it before building local graphs." -ForegroundColor Red
        exit 1
    }
    if (-not $Model) { $Model = $DefaultOllamaModel }
    Ensure-OllamaModel -ModelName $Model
}

$targets = @()
if ($CodeRoot) {
    if (-not (Test-Path $CodeRoot)) { Write-Host "X CodeRoot not found: $CodeRoot" -ForegroundColor Red; exit 1 }
    $targets = @(Get-ChildItem -Path $CodeRoot -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName '.git') } |
        ForEach-Object { $_.FullName })
    Write-Host "Batch mode: $($targets.Count) git repo(s) under $CodeRoot`n"
} else {
    $resolved = (Resolve-Path $Path).Path
    $targets = @($resolved)
    Write-Host "Single repo: $resolved`n"
}

$results = foreach ($target in $targets) {
    Invoke-Graphify -RepoPath $target -RepoName (Split-Path $target -Leaf)
}

$built = @($results | Where-Object { $_.Status -eq 'built' }).Count
$skipped = @($results | Where-Object { $_.Status -eq 'skipped' }).Count

Write-Host "`n----------------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "  built=$built  skipped=$skipped" -ForegroundColor Cyan
Write-Host '======================================================================' -ForegroundColor Cyan
