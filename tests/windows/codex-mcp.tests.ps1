Test-Case 'codex spawner MCP catalog hook is exported' {
    $entry = @($winCatalog.categories.components | Where-Object { $_.id -eq 'codex' })[0]; Assert-Equal $entry.postInstall 'Set-AutoOSCodexSpawnerMcp'
    Assert-True ($null -ne (Get-Command $entry.postInstall -ErrorAction SilentlyContinue)) 'Codex hook not exported'
    $spec = (Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json).codex_mcp_servers.'plangraph-spawner'
    Assert-Equal $spec.root_env 'PLANGRAPH_ROOT'
    Assert-Equal ($spec.env_vars -join ',') 'AUTOOS_OMNIROUTE_KEY,PLANGRAPH_GATEWAY_LOG_TOKEN,AUTOOS_ROOT,AUTOOS_HOST_NAME'
    Assert-Equal ($spec.args -join ',') '-m,plangraph.spawner.mcp'
}

foreach ($scenario in @('create', 'append', 'replace', 'equal', 'quoted', 'dotted', 'quoted-dotted', 'inline', 'nested', 'literal-key', 'array-type', 'multiline', 'native-ok', 'native-fail', 'absent', 'unset', 'missing', 'import', 'venv', 'dry-create', 'dry-replace', 'data', 'array-table', 'array-table-spaced', 'following-array', 'no-newline', 'atomic', 'in-use', 'relative', 'posix-root', 'drive-relative', 'unc-root', 'missing-root_env', 'missing-args', 'missing-env_vars', 'timeout', 'native-isolation', 'unexpected-render', 'catalog-rows')) {
    Test-Case "codex spawner MCP $scenario is hermetic and idempotent" {
        $nativePython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1; if (($scenario -like 'native-*' -or $scenario -eq 'timeout') -and -not $nativePython) { Skip 'no python for native import probe'; return }
        $scratch = Join-Path $env:TEMP ('autoos-codex-' + [Guid]::NewGuid().ToString('N'))
        $homeDir = Join-Path $scratch 'home'; $checkout = Join-Path $scratch "checkout with 'quotes'"; $bin = Join-Path $scratch 'bin'
        $null = New-Item -ItemType Directory -Path $homeDir, (Join-Path $checkout 'src'), $bin -Force
        $saved = @{}; $originalHandle = $null; $locationBefore = Get-Location
        foreach ($key in @('HOME', 'USERPROFILE', 'PATH', 'PLANGRAPH_ROOT', 'PYTHONPATH', 'AUTOOS_TEST_CHECKOUT', 'AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME')) {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        }
        $module = Get-Module AutoOS.Install; $harnessBefore = & $module { $script:AgentHarness }
        $mcpModule = Get-Module AutoOS.Mcp -All | Select-Object -First 1
        $renderBefore = & $mcpModule { (Get-Item Function:ConvertTo-AutoOSCodexToml).ScriptBlock }
        # Scope fixtures to the catalog rows actually under test, not future unrelated servers.
        $testHarness = Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json
        $testHarness.codex_mcp_servers = [pscustomobject]@{ 'plangraph-spawner' = $testHarness.codex_mcp_servers.'plangraph-spawner' }
        try {
            $env:HOME = $homeDir; $env:USERPROFILE = $homeDir; $env:PATH = $bin
            $env:PLANGRAPH_ROOT = $checkout; $env:PYTHONPATH = 'REPLACE_WITH_AMBIENT_PATH'
            foreach ($key in @('AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME')) {
                [Environment]::SetEnvironmentVariable($key, ('REPLACE_WITH_TEST_' + $key), 'Process')
            }
            [IO.File]::WriteAllText((Join-Path $bin 'python.exe'), '')
            if ($scenario -in @('native-ok', 'native-fail')) {
                $env:PATH = Split-Path -Parent $nativePython.Source
                $body = if ($scenario -eq 'native-ok') { '# synthetic importable checkout' } else { 'raise RuntimeError("synthetic import failure")' }
                [IO.File]::WriteAllText((Join-Path $checkout 'src/plangraph.py'), $body)
            }
            if ($scenario -in @('create', 'dry-create', 'data')) { [IO.File]::WriteAllText((Join-Path $bin 'codex.cmd'), '@exit /b 0') }
            $cfgDir = Join-Path $homeDir '.codex'; $cfg = Join-Path $cfgDir 'config.toml'
            if ($scenario -notin @('absent', 'unset', 'missing', 'import', 'create', 'dry-create', 'data')) { $null = New-Item -ItemType Directory -Path $cfgDir }
            if ($scenario -in @('unset', 'missing', 'import')) { [IO.File]::WriteAllText((Join-Path $bin 'codex.cmd'), '@exit /b 0') }
            if ($scenario -eq 'unset') { $env:PLANGRAPH_ROOT = $null }
            if ($scenario -eq 'missing') { $env:PLANGRAPH_ROOT = Join-Path $scratch 'missing' }
            if ($scenario -eq 'relative') { Set-Location -LiteralPath $scratch; $env:PLANGRAPH_ROOT = Split-Path -Leaf $checkout }
            if ($scenario -eq 'posix-root') { $env:PLANGRAPH_ROOT = '/opt/x' }
            if ($scenario -eq 'drive-relative') { $env:PLANGRAPH_ROOT = 'C:foo' }
            if ($scenario -eq 'unc-root') {
                $env:PLANGRAPH_ROOT = '\\REPLACE_WITH_TEST_HOST\REPLACE_WITH_TEST_SHARE\checkout'; $env:AUTOOS_TEST_CHECKOUT = $checkout
                # Resolve a synthetic UNC root locally: no network share or real home is touched.
                & $mcpModule {
                    function script:Test-Path { param($LiteralPath, $PathType) if ($LiteralPath -eq $env:PLANGRAPH_ROOT) { $PSBoundParameters['LiteralPath'] = $env:AUTOOS_TEST_CHECKOUT }; Microsoft.PowerShell.Management\Test-Path @PSBoundParameters }
                    function script:Get-Item { param($LiteralPath) if ($LiteralPath -eq $env:PLANGRAPH_ROOT) { $PSBoundParameters['LiteralPath'] = $env:AUTOOS_TEST_CHECKOUT }; Microsoft.PowerShell.Management\Get-Item @PSBoundParameters }
                }
            }
            if ($scenario -eq 'venv') { $null = New-Item -ItemType Directory -Path (Join-Path $checkout '.venv/Scripts') -Force; [IO.File]::WriteAllText((Join-Path $checkout '.venv/Scripts/python.exe'), '') }
            $server = 'plangraph-spawner'; $rootVar = 'PLANGRAPH_ROOT'
            if ($scenario -eq 'data') {
                $custom = $testHarness; $spec = $custom.codex_mcp_servers.'plangraph-spawner'
                $spec.root_env = 'AUTOOS_TEST_CHECKOUT'; $spec.env_vars = @('AUTOOS_TEST_FORWARD_NAME')
                $custom.codex_mcp_servers.PSObject.Properties.Remove('plangraph-spawner')
                $custom.codex_mcp_servers | Add-Member -NotePropertyName 'fixture-spawner' -NotePropertyValue $spec
                $env:AUTOOS_TEST_CHECKOUT = $checkout; $env:PLANGRAPH_ROOT = $null; $server = 'fixture-spawner'; $rootVar = 'AUTOOS_TEST_CHECKOUT'
            }
            if ($scenario -in @('catalog-rows', 'unexpected-render')) { $testHarness.codex_mcp_servers | Add-Member -NotePropertyName 'fixture-second' -NotePropertyValue $testHarness.codex_mcp_servers.'plangraph-spawner' }
            if ($scenario -like 'missing-*' -and $scenario -ne 'missing') { $testHarness.codex_mcp_servers.$server.PSObject.Properties.Remove($scenario.Substring('missing-'.Length)) }
            & $module { param($h) $script:AgentHarness = $h } $testHarness
            $probeRows = @($testHarness.codex_mcp_servers.PSObject.Properties | Where-Object {
                $row = $_.Value
                @('root_env', 'args', 'env_vars' | Where-Object { $null -eq $row.PSObject.Properties[$_] }).Count -eq 0 -and $scenario -notin @('absent', 'unset', 'missing', 'relative', 'posix-root', 'drive-relative')
            })
            $prefix = "# untouched $([char]0x2603)`r`nmodel = 'mine'`n[mcp_servers.other] # comment`r`ncommand = 'node'`r`n`r`n"
            if ($scenario -eq 'multiline') { $prefix = "description = '''`n[mcp_servers.plangraph-spawner]`nnot a table`n'''`n" + $prefix }
            $suffix = "# keep this separator`n[notice] # untouched`r`nhide = false" # no final newline
            if ($scenario -eq 'following-array') { $suffix = "# keep this separator`n[[other.array]] # untouched`r`nvalue = 'keep'`r`n`n[[other.array]]`nvalue = 'also keep'" }
            $oldSection = "[mcp_servers.$server] # stale`r`ncommand = 'old'`r`nargs = []`r`n[mcp_servers.$server.env]`r`nOLD = 'REPLACE_WITH_STALE_VALUE'`r`n"
            if ($scenario -eq 'quoted') { $oldSection = $oldSection.Replace('mcp_servers.', '"mcp_servers".') }
            if ($scenario -eq 'nested') { $oldSection = $oldSection.Replace('args = []', "extra = [`n[`"nested`"],`n[`"last`"]`n]") }
            $encoding = New-Object Text.UTF8Encoding($true)
            if ($scenario -in @('replace', 'dry-replace', 'quoted', 'nested', 'following-array', 'atomic')) { [IO.File]::WriteAllText($cfg, ($prefix + $oldSection + $suffix), $encoding) }
            if ($scenario -eq 'no-newline') { [IO.File]::WriteAllText($cfg, ($prefix + $oldSection.TrimEnd([char[]]"`r`n")), $encoding) }
            if ($scenario -like 'array-table*') {
                $header = if ($scenario -eq 'array-table') { "[[mcp_servers.$server]]" } else { "`t[[ 'mcp_servers' . `"$server`" ]] # keep" }
                [IO.File]::WriteAllText($cfg, ($prefix + $header + "`r`ncommand = 'old'`r`n" + $suffix), $encoding)
            }
            # A read/delete-sharing handle permits atomic replacement, not an in-place write.
            if ($scenario -eq 'atomic') { $originalHandle = [IO.File]::Open($cfg, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::Read -bor [IO.FileShare]::Delete)) }
            if ($scenario -in @('dotted', 'quoted-dotted', 'inline')) { $definition = switch ($scenario) { dotted { "$server.command = 'old'" } quoted-dotted { "`"$server`".command = 'old'" } inline { "$server = { command = 'old' }" } }; [IO.File]::WriteAllText($cfg, ("[mcp_servers]`n$definition`n" + $suffix), $encoding) }
            if ($scenario -in @('append', 'in-use')) { [IO.File]::WriteAllText($cfg, $prefix.TrimEnd([char[]]"`r`n"), $encoding) }
            if ($scenario -eq 'multiline') { [IO.File]::WriteAllText($cfg, $prefix, $encoding) }
            if ($scenario -in @('equal', 'literal-key', 'array-type')) {
                $literalCommand = (Join-Path $bin 'python.exe')
                $literalSource = (Join-Path $checkout 'src').Replace('\', '\\')
                $equalSection = "[mcp_servers.'$server'] # already correct`ncommand='$literalCommand' # keep formatting`nargs=['-m', 'plangraph.spawner.mcp']`nenabled=true`nenv_vars=['AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME']`n[mcp_servers.'$server'.env]`nPYTHONPATH = `"$literalSource`"`n"
                if ($scenario -eq 'literal-key') { $equalSection = $equalSection.Replace("[mcp_servers.'$server'.env]`nPYTHONPATH", '"env.PYTHONPATH"') }
                if ($scenario -eq 'array-type') { $equalSection = $equalSection.Replace("command='$literalCommand'", "command=['$literalCommand']").Replace("PYTHONPATH = `"$literalSource`"", "PYTHONPATH = [`"$literalSource`"]") }
                [IO.File]::WriteAllText($cfg, ($prefix + $equalSection + $suffix), $encoding)
            }
            $before = if (Test-Path $cfg) { [Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg)) } else { '' }
            $expectedPython = if ($scenario -like 'native-*') { $nativePython.Source } elseif ($scenario -eq 'venv') { Join-Path $checkout '.venv/Scripts/python.exe' } else { Join-Path $bin 'python.exe' }
            $probeState = @{ Calls = 0; Python = ''; Source = '' }
            $probe = { param($Python, $Source) $probeState.Calls++; $probeState.Python = $Python; $probeState.Source = $Source; $scenario -ne 'import' }
            Initialize-AutoOSInstaller -DryRun ($scenario -like 'dry-*') -RepoRoot $Root
            $log = Join-Path $scratch 'ui.log'; Initialize-AutoOSLog -Path $log
            if ($scenario -eq 'native-ok') {
                $pythonTemp = Join-Path $scratch 'python-temp'; $null = New-Item -ItemType Directory -Path $pythonTemp
                $neutral = & $mcpModule {
                    param($Python, $Expected, $PythonTemp)
                    Invoke-AutoOSCodexProbeProcess -FilePath $Python -Arguments @('-I', '-B', '-c', 'import os,sys,tempfile; tempfile.tempdir=sys.argv[2]; assert os.path.samefile(os.getcwd(), sys.argv[1]); assert not os.path.samefile(os.getcwd(), tempfile.gettempdir())', $Expected, $PythonTemp)
                } $nativePython.Source ([IO.Path]::GetTempPath()) $pythonTemp
                Assert-True $neutral 'probe cwd did not match the explicit expected directory'
            }
            if ($scenario -eq 'native-isolation') {
                $env:PATH = Split-Path -Parent $nativePython.Source
                $ambient = Join-Path $scratch 'ambient'; $untrusted = Join-Path $scratch 'untrusted-cwd'
                foreach ($fakeRoot in @($ambient, $untrusted)) {
                    $package = Join-Path $fakeRoot 'plangraph'; $null = New-Item -ItemType Directory -Path $package -Force
                    [IO.File]::WriteAllText((Join-Path $package '__init__.py'), 'from pathlib import Path; Path(__file__).with_name("imported").touch()')
                }
                foreach ($origin in @('ambient', 'cwd')) {
                    $env:PYTHONPATH = if ($origin -eq 'ambient') { $ambient } else { '' }
                    Set-Location -LiteralPath $(if ($origin -eq 'cwd') { $untrusted } else { $scratch })
                    Initialize-AutoOSLog -Path $log
                    Invoke-AutoOSPostInstall -Component ([pscustomobject]@{ PostInstall = 'Set-AutoOSCodexSpawnerMcp' })
                    Assert-True (-not (Test-Path $cfg)) "untrusted $origin made the real import probe pass"
                    Assert-True ([IO.File]::ReadAllText($log) -match 'skipped.*cannot import plangraph') "untrusted $origin was not skipped"
                    foreach ($fakeRoot in @($ambient, $untrusted)) { Assert-True (-not (Test-Path (Join-Path $fakeRoot 'plangraph/imported'))) "executed untrusted $origin package" }
                }
                return
            } elseif ($scenario -eq 'timeout') {
                $pidFile = Join-Path $scratch 'probe.pid'; $sleepScript = Join-Path $scratch 'sleep with spaces.py'; $shim = Join-Path $scratch 'python shim.cmd'
                # The child cannot exit on its own before any injected deadline.
                [IO.File]::WriteAllText($sleepScript, 'import os,sys,threading; from pathlib import Path; Path(sys.argv[1]+".tmp").write_text(str(os.getppid())+","+str(os.getpid())); os.replace(sys.argv[1]+".tmp",sys.argv[1]); threading.Event().wait()')
                [IO.File]::WriteAllText($shim, ('@"' + $nativePython.Source + '" %*' + "`r`n"))
                $ready = {
                    param($Process)
                    $startup = [Diagnostics.Stopwatch]::StartNew()
                    while (-not (Test-Path $pidFile) -and -not $Process.HasExited -and $startup.ElapsedMilliseconds -lt 30000) { Start-Sleep -Milliseconds 20 }
                    if (-not (Test-Path $pidFile)) { throw 'shim child did not become ready within the startup budget' }
                }
                $sleepProbe = {
                    & $mcpModule { param($Shim, $Script, $PidFile, $Timeout, $Ready) Invoke-AutoOSCodexProbeProcess -FilePath $Shim -Arguments @('-I', '-B', $Script, $PidFile) -TimeoutMilliseconds $Timeout -Started $Ready } $shim $sleepScript $pidFile $args[2] $ready
                }
                try {
                    & $module { param($Servers, $Probe) Set-AutoOSCodexMcpEntry -Servers $Servers -ImportProbe $Probe -ProbeTimeoutMilliseconds 100 -Backup { throw 'timeout must not back up' } } $testHarness.codex_mcp_servers $sleepProbe
                    Assert-True ([IO.File]::ReadAllText($log) -match 'skipped.*cannot import plangraph.*timed out') 'timeout reason missing'
                    Assert-True (-not (Test-Path $cfg)) 'timeout wrote a config'
                    Assert-True (Test-Path $pidFile) 'shim child never started'
                    foreach ($processId in ([IO.File]::ReadAllText($pidFile) -split ',')) {
                        $child = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
                        if ($child) { try { Assert-True ($child.WaitForExit(10000)) "timed-out process $processId survived" } finally { $child.Dispose() } }
                        Assert-True (-not (Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue)) "timed-out process $processId still alive"
                    }
                } finally {
                    if (Test-Path $pidFile) { foreach ($processId in ([IO.File]::ReadAllText($pidFile) -split ',')) { Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue } }
                }
                return
            } elseif ($scenario -like 'missing-*' -and $scenario -ne 'missing') {
                $key = $scenario.Substring('missing-'.Length)
                & $module { param($Servers, $Probe) Set-AutoOSCodexMcpEntry -Servers $Servers -ImportProbe $Probe -Backup { throw 'invalid catalog must not back up' } } $testHarness.codex_mcp_servers $probe
                Assert-True ([IO.File]::ReadAllText($log) -match "skipped.*missing.*$key") 'missing catalog key reason absent'
                Assert-True (-not (Test-Path $cfg)) 'invalid catalog wrote a config'
                Assert-Equal $probeState.Calls $probeRows.Count
                return
            } elseif ($scenario -eq 'unexpected-render') {
                & $mcpModule {
                    $script:TestRenderCalls = 0
                    function script:ConvertTo-AutoOSCodexToml { $script:TestRenderCalls++; throw (New-Object InvalidOperationException('synthetic render bug')) }
                }
                Set-AutoOSCodexSpawnerMcp -ImportProbe $probe
                $out = [IO.File]::ReadAllText($log)
                Assert-True ($out -match 'WARN\s+Codex MCP.*unexpected.*InvalidOperationException.*synthetic render bug') 'unexpected exception warning missing'
                Assert-True ($out -notmatch 'skipped:') 'unexpected bug was reported as a routine skip'
                Assert-Equal (& $mcpModule { $script:TestRenderCalls }) $probeRows.Count 'setup did not continue to the next row'
                Assert-True (-not (Test-Path $cfg)) 'unexpected render failure wrote a config'
                return
            } elseif ($scenario -eq 'catalog-rows') {
                Initialize-AutoOSInstaller -DryRun $true -RepoRoot $Root
                Set-AutoOSCodexSpawnerMcp -ImportProbe $probe
                Assert-Equal $probeState.Calls $probeRows.Count
                Assert-True (-not (Test-Path $cfg)) 'catalog row dry-run wrote a config'
                return
            } elseif ($scenario -eq 'in-use') {
                $lockedHandle = [IO.File]::Open($cfg, 'Open', 'Read', 'Read')
                try {
                    Set-AutoOSCodexSpawnerMcp -ImportProbe $probe
                    $out = [IO.File]::ReadAllText($log)
                    Assert-True ($out -match ('WARN\s+' + [regex]::Escape("Codex MCP '$server' skipped: config.toml is in use or cannot be replaced"))) 'locked config warning missing'
                    Assert-True ($out -notmatch 'configured in') 'locked config reported configured'
                    Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $before
                    Assert-Equal @(Get-ChildItem -LiteralPath $cfgDir -Filter '*.tmp' -Force).Count 0
                    $backups = @(Get-ChildItem -LiteralPath $cfgDir -Filter '*.autoos-backup-*')
                    Assert-Equal $backups.Count 1
                    Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backups[0].FullName))) $before
                } finally { $lockedHandle.Dispose() }
                Set-AutoOSCodexSpawnerMcp -ImportProbe $probe
                Assert-True ([IO.File]::ReadAllText($cfg).Contains("[mcp_servers.$server]")) 'unlocked config was not configured'
                Assert-True ([IO.File]::ReadAllText($log) -match 'configured in') 'unlocked config success missing'
                Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backups[0].FullName))) $before
                Assert-Equal @(Get-ChildItem -LiteralPath $cfgDir -Filter '*.tmp' -Force).Count 0
                return
            } elseif ($scenario -like 'native-*') { Invoke-AutoOSPostInstall -Component ([pscustomobject]@{ PostInstall = 'Set-AutoOSCodexSpawnerMcp' }) } else { Set-AutoOSCodexSpawnerMcp -ImportProbe $probe }
            $out = [IO.File]::ReadAllText($log)
            if ($scenario -notlike 'native-*') { Assert-Equal $probeState.Calls $probeRows.Count }
            if ($scenario -in @('dotted', 'quoted-dotted', 'inline', 'array-table', 'array-table-spaced')) { Assert-True ($out -match 'skipped.*(dotted|inline|array).*manual configuration') 'unsupported definition not safely skipped'
                Assert-True ($out -match 'MUTED\s+Codex MCP.*skipped:' -and $out -notmatch 'unexpected') 'manual configuration was not a routine skip'
                Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $before
                Assert-Equal @(Get-ChildItem $cfgDir -Filter '*.autoos-backup-*').Count 0
                return }
            if ($scenario -like 'native-*') {
                Assert-True (-not (Test-Path (Join-Path $checkout 'src/__pycache__'))) 'import probe wrote bytecode'
                if ($scenario -eq 'native-fail') { Assert-True ($out -match 'skipped.*cannot import plangraph') 'native failure not reported'
                    Assert-True (-not (Test-Path $cfg)) 'native import failure wrote a config'
                    return }
                $probeState.Python = $expectedPython; $probeState.Source = Join-Path $checkout 'src'
            }
            if ($scenario -in @('absent', 'unset', 'missing', 'import', 'relative', 'posix-root', 'drive-relative') -or $scenario -like 'dry-*') {
                $reason = switch ($scenario) { absent { 'codex.*not present' } unset { "$rootVar.*unset" } missing { "$rootVar.*does not exist" } import { 'cannot import plangraph' } { $_ -in @('relative', 'posix-root', 'drive-relative') } { 'skipped: PLANGRAPH_ROOT must be a drive-rooted or UNC absolute Windows path' } default { 'would.*mcp_servers' } }
                Assert-True ($out -match $reason) "missing reason: $out"
                if ($scenario -notlike 'dry-*') { Assert-True ($out -match 'skipped') 'not reported skipped' }
                if ($before) { Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $before }
                elseif ($scenario -in @('relative', 'posix-root', 'drive-relative')) { Assert-True (-not (Test-Path $cfg)) 'created config for non-Windows-absolute root' }
                else { Assert-True (-not (Test-Path $cfgDir)) 'created .codex without prerequisites' }
                Assert-Equal @(Get-ChildItem $homeDir -Recurse -Filter '*.autoos-backup-*').Count 0
            } else {
                $written = [IO.File]::ReadAllText($cfg)
                Assert-Equal $probeState.Python $expectedPython
                Assert-Equal $probeState.Source (Join-Path $checkout 'src')
                if ($scenario -eq 'equal') { Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $before
                    Assert-True ($out -match 'skipped') 'equal hand-formatted entry was rewritten'
                    Assert-Equal @(Get-ChildItem $cfgDir -Filter '*.autoos-backup-*').Count 0
                    return }
                Assert-True ($written.Contains("[mcp_servers.$server]")) 'missing server section'
                if ($scenario -in @('create', 'append')) { Assert-Equal ([regex]::Matches($written, ('(?m)^\[mcp_servers\.' + [regex]::Escape($server) + '\]\r?$')).Count) 1 }
                Assert-True ($written.Contains('enabled = true')) 'not enabled'
                Assert-True ($written.Contains('args = ["-m", "plangraph.spawner.mcp"]')) 'wrong module arguments'
                Assert-True ($written.Contains(('command = ' + (ConvertTo-Json -InputObject $expectedPython -Compress)))) 'wrong command'
                Assert-True ($written.Contains(('PYTHONPATH = ' + (ConvertTo-Json -InputObject (Join-Path $checkout 'src') -Compress)))) 'missing source path'
                $names = if ($scenario -eq 'data') { @('AUTOOS_TEST_FORWARD_NAME') } else { @('AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME') }
                Assert-True ($written.Contains(('env_vars = [' + (($names | ForEach-Object { '"' + $_ + '"' }) -join ', ') + ']'))) 'wrong forwarding names'
                Assert-Equal @([regex]::Matches($written, '(?m)^PYTHONPATH\s*=')).Count 1
                $envBody = ($written -split ('\[mcp_servers\.' + [regex]::Escape($server) + '\.env\]'))[1] -split '(?m)^\[' | Select-Object -First 1
                Assert-Equal @([regex]::Matches($envBody, '(?m)^\s*[^#\s][^\r\n]*=')).Count 1
                Assert-True ($written -notmatch 'REPLACE_WITH_(TEST|STALE|AMBIENT)') 'environment value leaked'
                if ($nativePython) { $prev = $ErrorActionPreference; try { $ErrorActionPreference = 'Continue'; & $nativePython.Source -c 'import sys,tomllib,pathlib; tomllib.loads(pathlib.Path(sys.argv[1]).read_bytes().decode(''utf-8-sig''))' $cfg 2>$null; Assert-Equal $LASTEXITCODE 0 'written config is invalid TOML' } finally { $ErrorActionPreference = $prev } }
                if ($scenario -in @('replace', 'quoted', 'nested', 'literal-key', 'array-type', 'following-array', 'atomic')) {
                    Assert-True ($written.StartsWith($prefix) -and $written.EndsWith($suffix)) 'outside section changed'
                    Assert-True ($written -notmatch 'nested|last|"env\.PYTHONPATH"|command=\[') 'stale TOML survived replacement'
                    $outside = $encoding.GetPreamble() + $encoding.GetBytes($prefix)
                    Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg)[0..($outside.Length - 1)])) ([Convert]::ToBase64String($outside))
                    $suffixBytes = $encoding.GetBytes($suffix); $allBytes = [IO.File]::ReadAllBytes($cfg)
                    Assert-Equal ([Convert]::ToBase64String($allBytes[($allBytes.Length - $suffixBytes.Length)..($allBytes.Length - 1)])) ([Convert]::ToBase64String($suffixBytes))
                }
                if ($scenario -eq 'no-newline') { Assert-True (-not $written.EndsWith("`n")) 'replacement added a final newline'; Assert-True ($written.StartsWith($prefix)) 'prefix changed' }
                if ($scenario -eq 'append') { Assert-True ($written.StartsWith($prefix.TrimEnd([char[]]"`r`n"))) 'append changed old bytes' }
                if ($scenario -eq 'multiline') { Assert-True ($written.StartsWith($prefix)) 'a table-like multiline string was edited' }
                $backups = @(Get-ChildItem $cfgDir -Filter '*.autoos-backup-*')
                Assert-Equal $backups.Count $(if ($before) { 1 } else { 0 })
                if ($before) { Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backups[0].FullName))) $before }
                $bytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg)); $mtime = (Get-Item $cfg).LastWriteTimeUtc
                Initialize-AutoOSLog -Path $log; Set-AutoOSCodexSpawnerMcp -ImportProbe $probe
                Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $bytes
                Assert-Equal (Get-Item $cfg).LastWriteTimeUtc $mtime
                Assert-True ([IO.File]::ReadAllText($log) -match 'skipped') 'second run not skipped'
                Assert-Equal @(Get-ChildItem $cfgDir -Filter '*.autoos-backup-*').Count $backups.Count
                Assert-Equal @(Get-ChildItem $cfgDir -Filter '*.tmp').Count 0
            }
            Assert-Equal $env:PYTHONPATH 'REPLACE_WITH_AMBIENT_PATH'
        } finally {
            if ($originalHandle) { $originalHandle.Dispose() }
            & $mcpModule { param($Render) Set-Item Function:script:ConvertTo-AutoOSCodexToml $Render } $renderBefore
            if ($scenario -eq 'unc-root') { & $mcpModule { Remove-Item Function:Test-Path, Function:Get-Item } }
            Set-Location -LiteralPath $locationBefore.Path
            & $module { param($h) $script:AgentHarness = $h } $harnessBefore
            foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
            Initialize-AutoOSInstaller -DryRun $false -RepoRoot $Root
            Initialize-AutoOSLog -Path (Join-Path ([IO.Path]::GetTempPath()) 'autoos-unused.log')
            Remove-Item -LiteralPath $scratch -Recurse -Force
        }
    }
}

Test-Case 'approving twice adds nothing the second time' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-mcp-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Enable-AutoOSProjectMcpServer -RepoPath $tmp -Name 'omnigraph' 6>$null | Out-Null
        Enable-AutoOSProjectMcpServer -RepoPath $tmp -Name 'omnigraph' 6>$null | Out-Null
        $after = Get-Content -Path (Join-Path $tmp '.claude\settings.local.json') -Raw -Encoding UTF8 |
                 ConvertFrom-Json
        Assert-Equal @($after.enabledMcpjsonServers).Count 1
    } finally {
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
