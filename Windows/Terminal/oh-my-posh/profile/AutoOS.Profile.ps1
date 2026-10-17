#Requires -Version 5.1
# Loaded by a small, backed-up managed block in both PowerShell profiles.
# No downloads or module installation happen while a shell is opening.
if ([Console]::IsOutputRedirected -or [Console]::IsInputRedirected) { return }

if (Get-Module -ListAvailable PSReadLine) {
    Import-Module PSReadLine -ErrorAction SilentlyContinue
    Set-PSReadLineOption -HistoryNoDuplicates -BellStyle None
    Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete
    Set-PSReadLineKeyHandler -Key UpArrow -Function HistorySearchBackward
    Set-PSReadLineKeyHandler -Key DownArrow -Function HistorySearchForward
    Set-PSReadLineKeyHandler -Chord Ctrl+r -Function ReverseSearchHistory
    # Older Windows PowerShell installations do not support predictions.
    if ((Get-Command Set-PSReadLineOption).Parameters.ContainsKey('PredictionSource')) {
        Set-PSReadLineOption -PredictionSource History
    }
    if ((Get-Command Set-PSReadLineOption).Parameters.ContainsKey('PredictionViewStyle')) {
        Set-PSReadLineOption -PredictionViewStyle InlineView
    }
}
if (Get-Module -ListAvailable Terminal-Icons) { Import-Module Terminal-Icons -ErrorAction SilentlyContinue }

function Set-AutoOSPromptTheme {
    [CmdletBinding()]
    param([ValidateSet('everyday', 'coding')][string]$Name = 'everyday')
    $file = if ($Name -eq 'coding') { 'powerlevel10k_rainbow_env.omp.json' } else { 'everyday.omp.json' }
    $config = Join-Path $env:LOCALAPPDATA "AutoOS\themes\$file"
    if ((Get-Command oh-my-posh -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $config)) {
        # Keep initialization in global scope when invoked through this helper.
        $shell = if ($PSVersionTable.PSVersion.Major -ge 6) { 'pwsh' } else { 'powershell' }
        $init = oh-my-posh init $shell --config $config
        . ([scriptblock]::Create($init -join "`n"))
    }
}

# Dot-source the function so prompt definitions survive profile initialization.
$autoosTheme = if ($env:AUTOOS_PROMPT_THEME -eq 'coding') { 'coding' } else { 'everyday' }
. Set-AutoOSPromptTheme -Name $autoosTheme
Remove-Variable autoosTheme -ErrorAction SilentlyContinue
