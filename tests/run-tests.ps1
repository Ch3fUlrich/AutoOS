#Requires -Version 5.1
<#
.SYNOPSIS
    AutoOS Windows test suite.

.DESCRIPTION
    Zero dependencies on purpose: the whole point of this repo is to run on a
    machine where nothing is installed yet, so the tests must not need Pester.

    No test installs anything. Providers are asserted on the PLANNED command,
    never on system state, and PATH tests run against a scratch copy rather
    than the real environment.

.EXAMPLE
    pwsh tests\run-tests.ps1
.EXAMPLE
    powershell -File tests\run-tests.ps1 -Filter catalog
#>
[CmdletBinding()]
param([string]$Filter = '')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Lib  = Join-Path $Root 'lib\windows'

Import-Module (Join-Path $Lib 'AutoOS.Ui.psm1')      -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Detect.psm1')  -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Catalog.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Install.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Download.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Serve.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.State.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.Usb.psm1')     -Force -DisableNameChecking

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0
$script:Current = ''
$script:Failures = @()

$useColor = -not [Console]::IsOutputRedirected
function C { param($t, $code) if ($useColor) { "$([char]27)[${code}m$t$([char]27)[0m" } else { $t } }

function Describe-Group { param([string]$Name) Write-Host ''; Write-Host (C "-- $Name" '2;38;5;245') }

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notlike "*$Filter*") { return }
    $script:Current = $Name
    try {
        & $Body
    } catch {
        $script:Fail++
        $script:Failures += $Name
        Write-Host ("  " + (C 'X' '1;38;5;167') + " $Name")
        Write-Host ("      " + (C $_.Exception.Message '2;38;5;245'))
        # Without a location a bare .NET message (e.g. a NullReferenceException
        # from inside a cmdlet) is almost impossible to trace back to a line.
        $where = ($_.ScriptStackTrace -split "`n" | Select-Object -First 2) -join ' | '
        if ($where) { Write-Host ("      " + (C $where '2;38;5;245')) }
    }
}

function Pass { $script:Pass++; Write-Host ("  " + (C '+' '38;5;71') + " $script:Current") }
function Skip { param($why) $script:Skip++; Write-Host ("  " + (C '-' '38;5;179') + " $script:Current " + (C "($why)" '2;38;5;245')) }

function Assert-Equal {
    param($Actual, $Expected)
    if ($Actual -eq $Expected) { Pass } else { throw "expected [$Expected] but got [$Actual]" }
}
function Assert-True {
    param($Condition, $Message = 'expected true')
    if ($Condition) { Pass } else { throw $Message }
}
function Assert-Contains {
    param($Collection, $Item)
    if (@($Collection) -contains $Item) { Pass } else { throw "[$Item] not found in [$(@($Collection) -join ', ')]" }
}
function Assert-NotContains {
    param($Collection, $Item)
    if (@($Collection) -notcontains $Item) { Pass } else { throw "[$Item] should not be present" }
}

Write-Host (C "AutoOS Windows test suite" '2;38;5;245') -NoNewline
Write-Host (C "  ($Root)" '2;38;5;245')

$winCatalog = Get-AutoOSCatalog (Join-Path $Root 'catalog\windows.json')

# ─── Catalog schema ─────────────────────────────────────────────────────────
Describe-Group 'catalog schema'

Test-Case 'windows catalog validates' {
    $p = @(Test-AutoOSCatalogSchema -Catalog $winCatalog)
    if ($p.Count -eq 0) { Pass } else { throw ($p -join '; ') }
}

Test-Case 'linux catalog validates too' {
    $c = Get-AutoOSCatalog (Join-Path $Root 'catalog\linux.json')
    $p = @(Test-AutoOSCatalogSchema -Catalog $c)
    if ($p.Count -eq 0) { Pass } else { throw ($p -join '; ') }
}

Test-Case 'a malformed catalog is rejected' {
    $bad = @'
{"categories":[{"id":"x","name":"X","components":[
 {"id":"Bad_ID","name":"n","description":"d","provider":"nope","package":"p","requires":["ghost"]}]}]}
'@ | ConvertFrom-Json
    $p = @(Test-AutoOSCatalogSchema -Catalog $bad)
    $joined = $p -join '; '
    Assert-True ($joined -match 'unknown provider' -and $joined -match 'kebab-case' -and $joined -match 'ghost') `
        "expected provider/kebab/ghost problems, got: $joined"
}

Test-Case 'every winget component has a non-empty package id' {
    $bad = @()
    foreach ($cat in $winCatalog.categories) {
        foreach ($c in $cat.components) {
            if ($c.provider -eq 'winget' -and [string]::IsNullOrWhiteSpace($c.package)) { $bad += $c.id }
        }
    }
    Assert-Equal ($bad -join ',') ''
}

# ─── Fixture-driven filtering ───────────────────────────────────────────────
Describe-Group 'component filtering'

function New-FakeSystem {
    param([string]$Arch = 'x64', [bool]$Headless = $false, [double]$Ram = 32, [int]$Cores = 16)
    # Mirrors every field Get-AutoOSSystemInfo really returns, so consumers such
    # as Get-AutoOSServeState can be tested without touching the live machine.
    [pscustomobject]@{
        Arch = $Arch; IsHeadless = $Headless; RamGB = $Ram; CpuCores = $Cores
        IsAdmin = $true; HasWinget = $true; HasChoco = $false; FreeDiskGB = 200
        VirtualizationEnabled = $true; OsBuild = 22631; WindowsMajor = 11
        OsName = 'Microsoft Windows 11 Pro'; OsVersion = '10.0.22631'
        Manufacturer = 'Contoso'; Model = 'TestBox 9000'
        CpuName = 'Contoso Test CPU'; Gpu = 'none'; HasGpu = $false
        HasDiscreteGpu = $false; IsLaptop = $false; IsVirtual = $true
        UserName = 'testuser'; PsVersion = '5.1.0'; PsEdition = 'Desktop'
        IsInteractive = $false; HasScoop = $false; HasGit = $true
        HasNode = $true; HasNpm = $true; HasDocker = $false; HasWsl = $false
        NodeVersion = 'v22.0.0'; HasMicrophone = $true; Microphone = 'Test Mic'
    }
}

Test-Case 'x64 machine sees the full catalog' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    Assert-True ($a.Count -gt 25) "only $($a.Count) components"
}

Test-Case 'profile membership drives the default ticks' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $light = @($a | Where-Object { 'light' -in $_.Profiles } | ForEach-Object { $_.Id })
    Assert-Contains $light 'claude-code'
}

Test-Case 'light profile does not include desktop customisation' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $light = @($a | Where-Object { 'light' -in $_.Profiles } | ForEach-Object { $_.Id })
    Assert-NotContains $light 'windhawk'
}

Test-Case 'local-ai profile ships ollama' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $localAi = @($a | Where-Object { 'local-ai' -in $_.Profiles } | ForEach-Object { $_.Id })
    Assert-Contains $localAi 'ollama'
}

Test-Case 'local-ai is not pulled in by the rescue profile' {
    # Two orders of magnitude apart: ~400 MB of rescue tools vs 1.4-4.7 GB of
    # model weights (B21) — a user who asked for a rescue stick has not asked
    # for that.
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $rescue = @($a | Where-Object { 'rescue' -in $_.Profiles } | ForEach-Object { $_.Id })
    Assert-NotContains $rescue 'ollama'
}

Test-Case 'every local-ai model component states its download size' {
    $models = @($winCatalog.categories.components | Where-Object { $_.id -like 'ollama-model-*' })
    $bad = @($models | Where-Object { $_.description -notmatch '[0-9]+(\.[0-9]+)?\s?GB' } | ForEach-Object { $_.id })
    Assert-True ($bad.Count -eq 0) "model entries with no size in the description: $($bad -join ' ')"
}

# ─── Dependency resolution ──────────────────────────────────────────────────
Describe-Group 'dependency resolution'

$available = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))

Test-Case 'pulls in transitive requirements' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @('claude-code'))
    Assert-Contains ($plan | ForEach-Object { $_.Id }) 'nodejs'
}

Test-Case 'orders dependencies before dependents' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @('claude-code'))
    $ids = @($plan | ForEach-Object { $_.Id })
    Assert-True ($ids.IndexOf('nodejs') -lt $ids.IndexOf('claude-code')) "order was: $($ids -join ' -> ')"
}

Test-Case 'flags auto-added dependencies' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @('claude-code'))
    $node = $plan | Where-Object { $_.Id -eq 'nodejs' }
    Assert-True $node.AutoAdded 'nodejs should be marked AutoAdded'
}

Test-Case 'does not flag what was explicitly requested' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @('claude-code', 'nodejs'))
    $node = $plan | Where-Object { $_.Id -eq 'nodejs' }
    Assert-True (-not $node.AutoAdded) 'nodejs was requested, so it is not auto-added'
}

Test-Case 'resolves a multi-level chain' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @('oh-my-posh'))
    $ids = @($plan | ForEach-Object { $_.Id })
    foreach ($want in @('powershell7', 'nerd-font', 'oh-my-posh')) {
        if ($ids -notcontains $want) { throw "missing $want in $($ids -join ', ')" }
    }
    Pass
}

Test-Case 'an empty selection yields an empty plan' {
    $plan = @(Resolve-AutoOSPlan -Available $available -SelectedIds @())
    Assert-Equal $plan.Count 0
}

Test-Case 'a dependency cycle is reported, not silently dropped' {
    $fake = @(
        [pscustomobject]@{ Id = 'a'; Name = 'A'; Description = 'd'; Provider = 'winget'; Package = 'A'; Requires = @('b'); Profiles = @(); PostInstall = $null; Prompt = $null; Notes = $null; Category = 'c'; CategoryId = 'c'; Source = $null }
        [pscustomobject]@{ Id = 'b'; Name = 'B'; Description = 'd'; Provider = 'winget'; Package = 'B'; Requires = @('a'); Profiles = @(); PostInstall = $null; Prompt = $null; Notes = $null; Category = 'c'; CategoryId = 'c'; Source = $null }
    )
    try {
        $null = Resolve-AutoOSPlan -Available $fake -SelectedIds @('a')
        throw 'expected a cycle error but resolution succeeded'
    } catch {
        if ($_.Exception.Message -match 'cycle') { Pass } else { throw $_ }
    }
}

# ─── Detection heuristics ───────────────────────────────────────────────────
Describe-Group 'detection'

Test-Case 'arm64 maps to the light profile' {
    Assert-Equal (Get-AutoOSSuggestedProfile -SystemInfo (New-FakeSystem -Arch 'arm64')) 'light'
}

Test-Case 'a big x64 desktop maps to workstation' {
    Assert-Equal (Get-AutoOSSuggestedProfile -SystemInfo (New-FakeSystem -Ram 32 -Cores 16)) 'workstation'
}

Test-Case 'an 8 GB laptop is NOT downgraded to light' {
    # 8 GB reports ~7.4 GB usable; 'light' is for Pi-class hardware only.
    Assert-Equal (Get-AutoOSSuggestedProfile -SystemInfo (New-FakeSystem -Ram 7.4 -Cores 8)) 'ai-coding'
}

Test-Case 'a headless machine skips desktop profiles' {
    Assert-Equal (Get-AutoOSSuggestedProfile -SystemInfo (New-FakeSystem -Headless $true)) 'ai-coding'
}

Test-Case 'no package manager is a hard blocker' {
    $s = New-FakeSystem
    $s.HasWinget = $false; $s.HasChoco = $false
    $b = @(Get-AutoOSBlockers -SystemInfo $s)
    Assert-True (@($b | Where-Object { $_.Severity -eq 'error' }).Count -ge 1) 'expected an error blocker'
}

Test-Case 'disabled virtualisation is a warning, not a blocker' {
    $s = New-FakeSystem
    $s.VirtualizationEnabled = $false
    $b = @(Get-AutoOSBlockers -SystemInfo $s)
    $v = @($b | Where-Object { $_.Message -match 'virtualisation' })
    Assert-Equal $v[0].Severity 'warn'
}

# ─── "Is it already installed?" ─────────────────────────────────────────────
# Every probe below runs against a synthetic inventory rather than this machine,
# so the suite asserts the same thing on a box where nothing is installed.
# The bar is asymmetric on purpose: a missed install costs a re-run, a false
# positive silently skips something the user asked for.

function New-FakeInventory {
    # Mirrors every field Get-AutoOSInstalledInventory really returns.
    param(
        [object[]]$Entries = @(),
        [string[]]$Paths = @(),
        [string[]]$Appx = @(),
        [string[]]$Programs = @(),
        [string[]]$WingetPackages = @()
    )
    [pscustomobject]@{
        Entries = $Entries; Paths = $Paths; Appx = $Appx
        Programs = $Programs; WingetPackages = $WingetPackages
    }
}

function New-FakeComponent {
    param([string]$Id = 'widget', [string]$Name = 'Widget',
          [string]$Provider = 'winget', [string]$Package = 'Contoso.Widget',
          [string]$Verify = '')
    [pscustomobject]@{ Id = $Id; Name = $Name; Provider = $Provider; Package = $Package; Verify = $Verify }
}

Test-Case 'a bitness suffix on the registered name still matches' {
    # Notepad++ registers as "Notepad++ (64-bit x64)" and went undetected.
    Assert-True (Test-AutoOSRegisteredName 'Notepad++ (64-bit x64)' 'Notepad++') 'bitness suffix rejected'
}

Test-Case 'a release-channel suffix on the registered name still matches' {
    # PowerToys registers as "PowerToys (Preview) x64".
    Assert-True (Test-AutoOSRegisteredName 'PowerToys (Preview) x64' 'PowerToys') 'preview suffix rejected'
}

Test-Case 'an architecture-plus-locale suffix still matches' {
    # "Mozilla Firefox (x64 en-US)". Widening the pattern for PowerToys dropped
    # this case once and silently un-detected Firefox on every English install.
    Assert-True (Test-AutoOSRegisteredName 'Mozilla Firefox (x64 en-US)' 'Mozilla Firefox') 'locale suffix rejected'
}

Test-Case 'a different product sharing a prefix is never matched' {
    foreach ($pair in @(@('Steam Tools', 'Steam'), @('Firefox Helper', 'Firefox'),
                        @('PowerToys Run Plugin', 'PowerToys'), @('Git Extensions', 'Git'))) {
        if (Test-AutoOSRegisteredName $pair[0] $pair[1]) { throw "'$($pair[0])' must not match '$($pair[1])'" }
    }
    Pass
}

Test-Case 'the shim directories package managers use are probed even when PATH is stale' {
    $dirs = @(Get-AutoOSShimDirectory)
    foreach ($want in @('scoop\shims', 'chocolatey\bin', 'Microsoft\WinGet\Links')) {
        if (-not @($dirs | Where-Object { $_ -like "*$want*" })) {
            throw "no probe directory for $want in: $($dirs -join '; ')"
        }
    }
    Pass
}

Test-Case 'a shim directory off the persistent PATH still resolves a verify command' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    try {
        Set-Content -LiteralPath (Join-Path $tmp 'widget.cmd') -Value '@echo off'
        $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent -Verify 'widget --version') `
                                            -Inventory (New-FakeInventory -Paths @($tmp))
        Assert-Equal $status 'installed'
    } finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'a winget package id present in the cached list counts as installed' {
    # winget's own record, read once per run - never one probe per catalog row.
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent) `
                                        -Inventory (New-FakeInventory -WingetPackages @('Contoso.Widget'))
    Assert-Equal $status 'installed'
}

Test-Case 'a winget package id missing from the cached list is not-detected' {
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent -Package 'Contoso.Other') `
                                        -Inventory (New-FakeInventory -WingetPackages @('Contoso.Widget'))
    Assert-Equal $status 'not-detected'
}

Test-Case 'the winget list is only trusted for winget rows' {
    # 'python' appears in that output as a moniker for something else entirely.
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent -Provider 'choco' -Package 'widget') `
                                        -Inventory (New-FakeInventory -WingetPackages @('widget'))
    Assert-Equal $status 'not-detected'
}

Test-Case 'a per-user install under LOCALAPPDATA\Programs counts as installed' {
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent -Name 'Obsidian' -Package 'Obsidian.Obsidian') `
                                        -Inventory (New-FakeInventory -Programs @('obsidian'))
    Assert-Equal $status 'installed'
}

Test-Case 'a per-user directory belonging to something else proves nothing' {
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent -Name 'Obsidian' -Package 'Obsidian.Obsidian') `
                                        -Inventory (New-FakeInventory -Programs @('obsidianhelper'))
    Assert-Equal $status 'not-detected'
}

Test-Case 'a per-user program directory is only believed when it holds an executable' {
    # %APPDATA%\JAM Software outlives TreeSize by years: configuration left
    # behind is not an installation, which is why only Programs\ is read and
    # only when an .exe is actually in it.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path (Join-Path $tmp 'Leftovers') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $tmp 'RealApp') -Force)
    try {
        Set-Content -LiteralPath (Join-Path $tmp 'RealApp\app.exe') -Value 'x'
        $found = @(Get-AutoOSProgramDirectoryName -Root $tmp)
        Assert-True (($found -contains 'realapp') -and ($found -notcontains 'leftovers')) `
            "expected only realapp, got: $($found -join ', ')"
    } finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'an appx package still wins over everything else' {
    $c = New-FakeComponent -Name 'Slack' -Package 'SlackTechnologies.Slack'
    Add-Member -InputObject $c -NotePropertyName InstalledAppx -NotePropertyValue @('91750D7E.Slack')
    $status = Get-AutoOSInstalledStatus -Component $c -Inventory (New-FakeInventory -Appx @('91750D7E.Slack'))
    Assert-Equal $status 'installed'
}

Test-Case 'nothing is guessed when every probe comes up empty' {
    $status = Get-AutoOSInstalledStatus -Component (New-FakeComponent) -Inventory (New-FakeInventory)
    Assert-Equal $status 'not-detected'
}

Test-Case 'the disk analyser is WizTree, and treesize is gone' {
    $ids = @($available | ForEach-Object { $_.Id })
    Assert-True (($ids -contains 'wiztree') -and ($ids -notcontains 'treesize')) `
        "catalog ids were: $($ids -join ', ')"
}

Test-Case 'git and the GitHub CLI are both in the workstation profile' {
    $ws = @($available | Where-Object { 'workstation' -in $_.Profiles } | ForEach-Object { $_.Id })
    foreach ($want in @('git', 'gh')) {
        if ($ws -notcontains $want) { throw "workstation is missing $want" }
    }
    Pass
}

Test-Case 'the windows catalog asks for a git identity and wires it to git' {
    foreach ($key in @('git_user_name', 'git_user_email')) {
        if ($winCatalog.prompts.PSObject.Properties.Name -notcontains $key) { throw "windows catalog never asks $key" }
    }
    $git = $available | Where-Object { $_.Id -eq 'git' }
    Assert-True (($git.Prompt -split '[, ]+') -contains 'git_user_name' -and
                 ($git.Prompt -split '[, ]+') -contains 'git_user_email') `
        "git prompt was: [$($git.Prompt)]"
}

Test-Case 'the git post-install step exists and is callable' {
    $git = $available | Where-Object { $_.Id -eq 'git' }
    Assert-True ($git.PostInstall -and (Get-Command $git.PostInstall -ErrorAction SilentlyContinue)) `
        "post-install '$($git.PostInstall)' is not a loaded function"
}

# ─── PATH handling (the critical regression) ────────────────────────────────
Describe-Group 'PATH handling'

Test-Case 'appending preserves every existing entry' {
    # The original Ansible task replaced Path outright, wiping the user's
    # environment. This asserts the read-modify-write behaviour directly.
    $existing = 'C:\Windows;C:\Windows\System32;C:\Program Files\Git\cmd'
    $parts = @($existing -split ';' | Where-Object { $_ -ne '' })
    $toAdd = @('C:\tools\miniconda3', 'C:\tools\miniconda3\Scripts')
    foreach ($d in $toAdd) {
        if (@($parts | Where-Object { $_.TrimEnd('\') -ieq $d.TrimEnd('\') }).Count -eq 0) { $parts += $d }
    }
    $result = $parts -join ';'
    Assert-True ($result.StartsWith($existing) -and $result -match 'miniconda3') "got: $result"
}

Test-Case 'appending the same directory twice is a no-op' {
    $parts = @('C:\Windows', 'C:\tools\miniconda3')
    $before = $parts.Count
    $d = 'C:\tools\miniconda3\'   # trailing slash must still match
    if (@($parts | Where-Object { $_.TrimEnd('\') -ieq $d.TrimEnd('\') }).Count -eq 0) { $parts += $d }
    Assert-Equal $parts.Count $before
}

Test-Case 'Add-AutoOSPathEntry makes no change in dry run' {
    Initialize-AutoOSInstaller -DryRun $true -RepoRoot $Root
    $before = [Environment]::GetEnvironmentVariable('Path', 'User')
    $null = Add-AutoOSPathEntry -Directory @('C:\autoos-test-should-not-persist')
    $after = [Environment]::GetEnvironmentVariable('Path', 'User')
    Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
    Assert-Equal $after $before
}

# ─── UI ─────────────────────────────────────────────────────────────────────
Describe-Group 'ui'

Test-Case 'colour can be turned off completely' {
    Set-AutoOSColor $false
    $t = Format-AutoOSColor 'hello' 'accent'
    Set-AutoOSColor $true
    Assert-Equal $t 'hello'
}

Test-Case 'colour output wraps in a reset' {
    Set-AutoOSColor $true
    $t = Format-AutoOSColor 'hello' 'accent'
    Assert-True ($t.EndsWith("$([char]27)[0m")) 'missing reset sequence'
}

Test-Case 'menu cursor skips group headers' {
    $rows = New-Object System.Collections.ArrayList
    [void]$rows.Add([pscustomobject]@{ Kind = 'header' })
    [void]$rows.Add([pscustomobject]@{ Kind = 'item' })
    [void]$rows.Add([pscustomobject]@{ Kind = 'header' })
    [void]$rows.Add([pscustomobject]@{ Kind = 'item' })
    Assert-Equal (Get-AutoOSNextItemIndex $rows 1 1) 3
}

Test-Case 'menu cursor stays put at the end of the list' {
    $rows = New-Object System.Collections.ArrayList
    [void]$rows.Add([pscustomobject]@{ Kind = 'item' })
    Assert-Equal (Get-AutoOSNextItemIndex $rows 0 1) 0
}

Test-Case 'Show-AutoOSRadioMenu returns DefaultId in non-interactive mode' {
    $env:AUTOOS_NONINTERACTIVE = '1'
    try {
        $items = @(
            [pscustomobject]@{ Id = 'workstation'; Name = 'workstation'; Description = 'Full dev' },
            [pscustomobject]@{ Id = 'light'; Name = 'light'; Description = 'Minimal' }
        )
        $res = Show-AutoOSRadioMenu -Items $items -Title 'Profile' -DefaultId 'light'
        Assert-Equal $res 'light'
    } finally {
        Remove-Item Env:\AUTOOS_NONINTERACTIVE -ErrorAction SilentlyContinue
    }
}

Test-Case 'Read-AutoOSConfirm returns default in non-interactive mode' {
    $env:AUTOOS_NONINTERACTIVE = '1'
    try {
        Assert-True (Read-AutoOSConfirm -Question 'Proceed?' -Default $true)
        Assert-True (-not (Read-AutoOSConfirm -Question 'Proceed?' -Default $false))
    } finally {
        Remove-Item Env:\AUTOOS_NONINTERACTIVE -ErrorAction SilentlyContinue
    }
}

Test-Case 'log lines are classified for the browser UI' {
    Assert-Equal (Get-AutoOSLineLevel '  + installed') 'ok'
}

Test-Case 'plain log lines get no class' {
    Assert-Equal (Get-AutoOSLineLevel 'just some text') ''
}

# ─── End to end ─────────────────────────────────────────────────────────────
Describe-Group 'end-to-end (dry run only)'

$setup = Join-Path $Root 'setup.ps1'

Test-Case '--CheckCatalog succeeds' {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $setup -CheckCatalog | Out-Null
    Assert-Equal $LASTEXITCODE 0
}

Test-Case 'a dry run exits cleanly' {
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $setup -DryRun -Profile light -Yes -NoColor 2>&1
    Assert-True ($LASTEXITCODE -eq 0) "exit $LASTEXITCODE : $($out | Select-Object -Last 3)"
}

Test-Case 'a dry run reports the plan' {
    $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -DryRun -Profile light -Yes -NoColor 2>&1) -join "`n"
    Assert-True ($out -match 'DRY RUN') 'no dry-run banner in output'
}

Test-Case 'two consecutive dry runs produce the same plan' {
    $re = '^\s+\d+\.\s'
    $a = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -DryRun -Profile light -Yes -NoColor 2>&1) -match $re
    $b = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -DryRun -Profile light -Yes -NoColor 2>&1) -match $re
    Assert-Equal ($a -join '|') ($b -join '|')
}

Test-Case 'an unknown component id is rejected' {
    $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Only 'definitely-not-a-thing' -Yes -NoColor 2>&1) -join "`n"
    Assert-True ($LASTEXITCODE -ne 0 -and $out -match 'Unknown component') "exit=$LASTEXITCODE out=$out"
}

# ─── Run state, verification, undo ──────────────────────────────────────────
Describe-Group 'state, verify and undo'

Test-Case 'macos catalog validates' {
    $c = Get-AutoOSCatalog (Join-Path $Root 'catalog\macos.json')
    $p = @(Test-AutoOSCatalogSchema -Catalog $c)
    if ($p.Count -eq 0) { Pass } else { throw ($p -join '; ') }
}

Test-Case 'brew is an accepted provider' {
    $c = Get-AutoOSCatalog (Join-Path $Root 'catalog\macos.json')
    $p = @(Test-AutoOSCatalogSchema -Catalog $c)
    Assert-True (($p -join ';') -notmatch "unknown provider 'brew'") "brew was rejected: $($p -join '; ')"
}

Test-Case 'cask is projected onto the component' {
    $c = Get-AutoOSCatalog (Join-Path $Root 'catalog\macos.json')
    $a = @(Get-AutoOSAvailableComponents -Catalog $c -SystemInfo (New-FakeSystem -Arch 'arm64'))
    $docker = $a | Where-Object { $_.Id -eq 'docker' }
    Assert-True $docker.Cask 'docker should be a cask'
}

Test-Case 'verify commands survive catalog projection' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $git = $a | Where-Object { $_.Id -eq 'git' }
    Assert-Equal $git.Verify 'git --version'
}

Test-Case 'a component with no verify command is unchecked' {
    Assert-Equal (Test-AutoOSComponentWorks -VerifyCommand '' -Name 'x') 'unchecked'
}

Test-Case 'verification passes for something that is installed' {
    Assert-Equal (Test-AutoOSComponentWorks -VerifyCommand 'cmd /c ver' -Name 'cmd') 'verified'
}

Test-Case 'verification reports unverified for a missing binary' {
    Assert-Equal (Test-AutoOSComponentWorks -VerifyCommand 'definitely-not-a-real-binary --version' -Name 'ghost') 'unverified'
}

Test-Case '-NoVerify skips the check entirely' {
    Set-AutoOSVerify $false
    $r = Test-AutoOSComponentWorks -VerifyCommand 'definitely-not-a-real-binary' -Name 'ghost'
    Set-AutoOSVerify $true
    Assert-Equal $r 'unchecked'
}

Test-Case 'a dry run verifies nothing' {
    Assert-Equal (Test-AutoOSComponentWorks -VerifyCommand 'cmd /c ver' -Name 'cmd' -DryRun $true) 'unchecked'
}

Test-Case 'state survives a save/load round trip' {
    $tmp = Join-Path $env:TEMP "autoos-state-test-$([Guid]::NewGuid().ToString('N')).json"
    Save-AutoOSState -Path $tmp -ProfileName 'light' -Selected @('git', 'nodejs') `
        -Answers @{ omnigraph_url = 'https://example.invalid' } `
        -Results @{ installed = @('git'); skipped = @('nodejs'); failed = @() } | Out-Null
    $back = Import-AutoOSState -Path $tmp
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    Assert-Equal "$($back.Profile)|$($back.Selected -join ',')|$($back.Answers['omnigraph_url'])" `
                 'light|git,nodejs|https://example.invalid'
}

Test-Case 'a dry run saves no state' {
    $tmp = Join-Path $env:TEMP "autoos-state-test-$([Guid]::NewGuid().ToString('N')).json"
    Save-AutoOSState -Path $tmp -ProfileName 'light' -Selected @('git') -DryRun $true | Out-Null
    $existed = Test-Path $tmp
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    Assert-True (-not $existed) 'dry run wrote a state file'
}

Test-Case 'backups are grouped newest-per-original' {
    $scratch = Join-Path $env:TEMP "autoos-undo-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $scratch -Force | Out-Null
    'original' | Out-File (Join-Path $scratch 'profile.ps1') -Encoding utf8
    'v1' | Out-File (Join-Path $scratch 'profile.ps1.autoos-backup-20260101-000000') -Encoding utf8
    'v2' | Out-File (Join-Path $scratch 'profile.ps1.autoos-backup-20260202-000000') -Encoding utf8
    $b = @(Get-AutoOSBackups -SearchRoot $scratch)
    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
    Assert-True ($b.Count -eq 1 -and $b[0].Backup -match '20260202') "got: $($b | ConvertTo-Json -Compress)"
}

Test-Case 'undo never uninstalls anything' {
    # The safety property, asserted on the source rather than by removing software.
    $src = Get-Content (Join-Path $Lib 'AutoOS.State.psm1') -Raw
    Assert-True ($src -notmatch 'winget\s+uninstall|choco\s+uninstall|npm\s+uninstall') `
        'the undo path contains an uninstall command'
}

# ─── Browser UI payload ─────────────────────────────────────────────────────
Describe-Group 'browser UI'

Test-Case 'every component has a homepage link' {
    $bad = @()
    foreach ($f in @('windows.json', 'linux.json', 'macos.json')) {
        $c = Get-AutoOSCatalog (Join-Path $Root "catalog\$f")
        foreach ($cat in $c.categories) {
            foreach ($comp in $cat.components) {
                if (-not $comp.PSObject.Properties.Name.Contains('homepage')) { $bad += "$f`:$($comp.id)" }
            }
        }
    }
    Assert-Equal ($bad -join ',') ''
}

Test-Case 'a non-URL homepage is rejected' {
    $bad = @'
{"categories":[{"id":"x","name":"X","components":[
 {"id":"thing","name":"Thing","description":"d","provider":"winget","package":"p","homepage":"not-a-url"}]}]}
'@ | ConvertFrom-Json
    $p = @(Test-AutoOSCatalogSchema -Catalog $bad)
    Assert-True (($p -join '; ') -match 'homepage') "expected a homepage complaint, got: $($p -join '; ')"
}

Test-Case 'the serve payload carries what the UI needs' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    $cc = $state.components | Where-Object { $_.id -eq 'claude-code' }
    foreach ($k in @('requires', 'homepage', 'verify', 'category', 'provider')) {
        if (-not $cc.Contains($k)) { throw "serve payload is missing '$k'" }
    }
    Pass
}

Test-Case 'the serve payload keeps the dependency edges' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    $cc = $state.components | Where-Object { $_.id -eq 'claude-code' }
    Assert-Contains $cc.requires 'nodejs'
}

Test-Case 'the serve payload survives JSON round-tripping' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    $back = ($state | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
    $cc = $back.components | Where-Object { $_.id -eq 'claude-code' }
    Assert-Contains @($cc.requires) 'nodejs'
}

Test-Case 'the Windows payload reports WSL as host, not guest' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    # Windows is never the WSL guest; the field must say so rather than be absent.
    Assert-True ($state.wsl.Contains('isWsl') -and -not $state.wsl.isWsl) `
        "wsl block: $($state.wsl | ConvertTo-Json -Compress)"
}

Test-Case 'the Windows payload reports whether WSL is available' {
    $sys = New-FakeSystem
    $sys.HasWsl = $true
    $state = Get-AutoOSServeState -SystemInfo $sys -Catalog $winCatalog
    Assert-True $state.wsl.available 'HasWsl should surface as wsl.available'
}

Test-Case 'the Windows payload carries an environment field' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    Assert-True ($state.system.Contains('environment')) 'system.environment missing'
}

Test-Case 'the serve payload labels platform availability' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    $handy = $state.components | Where-Object { $_.id -eq 'handy' }
    foreach ($p in @('windows', 'linux', 'macos')) {
        if (@($handy.platforms) -notcontains $p) {
            throw "handy platforms were: $(@($handy.platforms) -join ',')"
        }
    }
    Pass
}

Test-Case 'a Windows-only component is labelled as such' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    $wh = $state.components | Where-Object { $_.id -eq 'windhawk' }
    Assert-Equal (@($wh.platforms) -join ',') 'windows'
}

Test-Case 'Handy is offered for dictation in the coding profiles' {
    $handy = $null
    foreach ($cat in $winCatalog.categories) {
        foreach ($c in $cat.components) { if ($c.id -eq 'handy') { $handy = $c } }
    }
    if (-not $handy) { throw 'handy is not in the catalog' }
    Assert-Equal "$($handy.package)|$(($handy.profiles | Sort-Object) -join ',')" `
                 'cjpais.Handy|ai-coding,workstation'
}

Test-Case 'Handy says it needs a microphone rather than being hidden' {
    # It installs fine without one, so it is noted, not filtered out.
    $handy = $null
    foreach ($cat in $winCatalog.categories) {
        foreach ($c in $cat.components) { if ($c.id -eq 'handy') { $handy = $c } }
    }
    Assert-True ($handy.notes -match 'microphone') "notes were: $($handy.notes)"
}

Test-Case 'the payload reports the microphone' {
    $state = Get-AutoOSServeState -SystemInfo (New-FakeSystem) -Catalog $winCatalog
    Assert-Equal $state.system.microphone 'Test Mic'
}

$serveSource = Get-Content -Path (Join-Path $Root 'lib\windows\AutoOS.Serve.psm1') -Raw -Encoding UTF8
$pageSource  = Get-Content -Path (Join-Path $Root 'web\index.html')             -Raw -Encoding UTF8

Test-Case 'a busy port moves the server on instead of failing' {
    # The listener is opened in a walk, not a single Start(), so an already-taken
    # 8777 costs a line of output rather than the whole run.
    if ($serveSource -notmatch 'foreach \(\$candidatePort in') { throw 'no port walk in Start-AutoOSServer' }
    if ($serveSource -notmatch 'already in use - serving on')    { throw 'a moved port is never announced' }
    Pass
}

Test-Case 'a missing URL reservation stops the walk immediately' {
    # ERROR_ACCESS_DENIED will not be cured by the next port along; retrying
    # nineteen more times just delays the message that actually helps.
    Assert-True ($serveSource -match 'ErrorCode -eq 5') 'access-denied is not singled out'
}

Test-Case 'the installer output is drained without a reader thread' {
    # A PowerShell scriptblock has no runspace on a raw .NET thread: it does not
    # fail the read, it kills the process. This test is the tripwire for anyone
    # reaching for [System.Threading.Thread] here again.
    if ($serveSource -match '\[System\.Threading\.Thread\]') { throw 'a raw thread is back in the serve module' }
    if ($serveSource -notmatch 'ReadLineAsync')                 { throw 'nothing is draining the installer output' }
    Pass
}

Test-Case 'the server answers a heartbeat the page can poll' {
    if ($serveSource -notmatch "'/api/ping'") { throw 'no /api/ping route' }
    if ($pageSource  -notmatch '/api/ping')    { throw 'the page never polls a heartbeat' }
    Pass
}

Test-Case 'a page whose server has gone tears itself down' {
    if ($pageSource -notmatch 'function serverGone') { throw 'the page has no teardown path' }
    if ($pageSource -notmatch 'window\.close')       { throw 'the page never tries to close itself' }
    Pass
}

Test-Case 'the output can be copied without selecting it by hand' {
    if ($pageSource -notmatch 'id="copyLog"')    { throw 'no copy button on the output card' }
    if ($pageSource -notmatch 'function copyLog'){ throw 'the copy button does nothing' }
    # innerText returns nothing while the Output card is collapsed, so the text
    # has to be read off the child elements instead.
    if ($pageSource -notmatch 'function logText'){ throw 'the log text is not gathered safely' }
    if ($pageSource -notmatch 'execCommand')     { throw 'no fallback for a non-secure context' }
    Pass
}

Test-Case 'a verify command resolves to the file that will run' {
    $hint = Get-AutoOSLaunchHint -Component ([pscustomobject]@{
        Id = 'powershell7'; Name = 'PowerShell 7'; Verify = 'powershell -Command' })
    if ($hint.How -ne 'run  powershell') { throw "how was: $($hint.How)" }
    Assert-True ($hint.Path -match '\.exe$') "path was: $($hint.Path)"
}

Test-Case 'a component that is not here reports nothing rather than guessing' {
    # A blank is honest. A plausible-looking path that does not exist is worse
    # than saying nothing, because it reads like a fact.
    $hint = Get-AutoOSLaunchHint -Component ([pscustomobject]@{
        Id = 'autoos-nonesuch-xyz'; Name = 'AutoOS Nonesuch XYZ'; Verify = 'autoos-nonesuch-xyz' })
    if ($hint.How -ne '' -or $hint.Path -ne '') { throw "invented: $($hint.How) / $($hint.Path)" }
    Pass
}

Test-Case 'the report says where things landed' {
    $setupSource = Get-Content -Path (Join-Path $Root 'setup.ps1') -Raw -Encoding UTF8
    if ($setupSource -notmatch 'Where to find them')   { throw 'no launch section in the report' }
    if ($setupSource -notmatch 'Get-AutoOSLaunchHint') { throw 'the report never resolves a location' }
    Pass
}

Test-Case '-Only accepts a comma-separated list through -File' {
    # powershell -File passes every argument literally, so "a,b" arrives as one
    # string. The browser UI shells out exactly that way, so a multi-component
    # install used to fail as "unknown component id(s): a,b".
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $setup `
             -Only 'git,nodejs' -Yes -NoColor -DryRun 2>&1
    Assert-True ($LASTEXITCODE -eq 0 -and ($out -join "`n") -notmatch 'Unknown component') `
                "exit $LASTEXITCODE : $($out | Select-Object -Last 3)"
}

Test-Case 'a page whose scripts are blocked says so' {
    # Everything on the page is driven by one inline script. Without it the
    # header would sit at "connecting..." for ever and explain nothing.
    if ($pageSource -notmatch '<noscript>')            { throw 'no noscript fallback' }
    if ($pageSource -notmatch 'JavaScript is blocked')  { throw 'the noscript fallback says nothing useful' }
    Pass
}

# ─── MCP wiring ────────────────────────────────────────────────────
Describe-Group 'MCP wiring'

$installSource = Get-Content -Path (Join-Path $Root 'lib\windows\AutoOS.Install.psm1') -Raw -Encoding UTF8

Test-Case 'graphify is registered once, at user scope' {
    # The command is cwd-relative, so one definition serves every repository its
    # own graph. A per-repo entry would pin one repo's graph for all of them.
    if ($installSource -notmatch "Name 'graphify' -Command 'uv' -Scope 'user'") {
        throw 'graphify is not registered at user scope'
    }
    if ($installSource -notmatch 'graphify-out/graph.json') { throw 'graphify is not cwd-relative' }
    Pass
}

Test-Case 'omnigraph is never registered at user scope' {
    # A user-scope omnigraph silently wins over the per-repo one and answers
    # from the wrong graph, which looks identical to it working.
    if ($installSource -match "Register-AutoOSMcpServer[^\r\n]*'omnigraph'") {
        throw 'omnigraph is being registered as a user server'
    }
    if ($installSource -notmatch 'claude mcp remove omnigraph') { throw 'the shadowing case is never called out' }
    Pass
}

Test-Case 'a project MCP server is approved, not just declared' {
    # A tracked .mcp.json cannot approve itself; Claude Code skips an unapproved
    # project server silently.
    if ($installSource -notmatch 'enabledMcpjsonServers') { throw 'nothing writes the approval list' }
    Pass
}

Test-Case 'approving a server keeps the rest of the settings file' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-mcp-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $tmp '.claude') -Force | Out-Null
    $file = Join-Path $tmp '.claude\settings.local.json'
    '{"permissions":{"allow":["Bash(ls:*)"]},"enabledMcpjsonServers":["already"]}' |
        Out-File -FilePath $file -Encoding utf8
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Enable-AutoOSProjectMcpServer -RepoPath $tmp -Name 'omnigraph' 6>$null | Out-Null
        $after = Get-Content -Path $file -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $after.permissions) { throw 'the unrelated permissions block was dropped' }
        Assert-Contains $after.enabledMcpjsonServers 'omnigraph'
    } finally {
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'approving twice adds nothing the second time' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-mcp-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Enable-AutoOSProjectMcpServer -RepoPath $tmp -Name 'omnigraph' 6>$null | Out-Null
        Enable-AutoOSProjectMcpServer -RepoPath $tmp -Name 'omnigraph' 6>$null | Out-Null
        $after = Get-Content -Path (Join-Path $tmp '.claude\settings.local.json') -Raw -Encoding UTF8 |
                 ConvertFrom-Json
        Assert-Equal @($after.enabledMcpjsonServers).Count 1
    } finally {
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'the Antigravity config is merged, not replaced' {
    # It used to be written from scratch, which silently deleted every other MCP
    # server the user had configured there.
    if ($installSource -notmatch 'cfg\.Contains\(''mcpServers''\)') {
        throw 'Set-AutoOSAntigravityMcp does not read the existing servers back'
    }
    Pass
}

Test-Case 'no bearer token is ever invented' {
    # This repository is public. A real-looking secret in it is a leak whether
    # or not it happens to work, and a guessed one fails as an unexplainable 401.
    if ($installSource -match "OMNIGRAPH_TOKEN\s*=\s*'[^']") { throw 'a token literal is present' }
    if ($installSource -notmatch 'OMNIGRAPH_TOKEN is not set') { throw 'a missing token is never reported' }
    Pass
}

Test-Case 'Install-AutoOSAgentSkills links skills to Antigravity and Claude Code' {
    if ($installSource -notmatch 'agySkills = Join-Path \$env:USERPROFILE ''\.gemini\\config\\skills''') {
        throw 'Install-AutoOSAgentSkills does not configure Antigravity skills'
    }
    if ($installSource -notmatch 'claudeSkills = Join-Path \$env:USERPROFILE ''\.claude\\skills''') {
        throw 'Install-AutoOSAgentSkills does not configure Claude Code skills'
    }
    Pass
}

Test-Case 'Test-AutoOSInstalled checks both Documents\code and Documents\Code' {
    if ((Get-Content (Join-Path $Root 'lib/windows/AutoOS.Detect.psm1') -Raw) -notmatch 'Code\\agent-skills') {
        throw 'Test-AutoOSInstalled does not check both Code and code paths'
    }
    Pass
}

# ─── Claude autostart & session tracking ───────────────────────────────────
Describe-Group 'claude autostart'

Import-Module (Join-Path $Root 'lib\windows\AutoOS.ClaudeAutostart.psm1') -DisableNameChecking -Force

# A fixture transcript tree shaped exactly like %USERPROFILE%\.claude\projects:
# one directory per working directory, one *.jsonl per session, with cwd and
# sessionId carried in the records themselves (ADR 0001). The first line has no
# cwd, like a real transcript, so discovery has to read forward to find one.
function New-ClaudeFixture {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][object[]]$Sessions)
    foreach ($s in $Sessions) {
        $slug = ($s.Cwd -replace '[^A-Za-z0-9]', '-')
        $dir = Join-Path $Root "projects\$slug"
        [void](New-Item -ItemType Directory -Path $dir -Force)
        $file = Join-Path $dir "$($s.Uuid).jsonl"
        $lines = @(
            (@{ type = 'queue-operation'; sessionId = $s.Uuid } | ConvertTo-Json -Compress)
            (@{ sessionId = $s.Uuid; cwd = $s.Cwd; gitBranch = 'main'; type = 'user' } | ConvertTo-Json -Compress)
        )
        [System.IO.File]::WriteAllLines($file, $lines)
        (Get-Item $file).LastWriteTime = (Get-Date).AddMinutes(-1 * $s.AgeMinutes)
    }
}

function Use-ClaudeFixture {
    param([Parameter(Mandatory)][object[]]$Sessions, [int]$LiveCount = 1, [Parameter(Mandatory)][scriptblock]$Body)
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    try {
        New-ClaudeFixture -Root $tmp -Sessions $Sessions
        $env:AUTOOS_CLAUDE_HOME = $tmp
        $env:AUTOOS_CLAUDE_LIVE_COUNT = "$LiveCount"
        $env:AUTOOS_CLAUDE_STATE_FILE = Join-Path $tmp 'state.json'
        & $Body $tmp
    }
    finally {
        Remove-Item Env:\AUTOOS_CLAUDE_HOME, Env:\AUTOOS_CLAUDE_LIVE_COUNT, Env:\AUTOOS_CLAUDE_STATE_FILE -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'claude-autostart component exists in the Windows catalog' {
    $cat = Get-Content (Join-Path $Root 'catalog\windows.json') -Raw | ConvertFrom-Json
    $comp = $cat.categories.components | Where-Object { $_.id -eq 'claude-autostart' }
    Assert-Equal $comp.provider 'script'
}

Test-Case 'claude-autostart is absent from the macOS catalog' {
    # It routes through lib/linux/install.sh, which writes systemd units; on a Mac
    # that reported `installed` while doing nothing.
    $cat = Get-Content (Join-Path $Root 'catalog\macos.json') -Raw | ConvertFrom-Json
    $comp = @($cat.categories.components | Where-Object { $_.id -eq 'claude-autostart' })
    Assert-Equal $comp.Count 0
}

Test-Case 'discovery reads the cwd out of each transcript' {
    # The previous implementation parsed process command lines. Win32_Process has
    # no working directory, so it found nothing at all - 0 of 6 live sessions on a
    # real machine. This is that regression, pinned.
    Use-ClaudeFixture -LiveCount 2 -Sessions @(
        @{ Cwd = 'C:\work\alpha'; Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 2 }
        @{ Cwd = 'C:\work\beta';  Uuid = '22222222-2222-2222-2222-222222222222'; AgeMinutes = 3 }
    ) -Body {
        $found = @(Find-AutoOSClaudeSessions | Sort-Object cwd)
        Assert-Equal $found.Count 2
        Assert-Equal $found[0].cwd 'C:\work\alpha'
        Assert-Equal $found[0].session_uuid '11111111-1111-1111-1111-111111111111'
        Assert-Equal $found[0].name 'alpha'
    }
}

Test-Case 'a transcript older than the liveness window is not restored' {
    Use-ClaudeFixture -Sessions @(
        @{ Cwd = 'C:\work\fresh'; Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 5 }
        @{ Cwd = 'C:\work\stale'; Uuid = '22222222-2222-2222-2222-222222222222'; AgeMinutes = 900 }
    ) -Body {
        $found = @(Find-AutoOSClaudeSessions)
        Assert-Equal $found.Count 1
        Assert-Equal $found[0].cwd 'C:\work\fresh'
    }
}

Test-Case 'nothing is discovered while no Claude process is running' {
    Use-ClaudeFixture -LiveCount 0 -Sessions @(
        @{ Cwd = 'C:\work\alpha'; Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 2 }
    ) -Body {
        Assert-Equal @(Find-AutoOSClaudeSessions).Count 0
    }
}

Test-Case 'one session is recorded once even when it left transcripts in two places' {
    # Observed on a real machine: the same session id under two project slugs.
    # Restoring it twice opens two terminals fighting over one conversation.
    Use-ClaudeFixture -Sessions @(
        @{ Cwd = 'C:\work\proj';         Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 4 }
        @{ Cwd = 'C:\work\proj\scratch'; Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 2 }
    ) -Body {
        $found = @(Find-AutoOSClaudeSessions)
        Assert-Equal $found.Count 1
        Assert-Equal $found[0].cwd 'C:\work\proj\scratch'
    }
}

Test-Case 'max_sessions keeps the most recently active, not an arbitrary slice' {
    Use-ClaudeFixture -LiveCount 3 -Sessions @(
        @{ Cwd = 'C:\work\a'; Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 30 }
        @{ Cwd = 'C:\work\b'; Uuid = '22222222-2222-2222-2222-222222222222'; AgeMinutes = 2 }
        @{ Cwd = 'C:\work\c'; Uuid = '33333333-3333-3333-3333-333333333333'; AgeMinutes = 10 }
    ) -Body {
        $config = Get-AutoOSClaudeConfig
        $config['max_sessions'] = 2
        $found = @(Find-AutoOSClaudeSessions -Config $config)
        Assert-Equal $found.Count 2
        Assert-Equal (($found | ForEach-Object { $_.name }) -join ',') 'b,c'
    }
}

Test-Case 'Save-AutoOSClaudeSnapshot records the sessions it is given' {
    # It used to ignore its argument and re-derive from the process table, which
    # is what made the hook path a silent no-op that wrote "sessions": [].
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $file = Join-Path $tmp 'state.json'
    try {
        $mine = @([pscustomobject]@{
            session_uuid = '44444444-4444-4444-4444-444444444444'
            name = 'given'; cwd = 'C:\work\given'; remote_control = $false; last_active = 1
        })
        [void](Save-AutoOSClaudeSnapshot -Sessions $mine -Path $file)
        $doc = Get-Content $file -Raw | ConvertFrom-Json
        Assert-Equal @($doc.sessions).Count 1
        Assert-Equal $doc.sessions[0].name 'given'
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'a snapshot with nothing live never clobbers a good one' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $file = Join-Path $tmp 'state.json'
    try {
        $mine = @([pscustomobject]@{
            session_uuid = '44444444-4444-4444-4444-444444444444'
            name = 'kept'; cwd = 'C:\work\kept'; remote_control = $false; last_active = 1
        })
        [void](Save-AutoOSClaudeSnapshot -Sessions $mine -Path $file)
        # The timer fires again mid-boot, before anything is up.
        [void](Save-AutoOSClaudeSnapshot -Sessions @() -Path $file)
        $doc = Get-Content $file -Raw | ConvertFrom-Json
        Assert-Equal @($doc.sessions).Count 1
        Assert-Equal $doc.sessions[0].name 'kept'
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'remote_control=never strips --rc from a session recorded with it' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $file = Join-Path $tmp 'state.json'
    try {
        $session = @([pscustomobject]@{
            session_uuid = '55555555-5555-5555-5555-555555555555'
            name = 'alpha'; cwd = 'C:\work\alpha'; remote_control = $true; last_active = 1
        })
        [void](Save-AutoOSClaudeSnapshot -Sessions $session -Path $file)
        $config = Get-AutoOSClaudeConfig
        $config['remote_control'] = 'never'
        $plan = @(Get-AutoOSClaudeRestorePlan -Config $config -Path $file)
        Assert-Equal $plan.Count 1
        Assert-Equal $plan[0].Action 'START'
        Assert-Equal $plan[0].RemoteControl $false
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'a session with no recorded id is skipped with a reason, not started' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $file = Join-Path $tmp 'state.json'
    try {
        $doc = [pscustomobject]@{
            version = 2; captured_at = 1; captured_at_iso = 'x'
            sessions = @([pscustomobject]@{ session_uuid = ''; name = 'broken'; cwd = 'C:\work\broken'; remote_control = $false })
        }
        [IO.File]::WriteAllText($file, ($doc | ConvertTo-Json -Depth 10))
        $plan = @(Get-AutoOSClaudeRestorePlan -Path $file)
        Assert-Equal $plan[0].Action 'SKIP'
        Assert-True ($plan[0].Reason -like '*no session id*')
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'the defaults come from the shipped example config, not a second copy' {
    # Three homes for these values (here, claude_sessions.py, the example file) is
    # how the web UI ended up writing keys nothing read.
    $example = Get-Content (Join-Path $Root 'autoos.config.example.json') -Raw | ConvertFrom-Json
    $defaults = Get-AutoOSClaudeDefaults
    foreach ($p in $example.claude_autostart.PSObject.Properties) {
        Assert-Equal $defaults[$p.Name] $p.Value
    }
}

Test-Case 'Set-AutoOSClaudeConfig merges and never drops the keys it does not set' {
    # The web card's first version replaced the whole object, silently discarding
    # fallback_cwd, fallback_name and terminal_host on every save.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $file = Join-Path $tmp 'autoos.config.json'
    try {
        $seed = [pscustomobject]@{
            version = 1; profile = 'workstation'
            answers = [pscustomobject]@{ git_user_name = 'Keep Me' }
            claude_autostart = [pscustomobject]@{ fallback_cwd = 'C:\keep'; resume_mode = 'full' }
        }
        [IO.File]::WriteAllText($file, ($seed | ConvertTo-Json -Depth 10))
        [void](Set-AutoOSClaudeConfig -Settings @{ resume_mode = 'summary'; max_sessions = 3 } -ConfigPath $file)

        $after = Get-Content $file -Raw | ConvertFrom-Json
        Assert-Equal $after.claude_autostart.resume_mode 'summary'
        Assert-Equal $after.claude_autostart.max_sessions 3
        Assert-Equal $after.claude_autostart.fallback_cwd 'C:\keep'
        Assert-Equal $after.answers.git_user_name 'Keep Me'
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Set-AutoOSClaudeConfig refuses a setting that nothing reads' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    try {
        $threw = $false
        try { [void](Set-AutoOSClaudeConfig -Settings @{ resume_prompt_mode = 'full' } -ConfigPath (Join-Path $tmp 'c.json')) }
        catch { $threw = $true }
        Assert-True $threw 'an unknown key should be rejected, not written where no reader looks'
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'the scheduled-task arguments are stable, so a second run can skip' {
    $a = Get-AutoOSClaudeTaskArguments -Action 'restore'
    $b = Get-AutoOSClaudeTaskArguments -Action 'restore'
    Assert-Equal $a $b
    Assert-True ($a -like '*-Action restore*')
    # Hidden launcher, visible terminal: the window the user needs is the one
    # wt.exe opens, not this one (ADR 0002).
    Assert-True ($a -like '*-WindowStyle Hidden*')
}

Test-Case 'an unregistered task never counts as current' {
    Assert-Equal (Test-AutoOSClaudeTaskCurrent -TaskName 'AutoOS-Claude-DoesNotExist' -Arguments 'x') $false
}

Test-Case 'restore starts nothing when there is no terminal host' {
    # Windows Terminal is what a restored TUI appears in; with none, restore has
    # to refuse rather than orphan a process nobody can see (ADR 0002).
    $config = Get-AutoOSClaudeConfig
    $config['terminal_host'] = 'herdr'   # not a Windows host, so none resolves
    Assert-True ($null -eq (Get-AutoOSClaudeTerminalHost -Config $config))
}

Test-Case 'the live-process probe answers on this machine without throwing' {
    # Principle 9: the AUTOOS_CLAUDE_LIVE_COUNT seam every test above uses must
    # not be the only path that is ever exercised.
    Assert-True ((Get-AutoOSClaudeLiveCount) -ge 0)
}

Test-Case 'the claude card, chooser and configure affordance are present in the page' {
    $html = Get-Content (Join-Path $Root 'web\index.html') -Raw
    Assert-True ($html -match 'id="cardClaudeAutostart"')
    Assert-True ($html -match 'id="cardChooserBar"')
    Assert-True ($html -match 'data-configure-card')
}

Test-Case 'the web UI introduces no inline onclick handlers' {
    $html = Get-Content (Join-Path $Root 'web\index.html') -Raw
    Assert-True (-not ($html -match 'onclick="'))
}

Test-Case 'the reduced-motion guard for the progress bar survives' {
    $html = Get-Content (Join-Path $Root 'web\index.html') -Raw
    Assert-True ($html -match 'prefers-reduced-motion:reduce\)\{\.bar\.indeterminate>div\{animation:none')
}

Test-Case 'a round-trip timestamp is read back as the date it was written' {
    # A bare [datetime]::TryParse uses the current culture, which read the ISO
    # string 2026-09-11T12:04 back as 2026-11-09 - the status screen showed a
    # snapshot two months in the future next to rows dated correctly.
    $written = (Get-Date '2026-09-11T12:04:33.0000000+02:00').ToString('o')
    $shown = Format-AutoOSTimestamp $written
    Assert-True ($shown -like '2026-09-11 *') "expected 2026-09-11, got [$shown]"
}

Test-Case 'dry run is off by default' {
    $html = Get-Content (Join-Path $Root 'web\index.html') -Raw
    Assert-True ($html -match '<input type="checkbox" id="dryRun">')
}

# ─── Verified download (Task 3) ─────────────────────────────────────────────
Describe-Group 'verified download'

function ConvertTo-AutoOSTestFileUri {
    param([string]$Path)
    ([Uri]$Path).AbsoluteUri
}

Test-Case 'a checksum mismatch fails and leaves nothing behind' {
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_dl_" + [Guid]::NewGuid().ToString('N')))).FullName
    $src = Join-Path $tmp 'src'
    Set-Content -LiteralPath $src -Value 'hello' -NoNewline
    $out = Join-Path $tmp 'out'
    $uri = ConvertTo-AutoOSTestFileUri $src
    $threw = $false
    try {
        Get-AutoOSVerifiedFile -Uri $uri -Destination $out -Sha256 ('0' * 68) | Out-Null
    } catch {
        $threw = $true
    }
    Assert-True ($threw -and -not (Test-Path -LiteralPath $out)) `
        "threw=$threw, out exists=$(Test-Path -LiteralPath $out)"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Test-Case 'a matching checksum succeeds' {
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_dl_" + [Guid]::NewGuid().ToString('N')))).FullName
    $src = Join-Path $tmp 'src'
    Set-Content -LiteralPath $src -Value 'hello' -NoNewline
    $out = Join-Path $tmp 'out'
    $uri = ConvertTo-AutoOSTestFileUri $src
    $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash
    Get-AutoOSVerifiedFile -Uri $uri -Destination $out -Sha256 $sum | Out-Null
    Assert-True ((Test-Path -LiteralPath $out) -and (Get-Item -LiteralPath $out).Length -gt 0) `
        "verified download did not produce the file"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Test-Case 'a cached, already-verified file is skipped, not refetched' {
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_dl_" + [Guid]::NewGuid().ToString('N')))).FullName
    $src = Join-Path $tmp 'src'
    Set-Content -LiteralPath $src -Value 'hello' -NoNewline
    $out = Join-Path $tmp 'out'
    $uri = ConvertTo-AutoOSTestFileUri $src
    $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash
    Get-AutoOSVerifiedFile -Uri $uri -Destination $out -Sha256 $sum | Out-Null

    # Get-AutoOSVerifiedFile reports through Write-AutoOSLine, which writes
    # straight to [Console]::Out rather than the pipeline, so the skip
    # message is captured by redirecting the real Console stream - the
    # PowerShell equivalent of the bash suite capturing fetch_verified's own
    # combined stdout+stderr.
    $sw = [IO.StringWriter]::new()
    $origOut = [Console]::Out
    [Console]::SetOut($sw)
    try {
        Get-AutoOSVerifiedFile -Uri $uri -Destination $out -Sha256 $sum | Out-Null
    } finally {
        [Console]::SetOut($origOut)
    }
    $text = $sw.ToString()
    Assert-True ($text -like '*skipped*') "expected to contain [skipped] in [$text]"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# ─── USB device enumeration and the safety guard ────────────────────────────
# Task 5 of plan 2026-09-11-installer-usb-and-rescue-profile: Assert-
# AutoOSUsbSafe is the only thing standing between the installer-USB writer
# and a live workstation disk, so this block gets the most tests and no
# shortcuts — the exact mirror of lib/linux/usb.sh's "usb safety" tests.
# Every fixture is synthetic ($env:AUTOOS_FAKE_DISKS) — this suite must
# never enumerate the real machine's disks (AGENTS.md §5). Every test name
# carries "usb" so `-Filter usb` actually reaches it.
Describe-Group 'usb safety'

function New-FakeUsbDisk {
    param(
        [int]$Number, [string]$Model, [long]$Size, [string]$BusType,
        [bool]$IsBoot = $false, [bool]$IsSystem = $false, [bool]$IsRemovable = $false,
        [string]$DriveLetter = $null
    )
    # Every field _Get-AutoOSUsbRawDisks/_ConvertTo-AutoOSUsbRecord reads is
    # present explicitly - Set-StrictMode turns a missing one into a hard
    # error rather than a silently-$null one, which is the point: an
    # incomplete fixture must fail loudly, never quietly under-test the guard.
    [pscustomobject]@{
        Number      = $Number
        Model       = $Model
        Size        = $Size
        BusType     = $BusType
        IsBoot      = $IsBoot
        IsSystem    = $IsSystem
        IsRemovable = $IsRemovable
        Partitions  = @([pscustomobject]@{ DriveLetter = $DriveLetter })
    }
}

# The internal boot disk present in every fixture below, distinct from the
# "Disk 5" target every other fixture uses — the same shape the plan brief
# measured on the human partner's real machine (five non-removable internal
# disks alongside the one real USB target). Every fixture except
# root_is_boot_disk names a DIFFERENT DeviceId to Assert-AutoOSUsbSafe than
# this one, so its presence is what proves the guard looks past "some system
# disk exists" to "is THIS the system disk".
function New-FakeBootDisk {
    New-FakeUsbDisk -Number 0 -Model 'Boot SSD' -Size 256060514304 -BusType 'NVMe' `
        -IsBoot $true -IsSystem $true -DriveLetter 'C'
}

function Get-FakeUsbDisksJson {
    param([Parameter(Mandatory)][string]$Fixture)

    $bootDisk = New-FakeBootDisk
    $disks = switch ($Fixture) {
        # The most dangerous case: disk 0 itself is the system/boot disk.
        'root_is_boot_disk' { @($bootDisk) }

        # A second internal disk, distinct from the boot disk: not
        # removable, not USB/SCSI. Must be refused even though it does not
        # hold Windows.
        'internal_nvme' {
            @($bootDisk, (New-FakeUsbDisk -Number 1 -Model 'WD Black SN850' `
                -Size 1000204886016 -BusType 'NVMe'))
        }

        # Removable, USB, but has an assigned drive letter — must refuse
        # until the caller unmounts it.
        'usb_mounted' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'SanDisk Ultra' `
                -Size 32017047552 -BusType 'USB' -IsRemovable $true -DriveLetter 'E'))
        }

        # Removable, USB, unmounted — but smaller than the image the caller
        # asks for ($env:AUTOOS_IMAGE_BYTES in the test).
        'tiny_stick' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Kingston DataTraveler' `
                -Size 4000000000 -BusType 'USB' -IsRemovable $true))
        }

        # The real target, measured on the human partner's machine:
        # Disk 5, "Intenso Office Line", BusType USB, IsSystem False,
        # IsBoot False, 31,437,766,656 bytes, unmounted.
        'good_stick' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Intenso Office Line' `
                -Size 31437766656 -BusType 'USB' -IsRemovable $true))
        }

        # Finding A10: a USB SSD (commonly behind a UAS/UASP bridge)
        # enumerates as BusType 'SCSI' with IsRemovable=$false. Must still be
        # surfaced by Get-AutoOSUsbDevice - BusType alone is the signal.
        'usb_ssd_fixed' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Samsung T7 (USB enclosure)' `
                -Size 2000398934016 -BusType 'SCSI' -IsRemovable $false))
        }

        default { throw "unknown fake-usb fixture: $Fixture" }
    }
    ConvertTo-Json -InputObject @($disks) -Depth 6
}

function Invoke-WithFakeUsbEnv {
    # Sets the AUTOOS_FAKE_* environment variables for the duration of
    # $Body, then always clears them - $env: vars persist for the whole
    # session (unlike bash's `VAR=x cmd` subshell scoping), so a test that
    # forgot to clean up would silently contaminate every test after it.
    param(
        [string]$Fixture,
        [Nullable[long]]$ImageBytes = $null,
        [scriptblock]$Body
    )
    $env:AUTOOS_FAKE_DISKS = Get-FakeUsbDisksJson -Fixture $Fixture
    if ($null -ne $ImageBytes) { $env:AUTOOS_IMAGE_BYTES = [string]$ImageBytes }
    try {
        & $Body
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_DISKS -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_IMAGE_BYTES -ErrorAction SilentlyContinue
    }
}

Test-Case 'Assert-AutoOSUsbSafe refuses the disk holding the system volume' {
    Invoke-WithFakeUsbEnv -Fixture 'root_is_boot_disk' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE0' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*system disk*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'Assert-AutoOSUsbSafe refuses a non-removable internal disk' {
    Invoke-WithFakeUsbEnv -Fixture 'internal_nvme' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE1' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*not removable*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'Assert-AutoOSUsbSafe refuses a usb disk with a mounted volume' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_mounted' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*mounted*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'Assert-AutoOSUsbSafe refuses a usb stick smaller than the image' {
    Invoke-WithFakeUsbEnv -Fixture 'tiny_stick' -ImageBytes 8000000000 -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*too small*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'Assert-AutoOSUsbSafe accepts a real removable usb stick that is big enough' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $result = Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5'
        Assert-True ($null -ne $result -and $result.DeviceId -eq '\\.\PHYSICALDRIVE5') 'rejected a valid target'
    }
}

Test-Case 'Get-AutoOSUsbDevice still offers a USB SSD reporting as fixed (A10)' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_ssd_fixed' -Body {
        $devices = @(Get-AutoOSUsbDevice)
        Assert-Contains ($devices | ForEach-Object { $_.DeviceId }) '\\.\PHYSICALDRIVE5'
    }
}

Test-Case 'Assert-AutoOSElevated refuses to plan a usb write without admin (B10)' {
    $env:AUTOOS_FAKE_ELEVATED = '0'
    try {
        $threw = $false; $msg = ''
        try { Assert-AutoOSElevated | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*Administrator*') "threw=$threw msg=[$msg]"
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_ELEVATED -ErrorAction SilentlyContinue
    }
}

Test-Case 'Assert-AutoOSElevated is satisfied for a usb write when already admin (B10)' {
    $env:AUTOOS_FAKE_ELEVATED = '1'
    try {
        $result = Assert-AutoOSElevated
        Assert-True $result.IsElevated 'refused an already-elevated session'
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_ELEVATED -ErrorAction SilentlyContinue
    }
}

# ─── Static analysis ────────────────────────────────────────────────────────
Describe-Group 'static analysis'

Test-Case 'PSScriptAnalyzer is clean' {
    if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
        Skip 'PSScriptAnalyzer not installed'
        return
    }
    $files = @(Get-ChildItem -Path (Join-Path $Root 'lib\windows') -Filter *.psm1) +
             @(Get-Item $setup)
    $issues = @()
    $ruleCrashes = @()
    foreach ($f in $files) {
        # PSUseSingularNouns is excluded deliberately: these functions return
        # collections, and Get-AutoOSAvailableComponent(s) reads worse singular.
        # Everything else, including PSAvoidAssignmentToAutomaticVariable and
        # PSReviewUnusedParameter, is treated as a real failure.
        #
        # -ErrorVariable, not $ErrorActionPreference='Stop': the analyzer emits a
        # non-terminating RULE_ERROR when one of its own rules throws internally
        # (it does so here on a loaded module, with no single rule reproducing it).
        # Diagnostics still come through, so they stay the pass criterion - but a
        # crashed rule is reported rather than silently swallowed.
        $analyzerErrors = $null
        $issues += Invoke-ScriptAnalyzer -Path $f.FullName -Severity Error, Warning `
                   -ExcludeRule PSUseShouldProcessForStateChangingFunctions,
                                PSAvoidUsingWriteHost,
                                PSUseSingularNouns `
                   -ErrorVariable analyzerErrors -ErrorAction SilentlyContinue
        if ($analyzerErrors) { $ruleCrashes += $f.Name }
    }
    if ($issues.Count -gt 0) {
        throw (($issues | Select-Object -First 8 |
                ForEach-Object { "$($_.ScriptName):$($_.Line) $($_.RuleName)" }) -join '; ')
    }
    Pass
    if ($ruleCrashes.Count -gt 0) {
        Write-Host ("      " + (C ("note: a PSScriptAnalyzer rule threw internally on " +
                    ($ruleCrashes -join ', ') + " - diagnostics were still collected") '38;5;179'))
    }
}

Test-Case 'every PowerShell file has a UTF-8 BOM' {
    # Windows PowerShell 5.1 decodes .ps1/.psm1 as ANSI without one, which turns
    # every box-drawing character into a parse error.
    $missing = @()
    foreach ($f in Get-ChildItem -Path $Root -Include *.ps1, *.psm1 -Recurse -File) {
        if ($f.FullName -match '\\\.git\\') { continue }
        $bytes = [IO.File]::ReadAllBytes($f.FullName)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            $missing += $f.Name
        }
    }
    Assert-Equal ($missing -join ',') ''
}

# ─── Summary ────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host (C ('-' * 56) '2;38;5;245')
Write-Host ("  " + (C "passed $script:Pass" '38;5;71') +
            "   " + (C "failed $script:Fail" $(if ($script:Fail) { '1;38;5;167' } else { '2;38;5;245' })) +
            "   " + (C "skipped $script:Skip" '2;38;5;245'))
if ($script:Fail) {
    Write-Host ''
    Write-Host '  failures:'
    foreach ($f in $script:Failures) { Write-Host "    - $f" }
    exit 1
}
exit 0
