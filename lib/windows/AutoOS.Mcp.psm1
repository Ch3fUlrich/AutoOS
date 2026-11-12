#Requires -Version 5.1
# Codex's TOML writer is separate from the package installers: a checkout is
# supplied by the user, never downloaded or installed by AutoOS (D-5254).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

function Get-AutoOSCodexTomlValues {
    # Equality only needs the strings, string arrays and bools we emit. Unknown
    # TOML syntax is treated as different, never guessed equal or reserialized.
    param([string]$Body)
    $tokens = '\G(?:\s+|#[^\r\n]*|(?<str>"(?:\\.|[^"\\\r\n])*"|''[^''\r\n]*'')|(?<other>[A-Za-z0-9_-]+|[\[\],=]))'
    $clean = ''
    $offset = 0
    try {
        foreach ($token in [regex]::Matches($Body, $tokens)) {
            if ($token.Groups['str'].Success) {
                $s = $token.Groups['str'].Value
                $value = if ($s.StartsWith("'")) { $s.Substring(1, $s.Length - 2) } else { ConvertFrom-Json -InputObject $s }
                $clean += ConvertTo-Json -InputObject $value -Compress
            } else { $clean += $token.Groups['other'].Value }
            $offset = $token.Index + $token.Length
        }
        if ($offset -ne $Body.Length) { return $null }
        $string = '"(?:\\.|[^"\\])*"'
        $assignment = '\G(?<key>' + $string + '|[A-Za-z0-9_-]+)=(?<value>true|false|' + $string + '|\[(?:' + $string + '(?:,' + $string + ')*,?)?\])'
        $values = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
        $offset = 0
        foreach ($pair in [regex]::Matches($clean, $assignment)) {
            $key = $pair.Groups['key'].Value
            if ($key.StartsWith('"')) { $key = ConvertFrom-Json -InputObject $key }
            if ($values.ContainsKey($key)) { return $null }
            # Tokens are already decoded/re-escaped; keep brackets and scalar
            # types instead of ConvertFrom-Json's version-dependent unrolling.
            $values.Add($key, ($pair.Groups['value'].Value -replace ',\]$', ']'))
            $offset = $pair.Index + $pair.Length
        }
        if ($offset -ne $clean.Length) { return $null }
        return ,$values
    } catch { return $null }
}

function ConvertTo-AutoOSCodexToml {
    <# .SYNOPSIS Replace one server table (including its env subtable), preserving surrounding text. #>
    param([string]$Text, [string]$Name, [string[]]$Lines)
    $newline = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $entry = ($Lines -join $newline) + $newline
    # Accept bare/quoted names and header comments, but never a similarly named
    # server. Keep separator comments and blank lines before the following table.
    $key = '(?:' + [regex]::Escape($Name) + '|"' + [regex]::Escape($Name) + '"|''' + [regex]::Escape($Name) + ''')'
    $parent = '(?:mcp_servers|"mcp_servers"|''mcp_servers'')'
    $table = '^\[\s*' + $parent + '\s*\.\s*' + $key
    $ours = $table + '\s*(?:\.[^\]]+)?\]\s*(?:#.*)?$'
    $array = '^\[\[\s*' + $parent + '\s*\.\s*' + $key + '\s*(?:\.[^\]]+)?\]\]\s*(?:#.*)?$'
    # Consume strings/comments before looking for headers: a multiline prompt
    # may contain something that looks exactly like our table, but is user text.
    $assign = '(?<assign>^[\t ]*(?:' + $parent + '\s*\.\s*)?' + $key + '\s*[.=])|'
    $lex = '(?ms)' + $assign + '"""(?:\\.|(?!""").)*"""|''''''.*?''''''|"(?:\\.|[^"\\\r\n])*"|''[^''\r\n]*''|#[^\r\n]*|(?<header>^[\t ]*\[\[?(?:[^\]"''\r\n]|"(?:\\.|[^"\\])*"|''[^'']*'')+\]\]?[\t ]*(?:#[^\r\n]*)?(?=\r?$))|(?<open>[\[{])|(?<close>[\]}])'
    $headers = @()
    $depth = 0
    foreach ($token in [regex]::Matches($Text, $lex)) {
        if ($token.Groups['header'].Success) {
            if ($depth -eq 0) { $headers += $token }
        } elseif ($token.Groups['open'].Success) { $depth++ }
        elseif ($token.Groups['close'].Success) { $depth-- }
        elseif ($depth -eq 0 -and $token.Groups['assign'].Success) {
            throw 'dotted or inline server definition needs manual configuration'
        }
    }
    $spans = @()
    foreach ($header in $headers) {
        if ($header.Value.Trim() -cmatch $array) { throw 'array-of-tables server definition needs manual configuration' }
        if ($header.Value.Trim() -cmatch $ours) { $spans += $header }
    }
    if (-not $spans.Count) {
        $separator = if (-not $Text) { '' } elseif ($Text.EndsWith("`n")) { $newline } else { $newline + $newline }
        return $Text + $separator + $entry
    }
    $actual = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    $valid = $true
    foreach ($span in $spans) {
        $end = $Text.Length
        foreach ($header in $headers) { if ($header.Index -gt $span.Index) { $end = $header.Index; break } }
        $body = $Text.Substring($span.Index + $span.Length, $end - $span.Index - $span.Length)
        $namespace = if ($span.Value.Trim() -cmatch ($table + '\s*\]\s*(?:#.*)?$')) { 'server:' } else { 'env:' }
        if ($namespace -eq 'env:' -and $span.Value.Trim() -cnotmatch ($table + '\s*\.\s*env\s*\]\s*(?:#.*)?$')) { $valid = $false }
        $values = Get-AutoOSCodexTomlValues -Body $body
        if ($null -eq $values) { $valid = $false; continue }
        foreach ($k in $values.Keys) {
            if ($actual.ContainsKey($namespace + $k)) { $valid = $false } else { $actual.Add($namespace + $k, $values[$k]) }
        }
    }
    $expected = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    $fields = Get-AutoOSCodexTomlValues -Body (($Lines[1..4]) -join "`n")
    foreach ($k in $fields.Keys) { $expected.Add('server:' + $k, $fields[$k]) }
    $expected.Add('env:PYTHONPATH', (Get-AutoOSCodexTomlValues -Body $Lines[6])['PYTHONPATH'])
    if ($actual.Count -ne $expected.Count) { $valid = $false }
    foreach ($k in $expected.Keys) { if (-not $actual.ContainsKey($k) -or $actual[$k] -cne $expected[$k]) { $valid = $false } }
    if ($valid) { return $Text }
    # Descending edits keep offsets valid, including an env table that appears
    # after another server. Only the first span receives the replacement.
    $noFinalNewline = -not $Text.EndsWith("`n") -and $headers[-1].Index -eq $spans[-1].Index
    $first = $spans[0].Index
    foreach ($span in @($spans | Sort-Object Index -Descending)) {
        $end = $Text.Length
        foreach ($header in $headers) {
            if ($header.Index -gt $span.Index) { $end = $header.Index; break }
        }
        $body = $Text.Substring($span.Index, $end - $span.Index)
        $tail = [regex]::Match($body, '(?m)(?:^[\t ]*(?:#[^\r\n]*)?\r?\n)+\z').Value
        $end -= $tail.Length
        $replacement = if ($span.Index -eq $first) { $entry } else { '' }
        $Text = $Text.Substring(0, $span.Index) + $replacement + $Text.Substring($end)
    }
    if ($noFinalNewline -and $Text.EndsWith($newline)) { $Text = $Text.Substring(0, $Text.Length - $newline.Length) }
    $Text
}

function Invoke-AutoOSCodexProbeProcess {
    # Drain both pipes asynchronously: a noisy import must not defeat the deadline.
    param([string]$FilePath, [string[]]$Arguments, [ValidateRange(1, 15000)][int]$TimeoutMilliseconds = 15000)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    # ProcessStartInfo.ArgumentList is unavailable on Windows PowerShell 5.1.
    $info.Arguments = ($Arguments | ForEach-Object { '"' + (($_ -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }) -join ' '
    $info.WorkingDirectory = [IO.Path]::GetTempPath()
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $process.BeginOutputReadLine(); $process.BeginErrorReadLine()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch { } # An exit race must not hide the timeout reason.
            $null = $process.WaitForExit(1000)
            throw (New-Object TimeoutException('import probe timed out'))
        }
        return ($process.ExitCode -eq 0)
    } finally { $process.Dispose() }
}

function Set-AutoOSCodexMcpEntry {
    <# .SYNOPSIS Wire catalog-defined checkout servers into an existing Codex installation; probe/backup are injectable. #>
    param(
        [Parameter(Mandatory)][psobject]$Servers,
        [bool]$DryRun = $false,
        [scriptblock]$ImportProbe,
        [ValidateRange(1, 15000)][int]$ProbeTimeoutMilliseconds = 15000,
        [Parameter(Mandatory)][scriptblock]$Backup
    )
    $profileHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
    if (-not $profileHome) {
        Write-AutoOSLine 'Codex MCP skipped: no user home is set' -Level muted
        return
    }
    $dir = Join-Path $profileHome '.codex'
    if (-not (Test-Path -LiteralPath $dir -PathType Container) -and -not (Get-Command codex -ErrorAction SilentlyContinue)) {
        Write-AutoOSLine 'Codex MCP skipped: codex is not present (.codex absent and not on PATH)' -Level muted
        return
    }
    if (-not $ImportProbe) {
        $ImportProbe = {
            param($Python, $Source, $TimeoutMilliseconds)
            # -I excludes ambient PYTHON* paths and cwd; add only the checkout's
            # source to the isolated stdlib/site paths. -B keeps DryRun read-only.
            Invoke-AutoOSCodexProbeProcess -FilePath $Python -Arguments @('-I', '-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import plangraph', $Source) -TimeoutMilliseconds $TimeoutMilliseconds
        }
    }
    foreach ($server in $Servers.PSObject.Properties) {
        $name = $server.Name
        $spec = $server.Value
        $missing = @('root_env', 'args', 'env_vars' | Where-Object { $null -eq $spec -or $null -eq $spec.PSObject.Properties[$_] -or $null -eq $spec.PSObject.Properties[$_].Value })
        if ($missing.Count) {
            Write-AutoOSLine "Codex MCP '$name' skipped: catalog row missing required key(s): $($missing -join ', ')" -Level muted
            continue
        }
        $root = [Environment]::GetEnvironmentVariable($spec.root_env, 'Process')
        $reason = ''
        if ([string]::IsNullOrWhiteSpace($root)) { $reason = "$($spec.root_env) is unset" }
        elseif ($root -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') { $reason = "$($spec.root_env) must be an absolute path" }
        elseif (-not (Test-Path -LiteralPath $root -PathType Container)) { $reason = "$($spec.root_env) path does not exist" }
        if ($reason) {
            Write-AutoOSLine "Codex MCP '$name' skipped: $reason" -Level muted
            continue
        }
        $root = (Get-Item -LiteralPath $root).FullName
        $source = Join-Path $root 'src'
        $python = Join-Path $root '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            $found = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $found) {
                Write-AutoOSLine "Codex MCP '$name' skipped: no venv python or python on PATH" -Level muted
                continue
            }
            $python = $found.Source
        }
        $importable = $false
        $probeReason = 'selected python cannot import plangraph'
        try { $importable = & $ImportProbe $python $source $ProbeTimeoutMilliseconds }
        catch [TimeoutException] { $probeReason += ' (import probe timed out)' }
        catch { $importable = $false }
        if (-not $importable) {
            Write-AutoOSLine "Codex MCP '$name' skipped: $probeReason" -Level muted
            continue
        }
        # JSON basic-string escaping is also TOML basic-string escaping. Values
        # of env_vars are never read: only their catalog names are serialized.
        $lines = @(
            "[mcp_servers.$name]",
            ('command = ' + (ConvertTo-Json -InputObject $python -Compress)),
            ('args = [' + ((@($spec.args) | ForEach-Object { ConvertTo-Json -InputObject $_ -Compress }) -join ', ') + ']'),
            ('env_vars = [' + ((@($spec.env_vars) | ForEach-Object { ConvertTo-Json -InputObject $_ -Compress }) -join ', ') + ']'),
            'enabled = true',
            "[mcp_servers.$name.env]",
            ('PYTHONPATH = ' + (ConvertTo-Json -InputObject $source -Compress))
        )
        $path = Join-Path $dir 'config.toml'
        # Strict UTF-8 and an explicit preamble preserve the user's BOM (or its
        # absence), mixed line endings and missing final newline outside our span.
        $encoding = New-Object Text.UTF8Encoding($false, $true)
        $preamble = [byte[]]@()
        $text = ''
        if (Test-Path -LiteralPath $path) {
            try {
                $bytes = [IO.File]::ReadAllBytes($path)
                $offset = 0
                if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
                    $preamble = [byte[]]@(239, 187, 191); $offset = 3
                }
                $text = $encoding.GetString($bytes, $offset, $bytes.Length - $offset)
            } catch {
                Write-AutoOSLine "Codex MCP '$name' skipped: config.toml cannot be read as UTF-8" -Level warn
                continue
            }
        }
        try { $candidate = ConvertTo-AutoOSCodexToml -Text $text -Name $name -Lines $lines }
        catch {
            Write-AutoOSLine "Codex MCP '$name' skipped: $($_.Exception.Message)" -Level warn
            continue
        }
        if ($candidate -ceq $text) {
            Write-AutoOSLine "Codex MCP '$name' already configured - skipped" -Level muted
            continue
        }
        if ($DryRun) {
            Write-AutoOSLine "would set [mcp_servers.$name] in $path (forward environment names only)" -Level muted
            continue
        }
        if (Test-Path -LiteralPath $path) { $null = & $Backup $path }
        if (-not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
        $temp = Join-Path $dir ('.config.toml.autoos-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            [IO.File]::WriteAllBytes($temp, [byte[]]($preamble + $encoding.GetBytes($candidate)))
            if (Test-Path -LiteralPath $path) { [IO.File]::Replace($temp, $path, [NullString]::Value) }
            else { [IO.File]::Move($temp, $path) }
        } catch {
            Write-AutoOSLine "Codex MCP '$name' skipped: config.toml is in use or cannot be replaced" -Level warn
            continue
        } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } }
        Write-AutoOSLine "Codex MCP '$name' configured in $path" -Level ok
    }
}

function Set-AutoOSCodexSpawnerMcp {
    param([scriptblock]$ImportProbe)
    # Keep installer state and the backup callback in their owning module while
    # the Codex-specific hook lives alongside its TOML writer.
    & (Get-Module AutoOS.Install) {
        param($Probe)
        Set-AutoOSCodexMcpEntry -Servers (Get-AutoOSAgentHarness).codex_mcp_servers -DryRun $script:DryRun -ImportProbe $Probe -Backup { param($Path) Copy-AutoOSBackup -Path $Path }
    } $ImportProbe
}

Export-ModuleMember -Function Set-AutoOSCodexMcpEntry, Set-AutoOSCodexSpawnerMcp
