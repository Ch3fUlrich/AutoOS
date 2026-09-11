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
    if ($env:AUTOOS_NONINTERACTIVE) { return $false }
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

function Format-AutoOSTimestamp {
    <#
      .SYNOPSIS
        An ISO timestamp plus how long ago it was.
      .DESCRIPTION
        The age is the actionable half - a snapshot from four hours ago means the
        timer is not running - and ISO keeps this line in the same shape as the
        session rows below it, which a culture-formatted date did not.
    #>
    param([string]$Iso)

    if (-not $Iso) { return 'never' }
    $when = [datetime]::MinValue
    # InvariantCulture + RoundtripKind, not the bare TryParse: the value is an
    # ISO-8601 round-trip string, and parsing it under a d/M/y culture silently
    # swapped the day and the month (2026-09-11 read back as 2026-11-09).
    if (-not [datetime]::TryParse($Iso, [Globalization.CultureInfo]::InvariantCulture,
                                  [Globalization.DateTimeStyles]::RoundtripKind, [ref]$when)) {
        return $Iso
    }

    $minutes = [int]((Get-Date) - $when).TotalMinutes
    $ago = if ($minutes -lt 1) { 'just now' }
           elseif ($minutes -lt 60) { "$minutes min ago" }
           elseif ($minutes -lt 2880) { "$([int]($minutes / 60)) h ago" }
           else { "$([int]($minutes / 1440)) days ago" }
    "{0} ({1})" -f $when.ToString('yyyy-MM-dd HH:mm'), $ago
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

    $choice = if ($Default) { 0 } else { 1 } # 0 = Yes, 1 = No
    $first = $true
    try {
        [Console]::CursorVisible = $false
        while ($true) {
            $btnYes = if ($choice -eq 0) { (Format-AutoOSColor '[ Yes ]' 'sel') } else { (Format-AutoOSColor '  Yes  ' 'dim') }
            $btnNo  = if ($choice -eq 1) { (Format-AutoOSColor '[ No ]' 'sel') } else { (Format-AutoOSColor '  No  ' 'dim') }

            $line = "  " + (Format-AutoOSColor '?' 'accent') + " $Question  $btnYes  $btnNo"
            if ($first) {
                [Console]::Write($line)
                $first = $false
            } else {
                [Console]::Write("`r$($script:Esc)[2K$line")
            }

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'LeftArrow'  { $choice = 1 - $choice }
                'RightArrow' { $choice = 1 - $choice }
                'UpArrow'    { $choice = 1 - $choice }
                'DownArrow'  { $choice = 1 - $choice }
                'Tab'        { $choice = 1 - $choice }
                'Spacebar'   { $choice = 1 - $choice }
                'Enter'      {
                    [Console]::CursorVisible = $true
                    $ansText = if ($choice -eq 0) { Format-AutoOSColor 'Yes' 'ok' } else { Format-AutoOSColor 'No' 'warn' }
                    [Console]::WriteLine("`r$($script:Esc)[2K  " + (Format-AutoOSColor '?' 'accent') + " $Question  $ansText")
                    return ($choice -eq 0)
                }
                'Escape'     {
                    [Console]::CursorVisible = $true
                    [Console]::WriteLine("`r$($script:Esc)[2K  " + (Format-AutoOSColor '?' 'accent') + " $Question  " + (Format-AutoOSColor 'Cancelled' 'warn'))
                    return $false
                }
                default {
                    switch ($key.KeyChar) {
                        'y' {
                            [Console]::CursorVisible = $true
                            [Console]::WriteLine("`r$($script:Esc)[2K  " + (Format-AutoOSColor '?' 'accent') + " $Question  " + (Format-AutoOSColor 'Yes' 'ok'))
                            return $true
                        }
                        'n' {
                            [Console]::CursorVisible = $true
                            [Console]::WriteLine("`r$($script:Esc)[2K  " + (Format-AutoOSColor '?' 'accent') + " $Question  " + (Format-AutoOSColor 'No' 'warn'))
                            return $false
                        }
                        'h' { $choice = 1 - $choice }
                        'l' { $choice = 1 - $choice }
                        'j' { $choice = 1 - $choice }
                        'k' { $choice = 1 - $choice }
                    }
                }
            }
        }
    } finally {
        [Console]::CursorVisible = $true
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

# ─── Interactive single-choice radio selector ───────────────────────────────
function Show-AutoOSRadioMenu {
    <#
    .SYNOPSIS
        Interactive single-choice radio selector for profiles or options.
    .PARAMETER Items
        Objects with: Id, Name, Description, Badge
    .PARAMETER Title
        Header text.
    .PARAMETER DefaultId
        Default selected Id.
    #>
    param(
        [Parameter(Mandatory)][object[]]$Items,
        [string]$Title = 'Choose profile',
        [string]$DefaultId = ''
    )

    if (-not (Test-AutoOSInteractive)) {
        return $DefaultId
    }

    if ($Items.Count -eq 0) {
        return $DefaultId
    }

    $cursor = 0
    for ($i = 0; $i -lt $Items.Count; $i++) {
        if ($Items[$i].Id -eq $DefaultId) { $cursor = $i; break }
    }

    $rendered = 0
    try {
        [Console]::CursorVisible = $false
        while ($true) {
            $out = New-Object System.Text.StringBuilder
            if ($rendered -gt 0) { [void]$out.Append("$($script:Esc)[${rendered}A") }

            [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor "$Title`:" 'heading'))
            $lines = 1

            for ($i = 0; $i -lt $Items.Count; $i++) {
                $it = $Items[$i]
                $mark = if ($i -eq $cursor) { (Format-AutoOSColor '❯' 'sel') } else { ' ' }
                $radio = if ($i -eq $cursor) { (Format-AutoOSColor '(•)' 'sel') } else { (Format-AutoOSColor '( )' 'dim') }
                $name = "{0,-14}" -f $it.Name
                $nameText = if ($it.Locked) { Format-AutoOSColor $name 'muted' } elseif ($i -eq $cursor) { Format-AutoOSColor $name 'sel' } else { $name }
                $badgeText = if ($it.Badge) { " " + (Format-AutoOSColor "[$($it.Badge)]" 'accent') } else { "" }
                $descText = if ($it.Description) { "  " + (Format-AutoOSColor $it.Description 'muted') } else { "" }

                [void]$out.AppendLine("$($script:Esc)[2K  $mark $radio $nameText$badgeText$descText")
                $lines++
            }

            [void]$out.AppendLine("$($script:Esc)[2K")
            $keys = "↑↓/jk move   1-$($Items.Count) select   ENTER confirm   ESC default"
            [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor $keys 'dim'))
            $lines += 2

            [Console]::Write($out.ToString())
            $rendered = $lines

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'UpArrow'   { if ($cursor -gt 0) { $cursor-- } else { $cursor = $Items.Count - 1 } }
                'DownArrow' { if ($cursor -lt $Items.Count - 1) { $cursor++ } else { $cursor = 0 } }
                'Enter'     {
                    if ($rendered -gt 0) {
                        $clr = New-Object System.Text.StringBuilder
                        [void]$clr.Append("$($script:Esc)[${rendered}A")
                        for ($k = 0; $k -lt $rendered; $k++) { [void]$clr.AppendLine("$($script:Esc)[2K") }
                        [void]$clr.Append("$($script:Esc)[${rendered}A")
                        [Console]::Write($clr.ToString())
                    }
                    [Console]::CursorVisible = $true
                    return $Items[$cursor].Id
                }
                'Escape'    {
                    if ($rendered -gt 0) {
                        $clr = New-Object System.Text.StringBuilder
                        [void]$clr.Append("$($script:Esc)[${rendered}A")
                        for ($k = 0; $k -lt $rendered; $k++) { [void]$clr.AppendLine("$($script:Esc)[2K") }
                        [void]$clr.Append("$($script:Esc)[${rendered}A")
                        [Console]::Write($clr.ToString())
                    }
                    [Console]::CursorVisible = $true
                    return $DefaultId
                }
                default {
                    switch ($key.KeyChar) {
                        'k' { if ($cursor -gt 0) { $cursor-- } else { $cursor = $Items.Count - 1 } }
                        'j' { if ($cursor -lt $Items.Count - 1) { $cursor++ } else { $cursor = 0 } }
                        'q' { [Console]::CursorVisible = $true; return $DefaultId }
                        ' ' {
                            if ($rendered -gt 0) {
                                $clr = New-Object System.Text.StringBuilder
                                [void]$clr.Append("$($script:Esc)[${rendered}A")
                                for ($k = 0; $k -lt $rendered; $k++) { [void]$clr.AppendLine("$($script:Esc)[2K") }
                                [void]$clr.Append("$($script:Esc)[${rendered}A")
                                [Console]::Write($clr.ToString())
                            }
                            [Console]::CursorVisible = $true
                            return $Items[$cursor].Id
                        }
                        { $_ -ge '1' -and $_ -le '9' } {
                            $idx = [int][string]$_ - 1
                            if ($idx -ge 0 -and $idx -lt $Items.Count) { $cursor = $idx }
                        }
                    }
                }
            }
        }
    } finally {
        [Console]::CursorVisible = $true
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
            $pct = if ($Items.Count -gt 0) { [Math]::Floor(($selCount * 100) / $Items.Count) } else { 0 }
            $filled = if ($Items.Count -gt 0) { [Math]::Floor(($selCount * 10) / $Items.Count) } else { 0 }
            $empty = 10 - $filled
            $fillStr = '█' * $filled
            $emptyStr = '░' * $empty
            $bar = "  " + (Format-AutoOSColor "[$fillStr" 'sel') + (Format-AutoOSColor "$emptyStr] $pct%" 'dim')
            [void]$out.AppendLine("$($script:Esc)[2K  " + (Format-AutoOSColor $Title 'heading') +
                (Format-AutoOSColor "   $selCount of $($Items.Count) selected" 'muted') + $bar)
            [void]$out.AppendLine("$($script:Esc)[2K")
            $lines = 2

            for ($i = $top; $i -lt [Math]::Min($rows.Count, $top + $viewport); $i++) {
                $row = $rows[$i]
                if ($row.Kind -eq 'header') {
                    $grpItems = @($Items | Where-Object { $_.Group -eq $row.Text })
                    $grpSel = @($grpItems | Where-Object { $_.Selected }).Count
                    $grpStat = (Format-AutoOSColor " ($grpSel/$($grpItems.Count) selected)" 'dim')
                    [void]$out.AppendLine("$($script:Esc)[2K   " + (Format-AutoOSColor $row.Text.ToUpper() 'accent') + $grpStat)
                } else {
                    $it = $row.Item
                    $mark = if ($i -eq $cursor) { (Format-AutoOSColor '❯' 'sel') } else { ' ' }
                    $boxText = if ($it.Locked) { (Format-AutoOSColor '[=]' 'muted') }
                               elseif ($it.Selected) { (Format-AutoOSColor '[✓]' 'ok') }
                               else { (Format-AutoOSColor '[ ]' 'dim') }
                    $name = "{0,-26}" -f $it.Name
                    $nameText = if ($it.Locked) { Format-AutoOSColor $name 'muted' } elseif ($i -eq $cursor) { Format-AutoOSColor $name 'sel' } else { $name }
                    $instBadge = if ($it.Installed) { (Format-AutoOSColor '✓ installed' 'ok') + ' ' } else { '' }
                    $desc = Format-AutoOSColor $it.Description 'muted'
                    [void]$out.AppendLine("$($script:Esc)[2K  $mark $boxText $nameText $instBadge$desc")
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
            $keys = '↑↓/jk move   SPACE toggle   g group   a all   n none   i invert   ENTER confirm   ESC cancel'
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
                        'k' { $cursor = Get-AutoOSNextItemIndex $rows $cursor -1 }
                        'j' { $cursor = Get-AutoOSNextItemIndex $rows $cursor  1 }
                        'g' {
                            $curGroup = $rows[$cursor].Item.Group
                            $grpItems = @($Items | Where-Object { $_.Group -eq $curGroup -and -not $_.Locked })
                            $allSel = ($grpItems.Count -gt 0) -and -not ($grpItems | Where-Object { -not $_.Selected })
                            $newVal = -not $allSel
                            foreach ($git in $grpItems) { $git.Selected = $newVal }
                        }
                        'i' {
                            foreach ($it in $Items) {
                                if (-not $it.Locked) { $it.Selected = -not $it.Selected }
                            }
                        }
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
    Show-AutoOSRadioMenu, Get-AutoOSNextItemIndex, Format-AutoOSTimestamp
