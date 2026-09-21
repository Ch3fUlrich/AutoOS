#Requires -Version 5.1
<#
.SYNOPSIS
    Provider dispatch and post-install steps for AutoOS on Windows.

.DESCRIPTION
    Every installer is idempotent and honours -DryRun. Nothing here asks a
    question: by the time execution starts, every answer has been collected.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# No -Force here: forcing a re-import from inside a module REMOVES the caller's
# global copy, which silently strips these functions from the session.
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1')     -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Detect.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Progress.psm1') -DisableNameChecking

# winget's APPINSTALLER_CLI_ERROR_PACKAGE_ALREADY_INSTALLED. Script providers
# return it to mean "there was nothing left to do", which is how a second run
# reports `skipped` instead of `installed`.
$script:ExitCodeAlreadyInstalled = -1978335189

$script:DryRun  = $false
$script:Answers = @{}
$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:AgentHarness = $null

function Initialize-AutoOSInstaller {
    param([bool]$DryRun = $false, [hashtable]$Answers = @{}, [string]$RepoRoot = $null)
    $script:DryRun  = $DryRun
    $script:Answers = $Answers
    Clear-AutoOSInstalledStatus
    if ($RepoRoot) { $script:RepoRoot = $RepoRoot }
}

function Get-AutoOSAnswer {
    param([string]$Key, $Default = '')
    if ($script:Answers.ContainsKey($Key)) { $script:Answers[$Key] } else { $Default }
}

function ConvertTo-AutoOSProcessArgument {
    param([AllowEmptyString()][string]$Value)
    # Windows CommandLineToArgvW quoting, including trailing backslashes.
    '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Get-AutoOSNativePercent {
    param([string]$Line)
    if ($Line -match '(?<!\d)(100|\d{1,2})(?:\.\d+)?\s*%') { return [int]$Matches[1] }
    if ($Line -match '([0-9]+(?:[.,][0-9]+)?)\s*(KB|MB|GB|KiB|MiB|GiB)\s*/\s*([0-9]+(?:[.,][0-9]+)?)\s*(KB|MB|GB|KiB|MiB|GiB)') {
        $units = @{ KB=1000; MB=1000000; GB=1000000000; KiB=1024; MiB=1048576; GiB=1073741824 }
        $downloaded = [double]::Parse($Matches[1].Replace(',', '.'), [Globalization.CultureInfo]::InvariantCulture) * $units[$Matches[2]]
        $total = [double]::Parse($Matches[3].Replace(',', '.'), [Globalization.CultureInfo]::InvariantCulture) * $units[$Matches[4]]
        if ($total -gt 0) { return [int][Math]::Min(100, [Math]::Floor(100 * $downloaded / $total)) }
    }
    -1
}

function Read-AutoOSSecretsFile {
    <#
      .SYNOPSIS Read one api_keys.conf-style file into a hashtable (first wins).
    #>
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][hashtable]$Secrets)
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line -match '=') {
            $parts = $line.Split('=', 2)
            $key = $parts[0].Trim()
            if (-not $Secrets.ContainsKey($key)) { $Secrets[$key] = $parts[1].Trim() }
        }
    }
}

function Read-AutoOSApiSecrets {
    <#
      .SYNOPSIS Load API keys: env is consulted by callers; the file fills the rest.

      .DESCRIPTION
        Reads only the real api_keys.conf. The shipped api_keys.conf.example
        is never read: its placeholder keys are truthy, so a fallback to it
        made muse the default LLM with a key the provider rejects (401)
        instead of falling through to the local Ollama default.
    #>
    param([Parameter(Mandatory)][string]$SecretsPath)
    $secrets = @{}
    Read-AutoOSSecretsFile -Path $SecretsPath -Secrets $secrets
    return $secrets
}


function Resolve-AutoOSOllamaBaseUrl {
    <#
      .SYNOPSIS The Ollama endpoint for the generated configs, or $null for the catalog default.

      .DESCRIPTION
        OpenHands' agent-server runs on the host under agent-canvas (uvx) but in
        a container under docker compose, and 127.0.0.1 inside a container is the
        container itself. So: OLLAMA_BASE_URL when set (normalised to /v1); else
        host.docker.internal when Ollama answers there (Docker Desktop: reachable
        from the host AND from containers); else $null, which keeps the catalog's
        http://127.0.0.1:11434/v1 - right for a host-run agent-canvas, and for a
        native Ollama bound to loopback. Same rules as resolve_ollama_base_url.
    #>
    param(
        # Returns $true when Ollama answers at the URL. Injectable so tests
        # never touch the network.
        [scriptblock]$Probe = {
            param($Url)
            try { Invoke-RestMethod -Uri $Url -TimeoutSec 2 -ErrorAction Stop | Out-Null; $true } catch { $false }
        }
    )
    if ($env:OLLAMA_BASE_URL) {
        $url = $env:OLLAMA_BASE_URL.TrimEnd('/')
        if ($url -notmatch '/v1$') { $url += '/v1' }
        return $url
    }
    if (& $Probe 'http://host.docker.internal:11434/api/version') { return 'http://host.docker.internal:11434/v1' }
    return $null
}

function Invoke-AutoOSProcess {
    <#
      .SYNOPSIS Run a command, honouring -DryRun; returns @{ ExitCode; Output }.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int[]]$SuccessCodes = @(0),
        # Project-scoped tools write into the current directory, so where a
        # command runs is part of what it does.
        [string]$WorkingDirectory,
        [ValidateRange(1, 86400)][int]$TimeoutSeconds = 1800
    )
    $display = "$FilePath $($Arguments -join ' ')"
    if ($script:DryRun) {
        Write-AutoOSLine "would run: $display" -Level muted
        return @{ ExitCode = 0; Output = ''; DryRun = $true; Success = $true }
    }
    Write-AutoOSLine "run: $display" -Level muted
    if ($env:AUTOOS_INSTALL_TIMEOUT_SECONDS) {
        $configured = 0
        if (-not [int]::TryParse($env:AUTOOS_INSTALL_TIMEOUT_SECONDS, [ref]$configured) -or $configured -lt 1 -or $configured -gt 86400) {
            throw 'AUTOOS_INSTALL_TIMEOUT_SECONDS must be between 1 and 86400.'
        }
        $TimeoutSeconds = $configured
    }
    $process = $null
    $output = New-Object Text.StringBuilder
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $lastHeartbeat = -5.0
    $exitedAt = $null
    $timedOut = $false
    try {
        $command = Get-Command $FilePath -ErrorAction Stop
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = $command.Source
        $psi.Arguments = ($Arguments | ForEach-Object { ConvertTo-AutoOSProcessArgument $_ }) -join ' '
        if ($psi.FileName -match '\.(cmd|bat|ps1)$') {
            # Avoid cmd string interpolation: encode a PowerShell invocation with
            # literal arguments. Use the system shell so installing pwsh cannot
            # lock the very executable hosting this child.
            $literalArgs = ($Arguments | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ','
            $invoke = "& '" + $command.Source.Replace("'", "''") + "' @($literalArgs); " + 'exit $LASTEXITCODE'
            $psi.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invoke))
        }
        $psi.WorkingDirectory = if ($WorkingDirectory) { $WorkingDirectory } else { (Get-Location).Path }
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
        $psi.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
        $process = [Diagnostics.Process]::Start($psi)
        $streams = @($process.StandardOutput, $process.StandardError)
        $buffers = @((New-Object char[] 4096), (New-Object char[] 4096))
        $pending = @($streams[0].ReadAsync($buffers[0], 0, 4096), $streams[1].ReadAsync($buffers[1], 0, 4096))
        $partial = @('', '')
        $percent = -1
        $phase = 'starting'
        while ($true) {
            for ($s = 0; $s -lt 2; $s++) {
                if ($null -eq $pending[$s] -or -not $pending[$s].IsCompleted) { continue }
                $count = $pending[$s].GetAwaiter().GetResult()
                if ($count -eq 0) {
                    if ($partial[$s]) { Write-AutoOSLine $partial[$s] -Level muted }
                    $pending[$s] = $null
                    continue
                }
                $chunk = -join $buffers[$s][0..($count - 1)]
                [void]$output.Append($chunk)
                if ($output.Length -gt 65536) { [void]$output.Remove(0, $output.Length - 65536) }
                $parts = [regex]::Split(($partial[$s] + $chunk), '[\r\n]')
                $partial[$s] = $parts[-1]
                for ($l = 0; $l -lt $parts.Length - 1; $l++) {
                    $line = $parts[$l] -replace '\x1b\[[0-?]*[ -/]*[@-~]', ''
                    if (-not $line.Trim()) { continue }
                    # Native child text must not masquerade as a progress event.
                    Write-AutoOSLine ('  ' + $line) -Level muted
                    if ($line -match '(?i)(download|herunterlad)') { $phase = 'downloading' }
                    if ($line -match '(?i)(starting.*install|installation.*(start|wird)|installing package)') { $phase = 'installing'; $percent = -1 }
                    $native = Get-AutoOSNativePercent -Line $line
                    if ($native -ge 0) { $percent = $native }
                }
                if ($partial[$s].Length -gt 8192) { $partial[$s] = $partial[$s].Substring($partial[$s].Length - 8192) }
                $pending[$s] = $streams[$s].ReadAsync($buffers[$s], 0, 4096)
            }
            if ($timer.Elapsed.TotalSeconds - $lastHeartbeat -ge 5) {
                Write-AutoOSInstallProgress -Phase $phase -Percent $percent
                $lastHeartbeat = $timer.Elapsed.TotalSeconds
            }
            if ($process.HasExited) {
                if ($null -eq $exitedAt) { $exitedAt = $timer.Elapsed.TotalSeconds }
                if (($null -eq $pending[0] -and $null -eq $pending[1]) -or $timer.Elapsed.TotalSeconds - $exitedAt -gt 2) { break }
            } elseif ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                $timedOut = $true
                # Only this owned child tree; never kill unrelated MSI services.
                $stop = New-Object Diagnostics.ProcessStartInfo
                $stop.FileName = Join-Path $env:SystemRoot 'System32\taskkill.exe'
                $stop.Arguments = "/PID $($process.Id) /T /F"
                $stop.UseShellExecute = $false; $stop.CreateNoWindow = $true
                $killer = [Diagnostics.Process]::Start($stop)
                try { [void]$killer.WaitForExit(5000) } finally { $killer.Dispose() }
                Write-AutoOSLine "Installer exceeded ${TimeoutSeconds}s. Stopping this run; inspect the installer log and any Windows elevation prompt before retrying." -Level error
                break
            }
            Start-Sleep -Milliseconds 50
        }
        $code = if ($timedOut) { 124 } else { $process.ExitCode }
        @{ ExitCode = $code; Output = $output.ToString(); DryRun = $false; Success = (-not $timedOut -and $code -in $SuccessCodes); TimedOut = $timedOut }
    } catch {
        @{ ExitCode = 1; Output = $_.Exception.Message; DryRun = $false; Success = $false }
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
}

# ─── PATH handling ──────────────────────────────────────────────────────────
function Add-AutoOSPathEntry {
    <#
      .SYNOPSIS
        Append a directory to the user PATH without destroying what is there.
      .DESCRIPTION
        Read-modify-write, always. Setting PATH to a bare value is how this
        repository once wiped a user's entire environment; that must never
        happen again, so there is exactly one code path for PATH edits.
    #>
    param(
        [Parameter(Mandatory)][string[]]$Directory,
        [ValidateSet('User', 'Machine')][string]$Scope = 'User'
    )
    $current = [Environment]::GetEnvironmentVariable('Path', $Scope)
    if ($null -eq $current) { $current = '' }
    $parts = @($current -split ';' | Where-Object { $_ -ne '' })

    $added = @()
    foreach ($dir in $Directory) {
        $normalised = $dir.TrimEnd('\')
        $exists = @($parts | Where-Object { $_.TrimEnd('\') -ieq $normalised }).Count -gt 0
        if (-not $exists) { $parts += $dir; $added += $dir }
    }

    if ($added.Count -eq 0) {
        Write-AutoOSLine "PATH already contains $($Directory -join ', ')" -Level muted
        return $false
    }
    $new = ($parts -join ';')
    if ($script:DryRun) {
        Write-AutoOSLine "would append to $Scope PATH: $($added -join ', ')" -Level muted
        return $true
    }
    # Keep a copy before touching something this destructive.
    $backup = Join-Path $env:USERPROFILE ".autoos-path-backup-$Scope-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
    $current | Out-File -FilePath $backup -Encoding utf8
    [Environment]::SetEnvironmentVariable('Path', $new, $Scope)
    $env:Path = "$env:Path;$($added -join ';')"
    Write-AutoOSLine "appended to $Scope PATH: $($added -join ', ')" -Level ok
    Write-AutoOSLine "previous value saved to $backup" -Level muted
    $true
}

# ─── Idempotency checks ─────────────────────────────────────────────────────
function Test-AutoOSInstalled {
    param([Parameter(Mandatory)][psobject]$Component)
    (Get-AutoOSInstalledStatus -Component $Component) -eq 'installed'
}

function Get-AutoOSInstalledComponents {
    <#
      .SYNOPSIS Filter components to only those installed on this machine.
    #>
    param([Parameter(Mandatory)][psobject[]]$Components)
    @($Components | Where-Object { Test-AutoOSInstalled -Component $_ })
}

# ─── Providers ──────────────────────────────────────────────────────────────
function Install-AutoOSComponent {
    <#
      .SYNOPSIS Install one component. Returns 'installed' | 'skipped' | 'failed'.
    #>
    param([Parameter(Mandatory)][psobject]$Component)

    if (Test-AutoOSInstalled -Component $Component) {
        Write-AutoOSLine (([char]0x2713) + " $($Component.Name) is already installed - package skipped") -Level ok
        return 'skipped'
    }

    $result = switch ($Component.Provider) {
        'winget' {
            $wingetArgs = @('install', '--id', $Component.Package, '--exact',
                            '--accept-package-agreements', '--accept-source-agreements',
                            '--disable-interactivity', '--silent')
            $source = if ($Component.Source) { $Component.Source } else { 'winget' }
            $wingetArgs += @('--source', $source, '--verbose-logs')
            # 0x8A15002B = "no applicable upgrade / already installed"
            Invoke-AutoOSProcess -FilePath 'winget' -Arguments $wingetArgs -SuccessCodes @(0, -1978335189)
        }
        'choco' {
            Invoke-AutoOSProcess -FilePath 'choco' -Arguments @('install', $Component.Package, '-y', '--no-progress')
        }
        'npm' {
            Invoke-AutoOSProcess -FilePath 'npm' -Arguments @('install', '-g', $Component.Package)
        }
        'psmodule' {
            if ($Component.Package -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') { throw 'Invalid PowerShell module name.' }
            $minimum = if ($Component.MinimumVersion) { " -MinimumVersion '$([version]$Component.MinimumVersion)'" } else { '' }
            $install = "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " +
                "Install-PackageProvider NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force -ErrorAction Stop | Out-Null; " +
                "Install-Module -Name '$($Component.Package)'$minimum -Repository PSGallery -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop"
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("`$ErrorActionPreference='Stop'; " + $install))
            Invoke-AutoOSProcess -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded)
        }
        'script'  { Invoke-AutoOSScriptProvider -Component $Component }
        'custom'  { @{ ExitCode = 0; Output = 'handled by postInstall'; Success = $true } }
        'manual' { Write-AutoOSLine "Action required: $($Component.Name) - $($Component.Homepage)" -Level warn; return 'manual' }
        default   { @{ ExitCode = 1; Output = "unknown provider '$($Component.Provider)'"; Success = $false } }
    }

    if ($result.ContainsKey('TimedOut') -and $result.TimedOut) { throw [TimeoutException]::new("Installer timed out: $($Component.Name). Remaining applications were not started.") }
    # Script providers may return a bare hashtable; treat exit 0 as success.
    $ok = if ($result.ContainsKey('Success')) { [bool]$result.Success } else { $result.ExitCode -eq 0 }
    if (-not $ok) {
        Write-AutoOSLine "$($Component.Name) failed (exit $($result.ExitCode))" -Level error
        if ($result.Output) { Write-AutoOSLine ($result.Output.Trim() -split "`n" | Select-Object -First 3 | Out-String).Trim() -Level muted }
        return 'failed'
    }
    if (-not $script:DryRun) { Clear-AutoOSInstalledStatus }
    if ($result.ExitCode -eq $script:ExitCodeAlreadyInstalled) { return 'skipped' }
    'installed'
}

# agy (Antigravity CLI, replacing Gemini CLI at the human partner's direction)
# used to be dispatched here via `irm https://antigravity.google/cli/install.ps1
# | iex` — an unverified remote script piped straight into a shell (A14).
# Google publishes a signed winget package (Google.AntigravityCLI, x64 +
# arm64, sha256-pinned installers) that does the verification for us, so
# catalog/windows.json now installs it with provider "winget" instead and
# there is no longer a script path for it here.
function Invoke-AutoOSScriptProvider {
    param([Parameter(Mandatory)][psobject]$Component)
    switch ($Component.Package) {
        'meslo-nerd-font' { return Install-AutoOSNerdFont }
        'herdr'           { return Install-AutoOSHerdr }
        'claude-autostart'{ return Install-AutoOSClaudeAutostart }
        default           { return @{ ExitCode = 1; Output = "no script for '$($Component.Package)'" } }
    }
}

# ─── Post-install steps ─────────────────────────────────────────────────────

function Invoke-AutoOSOllamaPull {
    <#
      .SYNOPSIS Pull one Ollama model, idempotently.
      .DESCRIPTION
        Shared by the three local-ai model postInstall wrappers below — a
        catalog postInstall is invoked with no arguments (see
        Invoke-AutoOSPostInstall), so each model needs its own thin named
        wrapper; this is the one place that actually knows how to pull one.
        `ollama list` is checked first so a model already pulled reports
        nothing new to do, matching every other installer's "skipped" bar
        (AGENTS.md §4) even though the catalog's own custom-provider
        pre-check (Get-AutoOSInstalledStatus) already does the same test.
    #>
    param([Parameter(Mandatory)][string]$Model)
    if ($script:DryRun) {
        Write-AutoOSLine "would pull ollama model: $Model" -Level muted
        return
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine "ollama command not found; skipping model pull for $Model" -Level warn
        return
    }
    $existing = & ollama list 2>$null
    if ($existing -match [regex]::Escape($Model)) {
        Write-AutoOSLine "ollama model $Model already pulled" -Level muted
        return
    }
    Write-AutoOSLine "pulling Ollama model: $Model" -Level step
    & ollama pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-AutoOSLine "failed to pull ollama model: $Model" -Level warn
    }
}

# local-ai profile (B21): qwen3:4b is the pre-ticked default (2.5 GB, 256K
# context — long enough to paste a dmesg/SMART dump into), qwen3:1.7b and
# qwen2.5-coder:7b are offered but not pre-ticked. Sizes are verified against
# ollama.com and live in each catalog entry's description, which a test
# enforces.
function Install-AutoOSOllamaModelQwen34B { Invoke-AutoOSOllamaPull -Model 'qwen3:4b' }
function Install-AutoOSOllamaModelQwen317B { Invoke-AutoOSOllamaPull -Model 'qwen3:1.7b' }
function Install-AutoOSOllamaModelQwenCoder7B { Invoke-AutoOSOllamaPull -Model 'qwen2.5-coder:7b' }

function Install-AutoOSOterm {
    <#
      .SYNOPSIS Install oterm (the Ollama TUI client) via pip.
      .DESCRIPTION
        Verified 2026-09-12: oterm has no winget or Chocolatey package
        (winget.run and community.chocolatey.org both return zero matches).
        pip is the only cross-platform install path the upstream docs
        (ggozad.github.io/oterm/installation) list, so this is a "custom"
        provider postInstall rather than a normal winget/choco entry.
    #>
    if ($script:DryRun) {
        Write-AutoOSLine 'would install oterm via pip (pip install --user oterm)' -Level muted
        return
    }
    if (Get-Command oterm -ErrorAction SilentlyContinue) {
        Write-AutoOSLine 'oterm already installed' -Level muted
        return
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $py) {
        Write-AutoOSLine 'python not found; skipping oterm' -Level warn
        return
    }
    Write-AutoOSLine 'installing oterm (pip)' -Level step
    & $py.Source -m pip install --user oterm
    if ($LASTEXITCODE -ne 0) {
        Write-AutoOSLine 'oterm install failed' -Level warn
    }
}

function Add-AutoOSGitToPath {
    Add-AutoOSPathEntry -Directory @("$env:ProgramFiles\Git\cmd") | Out-Null
}

function Set-AutoOSGitConfig {
    <#
      .SYNOPSIS
        Put git on PATH and apply the commit identity the user was asked for.
      .DESCRIPTION
        The Linux side has asked for git_user_name / git_user_email since the
        beginning and applies them in setup_git_config; Windows asked for
        nothing, so a fresh machine was left committing as whatever git guessed
        from the hostname.

        Read-modify-write, never clobber: an empty answer leaves whatever the
        user already configured alone, and a value that is already set is
        reported as such rather than rewritten. `git config --global` edits one
        key in ~/.gitconfig, so the rest of the file survives.
    #>
    Add-AutoOSGitToPath

    $git = Get-Command git -ErrorAction SilentlyContinue
    $pairs = @(
        @{ Key = 'git_user_name';  Setting = 'user.name' },
        @{ Key = 'git_user_email'; Setting = 'user.email' }
    )
    if ($script:DryRun) {
        foreach ($pair in $pairs) {
            $answer = [string](Get-AutoOSAnswer $pair.Key '')
            if ($answer) { Write-AutoOSLine "would set git $($pair.Setting) = $answer" -Level muted }
        }
        return
    }
    if (-not $git) {
        # A brand-new install is not on this process's PATH yet. Fall back to the
        # file git.exe was just written to rather than silently doing nothing.
        $fallback = Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
        if (Test-Path -LiteralPath $fallback -PathType Leaf) { $git = Get-Command $fallback }
    }
    if (-not $git) {
        Write-AutoOSLine 'git not found - skipping git identity' -Level warn
        return
    }

    foreach ($pair in $pairs) {
        $answer = [string](Get-AutoOSAnswer $pair.Key '')
        if (-not $answer) { continue }
        $current = ''
        try { $current = [string](& $git.Source 'config' '--global' $pair.Setting 2>$null | Select-Object -First 1) } catch { $current = '' }
        if ($current -eq $answer) {
            Write-AutoOSLine "git $($pair.Setting) already set to $answer" -Level muted
            continue
        }
        try {
            & $git.Source 'config' '--global' $pair.Setting $answer 2>&1 | Out-Null
            Write-AutoOSLine "git $($pair.Setting) = $answer" -Level ok
        } catch {
            Write-AutoOSLine "could not set git $($pair.Setting): $($_.Exception.Message)" -Level warn
        }
    }
}

function Add-AutoOSCondaToPath {
    <#
      .SYNOPSIS
        Append Miniconda to PATH.
      .DESCRIPTION
        The Ansible original set Path to ONLY these four directories, wiping
        everything else the user had. Add-AutoOSPathEntry appends instead.
    #>
    $base = if (Test-Path 'C:\tools\miniconda3') { 'C:\tools\miniconda3' } else { "$env:USERPROFILE\miniconda3" }
    Add-AutoOSPathEntry -Directory @(
        $base, "$base\Scripts", "$base\Library\bin", "$base\condabin"
    ) | Out-Null
}

function New-AutoOSCondaEnv {
    param([string]$Name = 'suite2p', [string]$PythonVersion = '3.11')
    $base = if (Test-Path 'C:\tools\miniconda3') { 'C:\tools\miniconda3' } else { "$env:USERPROFILE\miniconda3" }
    $conda = Join-Path $base 'Scripts\conda.exe'
    if (-not (Test-Path $conda)) {
        Write-AutoOSLine "conda not found at $conda - skipping environment" -Level warn
        return
    }
    if (Test-Path (Join-Path $base "envs\$Name")) {
        Write-AutoOSLine "conda env '$Name' already exists" -Level muted
        return
    }
    Invoke-AutoOSProcess -FilePath $conda -Arguments @('create', '-n', $Name, "python=$PythonVersion", '-y') | Out-Null
    Invoke-AutoOSProcess -FilePath $conda -Arguments @('run', '-n', $Name, 'pip', 'install', $Name) | Out-Null
}

function Install-AutoOSNerdFont {
    <#
      .SYNOPSIS Install MesloLGS Nerd Font per-user (no winget package exists).
    #>
    $fontDir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
    $target  = Join-Path $fontDir 'MesloLGS NF Regular.ttf'
    if (Test-Path $target) { return @{ ExitCode = 0; Output = 'font already installed' } }
    if ($script:DryRun) {
        Write-AutoOSLine 'would download MesloLGS NF and register it for the current user' -Level muted
        return @{ ExitCode = 0; Output = 'dry-run' }
    }
    try {
        if (-not (Test-Path $fontDir)) { New-Item -ItemType Directory -Path $fontDir -Force | Out-Null }
        $url = 'https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Regular.ttf'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
        # Per-user font registration; no elevation needed.
        $key = 'HKCU:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
        if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
        New-ItemProperty -Path $key -Name 'MesloLGS NF Regular (TrueType)' -Value $target -PropertyType String -Force | Out-Null
        @{ ExitCode = 0; Output = 'installed' }
    } catch {
        @{ ExitCode = 1; Output = $_.Exception.Message }
    }
}

function Install-AutoOSHerdr {
    <#
      .SYNOPSIS
        Install Herdr from whichever source the user named.
      .DESCRIPTION
        Herdr is not in winget, so the source is a prompt answer rather than a
        hardcoded URL: npm:<pkg>, git:<url>, or a direct download URL.
    #>
    $source = Get-AutoOSAnswer 'herdr_source' 'npm:herdr'
    if ($source -match '^npm:(.+)$') {
        return Invoke-AutoOSProcess -FilePath 'npm' -Arguments @('install', '-g', $Matches[1])
    }
    if ($source -match '^git:(.+)$') {
        $dest = Join-Path $env:USERPROFILE '.herdr'
        if (Test-Path $dest) {
            return Invoke-AutoOSProcess -FilePath 'git' -Arguments @('-C', $dest, 'pull', '--ff-only')
        }
        return Invoke-AutoOSProcess -FilePath 'git' -Arguments @('clone', '--depth', '1', $Matches[1], $dest)
    }
    Write-AutoOSLine "Unrecognised Herdr source '$source' - skipping." -Level warn
    @{ ExitCode = 0; Output = 'skipped' }
}

function Install-AutoOSClaudeAutostart {
    <#
      .SYNOPSIS
        Register the Scheduled Tasks that snapshot and restore Claude Code sessions.
      .DESCRIPTION
        Writes nothing into the user's ~/.claude: the lifecycle-hook approach was
        removed because SessionEnd deletes the very record restore depends on, and
        a hook command hard-codes this checkout's path into a global config file
        (ADR 0003). The only state this owns is two Scheduled Tasks.

        Returns the winget "already installed" exit code when both tasks are
        already current, so a second run reports `skipped` rather than `installed`.
    #>
    Import-Module (Join-Path $PSScriptRoot 'AutoOS.ClaudeAutostart.psm1') -DisableNameChecking -Force
    $config = Get-AutoOSClaudeConfig

    if ($script:DryRun) {
        [void](Register-AutoOSClaudeAutostartTask -Config $config -DryRun)
        Write-AutoOSLine "would snapshot live sessions every $($config['snapshot_interval_mins']) minutes and reopen them at logon" -Level muted
        Write-AutoOSClaudeHostReadiness -Config $config
        return @{ ExitCode = 0; Success = $true }
    }

    try {
        $outcome = Register-AutoOSClaudeAutostartTask -Config $config
        Write-AutoOSClaudeHostReadiness -Config $config

        switch ($outcome) {
            'skipped'  { return @{ ExitCode = $script:ExitCodeAlreadyInstalled; Success = $true } }
            'failed'   { return @{ ExitCode = 1; Success = $false; Output = 'could not register the scheduled tasks' } }
            default    { return @{ ExitCode = 0; Success = $true } }
        }
    } catch {
        Write-AutoOSLine "could not configure Claude autostart: $($_.Exception.Message)" -Level warn
        return @{ ExitCode = 1; Output = $_.Exception.Message; Success = $false }
    }
}

function Write-AutoOSClaudeHostReadiness {
    <#
      .SYNOPSIS
        Say at install time whether a restored session will have anywhere to appear.
      .DESCRIPTION
        A restore with no terminal host does nothing (ADR 0002). Naming that here
        beats letting the user find out after a reboot.
    #>
    param([hashtable]$Config)

    if (Get-AutoOSClaudeTerminalHost -Config $Config) {
        Write-AutoOSLine 'restored sessions will open in Windows Terminal tabs' -Level ok
    } else {
        Write-AutoOSLine 'Windows Terminal was not found - restore will refuse to start a session it cannot show you' -Level warn
        Write-AutoOSLine 'install it with: winget install Microsoft.WindowsTerminal' -Level muted
    }
    if (-not (Get-Command -Name 'claude' -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'claude is not on PATH yet - autostart stays idle until Claude Code is installed' -Level warn
    }
}

function Install-AutoOSPoshTheme {
    Import-Module (Join-Path $PSScriptRoot 'AutoOS.Shell.psm1') -DisableNameChecking
    Install-AutoOSShellConfiguration -RepoRoot $script:RepoRoot -DryRun:$script:DryRun
}

function Add-AutoOSProfileLine {
    <#
      .SYNOPSIS Append (or with -Prepend, prepend) a line to a shell profile exactly once, with a backup.
    #>
    param(
        [Parameter(Mandatory)][string]$ProfilePath,
        [Parameter(Mandatory)][string]$Line,
        [Parameter(Mandatory)][string]$Marker,
        [switch]$Prepend
    )
    $dir = Split-Path -Parent $ProfilePath
    if ($script:DryRun) {
        Write-AutoOSLine "would ensure '$Marker' in $ProfilePath" -Level muted
        return
    }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (Test-Path $ProfilePath) {
        # -SimpleMatch: the line contains |, $ and . which are regex metacharacters.
        $existing = Get-Content -Path $ProfilePath -Raw -ErrorAction SilentlyContinue
        if ($existing -and (Select-String -InputObject $existing -Pattern $Marker -SimpleMatch -Quiet)) {
            Write-AutoOSLine "profile already configured ($Marker)" -Level muted
            return
        }
        Copy-Item $ProfilePath "$ProfilePath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    } else {
        New-Item -ItemType File -Path $ProfilePath -Force | Out-Null
    }
    if ($Prepend) {
        # A guard is useless at the end of a profile: everything slow has already run.
        # Rule 4: the rest of the file must come back unchanged, so it is decoded and
        # re-encoded in its own encoding (a BOM-less 5.1 profile is usually ANSI).
        $bytes = [IO.File]::ReadAllBytes($ProfilePath)
        $bom = 0
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            $enc = [Text.UTF8Encoding]::new($false); $bom = 3
        } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
            $enc = [Text.UnicodeEncoding]::new($false, $false); $bom = 2
        } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
            $enc = [Text.UnicodeEncoding]::new($true, $false); $bom = 2
        } elseif ($bytes.Length -eq 0) {
            # a new profile: UTF-8 with BOM reads the same in 5.1 and 7
            $enc = [Text.UTF8Encoding]::new($false); $bytes = [byte[]](0xEF, 0xBB, 0xBF); $bom = 3
        } else {
            try {
                $enc = [Text.UTF8Encoding]::new($false, $true)
                [void]$enc.GetString($bytes)
            } catch {
                # Some ANSI code page, which one is unknowable from here. Latin-1 maps every
                # byte to one char and back, so the file round-trips unchanged whatever it
                # was; `using`/`param` are ASCII, so the parser still finds them.
                $enc = [Text.Encoding]::GetEncoding(28591)
            }
        }
        $body = $enc.GetString($bytes, $bom, $bytes.Length - $bom)
        # `using` and `param` must stay the first statements, so the guard goes after them.
        $ast = [Management.Automation.Language.Parser]::ParseInput($body, [ref]$null, [ref]$null)
        $at = 0
        foreach ($u in $ast.UsingStatements) { $at = [Math]::Max($at, $u.Extent.EndOffset) }
        if ($ast.ParamBlock) { $at = [Math]::Max($at, $ast.ParamBlock.Extent.EndOffset) }
        $new = if ($at -eq 0) { "# added by AutoOS`r`n$Line`r`n$body" }
               else { $body.Substring(0, $at) + "`r`n# added by AutoOS`r`n$Line" + $body.Substring($at) }
        $out = [IO.MemoryStream]::new()
        $out.Write($bytes, 0, $bom)
        $newBytes = $enc.GetBytes($new)
        $out.Write($newBytes, 0, $newBytes.Length)
        [IO.File]::WriteAllBytes($ProfilePath, $out.ToArray())
    } else {
        Add-Content -Path $ProfilePath -Value "`n# added by AutoOS`n$Line"
    }
    Write-AutoOSLine "profile updated: $ProfilePath" -Level ok
}

function Install-AutoOSWindhawkMods {
    <#
      .SYNOPSIS
        Seed Windhawk mod settings, including the taskbar clock layout.
      .DESCRIPTION
        Windhawk has no CLI for installing mods, so AutoOS pre-seeds each mod's
        settings in the registry and then tells the user which mods to enable
        from the Windhawk UI. Settings written before the mod exists are picked
        up when it is installed, so the order does not matter.
    #>
    $catalogPath = Join-Path $script:RepoRoot 'catalog\windows.json'
    $catalog = Get-Content -Path $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $catalog.PSObject.Properties.Name.Contains('windhawk_mods')) { return }

    foreach ($mod in $catalog.windhawk_mods) {
        $key = "HKCU:\SOFTWARE\Windhawk\Engine\Mods\$($mod.id)\Settings"
        if ($script:DryRun) {
            Write-AutoOSLine "would seed settings for mod '$($mod.name)'" -Level muted
            foreach ($p in $mod.settings.PSObject.Properties) {
                Write-AutoOSLine "    $($p.Name) = $($p.Value)" -Level muted
            }
            continue
        }
        try {
            if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
            foreach ($p in $mod.settings.PSObject.Properties) {
                New-ItemProperty -Path $key -Name $p.Name -Value $p.Value -PropertyType String -Force | Out-Null
            }
            Write-AutoOSLine "seeded settings for '$($mod.name)'" -Level ok
        } catch {
            Write-AutoOSLine "could not seed '$($mod.name)': $($_.Exception.Message)" -Level warn
        }
    }
    Write-AutoOSLine 'Open Windhawk and enable these mods to apply the settings:' -Level step
    foreach ($mod in $catalog.windhawk_mods) { Write-AutoOSLine "    - $($mod.name)" -Level muted }
}

function Get-AutoOSMcpServerNames {
    <#
      .SYNOPSIS Server names Claude Code already knows about, whatever the scope.
    #>
    if (-not (Get-Command 'claude' -ErrorAction SilentlyContinue)) { return @() }
    try {
        $out = & claude mcp list 2>&1 | Out-String
    } catch { return @() }
    # Lines read "name: command - status". Plugin-provided servers are prefixed
    # "plugin:<plugin>:<name>", so the last colon-separated field is the name
    # that matters - a serena from a plugin is still a serena.
    @($out -split "`r?`n" | ForEach-Object {
        if ($_ -match '^\s*(\S+?):\s') { ($Matches[1] -split ':')[-1] }
    } | Where-Object { $_ })
}

function Get-AutoOSAgentHarness {
    <#
      .SYNOPSIS The parsed catalog/agent-harness.json, read from disk once.
    #>
    if ($null -eq $script:AgentHarness) {
        $path = Join-Path $script:RepoRoot 'catalog\agent-harness.json'
        $script:AgentHarness = Get-Content -Path $path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    $script:AgentHarness
}

function Get-AutoOSMcpPackage {
    <#
      .SYNOPSIS The pinned package spec for one MCP server, from the harness.
    #>
    param([Parameter(Mandatory)][string]$Name)
    $server = (Get-AutoOSAgentHarness).mcp_servers.$Name
    if (-not $server) { throw "catalog/agent-harness.json has no MCP server '$Name'" }
    $server.package
}

function Get-AutoOSSerenaExcludedTools {
    <#
      .SYNOPSIS Serena's excluded_tools list, the one list every client shares.
    #>
    @((Get-AutoOSAgentHarness).mcp_servers.serena.excluded_tools)
}

function Register-AutoOSMcpServer {
    <#
      .SYNOPSIS
        Add one MCP server to Claude Code, through Claude Code's own CLI.

      .DESCRIPTION
        Deliberately not a hand-edit of ~/.claude.json. That file is tens of
        kilobytes of the user's own session state, and rewriting all of it
        through ConvertTo-Json to change one key is precisely the "overwrite a
        config wholesale" failure this repository has shipped before.
        `claude mcp add` owns the file, refuses to clobber an entry that is
        already there, and behaves the same on every platform.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Command,
        [string[]]$Arguments = @(),
        [ValidateSet('user', 'project', 'local')][string]$Scope = 'user',
        [string]$WorkingDirectory
    )

    if (-not (Get-Command 'claude' -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine "claude is not on PATH - cannot register '$Name'. Install claude-code first." -Level warn
        return $false
    }
    if ($Name -in (Get-AutoOSMcpServerNames)) {
        Write-AutoOSLine "MCP server '$Name' is already registered - left alone." -Level muted
        return $true
    }

    $cliArgs = @('mcp', 'add', '--scope', $Scope, $Name, $Command)
    if ($Arguments.Count) { $cliArgs += '--'; $cliArgs += $Arguments }
    $r = Invoke-AutoOSProcess -FilePath 'claude' -Arguments $cliArgs -WorkingDirectory $WorkingDirectory
    # Invoke-AutoOSProcess reports success for a dry run too, and a dry run
    # that says "registered" is a lie the next reader has to discover.
    if ($r.DryRun) { return $true }
    if ($r.Success) {
        Write-AutoOSLine "registered MCP server '$Name' ($Scope scope)" -Level ok
        return $true
    }
    Write-AutoOSLine "could not register '$Name': $($r.Output.Trim())" -Level warn
    $false
}

function Enable-AutoOSProjectMcpServer {
    <#
      .SYNOPSIS
        Approve a project-scoped MCP server for a repository.

      .DESCRIPTION
        A tracked .mcp.json cannot approve itself: Claude Code skips a project
        server until it is named in that repo's own untracked
        .claude/settings.local.json. It skips it *silently*, which is the real
        problem - an unapproved server looks exactly like a broken one.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoPath,
        [Parameter(Mandatory)][string]$Name
    )
    $dir  = Join-Path $RepoPath '.claude'
    $path = Join-Path $dir 'settings.local.json'

    if ($script:DryRun) {
        Write-AutoOSLine "would approve project MCP server '$Name' in $path" -Level muted
        return
    }

    $settings = [ordered]@{}
    if (Test-Path $path) {
        try {
            $raw = Get-Content -Path $path -Raw -Encoding UTF8
            if ($raw.Trim()) {
                $parsed = $raw | ConvertFrom-Json
                foreach ($p in $parsed.PSObject.Properties) { $settings[$p.Name] = $p.Value }
            }
        } catch {
            Write-AutoOSLine "$path is not valid JSON - leaving it alone." -Level warn
            return
        }
        Copy-Item $path "$path.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    } elseif (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    $enabled = @()
    if ($settings.Contains('enabledMcpjsonServers')) { $enabled = @($settings['enabledMcpjsonServers']) }
    if ($Name -in $enabled) {
        Write-AutoOSLine "project MCP server '$Name' was already approved" -Level muted
        return
    }
    $settings['enabledMcpjsonServers'] = @($enabled + $Name)
    $settings | ConvertTo-Json -Depth 12 | Out-File -FilePath $path -Encoding utf8
    Write-AutoOSLine "approved project MCP server '$Name' in $path" -Level ok
}

function Write-AutoOSOmnigraphReadiness {
    <#
      .SYNOPSIS
        Name whichever of Omnigraph's prerequisites are missing.

      .DESCRIPTION
        The omnigraph MCP server is a container talking to a graph server over a
        Docker network. Miss the image, the network or the token and MCP start-up
        fails with "pull access denied", "fetch failed" or "missing bearer token"
        respectively - none of which say which of the three it was. AutoOS does
        not build or start that stack; it reports what is not ready yet.
    #>
    param([Parameter(Mandatory)][string]$AgentSkillsDir)

    if (-not (Get-Command 'docker' -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'docker is not installed - omnigraph runs as a container.' -Level warn
        return $false
    }

    $ready = $true
    $images = (& docker images --format '{{.Repository}}:{{.Tag}}' 2>&1 | Out-String)
    if ($images -notmatch 'omnigraph-mcp:latest') {
        Write-AutoOSLine 'omnigraph-mcp:latest is not built. Build it with:' -Level warn
        Write-AutoOSLine "    docker build -t omnigraph-mcp:latest $AgentSkillsDir\infra\mcp-servers\servers\omnigraph-mcp" -Level muted
        $ready = $false
    }
    $nets = (& docker network ls --format '{{.Name}}' 2>&1 | Out-String)
    if ($nets -notmatch 'mcp-server') {
        Write-AutoOSLine 'no mcp-server Docker network - the graph server stack is not up.' -Level warn
        Write-AutoOSLine "    docker compose -f $AgentSkillsDir\infra\mcp-servers\docker-compose.client.yml up -d" -Level muted
        $ready = $false
    }
    if (-not $env:OMNIGRAPH_TOKEN) {
        # Never invent one. An empty bearer fails as "missing bearer token",
        # which at least names itself; a made-up value fails as a 401 nobody can
        # explain - and this repository is public, so a real-looking secret in it
        # is a leak whether or not it happens to work.
        Write-AutoOSLine 'OMNIGRAPH_TOKEN is not set - the server will reject every call.' -Level warn
        Write-AutoOSLine '    it is issued by the graph server, not by AutoOS. Copy' -Level muted
        Write-AutoOSLine "    $AgentSkillsDir\infra\mcp-servers\.env.client.example to .env.client and fill it in." -Level muted
        $ready = $false
    }
    $ready
}

function Install-AutoOSAgentSkills {
    <#
      .SYNOPSIS
        Clone agent-skills and wire its MCP servers into Claude Code for real.

      .DESCRIPTION
        graphify and omnigraph are wired in opposite ways, and getting it the
        wrong way round fails silently rather than loudly:

          graphify  - ONE user-scope entry. Its command is cwd-relative, so a
                      single definition serves every repository its own graph. A
                      per-repo entry pins one repo's graph for all of them.
          omnigraph - project scope only, pinned per repo by OMNIGRAPH_GRAPH_ID.
                      A user-scope `omnigraph` silently WINS over the project one
                      and answers from the wrong graph, so this never creates one
                      and says so when it finds one.
    #>
    $myDocs = [Environment]::GetFolderPath('MyDocuments')
    $codeRoot = Join-Path $myDocs 'Code'
    if (Test-Path (Join-Path $myDocs 'code')) {
        $codeRoot = Join-Path $myDocs 'code'
    }
    $dest = Join-Path $codeRoot 'agent-skills'
    if (-not $script:DryRun -and -not (Test-Path $codeRoot)) {
        New-Item -ItemType Directory -Path $codeRoot -Force | Out-Null
    }
    if (Test-Path $dest) {
        Invoke-AutoOSProcess -FilePath 'git' -Arguments @('-C', $dest, 'pull', '--ff-only') | Out-Null
    } else {
        Invoke-AutoOSProcess -FilePath 'git' -Arguments @(
            'clone', 'https://github.com/Ch3fUlrich/agent-skills.git', $dest) | Out-Null
    }

    $omniUrl = Get-AutoOSAnswer 'omnigraph_url' ''
    $baseUrl = if ([string]::IsNullOrWhiteSpace($omniUrl)) { 'http://localhost:8080' } else { $omniUrl.TrimEnd('/') }
    Write-AutoOSLine "Omnigraph base URL: $baseUrl" -Level info
    if (-not $script:DryRun) {
        $envFile = Join-Path $env:USERPROFILE '.autoos-omnigraph.env'
        "OMNIGRAPH_BASE_URL=$baseUrl" | Out-File -FilePath $envFile -Encoding utf8
        Write-AutoOSLine "Omnigraph URL saved to $envFile" -Level ok
    }

    # ── MCP stack: wire user-scope servers across Claude Code and Antigravity ──
    Install-AutoOSMcpGraphify
    Install-AutoOSMcpSerena
    Install-AutoOSMcpPlaywright
    Install-AutoOSMcpContext7

    # ── omnigraph: project scope, and only project scope ──────────────────────
    if ('omnigraph' -in (Get-AutoOSMcpServerNames)) {
        Write-AutoOSLine 'A user-scope omnigraph server exists. It silently overrides the' -Level warn
        Write-AutoOSLine 'per-repo one and answers from the wrong graph. Remove it with:' -Level warn
        Write-AutoOSLine '    claude mcp remove omnigraph --scope user' -Level muted
    }
    $projectMcp = Join-Path $dest '.mcp.json'
    if (Test-Path $projectMcp) {
        Write-AutoOSLine "omnigraph is declared per-repo in $projectMcp" -Level muted
        Enable-AutoOSProjectMcpServer -RepoPath $dest -Name 'omnigraph'
    } else {
        Write-AutoOSLine "no .mcp.json in $dest - nothing to pin omnigraph to." -Level warn
    }

    Set-AutoOSAntigravityMcp

    # ── Wire skills into Antigravity and Claude Code global skills directories ──
    $agySkills = Join-Path $env:USERPROFILE '.gemini\config\skills'
    $claudeSkills = Join-Path $env:USERPROFILE '.claude\skills'
    $skillsSrc = Join-Path $dest 'skills'
    if (Test-Path $skillsSrc) {
        if ($script:DryRun) {
            Write-AutoOSLine "would link skills from $skillsSrc to $agySkills and $claudeSkills" -Level muted
        } else {
            foreach ($dir in @($agySkills, $claudeSkills)) {
                if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            }
            foreach ($s in Get-ChildItem -Path $skillsSrc -Directory) {
                $agyTarget = Join-Path $agySkills $s.Name
                $claudeTarget = Join-Path $claudeSkills $s.Name
                if (-not (Test-Path $agyTarget)) {
                    try {
                        New-Item -ItemType Junction -Path $agyTarget -Target $s.FullName | Out-Null
                    } catch {
                        Copy-Item -Path $s.FullName -Destination $agyTarget -Recurse -Force
                    }
                }
                if (-not (Test-Path $claudeTarget)) {
                    try {
                        New-Item -ItemType Junction -Path $claudeTarget -Target $s.FullName | Out-Null
                    } catch {
                        Copy-Item -Path $s.FullName -Destination $claudeTarget -Recurse -Force
                    }
                }
            }
            Write-AutoOSLine 'Agent skills registered with Antigravity and Claude Code' -Level ok
        }
    }

    if ($script:DryRun) {
        Write-AutoOSLine 'would check the omnigraph image, network and token' -Level muted
        return
    }
    if (Write-AutoOSOmnigraphReadiness -AgentSkillsDir $dest) {
        Write-AutoOSLine 'omnigraph prerequisites are all present.' -Level ok
    }
    Write-AutoOSLine 'Restart Claude Code and Antigravity - MCP servers are only read at session start.' -Level info
}

function Set-AutoOSAntigravityMcp {
    <#
      .SYNOPSIS Point Antigravity at the same Omnigraph server as Claude Code.

      .DESCRIPTION
        Merges one entry into Antigravity's MCP config. It used to write the file
        from scratch, which silently deleted every other MCP server the user had
        configured there - a backup makes that recoverable, not acceptable.

        The token and graph id are read from the environment and only written
        when they are actually set. AutoOS has no business inventing either: an
        absent OMNIGRAPH_TOKEN fails as "missing bearer token", which says what
        is wrong, and an absent OMNIGRAPH_GRAPH_ID is better than a guessed one,
        because the fallback graph is the shared `memory` graph that this repo's
        data must never be written to.
    #>
    $omniUrl = Get-AutoOSAnswer 'omnigraph_url' ''
    $baseUrl = if ([string]::IsNullOrWhiteSpace($omniUrl)) { 'http://localhost:8080' } else { $omniUrl.TrimEnd('/') }
    $cfgDir  = Join-Path $env:APPDATA 'Antigravity'
    $cfgPath = Join-Path $cfgDir 'mcp_config.json'

    $envBlock = [ordered]@{ OMNIGRAPH_BASE_URL = $baseUrl }
    if ($env:OMNIGRAPH_GRAPH_ID) { $envBlock['OMNIGRAPH_GRAPH_ID'] = $env:OMNIGRAPH_GRAPH_ID }
    if ($env:OMNIGRAPH_TOKEN)    { $envBlock['OMNIGRAPH_TOKEN']    = $env:OMNIGRAPH_TOKEN }

    if ($script:DryRun) {
        Write-AutoOSLine "would merge omnigraph into $cfgPath (base: $baseUrl)" -Level muted
        return
    }
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }

    $cfg = [ordered]@{}
    if (Test-Path $cfgPath) {
        try {
            $raw = Get-Content -Path $cfgPath -Raw -Encoding UTF8
            if ($raw.Trim()) {
                $parsed = $raw | ConvertFrom-Json
                foreach ($p in $parsed.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
            }
        } catch {
            Write-AutoOSLine "$cfgPath is not valid JSON - leaving it alone." -Level warn
            return
        }
        Copy-Item $cfgPath "$cfgPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }

    $servers = [ordered]@{}
    if ($cfg.Contains('mcpServers') -and $cfg['mcpServers']) {
        foreach ($p in $cfg['mcpServers'].PSObject.Properties) { $servers[$p.Name] = $p.Value }
    }
    $servers['omnigraph'] = [ordered]@{
        command = 'npx'
        args    = @('-y', (Get-AutoOSMcpPackage -Name 'omnigraph'))
        env     = $envBlock
    }
    $cfg['mcpServers'] = $servers

    $cfg | ConvertTo-Json -Depth 12 | Out-File -FilePath $cfgPath -Encoding utf8
    $kept = @($servers.Keys | Where-Object { $_ -ne 'omnigraph' })
    if ($kept.Count) {
        Write-AutoOSLine "omnigraph merged into $cfgPath (kept: $($kept -join ', '))" -Level ok
    } else {
        Write-AutoOSLine "Antigravity MCP config written to $cfgPath" -Level ok
    }
    if (-not $env:OMNIGRAPH_TOKEN) {
        Write-AutoOSLine 'OMNIGRAPH_TOKEN was not set, so no bearer token was written.' -Level warn
    }
}

function Register-AutoOSAntigravityMcpServer {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][hashtable]$Spec
    )
    $cfgDir  = Join-Path $env:APPDATA 'Antigravity'
    $cfgPath = Join-Path $cfgDir 'mcp_config.json'

    if ($script:DryRun) {
        Write-AutoOSLine "would merge $Name into $cfgPath" -Level muted
        return
    }
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }

    $cfg = [ordered]@{}
    if (Test-Path $cfgPath) {
        try {
            $raw = Get-Content -Path $cfgPath -Raw -Encoding UTF8
            if ($raw.Trim()) {
                $parsed = $raw | ConvertFrom-Json
                foreach ($p in $parsed.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
            }
        } catch {
            Write-AutoOSLine "$cfgPath is not valid JSON - leaving it alone." -Level warn
            return
        }
        Copy-Item $cfgPath "$cfgPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }

    $servers = [ordered]@{}
    if ($cfg.Contains('mcpServers') -and $cfg['mcpServers']) {
        foreach ($p in $cfg['mcpServers'].PSObject.Properties) { $servers[$p.Name] = $p.Value }
    }
    $servers[$Name] = $Spec
    $cfg['mcpServers'] = $servers

    $cfg | ConvertTo-Json -Depth 12 | Out-File -FilePath $cfgPath -Encoding utf8
    Write-AutoOSLine "Antigravity MCP server '$Name' configured in $cfgPath" -Level ok
}

function Install-AutoOSMcpSerena {
    Write-AutoOSLine 'Configuring Serena MCP server (Claude Code + Antigravity)' -Level step
    $serenaPkg = Get-AutoOSMcpPackage -Name 'serena'
    [void](Register-AutoOSMcpServer -Name 'serena' -Command 'uvx' -Scope 'user' -Arguments @(
        '--from', $serenaPkg, 'serena', 'start-mcp-server',
        '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'))

    $serenaHome = Join-Path $HOME '.serena'
    Register-AutoOSAntigravityMcpServer -Name 'serena' -Spec ([ordered]@{
        command      = 'uvx'
        args         = @('--from', $serenaPkg, 'serena', 'start-mcp-server', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false')
        env          = @{ SERENA_HOME = $serenaHome }
        excludeTools = @(Get-AutoOSSerenaExcludedTools)
    })

    Set-AutoOSSerenaExclusions

    # excludeTools above only reaches Serena when Claude Code/Antigravity start
    # it with that flag. Serena's OWN global config is what every other MCP
    # client (or a bare `serena start-mcp-server`) gets instead, so this probe
    # actually starts Serena and asks it, over real JSON-RPC, which tools it
    # exposes - the only way to know Set-AutoOSSerenaExclusions' file edit
    # really took effect. It must run here, after the call above, and never
    # inside Set-AutoOSSerenaExclusions itself, so tests that call that
    # function directly never spawn a real Serena process.
    $py = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
    $hasSerenaOrUvx = (Get-Command serena -ErrorAction SilentlyContinue) -or (Get-Command uvx -ErrorAction SilentlyContinue)
    if ($py -and $hasSerenaOrUvx -and -not $script:DryRun) {
        $probeScript = Join-Path $script:RepoRoot 'tools\check-serena-tools.py'
        # Under $ErrorActionPreference = 'Stop' (this module's default), Windows
        # PowerShell 5.1 turns every native stderr LINE that `2>&1` merges in into
        # a terminating error - so a probe that actually passed (exit 0, with some
        # stderr chatter) still landed in the catch block and reported "failed".
        # A local 'Continue' for the duration of just this native call keeps its
        # stderr merely informational; only $LASTEXITCODE decides pass/fail.
        $savedEap = $ErrorActionPreference
        $launchFailed = $false
        try {
            $ErrorActionPreference = 'Continue'
            $probeOut = & $py.Source $probeScript 2>&1 | Out-String
        } catch {
            $launchFailed = $true
            $probeOut = $_.Exception.Message
        } finally {
            $ErrorActionPreference = $savedEap
        }
        if (-not $launchFailed -and $LASTEXITCODE -eq 0) {
            Write-AutoOSLine "Serena tool exclusion probe: $($probeOut.Trim())" -Level ok
        } else {
            Write-AutoOSLine "Serena tool exclusion probe failed: $($probeOut.Trim())" -Level warn
        }
    }
}

function Set-AutoOSSerenaExclusions {
    <#
      .SYNOPSIS Ensure Serena's global config excludes AutoOS's canonical tool list.

      .DESCRIPTION
        Mirrors ensure_serena_exclusions() in lib/linux/install.sh, line for
        line where PowerShell allows it - the two must treat every YAML shape
        below identically, since both are exercised against the very same
        fixture files. Serena's global config (~/.serena/serena_config.yml by
        default) has its own excluded_tools list, independent of the
        excludeTools flag Register-AutoOS*McpServer bakes into each client's
        invocation. Anything missing from THIS list is exposed to any MCP
        client that talks to Serena directly - onboarding and the memory
        tools in particular, which this repo deliberately routes through
        Omnigraph instead (see CLAUDE.md's "one tool per job"). This adds the
        canonical 15 while leaving every other BYTE of the file - the user's
        own extra exclusions, comments, encoding and line endings - untouched.

        Bytes, not text: the file is decoded strictly as UTF-8 first (BOM
        preserved if present); if that throws, it falls back to Latin-1,
        which - like Python's errors='surrogateescape' on the bash side -
        maps every one of the 256 byte values to exactly one char and back,
        so an untouched region round-trips byte-for-byte even if the file
        isn't valid UTF-8 (e.g. a stray non-UTF-8 byte in someone's comment).
    #>
    param([string]$ConfigPath = (Join-Path $HOME '.serena\serena_config.yml'))

    $canonical = @(Get-AutoOSSerenaExcludedTools)

    if ($script:DryRun) {
        Write-AutoOSLine "would ensure Serena's excluded_tools list is complete in $ConfigPath" -Level muted
        return
    }

    $existsAsAny = Test-Path $ConfigPath
    $existed = Test-Path $ConfigPath -PathType Leaf
    if ($existsAsAny -and -not $existed) {
        Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - it is a directory" -Level warn
        return
    }

    $hadBom = $false
    $writeEncoding = [Text.UTF8Encoding]::new($false)
    $raw = ''
    if ($existed) {
        try {
            $rawBytes = [IO.File]::ReadAllBytes($ConfigPath)
        } catch {
            Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - $($_.Exception.Message)" -Level warn
            return
        }
        $offset = 0
        if ($rawBytes.Length -ge 3 -and $rawBytes[0] -eq 0xEF -and $rawBytes[1] -eq 0xBB -and $rawBytes[2] -eq 0xBF) {
            $hadBom = $true
            $offset = 3
        }
        try {
            $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
            $raw = $strictUtf8.GetString($rawBytes, $offset, $rawBytes.Length - $offset)
            $writeEncoding = [Text.UTF8Encoding]::new($false)
        } catch {
            if ($hadBom) {
                Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - has a UTF-8 BOM but is not valid UTF-8" -Level warn
                return
            }
            $writeEncoding = [Text.Encoding]::GetEncoding(28591)
            $raw = $writeEncoding.GetString($rawBytes, 0, $rawBytes.Length)
        }
    }

    $newline = "`n"
    if ($raw -match "`r`n") { $newline = "`r`n" }

    # Each line keeps its own terminator ($ends[i]: CRLF, LF or '' for an
    # unterminated last line), like the bash twin's splitlines(keepends=True).
    # Only lines this function generates use the dominant $newline, so a file
    # with mixed endings keeps every byte outside the edited block.
    $lines = [System.Collections.Generic.List[string]]::new()
    $ends = [System.Collections.Generic.List[string]]::new()
    if ($raw.Length -gt 0) {
        $pos = 0
        foreach ($m in [regex]::Matches($raw, "`r`n|`n")) {
            [void]$lines.Add($raw.Substring($pos, $m.Index - $pos))
            [void]$ends.Add($m.Value)
            $pos = $m.Index + $m.Length
        }
        if ($pos -lt $raw.Length) {
            [void]$lines.Add($raw.Substring($pos))
            [void]$ends.Add('')
        }
    }

    # Quote-aware, comment-aware scalar value: a quoted value runs up to its
    # matching closing quote (a '#' inside the quotes is just a character);
    # an unquoted value ends at the first whitespace-then-'#' (a trailing
    # comment) or at end of string.
    function Get-AutoOSSerenaScalar([string]$Value) {
        $s = $Value.Trim()
        if ($s.Length -eq 0) { return '' }
        if ($s[0] -eq "'" -or $s[0] -eq '"') {
            $q = $s[0]
            $i = 1
            while ($i -lt $s.Length -and $s[$i] -ne $q) { $i++ }
            if ($i -lt $s.Length) { return $s.Substring(1, $i - 1) }
            return $s.Substring(1)
        }
        if ($s.StartsWith('#')) { return '' }
        $m = [regex]::Match($s, '\s#')
        if ($m.Success) { return $s.Substring(0, $m.Index).Trim() }
        return $s.Trim()
    }

    # Comma-split that does not split on a comma inside quotes.
    function Get-AutoOSSerenaFlowItems([string]$Inner) {
        $items = [System.Collections.Generic.List[string]]::new()
        $cur = ''
        $q = $null
        foreach ($ch in $Inner.ToCharArray()) {
            if ($q) {
                $cur += $ch
                if ($ch -eq $q) { $q = $null }
            } elseif ($ch -eq "'" -or $ch -eq '"') {
                $q = $ch
                $cur += $ch
            } elseif ($ch -eq ',') {
                [void]$items.Add($cur)
                $cur = ''
            } else {
                $cur += $ch
            }
        }
        if ($cur.Trim().Length -gt 0) { [void]$items.Add($cur) }
        return $items
    }

    # Like Get-AutoOSSerenaScalar, but also returns a trailing comment on the
    # item line itself (e.g. "- 'read_file'  # canonical"), quote-aware:
    # text after a quoted value's closing quote is a comment candidate
    # exactly like text after an unquoted value. Returns a hashtable with
    # Value and Comment (Comment is $null when there wasn't one).
    function Get-AutoOSSerenaItemValue([string]$Line) {
        $s = $Line.Trim() -replace '^-\s*', ''
        $value = ''
        $tail = ''
        if ($s.Length -gt 0 -and ($s[0] -eq "'" -or $s[0] -eq '"')) {
            $q = $s[0]
            $i = 1
            while ($i -lt $s.Length -and $s[$i] -ne $q) { $i++ }
            if ($i -lt $s.Length) {
                $value = $s.Substring(1, $i - 1)
                $tail = $s.Substring($i + 1)
            } else {
                $value = $s.Substring(1)
            }
        } elseif ($s.StartsWith('#')) {
            $tail = $s
        } else {
            $m = [regex]::Match($s, '\s#')
            if ($m.Success) {
                $value = $s.Substring(0, $m.Index).Trim()
                $tail = $s.Substring($m.Index)
            } else {
                $value = $s.Trim()
            }
        }
        $comment = $tail.Trim()
        return @{ Value = $value; Comment = $(if ($comment.StartsWith('#')) { $comment } else { $null }) }
    }

    # "-" is a list item only when followed by whitespace or nothing; "---"
    # (a document separator) or "-foo" is ordinary text, not an item.
    function Test-AutoOSSerenaBlockItem([string]$S) {
        if ($S -eq '-') { return $true }
        if (-not $S.StartsWith('-')) { return $false }
        return $S.Length -gt 1 -and [char]::IsWhiteSpace($S[1])
    }

    # Scans a bracketed flow list starting at $TextHere.Substring($Idx)
    # ($TextHere is $Lines[$J] - no terminator to strip, PowerShell's $lines
    # never carry one), consuming further lines until the matching unquoted
    # ']'. A '#' outside quotes, at the start of a physical line or preceded
    # by whitespace, starts a comment that runs to the end of that line: it
    # is stripped out of the buffer (so it can't swallow the next item) and
    # returned separately. Returns a hashtable: Buf, EndLine, Tail, Comments.
    function Get-AutoOSSerenaFlowScan([System.Collections.Generic.List[string]]$Lines, [int]$J, [string]$TextHere, [int]$Idx) {
        $buf = ''
        $q = $null
        $tail = $null
        $comments = [System.Collections.Generic.List[string]]::new()
        while ($true) {
            $seg = $TextHere
            while ($Idx -lt $seg.Length) {
                $c = $seg[$Idx]
                if ($q) {
                    $buf += $c
                    if ($c -eq $q) { $q = $null }
                } elseif ($c -eq "'" -or $c -eq '"') {
                    $q = $c
                    $buf += $c
                } elseif ($c -eq ']' -and -not $q) {
                    $tail = $seg.Substring($Idx + 1)
                    break
                } elseif ($c -eq '#' -and -not $q -and ($Idx -eq 0 -or $seg[$Idx - 1] -eq ' ' -or $seg[$Idx - 1] -eq "`t")) {
                    $comment = $seg.Substring($Idx)
                    if ($comment.Trim().Length -gt 0) { [void]$comments.Add($comment) }
                    break
                } else {
                    $buf += $c
                }
                $Idx++
            }
            if ($null -ne $tail) { break }
            $J++
            if ($J -ge $Lines.Count) { $tail = ''; break }
            $buf += "`n"
            $TextHere = $Lines[$J]
            $Idx = 0
        }
        return @{ Buf = $buf; EndLine = $J; Tail = $tail; Comments = $comments }
    }

    $keyStart = -1
    $keyEndExclusive = -1
    $existing = [System.Collections.Generic.List[string]]::new()
    $interiorComments = [System.Collections.Generic.List[string]]::new()
    $newKeyLineText = $null

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith('excluded_tools:')) {
            $keyStart = $i
            $restNoLnEnd = $lines[$i].Substring('excluded_tools:'.Length)
            $rest = $restNoLnEnd.Trim()

            if ($rest.StartsWith('[')) {
                # Flow form: [a, b] - possibly with a trailing comment,
                # possibly spanning several lines before its closing ']'.
                $idx0 = $restNoLnEnd.IndexOf('[') + 1
                $scan = Get-AutoOSSerenaFlowScan $lines $i $restNoLnEnd $idx0
                foreach ($part in (Get-AutoOSSerenaFlowItems $scan.Buf)) {
                    $val = Get-AutoOSSerenaScalar $part
                    if ($val) { [void]$existing.Add($val) }
                }
                foreach ($c in $scan.Comments) { [void]$interiorComments.Add($c) }
                $keyEndExclusive = $scan.EndLine + 1
                if ($scan.Tail -and $scan.Tail.Trim().StartsWith('#')) {
                    $newKeyLineText = 'excluded_tools:' + $scan.Tail
                } else {
                    $newKeyLineText = 'excluded_tools:'
                }
            } elseif ($rest -eq '' -or $rest.StartsWith('#')) {
                # Bare key (or one with a trailing comment). The value is
                # normally an indented block of "- item" lines starting on
                # the next line, but it can also be a flow list moved to its
                # own line ("excluded_tools:\n  [a, b]") - peek past any
                # blank/comment lines for the first real content and
                # dispatch on it.
                $k = $i + 1
                $peekComments = [System.Collections.Generic.List[string]]::new()
                while ($k -lt $lines.Count) {
                    $tk = $lines[$k].Trim()
                    if ($tk -eq '') { $k++ }
                    elseif ($tk.StartsWith('#')) { [void]$peekComments.Add($lines[$k]); $k++ }
                    else { break }
                }

                if ($k -lt $lines.Count -and $lines[$k].Trim().StartsWith('[')) {
                    $textK = $lines[$k]
                    $idx0 = $textK.IndexOf('[') + 1
                    $scan = Get-AutoOSSerenaFlowScan $lines $k $textK $idx0
                    foreach ($part in (Get-AutoOSSerenaFlowItems $scan.Buf)) {
                        $val = Get-AutoOSSerenaScalar $part
                        if ($val) { [void]$existing.Add($val) }
                    }
                    foreach ($c in $peekComments) { [void]$interiorComments.Add($c) }
                    foreach ($c in $scan.Comments) { [void]$interiorComments.Add($c) }
                    if ($scan.Tail -and $scan.Tail.Trim().StartsWith('#')) { [void]$interiorComments.Add($scan.Tail.Trim()) }
                    $keyEndExclusive = $scan.EndLine + 1
                } else {
                    # Block form. A comment line only belongs to the block if
                    # another list item follows it later - move those to
                    # directly after the key line, in original order. A
                    # trailing comment after the last item (or before the
                    # next top-level key) is NOT ours and is left where it
                    # is. Blank lines inside the block are simply dropped.
                    $j = $i + 1
                    $committedEnd = $i + 1
                    $pendingComments = [System.Collections.Generic.List[string]]::new()
                    while ($j -lt $lines.Count) {
                        $l = $lines[$j]
                        $trimmed = $l.Trim()
                        if (Test-AutoOSSerenaBlockItem $trimmed) {
                            $itemResult = Get-AutoOSSerenaItemValue $l
                            [void]$existing.Add($itemResult.Value)
                            foreach ($pc in $pendingComments) { [void]$interiorComments.Add($pc) }
                            $pendingComments.Clear()
                            if ($itemResult.Comment) { [void]$interiorComments.Add($itemResult.Comment) }
                            $j++
                            $committedEnd = $j
                        } elseif ($trimmed -eq '') {
                            $j++
                        } elseif ($trimmed.StartsWith('#')) {
                            [void]$pendingComments.Add($l)
                            $j++
                        } else {
                            break
                        }
                    }
                    $keyEndExclusive = $committedEnd
                }
                $newKeyLineText = $lines[$keyStart]
            } else {
                # Some other scalar (e.g. "excluded_tools: null") - not a
                # list AutoOS understands; replace it outright.
                $keyEndExclusive = $i + 1
                $newKeyLineText = 'excluded_tools:'
            }
            break
        }
    }

    # Serena tool names are case-sensitive; PowerShell's -notcontains is not
    # (it falls back to -eq's default case-insensitive string comparison),
    # so an existing "Read_File" would wrongly count as satisfying the
    # canonical "read_file" and this would report "skipped" when it should
    # add both. -cnotcontains forces an ordinal, case-sensitive comparison -
    # the same one $seen (a HashSet[string], case-sensitive by default) and
    # Python's `set`/`in` already use on the bash side.
    $allPresent = $true
    foreach ($c in $canonical) {
        if ($existing -cnotcontains $c) { $allPresent = $false; break }
    }
    if ($allPresent) {
        Write-AutoOSLine 'Serena excluded_tools already complete (skipped)' -Level muted
        return
    }

    $merged = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($c in $canonical) { [void]$merged.Add($c); [void]$seen.Add($c) }
    foreach ($item in $existing) {
        if ($item -and -not $seen.Contains($item)) {
            [void]$merged.Add($item)
            [void]$seen.Add($item)
        }
    }
    $itemLines = foreach ($m in $merged) { "- $m" }

    # Untouched lines keep their own terminator ($ends); generated lines end
    # with $newline. So the generated block always ends cleanly, while an
    # untouched tail keeps its final newline, or its lack of one.
    $sb = [System.Text.StringBuilder]::new()
    if ($keyStart -lt 0) {
        for ($i = 0; $i -lt $lines.Count; $i++) {
            # An unterminated last line needs a newline before the new block.
            $end = if ($ends[$i] -eq '') { $newline } else { $ends[$i] }
            [void]$sb.Append($lines[$i]).Append($end)
        }
        [void]$sb.Append('excluded_tools:').Append($newline)
        foreach ($il in $itemLines) { [void]$sb.Append($il).Append($newline) }
    } else {
        for ($i = 0; $i -lt $keyStart; $i++) { [void]$sb.Append($lines[$i]).Append($ends[$i]) }
        [void]$sb.Append($newKeyLineText).Append($newline)
        foreach ($ic in $interiorComments) { [void]$sb.Append($ic).Append($newline) }
        foreach ($il in $itemLines) { [void]$sb.Append($il).Append($newline) }
        for ($i = $keyEndExclusive; $i -lt $lines.Count; $i++) { [void]$sb.Append($lines[$i]).Append($ends[$i]) }
    }

    $content = $sb.ToString()
    $contentBytes = $writeEncoding.GetBytes($content)
    $newBytes = if ($hadBom) { [byte[]](0xEF, 0xBB, 0xBF) + $contentBytes } else { $contentBytes }

    $cfgDir = Split-Path -Parent $ConfigPath
    try {
        if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }
    } catch {
        Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - $($_.Exception.Message)" -Level warn
        return
    }

    if ($existed) {
        try {
            Copy-Item $ConfigPath "$ConfigPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
        } catch {
            Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - could not back it up: $($_.Exception.Message)" -Level warn
            return
        }
    }

    # Temp file + rename: the config is either fully replaced or left
    # completely untouched, never partially written.
    $tmpPath = "$ConfigPath.autoos-tmp-$PID"
    try {
        [IO.File]::WriteAllBytes($tmpPath, $newBytes)
        Move-Item -Path $tmpPath -Destination $ConfigPath -Force
    } catch {
        if (Test-Path $tmpPath) { Remove-Item -Path $tmpPath -Force -ErrorAction SilentlyContinue }
        Write-AutoOSLine "could not update Serena's excluded_tools in $ConfigPath - $($_.Exception.Message)" -Level warn
        return
    }

    if ($existed) {
        Write-AutoOSLine "updated Serena excluded_tools in $ConfigPath" -Level ok
    } else {
        Write-AutoOSLine "created $ConfigPath with Serena's excluded_tools" -Level ok
    }
}

function Install-AutoOSMcpGraphify {
    Write-AutoOSLine 'Configuring Graphify MCP server (Claude Code + Antigravity)' -Level step
    $graphifyPkg = Get-AutoOSMcpPackage -Name 'graphify'
    [void](Register-AutoOSMcpServer -Name 'graphify' -Command 'uv' -Scope 'user' -Arguments @(
        '--quiet', 'run', '--with', $graphifyPkg, 'python', '-m',
        'graphify.serve', 'graphify-out/graph.json'))

    Register-AutoOSAntigravityMcpServer -Name 'graphify' -Spec ([ordered]@{
        command = 'uv'
        args    = @('--quiet', 'run', '--with', $graphifyPkg, 'python', '-m', 'graphify.serve', '${workspaceFolder}/graphify-out/graph.json')
    })
}

function Install-AutoOSMcpPlaywright {
    Write-AutoOSLine 'Configuring Playwright MCP server (Claude Code + Antigravity)' -Level step
    $playwrightPkg = Get-AutoOSMcpPackage -Name 'playwright'
    [void](Register-AutoOSMcpServer -Name 'playwright' -Command 'npx' -Scope 'user' -Arguments @(
        '-y', $playwrightPkg))

    Register-AutoOSAntigravityMcpServer -Name 'playwright' -Spec ([ordered]@{
        command = 'npx'
        args    = @('-y', $playwrightPkg)
    })
}

function Install-AutoOSMcpContext7 {
    Write-AutoOSLine 'Configuring Context7 MCP server (Claude Code + Antigravity)' -Level step
    $context7Pkg = Get-AutoOSMcpPackage -Name 'context7'
    $key = Get-AutoOSAnswer 'context7_api_key' $env:CONTEXT7_API_KEY
    if ($key) {
        [void](Register-AutoOSMcpServer -Name 'context7' -Command 'npx' -Scope 'user' -Arguments @(
            '-y', $context7Pkg, '--api-key', $key))
        Register-AutoOSAntigravityMcpServer -Name 'context7' -Spec ([ordered]@{
            command = 'npx'
            args    = @('-y', $context7Pkg, '--api-key', $key)
        })
    } else {
        [void](Register-AutoOSMcpServer -Name 'context7' -Command 'npx' -Scope 'user' -Arguments @(
            '-y', $context7Pkg))
        Register-AutoOSAntigravityMcpServer -Name 'context7' -Spec ([ordered]@{
            command = 'npx'
            args    = @('-y', $context7Pkg)
        })
    }
}

function Get-AutoOSSkillsSource {
    <#
      .SYNOPSIS Locate the machine's agent-skills skills directory.

      .DESCRIPTION
        Returns the first existing of Documents\Code\agent-skills\skills and
        Documents\code\agent-skills\skills, or $null when neither exists.
    #>
    $myDocs = [Environment]::GetFolderPath('MyDocuments')
    foreach ($rel in @('Code\agent-skills\skills', 'code\agent-skills\skills')) {
        $candidate = Join-Path $myDocs $rel
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Set-AutoOSOpenCodeConfig {
    <#
      .SYNOPSIS Configure OpenCode CLI with local Ollama, MCP tools, and optional keys.

      .DESCRIPTION
        Configures OpenCode in ~/.config/opencode/opencode.json (and copies to
        %APPDATA%\opencode\config.json for Windows compatibility).
        Works 100% keyless by default against local Ollama (http://127.0.0.1:11434/v1).
        If API keys are present in env or secrets/api_keys.conf, registers Meta (muse-spark-1.3)
        and DeepSeek (deepseek-chat).
    #>
    $configDir = Join-Path $HOME '.config\opencode'
    $configFile = Join-Path $configDir 'opencode.json'

    if ($script:DryRun) {
        Write-AutoOSLine "would configure OpenCode in $configFile" -Level muted
        Write-AutoOSLine 'would apply the agent harness (catalog/agent-harness.json)' -Level muted
        return
    }

    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }

    $existing = [ordered]@{}
    if (Test-Path $configFile) {
        try {
            $raw = Get-Content -Path $configFile -Raw -Encoding UTF8
            if ($raw.Trim()) {
                $parsed = $raw | ConvertFrom-Json
                foreach ($p in $parsed.PSObject.Properties) { $existing[$p.Name] = $p.Value }
            }
        } catch {
            Write-AutoOSLine "$configFile is not valid JSON - leaving it alone." -Level warn
            return
        }
        Copy-Item $configFile "$configFile.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }

    if (-not $existing.Contains('$schema')) {
        $existing['$schema'] = 'https://opencode.ai/config.json'
    }

    $providers = [ordered]@{}
    if ($existing.Contains('provider') -and $existing['provider']) {
        foreach ($p in $existing['provider'].PSObject.Properties) { $providers[$p.Name] = $p.Value }
    }

    $modelsFile = Join-Path $script:RepoRoot 'catalog\llm-models.json'
    $repoModels = (Get-Content -Path $modelsFile -Raw -Encoding UTF8 | ConvertFrom-Json).models
    $repoById = @{}
    foreach ($m in $repoModels) { $repoById[$m.id] = $m }
    $ollamaUrl = Resolve-AutoOSOllamaBaseUrl
    if ($ollamaUrl) { $repoById['ollama-qwen2.5-coder'].direct.base_url = $ollamaUrl }

    function Get-OpenRouterModelEntry($m) {
        # Projects one shared entry into the OpenCode provider shape.
        # `cost` uses the paid counterpart where one exists, so estimates
        # hold past the free cap; free variants themselves bill $0.
        $inPrice = if ($m.PSObject.Properties.Name.Contains('paid_input_price') -and $null -ne $m.paid_input_price) { $m.paid_input_price } else { $m.input_price }
        $outPrice = if ($m.PSObject.Properties.Name.Contains('paid_output_price') -and $null -ne $m.paid_output_price) { $m.paid_output_price } else { $m.output_price }
        $cost = [ordered]@{ input = $inPrice * 1e6; output = $outPrice * 1e6 }
        if ($m.PSObject.Properties.Name.Contains('cache_read_price') -and $m.cache_read_price) {
            $cost['cache_read'] = $m.cache_read_price * 1e6
        }
        $entry = [ordered]@{
            name  = $m.name
            limit = [ordered]@{ context = $m.context; output = $m.output }
            cost  = $cost
        }
        if ($m.PSObject.Properties.Name.Contains('reasoning') -and $m.reasoning) { $entry['reasoning'] = $true }
        return $entry
    }

    $providers['ollama'] = [ordered]@{
        npm     = $repoById['ollama-qwen2.5-coder'].direct.npm
        name    = 'Ollama'
        options = [ordered]@{
            baseURL = $repoById['ollama-qwen2.5-coder'].direct.base_url
        }
        models  = [ordered]@{
            'qwen2.5-coder:7b' = [ordered]@{ name = 'Qwen 2.5 Coder 7B' }
            'qwen3:30b'        = [ordered]@{ name = 'Qwen 3 30B' }
            'hermes3:8b'       = [ordered]@{ name = 'Hermes 3 8B' }
        }
    }

    $secretsPath = Join-Path $HOME 'Documents\Code\agent-skills\secrets\api_keys.conf'
    $secrets = Read-AutoOSApiSecrets -SecretsPath $secretsPath

    $museKey = if ($env:MUSE_API_KEY) { $env:MUSE_API_KEY } elseif ($secrets.ContainsKey('muse')) { $secrets['muse'] } else { $null }
    if ($museKey) {
        $muse = $repoById['muse-spark']
        $museModelId = $muse.direct.model.Split('/', 2)[1]
        $providers['meta'] = [ordered]@{
            npm     = $muse.direct.npm
            name    = 'Meta'
            options = [ordered]@{
                baseURL = $muse.direct.base_url
                apiKey  = $museKey
            }
            models  = [ordered]@{
                $museModelId = [ordered]@{
                    name      = $muse.name
                    reasoning = $true
                    limit     = [ordered]@{ context = $muse.context; output = $muse.output }
                    options   = [ordered]@{ reasoningEffort = $muse.direct.reasoning_effort }
                }
            }
        }
    }

    $deepseekKey = if ($env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY } elseif ($secrets.ContainsKey('deepseek')) { $secrets['deepseek'] } else { $null }
    if ($deepseekKey) {
        $ds = $repoById['deepseek-chat'].direct
        $dsModels = [ordered]@{}
        foreach ($mid in @('deepseek-chat', 'deepseek-reasoner')) {
            $dm = $repoById[$mid]
            $entry = [ordered]@{
                name  = $dm.name
                limit = [ordered]@{ context = $dm.context; output = $dm.output }
            }
            if ($dm.PSObject.Properties.Name.Contains('reasoning') -and $dm.reasoning) { $entry['reasoning'] = $true }
            $dsModels[$mid] = $entry
        }
        $providers['deepseek'] = [ordered]@{
            npm     = $ds.npm
            name    = 'DeepSeek'
            options = [ordered]@{
                baseURL = $ds.base_url
                apiKey  = $deepseekKey
            }
            models  = $dsModels
        }
    }

    # Projected from catalog/llm-models.json (single source of truth):
    # `limit` carries the free-variant context window; `cost` is per 1M
    # tokens, so chat spend stays estimable once a free cap is exhausted.
    $openrouterKey = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } elseif ($secrets.ContainsKey('openrouter')) { $secrets['openrouter'] } else { $null }
    if ($openrouterKey) {
        $orModels = [ordered]@{}
        foreach ($m in $repoModels) {
            if (-not $m.openrouter_id) { continue }
            $orModels[$m.openrouter_id] = Get-OpenRouterModelEntry $m
        }
        $providers['openrouter'] = [ordered]@{
            npm     = '@ai-sdk/openai-compatible'
            name    = 'OpenRouter'
            options = [ordered]@{
                baseURL = 'https://openrouter.ai/api/v1'
                apiKey  = $openrouterKey
            }
            models  = $orModels
        }
    }

    $existing['provider'] = $providers

    if (-not $existing.Contains('model') -or -not $existing['model']) {
        if ($museKey) {
            $existing['model'] = 'meta/' + $repoById['muse-spark'].direct.model.Split('/', 2)[1]
        } else {
            $existing['model'] = 'ollama/' + $repoById['ollama-qwen2.5-coder'].direct.model.Split('/', 2)[1]
        }
    }


    $mcpServers = [ordered]@{}
    if ($existing.Contains('mcp') -and $existing['mcp']) {
        foreach ($p in $existing['mcp'].PSObject.Properties) { $mcpServers[$p.Name] = $p.Value }
    }

    $mcpServers['serena'] = [ordered]@{
        type    = 'local'
        command = @('uvx', '--from', (Get-AutoOSMcpPackage -Name 'serena'), 'serena', 'start-mcp-server', '--context', 'claude-code', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false')
        enabled = $true
    }
    $mcpServers['graphify'] = [ordered]@{
        type    = 'local'
        command = @('uvx', '--from', (Get-AutoOSMcpPackage -Name 'graphify'), 'python', '-m', 'graphify.serve', 'graphify-out/graph.json')
        enabled = $true
    }
    $mcpServers['context7'] = [ordered]@{
        type    = 'local'
        command = @('npx', '-y', (Get-AutoOSMcpPackage -Name 'context7'))
        enabled = $true
    }
    $mcpServers['omnigraph'] = [ordered]@{
        type    = 'local'
        command = @('npx', '-y', (Get-AutoOSMcpPackage -Name 'omnigraph'))
        enabled = $true
        environment = [ordered]@{
            OMNIGRAPH_BASE_URL = 'http://localhost:8080'
            OMNIGRAPH_GRAPH_ID = 'autoos'
        }
    }
    $mcpServers['playwright'] = [ordered]@{
        type    = 'local'
        command = @('npx', '-y', (Get-AutoOSMcpPackage -Name 'playwright'))
        enabled = $true
    }

    $existing['mcp'] = $mcpServers

    $json = $existing | ConvertTo-Json -Depth 10
    $json | Out-File -FilePath $configFile -Encoding utf8
    Write-AutoOSLine "OpenCode configuration written to $configFile" -Level ok

    # Merge the shared agent harness (roles, skills link) after the config is
    # written. Judge by exit code only; no 2>&1, since under 'Stop' Windows
    # PowerShell 5.1 turns a native stderr line into a terminating error.
    $skillsSource = Get-AutoOSSkillsSource
    if (-not $skillsSource) {
        # Pass the would-be path even when missing: the generator records
        # "skills: source missing" rather than failing.
        $skillsSource = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Code\agent-skills\skills'
    }
    $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $pythonCmd) {
        $pythonCmd = (Get-Command py -ErrorAction SilentlyContinue)
    }
    if ($pythonCmd) {
        $harnessOut = & $pythonCmd.Source (Join-Path $script:RepoRoot 'lib\agent_harness.py') opencode --config $configFile --repo-root $script:RepoRoot --skills-source $skillsSource
        if ($LASTEXITCODE -ne 0) {
            Write-AutoOSLine "agent harness not applied to OpenCode (exit $LASTEXITCODE)" -Level warn
        } else {
            foreach ($line in $harnessOut) { Write-AutoOSLine $line -Level muted }
        }
    } else {
        Write-AutoOSLine "agent harness not applied: python not found" -Level warn
    }

    if ($env:APPDATA) {
        $appDataDir = Join-Path $env:APPDATA 'opencode'
        if (-not (Test-Path $appDataDir)) { New-Item -ItemType Directory -Path $appDataDir -Force | Out-Null }
        $appDataFile = Join-Path $appDataDir 'config.json'
        (Get-Content $configFile -Raw) | Out-File -FilePath $appDataFile -Encoding utf8
    }
}

function Set-AutoOSOpenHandsConfig {
    <#
      .SYNOPSIS Configure OpenHands settings, models, profiles, skills, and automations.

      .DESCRIPTION
        Configures OpenHands in ~/.openhands/settings.json, creates model profiles in
        ~/.openhands/profiles/ (with 1M token contexts for DeepSeek and Muse Spark Contributor),
        wires agent-skills via junction/symlink, creates ACP agent profiles, and
        seeds default configuration.
    #>
    $openhandsDir = Join-Path $HOME '.openhands'
    $settingsFile = Join-Path $openhandsDir 'settings.json'

    if ($script:DryRun) {
        Write-AutoOSLine "would configure OpenHands in $openhandsDir" -Level muted
        return
    }

    if (-not (Test-Path $openhandsDir)) {
        New-Item -ItemType Directory -Path $openhandsDir -Force | Out-Null
    }

    $profilesDir = Join-Path $openhandsDir 'profiles'
    if (-not (Test-Path $profilesDir)) {
        New-Item -ItemType Directory -Path $profilesDir -Force | Out-Null
    }

    $agentProfilesDir = Join-Path $openhandsDir 'agent-profiles'
    if (-not (Test-Path $agentProfilesDir)) {
        New-Item -ItemType Directory -Path $agentProfilesDir -Force | Out-Null
    }

    $autoDir = Join-Path $openhandsDir 'automation'
    if (-not (Test-Path $autoDir)) {
        New-Item -ItemType Directory -Path $autoDir -Force | Out-Null
    }

    if (Test-Path $settingsFile) {
        Copy-Item $settingsFile "$settingsFile.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }

    $secretsPath = Join-Path $HOME 'Documents\Code\agent-skills\secrets\api_keys.conf'
    $secrets = Read-AutoOSApiSecrets -SecretsPath $secretsPath

    $museKey = if ($env:MUSE_API_KEY) { $env:MUSE_API_KEY } elseif ($secrets.ContainsKey('muse')) { $secrets['muse'] } else { $null }
    $deepseekKey = if ($env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY } elseif ($secrets.ContainsKey('deepseek')) { $secrets['deepseek'] } else { $null }
    $openrouterKey = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } elseif ($secrets.ContainsKey('openrouter')) { $secrets['openrouter'] } else { $null }
    $context7Key = if ($env:CONTEXT7_API_KEY) { $env:CONTEXT7_API_KEY } elseif ($secrets.ContainsKey('context7')) { $secrets['context7'] } else { $null }

    $skillsSource = Get-AutoOSSkillsSource
    $skillsTarget = Join-Path $openhandsDir 'skills'
    if ($skillsSource -and (Test-Path $skillsSource) -and -not (Test-Path $skillsTarget)) {
        try {
            cmd.exe /c "mklink /J `"$skillsTarget`" `"$skillsSource`"" | Out-Null
            Write-AutoOSLine "Linked agent-skills to OpenHands skills directory" -Level ok
        } catch {
            Write-AutoOSLine "Could not create skills junction: $_" -Level warn
        }
    }

    $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $pythonCmd) {
        $pythonCmd = (Get-Command py -ErrorAction SilentlyContinue)
    }

    if ($pythonCmd) {
        # Literal (single-quoted) here-string: the embedded Python contains
        # $-expressions for bash ($HOME/$PATH) and Python comments ($0) that
        # PowerShell must NOT expand. Under Set-StrictMode an expanding @"..@
        # string throws InvalidOperation on every one of them, which broke
        # automatic OpenHands setup entirely. The catalog is read from disk
        # via the repo-root argument, never inlined into a string literal.
        $setupScript = @'
import os, sys, json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

_repo_root_arg = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] not in ('', 'null') else ''
_models_file = os.path.join(_repo_root_arg, 'catalog', 'llm-models.json')
with open(_models_file, 'r', encoding='utf-8') as _mf:
    REPO_MODELS = json.load(_mf)['models']
REPO_BY_ID = {m['id']: m for m in REPO_MODELS}
# MCP package specs live in catalog/agent-harness.json, never inlined here.
_harness_file = os.path.join(_repo_root_arg, 'catalog', 'agent-harness.json')
with open(_harness_file, 'r', encoding='utf-8') as _hf:
    MCP_PACKAGES = {k: v['package'] for k, v in json.load(_hf)['mcp_servers'].items()}
# Resolve-AutoOSOllamaBaseUrl's answer; empty keeps the catalog default. Applied
# to the catalog entry so the profiles and the settings.json fallback agree.
if os.environ.get('OLLAMA_BASE_URL'):
    REPO_BY_ID['ollama-qwen2.5-coder']['direct']['base_url'] = os.environ['OLLAMA_BASE_URL']
# OpenHands reaches Ollama through LiteLLM, whose ollama routes append /api/... to the base:
# the OpenAI-compatible /v1 base (right for OpenCode) gives 404 there (measured 2026-09-19).
# ollama_chat/ uses /api/chat, which supports tool calls. OpenHands only; OpenCode keeps /v1.
_ol = REPO_BY_ID['ollama-qwen2.5-coder']['direct']
_ol['model'] = 'ollama_chat/' + _ol['model'].split('/', 1)[1]
_ol['base_url'] = _ol['base_url'].rstrip('/')
if _ol['base_url'].endswith('/v1'):
    _ol['base_url'] = _ol['base_url'][:-3]

def _profile_for(mid, key, name=None):
    m = REPO_BY_ID[mid]
    profile_name = (name or mid) + '.json'
    # Vendored structural template (openhands/profiles/<name>.json): model id,
    # base_url, auth shape and the thinking flags. Token windows, prices and
    # the api_key are projected below, so the catalog stays the single source
    # of truth for everything numeric.
    p = dict(TEMPLATES.get(profile_name, {}))
    if m.get('openrouter_id'):
        model = 'openrouter/' + m['openrouter_id']
    else:
        model = m['direct']['model']
    p.update({'model': model, 'max_input_tokens': m['context'],
              'max_output_tokens': m['output'],
              'input_cost_per_token': m['input_price'],
              'output_cost_per_token': m['output_price']})
    if m.get('direct', {}).get('base_url'):
        p['base_url'] = m['direct']['base_url']
    elif 'base_url' in p and m.get('openrouter_id'):
        # OpenRouter endpoints resolve server-side; never persist a stale URL.
        del p['base_url']
    if m.get('reasoning'):
        p['reasoning_effort'] = 'high'
    else:
        # Explicit opt-out. The OpenHands SDK LLM model defaults to
        # reasoning_effort=high + encrypted reasoning + a 200k thinking
        # budget, and any sparse profile is materialized through those
        # defaults. Non-thinking providers (Ollama) hard-fail such requests
        # with '"model" does not support thinking'.
        p['reasoning_effort'] = 'none'
        p['enable_encrypted_reasoning'] = False
        p['extended_thinking_budget'] = None
    for opt in ('paid_input_price', 'paid_output_price', 'cache_read_price'):
        dst = {'paid_input_price': 'paid_input_cost_per_token',
               'paid_output_price': 'paid_output_cost_per_token',
               'cache_read_price': 'cache_read_cost_per_token'}[opt]
        if m.get(opt) is not None:
            p[dst] = m[opt]
    p['api_key'] = key
    return profile_name, p

openhands_dir = sys.argv[1]
repo_root = _repo_root_arg
TEMPLATES = {}
_templates_dir = os.path.join(repo_root, 'openhands', 'profiles') if repo_root else ''
if _templates_dir and os.path.isdir(_templates_dir):
    for _fn in os.listdir(_templates_dir):
        if _fn.endswith('.json'):
            try:
                with open(os.path.join(_templates_dir, _fn), 'r', encoding='utf-8') as _tf:
                    TEMPLATES[_fn] = json.load(_tf)
            except Exception:
                pass
muse_key = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != 'null' else None
deepseek_key = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != 'null' else None
openrouter_key = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != 'null' else None

settings_file = os.path.join(openhands_dir, 'settings.json')
settings = {}
if os.path.isfile(settings_file):
    try:
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    except Exception:
        settings = {}

settings.setdefault('schema_version', 2)
agent_settings = settings.setdefault('agent_settings', {})
agent_settings.setdefault('schema_version', 5)
agent_settings.setdefault('agent_kind', 'openhands')
agent_settings.setdefault('agent', 'CodeActAgent')

llm = agent_settings.setdefault('llm', {})
_muse = REPO_BY_ID['muse-spark']['direct']
_ds = REPO_BY_ID['deepseek-chat']['direct']
# reasoning_effort must follow the chosen default: only thinking models get
# "high". The unconditional "high" used to poison the local/Ollama fallback
# (and deepseek-chat) with thinking params Ollama rejects outright.
_default_reasoning = False
if muse_key:
    llm['model'] = _muse['model']
    llm['base_url'] = _muse['base_url']
    llm['api_key'] = muse_key
    _default_reasoning = bool(REPO_BY_ID['muse-spark'].get('reasoning'))
elif deepseek_key:
    llm['model'] = _ds['model']
    llm['base_url'] = _ds['base_url']
    llm['api_key'] = deepseek_key
    _default_reasoning = bool(REPO_BY_ID['deepseek-chat'].get('reasoning'))
elif openrouter_key:
    llm['model'] = 'openrouter/openrouter/free'
    llm['base_url'] = 'https://openrouter.ai/api/v1'
    llm['api_key'] = openrouter_key
    _default_reasoning = bool(REPO_BY_ID['openrouter-free'].get('reasoning'))
else:
    _local = REPO_BY_ID['ollama-qwen2.5-coder']['direct']
    llm['model'] = _local['model']
    llm['base_url'] = _local['base_url']
    _default_reasoning = bool(REPO_BY_ID['ollama-qwen2.5-coder'].get('reasoning'))

llm['max_input_tokens'] = 1048576
llm['max_output_tokens'] = 65536
if _default_reasoning:
    llm['reasoning_effort'] = 'high'
else:
    llm['reasoning_effort'] = 'none'
    llm['enable_encrypted_reasoning'] = False
    llm['extended_thinking_budget'] = None
llm['drop_params'] = True
llm['modify_params'] = True

agent_context = agent_settings.setdefault('agent_context', {})
agent_context['load_user_skills'] = True

mcp_cfg = agent_settings.setdefault('mcp_config', {})
mcp_cfg['serena'] = {
    'transport': 'stdio',
    'command': 'uvx',
    'args': ['--from', MCP_PACKAGES['serena'], 'serena', 'start-mcp-server', '--project-from-cwd', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'],
    'description': 'Semantic code retrieval and symbol intelligence',
    'enabled': True
}
mcp_cfg['graphify'] = {
    'transport': 'stdio',
    'command': 'uv',
    'args': ['--quiet', 'run', '--with', MCP_PACKAGES['graphify'], 'python', '-m', 'graphify.serve', 'graphify-out/graph.json'],
    'description': 'Codebase dependency knowledge graph',
    'enabled': True
}
mcp_cfg['omnigraph'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ['-y', MCP_PACKAGES['omnigraph']],
    'env': {'OMNIGRAPH_BASE_URL': 'http://localhost:8080', 'OMNIGRAPH_GRAPH_ID': 'autoos'},
    'description': 'Project memory graph for this repository (repo-scoped, not global)',
    'enabled': True
}
ctx7_args = ['-y', MCP_PACKAGES['context7']]
context7_key = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != 'null' else os.environ.get('CONTEXT7_API_KEY')
if context7_key:
    ctx7_args.extend(['--api-key', context7_key])
mcp_cfg['context7'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ctx7_args,
    'description': 'Upstash Context7 semantic search and retrieval',
    'enabled': True
}
mcp_cfg['playwright'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ['-y', MCP_PACKAGES['playwright']],
    'description': 'Browser automation and end-to-end verification',
    'enabled': True
}
mcp_cfg['cao-ops'] = {
    'transport': 'stdio',
    'command': 'wsl',
    'args': ['-d', 'Ubuntu', 'bash', '-c', 'export PATH=\"$HOME/.local/bin:$PATH\"; export CAO_HOME_DIR=\"$HOME/.cao\"; cao-ops-mcp-server'],
    'description': 'CLI Agent Orchestrator 3-level coordination bridge',
    'enabled': True
}
if 'github' in mcp_cfg:
    del mcp_cfg['github']

with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)

profiles_dir = os.path.join(openhands_dir, 'profiles')
# Prices are USD per token from catalog/llm-models.json. Free variants bill
# $0 while under the daily cap; paid_*_cost_per_token applies past it, so
# spend = in_tokens*in_price + out_tokens*out_price stays auditable.
profiles = dict([
    _profile_for('deepseek-chat', deepseek_key),
    _profile_for('deepseek-reasoner', deepseek_key),
    _profile_for('deepseek-v4-flash', deepseek_key),
    _profile_for('muse-spark', muse_key, 'muse-spark-1.3'),
    _profile_for('muse-spark', muse_key, 'muse-spark-1.3-contributor'),
    _profile_for('openrouter-free', openrouter_key),
    _profile_for('openrouter-nemotron-ultra', openrouter_key),
    _profile_for('openrouter-nemotron-super', openrouter_key),
    _profile_for('openrouter-nemotron-lightning', openrouter_key),
    _profile_for('openrouter-nemotron-nano-omni', openrouter_key),
    _profile_for('openrouter-laguna', openrouter_key),
    _profile_for('openrouter-laguna-xs', openrouter_key),
    _profile_for('openrouter-north-mini-code', openrouter_key),
    _profile_for('openrouter-nex-pro', openrouter_key),
    _profile_for('openrouter-nex-mini', openrouter_key),
    _profile_for('openrouter-inkling', openrouter_key),
    _profile_for('openrouter-inkling-small', openrouter_key),
    _profile_for('openrouter-dots3-note', openrouter_key),
    _profile_for('openrouter-ling-fin', openrouter_key),
    _profile_for('openrouter-ling-sante', openrouter_key),
    _profile_for('openrouter-ling-vl', openrouter_key),
    _profile_for('ollama-qwen2.5-coder', None),
])
# Legacy alias: older setups wrote ollama-qwen-coder.json and existing UI
# selections point at it. Keep it byte-identical to the canonical profile.
_alias_src, _alias_data = _profile_for('ollama-qwen2.5-coder', None)
profiles['ollama-qwen-coder.json'] = _alias_data
for name, p_data in profiles.items():
    with open(os.path.join(profiles_dir, name), 'w', encoding='utf-8') as f:
        json.dump(p_data, f, indent=2)

agent_profiles_dir = os.path.join(openhands_dir, 'agent-profiles')
# Vendored agent profiles (openhands/agent-profiles/*.json in the repo) are
# the desired state and are copied verbatim on every setup. Their
# llm_profile_ref values point at the canonical profile names written above.
# Role profiles are generated from the harness below, not copied: copying them
# here would race the generator and freeze the role's managed keys.
_role_profiles = {r['openhands']['profile'] + '.json' for r in json.load(open(os.path.join(repo_root, 'catalog', 'agent-harness.json'), encoding='utf-8'))['roles'].values()}
_vendored_agents = os.path.join(repo_root, 'openhands', 'agent-profiles') if repo_root else ''
if _vendored_agents and os.path.isdir(_vendored_agents):
    for _fn in sorted(os.listdir(_vendored_agents)):
        if not _fn.endswith('.json') or _fn in _role_profiles:
            continue
        try:
            with open(os.path.join(_vendored_agents, _fn), 'r', encoding='utf-8') as _af:
                _a_data = json.load(_af)
            with open(os.path.join(agent_profiles_dir, _fn), 'w', encoding='utf-8') as _of:
                json.dump(_a_data, _of, indent=2)
        except Exception:
            pass
'@
        $argMuse = if ($museKey) { $museKey } else { 'null' }
        $argDeepseek = if ($deepseekKey) { $deepseekKey } else { 'null' }
        $argOpenrouter = if ($openrouterKey) { $openrouterKey } else { 'null' }
        $argContext7 = if ($context7Key) { $context7Key } else { 'null' }

        # The resolved address reaches the child through its environment; the
        # caller's own OLLAMA_BASE_URL is restored afterwards.
        $savedOllama = $env:OLLAMA_BASE_URL
        try {
            $env:OLLAMA_BASE_URL = Resolve-AutoOSOllamaBaseUrl
            & $pythonCmd.Source -c $setupScript $openhandsDir $argMuse $argDeepseek $argOpenrouter $argContext7 $script:RepoRoot
        }
        finally { $env:OLLAMA_BASE_URL = $savedOllama }

        # The role agent profiles are the generator's job: it merges the
        # harness into whatever the embedded script left, so the roles stay in
        # one place. Judge by exit code only; no 2>&1, since under 'Stop' Windows
        # PowerShell 5.1 turns a native stderr line into a terminating error.
        $harnessOut = & $pythonCmd.Source (Join-Path $script:RepoRoot 'lib\agent_harness.py') openhands --openhands-dir $openhandsDir --repo-root $script:RepoRoot
        if ($LASTEXITCODE -ne 0) {
            Write-AutoOSLine "agent harness not applied to OpenHands (exit $LASTEXITCODE)" -Level warn
        } else {
            foreach ($line in $harnessOut) { Write-AutoOSLine $line -Level muted }
        }
    }

    # OpenHands probes PowerShell with a 5 s timeout and without -NoProfile
    # (OpenHands/software-agent-sdk#5133; fix pending in #3913). It sets AI_AGENT, so a
    # profile that returns at once for it keeps the probe fast. Existing profiles only.
    $docs = [Environment]::GetFolderPath('MyDocuments')
    foreach ($prof in @((Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1'),
                        (Join-Path $docs 'PowerShell\profile.ps1'),
                        (Join-Path $docs 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
                        (Join-Path $docs 'WindowsPowerShell\profile.ps1'))) {
        if (Test-Path $prof) {
            Add-AutoOSProfileLine -Prepend -ProfilePath $prof -Marker "AI_AGENT -eq 'openhands'" `
                -Line "if (`$env:AI_AGENT -eq 'openhands') { return }  # OpenHands' PowerShell probe times out after 5 s (software-agent-sdk#5133)"
        }
    }

    Write-AutoOSLine "OpenHands configuration and profiles written to $openhandsDir" -Level ok
}

function Install-AutoOSLitellm {
    <#
      .SYNOPSIS Install the litellm proxy (the tier router in configuration/litellm).
    #>
    if ($script:DryRun) {
        Write-AutoOSLine 'would install litellm[proxy] via pipx or pip' -Level muted
        return
    }
    if (Get-Command litellm -ErrorAction SilentlyContinue) {
        Write-AutoOSLine 'litellm already installed' -Level muted
        return
    }
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        Invoke-AutoOSProcess -FilePath 'pipx' -Arguments @('install', 'litellm[proxy]') | Out-Null
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-AutoOSProcess -FilePath 'py' -Arguments @('-m', 'pip', 'install', '--user', 'litellm[proxy]') | Out-Null
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Invoke-AutoOSProcess -FilePath 'python' -Arguments @('-m', 'pip', 'install', '--user', 'litellm[proxy]') | Out-Null
    } else {
        Write-AutoOSLine "no Python on PATH - install it, then: pip install litellm[proxy]" -Level warn
        return
    }
    Write-AutoOSLine 'next: copy configuration/litellm/.env.example to .env, add keys (docs/api-keys.md)' -Level info
}

function Set-AutoOSZedProxy {
    <#
      .SYNOPSIS Point Zed's agent panel at the local OmniRoute gateway.
      Only the provider id 'autoos-omniroute' is written; every other Zed
      setting is kept. The key comes from env AUTOOS_OMNIROUTE_KEY.
    #>
    $cfgDir  = Join-Path $env:APPDATA 'Zed'
    $cfgPath = Join-Path $cfgDir 'settings.json'
    if ($script:DryRun) {
        Write-AutoOSLine "would route Zed agents to OmniRoute in $cfgPath" -Level muted
        return
    }
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }
    if (Test-Path $cfgPath) {
        Copy-Item $cfgPath "$cfgPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }
    $settings = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { New-Object psobject }
    # StrictMode turns a missing-property read into a throw (even member
    # enumeration over an empty property set), so probe the collection with
    # the indexer, which returns $null for a missing name instead of throwing.
    if ($null -eq $settings.PSObject.Properties['language_models']) {
        Add-Member -InputObject $settings -NotePropertyName 'language_models' -NotePropertyValue (New-Object psobject)
    }
    if ($null -eq $settings.language_models.PSObject.Properties['openai_compatible']) {
        Add-Member -InputObject $settings.language_models -NotePropertyName 'openai_compatible' -NotePropertyValue (New-Object psobject)
    }
    $entry = @{
        api_url = 'http://127.0.0.1:20128/v1'
        available_models = @(
            @{ name = 'auto/smart'; display_name = 'tier1 orchestrator (auto smart)'; max_tokens = 131072; reasoning_effort = 'xhigh' },
            @{ name = 'auto'; display_name = 'tier2 smart (auto balanced)'; max_tokens = 131072 },
            @{ name = 'auto/cheap'; display_name = 'tier3 driver (auto cheap)'; max_tokens = 131072 }
        )
    }
    Add-Member -InputObject $settings.language_models.openai_compatible -NotePropertyName 'autoos-omniroute' -NotePropertyValue $entry -Force
    $settings | ConvertTo-Json -Depth 8 | Out-File -FilePath $cfgPath -Encoding utf8
    Write-AutoOSLine 'Zed agents routed to OmniRoute (key via AUTOOS_OMNIROUTE_KEY)' -Level ok
}

function Install-AutoOSOpenHands {
    <#
      .SYNOPSIS Pull the OpenHands image; the container is started on demand.
      .DESCRIPTION
        OpenHands runs as a Docker container wired to the OmniRoute gateway by
        configuration/start-stack.ps1 -App openhands. Installing is "have the
        image locally"; nothing is started here, so setup stays non-interactive.
    #>
    $image = 'docker.openhands.dev/openhands/openhands:latest'
    if ($script:DryRun) {
        Write-AutoOSLine "would pull $image (Docker Desktop must be running)" -Level muted
        return
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'docker CLI not found - install Docker Desktop, then re-run' -Level warn
        return
    }
    $probe = Invoke-AutoOSProcess -FilePath 'docker' -Arguments @('info', '--format', '{{.ServerVersion}}')
    if (-not $probe.Success) {
        Write-AutoOSLine 'Docker daemon is not running - start Docker Desktop, then: docker pull ' -Level warn
        Write-AutoOSLine "    $image" -Level muted
        return
    }
    $pull = Invoke-AutoOSProcess -FilePath 'docker' -Arguments @('pull', $image)
    if ($pull.Success) {
        Write-AutoOSLine 'OpenHands image ready' -Level ok
        Write-AutoOSLine 'start it with: .\configuration\start-stack.ps1 -App openhands' -Level info
    } else {
        Write-AutoOSLine "docker pull failed (exit $($pull.ExitCode)) - check Docker Desktop" -Level warn
    }
}

function Install-AutoOSNeovim {
    <#
      .SYNOPSIS
        Make nvim reachable and install the LazyVim starter config.
      .DESCRIPTION
        PATH is only ever appended to via Add-AutoOSPathEntry (the single PATH
        code path) — never replaced. The LazyVim + sidekick steps mirror
        install_lazyvim / enable_sidekick_extra in lib/linux/install.sh.
        Idempotent: every step is a no-op when already done. Dry-run safe:
        announces, writes nothing.
    #>
    $binDir = Join-Path $env:ProgramFiles 'Neovim\bin'
    if (Test-Path (Join-Path $binDir 'nvim.exe')) {
        $onPath = $false
        foreach ($scope in @('User', 'Machine')) {
            $scopePath = [Environment]::GetEnvironmentVariable('Path', $scope)
            if ($scopePath -and (@($scopePath -split ';' |
                    Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') }).Count -gt 0)) {
                $onPath = $true
            }
        }
        if ($onPath) {
            Write-AutoOSLine "Neovim already on PATH ($binDir)" -Level muted
        } else {
            Add-AutoOSPathEntry -Directory @($binDir) | Out-Null
        }
    } elseif (-not (Get-Command nvim -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'nvim.exe not found - install Neovim, then re-run' -Level warn
    }
    Install-AutoOSLazyVim
}

function Install-AutoOSLazyVim {
    <#
      .SYNOPSIS Clone the LazyVim starter config once, then enable sidekick.
    #>
    $dest = Join-Path $env:LOCALAPPDATA 'nvim'
    if (Test-Path $dest) {
        Write-AutoOSLine 'nvim config already exists - leaving it alone' -Level muted
    } elseif ($script:DryRun) {
        Write-AutoOSLine "would install the LazyVim starter into $dest" -Level muted
    } else {
        $cloned = Invoke-AutoOSProcess -FilePath 'git' -Arguments @(
            'clone', '--depth', '1', 'https://github.com/LazyVim/starter', $dest)
        if (-not $cloned.Success) {
            Write-AutoOSLine 'LazyVim clone failed - check git, then re-run' -Level warn
            return
        }
        Remove-Item (Join-Path $dest '.git') -Recurse -Force -ErrorAction SilentlyContinue
        Write-AutoOSLine 'LazyVim starter installed' -Level ok
    }
    Enable-AutoOSSidekickExtra
}

function Enable-AutoOSSidekickExtra {
    <#
      .SYNOPSIS Enable Folke's sidekick.nvim extra (opencode in Neovim).
      .DESCRIPTION
        sidekick embeds the opencode CLI (<leader>aa), which inherits the repo
        routing. Enabling = one id in lazyvim.json; every other key is kept,
        and the file is backed up before the first write. Mirrors
        enable_sidekick_extra in lib/linux/install.sh.
    #>
    $cfgDir = Join-Path $env:LOCALAPPDATA 'nvim'
    $lj = Join-Path $cfgDir 'lazyvim.json'
    $extra = 'lazyvim.plugins.extras.ai.sidekick'
    if ($script:DryRun) {
        Write-AutoOSLine "would enable the sidekick extra in $lj" -Level muted
        return
    }
    if (-not (Test-Path $cfgDir)) {
        Write-AutoOSLine 'no nvim config - skipping sidekick' -Level muted
        return
    }
    $cfg = @{}
    if (Test-Path $lj) {
        try {
            $parsed = Get-Content $lj -Raw | ConvertFrom-Json
            if ($parsed -is [System.Collections.IEnumerable] -and $parsed -isnot [string]) {
                Write-AutoOSLine "unexpected shape in $lj - leaving it alone" -Level warn
                return
            }
            foreach ($p in $parsed.PSObject.Properties) {
                $cfg[$p.Name] = $p.Value
            }
        } catch {
            Write-AutoOSLine "could not parse $lj - leaving it alone" -Level warn
            return
        }
        Copy-Item $lj "$lj.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }
    $extras = @()
    if ($cfg.ContainsKey('extras')) { $extras = @($cfg['extras']) }
    if ($extras -contains $extra) {
        Write-AutoOSLine 'sidekick extra already enabled' -Level muted
        return
    }
    $cfg['extras'] = @($extras) + @($extra)
    $cfg | ConvertTo-Json -Depth 8 | Out-File -FilePath $lj -Encoding utf8
    Write-AutoOSLine 'sidekick extra enabled (<leader>aa toggles the opencode panel)' -Level ok
}

function Invoke-AutoOSPostInstall {
    param([Parameter(Mandatory)][psobject]$Component)
    if (-not $Component.PostInstall) { return }
    $fn = Get-Command $Component.PostInstall -ErrorAction SilentlyContinue
    if (-not $fn) {
        Write-AutoOSLine "post-install '$($Component.PostInstall)' not found" -Level warn
        return
    }
    Write-AutoOSLine "post-install: $($Component.PostInstall)" -Level step
    & $fn
}

Export-ModuleMember -Function `
    Initialize-AutoOSInstaller, Get-AutoOSAnswer, Invoke-AutoOSProcess, Add-AutoOSPathEntry,
    Read-AutoOSSecretsFile, Read-AutoOSApiSecrets, Resolve-AutoOSOllamaBaseUrl,
    Get-AutoOSMcpPackage, Get-AutoOSSerenaExcludedTools,
    Register-AutoOSMcpServer, Enable-AutoOSProjectMcpServer, Get-AutoOSMcpServerNames,
    Write-AutoOSOmnigraphReadiness,
    Test-AutoOSInstalled, Get-AutoOSInstalledComponents, Install-AutoOSComponent, Invoke-AutoOSPostInstall,
    Add-AutoOSGitToPath, Set-AutoOSGitConfig, Add-AutoOSCondaToPath, New-AutoOSCondaEnv, Install-AutoOSNerdFont,
    Install-AutoOSHerdr, Install-AutoOSClaudeAutostart,
    Write-AutoOSClaudeHostReadiness, Install-AutoOSPoshTheme, Add-AutoOSProfileLine,
    Install-AutoOSWindhawkMods, Install-AutoOSAgentSkills, Set-AutoOSAntigravityMcp,
    Register-AutoOSAntigravityMcpServer, Install-AutoOSMcpSerena, Set-AutoOSSerenaExclusions, Install-AutoOSMcpGraphify,
    Install-AutoOSMcpPlaywright, Install-AutoOSMcpContext7,
    Set-AutoOSOpenCodeConfig, Set-AutoOSOpenHandsConfig,
    Install-AutoOSLitellm, Set-AutoOSZedProxy, Install-AutoOSOpenHands,
    Install-AutoOSNeovim, Install-AutoOSLazyVim, Enable-AutoOSSidekickExtra,
    Invoke-AutoOSScriptProvider,
    Install-AutoOSOllamaModelQwen34B, Install-AutoOSOllamaModelQwen317B, Install-AutoOSOllamaModelQwenCoder7B,
    Install-AutoOSOterm
