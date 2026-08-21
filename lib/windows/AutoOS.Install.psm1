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

$script:DryRun  = $false
$script:Answers = @{}
$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Initialize-AutoOSInstaller {
    param([bool]$DryRun = $false, [hashtable]$Answers = @{}, [string]$RepoRoot = $null)
    $script:DryRun  = $DryRun
    $script:Answers = $Answers
    if ($RepoRoot) { $script:RepoRoot = $RepoRoot }
}

function Get-AutoOSAnswer {
    param([string]$Key, $Default = '')
    if ($script:Answers.ContainsKey($Key)) { $script:Answers[$Key] } else { $Default }
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
        [string]$WorkingDirectory
    )
    $display = "$FilePath $($Arguments -join ' ')"
    if ($script:DryRun) {
        Write-AutoOSLine "would run: $display" -Level muted
        return @{ ExitCode = 0; Output = ''; DryRun = $true; Success = $true }
    }
    Write-AutoOSLine "run: $display" -Level muted
    $pushed = $false
    try {
        if ($WorkingDirectory -and (Test-Path $WorkingDirectory)) {
            Push-Location $WorkingDirectory; $pushed = $true
        }
        $out = & $FilePath @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        # SuccessCodes is why this exists: winget returns 0x8A15002B when the
        # package is already present, which is a success for our purposes.
        @{ ExitCode = $code; Output = $out; DryRun = $false; Success = ($code -in $SuccessCodes) }
    } catch {
        @{ ExitCode = 1; Output = $_.Exception.Message; DryRun = $false; Success = $false }
    } finally {
        if ($pushed) { Pop-Location }
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
    switch ($Component.Provider) {
        'winget' {
            if (-not (Test-AutoOSCommand 'winget')) { return $false }
            $out = & winget list --id $Component.Package --exact --disable-interactivity 2>&1 | Out-String
            return ($out -match [regex]::Escape($Component.Package))
        }
        'choco' {
            if (-not (Test-AutoOSCommand 'choco')) { return $false }
            $out = & choco list --local-only --exact $Component.Package 2>&1 | Out-String
            return ($out -match '1 packages installed')
        }
        'npm' {
            if (-not (Test-AutoOSCommand 'npm')) { return $false }
            $out = & npm ls -g --depth=0 2>&1 | Out-String
            return ($out -match [regex]::Escape($Component.Package))
        }
        default { return $false }
    }
}

# ─── Providers ──────────────────────────────────────────────────────────────
function Install-AutoOSComponent {
    <#
      .SYNOPSIS Install one component. Returns 'installed' | 'skipped' | 'failed'.
    #>
    param([Parameter(Mandatory)][psobject]$Component)

    if (Test-AutoOSInstalled -Component $Component) {
        Write-AutoOSLine "$($Component.Name) is already installed" -Level muted
        return 'skipped'
    }

    $result = switch ($Component.Provider) {
        'winget' {
            $wingetArgs = @('install', '--id', $Component.Package, '--exact',
                            '--accept-package-agreements', '--accept-source-agreements',
                            '--disable-interactivity', '--silent')
            if ($Component.Source) { $wingetArgs += @('--source', $Component.Source) }
            # 0x8A15002B = "no applicable upgrade / already installed"
            Invoke-AutoOSProcess -FilePath 'winget' -Arguments $wingetArgs -SuccessCodes @(0, -1978335189)
        }
        'choco' {
            Invoke-AutoOSProcess -FilePath 'choco' -Arguments @('install', $Component.Package, '-y', '--no-progress')
        }
        'npm' {
            Invoke-AutoOSProcess -FilePath 'npm' -Arguments @('install', '-g', $Component.Package)
        }
        'script'  { Invoke-AutoOSScriptProvider -Component $Component }
        'custom'  { @{ ExitCode = 0; Output = 'handled by postInstall'; Success = $true } }
        default   { @{ ExitCode = 1; Output = "unknown provider '$($Component.Provider)'"; Success = $false } }
    }

    # Script providers may return a bare hashtable; treat exit 0 as success.
    $ok = if ($result.ContainsKey('Success')) { [bool]$result.Success } else { $result.ExitCode -eq 0 }
    if (-not $ok) {
        Write-AutoOSLine "$($Component.Name) failed (exit $($result.ExitCode))" -Level error
        if ($result.Output) { Write-AutoOSLine ($result.Output.Trim() -split "`n" | Select-Object -First 3 | Out-String).Trim() -Level muted }
        return 'failed'
    }
    'installed'
}

function Invoke-AutoOSScriptProvider {
    param([Parameter(Mandatory)][psobject]$Component)
    switch ($Component.Package) {
        'meslo-nerd-font' { return Install-AutoOSNerdFont }
        'herdr'           { return Install-AutoOSHerdr }
        default           { return @{ ExitCode = 1; Output = "no script for '$($Component.Package)'" } }
    }
}

# ─── Post-install steps ─────────────────────────────────────────────────────
function Add-AutoOSGitToPath {
    Add-AutoOSPathEntry -Directory @("$env:ProgramFiles\Git\cmd") | Out-Null
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

function Install-AutoOSPoshTheme {
    <#
      .SYNOPSIS
        Install the AutoOS oh-my-posh theme and wire it into the right profile.
      .DESCRIPTION
        Two bugs from the Ansible original are fixed here: the theme is installed
        under its OWN name instead of overwriting the shipped
        powerlevel10k_rainbow.omp.json, and the init line goes into the profile
        of the shell it actually initialises.
    #>
    $themeSrc = Join-Path $script:RepoRoot 'Windows\Terminal\oh-my-posh\theme\powerlevel10k_rainbow_env.omp.json'
    if (-not (Test-Path $themeSrc)) {
        Write-AutoOSLine "theme file not found at $themeSrc" -Level warn
        return
    }
    $themeDir = Join-Path $env:LOCALAPPDATA 'AutoOS\themes'
    $themeDst = Join-Path $themeDir 'powerlevel10k_rainbow_env.omp.json'
    if (-not $script:DryRun) {
        if (-not (Test-Path $themeDir)) { New-Item -ItemType Directory -Path $themeDir -Force | Out-Null }
        Copy-Item -Path $themeSrc -Destination $themeDst -Force
    }
    Write-AutoOSLine "theme installed to $themeDst" -Level ok

    $line = "oh-my-posh init pwsh --config `"$themeDst`" | Invoke-Expression"
    # PowerShell 7 profile; falls back to 5.1 when pwsh is absent.
    $pwshProfile = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'PowerShell\Microsoft.PowerShell_profile.ps1'
    $ps5Profile  = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'
    $target = if (Test-AutoOSCommand 'pwsh') { $pwshProfile } else { $ps5Profile }
    if ($target -eq $ps5Profile) { $line = $line -replace 'init pwsh', 'init powershell' }

    Add-AutoOSProfileLine -ProfilePath $target -Line $line -Marker 'oh-my-posh init'
}

function Add-AutoOSProfileLine {
    <#
      .SYNOPSIS Append a line to a shell profile exactly once, with a backup.
    #>
    param(
        [Parameter(Mandatory)][string]$ProfilePath,
        [Parameter(Mandatory)][string]$Line,
        [Parameter(Mandatory)][string]$Marker
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
    Add-Content -Path $ProfilePath -Value "`n# added by AutoOS`n$Line"
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
    $codeRoot = Join-Path $env:USERPROFILE 'Documents\Code'
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

    # ── graphify: one user-scope entry, cwd-relative ──────────────────────────
    [void](Register-AutoOSMcpServer -Name 'graphify' -Command 'uv' -Scope 'user' -Arguments @(
        '--quiet', 'run', '--with', 'graphifyy[mcp]', 'python', '-m',
        'graphify.serve', 'graphify-out/graph.json'))

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

    if ($script:DryRun) {
        Write-AutoOSLine 'would check the omnigraph image, network and token' -Level muted
        return
    }
    if (Write-AutoOSOmnigraphReadiness -AgentSkillsDir $dest) {
        Write-AutoOSLine 'omnigraph prerequisites are all present.' -Level ok
    }
    Write-AutoOSLine 'Restart Claude Code - MCP servers are only read at session start.' -Level info
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
        args    = @('-y', '@modernrelay/omnigraph-mcp@0.8.0')
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
    Register-AutoOSMcpServer, Enable-AutoOSProjectMcpServer, Get-AutoOSMcpServerNames,
    Write-AutoOSOmnigraphReadiness,
    Test-AutoOSInstalled, Install-AutoOSComponent, Invoke-AutoOSPostInstall,
    Add-AutoOSGitToPath, Add-AutoOSCondaToPath, New-AutoOSCondaEnv, Install-AutoOSNerdFont,
    Install-AutoOSHerdr, Install-AutoOSPoshTheme, Add-AutoOSProfileLine,
    Install-AutoOSWindhawkMods, Install-AutoOSAgentSkills, Set-AutoOSAntigravityMcp,
    Invoke-AutoOSScriptProvider
