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

function Invoke-AutoOSScriptProvider {
    param([Parameter(Mandatory)][psobject]$Component)
    switch ($Component.Package) {
        'meslo-nerd-font' { return Install-AutoOSNerdFont }
        'herdr'           { return Install-AutoOSHerdr }
        'claude-autostart'{ return Install-AutoOSClaudeAutostart }
        'agy'             { return Install-AutoOSAgy }
        default           { return @{ ExitCode = 1; Output = "no script for '$($Component.Package)'" } }
    }
}

function Install-AutoOSAgy {
    if ($script:DryRun) {
        Write-AutoOSLine "would install Antigravity CLI via antigravity.google/cli/install.ps1" -Level muted
        return @{ ExitCode = 0; Success = $true }
    }
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://antigravity.google/cli/install.ps1 | iex"
        return @{ ExitCode = $LASTEXITCODE; Success = ($LASTEXITCODE -eq 0) }
    } catch {
        return @{ ExitCode = 1; Output = $_.Exception.Message; Success = $false }
    }
}

# ─── Post-install steps ─────────────────────────────────────────────────────
function Add-AutoOSAgyToPath {
    Add-AutoOSPathEntry -Directory @(Join-Path $env:LOCALAPPDATA 'agy\bin') | Out-Null
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
    [void](Register-AutoOSMcpServer -Name 'serena' -Command 'uvx' -Scope 'user' -Arguments @(
        '--from', 'serena-agent', 'serena', 'start-mcp-server',
        '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'))

    $serenaHome = Join-Path $HOME '.serena'
    Register-AutoOSAntigravityMcpServer -Name 'serena' -Spec ([ordered]@{
        command      = 'uvx'
        args         = @('--from', 'serena-agent', 'serena', 'start-mcp-server', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false')
        env          = @{ SERENA_HOME = $serenaHome }
        excludeTools = @('onboarding', 'open_dashboard', 'initial_instructions', 'write_memory', 'read_memory', 'list_memories', 'delete_memory', 'rename_memory', 'edit_memory')
    })
}

function Install-AutoOSMcpGraphify {
    Write-AutoOSLine 'Configuring Graphify MCP server (Claude Code + Antigravity)' -Level step
    [void](Register-AutoOSMcpServer -Name 'graphify' -Command 'uv' -Scope 'user' -Arguments @(
        '--quiet', 'run', '--with', 'graphifyy[mcp]', 'python', '-m',
        'graphify.serve', 'graphify-out/graph.json'))

    Register-AutoOSAntigravityMcpServer -Name 'graphify' -Spec ([ordered]@{
        command = 'uv'
        args    = @('--quiet', 'run', '--with', 'graphifyy[mcp]', 'python', '-m', 'graphify.serve', '${workspaceFolder}/graphify-out/graph.json')
    })
}

function Install-AutoOSMcpPlaywright {
    Write-AutoOSLine 'Configuring Playwright MCP server (Claude Code + Antigravity)' -Level step
    [void](Register-AutoOSMcpServer -Name 'playwright' -Command 'npx' -Scope 'user' -Arguments @(
        '-y', '@playwright/mcp@latest'))

    Register-AutoOSAntigravityMcpServer -Name 'playwright' -Spec ([ordered]@{
        command = 'npx'
        args    = @('-y', '@playwright/mcp@latest')
    })
}

function Install-AutoOSMcpContext7 {
    Write-AutoOSLine 'Configuring Context7 MCP server (Claude Code + Antigravity)' -Level step
    $key = Get-AutoOSAnswer 'context7_api_key' $env:CONTEXT7_API_KEY
    if ($key) {
        [void](Register-AutoOSMcpServer -Name 'context7' -Command 'npx' -Scope 'user' -Arguments @(
            '-y', '@upstash/context7-mcp', '--api-key', $key))
        Register-AutoOSAntigravityMcpServer -Name 'context7' -Spec ([ordered]@{
            command = 'npx'
            args    = @('-y', '@upstash/context7-mcp', '--api-key', $key)
        })
    } else {
        [void](Register-AutoOSMcpServer -Name 'context7' -Command 'npx' -Scope 'user' -Arguments @(
            '-y', '@upstash/context7-mcp'))
        Register-AutoOSAntigravityMcpServer -Name 'context7' -Spec ([ordered]@{
            command = 'npx'
            args    = @('-y', '@upstash/context7-mcp')
        })
    }
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

    $providers['ollama'] = [ordered]@{
        npm     = '@ai-sdk/openai'
        name    = 'Ollama'
        options = [ordered]@{
            baseURL = 'http://127.0.0.1:11434/v1'
        }
        models  = [ordered]@{
            'qwen2.5-coder:7b' = [ordered]@{ name = 'Qwen 2.5 Coder 7B' }
            'qwen3:30b'        = [ordered]@{ name = 'Qwen 3 30B' }
            'hermes3:8b'       = [ordered]@{ name = 'Hermes 3 8B' }
        }
    }

    $secretsPath = Join-Path $HOME 'Documents\Code\agent-skills\secrets\api_keys.conf'
    $secrets = @{}
    if (Test-Path $secretsPath) {
        Get-Content $secretsPath -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith('#') -and $line -match '=') {
                $parts = $line.Split('=', 2)
                $secrets[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
    }

    $museKey = if ($env:MUSE_API_KEY) { $env:MUSE_API_KEY } elseif ($secrets.ContainsKey('muse')) { $secrets['muse'] } else { $null }
    if ($museKey) {
        $providers['meta'] = [ordered]@{
            npm     = '@ai-sdk/openai'
            name    = 'Meta'
            options = [ordered]@{
                baseURL = 'https://api.meta.ai/v1'
                apiKey  = $museKey
            }
            models  = [ordered]@{
                'muse-spark-1.3-contributor' = [ordered]@{
                    name      = 'Muse Spark 1.3 Contributor'
                    reasoning = $true
                    limit     = [ordered]@{ context = 1048576; output = 131072 }
                    options   = [ordered]@{ reasoningEffort = 'high' }
                }
            }
        }
    }

    $deepseekKey = if ($env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY } elseif ($secrets.ContainsKey('deepseek')) { $secrets['deepseek'] } else { $null }
    if ($deepseekKey) {
        $providers['deepseek'] = [ordered]@{
            npm     = '@ai-sdk/openai'
            name    = 'DeepSeek'
            options = [ordered]@{
                baseURL = 'https://api.deepseek.com'
                apiKey  = $deepseekKey
            }
            models  = [ordered]@{
                'deepseek-chat'     = [ordered]@{ name = 'DeepSeek V3'; limit = [ordered]@{ context = 1048576; output = 65536 } }
                'deepseek-reasoner' = [ordered]@{ name = 'DeepSeek R1'; reasoning = $true; limit = [ordered]@{ context = 1048576; output = 65536 } }
            }
        }
    }

    # OpenRouter free-tier catalogue (September 2026, verified against
    # https://openrouter.ai/api/v1/models). `limit` carries the free-variant
    # context window; `cost` is per 1M tokens from the paid counterpart, so
    # chat spend stays estimable once a free cap is exhausted. Free variants
    # themselves bill $0.
    $openrouterKey = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } elseif ($secrets.ContainsKey('openrouter')) { $secrets['openrouter'] } else { $null }
    if ($openrouterKey) {
        $providers['openrouter'] = [ordered]@{
            npm     = '@ai-sdk/openai-compatible'
            name    = 'OpenRouter'
            options = [ordered]@{
                baseURL = 'https://openrouter.ai/api/v1'
                apiKey  = $openrouterKey
            }
            models  = [ordered]@{
                'free' = [ordered]@{
                    name  = 'OpenRouter Free Auto-Router'
                    limit = [ordered]@{ context = 200000; output = 32768 }
                    cost  = [ordered]@{ input = 0; output = 0 }
                }
                'nvidia/nemotron-3-ultra-550b-a55b:free' = [ordered]@{
                    name      = 'Nemotron 3 Ultra (Free)'
                    reasoning = $true
                    limit     = [ordered]@{ context = 1000000; output = 32768 }
                    cost      = [ordered]@{ input = 0.6; output = 2.4; cache_read = 0.12 }
                }
                'nvidia/nemotron-3-super-120b-a12b:free' = [ordered]@{
                    name      = 'Nemotron 3 Super (Free)'
                    reasoning = $true
                    limit     = [ordered]@{ context = 262144; output = 32768 }
                    cost      = [ordered]@{ input = 0.08; output = 0.45 }
                }
                'nvidia/nemotron-3.5-lightning:free' = [ordered]@{
                    name  = 'Nemotron 3.5 Lightning (Free)'
                    limit = [ordered]@{ context = 1000000; output = 32768 }
                    cost  = [ordered]@{ input = 0.08; output = 0.2; cache_read = 0.04 }
                }
                'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free' = [ordered]@{
                    name      = 'Nemotron 3 Nano Omni (Free)'
                    reasoning = $true
                    limit     = [ordered]@{ context = 256000; output = 32768 }
                    cost      = [ordered]@{ input = 0; output = 0 }
                }
                'poolside/laguna-s-2.1:free' = [ordered]@{
                    name  = 'Laguna S 2.1 (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0.09; output = 0.18; cache_read = 0.009 }
                }
                'poolside/laguna-xs-2.1:free' = [ordered]@{
                    name  = 'Laguna XS 2.1 (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0.06; output = 0.12; cache_read = 0.03 }
                }
                'cohere/north-mini-code:free' = [ordered]@{
                    name  = 'North Mini Code (Free)'
                    limit = [ordered]@{ context = 256000; output = 32768 }
                    cost  = [ordered]@{ input = 0; output = 0 }
                }
                'nex-agi/nex-n2.5-pro:free' = [ordered]@{
                    name  = 'Nex-N2.5-Pro (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0; output = 0 }
                }
                'nex-agi/nex-n2.5-mini:free' = [ordered]@{
                    name  = 'Nex-N2.5-Mini (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0; output = 0 }
                }
                'thinkingmachines/inkling:free' = [ordered]@{
                    name  = 'Inkling (Free)'
                    limit = [ordered]@{ context = 1048576; output = 32768 }
                    cost  = [ordered]@{ input = 1.0; output = 4.05; cache_read = 0.17 }
                }
                'thinkingmachines/inkling-small:free' = [ordered]@{
                    name  = 'Inkling Small (Free)'
                    limit = [ordered]@{ context = 1048576; output = 32768 }
                    cost  = [ordered]@{ input = 0.45; output = 1.2; cache_read = 0.1 }
                }
                'dots-studio/dots-3-note-preview:free' = [ordered]@{
                    name      = 'Dots3-Note Preview (Free)'
                    reasoning = $true
                    limit     = [ordered]@{ context = 512000; output = 32768 }
                    cost      = [ordered]@{ input = 0; output = 0 }
                }
                'inclusionai/ling-3.0-flash-fin:free' = [ordered]@{
                    name  = 'Ling 3.0 Flash Fin (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0.06; output = 0.18; cache_read = 0.012 }
                }
                'inclusionai/ling-3.0-flash-sante:free' = [ordered]@{
                    name  = 'Ling 3.0 Flash Sante (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0; output = 0 }
                }
                'inclusionai/ling-3.0-flash-vl:free' = [ordered]@{
                    name  = 'Ling 3.0 Flash VL (Free)'
                    limit = [ordered]@{ context = 262144; output = 32768 }
                    cost  = [ordered]@{ input = 0.06; output = 0.18; cache_read = 0.012 }
                }
            }
        }
    }

    $existing['provider'] = $providers

    if (-not $existing.Contains('model') -or -not $existing['model']) {
        if ($museKey) {
            $existing['model'] = 'meta/muse-spark-1.3-contributor'
        } else {
            $existing['model'] = 'ollama/qwen2.5-coder:7b'
        }
    }


    $mcpServers = [ordered]@{}
    if ($existing.Contains('mcp') -and $existing['mcp']) {
        foreach ($p in $existing['mcp'].PSObject.Properties) { $mcpServers[$p.Name] = $p.Value }
    }

    $mcpServers['serena'] = [ordered]@{
        type    = 'local'
        command = @('uvx', '--from', 'serena-agent', 'serena', 'start-mcp-server', '--context', 'claude-code', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false')
        enabled = $true
    }
    $mcpServers['graphify'] = [ordered]@{
        type    = 'local'
        command = @('uvx', '--from', 'graphifyy[mcp]', 'python', '-m', 'graphify.serve', 'graphify-out/graph.json')
        enabled = $true
    }
    $mcpServers['playwright'] = [ordered]@{
        type    = 'local'
        command = @('npx', '-y', '@playwright/mcp@latest')
        enabled = $true
    }

    $existing['mcp'] = $mcpServers

    $json = $existing | ConvertTo-Json -Depth 10
    $json | Out-File -FilePath $configFile -Encoding utf8
    Write-AutoOSLine "OpenCode configuration written to $configFile" -Level ok

    if ($env:APPDATA) {
        $appDataDir = Join-Path $env:APPDATA 'opencode'
        if (-not (Test-Path $appDataDir)) { New-Item -ItemType Directory -Path $appDataDir -Force | Out-Null }
        $appDataFile = Join-Path $appDataDir 'config.json'
        $json | Out-File -FilePath $appDataFile -Encoding utf8
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
    if (-not (Test-Path $secretsPath)) {
        $secretsPath = Join-Path $HOME 'Documents\code\agent-skills\secrets\api_keys.conf'
    }
    $secrets = @{}
    if (Test-Path $secretsPath) {
        Get-Content $secretsPath -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith('#') -and $line -match '=') {
                $parts = $line.Split('=', 2)
                $secrets[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
    }

    $museKey = if ($env:MUSE_API_KEY) { $env:MUSE_API_KEY } elseif ($secrets.ContainsKey('muse')) { $secrets['muse'] } else { $null }
    $deepseekKey = if ($env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY } elseif ($secrets.ContainsKey('deepseek')) { $secrets['deepseek'] } else { $null }
    $openrouterKey = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } elseif ($secrets.ContainsKey('openrouter')) { $secrets['openrouter'] } else { $null }
    $context7Key = if ($env:CONTEXT7_API_KEY) { $env:CONTEXT7_API_KEY } elseif ($secrets.ContainsKey('context7')) { $secrets['context7'] } else { $null }

    $myDocs = [Environment]::GetFolderPath('MyDocuments')
    $skillsSource = Join-Path $myDocs 'Code\agent-skills\skills'
    if (-not (Test-Path $skillsSource)) {
        $skillsSource = Join-Path $myDocs 'code\agent-skills\skills'
    }
    $skillsTarget = Join-Path $openhandsDir 'skills'
    if ((Test-Path $skillsSource) -and -not (Test-Path $skillsTarget)) {
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
        $setupScript = @"
import os, sys, json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

openhands_dir = sys.argv[1]
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
if muse_key:
    llm['model'] = 'openai/muse-spark-1.3-contributor'
    llm['base_url'] = 'https://api.meta.ai/v1'
    llm['api_key'] = muse_key
elif deepseek_key:
    llm['model'] = 'deepseek/deepseek-chat'
    llm['base_url'] = 'https://api.deepseek.com'
    llm['api_key'] = deepseek_key
elif openrouter_key:
    llm['model'] = 'openrouter/openrouter/free'
    llm['base_url'] = 'https://openrouter.ai/api/v1'
    llm['api_key'] = openrouter_key
else:
    llm['model'] = 'ollama/qwen2.5-coder:7b'
    llm['base_url'] = 'http://127.0.0.1:11434/v1'

llm['max_input_tokens'] = 1048576
llm['max_output_tokens'] = 65536
llm['reasoning_effort'] = 'high'
llm['drop_params'] = True
llm['modify_params'] = True

agent_context = agent_settings.setdefault('agent_context', {})
agent_context['load_user_skills'] = True

mcp_cfg = agent_settings.setdefault('mcp_config', {})
mcp_cfg['serena'] = {
    'transport': 'stdio',
    'command': 'uvx',
    'args': ['--from', 'serena-agent', 'serena', 'start-mcp-server', '--project-from-cwd', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'],
    'description': 'Semantic code retrieval and symbol intelligence',
    'timeout': 120.0,
    'enabled': True
}
mcp_cfg['graphify'] = {
    'transport': 'stdio',
    'command': 'uv',
    'args': ['--quiet', 'run', '--with', 'graphifyy[mcp]', 'python', '-m', 'graphify.serve', 'graphify-out/graph.json'],
    'description': 'Codebase dependency knowledge graph',
    'timeout': 120.0,
    'enabled': True
}
mcp_cfg['omnigraph'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ['-y', '@modernrelay/omnigraph-mcp'],
    'description': 'Shared organizational graph and decision repository',
    'timeout': 120.0,
    'enabled': True
}
ctx7_args = ['-y', '@upstash/context7-mcp']
context7_key = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != 'null' else os.environ.get('CONTEXT7_API_KEY')
if context7_key:
    ctx7_args.extend(['--api-key', context7_key])
mcp_cfg['context7'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ctx7_args,
    'description': 'Upstash Context7 semantic search and retrieval',
    'timeout': 120.0,
    'enabled': True
}
mcp_cfg['playwright'] = {
    'transport': 'stdio',
    'command': 'npx',
    'args': ['-y', '@playwright/mcp'],
    'description': 'Browser automation and end-to-end verification',
    'timeout': 120.0,
    'enabled': True
}
mcp_cfg['cao-ops'] = {
    'transport': 'stdio',
    'command': 'wsl',
    'args': ['-d', 'Ubuntu', 'bash', '-c', 'export PATH=\"$HOME/.local/bin:$PATH\"; export CAO_HOME_DIR=\"$HOME/.cao\"; cao-ops-mcp-server'],
    'description': 'CLI Agent Orchestrator 3-level coordination bridge',
    'timeout': 120.0,
    'enabled': True
}
if 'github' in mcp_cfg:
    del mcp_cfg['github']

with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)

profiles_dir = os.path.join(openhands_dir, 'profiles')
# Prices are USD per token (OpenRouter, September 2026). Free variants bill
# $0 while under the daily cap; the paid-counterpart rates apply past it.
# cost_per_token = input + output lets a chat client estimate spend from
# prompt/completion token counts: spend = in_tokens*in_price + out_tokens*out_price.
profiles = {
    'deepseek-chat.json': {'model': 'deepseek/deepseek-chat', 'max_input_tokens': 131072, 'max_output_tokens': 8192, 'input_cost_per_token': 2.8e-07, 'output_cost_per_token': 4.2e-07, 'api_key': deepseek_key},
    'deepseek-reasoner.json': {'model': 'deepseek/deepseek-reasoner', 'max_input_tokens': 131072, 'max_output_tokens': 65536, 'reasoning_effort': 'high', 'input_cost_per_token': 2.8e-07, 'output_cost_per_token': 4.2e-07, 'api_key': deepseek_key},
    'muse-spark-1.3.json': {'model': 'openai/muse-spark-1.3-contributor', 'base_url': 'https://api.meta.ai/v1', 'max_input_tokens': 1048576, 'max_output_tokens': 131072, 'reasoning_effort': 'high', 'input_cost_per_token': 1e-07, 'output_cost_per_token': 2e-07, 'api_key': muse_key},
    'muse-spark-1.3-contributor.json': {'model': 'openai/muse-spark-1.3-contributor', 'base_url': 'https://api.meta.ai/v1', 'max_input_tokens': 1048576, 'max_output_tokens': 131072, 'reasoning_effort': 'high', 'input_cost_per_token': 1e-07, 'output_cost_per_token': 2e-07, 'api_key': muse_key},
    'openrouter-free.json': {'model': 'openrouter/openrouter/free', 'max_input_tokens': 200000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-nemotron-ultra.json': {'model': 'openrouter/nvidia/nemotron-3-ultra-550b-a55b:free', 'max_input_tokens': 1000000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 6e-07, 'paid_output_cost_per_token': 2.4e-06, 'api_key': openrouter_key},
    'openrouter-nemotron-super.json': {'model': 'openrouter/nvidia/nemotron-3-super-120b-a12b:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 8e-08, 'paid_output_cost_per_token': 4.5e-07, 'api_key': openrouter_key},
    'openrouter-nemotron-lightning.json': {'model': 'openrouter/nvidia/nemotron-3.5-lightning:free', 'max_input_tokens': 1000000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 8e-08, 'paid_output_cost_per_token': 2e-07, 'api_key': openrouter_key},
    'openrouter-nemotron-nano-omni.json': {'model': 'openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free', 'max_input_tokens': 256000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-laguna.json': {'model': 'openrouter/poolside/laguna-s-2.1:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 9e-08, 'paid_output_cost_per_token': 1.8e-07, 'api_key': openrouter_key},
    'openrouter-laguna-xs.json': {'model': 'openrouter/poolside/laguna-xs-2.1:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 6e-08, 'paid_output_cost_per_token': 1.2e-07, 'api_key': openrouter_key},
    'openrouter-north-mini-code.json': {'model': 'openrouter/cohere/north-mini-code:free', 'max_input_tokens': 256000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-nex-pro.json': {'model': 'openrouter/nex-agi/nex-n2.5-pro:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-nex-mini.json': {'model': 'openrouter/nex-agi/nex-n2.5-mini:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-inkling.json': {'model': 'openrouter/thinkingmachines/inkling:free', 'max_input_tokens': 1048576, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 1e-06, 'paid_output_cost_per_token': 4.05e-06, 'api_key': openrouter_key},
    'openrouter-inkling-small.json': {'model': 'openrouter/thinkingmachines/inkling-small:free', 'max_input_tokens': 1048576, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 4.5e-07, 'paid_output_cost_per_token': 1.2e-06, 'api_key': openrouter_key},
    'openrouter-dots3-note.json': {'model': 'openrouter/dots-studio/dots-3-note-preview:free', 'max_input_tokens': 512000, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-ling-fin.json': {'model': 'openrouter/inclusionai/ling-3.0-flash-fin:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 6e-08, 'paid_output_cost_per_token': 1.8e-07, 'api_key': openrouter_key},
    'openrouter-ling-sante.json': {'model': 'openrouter/inclusionai/ling-3.0-flash-sante:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'api_key': openrouter_key},
    'openrouter-ling-vl.json': {'model': 'openrouter/inclusionai/ling-3.0-flash-vl:free', 'max_input_tokens': 262144, 'max_output_tokens': 32768, 'input_cost_per_token': 0, 'output_cost_per_token': 0, 'paid_input_cost_per_token': 6e-08, 'paid_output_cost_per_token': 1.8e-07, 'api_key': openrouter_key},
    'ollama-qwen2.5-coder.json': {'model': 'ollama/qwen2.5-coder:7b', 'base_url': 'http://127.0.0.1:11434/v1', 'max_input_tokens': 32768, 'max_output_tokens': 8192, 'input_cost_per_token': 0, 'output_cost_per_token': 0}
}
for name, p_data in profiles.items():
    with open(os.path.join(profiles_dir, name), 'w', encoding='utf-8') as f:
        json.dump(p_data, f, indent=2)

agent_profiles_dir = os.path.join(openhands_dir, 'agent-profiles')
acp_agents = {
    'claude-sonnet.json': {'name': 'Claude Sonnet ACP', 'model': 'anthropic/claude-3-7-sonnet-latest', 'description': 'Claude Sonnet coding agent'},
    'claude-opus.json': {'name': 'Claude Opus ACP', 'model': 'anthropic/claude-3-opus-latest', 'description': 'Claude Opus high-reasoning agent'},
    'claude-haiku.json': {'name': 'Claude Haiku ACP', 'model': 'anthropic/claude-3-5-haiku-latest', 'description': 'Claude Haiku fast execution agent'},
    'agy-gemini-3.8-flash.json': {'name': 'Gemini 3.8 Flash ACP', 'model': 'gemini/gemini-2.5-flash', 'description': 'Fast Google Antigravity Gemini agent'},
    'agy-gemini-pro.json': {'name': 'Gemini Pro ACP', 'model': 'gemini/gemini-2.5-pro', 'description': 'Deep reasoning Antigravity Gemini agent'}
}
for name, a_data in acp_agents.items():
    with open(os.path.join(agent_profiles_dir, name), 'w', encoding='utf-8') as f:
        json.dump(a_data, f, indent=2)
"@
        $argMuse = if ($museKey) { $museKey } else { 'null' }
        $argDeepseek = if ($deepseekKey) { $deepseekKey } else { 'null' }
        $argOpenrouter = if ($openrouterKey) { $openrouterKey } else { 'null' }
        $argContext7 = if ($context7Key) { $context7Key } else { 'null' }

        & $pythonCmd.Source -c $setupScript $openhandsDir $argMuse $argDeepseek $argOpenrouter $argContext7
    }

    Write-AutoOSLine "OpenHands configuration and profiles written to $openhandsDir" -Level ok
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
    Test-AutoOSInstalled, Get-AutoOSInstalledComponents, Install-AutoOSComponent, Invoke-AutoOSPostInstall,
    Add-AutoOSGitToPath, Set-AutoOSGitConfig, Add-AutoOSAgyToPath, Add-AutoOSCondaToPath, New-AutoOSCondaEnv, Install-AutoOSNerdFont,
    Install-AutoOSHerdr, Install-AutoOSAgy, Install-AutoOSClaudeAutostart,
    Write-AutoOSClaudeHostReadiness, Install-AutoOSPoshTheme, Add-AutoOSProfileLine,
    Install-AutoOSWindhawkMods, Install-AutoOSAgentSkills, Set-AutoOSAntigravityMcp,
    Register-AutoOSAntigravityMcpServer, Install-AutoOSMcpSerena, Install-AutoOSMcpGraphify,
    Install-AutoOSMcpPlaywright, Install-AutoOSMcpContext7,
    Set-AutoOSOpenCodeConfig, Set-AutoOSOpenHandsConfig,
    Invoke-AutoOSScriptProvider

