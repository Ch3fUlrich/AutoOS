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
    [ValidateSet('workstation', 'ai-coding', 'light', 'custom')]
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
    [switch]$NoVerify,
    [switch]$Undo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# `powershell -File setup.ps1 -Only a,b` hands "a,b" over as one string - -File
# passes every argument literally and never splits on commas the way the normal
# parser does. The browser UI shells out exactly that way, so without this a
# multi-component install fails as "unknown component id(s): a,b".
$Only = @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } |
          Where-Object { $_ })

$RepoRoot = $PSScriptRoot
$LibDir   = Join-Path $RepoRoot 'lib\windows'

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
    foreach ($cat in $catalog.categories) {
        Write-AutoOSSection $cat.name
        foreach ($c in $cat.components) {
            $profs = if ($c.PSObject.Properties.Name -contains 'profiles') { $c.profiles -join ',' } else { '' }
            Write-AutoOSLine ("  {0,-18} {1,-10} {2}" -f $c.id, $c.provider, $c.description)
            if ($profs) { Write-AutoOSLine ("  {0,-18} profiles: {1}" -f '', $profs) -Level muted }
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
$available = @(Get-AutoOSAvailableComponents -Catalog $catalog -SystemInfo $sys)
$suggested = Get-AutoOSSuggestedProfile -SystemInfo $sys

$statePayload = $null
if ($FromState) {
    $statePayload = Import-AutoOSState -Path $FromState
    $InstallProfile = $statePayload.Profile
} elseif ($Only) {
    $InstallProfile = 'custom'
} elseif (-not $InstallProfile) {
    Write-AutoOSSection 'Profile'
    Write-AutoOSLine "Suggested for this machine: " -NoNewline
    Write-AutoOSLine (Format-AutoOSColor $suggested 'accent')
    Write-AutoOSLine ''
    foreach ($p in $catalog.profiles.PSObject.Properties) {
        $mark = if ($p.Name -eq $suggested) { '*' } else { ' ' }
        Write-AutoOSLine ("  $mark {0,-13} {1}" -f $p.Name, $p.Value)
    }
    Write-AutoOSLine ''
    if ($Yes) {
        $InstallProfile = $suggested
    } else {
        $InstallProfile = Read-AutoOSValue -Question 'Profile' -Default $suggested -Validator {
            param($v)
            if ($v -in @('workstation', 'ai-coding', 'light', 'custom')) { $true }
            else { "Choose one of: workstation, ai-coding, light, custom" }
        }
    }
}
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
    $selectedIds = @($available | Where-Object { $InstallProfile -ne 'custom' -and $InstallProfile -in $_.Profiles } | ForEach-Object { $_.Id })
} else {
    $items = @($available | ForEach-Object { New-AutoOSMenuItem -Component $_ -Profile $InstallProfile })
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
    if ($c.Notes) { Write-AutoOSLine "      $($c.Notes)" -Level muted }
}
Write-AutoOSLine ''
Write-AutoOSLine "$($plan.Count) component(s); $(@($plan | Where-Object { $_.AutoAdded }).Count) pulled in as dependencies." -Level info

# ─── 5. Questions (all of them, before anything is touched) ─────────────────
$answers = @{}
if ($statePayload) { $answers = $statePayload.Answers }
$needed = @($plan | Where-Object { $_.Prompt } | ForEach-Object { $_.Prompt } | Select-Object -Unique)

# An AUTOOS_ANSWER_<KEY> environment variable pre-answers a prompt; this is how
# -Serve passes the browser's answers through to the same code path.
$fromEnv = @{}
foreach ($key in $needed) {
    $envName = 'AUTOOS_ANSWER_' + ($key.ToUpper() -replace '-', '_')
    $val = [Environment]::GetEnvironmentVariable($envName)
    if ($null -ne $val -and $val -ne '') { $fromEnv[$key] = $val; $answers[$key] = $val }
}
$needed = @($needed | Where-Object { -not $fromEnv.ContainsKey($_) -and -not $answers.ContainsKey($_) })

if ($needed.Count -and -not $Yes) {
    Write-AutoOSSection 'A few questions'
    foreach ($key in $needed) {
        $spec = $catalog.prompts.$key
        $help = if ($spec.PSObject.Properties.Name -contains 'help') { $spec.help } else { '' }
        $def  = if ($spec.PSObject.Properties.Name -contains 'default') { $spec.default } else { '' }
        $answers[$key] = Read-AutoOSValue -Question $spec.question -Default $def -Help $help
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

# ─── 7. Execute ─────────────────────────────────────────────────────────────
Initialize-AutoOSInstaller -DryRun:$DryRun.IsPresent -Answers $answers -RepoRoot $RepoRoot

Write-AutoOSSection 'Installing'
$results = [ordered]@{ installed = @(); skipped = @(); failed = @() }
$unverified = 0
$n = 0
foreach ($c in $plan) {
    $n++
    Write-AutoOSLine "[$n/$($plan.Count)] $($c.Name)" -Level step
    try {
        $state = Install-AutoOSComponent -Component $c
        if ($state -ne 'failed') { Invoke-AutoOSPostInstall -Component $c }
        $results[$state] += $c.Id
        if ($state -eq 'installed') {
            $v = Test-AutoOSComponentWorks -VerifyCommand $c.Verify -Name $c.Name -DryRun:$DryRun.IsPresent
            if ($v -eq 'unverified') { $unverified++ }
            Write-AutoOSLine "$($c.Name) done" -Level ok
        }
    } catch {
        Write-AutoOSLine "$($c.Name): $($_.Exception.Message)" -Level error
        $results.failed += $c.Name
    }
}

# ─── 8. Report ──────────────────────────────────────────────────────────────
Write-AutoOSSection 'Summary'
Write-AutoOSKeyValue 'Installed' "$($results.installed.Count)" 'ok'
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
