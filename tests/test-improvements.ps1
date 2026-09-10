#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testRoot = Split-Path -Parent $PSScriptRoot
foreach ($module in @('Ui', 'Detect', 'Catalog', 'Install', 'Progress', 'Shell')) {
    Import-Module (Join-Path $testRoot "lib\windows\AutoOS.$module.psm1") -DisableNameChecking
}
function Assert-Improvement { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message }; Write-AutoOSLine $Message -Level ok }

# Synthetic registration, including a lookalike and an MSIX-only app. No
# package manager is invoked to test detection or skipping.
$detectModule = Get-Module AutoOS.Detect
& $detectModule {
    $script:InstalledCache = @{}
    $script:InstalledInventory = [pscustomobject]@{
        Entries = @([pscustomobject]@{ Name = 'Steam Tools'; Package = ''; Key = '' },
                    [pscustomobject]@{ Name = 'Git version 9.0'; Package = 'Git.Git'; Key = '' })
        Paths = @(); Appx = @('Microsoft.PowerShell')
    }
}
Assert-Improvement ((Get-AutoOSInstalledStatus ([pscustomobject]@{Provider='winget'; Package='Git.Git'; Name='Git'})) -eq 'installed') 'exact registered package is installed'
Assert-Improvement ((Get-AutoOSInstalledStatus ([pscustomobject]@{Provider='winget'; Package='Valve.Steam'; Name='Steam'})) -ne 'installed') 'similarly named app is not marked installed'
Assert-Improvement ((Get-AutoOSInstalledStatus ([pscustomobject]@{Provider='winget'; Package='Microsoft.PowerShell'; Name='PowerShell 7'; InstalledAppx=@('Microsoft.PowerShell')})) -eq 'installed') 'MSIX PowerShell is recognized without using injected PATH'
& $detectModule { $script:InstalledCache = @{}; $script:InstalledInventory = $null }

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('autoos-improvements-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $scratch)
try {
    $docs = Join-Path $scratch 'Documents'
    $profilePath = Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1'
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $profilePath) -Force)
    [IO.File]::WriteAllText($profilePath, '# my existing customization')
    Install-AutoOSShellConfiguration -RepoRoot $testRoot -DataRoot $scratch -Documents $docs -DryRun
    Assert-Improvement (-not (Test-Path (Join-Path $scratch 'AutoOS'))) 'shell dry run creates no configuration'
    Install-AutoOSShellConfiguration -RepoRoot $testRoot -DataRoot $scratch -Documents $docs
    $first = [IO.File]::ReadAllText($profilePath)
    $count = @(Get-ChildItem -LiteralPath (Split-Path -Parent $profilePath) -Filter '*.autoos-backup-*').Count
    Install-AutoOSShellConfiguration -RepoRoot $testRoot -DataRoot $scratch -Documents $docs
    Assert-Improvement ($first -eq [IO.File]::ReadAllText($profilePath)) 'second shell setup leaves the profile unchanged'
    Assert-Improvement ($count -eq 1 -and @(Get-ChildItem -LiteralPath (Split-Path -Parent $profilePath) -Filter '*.autoos-backup-*').Count -eq 1) 'shell configuration is backed up once and only when changed'
    Assert-Improvement ($first.Contains('# my existing customization')) 'unrelated profile customizations survive'

    # A child fixture exercises real pipes and command-line quoting, never an installer.
    $fixture = Join-Path $scratch 'child with spaces.ps1'
    [IO.File]::WriteAllText($fixture, 'param([string]$Value) [Console]::Out.WriteLine($Value); [Console]::Error.WriteLine("stderr-final"); exit 7')
    Initialize-AutoOSInstaller -DryRun:$false
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $argument = 'spaces "quoted" trailing\'
    $r = Invoke-AutoOSProcess -FilePath $shell -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fixture, $argument) -SuccessCodes @(7) -TimeoutSeconds 10
    Assert-Improvement ($r.Success -and $r.ExitCode -eq 7 -and $r.Output.Contains($argument) -and $r.Output.Contains('stderr-final')) 'child arguments, stderr and accepted exit codes survive'
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-AutoOSProcess -FilePath $shell -Arguments @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30') -TimeoutSeconds 1
    Assert-Improvement ($r.TimedOut -and -not $r.Success -and $clock.Elapsed.TotalSeconds -lt 8) 'a stalled owned process is stopped within the timeout bound'

    $catalog = Get-AutoOSCatalog (Join-Path $testRoot 'catalog\windows.json')
    $manual = $catalog.categories.components | Where-Object id -eq 'fiio-k3'
    Assert-Improvement ((Install-AutoOSComponent -Component $manual) -eq 'manual') 'vendor setup is action-required, never reported as installed'
    $available = @(Get-AutoOSAvailableComponents -Catalog $catalog -SystemInfo ([pscustomobject]@{Arch='x64'; IsHeadless=$false}))
    $manualItem = New-AutoOSMenuItem -Component ($available | Where-Object Id -eq 'fiio-k3') -Profile everyday
    Assert-Improvement ($manualItem.Locked -and -not $manualItem.Selected) 'manual-only applications are disabled and not preselected'
    $rejected = $false
    try { Resolve-AutoOSPlan -Available $available -SelectedIds @('fiio-k3') | Out-Null } catch { $rejected = $_.Exception.Message -like '*cannot install*' }
    Assert-Improvement $rejected 'direct requests cannot bypass the disabled manual installer'
    & $detectModule {
        $script:InstalledCache = @{}
        $script:InstalledInventory = [pscustomobject]@{Entries=@([pscustomobject]@{Name='Git';Package='Git.Git';Key=''});Paths=@();Appx=@()}
    }
    $git = $available | Where-Object Id -eq 'git'
    Assert-Improvement ((Install-AutoOSComponent $git) -eq 'skipped' -and (Install-AutoOSComponent $git) -eq 'skipped') 'two runs skip an already registered application without invoking its installer'
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $settingsPath = Join-Path $scratch 'winget-settings.json'
        [IO.File]::WriteAllText($settingsPath, '{"visual":{"progressBar":"rainbow"},"network":{"doProgressTimeoutInSeconds":30}}')
        $original = [IO.File]::ReadAllText($settingsPath)
        $helper = Join-Path $testRoot 'Windows/powershell/set_winget_downloader.ps1'
        & $helper -SettingsPath $settingsPath -DryRun
        Assert-Improvement ($original -eq [IO.File]::ReadAllText($settingsPath)) 'downloader preview does not modify settings'
        & $helper -SettingsPath $settingsPath
        & $helper -SettingsPath $settingsPath
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        Assert-Improvement ($settings.network.downloader -eq 'wininet' -and $settings.network.doProgressTimeoutInSeconds -eq 30 -and $settings.visual.progressBar -eq 'rainbow') 'downloader change preserves unrelated settings'
        Assert-Improvement (@(Get-ChildItem -LiteralPath $scratch -Filter 'winget-settings.json.autoos-backup-*').Count -eq 1) 'downloader settings are backed up only when changed'
    }
} finally {
    # Only the explicitly constructed scratch directory is eligible for cleanup.
    $resolved = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved).StartsWith('autoos-improvements-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
