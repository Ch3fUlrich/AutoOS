#Requires -Version 5.1
<#
.SYNOPSIS
    AutoOS - post-install provisioning for Windows.

.DESCRIPTION
    One entry point. Detects the machine, suggests a profile, lets you tick
    exactly what you want, shows the plan, then installs it.

    Pipeline: detect -> profile -> select -> plan -> confirm -> execute -> report
    Nothing is installed before the confirmation step.

.PARAMETER InstallProfile
    workstation | ai-coding | light | custom. Skips the profile question.

.PARAMETER Only
    Install exactly these component ids and nothing else. Implies -Yes.

.PARAMETER DryRun
    Print every command that would run without changing anything.

.PARAMETER Yes
    Non-interactive: accept the profile defaults and skip confirmation.

.PARAMETER NoColor
    Disable ANSI colour.

.PARAMETER Serve
    Start the browser UI instead of the terminal menu. For headless machines.

.PARAMETER Port
    Port for -Serve. Default 8777.

.PARAMETER Bind
    Bind address for -Serve. Default 127.0.0.1. Anything wider needs elevation.

.PARAMETER ListComponents
    Print the catalog and exit.

.PARAMETER CheckCatalog
    Validate the catalog schema and exit non-zero on any problem.

.PARAMETER FromState
    Replay a previous run's selection and answers from a saved state file.

.PARAMETER SaveState
    Where to write this run's state. Defaults to .autoos-state.json in the repo.

.PARAMETER NoVerify
    Skip the post-install check that each component actually runs.

.PARAMETER Undo
    Restore files AutoOS backed up. Does NOT uninstall packages.

.EXAMPLE
    .\setup.ps1
    Interactive: detect, choose a profile, tick components, install.

.EXAMPLE
    .\setup.ps1 -Profile ai-coding -DryRun
    Show exactly what the ai-coding profile would do.

.EXAMPLE
    .\setup.ps1 -Only claude-code,tailscale -Yes
    Install just those two plus their dependencies.

.EXAMPLE
    .\setup.ps1 -Serve
    Drive the install from a browser over Tailscale.
#>
[CmdletBinding()]
param(
    # Named InstallProfile because $Profile is a PowerShell automatic variable
    # ($PROFILE); the alias keeps -Profile working on the command line.
    [Alias('Profile')]
    [string]$InstallProfile,
    [string[]]$Only,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$NoColor,
    [switch]$Serve,
    [int]$Port = 8777,
    [string]$Bind = '127.0.0.1',
    [switch]$ListComponents,
    [switch]$CheckCatalog,
    [string]$FromState,
    [string]$SaveState,
    [string]$Config,
    [switch]$NoVerify,
    [switch]$Undo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

# `powershell -File setup.ps1 -Only a,b` hands "a,b" over as one string - -File
# passes every argument literally and never splits on commas the way the normal
# parser does. The browser UI shells out exactly that way, so without this a
# multi-component install fails as "unknown component id(s): a,b".
$Only = @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } |
          Where-Object { $_ })

$RepoRoot = $PSScriptRoot
$LibDir   = Join-Path $RepoRoot 'lib\windows'

if (-not $Config) { $Config = Join-Path $RepoRoot 'autoos.config.json' }

Import-Module (Join-Path $LibDir 'AutoOS.Ui.psm1')      -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AutoOS.Detect.psm1')  -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AutoOS.Catalog.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AutoOS.Install.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AutoOS.State.psm1')   -Force -DisableNameChecking

if ($NoColor) { Set-AutoOSColor $false }
if ($NoVerify) { Set-AutoOSVerify $false }
if (-not $SaveState) { $SaveState = Join-Path $RepoRoot '.autoos-state.json' }

$CatalogPath = Join-Path $RepoRoot 'catalog\windows.json'
$catalog = Get-AutoOSCatalog -Path $CatalogPath
$profileNames = @($catalog.profiles.PSObject.Properties.Name)
if ($InstallProfile -and $InstallProfile -notin $profileNames) { throw "Unknown profile '$InstallProfile'. Choose: $($profileNames -join ', ')" }

# ─── Catalog-only modes ─────────────────────────────────────────────────────
if ($CheckCatalog) {
    $problems = @(Test-AutoOSCatalogSchema -Catalog $catalog)
    if ($problems.Count -eq 0) {
        Write-AutoOSLine 'Catalog is valid.' -Level ok
        exit 0
    }
    foreach ($p in $problems) { Write-AutoOSLine $p -Level error }
    exit 1
}

if ($ListComponents) {
    $allComponents = @($catalog.categories | ForEach-Object { $_.components })
    $installedMap = @{}
    foreach ($inst in (Get-AutoOSInstalledComponents -Components $allComponents)) {
        $installedMap[$inst.Id] = $true
    }
    foreach ($cat in $catalog.categories) {
        Write-AutoOSSection $cat.name
        foreach ($c in $cat.components) {
            $profs = if ($c.PSObject.Properties.Name -contains 'profiles') { $c.profiles -join ',' } else { '' }
            $instMark = if ($installedMap.ContainsKey($c.id)) { (Format-AutoOSColor '✓' 'ok') + ' ' } else { '  ' }
            Write-AutoOSLine ("  {0}{1,-18} {2,-10} {3}" -f $instMark, $c.id, $c.provider, $c.description)
            if ($profs) { Write-AutoOSLine ("    {0,-18} profiles: {1}" -f '', $profs) -Level muted }
        }
    }
    exit 0
}

# ─── 1. Detect ──────────────────────────────────────────────────────────────
Write-AutoOSBanner 'Windows'
Initialize-AutoOSLog -Path (Join-Path $RepoRoot "logs\autoos-$(Get-Date -Format 'yyyyMMdd-HHmmss').log")

Write-AutoOSSection 'Detected system'
$sys = Get-AutoOSSystemInfo
Write-AutoOSKeyValue 'Operating system' "$($sys.OsName) (build $($sys.OsBuild))"
Write-AutoOSKeyValue 'Architecture'     $sys.Arch
Write-AutoOSKeyValue 'Machine'          "$($sys.Manufacturer) $($sys.Model)"
Write-AutoOSKeyValue 'CPU'              "$($sys.CpuName) - $($sys.CpuCores) threads"
Write-AutoOSKeyValue 'Memory'           "$($sys.RamGB) GB"
Write-AutoOSKeyValue 'GPU'              $sys.Gpu
Write-AutoOSKeyValue 'Free disk'        "$($sys.FreeDiskGB) GB"
Write-AutoOSKeyValue 'Microphone'       $sys.Microphone
Write-AutoOSKeyValue 'Elevated'         $(if ($sys.IsAdmin) { 'yes' } else { 'no' }) $(if ($sys.IsAdmin) { 'ok' } else { 'warn' })

$managers = @()
if ($sys.HasWinget) { $managers += 'winget' }
if ($sys.HasChoco)  { $managers += 'choco' }
if ($sys.HasScoop)  { $managers += 'scoop' }
Write-AutoOSKeyValue 'Package managers' $(if ($managers) { $managers -join ', ' } else { 'none' })

$present = @()
foreach ($t in @('Git', 'Node', 'Docker', 'Wsl')) {
    if ($sys."Has$t") { $present += $t.ToLower() }
}
Write-AutoOSKeyValue 'Already present' $(if ($present) { $present -join ', ' } else { 'nothing relevant' })

$availableForDetect = @(Get-AutoOSAvailableComponents -Catalog $catalog -SystemInfo $sys)
$installedApps = @(Get-AutoOSInstalledComponents -Components $availableForDetect | ForEach-Object { $_.Name })
if ($installedApps.Count -gt 0) {
    $check = Format-AutoOSColor '✓' 'ok'
    Write-AutoOSKeyValue 'Installed apps' ("$check $($installedApps -join ', ') ($($installedApps.Count) detected)")
} else {
    Write-AutoOSKeyValue 'Installed apps' 'none detected'
}

$blockers = @(Get-AutoOSBlockers -SystemInfo $sys)
if ($blockers.Count) {
    Write-AutoOSSection 'Warnings'
    foreach ($b in $blockers) {
        Write-AutoOSLine $b.Message -Level $(if ($b.Severity -eq 'error') { 'error' } else { 'warn' })
        Write-AutoOSLine "    $($b.Fix)" -Level muted
    }
    if (@($blockers | Where-Object { $_.Severity -eq 'error' }).Count -and -not $DryRun) {
        Write-AutoOSLine 'Cannot continue until the errors above are resolved.' -Level error
        exit 1
    }
}

# ─── Undo ───────────────────────────────────────────────────────────────────
if ($Undo) {
    Invoke-AutoOSUndo -DryRun:$DryRun.IsPresent -AssumeYes:$Yes.IsPresent
    exit 0
}

# ─── Browser mode ───────────────────────────────────────────────────────────
if ($Serve) {
    Import-Module (Join-Path $LibDir 'AutoOS.Serve.psm1') -Force -DisableNameChecking
    Start-AutoOSServer -RepoRoot $RepoRoot -Port $Port -Bind $Bind -SystemInfo $sys -Catalog $catalog -DryRun:$DryRun
    exit 0
}

# ─── 2. Profile ─────────────────────────────────────────────────────────────
$cfgAnswers = @{}
if (Test-Path $Config) {
    try {
        $raw = Get-Content $Config -Raw -Encoding UTF8
        $cfgObj = $raw | ConvertFrom-Json
        if (-not $InstallProfile -and $cfgObj.PSObject.Properties.Name -contains 'profile' -and $cfgObj.profile) {
            $InstallProfile = $cfgObj.profile
        }
        if ($cfgObj.PSObject.Properties.Name -contains 'answers' -and $cfgObj.answers) {
            foreach ($p in $cfgObj.answers.PSObject.Properties) {
                $cfgAnswers[$p.Name] = [string]$p.Value
            }
        }
    } catch { throw "Unable to read configuration: $($_.Exception.Message)" }
}

$available = @(Get-AutoOSAvailableComponents -Catalog $catalog -SystemInfo $sys)
Set-AutoOSInstalledStatus -Components $available
$suggested = Get-AutoOSSuggestedProfile -SystemInfo $sys

$statePayload = $null
if ($FromState) {
    $statePayload = Import-AutoOSState -Path $FromState
    $InstallProfile = $statePayload.Profile
} elseif ($Only) {
    $InstallProfile = 'custom'
} elseif (-not $InstallProfile) {
    Write-AutoOSSection 'Profile'
    if ($Yes) {
        $InstallProfile = $suggested
    } else {
        $profileItems = @()
        foreach ($p in $catalog.profiles.PSObject.Properties) {
            $badge = if ($p.Name -eq $suggested) { 'suggested' } else { '' }
            $profileItems += [pscustomobject]@{
                Id          = $p.Name
                Name        = $p.Name
                Description = [string]$p.Value
                Badge       = $badge
            }
        }
        $InstallProfile = Show-AutoOSRadioMenu -Items $profileItems -Title 'Choose installation profile' -DefaultId $suggested
    }
}
if ($InstallProfile -notin $profileNames) { throw "Unknown profile: $InstallProfile" }
Write-AutoOSLine "Using profile: $InstallProfile" -Level ok

# ─── 3. Select ──────────────────────────────────────────────────────────────
if ($statePayload) {
    # Keep only what this machine actually offers; a Pi replaying a workstation
    # state should quietly drop what does not apply rather than fail.
    $stateIds = @($statePayload.Selected)
    $dropped  = @($stateIds | Where-Object { $_ -notin $available.Id })
    if ($dropped.Count) {
        Write-AutoOSLine "not available on this machine, skipping: $($dropped -join ', ')" -Level warn
    }
    $selectedIds = @($stateIds | Where-Object { $_ -in $available.Id })
} elseif ($Only) {
    $unknown = @($Only | Where-Object { $_ -notin $available.Id })
    if ($unknown.Count) {
        Write-AutoOSLine "Unknown component id(s): $($unknown -join ', ')" -Level error
        Write-AutoOSLine 'Run with -ListComponents to see valid ids.' -Level muted
        exit 1
    }
    $selectedIds = @($Only)
} elseif ($Yes) {
    $selectedIds = @($available | Where-Object { $_.Provider -ne 'manual' -and $InstallProfile -ne 'custom' -and $InstallProfile -in $_.Profiles } | ForEach-Object { $_.Id })
} else {
    $installedMap = @{}
    foreach ($inst in (Get-AutoOSInstalledComponents -Components $available)) {
        $installedMap[$inst.Id] = $true
    }
    $items = @($available | ForEach-Object {
        New-AutoOSMenuItem -Component $_ -Profile $InstallProfile -Installed ([bool]$installedMap.ContainsKey($_.Id))
    })
    $menuResult = Show-AutoOSMenu -Items $items -Title 'Choose what to install' `
                   -Footer 'Dependencies are added automatically.'
    if ($null -eq $menuResult) {
        Write-AutoOSLine 'Cancelled - nothing was changed.' -Level warn
        exit 0
    }
    $selectedIds = @($menuResult)
}

if (-not $selectedIds -or @($selectedIds).Count -eq 0) {
    Write-AutoOSLine 'Nothing selected - nothing to do.' -Level warn
    exit 0
}

# ─── 4. Plan ────────────────────────────────────────────────────────────────
$plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds $selectedIds)

Write-AutoOSSection 'Plan'
$i = 0
foreach ($c in $plan) {
    $i++
    $tag = if ($c.AutoAdded) { Format-AutoOSColor '(dependency)' 'muted' } else { '' }
    Write-AutoOSLine ("  {0,2}. {1,-20} {2,-8} {3} {4}" -f $i, $c.Name, $c.Provider, $c.Package, $tag)
    if ($c.Installed) { Write-AutoOSLine (([char]0x2713) + ' Already installed - package will be skipped') -Level ok }
    if ($c.Notes) { Write-AutoOSLine "      $($c.Notes)" -Level muted }
}
Write-AutoOSLine ''
Write-AutoOSLine "$($plan.Count) component(s); $(@($plan | Where-Object { $_.AutoAdded }).Count) pulled in as dependencies." -Level info

# ─── 5. Questions (all of them, before anything is touched) ─────────────────
$answers = @{}
if ($statePayload) { $answers = $statePayload.Answers }
foreach ($k in $cfgAnswers.Keys) {
    if (-not $answers.ContainsKey($k)) { $answers[$k] = $cfgAnswers[$k] }
}
$needed = @($plan | Where-Object { $_.Prompt } | ForEach-Object {
    $_.Prompt -split '[, ]+' | Where-Object { $_ }
} | Select-Object -Unique)

# An AUTOOS_ANSWER_<KEY> environment variable pre-answers a prompt; this is how
# -Serve passes the browser's answers through to the same code path.
$fromEnv = @{}
foreach ($key in $needed) {
    $envName = 'AUTOOS_ANSWER_' + ($key.ToUpper() -replace '-', '_')
    $val = [Environment]::GetEnvironmentVariable($envName)
    if ($null -ne $val -and $val -ne '') { $fromEnv[$key] = $val; $answers[$key] = $val }
}
$needed = @($needed | Where-Object { -not $fromEnv.ContainsKey($_) -and -not $answers.ContainsKey($_) })

$configUpdated = $false
if ($needed.Count -and -not $Yes) {
    Write-AutoOSSection 'A few questions'
    foreach ($key in $needed) {
        $spec = $catalog.prompts.$key
        $help = if ($spec.PSObject.Properties.Name -contains 'help') { $spec.help } else { '' }
        $def  = if ($spec.PSObject.Properties.Name -contains 'default') { $spec.default } else { '' }
        $answers[$key] = Read-AutoOSValue -Question $spec.question -Default $def -Help $help
        $configUpdated = $true
    }
} elseif ($needed.Count) {
    foreach ($key in $needed) {
        $spec = $catalog.prompts.$key
        $answers[$key] = if ($spec.PSObject.Properties.Name -contains 'default') { $spec.default } else { '' }
    }
}

# ─── 6. Confirm ─────────────────────────────────────────────────────────────
if ($DryRun) {
    Write-AutoOSLine 'DRY RUN - no changes will be made.' -Level warn
} elseif (-not $Yes) {
    Write-AutoOSLine ''
    if (-not (Read-AutoOSConfirm -Question "Install these $($plan.Count) component(s)?" -Default $true)) {
        Write-AutoOSLine 'Cancelled - nothing was changed.' -Level warn
        exit 0
    }
}

if ($configUpdated -and -not $DryRun.IsPresent -and (Test-Path -LiteralPath $Config)) {
    # Preserve unrelated settings and back up the original only after consent.
    $cfgObj | Add-Member -NotePropertyName profile -NotePropertyValue $InstallProfile -Force
    $cfgObj | Add-Member -NotePropertyName answers -NotePropertyValue $answers -Force
    Copy-Item -LiteralPath $Config -Destination "$Config.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')"
    [IO.File]::WriteAllText($Config, ($cfgObj | ConvertTo-Json -Depth 100), [Text.Encoding]::UTF8)
    Write-AutoOSLine "Saved answers to $Config" -Level ok
}

# ─── 7. Execute ─────────────────────────────────────────────────────────────
Initialize-AutoOSInstaller -DryRun:$DryRun.IsPresent -Answers $answers -RepoRoot $RepoRoot

Write-AutoOSSection 'Installing'
$results = [ordered]@{ installed = @(); skipped = @(); failed = @(); manual = @() }
Import-Module (Join-Path $LibDir 'AutoOS.Progress.psm1') -DisableNameChecking
$unverified = 0
$n = 0
foreach ($c in $plan) {
    $n++
    Write-AutoOSLine "[$n/$($plan.Count)] $($c.Name)" -Level step
    Start-AutoOSComponentProgress -Id $c.Id -Name $c.Name -Done ($n - 1) -Total $plan.Count
    $state = 'failed'
    $abortRun = $false
    try {
        $blocked = @($c.Requires | Where-Object { $_ -in $results.failed -or $_ -in $results.manual })
        if ($blocked.Count) { throw "Required components did not complete: $($blocked -join ', ')" }
        $state = Install-AutoOSComponent -Component $c
        if ($state -in @('installed', 'skipped')) { Write-AutoOSInstallProgress -Phase 'configuring'; Invoke-AutoOSPostInstall -Component $c | Out-Null }
        if ($state -eq 'installed') {
            Write-AutoOSInstallProgress -Phase 'verifying'
            $v = Test-AutoOSComponentWorks -VerifyCommand $c.Verify -Name $c.Name -DryRun:$DryRun.IsPresent
            if ($v -eq 'unverified') { $unverified++ }
            Write-AutoOSLine "$($c.Name) done" -Level ok
        }
        $results[$state] += $c.Id
    } catch {
        Write-AutoOSLine "$($c.Name): $($_.Exception.Message)" -Level error
        $state = 'failed'
        $results.failed += $c.Id
        $abortRun = $_.Exception -is [TimeoutException]
    }
    Write-AutoOSInstallProgress -Phase $state -Complete
    if ($abortRun) { break }
}

# ─── 8. Report ──────────────────────────────────────────────────────────────
Write-AutoOSSection 'Summary'
Write-AutoOSKeyValue $(if ($DryRun) { 'Planned' } else { 'Installed' }) "$($results.installed.Count)" 'ok'
Write-AutoOSKeyValue 'Action required' "$($results.manual.Count)" 'warn'
Write-AutoOSKeyValue 'Already present' "$($results.skipped.Count)" 'muted'
if ($unverified -gt 0) { Write-AutoOSKeyValue 'Installed but unverified' "$unverified" 'warn' }
Write-AutoOSKeyValue 'Failed' "$($results.failed.Count)" $(if ($results.failed.Count) { 'err' } else { 'muted' })
if ($results.failed.Count) {
    Write-AutoOSLine ''
    foreach ($f in $results.failed) { Write-AutoOSLine $f -Level error }
    Write-AutoOSLine 'Re-run to retry only the failures; everything else reports as already present.' -Level muted
}
# ─── Where it landed ────────────────────────────────────────────────────────
# "Installed 1" answers nothing on its own: most of this catalog is desktop
# software with no command on PATH, and the reasonable next question is where it
# went and how to open it. Resolved from the live machine, so a blank line means
# genuinely not found rather than a guess that reads like a fact.
$landed = @($plan | Where-Object { $results.installed -contains $_.Id -or $results.skipped -contains $_.Id })
if ($landed.Count) {
    Write-AutoOSSection 'Where to find them'
    if ($DryRun) {
        Write-AutoOSLine 'Dry run installed nothing - these are the locations as they stand now.' -Level muted
    }
    foreach ($c in $landed) {
        $hint = Get-AutoOSLaunchHint -Component $c
        if ($hint.How) {
            Write-AutoOSKeyValue $c.Name $hint.How
            if ($hint.Path) { Write-AutoOSLine ("  " + (" " * 22) + " " + $hint.Path) -Level muted }
        } else {
            Write-AutoOSKeyValue $c.Name 'no launcher found yet' 'warn'
            Write-AutoOSLine ("  " + (" " * 22) + ' open a new terminal, or sign out and back in, then re-run') -Level muted
        }
    }
}

# Saved last, so a replay reflects what actually happened rather than what was planned.
Save-AutoOSState -Path $SaveState -ProfileName $InstallProfile -Selected $selectedIds `
                 -Answers $answers -Results $results -DryRun:$DryRun.IsPresent

Write-AutoOSLine ''
Write-AutoOSLine 'Some changes (PATH, fonts, Docker) need a new terminal or a reboot.' -Level info
Write-AutoOSLine "Repeat this setup elsewhere with:  .\setup.ps1 -FromState $SaveState" -Level muted
exit $(if ($results.failed.Count) { 1 } else { 0 })
