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

# ─── Shared LLM model catalogue (single source of truth) ────────────────
Describe-Group 'llm models'

Test-Case 'llm-models.json is valid and has unique ids' {
    $doc = Get-Content (Join-Path $Root 'catalog\llm-models.json') -Raw | ConvertFrom-Json
    $ids = @($doc.models | ForEach-Object { $_.id })
    Assert-True ($ids.Count -ge 20) "expected >= 20 models, got $($ids.Count)"
    $dupes = @($ids | Group-Object | Where-Object { $_.Count -gt 1 } | ForEach-Object { $_.Name })
    @(Assert-True ($dupes.Count -eq 0) ("duplicate model ids: " + ($dupes -join ', ')))
    foreach ($m in $doc.models) {
        $hasOr = $m.PSObject.Properties.Name.Contains('openrouter_id') -and $m.openrouter_id
        $hasDirect = $m.PSObject.Properties.Name.Contains('direct') -and $m.direct
        if (-not $hasOr -and -not $hasDirect) { throw "model '$($m.id)' has neither openrouter_id nor direct" }
    }
}

Test-Case 'the windows installer projects the shared catalogue' {
    $src = Get-Content (Join-Path $Root 'lib\windows\AutoOS.Install.psm1') -Raw
    Assert-True ($src -match 'catalog.llm-models\.json') 'shared llm-models.json is not loaded'
    foreach ($witness in @('REPO_MODELS', 'repoById', 'Get-OpenRouterModelEntry', '_profile_for')) {
        Assert-True ($src.Contains($witness)) "'$witness' projector missing"
    }
    Assert-True (($src -split '_profile_for\(').Count -ge 20) 'expected >= 19 profile projections'
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

Test-Case 'discovery skips sessions whose working directory does not exist' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    $real = Join-Path $tmp 'real'
    [void](New-Item -ItemType Directory -Path $real -Force)
    $ghost = Join-Path $tmp 'ghost'
    try {
        Use-ClaudeFixture -Sessions @(
            @{ Cwd = $real;  Uuid = '11111111-1111-1111-1111-111111111111'; AgeMinutes = 2 }
            @{ Cwd = $ghost; Uuid = '22222222-2222-2222-2222-222222222222'; AgeMinutes = 2 }
        ) -Body {
            $env:AUTOOS_CLAUDE_TEST_EXISTENCE = '1'
            try {
                $found = @(Find-AutoOSClaudeSessions)
                Assert-Equal $found.Count 1
                Assert-Equal $found[0].session_uuid '11111111-1111-1111-1111-111111111111'
            } finally {
                Remove-Item Env:\AUTOOS_CLAUDE_TEST_EXISTENCE -ErrorAction SilentlyContinue
            }
        }
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
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

function Start-AutoOSTestHttpServer {
    # A throwaway HTTP server on 127.0.0.1 and an OS-assigned port, serving
    # GET of the files under -Directory. Mirror of tests/run-tests.sh's
    # _start_test_http_server, but written in PowerShell on a TcpListener
    # inside a background job: the first version launched python via
    # Start-Process and never got a port back on the windows-latest CI
    # runner. No python, no HttpListener URL ACL, nothing to install.
    # Returns @{ Job; Port }; the caller must Stop-AutoOSTestHttpServer it.
    param([Parameter(Mandatory)][string]$Directory)
    $portFile = Join-Path ([IO.Path]::GetTempPath()) ('aos_port_' + [Guid]::NewGuid().ToString('N'))
    $job = Start-Job -ArgumentList $Directory, $portFile -ScriptBlock {
        param($dir, $portFile)
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        [IO.File]::WriteAllText($portFile, [string]$listener.LocalEndpoint.Port)
        while ($true) {
            $client = $listener.AcceptTcpClient()
            try {
                $stream = $client.GetStream()
                $reader = [IO.StreamReader]::new($stream)
                $request = $reader.ReadLine()
                while ($true) { $h = $reader.ReadLine(); if ($null -eq $h -or $h -eq '') { break } }
                $status = '404 Not Found'; $body = [byte[]]::new(0)
                if ($request -match '^GET\s+(\S+)') {
                    $path = [Uri]::UnescapeDataString(($Matches[1] -split '\?')[0]).TrimStart('/').Replace('/', '\')
                    $full = Join-Path $dir $path
                    if ((Test-Path -LiteralPath $full -PathType Container)) { $full = Join-Path $full 'index.html' }
                    if (Test-Path -LiteralPath $full -PathType Leaf) {
                        $body = [IO.File]::ReadAllBytes($full); $status = '200 OK'
                    }
                }
                $head = [Text.Encoding]::ASCII.GetBytes("HTTP/1.0 $status`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n")
                $stream.Write($head, 0, $head.Length)
                if ($body.Length) { $stream.Write($body, 0, $body.Length) }
                $stream.Flush()
            } catch {
                # A client that hangs up mid-request is not the server's problem.
                Write-Verbose "test http server: $($_.Exception.Message)"
            } finally {
                $client.Close()
            }
        }
    }
    $port = $null
    for ($i = 0; $i -lt 100; $i++) {
        if ((Test-Path -LiteralPath $portFile) -and (Get-Item -LiteralPath $portFile).Length -gt 0) {
            $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim(); break
        }
        if ($job.State -in @('Failed', 'Completed', 'Stopped')) { break }
        Start-Sleep -Milliseconds 100
    }
    Remove-Item -LiteralPath $portFile -Force -ErrorAction SilentlyContinue
    if (-not $port) {
        $why = (Receive-Job $job -ErrorAction SilentlyContinue | Out-String).Trim()
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        throw "test http server did not report a port (job state $($job.State)) $why"
    }
    @{ Job = $job; Port = $port }
}

function Stop-AutoOSTestHttpServer {
    param($Server)
    if ($Server -and $Server.Job) { Remove-Job -Job $Server.Job -Force -ErrorAction SilentlyContinue }
}

Test-Case 'verified download: an http URL is streamed to disk through curl.exe and verifies (http)' {
    # Windows PowerShell 5.1's Invoke-WebRequest -OutFile buffers the whole
    # response in memory - unusable for a 6 GB ISO (found on the first real
    # run, 2026-09-17). Get-AutoOSRawDownload must take the curl.exe route
    # for http(s); this proves it against a loopback server, no internet.
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_http_" + [Guid]::NewGuid().ToString('N')))).FullName
    $srv = Start-AutoOSTestHttpServer -Directory $tmp
    try {
        $src = Join-Path $tmp 'src.bin'
        [IO.File]::WriteAllBytes($src, [byte[]](1..200000 | ForEach-Object { $_ % 251 }))
        $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash
        $out = Join-Path $tmp 'out.bin'
        Get-AutoOSVerifiedFile -Uri "http://127.0.0.1:$($srv.Port)/src.bin" -Destination $out -Sha256 $sum | Out-Null
        Assert-True ((Test-Path -LiteralPath $out) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $out).Hash -eq $sum)) 'downloaded file missing or digest differs'
    } finally {
        Stop-AutoOSTestHttpServer $srv
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'verified download: an http 404 is a transport failure that leaves no .part behind (http)' {
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_http_" + [Guid]::NewGuid().ToString('N')))).FullName
    $srv = Start-AutoOSTestHttpServer -Directory $tmp
    try {
        $out = Join-Path $tmp 'out.bin'
        $threw = $false; $msg = ''
        try { Get-AutoOSVerifiedFile -Uri "http://127.0.0.1:$($srv.Port)/does-not-exist.bin" -Destination $out -Sha256 ('0' * 64) | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*transport failure*' -and -not (Test-Path -LiteralPath "$out.part") -and -not (Test-Path -LiteralPath $out)) "threw=$threw msg=[$msg]"
    } finally {
        Stop-AutoOSTestHttpServer $srv
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'verified download: an uncached (FILE_FLAG_NO_BUFFERING) read hashes identically to a cached one at every sector and chunk boundary (uncached)' {
    # No-buffering reads must be sector-aligned in buffer and count, so the
    # classic bugs live exactly at these sizes: an empty file, one byte,
    # either side of a 512/4096 sector, and either side of a chunk. (It does
    # NOT exercise the tail clamp: removing the clamp still passes, because
    # ReadFile returns only valid bytes at end of file - see the C# comment.)
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_unc_" + [Guid]::NewGuid().ToString('N')))).FullName
    try {
        $rng = [System.Random]::new(4242)
        $bad = @()
        foreach ($n in @(0, 1, 511, 512, 513, 4095, 4096, 4097, 1048575, 1048576, 1048577, 3150001)) {
            $p = Join-Path $tmp "f$n.bin"
            $buf = [byte[]]::new($n); $rng.NextBytes($buf)
            [IO.File]::WriteAllBytes($p, $buf)
            $want = Get-AutoOSFileSha256 -Path $p
            if ((Get-AutoOSUncachedFileSha256 -Path $p) -ne $want) { $bad += "$n(1MiB chunk)" }
            if ((Get-AutoOSUncachedFileSha256 -Path $p -ChunkSize 4096) -ne $want) { $bad += "$n(4KiB chunk)" }
        }
        $refused = $false
        try { Get-AutoOSUncachedFileSha256 -Path (Join-Path $tmp 'f4096.bin') -ChunkSize 1000 | Out-Null } catch { $refused = $true }
        Assert-True ($bad.Count -eq 0 -and $refused) "mismatched sizes: $($bad -join ', '); unaligned chunk refused: $refused"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'verified download: hashes and verifies a file under Windows PowerShell 5.1, the engine setup.ps1 actually runs in (5.1)' {
    # This suite runs under pwsh 7; setup.ps1 is launched with powershell.exe
    # 5.1 (elevated launches in particular). The first real 6 GB download on
    # 2026-09-17 completed and then failed in the 5.1 process with
    # "Get-FileHash is not recognized" - a path no test had ever run under
    # 5.1. The hash now goes through .NET; this proves the whole
    # download-and-verify path under the real engine.
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_51_" + [Guid]::NewGuid().ToString('N')))).FullName
    try {
        $src = Join-Path $tmp 'src'; Set-Content -LiteralPath $src -Value 'hello' -NoNewline
        $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash
        $out = Join-Path $tmp 'out'
        $probe = Join-Path $tmp 'probe.ps1'
        $mod = Join-Path $Root 'lib\windows\AutoOS.Download.psm1'
        $lines = @(
            'Set-StrictMode -Version Latest',
            "`$ErrorActionPreference = 'Stop'",
            "Import-Module '$mod' -DisableNameChecking -Force",
            "Get-AutoOSVerifiedFile -Uri '$(([Uri]$src).AbsoluteUri)' -Destination '$out' -Sha256 '$sum' | Out-Null",
            "'VERIFIED-51 ' + (Get-AutoOSFileSha256 -Path '$out')"
        )
        [IO.File]::WriteAllText($probe, ($lines -join "`r`n") + "`r`n", [Text.Encoding]::ASCII)
        $res = (& powershell -NoProfile -ExecutionPolicy Bypass -File $probe 2>&1) -join "`n"
        Assert-True ($LASTEXITCODE -eq 0 -and $res -like "*VERIFIED-51 $($sum.ToLowerInvariant())*" -and (Test-Path -LiteralPath $out)) "exit=$LASTEXITCODE out=$res"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'verified download: gpg stderr chatter (gpg-agent directory created) is not a failure, only the exit code is (gpg)' {
    # The first real run on 2026-09-17 died here: gpg prints "gpg-agent[n]:
    # directory '...' created" to stderr on every fresh GNUPGHOME, and under
    # the module's $ErrorActionPreference = 'Stop' that became a terminating
    # NativeCommandError before any signature was checked (handoff L6). A
    # fake gpg.cmd on PATH that chatters on stderr and exits 0 reproduces it
    # without a keyserver or a real key.
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_gpg_" + [Guid]::NewGuid().ToString('N')))).FullName
    $shim = Join-Path $tmp 'gpg.cmd'
    [IO.File]::WriteAllText($shim, "@echo gpg-agent[1234]: directory 'private-keys-v1.d' created 1>&2`r`n@exit /b 0`r`n")
    $src = Join-Path $tmp 'src'; Set-Content -LiteralPath $src -Value 'hello' -NoNewline
    $sig = Join-Path $tmp 'src.gpg'; Set-Content -LiteralPath $sig -Value 'not-a-real-signature' -NoNewline
    $prevPath = $env:Path
    $env:Path = "$tmp;$prevPath"
    try {
        $ok = Test-AutoOSGpgSignature -Path $src -SignatureUri (ConvertTo-AutoOSTestFileUri $sig) -Fingerprint 'ABCDEF0123456789'
        Assert-True ($ok -eq $true) "expected `$true from a gpg that exits 0 despite stderr chatter, got [$ok]"
    } finally {
        $env:Path = $prevPath
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'verified download: a signature check that throws leaves no .part behind (gpg)' {
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_gpg_" + [Guid]::NewGuid().ToString('N')))).FullName
    $src = Join-Path $tmp 'src'; Set-Content -LiteralPath $src -Value 'hello' -NoNewline
    $out = Join-Path $tmp 'out'
    $prevPath = $env:Path
    # An empty PATH dir first and no gpg anywhere: Get-Command gpg fails and
    # Test-AutoOSGpgSignature throws 'gpg not found' - the throwing path.
    $env:Path = (Join-Path $tmp 'nothing')
    try {
        $threw = $false; $msg = ''
        try { Get-AutoOSVerifiedFile -Uri (ConvertTo-AutoOSTestFileUri $src) -Destination $out -SignatureUri 'file:///nonexistent.sig' -GpgFingerprint 'ABCDEF0123456789' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*signature verification failed*' -and -not (Test-Path -LiteralPath "$out.part") -and -not (Test-Path -LiteralPath $out)) "threw=$threw msg=[$msg] part=$(Test-Path -LiteralPath "$out.part")"
    } finally {
        $env:Path = $prevPath
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

# ─── image_resolve (Task 4B) ────────────────────────────────────────────────
# Mirrors lib/linux/download.sh's own "image_resolve" test block one-for-one
# (same fixtures under tests\helpers\image_index_fixtures, same scenarios).
# file:// is fine here (unlike the bash suite's loopback-server workaround):
# Resolve-AutoOSImageUrl does its own string-joining of relative hrefs
# against the page's own directory URI, so a fixture-tree file:// URI
# behaves identically to a live server's for every case tested below - no
# real network is ever touched.
Describe-Group 'image_resolve'

function ConvertTo-AutoOSTestDirUri {
    param([string]$Path)
    ([Uri]($Path.TrimEnd('\') + '\')).AbsoluteUri
}

$imgFixtureRoot = Join-Path $Root 'tests\helpers\image_index_fixtures'

function New-AutoOSImageTestCatalog {
    param(
        [string]$Id, [string]$FixtureSubdir, [string]$FileRegex,
        [string]$Sums = 'SHA256SUMS', [string]$Sig = '-', [string]$Leaf = ''
    )
    $indexUri = ConvertTo-AutoOSTestDirUri (Join-Path $imgFixtureRoot $FixtureSubdir)
    $path = Join-Path ([IO.Path]::GetTempPath()) ('aos_img_cat_' + [Guid]::NewGuid().ToString('N') + '.json')
    $entry = @{ id = $Id; index = $indexUri; file = $FileRegex; sums = $Sums; sig = $Sig }
    # Omitted (default '') the same way every pre-existing caller above uses
    # it: left out of the entry entirely, mirroring the bash suite's
    # _image_resolve_test_catalog and the "absent means ''" catalog contract.
    if ($Leaf) { $entry['leaf'] = $Leaf }
    @{ images = @($entry) } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
    $path
}

Test-Case 'image_resolve: an LTS entry picks the current LTS release, never an interim one' {
    $cat = New-AutoOSImageTestCatalog -Id 'ubuntu-desktop-lts' -FixtureSubdir 'ubuntu_releases' `
        -FileRegex 'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' -Sums 'SHA256SUMS' -Sig 'SHA256SUMS.gpg'
    try {
        $r = Resolve-AutoOSImageUrl -ImageId 'ubuntu-desktop-lts' -CatalogPath $cat
        # One Assert-True per Test-Case, same convention as every other test
        # in this suite (Pass/Fail print $script:Current, so more than one
        # assertion per case would double-count and re-print the name).
        $ok = ($r.File -eq 'ubuntu-26.04.1-desktop-amd64.iso') -and
            ($r.Url -like '*26.04.1/ubuntu-26.04.1-desktop-amd64.iso') -and
            ($r.Sig -like '*26.04.1/SHA256SUMS.gpg') -and
            ($r.Url -notlike '*25.10*') -and ($r.Url -notlike '*24.04.5*')
        Assert-True $ok "file=$($r.File) url=$($r.Url) sig=$($r.Sig)"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case "image_resolve: Debian's current/ symlink shape resolves with no directory recursion" {
    $cat = New-AutoOSImageTestCatalog -Id 'debian-netinst-stable' -FixtureSubdir 'debian_iso_cd' `
        -FileRegex 'debian-[0-9]+\.[0-9]+\.[0-9]+-amd64-netinst\.iso$' -Sums 'SHA256SUMS' -Sig 'SHA256SUMS.sign'
    try {
        $r = Resolve-AutoOSImageUrl -ImageId 'debian-netinst-stable' -CatalogPath $cat
        $ok = ($r.File -eq 'debian-13.1.0-amd64-netinst.iso') -and ($r.Sig -like '*debian_iso_cd/SHA256SUMS.sign')
        Assert-True $ok "file=$($r.File) sig=$($r.Sig)"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case 'image_resolve: a pattern matching nothing throws loudly, never guesses' {
    $cat = New-AutoOSImageTestCatalog -Id 'nothing-here' -FixtureSubdir 'no_match' -FileRegex 'nonexistent-[0-9]+\.iso$'
    try {
        $threw = $false; $msg = ''
        try { Resolve-AutoOSImageUrl -ImageId 'nothing-here' -CatalogPath $cat | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*matched nothing*') "threw=$threw msg=$msg"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case 'image_resolve: a pattern matching several files that differ by more than a version throws, never a silent first-match' {
    # Same version, different arch - a regex broader than its author meant.
    # No ordering rule applies here, unlike the point-release case below.
    $cat = New-AutoOSImageTestCatalog -Id 'ambiguous' -FixtureSubdir 'multiple_match' `
        -FileRegex 'debian-[0-9]+\.[0-9]+\.[0-9]+-(amd64|arm64)-netinst\.iso$'
    try {
        $threw = $false; $msg = ''
        try { Resolve-AutoOSImageUrl -ImageId 'ambiguous' -CatalogPath $cat | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*matched multiple*' -and $msg -like '*debian-13.2.0-amd64-netinst.iso*' -and $msg -like '*debian-13.2.0-arm64-netinst.iso*') `
            "threw=$threw msg=$msg"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case 'image_resolve: a point release listed beside its .0 release in one directory resolves to the highest version (live 26.04.1 shape)' {
    # Found on the first real run, 2026-09-17: releases.ubuntu.com/26.04.1/
    # lists ubuntu-26.04-desktop-amd64.iso AND ubuntu-26.04.1-desktop-amd64.iso.
    # Names identical except for one dotted version number are the same
    # artefact at different point releases - the highest wins (26.04 < 26.04.1).
    $cat = New-AutoOSImageTestCatalog -Id 'ubuntu-desktop-lts' -FixtureSubdir 'ubuntu_point_release' `
        -FileRegex 'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' -Sums 'SHA256SUMS' -Sig 'SHA256SUMS.gpg'
    try {
        $r = Resolve-AutoOSImageUrl -ImageId 'ubuntu-desktop-lts' -CatalogPath $cat
        Assert-True ($r.File -eq 'ubuntu-26.04.1-desktop-amd64.iso' -and $r.Url -like '*/ubuntu_point_release/ubuntu-26.04.1-desktop-amd64.iso' -and $r.Sums -like '*/SHA256SUMS') "got: $($r | ConvertTo-Json -Compress)"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case 'image_resolve: an -lts id with only interim directories present throws instead of picking one' {
    $cat = New-AutoOSImageTestCatalog -Id 'ubuntu-desktop-lts' -FixtureSubdir 'ubuntu_only_interim' `
        -FileRegex 'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' -Sums 'SHA256SUMS' -Sig 'SHA256SUMS.gpg'
    try {
        $threw = $false; $msg = ''
        try { Resolve-AutoOSImageUrl -ImageId 'ubuntu-desktop-lts' -CatalogPath $cat | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*no LTS release directory*') "threw=$threw msg=$msg"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case "image_resolve: a fedora-shaped entry resolves via 'leaf' to the ISO and CHECKSUM three directories below the chosen version dir, picking the highest of 43/44" {
    # Real upstream shape (verified 2026-09-18): releases/ lists bare-integer
    # version dirs (44/, no dot), and the ISO sits three levels below the
    # chosen one (Workstation/x86_64/iso/) - 'leaf' is what lets a single
    # recursion step land there directly.
    $cat = New-AutoOSImageTestCatalog -Id 'fedora-workstation' -FixtureSubdir 'fedora_releases' `
        -FileRegex 'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' `
        -Sums 'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' -Sig '-' -Leaf 'Workstation/x86_64/iso/'
    try {
        $r = Resolve-AutoOSImageUrl -ImageId 'fedora-workstation' -CatalogPath $cat
        $ok = ($r.File -eq 'Fedora-Workstation-Live-44-1.7.x86_64.iso') -and
            ($r.Url -like '*/44/Workstation/x86_64/iso/Fedora-Workstation-Live-44-1.7.x86_64.iso') -and
            ($r.Sums -like '*/44/Workstation/x86_64/iso/Fedora-Workstation-44-1.7-x86_64-CHECKSUM') -and
            ($r.Url -notlike '*/43/*') -and ($r.Url -notlike '*/test/*')
        Assert-True $ok "file=$($r.File) url=$($r.Url) sums=$($r.Sums)"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case "image_resolve: a bare-integer version directory (Fedora's '44/') is never LTS-eligible - it has no .04, so it must be ineligible" {
    $cat = New-AutoOSImageTestCatalog -Id 'fedora-workstation-lts' -FixtureSubdir 'fedora_releases' `
        -FileRegex 'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' `
        -Sums 'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' -Sig '-'
    try {
        $threw = $false; $msg = ''
        try { Resolve-AutoOSImageUrl -ImageId 'fedora-workstation-lts' -CatalogPath $cat | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*no LTS release directory*') "threw=$threw msg=$msg"
    } finally { Remove-Item -Force $cat -ErrorAction SilentlyContinue }
}

Test-Case 'image_resolve: custom-url is a specific refusal, not a crash' {
    $threw = $false; $msg = ''
    try { Resolve-AutoOSImageUrl -ImageId 'custom-url' | Out-Null }
    catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -like '*UI affordance*') "threw=$threw msg=$msg"
}

Test-Case 'image_resolve: custom-local is a specific refusal, not a crash' {
    $threw = $false; $msg = ''
    try { Resolve-AutoOSImageUrl -ImageId 'custom-local' | Out-Null }
    catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -like '*UI affordance*') "threw=$threw msg=$msg"
}

Test-Case 'image_resolve: an unknown image id throws a clear error, not a crash' {
    $threw = $false; $msg = ''
    try { Resolve-AutoOSImageUrl -ImageId 'totally-not-a-real-image-id' -CatalogPath (Join-Path $Root 'catalog\images.json') | Out-Null }
    catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -like '*no catalog entry*') "threw=$threw msg=$msg"
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
        [string]$DriveLetter = $null,
        # FileSystem/IsReadOnly (Task 6): what Assert-AutoOSUsbSafe's
        # 'MountedFat32Writable' mode (the uefi-copy engine's guard mode)
        # checks — the exact mirror of tests/helpers/fake_usb.py's
        # fstype/ro fields.
        [string]$FileSystem = '',
        [bool]$IsReadOnly = $false
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
        Partitions  = @([pscustomobject]@{
            DriveLetter = $DriveLetter
            FileSystem  = $FileSystem
            IsReadOnly  = $IsReadOnly
        })
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

        # Task 6 (B16): the uefi-copy engine writes onto an EXISTING
        # mounted FAT32 volume rather than the raw disk, so
        # Assert-AutoOSUsbSafe's 'MountedFat32Writable' mode wants the
        # opposite of every fixture above — mounted, not unmounted. This is
        # the human partner's real stick shape: Disk 5, FAT32, writable,
        # already mounted at J:.
        'usb_fat32_mounted' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Intenso Office Line' `
                -Size 31437766656 -BusType 'USB' -IsRemovable $true -DriveLetter 'J' `
                -FileSystem 'FAT32' -IsReadOnly $false))
        }

        # Same target, but its mounted volume is NTFS, not FAT32 — the
        # guard must name the actual filesystem so the refusal is actionable.
        'usb_wrong_fs_mounted' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Intenso Office Line' `
                -Size 31437766656 -BusType 'USB' -IsRemovable $true -DriveLetter 'J' `
                -FileSystem 'NTFS' -IsReadOnly $false))
        }

        # FAT32, mounted, but read-only — uefi-copy needs to write to it.
        'usb_fat32_readonly' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Intenso Office Line' `
                -Size 31437766656 -BusType 'USB' -IsRemovable $true -DriveLetter 'J' `
                -FileSystem 'FAT32' -IsReadOnly $true))
        }

        # Finding F2': mounted, FAT32, writable, on a real USB/removable
        # disk that is neither IsBoot nor IsSystem — every other check in
        # MountedFat32Writable mode accepts this, but its drive letter is
        # the live system drive, the Windows analogue of a USB-hosted
        # /boot/efi (lib/linux/usb.sh's own F2' fixture).
        'usb_system_drive_mounted' {
            @($bootDisk, (New-FakeUsbDisk -Number 5 -Model 'Intenso Office Line' `
                -Size 31437766656 -BusType 'USB' -IsRemovable $true -DriveLetter $env:SystemDrive.TrimEnd(':') `
                -FileSystem 'FAT32' -IsReadOnly $false))
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

function Invoke-AutoOSCapturedConsole {
    # Write-AutoOSLine writes straight to [Console]::Out rather than the
    # pipeline (see the verified-download tests above), so a function's own
    # progress/refusal lines are captured by redirecting the real Console
    # stream for the duration of -Body. Returns the captured text; -Body's
    # own exception, if any, is rethrown after the stream is restored.
    param([scriptblock]$Body)
    # Both streams: Write-AutoOSLine -Level error goes to [Console]::Error.
    $sw = [IO.StringWriter]::new()
    $origOut = [Console]::Out
    $origErr = [Console]::Error
    [Console]::SetOut($sw)
    [Console]::SetError($sw)
    try { & $Body | Out-Null } finally { [Console]::SetOut($origOut); [Console]::SetError($origErr) }
    $sw.ToString()
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

# ─── Engine-aware guard modes (Task 6, B16) ─────────────────────────────────
# The real-hardware bug that started Task 6: Assert-AutoOSUsbSafe correctly
# refused every internal disk, then refused the legitimate USB stick too,
# because the only mode it knew was "must be unmounted" — wrong for
# uefi-copy, which writes onto a volume that is ALREADY mounted. The -Mode
# parameter is what fixes this; every branch gets its own fixture and test.
Test-Case 'usb: Assert-AutoOSUsbSafe (MountedFat32Writable) accepts an already-mounted writable FAT32 target' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_fat32_mounted' -Body {
        $result = Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' -Mode MountedFat32Writable
        Assert-True ($null -ne $result) 'rejected a valid uefi-copy target'
    }
}

Test-Case 'usb: Assert-AutoOSUsbSafe (MountedFat32Writable) refuses a target with nothing mounted' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' -Mode MountedFat32Writable | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*no mounted volume*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'usb: Assert-AutoOSUsbSafe (MountedFat32Writable) refuses a mounted volume that is not FAT32' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_wrong_fs_mounted' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' -Mode MountedFat32Writable | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*not FAT32*' -and $msg -like '*NTFS*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'usb: Assert-AutoOSUsbSafe (MountedFat32Writable) refuses a read-only FAT32 volume' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_fat32_readonly' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' -Mode MountedFat32Writable | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*read-only*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'usb: Assert-AutoOSUsbSafe still defaults to Unmounted mode when none is given' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_mounted' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*mounted*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'usb: Assert-AutoOSUsbSafe (MountedFat32Writable) refuses a volume mounted at the system drive, regardless of bus (finding F2-prime)' {
    Invoke-WithFakeUsbEnv -Fixture 'usb_system_drive_mounted' -Body {
        $threw = $false; $msg = ''
        try { Assert-AutoOSUsbSafe -DeviceId '\\.\PHYSICALDRIVE5' -Mode MountedFat32Writable | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*system drive*') "threw=$threw msg=[$msg]"
    }
}

# ─── The write planner (Task 6) ─────────────────────────────────────────────
# New-AutoOSUsbPlan turns (image, kind, engine, device) into the exact
# write commands, checks every compatibility/platform/lock/safety question
# first, and runs nothing — the exact mirror of lib/linux/usb.sh's
# usb_plan(). Every test name below carries "usb" so `-Filter usb` reaches it.
Test-Case 'usb: New-AutoOSUsbPlan names the device and Ventoy for a dry run' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $plan = @(New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' `
            -Engine 'ventoy' -DeviceId '\\.\PHYSICALDRIVE5' -DryRun)
        $joined = $plan -join "`n"
        Assert-True ($joined -like '*PHYSICALDRIVE5*') "device missing from plan: $joined"
        Assert-True ($joined -like '*Ventoy2Disk*') "Ventoy2Disk missing from plan: $joined"
        Assert-True ($joined -notlike '*installed*') "unexpected 'installed' in plan: $joined"
    }
}

Test-Case 'usb: New-AutoOSUsbPlan fetches and verifies the image as its first line, before anything touches the device (plan fetch)' {
    # The seam the 2026-09-17 handoff names as the single gap stopping the
    # headline feature: Resolve-AutoOSImageUrl existed, Get-AutoOSVerifiedFile
    # existed, and the plan named a cache path nobody ever filled.
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $env:AUTOOS_CACHE_DIR = 'C:\scratch\images'
        try {
            $plan = @(New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' `
                -Engine 'ventoy' -DeviceId '\\.\PHYSICALDRIVE5' -DryRun)
        } finally {
            Remove-Item Env:\AUTOOS_CACHE_DIR -ErrorAction SilentlyContinue
        }
        Assert-Equal $plan[0] 'Invoke-AutoOSUsbFetchImage ubuntu-desktop-lts C:\scratch\images\ubuntu-desktop-lts.iso'
    }
}

Test-Case 'usb: New-AutoOSUsbPlan pins the device identity and emits a re-verify step immediately before the destructive lines (F3/F6, plan reverify)' {
    # Mirror of the bash F3/F6 test: until 2026-09-17 the Windows plan had
    # no re-verify at all. The fetch line precedes it (the hour-long step
    # must not sit between the re-verify and the write).
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $env:AUTOOS_FAKE_DISK_ID = 'serial-XYZ'
        try {
            $plan = @(New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' -Engine 'ventoy' -DeviceId '\\.\PHYSICALDRIVE5' -DryRun)
        } finally { Remove-Item Env:\AUTOOS_FAKE_DISK_ID -ErrorAction SilentlyContinue }
        $rev = [array]::IndexOf($plan, 'Invoke-AutoOSUsbReverify \\.\PHYSICALDRIVE5 Unmounted 6000000000 serial-XYZ')
        $ventoy = [array]::IndexOf($plan, 'Ventoy2Disk.exe -I -G \\.\PHYSICALDRIVE5')
        Assert-True ($rev -ge 1 -and $ventoy -eq ($rev + 1) -and $plan[0] -like 'Invoke-AutoOSUsbFetchImage *') "plan=$($plan -join ' | ')"
    }
}

Test-Case 'usb: Invoke-AutoOSUsbReverify refuses when the device identity no longer matches what was pinned (F3/F6 TOCTOU, reverify)' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $env:AUTOOS_FAKE_DISK_ID = 'serial-CURRENT'
        try {
            $threw = $false; $msg = ''
            try { Invoke-AutoOSUsbReverify -DeviceId '\\.\PHYSICALDRIVE5' -Mode 'Unmounted' -ImageBytes 0 -PinnedId 'serial-PINNED' }
            catch { $threw = $true; $msg = $_.Exception.Message }
            Assert-True ($threw -and $msg -like '*identity changed*') "threw=$threw msg=[$msg]"
        } finally { Remove-Item Env:\AUTOOS_FAKE_DISK_ID -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb: Invoke-AutoOSUsbReverify accepts when the device identity still matches, and a plan line dispatches to it (reverify)' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $env:AUTOOS_FAKE_DISK_ID = 'serial-SAME'
        try {
            Invoke-AutoOSUsbReverify -DeviceId '\\.\PHYSICALDRIVE5' -Mode 'Unmounted' -ImageBytes 0 -PinnedId 'serial-SAME'
            # Through the executor, as the real plan line, with nothing
            # destructive after it: dispatch must reach the function (a
            # malformed/unknown line would throw), and it must not throw.
            $threw = $false; $msg = ''
            try { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @('Invoke-AutoOSUsbReverify \\.\PHYSICALDRIVE5 Unmounted 0 serial-SAME') | Out-Null }
            catch { $threw = $true; $msg = $_.Exception.Message }
            Assert-True (-not $threw) "unexpected throw: $msg"
        } finally { Remove-Item Env:\AUTOOS_FAKE_DISK_ID -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb: New-AutoOSUsbPlan adds the Ventoy persistence step for live-persistent only (plan persistence)' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $live = @(New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'live-persistent' -Engine 'ventoy' -DeviceId '\\.\PHYSICALDRIVE5' -DryRun)
        $inst = @(New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' -Engine 'ventoy' -DeviceId '\\.\PHYSICALDRIVE5' -DryRun)
        Assert-True ($live[-1] -eq 'Add-AutoOSUsbVentoyPersistence \\.\PHYSICALDRIVE5' -and $live[-2] -like 'Invoke-AutoOSUsbCopyImage *' -and (($inst -join "`n") -notlike '*Persistence*')) "live=$($live -join ' | ') inst=$($inst -join ' | ')"
    }
}

Test-Case 'usb: New-AutoOSUsbPlan never plans a raw image onto ventoy''s copy path' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $threw = $false; $msg = ''
        try { New-AutoOSUsbPlan -ImageId 'proxmox-ve' -Kind 'installer' -Engine 'ventoy' `
            -DeviceId '\\.\PHYSICALDRIVE5' -DryRun | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*raw*') "threw=$threw msg=[$msg]"
    }
}

Test-Case 'usb: New-AutoOSUsbPlan refuses a full-os kind on an engine that cannot build one' {
    $threw = $false; $msg = ''
    try { New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'full-os' -Engine 'ventoy' `
        -DeviceId '\\.\PHYSICALDRIVE5' -DryRun | Out-Null }
    catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -like '*cannot build*') "threw=$threw msg=[$msg]"
}

Test-Case 'usb: the ventoy engine is hidden on arm64, not offered and broken' {
    $env:AUTOOS_FAKE_ARCH = 'arm64'
    try {
        $ids = @(Get-AutoOSUsbEngineList -Platform 'windows' -Arch (Get-AutoOSUsbCurrentArch) | ForEach-Object { $_.id })
        Assert-NotContains $ids 'ventoy'
        Assert-Contains $ids 'native'
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_ARCH -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: New-AutoOSUsbPlan refuses a second write while one is already in progress' {
    $env:AUTOOS_FAKE_RUN_ACTIVE = '1'
    try {
        $threw = $false; $msg = ''
        try { New-AutoOSUsbPlan -ImageId 'ubuntu-desktop-lts' -Kind 'installer' -Engine 'ventoy' `
            -DeviceId '\\.\PHYSICALDRIVE5' -DryRun | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*already in progress*') "threw=$threw msg=[$msg]"
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_RUN_ACTIVE -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: setup.ps1 -CreateUsb dry run names the device and Ventoy' {
    $env:AUTOOS_FAKE_DISKS = Get-FakeUsbDisksJson -Fixture 'good_stick'
    $env:AUTOOS_IMAGE_BYTES = '4000000000'
    try {
        $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -CreateUsb `
            -Image 'ubuntu-desktop-lts' -Kind 'installer' -Engine 'ventoy' `
            -UsbDevice '\\.\PHYSICALDRIVE5' -DryRun -NoColor 2>&1) -join "`n"
        Assert-True ($out -like '*PHYSICALDRIVE5*' -and $out -like '*Ventoy2Disk*') "out=$out"
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_DISKS -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_IMAGE_BYTES -ErrorAction SilentlyContinue
    }
}

# ─── setup.ps1 actually executing a plan ───────────────────────────────────
# Until 2026-09-17 a -CreateUsb WITHOUT -DryRun printed the plan and exited
# 0 - a "create" that created nothing; Invoke-AutoOSUsbPlan had no caller
# outside the tests. These two prove the wiring without a dry-run flag,
# which AGENTS.md §5 otherwise reserves for end-to-end tests: both are
# guaranteed to run no step - the first refuses before Invoke-AutoOSUsbPlan
# is ever reached, the second is stopped by AUTOOS_FORCE_FAIL on the very
# first step - both run against the synthetic disk fixture with elevation
# faked, and both assert the scratch cache dir came out exactly as it went in.
function Invoke-AutoOSSetupUsbRealRun {
    param([string[]]$ExtraArgs, [string]$Scratch)
    $env:AUTOOS_FAKE_DISKS = Get-FakeUsbDisksJson -Fixture 'good_stick'
    $env:AUTOOS_IMAGE_BYTES = '4000000000'
    $env:AUTOOS_FAKE_ELEVATED = '1'
    $env:AUTOOS_CACHE_DIR = $Scratch
    try {
        $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -CreateUsb `
            -Image 'ubuntu-desktop-lts' -Kind 'installer' -Engine 'ventoy' `
            -UsbDevice '\\.\PHYSICALDRIVE5' -NoColor @ExtraArgs 2>&1) -join "`n"
        [pscustomobject]@{ Out = $out; ExitCode = $LASTEXITCODE }
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_DISKS -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_IMAGE_BYTES -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_FAKE_ELEVATED -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_CACHE_DIR -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: setup.ps1 -CreateUsb without -WipeTargetDisk shows the plan, then refuses before running any step (execute)' {
    $scratch = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_usb_" + [Guid]::NewGuid().ToString('N')))).FullName
    try {
        $r = Invoke-AutoOSSetupUsbRealRun -Scratch $scratch -ExtraArgs @()
        $left = @(Get-ChildItem -LiteralPath $scratch -Recurse -Force).Count
        Assert-True ($r.ExitCode -ne 0 -and $r.Out -like '*-WipeTargetDisk*' -and $r.Out -like '*Invoke-AutoOSUsbFetchImage*' `
            -and $r.Out -notlike '*TRACE*' -and $r.Out -notlike '*Ready to boot*' -and $left -eq 0) "exit=$($r.ExitCode) left=$left out=$($r.Out)"
    } finally {
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: setup.ps1 -CreateUsb with -WipeTargetDisk hands the printed plan to Invoke-AutoOSUsbPlan (execute)' {
    $scratch = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_usb_" + [Guid]::NewGuid().ToString('N')))).FullName
    $env:AUTOOS_FORCE_FAIL = '1'
    $env:AUTOOS_TRACE = '1'
    try {
        $r = Invoke-AutoOSSetupUsbRealRun -Scratch $scratch -ExtraArgs @('-WipeTargetDisk')
        $left = @(Get-ChildItem -LiteralPath $scratch -Recurse -Force).Count
        # The forced failure must land on the FETCH step - proof that the
        # first thing a real run does is download, not touch the device.
        Assert-True ($r.ExitCode -ne 0 -and $r.Out -like '*TRACE Invoke-AutoOSUsbFetchImage ubuntu-desktop-lts*' `
            -and $r.Out -like '*forced failure*' -and $r.Out -notlike '*Ready to boot*' -and $left -eq 0) "exit=$($r.ExitCode) left=$left out=$($r.Out)"
    } finally {
        Remove-Item Env:\AUTOOS_FORCE_FAIL -ErrorAction SilentlyContinue
        Remove-Item Env:\AUTOOS_TRACE -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }
}

# ─── Invoke-AutoOSUsbFetchImage: the resolver-to-downloader bridge ──────────
# Mirror of the bash suite's "usb fetch" block: a scratch release directory
# (a tiny text file standing in for the ISO plus a real SHA256SUMS computed
# from it) reached over file:// the same way the image_resolve tests above
# reach their fixtures, and a scratch catalog whose one entry points at it.
# No test here reaches the real internet or the real catalog. Every test
# name carries "usb" and "fetch" so `-Filter usb` and `-Filter fetch` both
# reach them.
function New-AutoOSUsbFetchFixture {
    # -Mode good lists the image's real digest, bad an all-zero one, missing
    # a different filename entirely. -Sums '-' models a catalog entry with
    # no checksum manifest at all.
    param([string]$Mode = 'good', [string]$Sums = 'SHA256SUMS', [string[]]$Mirrors = @())
    $rel = Join-Path ([IO.Path]::GetTempPath()) ('aos_fetch_' + [Guid]::NewGuid().ToString('N'))
    $dir = Join-Path $rel '1.0'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $iso = Join-Path $dir 'testos-1.0-amd64.iso'
    [IO.File]::WriteAllText($iso, "AUTOOS TEST IMAGE - not a real ISO`n")
    $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $iso).Hash.ToLowerInvariant()
    $line = switch ($Mode) {
        'good'    { "$sum *testos-1.0-amd64.iso" }
        'bad'     { ('0' * 64) + ' *testos-1.0-amd64.iso' }
        'missing' { "$sum *something-else.iso" }
    }
    [IO.File]::WriteAllText((Join-Path $dir 'SHA256SUMS'), "$line`n")
    $html = '<html><body><pre><a href="../">../</a>' + "`n" +
            '<a href="testos-1.0-amd64.iso">testos-1.0-amd64.iso</a>' + "`n" +
            '<a href="SHA256SUMS">SHA256SUMS</a>' + "`n" + '</pre></body></html>'
    [IO.File]::WriteAllText((Join-Path $dir 'index.html'), $html)
    $cat = Join-Path $rel 'images.json'
    $entry = @{ id = 'testos'; name = 'Test OS'; homepage = 'http://127.0.0.1/'
                index = (ConvertTo-AutoOSTestDirUri $dir); file = 'testos-[0-9.]+-amd64\.iso$'
                sums = $Sums; sig = '-'; key = '-'; kinds = @('installer'); writeMode = 'hybrid'; sizeGb = 0.001 }
    if ($Mirrors.Count -gt 0) { $entry['mirrors'] = @($Mirrors) }
    @{ images = @($entry) } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $cat -Encoding UTF8
    [pscustomobject]@{ Root = $rel; Catalog = $cat; Iso = $iso; Dir = $dir; Dest = (Join-Path $rel 'cache\testos.iso') }
}

function New-AutoOSUsbFetchMirror {
    # A second release dir with the same 1.0\ layout served over loopback
    # http as a "mirror": an exact copy, or one whose image bytes differ
    # from what the canonical manifest publishes. Returns @{ Root; Server }.
    param([Parameter(Mandatory)][string]$FixtureDir, [string]$Mode = 'good')
    $m = Join-Path ([IO.Path]::GetTempPath()) ('aos_mirror_' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $m '1.0') -Force | Out-Null
    Copy-Item -Path (Join-Path $FixtureDir '*') -Destination (Join-Path $m '1.0') -Force
    if ($Mode -eq 'corrupt') { [IO.File]::WriteAllText((Join-Path $m '1.0\testos-1.0-amd64.iso'), "CORRUPT MIRROR COPY`n") }
    $srv = Start-AutoOSTestHttpServer -Directory $m
    # Url is the mirror's equivalent of the catalog `index` (the release
    # directory itself), so the resolved relative filename appends cleanly.
    @{ Root = $m; Server = $srv; Url = "http://127.0.0.1:$($srv.Port)/1.0/" }
}

Test-Case 'usb: Get-AutoOSUsbSumsDigest reads coreutils, starred and BSD-style manifests and ignores non-SHA-256 lines (fetch)' {
    $m = Join-Path ([IO.Path]::GetTempPath()) ('aos_sums_' + [Guid]::NewGuid().ToString('N'))
    $d = { param($n) ([string]$n) * 64 }
    $lines = @(
        'd41d8cd98f00b204e9800998ecf8427e *plain.iso',
        "$(& $d 1)  plain.iso",
        "$(& $d 2) *starred.iso",
        "$(& $d 3)  ./dotslash.iso",
        "SHA256 (bsd.iso) = $(& $d 4)"
    )
    [IO.File]::WriteAllText($m, ($lines -join "`n") + "`n")
    try {
        $r1 = Get-AutoOSUsbSumsDigest -ManifestPath $m -FileName 'plain.iso'
        $r2 = Get-AutoOSUsbSumsDigest -ManifestPath $m -FileName 'starred.iso'
        $r3 = Get-AutoOSUsbSumsDigest -ManifestPath $m -FileName 'dotslash.iso'
        $r4 = Get-AutoOSUsbSumsDigest -ManifestPath $m -FileName 'bsd.iso'
        $r5 = Get-AutoOSUsbSumsDigest -ManifestPath $m -FileName 'absent.iso'
        Assert-True ($r1 -eq (& $d 1) -and $r2 -eq (& $d 2) -and $r3 -eq (& $d 3) -and $r4 -eq (& $d 4) -and $null -eq $r5) "plain=$r1 starred=$r2 dotslash=$r3 bsd=$r4 absent=[$r5]"
    } finally {
        Remove-Item -Force $m -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage downloads the image, verifies it against the published SHA256SUMS and keeps the manifest beside it (fetch)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good
    try {
        $text = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog }
        $same = (Test-Path -LiteralPath $fx.Dest) -and ((Get-FileHash -LiteralPath $fx.Dest).Hash -eq (Get-FileHash -LiteralPath $fx.Iso).Hash)
        Assert-True ($same -and (Test-Path -LiteralPath "$($fx.Dest).sums") -and $text -like '*verified image*') "same=$same out=$text"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage reports a second run as skipped, never a refetch (fetch idempotent)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good
    try {
        Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog } | Out-Null
        $text = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog }
        Assert-True ($text -like '*skipped: testos.iso already downloaded*') "out=$text"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage refuses a checksum mismatch and leaves nothing at the destination (fetch)' {
    $fx = New-AutoOSUsbFetchFixture -Mode bad
    try {
        $threw = $false; $msg = ''
        try { Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog } | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*checksum mismatch*' -and -not (Test-Path -LiteralPath $fx.Dest) -and -not (Test-Path -LiteralPath "$($fx.Dest).part")) "threw=$threw msg=[$msg]"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage refuses a manifest that does not list the image before any image download (fetch)' {
    $fx = New-AutoOSUsbFetchFixture -Mode missing
    try {
        $threw = $false; $msg = ''
        try { Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog } | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*does not list*' -and -not (Test-Path -LiteralPath $fx.Dest)) "threw=$threw msg=[$msg]"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage refuses a catalog entry with no checksum manifest, nothing fetched (fetch)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good -Sums '-'
    try {
        $threw = $false; $msg = ''
        try { Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog } | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*no checksum manifest*' -and -not (Test-Path -LiteralPath $fx.Dest) -and -not (Test-Path -LiteralPath "$($fx.Dest).sums")) "threw=$threw msg=[$msg]"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage: custom-local without its source and digest is a specific refusal, not a fetch attempt (fetch)' {
    # custom-local / custom-url are real images now, but only through
    # Invoke-AutoOSUsbFetchCustomImage. Called the catalog way they must
    # refuse by name, never fall through to Resolve-AutoOSImageUrl.
    $threw = $false; $msg = ''
    try { Invoke-AutoOSUsbFetchImage -ImageId 'custom-local' -Destination 'C:\nowhere\x.iso' | Out-Null }
    catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -like '*needs its source and digest*') "threw=$threw msg=[$msg]"
}

Test-Case 'usb: Invoke-AutoOSUsbPlan routes a fetch line to the fetch, keeping a path with spaces as one argument (fetch)' {
    # The error naming the WHOLE spaced path is the proof the line reached
    # the function with the path intact: a dispatcher that split on spaces
    # would have reported only "dir\x.iso", or bound "C:\some" to the wrong
    # parameter. custom-local's line carries the path LAST precisely so a
    # Windows path may contain spaces. Driven through the exported
    # Invoke-AutoOSUsbPlan since the per-step dispatcher is module-private.
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $threw = $false; $msg = ''
        try {
            Invoke-AutoOSCapturedConsole {
                Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @('Invoke-AutoOSUsbFetchImage custom-local - C:\some dir\x.iso')
            } | Out-Null
        } catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*C:\some dir\x.iso is no longer a readable file*') "threw=$threw msg=[$msg]"
    }
}

# ─── custom-url / custom-local (an image the catalog does not describe) ────
# Mirror of the bash suite's "usb custom image" block. Every name carries
# "usb" and "custom" so -Filter usb and -Filter custom both reach them.
function New-AutoOSCustomImageFixture {
    # A throwaway image in a directory whose name contains a SPACE, because
    # a Windows path may legitimately contain one and the plan must carry it.
    $dir = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos custom " + [Guid]::NewGuid().ToString('N')))).FullName
    $img = Join-Path $dir 'my image.iso'
    [IO.File]::WriteAllText($img, "AUTOOS CUSTOM TEST IMAGE`n")
    [pscustomobject]@{ Dir = $dir; Image = $img; Sha = (Get-AutoOSFileSha256 -Path $img); Bytes = (Get-Item -LiteralPath $img).Length }
}

function Invoke-AutoOSCustomPlan {
    # Returns @{ Plan; Error } so a refusal is asserted on its message.
    param([hashtable]$Arguments)
    try {
        [pscustomobject]@{ Plan = @(New-AutoOSUsbPlan -Kind installer -DeviceId '\\.\PHYSICALDRIVE5' -DryRun @Arguments); Error = '' }
    } catch {
        [pscustomobject]@{ Plan = @(); Error = $_.Exception.Message }
    }
}

Test-Case 'usb custom: custom-local plans the file itself as the write source, with its real size and "-" for no digest' {
    $fx = New-AutoOSCustomImageFixture
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        try {
            $r = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = $fx.Image; WriteMode = 'hybrid' }
            $joined = $r.Plan -join "`n"
            Assert-True ($r.Plan[0] -eq "Invoke-AutoOSUsbFetchImage custom-local - $($fx.Image)" `
                -and $joined -like "*Invoke-AutoOSUsbReverify \\.\PHYSICALDRIVE5 Unmounted $($fx.Bytes) *" `
                -and $joined -like "*Invoke-AutoOSUsbCopyImage \\.\PHYSICALDRIVE5 $($fx.Image)*") "error=[$($r.Error)] plan=$joined"
        } finally { Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb custom: a custom image refuses without -WriteMode, and a raw one is refused on Ventoy but planned on native (A11)' {
    $fx = New-AutoOSCustomImageFixture
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        try {
            $noMode = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = $fx.Image }
            $rawVentoy = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = $fx.Image; WriteMode = 'raw' }
            $rawNative = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'native'; ImagePath = $fx.Image; WriteMode = 'raw' }
            $badMode = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = $fx.Image; WriteMode = 'iso' }
            Assert-True ($noMode.Error -like '*needs -WriteMode*' -and $rawVentoy.Error -like "*cannot write a 'raw' image*" `
                -and ($rawNative.Plan -join "`n") -like '*Write-AutoOSUsbRaw*' -and $badMode.Error -like "*must be 'hybrid' or 'raw'*") `
                "noMode=[$($noMode.Error)] rawVentoy=[$($rawVentoy.Error)] rawNative=[$($rawNative.Plan -join ' | ')] badMode=[$($badMode.Error)]"
        } finally { Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb custom: custom-local refuses a missing file and an empty file' {
    $fx = New-AutoOSCustomImageFixture
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        try {
            $empty = Join-Path $fx.Dir 'empty.iso'; [IO.File]::WriteAllBytes($empty, [byte[]]::new(0))
            $missing = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = (Join-Path $fx.Dir 'nope.iso'); WriteMode = 'hybrid' }
            $emptyR = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-local'; Engine = 'ventoy'; ImagePath = $empty; WriteMode = 'hybrid' }
            Assert-True ($missing.Error -like '*is not a readable file*' -and $emptyR.Error -like '*is empty*') "missing=[$($missing.Error)] empty=[$($emptyR.Error)]"
        } finally { Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb custom: custom-url refuses without a digest, with a malformed digest, and with a non-http scheme; plans a digest-keyed cache path otherwise' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $sha = 'ab' * 32
        $env:AUTOOS_CACHE_DIR = 'C:\scratch\images'
        try {
            $noSha = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-url'; Engine = 'ventoy'; ImageUrl = 'https://example.invalid/x.iso'; WriteMode = 'hybrid' }
            $badSha = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-url'; Engine = 'ventoy'; ImageUrl = 'https://example.invalid/x.iso'; WriteMode = 'hybrid'; ImageSha256 = 'nothex' }
            $ftp = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-url'; Engine = 'ventoy'; ImageUrl = 'ftp://example.invalid/x.iso'; WriteMode = 'hybrid'; ImageSha256 = $sha }
            $ok = Invoke-AutoOSCustomPlan @{ ImageId = 'custom-url'; Engine = 'ventoy'; ImageUrl = 'https://example.invalid/x.iso'; WriteMode = 'hybrid'; ImageSha256 = $sha.ToUpperInvariant() }
        } finally { Remove-Item Env:\AUTOOS_CACHE_DIR -ErrorAction SilentlyContinue }
        Assert-True ($noSha.Error -like '*needs -ImageSha256*' -and $badSha.Error -like '*64 hexadecimal*' -and $ftp.Error -like '*http:// or https://*' `
            -and $ok.Plan[0] -eq "Invoke-AutoOSUsbFetchImage custom-url $sha https://example.invalid/x.iso C:\scratch\images\custom-url-$($sha.Substring(0,16)).iso") `
            "noSha=[$($noSha.Error)] badSha=[$($badSha.Error)] ftp=[$($ftp.Error)] ok=[$($ok.Plan -join ' | ')]"
    }
}

Test-Case 'usb custom: executing custom-local verifies the digest - a wrong one stops before any write, "not touched"' {
    $fx = New-AutoOSCustomImageFixture
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        try {
            $good = @(New-AutoOSUsbPlan -ImageId custom-local -Kind installer -Engine ventoy -DeviceId '\\.\PHYSICALDRIVE5' -DryRun -ImagePath $fx.Image -WriteMode hybrid -ImageSha256 $fx.Sha)
            $bad = @(New-AutoOSUsbPlan -ImageId custom-local -Kind installer -Engine ventoy -DeviceId '\\.\PHYSICALDRIVE5' -DryRun -ImagePath $fx.Image -WriteMode hybrid -ImageSha256 ('0' * 64))
            $goodText = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @($good[0]) }
            $script:customBadMsg = ''
            $badText = Invoke-AutoOSCapturedConsole {
                try { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @($bad[0]) } catch { $script:customBadMsg = $_.Exception.Message }
            }
            Assert-True ($goodText -like '*verified image*' -and $script:customBadMsg -like '*does not match the SHA-256 you supplied*' -and $badText -like '*not touched*') `
                "good=[$goodText] badMsg=[$($script:customBadMsg)] bad=[$badText]"
        } finally { Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue }
    }
}

Test-Case 'usb custom: executing custom-url downloads and verifies against the supplied digest, and a second run is skipped' {
    $fx = New-AutoOSCustomImageFixture
    $srv = Start-AutoOSTestHttpServer -Directory $fx.Dir
    $cache = Join-Path $fx.Dir 'cache'
    $url = "http://127.0.0.1:$($srv.Port)/my%20image.iso"
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $env:AUTOOS_CACHE_DIR = $cache
        try {
            $plan = @(New-AutoOSUsbPlan -ImageId custom-url -Kind installer -Engine ventoy -DeviceId '\\.\PHYSICALDRIVE5' -DryRun -ImageUrl $url -WriteMode hybrid -ImageSha256 $fx.Sha)
            $first = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @($plan[0]) }
            $second = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @($plan[0]) }
            $dest = Join-Path $cache ('custom-url-' + $fx.Sha.Substring(0, 16) + '.iso')
            $same = (Test-Path -LiteralPath $dest) -and ((Get-AutoOSFileSha256 -Path $dest) -eq $fx.Sha)
            Assert-True ($same -and $first -like '*verified image*' -and $second -like '*skipped*') "same=$same first=[$first] second=[$second]"
        } finally {
            Remove-Item Env:\AUTOOS_CACHE_DIR -ErrorAction SilentlyContinue
            Stop-AutoOSTestHttpServer $srv
            Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue
        }
    }
}

Test-Case 'usb custom: setup.ps1 -CreateUsb with a custom-local image and -DryRun shows the plan and writes nothing' {
    $fx = New-AutoOSCustomImageFixture
    $env:AUTOOS_FAKE_DISKS = Get-FakeUsbDisksJson -Fixture 'good_stick'
    try {
        $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -CreateUsb -Image custom-local -ImagePath $fx.Image `
            -WriteMode hybrid -Engine ventoy -UsbDevice '\\.\PHYSICALDRIVE5' -DryRun -NoColor 2>&1) -join "`n"
        $code = $LASTEXITCODE
        Assert-True ($code -eq 0 -and $out -like "*Invoke-AutoOSUsbFetchImage custom-local - $($fx.Image)*") "exit=$code out=$out"
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_DISKS -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $fx.Dir -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbPlan refuses a plan step with an unrecognised command shape and never executes it (finding F4, execute)' {
    # Windows half of F4: the dispatcher used Invoke-Expression for every
    # line it did not special-case, so a line shaped like "cmd; other-cmd"
    # would have run both halves. Now every known shape is matched
    # explicitly and anything else is refused - the marker must not exist.
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $marker = Join-Path $env:TEMP ('aos_f4_' + [Guid]::NewGuid().ToString('N'))
        $threw = $false; $msg = ''
        try { Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @("Write-Host pwned; New-Item -ItemType File -Path '$marker'") } | Out-Null }
        catch { $threw = $true; $msg = $_.Exception.Message }
        Assert-True ($threw -and $msg -like '*known command shape*' -and -not (Test-Path -LiteralPath $marker)) "threw=$threw msg=[$msg] marker=$(Test-Path -LiteralPath $marker)"
        Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbPlan reports a fetch-step failure as "not touched", never "rewrite from wipefs" (fetch)' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $env:AUTOOS_FORCE_FAIL = '1'
        try {
            $script:usbFetchFailThrew = $false
            $text = Invoke-AutoOSCapturedConsole {
                try { Invoke-AutoOSUsbPlan -DeviceId '\\.\PHYSICALDRIVE5' -Plan @('Invoke-AutoOSUsbFetchImage ubuntu-desktop-lts C:\x\y.iso') }
                catch { $script:usbFetchFailThrew = $true }
            }
            Assert-True ($script:usbFetchFailThrew -and $text -like '*not touched*' -and $text -notlike '*wipefs*') "threw=$($script:usbFetchFailThrew) text=$text"
        } finally {
            Remove-Item Env:\AUTOOS_FORCE_FAIL -ErrorAction SilentlyContinue
        }
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage uses a healthy mirror before the canonical source, verified against the canonical manifest (fetch mirror)' {
    # The canonical copy is corrupted AFTER its manifest was written: were
    # the canonical source tried first, the digest check would fail.
    # Success therefore proves the mirror served the bytes.
    $fx = New-AutoOSUsbFetchFixture -Mode good
    $mir = New-AutoOSUsbFetchMirror -FixtureDir $fx.Dir -Mode good
    try {
        $fx2 = New-AutoOSUsbFetchFixture -Mode good -Mirrors @($mir.Url)
        [IO.File]::WriteAllText($fx2.Iso, "CANONICAL COPY NOW CORRUPT`n")
        # Same bytes on the mirror as fx2's manifest expects: both fixtures
        # write the identical test image, so their digests agree.
        $text = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx2.Dest -CatalogPath $fx2.Catalog }
        $same = (Test-Path -LiteralPath $fx2.Dest) -and ((Get-FileHash -LiteralPath $fx2.Dest).Hash -eq (Get-FileHash -LiteralPath $fx.Iso).Hash)
        Assert-True ($same -and $text -like "*from $($mir.Url)testos-1.0-amd64.iso*" -and $text -like '*verified image*') "same=$same out=$text"
        Remove-Item -Recurse -Force $fx2.Root -ErrorAction SilentlyContinue
    } finally {
        Stop-AutoOSTestHttpServer $mir.Server
        Remove-Item -Recurse -Force $fx.Root, $mir.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage skips an unreachable mirror with a warning and uses the canonical source (fetch mirror)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good -Mirrors @('http://127.0.0.1:1/1.0/')
    try {
        $text = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx.Dest -CatalogPath $fx.Catalog }
        Assert-True ((Test-Path -LiteralPath $fx.Dest) -and $text -like '*http://127.0.0.1:1/1.0/testos-1.0-amd64.iso failed*' -and $text -like '*trying the next source*' -and $text -like '*verified image*') "out=$text"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage skips a mirror whose bytes do not match the manifest, the canonical copy wins (fetch mirror)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good
    $mir = New-AutoOSUsbFetchMirror -FixtureDir $fx.Dir -Mode corrupt
    try {
        $fx2 = New-AutoOSUsbFetchFixture -Mode good -Mirrors @($mir.Url)
        $text = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $fx2.Dest -CatalogPath $fx2.Catalog }
        $same = (Test-Path -LiteralPath $fx2.Dest) -and ((Get-FileHash -LiteralPath $fx2.Dest).Hash -eq (Get-FileHash -LiteralPath $fx2.Iso).Hash)
        Assert-True ($same -and $text -like '*checksum mismatch*' -and $text -like '*trying the next source*') "same=$same out=$text"
        Remove-Item -Recurse -Force $fx2.Root -ErrorAction SilentlyContinue
    } finally {
        Stop-AutoOSTestHttpServer $mir.Server
        Remove-Item -Recurse -Force $fx.Root, $mir.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: the read-back hashes the destination uncached, and when that is impossible still compares and warns exactly once (copy readback uncached)' {
    # The default path must NOT warn (it took the uncached read); a forced
    # refusal must still catch a corrupted file - a fallback that skipped
    # the comparison would be worse than no read-back at all - and must say
    # so once, not once per file.
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_rbu_" + [Guid]::NewGuid().ToString('N')))).FullName
    try {
        $src = Join-Path $tmp 'src'; $dst = Join-Path $tmp 'dst'
        New-Item -ItemType Directory -Path $src, $dst -Force | Out-Null
        $bytes = [byte[]](1..20000 | ForEach-Object { $_ % 251 })
        foreach ($name in @('a.bin', 'b.bin', 'c.bin')) {
            [IO.File]::WriteAllBytes((Join-Path $src $name), $bytes); [IO.File]::WriteAllBytes((Join-Path $dst $name), $bytes)
        }
        $script:rbDefault = $null
        $textDefault = Invoke-AutoOSCapturedConsole { $script:rbDefault = @(Test-AutoOSUsbCopyReadback -SourceRoot $src -DestRoot $dst) }

        $corrupt = [byte[]]$bytes.Clone(); $corrupt[9000] = [byte](255 - $corrupt[9000])
        [IO.File]::WriteAllBytes((Join-Path $dst 'b.bin'), $corrupt)
        $env:AUTOOS_FAKE_NO_UNCACHED = '1'
        try {
            $script:rbForced = $null
            $textForced = Invoke-AutoOSCapturedConsole { $script:rbForced = @(Test-AutoOSUsbCopyReadback -SourceRoot $src -DestRoot $dst) }
        } finally {
            Remove-Item Env:\AUTOOS_FAKE_NO_UNCACHED -ErrorAction SilentlyContinue
        }
        # A phrase that occurs once per warning (the warning embeds the
        # exception text, which also says "uncached read").
        $warnings = ([regex]::Matches($textForced, 'comparing through the file cache instead')).Count
        $forcedBad = @($script:rbForced | ForEach-Object { "$($_.Path)=$($_.Reason)" })
        Assert-True ($script:rbDefault.Count -eq 0 -and $textDefault -notlike '*comparing through the file cache*' `
            -and $forcedBad.Count -eq 1 -and $forcedBad[0] -eq 'b.bin=content' -and $warnings -eq 1) `
            "default=$($script:rbDefault.Count) defaultText=[$textDefault] forced=$($forcedBad -join ',') warnings=$warnings"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Test-AutoOSUsbCopyReadback reports a file whose bytes differ at the same size, a missing file and a size change, and nothing for an identical tree (copy readback)' {
    # The second real build on 2026-09-17 finished and printed "Ready to
    # boot" while the stick had silently replaced one 16 KB cluster of
    # md5sum.txt with garbage at the same size. Only reading back catches
    # that, so the read-back is a pure dir-vs-dir function tested here.
    $tmp = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("aos_rb_" + [Guid]::NewGuid().ToString('N')))).FullName
    try {
        $src = Join-Path $tmp 'src'; $dst = Join-Path $tmp 'dst'
        foreach ($d in @("$src\casper", "$dst\casper", "$src\EFI\boot", "$dst\EFI\boot")) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        $bytes = [byte[]](1..40000 | ForEach-Object { $_ % 253 })
        foreach ($rel in @('md5sum.txt', 'casper\minimal.squashfs', 'EFI\boot\bootx64.efi', 'casper\vmlinuz')) {
            [IO.File]::WriteAllBytes((Join-Path $src $rel), $bytes); [IO.File]::WriteAllBytes((Join-Path $dst $rel), $bytes)
        }
        $clean = @(Test-AutoOSUsbCopyReadback -SourceRoot $src -DestRoot $dst)
        # Same size, one "cluster" of garbage in the middle - the live shape.
        $corrupt = [byte[]]$bytes.Clone(); for ($i = 8192; $i -lt 24576; $i++) { $corrupt[$i] = [byte](255 - $corrupt[$i]) }
        [IO.File]::WriteAllBytes((Join-Path $dst 'md5sum.txt'), $corrupt)
        Remove-Item -LiteralPath (Join-Path $dst 'casper\vmlinuz') -Force
        [IO.File]::WriteAllBytes((Join-Path $dst 'EFI\boot\bootx64.efi'), $bytes[0..999])
        $bad = @(Test-AutoOSUsbCopyReadback -SourceRoot $src -DestRoot $dst)
        $byPath = @{}; foreach ($b in $bad) { $byPath[$b.Path] = $b.Reason }
        Assert-True ($clean.Count -eq 0 -and $bad.Count -eq 3 -and $byPath['md5sum.txt'] -eq 'content' -and $byPath['casper\vmlinuz'] -eq 'missing' -and $byPath['EFI\boot\bootx64.efi'] -eq 'size') "clean=$($clean.Count) bad=$(($bad | ForEach-Object { "$($_.Path)=$($_.Reason)" }) -join ',')"
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Test-AutoOSRobocopyFailed treats only exit codes 0..7 as success - negative and out-of-range codes are failures (copy)' {
    # robocopy killed or losing its destination mid-copy can exit with a
    # negative or otherwise out-of-range code; "-ge 8" read those as
    # success. 0..7 is the documented success bitmask (copied/extras/
    # mismatches); 8 = some files failed, 16 = fatal.
    $ok = @(0, 1, 2, 3, 4, 5, 6, 7) | ForEach-Object { Test-AutoOSRobocopyFailed -ExitCode $_ }
    $bad = @(8, 9, 16, 24, -1, -1073741510, 259) | ForEach-Object { Test-AutoOSRobocopyFailed -ExitCode $_ }
    Assert-True ((@($ok | Where-Object { $_ }).Count -eq 0) -and (@($bad | Where-Object { -not $_ }).Count -eq 0)) "ok=$($ok -join ',') bad=$($bad -join ',')"
}

Test-Case 'usb: Invoke-AutoOSUsbFetchImage creates a cache directory that does not exist yet, as on a first run (fetch)' {
    $fx = New-AutoOSUsbFetchFixture -Mode good
    try {
        $dest = Join-Path $fx.Root 'brand\new\dir\testos.iso'
        Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbFetchImage -ImageId 'testos' -Destination $dest -CatalogPath $fx.Catalog } | Out-Null
        Assert-True (Test-Path -LiteralPath $dest) "expected $dest to exist"
    } finally {
        Remove-Item -Recurse -Force $fx.Root -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb chooser: image items name every real image and exclude the custom pseudo-entries (chooser)' {
    $items = @(Get-AutoOSUsbChooserImageItem)
    $ids = @($items | ForEach-Object { $_.Id })
    $ubuntu = $items | Where-Object { $_.Id -eq 'ubuntu-desktop-lts' }
    Assert-True (($ids -contains 'ubuntu-desktop-lts') -and ($ids -notcontains 'custom-url') -and ($ids -notcontains 'custom-local') -and $ubuntu.Description -like '*installer, live-persistent*6 GB*') "ids=$($ids -join ',') desc=$($ubuntu.Description)"
}

Test-Case 'usb chooser: engine items list what Windows can build for the kind; rufus is offered to the terminal, badged interactive (chooser)' {
    $env:AUTOOS_FAKE_ARCH = 'x64'
    try {
        $hybrid = @(Get-AutoOSUsbChooserEngineItem -Kind 'installer' -WriteMode 'hybrid')
        $raw = @(Get-AutoOSUsbChooserEngineItem -Kind 'installer' -WriteMode 'raw')
        $rufus = $hybrid | Where-Object { $_.Id -eq 'rufus' }
        $ventoy = $hybrid | Where-Object { $_.Id -eq 'ventoy' }
        Assert-True ($rufus.Badge -eq 'interactive' -and $ventoy.Badge -eq 'default' -and (@($hybrid.Id) -contains 'uefi-copy') -and (@($raw.Id) -notcontains 'ventoy') -and (@($raw.Id) -contains 'native')) "hybrid=$($hybrid.Id -join ',') raw=$($raw.Id -join ',')"
    } finally { Remove-Item Env:\AUTOOS_FAKE_ARCH -ErrorAction SilentlyContinue }
}

Test-Case 'usb chooser: a non-interactive run takes the default at every step and never consents to the write itself (chooser)' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -Body {
        $env:AUTOOS_NONINTERACTIVE = '1'; $env:AUTOOS_FAKE_ARCH = 'x64'
        try {
            $text = Invoke-AutoOSCapturedConsole { $script:chooserResult = Invoke-AutoOSUsbChooser }
            $dry = $null
            $textDry = Invoke-AutoOSCapturedConsole { $script:chooserDry = Invoke-AutoOSUsbChooser -DryRun }
            $dry = $script:chooserDry
            Assert-True (($null -eq $script:chooserResult) -and $text -like '*Not confirmed*' `
                -and $dry.Image -eq 'ubuntu-desktop-lts' -and $dry.Kind -eq 'installer' -and $dry.Engine -eq 'ventoy' -and $dry.Device -eq '\\.\PHYSICALDRIVE5' -and (-not $dry.Wipe) `
                -and $textDry -like '*PHYSICALDRIVE5*' -and $textDry -like '*GB*') "result=$($script:chooserResult) text=$text dry=$($dry | ConvertTo-Json -Compress) textDry=$textDry"
        } finally {
            Remove-Item Env:\AUTOOS_NONINTERACTIVE, Env:\AUTOOS_FAKE_ARCH -ErrorAction SilentlyContinue
        }
    }
}

Test-Case 'usb chooser: the profile menu offers Create installer USB and a non-interactive -CreateUsb without flags still refuses (chooser)' {
    $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -CreateUsb -NoColor 2>&1) -join "`n"
    Assert-True ($LASTEXITCODE -eq 2 -and $out -like '*requires -Image*' -and ((Get-Content $setup -Raw) -like "*Id = 'create-usb'; Name = 'Create installer USB'*")) "exit=$LASTEXITCODE out=$out"
}

Test-Case 'usb: setup.ps1 -Undo states plainly that a USB write cannot be undone' {
    $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Undo -DryRun -NoColor 2>&1) -join "`n"
    Assert-True ($out -like '*USB*') "out=$out"
}

# ─── Browser UI: the three USB endpoints (Task 11b) ────────────────────────
# Task 11 shipped web/index.html's "Create installer USB" button and
# lib/linux/serve.py's three endpoints, but never mirrored them into
# AutoOS.Serve.psm1 - the button 404'd on every call on the platform the
# human partner actually runs. Get-AutoOSServeUsbCatalog/-UsbDevices/
# -UsbCreateResult are the module-level, exported, directly-callable mirror
# of serve.py's usb_images_response/usb_devices_response/
# usb_create_response, for the identical reason those are module-level in
# serve.py: a test can call them without starting a real HttpListener.
# Every test name below carries "usb" so `-Filter usb` reaches it.
function Invoke-WithModuleScope {
    # Reaches into AutoOS.Serve.psm1's module scope to set/clear
    # $script:RunInfo.Running directly - the in-memory run-lock
    # /api/install and /api/usb/create both check - without spinning up a
    # real installer/usb-create subprocess just to flip one flag.
    param([scriptblock]$Body)
    $mod = Get-Module AutoOS.Serve
    & $mod $Body
}

Test-Case 'usb: Get-AutoOSServeUsbCatalog excludes the interactive rufus engine' {
    $env:AUTOOS_FAKE_ARCH = 'x64'
    try {
        $catalog = Get-AutoOSServeUsbCatalog -RepoRoot $Root
        $ids = @($catalog.engines | ForEach-Object { $_.id })
        Assert-NotContains $ids 'rufus'
        Assert-Contains $ids 'ventoy'
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_ARCH -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Get-AutoOSServeUsbDevices reports elevated=false with a reason when unelevated' {
    $env:AUTOOS_FAKE_ELEVATED = '0'
    try {
        $result = Get-AutoOSServeUsbDevices
        Assert-True (-not $result.elevated) 'expected elevated=false'
        Assert-True ([string]$result.reason -like '*Administrator*') "reason=$($result.reason)"
    } finally {
        Remove-Item Env:\AUTOOS_FAKE_ELEVATED -ErrorAction SilentlyContinue
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult refuses a device Assert-AutoOSUsbSafe would refuse' {
    Invoke-WithFakeUsbEnv -Fixture 'root_is_boot_disk' -Body {
        $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE0'; image = 'ubuntu-desktop-lts'; kind = 'installer'; engine = 'ventoy' }
        $result = Get-AutoOSServeUsbCreateResult -Body $body
        Assert-True ($result.Code -eq 400 -and [string]$result.Payload.error -like '*system disk*') `
            "code=$($result.Code) error=$($result.Payload.error)"
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult guards the device before checking image/engine' {
    Invoke-WithFakeUsbEnv -Fixture 'root_is_boot_disk' -Body {
        # No image/engine at all - if the guard ran second this would come
        # back "image and engine are both required" instead.
        $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE0' }
        $result = Get-AutoOSServeUsbCreateResult -Body $body
        Assert-True ($result.Code -eq 400 -and [string]$result.Payload.error -like '*system disk*') `
            "code=$($result.Code) error=$($result.Payload.error)"
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult refuses a device smaller than the image (finding F12)' {
    # Finding F12 (mirror of lib/linux/serve.py's usb_create_response): this
    # used to call Assert-AutoOSUsbSafe with no $env:AUTOOS_IMAGE_BYTES at
    # all, so the size check compared against 0 and a device smaller than
    # the image still got a 202. tiny_stick is 4 GB; ubuntu-desktop-lts
    # declares sizeGb: 6 in catalog\images.json.
    Invoke-WithFakeUsbEnv -Fixture 'tiny_stick' -Body {
        $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE5'; image = 'ubuntu-desktop-lts'; kind = 'installer'; engine = 'ventoy' }
        $result = Get-AutoOSServeUsbCreateResult -Body $body
        Assert-True ($result.Code -eq 400 -and [string]$result.Payload.error -like '*too small*') `
            "code=$($result.Code) error=$($result.Payload.error)"
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult returns 409 while a run is already in progress' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        Invoke-WithModuleScope { $script:RunInfo.Running = $true }
        try {
            $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE5'; image = 'ubuntu-desktop-lts'; kind = 'installer'; engine = 'ventoy' }
            $result = Get-AutoOSServeUsbCreateResult -Body $body
            Assert-True ($result.Code -eq 409 -and [string]$result.Payload.error -like '*already in progress*') `
                "code=$($result.Code) error=$($result.Payload.error)"
        } finally {
            Invoke-WithModuleScope { $script:RunInfo.Running = $false }
        }
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult returns 409 for a second write, not just during an install' {
    # /api/install and /api/usb/create share one in-memory run flag - this
    # is the "409 while an install runs" half of that contract, exercised
    # from the usb-create side.
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        Invoke-WithModuleScope { $script:RunInfo.Running = $true; $script:RunInfo.Summary = 'installing components' }
        try {
            $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE5'; image = 'ubuntu-desktop-lts'; kind = 'installer'; engine = 'ventoy' }
            $result = Get-AutoOSServeUsbCreateResult -Body $body
            Assert-True ($result.Code -eq 409) "expected 409 while an install runs, got $($result.Code)"
        } finally {
            Invoke-WithModuleScope { $script:RunInfo.Running = $false; $script:RunInfo.Summary = '' }
        }
    }
}

Test-Case 'usb: Get-AutoOSServeUsbCreateResult accepts a valid request and reports the device' {
    Invoke-WithFakeUsbEnv -Fixture 'good_stick' -ImageBytes 4000000000 -Body {
        $body = [pscustomobject]@{ device = '\\.\PHYSICALDRIVE5'; image = 'ubuntu-desktop-lts'; kind = 'installer'; engine = 'ventoy' }
        $result = Get-AutoOSServeUsbCreateResult -Body $body
        Assert-True ($result.Code -eq 202 -and $result.Payload.device -eq '\\.\PHYSICALDRIVE5') `
            "code=$($result.Code) payload=$($result.Payload | ConvertTo-Json -Compress)"
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

Test-Case 'Resolve-AutoOSOllamaBaseUrl: OLLAMA_BASE_URL wins and is normalised to /v1' {
    $saved = $env:OLLAMA_BASE_URL
    try {
        $env:OLLAMA_BASE_URL = 'http://gpu-box:11434/'
        Assert-Equal (Resolve-AutoOSOllamaBaseUrl -Probe { param($u) $true }) 'http://gpu-box:11434/v1'
    } finally { $env:OLLAMA_BASE_URL = $saved }
}

Test-Case 'Resolve-AutoOSOllamaBaseUrl: host.docker.internal only when Ollama answers there' {
    $saved = $env:OLLAMA_BASE_URL
    try {
        $env:OLLAMA_BASE_URL = $null
        Assert-Equal (Resolve-AutoOSOllamaBaseUrl -Probe { param($u) $u -like '*host.docker.internal:11434/api/version' }) 'http://host.docker.internal:11434/v1'
        # No answer there (native Ollama bound to 127.0.0.1, no Docker Desktop):
        # $null keeps the catalog's 127.0.0.1, which a host-run agent-canvas needs.
        Assert-Equal (Resolve-AutoOSOllamaBaseUrl -Probe { param($u) $false }) $null
    } finally { $env:OLLAMA_BASE_URL = $saved }
}

function Get-AutoOSConsoleCapture {
    <#
      .SYNOPSIS Run a scriptblock while Console.Out is redirected, returning what it wrote.
      .DESCRIPTION
        Write-AutoOSLine writes via [Console]::WriteLine/Write, not Write-Information,
        so `6>&1` alone does not capture it (verified: only actual Write-Information/
        Write-Host output crosses stream 6 - a bare Console.WriteLine bypasses the
        PowerShell stream pipeline entirely). Redirecting Console.Out for the
        duration of the call is the only way to capture that text in-process.
    #>
    param([Parameter(Mandatory)][scriptblock]$Body)
    $origOut = [Console]::Out
    $sw = New-Object IO.StringWriter
    [Console]::SetOut($sw)
    try { & $Body | Out-Null } finally { [Console]::SetOut($origOut) }
    $sw.ToString()
}

# tests/fixtures/serena/<case>.yml -> <case>.expected.yml is the ONE set of
# YAML shapes both Set-AutoOSSerenaExclusions (here) and ensure_serena_exclusions
# (tests/run-tests.sh) are graded against, so the two implementations cannot
# quietly drift apart. Comparison is byte-for-byte (base64 of the raw bytes),
# not line-based - CRLF, a UTF-8 BOM or a trailing blank line is a real diff.
$autoosSerenaFixtureDir = Join-Path $Root 'tests\fixtures\serena'
Get-ChildItem -Path $autoosSerenaFixtureDir -Filter '*.yml' | Where-Object { $_.Name -notlike '*.expected.yml' } | ForEach-Object {
    $fixtureFile = $_
    $fixtureName = $_.BaseName
    $expectedPath = Join-Path $autoosSerenaFixtureDir "$fixtureName.expected.yml"

    Test-Case "Set-AutoOSSerenaExclusions matches fixture '$fixtureName' byte-for-byte (serena)" {
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-fx-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        $p = Join-Path $tmp 'config.yml'
        Copy-Item -Path $fixtureFile.FullName -Destination $p
        try {
            Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
            Set-AutoOSSerenaExclusions -ConfigPath $p 6>$null
            $gotB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            $expB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($expectedPath))
            Assert-Equal $gotB64 $expB64
        } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Test-Case "Set-AutoOSSerenaExclusions second run on fixture '$fixtureName' skips and writes nothing (serena)" {
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-fx-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        $p = Join-Path $tmp 'config.yml'
        Copy-Item -Path $fixtureFile.FullName -Destination $p
        try {
            Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
            Set-AutoOSSerenaExclusions -ConfigPath $p 6>$null
            $hash1 = (Get-FileHash -Path $p -Algorithm SHA256).Hash
            $out = Get-AutoOSConsoleCapture { Set-AutoOSSerenaExclusions -ConfigPath $p 6>&1 }
            $hash2 = (Get-FileHash -Path $p -Algorithm SHA256).Hash
            Assert-True ($out -like '*skipped*') "expected 'skipped' in output, got: [$out]"
            Assert-Equal $hash2 $hash1
            Assert-Equal @(Get-ChildItem -Path $tmp -Filter '*.autoos-backup-*').Count 1
        } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

Test-Case 'Set-AutoOSSerenaExclusions creates a missing config with just the key (serena)' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'nested\serena_config.yml'
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Set-AutoOSSerenaExclusions -ConfigPath $p 6>$null

        $got = @(Get-Content -Path $p) | Where-Object { $_ -match '^- ' } | ForEach-Object { $_ -replace '^- ', '' }
        $want = @('create_text_file', 'read_file', 'execute_shell_command', 'list_dir', 'search_for_pattern',
            'find_file', 'replace_content', 'replace_in_files', 'onboarding', 'write_memory', 'read_memory',
            'list_memories', 'edit_memory', 'rename_memory', 'delete_memory')
        Assert-Equal ($got -join ',') ($want -join ',')
        Assert-Equal @(Get-Content -Path $p).Count 16
        Assert-Equal @(Get-ChildItem -Path $tmp -Recurse -Filter '*.autoos-backup-*').Count 0
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Set-AutoOSSerenaExclusions skips on the first run when all 15 are already present, any order (serena)' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'serena_config.yml'
    $body = "excluded_tools:`r`n- delete_memory`r`n- create_text_file`r`n- read_file`r`n- execute_shell_command`r`n" +
            "- list_dir`r`n- search_for_pattern`r`n- find_file`r`n- replace_content`r`n- replace_in_files`r`n" +
            "- onboarding`r`n- write_memory`r`n- read_memory`r`n- list_memories`r`n- edit_memory`r`n- rename_memory`r`n"
    [IO.File]::WriteAllText($p, $body)
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        $hash1 = (Get-FileHash -Path $p -Algorithm SHA256).Hash
        $out = Get-AutoOSConsoleCapture { Set-AutoOSSerenaExclusions -ConfigPath $p 6>&1 }
        $hash2 = (Get-FileHash -Path $p -Algorithm SHA256).Hash
        Assert-True ($out -like '*skipped*') "expected 'skipped' in output, got: [$out]"
        Assert-Equal $hash2 $hash1
        Assert-Equal @(Get-ChildItem -Path $tmp -Filter '*.autoos-backup-*').Count 0
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Set-AutoOSSerenaExclusions on a directory path warns and writes nothing (serena)' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $adir = Join-Path $tmp 'adir'
    New-Item -ItemType Directory -Path $adir -Force | Out-Null
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        $out = Get-AutoOSConsoleCapture { Set-AutoOSSerenaExclusions -ConfigPath $adir 6>&1 }
        Assert-True ($out -like '*directory*') "expected a directory warning, got: [$out]"
        Assert-True (Test-Path $adir -PathType Container) 'the directory itself must survive'
        Assert-Equal @(Get-ChildItem -Path $adir -Force).Count 0
        Assert-Equal @(Get-ChildItem -Path $tmp -Filter '*.autoos-backup-*').Count 0
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

# CRLF and a raw non-UTF-8 byte can't live in a committed *.yml fixture -
# .gitattributes forces `*.yml text eol=lf`, which would silently rewrite a
# checked-in CRLF fixture to LF and defeat the point of the test. These two
# build their own bytes at run time instead, exactly like their bash twins.
Test-Case 'Set-AutoOSSerenaExclusions preserves CRLF line endings and untouched content byte-for-byte (serena)' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'serena_config.yml'
    $enc = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllBytes($p, $enc.GetBytes("# top comment`r`nother_key: 1`r`nexcluded_tools:`r`n- read_file`r`n- my_tool`r`nlast_key: x`r`n"))
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Set-AutoOSSerenaExclusions -ConfigPath $p 6>$null
        $bytes = [IO.File]::ReadAllBytes($p)
        $cr = @($bytes | Where-Object { $_ -eq 0x0D }).Count
        $lf = @($bytes | Where-Object { $_ -eq 0x0A }).Count
        Assert-Equal $cr $lf
        Assert-True ($cr -gt 0) 'expected at least one CRLF pair'
        $firstLine = (Get-Content -Path $p)[0]
        Assert-Equal $firstLine '# top comment'
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Set-AutoOSSerenaExclusions preserves a non-UTF-8 byte outside the block untouched (serena)' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-serena-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'serena_config.yml'
    $prefix = [Text.Encoding]::ASCII.GetBytes("excluded_tools:`n- read_file`n#comment with byte: ")
    $suffix = [Text.Encoding]::ASCII.GetBytes("`nlast_key: x`n")
    $bytes = $prefix + [byte[]](0xE9) + $suffix
    [IO.File]::WriteAllBytes($p, $bytes)
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Set-AutoOSSerenaExclusions -ConfigPath $p 6>$null
        $result = [IO.File]::ReadAllBytes($p)
        $expectedTail = [Text.Encoding]::ASCII.GetBytes("#comment with byte: ") + [byte[]](0xE9) + [Text.Encoding]::ASCII.GetBytes("`nlast_key: x`n")
        $tail = $result[($result.Length - $expectedTail.Length)..($result.Length - 1)]
        Assert-Equal ([BitConverter]::ToString($tail)) ([BitConverter]::ToString($expectedTail))
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Add-AutoOSProfileLine -Prepend puts the line first, exactly once' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-prof-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'Microsoft.PowerShell_profile.ps1'
    Set-Content -Path $p -Value 'Write-Host slow-profile' -Encoding utf8
    $line = "if (`$env:AI_AGENT -eq 'openhands') { return }"
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line $line -Marker "AI_AGENT -eq 'openhands'" 6>$null
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line $line -Marker "AI_AGENT -eq 'openhands'" 6>$null
        $lines = @(Get-Content -Path $p)
        Assert-Equal $lines[0] '# added by AutoOS'
        Assert-Equal $lines[1] $line
        Assert-Equal @($lines | Where-Object { $_ -like '*AI_AGENT*' }).Count 1
        Assert-Contains $lines 'Write-Host slow-profile'
        # rule 5: the user's profile was backed up before the first write, once
        Assert-Equal @(Get-ChildItem -Path $tmp -Filter '*.autoos-backup-*').Count 1
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Add-AutoOSProfileLine -Prepend keeps an ANSI profile byte-for-byte' {
    # A Windows PowerShell 5.1 profile without a BOM is read as the ANSI code page.
    # Re-encoding it as UTF-8 would turn every non-ASCII byte into U+FFFD.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-prof-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'Microsoft.PowerShell_profile.ps1'
    $orig = [byte[]](0x23, 0x20, 0x93, 0x20, 0x81, 0x66, 0xE9, 0x0D, 0x0A)   # cp1252 quote, a byte cp1252 leaves undefined, é
    [IO.File]::WriteAllBytes($p, $orig)
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line 'X-GUARD' -Marker 'X-GUARD' 6>$null
        $after = [IO.File]::ReadAllBytes($p)
        $tail = $after[($after.Length - $orig.Length)..($after.Length - 1)]
        Assert-Equal ([BitConverter]::ToString($tail)) ([BitConverter]::ToString($orig))
        Assert-True ($after[0] -eq 0x23) 'a BOM was added to an ANSI profile'
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Add-AutoOSProfileLine -Prepend goes after a using/param preamble' {
    # `using` and `param` must be a script's first statements; a guard above them
    # would make every PowerShell start fail with a parse error.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-prof-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'Microsoft.PowerShell_profile.ps1'
    [IO.File]::WriteAllText($p, "using namespace System.Text`r`nparam([string]`$x)`r`nWrite-Host slow-profile`r`n")
    $line = "if (`$env:AI_AGENT -eq 'openhands') { return }"
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line $line -Marker "AI_AGENT -eq 'openhands'" 6>$null
        $lines = @(Get-Content -Path $p)
        Assert-Equal $lines[0] 'using namespace System.Text'
        Assert-Equal $lines[1] 'param([string]$x)'
        Assert-Equal $lines[3] $line
        Assert-Equal $lines[4] 'Write-Host slow-profile'
        $errs = $null
        [void][Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs)
        Assert-Equal @($errs).Count 0
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'Set-AutoOSOpenHandsConfig prepends the OpenHands guard to both existing profiles' {
    $body = (Get-Command Set-AutoOSOpenHandsConfig).Definition
    Assert-True ($body -match 'Add-AutoOSProfileLine -Prepend') 'no -Prepend guard call'
    Assert-True ($body -match "PowerShell\\Microsoft\.PowerShell_profile\.ps1") 'pwsh profile not guarded'
    Assert-True ($body -match "WindowsPowerShell\\Microsoft\.PowerShell_profile\.ps1") 'Windows PowerShell profile not guarded'
}

Test-Case 'the embedded OpenHands setup script writes the resolved Ollama address' {
    # Runs the real here-string against a temp ~/.openhands. USERPROFILE, HOME
    # and LOCALAPPDATA point into the temp dir too, so its uv-cache walk finds
    # nothing and no file outside the temp dir is read or written.
    $py = Get-Command python, python3 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $py) { Skip 'no python on PATH'; return }
    $src = Get-Content (Join-Path $Root 'lib\windows\AutoOS.Install.psm1') -Raw -Encoding UTF8
    $script = [regex]::Match($src, "(?s)\`$setupScript = @'\r?\n(.*?)\r?\n'@").Groups[1].Value
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "autoos-oh-e2e-$PID"
    $oh = Join-Path $tmp '.openhands'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $oh 'profiles'), (Join-Path $oh 'agent-profiles')
    $saved = @{ OLLAMA_BASE_URL = $env:OLLAMA_BASE_URL; USERPROFILE = $env:USERPROFILE; HOME = $env:HOME; LOCALAPPDATA = $env:LOCALAPPDATA }
    try {
        $env:OLLAMA_BASE_URL = 'http://ollama:11434/v1'
        $env:USERPROFILE = $tmp; $env:HOME = $tmp; $env:LOCALAPPDATA = $tmp
        & $py.Source -c $script $oh 'null' 'null' 'null' 'null' $Root *> $null
        $profile = Get-Content (Join-Path $oh 'profiles\ollama-qwen2.5-coder.json') -Raw | ConvertFrom-Json
        $settings = Get-Content (Join-Path $oh 'settings.json') -Raw | ConvertFrom-Json
        Assert-Equal $profile.base_url 'http://ollama:11434/v1'
        Assert-Equal $settings.agent_settings.llm.base_url 'http://ollama:11434/v1'
        Assert-True (Test-Path (Join-Path $oh 'agent-profiles\orchestrator.json')) 'vendored agent profiles not copied'
        # OpenHands ignores an MCP timeout today and may soon read it as milliseconds (#3254)
        Assert-True (-not @($settings.agent_settings.mcp_config.PSObject.Properties.Value | Where-Object { $_.PSObject.Properties.Name -contains 'timeout' })) 'an MCP server still has a timeout'
    } finally {
        foreach ($k in $saved.Keys) {
            # an unset variable must be unset again, not left pointing at $tmp
            if ($null -eq $saved[$k]) { Remove-Item "env:$k" -ErrorAction SilentlyContinue }
            else { Set-Item "env:$k" -Value $saved[$k] }
        }
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'the embedded OpenHands setup script is valid Python' {
    # Set-AutoOSOpenHandsConfig pipes a literal here-string to `python -c`.
    # Nothing else parses it before a real install, so a missing `except`
    # once shipped and silently broke every Windows OpenHands setup.
    $py = Get-Command python, python3 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $py) { Skip 'no python on PATH'; return }
    $src = Get-Content (Join-Path $Root 'lib\windows\AutoOS.Install.psm1') -Raw -Encoding UTF8
    $m = [regex]::Match($src, "(?s)\`$setupScript = @'\r?\n(.*?)\r?\n'@")
    Assert-True $m.Success 'setupScript here-string not found'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "autoos-oh-setup-$PID.py"
    [IO.File]::WriteAllText($tmp, $m.Groups[1].Value)
    try {
        $out = & $py.Source -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' $tmp 2>&1
        Assert-True ($LASTEXITCODE -eq 0) "embedded python does not parse: $out"
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
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
