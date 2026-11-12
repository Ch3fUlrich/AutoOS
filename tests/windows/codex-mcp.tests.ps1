Test-Case 'codex spawner MCP catalog hook is exported' {
    $entry = @($winCatalog.categories.components | Where-Object { $_.id -eq 'codex' })[0]; Assert-Equal $entry.postInstall 'Set-AutoOSCodexSpawnerMcp'
    Assert-True ($null -ne (Get-Command $entry.postInstall -ErrorAction SilentlyContinue)) 'Codex hook not exported'
    $spec = (Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json).codex_mcp_servers.'plangraph-spawner'
    Assert-Equal $spec.root_env 'PLANGRAPH_ROOT'
    Assert-Equal ($spec.env_vars -join ',') 'AUTOOS_OMNIROUTE_KEY,PLANGRAPH_GATEWAY_LOG_TOKEN,AUTOOS_ROOT,AUTOOS_HOST_NAME'
    Assert-Equal ($spec.args -join ',') '-m,plangraph.spawner.mcp'
}

foreach ($scenario in @('create', 'append', 'replace', 'equal', 'quoted', 'dotted', 'quoted-dotted', 'inline', 'nested', 'literal-key', 'array-type', 'multiline', 'native-ok', 'native-fail', 'absent', 'unset', 'missing', 'import', 'venv', 'dry-create', 'dry-replace', 'data', 'array-table', 'array-table-spaced', 'following-array', 'no-newline', 'atomic', 'relative', 'missing-root_env', 'missing-args', 'missing-env_vars', 'timeout', 'native-isolation')) {
    Test-Case "codex spawner MCP $scenario is hermetic and idempotent" {
        $nativePython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1; if ($scenario -like 'native-*' -and -not $nativePython) { Skip 'no python for native import probe'; return }
        $scratch = Join-Path $env:TEMP ('autoos-codex-' + [Guid]::NewGuid().ToString('N'))
        $homeDir = Join-Path $scratch 'home'; $checkout = Join-Path $scratch "checkout with 'quotes'"; $bin = Join-Path $scratch 'bin'
        $null = New-Item -ItemType Directory -Path $homeDir, (Join-Path $checkout 'src'), $bin -Force
        $saved = @{}; $originalHandle = $null; $locationBefore = Get-Location
        foreach ($key in @('HOME', 'USERPROFILE', 'PATH', 'PLANGRAPH_ROOT', 'PYTHONPATH', 'AUTOOS_TEST_CHECKOUT', 'AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME')) {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        }
        $module = Get-Module AutoOS.Install; $harnessBefore = & $module { $script:AgentHarness }
        try {
            $env:HOME = $homeDir; $env:USERPROFILE = $homeDir; $env:PATH = $bin
            $env:PLANGRAPH_ROOT = $checkout; $env:PYTHONPATH = 'REPLACE_WITH_AMBIENT_PATH'
            foreach ($key in @('AUTOOS_OMNIROUTE_KEY', 'PLANGRAPH_GATEWAY_LOG_TOKEN', 'AUTOOS_ROOT', 'AUTOOS_HOST_NAME')) {
                [Environment]::SetEnvironmentVariable($key, ('REPLACE_WITH_TEST_' + $key), 'Process')
            }
            [IO.File]::WriteAllText((Join-Path $bin 'python.exe'), '')
            if ($scenario -in @('native-ok', 'native-fail')) {
                $env:PATH = Split-Path -Parent $nativePython.Source
                $body = if ($scenario -eq 'native-ok') { 'import os,tempfile; assert os.path.samefile(os.getcwd(), tempfile.gettempdir()) # synthetic importable checkout' } else { 'raise RuntimeError("synthetic import failure")' }
                [IO.File]::WriteAllText((Join-Path $checkout 'src/plangraph.py'), $body)
            }
            if ($scenario -in @('create', 'dry-create', 'data')) { [IO.File]::WriteAllText((Join-Path $bin 'codex.cmd'), '@exit /b 0') }
            $cfgDir = Join-Path $homeDir '.codex'; $cfg = Join-Path $cfgDir 'config.toml'
            if ($scenario -notin @('absent', 'unset', 'missing', 'import', 'create', 'dry-create', 'data')) { $null = New-Item -ItemType Directory -Path $cfgDir }
            if ($scenario -in @('unset', 'missing', 'import')) { [IO.File]::WriteAllText((Join-Path $bin 'codex.cmd'), '@exit /b 0') }
            if ($scenario -eq 'unset') { $env:PLANGRAPH_ROOT = $null }
            if ($scenario -eq 'missing') { $env:PLANGRAPH_ROOT = Join-Path $scratch 'missing' }
            if ($scenario -eq 'relative') { Set-Location -LiteralPath $scratch; $env:PLANGRAPH_ROOT = Split-Path -Leaf $checkout }
            if ($scenario -eq 'venv') { $null = New-Item -ItemType Directory -Path (Join-Path $checkout '.venv/Scripts') -Force; [IO.File]::WriteAllText((Join-Path $checkout '.venv/Scripts/python.exe'), '') }
            $server = 'plangraph-spawner'; $rootVar = 'PLANGRAPH_ROOT'
            if ($scenario -eq 'data') {
                $custom = Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json; $spec = $custom.codex_mcp_servers.'plangraph-spawner'
                $spec.root_env = 'AUTOOS_TEST_CHECKOUT'; $spec.env_vars = @('AUTOOS_TEST_FORWARD_NAME')
                $custom.codex_mcp_servers.PSObject.Properties.Remove('plangraph-spawner')
                $custom.codex_mcp_servers | Add-Member -NotePropertyName 'fixture-spawner' -NotePropertyValue $spec
                & $module { param($h) $script:AgentHarness = $h } $custom
                $env:AUTOOS_TEST_CHECKOUT = $checkout; $env:PLANGRAPH_ROOT = $null; $server = 'fixture-spawner'; $rootVar = 'AUTOOS_TEST_CHECKOUT'
            }
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
            if ($scenario -eq 'append') { [IO.File]::WriteAllText($cfg, $prefix.TrimEnd([char[]]"`r`n"), $encoding) }
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
                $probeShell = (Get-Process -Id $PID).Path
                $pidFile = Join-Path $scratch 'probe.pid'; $sleepScript = Join-Path $scratch 'sleep.ps1'
                [IO.File]::WriteAllText($sleepScript, '[IO.File]::WriteAllText($args[0], [string]$PID); Start-Sleep -Seconds 30')
                $sleepProbe = {
                    $timeoutMilliseconds = $args[2] # The injected child ignores Python/source, not the production deadline.
                    & (Get-Module AutoOS.Mcp -All | Select-Object -First 1) {
                        param($Shell, $Script, $PidFile, $Timeout)
                        Invoke-AutoOSCodexProbeProcess -FilePath $Shell -Arguments @('-NoProfile', '-File', $Script, $PidFile) -TimeoutMilliseconds $Timeout
                    } $probeShell $sleepScript $pidFile $timeoutMilliseconds
                }
                $clock = [Diagnostics.Stopwatch]::StartNew()
                $specs = (Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json).codex_mcp_servers
                & $module { param($Servers, $Probe) Set-AutoOSCodexMcpEntry -Servers $Servers -ImportProbe $Probe -ProbeTimeoutMilliseconds 2500 -Backup { throw 'timeout must not back up' } } $specs $sleepProbe
                Assert-True ($clock.Elapsed.TotalSeconds -lt 10) 'sleeping probe exceeded its deadline'
                Assert-True ([IO.File]::ReadAllText($log) -match 'skipped.*cannot import plangraph.*timed out') 'timeout reason missing'
                Assert-True (-not (Test-Path $cfg)) 'timeout wrote a config'
                Assert-True (Test-Path $pidFile) 'sleeping child never started'
                $childId = [int][IO.File]::ReadAllText($pidFile)
                Assert-True (-not (Get-Process -Id $childId -ErrorAction SilentlyContinue)) 'timed-out probe was not killed'
                return
            } elseif ($scenario -like 'missing-*' -and $scenario -ne 'missing') {
                $specs = Get-Content (Join-Path $Root 'catalog/agent-harness.json') -Raw | ConvertFrom-Json
                $key = $scenario.Substring('missing-'.Length); $specs.codex_mcp_servers.$server.PSObject.Properties.Remove($key)
                & $module { param($Servers, $Probe) Set-AutoOSCodexMcpEntry -Servers $Servers -ImportProbe $Probe -Backup { throw 'invalid catalog must not back up' } } $specs.codex_mcp_servers $probe
                Assert-True ([IO.File]::ReadAllText($log) -match "skipped.*missing.*$key") 'missing catalog key reason absent'
                Assert-True (-not (Test-Path $cfg)) 'invalid catalog wrote a config'
                Assert-Equal $probeState.Calls 0
                return
            } elseif ($scenario -like 'native-*') { Invoke-AutoOSPostInstall -Component ([pscustomobject]@{ PostInstall = 'Set-AutoOSCodexSpawnerMcp' }) } else { Set-AutoOSCodexSpawnerMcp -ImportProbe $probe }
            $out = [IO.File]::ReadAllText($log)
            if ($scenario -notlike 'native-*' -and $scenario -notin @('absent', 'unset', 'missing', 'relative')) { Assert-Equal $probeState.Calls 1 }
            if ($scenario -in @('dotted', 'quoted-dotted', 'inline', 'array-table', 'array-table-spaced')) { Assert-True ($out -match 'skipped.*(dotted|inline|array).*manual configuration') 'unsupported definition not safely skipped'
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
            if ($scenario -in @('absent', 'unset', 'missing', 'import', 'relative') -or $scenario -like 'dry-*') {
                $reason = switch ($scenario) { absent { 'codex.*not present' } unset { "$rootVar.*unset" } missing { "$rootVar.*does not exist" } import { 'cannot import plangraph' } relative { 'skipped: PLANGRAPH_ROOT must be an absolute path' } default { 'would.*mcp_servers' } }
                Assert-True ($out -match $reason) "missing reason: $out"
                if ($scenario -notlike 'dry-*') { Assert-True ($out -match 'skipped') 'not reported skipped' }
                if ($before) { Assert-Equal ([Convert]::ToBase64String([IO.File]::ReadAllBytes($cfg))) $before }
                elseif ($scenario -eq 'relative') { Assert-True (-not (Test-Path $cfg)) 'created config for relative root' }
                else { Assert-True (-not (Test-Path $cfgDir)) 'created .codex without prerequisites' }
                Assert-Equal @(Get-ChildItem $homeDir -Recurse -Filter '*.autoos-backup-*').Count 0
                if ($scenario -in @('absent', 'unset', 'missing', 'relative')) { Assert-Equal $probeState.Calls 0 }
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
