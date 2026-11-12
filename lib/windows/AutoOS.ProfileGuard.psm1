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
    if ($DryRun) {
        Write-AutoOSLine "would ensure '$Marker' in $ProfilePath" -Level muted
        return
    }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (($present = Test-Path -LiteralPath $ProfilePath)) {
        # -SimpleMatch: the line contains |, $ and . which are regex metacharacters.
        $existing = Get-Content -Path $ProfilePath -Raw -ErrorAction SilentlyContinue
        if ($existing -and (Select-String -InputObject $existing -Pattern $Marker -SimpleMatch -Quiet)) {
            Write-AutoOSLine "profile already configured ($Marker)" -Level muted
            return
        }
        if (-not $Prepend) { $null = & $Backup $ProfilePath }
    } else {
        New-Item -ItemType File -Path $ProfilePath -Force | Out-Null
    }
    if ($Prepend) {
        # A guard is useless at the end of a profile: everything slow has already run.
        # Rule 4: the rest of the file must come back unchanged, so it is decoded and
        # re-encoded in its own encoding (a BOM-less 5.1 profile is usually ANSI).
        $bytes = [IO.File]::ReadAllBytes($ProfilePath)
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
        # `using` and `param` must stay the first statements, so the guard goes after them.
        $ast = [Management.Automation.Language.Parser]::ParseInput($body, [ref]$null, [ref]$null)
        if ($ast.BeginBlock -or $ast.ProcessBlock -or $ast.DynamicParamBlock -or ($ast.PSObject.Properties['CleanBlock'] -and $ast.CleanBlock) -or ($ast.EndBlock -and -not $ast.EndBlock.Unnamed)) {
            Write-AutoOSLine "profile has named blocks; left unchanged (manual guard needed): $ProfilePath" -Level warn
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
        $suffix = $body.Substring($at); $separator = if ($suffix -match '^\r?\n') { '' } else { "`r`n" }
        $new = if ($at -eq 0) { "# added by AutoOS`r`n$Line`r`n$body" }
               else { $body.Substring(0, $at) + "`r`n# added by AutoOS`r`n$Line$separator" + $suffix }
        $out = [IO.MemoryStream]::new()
        $out.Write($bytes, 0, $bom)
        $newBytes = $enc.GetBytes($new)
        $out.Write($newBytes, 0, $newBytes.Length)
        [IO.File]::WriteAllBytes($ProfilePath, $out.ToArray())
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
        with no callback. Never creates missing profiles; a marker makes reruns
        leave existing guards alone. DryRun reports the plan without writing.
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
