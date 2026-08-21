#Requires -Version 5.1
<#
.SYNOPSIS
    Zero-dependency terminal UI for AutoOS: colour, layout and an interactive
    checkbox selector.

.DESCRIPTION
    Everything user-visible goes through here so that colour, NO_COLOR, non-TTY
    redirection and the transcript log are all handled in exactly one place.
    Nothing in this module touches the system.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ─── Capability detection ───────────────────────────────────────────────────
$script:Esc = [char]27
$script:LogPath = $null

function Test-AutoOSColorSupport {
    if ($env:NO_COLOR) { return $false }
    if ($env:AUTOOS_NO_COLOR) { return $false }
    if ([Console]::IsOutputRedirected) { return $false }
    # Windows Terminal, VS Code, ConHost on Win10 1511+ and pwsh 7 all handle VT.
    if ($env:WT_SESSION -or $env:TERM_PROGRAM) { return $true }
    if ($PSVersionTable.PSVersion.Major -ge 6) { return $true }
    try { return [Environment]::OSVersion.Version.Build -ge 10586 } catch { return $false }
}

$script:UseColor = Test-AutoOSColorSupport

function Set-AutoOSColor { param([bool]$Enabled) $script:UseColor = $Enabled }
function Test-AutoOSInteractive {
    -not ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)
}

# ─── Palette ────────────────────────────────────────────────────────────────
# Steel blue as the structural accent; warm ramp reserved for severity, so a
# warning never reads as decoration.
$script:Palette = @{
    reset   = "0"
    dim     = "2;38;5;245"
    accent  = "1;38;5;74"
    heading = "1;38;5;252"
    muted   = "38;5;245"
    ok      = "38;5;71"
    warn    = "38;5;179"
    err     = "1;38;5;167"
    sel     = "1;38;5;80"
    inv     = "7"
}

function Format-AutoOSColor {
    param([string]$Text, [string]$Style)
    if (-not $script:UseColor -or -not $script:Palette.ContainsKey($Style)) { return $Text }
    "$($script:Esc)[$($script:Palette[$Style])m$Text$($script:Esc)[0m"
}

# ─── Logging + output ───────────────────────────────────────────────────────
function Initialize-AutoOSLog {
    param([string]$Path)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $script:LogPath = $Path
    "=== AutoOS run $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $Path -Encoding utf8 -Append
}

function Write-AutoOSLine {
    <#
      .SYNOPSIS Single output path: colours the console, keeps the log plain.
    #>
    param(
        [Parameter(Position = 0)][string]$Message = '',
        [ValidateSet('plain', 'info', 'ok', 'warn', 'error', 'step', 'muted', 'head')]
        [string]$Level = 'plain',
        [switch]$NoNewline
    )
    $prefix = switch ($Level) {
        'ok'    { '  ' + (Format-AutoOSColor '+' 'ok')     + ' ' }
        'warn'  { '  ' + (Format-AutoOSColor '!' 'warn')   + ' ' }
        'error' { '  ' + (Format-AutoOSColor 'x' 'err')    + ' ' }
        'step'  { '  ' + (Format-AutoOSColor '>' 'accent') + ' ' }
        'info'  { '  ' + (Format-AutoOSColor '-' 'muted')  + ' ' }
        default { '' }
    }
    $body = switch ($Level) {
        'muted' { Format-AutoOSColor $Message 'muted' }
        'head'  { Format-AutoOSColor $Message 'heading' }
        'error' { Format-AutoOSColor $Message 'err' }
        'warn'  { Format-AutoOSColor $Message 'warn' }
        default { $Message }
    }
    if ($NoNewline) { [Console]::Write("$prefix$body") } else { [Console]::WriteLine("$prefix$body") }

    if ($script:LogPath) {
        $plain = ($Message -replace "$script:Esc\[[0-9;]*m", '')
        "[{0}] {1,-5} {2}" -f (Get-Date -Format 'HH:mm:ss'), $Level.ToUpper(), $plain |
            Out-File -FilePath $script:LogPath -Encoding utf8 -Append
    }
}

function Write-AutoOSBanner {
    param([string]$Subtitle = '')
    $bar = '─' * 62
    Write-AutoOSLine ''
    Write-AutoOSLine (Format-AutoOSColor $bar 'accent')
    Write-AutoOSLine ("  " + (Format-AutoOSColor 'AutoOS' 'accent') + (Format-AutoOSColor '  ·  post-install provisioning' 'muted'))
    if ($Subtitle) { Write-AutoOSLine ("  " + (Format-AutoOSColor $Subtitle 'muted')) }
    Write-AutoOSLine (Format-AutoOSColor $bar 'accent')
    Write-AutoOSLine ''
}

function Write-AutoOSSection {
    param([string]$Title)
    Write-AutoOSLine ''
    Write-AutoOSLine (Format-AutoOSColor "── $Title " 'heading') -NoNewline
    Write-AutoOSLine (Format-AutoOSColor ('─' * [Math]::Max(0, 60 - $Title.Length)) 'dim')
}

function Write-AutoOSKeyValue {
    param([string]$Key, [string]$Value, [string]$Style = 'plain')
    $k = (Format-AutoOSColor ("{0,-22}" -f $Key) 'muted')
    $v = if ($Style -eq 'plain') { $Value } else { Format-AutoOSColor $Value $Style }
    Write-AutoOSLine "  $k $v"
}

# ─── Prompts ────────────────────────────────────────────────────────────────
function Read-AutoOSConfirm {
    param([string]$Question, [bool]$Default = $true)
    if (-not (Test-AutoOSInteractive)) { return $Default }
    $hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        Write-AutoOSLine ("  " + (Format-AutoOSColor '?' 'accent') + " $Question " + (Format-AutoOSColor $hint 'muted') + ' ') -NoNewline
        $answer = [Console]::ReadLine()
        if ($null -eq $answer -or $answer.Trim() -eq '') { return $Default }
        switch -Regex ($answer.Trim()) {
            '^(y|yes|j|ja)$' { return $true }
            '^(n|no|nein)$'  { return $false }
            default { Write-AutoOSLine 'Please answer y or n.' -Level warn }
        }
    }
}

function Read-AutoOSValue {
    param([string]$Question, [string]$Default = '', [string]$Help = '', [scriptblock]$Validator = $null)
    if (-not (Test-AutoOSInteractive)) { return $Default }
    if ($Help) { Write-AutoOSLine "    $Help" -Level muted }
    while ($true) {
        $shown = if ($Default) { " [$Default]" } else { '' }
        Write-AutoOSLine ("  " + (Format-AutoOSColor '?' 'accent') + " $Question" + (Format-AutoOSColor $shown 'muted') + ': ') -NoNewline
        $value = [Console]::ReadLine()
        if ([string]::IsNullOrWhiteSpace($value)) { $value = $Default }
        if ($null -eq $Validator) { return $value }
        $result = & $Validator $value
        if ($result -eq $true) { return $value }
        Write-AutoOSLine $result -Level warn
    }
}

# ─── The checkbox selector ──────────────────────────────────────────────────
function Get-AutoOSNextItemIndex {
    <#
      .SYNOPSIS
        Next selectable row in a given direction, skipping group headers.
      .DESCRIPTION
        $From may deliberately sit outside the array so that Home (-1, +1) and
        End (count, -1) fall out of the same logic. When no selectable row
        exists in that direction the cursor stays put.
    #>
    param(
        [Parameter(Mandatory)][System.Collections.ArrayList]$Rows,
        [Parameter(Mandatory)][int]$From,
        [Parameter(Mandatory)][int]$Delta
    )
    for ($i = $From + $Delta; $i -ge 0 -and $i -lt $Rows.Count; $i += $Delta) {
        if ($Rows[$i].Kind -eq 'item') { return $i }
    }
    if ($From -ge 0 -and $From -lt $Rows.Count -and $Rows[$From].Kind -eq 'item') { return $From }
    for ($i = 0; $i -lt $Rows.Count; $i++) { if ($Rows[$i].Kind -eq 'item') { return $i } }
    return 0
}

function Show-AutoOSMenu {
    <#
    .SYNOPSIS
        Arrow-key checkbox selector over grouped items.
    .PARAMETER Items
        Objects with: Id, Name, Description, Group, Selected, Locked, Reason.
        A Locked item is shown but cannot be toggled (dependency of something else).
    .OUTPUTS
        String[] of selected Ids, or $null if the user cancelled.
    #>
    param(
        [Parameter(Mandatory)][object[]]$Items,
        [string]$Title = 'Select components',
        [string]$Footer = ''
    )

    if (-not (Test-AutoOSInteractive)) {
        return @($Items | Where-Object { $_.Selected } | ForEach-Object { $_.Id })
    }

    # Build a flat render list: group headers interleaved with their items.
    $rows = New-Object System.Collections.ArrayList
    foreach ($group in ($Items | Group-Object Group)) {
        [void]$rows.Add([pscustomobject]@{ Kind = 'header'; Text = $group.Name; Item = $null })
        foreach ($it in $group.Group) {
            [void]$rows.Add([pscustomobject]@{ Kind = 'item'; Text = $it.Name; Item = $it })
        }
    }

    $cursor = 0
    while ($rows[$cursor].Kind -ne 'item' -and $cursor -lt $rows.Count - 1) { $cursor++ }

    $viewport = [Math]::Max(8, [Math]::Min(22, [Console]::WindowHeight - 12))
    $top = 0
    $rendered = 0

    try {
        [Console]::CursorVisible = $false
        while ($true) {
            # keep cursor inside the viewport
            if ($cursor -lt $top) { $top = $cursor }
            if ($cursor -ge $top + $viewport) { $top = $cursor - $viewport + 1 }

            $out = New-Object System.Text.StringBuilder
            if ($rendered -gt 0) { [void]$out.Append("$($script:Esc)[${rendered}A") }

            $selCount = @($Items | Where-Object { $_.Selected }).Count
            [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor $Title 'heading') +
                (Format-AutoOSColor "   $selCount of $($Items.Count) selected" 'muted'))
            [void]$out.AppendLine("$($script:Esc)[2K")
            $lines = 2

            for ($i = $top; $i -lt [Math]::Min($rows.Count, $top + $viewport); $i++) {
                $row = $rows[$i]
                if ($row.Kind -eq 'header') {
                    [void]$out.AppendLine("$($script:Esc)[2K   " + (Format-AutoOSColor $row.Text.ToUpper() 'accent'))
                } else {
                    $it = $row.Item
                    $mark = if ($i -eq $cursor) { (Format-AutoOSColor '>' 'sel') } else { ' ' }
                    $boxText = if ($it.Locked) { (Format-AutoOSColor '[=]' 'muted') }
                               elseif ($it.Selected) { (Format-AutoOSColor '[x]' 'ok') }
                               else { (Format-AutoOSColor '[ ]' 'dim') }
                    $name = "{0,-26}" -f $it.Name
                    $nameText = if ($i -eq $cursor) { Format-AutoOSColor $name 'sel' } else { $name }
                    $desc = Format-AutoOSColor $it.Description 'muted'
                    [void]$out.AppendLine("$($script:Esc)[2K  $mark $boxText $nameText $desc")
                }
                $lines++
            }

            $more = $rows.Count - ($top + $viewport)
            if ($more -gt 0) {
                [void]$out.AppendLine("$($script:Esc)[2K      " + (Format-AutoOSColor "... $more more below" 'dim'))
            } else {
                [void]$out.AppendLine("$($script:Esc)[2K")
            }
            $lines++

            [void]$out.AppendLine("$($script:Esc)[2K")
            $keys = '↑↓ move   SPACE toggle   A all   N none   ENTER confirm   ESC cancel'
            [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor $keys 'dim'))
            $lines += 2
            if ($Footer) { [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor $Footer 'muted')); $lines++ }

            [Console]::Write($out.ToString())
            $rendered = $lines

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'UpArrow'   { $cursor = Get-AutoOSNextItemIndex $rows $cursor -1 }
                'DownArrow' { $cursor = Get-AutoOSNextItemIndex $rows $cursor  1 }
                'PageUp'    { for ($n = 0; $n -lt 5; $n++) { $cursor = Get-AutoOSNextItemIndex $rows $cursor -1 } }
                'PageDown'  { for ($n = 0; $n -lt 5; $n++) { $cursor = Get-AutoOSNextItemIndex $rows $cursor  1 } }
                'Home'      { $cursor = Get-AutoOSNextItemIndex $rows -1 1 }
                'End'       { $cursor = Get-AutoOSNextItemIndex $rows $rows.Count -1 }
                'Spacebar'  {
                    $it = $rows[$cursor].Item
                    if (-not $it.Locked) { $it.Selected = -not $it.Selected }
                }
                'Enter'     {
                    [Console]::CursorVisible = $true
                    [Console]::WriteLine()
                    return @($Items | Where-Object { $_.Selected } | ForEach-Object { $_.Id })
                }
                'Escape'    {
                    [Console]::CursorVisible = $true
                    [Console]::WriteLine()
                    return $null
                }
                default {
                    switch ($key.KeyChar) {
                        'a' { foreach ($it in $Items) { if (-not $it.Locked) { $it.Selected = $true } } }
                        'n' { foreach ($it in $Items) { if (-not $it.Locked) { $it.Selected = $false } } }
                        'q' { [Console]::CursorVisible = $true; [Console]::WriteLine(); return $null }
                    }
                }
            }
        }
    } finally {
        [Console]::CursorVisible = $true
    }
}

Export-ModuleMember -Function `
    Test-AutoOSColorSupport, Set-AutoOSColor, Test-AutoOSInteractive, Format-AutoOSColor,
    Initialize-AutoOSLog, Write-AutoOSLine, Write-AutoOSBanner, Write-AutoOSSection,
    Write-AutoOSKeyValue, Read-AutoOSConfirm, Read-AutoOSValue, Show-AutoOSMenu,
    Get-AutoOSNextItemIndex
