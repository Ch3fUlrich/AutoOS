#Requires -Version 5.1
# Dot-sourced by run-tests.ps1 in the profile-writing group, using its test helpers.
. (Join-Path $PSScriptRoot 'profile-guard.fixtures.ps1')
function Invoke-BackgroundProfileFixture {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignments', '', Justification = 'Fixture variables are consumed by dynamically scoped test bodies.')]
    param([scriptblock]$Body)
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ("opencode\autoos-prof-" + [Guid]::NewGuid().ToString('N'))
    $savedHome = $HOME
    $savedEnv = @{}; foreach ($name in @('HOME', 'USERPROFILE')) { $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name) }
    $docs = Join-Path $scratch 'Documents'; $p = Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1'
    $component = [pscustomobject]@{ Id = 'powershell7'; CategoryId = 'terminal'; PostInstall = $null }
    $seed = "using namespace System.Text`r`nparam([string]`$x)`r`nif (`$env:AI_AGENT -eq 'openhands') { return }`r`n# conda / oh-my-posh / PSFzf`r`n'profile-sentinel'`r`n"
    $marker = 'AutoOS background-shell guard'
    $apply = { Invoke-AutoOSPostInstall -Component $component -Documents $docs }
    try {
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $p) -Force
        $env:HOME = $scratch; $env:USERPROFILE = $scratch
        Set-Variable -Name HOME -Value $scratch -Force -Scope Global
        [IO.File]::WriteAllText($p, $seed, [Text.UTF8Encoding]::new($true))
        Initialize-AutoOSInstaller -DryRun:$false -RepoRoot $Root
        & $Body
    } finally {
        foreach ($name in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnv[$name]) }
        Set-Variable -Name HOME -Value $savedHome -Force -Scope Global
        Initialize-AutoOSInstaller -DryRun:$false -RepoRoot $Root
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
Test-Case 'background profile guard post-install: all four existing profiles, preamble, backup and byte-identical second run' {
    Invoke-BackgroundProfileFixture {
        $paths = @('PowerShell\Microsoft.PowerShell_profile.ps1', 'PowerShell\profile.ps1', 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1', 'WindowsPowerShell\profile.ps1') | ForEach-Object { Join-Path $docs $_ }
        foreach ($path in $paths) { $null = New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force; [IO.File]::WriteAllText($path, $seed, [Text.UTF8Encoding]::new($true)) }
        & $apply 6>$null
        foreach ($path in $paths) {
            $text = [IO.File]::ReadAllText($path); $errs = $null
            $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errs)
            Assert-Equal @($errs).Count 0
            Assert-True ($text.StartsWith("using namespace System.Text`r`nparam([string]`$x)`r`n")) 'preamble changed'
            Assert-True ($ast.EndBlock.Statements[0].Extent.Text -match '^if \(-not') 'guard is not the first executable statement'
            Assert-True ($text.IndexOf($marker) -lt $text.IndexOf('AI_AGENT')) 'background guard must precede OpenHands'
            Assert-True ($text.EndsWith($seed.Substring($seed.IndexOf('if (')))) 'initializer/sentinel tail changed'
            $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path))) $before
            Assert-True ($log -match 'already configured') 'second run did not report left alone'
            $backups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $path) -Filter "$(Split-Path -Leaf $path).autoos-backup-*")
            Assert-Equal $backups.Count 1
            Assert-Equal ([IO.File]::ReadAllText($backups[0].FullName)) $seed
        }
    }
}
Test-Case 'background profile guard post-install: encoding and BOM survive, including an empty BOM-less profile' {
    Invoke-BackgroundProfileFixture {
        $utf8 = [Text.UTF8Encoding]::new($true); $le = [Text.UnicodeEncoding]::new($false, $true); $be = [Text.UnicodeEncoding]::new($true, $true)
        foreach ($encoding in @($utf8, $le, $be, [Text.UTF32Encoding]::new($false, $true), [Text.UTF32Encoding]::new($true, $true), [Text.UTF8Encoding]::new($false), [Text.Encoding]::GetEncoding(28591))) {
            $tail = $encoding.GetBytes("# caf$([char]0xE9)`r`n'profile-sentinel'`r`n"); $bom = $encoding.GetPreamble()
            [IO.File]::WriteAllBytes($p, ($bom + $tail)); & $apply 6>$null
            $bytes = [IO.File]::ReadAllBytes($p)
            Assert-Equal ([BitConverter]::ToString($bytes[($bytes.Length - $tail.Length)..($bytes.Length - 1)])) ([BitConverter]::ToString($tail))
            if ($bom.Length) { Assert-Equal ([BitConverter]::ToString($bytes[0..($bom.Length - 1)])) ([BitConverter]::ToString($bom)) }
            else { Assert-Equal $bytes[0] ([byte][char]'#') }
        }
        [IO.File]::WriteAllBytes($p, [byte[]]@()); & $apply 6>$null
        Assert-Equal ([IO.File]::ReadAllBytes($p)[0]) ([byte][char]'#') 'empty existing profile gained a BOM'
        [IO.File]::WriteAllBytes($p, [byte[]]@([byte][char]'#')); & $apply 6>$null
        Assert-Equal ([IO.File]::ReadAllBytes($p)[-1]) ([byte][char]'#') 'single-byte profile tail changed'
    }
}
Test-Case 'background profile guard post-install: DryRun and missing profiles write nothing; partial marker is left alone' {
    Invoke-BackgroundProfileFixture {
        $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
        Initialize-AutoOSInstaller -DryRun:$true -RepoRoot $Root
        $log = Get-AutoOSConsoleCapture $apply
        Assert-True ($log -match 'would ensure') 'DryRun omitted its plan'
        Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
        Initialize-AutoOSInstaller -DryRun:$false -RepoRoot $Root
        [IO.File]::WriteAllText($p, "# $marker (older partial guard)`r`nreturn`r`n")
        $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p)); $log = Get-AutoOSConsoleCapture $apply
        Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
        # A bare return has no closing block: conservatively require manual review.
        Assert-True ($log -match 'needs manual review') 'partial guard was not flagged for manual review'
        Assert-Equal ([regex]::Matches([IO.File]::ReadAllText($p), [regex]::Escape($marker)).Count) 1
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
        Remove-Item -LiteralPath $p; & $apply 6>$null
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 0
    }
}
Test-Case 'background profile guard post-install: component selection tolerates missing Id and CategoryId; unrelated components are not guarded' {
    Invoke-BackgroundProfileFixture {
        $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
        & (Get-Module -All AutoOS.ProfileGuard) { param($Documents) Install-AutoOSProfileGuard -Component ([pscustomobject]@{}) -Documents $Documents -Backup { throw 'unrelated component must not back up' } } $docs
        Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
        & (Get-Module -All AutoOS.ProfileGuard) { param($Documents) Install-AutoOSProfileGuard -Component ([pscustomobject]@{ CategoryId = 'terminal' }) -Documents $Documents -Backup { param($Path) Copy-AutoOSBackup -Path $Path } } $docs 6>$null
        Assert-True ([IO.File]::ReadAllText($p).Contains($marker)) 'terminal component without Id was not guarded'
        foreach ($case in @(@('powershell7', 'terminal', $true), @('opencode', 'ai-coding', $true), @('miniconda', 'science', $true), @('widget', 'media', $false))) {
            [IO.File]::WriteAllText($p, $seed)
            $component.Id = $case[0]; $component.CategoryId = $case[1]; & $apply 6>$null
            Assert-Equal ([IO.File]::ReadAllText($p).Contains($marker)) $case[2]
        }
        $component.CategoryId = 'terminal'; $component.PostInstall = 'Add-AutoOSGitToPath'
        Invoke-WithModuleStub -Stubs @{ 'Add-AutoOSGitToPath' = { Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line "if (`$env:AI_AGENT -eq 'openhands') { return }" -Marker 'AI_AGENT' } } -Body {
            [IO.File]::WriteAllText($p, "'profile-sentinel'`r`n"); & $apply 6>$null
            $text = [IO.File]::ReadAllText($p)
            Assert-True ($text.Contains($marker) -and $text.IndexOf($marker) -lt $text.IndexOf('AI_AGENT')) 'callback prepended above the guard'
        }
        $setupText = [IO.File]::ReadAllText((Join-Path $Root 'setup.ps1'))
        Assert-True ($setupText -match "(?m)if \(\`$state -in @\('installed', 'skipped'\)\).*Invoke-AutoOSPostInstall -Component \`$c") 'normal setup no longer reaches the profile guard'
    }
}
Test-Case 'background profile guard predicate: console payloads keep profiles; host NonInteractive, redirection, other hosts and probe exceptions skip' {
    Invoke-BackgroundProfileFixture {
        & $apply 6>$null
        $ast = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$null); $predicate = $ast.Find({ param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst] }, $true).ScriptBlock.GetScriptBlock()
        $probe = { $inputs }
        $cases = @(
            @('console, no args', @{}, $true),
            @('-NonInteractive', @{ Arguments = @('pwsh', '-NonInteractive') }, $false),
            @('-NonI', @{ Arguments = @('pwsh', '-NONI') }, $false),
            @('/NonInteractive', @{ Arguments = @('pwsh', '/NonInteractive') }, $false),
            @('/NonI', @{ Arguments = @('pwsh', '/NonI') }, $false),
            @('-NoExit -NonInteractive', @{ Arguments = @('pwsh', '-NoExit', '-NonInteractive') }, $false),
            @('-NonI -NoExit', @{ Arguments = @('pwsh', '-NonI', '-NoExit') }, $false),
            @('input redirected', @{ InputRedirected = $true }, $false),
            @('output redirected', @{ OutputRedirected = $true }, $false),
            @('other host', @{ HostName = 'OtherHost' }, $false),
            @('not a host switch', @{ Arguments = @('pwsh', '-CommandNotASwitch', 'file-with--NonI.ps1') }, $true)
        )
        $payloadOptions = @('File', 'F', 'Command', 'CommandWithArgs', 'C', 'EncodedCommand', 'Enc', 'EC', 'E') | ForEach-Object { "-$_"; "/$_" }
        foreach ($switch in $payloadOptions) {
            $cases += ,@("$switch, no payload", @{ Arguments = @('pwsh', $switch) }, $true)
            $cases += ,@($switch, @{ Arguments = @('pwsh', $switch.ToUpperInvariant(), 'x.ps1') }, $true)
            $cases += ,@("host -NoExit $switch", @{ Arguments = @('pwsh', '-NoExit', $switch, 'payload') }, $true)
            $cases += ,@("$switch payload -NoExit", @{ Arguments = @('pwsh', $switch, 'payload', '-NoExit') }, $true)
        }
        foreach ($mode in $payloadOptions) {
            $cases += ,@("$mode payload -NonInteractive", @{ Arguments = @('pwsh', $mode, 'x.ps1', '-NonInteractive') }, $true)
            $cases += ,@("$mode payload /NonInteractive", @{ Arguments = @('pwsh', $mode, 'x.ps1', '/NonInteractive') }, $true)
            $cases += ,@("-NonI before $mode", @{ Arguments = @('pwsh', '-NonI', $mode, 'payload') }, $false)
            $cases += ,@("/NonI before $mode", @{ Arguments = @('pwsh', '/NonI', $mode, 'payload') }, $false)
        }
        foreach ($case in $cases) {
            $inputs = @{ HostName = 'ConsoleHost'; InputRedirected = $false; OutputRedirected = $false; Arguments = @('pwsh'); Environment = @{} }
            foreach ($field in $case[1].Keys) { $inputs[$field] = $case[1][$field] }
            Assert-Equal (& $predicate $probe) $case[2] $case[0]
        }
        Assert-Equal (& $predicate { throw 'no console handles' }) $false
    }
}
function Get-BackgroundProfileMarker {
    & (Get-Module -All AutoOS.ProfileGuard) { $script:BackgroundShellMarkers }
}
Test-Case 'background profile guard predicate: each non-empty agent marker skips, empty or unset markers keep, and the rendered list has one module source' {
    Invoke-BackgroundProfileFixture {
        & $apply 6>$null
        $markers = @(Get-BackgroundProfileMarker)
        Assert-Equal ($markers -join ',') 'AI_AGENT,CLAUDECODE,OPENCODE,CODEX_SANDBOX,AUTOOS_BACKGROUND_SHELL'
        $ast = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$null)
        $assignments = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$agentMarkers' }, $true))
        Assert-Equal $assignments.Count 1 'rendered guard must have exactly one marker list'
        $rendered = $assignments[0].Right.Find({ param($n) $n -is [Management.Automation.Language.ArrayExpressionAst] }, $true).SafeGetValue()
        Assert-Equal ($rendered -join ',') ($markers -join ',') 'rendered markers differ from the module source'
        $predicate = $ast.Find({ param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst] }, $true).ScriptBlock.GetScriptBlock()
        $inputs = @{ HostName = 'ConsoleHost'; InputRedirected = $false; OutputRedirected = $false; Arguments = @('pwsh'); Environment = @{} }
        $probe = { $inputs }
        Assert-Equal (& $predicate $probe) $true 'unset markers'
        foreach ($name in $markers) {
            foreach ($spelling in @($name, $name.ToLowerInvariant(), ($name.Substring(0, 1) + $name.Substring(1).ToLowerInvariant()))) {
                foreach ($value in @('agent', ' ', '')) {
                    $inputs.Environment = @{ $spelling = $value }
                    Assert-Equal (& $predicate $probe) ([string]::IsNullOrEmpty($value)) "$spelling='$value'"
                }
            }
        }
        # Equality alone could allow a second hard-coded copy: changing the source must change the guard.
        $guardModule = Get-Module -All AutoOS.ProfileGuard
        try {
            & $guardModule { $script:BackgroundShellMarkers = @('AUTOOS_TEST_MARKER') }
            & $apply 6>$null
            Assert-True ([IO.File]::ReadAllText($p).Contains('$agentMarkers = @(''AUTOOS_TEST_MARKER'')')) 'rendering did not use the module marker list'
        } finally { & $guardModule { param($original) $script:BackgroundShellMarkers = $original } $markers }
    }
}
Test-Case 'background profile guard default probe: lower-case environment markers resolve upper-case names in real PowerShell children' {
    Invoke-BackgroundProfileFixture {
        & $apply 6>$null
        $command = @'
$ErrorActionPreference = 'Stop'
try {
$ast = [Management.Automation.Language.Parser]::ParseFile('__PROFILE_PATH__', [ref]$null, [ref]$null)
$predicateAst = $ast.Find({ param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst] }, $true).ScriptBlock
$predicate = $predicateAst.GetScriptBlock()
$probe = $predicateAst.ParamBlock.Parameters[0].DefaultValue.ScriptBlock.GetScriptBlock()
foreach ($name in @('claudecode', 'opencode')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    Set-Item -LiteralPath "Env:$name" -Value 'agent'
    if (-not (@([Environment]::GetEnvironmentVariables().Keys) -ccontains $name)) { throw "marker not stored in lower case: $name" }
    $state = & $probe
    if ($state.Environment[$name.ToUpperInvariant()] -cne 'agent') { throw "probe lookup is case-sensitive: $name" }
    # Captured stdout must not mask a missed marker by making the predicate skip anyway.
    $state.HostName = 'ConsoleHost'; $state.InputRedirected = $false; $state.OutputRedirected = $false; $state.Arguments = @('pwsh')
    if (& $predicate { $state }) { throw "predicate missed marker: $name" }
    Remove-Item -LiteralPath "Env:$name"
}
'probe-case-lookup-ok'
} catch { $_.Exception.Message; exit 1 }
'@
        $command = $command.Replace('__PROFILE_PATH__', $p.Replace("'", "''"))
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        foreach ($engine in @('pwsh', 'powershell.exe')) {
            $exe = Get-Command $engine -ErrorAction SilentlyContinue
            if (-not $exe) { Skip "$engine unavailable for default-probe regression"; continue }
            $output = & $exe.Source -NoProfile -EncodedCommand $encoded | Out-String
            Assert-Equal $LASTEXITCODE 0 "$engine default probe: $output"
            Assert-True ($output -match 'probe-case-lookup-ok') "$engine did not verify lower-case markers"
        }
    }
}
Test-Case 'background profile guard default probe: case-colliding names retain any non-empty value' {
    Invoke-BackgroundProfileFixture {
        & $apply 6>$null
        $ast = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$null)
        $predicateAst = $ast.Find({ param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst] }, $true).ScriptBlock
        $probeText = $predicateAst.ParamBlock.Parameters[0].DefaultValue.ScriptBlock.Extent.Text
        # Windows normally coalesces names; inject a case-sensitive snapshot into the real probe.
        $probe = [scriptblock]::Create('param($variables)' + $probeText.Substring(1, $probeText.Length - 2).Replace('[Environment]::GetEnvironmentVariables()', '$variables'))
        foreach ($values in @(@('agent', ''), @('', 'agent'))) {
            $variables = Get-BackgroundProfileEnvironmentFixture -LowerValue $values[0] -UpperValue $values[1]
            $state = & $probe $variables
            Assert-Equal $state.Environment['CLAUDECODE'] 'agent'
            Assert-Equal $state.Environment['claudecode'] 'agent'
        }
    }
}
Test-Case 'background profile guard: later OpenHands prepends stay below it and a preamble ending at EOF remains valid' {
    Invoke-BackgroundProfileFixture {
        [IO.File]::WriteAllText($p, 'param([string]$x)'); & $apply 6>$null
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line "if (`$env:AI_AGENT -eq 'openhands') { return }" -Marker 'AI_AGENT' 6>$null
        $text = [IO.File]::ReadAllText($p); $errs = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs)
        Assert-Equal @($errs).Count 0
        Assert-True ($ast.EndBlock.Statements[0].Extent.Text.Contains($marker)) 'later prepend displaced the background guard'
        Assert-True ($text.IndexOf($marker) -lt $text.IndexOf('AI_AGENT')) 'OpenHands must follow the background guard'
    }
}
Test-Case 'background profile guard: named-block profiles warn and remain byte-identical instead of breaking their semantics' {
    Invoke-BackgroundProfileFixture {
        $profiles = @('begin { "native-hook" }', 'param(); process { "native-hook" }', 'end { "native-hook" }')
        if ($PSVersionTable.PSVersion -ge [version]'7.3') { $profiles += 'clean { "native-hook" }' }
        foreach ($text in $profiles) {
            [IO.File]::WriteAllText($p, $text); $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
            Assert-True ($log -match 'named blocks.*left unchanged') 'unsupported profile was not announced'
            Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
        }
    }
}
Test-Case 'background profile guard: redirected command-mode PowerShell never reaches the sentinel' {
    Invoke-BackgroundProfileFixture {
        [IO.File]::WriteAllText($p, "'profile-sentinel'`r`n")
        & $apply 6>$null
        $savedMarkers = @{}
        try {
            foreach ($name in (Get-BackgroundProfileMarker)) {
                $savedMarkers[$name] = [Environment]::GetEnvironmentVariable($name)
                [Environment]::SetEnvironmentVariable($name, $null)
            }
            foreach ($engine in @('pwsh', 'powershell.exe')) {
                $exe = Get-Command $engine -ErrorAction SilentlyContinue; if (-not $exe) { continue }
                $command = ". '$($p.Replace("'", "''"))'; 'command-completed'; 'stdout-redirected=' + [Console]::IsOutputRedirected"
                $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
                # Out-String redirects stdout: -EncodedCommand alone must not skip the profile.
                $output = & $exe.Source -NoProfile -EncodedCommand $encoded | Out-String
                Assert-Equal $LASTEXITCODE 0
                Assert-True ($output -match 'stdout-redirected=True') "$engine did not run with redirected stdout"
                Assert-True ($output -match 'command-completed' -and $output -notmatch 'profile-sentinel') "$engine ran profile hooks or failed to execute"
            }
        } finally { foreach ($name in $savedMarkers.Keys) { [Environment]::SetEnvironmentVariable($name, $savedMarkers[$name]) } }
    }
}
function Get-BackgroundProfileGuardReference {
    # Generate the current block through production code, in a separate scratch profile.
    $referenceDocs = Join-Path $scratch 'reference'
    $referencePath = Join-Path $referenceDocs 'PowerShell\profile.ps1'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $referencePath) -Force
    [IO.File]::WriteAllText($referencePath, '')
    Invoke-AutoOSPostInstall -Component $component -Documents $referenceDocs 6>$null
    $ast = [Management.Automation.Language.Parser]::ParseFile($referencePath, [ref]$null, [ref]$null)
    $ast.EndBlock.Statements[0].Extent.Text
}
Test-Case 'background profile guard: literal previous guards are each replaced once with a backup, then already configured' {
    foreach ($fixture in (Get-BackgroundProfileGuardLegacyFixture).GetEnumerator()) {
        Invoke-BackgroundProfileFixture {
            $current = (Get-BackgroundProfileGuardReference) -replace '\r?\n', "`r`n"
            $previous = $fixture.Value -replace '\r?\n', "`r`n"
            Assert-True ($previous -cne $current) "$($fixture.Key) guard is still current"
            $prefix = "# added by AutoOS`r`n"; $suffix = "`r`n'profile-sentinel'`r`n"
            [IO.File]::WriteAllText($p, ($prefix + $previous + $suffix), [Text.UTF8Encoding]::new($true))
            $original = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-True ($log -match 'profile updated' -and $log -notmatch 'already configured') "$($fixture.Key) guard was not updated"
            Assert-Equal ([IO.File]::ReadAllText($p)) ($prefix + $current + $suffix)
            $after = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $after
            Assert-True ($log -match 'already configured' -and $log -notmatch 'profile updated') 'second run did not leave the current guard alone'
            $backups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $p) -Filter '*.autoos-backup-*')
            Assert-Equal $backups.Count 1
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backups[0].FullName))) $original
        }
    }
}
Test-Case 'background profile guard: outdated block is replaced once in place, preserving all surrounding bytes and its backup' {
    $encodings = @([Text.UTF8Encoding]::new($true), [Text.UnicodeEncoding]::new($false, $true), [Text.UnicodeEncoding]::new($true, $true),
        [Text.UTF32Encoding]::new($false, $true), [Text.UTF32Encoding]::new($true, $true), [Text.UTF8Encoding]::new($false), [Text.Encoding]::GetEncoding(28591))
    foreach ($encoding in $encodings) {
        Invoke-BackgroundProfileFixture {
            $current = (Get-BackgroundProfileGuardReference) -replace '\r?\n', "`r`n"
            $prefix = "# caf$([char]0xE9)`nusing namespace System.Text`r`n'preceding hook'`r`n# added by AutoOS`r`n"
            $old = "if (`$true) { # $marker`: older version`r`n    `$text = 'quoted } brace'`r`n    # } not the end`r`n    if (`$false) { return }`r`n    return`r`n}"
            $suffix = "`r`n# following caf$([char]0xE9)`n'profile-sentinel'" # Deliberately no trailing newline.
            $original = $encoding.GetPreamble() + $encoding.GetBytes($prefix + $old + $suffix)
            [IO.File]::WriteAllBytes($p, $original)
            $log = Get-AutoOSConsoleCapture $apply
            Assert-True ($log -match 'profile updated' -and $log -notmatch 'already configured') 'outdated guard was not reported as updated'
            $expected = $encoding.GetPreamble() + $encoding.GetBytes($prefix + $current + $suffix)
            $after = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            Assert-Equal $after ([Convert]::ToBase64String($expected)) 'replacement changed bytes outside the block or did not install the current block'
            Assert-Equal ([regex]::Matches($encoding.GetString([IO.File]::ReadAllBytes($p)), [regex]::Escape($marker)).Count) 1
            $backups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $p) -Filter '*.autoos-backup-*')
            Assert-Equal $backups.Count 1
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backups[0].FullName))) ([Convert]::ToBase64String($original))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $after
            Assert-True ($log -match 'already configured' -and $log -notmatch 'profile updated') 'current guard was not reported as already configured'
            Assert-Equal @(Get-ChildItem -LiteralPath (Split-Path -Parent $p) -Filter '*.autoos-backup-*').Count 1
        }
    }
}
Test-Case 'background profile guard: inserted and replaced blocks use LF-only or CRLF profile line endings' {
    foreach ($eol in @("`n", "`r`n")) {
        Invoke-BackgroundProfileFixture {
            $seed = "using namespace System.Text${eol}param([string]`$x)${eol}'profile-sentinel'${eol}"
            [IO.File]::WriteAllText($p, $seed, [Text.UTF8Encoding]::new($false))
            & $apply 6>$null
            $inserted = [IO.File]::ReadAllText($p)
            if ($eol -eq "`n") { Assert-True (-not $inserted.Contains("`r")) 'LF-only insertion added a CR byte' }
            else { Assert-True ($inserted -notmatch '(?<!\r)\n') 'CRLF insertion added a bare LF' }
            $current = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$null).EndBlock.Statements[0].Extent.Text
            $outdated = $inserted.Replace($current, $current.Replace('native profile hooks', 'outdated native hooks'))
            [IO.File]::WriteAllText($p, $outdated, [Text.UTF8Encoding]::new($false))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-True ($log -match 'profile updated') 'line-ending replacement was not performed'
            foreach ($text in @($inserted, [IO.File]::ReadAllText($p))) {
                if ($eol -eq "`n") { Assert-True (-not $text.Contains("`r")) 'LF-only profile gained a CR byte' }
                else { Assert-True ($text -notmatch '(?<!\r)\n') 'CRLF profile gained a bare LF' }
            }
            Assert-Equal ([IO.File]::ReadAllText($p)) $inserted 'replacement did not restore the current block and original trailing newline'
        }
    }
}
Test-Case 'background profile guard: DryRun on an outdated block reports an update without writes or backups' {
    Invoke-BackgroundProfileFixture {
        [IO.File]::WriteAllText($p, "if (`$true) { # $marker`: old`n    return`n}`n'profile-sentinel'", [Text.UTF8Encoding]::new($true))
        $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
        Initialize-AutoOSInstaller -DryRun:$true -RepoRoot $Root
        $log = Get-AutoOSConsoleCapture $apply
        Assert-True ($log -match 'would update') 'DryRun did not identify the outdated guard update'
        Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
    }
}
Test-Case 'background profile guard: ambiguous, nested, unclosed or non-comment markers need manual review without writes' {
    Invoke-BackgroundProfileFixture {
        $cases = @("if (`$true) { # $marker`nreturn", "# $marker`nif (`$true) { return }",
            "function f { if (`$true) { # $marker`nreturn } }", "'quoted $marker'`nif (`$true) { return }",
            "if (`$true) { # $marker`nreturn }`n# $marker", "'before'; if (`$true) { # $marker`nreturn }",
            "begin { # $marker`n'profile-sentinel' }", "# $marker")
        foreach ($text in $cases) {
            [IO.File]::WriteAllText($p, $text, [Text.UTF8Encoding]::new($true))
            $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))
            $log = Get-AutoOSConsoleCapture $apply
            Assert-True ($log -match 'needs manual review') 'unsafe guard boundaries were accepted'
            Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
            Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 1
        }
    }
}

Test-Case 'Set-AutoOSOpenHandsConfig prepends the OpenHands guard to all four existing profiles' {
    $body = (Get-Command Set-AutoOSOpenHandsConfig).Definition
    Assert-True ($body -match 'Add-AutoOSProfileLine -Prepend') 'no -Prepend guard call'
    Assert-True ($body -match "PowerShell\\Microsoft\.PowerShell_profile\.ps1") 'pwsh profile not guarded'
    Assert-True ($body -match "'PowerShell\\profile\.ps1'") 'pwsh all-hosts profile.ps1 not guarded'
    Assert-True ($body -match "WindowsPowerShell\\Microsoft\.PowerShell_profile\.ps1") 'Windows PowerShell profile not guarded'
    Assert-True ($body -match "WindowsPowerShell\\profile\.ps1") 'Windows PowerShell all-hosts profile.ps1 not guarded'
}
