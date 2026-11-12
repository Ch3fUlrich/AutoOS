#Requires -Version 5.1
# Dot-sourced by run-tests.ps1 in the profile-writing group, using its test helpers.

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
            Assert-True ($text.IndexOf($marker) -lt $text.IndexOf('AI_AGENT')) 'guard is below OpenHands'
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
        $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p)); & $apply 6>$null
        Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p))) $before
        Remove-Item -LiteralPath $p; & $apply 6>$null
        Assert-Equal @(Get-ChildItem -LiteralPath $scratch -Recurse -File).Count 0
    }
}
Test-Case 'background profile guard post-install: agent, shell and conda components without callbacks are reached; unrelated components are not' {
    Invoke-BackgroundProfileFixture {
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
Test-Case 'background profile guard predicate: interactive inputs, every mode switch, NoExit, redirection, host and probe exceptions' {
    Invoke-BackgroundProfileFixture {
        & $apply 6>$null
        $ast = [Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$null); $predicate = $ast.Find({ param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst] }, $true).ScriptBlock.GetScriptBlock()
        $inputs = @{ HostName = 'ConsoleHost'; InputRedirected = $false; OutputRedirected = $false; Arguments = @('pwsh') }
        $probe = { $inputs }
        Assert-Equal (& $predicate $probe) $true
        foreach ($switch in @('-Command', '-CommandWithArgs', '-C', '-EncodedCommand', '-Enc', '-EC', '-E', '-File', '-F', '-NonInteractive', '-NonI')) {
            $inputs.Arguments = @('powershell', $switch.ToUpperInvariant(), 'payload'); Assert-Equal (& $predicate $probe) $false $switch
            $inputs.Arguments = @('powershell', '-NoExit', $switch, 'payload'); Assert-Equal (& $predicate $probe) ($switch -notlike '-Non*') "host -NoExit $switch"
            $inputs.Arguments = @('powershell', $switch, 'payload', '-NoExit'); Assert-Equal (& $predicate $probe) ($switch -match '^-(EncodedCommand|Enc|EC|E)$') "$switch payload -NoExit"
        }
        $inputs.Arguments = @('pwsh', '-NoLogo', '-CommandNotASwitch', 'file-with--NonI.ps1'); Assert-Equal (& $predicate $probe) $true
        foreach ($mode in @('-Command', '-File')) { $inputs.Arguments = @('pwsh', '-NoExit', $mode, 'payload', '-NonInteractive'); Assert-Equal (& $predicate $probe) $true 'payload is not a host option' }
        foreach ($field in @('InputRedirected', 'OutputRedirected', 'HostName')) {
            $old = $inputs[$field]; $inputs[$field] = if ($field -eq 'HostName') { 'OtherHost' } else { $true }
            Assert-Equal (& $predicate $probe) $false $field; $inputs[$field] = $old
        }
        Assert-Equal (& $predicate { throw 'no console handles' }) $false
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
        Assert-True ($text.IndexOf($marker) -lt $text.IndexOf('AI_AGENT')) 'OpenHands is above the guard'
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
Test-Case 'background profile guard: non-interactive command-mode PowerShell never reaches the sentinel' {
    Invoke-BackgroundProfileFixture {
        [IO.File]::WriteAllText($p, "'profile-sentinel'`r`n")
        & $apply 6>$null
        foreach ($engine in @('pwsh', 'powershell.exe')) {
            $exe = Get-Command $engine -ErrorAction SilentlyContinue; if (-not $exe) { continue }
            $command = ". '$($p.Replace("'", "''"))'; 'command-completed'"
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $output = & $exe.Source -NoProfile -NonInteractive -EncodedCommand $encoded | Out-String
            Assert-Equal $LASTEXITCODE 0
            Assert-True ($output -match 'command-completed' -and $output -notmatch 'profile-sentinel') "$engine ran profile hooks or failed to execute"
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
