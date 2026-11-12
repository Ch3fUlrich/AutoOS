#Requires -Version 5.1
# Literal shipped guards, not derived from the implementation being tested.

function Get-BackgroundProfileEnvironmentFixture {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseLiteralInitializerForHashtable', '', Justification = 'Case-sensitive snapshots are required to reproduce colliding environment names.')]
    param([string]$LowerValue, [string]$UpperValue)
    $variables = [Collections.Hashtable]::new([StringComparer]::Ordinal)
    $variables['claudecode'] = $LowerValue; $variables['CLAUDECODE'] = $UpperValue
    return $variables
}

function Get-BackgroundProfileGuardLegacyFixture {
    [ordered]@{
        'round-1' = @'
if (-not (& { # AutoOS background-shell guard: native profile hooks need an interactive terminal, not an agent/background shell.
    param([scriptblock]$Probe = {
        @{ HostName = $Host.Name; InputRedirected = [Console]::IsInputRedirected
           OutputRedirected = [Console]::IsOutputRedirected; Arguments = [Environment]::GetCommandLineArgs() }
    })
    try {
        $state = & $Probe
        # -File/-Command consume the remainder: a payload's -NoExit is not a host option.
        $hostArguments = @($state.Arguments)
        for ($i = 0; $i -lt $hostArguments.Count; $i++) {
            if ($hostArguments[$i] -match '^-(Command|CommandWithArgs|C|File|F)$') { $hostArguments = $hostArguments[0..$i]; break }
        }
        return ($state.HostName -eq 'ConsoleHost' -and -not $state.InputRedirected -and -not $state.OutputRedirected -and
            -not @($hostArguments -match '^-(NonInteractive|NonI)$').Count -and
            (@($hostArguments -match '^-NoExit$').Count -or
             -not @($state.Arguments -match '^-(Command|CommandWithArgs|C|EncodedCommand|Enc|EC|E|File|F)$').Count))
    } catch { return $false }
})) { return }
'@
        'c8f8e983' = @'
if (-not (& { # AutoOS background-shell guard: native profile hooks need an interactive terminal, not an agent/background shell.
    param([scriptblock]$Probe = {
        @{ HostName = $Host.Name; InputRedirected = [Console]::IsInputRedirected
           OutputRedirected = [Console]::IsOutputRedirected; Arguments = [Environment]::GetCommandLineArgs()
           Environment = [Environment]::GetEnvironmentVariables() }
    })
    try {
        $agentMarkers = @('AI_AGENT', 'CLAUDECODE', 'OPENCODE', 'CODEX_SANDBOX', 'AUTOOS_BACKGROUND_SHELL')
        $state = & $Probe
        # -File/-Command consume the remainder: a payload's -NonInteractive is not a host option.
        $hostArguments = @($state.Arguments)
        for ($i = 0; $i -lt $hostArguments.Count; $i++) {
            if ($hostArguments[$i] -match '^-(Command|CommandWithArgs|C|File|F)$') { $hostArguments = $hostArguments[0..$i]; break }
        }
        foreach ($name in $agentMarkers) {
            if (-not [string]::IsNullOrEmpty($state.Environment[$name])) { return $false }
        }
        return ($state.HostName -eq 'ConsoleHost' -and -not $state.InputRedirected -and -not $state.OutputRedirected -and
            -not @($hostArguments -match '^-(NonInteractive|NonI)$').Count)
    } catch { return $false }
})) { return }
'@
    }
}
