#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

function Set-AutoOSManagedFile {
    param([string]$Path, [string]$Content, [switch]$DryRun)
    $existing = if (Test-Path -LiteralPath $Path) { [IO.File]::ReadAllText($Path) } else { $null }
    if ($existing -ceq $Content) { return }
    if ($DryRun) { Write-AutoOSLine "would update with backup: $Path" -Level muted; return }
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) { [void](New-Item -ItemType Directory -Path $directory -Force) }
    if ($null -ne $existing) { Copy-Item -LiteralPath $Path -Destination "$Path.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')" }
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($true)))
    Write-AutoOSLine "updated: $Path" -Level ok
}

function Set-AutoOSProfileBlock {
    param([string]$Path, [switch]$DryRun)
    $content = if (Test-Path -LiteralPath $Path) { [IO.File]::ReadAllText($Path) } else { '' }
    $block = @'
# BEGIN AutoOS shell
. "$env:LOCALAPPDATA\AutoOS\shell\AutoOS.Profile.ps1"
# END AutoOS shell
'@
    if ($content -match '(?ms)^# BEGIN AutoOS shell\r?\n.*?^# END AutoOS shell') {
        $content = [regex]::Replace($content, '(?ms)^# BEGIN AutoOS shell\r?\n.*?^# END AutoOS shell', [Text.RegularExpressions.MatchEvaluator]{ $block })
    } else {
        # Migrate only the known old AutoOS initializer; retain unrelated code.
        $content = [regex]::Replace($content, '(?m)^oh-my-posh init (pwsh|powershell) --config [^\r\n]*AutoOS[\\/]themes[\\/]powerlevel10k_rainbow_env\.omp\.json[^\r\n]*\r?\n?', '')
        $content = $content.TrimEnd() + "`r`n`r`n" + $block + "`r`n"
    }
    Set-AutoOSManagedFile -Path $Path -Content $content -DryRun:$DryRun
}

function Install-AutoOSShellConfiguration {
    param([string]$RepoRoot, [switch]$DryRun, [string]$DataRoot = $env:LOCALAPPDATA,
          [string]$Documents = [Environment]::GetFolderPath('MyDocuments'))
    foreach ($theme in @('everyday.omp.json', 'powerlevel10k_rainbow_env.omp.json')) {
        $source = Join-Path $RepoRoot "Windows\Terminal\oh-my-posh\theme\$theme"
        Set-AutoOSManagedFile -Path (Join-Path $DataRoot "AutoOS\themes\$theme") -Content ([IO.File]::ReadAllText($source)) -DryRun:$DryRun
    }
    $snippet = Join-Path $RepoRoot 'Windows\Terminal\oh-my-posh\profile\AutoOS.Profile.ps1'
    Set-AutoOSManagedFile -Path (Join-Path $DataRoot 'AutoOS\shell\AutoOS.Profile.ps1') -Content ([IO.File]::ReadAllText($snippet)) -DryRun:$DryRun
    foreach ($shellFolder in @('PowerShell', 'WindowsPowerShell')) {
        Set-AutoOSProfileBlock -Path (Join-Path $Documents "$shellFolder\Microsoft.PowerShell_profile.ps1") -DryRun:$DryRun
    }
    # A Terminal settings fragment adds a dedicated font-configured profile.
    # The user's JSONC settings and existing default profile are left intact.
    $fragment = [ordered]@{ profiles = @([ordered]@{
        guid = '{1f682b0f-e079-4823-a20a-0b2f7dc6089f}'; name = 'AutoOS Everyday'
        commandline = 'powershell.exe -NoLogo'; startingDirectory = '%USERPROFILE%'
        font = @{ face = 'MesloLGS NF' }; colorScheme = 'One Half Dark'
    }) }
    Set-AutoOSManagedFile -Path (Join-Path $DataRoot 'Microsoft\Windows Terminal\Fragments\AutoOS\everyday.json') -Content ($fragment | ConvertTo-Json -Depth 8) -DryRun:$DryRun
    Write-AutoOSLine 'Open the AutoOS Everyday profile in Windows Terminal for the configured font and colors.' -Level info
    Write-AutoOSLine 'Everyday prompt: folder, clock, success/error and duration. Coding theme: . Set-AutoOSPromptTheme coding' -Level info
}

Export-ModuleMember -Function Install-AutoOSShellConfiguration, Set-AutoOSManagedFile, Set-AutoOSProfileBlock
