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
Import-Module (Join-Path $Lib 'AutoOS.Serve.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $Lib 'AutoOS.State.psm1')   -Force -DisableNameChecking

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

# â”€â”€â”€ Catalog schema â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Fixture-driven filtering â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Dependency resolution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Detection heuristics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ PATH handling (the critical regression) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ UI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

Test-Case 'log lines are classified for the browser UI' {
    Assert-Equal (Get-AutoOSLineLevel '  + installed') 'ok'
}

Test-Case 'plain log lines get no class' {
    Assert-Equal (Get-AutoOSLineLevel 'just some text') ''
}

# â”€â”€â”€ End to end â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Run state, verification, undo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Browser UI payload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€ Static analysis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    # Only shipped files are policed: anything git ignores (local-only helpers
    # like additional_Features/, per-machine .serena/) is skipped.
    $missing = @()
    $haveGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
    foreach ($f in Get-ChildItem -Path $Root -Include *.ps1, *.psm1 -Recurse -File) {
        if ($f.FullName -match '\\\.(git|serena)\\') { continue }
        if ($haveGit) {
            git -C $Root check-ignore -q $f.FullName 2>$null
            if ($LASTEXITCODE -eq 0) { continue }
        }
        $bytes = [IO.File]::ReadAllBytes($f.FullName)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            $missing += $f.Name
        }
    }
    Assert-Equal ($missing -join ',') ''
}

# â”€â”€â”€ AI routing stack (omniroute / litellm / zed / opencode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Describe-Group 'ai routing'

function Get-AutoOSWinComponent {
    param([string]$Id)
    foreach ($cat in $winCatalog.categories) {
        foreach ($c in $cat.components) { if ($c.id -eq $Id) { return $c } }
    }
    return $null
}

Test-Case 'ai routing components exist with the right providers' {
    $zed = Get-AutoOSWinComponent 'zed'
    $lit = Get-AutoOSWinComponent 'litellm'
    $oc = Get-AutoOSWinComponent 'opencode-cli'
    $om = Get-AutoOSWinComponent 'omniroute'
    $oh = Get-AutoOSWinComponent 'openhands'
    Assert-True ($null -ne $zed -and $null -ne $lit -and $null -ne $oc -and $null -ne $om -and $null -ne $oh) 'missing component'
    Assert-Equal "$($zed.provider)|$($lit.provider)|$($oc.provider)|$($om.provider)|$($oh.provider)" 'winget|custom|npm|npm|custom'
    Assert-Equal "$($zed.package)|$($oc.package)|$($om.package)" 'ZedIndustries.Zed|@opencode/cli|omniroute'
    Assert-Equal $oh.postInstall 'Install-AutoOSOpenHands'
    Assert-True ($oh.verify -match 'docker\.openhands\.dev') 'openhands verify does not check the current image'
}

Test-Case 'zed routes through litellm and both ride ai-coding' {
    $zed = Get-AutoOSWinComponent 'zed'
    Assert-Contains $zed.requires 'litellm'
    Assert-Contains $zed.profiles 'ai-coding'
    Assert-Contains (Get-AutoOSWinComponent 'litellm').profiles 'ai-coding'
    Assert-Contains (Get-AutoOSWinComponent 'omniroute').profiles 'ai-coding'
}

Test-Case 'opencode-cli is the headless fallback on light' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $light = @($a | Where-Object { 'light' -in $_.Profiles } | ForEach-Object { $_.Id })
    Assert-Contains $light 'opencode-cli'
}

Test-Case 'ai postInstall hooks are exported' {
    foreach ($fn in @('Install-AutoOSLitellm', 'Set-AutoOSZedProxy')) {
        Assert-True ($null -ne (Get-Command $fn -ErrorAction SilentlyContinue)) "$fn missing"
    }
    Pass
}

Test-Case 'litellm installer announces instead of running in dry run' {
    Initialize-AutoOSInstaller -DryRun $true -RepoRoot $Root
    # Write-AutoOSLine goes to the console and the module log, not the output
    # stream: capture it through a scratch log file, the only reliable way.
    $log = Join-Path ([IO.Path]::GetTempPath()) "autoos-dry-$([Guid]::NewGuid().ToString('N')).log"
    try {
        Initialize-AutoOSLog -Path $log
        Install-AutoOSLitellm
        $text = Get-Content $log -Raw -Encoding utf8
        Assert-True ($text -match 'would install litellm') 'no dry-run announcement in the log'
    } finally {
        Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
        Remove-Item $log -Force -ErrorAction SilentlyContinue
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
    }
}

Test-Case 'zed routing writes nothing in dry run' {
    $realAppData = $env:APPDATA
    $scratch = Join-Path $env:TEMP "autoos-apex-$([Guid]::NewGuid().ToString('N'))"
    try {
        $env:APPDATA = $scratch
        Initialize-AutoOSInstaller -DryRun $true -RepoRoot $Root
        Set-AutoOSZedProxy
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Assert-Equal (Test-Path (Join-Path $scratch 'Zed\settings.json')) $false
    } finally { $env:APPDATA = $realAppData }
}

Test-Case 'zed routing merges one provider and keeps the rest' {
    $realAppData = $env:APPDATA
    $scratch = Join-Path $env:TEMP "autoos-apex-$([Guid]::NewGuid().ToString('N'))"
    try {
        $env:APPDATA = $scratch
        $cfgDir = Join-Path $scratch 'Zed'
        New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
        '{"theme":"mine","language_models":{"openai":{"api_url":"https://x"}}}' |
            Out-File (Join-Path $cfgDir 'settings.json') -Encoding utf8
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Set-AutoOSZedProxy
        Set-AutoOSZedProxy
        $s = Get-Content (Join-Path $cfgDir 'settings.json') -Raw | ConvertFrom-Json
        Assert-Equal $s.theme 'mine'
        Assert-Equal $s.language_models.openai_compatible.'autoos-omniroute'.api_url 'http://127.0.0.1:20128/v1'
        $models = @($s.language_models.openai_compatible.'autoos-omniroute'.available_models | ForEach-Object { $_.name })
        Assert-Equal ($models -join ',') 'auto/smart,auto,auto/cheap'
        Assert-True ((@(Get-ChildItem $cfgDir -Filter '*.autoos-backup-*')).Count -ge 1) 'no backup written'
        $raw = Get-Content (Join-Path $cfgDir 'settings.json') -Raw
        Assert-True ($raw -notmatch 'sk-' -and $raw -notmatch 'AUTOOS_OMNIROUTE_KEY|api_key') 'secret leaked into settings'
    } finally { $env:APPDATA = $realAppData }
}

Test-Case 'opencode repo config pins omniroute with litellm fallback' {
    $raw = Get-Content (Join-Path $Root 'opencode.jsonc') -Raw -Encoding utf8
    $stripped = $raw -replace '(?m)^\s*//.*$', ''
    $oc = $stripped | ConvertFrom-Json
    Assert-Equal $oc.model 'omniroute/tier1'
    Assert-Equal $oc.providers.omniroute.settings.baseURL 'http://127.0.0.1:20128/v1'
    Assert-Equal (@($oc.providers.omniroute.models.PSObject.Properties.Name | Sort-Object) -join ',') 'auto,auto/cheap,auto/smart,tier1,tier1-clean,tier2,tier2-clean,tier3,tier3-clean'
    Assert-True ($null -ne $oc.providers.litellm) 'litellm fallback missing'
    Assert-Equal (@($oc.mcp.servers.PSObject.Properties.Name | Sort-Object) -join ',') 'graphify,playwright,serena'
}

Test-Case 'openhands template routes tiers with no secrets' {
    $toml = Get-Content (Join-Path $Root 'configuration\openhands\config.toml') -Raw -Encoding utf8
    foreach ($section in @('[llm]', '[llm.tier1]', '[llm.tier2]', '[llm.tier3]', '[llm.draft_editor]', '[agent.CodeActAgent]')) {
        Assert-True ($toml -match [regex]::Escape($section)) "missing $section"
    }
    Assert-True ($toml -match 'host\.docker\.internal:20128') 'not pointed at the gateway'
    Assert-True ($toml -notmatch 'sk-[A-Za-z0-9]{10,}') 'credential-shaped value committed'
    Pass
}

Test-Case 'litellm fallback config is internally consistent' {
    $yaml = Get-Content (Join-Path $Root 'configuration\litellm\config.yaml') -Raw -Encoding utf8
    $groups = @([regex]::Matches($yaml, '(?m)^\s*-\s*model_name:\s*(\S+)\s*$') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    foreach ($g in @('tier1', 'tier1-paid', 'tier2', 'tier2-paid', 'tier3', 'tier3-paid')) {
        Assert-Contains $groups $g
    }
    $fb = [regex]::Match($yaml, '(?s)fallbacks:(.*?)(?:\r?\n\S|\z)').Groups[1].Value
    foreach ($m in [regex]::Matches($fb, '[- ](\S+):\s*\[([^\]]*)\]')) {
        Assert-Contains $groups $m.Groups[1].Value
        foreach ($t in ($m.Groups[2].Value -split ',')) { Assert-Contains $groups $t.Trim() }
    }
    $models = [regex]::Matches($yaml, '(?m)^\s*model:\s*(\S+)\s*$') | ForEach-Object { $_.Groups[1].Value }
    Assert-True (($models -match 'cerebras').Count -eq 0 -and ($models -match 'llama-3\.3-70b').Count -eq 0) 'retired free legs still routed'
    Assert-True ($yaml -notmatch 'sk-[A-Za-z0-9]{10,}') 'credential-shaped value committed'
    Pass
}

Test-Case 'start scripts exist and name the client key' {
    Assert-True (Test-Path (Join-Path $Root 'configuration\start-stack.ps1')) 'ps1 missing'
    Assert-True (Test-Path (Join-Path $Root 'configuration\start-stack.sh')) 'sh missing'
    $ps1 = Get-Content (Join-Path $Root 'configuration\start-stack.ps1') -Raw
    $sh = Get-Content (Join-Path $Root 'configuration\start-stack.sh') -Raw
    Assert-True ($ps1 -match 'AUTOOS_OMNIROUTE_KEY' -and $sh -match 'AUTOOS_OMNIROUTE_KEY') 'key wiring missing'
    Assert-True ($ps1 -match 'openhands' -and $sh -match 'host\.docker\.internal') 'openhands launch missing'
    Pass
}

Test-Case 'start-stack.ps1 parses without syntax errors' {
    $errors = $null
    $null = [System.Management.Automation.PSParser]::Tokenize(
        (Get-Content (Join-Path $Root 'configuration\start-stack.ps1') -Raw), [ref]$errors)
    Assert-Equal (@($errors)).Count 0
}

Test-Case 'zed routing creates a fresh config when none exists' {
    $realAppData = $env:APPDATA
    $scratch = Join-Path $env:TEMP "autoos-apex-$([Guid]::NewGuid().ToString('N'))"
    try {
        $env:APPDATA = $scratch
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Set-AutoOSZedProxy
        $cfgPath = Join-Path $scratch 'Zed\settings.json'
        Assert-True (Test-Path $cfgPath) 'settings.json not created'
        $s = Get-Content $cfgPath -Raw | ConvertFrom-Json
        Assert-Equal $s.language_models.openai_compatible.'autoos-omniroute'.available_models.Count 3
    } finally { $env:APPDATA = $realAppData }
}

Test-Case 'component profiles name real profiles and verify is set' {
    $bad = @()
    foreach ($file in @('catalog\windows.json', 'catalog\linux.json', 'catalog\macos.json')) {
        $c = Get-Content (Join-Path $Root $file) -Raw -Encoding utf8 | ConvertFrom-Json
        $profiles = @($c.profiles.PSObject.Properties.Name)
        foreach ($cat in $c.categories) {
            foreach ($comp in $cat.components) {
                if ($comp.id -notin @('zed', 'litellm', 'opencode-cli', 'omniroute')) { continue }
                foreach ($p in $comp.profiles) {
                    if ($p -notin $profiles) { $bad += "$file/$($comp.id):$p" }
                }
                if ([string]::IsNullOrWhiteSpace($comp.verify)) { $bad += "$file/$($comp.id):no-verify" }
            }
        }
    }
    Assert-Equal ($bad -join ',') ''
}

Test-Case 'ai-coding ticks the whole routing stack' {
    $a = @(Get-AutoOSAvailableComponents -Catalog $winCatalog -SystemInfo (New-FakeSystem))
    $ticks = @($a | Where-Object { 'ai-coding' -in $_.Profiles } | ForEach-Object { $_.Id })
    foreach ($id in @('zed', 'litellm', 'opencode-cli', 'omniroute', 'openhands')) {
        Assert-Contains $ticks $id
    }
    Pass
}

# ── OpenHands self-checks (need Docker; they report, never install) ─────────
# The container takes a while to boot agent-server images, so these wait.
function Wait-AutoOSHttp {
    param([string]$Url, [int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            if ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { return $true }
        } catch { Start-Sleep 5 }
    }
    return $false
}

Test-Case 'openhands container answers on :3000' {
    $names = (& docker ps --format '{{.Names}}' 2>$null) -join "`n"
    if ($names -notmatch 'openhands') { Skip 'no openhands container running'; return }
    Assert-True (Wait-AutoOSHttp 'http://localhost:3000/') 'OpenHands UI did not answer on :3000'
}

Test-Case 'openhands llm profile points at the gateway' {
    $settingsPath = Join-Path $env:USERPROFILE '.openhands\settings.json'
    if (-not (Test-Path $settingsPath)) { Skip 'no ~/.openhands/settings.json yet'; return }
    $settings = Get-Content $settingsPath -Raw -Encoding utf8 | ConvertFrom-Json
    $llm = $settings.agent_settings.llm
    Assert-True ($null -ne $llm) 'agent_settings.llm missing'
    Assert-Equal $llm.base_url 'http://host.docker.internal:20128/v1'
    Assert-True ($llm.model -match '^openai/') 'model lacks the openai/ LiteLLM prefix'
}

Test-Case 'litellm installer delegates to pipx when present' {
    $realPath = $env:PATH
    $stub = Join-Path $env:TEMP "autoos-pipxstub-$([Guid]::NewGuid().ToString('N'))"
    $log = Join-Path ([IO.Path]::GetTempPath()) "autoos-pipx-$([Guid]::NewGuid().ToString('N')).log"
    try {
        New-Item -ItemType Directory -Path $stub -Force | Out-Null
        '@echo off' + "`r`n" + 'echo called > "%~dp0called.txt"' |
            Out-File (Join-Path $stub 'pipx.cmd') -Encoding ascii
        $env:PATH = "$stub;$realPath"
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Initialize-AutoOSLog -Path $log
        Install-AutoOSLitellm
        $text = Get-Content $log -Raw -Encoding utf8
        Assert-True (Test-Path (Join-Path $stub 'called.txt')) 'pipx stub was not invoked'
        Assert-True ($text -match 'configuration/litellm') 'no next-steps hint in the log'
    } finally {
        # Dead log path: Initialize-AutoOSLog always rewrites it, while ''
        # would only hide the leak until the next Initialize-AutoOSLog call.
        Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
        $env:PATH = $realPath
        Remove-Item $log -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'litellm installer falls back to the py launcher' {
    $realPath = $env:PATH
    $stub = Join-Path $env:TEMP "autoos-pystub-$([Guid]::NewGuid().ToString('N'))"
    $log = Join-Path ([IO.Path]::GetTempPath()) "autoos-py-$([Guid]::NewGuid().ToString('N')).log"
    try {
        New-Item -ItemType Directory -Path $stub -Force | Out-Null
        '@echo off' + "`r`n" + 'echo called > "%~dp0called.txt"' |
            Out-File (Join-Path $stub 'py.cmd') -Encoding ascii
        $env:PATH = "$stub;$env:SystemRoot\System32"
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Initialize-AutoOSLog -Path $log
        Install-AutoOSLitellm
        Assert-True (Test-Path (Join-Path $stub 'called.txt')) 'py launcher stub was not invoked'
    } finally {
        # Dead log path: Initialize-AutoOSLog always rewrites it, while ''
        # would only hide the leak until the next Initialize-AutoOSLog call.
        Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
        $env:PATH = $realPath
        Remove-Item $log -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'litellm installer detects an existing install' {
    $realPath = $env:PATH
    $stub = Join-Path $env:TEMP "autoos-litstub-$([Guid]::NewGuid().ToString('N'))"
    $log = Join-Path ([IO.Path]::GetTempPath()) "autoos-lit-$([Guid]::NewGuid().ToString('N')).log"
    try {
        New-Item -ItemType Directory -Path $stub -Force | Out-Null
        '@echo off' + "`r`n" + 'exit /b 0' | Out-File (Join-Path $stub 'litellm.cmd') -Encoding ascii
        $env:PATH = "$stub;$realPath"
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Initialize-AutoOSLog -Path $log
        Install-AutoOSLitellm
        $text = Get-Content $log -Raw -Encoding utf8
        Assert-True ($text -match 'already installed') 'no already-installed message in the log'
    } finally {
        # Dead log path: Initialize-AutoOSLog always rewrites it, while ''
        # would only hide the leak until the next Initialize-AutoOSLog call.
        Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
        $env:PATH = $realPath
        Remove-Item $log -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'litellm installer warns when no python is available' {
    $realPath = $env:PATH
    $empty = Join-Path $env:TEMP "autoos-noempty-$([Guid]::NewGuid().ToString('N'))"
    $log = Join-Path ([IO.Path]::GetTempPath()) "autoos-nopy-$([Guid]::NewGuid().ToString('N')).log"
    try {
        New-Item -ItemType Directory -Path $empty -Force | Out-Null
        $env:PATH = $empty
        Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
        Initialize-AutoOSLog -Path $log
        Install-AutoOSLitellm
        $text = Get-Content $log -Raw -Encoding utf8
        Assert-True ($text -match 'no Python on PATH') 'no missing-python warning in the log'
    } finally {
        # Dead log path: Initialize-AutoOSLog always rewrites it, while ''
        # would only hide the leak until the next Initialize-AutoOSLog call.
        Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
        $env:PATH = $realPath
        Remove-Item $log -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'env template carries placeholders only' {
    $bad = @()
    $n = 0
    foreach ($line in (Get-Content (Join-Path $Root 'configuration\litellm\.env.example') -Encoding utf8)) {
        $n++
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        if ($t -notmatch '^[A-Z_]+=REPLACE_WITH_[A-Z_]+$') { $bad += "line $n" }
    }
    Assert-Equal ($bad -join ',') ''
}

Test-Case 'api-keys example carries placeholders only' {
    $bad = @()
    $n = 0
    foreach ($line in (Get-Content (Join-Path $Root 'configuration\api-keys.example.yml') -Encoding utf8)) {
        $n++
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $val = ($t -split ':', 2)[1].Trim()
        if ($val -notmatch '^REPLACE_WITH_[A-Z_]+$') { $bad += "line $n" }
    }
    Assert-Equal ($bad -join ',') ''
}

Test-Case 'provider status reads keys but never exposes them' {
    $scratch = Join-Path $env:TEMP "autoos-keys-$([Guid]::NewGuid().ToString('N'))"
    try {
        New-Item -ItemType Directory -Path (Join-Path $scratch 'configuration') -Force | Out-Null
        @(
            'groq: gsk_SUPERSECRETVALUE123',
            '# comment',
            'deepseek:',
            'mistral: 5xLsSecretValue',
            'not-a-provider: nope'
        ) | Out-File (Join-Path $scratch 'configuration\api-keys.yml') -Encoding utf8
        $status = @(Get-AutoOSProviderStatus -RepoRoot $scratch)
        $json = $status | ConvertTo-Json
        Assert-True ($status.Count -eq 13) "expected 13 provider entries, got $($status.Count)"
        $groq = $status | Where-Object { $_.id -eq 'groq' }
        $ds = $status | Where-Object { $_.id -eq 'deepseek' }
        Assert-True ($groq.configured -and -not $ds.configured) 'configured flags are wrong'
        Assert-True ($json -notmatch 'SUPERSECRET|SecretValue') 'a key value leaked into the payload'
    } finally { Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case 'combos.json is valid, named and provider/model shaped' {
    $combos = (Get-Content (Join-Path $Root 'configuration\omniroute\combos.json') -Raw -Encoding utf8 |
        ConvertFrom-Json).combos
    $names = @($combos | ForEach-Object { $_.name })
    Assert-Equal ($names -join ',') 'tier1,tier1-clean,tier2,tier2-clean,tier3,tier3-clean'
    $contexts = @{
        'tier1' = '1M'; 'tier1-clean' = '1M'; 'tier2' = '128k'
        'tier2-clean' = '128k'; 'tier3' = '128k'; 'tier3-clean' = '128k'
    }
    foreach ($c in $combos) {
        Assert-True ($c.models.Count -ge 1) "$($c.name) has no models"
        foreach ($m in $c.models) {
            Assert-True ($m -match '^[A-Za-z0-9@._/-]+$') "$($c.name): bad ref '$m'"
        }
        Assert-Equal $c.context $contexts[$c.name]
    }
}

Test-Case 'apply --dry-run registers nothing and starts nothing' {
    # The suite runs $ErrorActionPreference = 'Stop', but the omniroute CLI
    # writes a harmless warning to stderr on every call, which 5.1 promotes
    # to a terminating error. So the -DryRun call runs in a child powershell:
    # a stray preference or a non-zero exit can never abort the suite, and
    # only the captured text counts. combos.json must be byte-identical after.
    $combosPath = Join-Path $Root 'configuration\omniroute\combos.json'
    $before = Get-Content $combosPath -Raw -Encoding utf8
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $Root 'configuration\omniroute\apply.ps1') -DryRun 2>&1 | Out-String
    Assert-True ($out -match 'dry run stops here|would create|would register') 'dry run announced nothing'
    Assert-Equal (Get-Content $combosPath -Raw -Encoding utf8) $before
}

Test-Case 'apply scripts carry the Cloudflare User-Agent fix' {
    $ps1 = Get-Content (Join-Path $Root 'configuration\omniroute\apply.ps1') -Raw
    $sh = Get-Content (Join-Path $Root 'configuration\omniroute\apply.sh') -Raw
    foreach ($text in @($ps1, $sh)) {
        Assert-True ($text -match 'customUserAgent') 'customUserAgent missing'
        Assert-True ($text -match 'provider-specific-data') 'provider-specific-data flag missing'
        Assert-True ($text -match 'muse-code') 'meta -> muse-code mapping missing'
    }
    Pass
}

Test-Case 'opencode tiers declare matching context limits' {
    $raw = Get-Content (Join-Path $Root 'opencode.jsonc') -Raw -Encoding utf8
    $stripped = $raw -replace '(?m)^\s*//.*$', ''
    $oc = $stripped | ConvertFrom-Json
    $models = $oc.providers.omniroute.models
    Assert-Equal $models.tier1.limit.context 1000000
    Assert-Equal $models.'tier1-clean'.limit.context 1000000
    Assert-Equal $models.tier2.limit.context 131072
    Assert-Equal $models.'tier2-clean'.limit.context 131072
    Assert-Equal $models.tier3.limit.context 131072
    Assert-Equal $models.'tier3-clean'.limit.context 131072
    foreach ($name in @('tier1', 'tier1-clean', 'tier2', 'tier2-clean', 'tier3', 'tier3-clean', 'auto', 'auto/cheap', 'auto/smart')) {
        Assert-True ($null -ne $models.$name) "missing model $name"
        Assert-Equal $models.$name.modelID $name
    }
    Pass
}

# â”€â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
