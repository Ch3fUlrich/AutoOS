<#
.SYNOPSIS
    Initialize Serena project configuration with per-repository language
    autodetection (Windows).

.DESCRIPTION
    Detects which programming languages are actually present in a repository by
    scanning file extensions (ignoring vendored / build / cache directories) and
    creates a Serena project.yml that enables language servers ONLY for those
    languages.

    This replaces the previous behaviour of creating every project with the full
    fixed language list, which caused Serena to try to start language servers for
    languages that are not in the repo (e.g. Go / C# LSPs crashing in a pure
    Python project because their toolchains are not installed).

    By default, a detected *compiled* language (go, csharp, rust, cpp, java) is
    skipped when its toolchain is not on PATH, so a stray source file cannot
    break Serena startup. Use -SkipToolchainCheck to disable that guard.

.PARAMETER Path
    A single repository to initialize. Defaults to the current directory.

.PARAMETER CodeRoot
    If set, batch-initializes every git repository directly under this directory
    (overrides -Path). Replaces the old hard-coded code root.

.PARAMETER Force
    Recreate project.yml even if one already exists.

.PARAMETER DryRun
    Detect and print the languages but do not call Serena or write anything.

.PARAMETER SkipToolchainCheck
    Include detected compiled languages even if their toolchain is missing.

.PARAMETER NoIndex
    Create project.yml but skip the (slower) symbol-indexing step.

.EXAMPLE
    .\init-serena-projects.ps1
    Initialize the repository in the current directory.

.EXAMPLE
    .\init-serena-projects.ps1 -CodeRoot $env:CODE_ROOT
    Initialize every git repo directly under the given folder.

.EXAMPLE
    .\init-serena-projects.ps1 -DryRun
    Preview the detected languages without changing anything.
#>
[CmdletBinding()]
param(
    [string]$Path = (Get-Location).Path,
    [string]$CodeRoot,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$SkipToolchainCheck,
    [switch]$NoIndex
)

$ErrorActionPreference = "Stop"

# --- File extension (lower-case, no dot) -> Serena language ------------------
# Serena conventions: use 'typescript' for JS, 'scss' for CSS/Sass, 'cpp' for C.
$ExtToLang = @{
    'py' = 'python'; 'pyi' = 'python'; 'pyw' = 'python'
    'ts' = 'typescript'; 'tsx' = 'typescript'; 'mts' = 'typescript'; 'cts' = 'typescript'
    'js' = 'typescript'; 'jsx' = 'typescript'; 'mjs' = 'typescript'; 'cjs' = 'typescript'
    'html' = 'html'; 'htm' = 'html'
    'css' = 'scss'; 'scss' = 'scss'; 'sass' = 'scss'
    'md' = 'markdown'; 'markdown' = 'markdown'
    'yaml' = 'yaml'; 'yml' = 'yaml'
    'json' = 'json'; 'jsonc' = 'json'
    'toml' = 'toml'
    'cs' = 'csharp'
    'sh' = 'bash'; 'bash' = 'bash'
    'ps1' = 'powershell'; 'psm1' = 'powershell'; 'psd1' = 'powershell'
    'rs' = 'rust'
    'c' = 'cpp'; 'h' = 'cpp'; 'cpp' = 'cpp'; 'cc' = 'cpp'; 'cxx' = 'cpp'
    'hpp' = 'cpp'; 'hh' = 'cpp'; 'hxx' = 'cpp'
    'go' = 'go'
    'java' = 'java'
    'rb' = 'ruby'
    'php' = 'php'
    'lua' = 'lua'
    'swift' = 'swift'
    'kt' = 'kotlin'; 'kts' = 'kotlin'
    'dart' = 'dart'
    'r' = 'r'
    'jl' = 'julia'
    'ex' = 'elixir'; 'exs' = 'elixir'
    'erl' = 'erlang'; 'hrl' = 'erlang'
    'zig' = 'zig'
    'vue' = 'vue'
    'svelte' = 'svelte'
    'tf' = 'terraform'; 'tfvars' = 'terraform'
    'clj' = 'clojure'; 'cljs' = 'clojure'; 'cljc' = 'clojure'
    'scala' = 'scala'; 'sc' = 'scala'
    'hs' = 'haskell'
    'nix' = 'nix'
    'sol' = 'solidity'
}

# Compiled languages whose language server needs an external toolchain on PATH.
# csharp is handled separately below: `dotnet` merely existing is not enough,
# Serena's C# LSP needs .NET runtime 10.x specifically.
$ToolchainFor = @{
    'go' = 'go'; 'rust' = 'rustc'; 'cpp' = 'clangd'; 'java' = 'java'
}

function Test-DotnetRuntime10 {
    <#
        Serena's C# language server fails to start on a 6.x/8.x-only .NET
        install (it needs runtime 10.x specifically). Because the
        LanguageServerManager fails all-or-nothing, that single startup
        failure disables every symbolic tool for the whole project, not
        just C#'s -- so `dotnet` being on PATH is not a sufficient guard.
    #>
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { return $false }
    $runtimes = & dotnet --list-runtimes 2>$null
    return [bool]($runtimes | Select-String -Pattern '^Microsoft\.NETCore\.App 10\.')
}

# Directories never worth scanning (vendored deps, build output, caches, VCS).
$IgnoreDirs = @(
    '.git', '.serena', '.cursor', '.idea', '.vscode', '.vs',
    '.venv', 'venv', 'env', 'ENV', 'node_modules', '__pycache__',
    '.mypy_cache', '.ruff_cache', '.pytest_cache', '.tox', '.nox',
    'build', 'dist', 'target', 'out', 'obj', 'bin', 'site-packages',
    '_build', 'htmlcov', '.eggs', '__marimo__', '.next', '.cache', '.gradle'
)
$IgnoreSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($d in $IgnoreDirs) { [void]$IgnoreSet.Add($d) }

function Get-RepoLanguageCounts {
    <#
        Walk the repo (pruning ignore dirs) and return a hashtable of
        Serena-language -> file count.
    #>
    param([string]$RepoPath)
    $counts = @{}
    $stack = [System.Collections.Generic.Stack[string]]::new()
    $stack.Push($RepoPath)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try {
            foreach ($file in [System.IO.Directory]::EnumerateFiles($dir)) {
                $ext = [System.IO.Path]::GetExtension($file)
                if ($ext) {
                    $ext = $ext.TrimStart('.').ToLowerInvariant()
                    $lang = $ExtToLang[$ext]
                    if ($lang) {
                        if ($counts.ContainsKey($lang)) { $counts[$lang]++ } else { $counts[$lang] = 1 }
                    }
                }
            }
            foreach ($sub in [System.IO.Directory]::EnumerateDirectories($dir)) {
                $name = [System.IO.Path]::GetFileName($sub)
                if (-not $IgnoreSet.Contains($name)) { $stack.Push($sub) }
            }
        } catch {
            # Unreadable directory (permissions, race) -> skip it.
        }
    }
    return $counts
}

function Initialize-SerenaProject {
    param([string]$RepoPath)

    $name = Split-Path $RepoPath -Leaf
    $ymlPath = Join-Path $RepoPath ".serena\project.yml"

    if (Test-Path $ymlPath) {
        if (-not $Force) {
            Write-Host "  - $name : already configured (use -Force to regenerate)" -ForegroundColor DarkGray
            return [pscustomobject]@{ Repo = $name; Status = 'skipped'; Languages = $null }
        }
        if (-not $DryRun) {
            # `serena project create` hard-refuses when project.yml already exists
            # (no overwrite flag on its own CLI) -- -Force must remove it first.
            Remove-Item $ymlPath -Force
        }
    }

    $counts = Get-RepoLanguageCounts -RepoPath $RepoPath
    # Order by prevalence: the most common language becomes Serena's default/fallback.
    $langs = @($counts.GetEnumerator() | Sort-Object -Property Value -Descending | ForEach-Object { $_.Key })

    # Toolchain guard for compiled languages.
    if (-not $SkipToolchainCheck -and $langs.Count -gt 0) {
        $kept = @()
        foreach ($l in $langs) {
            if ($l -eq 'csharp') {
                if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
                    Write-Host "      (skipping 'csharp': 'dotnet' not on PATH)" -ForegroundColor DarkYellow
                    continue
                }
                if (-not (Test-DotnetRuntime10)) {
                    Write-Host "      (skipping 'csharp': dotnet found but no .NET 10.x runtime installed)" -ForegroundColor DarkYellow
                    continue
                }
                $kept += $l
                continue
            }
            if ($ToolchainFor.ContainsKey($l) -and -not (Get-Command $ToolchainFor[$l] -ErrorAction SilentlyContinue)) {
                Write-Host "      (skipping '$l': '$($ToolchainFor[$l])' not on PATH)" -ForegroundColor DarkYellow
                continue
            }
            $kept += $l
        }
        $langs = $kept
    }

    $display = if ($langs.Count) { $langs -join ', ' } else { '(none detected -> Serena will infer)' }
    Write-Host "  - $name : $display" -ForegroundColor Green

    if ($DryRun) {
        return [pscustomobject]@{ Repo = $name; Status = 'dry-run'; Languages = $langs }
    }

    $createArgs = @('project', 'create', $RepoPath, '--name', $name)
    foreach ($l in $langs) { $createArgs += @('--language', $l) }  # none => Serena infers
    if (-not $NoIndex) { $createArgs += '--index' }

    try {
        # Feed newlines as a safety net in case any prompt appears (flags should
        # already make this non-interactive).
        ("`n" * 5) | & serena @createArgs 2>&1 | Out-Null
        Write-Host "      created + $(if ($NoIndex) {'(not indexed)'} else {'indexed'})" -ForegroundColor Green
        return [pscustomobject]@{ Repo = $name; Status = 'created'; Languages = $langs }
    } catch {
        Write-Host "      X error: $_" -ForegroundColor Red
        return [pscustomobject]@{ Repo = $name; Status = 'error'; Languages = $langs }
    }
}

# --- main -------------------------------------------------------------------
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  Serena - Initialize Projects (per-repo language autodetection)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

if (-not $DryRun -and -not (Get-Command serena -ErrorAction SilentlyContinue)) {
    Write-Host "X 'serena' is not on PATH. Install it first: uv tool install serena-agent" -ForegroundColor Red
    exit 1
}

# Version guard -- mirrors init-serena-projects.sh; keep the two in sync.
# `serena project create` writes project.yml in the schema of the serena that RUNS
# IT, and that key is version-coupled: <=1.6.1 read `languages:`, 1.7.0+ read
# `language_servers:`, with no backward mapping. Generating with a different build
# than the one serving MCP kills every symbolic tool in every repo, with nothing
# visibly wrong in the file (as-rule-serena-project-yml-key-version-coupled).
function Get-McpSerenaVersion {
    # The server's own startup log is the only authority on what is RUNNING; a pin
    # in ~/.claude.json is what will run after the next restart, not what runs now.
    $log = Get-ChildItem "$HOME\.serena\logs\*\mcp_*.txt" -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $log) { return $null }
    $m = Select-String -Path $log.FullName -Pattern 'Starting Serena server \(version=([0-9][0-9.]*)' |
         Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

if (-not $DryRun -and $env:SKIP_VERSION_CHECK -ne '1') {
    $cliRaw = (& serena --version 2>$null | Out-String)
    $cliV = if ($cliRaw -match '[Ss]erena\s+([0-9][0-9.]*)') { $Matches[1] } else { $null }
    $mcpV = Get-McpSerenaVersion
    if ($mcpV -and $cliV -and ($cliV -ne $mcpV)) {
        Write-Host "X REFUSING TO WRITE -- serena version mismatch." -ForegroundColor Red
        Write-Host "    PATH 'serena' (would generate project.yml) : $cliV"
        Write-Host "    serena actually serving MCP (its own log)  : $mcpV"
        Write-Host ""
        Write-Host "  project.yml's language key is version-coupled: <=1.6.1 use 'languages:',"
        Write-Host "  1.7.0+ use 'language_servers:', and there is no backward mapping."
        Write-Host "  Generating with $cliV while MCP runs $mcpV breaks activate_project in EVERY repo."
        Write-Host ""
        Write-Host "  Fix one of:"
        Write-Host "    - run via the version serving MCP:  uvx --from serena-agent==$mcpV serena project create ..."
        Write-Host "    - align the install:                uv tool install --force serena-agent==$mcpV"
        Write-Host "    - override deliberately:            `$env:SKIP_VERSION_CHECK='1'"
        exit 1
    }
    if (-not $mcpV) {
        Write-Host "! Could not read the MCP serena version from ~/.serena/logs/*/mcp_*.txt;" -ForegroundColor Yellow
        Write-Host "  proceeding with PATH serena $cliV. Verify activate_project afterwards." -ForegroundColor Yellow
    }
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

$results = foreach ($t in $targets) { Initialize-SerenaProject -RepoPath $t }

$created = @($results | Where-Object { $_.Status -eq 'created' }).Count
$skipped = @($results | Where-Object { $_.Status -eq 'skipped' }).Count
$errors = @($results | Where-Object { $_.Status -eq 'error' }).Count
$dry = @($results | Where-Object { $_.Status -eq 'dry-run' }).Count

Write-Host "`n----------------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "  created=$created  skipped=$skipped  dry-run=$dry  errors=$errors" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
