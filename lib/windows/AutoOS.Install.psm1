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
        [int[]]$SuccessCodes = @(0)
    )
    $display = "$FilePath $($Arguments -join ' ')"
    if ($script:DryRun) {
        Write-AutoOSLine "would run: $display" -Level muted
        return @{ ExitCode = 0; Output = ''; DryRun = $true; Success = $true }
    }
    Write-AutoOSLine "run: $display" -Level muted
    try {
        $out = & $FilePath @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        # SuccessCodes is why this exists: winget returns 0x8A15002B when the
        # package is already present, which is a success for our purposes.
        @{ ExitCode = $code; Output = $out; DryRun = $false; Success = ($code -in $SuccessCodes) }
    } catch {
        @{ ExitCode = 1; Output = $_.Exception.Message; DryRun = $false; Success = $false }
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

function Install-AutoOSAgentSkills {
    <#
      .SYNOPSIS
        Clone agent-skills and point the MCP stack at the chosen Omnigraph server.
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

    if ($script:DryRun) {
        Write-AutoOSLine 'would register serena, graphify and superpowers in ~/.claude.json' -Level muted
        return
    }

    $setup = Join-Path $dest 'infra\mcp-servers\scripts\windows\register-claude-code-mcp.ps1'
    if (Test-Path $setup) {
        Write-AutoOSLine "run this to register the MCP servers:" -Level step
        Write-AutoOSLine "    pwsh $setup -Server serena,graphify,superpowers" -Level muted
    }
    $envFile = Join-Path $env:USERPROFILE '.autoos-omnigraph.env'
    "OMNIGRAPH_BASE_URL=$baseUrl" | Out-File -FilePath $envFile -Encoding utf8
    Write-AutoOSLine "Omnigraph URL saved to $envFile" -Level ok
}

function Set-AutoOSAntigravityMcp {
    <#
      .SYNOPSIS Point Antigravity at the same Omnigraph server as Claude Code.
    #>
    $omniUrl = Get-AutoOSAnswer 'omnigraph_url' ''
    $baseUrl = if ([string]::IsNullOrWhiteSpace($omniUrl)) { 'http://localhost:8080' } else { $omniUrl.TrimEnd('/') }
    $cfgDir  = Join-Path $env:APPDATA 'Antigravity'
    $cfgPath = Join-Path $cfgDir 'mcp_config.json'
    if ($script:DryRun) {
        Write-AutoOSLine "would write Antigravity MCP config -> $cfgPath (omnigraph: $baseUrl)" -Level muted
        return
    }
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }
    if (Test-Path $cfgPath) {
        Copy-Item $cfgPath "$cfgPath.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }
    $cfg = [ordered]@{
        mcpServers = [ordered]@{
            omnigraph = [ordered]@{
                command = 'npx'
                args    = @('-y', '@modernrelay/omnigraph-mcp@0.8.0')
                env     = [ordered]@{ OMNIGRAPH_BASE_URL = $baseUrl }
            }
        }
    }
    $cfg | ConvertTo-Json -Depth 8 | Out-File -FilePath $cfgPath -Encoding utf8
    Write-AutoOSLine "Antigravity MCP config written to $cfgPath" -Level ok
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
    Test-AutoOSInstalled, Install-AutoOSComponent, Invoke-AutoOSPostInstall,
    Add-AutoOSGitToPath, Add-AutoOSCondaToPath, New-AutoOSCondaEnv, Install-AutoOSNerdFont,
    Install-AutoOSHerdr, Install-AutoOSPoshTheme, Add-AutoOSProfileLine,
    Install-AutoOSWindhawkMods, Install-AutoOSAgentSkills, Set-AutoOSAntigravityMcp,
    Invoke-AutoOSScriptProvider
