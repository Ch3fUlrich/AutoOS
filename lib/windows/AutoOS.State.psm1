#Requires -Version 5.1
<#
.SYNOPSIS
    Run state (save / replay), post-install verification, and undo.

.DESCRIPTION
    Three things that turn a one-shot installer into something you can rely on
    twice: a record of what you chose, proof that what was installed actually
    runs, and a way back for the files AutoOS touched.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

$script:Verify = $true

function Set-AutoOSVerify { param([bool]$Enabled) $script:Verify = $Enabled }

# ─── Post-install verification ──────────────────────────────────────────────
function Test-AutoOSComponentWorks {
    <#
      .SYNOPSIS
        Run a component's `verify` command and say whether it worked.
      .DESCRIPTION
        A package manager reporting success is not proof the thing works: a
        binary can land outside PATH, or a shim can be written without its
        runtime. Returns 'verified' | 'unverified' | 'unchecked'.
    #>
    param(
        [string]$VerifyCommand,
        [string]$Name = 'component',
        [bool]$DryRun = $false
    )
    if ([string]::IsNullOrWhiteSpace($VerifyCommand)) { return 'unchecked' }
    if (-not $script:Verify) { return 'unchecked' }
    if ($DryRun) {
        Write-AutoOSLine "would verify: $VerifyCommand" -Level muted
        return 'unchecked'
    }

    # A fresh install usually lands in a directory this process's PATH predates,
    # so re-read the persisted PATH before probing.
    $probe = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    ) -join ';'
    $savedPath = $env:Path
    try {
        $env:Path = $probe
        $parts = $VerifyCommand -split '\s+', 2
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) {
            Write-AutoOSLine "$Name installed but '$($parts[0])' is not on PATH yet - open a new terminal." -Level warn
            return 'unverified'
        }
        $null = & $parts[0] @($parts[1] -split '\s+' | Where-Object { $_ }) 2>&1
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            Write-AutoOSLine "verified: $Name" -Level ok
            return 'verified'
        }
        Write-AutoOSLine "$Name installed but '$VerifyCommand' exited $LASTEXITCODE." -Level warn
        return 'unverified'
    } catch {
        Write-AutoOSLine "$Name installed but '$VerifyCommand' did not run: $($_.Exception.Message)" -Level warn
        return 'unverified'
    } finally {
        $env:Path = $savedPath
    }
}

# ─── Run state ──────────────────────────────────────────────────────────────
function Save-AutoOSState {
    <#
      .SYNOPSIS
        Record what this run chose, so another machine can replay it.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$ProfileName = 'custom',
        [string[]]$Selected = @(),
        [hashtable]$Answers = @{},
        [hashtable]$Results = @{},
        [bool]$DryRun = $false
    )
    if ($DryRun) {
        Write-AutoOSLine "would save run state to $Path" -Level muted
        return
    }
    $state = [ordered]@{
        version  = 1
        savedAt  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        platform = 'windows'
        profile  = $ProfileName
        selected = @($Selected)
        answers  = $Answers
        results  = [ordered]@{
            installed = @($Results['installed']); skipped = @($Results['skipped'])
            failed    = @($Results['failed'])
        }
    }
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $state | ConvertTo-Json -Depth 8 | Out-File -FilePath $Path -Encoding utf8
    Write-AutoOSLine "run state saved to $Path" -Level ok
}

function Import-AutoOSState {
    <#
      .SYNOPSIS Read a saved run state. Returns @{ Profile; Selected; Answers }.
    #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { throw "No state file at $Path" }
    $raw = Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json

    $answers = @{}
    if ($raw.PSObject.Properties.Name -contains 'answers' -and $raw.answers) {
        foreach ($p in $raw.answers.PSObject.Properties) { $answers[$p.Name] = $p.Value }
    }
    $prof = if ($raw.PSObject.Properties.Name -contains 'profile') { $raw.profile } else { 'custom' }
    $sel  = if ($raw.PSObject.Properties.Name -contains 'selected') { @($raw.selected) } else { @() }

    Write-AutoOSLine "loaded state from $Path (profile: $prof)" -Level ok
    @{ Profile = $prof; Selected = $sel; Answers = $answers }
}

# ─── Undo ───────────────────────────────────────────────────────────────────
function Get-AutoOSBackups {
    <#
      .SYNOPSIS Newest backup per original file that AutoOS has written.
    #>
    param([string]$SearchRoot = $env:USERPROFILE)

    $found = @(Get-ChildItem -Path $SearchRoot -Recurse -Depth 4 -File `
                             -Filter '*.autoos-backup-*' -ErrorAction SilentlyContinue)
    $groups = $found | Group-Object { ($_.FullName -replace '\.autoos-backup-.*$', '') }
    foreach ($g in $groups) {
        [pscustomobject]@{
            Original = $g.Name
            Backup   = ($g.Group | Sort-Object Name | Select-Object -Last 1).FullName
            Count    = $g.Count
        }
    }
}

function Invoke-AutoOSUndo {
    <#
      .SYNOPSIS
        Restore files AutoOS backed up, and offer to restore the saved PATH.
      .DESCRIPTION
        Deliberately does NOT uninstall packages. Guessing which of a package
        manager's changes were "ours" is how an undo becomes a second incident;
        removing software is left to winget/choco, which own that record.
    #>
    param([bool]$DryRun = $false, [bool]$AssumeYes = $false)

    $backups = @(Get-AutoOSBackups)
    $pathBackups = @(Get-ChildItem -Path $env:USERPROFILE -File `
                     -Filter '.autoos-path-backup-*.txt' -ErrorAction SilentlyContinue |
                     Sort-Object Name)

    if ($backups.Count -eq 0 -and $pathBackups.Count -eq 0) {
        Write-AutoOSLine 'Nothing to undo - AutoOS has not backed up anything on this machine.' -Level info
        return
    }

    Write-AutoOSSection 'What can be restored'
    foreach ($b in $backups) {
        Write-AutoOSLine ("  {0,-52} <- {1}" -f $b.Original, (Split-Path -Leaf $b.Backup))
    }
    if ($pathBackups.Count) {
        $newest = $pathBackups[-1]
        Write-AutoOSLine ("  {0,-52} <- {1}" -f 'User PATH', $newest.Name)
    }
    Write-AutoOSLine 'Installed packages are NOT removed - only these are restored.' -Level muted

    if ($DryRun) { Write-AutoOSLine 'DRY RUN - nothing restored.' -Level warn; return }
    if (-not $AssumeYes) {
        if (-not (Read-AutoOSConfirm -Question 'Restore the items above?' -Default $false)) {
            Write-AutoOSLine 'Cancelled - nothing was changed.' -Level warn
            return
        }
    }

    foreach ($b in $backups) {
        Copy-Item -Path $b.Backup -Destination $b.Original -Force
        Write-AutoOSLine "restored $($b.Original)" -Level ok
    }
    if ($pathBackups.Count) {
        $newest = $pathBackups[-1]
        $saved = (Get-Content -Path $newest.FullName -Raw).Trim()
        if ($saved) {
            [Environment]::SetEnvironmentVariable('Path', $saved, 'User')
            Write-AutoOSLine "restored the user PATH from $($newest.Name)" -Level ok
        }
    }
}

Export-ModuleMember -Function `
    Set-AutoOSVerify, Test-AutoOSComponentWorks, Save-AutoOSState, Import-AutoOSState,
    Get-AutoOSBackups, Invoke-AutoOSUndo
