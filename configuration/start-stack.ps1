<#
.SYNOPSIS
  Start the AutoOS AI stack: OmniRoute gateway, then the app you pick.

.DESCRIPTION
  1. Ensures the OmniRoute gateway answers on http://127.0.0.1:20128
     (starts it in the background when needed).
  2. Requires $env:AUTOOS_OMNIROUTE_KEY (client key from the OmniRoute
     dashboard -> api-manager). Pass -App to launch it wired:
     opencode | zed | nvim | openhands

.EXAMPLE
  $env:AUTOOS_OMNIROUTE_KEY = 'sk-...'
  .\configuration\start-stack.ps1 -App opencode
#>
[CmdletBinding()]
param([ValidateSet('opencode', 'zed', 'nvim', 'openhands', 'opencode-serve', 'none')][string]$App = 'none')

$ErrorActionPreference = 'Stop'
$Gateway = 'http://127.0.0.1:20128'
$keysFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'configuration\api-keys.yml'

function Get-AutoOSKeyValue {
    # Last uncommented (as bash keys_value, tail -n1) `<Name>: <value>` line in a YAML-ish key file.
    # YAML plain/quoted scalar parse (enough for this file):
    #   - starts with " : up to the next " (no escapes)
    #   - starts with ' : up to the next '
    #   - otherwise    : up to the first # preceded by space or tab, then trim
    # CR is stripped. Returns '' when the file or key is missing, or when the
    # value starts with REPLACE_WITH_ (the placeholder for "not filled in").
    param([string]$Path, [string]$Name)
    if (-not $Path -or -not $Name -or -not (Test-Path -LiteralPath $Path)) { return '' }
    $escaped = [regex]::Escape($Name)
    $last = ''
    $found = $false
    foreach ($raw in (Get-Content -LiteralPath $Path -Encoding utf8)) {
        $line = $raw -replace '\r$',''
        if ($line -cmatch "^$escaped\s*:\s*(.+)$") {
            $v = $Matches[1]
            if ($v -match '^\s*#') { continue }
            if ($v.Length -gt 0 -and $v[0] -eq '"') {
                $rest = $v.Substring(1)
                $q = $rest.IndexOf('"')
                if ($q -lt 0) { $v = $rest } else { $v = $rest.Substring(0, $q) }
            }
            elseif ($v.Length -gt 0 -and $v[0] -eq "'") {
                $rest = $v.Substring(1)
                $q = $rest.IndexOf("'")
                if ($q -lt 0) { $v = $rest } else { $v = $rest.Substring(0, $q) }
            }
            else {
                $m = [regex]::Match($v, '([ \t])#')
                if ($m.Success) { $v = $v.Substring(0, $m.Index + 1) }
                $v = $v.TrimEnd()
            }
            if ($v -clike 'REPLACE_WITH_*') { $v = '' }
            $last = $v
            $found = $true
        }
    }
    if (-not $found) { return '' }
    $last
}

# ─── OmniRoute gateway key resolution (mirrors lib/linux/install.sh) ───
# This is a COPY of Test-AutoOSLocalGateway from lib/windows/AutoOS.Install.psm1.
# If the logic changes, update both. A parity test in tests/run-tests.ps1 asserts
# they give the same answers for the WS-OMNIREMOTE URL table.
function Test-AutoOSLocalGateway {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) { return $true }
    try {
        $uri = [Uri]$Url
        $gwHost = $uri.Host
    } catch {
        return $false
    }
    # A relative or host-less URI (e.g. 'not-a-url') has no Host: non-local, like Python and bash.
    if ([string]::IsNullOrEmpty($gwHost)) { return $false }
    $gwHost = $gwHost.Trim('[',']').ToLowerInvariant()
    $gwHost -in @('127.0.0.1','localhost','::1')
}

function Get-AutoOSHostConfigPath {
    if ($env:AUTOOS_HOST_CONFIG) { return [Environment]::ExpandEnvironmentVariables($env:AUTOOS_HOST_CONFIG) }
    if ([Environment]::OSVersion.Platform -eq 'Win32NT') {
        $base = $env:LOCALAPPDATA
        if (-not $base) { $base = "$env:USERPROFILE\AppData\Local" }
        return Join-Path $base 'autoos\host.yml'
    }
    $base = $env:XDG_CONFIG_HOME
    if (-not $base) { $base = "$env:HOME/.config" }
    return Join-Path $base 'autoos/host.yml'
}

function ConvertTo-AutoOSHostName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $Name = $Name.Split('.')[0]
    $Name = $Name.ToLowerInvariant()
    $Name -replace '[^a-z0-9_]','_'
}

function Get-AutoOSHostName {
    # Order: 1) AUTOOS_HOST_NAME env, 2) host_name: from host.yml, 3) short hostname
    if ($env:AUTOOS_HOST_NAME) { return ConvertTo-AutoOSHostName $env:AUTOOS_HOST_NAME }
    $hostFile = Get-AutoOSHostConfigPath
    if (Test-Path -LiteralPath $hostFile) {
        foreach ($line in (Get-Content -LiteralPath $hostFile -Encoding utf8)) {
            $line = $line.Trim()
            if ($line -match '^host_name\s*:\s*(.+)$') {
                $v = $Matches[1].Trim().Trim('"',"'")
                if ($v) { return ConvertTo-AutoOSHostName $v }
            }
        }
    }
    try { $fqdn = [System.Net.Dns]::GetHostName() } catch { $fqdn = 'localhost' }
    $normalized = ConvertTo-AutoOSHostName $fqdn
    Write-Host "AutoOS: using hostname '$normalized' for omniroute key field (set AUTOOS_HOST_NAME or host_name in $hostFile to override)"
    return $normalized
}

function Get-AutoOSClientKeyField {
    $gatewayUrl = $env:AUTOOS_OMNIROUTE_URL
    if (Test-AutoOSLocalGateway $gatewayUrl) {
        return "omniroute_$(Get-AutoOSHostName)"
    } else {
        return 'omniroute_server'
    }
}

function Get-AutoOSClientKey {
    param([string]$KeysFile)
    # 1. Explicit env always wins
    if (-not [string]::IsNullOrWhiteSpace($env:AUTOOS_OMNIROUTE_KEY)) {
        return $env:AUTOOS_OMNIROUTE_KEY
    }
    $field = Get-AutoOSClientKeyField
    $isLocal = Test-AutoOSLocalGateway $env:AUTOOS_OMNIROUTE_URL
    # 2. New field
    if (Test-Path -LiteralPath $KeysFile) {
        $key = Get-AutoOSKeyValue -Path $KeysFile -Name $field
        if (-not [string]::IsNullOrWhiteSpace($key)) { return $key }
    }
    # 3. Legacy fallback (one release, read-only)
    $legacyField = if ($isLocal) { 'omniroute' } else { "omniroute_client_$(Get-AutoOSHostName)" }
    if (Test-Path -LiteralPath $KeysFile) {
        $legacyKey = Get-AutoOSKeyValue -Path $KeysFile -Name $legacyField
        if (-not [string]::IsNullOrWhiteSpace($legacyKey)) {
            Write-Host "api-keys.yml: '$legacyField' is deprecated, rename it to '$field'"
            return $legacyKey
        }
    }
    # 4. Missing - clear error
    $context = if ($isLocal) { 'a local gateway' } else { 'a non-local gateway' }
    $hostFile = Get-AutoOSHostConfigPath
    Write-Host "No OmniRoute client key for $context. Expected field '$field' in $KeysFile (or set AUTOOS_OMNIROUTE_KEY). Host name from AUTOOS_HOST_NAME or $hostFile (host_name:), falling back to short hostname."
    return $null
}

$Key = Get-AutoOSClientKey -KeysFile $keysFile
if ($null -eq $Key) { exit 1 }
# Export so the launched apps inherit it: opencode.jsonc and the Zed settings
# carry no key by design ("key via env"), so without this the apps the script
# launches would start unauthenticated.
$env:AUTOOS_OMNIROUTE_KEY = $Key

function New-FileBackup {
    # <file>.autoos-backup-<stamp>, and the path it returns. The stamp has
    # whole-second resolution and Copy-Item -Force would let a second backup in
    # the same second replace the first: the user's ORIGINAL gone. On a name
    # clash this appends -1, -2, ... (the same rule as Copy-AutoOSBackup in
    # lib\windows\AutoOS.Install.psm1, which this standalone launcher does not
    # import). -Stamp exists so a test can force the clash.
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$Stamp = (Get-Date -Format 'yyyyMMdd-HHmmss')
    )
    $base = "$Path.autoos-backup-$Stamp"
    $backup = $base
    $n = 0
    while (Test-Path -LiteralPath $backup) { $n++; $backup = "$base-$n" }
    Copy-Item -LiteralPath $Path -Destination $backup
    $backup
}

function Test-Gateway {
    # /api/health, not /v1/models: the latter 401s for a normal client key in
    # this build, so probing it would call a healthy gateway "down" forever.
    try { (Invoke-WebRequest -Uri "$Gateway/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}

if (-not (Test-Gateway)) {
    if (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
        Write-Host "omniroute is not installed. Run: .\setup.ps1 -Only omniroute -Yes"
        exit 1
    }
    Write-Host 'Starting OmniRoute in the background...'
    Start-Process -FilePath 'omniroute' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
    $tries = 0
    while ((-not (Test-Gateway)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
    if (-not (Test-Gateway)) { Write-Host 'Gateway did not answer. Run `omniroute doctor`.'; exit 1 }
}
Write-Host "Gateway OK on $Gateway"

switch ($App) {
    'opencode'  { & opencode }
    'zed'       { & "$env:LOCALAPPDATA\Programs\Zed\zed.exe" . }
    'nvim'      { & nvim }
    'openhands' {
        # Docker Desktop must be running; the container reaches the gateway via
        # host.docker.internal. Image name is the current upstream one (the old
        # docker.all-hands.dev registry is gone). The sandbox/agent-server image
        # is chosen by OpenHands itself on first conversation - do not pin it.
        # The client key is exported and inherited with `-e LLM_API_KEY` (no
        # value on the command line, so `ps` never shows it).
        # User settings drift newer than the image (measured 2026-09-23:
        # agent-canvas 1.20 writes agent_settings.schema_version 6 + an
        # `enabled` key on every MCP server, while the
        # docker.openhands.dev/openhands/openhands:latest image supports
        # version 4 and rejects `enabled` with extra_forbidden -> every
        # /api settings route 500s. Repair in place (with a backup, never
        # a delete): clamp the version DOWN to 4 (older payloads keep
        # theirs so the image's own migrations still run) and strip the
        # `enabled` keys. Unparseable files still move aside - OpenHands
        # regenerates. NOTE: the version lives under agent_settings, NOT
        # top-level schema_version (top stays 2-3 on both good and bad
        # files, so checking the top level misses the breakage).
        $ohSettings = Join-Path $env:USERPROFILE '.openhands\settings.json'
        if (Test-Path $ohSettings) {
            try {
                $ohJson = Get-Content $ohSettings -Raw -Encoding utf8 | ConvertFrom-Json
                $ohAgentVer = $ohJson.agent_settings.schema_version
                $ohHasEnabled = @($ohJson.agent_settings.mcp_config.PSObject.Properties.Value |
                    Where-Object { $_.PSObject.Properties.Name -contains 'enabled' }).Count -gt 0
                $ohNum = 0
                if ("$ohAgentVer" -match '^\d+$') { $ohNum = [int]"$ohAgentVer" }
                if ($ohNum -gt 4 -or $ohHasEnabled) {
                    $backup = New-FileBackup -Path $ohSettings
                    if ($ohNum -gt 4) { $ohJson.agent_settings.schema_version = 4 }
                    foreach ($srv in @($ohJson.agent_settings.mcp_config.PSObject.Properties.Value)) {
                        if ($null -ne $srv.PSObject.Properties['enabled']) { $srv.PSObject.Properties.Remove('enabled') }
                    }
                    # BOM-free on every host: PS 5.1 Set-Content -Encoding
                    # utf8 emits a BOM, which breaks the container's json
                    # parse (same reason the Zed writer asserts no BOM).
                    [IO.File]::WriteAllText($ohSettings, ($ohJson | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
                    Write-Host "Repaired OpenHands settings (agent_settings.schema_version $ohAgentVer -> $($ohJson.agent_settings.schema_version), stripped enabled keys). Backup: $backup."
                }
            } catch {
                $backup = New-FileBackup -Path $ohSettings
                Remove-Item $ohSettings -Force
                Write-Host "Unparseable OpenHands settings moved aside to $backup."
            }
        }
        # Re-project the tier profiles from the spec on every start: a rotated
        # key, a re-curated spec, or a hand edit converges back automatically.
        # Scoped Continue: under Stop, 5.1 turns the child python's stderr
        # into a terminating error even when captured.
        $syncPy = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($syncPy) {
            $prevAction = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $syncOut = & $syncPy.Source (Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\sync-openhands-profiles.py') --openhands-dir (Join-Path $env:USERPROFILE '.openhands') 2>&1
                foreach ($line in $syncOut) { Write-Host $line }
                if ($LASTEXITCODE -ne 0) { Write-Host 'tier profile sync reported a problem - continuing with existing profiles' }
            } finally { $ErrorActionPreference = $prevAction }
        } else {
            Write-Host 'python not found - tier profile sync skipped (the installer covers it)'
        }
        $existing = (& docker ps -a --format '{{.Names}}' 2>$null) -join "`n"
        if ($existing -match '(?m)^openhands-app$') {
            $running = (& docker ps --format '{{.Names}}' 2>$null) -join "`n"
            if ($running -notmatch '(?m)^openhands-app$') { & docker start openhands-app | Out-Null }
        } else {
            # Fresh create: the LLM_* env is the container's FIRST impression
            # (read before settings.json exists), so it must already be the
            # gateway tier - otherwise the first-run UI shows no usable agent
            # until a reinstall. Key via inherited env (never argv, never ps).
            $env:LLM_API_KEY = $Key
            # Detached, no -it: -it fails without a TTY (non-interactive shells)
            # and foreground -it never returns, so the URL line below would lie.
            & docker run -d --rm `
                -e LLM_MODEL=openai/t1-orchestrator `
                -e LLM_API_KEY `
                -e LLM_BASE_URL="http://host.docker.internal:20128/v1" `
                -e LOG_ALL_EVENTS=true `
                -p 3000:3000 `
                -v /var/run/docker.sock:/var/run/docker.sock `
                -v "$env:USERPROFILE\.openhands:/.openhands" `
                --add-host host.docker.internal:host-gateway `
                --name openhands-app `
                docker.openhands.dev/openhands/openhands:latest | Out-Null
            Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
        }
        # No URL line without a probe behind it (the old script printed the URL
        # even when -it had failed to start anything).
        $deadline = (Get-Date).AddSeconds(180)
        $up = $false
        while ((Get-Date) -lt $deadline) {
            try {
                if ((Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { $up = $true; break }
            } catch { Start-Sleep 5 }
        }
        if (-not $up) { Write-Host 'OpenHands did not answer on :3000 - see: docker logs openhands-app'; exit 1 }
        Write-Host 'OpenHands UI: http://localhost:3000'
    }
    'opencode-serve' {
        # Phone fallback UI (docs/openhands-runbook.md rung 2): resume when
        # down, no-op when up. 401 without pairing credentials = alive.
        $ocUp = $false
        try { $ocUp = (Invoke-WebRequest -Uri 'http://127.0.0.1:4096/' -UseBasicParsing -TimeoutSec 5).StatusCode -in 200, 401 }
        catch {
            $r = $_.Exception.Response
            if ($null -ne $r) { $ocUp = ([int]$r.StatusCode) -in 200, 401 }
        }
        if ($ocUp) { Write-Host 'opencode serve already up on :4096 - nothing to do.' }
        elseif (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
            Write-Host 'opencode is not installed. Run: .\setup.ps1 -Only opencode-cli -Yes'; exit 1
        } else {
            $pw = Get-AutoOSKeyValue -Path $keysFile -Name 'opencode_password'
            if ($pw) {
                $env:OPENCODE_PASSWORD = $pw
                Write-Host "Serve password: from $keysFile (user: opencode)."
            } else {
                Write-Host "No opencode_password in $keysFile - opencode serve picks a new random password every start; add one (see docs/web-services.md#logins-and-secrets)."
            }
            Write-Host 'Starting opencode serve in the background...'
            Start-Process -FilePath 'opencode' -ArgumentList 'serve', '--hostname', '0.0.0.0', '--port', '4096' -WindowStyle Hidden
            Write-Host 'opencode serve should answer on http://localhost:4096 (401 = alive, pair via: opencode pair).'
        }
    }
}
