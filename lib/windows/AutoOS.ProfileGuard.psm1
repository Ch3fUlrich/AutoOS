#Requires -Version 5.1
<#
.SYNOPSIS
    Idempotent background-shell guards for existing Windows PowerShell profiles.
.DESCRIPTION
    Setup protects native hooks without replacing a user's profile. The shared
    installer backup callback keeps backup naming in one place without a circular
    module dependency. Both PowerShell 5.1 and 7 profiles retain their own bytes.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Do not force a sibling import: that would remove the caller's global copy.
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

# One source for the installed guard; other launchers can opt in with AUTOOS_BACKGROUND_SHELL.
$script:BackgroundShellMarkers = @('AI_AGENT', 'CLAUDECODE', 'OPENCODE', 'CODEX_SANDBOX', 'AUTOOS_BACKGROUND_SHELL')

function Add-AutoOSGuardedProfileLine {
    <#
      .SYNOPSIS
        Append or prepend a profile block once, preserving the user's content.
      .DESCRIPTION
        The installer supplies its shared backup writer so this module does not
        import AutoOS.Install back into itself. Prepending preserves encoding,
        using/param placement and the background guard's precedence over hooks;
        named-block profiles are left unchanged rather than changing semantics.
    #>
    param(
        [Parameter(Mandatory)][string]$ProfilePath,
        [Parameter(Mandatory)][string]$Line,
        [Parameter(Mandatory)][string]$Marker,
        [switch]$Prepend,
        [bool]$DryRun = $false,
        [Parameter(Mandatory)][scriptblock]$Backup
    )
    $dir = Split-Path -Parent $ProfilePath
    $isGuard = $Prepend -and $Marker -eq 'AutoOS background-shell guard'
    if ($DryRun -and -not $isGuard) {
        Write-AutoOSLine "would ensure '$Marker' in $ProfilePath" -Level muted
        return
    }
    if (-not $DryRun -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (($present = Test-Path -LiteralPath $ProfilePath)) {
        # -SimpleMatch: the line contains |, $ and . which are regex metacharacters.
        $existing = Get-Content -Path $ProfilePath -Raw -ErrorAction SilentlyContinue
        if (-not $isGuard -and $existing -and (Select-String -InputObject $existing -Pattern $Marker -SimpleMatch -Quiet)) {
            Write-AutoOSLine "profile already configured ($Marker)" -Level muted
            return
        }
        if (-not $Prepend) { $null = & $Backup $ProfilePath }
    } elseif (-not $DryRun) {
        New-Item -ItemType File -Path $ProfilePath -Force | Out-Null
    }
    if ($Prepend) {
        # A guard is useless at the end of a profile: everything slow has already run.
        # Rule 4: decode for parsing, but splice the original bytes around the edit
        # in their own encoding (a BOM-less 5.1 profile is usually ANSI).
        $bytes = [byte[]]@()
        if ($present) { $bytes = [IO.File]::ReadAllBytes($ProfilePath) }
        $bom = 0
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            $enc = [Text.UTF8Encoding]::new($false); $bom = 3
        } elseif ($bytes.Length -ge 4 -and [BitConverter]::ToString($bytes, 0, 4) -eq 'FF-FE-00-00') {
            $enc = [Text.UTF32Encoding]::new($false, $false); $bom = 4
        } elseif ($bytes.Length -ge 4 -and [BitConverter]::ToString($bytes, 0, 4) -eq '00-00-FE-FF') {
            $enc = [Text.UTF32Encoding]::new($true, $false); $bom = 4
        } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
            $enc = [Text.UnicodeEncoding]::new($false, $false); $bom = 2
        } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
            $enc = [Text.UnicodeEncoding]::new($true, $false); $bom = 2
        } else {
            try {
                $enc = [Text.UTF8Encoding]::new($false, $true)
                [void]$enc.GetString($bytes)
            } catch {
                # Some ANSI code page, which one is unknowable from here. Latin-1 maps every
                # byte to one char and back, so the file round-trips unchanged whatever it
                # was; `using`/`param` are ASCII, so the parser still finds them.
                $enc = [Text.Encoding]::GetEncoding(28591)
            }
        }
        $body = $enc.GetString($bytes, $bom, $bytes.Length - $bom)
        # Refuse lossy decoding: character offsets must map exactly to byte offsets.
        if ([Convert]::ToBase64String($enc.GetBytes($body)) -cne [Convert]::ToBase64String($bytes, $bom, $bytes.Length - $bom)) {
            Write-AutoOSLine "profile needs manual review (encoding does not round-trip): $ProfilePath" -Level warn
            return
        }
        $eol = if (-not $body -or $body.Contains("`r`n")) { "`r`n" } else { "`n" }
        $Line = $Line -replace '\r?\n', $eol
        # `using` and `param` must stay the first statements, so the guard goes after them.
        $tokens = $null; $parseErrors = $null
        $ast = [Management.Automation.Language.Parser]::ParseInput($body, [ref]$tokens, [ref]$parseErrors)
        $markers = [regex]::Matches($body, [regex]::Escape($Marker), [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($isGuard -and $markers.Count) {
            # The owned block starts at the marked line's beginning and ends at the
            # parser's closing brace of ONE top-level if statement on that line.
            # The marker must be a line comment inside that statement; surrounding
            # text on its start/end lines may only be whitespace. The closing line's
            # EOL is NOT owned, preserving suffix bytes and EOF newline state. Parse
            # errors, duplicate markers and partial/bare-return guards are ambiguous:
            # never guess a boundary or append a duplicate in those cases.
            $markerAt = $markers[0].Index
            $at = $body.LastIndexOf("`n", $markerAt) + 1
            $statements = @(if ($ast.EndBlock -and $ast.EndBlock.Unnamed) { $ast.EndBlock.Statements | Where-Object {
                $_ -is [Management.Automation.Language.IfStatementAst] -and
                $_.Extent.StartOffset -ge $at -and $_.Extent.StartOffset -lt $markerAt -and $_.Extent.EndOffset -gt $markerAt
            } })
            $comments = @($tokens | Where-Object {
                $_.Kind -eq 'Comment' -and $_.Text.StartsWith('#') -and
                $_.Extent.StartOffset -le $markerAt -and $_.Extent.EndOffset -ge ($markerAt + $Marker.Length)
            })
            $safe = $markers.Count -eq 1 -and @($parseErrors).Count -eq 0 -and $statements.Count -eq 1 -and $comments.Count -eq 1 -and $ast.EndBlock.Unnamed
            if ($safe) {
                $statement = $statements[0]; $end = $statement.Extent.EndOffset
                $lineEnd = $body.IndexOf("`n", $end); if ($lineEnd -lt 0) { $lineEnd = $body.Length }
                $safe = $body.Substring($at, $statement.Extent.StartOffset - $at) -match '^[\t ]*$' -and
                    $comments[0].Extent.StartOffset -gt $statement.Extent.StartOffset -and
                    $body.Substring($end, $lineEnd - $end) -match '^[\t \r]*$'
            }
            if (-not $safe) {
                Write-AutoOSLine "profile needs manual review (guard boundaries are ambiguous): $ProfilePath" -Level warn
                return
            }
            $startByte = $bom + $enc.GetByteCount($body.Substring(0, $at))
            $endByte = $bom + $enc.GetByteCount($body.Substring(0, $end))
            $newBytes = $enc.GetBytes($Line)
            if ([Convert]::ToBase64String($bytes, $startByte, $endByte - $startByte) -ceq [Convert]::ToBase64String($newBytes)) {
                Write-AutoOSLine "profile already configured ($Marker)" -Level muted
                return
            }
            if ($DryRun) {
                Write-AutoOSLine "would update '$Marker' in $ProfilePath" -Level muted
                return
            }
            if ($present) { $null = & $Backup $ProfilePath }
            $out = [IO.MemoryStream]::new()
            try {
                $out.Write($bytes, 0, $startByte)
                $out.Write($newBytes, 0, $newBytes.Length)
                $out.Write($bytes, $endByte, $bytes.Length - $endByte)
                [IO.File]::WriteAllBytes($ProfilePath, $out.ToArray())
            } finally { $out.Dispose() }
            Write-AutoOSLine "profile updated: $ProfilePath" -Level ok
            return
        }
        if ($ast.BeginBlock -or $ast.ProcessBlock -or $ast.DynamicParamBlock -or ($ast.PSObject.Properties['CleanBlock'] -and $ast.CleanBlock) -or ($ast.EndBlock -and -not $ast.EndBlock.Unnamed)) {
            Write-AutoOSLine "profile has named blocks; left unchanged (manual guard needed): $ProfilePath" -Level warn
            return
        }
        if ($DryRun) {
            Write-AutoOSLine "would ensure '$Marker' in $ProfilePath" -Level muted
            return
        }
        if ($present) { $null = & $Backup $ProfilePath }
        $at = 0
        foreach ($u in $ast.UsingStatements) { $at = [Math]::Max($at, $u.Extent.EndOffset) }
        if ($ast.ParamBlock) { $at = [Math]::Max($at, $ast.ParamBlock.Extent.EndOffset) }
        # Later prepends (notably OpenHands) must not displace the background guard.
        foreach ($statement in @($ast.EndBlock.Statements)) {
            if ($statement.Extent.Text.Contains('# AutoOS background-shell guard:')) { $at = [Math]::Max($at, $statement.Extent.EndOffset) }
        }
        $suffix = $body.Substring($at); $separator = if ($suffix -match '^\r?\n') { '' } else { $eol }
        $new = if ($at -eq 0) { "# added by AutoOS${eol}$Line$eol" }
               else { "${eol}# added by AutoOS${eol}$Line$separator" }
        $startByte = $bom + $enc.GetByteCount($body.Substring(0, $at))
        $out = [IO.MemoryStream]::new()
        try {
            $out.Write($bytes, 0, $startByte)
            $newBytes = $enc.GetBytes($new)
            $out.Write($newBytes, 0, $newBytes.Length)
            $out.Write($bytes, $startByte, $bytes.Length - $startByte)
            [IO.File]::WriteAllBytes($ProfilePath, $out.ToArray())
        } finally { $out.Dispose() }
    } else {
        Add-Content -Path $ProfilePath -Value "`n# added by AutoOS`n$Line"
    }
    Write-AutoOSLine "profile updated: $ProfilePath" -Level ok
}

function Install-AutoOSProfileGuard {
    <#
      .SYNOPSIS
        Guard existing profiles for terminal, coding-agent and Miniconda components.
      .DESCRIPTION
        Called after post-install callbacks, including for already-present tools
        with no callback. Never creates missing profiles; reruns update outdated
        guards but leave current guards alone. DryRun reports the plan without writing.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Component,
        [string]$Documents = [Environment]::GetFolderPath('MyDocuments'),
        [bool]$DryRun = $false,
        [Parameter(Mandatory)][scriptblock]$Backup
    )
    # Setup calls this for installed AND already-present components, even without a callback.
    $category = $Component.PSObject.Properties['CategoryId']
    if (-not (($category -and $category.Value -in @('terminal', 'ai-coding')) -or $Component.Id -eq 'miniconda')) { return }
    $guard = @'
if (-not (& { # AutoOS background-shell guard: native profile hooks need an interactive terminal, not an agent/background shell.
    param([scriptblock]$Probe = {
        @{ HostName = $Host.Name; InputRedirected = [Console]::IsInputRedirected
           OutputRedirected = [Console]::IsOutputRedirected; Arguments = [Environment]::GetCommandLineArgs()
           Environment = [Environment]::GetEnvironmentVariables() }
    })
    try {
        $agentMarkers = @(__AUTOOS_AGENT_MARKERS__)
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
    $guard = $guard.Replace('__AUTOOS_AGENT_MARKERS__', (($script:BackgroundShellMarkers | ForEach-Object { "'$_'" }) -join ', '))
    if (-not $Documents) { return }
    foreach ($folder in @('PowerShell', 'WindowsPowerShell')) {
        foreach ($name in @('Microsoft.PowerShell_profile.ps1', 'profile.ps1')) {
            $path = Join-Path $Documents "$folder\$name"
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                Add-AutoOSGuardedProfileLine -Prepend -ProfilePath $path -Line $guard -Marker 'AutoOS background-shell guard' -DryRun:$DryRun -Backup $Backup
            }
        }
    }
}

Export-ModuleMember -Function Add-AutoOSGuardedProfileLine, Install-AutoOSProfileGuard
